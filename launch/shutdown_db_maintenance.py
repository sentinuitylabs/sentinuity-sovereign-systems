#!/usr/bin/env python3
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HOT = ROOT / "sentinuity_matrix.db"
INTEL = ROOT / "sentinuity_intelligence.db"
GOVERNOR = ROOT / "services" / "db_size_governor.py"
BACKUPS = ROOT / "db_backups"

STALE_READY_SECONDS = 900
KEEP_FULL_BACKUPS = 3


def size_mb(path: Path) -> float:
    return path.stat().st_size / 1024 / 1024 if path.exists() else 0.0


def quick_check(path: Path) -> str:
    if not path.exists():
        return "missing"

    db = sqlite3.connect(path, timeout=30)
    try:
        return str(db.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        db.close()


def epoch_expression(column: str) -> str:
    q = '"' + column.replace('"', '""') + '"'
    return f"""
    CASE
        WHEN typeof({q}) IN ('integer','real') THEN
            CASE
                WHEN CAST({q} AS REAL) > 1000000000000
                    THEN CAST({q} AS REAL) / 1000.0
                ELSE CAST({q} AS REAL)
            END
        ELSE CAST(strftime('%s', {q}) AS REAL)
    END
    """


def clear_stale_execution_ready() -> int:
    db = sqlite3.connect(HOT, timeout=30)
    db.row_factory = sqlite3.Row

    try:
        open_positions = db.execute(
            """
            SELECT COUNT(*)
            FROM paper_positions
            WHERE UPPER(COALESCE(status,''))='OPEN'
            """
        ).fetchone()[0]

        if open_positions:
            raise RuntimeError(
                f"REFUSED: {open_positions} open Solana positions"
            )

        substrate_open = 0

        for table in (
            "substrate_paper_positions",
            "substrate_position_journal",
        ):
            exists = db.execute(
                """
                SELECT 1
                FROM sqlite_master
                WHERE type='table' AND name=?
                """,
                (table,),
            ).fetchone()

            if not exists:
                continue

            cols = {
                row[1]
                for row in db.execute(
                    f'PRAGMA table_info("{table}")'
                )
            }

            if "status" in cols:
                substrate_open += db.execute(
                    f"""
                    SELECT COUNT(*)
                    FROM "{table}"
                    WHERE UPPER(COALESCE(status,''))='OPEN'
                    """
                ).fetchone()[0]

        if substrate_open:
            raise RuntimeError(
                f"REFUSED: {substrate_open} open Substrate positions"
            )

        exists = db.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type='table' AND name='market_snapshots'
            """
        ).fetchone()

        if not exists:
            print("market_snapshots missing: no execution-ready cleanup")
            return 0

        cols = {
            row[1]
            for row in db.execute(
                "PRAGMA table_info(market_snapshots)"
            )
        }

        if "execution_ready" not in cols:
            print("execution_ready column missing: no cleanup")
            return 0

        latched = 0

        if "latched" in cols:
            latched = db.execute(
                """
                SELECT COUNT(*)
                FROM market_snapshots
                WHERE COALESCE(latched,0)=1
                """
            ).fetchone()[0]

        if latched:
            raise RuntimeError(
                f"REFUSED: {latched} latched candidates"
            )

        time_column = next(
            (
                name for name in (
                    "execution_ready_at",
                    "updated_at",
                    "last_seen_at",
                    "qualified_at",
                    "created_at",
                    "timestamp",
                    "ts",
                )
                if name in cols
            ),
            None,
        )

        if not time_column:
            remaining = db.execute(
                """
                SELECT COUNT(*)
                FROM market_snapshots
                WHERE COALESCE(execution_ready,0) IN (1,2)
                """
            ).fetchone()[0]

            if remaining:
                raise RuntimeError(
                    "REFUSED: execution_ready rows exist but no "
                    "trusted timestamp column is available"
                )

            return 0

        cutoff = time.time() - STALE_READY_SECONDS
        time_expr = epoch_expression(time_column)

        assignments = ['execution_ready=0']

        if "execution_ready_at" in cols:
            assignments.append("execution_ready_at=NULL")

        if "claimed_by" in cols:
            assignments.append("claimed_by=NULL")

        if "claim_id" in cols:
            assignments.append("claim_id=NULL")

        if "claimed_at" in cols:
            assignments.append("claimed_at=NULL")

        if "claim_expires_at" in cols:
            assignments.append("claim_expires_at=NULL")

        latched_filter = (
            "AND COALESCE(latched,0)=0"
            if "latched" in cols
            else ""
        )

        cursor = db.execute(
            f"""
            UPDATE market_snapshots
            SET {", ".join(assignments)}
            WHERE COALESCE(execution_ready,0) IN (1,2)
              {latched_filter}
              AND ({time_expr}) < ?
            """,
            (cutoff,),
        )

        db.commit()

        remaining = db.execute(
            """
            SELECT COUNT(*)
            FROM market_snapshots
            WHERE COALESCE(execution_ready,0) IN (1,2)
            """
        ).fetchone()[0]

        print(
            f"stale execution_ready cleared: {cursor.rowcount}"
        )
        print(
            f"execution_ready remaining: {remaining}"
        )

        return int(cursor.rowcount or 0)

    finally:
        db.close()


def rotate_backups() -> None:
    if not BACKUPS.exists():
        return

    groups = (
        "pre_governor_hot_",
        "pre_governor_intel_",
    )

    for prefix in groups:
        files = sorted(
            BACKUPS.glob(f"{prefix}*.db"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )

        for stale in files[KEEP_FULL_BACKUPS:]:
            try:
                stale.unlink()
                print("removed old backup:", stale.name)
            except OSError as exc:
                print("backup removal warning:", exc)


def main() -> int:
    os.chdir(ROOT)

    print("=" * 76)
    print("SENTINUITY UNIFIED SHUTDOWN DB MAINTENANCE")
    print("=" * 76)

    print("hot before:  ", round(size_mb(HOT), 2), "MB")
    print("intel before:", round(size_mb(INTEL), 2), "MB")
    print("hot check:   ", quick_check(HOT))
    print("intel check: ", quick_check(INTEL))

    if quick_check(HOT) != "ok":
        raise RuntimeError("hot DB quick_check failed")

    if INTEL.exists() and quick_check(INTEL) != "ok":
        raise RuntimeError("intelligence DB quick_check failed")

    clear_stale_execution_ready()

    command = [
        sys.executable,
        str(GOVERNOR),
        "--maintain",
        "--force-window",
        "--hot-max",
        "80",
        "--intel-max",
        "150",
        "--tick-retention-days",
        "3",
    ]

    print("\nrunning governor:")
    print(" ".join(command))

    result = subprocess.run(
        command,
        cwd=ROOT,
        check=False,
    )

    if result.returncode != 0:
        raise RuntimeError(
            f"DB governor failed with exit code {result.returncode}"
        )

    if quick_check(HOT) != "ok":
        raise RuntimeError("hot DB failed post-maintenance check")

    if INTEL.exists() and quick_check(INTEL) != "ok":
        raise RuntimeError(
            "intelligence DB failed post-maintenance check"
        )

    rotate_backups()

    print("\nFINAL SIZES")
    print("hot:         ", round(size_mb(HOT), 2), "MB")
    print("intelligence:", round(size_mb(INTEL), 2), "MB")
    print(
        "hot archive: ",
        round(size_mb(ROOT / "sentinuity_archive.db"), 2),
        "MB",
    )
    print(
        "tick archive:",
        round(size_mb(ROOT / "sentinuity_tick_archive.db"), 2),
        "MB",
    )

    print("hot quick_check:  ", quick_check(HOT))
    print("intel quick_check:", quick_check(INTEL))
    print("maintenance complete")

    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print("\nMAINTENANCE REFUSED/FAILED:", exc)
        raise SystemExit(1)
