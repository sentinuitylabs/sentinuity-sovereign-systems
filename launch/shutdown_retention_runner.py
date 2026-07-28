#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import datetime
from pathlib import Path


def quick_check(path: Path) -> str:
    con = sqlite3.connect(path, timeout=120)
    try:
        con.execute("PRAGMA busy_timeout=120000")
        return str(con.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        con.close()


def backup_database(source: Path, target: Path) -> None:
    src = sqlite3.connect(source, timeout=120)
    dst = sqlite3.connect(target, timeout=120)
    try:
        src.execute("PRAGMA busy_timeout=120000")
        src.backup(dst)
        dst.commit()
        result = dst.execute("PRAGMA quick_check").fetchone()[0]
        if result != "ok":
            raise RuntimeError(f"backup quick_check={result}")
    finally:
        dst.close()
        src.close()


def final_vacuum(path: Path) -> str:
    con = sqlite3.connect(path, timeout=300)
    try:
        con.execute("PRAGMA busy_timeout=300000")
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        con.execute("PRAGMA journal_mode=DELETE")
        con.execute("VACUUM")
        con.execute("PRAGMA journal_mode=WAL")
        try:
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except sqlite3.DatabaseError:
            pass
        return str(con.execute("PRAGMA quick_check").fetchone()[0])
    finally:
        con.close()


def footprint(path: Path) -> tuple[float, dict[str, float]]:
    parts = [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]
    sizes = {
        p.name: (p.stat().st_size / 1048576 if p.exists() else 0.0)
        for p in parts
    }
    return sum(sizes.values()), sizes


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    args = ap.parse_args()

    root = Path(args.root).resolve()
    db = root / "sentinuity_matrix.db"
    archive = root / "sentinuity_archive.db"
    trim = root / "launch" / "db_retention_trim.py"
    backup_dir = root / "db_backups"
    log_dir = root / "logs" / "db_retention"

    if not db.exists():
        print(f"[FAIL] Missing database: {db}")
        return 2
    if not trim.exists():
        print(f"[FAIL] Missing retention engine: {trim}")
        return 3

    backup_dir.mkdir(parents=True, exist_ok=True)
    log_dir.mkdir(parents=True, exist_ok=True)

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_dir / f"sentinuity_matrix.SHUTDOWN_before_retention_{stamp}.db"
    report = log_dir / f"matrix_shutdown_retention_{stamp}.json"
    log = log_dir / f"matrix_shutdown_retention_{stamp}.log"

    before_mb, before_sizes = footprint(db)
    print(f"[BEFORE] footprint_mb={before_mb:.2f} sizes={before_sizes}")

    qc = quick_check(db)
    print(f"quick_check={qc}")
    if qc != "ok":
        print("[FAIL] Pre-retention quick_check failed.")
        return 4

    backup_database(db, backup)
    print(f"[PASS] Verified backup: {backup}")

    cmd = [
        sys.executable,
        str(trim),
        "--db", str(db),
        "--archive", str(archive),
        "--apply",
        "--vacuum",
        "--target-mb", "12",
        "--max-safe-mb", "20",
        "--heartbeat-grace-seconds", "12",
        "--keep-backups", "3",
        "--json", str(report),
    ]

    with log.open("w", encoding="utf-8") as fh:
        proc = subprocess.Popen(
            cmd,
            cwd=root,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            print(line, end="")
            fh.write(line)
        rc = proc.wait()

    if rc != 0:
        print(f"[FAIL] Retention engine exit={rc}")
        print(f"Log: {log}")
        print(f"Report: {report}")
        print(f"Backup: {backup}")
        return rc or 5

    post_qc = final_vacuum(db)
    after_mb, after_sizes = footprint(db)

    print(f"post_vacuum_quick_check={post_qc}")
    print("sizes_mb=" + json.dumps(
        {k: round(v, 2) for k, v in after_sizes.items()},
        sort_keys=True,
    ))
    print(f"total_footprint_mb={after_mb:.2f}")
    print(f"reclaimed_mb={before_mb - after_mb:.2f}")

    if post_qc != "ok":
        print("[FAIL] Post-retention quick_check failed.")
        return 6
    if after_mb > 20:
        print("[FAIL] Matrix footprint remains above signed-off 20 MB ceiling.")
        return 7

    print("[PASS] Shutdown retention complete.")
    print(f"Backup: {backup}")
    print(f"Log: {log}")
    print(f"Report: {report}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
