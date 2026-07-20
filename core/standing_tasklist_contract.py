#!/usr/bin/env python3
"""
core/standing_tasklist_contract.py — STANDING TASKLIST (Task 7)

Seeds and maintains the organism's 13 standing tasks in
sentinuity_intelligence.db::standing_tasklist with owner, state,
autonomy flags and next actions. Idempotent: existing task rows keep
their live state; only missing tasks are seeded, retired tasks flagged.

Autonomy doctrine (enforced as data — the UI and guard read it):
  autonomous_allowed = 1 : metrics, dashboards, read-only audits,
    paper-only intelligence, copytrade observe/paper, stale/starved
    sensors, DB lights, tasklist updates
  autonomous_allowed = 0 : live arming, live size, wallet/signing,
    deleting active positions, hard-stop/live risk gates, strategy merge

Run:
  python core/standing_tasklist_contract.py           # seed/sync + print
  python core/standing_tasklist_contract.py --set "Signal Gate Starvation Watch" --state PASS --note "sensor live"
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_lane_common import find_intel_db, connect_intel_rw  # noqa: E402

VALID_STATES = ("OBSERVE", "RESEARCH", "BUILDING", "TESTING",
                "NEEDS-YOU", "PASS", "BLOCKED")

DDL = """
CREATE TABLE IF NOT EXISTS standing_tasklist (
    task_name TEXT PRIMARY KEY,
    owner TEXT,
    state TEXT CHECK (state IN
      ('OBSERVE','RESEARCH','BUILDING','TESTING','NEEDS-YOU','PASS','BLOCKED')),
    next_action TEXT,
    autonomous_allowed INTEGER DEFAULT 0,
    code_changed INTEGER DEFAULT 0,
    operator_needed INTEGER DEFAULT 0,
    note TEXT,
    retired INTEGER DEFAULT 0,
    last_update REAL
)
"""

# task_name, owner, initial state, next_action, autonomous_allowed
STANDING_TASKS = [
    ("Signal Gate Starvation Watch", "Guardian", "OBSERVE",
     "signal_gate_sensor loop diagnoses STARVED into exact reasons", 1),
    ("Copytrade Observer Refresh", "Polaris", "RESEARCH",
     "verify wallet trades -> likelihood signals -> bounded paper influence ledger", 1),
    ("Smart Wallet Source Refresh", "Oracle", "OBSERVE",
     "refresh GMGN ranked-wallet roster, then RPC-observe trades and rescore fingerprints", 1),
    ("Debate Chamber Pulse", "Polaris", "OBSERVE",
     "chamber shows IDLE from real rows when no live debate", 1),
    ("DB Lights / Hot DB Maintenance", "Guardian", "OBSERVE",
     "db_lights loop: colour, freeze-entries, prune-when-flat", 1),
    ("Live Lane Readiness", "Fable", "TESTING",
     "AUDIT_LIVE_MECHANISM_READINESS must PASS all 10 gates", 0),
    ("Oracle Freshness / Stale Price Guard", "Guardian", "OBSERVE",
     "no stale-price openings; oracle age <60-90s", 1),
    ("Executor Latency Watch", "Nugget", "OBSERVE",
     "latch->exec_ready->open timings into mechanism ledger", 1),
    ("Runner Missed-Exit Review", "Ivaris", "RESEARCH",
     "hourly profile missed_runner_count; exit gap from peak", 1),
    ("Substrate Node Paper-Only Watch", "Ivaris", "OBSERVE",
     "substrate stays paper; journal preserved across reboot", 1),
    ("Intelligence Tab Opportunity Research", "Polaris", "RESEARCH",
     "paper-only intelligence from hour/pressure/sub100 tables", 1),
    ("Hour Colourmap Refresh", "Nugget", "OBSERVE",
     "hour_intelligence loop from paper_positions ONLY", 1),
    ("Sub-100 Hot-Potato Study", "Polaris", "RESEARCH",
     "sub100_hour_profile recommended_mode per hour", 1),
    ("Winrate Wiring Integrity", "Fable", "TESTING",
     "UI == CLI via winrate_truth; never 0.0% with closed trades", 1),
]


def sync(intel_db: Optional[Path] = None, quiet: bool = False) -> None:
    now = time.time()
    con = connect_intel_rw(find_intel_db(str(intel_db) if intel_db else None))
    try:
        con.execute(DDL)
        existing = {r[0] for r in con.execute(
            "SELECT task_name FROM standing_tasklist")}
        for name, owner, state, action, auto in STANDING_TASKS:
            if name in existing:
                continue
            con.execute(
                """INSERT INTO standing_tasklist
                   (task_name, owner, state, next_action, autonomous_allowed,
                    code_changed, operator_needed, note, retired, last_update)
                   VALUES (?,?,?,?,?,0,?,?,0,?)""",
                (name, owner, state, action, auto,
                 0 if auto else 1, "seeded by contract", now))
            if not quiet:
                print(f"  [seed] {name} (owner={owner}, auto={bool(auto)})")
        current = {t[0] for t in STANDING_TASKS}
        for r in con.execute(
            "SELECT task_name FROM standing_tasklist WHERE retired=0"):
            if r[0] not in current:
                con.execute(
                    "UPDATE standing_tasklist SET retired=1, last_update=? "
                    "WHERE task_name=?", (now, r[0]))
                if not quiet:
                    print(f"  [retire] {r[0]}")
        con.commit()
    finally:
        con.close()


def set_state(task: str, state: str, note: str = "",
              code_changed: Optional[bool] = None,
              operator_needed: Optional[bool] = None,
              intel_db: Optional[Path] = None) -> bool:
    if state not in VALID_STATES:
        print(f"[FAIL] invalid state {state}; valid: {VALID_STATES}")
        return False
    con = connect_intel_rw(find_intel_db(str(intel_db) if intel_db else None))
    try:
        con.execute(DDL)
        cur = con.execute(
            "UPDATE standing_tasklist SET state=?, note=?, last_update=?"
            + (", code_changed=?" if code_changed is not None else "")
            + (", operator_needed=?" if operator_needed is not None else "")
            + " WHERE task_name=?",
            tuple(x for x in (state, note, time.time(),
                              (1 if code_changed else 0) if code_changed
                              is not None else None,
                              (1 if operator_needed else 0) if operator_needed
                              is not None else None, task) if x is not None))
        con.commit()
        return cur.rowcount > 0
    finally:
        con.close()


def show(intel_db: Optional[Path] = None) -> None:
    now = time.time()
    con = connect_intel_rw(find_intel_db(str(intel_db) if intel_db else None))
    try:
        con.execute(DDL)
        print(f"\n{'TASK':38} {'OWNER':9} {'STATE':10} {'AUTO':4} "
              f"{'OP?':3} {'AGE':>8}  NEXT")
        for r in con.execute(
            "SELECT * FROM standing_tasklist WHERE retired=0 "
            "ORDER BY task_name"):
            age = now - (r["last_update"] or now)
            age_s = f"{age/60:.0f}m" if age < 3600 else f"{age/3600:.1f}h"
            print(f"{r['task_name'][:37]:38} {r['owner']:9} {r['state']:10} "
                  f"{'yes' if r['autonomous_allowed'] else 'NO ':4} "
                  f"{'yes' if r['operator_needed'] else '-  ':3} "
                  f"{age_s:>8}  {str(r['next_action'])[:44]}")
    finally:
        con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", dest="task", default=None)
    ap.add_argument("--state", default=None)
    ap.add_argument("--note", default="")
    ap.add_argument("--intel-db", default=None)
    a = ap.parse_args()
    idb = Path(a.intel_db) if a.intel_db else None
    sync(idb)
    if a.task and a.state:
        ok = set_state(a.task, a.state.upper(), a.note, intel_db=idb)
        print(f"[{'OK' if ok else 'FAIL'}] {a.task} → {a.state}")
    show(idb)
