#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import time
from pathlib import Path

ACTIVE_STATES = {"OPEN", "ACTIVE", "PENDING", "LIVE", "EXECUTING"}

# Exact policies derived from the operator's 91.2188 MB database schema.
KEEP_TAIL = {
    "api_usage_ledger": 200,
    "cognition_log": 300,
    "copytrade_influence_ledger": 500,
    "council_model_assignments": 200,
    "council_model_evolution_log": 200,
    "council_stalemates": 100,
    "council_task_evidence": 250,
    "council_work_queue": 250,
    "council_world_tasks": 100,
    "debate_log": 300,
    "env_integrity_snapshots": 100,
    "exit_watch_telemetry": 250,
    "forge_research_cache": 100,
    "improvement_queue": 250,
    "legacy_cluster_candidates": 300,
    "lilypad_harvest_events": 300,
    "live_escalation_ledger": 300,
    "live_shadow_ledger": 300,
    "mark_quarantine": 200,
    "mark_tape": 500,
    "market_snapshots": 300,
    "mode_b_decision_ledger": 500,
    "momentum_gate_audit": 300,
    "paper_executions": 500,
    "polaris_trade_reviews": 250,
    "raw_dna": 500,
    "resolved_transactions": 500,
    "runner_likelihood_scores": 500,
    "security_events": 200,
    "shadow_runners": 500,
    "smart_wallet_events": 500,
    "smart_wallet_trades": 1000,
    "substrate_council_votes": 300,
    "substrate_execution_audit": 300,
    "substrate_opportunities": 500,
    "substrate_provider_health": 100,
    "substrate_strategy_results": 300,
    "substrate_strategy_signals": 300,
    "substrate_trade_log": 300,
    "system_health_events": 250,
    "task_runs": 200,
    "telegram_anomaly_events": 200,
    "telegram_calls": 250,
    "token_metrics": 500,
    "trade_afterlife_metrics": 500,
    "trade_autopsies": 300,
    "trade_lifecycle_events": 400,
    "ui_recent_trade_feed_cache": 1200,
    "wallet_entry_likelihood_signals": 350,
    "wallet_pattern_observations": 500,
    "wallet_transactions": 1000,
    "wallet_write_log": 250,
    "winner_snapshot_archive": 300,
    "world_command_log": 200,
}

# These are pure archives/snapshots in the hot matrix DB. A complete pre-prune
# database backup is retained, so deleting them from the hot DB is reversible.
CLEAR_TABLES = {
    "active_pipeline_stale_archive",
}

POSITION_TABLES = {
    "paper_positions": 300,
    "substrate_paper_positions": 200,
    "substrate_position_journal": 200,
    "substrate_positions": 200,
    "substrate_live_orders": 100,
}

def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None

def cols(con: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in con.execute(f"PRAGMA table_info({q(table)})")]

def count(con: sqlite3.Connection, table: str) -> int:
    return int(con.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0])

def db_mb(path: Path) -> float:
    return round(path.stat().st_size / 1048576, 3)

def retain_latest_rows(con: sqlite3.Connection, table: str, keep: int) -> dict:
    before = count(con, table)
    if before <= keep:
        return {"before": before, "after": before, "deleted": 0}
    con.execute(
        f"DELETE FROM {q(table)} WHERE rowid NOT IN "
        f"(SELECT rowid FROM {q(table)} ORDER BY rowid DESC LIMIT ?)",
        (keep,),
    )
    after = count(con, table)
    return {"before": before, "after": after, "deleted": before - after}

def retain_positions(con: sqlite3.Connection, table: str, keep_closed: int) -> dict:
    before = count(con, table)
    table_cols = set(cols(con, table))
    status_col = next((c for c in ("status", "state") if c in table_cols), None)
    if not status_col:
        return {"before": before, "after": before, "deleted": 0,
                "skipped": "no status/state column"}
    placeholders = ",".join("?" for _ in ACTIVE_STATES)
    sql = f"""
        DELETE FROM {q(table)}
        WHERE UPPER(COALESCE(CAST({q(status_col)} AS TEXT),'')) NOT IN ({placeholders})
          AND rowid NOT IN (
              SELECT rowid FROM {q(table)}
              WHERE UPPER(COALESCE(CAST({q(status_col)} AS TEXT),'')) NOT IN ({placeholders})
              ORDER BY rowid DESC LIMIT ?
          )
    """
    params = tuple(ACTIVE_STATES) + tuple(ACTIVE_STATES) + (keep_closed,)
    con.execute(sql, params)
    after = count(con, table)
    return {"before": before, "after": after, "deleted": before - after}

def compact_config_snapshots(con: sqlite3.Connection) -> dict:
    table = "code_vault_config_snapshots"
    if not exists(con, table):
        return {}
    before = count(con, table)
    cs = set(cols(con, table))
    if "key" not in cs:
        return retain_latest_rows(con, table, 100)
    # Keep the newest value for each configuration key, not merely ten rows globally.
    con.execute(f"""
        DELETE FROM {q(table)}
        WHERE rowid NOT IN (
            SELECT MAX(rowid) FROM {q(table)} GROUP BY {q("key")}
        )
    """)
    after = count(con, table)
    return {"before": before, "after": after, "deleted": before - after,
            "policy": "latest row per key"}

def compact_code_snapshots(con: sqlite3.Connection) -> dict:
    table = "code_vault_snapshots"
    if not exists(con, table):
        return {}
    before = count(con, table)
    cs = set(cols(con, table))
    path_col = next((c for c in ("file_path", "file_name") if c in cs), None)
    if not path_col:
        return retain_latest_rows(con, table, 10)
    # Keep one current snapshot per source file. The full DB backup preserves all history.
    con.execute(f"""
        DELETE FROM {q(table)}
        WHERE rowid NOT IN (
            SELECT MAX(rowid) FROM {q(table)} GROUP BY {q(path_col)}
        )
    """)
    after = count(con, table)
    return {"before": before, "after": after, "deleted": before - after,
            "policy": f"latest row per {path_col}"}

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--report", required=True)
    args = ap.parse_args()

    db = Path(args.db).resolve()
    report_path = Path(args.report).resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    backup_dir = db.parent / "db_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = backup_dir / f"{db.stem}.FULL_before_targeted_v5_{stamp}.db"

    report = {
        "database": str(db),
        "before_mb": db_mb(db),
        "backup": str(backup),
        "operations": {},
        "warnings": [],
    }

    con = sqlite3.connect(db, timeout=180)
    con.execute("PRAGMA busy_timeout=180000")

    quick = con.execute("PRAGMA quick_check").fetchone()[0]
    if quick != "ok":
        con.close()
        raise SystemExit(f"SAFETY ABORT: quick_check={quick}")

    # Online backup API gives a consistent complete copy even in WAL mode.
    backup_con = sqlite3.connect(backup)
    con.backup(backup_con)
    backup_con.close()

    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        for table in sorted(CLEAR_TABLES):
            if exists(con, table):
                before = count(con, table)
                con.execute(f"DELETE FROM {q(table)}")
                report["operations"][table] = {
                    "before": before, "after": 0, "deleted": before,
                    "policy": "clear hot archive; full backup retained",
                }

        report["operations"]["code_vault_config_snapshots"] = compact_config_snapshots(con)
        report["operations"]["code_vault_snapshots"] = compact_code_snapshots(con)

        for table, keep in sorted(KEEP_TAIL.items()):
            if exists(con, table):
                report["operations"][table] = retain_latest_rows(con, table, keep)

        for table, keep_closed in POSITION_TABLES.items():
            if exists(con, table):
                report["operations"][table] = retain_positions(con, table, keep_closed)

        # Keep only one heartbeat row per service where possible.
        for table in ("system_heartbeat", "service_heartbeats"):
            if not exists(con, table):
                continue
            cs = set(cols(con, table))
            service_col = next((c for c in ("service_name", "service", "name", "component") if c in cs), None)
            before = count(con, table)
            if service_col:
                con.execute(f"""
                    DELETE FROM {q(table)}
                    WHERE rowid NOT IN (
                        SELECT MAX(rowid) FROM {q(table)} GROUP BY {q(service_col)}
                    )
                """)
            else:
                con.execute(
                    f"DELETE FROM {q(table)} WHERE rowid NOT IN "
                    f"(SELECT rowid FROM {q(table)} ORDER BY rowid DESC LIMIT 50)"
                )
            after = count(con, table)
            report["operations"][table] = {
                "before": before, "after": after, "deleted": before - after,
                "policy": "latest heartbeat per service" if service_col else "latest 50",
            }

        con.commit()
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("VACUUM")
        con.execute("PRAGMA optimize")
        con.execute("PRAGMA journal_mode=WAL")
        con.commit()

        quick_after = con.execute("PRAGMA quick_check").fetchone()[0]
        if quick_after != "ok":
            raise RuntimeError(f"post-prune quick_check={quick_after}")

        report["quick_check"] = quick_after
        report["after_mb"] = db_mb(db)
        report["reclaimed_mb"] = round(report["before_mb"] - report["after_mb"], 3)
    except Exception:
        con.close()
        shutil.copy2(backup, db)
        raise
    finally:
        try:
            con.close()
        except Exception:
            pass

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps(report, indent=2))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"TARGETED COMPACTOR FAILED: {exc}", file=sys.stderr)
        raise
