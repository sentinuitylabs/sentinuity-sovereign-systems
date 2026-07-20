#!/usr/bin/env python3
"""Offline smoke test for the V2 Fable 5 integration contracts."""
from __future__ import annotations
import sqlite3, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
import sys
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)
    print(f"[PASS] {message}")


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="sentinuity_fable5_") as tmp:
        db_path = Path(tmp) / "matrix.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        from services.inspiration_intake_ledger import ensure_schema as ensure_inspiration
        ensure_inspiration(conn)

        from services.github_scout import _record_repo_inspiration
        _record_repo_inspiration(
            conn,
            project_key="agentic_trading_frameworks",
            topic="agentic_trading_architecture",
            query="offline smoke fixture",
            repo={
                "name": "example/project",
                "description": "fixture only",
                "stars": 42,
                "language": "Python",
                "url": "https://github.com/example/project",
                "topics": ["agents", "paper-trading"],
                "owner": "example",
                "licence": "MIT",
            },
            readme="Offline fixture README; no external request performed.",
        )
        row = conn.execute(
            "SELECT source_type, source_ref, author, licence, stage "
            "FROM inspiration_intake_ledger"
        ).fetchone()
        check(row is not None, "GitHub discovery persisted to inspiration ledger")
        check(row["source_type"] == "github_repo", "GitHub source type retained")
        check(row["licence"] == "MIT" and row["author"] == "example", "GitHub provenance retained")
        check(row["stage"] == "INTAKE", "External inspiration cannot skip intake gates")

        conn.execute("""
            CREATE TABLE polaris_standing_tasks (
                id INTEGER PRIMARY KEY, title TEXT, domain TEXT, risk_level TEXT,
                priority INTEGER, status TEXT, blocked_reason TEXT,
                operator_priority INTEGER DEFAULT 0
            )
        """)
        conn.executemany(
            "INSERT INTO polaris_standing_tasks VALUES (?,?,?,?,?,?,?,?)",
            [
                (1, "Task A", "intelligence", "LOW", 2, "OPEN", "", 0),
                (2, "Task B", "substrate", "LOW", 3, "OPEN", "", 0),
                (3, "Operator Task", "operator", "LOW", 9, "OPEN", "", 1),
            ],
        )
        from services.standing_task_scheduler import select_task_fair, record_progress
        selected = select_task_fair(conn)
        check(selected is not None and selected["id"] == 3, "Operator priority overrides fairness ordering")
        record_progress(conn, 3, "fixture artefact")
        progress = conn.execute(
            "SELECT total_progress_events FROM standing_task_schedule WHERE task_id=3"
        ).fetchone()
        check(progress and progress[0] >= 1, "Meaningful progress is persisted")

        from ui.paper_live_divergence import (
            classify_divergence, CLASS_FILL_FAILURE, CLASS_QTY_MISMATCH,
        )
        cls, severity, _ = classify_divergence({"chain_state": "FAILED_ON_CHAIN"})
        check(cls == CLASS_FILL_FAILURE and severity == "bad", "Fill failure classification is fail-visible")
        cls, severity, _ = classify_divergence({
            "chain_state": "RECONCILED", "paper_qty": 100, "live_qty": 70
        })
        check(cls == CLASS_QTY_MISMATCH and severity == "bad", "Quantity mismatch classification is fail-visible")

        conn.execute("""
            CREATE TABLE patch_apply_journal (
                id INTEGER PRIMARY KEY, patch_id INTEGER, ts REAL,
                action TEXT, outcome TEXT, detail TEXT
            )
        """)
        conn.execute(
            "INSERT INTO patch_apply_journal VALUES (1, 99, 1, 'apply', 'OK', 'fixture')"
        )
        from services.build_retrospective import run_once
        created = run_once(conn=conn)
        check(created == 1, "Applied patch creates a retrospective record")
        check(conn.execute("SELECT COUNT(*) FROM build_retrospectives").fetchone()[0] == 1,
              "Retrospective is durable")
        conn.close()

    print("\nFABLE 5 INTEGRATION SMOKE TEST PASSED")


if __name__ == "__main__":
    main()
