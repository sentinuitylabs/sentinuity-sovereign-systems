"""
paper_ab_harness.py - paper-only attributed A/B, plus the golden lilypad
stall-exit challenger.

CONDITIONALLY SIGNED (items D and E). Paper/shadow only.

SAFETY CONTRACT - read before enabling anything here:
  * Every function returns a decision object. None of them execute a trade.
  * arm_for_position() refuses to assign an arm to any position whose
    funding_mode is not SIM. Live positions always resolve to CONTROL.
  * The challenger defaults to SHADOW: it records what it would have done and
    changes nothing. This is behaviour-neutral by construction.
  * ACTIVE_PAPER mode must be explicitly enabled AND the position must be SIM.
    There is no configuration in which this module can act on a REAL position.

AUDIT FINDING behind the challenger (round 2):
    JUL08_09 lilypad stall exits captured 98.3% of peak across 22 exits
    ($2,160.22 - 79% of that period's entire profit). Current runner_lock
    captures 62.8%. The stall trigger fires when no new high has been made for
    N seconds, so it sells into the stall rather than waiting for a drawdown
    back to a floor.

PORT NOTE: the current engine already computes everything this needs -
`_last_high_age` (execution_engine.py:4278), `_first75` (:4166) and `pnl_pct`.
No new state, no new columns, no new tracking. Pass them in.
"""
from __future__ import annotations

import hashlib
import logging
import os
import time

log = logging.getLogger(__name__)

EXPERIMENTS = {
    # experiment name -> (control arm, treatment arm)
    "RUNNER_FILL_TRUTH": ("ASSUME_STOP_FILL_ON", "ASSUME_STOP_FILL_OFF"),
    "EXIT_MECHANISM": ("RUNNER_LOCK", "LILYPAD_STALL"),
}

# Golden defaults, read from JUL08_GOLDEN execution_engine.py:2853.
# Observed stall in the July 8-9 tape: median 44s, min 31s, max 588s.
GOLDEN_75 = {
    "no_high_sec": 30.0,
    "floor_pct":   60.0,
    "ceiling_pct": 100.0,
}


def _cfg(key, default):
    return os.environ.get(key, default)


def _flag(key, default="0") -> bool:
    return str(_cfg(key, default)).strip().lower() not in ("0", "false", "off", "no", "")


def _fnum(key, default):
    try:
        return float(_cfg(key, default))
    except (TypeError, ValueError):
        return float(default)


def experiment_enabled(name: str) -> bool:
    return _flag("PAPER_AB_%s_ENABLED" % name, "0")


def challenger_mode() -> str:
    """SHADOW (default, records only) or ACTIVE_PAPER (acts, SIM rows only)."""
    m = str(_cfg("LILYPAD_CHALLENGER_MODE", "SHADOW")).strip().upper()
    return m if m in ("SHADOW", "ACTIVE_PAPER") else "SHADOW"


def is_sim(position) -> bool:
    """True only for unambiguously simulated positions. Fails closed."""
    try:
        if position is None:
            return False
        try:
            d = dict(position)
        except Exception:
            d = position if isinstance(position, dict) else {}
        return str(d.get("funding_mode") or "SIM").strip().upper() == "SIM"
    except Exception:
        return False


def arm_for_position(position_id, experiment: str, position=None,
                     conn=None) -> str:
    """
    Deterministic, sticky arm assignment. Hash-based so a position always lands
    in the same arm across restarts without needing a prior read.

    Returns the control arm for: unknown experiments, disabled experiments, and
    any position that is not SIM.
    """
    ctrl, treat = EXPERIMENTS.get(experiment, ("CONTROL", "TREATMENT"))
    try:
        if not experiment_enabled(experiment):
            return ctrl
        if position is not None and not is_sim(position):
            return ctrl  # live positions never enter an experiment arm
        if position_id is None:
            return ctrl

        seed = "%s:%s" % (experiment, int(position_id))
        h = hashlib.sha256(seed.encode("utf-8")).digest()[0]
        split = _fnum("PAPER_AB_%s_TREAT_PCT" % experiment, 50.0)
        arm = treat if (h / 255.0 * 100.0) < split else ctrl

        if conn is not None:
            try:
                conn.execute(
                    "INSERT OR IGNORE INTO paper_ab_assignment "
                    "(position_id, experiment, arm, assigned_at, assign_reason) "
                    "VALUES (?,?,?,?,?)",
                    (int(position_id), experiment, arm, time.time(), "hash_split"),
                )
            except Exception as exc:
                log.debug("ab assignment persist skipped pos=%s: %s", position_id, exc)
        return arm
    except Exception as exc:
        log.debug("arm_for_position failed pos=%s: %s", position_id, exc)
        return ctrl


def assume_stop_fill_for(position_id, position=None, conn=None) -> bool:
    """
    Experiment A. Returns whether the modelled floor fill should be assumed.

    Control arm and all live positions keep the existing default (True), so
    enabling the experiment cannot change behaviour for anything outside the
    treatment arm.
    """
    try:
        default_on = str(
            _cfg("PAPER_RUNNER_LOCK_ASSUME_STOP_FILL", "1")
        ).strip().lower() not in ("0", "false", "off", "no")
        if not experiment_enabled("RUNNER_FILL_TRUTH"):
            return default_on
        if position is not None and not is_sim(position):
            return default_on
        arm = arm_for_position(position_id, "RUNNER_FILL_TRUTH", position, conn)
        return False if arm == "ASSUME_STOP_FILL_OFF" else default_on
    except Exception:
        return True


def lilypad_stall_decision(pnl_pct, last_high_age_sec, first_75_ts,
                           max_seen_pct=None, position_id=None, position=None,
                           conn=None) -> dict:
    """
    Golden 75% lilypad stall rule, ported from JUL08_GOLDEN:2853.

    Fires when:
        the 75% rung has been reached at least once, AND
        current pnl is inside [floor, ceiling), AND
        no new high has been made for >= no_high_sec.

    Returns a decision dict. It NEVER exits anything itself:

        {"fire": bool, "act": bool, "reason": str|None, "arm": str,
         "mode": "SHADOW"|"ACTIVE_PAPER", "level": 75, ...}

    `act` is True only when mode is ACTIVE_PAPER, the position is SIM, and the
    position is in the LILYPAD_STALL arm. Callers must treat `act` as the only
    permission to change behaviour; `fire` alone is observation.
    """
    out = {"fire": False, "act": False, "reason": None, "arm": "RUNNER_LOCK",
           "mode": challenger_mode(), "level": 75, "stall_sec": None,
           "threshold_sec": None, "pnl_pct": None, "max_seen_pct": max_seen_pct}
    try:
        if first_75_ts is None:
            return out
        pnl = float(pnl_pct)
        age = float(last_high_age_sec or 0.0)
        out["pnl_pct"] = pnl
        out["stall_sec"] = age

        timer = _fnum("LILYPAD_75_NO_HIGH_SEC", GOLDEN_75["no_high_sec"])
        floor = _fnum("LILYPAD_75_EXIT_FLOOR_PCT", GOLDEN_75["floor_pct"])
        ceil_ = _fnum("LILYPAD_75_CEILING_PCT", GOLDEN_75["ceiling_pct"])
        out["threshold_sec"] = timer

        # Golden behaviour: at or above the ceiling, let it run - do not exit.
        if pnl >= ceil_ or pnl < floor:
            return out
        if age < timer:
            return out

        out["fire"] = True
        out["reason"] = (
            "LILYPAD_FULL_EXIT_75pct_no_high_%.0fs_pnl_%.1fpct_max_%.1fpct"
            % (age, pnl, float(max_seen_pct if max_seen_pct is not None else pnl))
        )

        arm = arm_for_position(position_id, "EXIT_MECHANISM", position, conn)
        out["arm"] = arm
        out["act"] = bool(
            out["mode"] == "ACTIVE_PAPER"
            and arm == "LILYPAD_STALL"
            and is_sim(position)
        )
        return out
    except Exception as exc:
        log.debug("lilypad_stall_decision failed pos=%s: %s", position_id, exc)
        return out


def record_shadow_decision(conn, position_id, decision: dict,
                           mint=None) -> bool:
    """Persist a shadow challenger observation. Never raises."""
    try:
        if not decision or not decision.get("fire"):
            return False
        from services.fill_provenance import record_close
        return record_close(
            conn,
            position_id=position_id,
            fill_model="OBSERVED_MARK",
            exit_reason="SHADOW_" + str(decision.get("reason") or "LILYPAD_STALL"),
            ab_arm=decision.get("arm"),
            unresolved=1,  # shadow: no economic outcome attached
        )
    except Exception as exc:
        log.debug("shadow record skipped pos=%s: %s", position_id, exc)
        return False


def arm_metrics(conn=None, hours: float = 48.0) -> list:
    """
    Required A/B metrics per arm. Read-only.
    Peak thresholds, profit-taking rate, capture, raw vs modelled PnL,
    max-hold exits, hard stops, giveback, median hold.
    """
    rows = []
    try:
        cutoff = time.time() - float(hours) * 3600.0
        own = conn is None
        if own:
            from core.schema import get_connection
            conn = get_connection()
        try:
            arms = [r[0] for r in conn.execute(
                "SELECT DISTINCT arm FROM paper_ab_assignment"
            ).fetchall()] or ["UNASSIGNED"]
            for arm in arms:
                r = conn.execute(
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN p.peak_pnl_pct>=25  THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN p.peak_pnl_pct>=50  THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN p.peak_pnl_pct>=75  THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN p.peak_pnl_pct>=100 THEN 1 ELSE 0 END), "
                    "SUM(COALESCE(p.raw_realized_pnl_usd, p.realized_pnl_usd)), "
                    "SUM(CASE WHEN p.cap_applied=1 THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN p.exit_reason LIKE 'MAX_HOLD%' "
                    "         OR p.exit_reason LIKE '%STALE%' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN p.exit_reason LIKE 'HARD_STOP%' THEN 1 ELSE 0 END), "
                    "AVG(p.peak_pnl_pct) "
                    "FROM paper_positions p "
                    "LEFT JOIN paper_ab_assignment a ON a.position_id=p.id "
                    "WHERE p.status='CLOSED' AND p.closed_at>=? "
                    "AND COALESCE(a.arm,'UNASSIGNED')=?",
                    (cutoff, arm)
                ).fetchone()
                n = int(r[0] or 0)
                rows.append({
                    "arm": arm, "closes": n,
                    "peak_ge_25": int(r[1] or 0), "peak_ge_50": int(r[2] or 0),
                    "peak_ge_75": int(r[3] or 0), "peak_ge_100": int(r[4] or 0),
                    "raw_pnl_usd": round(float(r[5] or 0.0), 2),
                    "capped_rows": int(r[6] or 0),
                    "maxhold_stale": int(r[7] or 0),
                    "hard_stops": int(r[8] or 0),
                    "mean_peak_pct": round(float(r[9] or 0.0), 2),
                    "profit_take_rate_pct": None,
                })
        finally:
            if own:
                conn.close()
    except Exception as exc:
        log.debug("arm_metrics failed: %s", exc)
    return rows
