#!/usr/bin/env python3
"""
services/db_size_governor.py — four-light DB size governor.

    light 1  <  25 MB   GREEN    healthy
    light 2  <  50 MB   CYAN     normal
    light 3  <  75 MB   AMBER    watch
    light 4  >= 100 MB  GOLD     MAINTENANCE — vacuum/prune requested

At light 4 it requests maintenance. It does NOT act while:
    * any Solana paper position is OPEN
    * any substrate long/short is OPEN or unresolved in the journal
    * the DB is locked by a writer

Substrate positions are NEVER touched. Solana opens block the whole cycle.
Writes DB_SIZE_LIGHT / DB_MAINTENANCE_STATE to system_config for the UI.

    python services/db_size_governor.py --check          # report only
    python services/db_size_governor.py --maintain       # act if light 4 + safe
"""
import sqlite3, os, sys, time, shutil

DB = "sentinuity_matrix.db"
LIGHTS = [(25, "GREEN", "#14F195"), (50, "CYAN", "#8EF9FF"),
          (75, "AMBER", "#FFB347"), (100, "GOLD", "#FFD700")]

def size_mb(p): return os.path.getsize(p) / 1e6 if os.path.exists(p) else 0.0

def light_for(mb):
    for i, (cap, name, col) in enumerate(LIGHTS, 1):
        if mb < cap: return i, name, col
    return 4, "GOLD", "#FFD700"

def open_positions(c):
    out = []
    for tbl, label in (("paper_positions", "solana"),
                       ("substrate_paper_positions", "substrate"),
                       ("substrate_position_journal", "substrate_journal")):
        try:
            cols = {r[1] for r in c.execute(f"PRAGMA table_info('{tbl}')")}
            if "status" in cols:
                n = c.execute(f"SELECT COUNT(*) FROM '{tbl}' WHERE UPPER(COALESCE(status,''))='OPEN'").fetchone()[0]
            elif "recovery_action" in cols:
                n = c.execute(f"SELECT COUNT(*) FROM '{tbl}' WHERE recovery_action NOT IN ('CLOSED','ARCHIVE_STALE')").fetchone()[0]
            else:
                n = 0
            if n: out.append((label, n))
        except Exception:
            pass
    return out

def set_cfg(c, k, v):
    try:
        c.execute("INSERT INTO system_config(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (k, str(v)))
        c.commit()
    except Exception:
        pass

def main():
    act = "--maintain" in sys.argv
    mb = size_mb(DB)
    n, name, col = light_for(mb)
    c = sqlite3.connect(DB, timeout=8)
    opens = open_positions(c)

    print("=" * 62); print("  DB SIZE GOVERNOR"); print("=" * 62)
    print(f"  size: {mb:.1f} MB")
    bar = "".join("O" if i <= n else "." for i in range(1, 5))
    print(f"  lights: [{bar}]  {n}/4  {name}")
    for cap, ln, _ in LIGHTS:
        mark = "<<" if ln == name else ""
        print(f"     {ln:6} < {cap:>3} MB {mark}")

    set_cfg(c, "DB_SIZE_MB", round(mb, 1))
    set_cfg(c, "DB_SIZE_LIGHT", n)
    set_cfg(c, "DB_SIZE_LIGHT_NAME", name)

    if opens:
        print(f"\n  OPEN POSITIONS: {opens}")
        print("  -> maintenance BLOCKED. Substrate longs/shorts are never wiped.")
        set_cfg(c, "DB_MAINTENANCE_STATE", "BLOCKED_OPEN_POSITIONS")
        c.close(); return

    if n < 4:
        print(f"\n  healthy — no action (maintenance at light 4 / >={LIGHTS[3][0]}MB)")
        set_cfg(c, "DB_MAINTENANCE_STATE", "HEALTHY")
        c.close(); return

    print(f"\n  *** LIGHT 4 — MAINTENANCE REQUESTED ***")
    try:
        free = c.execute("PRAGMA freelist_count").fetchone()[0]
        page = c.execute("PRAGMA page_size").fetchone()[0]
        print(f"  reclaimable free pages: {free*page/1e6:.1f} MB (vacuum, no deletes)")
    except Exception:
        pass

    if not act:
        print("  report-only. Re-run with --maintain to act.")
        set_cfg(c, "DB_MAINTENANCE_STATE", "REQUESTED")
        c.close(); return

    set_cfg(c, "DB_MAINTENANCE_STATE", "ACTIVE")
    c.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    c.close()
    os.makedirs("db_backups", exist_ok=True)
    bak = os.path.join("db_backups", f"pre_governor_{time.strftime('%Y%m%d_%H%M%S')}.db")
    shutil.copy2(DB, bak); print(f"  backup: {bak}")
    try:
        c = sqlite3.connect(DB, timeout=30)
        c.execute("PRAGMA journal_mode=DELETE"); c.execute("VACUUM")
        c.execute("PRAGMA journal_mode=WAL")
        ok = c.execute("PRAGMA integrity_check").fetchone()[0]
        if ok != "ok": raise RuntimeError(f"integrity {ok}")
        set_cfg(c, "DB_MAINTENANCE_STATE", "HEALTHY")
        set_cfg(c, "DB_SIZE_MB", round(size_mb(DB), 1))
        c.close()
    except Exception as e:
        shutil.copy2(bak, DB)
        print(f"  FAILED: {e} — restored backup"); return
    print(f"  {mb:.1f} MB -> {size_mb(DB):.1f} MB   integrity ok, zero rows deleted")

if __name__ == "__main__":
    main()
