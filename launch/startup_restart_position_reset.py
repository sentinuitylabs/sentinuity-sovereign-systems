# coding: utf-8
"""
STARTUP_RESTART_POSITION_RESET_RESTORE_20260702_SIGNOFF

Purpose:
  Safe startup repair for launcher path:
    launch/startup_restart_position_reset.py

Why it exists:
  Some launch scripts call this file before starting services. If the file is
  missing, launch aborts before supervisor/executor can run.

Safety:
  - Does NOT close open positions.
  - Does NOT mutate wallet balances.
  - Does NOT alter live-trading flags.
  - Only clears transient startup claims / stale execution locks where columns
    exist.
  - Archives old non-open latched/execution_ready market snapshot claims that
    are already stale, so a fresh launch starts with a clean handoff lane.

Run:
  python .\\launch\\startup_restart_position_reset.py
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"

STALE_CLAIM_SEC = 300
STALE_LATCH_SEC = 300

def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()

def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        conn.execute(f"SELECT 1 FROM {table} LIMIT 1").fetchone()
        return True
    except Exception:
        return False

def _q(name: str) -> str:
    # Internal static-ish identifiers only, but keep quoting defensive.
    return '"' + str(name).replace('"', '""') + '"'

def main() -> int:
    now = time.time()
    print("[STARTUP_RESET] DB:", DB)

    if not DB.exists():
        print("[STARTUP_RESET] SKIP: DB not found")
        return 0

    conn = sqlite3.connect(str(DB), timeout=30)
    conn.row_factory = sqlite3.Row

    total = 0

    try:
        # Do not touch OPEN positions. Only clear stale transient close claims.
        if _table_exists(conn, "paper_positions"):
            pp = _cols(conn, "paper_positions")
            sets = []
            where = ["status='OPEN'"]

            if "close_claimed_until" in pp:
                sets.append("close_claimed_until=NULL")
                where.append("(close_claimed_until IS NOT NULL AND CAST(close_claimed_until AS REAL) < ?)")
                params = [now]
                conn.execute(
                    "UPDATE paper_positions SET " + ", ".join(sets) + " WHERE " + " AND ".join(where),
                    params,
                )
                changed = conn.total_changes - total
                total = conn.total_changes
                print("[STARTUP_RESET] cleared stale paper_position close claims:", changed)
            else:
                print("[STARTUP_RESET] paper_positions close_claimed_until column absent")

        # Clear stale market snapshot claim locks and old latched rows that are
        # not executed. This prevents dead prelaunch latches from confusing the
        # supervisor/executor handoff after restart.
        if _table_exists(conn, "market_snapshots"):
            ms = _cols(conn, "market_snapshots")

            # Clear stale claim-until columns if present.
            claim_cols = [
                "latch_claimed_until",
                "execution_claimed_until",
                "qualify_claimed_until",
                "close_claimed_until",
            ]
            for col in claim_cols:
                if col in ms:
                    before = conn.total_changes
                    conn.execute(
                        f"UPDATE market_snapshots SET {_q(col)}=NULL "
                        f"WHERE {_q(col)} IS NOT NULL AND CAST({_q(col)} AS REAL) < ?",
                        (now,),
                    )
                    print(f"[STARTUP_RESET] cleared stale {col}:", conn.total_changes - before)

            # Archive stale non-executed latches/execution_ready claims.
            time_expr_parts = []
            for c in ["latched_at", "execution_ready_at", "qualified_at", "price_updated_at", "created_at", "first_seen_at", "timestamp"]:
                if c in ms:
                    time_expr_parts.append(f"CAST(COALESCE({_q(c)},0) AS REAL)")
            if time_expr_parts:
                time_expr = "MAX(" + ",".join(time_expr_parts) + ")"
            else:
                time_expr = "0"

            conditions = []
            if "latched" in ms:
                conditions.append("COALESCE(latched,0)=1")
            if "execution_ready" in ms:
                conditions.append("COALESCE(execution_ready,0)=1")
            if "candidate_state" in ms:
                conditions.append("candidate_state='latched'")

            if conditions and "candidate_state" in ms:
                set_parts = ["candidate_state='expired_stale'"]
                if "quality_reason" in ms:
                    set_parts.append(
                        "quality_reason=CASE "
                        "WHEN quality_reason IS NULL OR quality_reason='' OR quality_reason='OK' "
                        "THEN 'STARTUP_RESET_STALE_LATCH' "
                        "ELSE quality_reason || '|STARTUP_RESET_STALE_LATCH' END"
                    )
                if "latched" in ms:
                    set_parts.append("latched=0")
                if "execution_ready" in ms:
                    set_parts.append("execution_ready=0")

                executed_guard = []
                if "executed" in ms:
                    executed_guard.append("COALESCE(executed,0)=0")
                if "candidate_state" in ms:
                    executed_guard.append("candidate_state NOT IN ('executed','exited')")

                where = "(" + " OR ".join(conditions) + ")"
                if executed_guard:
                    where += " AND " + " AND ".join(executed_guard)
                where += f" AND ({time_expr}) < ?"

                before = conn.total_changes
                conn.execute(
                    "UPDATE market_snapshots SET " + ", ".join(set_parts) + " WHERE " + where,
                    (now - STALE_LATCH_SEC,),
                )
                print("[STARTUP_RESET] archived stale market_snapshot latches:", conn.total_changes - before)
            else:
                print("[STARTUP_RESET] market_snapshots latch columns absent or incompatible")

        conn.commit()
        print("[STARTUP_RESET] committed. total changes:", conn.total_changes)
        print("[STARTUP_RESET] done")
        return 0

    except Exception as exc:
        conn.rollback()
        print("[STARTUP_RESET] ERROR:", exc)
        return 1

    finally:
        conn.close()

if __name__ == "__main__":
    raise SystemExit(main())
