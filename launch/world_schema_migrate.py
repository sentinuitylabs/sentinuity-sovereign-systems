# coding: utf-8
"""
launch/world_schema_migrate.py — SOVEREIGN_WORLD_UPGRADE_20260723
Idempotent. Safe to run on every launch, any number of times.

1. Creates world_buildings / world_agent_state / world_tools / world_events.
2. Adds new council_world_tasks columns:
   building_id, task_phase, evidence_ref, progress_weight,
   blocker_reason, collaboration_group.
3. Migrates every legacy world_location deterministically into the canonical
   building registry (LEGACY_LOCATION_MAP) and stamps building_id.
Writes only world_* tables and council_world_tasks. Never touches trading tables.
"""
from __future__ import annotations
import sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from services.world_build_state import (          # noqa: E402
    DB_PATH, LEGACY_LOCATION_MAP, canonical_location, ensure_schema,
)

TASK_COLS = [
    ("building_id", "TEXT"),
    ("task_phase", "TEXT"),
    ("evidence_ref", "TEXT"),
    ("progress_weight", "REAL DEFAULT 1.0"),
    ("blocker_reason", "TEXT"),
    ("collaboration_group", "TEXT"),
]


def migrate(db_path: Path | None = None) -> dict:
    db = Path(db_path or DB_PATH)
    ensure_schema(db)
    out = {"world_tables": True, "task_cols_added": [], "locations_migrated": 0}
    con = sqlite3.connect(str(db), timeout=10)
    try:
        con.execute("PRAGMA busy_timeout=8000")
        try:
            cols = {r[1] for r in con.execute("PRAGMA table_info(council_world_tasks)")}
        except Exception:
            cols = set()
        if cols:
            for name, decl in TASK_COLS:
                if name not in cols:
                    con.execute(f"ALTER TABLE council_world_tasks ADD COLUMN {name} {decl}")
                    out["task_cols_added"].append(name)
            for legacy, canon in LEGACY_LOCATION_MAP.items():
                cur = con.execute(
                    "UPDATE council_world_tasks SET building_id=?, world_location=? "
                    "WHERE world_location=? AND (building_id IS NULL OR building_id='')",
                    (canon, canon, legacy))
                out["locations_migrated"] += cur.rowcount
            # any row still without building_id gets its canonical mapping
            pk = "task_id" if "task_id" in cols else "id"
            for r in con.execute(f"SELECT {pk}, world_location FROM council_world_tasks "
                                 "WHERE building_id IS NULL OR building_id=''").fetchall():
                con.execute(f"UPDATE council_world_tasks SET building_id=? WHERE {pk}=?",
                            (canonical_location(r[1]), r[0]))
        con.commit()
    finally:
        con.close()
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(migrate(), indent=2))
