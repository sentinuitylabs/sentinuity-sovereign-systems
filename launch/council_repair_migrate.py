#!/usr/bin/env python3
"""
launch/council_repair_migrate.py — COUNCIL_REPAIR_20260729

Idempotent migration for the Council chain repair.

Guarantees:
  * inspects the ACTUAL existing schema before touching anything
  * preserves every compatible existing row
  * creates the missing canonical tables
  * copies classifiable rows safely
  * NEVER drops the old table
  * writes a migration report
  * can be re-run harmlessly

Usage:
    python launch/council_repair_migrate.py --db sentinuity_build.db --dry-run
    python launch/council_repair_migrate.py --db sentinuity_build.db
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

REPORT: dict = {"started_at": time.time(), "actions": [], "warnings": [],
                "dry_run": False, "db": ""}


def act(msg: str) -> None:
    REPORT["actions"].append(msg)
    print(f"  [DO]   {msg}")


def skip(msg: str) -> None:
    REPORT["actions"].append(f"(skip) {msg}")
    print(f"  [SKIP] {msg}")


def warn(msg: str) -> None:
    REPORT["warnings"].append(msg)
    print(f"  [WARN] {msg}")


def cols(c: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in c.execute(f'PRAGMA table_info("{table}")')}
    except sqlite3.Error:
        return set()


def has_table(c: sqlite3.Connection, table: str) -> bool:
    return bool(c.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone())


def add_columns(c: sqlite3.Connection, table: str, spec: dict, dry: bool) -> None:
    if not has_table(c, table):
        skip(f"{table} absent; nothing to extend")
        return
    present = cols(c, table)
    for name, decl in spec.items():
        if name in present:
            continue
        if dry:
            act(f"WOULD add {table}.{name} {decl}")
            continue
        c.execute(f'ALTER TABLE "{table}" ADD COLUMN "{name}" {decl}')
        act(f"added {table}.{name} {decl}")


# ── canonical DDL ───────────────────────────────────────────────────────────
DDL_STAGE = """
CREATE TABLE IF NOT EXISTS council_stage_evidence(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_key TEXT, stage TEXT, artifact_kind TEXT, artifact_ref TEXT,
    delta_summary TEXT, is_spin INTEGER DEFAULT 0, run_id TEXT,
    attempt_id TEXT, actor TEXT, status TEXT, blocker TEXT,
    previous_stage TEXT, ts REAL, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_cse_task ON council_stage_evidence(task_key, ts);
CREATE INDEX IF NOT EXISTS idx_cse_attempt ON council_stage_evidence(attempt_id);
"""

DDL_RESEARCH = """
CREATE TABLE IF NOT EXISTS council_research_evidence(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    canonical_id INTEGER, kind TEXT, summary TEXT, source TEXT,
    payload_json TEXT, sample_size INTEGER, freshness_sec REAL,
    confidence REAL, methodology TEXT, limitations TEXT, created_at REAL);
CREATE INDEX IF NOT EXISTS idx_cre_task ON council_research_evidence(canonical_id);
"""

DDL_FAILURES = """
CREATE TABLE IF NOT EXISTS council_stage_write_failures(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL, task_key TEXT, run_id TEXT, phase TEXT, db_path TEXT,
    expected_schema TEXT, actual_columns TEXT, exception TEXT);
"""

DDL_GAPS = """
CREATE TABLE IF NOT EXISTS council_capability_gaps_v2(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    missing_handler_key TEXT NOT NULL, original_task_id INTEGER NOT NULL,
    reason TEXT, detected_at REAL NOT NULL, required_inputs TEXT,
    expected_outputs TEXT, status TEXT DEFAULT 'OPEN', restored_at REAL,
    child_task_id INTEGER,
    UNIQUE(missing_handler_key, original_task_id));
"""

TASK_COLUMNS = {
    "task_key": "TEXT",
    "handler_key": "TEXT",
    "task_type": "TEXT",
    "network_requirement": "TEXT DEFAULT 'LOCAL'",
    "attempt_id": "TEXT",
    "base_sha256": "TEXT",
}

PROPOSAL_COLUMNS = {
    "base_sha256": "TEXT",
    "target_file": "TEXT",
    "risk_tier": "TEXT",
    "lifecycle_state": "TEXT DEFAULT 'OPEN'",
}

PATCH_COLUMNS = {
    "base_sha256": "TEXT",
    "result_sha256": "TEXT",
    "attempt_id": "TEXT",
    "apply_stage": "TEXT",
}


def migrate(db_path: Path, dry: bool) -> int:
    REPORT["db"] = str(db_path)
    REPORT["dry_run"] = dry
    if not db_path.exists():
        warn(f"{db_path} does not exist — nothing to migrate")
        return 0

    c = sqlite3.connect(str(db_path), timeout=30.0)
    c.execute("PRAGMA busy_timeout=20000")
    try:
        print(f"\n-- {db_path.name} --")

        # 1. canonical tables
        for name, ddl in (("council_stage_evidence", DDL_STAGE),
                          ("council_research_evidence", DDL_RESEARCH),
                          ("council_stage_write_failures", DDL_FAILURES),
                          ("council_capability_gaps_v2", DDL_GAPS)):
            if has_table(c, name):
                skip(f"{name} already present")
            elif dry:
                act(f"WOULD create {name}")
            else:
                c.executescript(ddl)
                act(f"created {name}")

        # 2. classify and copy rows out of the shared legacy table.
        #    The legacy table is NEVER dropped.
        legacy = cols(c, "council_task_evidence")
        if legacy:
            looks_stage = "task_key" in legacy
            looks_research = "canonical_id" in legacy
            act(f"legacy council_task_evidence columns: {sorted(legacy)}")
            if looks_stage and not dry:
                # Legacy stage schemas vary across databases. Build the SELECT
                # from columns that actually exist instead of assuming newer
                # telemetry fields such as run_id/actor/status are present.
                def _legacy_expr(name: str, fallback: str = "NULL") -> str:
                    return name if name in legacy else fallback

                stage_select = ", ".join([
                    _legacy_expr("task_key"),
                    _legacy_expr("stage", "'UNKNOWN'"),
                    _legacy_expr("artifact_kind"),
                    _legacy_expr("artifact_ref"),
                    _legacy_expr("delta_summary"),
                    ("COALESCE(is_spin,0)" if "is_spin" in legacy else "0"),
                    _legacy_expr("run_id"),
                    _legacy_expr("actor", "'LEGACY_MIGRATION'"),
                    _legacy_expr("status", "'MIGRATED'"),
                    _legacy_expr("blocker"),
                    _legacy_expr("previous_stage"),
                    _legacy_expr("ts", "strftime('%s','now')"),
                    _legacy_expr("ts", "strftime('%s','now')"),
                ])
                stage_value = _legacy_expr("stage", "'UNKNOWN'")
                ts_value = _legacy_expr("ts", "strftime('%s','now')")
                sql = (
                    "INSERT INTO council_stage_evidence(task_key, stage,"
                    " artifact_kind, artifact_ref, delta_summary, is_spin,"
                    " run_id, actor, status, blocker, previous_stage, ts,"
                    " created_at) SELECT " + stage_select +
                    " FROM council_task_evidence WHERE task_key IS NOT NULL"
                    " AND NOT EXISTS (SELECT 1 FROM council_stage_evidence s"
                    "   WHERE s.task_key=council_task_evidence.task_key"
                    "     AND s.ts=" + ts_value +
                    "     AND s.stage=" + stage_value + ")"
                )
                n = c.execute(sql).rowcount
                act(f"copied {max(n,0)} stage row(s) -> council_stage_evidence")
            elif looks_stage:
                act("WOULD copy stage rows -> council_stage_evidence")

            if looks_research and not dry:
                n = c.execute(
                    "INSERT INTO council_research_evidence(canonical_id, kind,"
                    " summary, payload_json, sample_size, freshness_sec,"
                    " confidence, methodology, limitations, created_at)"
                    " SELECT canonical_id, kind, summary, data, sample_size,"
                    " freshness_sec, confidence, methodology, limitations, ts"
                    " FROM council_task_evidence WHERE canonical_id IS NOT NULL"
                    " AND NOT EXISTS (SELECT 1 FROM council_research_evidence r"
                    "   WHERE r.canonical_id=council_task_evidence.canonical_id"
                    "     AND r.created_at=council_task_evidence.ts)").rowcount
                act(f"copied {max(n,0)} research row(s) -> council_research_evidence")
            elif looks_research:
                act("WOULD copy research rows -> council_research_evidence")

            if not (looks_stage or looks_research):
                warn("legacy council_task_evidence matches neither contract; "
                     "left untouched for manual review")
            act("legacy council_task_evidence retained (never dropped)")
        else:
            skip("no legacy council_task_evidence table")

        # 3. typed task / proposal / patch columns
        add_columns(c, "council_task_ledger", TASK_COLUMNS, dry)
        add_columns(c, "polaris_proposals", PROPOSAL_COLUMNS, dry)
        add_columns(c, "code_patches", PATCH_COLUMNS, dry)

        # 4. backfill task_key + handler_key from titles (migration only)
        if has_table(c, "council_task_ledger") and not dry:
            try:
                from services.council_handler_registry import infer_handler_key
            except Exception:
                infer_handler_key = lambda t: ""      # noqa: E731
            rows = c.execute(
                "SELECT canonical_id, title, handler_key, task_key FROM"
                " council_task_ledger").fetchall()
            n_hk = n_tk = 0
            for cid, title, hk, tk in rows:
                if not tk:
                    key = (title or f"task_{cid}").strip().upper()
                    key = "".join(ch if ch.isalnum() else "_" for ch in key)[:80]
                    c.execute("UPDATE council_task_ledger SET task_key=?"
                              " WHERE canonical_id=?", (key, cid))
                    n_tk += 1
                if not hk:
                    inferred = infer_handler_key(title or "")
                    if inferred:
                        c.execute("UPDATE council_task_ledger SET handler_key=?"
                                  " WHERE canonical_id=?", (inferred, cid))
                        n_hk += 1
            act(f"backfilled task_key on {n_tk} row(s), "
                f"handler_key on {n_hk} row(s) via LEGACY_TITLE_MAP")
        elif dry:
            act("WOULD backfill task_key/handler_key from titles")

        # 5. proposals default to a non-terminal lifecycle state so the cleaner
        #    cannot treat pre-existing rows as archivable
        if has_table(c, "polaris_proposals") and not dry:
            n = c.execute(
                "UPDATE polaris_proposals SET lifecycle_state='OPEN'"
                " WHERE lifecycle_state IS NULL OR lifecycle_state=''").rowcount
            act(f"defaulted lifecycle_state on {max(n,0)} proposal row(s)")

        if dry:
            c.rollback()
            print("  (dry run — rolled back)")
        else:
            c.commit()
    finally:
        c.close()
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="append", default=None,
                    help="database to migrate (repeatable)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--report", default="council_repair_migration_report.json")
    a = ap.parse_args()

    dbs = [Path(d) for d in (a.db or ["sentinuity_build.db"])]
    print("=" * 70)
    print("  COUNCIL REPAIR MIGRATION" + ("  (DRY RUN)" if a.dry_run else ""))
    print("=" * 70)
    for d in dbs:
        migrate(d if d.is_absolute() else ROOT / d, a.dry_run)

    REPORT["finished_at"] = time.time()
    Path(a.report).write_text(json.dumps(REPORT, indent=2), encoding="utf-8")
    print(f"\n  actions={len(REPORT['actions'])} warnings={len(REPORT['warnings'])}")
    print(f"  report: {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
