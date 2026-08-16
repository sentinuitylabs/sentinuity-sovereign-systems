#!/usr/bin/env python3
"""
SENTINUITY — BOUNDED LIVE CANARY GOVERNOR

Supplies the canary-contract limits that do not currently exist anywhere in the
tree. Verified absent by search across the supplied files:
  * no 24-hour attempt cap  (no per_day / attempts_24h / MAX_LIVE_ATTEMPTS)
  * no hard executable-loss ceiling in live_trading.py
  * no consecutive-loss disarm

What DOES already exist and is deliberately not duplicated here:
  * conjunctive live arming (execution_engine._live_lane_armed, 5 flags)
  * half-size staging until 3 clean canaries (pattern_live_arming._live_size_stage)
  * LIVE_DAILY_LOSS_LIMIT_USD (execution_engine.py:3563)
  * bounded sell slippage, 2 tiers, 3000 bps ceiling (live_trading.py:121-135)
  * executability before live verdict (execution_engine.py:3437 -> verdict)

This module only ANSWERS "may another canary fire?". It never arms anything,
never sizes anything, and returns a refusal by default.
"""
from __future__ import annotations

import os
import time
import uuid
from typing import Any, Dict, Optional

SERVICE = "canary_governor"
LEDGER_TABLE = "canary_attempt_ledger"

MAX_ATTEMPTS_24H = int(os.environ.get("CANARY_MAX_ATTEMPTS_24H", "2"))
MAX_CONSECUTIVE_LOSSES = int(os.environ.get("CANARY_MAX_CONSECUTIVE_LOSSES", "2"))
# Hard ceiling on a single realisable loss, as a percentage of position size.
# Sell slippage is bounded at 3000 bps and impact adds to it, so a stop can
# realise far worse than its intended -4%. Beyond this the canary must not fire.
MAX_EXECUTABLE_LOSS_PCT = float(os.environ.get("CANARY_MAX_EXECUTABLE_LOSS_PCT", "25.0"))

# Attempt lifecycle (directive section 4).
ELIGIBLE = "ELIGIBLE"
RESERVED = "RESERVED"
SUBMITTED = "SUBMITTED"
SETTLED_WIN = "SETTLED_WIN"
SETTLED_LOSS = "SETTLED_LOSS"
FAILED_UNRESOLVED = "FAILED_UNRESOLVED"
RECON_CONTRADICTION = "RECONCILIATION_CONTRADICTION"
DISARMED = "DISARMED"

OPEN_STATES = frozenset({RESERVED, SUBMITTED})
SETTLED_STATES = frozenset({SETTLED_WIN, SETTLED_LOSS, FAILED_UNRESOLVED,
                            RECON_CONTRADICTION})

# A reservation that never progresses is terminalised as FAILED_UNRESOLVED.
# It still counts toward the 24h cap: a crashed attempt consumed a real chance
# to move capital and must not be silently forgiven.
RESERVATION_TIMEOUT_SEC = float(os.environ.get("CANARY_RESERVATION_TIMEOUT_SEC", "600"))

_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    attempted_at REAL NOT NULL,
    position_id INTEGER,
    mint_address TEXT,
    size_usd REAL,
    state TEXT NOT NULL DEFAULT 'RESERVED',
    outcome TEXT,
    realised_pnl_usd REAL,
    settled INTEGER DEFAULT 0,
    reconciliation_ok INTEGER,
    state_updated_at REAL,
    reservation_token TEXT UNIQUE,
    note TEXT
);
"""


def ensure_schema(conn) -> bool:
    try:
        conn.execute(_DDL)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_cal_ts "
                     f"ON {LEDGER_TABLE}(attempted_at)")
        return True
    except Exception:
        return False


def _expire_stale_reservations(conn) -> int:
    """A crashed reservation is terminalised, never silently ignored."""
    try:
        cut = time.time() - RESERVATION_TIMEOUT_SEC
        cur = conn.execute(
            f"UPDATE {LEDGER_TABLE} SET state=?, settled=1, outcome='UNRESOLVED', "
            "reconciliation_ok=0, state_updated_at=?, "
            "note=COALESCE(note,'')||' [expired reservation]' "
            "WHERE state IN ('RESERVED','SUBMITTED') "
            "AND COALESCE(state_updated_at, attempted_at) < ?",
            (FAILED_UNRESOLVED, time.time(), cut))
        return cur.rowcount or 0
    except Exception:
        return 0


def _readiness_metric_summary(stats: Any) -> str:
    """Compact measured-vs-threshold summary for the terminal refusal reason.

    SENTINUITY_EXIT_INFRA_20260805. Reporting only: reads thresholds, never
    sets or compares them for a decision. Threshold constants are imported so
    this string can never drift from the gate that produced it.
    """
    if not isinstance(stats, dict) or not stats:
        return ""
    try:
        from services import stop_realisability as _SR
        t_samples = int(getattr(_SR, "MIN_SAMPLES_ABSOLUTE", 50))
        t_cov = float(getattr(_SR, "MIN_QUOTE_COVERAGE_PCT", 95.0))
        t_med = float(getattr(_SR, "MAX_MEDIAN_STOP_PCT", -8.0))
        t_p90 = float(getattr(_SR, "MAX_P90_STOP_PCT", -15.0))
        t_worst = float(getattr(_SR, "MAX_WORST_STOP_PCT", -25.0))
        t_lat_med = float(getattr(_SR, "MAX_MEDIAN_TRIGGER_TO_QUOTE_SEC", 1.5))
        t_lat_p90 = float(getattr(_SR, "MAX_P90_TRIGGER_TO_QUOTE_SEC", 3.0))
    except Exception:
        t_samples, t_cov = 50, 95.0
        t_med, t_p90, t_worst = -8.0, -15.0, -25.0
        t_lat_med, t_lat_p90 = 1.5, 3.0

    def _num(key):
        v = stats.get(key)
        try:
            return None if v is None else float(v)
        except Exception:
            return None

    parts = []
    n = _num("n")
    if n is not None:
        parts.append(f"samples={int(n)}{'<' if n < t_samples else '>='}{t_samples}")
    for key, thr, op_bad in (("quote_coverage_pct", t_cov, "lt"),
                             ("median_executable_pct", t_med, "lt"),
                             ("p90_executable_pct", t_p90, "lt"),
                             ("worst_executable_pct", t_worst, "lt"),
                             ("median_trigger_to_quote_sec", t_lat_med, "gt"),
                             ("p90_trigger_to_quote_sec", t_lat_p90, "gt")):
        v = _num(key)
        if v is None:
            continue
        label = {"quote_coverage_pct": "coverage",
                 "median_executable_pct": "median",
                 "p90_executable_pct": "p90",
                 "worst_executable_pct": "worst",
                 "median_trigger_to_quote_sec": "median_latency",
                 "p90_trigger_to_quote_sec": "p90_latency"}[key]
        if op_bad == "lt":
            sym = "<" if v < thr else ">="
        else:
            sym = ">" if v > thr else "<="
        parts.append(f"{label}={v:.2f}{sym}{thr:g}")
    return ";".join(parts)


def may_fire_canary(conn, *,
                    projected_executable_loss_pct: float = None,
                    lane_armed: bool = False,
                    mode_b_pass: bool = False,
                    pattern_pass: bool = False,
                    executability_ok: bool = False,
                    paper_enabled: bool = False,
                    readiness_status: str = None,
                    readiness_blocking: Any = None,
                    readiness_stats: Any = None) -> Dict[str, Any]:
    """
    Returns {"allowed": bool, "reason": str, ...}.

    FAIL-CLOSED. Every argument defaults to the refusing value, so a caller
    that forgets to pass a gate gets a refusal, not a permission.

    This function sits DOWNSTREAM of every existing gate and UPSTREAM of live
    submission. It never arms the lane and never sets a flag; it can only
    withhold permission the existing conjunctive arming has already granted.
    """
    out = {"allowed": False, "reason": "not_evaluated",
           "attempts_24h": None, "consecutive_losses": None,
           "readiness_status": readiness_status}
    try:
        ensure_schema(conn)
        _expire_stale_reservations(conn)
        now = time.time()

        # 1. Existing gates must already have passed. The governor never
        #    substitutes for them.
        for label, ok in (("lane_not_armed", lane_armed),
                          ("mode_b_not_passed", mode_b_pass),
                          ("pattern_authority_absent", pattern_pass),
                          ("executability_not_confirmed", executability_ok),
                          ("paper_lane_not_running", paper_enabled)):
            if not ok:
                out["reason"] = label
                return out

        # 2. Stop-realisability evidence must have reached READY.
        if readiness_status is None:
            try:
                from services.stop_realisability import readiness, STATUS_READY
                r = readiness(conn)
                readiness_status = r["status"]
                out["readiness_status"] = readiness_status
                out["readiness_blocking"] = r.get("blocking", [])[:6]
                out["readiness_stats"] = r.get("stats") or {}
            except Exception:
                out["reason"] = "stop_readiness_unavailable"
                return out
        else:
            # SENTINUITY_EXIT_INFRA_20260805: a caller that has already computed
            # readiness passes the status here, which previously skipped the
            # block above and silently discarded the blocking list -- the exact
            # reason pos=4035 recorded STOP_REALISABILITY_FAILED with no numbers
            # while the caller held them. Detail now travels with the status.
            out["readiness_blocking"] = [str(x) for x in (readiness_blocking or [])][:6]
            out["readiness_stats"] = readiness_stats if isinstance(readiness_stats, dict) else {}
        if str(readiness_status) != "STOP_REALISABILITY_PASSED":
            blockers = [str(x) for x in (out.get("readiness_blocking") or []) if str(x)]
            blocker_text = "; ".join(blockers[:6])
            metrics = _readiness_metric_summary(out.get("readiness_stats"))
            out["reason"] = (
                f"stop_readiness={readiness_status}"
                + (f":{metrics}" if metrics else "")
                + (f" | blockers={blocker_text}" if blocker_text else "")
            )
            return out

        # 3. Parity must be complete: no orphan live handoffs outstanding.
        try:
            from services.paper_live_parity import coverage as parity_coverage
            pc = parity_coverage(conn)
            out["parity_terminal_coverage_pct"] = pc["terminal_coverage_pct"]
            if pc["orphan_fire_path_open"] > 0:
                out["reason"] = (f"orphan_fire_path_open="
                                 f"{pc['orphan_fire_path_open']}")
                return out
            if pc["n"] and pc["terminal_coverage_pct"] < 99.0:
                out["reason"] = (f"parity_terminal_coverage="
                                 f"{pc['terminal_coverage_pct']:.1f}% < 99%")
                return out
        except Exception:
            out["reason"] = "parity_unavailable"
            return out

        # 4. Rolling 24h cap. RESERVED attempts count: a reservation consumed a
        #    real opportunity to move capital.
        n = int(conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} WHERE attempted_at >= ?",
            (now - 86400,)).fetchone()[0] or 0)
        out["attempts_24h"] = n
        if n >= MAX_ATTEMPTS_24H:
            out["reason"] = f"attempt_cap_24h_reached ({n}/{MAX_ATTEMPTS_24H})"
            return out

        # 5. No unresolved prior attempt.
        open_n = int(conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} WHERE state IN ('RESERVED','SUBMITTED')"
        ).fetchone()[0] or 0)
        if open_n:
            out["reason"] = f"prior_attempt_unresolved ({open_n})"
            return out
        unres = int(conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} WHERE state=?",
            (FAILED_UNRESOLVED,)).fetchone()[0] or 0)
        if unres:
            out["reason"] = f"unresolved_sell_outstanding ({unres})"
            return out

        # 6. Consecutive settled losses.
        rows = conn.execute(
            f"SELECT state FROM {LEDGER_TABLE} WHERE settled=1 "
            "ORDER BY attempted_at DESC LIMIT ?", (MAX_CONSECUTIVE_LOSSES,)).fetchall()
        losses = 0
        for r in rows:
            if str(r[0] or "") == SETTLED_LOSS:
                losses += 1
            else:
                break
        out["consecutive_losses"] = losses
        if losses >= MAX_CONSECUTIVE_LOSSES:
            out["reason"] = f"disarmed_consecutive_losses ({losses})"
            return out

        # 7. Reconciliation contradiction is permanently disarming.
        bad = int(conn.execute(
            f"SELECT COUNT(*) FROM {LEDGER_TABLE} "
            "WHERE state=? OR (settled=1 AND reconciliation_ok=0)",
            (RECON_CONTRADICTION,)).fetchone()[0] or 0)
        if bad:
            out["reason"] = f"disarmed_reconciliation_contradiction ({bad})"
            return out

        # 8. Executable-loss ceiling for this specific candidate.
        if projected_executable_loss_pct is None:
            out["reason"] = "no_stop_realisability_evidence"
            return out
        try:
            p = abs(float(projected_executable_loss_pct))
        except (TypeError, ValueError):
            out["reason"] = "projected_loss_unparseable"
            return out
        if p > MAX_EXECUTABLE_LOSS_PCT:
            out["reason"] = (f"projected_executable_loss_exceeds_ceiling "
                             f"({p:.1f}% > {MAX_EXECUTABLE_LOSS_PCT:.1f}%)")
            return out

        out["allowed"] = True
        out["reason"] = "within_canary_contract"
        return out
    except Exception as exc:
        out["reason"] = f"governor_error:{type(exc).__name__}"
        return out


def reserve_attempt(conn, *, position_id: int, mint: str, size_usd: float,
                    note: str = "") -> Optional[str]:
    """
    Atomically reserve the single canary slot.

    Atomicity comes from an IMMEDIATE transaction plus a UNIQUE reservation
    token and a re-check of the open/cap conditions inside the same
    transaction. Two concurrent callers cannot both reserve: the second either
    loses the UNIQUE race or observes the first row and refuses.

    Returns the reservation token, or None if the slot could not be taken.
    """
    try:
        ensure_schema(conn)
        token = f"canary_{int(time.time()*1000)}_{uuid.uuid4().hex[:12]}"
        now = time.time()
        try:
            conn.execute("BEGIN IMMEDIATE")
        except Exception:
            pass
        try:
            _expire_stale_reservations(conn)
            open_n = int(conn.execute(
                f"SELECT COUNT(*) FROM {LEDGER_TABLE} "
                "WHERE state IN ('RESERVED','SUBMITTED')").fetchone()[0] or 0)
            if open_n:
                conn.rollback()
                return None
            n24 = int(conn.execute(
                f"SELECT COUNT(*) FROM {LEDGER_TABLE} WHERE attempted_at >= ?",
                (now - 86400,)).fetchone()[0] or 0)
            if n24 >= MAX_ATTEMPTS_24H:
                conn.rollback()
                return None
            conn.execute(
                f"INSERT INTO {LEDGER_TABLE} (attempted_at, position_id, "
                "mint_address, size_usd, state, settled, state_updated_at, "
                "reservation_token, note) VALUES (?,?,?,?,?,0,?,?,?)",
                (now, int(position_id), str(mint)[:64], float(size_usd),
                 RESERVED, now, token, str(note)[:200]))
            conn.commit()
            return token
        except Exception:
            try:
                conn.rollback()
            except Exception:
                pass
            return None
    except Exception:
        return None


def mark_submitted(conn, *, reservation_token: str) -> bool:
    try:
        conn.execute(
            f"UPDATE {LEDGER_TABLE} SET state=?, state_updated_at=? "
            "WHERE reservation_token=? AND state=?",
            (SUBMITTED, time.time(), reservation_token, RESERVED))
        return True
    except Exception:
        return False



def mark_failed_unresolved(conn, *, reservation_token: str = None, position_id: int = None, note: str = "") -> bool:
    """Terminalise a reserved/submitted attempt that did not settle cleanly."""
    try:
        ensure_schema(conn)
        where, args = None, []
        if reservation_token:
            where, args = "reservation_token=?", [str(reservation_token)]
        elif position_id is not None:
            where, args = "position_id=? AND settled=0", [int(position_id)]
        else:
            return False
        conn.execute(
            f"UPDATE {LEDGER_TABLE} SET settled=1, outcome='UNRESOLVED', state=?, "
            "reconciliation_ok=0, state_updated_at=?, note=COALESCE(note,'')||? WHERE " + where,
            [FAILED_UNRESOLVED, time.time(), (" " + str(note)[:180]) if note else ""] + args)
        return True
    except Exception:
        return False


def mark_reconciliation_contradiction(conn, *, position_id: int, note: str = "") -> bool:
    """Permanently disarm after a live settlement/reconciliation contradiction."""
    try:
        ensure_schema(conn)
        conn.execute(
            f"UPDATE {LEDGER_TABLE} SET settled=1, outcome='CONTRADICTION', state=?, "
            "reconciliation_ok=0, state_updated_at=?, note=COALESCE(note,'')||? "
            "WHERE position_id=? AND settled=0",
            (RECON_CONTRADICTION, time.time(), (" " + str(note)[:180]) if note else "", int(position_id)))
        return True
    except Exception:
        return False


def record_attempt(conn, *, position_id: int, mint: str, size_usd: float,
                   note: str = "") -> bool:
    """Backwards-compatible wrapper around reserve_attempt()."""
    return reserve_attempt(conn, position_id=position_id, mint=mint,
                           size_usd=size_usd, note=note) is not None


def settle_attempt(conn, *, position_id: int, realised_pnl_usd: float,
                   reconciliation_ok: bool) -> bool:
    try:
        if not reconciliation_ok:
            state, outcome = RECON_CONTRADICTION, "CONTRADICTION"
        elif float(realised_pnl_usd) > 0:
            state, outcome = SETTLED_WIN, "WIN"
        else:
            state, outcome = SETTLED_LOSS, "LOSS"
        conn.execute(
            f"UPDATE {LEDGER_TABLE} SET settled=1, realised_pnl_usd=?, "
            "outcome=?, state=?, reconciliation_ok=?, state_updated_at=? "
            "WHERE position_id=? AND settled=0",
            (float(realised_pnl_usd), outcome, state,
             1 if reconciliation_ok else 0, time.time(), int(position_id)))
        return True
    except Exception:
        return False
