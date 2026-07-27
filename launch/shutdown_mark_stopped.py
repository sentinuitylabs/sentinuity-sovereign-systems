from __future__ import annotations
import sqlite3, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"

def main() -> int:
    if not DB.exists():
        print("DB missing; heartbeat stop mark skipped")
        return 0
    con = sqlite3.connect(str(DB), timeout=15)
    cur = con.cursor()
    tabs = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    if "service_heartbeats" not in tabs:
        print("service_heartbeats missing")
        con.close()
        return 0
    cols = {r[1] for r in cur.execute("PRAGMA table_info(service_heartbeats)")}
    sets = []
    vals = []
    if "status" in cols:
        sets.append("status=?"); vals.append("STOPPED_EXPRESS")
    if "note" in cols:
        sets.append("note=?"); vals.append("express_shutdown_prune_clean")
    if "restart_claimed_until" in cols:
        sets.append("restart_claimed_until=?"); vals.append(0)
    if "updated_at" in cols:
        sets.append("updated_at=?"); vals.append(time.time())
    if "last_seen" in cols:
        sets.append("last_seen=?"); vals.append(time.time())
    if sets:
        cur.execute("UPDATE service_heartbeats SET " + ",".join(sets), vals)
        con.commit()
        print("service_heartbeats marked STOPPED_EXPRESS rows=", cur.rowcount)
    con.close()
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
