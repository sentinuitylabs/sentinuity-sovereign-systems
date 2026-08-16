"""
fill_provenance.py - fill truth on EVERY close, and raw/capped PnL separation.

SIGNED OFF (items B and C).

Two defects this closes, both established by the round-2 audit:

  1. fill_class is confounded with outcome. Integrity-bearing rows are 91.0%
     winners in JUL25_27 and 94.7% winners in CURRENT, because provenance is
     only written on paths winners take. Any "observed-only" comparison built
     on that classification is a winners-only subset and cannot measure edge.
     Fix: record_close() is called on every close - win, loss and flat.

  2. Recorded loss replaces raw loss. 140 CURRENT rows recorded exactly -$1.00
     each (-$140.00 total) against a raw -$668.60; $528.60 of realised loss is
     absent from the ledger. One row books a raw -51% as -4%.
     Fix: raw economic PnL is primary and always stored; the capped control
     value is stored beside it, never on top of it.

CONTRACT:
  * raw_realized_pnl_usd is the primary performance truth;
  * capped_realized_pnl_usd exists only for risk-model/control consumers;
  * historical rows are never rewritten by this module;
  * live chain-fill truth remains authoritative - if a chain fill is supplied
    it is recorded as ACTUAL_FILL and no model may override it;
  * never raises into the caller.
"""
from __future__ import annotations

import logging
import time

log = logging.getLogger(__name__)

# Ordered by trust. ACTUAL_FILL is chain truth and outranks everything.
FILL_MODELS = (
    "ACTUAL_FILL",        # on-chain confirmed fill
    "EXECUTABLE_QUOTE",   # routable quote at exit time
    "OBSERVED_MARK",      # observed mark, no execution guarantee
    "MODELLED_FLOOR",     # synthetic: protective floor assumed to fill
    "CAPPED_STOP_FLOOR",  # synthetic: loss clamped by risk model
    "UNKNOWN",
)

TRUSTED_MODELS = frozenset({"ACTUAL_FILL", "EXECUTABLE_QUOTE", "OBSERVED_MARK"})
SYNTHETIC_MODELS = frozenset({"MODELLED_FLOOR", "CAPPED_STOP_FLOOR"})

_FIELDS = (
    "position_id", "ts_utc", "fill_model", "observed_exit_price",
    "modelled_exit_price", "raw_realized_pnl_usd", "recorded_pnl_usd",
    "raw_pnl_pct", "recorded_pnl_pct", "cap_applied", "cap_floor_value",
    "cap_reason", "quote_source", "chain_source", "unresolved", "exit_reason",
    "outcome_class", "ab_arm",
)

_INSERT = (
    "INSERT INTO fill_provenance (" + ",".join(_FIELDS) + ") VALUES ("
    + ",".join("?" * len(_FIELDS)) + ")"
)


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _txt(v, limit=200):
    if v is None:
        return None
    s = str(v).strip()
    return s[:limit] if s else None


def classify_outcome(raw_pnl) -> str:
    v = _num(raw_pnl)
    if v is None:
        return "UNRESOLVED"
    if v > 0:
        return "WIN"
    if v < 0:
        return "LOSS"
    return "FLAT"


def normalise_model(model, chain_source=None) -> str:
    """Chain truth wins. Otherwise map to a known model or UNKNOWN."""
    if _txt(chain_source):
        return "ACTUAL_FILL"
    m = str(model or "").strip().upper()
    return m if m in FILL_MODELS else "UNKNOWN"


def record_close(conn=None, **kw) -> bool:
    """
    Record provenance for one close. Call on EVERY close regardless of outcome.
    Never raises. Returns True if persisted.

    Required: position_id, raw_realized_pnl_usd (or observed price + entry).
    """
    try:
        pid = kw.get("position_id")
        if pid is None:
            return False
        pid = int(pid)

        raw_usd = _num(kw.get("raw_realized_pnl_usd"))
        rec_usd = _num(kw.get("recorded_pnl_usd"))
        # If only one is supplied they are the same thing - no cap was applied.
        if raw_usd is None and rec_usd is not None:
            raw_usd = rec_usd
        if rec_usd is None and raw_usd is not None:
            rec_usd = raw_usd

        cap_applied = kw.get("cap_applied")
        if cap_applied is None:
            cap_applied = int(
                raw_usd is not None and rec_usd is not None
                and abs(raw_usd - rec_usd) > 1e-9
            )
        cap_applied = 1 if cap_applied else 0

        model = normalise_model(kw.get("fill_model"), kw.get("chain_source"))

        row = (
            pid,
            _num(kw.get("ts_utc")) or time.time(),
            model,
            _num(kw.get("observed_exit_price")),
            _num(kw.get("modelled_exit_price")),
            raw_usd,
            rec_usd,
            _num(kw.get("raw_pnl_pct")),
            _num(kw.get("recorded_pnl_pct")),
            cap_applied,
            _num(kw.get("cap_floor_value")),
            _txt(kw.get("cap_reason"), 120),
            _txt(kw.get("quote_source"), 48),
            _txt(kw.get("chain_source"), 64),
            1 if kw.get("unresolved") else 0,
            _txt(kw.get("exit_reason"), 240),
            classify_outcome(raw_usd),
            _txt(kw.get("ab_arm"), 48),
        )
    except Exception as exc:
        log.debug("fill_provenance payload build failed: %s", exc)
        return False

    try:
        if conn is not None:
            _write(conn, row, pid, raw_usd, rec_usd, cap_applied, kw)
            return True
        from core.schema import get_connection
        with get_connection() as c:
            _write(c, row, pid, raw_usd, rec_usd, cap_applied, kw)
        return True
    except Exception as exc:
        log.debug("fill_provenance write dropped pos=%s: %s", kw.get("position_id"), exc)
        return False


def _write(conn, row, pid, raw_usd, rec_usd, cap_applied, kw) -> None:
    conn.execute(_INSERT, row)
    # Mirror onto paper_positions so existing queries see truthful values
    # without joining. Additive columns only; realized_pnl_usd is NOT touched,
    # so no historical consumer changes behaviour.
    try:
        conn.execute(
            "UPDATE paper_positions SET raw_realized_pnl_usd=?, raw_realized_pnl_pct=?, "
            "capped_realized_pnl_usd=?, cap_applied=?, cap_reason=?, ab_arm=? WHERE id=?",
            (raw_usd, _num(kw.get("raw_pnl_pct")),
             rec_usd if cap_applied else None,
             cap_applied, _txt(kw.get("cap_reason"), 120),
             _txt(kw.get("ab_arm"), 48), pid),
        )
    except Exception as exc:
        log.debug("fill_provenance mirror skipped pos=%s: %s", pid, exc)


def coverage(conn=None, hours: float = 48.0) -> dict:
    """
    Validation metric for defect 1. Provenance coverage must be independent of
    outcome. If win rate inside the covered set differs sharply from the win
    rate of all closes, provenance is still outcome-confounded.
    """
    out = {"window_hours": hours, "closes": 0, "covered": 0, "coverage_pct": 0.0,
           "win_rate_all_pct": None, "win_rate_covered_pct": None,
           "confounded": None, "raw_total_usd": None, "recorded_total_usd": None}
    try:
        cutoff = time.time() - float(hours) * 3600.0
        own = conn is None
        if own:
            from core.schema import get_connection
            conn = get_connection()
        try:
            n, wins = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN realized_pnl_usd>0 THEN 1 ELSE 0 END) "
                "FROM paper_positions WHERE status='CLOSED' AND closed_at >= ?",
                (cutoff,)
            ).fetchone()
            out["closes"] = int(n or 0)
            if out["closes"]:
                out["win_rate_all_pct"] = round(100.0 * (wins or 0) / out["closes"], 2)

            c, cw, rawt, rect = conn.execute(
                "SELECT COUNT(*), "
                "SUM(CASE WHEN raw_realized_pnl_usd>0 THEN 1 ELSE 0 END), "
                "SUM(raw_realized_pnl_usd), SUM(recorded_pnl_usd) "
                "FROM fill_provenance WHERE ts_utc >= ?", (cutoff,)
            ).fetchone()
            out["covered"] = int(c or 0)
            out["raw_total_usd"] = _num(rawt)
            out["recorded_total_usd"] = _num(rect)
            if out["covered"]:
                out["win_rate_covered_pct"] = round(100.0 * (cw or 0) / out["covered"], 2)
            if out["closes"]:
                out["coverage_pct"] = round(100.0 * out["covered"] / out["closes"], 2)
            if out["win_rate_all_pct"] is not None and out["win_rate_covered_pct"] is not None:
                out["confounded"] = abs(
                    out["win_rate_covered_pct"] - out["win_rate_all_pct"]
                ) > 15.0
        finally:
            if own:
                conn.close()
    except Exception as exc:
        log.debug("fill_provenance coverage failed: %s", exc)
    return out
