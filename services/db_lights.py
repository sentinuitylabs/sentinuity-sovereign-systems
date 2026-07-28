#!/usr/bin/env python3
"""
services/db_lights.py — DB LIGHTS + HOT DB MAINTENANCE (Task 9)

Directive thresholds (supersedes the 4-light db_size_governor for display;
the old governor is left in place and untouched):
    DB_MAX_MB   = 125
    soft        =  75 MB
    hard        = 125 MB
    emergency   = 175 MB
Colour by percent of DB_MAX_MB:
    0–25%    GREEN        #14F195
    25–50%   CYAN_GREEN   #38E1FF
    50–75%   AMBER_GOLD   #FFD700
    75–100%  RED          #FF073A
    >100%    VIOLET       #9945FF   (emergency)

Behaviour:
  * open positions present  → NEVER prune; freeze new entries at RED+,
    keep exits alive, defer heavy maintenance
  * flat + RED/hard         → backup → WAL checkpoint → safe prune
  * VIOLET                  → freeze entries, checkpoint WAL immediately,
    schedule prune once flat
  * WAL checkpoint (PASSIVE) is always safe to attempt in-process
  * full restart is a last resort, never triggered here

Freeze mechanism: writes system_config ENTRIES_FROZEN=1 plus
DB_LIGHTS_* keys. NOTE (honest): the execution engine does not yet read
ENTRIES_FROZEN — until the engine consumes it this is an advisory flag
surfaced in UI + the readiness audit blocks the live test on it.

NEVER pruned: paper_positions/live positions (open OR any), wallet state,
latest oracle prices, exec_ready rows, system_config, heartbeats, live
fill/slippage/gas ledgers, recent PnL, council/debate/task state.
Prune targets are high-churn logs only, older than --keep-days, archived
into archives/ backup first.

Run:
  python services/db_lights.py --check          # report only
  python services/db_lights.py --once           # one governed cycle
  python services/db_lights.py                  # loop 60s
  python services/db_lights.py --maintain       # act now if safe
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_lane_common import (  # noqa: E402
    find_matrix_db, find_intel_db, connect_intel_rw, table_exists, cols,
)

DB_MAX_MB = 125.0
SOFT_MB, HARD_MB, EMERGENCY_MB = 75.0, 125.0, 175.0

BANDS = [  # (max_pct_exclusive, name, colour)
    (25.0,  "GREEN",      "#14F195"),
    (50.0,  "CYAN_GREEN", "#38E1FF"),
    (75.0,  "AMBER_GOLD", "#FFD700"),
    (100.0, "RED",        "#FF073A"),
    (1e9,   "VIOLET",     "#9945FF"),
]

# high-churn log tables that MAY be pruned (aged rows only)
PRUNABLE = ["cognition_log", "mtm_ticks", "wallet_write_log",
            "oracle_write_log", "raw_dna_stream", "trade_trace_log",
            "substrate_trade_log"]
KEEP_DAYS_DEFAULT = 3.0

DDL = """
CREATE TABLE IF NOT EXISTS db_lights_state (
    id INTEGER PRIMARY KEY CHECK (id=1),
    db_mb REAL, wal_mb REAL, pct_of_max REAL,
    colour TEXT, colour_hex TEXT,
    maintenance_state TEXT, entries_frozen INTEGER,
    open_positions INTEGER,
    last_prune_at REAL, last_prune_freed_mb REAL,
    next_prune_reason TEXT, updated_at REAL
)
"""


def sizes(db: Path) -> Tuple[float, float]:
    mb = db.stat().st_size / 1e6 if db.exists() else 0.0
    wal = Path(str(db) + "-wal")
    return mb, (wal.stat().st_size / 1e6 if wal.exists() else 0.0)


def band_for(pct: float) -> Tuple[str, str]:
    for mx, name, col in BANDS:
        if pct < mx:
            return name, col
    return "VIOLET", "#9945FF"


def open_position_count(con: sqlite3.Connection) -> int:
    n = 0
    for t in ("paper_positions", "substrate_paper_positions"):
        try:
            if table_exists(con, t):
                n += con.execute(
                    f"SELECT COUNT(*) FROM '{t}' WHERE "
                    f"UPPER(COALESCE(status,''))='OPEN'").fetchone()[0]
        except Exception:
            pass
    return n


def set_cfg(con: sqlite3.Connection, k: str, v) -> None:
    try:
        con.execute(
            "INSERT INTO system_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (k, str(v)))
    except Exception:
        pass


def checkpoint_wal(con: sqlite3.Connection) -> str:
    try:
        r = con.execute("PRAGMA wal_checkpoint(PASSIVE)").fetchone()
        return f"checkpoint {r}"
    except Exception as e:
        return f"checkpoint failed: {e}"


def safe_prune(db: Path, con: sqlite3.Connection,
               keep_days: float) -> Tuple[float, str]:
    """Backup → delete aged rows from PRUNABLE only → checkpoint.
    Returns (freed_mb, detail). Caller has already verified flat."""
    before, _ = sizes(db)
    bdir = ROOT / "archives"
    bdir.mkdir(exist_ok=True)
    bpath = bdir / f"matrix_prelight_prune_{time.strftime('%Y%m%d_%H%M%S')}.db"
    try:
        bk = sqlite3.connect(str(bpath))
        con.backup(bk)
        bk.close()
    except Exception as e:
        return 0.0, f"BACKUP FAILED — prune aborted: {e}"
    cutoff = time.time() - keep_days * 86400
    deleted: List[str] = []
    for t in PRUNABLE:
        if not table_exists(con, t):
            continue
        c = cols(con, t)
        tsc = next((x for x in ("created_at", "timestamp", "ts", "updated_at")
                    if x in c), None)
        if not tsc:
            continue
        try:
            cur = con.execute(
                f"DELETE FROM '{t}' WHERE COALESCE({tsc},0) < ? "
                f"AND COALESCE({tsc},0) > 0", (cutoff,))
            if cur.rowcount:
                deleted.append(f"{t}:{cur.rowcount}")
        except Exception:
            continue
    con.commit()
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA incremental_vacuum")
    except Exception:
        pass
    after, _ = sizes(db)
    return max(0.0, before - after), \
        f"backup={bpath.name}; deleted[{', '.join(deleted) or 'none'}]"


def write_state(intel_db: Optional[Path], s: Dict[str, Any]) -> None:
    con = connect_intel_rw(find_intel_db(str(intel_db) if intel_db else None))
    try:
        con.execute(DDL)
        con.execute(
            """INSERT INTO db_lights_state
               (id, db_mb, wal_mb, pct_of_max, colour, colour_hex,
                maintenance_state, entries_frozen, open_positions,
                last_prune_at, last_prune_freed_mb, next_prune_reason,
                updated_at)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET db_mb=excluded.db_mb,
                wal_mb=excluded.wal_mb, pct_of_max=excluded.pct_of_max,
                colour=excluded.colour, colour_hex=excluded.colour_hex,
                maintenance_state=excluded.maintenance_state,
                entries_frozen=excluded.entries_frozen,
                open_positions=excluded.open_positions,
                last_prune_at=COALESCE(excluded.last_prune_at,
                                       db_lights_state.last_prune_at),
                last_prune_freed_mb=COALESCE(excluded.last_prune_freed_mb,
                                       db_lights_state.last_prune_freed_mb),
                next_prune_reason=excluded.next_prune_reason,
                updated_at=excluded.updated_at""",
            (s["db_mb"], s["wal_mb"], s["pct"], s["colour"], s["hex"],
             s["maint"], s["frozen"], s["open_n"],
             s.get("last_prune_at"), s.get("freed_mb"),
             s["next_reason"], time.time()))
        con.commit()
    finally:
        con.close()


def cycle(matrix_db: Optional[str], intel_db: Optional[str],
          act: bool, keep_days: float) -> Dict[str, Any]:
    db = find_matrix_db(matrix_db)
    if db is None:
        print("[db_lights] matrix DB not found")
        return {}
    mb, wal = sizes(db)
    pct = 100.0 * mb / DB_MAX_MB
    colour, chex = band_for(pct)
    con = sqlite3.connect(str(db), timeout=10)
    try:
        open_n = open_position_count(con)
        frozen = 1 if (colour in ("RED", "VIOLET")) else 0
        maint, next_reason, freed, pruned_at = "NORMAL", "size below soft threshold", None, None

        if colour == "VIOLET":
            maint = "EMERGENCY"
            print(f"[db_lights] VIOLET emergency: {checkpoint_wal(con)}")
            next_reason = ("prune scheduled once flat"
                           if open_n else "flat — prune eligible NOW")
        elif colour == "RED":
            maint = "MAINTENANCE_DUE"
            next_reason = ("deferred: open positions present"
                           if open_n else "flat + hard threshold reached")
        elif mb >= SOFT_MB:
            maint = "WATCH"
            next_reason = f"soft threshold ({SOFT_MB:.0f}MB) exceeded"

        if act and colour in ("RED", "VIOLET") and open_n == 0:
            print("[db_lights] flat + threshold → safe prune...")
            freed, detail = safe_prune(db, con, keep_days)
            pruned_at = time.time()
            maint = "PRUNED"
            next_reason = f"pruned, freed {freed:.1f}MB ({detail})"
            mb, wal = sizes(db)
            pct = 100.0 * mb / DB_MAX_MB
            colour, chex = band_for(pct)
        elif act and colour in ("RED", "VIOLET") and open_n > 0:
            print(f"[db_lights] prune requested but {open_n} open position(s) "
                  f"— NEVER pruning with opens; entries frozen, exits alive")

        # advisory flags for UI + readiness gate
        set_cfg(con, "ENTRIES_FROZEN", frozen)
        set_cfg(con, "DB_LIGHTS_COLOUR", colour)
        set_cfg(con, "DB_LIGHTS_PCT", f"{pct:.1f}")
        set_cfg(con, "DB_MAINTENANCE_STATE", maint)
        con.commit()

        s = {"db_mb": round(mb, 2), "wal_mb": round(wal, 2),
             "pct": round(pct, 1), "colour": colour, "hex": chex,
             "maint": maint, "frozen": frozen, "open_n": open_n,
             "next_reason": next_reason, "freed_mb": freed,
             "last_prune_at": pruned_at}
        write_state(Path(intel_db) if intel_db else None, s)
        print(f"[db_lights] {mb:.1f}MB (+{wal:.1f} WAL) = {pct:.0f}% of "
              f"{DB_MAX_MB:.0f}MB → {colour} | {maint} | frozen={bool(frozen)}"
              f" | opens={open_n} | next: {next_reason}")
        return s
    finally:
        con.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--maintain", action="store_true")
    ap.add_argument("--keep-days", type=float, default=KEEP_DAYS_DEFAULT)
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--matrix-db", default=None)
    ap.add_argument("--intel-db", default=None)
    a = ap.parse_args()
    act = a.maintain and not a.check
    while True:
        cycle(a.matrix_db, a.intel_db, act=act or (not a.check and not a.once
                                                   and not a.maintain),
              keep_days=a.keep_days)
        if a.check or a.once or a.maintain:
            sys.exit(0)
        time.sleep(max(15.0, a.interval))
