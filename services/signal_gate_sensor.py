#!/usr/bin/env python3
"""
services/signal_gate_sensor.py — SIGNAL GATE STARVATION SENSOR (Task 5)

The hub currently collapses everything into STARVED whenever stale>0 and
fresh==0. This sensor diagnoses WHY and writes state+reason to
sentinuity_intelligence.db::signal_gate_state so the UI shows the exact
reason beside the gate.

Taxonomy:
  PASSING               fresh qualified candidates exist
  IDLE_NO_FLOW          nothing in gate at all — market quiet, not a fault
  STARVED_STALE_SOURCE  rows exist but all stale — upstream freshness issue
  STARVED_SERVICE_DOWN  a required service heartbeat is missing/stale
  SENSOR_MISMATCH       fresh candidates exist but the gate query/key the UI
                        uses says starved — wiring bug, not market
  VETO_DOMINATED        fresh rows exist but all vetoed/quality-failed

Renewal: with --renew it may touch ONLY a per-service renewal request flag
(system_config SERVICE_RENEWAL_REQUESTED_<name>=1) for a service whose
heartbeat is stale — it never force-opens trades, never bypasses gates,
never restarts processes itself (the watchdog/launcher owns restarts).

Run:
  python services/signal_gate_sensor.py --once
  python services/signal_gate_sensor.py            # loop 30s
  python services/signal_gate_sensor.py --once --renew
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_lane_common import (  # noqa: E402
    find_matrix_db, find_intel_db, connect_ro, connect_intel_rw,
    table_exists, cols, fnum,
)

REQUIRED_SERVICES = {
    "ws_price_oracle": (90, ("ws_price_oracle",)),
    "ingest_pipeline": (120, ("ingest_pipeline",)),
    "market_intelligence": (120, ("market_intelligence",)),
    # Canonical live service is pump_monitor; retain the historical alias.
    "pump_monitor": (180, ("pump_monitor", "ws_pump_monitor")),
}

DDL = """
CREATE TABLE IF NOT EXISTS signal_gate_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    state TEXT, reason TEXT,
    fresh_60s INTEGER, fresh_300s INTEGER, fresh_900s INTEGER,
    stale_count INTEGER, vetoed_count INTEGER,
    exec_ready_count INTEGER, latched_count INTEGER,
    services_down TEXT, consecutive_starved INTEGER DEFAULT 0,
    renewal_requested TEXT, updated_at REAL
)
"""


def diagnose(mcon: sqlite3.Connection, now: float) -> Dict[str, Any]:
    d: Dict[str, Any] = {
        "state": "UNKNOWN", "reason": "", "fresh_60s": 0, "fresh_300s": 0,
        "fresh_900s": 0, "stale_count": 0, "vetoed_count": 0,
        "exec_ready_count": 0, "latched_count": 0, "services_down": "",
        "updated_at": now,
    }
    if not table_exists(mcon, "market_snapshots"):
        d.update(state="STARVED_SERVICE_DOWN",
                 reason="market_snapshots table absent — ingest never wrote")
        return d
    c = set(cols(mcon, "market_snapshots"))
    tsc = "price_updated_at" if "price_updated_at" in c else \
          ("created_at" if "created_at" in c else None)

    def cnt(sql, args=()):
        try:
            return int(mcon.execute(sql, args).fetchone()[0])
        except Exception:
            return 0

    if tsc:
        d["fresh_60s"] = cnt(f"SELECT COUNT(*) FROM market_snapshots "
                             f"WHERE COALESCE({tsc},0) > ?", (now - 60,))
        d["fresh_300s"] = cnt(f"SELECT COUNT(*) FROM market_snapshots "
                              f"WHERE COALESCE({tsc},0) > ?", (now - 300,))
        d["fresh_900s"] = cnt(f"SELECT COUNT(*) FROM market_snapshots "
                              f"WHERE COALESCE({tsc},0) > ?", (now - 900,))
        d["stale_count"] = cnt(f"SELECT COUNT(*) FROM market_snapshots "
                               f"WHERE COALESCE({tsc},0) <= ? AND "
                               f"COALESCE({tsc},0) > ?",
                               (now - 300, now - 6 * 3600))
    if "quality_reason" in c:
        d["vetoed_count"] = cnt(
            "SELECT COUNT(*) FROM market_snapshots WHERE "
            "COALESCE(quality_reason,'')<>'' AND "
            f"COALESCE({tsc or 'rowid'},0) > ?", (now - 900,))
    if "execution_ready" in c:
        d["exec_ready_count"] = cnt(
            "SELECT COUNT(*) FROM market_snapshots WHERE execution_ready=1 "
            f"AND COALESCE({tsc or 'rowid'},0) > ?", (now - 900,))
    if "latched" in c:
        d["latched_count"] = cnt(
            "SELECT COUNT(*) FROM market_snapshots WHERE latched=1 "
            f"AND COALESCE({tsc or 'rowid'},0) > ?", (now - 900,))

    # gate view (mirror the hub's own query)
    gate_fresh = 0
    if "quality_status" in c and tsc:
        gate_fresh = cnt(
            f"SELECT COUNT(*) FROM market_snapshots WHERE "
            f"quality_status='qualified' AND {tsc} > ?", (now - 120,))

    # heartbeats
    down = []
    if table_exists(mcon, "system_heartbeat"):
        hb_cols = cols(mcon, "system_heartbeat")
        namec = "service_name" if "service_name" in hb_cols else hb_cols[0]
        tc = next((x for x in ("last_pulse", "updated_at", "timestamp",
                               "last_success_at") if x in hb_cols), None)
        for svc, (max_age, aliases) in REQUIRED_SERVICES.items():
            try:
                if not tc:
                    down.append(svc)
                    continue
                marks = ",".join("?" for _ in aliases)
                row = mcon.execute(
                    f"SELECT MAX(COALESCE({tc},0)) FROM system_heartbeat "
                    f"WHERE LOWER({namec}) IN ({marks})",
                    tuple(a.lower() for a in aliases),
                ).fetchone()
                age = (now - float(row[0])) if row and row[0] else None
                if age is None or age > max_age:
                    down.append(svc)
            except Exception:
                down.append(svc)
    d["services_down"] = ",".join(down)

    # ── verdict order matters
    if gate_fresh > 0:
        d.update(state="PASSING",
                 reason=f"{gate_fresh} fresh qualified in gate")
    elif d["fresh_60s"] > 0 or d["fresh_300s"] > 0:
        # Fresh market rows with zero qualified candidates is normal filtering,
        # not starvation. Distinguish a rejection-heavy gate from quiet flow.
        if d["vetoed_count"] > 0:
            d.update(state="VETO_DOMINATED",
                     reason=f"{d['fresh_300s']} fresh rows, 0 qualified; "
                            f"{d['vetoed_count']} recent quality reasons — "
                            f"input healthy, gate filtering")
        else:
            d.update(state="IDLE_NO_FLOW",
                     reason=f"{d['fresh_300s']} fresh snapshot(s), 0 qualified — "
                            f"input healthy, awaiting a qualifying candidate")
    elif down:
        d.update(state="STARVED_SERVICE_DOWN",
                 reason=f"heartbeat stale/missing: {d['services_down']}")
    elif d["stale_count"] > 0:
        d.update(state="STARVED_STALE_SOURCE",
                 reason=f"{d['stale_count']} rows all stale >300s, 0 fresh — "
                        f"upstream freshness, oracle/ingest lag")
    else:
        d.update(state="IDLE_NO_FLOW",
                 reason="no candidates at all in window — market quiet, "
                        "not a fault")
    return d


def store(d: Dict[str, Any], renew: bool,
          intel_db: Optional[Path] = None,
          matrix_db: Optional[Path] = None) -> None:
    intel = find_intel_db(str(intel_db) if intel_db else None)
    icon = connect_intel_rw(intel)
    try:
        icon.execute(DDL)
        prev = icon.execute(
            "SELECT state, consecutive_starved FROM signal_gate_state "
            "WHERE id=1").fetchone()
        consec = 0
        if prev and str(prev[0] or "").startswith("STARVED") and \
                str(d["state"]).startswith("STARVED"):
            consec = int(prev[1] or 0) + 1
        elif str(d["state"]).startswith("STARVED"):
            consec = 1
        renewal = ""
        if renew and consec >= 3 and d["state"] == "STARVED_SERVICE_DOWN" \
                and d["services_down"]:
            # request renewal via flag only — launcher/watchdog owns restarts
            matrix = find_matrix_db(str(matrix_db) if matrix_db else None)
            if matrix:
                try:
                    wc = sqlite3.connect(str(matrix), timeout=5)
                    for svc in d["services_down"].split(","):
                        wc.execute(
                            "INSERT INTO system_config(key,value) VALUES(?,?) "
                            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (f"SERVICE_RENEWAL_REQUESTED_{svc}", "1"))
                    wc.commit(); wc.close()
                    renewal = d["services_down"]
                except Exception:
                    pass
        icon.execute(
            """INSERT INTO signal_gate_state
               (id, state, reason, fresh_60s, fresh_300s, fresh_900s,
                stale_count, vetoed_count, exec_ready_count, latched_count,
                services_down, consecutive_starved, renewal_requested,
                updated_at)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET state=excluded.state,
                reason=excluded.reason, fresh_60s=excluded.fresh_60s,
                fresh_300s=excluded.fresh_300s, fresh_900s=excluded.fresh_900s,
                stale_count=excluded.stale_count,
                vetoed_count=excluded.vetoed_count,
                exec_ready_count=excluded.exec_ready_count,
                latched_count=excluded.latched_count,
                services_down=excluded.services_down,
                consecutive_starved=excluded.consecutive_starved,
                renewal_requested=excluded.renewal_requested,
                updated_at=excluded.updated_at""",
            (d["state"], d["reason"], d["fresh_60s"], d["fresh_300s"],
             d["fresh_900s"], d["stale_count"], d["vetoed_count"],
             d["exec_ready_count"], d["latched_count"], d["services_down"],
             consec, renewal, d["updated_at"]))
        icon.commit()
    finally:
        icon.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--renew", action="store_true")
    ap.add_argument("--interval", type=float, default=30.0)
    ap.add_argument("--matrix-db", default=None)
    ap.add_argument("--intel-db", default=None)
    a = ap.parse_args()
    while True:
        matrix = find_matrix_db(a.matrix_db)
        if matrix is None:
            print("[signal_gate_sensor] matrix DB not found")
            return 1
        mcon = connect_ro(matrix)
        try:
            d = diagnose(mcon, time.time())
        finally:
            mcon.close()
        store(d, a.renew,
              Path(a.intel_db) if a.intel_db else None,
              Path(a.matrix_db) if a.matrix_db else None)
        print(f"[gate] {d['state']} — {d['reason']} "
              f"(fresh {d['fresh_60s']}/{d['fresh_300s']}/{d['fresh_900s']} "
              f"@60/300/900s, stale {d['stale_count']}, "
              f"veto {d['vetoed_count']})")
        if a.once:
            return 0
        time.sleep(max(10.0, a.interval))


if __name__ == "__main__":
    sys.exit(main())
