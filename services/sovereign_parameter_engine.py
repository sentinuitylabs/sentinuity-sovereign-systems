"""
PATCH 2 — sovereign_parameter_engine.py
Bridge: polaris_proposals(approved) → apply_proposal()

PROBLEM:
  apply_proposal() exists but is NEVER called by anything in the codebase.
  The UI sets polaris_proposals.status = 'approved'.
  Nothing picks that up and routes it to apply_proposal().
  The self-improvement loop is broken at the application step.

FIX:
  Add _poll_approved_proposals() to SovereignParameterEngine.
  Call it inside the run() loop alongside run_pending_evaluations().
  It reads polaris_proposals WHERE status='approved', constructs a Proposal,
  calls apply_proposal(), then marks the DB row as 'applied'.

  Also enforces BANNED_MUTATIONS from mutation_enums before applying.

HOW TO APPLY:
  Add the import block and the two methods shown below into
  sovereign_parameter_engine.py.

NOTES:
  - Only handles proposal_type='parameter' (PARAMETER_CHANGE).
    CODE_UPGRADE proposals are NOT auto-applied — they require separate
    handling and should remain as human-review only.
  - BANNED_MUTATIONS check runs before throttle check.
  - On apply failure the row is set to 'apply_failed' not left as 'approved'
    so it doesn't retry forever.
"""

import json
import time
import sqlite3
import logging
import sys
from copy import deepcopy
from datetime import datetime, timezone, timedelta
from typing import Any, Optional
from dataclasses import dataclass, asdict
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

DB_PATH = str(BASE_DIR / "sentinuity_matrix.db")

MAX_CHANGE_PCT_PER_HOUR      = 0.005
ROLLBACK_EVALUATION_WINDOW_S = 3600
ROLLBACK_MIN_TRADES          = 3

THROTTLED_PARAMETERS = {
    "STOP_LOSS_PCT",
    "MIN_LIQUIDITY_USD",
}

# ── Import BANNED_MUTATIONS from mutation_enums ────────────────────────────────
try:
    from core.mutation_enums import BANNED_MUTATIONS, BANNED_FILES
    _ENUMS_AVAILABLE = True
except Exception:
    _ENUMS_AVAILABLE = False
    BANNED_MUTATIONS = {
        "CONFIDENCE_CEILING":   0.87,
        "RESOLVER_HARD_CAP":    0.89,
        "CONFIDENCE_FLOOR_MIN": 0.65,
    }
    BANNED_FILES = set()

log = logging.getLogger("sovereign_parameter_engine")
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
)


@dataclass
class ParameterChange:
    param: str
    old_value: Any
    new_value: Any
    proposal_id: str
    applied_at: str = ""
    snapshot_id: str = ""


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=60)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA busy_timeout=60000")
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS parameter_snapshots (
            snapshot_id    TEXT PRIMARY KEY,
            proposal_id    TEXT NOT NULL,
            config_json    TEXT NOT NULL,
            applied_at     TEXT NOT NULL,
            evaluation_due TEXT NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending'
        );
        CREATE TABLE IF NOT EXISTS parameter_change_log (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            proposal_id  TEXT NOT NULL,
            param        TEXT NOT NULL,
            old_value    TEXT,
            new_value    TEXT,
            changed_at   TEXT NOT NULL,
            snapshot_id  TEXT
        );
    """)
    conn.commit()


def _last_change_time(conn, param: str) -> Optional[float]:
    row = conn.execute(
        "SELECT changed_at FROM parameter_change_log WHERE param=? ORDER BY id DESC LIMIT 1",
        (param,),
    ).fetchone()
    if not row:
        return None
    try:
        return datetime.fromisoformat(row["changed_at"]).timestamp()
    except Exception:
        return None


def _check_throttle(conn, param: str, old_val: float, new_val: float) -> Optional[str]:
    if param not in THROTTLED_PARAMETERS:
        return None
    pct_change = abs(new_val - old_val) / abs(old_val) if old_val != 0 else float("inf")
    if pct_change > MAX_CHANGE_PCT_PER_HOUR:
        return (f"Thermal throttle: {pct_change*100:.3f}% exceeds max "
                f"{MAX_CHANGE_PCT_PER_HOUR*100:.1f}%/hr for {param}")
    last = _last_change_time(conn, param)
    if last:
        elapsed_hr = (time.time() - last) / 3600
        if elapsed_hr < 1.0:
            return (f"Thermal throttle: {param} changed {elapsed_hr*60:.1f} min ago. "
                    "Minimum cooldown is 1 hour.")
    return None


def _check_banned(param: str, new_val: Any) -> Optional[str]:
    """
    Enforce BANNED_MUTATIONS from mutation_enums.
    Returns error string if the change is forbidden, else None.
    """
    if param in BANNED_MUTATIONS:
        canonical = BANNED_MUTATIONS[param]
        try:
            if float(new_val) != float(canonical):
                return (
                    f"BANNED MUTATION: {param} is a non-negotiable constant "
                    f"(canonical={canonical}). Auto-rejected."
                )
        except (TypeError, ValueError):
            return f"BANNED MUTATION: {param} is a protected constant. Auto-rejected."
    return None


def _read_config(conn) -> dict:
    rows = conn.execute("SELECT key, value FROM system_config").fetchall()
    result = {}
    for r in rows:
        try:
            result[r["key"]] = json.loads(r["value"])
        except Exception:
            result[r["key"]] = r["value"]
    return result


def _write_config_key(conn, key: str, value: Any) -> None:
    conn.execute(
        """INSERT INTO system_config (key, value, updated_at)
           VALUES (?, ?, ?)
           ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at""",
        (key, json.dumps(value) if not isinstance(value, str) else value,
         datetime.now(timezone.utc).isoformat()),
    )


def _snapshot_config(conn, config: dict, proposal_id: str) -> str:
    import uuid
    snapshot_id    = f"snap_{uuid.uuid4().hex[:12]}"
    evaluation_due = (datetime.now(timezone.utc) +
                      timedelta(seconds=ROLLBACK_EVALUATION_WINDOW_S)).isoformat()
    conn.execute(
        """INSERT INTO parameter_snapshots
               (snapshot_id, proposal_id, config_json, applied_at, evaluation_due, status)
           VALUES (?, ?, ?, ?, ?, 'pending')""",
        (snapshot_id, proposal_id, json.dumps(config),
         datetime.now(timezone.utc).isoformat(), evaluation_due),
    )
    conn.commit()
    return snapshot_id


class SovereignParameterEngine:

    def apply_proposal(self, proposal) -> dict:
        conn = _get_conn()
        _ensure_tables(conn)
        current_config = _read_config(conn)
        snapshot_id    = _snapshot_config(conn, current_config, proposal.proposal_id)
        applied, blocked = [], []

        for change in proposal.parameter_changes:
            param    = change.param
            old_val  = current_config.get(change.param)
            new_val  = change.new_value

            # ── BANNED MUTATIONS CHECK (runs before throttle) ──────────────
            banned_err = _check_banned(param, new_val)
            if banned_err:
                log.error("AUTO-REJECT %s: %s", param, banned_err)
                blocked.append({"param": param, "reason": banned_err})
                continue

            # ── THROTTLE CHECK ─────────────────────────────────────────────
            if isinstance(old_val, (int, float)) and isinstance(new_val, (int, float)):
                err = _check_throttle(conn, param, float(old_val), float(new_val))
                if err:
                    log.warning("BLOCKED %s: %s", param, err)
                    blocked.append({"param": param, "reason": err})
                    continue

            _write_config_key(conn, param, new_val)
            conn.execute(
                """INSERT INTO parameter_change_log
                       (proposal_id, param, old_value, new_value, changed_at, snapshot_id)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (proposal.proposal_id, param, json.dumps(old_val), json.dumps(new_val),
                 datetime.now(timezone.utc).isoformat(), snapshot_id),
            )
            applied.append(param)
            log.info("Applied %s: %s → %s", param, old_val, new_val)

        conn.commit()
        conn.close()
        return {"success": len(applied) > 0, "applied": applied,
                "blocked": blocked, "snapshot_id": snapshot_id}

    # ── NEW: Poll polaris_proposals for approved parameter proposals ───────────
    def _poll_approved_proposals(self) -> list:
        """
        Bridge between UI approval and parameter application.

        Polaris writes plain-English suggested_action strings (e.g. "Raise minimum
        confidence floor") — not KEY=value pairs. Resolution is handled by
        _resolve_intent_to_changes(), which maps known proposal types and action
        phrases to specific system_config keys and conservative delta values.

        Types that cannot be auto-applied (CODE_UPGRADE, ECOSYSTEM_EXPANSION,
        STRATEGY_SHIFT, RAW_DNA_ERROR) are skipped and left in 'approved' state
        for human-assisted handling — they are NOT marked apply_failed.
        """
        # Proposal types that require human action, not auto-apply
        HUMAN_ONLY_TYPES = {
            "CODE_UPGRADE", "ECOSYSTEM_EXPANSION", "STRATEGY_SHIFT", "RAW_DNA_ERROR",
        }

        results = []
        try:
            conn = _get_conn()
            rows = conn.execute(
                """SELECT id, proposal_type, proposal_text, suggested_action,
                          confidence, metrics_json
                   FROM polaris_proposals
                   WHERE status = 'approved'
                   ORDER BY id ASC
                   LIMIT 5"""
            ).fetchall()
            conn.close()
        except Exception as e:
            log.error("_poll_approved_proposals read failed: %s", e)
            return results

        for row in rows:
            pid = row["id"]
            ptype = str(row["proposal_type"] or "").strip().upper()

            # Skip types that require human-assisted handling — leave in 'approved'
            if ptype in HUMAN_ONLY_TYPES:
                log.info(
                    "Proposal %d type=%s requires human action — skipping auto-apply",
                    pid, ptype,
                )
                continue

            try:
                action = str(row["suggested_action"] or "").strip()
                changes = _resolve_intent_to_changes(
                    str(pid), ptype, action, row["metrics_json"]
                )

                if not changes:
                    log.warning("Proposal %d has no parseable parameter changes — skipping", pid)
                    _mark_proposal(pid, "apply_failed")
                    continue

                proposal = Proposal(
                    proposal_id=str(pid),
                    hypothesis=str(row["proposal_text"] or ""),
                    expected_outcome="Improvement based on POLARIS analysis",
                    falsifiability_condition="Performance degrades in rollback window",
                    risk_assessment="Reviewed by IVARIS debate",
                    success_metric="PnL and win rate improvement",
                    parameter_changes=changes,
                    status="approved",
                )

                result = self.apply_proposal(proposal)

                if result["success"]:
                    log.info("Proposal %d applied: %s", pid, result["applied"])
                    _mark_proposal(pid, "applied")
                else:
                    # All changes were blocked (throttle or banned)
                    log.warning("Proposal %d fully blocked: %s", pid, result["blocked"])
                    _mark_proposal(pid, "apply_failed")

                results.append({"proposal_id": pid, **result})

            except Exception as e:
                log.exception("Failed to apply proposal %d: %s", pid, e)
                _mark_proposal(pid, "apply_failed")

        return results

    def evaluate_rollback(self, snapshot_id: str) -> dict:
        conn = _get_conn()
        _ensure_tables(conn)
        snap_row = conn.execute(
            "SELECT * FROM parameter_snapshots WHERE snapshot_id=?", (snapshot_id,)
        ).fetchone()
        if not snap_row:
            conn.close()
            return {"action": "error", "reason": f"Snapshot {snapshot_id} not found"}
        if snap_row["status"] != "pending":
            conn.close()
            return {"action": "skip", "reason": f"Snapshot already {snap_row['status']}"}

        rows = conn.execute(
            "SELECT realized_pnl_usd as pnl, win_loss as outcome FROM trade_autopsies WHERE created_at > ?",
            (snap_row["applied_at"],),
        ).fetchall()

        if len(rows) < ROLLBACK_MIN_TRADES:
            conn.close()
            return {"action": "wait", "reason": f"Only {len(rows)} trades. Need {ROLLBACK_MIN_TRADES}."}

        total_pnl = sum(float(r["pnl"] or 0) for r in rows)
        win_rate  = sum(1 for r in rows if str(r["outcome"]).upper() == "WIN") / len(rows)
        should_rollback = total_pnl < 0 or win_rate < 0.4
        action = "rollback" if should_rollback else "validate"

        if should_rollback:
            previous_config = json.loads(snap_row["config_json"])
            for key, value in previous_config.items():
                _write_config_key(conn, key, value)
            conn.execute(
                "UPDATE parameter_snapshots SET status='rolled_back' WHERE snapshot_id=?",
                (snapshot_id,),
            )
            log.warning("AUTO-ROLLBACK %s — PnL=%.4f win_rate=%.2f", snapshot_id, total_pnl, win_rate)
        else:
            conn.execute(
                "UPDATE parameter_snapshots SET status='validated' WHERE snapshot_id=?",
                (snapshot_id,),
            )

        conn.commit()
        conn.close()
        return {"action": action, "snapshot_id": snapshot_id,
                "metrics": {"total_pnl": total_pnl, "win_rate": win_rate, "trades": len(rows)}}

    def run_pending_evaluations(self) -> list:
        conn = _get_conn()
        _ensure_tables(conn)
        now  = datetime.now(timezone.utc).isoformat()
        rows = conn.execute(
            "SELECT snapshot_id FROM parameter_snapshots WHERE status='pending' AND evaluation_due <= ?",
            (now,),
        ).fetchall()
        conn.close()
        return [self.evaluate_rollback(r["snapshot_id"]) for r in rows]

    def run(self) -> None:
        from core.schema import update_heartbeat
        log.info("SOVEREIGN PARAMETER ENGINE ONLINE")
        _ensure_tables(_get_conn())
        while True:
            try:
                # ── Poll for approved proposals and apply them ─────────────
                applied_results = self._poll_approved_proposals()
                if applied_results:
                    log.info("Applied %d approved proposals", len(applied_results))

                # ── Evaluate pending rollback windows ──────────────────────
                eval_results = self.run_pending_evaluations()
                if eval_results:
                    log.info("Evaluated %d pending snapshots", len(eval_results))

                update_heartbeat(
                    "sovereign_parameter_engine", "ALIVE",
                    f"applied={len(applied_results)} evaluated={len(eval_results)}",
                )
            except Exception as e:
                log.exception("SPE error: %s", e)
            time.sleep(300)


# ── HELPERS ───────────────────────────────────────────────────────────────────

def _mark_proposal(proposal_id: int, new_status: str) -> None:
    try:
        conn = _get_conn()
        conn.execute(
            "UPDATE polaris_proposals SET status=? WHERE id=?",
            (new_status, proposal_id),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        log.error("Failed to mark proposal %d as %s: %s", proposal_id, new_status, e)


def _resolve_intent_to_changes(
    proposal_id: str,
    proposal_type: str,
    action: str,
    metrics_json,
) -> list:
    """
    Resolve Polaris intent to concrete ParameterChange objects.

    Polaris stores plain-English suggested_action strings — not KEY=value.
    This function maps known proposal_type + action phrase combinations to
    specific system_config keys and conservative delta values derived from
    current config.

    Intent map covers every proposal type Polaris currently generates:
      EARLY_STOP_CLUSTER  → tighten entry: raise SUPERVISOR_MIN_MINT_CONFIDENCE +0.02
      LOW_WIN_RATE        → raise confidence floor: raise SUPERVISOR_MIN_MINT_CONFIDENCE +0.02
      NEGATIVE_EDGE       → expand reward/reduce SL sensitivity:
                            raise TAKE_PROFIT_PCT +2.0, raise STOP_LOSS_PCT +1.0
      PARAMETER_CHANGE    → same intent map applied by keyword in action string

    All deltas are conservative and subject to throttle + BANNED_MUTATIONS checks.
    Returns empty list (→ apply_failed) if no intent can be resolved.
    """
    import re as _re

    # Read current config for delta calculations
    try:
        conn = _get_conn()
        current = _read_config(conn)
        conn.close()
    except Exception:
        current = {}

    def _cur(key, default):
        try:
            return float(current.get(key, default))
        except (TypeError, ValueError):
            return float(default)

    action_l = action.lower()
    changes = []

    # ── Intent resolution map ─────────────────────────────────────────────────
    #
    # First try: explicit KEY=value or JSON (handles future structured output)
    # Second try: proposal_type-based intent mapping
    # Third try: keyword scan of action string

    # Pass 1 — explicit structured formats (forward-compatible)
    try:
        data = json.loads(action)
        if isinstance(data, dict):
            if "changes" in data:
                for item in data["changes"]:
                    changes.append(ParameterChange(
                        param=item["param"], old_value=None,
                        new_value=item["value"], proposal_id=proposal_id,
                    ))
                return changes
            if "param" in data and "value" in data:
                changes.append(ParameterChange(
                    param=data["param"], old_value=None,
                    new_value=data["value"], proposal_id=proposal_id,
                ))
                return changes
    except Exception:
        pass

    pairs = _re.findall(r'([A-Z_]{3,40})\s*=\s*([^\s,;]+)', action)
    for key, val in pairs:
        try:
            numeric = float(val)
            val = int(numeric) if numeric == int(numeric) else numeric
        except ValueError:
            pass
        changes.append(ParameterChange(
            param=key, old_value=None, new_value=val, proposal_id=proposal_id,
        ))
    if changes:
        return changes

    # Pass 2 — proposal_type intent map
    ptype = proposal_type.upper()

    if ptype == "EARLY_STOP_CLUSTER":
        # High SL rate: tighten entry by raising confidence threshold
        cur = _cur("SUPERVISOR_MIN_MINT_CONFIDENCE", 0.72)
        new_val = round(min(cur + 0.02, 0.85), 3)   # hard cap below CONFIDENCE_CEILING
        changes.append(ParameterChange(
            param="SUPERVISOR_MIN_MINT_CONFIDENCE",
            old_value=cur, new_value=new_val, proposal_id=proposal_id,
        ))

    elif ptype == "LOW_WIN_RATE":
        # Low win rate: raise minimum confidence floor to reduce noise entries
        cur = _cur("SUPERVISOR_MIN_MINT_CONFIDENCE", 0.72)
        new_val = round(min(cur + 0.02, 0.85), 3)
        changes.append(ParameterChange(
            param="SUPERVISOR_MIN_MINT_CONFIDENCE",
            old_value=cur, new_value=new_val, proposal_id=proposal_id,
        ))

    elif ptype == "NEGATIVE_EDGE":
        # Positive win rate but negative PnL: expand TP and widen SL slightly
        cur_tp = _cur("TAKE_PROFIT_PCT", 25.0)
        cur_sl = _cur("STOP_LOSS_PCT", 10.0)
        changes.append(ParameterChange(
            param="TAKE_PROFIT_PCT",
            old_value=cur_tp, new_value=round(cur_tp + 2.0, 1), proposal_id=proposal_id,
        ))
        changes.append(ParameterChange(
            param="STOP_LOSS_PCT",
            old_value=cur_sl, new_value=round(cur_sl + 1.0, 1), proposal_id=proposal_id,
        ))

    elif ptype == "PARAMETER_CHANGE":
        # GPT-generated proposal: fall through to Pass 3 keyword scan below
        pass

    # Pass 3 — keyword scan (catches GPT PARAMETER_CHANGE and any novel phrasings)
    if not changes:
        if any(k in action_l for k in ["confidence floor", "confidence threshold",
                                        "entry threshold", "raise minimum confidence",
                                        "increase entry"]):
            cur = _cur("SUPERVISOR_MIN_MINT_CONFIDENCE", 0.72)
            new_val = round(min(cur + 0.02, 0.85), 3)
            changes.append(ParameterChange(
                param="SUPERVISOR_MIN_MINT_CONFIDENCE",
                old_value=cur, new_value=new_val, proposal_id=proposal_id,
            ))

        elif any(k in action_l for k in ["take profit", "expand profit", "tp "]):
            cur = _cur("TAKE_PROFIT_PCT", 25.0)
            changes.append(ParameterChange(
                param="TAKE_PROFIT_PCT",
                old_value=cur, new_value=round(cur + 2.0, 1), proposal_id=proposal_id,
            ))

        elif any(k in action_l for k in ["stop loss", "reduce sl", "sl sensitivity",
                                          "tighten sl", "widen sl"]):
            cur = _cur("STOP_LOSS_PCT", 10.0)
            # "tighten" = reduce, "widen/reduce sensitivity" = increase
            if any(k in action_l for k in ["tighten", "reduce sl"]):
                new_val = round(max(cur - 1.0, 5.0), 1)
            else:
                new_val = round(cur + 1.0, 1)
            changes.append(ParameterChange(
                param="STOP_LOSS_PCT",
                old_value=cur, new_value=new_val, proposal_id=proposal_id,
            ))

        elif any(k in action_l for k in ["liquidity", "min liquidity"]):
            cur = _cur("MIN_LIQUIDITY_USD", 5000.0)
            changes.append(ParameterChange(
                param="MIN_LIQUIDITY_USD",
                old_value=cur, new_value=round(cur * 1.1, 0), proposal_id=proposal_id,
            ))

        elif any(k in action_l for k in ["position size", "reduce size", "increase size"]):
            cur = _cur("POSITION_SIZE_PCT", 5.0)
            if "reduce" in action_l:
                new_val = round(max(cur - 0.5, 1.0), 1)
            else:
                new_val = round(min(cur + 0.5, 10.0), 1)
            changes.append(ParameterChange(
                param="POSITION_SIZE_PCT",
                old_value=cur, new_value=new_val, proposal_id=proposal_id,
            ))

    if not changes:
        log.warning(
            "Proposal %s type=%s action=%r — no intent resolved, cannot auto-apply",
            proposal_id, proposal_type, action[:80],
        )

    return changes



@dataclass
class Proposal:
    proposal_id: str
    hypothesis: str
    expected_outcome: str
    falsifiability_condition: str
    risk_assessment: str
    success_metric: str
    parameter_changes: list
    status: str = "pending"
    created_at: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.parameter_changes:
            raise ValueError("Proposal must include at least one parameter change")


if __name__ == "__main__":
    SovereignParameterEngine().run()
