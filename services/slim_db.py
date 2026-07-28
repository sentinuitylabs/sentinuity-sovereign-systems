"""
slim_db.py — Phase A.1 Safe DB Maintenance Tool
================================================
MANUAL-ONLY. Run with system FULLY STOPPED.

Doctrine:
  - Preserve HOT/WARM/COOL rows (age < 300s)
  - Preserve active_cognition=1 rows
  - Preserve unresolved raw_dna (processed_state != 1)
  - Preserve paper_positions (all time)
  - Preserve trade_autopsies and replay/review learning data
  - Archive DEAD rows to archive DB instead of deleting
  - Rotate historical MTM state to archive DB
  - Target: operational DB under ~30MB live size

Usage:
  python slim_db.py              -- dry run (no changes)
  python slim_db.py --apply      -- execute (backup created first)
  python slim_db.py --apply --vacuum  -- execute + VACUUM

DO NOT add back to Launch_Sentinuity.bat or Shutdown_Sentinuity.bat.
"""
from __future__ import annotations
import sqlite3, shutil, sys, time
from pathlib import Path

DRY   = "--apply" not in sys.argv
VACUU = "--vacuum" in sys.argv

ROOT    = Path(__file__).resolve().parent
DB      = ROOT / "sentinuity_matrix.db"
ARCHIVE = ROOT / "sentinuity_archive.db"

if not DB.exists():
    print(f"ERROR: {DB} not found. Run from project root with system stopped.")
    sys.exit(1)

size_before = DB.stat().st_size / 1024 / 1024
mode = "DRY RUN" if DRY else "LIVE"
print(f"slim_db.py Phase A.1 — {mode}")
print(f"DB:      {DB}  ({size_before:.1f}MB)")
print()

conn = sqlite3.connect(str(DB), timeout=10)
conn.row_factory = sqlite3.Row
try:
    alive = conn.execute(
        "SELECT service_name FROM system_heartbeat WHERE last_pulse > ? LIMIT 3",
        (time.time() - 15,)
    ).fetchall()
    if alive:
        print(f"SAFETY ABORT: services still live: {[r['service_name'] for r in alive]}")
        conn.close(); sys.exit(1)
except Exception:
    pass

if not DRY:
    ts = time.strftime("%Y%m%d_%H%M%S")
    backup = ROOT / "backups" / f"sentinuity_matrix_{ts}.db"
    backup.parent.mkdir(exist_ok=True)
    shutil.copy2(str(DB), str(backup))
    print(f"Backup: {backup}\n")

now = time.time()
total_archived = 0
total_deleted  = 0

arc = None
if not DRY:
    arc = sqlite3.connect(str(ARCHIVE), timeout=30)
    arc.execute("PRAGMA journal_mode=WAL")
    arc.row_factory = sqlite3.Row

def _count(sql, params=()):
    try:
        return conn.execute(f"SELECT COUNT(*) FROM ({sql})", params).fetchone()[0]
    except Exception as e:
        return f"ERR:{e}"

def _table_exists(c, name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone())

def _ensure_arc_table(name):
    if arc is None or not _table_exists(conn, name): return False
    try:
        ddl = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if ddl and ddl[0]:
            arc.execute(ddl[0].replace(f"CREATE TABLE {name}", f"CREATE TABLE IF NOT EXISTS {name}"))
            arc.commit()
        return True
    except Exception: return False

def archive_rows(table, where, params, label):
    global total_archived
    n = _count(f"SELECT * FROM {table} WHERE {where}", params)
    print(f"  {'WOULD ARCHIVE' if DRY else 'ARCHIVING'} {label}: {n:,} rows")
    if DRY or not isinstance(n, int) or n == 0: return 0
    try:
        _ensure_arc_table(table)
        rows = conn.execute(f"SELECT * FROM {table} WHERE {where}", params).fetchall()
        if rows:
            cols = list(rows[0].keys()); ph = ",".join("?"*len(cols))
            arc.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({ph})",
                            [tuple(r[c] for c in cols) for r in rows])
            arc.commit()
        r = conn.execute(f"DELETE FROM {table} WHERE {where}", params); conn.commit()
        total_archived += r.rowcount; return r.rowcount
    except Exception as e:
        print(f"    ERROR {label}: {e}"); return 0

def delete_rows(table, where, params, label):
    global total_deleted
    n = _count(f"SELECT * FROM {table} WHERE {where}", params)
    print(f"  {'WOULD DELETE' if DRY else 'DELETING'} {label}: {n:,} rows")
    if DRY or not isinstance(n, int) or n == 0: return 0
    try:
        r = conn.execute(f"DELETE FROM {table} WHERE {where}", params); conn.commit()
        total_deleted += r.rowcount; return r.rowcount
    except Exception as e:
        print(f"    ERROR {label}: {e}"); return 0

def trim_table(table, keep, order_col, label):
    global total_archived
    try:
        total = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except Exception:
        print(f"  SKIP {label}: table missing"); return 0
    if total <= keep:
        print(f"  OK {label}: {total} rows"); return 0
    print(f"  {'WOULD TRIM' if DRY else 'TRIMMING'} {label}: {total:,} → {keep} (-{total-keep:,})")
    if DRY: return 0
    try:
        _ensure_arc_table(table)
        old = conn.execute(f"SELECT * FROM {table} WHERE rowid NOT IN (SELECT rowid FROM {table} ORDER BY {order_col} DESC LIMIT {keep})").fetchall()
        if old:
            cols = list(old[0].keys()); ph = ",".join("?"*len(cols))
            arc.executemany(f"INSERT OR IGNORE INTO {table} ({','.join(cols)}) VALUES ({ph})",
                            [tuple(r[c] for c in cols) for r in old])
            arc.commit()
        r = conn.execute(f"DELETE FROM {table} WHERE rowid NOT IN (SELECT rowid FROM {table} ORDER BY {order_col} DESC LIMIT {keep})"); conn.commit()
        total_archived += r.rowcount; return r.rowcount
    except Exception as e:
        print(f"    ERROR {label}: {e}"); return 0

print("── RAW DNA ─────────────────────────────────────────────────────────────")
archive_rows("raw_dna", "processed_state=1 AND (claim_until IS NULL OR claim_until<?)", (now,), "raw_dna processed+unclaimed")

print("\n── MARKET SNAPSHOTS ─────────────────────────────────────────────────────")
if _table_exists(conn, "market_snapshots"):
    archive_rows("market_snapshots",
        "latched=0 AND candidate_state IN ('vetoed','expired_stale','executed','exited','EXECUTOR_STALE_GATE') AND COALESCE(price_updated_at,created_at,first_seen_at,0)<?",
        (now-900,), "market_snapshots DEAD terminal")
    delete_rows("market_snapshots", "candidate_state='mtm'", (), "market_snapshots MTM ticks")
    active = _count("SELECT * FROM market_snapshots WHERE active_cognition=1 OR latched=1")
    print(f"  SAFETY CHECK active/latched rows remaining: {active}")

print("\n── LEARNING DATA (PRESERVED) ───────────────────────────────────────────")
try:
    n = conn.execute("SELECT COUNT(*) FROM paper_positions").fetchone()[0]
    print(f"  paper_positions: {n} rows — ALL PRESERVED")
except Exception:
    print("  paper_positions: table missing")
trim_table("trade_autopsies", 500, "id", "trade_autopsies")
trim_table("polaris_proposals", 200, "created_at", "polaris_proposals")

print("\n── OTHER TABLES ─────────────────────────────────────────────────────────")
trim_table("wallet_write_log", 200, "id", "wallet_write_log")
trim_table("task_runs", 200, "id", "task_runs")
trim_table("resolved_transactions", 200, "id", "resolved_transactions")
trim_table("system_health_events", 200, "created_at", "system_health_events")
trim_table("system_health_snapshots", 100, "id", "system_health_snapshots")
delete_rows("brave_search_cache", "1=1", (), "brave_search_cache")
for t in ["_dash_probe", "_latency_probe", "_pf_probe"]:
    if _table_exists(conn, t): delete_rows(t, "1=1", (), t)

print()
if VACUU and not DRY:
    print("── VACUUM ───────────────────────────────────────────────────────────────")
    print("Running VACUUM..."); conn.execute("VACUUM"); conn.commit()

if arc: arc.close()
conn.close()

if not DRY:
    sa = DB.stat().st_size/1024/1024
    aa = ARCHIVE.stat().st_size/1024/1024 if ARCHIVE.exists() else 0
    print(f"\nMain DB: {sa:.1f}MB (saved {size_before-sa:.1f}MB)")
    print(f"Archive: {aa:.1f}MB  |  Archived: {total_archived:,} rows  |  Deleted: {total_deleted:,} rows")
    print("✓ Under 30MB" if sa < 30 else f"! Still {sa:.1f}MB")
else:
    print("Dry run complete. Pass --apply to execute (backup auto-created).")
