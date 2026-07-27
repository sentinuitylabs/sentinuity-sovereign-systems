# coding: utf-8
"""Create and validate the isolated council build plane.

This migration is idempotent and must run before council_autobuilder starts.
It never alters trading tables and never performs runtime schema changes.
"""
from __future__ import annotations
import json, sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
BUILD_DB = ROOT / "sentinuity_build.db"
MARKET_DB = ROOT / "sentinuity_matrix.db"

def migrate() -> dict:
    from services.council_autobuilder import ensure_schema
    from services import council_task_ledger as ledger
    ensure_schema(BUILD_DB)
    imported = ledger.import_sources(BUILD_DB, source_db_path=MARKET_DB)
    c = sqlite3.connect(str(BUILD_DB), timeout=2)
    try:
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA synchronous=NORMAL")
        c.execute("CREATE TABLE IF NOT EXISTS build_plane_meta(key TEXT PRIMARY KEY,value TEXT,updated_at REAL)")
        c.execute("INSERT INTO build_plane_meta(key,value,updated_at) VALUES('schema_version','COUNCIL_BUILD_PLANE_20260724',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (time.time(),))
        c.execute("INSERT INTO build_plane_meta(key,value,updated_at) VALUES('market_db_mode','READ_ONLY_FAIL_FAST',?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at", (time.time(),))
        c.commit()
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        c.close()
    required = {'council_task_ledger','council_task_transitions','council_task_evidence','code_patches','build_retrospectives','build_plane_meta'}
    missing = sorted(required - tables)
    if missing:
        raise RuntimeError(f"build DB missing tables: {missing}")
    return {'ok': True, 'build_db': str(BUILD_DB), 'imported': imported, 'missing': missing}

if __name__ == '__main__':
    print(json.dumps(migrate(), indent=2))
