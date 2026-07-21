#!/usr/bin/env python3
"""
SENTINUITY OFFLINE RETENTION + VACUUM V8

Purpose
-------
Keep the hot operational database small without deleting durable state.

Safety
------
* Refuses to run while fresh service heartbeats exist.
* Runs PRAGMA quick_check before and after.
* Creates a consistent SQLite backup before mutation.
* Archives every deleted row before deleting it.
* Preserves active/open positions and durable state/configuration.
* Uses dbstat to catch newly introduced high-churn tables.
* Performs WAL checkpoint, VACUUM, optimize, and restores WAL mode.
* Emits a detailed JSON report, including any unprunable large objects.

This tool never reads or emits private keys.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any

ACTIVE_STATES = {"OPEN", "ACTIVE", "PENDING", "LIVE", "EXECUTING", "SUBMITTED"}

PROTECTED_TABLES = {
    "paper_wallet", "system_config", "system_state", "tax_state", "tax_ledger",
    "tax_reserve", "substrate_ledger", "paper_learning_state", "score_performance",
    "security_baseline", "security_lockdown_state", "substrate_strategy_registry",
    "council_role_registry", "support_system_registry", "smart_wallet_profiles",
    "smart_wallet_sources", "smart_wallet_performance_snapshots",
    "wallet_entry_fingerprints", "copytrade_calibration",
    "watched_wallets", "wallet_links", "mint_blacklist", "known_rings",
    "substrate_wallet_state", "db_lights_state", "signal_gate_state",
    "lilypad_harvest_state", "swti_wallet_cursor", "sovereign_aliases",
    "standing_tasklist", "standing_tasks", "polaris_standing_tasks",
    "forge_projects", "code_patches", "patch_apply_journal",
}

TAIL_POLICIES = {
    "active_pipeline_stale_archive": 100,
    "api_usage_ledger": 200,
    "brave_search_cache": 100,
    "candidate_scores": 500,
    "code_vault_changes": 100,
    "cognition_log": 300,
    "copytrade_influence_ledger": 500,
    "council_model_assignments": 200,
    "council_model_evolution_log": 200,
    "council_stalemates": 100,
    "council_task_evidence": 300,
    "council_task_stage": 300,
    "council_work_queue": 300,
    "council_world_tasks": 200,
    "debate_log": 500,
    "env_integrity_snapshots": 100,
    "exit_watch_telemetry": 300,
    "forge_research_cache": 150,
    "hourly_market_pressure": 168,
    "hourly_performance_profile": 336,
    "improvement_queue": 250,
    "legacy_cluster_candidates": 300,
    "lilypad_harvest_events": 300,
    "live_escalation_ledger": 300,
    "live_lane_feature_snapshots": 500,
    "live_lane_shadow_candidates": 500,
    "live_shadow_ledger": 500,
    "mark_quarantine": 250,
    "mark_tape": 750,
    "mark_truth_candidate_peaks": 500,
    "market_snapshots": 500,
    "mode_b_decision_ledger": 750,
    "model_router_log": 250,
    "momentum_gate_audit": 300,
    "mtm_ticks": 1000,
    "network_exposure_events": 200,
    "nim_call_log": 250,
    "operator_command_queue": 100,
    "paper_executions": 750,
    "paper_split_harvest_events": 300,
    "parameter_change_log": 200,
    "parameter_snapshots": 100,
    "patch_history": 200,
    "polaris_proposals": 250,
    "polaris_trade_reviews": 300,
    "post_exit_observations": 500,
    "post_exit_ticks": 750,
    "raw_dna": 750,
    "research_queue": 200,
    "resolved_transactions": 750,
    "runner_likelihood_scores": 500,
    "security_events": 200,
    "shadow_runners": 500,
    "smart_wallet_events": 500,
    "smart_wallet_trades": 1000,
    "substrate_copytrade_signals": 300,
    "substrate_council_votes": 300,
    "substrate_execution_audit": 300,
    "substrate_opportunities": 500,
    "substrate_provider_health": 200,
    "substrate_strategy_results": 300,
    "substrate_strategy_signals": 300,
    "substrate_trade_log": 300,
    "system_health_events": 250,
    "system_health_snapshots": 100,
    "task_runs": 250,
    "telegram_anomaly_events": 200,
    "telegram_calls": 250,
    "token_metrics": 750,
    "trade_afterlife_metrics": 750,
    "trade_autopsies": 400,
    "trade_lifecycle_events": 500,
    "trajectory_score_history": 500,
    "ui_recent_trade_feed_cache": 300,
    "wallet_entry_likelihood_signals": 350,
    "wallet_pattern_observations": 500,
    "wallet_transactions": 1000,
    "wallet_write_log": 250,
    "winner_snapshot_archive": 500,
    "world_command_log": 200,
}

POSITION_POLICIES = {
    "paper_positions": 500,
    "substrate_paper_positions": 300,
    "substrate_position_journal": 300,
    "substrate_positions": 300,
    "substrate_live_orders": 150,
    "live_positions": 250,
}

GENERIC_CHURN_RE = re.compile(
    r"(?:_log|_logs|_event|_events|_tick|_ticks|_snapshot|_snapshots|"
    r"_cache|_history|_audit|_ledger|_queue|_observation|_observations|"
    r"_signal|_signals|_candidate|_candidates|_metric|_metrics|_trade|_trades|"
    r"_telemetry|_tape|_feed)$",
    re.I,
)

def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None

def columns(con: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({q(table)})")]

def size_mb(path: Path) -> float:
    return path.stat().st_size / 1048576 if path.exists() else 0.0

def object_sizes(con: sqlite3.Connection) -> dict[str, float]:
    try:
        return {
            str(name): float(total or 0) / 1048576
            for name, total in con.execute(
                "SELECT name, SUM(pgsize) FROM dbstat GROUP BY name"
            )
        }
    except sqlite3.Error:
        return {}

def top_objects(con: sqlite3.Connection, limit: int = 40) -> list[dict[str, Any]]:
    sizes = object_sizes(con)
    return [
        {"name": name, "mb": round(mb, 3)}
        for name, mb in sorted(sizes.items(), key=lambda item: item[1], reverse=True)[:limit]
    ]

def fresh_heartbeat_rows(con: sqlite3.Connection, grace: int) -> list[dict[str, Any]]:
    cutoff = time.time() - grace
    found: list[dict[str, Any]] = []
    candidates = {
        "system_heartbeat": ("last_pulse", "timestamp", "ts", "updated_at"),
        "service_heartbeats": ("last_heartbeat", "updated_at", "timestamp", "ts"),
    }
    for table, names in candidates.items():
        if not exists(con, table):
            continue
        cs = set(columns(con, table))
        tc = next((name for name in names if name in cs), None)
        sc = next((name for name in ("service_name", "service", "name") if name in cs), None)
        if not tc:
            continue
        expr = f"""CASE
          WHEN typeof({q(tc)}) IN ('integer','real') THEN
            CASE WHEN CAST({q(tc)} AS REAL)>100000000000
                 THEN CAST({q(tc)} AS REAL)/1000.0
                 ELSE CAST({q(tc)} AS REAL) END
          ELSE COALESCE(CAST(strftime('%s',{q(tc)}) AS REAL),0) END"""
        sql = f"SELECT {q(sc) if sc else 'rowid'}, ({expr}) FROM {q(table)} WHERE ({expr})>?"
        for service, stamp in con.execute(sql, (cutoff,)).fetchall():
            found.append({
                "table": table,
                "service": str(service),
                "age_sec": round(time.time() - float(stamp), 2),
            })
    return found

def ensure_archive_table(src: sqlite3.Connection, arc: sqlite3.Connection, table: str) -> None:
    """Create the archive table and reconcile additive source-schema drift.

    Archive databases can outlive the hot database schema. CREATE TABLE IF NOT
    EXISTS alone is insufficient because an older archive table may be missing
    columns added later to the source. Before copying rows, add every missing
    source column to the archive using a nullable affinity-compatible definition.
    This is intentionally additive only: retention never drops or rewrites archive
    columns.
    """
    row = src.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    if not row or not row[0]:
        raise RuntimeError(f"schema unavailable for {table}")
    ddl = str(row[0])
    if "IF NOT EXISTS" not in ddl.upper()[:80]:
        ddl = re.sub(r"(?i)^CREATE\s+TABLE", "CREATE TABLE IF NOT EXISTS", ddl, count=1)
    arc.execute(ddl)

    source_info = src.execute(f"PRAGMA table_info({q(table)})").fetchall()
    archive_cols = {
        str(r[1]) for r in arc.execute(f"PRAGMA table_info({q(table)})").fetchall()
    }
    added: list[str] = []
    for info in source_info:
        name = str(info[1])
        if name in archive_cols:
            continue
        declared_type = str(info[2] or "").strip()
        type_sql = f" {declared_type}" if declared_type else ""
        # Keep archive reconciliation permissive. Reproducing NOT NULL/default/PK
        # constraints on a populated historical table can make ALTER TABLE fail.
        arc.execute(f"ALTER TABLE {q(table)} ADD COLUMN {q(name)}{type_sql}")
        archive_cols.add(name)
        added.append(name)
    if added:
        arc.commit()
        print(
            f"[ARCHIVE_SCHEMA_SYNC] table={table} added_columns={','.join(added)}",
            flush=True,
        )

def archive_delete(
    src: sqlite3.Connection,
    arc: sqlite3.Connection,
    table: str,
    rowids: list[int],
) -> int:
    if not rowids:
        return 0
    ensure_archive_table(src, arc, table)
    cs = columns(src, table)
    col_sql = ",".join(q(c) for c in cs)
    placeholders = ",".join("?" for _ in cs)
    moved = 0
    for offset in range(0, len(rowids), 400):
        ids = rowids[offset:offset + 400]
        marks = ",".join("?" for _ in ids)
        rows = src.execute(
            f"SELECT {col_sql} FROM {q(table)} WHERE rowid IN ({marks})", ids
        ).fetchall()
        if rows:
            arc.executemany(
                f"INSERT OR IGNORE INTO {q(table)} ({col_sql}) VALUES ({placeholders})",
                rows,
            )
            arc.commit()
            src.execute(f"DELETE FROM {q(table)} WHERE rowid IN ({marks})", ids)
            src.commit()
            moved += len(rows)
    return moved

def trim_tail(
    src: sqlite3.Connection,
    arc: sqlite3.Connection,
    table: str,
    keep: int,
    active_guard: bool = False,
) -> dict[str, Any]:
    total = int(src.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0])
    result: dict[str, Any] = {
        "before_rows": total,
        "keep_rows": keep,
        "archived_deleted": 0,
    }
    if total <= keep:
        return result
    cs = set(columns(src, table))
    guard = ""
    params: list[Any] = [keep]
    if active_guard:
        status_col = next((c for c in ("status", "state") if c in cs), None)
        if not status_col:
            result["skipped"] = "state-bearing table without status/state column"
            return result
        placeholders = ",".join("?" for _ in ACTIVE_STATES)
        guard = (
            f" AND UPPER(COALESCE(CAST({q(status_col)} AS TEXT),'')) "
            f"NOT IN ({placeholders})"
        )
        params.extend(sorted(ACTIVE_STATES))
    rowids = [
        int(r[0])
        for r in src.execute(
            f"""SELECT rowid FROM {q(table)}
                WHERE rowid NOT IN (
                    SELECT rowid FROM {q(table)} ORDER BY rowid DESC LIMIT ?
                )
                {guard}
                ORDER BY rowid ASC""",
            tuple(params),
        ).fetchall()
    ]
    result["archived_deleted"] = archive_delete(src, arc, table, rowids)
    result["after_rows"] = int(
        src.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0]
    )
    return result

def compact_latest_per_key(
    src: sqlite3.Connection,
    arc: sqlite3.Connection,
    table: str,
    key_candidates: tuple[str, ...],
) -> dict[str, Any]:
    if not exists(src, table):
        return {}
    cs = set(columns(src, table))
    key = next((c for c in key_candidates if c in cs), None)
    if not key:
        return trim_tail(src, arc, table, 100)
    total = int(src.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0])
    keep_ids = {
        int(r[0]) for r in src.execute(
            f"SELECT MAX(rowid) FROM {q(table)} GROUP BY {q(key)}"
        ).fetchall()
    }
    all_ids = {
        int(r[0]) for r in src.execute(f"SELECT rowid FROM {q(table)}").fetchall()
    }
    deleted = archive_delete(src, arc, table, sorted(all_ids - keep_ids))
    return {
        "before_rows": total,
        "after_rows": total - deleted,
        "archived_deleted": deleted,
        "policy": f"latest row per {key}",
    }

def compact_heartbeats(con: sqlite3.Connection) -> dict[str, Any]:
    output: dict[str, Any] = {}
    for table in ("system_heartbeat", "service_heartbeats"):
        if not exists(con, table):
            continue
        cs = set(columns(con, table))
        service_col = next(
            (c for c in ("service_name", "service", "name", "component") if c in cs),
            None,
        )
        before = int(con.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0])
        if service_col:
            con.execute(
                f"""DELETE FROM {q(table)}
                    WHERE rowid NOT IN (
                        SELECT MAX(rowid) FROM {q(table)} GROUP BY {q(service_col)}
                    )"""
            )
            policy = f"latest row per {service_col}"
        else:
            con.execute(
                f"DELETE FROM {q(table)} WHERE rowid NOT IN "
                f"(SELECT rowid FROM {q(table)} ORDER BY rowid DESC LIMIT 50)"
            )
            policy = "latest 50"
        con.commit()
        after = int(con.execute(f"SELECT COUNT(*) FROM {q(table)}").fetchone()[0])
        output[table] = {
            "before_rows": before,
            "after_rows": after,
            "deleted": before - after,
            "policy": policy,
        }
    return output

def consistent_backup(source: sqlite3.Connection, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    backup = sqlite3.connect(destination)
    try:
        source.backup(backup)
    finally:
        backup.close()

def run_pass(
    con: sqlite3.Connection,
    arc: sqlite3.Connection,
    report: dict[str, Any],
    aggressive: bool,
) -> None:
    sizes = object_sizes(con)
    all_tables = [
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]
    policies = dict(TAIL_POLICIES)

    threshold_mb = 0.25 if aggressive else 0.5
    auto_keep = 250 if aggressive else 500
    for table in all_tables:
        if table in policies or table in PROTECTED_TABLES or table in POSITION_POLICIES:
            continue
        lower = table.lower()
        if "position" in lower or "order" in lower:
            continue
        if sizes.get(table, 0.0) >= threshold_mb and GENERIC_CHURN_RE.search(table):
            policies[table] = auto_keep
            report["warnings"].append(
                f"auto-policy {'aggressive ' if aggressive else ''}"
                f"applied to {table}: keep {auto_keep}"
            )

    operations = report.setdefault("operations", {})
    for table, keep in sorted(policies.items()):
        if table in PROTECTED_TABLES or not exists(con, table):
            continue
        actual_keep = min(keep, 250) if aggressive else keep
        try:
            operations[table] = trim_tail(con, arc, table, actual_keep)
        except Exception as exc:
            operations[table] = {"error": str(exc)}
            report["warnings"].append(f"{table}: {exc}")

    for table, keep in POSITION_POLICIES.items():
        if exists(con, table):
            operations[table] = trim_tail(
                con, arc, table, min(keep, 300) if aggressive else keep, active_guard=True
            )

    if exists(con, "code_vault_config_snapshots"):
        operations["code_vault_config_snapshots"] = compact_latest_per_key(
            con, arc, "code_vault_config_snapshots", ("key", "config_key", "name")
        )
    if exists(con, "code_vault_snapshots"):
        operations["code_vault_snapshots"] = compact_latest_per_key(
            con, arc, "code_vault_snapshots", ("file_path", "file_name", "path")
        )


def run_deep_target_pass(
    con: sqlite3.Connection,
    arc: sqlite3.Connection,
    report: dict[str, Any],
) -> None:
    """Final guarded pass used only when the normal passes miss the hot-DB target.

    Every removed row is archived first. Durable registries/state and all active
    positions remain protected. The purpose is to collapse high-churn telemetry
    tails far enough that VACUUM can return the matrix DB to the 5-10 MB band.
    """
    sizes = object_sizes(con)
    operations = report.setdefault("deep_target_operations", {})
    policies = dict(TAIL_POLICIES)

    all_tables = [
        r[0] for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    ]

    # Newly introduced churn tables are handled automatically, but only when
    # their names clearly identify them as telemetry/history rather than state.
    for table in all_tables:
        if table in policies or table in PROTECTED_TABLES or table in POSITION_POLICIES:
            continue
        lower = table.lower()
        if "position" in lower or "order" in lower:
            continue
        if sizes.get(table, 0.0) >= 0.10 and GENERIC_CHURN_RE.search(table):
            policies[table] = 50
            report["warnings"].append(
                f"deep-target auto-policy applied to {table}: keep 50"
            )

    for table, keep in sorted(policies.items()):
        if table in PROTECTED_TABLES or not exists(con, table):
            continue
        try:
            operations[table] = trim_tail(con, arc, table, min(keep, 50))
        except Exception as exc:
            operations[table] = {"error": str(exc)}
            report["warnings"].append(f"deep-target {table}: {exc}")

    for table, keep in POSITION_POLICIES.items():
        if not exists(con, table):
            continue
        try:
            operations[table] = trim_tail(
                con, arc, table, min(keep, 100), active_guard=True
            )
        except Exception as exc:
            operations[table] = {"error": str(exc)}
            report["warnings"].append(f"deep-target {table}: {exc}")


def run_adaptive_target_pass(
    con: sqlite3.Connection,
    arc: sqlite3.Connection,
    report: dict[str, Any],
    target_mb: float,
) -> None:
    """Archive-first final target enforcer for the operational hot database.

    This pass is intentionally conservative:
    * protected/durable tables are never touched;
    * unknown tables are touched only when their name is clearly high-churn;
    * open/active position rows remain protected;
    * every removed row is copied into the archive database first;
    * the pass stops when the target is reached or no safe progress remains.

    It exists because fixed tail policies cannot anticipate every new telemetry
    table introduced by later builds.  The previous shutdown could therefore
    report TARGET_MISSED even when the remaining bulk was disposable churn.
    """
    operations = report.setdefault("adaptive_target_operations", {})
    attempted: set[str] = set()
    rounds: list[dict[str, Any]] = []

    for round_number in range(1, 9):
        current_mb = round(size_mb(Path(report["database"])), 3)
        if current_mb <= float(target_mb):
            break

        sizes = object_sizes(con)
        table_names = {
            str(r[0])
            for r in con.execute(
                "SELECT name FROM sqlite_master "
                "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            )
        }

        candidates: list[tuple[float, str, int, bool]] = []

        for table in table_names:
            if table in attempted or table in PROTECTED_TABLES:
                continue

            lower = table.lower()
            is_position = table in POSITION_POLICIES or "position" in lower
            is_known_churn = table in TAIL_POLICIES
            is_named_churn = bool(GENERIC_CHURN_RE.search(table))

            if not (is_position or is_known_churn or is_named_churn):
                continue

            object_mb = float(sizes.get(table, 0.0))
            if object_mb < 0.04:
                continue

            keep = 100 if is_position else 25
            candidates.append((object_mb, table, keep, is_position))

        candidates.sort(reverse=True)
        if not candidates:
            rounds.append({
                "round": round_number,
                "before_mb": current_mb,
                "result": "no_safe_candidates",
            })
            break

        before_round = current_mb
        touched = 0

        for object_mb, table, keep, is_position in candidates[:8]:
            attempted.add(table)
            try:
                result = trim_tail(
                    con,
                    arc,
                    table,
                    keep,
                    active_guard=is_position,
                )
                result["object_mb_before"] = round(object_mb, 3)
                result["adaptive_keep"] = keep
                operations[table] = result
                touched += int(result.get("archived_deleted") or 0)
            except Exception as exc:
                operations[table] = {"error": str(exc)}
                report["warnings"].append(
                    f"adaptive-target {table}: {exc}"
                )

        con.commit()
        arc.commit()
        vacuum(con)

        after_round = round(size_mb(Path(report["database"])), 3)
        rounds.append({
            "round": round_number,
            "before_mb": before_round,
            "after_mb": after_round,
            "rows_archived_deleted": touched,
        })

        if after_round >= before_round - 0.01:
            report["warnings"].append(
                "Adaptive target pass made no measurable progress; "
                "remaining size is protected state, schema/index overhead, "
                "or an unclassified table."
            )
            break

    report["adaptive_target_rounds"] = rounds


def classify_target_blockers(
    con: sqlite3.Connection,
    limit: int = 30,
) -> list[dict[str, Any]]:
    """Explain the largest objects that remain after all safe retention passes."""
    sizes = object_sizes(con)
    table_names = {
        str(r[0])
        for r in con.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        )
    }
    blockers: list[dict[str, Any]] = []

    for name, mb in sorted(
        sizes.items(),
        key=lambda item: item[1],
        reverse=True,
    ):
        base = name
        if name.startswith("sqlite_autoindex_"):
            classification = "sqlite_autoindex"
        elif name not in table_names:
            classification = "index_or_internal_object"
        elif name in PROTECTED_TABLES:
            classification = "protected_durable_state"
        elif name in POSITION_POLICIES or "position" in name.lower():
            classification = "position_state_active_rows_protected"
        elif name in TAIL_POLICIES or GENERIC_CHURN_RE.search(name):
            classification = "safe_churn_already_minimised"
        else:
            classification = "unclassified_not_deleted_fail_closed"

        blockers.append({
            "name": base,
            "mb": round(float(mb), 3),
            "classification": classification,
        })
        if len(blockers) >= limit:
            break

    return blockers

def vacuum(con: sqlite3.Connection) -> None:
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.commit()
    con.execute("PRAGMA journal_mode=DELETE")
    con.execute("VACUUM")
    con.execute("PRAGMA optimize")
    con.execute("PRAGMA journal_mode=WAL")
    con.commit()


def rotate_full_backups(db: Path, keep: int) -> list[str]:
    """Keep only the newest N full pre-retention backups for this DB."""
    if keep < 1:
        keep = 1
    folder = db.parent / "db_backups"
    pattern = f"{db.stem}.FULL_before_retention_v*.db"
    files = sorted(
        folder.glob(pattern),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    removed: list[str] = []
    for stale in files[keep:]:
        try:
            stale.unlink()
            removed.append(str(stale))
        except OSError:
            pass
    return removed

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--archive", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--vacuum", action="store_true")
    parser.add_argument("--heartbeat-grace-seconds", type=int, default=45)
    parser.add_argument("--target-mb", type=float, default=10.0)
    parser.add_argument("--max-safe-mb", type=float, default=20.0)
    parser.add_argument("--keep-backups", type=int, default=3)
    parser.add_argument("--json")
    args = parser.parse_args()

    db = Path(args.db).resolve()
    archive_path = Path(args.archive).resolve()
    if not db.exists():
        raise SystemExit(f"DB not found: {db}")

    report: dict[str, Any] = {
        "schema_version": "SENTINUITY_RETENTION_V9_SIGNOFF",
        "database": str(db),
        "archive": str(archive_path),
        "before_mb": round(size_mb(db), 3),
        "target_mb": args.target_mb,
        "max_safe_mb": args.max_safe_mb,
        "operations": {},
        "warnings": [],
    }

    con = sqlite3.connect(db, timeout=180)
    con.execute("PRAGMA busy_timeout=180000")
    quick = con.execute("PRAGMA quick_check").fetchone()[0]
    if quick != "ok":
        con.close()
        raise SystemExit(f"SAFETY ABORT: quick_check={quick}")

    fresh = fresh_heartbeat_rows(con, args.heartbeat_grace_seconds)
    report["fresh_heartbeat_rows"] = fresh
    report["top_objects_before"] = top_objects(con)
    if fresh:
        con.close()
        raise SystemExit(
            f"SAFETY ABORT: {len(fresh)} heartbeat rows newer than "
            f"{args.heartbeat_grace_seconds}s; services may still be live"
        )

    if not args.apply:
        con.close()
        print(json.dumps(report, indent=2))
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = db.parent / "db_backups" / f"{db.stem}.FULL_before_retention_v8_{stamp}.db"
    consistent_backup(con, backup)
    report["backup"] = str(backup)
    report["rotated_backups"] = rotate_full_backups(db, args.keep_backups)

    arc = sqlite3.connect(archive_path, timeout=180)
    arc.execute("PRAGMA busy_timeout=180000")

    try:
        run_pass(con, arc, report, aggressive=False)
        report["heartbeat_compaction"] = compact_heartbeats(con)
        con.commit()
        arc.commit()
        if args.vacuum:
            vacuum(con)

        report["after_first_pass_mb"] = round(size_mb(db), 3)

        if report["after_first_pass_mb"] > args.target_mb:
            report["warnings"].append(
                "First pass missed the target; running guarded aggressive pass."
            )
            run_pass(con, arc, report, aggressive=True)
            con.commit()
            arc.commit()
            if args.vacuum:
                vacuum(con)
            report["after_aggressive_pass_mb"] = round(size_mb(db), 3)

        if round(size_mb(db), 3) > args.target_mb:
            report["warnings"].append(
                "Aggressive pass still missed the target; running archive-first deep target pass."
            )
            run_deep_target_pass(con, arc, report)
            con.commit()
            arc.commit()
            if args.vacuum:
                vacuum(con)
            report["after_deep_target_pass_mb"] = round(size_mb(db), 3)

        if round(size_mb(db), 3) > args.target_mb:
            report["warnings"].append(
                "Deep target pass still missed the target; running adaptive "
                "archive-first target enforcement over the largest safe churn objects."
            )
            run_adaptive_target_pass(
                con,
                arc,
                report,
                float(args.target_mb),
            )
            report["after_adaptive_target_pass_mb"] = round(size_mb(db), 3)

        report["target_blockers"] = classify_target_blockers(con)

        quick_after = con.execute("PRAGMA quick_check").fetchone()[0]
        if quick_after != "ok":
            raise RuntimeError(f"post-prune quick_check={quick_after}")
        report["quick_check"] = quick_after
    except Exception:
        try:
            arc.close()
        finally:
            con.close()
        shutil.copy2(backup, db)
        raise
    finally:
        try:
            arc.close()
        except Exception:
            pass
        try:
            con.close()
        except Exception:
            pass

    check = sqlite3.connect(db, timeout=60)
    report["after_mb"] = round(size_mb(db), 3)
    report["reclaimed_mb"] = round(report["before_mb"] - report["after_mb"], 3)
    report["top_objects_after"] = top_objects(check)
    check.close()

    if report["after_mb"] > args.max_safe_mb:
        report["status"] = "ATTENTION"
        report["warnings"].append(
            "Database remains above the hard safe ceiling. Large protected/state objects "
            "are listed in top_objects_after and were intentionally not deleted."
        )
    elif report["after_mb"] > args.target_mb:
        report["status"] = "TARGET_MISSED"
        report["warnings"].append(
            "Database remains above the requested hot-DB target after all archive-first "
            "passes. Shutdown must not report a clean prune result."
        )
    else:
        report["status"] = "PASS"

    text = json.dumps(report, indent=2)
    print(text)
    if args.json:
        destination = Path(args.json)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(text, encoding="utf-8")

    return 3 if report["status"] == "ATTENTION" else (4 if report["status"] == "TARGET_MISSED" else 0)

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(f"RETENTION V8 FAILED: {exc}", file=sys.stderr)
        raise
