#!/usr/bin/env python3
"""launch/stamp_hitl_guard.py — FORCE HITL_REQUIRED=1 AND PROVE IT.

Replaces the malformed launcher one-liner:

    "%PY%" -c "import sqlite3,time; c=...; c.execute("INSERT INTO system_config...

Under Windows cmd.exe the outer quote closed at the first inner quote, so
Python received a truncated program and raised
    SyntaxError: '(' was never closed
The traceback was redirected into hitl_guard.log and the launcher continued.
HITL_REQUIRED was never stamped.

That is fail-safe ONLY on a pristine database: sovereign_governor reads
get_config_value("HITL_REQUIRED", "1"), so a missing row defaults to required.
But services/initiate_intelligence_build.py performs
    UPDATE system_config SET value='0' WHERE key='HITL_REQUIRED'
so on any database where that has ever run, the row exists at 0 and the broken
stamp left AUTO-APPLY ARMED.

This script writes the value, READS IT BACK, and exits non-zero unless the
persisted value is exactly "1". The launcher must abort Council/autobuilder
startup on a non-zero exit.

Exit codes:
    0  HITL_REQUIRED verified == "1"
    2  database missing / unopenable / locked   (FAIL CLOSED)
    3  write succeeded but read-back != "1"     (FAIL CLOSED)
    4  unexpected error                         (FAIL CLOSED)

Usage:
    python launch/stamp_hitl_guard.py [--db PATH] [--log PATH]
"""
from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from pathlib import Path

EXIT_OK = 0
EXIT_DB_UNAVAILABLE = 2
EXIT_READBACK_FAILED = 3
EXIT_UNEXPECTED = 4

KEY = "HITL_REQUIRED"
REQUIRED_VALUE = "1"


def _log(log_path: Path | None, msg: str) -> None:
    line = f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] stamp_hitl_guard: {msg}"
    print(line)
    if log_path is None:
        return
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(line + "\n")
    except Exception:
        pass


def _ensure_system_config(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS system_config("
        " key TEXT PRIMARY KEY, value TEXT, description TEXT, updated_at REAL)"
    )
    # Older databases may lack updated_at.
    cols = {r[1] for r in con.execute("PRAGMA table_info(system_config)").fetchall()}
    if "updated_at" not in cols:
        try:
            con.execute("ALTER TABLE system_config ADD COLUMN updated_at REAL")
        except Exception:
            pass
    if "description" not in cols:
        try:
            con.execute("ALTER TABLE system_config ADD COLUMN description TEXT")
        except Exception:
            pass


def stamp(db_path: str | Path, log_path: Path | None = None) -> int:
    p = Path(db_path)
    if not p.exists():
        _log(log_path, f"FAIL-CLOSED: database not found: {p}")
        return EXIT_DB_UNAVAILABLE

    try:
        con = sqlite3.connect(str(p), timeout=10)
        con.execute("PRAGMA busy_timeout=8000")
    except Exception as exc:
        _log(log_path, f"FAIL-CLOSED: cannot open database: {type(exc).__name__}: {exc}")
        return EXIT_DB_UNAVAILABLE

    try:
        try:
            _ensure_system_config(con)
        except Exception as exc:
            _log(log_path, f"FAIL-CLOSED: cannot ensure system_config: {type(exc).__name__}: {exc}")
            return EXIT_DB_UNAVAILABLE

        before_row = con.execute(
            "SELECT value FROM system_config WHERE key=?", (KEY,)
        ).fetchone()
        before = None if before_row is None else str(before_row[0])
        _log(log_path, f"prior value: {before!r}")

        now = time.time()
        try:
            con.execute(
                "INSERT INTO system_config(key,value,description,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value, "
                "updated_at=excluded.updated_at",
                (KEY, REQUIRED_VALUE, "forced on every launch by stamp_hitl_guard", now),
            )
            con.commit()
        except Exception as exc:
            _log(log_path, f"FAIL-CLOSED: write failed (locked?): {type(exc).__name__}: {exc}")
            return EXIT_DB_UNAVAILABLE

        # ---- MANDATORY READ-BACK on a SEPARATE connection --------------------
        # A write that is not read back is not a guarantee.
        try:
            verify = sqlite3.connect(str(p), timeout=10)
            row = verify.execute(
                "SELECT value FROM system_config WHERE key=?", (KEY,)
            ).fetchone()
            verify.close()
        except Exception as exc:
            _log(log_path, f"FAIL-CLOSED: read-back failed: {type(exc).__name__}: {exc}")
            return EXIT_READBACK_FAILED

        after = None if row is None else str(row[0]).strip()
        if after != REQUIRED_VALUE:
            _log(log_path, f"FAIL-CLOSED: read-back mismatch: expected '1', got {after!r}")
            return EXIT_READBACK_FAILED

        changed = " (CHANGED from 0 — auto-apply was armed)" if before == "0" else ""
        _log(log_path, f"VERIFIED {KEY}={after}{changed}")
        return EXIT_OK
    finally:
        try:
            con.close()
        except Exception:
            pass


def _default_db() -> str:
    env = os.getenv("SENTINUITY_DB")
    if env:
        return env
    return str(Path(__file__).resolve().parent.parent / "sentinuity_matrix.db")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Force and verify HITL_REQUIRED=1")
    ap.add_argument("--db", default=_default_db())
    ap.add_argument("--log", default=None)
    args = ap.parse_args(argv)

    log_path = Path(args.log) if args.log else (
        Path(__file__).resolve().parent.parent / "logs" / "hitl_guard.log"
    )
    try:
        return stamp(args.db, log_path)
    except Exception as exc:
        _log(log_path, f"FAIL-CLOSED: unexpected: {type(exc).__name__}: {exc}")
        return EXIT_UNEXPECTED


if __name__ == "__main__":
    sys.exit(main())
