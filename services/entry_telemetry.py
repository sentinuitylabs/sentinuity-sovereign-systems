"""
entry_telemetry.py - accepted + rejected candidate instrumentation.

SIGNED OFF (item A). This module is the fix for the finding that entry causality
cannot be reconstructed: every entry-funnel field in the historical database is
0.0% populated, in all three audited periods.

CONTRACT - this module may never change a trade decision:
  * every public call is wrapped so no exception escapes to the caller;
  * no network I/O, ever;
  * writes are single-statement, autocommit, WAL-compatible;
  * on any DB error the call is dropped silently and counted;
  * a bounded in-process buffer prevents unbounded growth if the DB is locked.

Usage in the entry path (both branches must be instrumented or the funnel is
still blind):

    from services.entry_telemetry import log_candidate, GateTrace

    trace = GateTrace(mint=mint, source="ingest_pipeline")
    trace.observe(confidence=conf, signal_age_sec=age, market_cap_usd=mc, ...)

    if not passes_momentum:
        trace.reject("MOMENTUM_BELOW_FLOOR", gate="momentum")
        continue
    ...
    trace.accept(gate="final")
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)

_MEL = timezone(timedelta(hours=10))  # Australia/Melbourne AEST

# Canonical rejection reasons. Free-text reasons defeat the whole point of the
# ledger, so anything not in this set is recorded as OTHER with the raw string
# preserved in deciding_gate.
CANONICAL_REJECTIONS = frozenset({
    "DUPLICATE_MINT",
    "DUPLICATE_IN_BATCH",
    "ALREADY_OPEN",
    "CONFIDENCE_BELOW_FLOOR",
    "MOMENTUM_BELOW_FLOOR",
    "ACCELERATION_BELOW_FLOOR",
    "SIGNAL_TOO_OLD",
    "PRICE_TOO_OLD",
    "ORACLE_STALE",
    "ORACLE_UNAVAILABLE",
    "MARKET_CAP_BELOW_MIN",
    "MARKET_CAP_ABOVE_MAX",
    "LIQUIDITY_BELOW_MIN",
    "VOLUME_BELOW_MIN",
    "PATTERN_NOT_ARMED",
    "HOUR_TERRITORY_BLOCKED",
    "MAX_POSITIONS_REACHED",
    "PACING_THROTTLED",
    "EXPOSURE_CAP_REACHED",
    "PRICE_UNAVAILABLE",
    "ROUTE_UNAVAILABLE",
    "BLOCKLIST",
    "OTHER",
})

_DROPPED = {"count": 0}
_LOCK = threading.Lock()

_FIELDS = (
    "ts_utc", "ts_mel", "mint_address", "token_name", "token_symbol",
    "candidate_source", "decision", "rejection_reason", "deciding_gate",
    "confidence", "confidence_parts", "signal_age_sec", "price_age_sec",
    "oracle_source", "oracle_age_sec", "market_cap_usd", "liquidity_usd",
    "volume_24h_usd", "momentum_pct", "acceleration_pct", "pattern_state",
    "hour_territory", "dedup_outcome", "open_positions", "max_positions",
    "snapshot_id", "engine_mode",
)

_INSERT = (
    "INSERT INTO entry_candidate_log (" + ",".join(_FIELDS) + ") VALUES ("
    + ",".join("?" * len(_FIELDS)) + ")"
)


def telemetry_enabled() -> bool:
    return str(os.environ.get("ENTRY_TELEMETRY_ENABLED", "1")).strip().lower() \
        not in ("0", "false", "off", "no")


def dropped_count() -> int:
    with _LOCK:
        return _DROPPED["count"]


def _bump_dropped() -> None:
    with _LOCK:
        _DROPPED["count"] += 1


def _mel_now(ts: float) -> str:
    try:
        return datetime.fromtimestamp(ts, tz=timezone.utc).astimezone(_MEL).isoformat()
    except Exception:
        return ""


def _canon(reason) -> str:
    if reason is None:
        return "OTHER"
    r = str(reason).strip().upper().replace(" ", "_").replace("-", "_")
    return r if r in CANONICAL_REJECTIONS else "OTHER"


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _txt(v, limit: int = 200):
    if v is None:
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def log_candidate(conn=None, **kw) -> bool:
    """
    Record one candidate decision. Never raises. Returns True if persisted.

    Pass an existing `conn` to join the caller's connection (preferred inside a
    loop that already holds one). Otherwise a short-lived connection is opened
    and closed immediately.
    """
    if not telemetry_enabled():
        return False
    try:
        ts = _num(kw.get("ts_utc")) or time.time()
        decision = str(kw.get("decision") or "REJECTED").strip().upper()
        if decision not in ("ACCEPTED", "REJECTED"):
            decision = "REJECTED"

        parts = kw.get("confidence_parts")
        if isinstance(parts, (dict, list)):
            try:
                parts = json.dumps(parts, separators=(",", ":"))[:800]
            except Exception:
                parts = None

        row = (
            ts,
            _mel_now(ts),
            _txt(kw.get("mint") or kw.get("mint_address"), 64) or "UNKNOWN_MINT",
            _txt(kw.get("token_name"), 64),
            _txt(kw.get("token_symbol"), 32),
            _txt(kw.get("candidate_source") or kw.get("source"), 64),
            decision,
            _canon(kw.get("rejection_reason")) if decision == "REJECTED" else None,
            _txt(kw.get("deciding_gate"), 120),
            _num(kw.get("confidence")),
            _txt(parts, 800),
            _num(kw.get("signal_age_sec")),
            _num(kw.get("price_age_sec")),
            _txt(kw.get("oracle_source"), 48),
            _num(kw.get("oracle_age_sec")),
            _num(kw.get("market_cap_usd")),
            _num(kw.get("liquidity_usd")),
            _num(kw.get("volume_24h_usd")),
            _num(kw.get("momentum_pct")),
            _num(kw.get("acceleration_pct")),
            _txt(kw.get("pattern_state"), 48),
            _txt(kw.get("hour_territory"), 48),
            _txt(kw.get("dedup_outcome"), 48),
            _num(kw.get("open_positions")),
            _num(kw.get("max_positions")),
            _txt(kw.get("snapshot_id"), 64),
            _txt(kw.get("engine_mode"), 32),
        )
    except Exception as exc:  # defensive: malformed kwargs must not reach caller
        log.debug("entry_telemetry payload build failed: %s", exc)
        _bump_dropped()
        return False

    try:
        if conn is not None:
            conn.execute(_INSERT, row)
            return True
        from core.schema import get_connection
        with get_connection() as c:
            c.execute(_INSERT, row)
        return True
    except Exception as exc:
        log.debug("entry_telemetry write dropped: %s", exc)
        _bump_dropped()
        return False


class GateTrace:
    """
    Accumulates observed gate evidence for one candidate, then records exactly
    one terminal row. Attribute assignment is free-form so new gates do not
    require a schema change - unknown keys are ignored at write time.
    """

    __slots__ = ("_d", "_done", "_conn")

    def __init__(self, mint=None, source=None, conn=None, **kw):
        self._d = {"mint": mint, "candidate_source": source}
        self._d.update(kw)
        self._done = False
        self._conn = conn

    def observe(self, **kw) -> "GateTrace":
        try:
            self._d.update(kw)
        except Exception:
            pass
        return self

    def reject(self, reason, gate=None, **kw) -> bool:
        if self._done:
            return False
        self._done = True
        self._d.update(kw)
        self._d.update({"decision": "REJECTED", "rejection_reason": reason,
                        "deciding_gate": gate or reason})
        return log_candidate(self._conn, **self._d)

    def accept(self, gate=None, **kw) -> bool:
        if self._done:
            return False
        self._done = True
        self._d.update(kw)
        self._d.update({"decision": "ACCEPTED", "deciding_gate": gate or "final"})
        return log_candidate(self._conn, **self._d)

    def __enter__(self):
        return self

    def __exit__(self, et, ev, tb):
        # An un-terminated trace means a code path escaped without a decision.
        # Record it rather than lose the candidate.
        if not self._done:
            self.reject("OTHER", gate="unterminated_trace")
        return False


def prune(conn=None, retention_days: float = 30.0) -> int:
    """Bounded retention. Returns rows deleted. Never raises."""
    try:
        cutoff = time.time() - float(retention_days) * 86400.0
        sql = "DELETE FROM entry_candidate_log WHERE ts_utc < ?"
        if conn is not None:
            return int(conn.execute(sql, (cutoff,)).rowcount or 0)
        from core.schema import get_connection
        with get_connection() as c:
            return int(c.execute(sql, (cutoff,)).rowcount or 0)
    except Exception as exc:
        log.debug("entry_telemetry prune skipped: %s", exc)
        return 0


def funnel_summary(conn=None, hours: float = 24.0) -> dict:
    """Read-only funnel rollup for validation and the UI."""
    out = {"window_hours": hours, "total": 0, "accepted": 0, "rejected": 0,
           "pass_rate_pct": 0.0, "by_reason": []}
    try:
        cutoff = time.time() - float(hours) * 3600.0
        own = conn is None
        if own:
            from core.schema import get_connection
            conn = get_connection()
        try:
            r = conn.execute(
                "SELECT decision, COUNT(*) FROM entry_candidate_log "
                "WHERE ts_utc >= ? GROUP BY decision", (cutoff,)
            ).fetchall()
            for dec, n in r:
                if str(dec).upper() == "ACCEPTED":
                    out["accepted"] = int(n)
                else:
                    out["rejected"] += int(n)
            out["total"] = out["accepted"] + out["rejected"]
            if out["total"]:
                out["pass_rate_pct"] = round(100.0 * out["accepted"] / out["total"], 2)
            out["by_reason"] = [
                {"reason": str(a), "n": int(b)} for a, b in conn.execute(
                    "SELECT rejection_reason, COUNT(*) FROM entry_candidate_log "
                    "WHERE ts_utc >= ? AND decision='REJECTED' "
                    "GROUP BY rejection_reason ORDER BY 2 DESC", (cutoff,)
                ).fetchall()
            ]
        finally:
            if own:
                conn.close()
    except Exception as exc:
        log.debug("funnel_summary failed: %s", exc)
    return out
