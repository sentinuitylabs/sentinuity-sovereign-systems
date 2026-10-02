"""
services/spine_liveness.py — SPINE_LIVENESS_20261001
====================================================

Liveness contract for the qualification / supervision spine.

On 2026-10-01 03:16 -> 06:39 the WebSocket price oracle aged to 8,398 s, the
reconciler's RPC calls failed and the substrate lab reported
price_fresh_assets=0, while the execution engine kept logging
`[EXEC_TRUTH] state=HEALTHY basis=HEARTBEAT_IDLE`. HEALTHY there only meant
"the quote producer is idle and alive"; nothing checked the stages upstream.

Required stages and their signed TTLs (no new thresholds are invented):
  supervisor  system_heartbeat['neural_supervisor'].last_pulse
              TTL = HEARTBEAT_DEAD_SECONDS (120 s, services/system_guardian.py)
  qualifier   system_heartbeat['qualifier'].last_pulse, TTL 120 s (same)
  oracle      newest mtm tick age, TTL = ORACLE_GATE_SEC (300 s, the engine's
              existing oracle gate)
Informational (shown, never degrades — quiet markets are legitimate):
  candidate   newest market_snapshots row age

A stale REQUIRED stage makes the spine DEGRADED. The engine then publishes
EXEC_TRUTH_STATE=DEGRADED (basis SPINE_STALE:<stage>), which its existing
entry-scan enforcement already treats as "refuse new exposure". Exits are
never gated by this module.
"""
from __future__ import annotations

import math
import time
from typing import Any, Mapping, Optional

HEARTBEAT_TTL_SEC = 120.0
ORACLE_TTL_SEC = 300.0
CANDIDATE_TTL_SEC = 900.0

REQUIRED = ("supervisor", "qualifier", "oracle")


def _age(now: float, ts: Any) -> Optional[float]:
    try:
        t = float(ts)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(t) or t <= 0:
        return None
    if t > 1e12:          # milliseconds
        t /= 1000.0
    return max(0.0, now - t)


def evaluate(ages: Mapping[str, Optional[float]],
             ttls: Optional[Mapping[str, float]] = None,
             statuses: Optional[Mapping[str, str]] = None,
             required_stages: Optional[tuple[str, ...]] = None) -> dict:
    """Pure: classify each stage and the spine as a whole.

    LATCH_COLDSTART_REPAIR_20261001:
    ``required_stages`` allows callers to preserve the pre-existing executor
    contract that oracle freshness is not a first-admission requirement when
    there are zero open positions. Supervisor and qualifier remain required.
    If omitted, the original fail-closed REQUIRED tuple is used.
    """
    t = {"supervisor": HEARTBEAT_TTL_SEC, "qualifier": HEARTBEAT_TTL_SEC,
         "oracle": ORACLE_TTL_SEC, "candidate": CANDIDATE_TTL_SEC}
    if ttls:
        t.update({k: float(v) for k, v in ttls.items() if v})
    statuses = statuses or {}
    required_set = set(REQUIRED if required_stages is None else required_stages)
    stages = []
    stale_required = []
    for name in ("supervisor", "qualifier", "oracle", "candidate"):
        age = ages.get(name)
        required = name in required_set
        status = str(statuses.get(name) or "").upper()
        errored = status in ("ERROR", "DEAD", "OFFLINE")
        stale = (age is None) or (age > t[name]) or errored
        if required and stale:
            stale_required.append(name)
        stages.append({"name": name, "age_sec": None if age is None else round(age, 1),
                       "ttl_sec": t[name], "required": required,
                       "stale": bool(stale), "status": status or None})
    return {"state": "DEGRADED" if stale_required else "HEALTHY",
            "degraded": bool(stale_required),
            "stale_required": stale_required, "stages": stages}


def read_ages(conn, *, now: Optional[float] = None,
              oracle_age_sec: Optional[float] = None,
              intel_conn=None) -> tuple[dict, dict]:
    """Read stage ages from the matrix DB (and optionally the intel DB)."""
    now = time.time() if now is None else float(now)
    ages: dict = {"supervisor": None, "qualifier": None,
                  "oracle": oracle_age_sec, "candidate": None}
    statuses: dict = {}
    try:
        for svc, key in (("neural_supervisor", "supervisor"), ("qualifier", "qualifier")):
            r = conn.execute("SELECT status,last_pulse FROM system_heartbeat WHERE service_name=?",
                             (svc,)).fetchone()
            if r:
                statuses[key] = str(r[0] or "")
                ages[key] = _age(now, r[1])
    except Exception:
        pass
    try:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}
        tcol = next((c for c in ("created_at", "timestamp", "first_seen_at") if c in cols), None)
        if tcol:
            r = conn.execute(f"SELECT MAX(CAST({tcol} AS REAL)) FROM market_snapshots").fetchone()
            ages["candidate"] = _age(now, r[0] if r else None)
    except Exception:
        pass
    if ages["oracle"] is None and intel_conn is not None:
        try:
            r = intel_conn.execute("SELECT MAX(ts_ms) FROM mtm_ticks").fetchone()
            ages["oracle"] = _age(now, r[0] if r else None)
        except Exception:
            pass
    return ages, statuses
