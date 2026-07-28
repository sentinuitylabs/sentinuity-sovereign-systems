"""
live_decision_contract.py
───────────────────────────────────────────────────────────────────────────────
SENTINUITY — EXECUTOR-AUTHORED LIVE DECISION CONTRACT
(SIGNOFF_LIVE_LEDGER_20260716 — resolves the executor/UI parity blocker)

THE PROBLEM THIS SOLVES
───────────────────────
Before this module, ui/live_gate_constellation.py computed its own FINAL FIRE
verdict by independently calling evaluate_pattern_permission() and re-deriving
gate state from raw tables. The executor computed its verdict separately in
scan_for_entries(). Two code paths, two verdicts, converging only by
coincidence. The directive is explicit: "The central state must come from the
exact executor decision contract" and "No UI-only inference may claim live
readiness."

THE CONTRACT
────────────
The executor — and ONLY the executor — writes a row here each time it evaluates
the live lane. The UI reads. It never recomputes.

  publish(...)        ← called by services/execution_engine.py at the live
                        decision point. Records the authoritative verdict, the
                        exact failing gate, and every gate's PASS/WARN/BLOCK.
  read_contract()     ← called by the UI. Returns the executor's own verdict,
                        with staleness, or UNAVAILABLE. Never a guess.

VERDICT VOCABULARY (matches the directive's central core states)
  BLOCKED             — a hard gate failed. `blocker` names the exact gate.
  ALIGNING            — gates passing but not all sensing/decision inputs ready.
  ARMED_WAITING       — healthy and armed; simply no candidate right now.
                        An idle healthy system says this, never BLOCKED.
  FIRE_PATH_OPEN      — executor would fire on the next qualifying candidate.
  BUY_SUBMITTED / OPEN_REAL / SELL_SUBMITTED / SETTLED / MANUAL_INTERVENTION
                      — execution/settlement states, sourced from live_state.

STALENESS DOCTRINE
──────────────────
A contract older than STALE_AFTER_SEC is NOT treated as truth. read_contract()
returns stale=True and the UI must render UNAVAILABLE rather than a remembered
verdict — a remembered verdict is exactly the "visual readiness ≠ executor
readiness" failure the directive forbids.

This module never decides anything. It records and reports decisions made
elsewhere. It has no authority over capital.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any, Optional

log = logging.getLogger("live_decision_contract")

STALE_AFTER_SEC = 120.0
EXECUTOR_HEARTBEAT_MAX_SEC = 90.0

VERDICT_BLOCKED = "BLOCKED"
VERDICT_ALIGNING = "ALIGNING"
VERDICT_ARMED_WAITING = "ARMED_WAITING"
VERDICT_FIRE_PATH_OPEN = "FIRE_PATH_OPEN"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_decision_contract (
    id                 INTEGER PRIMARY KEY AUTOINCREMENT,
    verdict            TEXT NOT NULL,
    blocker            TEXT,
    next_event         TEXT,
    lane_armed         INTEGER DEFAULT 0,
    pattern_state      TEXT,
    pattern_armed      INTEGER DEFAULT 0,
    pattern_multiplier REAL,
    pattern_reason     TEXT,
    size_multiplier    REAL,
    would_fire_usd     REAL,
    open_real          INTEGER DEFAULT 0,
    real_exposure_usd  REAL,
    gates_json         TEXT,
    candidate_mint     TEXT,
    position_id        INTEGER,
    decision_latency_sec REAL,
    authored_by        TEXT NOT NULL,
    created_at         REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ldc_created ON live_decision_contract(created_at DESC);
"""


def ensure_schema(conn) -> None:
    for stmt in _SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass


def publish(
    *,
    verdict: str,
    gates: list[dict[str, Any]],
    blocker: Optional[str] = None,
    next_event: Optional[str] = None,
    lane_armed: bool = False,
    pattern_state: Optional[str] = None,
    pattern_armed: bool = False,
    pattern_multiplier: Optional[float] = None,
    pattern_reason: Optional[str] = None,
    size_multiplier: Optional[float] = None,
    would_fire_usd: Optional[float] = None,
    open_real: int = 0,
    real_exposure_usd: Optional[float] = None,
    candidate_mint: Optional[str] = None,
    position_id: Optional[int] = None,
    decision_latency_sec: Optional[float] = None,
    authored_by: str = "execution_engine",
) -> None:
    """
    Executor-only writer. Never raises: publishing the contract must not be able
    to break the trading path. A failed publish makes the UI show UNAVAILABLE —
    which is correct and safe — rather than a stale or invented verdict.

    `gates` is a list of {"name","state","current","contract"} where state is
    PASS | WARN | BLOCK, mirroring the directive's per-node requirement that
    each node states PASS/WARN/BLOCK with its current reason, source, threshold
    and actual value.
    """
    try:
        from core.schema import get_connection
        now = time.time()
        # Contract-level safety invariant. A caller cannot publish a funded
        # fire path while the pattern authority is DORMANT/WATCHING/unavailable.
        pattern_state_norm = str(pattern_state or "").upper()
        pattern_authorised = bool(
            pattern_armed
            and pattern_state_norm in {"ARMED", "CONFIRMED", "BYPASSED"}
            and (pattern_state_norm == "BYPASSED" or float(pattern_multiplier or 0.0) > 0.0)
        )
        if verdict == VERDICT_FIRE_PATH_OPEN and not pattern_authorised:
            verdict = VERDICT_BLOCKED
            blocker = blocker or f"PATTERN: {pattern_state_norm or 'UNAVAILABLE'} has not earned capital authority"
            next_event = next_event or "await independent realised confirmations"
            pattern_armed = False
            pattern_multiplier = 0.0

        with get_connection() as conn:
            ensure_schema(conn)
            cur = conn.execute(
                "INSERT INTO live_decision_contract (verdict,blocker,next_event,"
                "lane_armed,pattern_state,pattern_armed,pattern_multiplier,"
                "pattern_reason,size_multiplier,would_fire_usd,open_real,"
                "real_exposure_usd,gates_json,candidate_mint,position_id,"
                "decision_latency_sec,authored_by,created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (verdict, blocker, next_event, 1 if lane_armed else 0,
                 pattern_state, 1 if pattern_armed else 0, pattern_multiplier,
                 pattern_reason, size_multiplier, would_fire_usd, int(open_real),
                 real_exposure_usd,
                 json.dumps(gates or [], separators=(",", ":"), default=str),
                 candidate_mint, position_id, decision_latency_sec,
                 authored_by, now),
            )
            # Bounded retention without adding a DELETE write to every decision.
            # Prune only once per 25 inserts; this materially reduces writer
            # contention during busy runner-rich periods.
            inserted_id = int(cur.lastrowid or 0)
            if inserted_id and inserted_id % 25 == 0:
                conn.execute(
                    "DELETE FROM live_decision_contract WHERE id NOT IN "
                    "(SELECT id FROM live_decision_contract "
                    " ORDER BY created_at DESC LIMIT 500)"
                )
            conn.commit()
    except Exception as exc:
        log.debug("contract publish skipped: %s", exc)


def read_contract(conn=None) -> dict:
    """
    UI-side reader. READ-ONLY, and the only sanctioned source of live-readiness
    truth for any surface. Returns:

      {"available": bool, "stale": bool, "age_sec": float, "verdict": str,
       "blocker": str, "next_event": str, "gates": [...], ...}

    available=False or stale=True MUST render as UNAVAILABLE. Callers must not
    substitute their own computation — that would reintroduce the exact parity
    defect this contract exists to remove.
    """
    out: dict[str, Any] = {
        "available": False, "stale": True, "age_sec": None,
        "verdict": "UNAVAILABLE",
        "blocker": "Executor has not published a decision contract",
        "next_event": "Waiting for the executor to complete a live evaluation cycle",
        "gates": [], "pattern_state": None, "pattern_armed": False,
        "lane_armed": False, "size_multiplier": None, "would_fire_usd": None,
        "open_real": 0, "real_exposure_usd": None, "decision_latency_sec": None,
        "authored_by": None, "error": None,
    }
    own = conn is None
    try:
        if own:
            from core.schema import get_connection
            conn = get_connection()
        row = conn.execute(
            "SELECT * FROM live_decision_contract ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        if not row:
            return out
        keys = row.keys() if hasattr(row, "keys") else []
        rec = {k: row[k] for k in keys}
        age = time.time() - float(rec.get("created_at") or 0)
        stale = age > STALE_AFTER_SEC

        # Resolve executor liveness independently from the decision age. Different
        # Sentinuity eras used slightly different heartbeat table/column shapes and
        # occasionally namespaced service values. The UI must not call a live
        # executor "unknown" merely because one legacy reader missed that shape.
        executor_fresh = False
        executor_hb_age = None
        executor_hb_source = None

        def _to_epoch(raw):
            if raw is None:
                return None
            try:
                value = float(raw)
                # Accept millisecond epochs without treating them as far-future.
                if value > 10_000_000_000:
                    value /= 1000.0
                return value
            except Exception:
                pass
            try:
                from datetime import datetime
                text = str(raw).strip().replace("Z", "+00:00")
                return datetime.fromisoformat(text).timestamp()
            except Exception:
                return None

        for table in ("system_heartbeat", "service_heartbeats"):
            try:
                cols = {str(r[1]) for r in conn.execute(
                    f"PRAGMA table_info({table})").fetchall()}
                if not cols:
                    continue
                service_col = next((c for c in (
                    "service", "service_name", "name", "component", "process"
                ) if c in cols), None)
                time_col = next((c for c in (
                    "last_pulse", "heartbeat_at", "last_seen", "updated_at", "timestamp",
                    "ts", "last_heartbeat", "created_at"
                ) if c in cols), None)
                if not service_col or not time_col:
                    continue
                hb = conn.execute(
                    f"SELECT {time_col}, {service_col} FROM {table} "
                    f"WHERE lower(CAST({service_col} AS TEXT)) LIKE '%execution_engine%' "
                    f"OR lower(CAST({service_col} AS TEXT)) LIKE '%exec_engine%' "
                    f"OR lower(CAST({service_col} AS TEXT)) LIKE '%paper_executor%' "
                    f"ORDER BY rowid DESC LIMIT 1"
                ).fetchone()
                if not hb:
                    continue
                hb_ts = _to_epoch(hb[0])
                if hb_ts is None:
                    continue
                hb_age = max(0.0, time.time() - hb_ts)
                if executor_hb_age is None or hb_age < executor_hb_age:
                    executor_hb_age = hb_age
                    executor_hb_source = f"{table}.{time_col}:{hb[1]}"
                if hb_age <= EXECUTOR_HEARTBEAT_MAX_SEC:
                    executor_fresh = True
                    break
            except Exception:
                continue

        idle_healthy = stale and executor_fresh
        out.update({
            "available": True,
            "stale": stale and not idle_healthy,
            "idle_healthy": idle_healthy,
            "executor_fresh": executor_fresh,
            "executor_heartbeat_age_sec": (round(executor_hb_age, 1) if executor_hb_age is not None else None),
            "executor_heartbeat_source": executor_hb_source,
            "age_sec": round(age, 1),
            "verdict": (VERDICT_ARMED_WAITING if idle_healthy else
                        ("UNAVAILABLE" if stale else str(rec.get("verdict") or "UNAVAILABLE"))),
            "blocker": ((f"Executor healthy; last candidate decision was {age:.0f}s ago"
                         if idle_healthy else
                         f"Executor contract is stale ({age:.0f}s old) and heartbeat is not fresh")
                        if stale else rec.get("blocker")),
            "next_event": rec.get("next_event"),
            "lane_armed": bool(rec.get("lane_armed")),
            "pattern_state": rec.get("pattern_state"),
            "pattern_armed": bool(rec.get("pattern_armed")),
            "pattern_multiplier": rec.get("pattern_multiplier"),
            "pattern_reason": rec.get("pattern_reason"),
            "size_multiplier": rec.get("size_multiplier"),
            "would_fire_usd": rec.get("would_fire_usd"),
            "open_real": int(rec.get("open_real") or 0),
            "real_exposure_usd": rec.get("real_exposure_usd"),
            "decision_latency_sec": rec.get("decision_latency_sec"),
            "candidate_mint": rec.get("candidate_mint"),
            "authored_by": rec.get("authored_by"),
        })
        try:
            out["gates"] = json.loads(rec.get("gates_json") or "[]")
        except Exception:
            out["gates"] = []
        return out
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}:{exc}"
        return out
    finally:
        if own and conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def derive_verdict(*, lane_armed: bool, hard_gates: list[dict],
                   flow_ready: bool) -> tuple[str, Optional[str]]:
    """
    Single canonical verdict derivation, used by the executor when it publishes.
    Kept here (not in the UI) so exactly one implementation exists.

    An idle but healthy system reports ARMED_WAITING, never BLOCKED — directive
    Part 4. A genuine blocker names the exact failing gate.
    """
    blocked = [g for g in hard_gates if str(g.get("state")).upper() == "BLOCK"]
    if blocked:
        g = blocked[0]
        return VERDICT_BLOCKED, f"{g.get('name')}: {g.get('current')}"
    if not lane_armed:
        return VERDICT_ALIGNING, "Live lane not armed"
    if flow_ready:
        return VERDICT_FIRE_PATH_OPEN, None
    return VERDICT_ARMED_WAITING, "No execution-ready candidate right now"
