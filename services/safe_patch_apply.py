"""
services/safe_patch_apply.py

Minimal safe patch applier for Sentinuity council work queue.
It is intentionally conservative:
- backs up files before replacement
- compiles Python files
- optionally runs a verifier command
- records patch_apply_journal
- blocks HIGH risk unless approved_by_operator=1
"""
from __future__ import annotations

import argparse
import py_compile
try:
    from services.autonomous_apply_policy import can_autonomous_apply
except Exception:
    from autonomous_apply_policy import can_autonomous_apply

import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "sentinuity_matrix.db"
HIGH_RISK_NAMES = (
    "execution_engine.py", "launch_config.py", "prelaunch.py", "Launch_Sentinuity.bat",
    "Restart_Sentinuity.bat", "system_guardian.py", "ws_price_oracle.py",
    "wallet", "signing", "swap", "private", "live_trading.py",
)


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn


def ensure_journal(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS patch_apply_journal (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            task_id INTEGER,
            file_path TEXT,
            backup_path TEXT,
            patch_summary TEXT,
            risk_level TEXT,
            precheck_result TEXT,
            apply_result TEXT,
            postcheck_result TEXT,
            rollback_result TEXT,
            final_status TEXT
        )
    """)
    existing = {r[1] for r in conn.execute("PRAGMA table_info(patch_apply_journal)")}
    for col, spec in [
        ("created_at", "REAL"), ("task_id", "INTEGER"), ("file_path", "TEXT"),
        ("backup_path", "TEXT"), ("patch_summary", "TEXT"), ("risk_level", "TEXT"),
        ("precheck_result", "TEXT"), ("apply_result", "TEXT"),
        ("postcheck_result", "TEXT"), ("rollback_result", "TEXT"),
        ("final_status", "TEXT"), ("patch_ref", "TEXT"), ("stage", "INTEGER"),
        ("patch_id", "INTEGER"), ("ts", "REAL"), ("action", "TEXT"),
        ("outcome", "TEXT"), ("detail", "TEXT"),
    ]:
        if col not in existing:
            conn.execute(f"ALTER TABLE patch_apply_journal ADD COLUMN {col} {spec}")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS council_work_queue (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL,
            updated_at REAL,
            title TEXT,
            risk_level TEXT,
            phase TEXT,
            approved_by_operator INTEGER DEFAULT 0,
            backup_path TEXT,
            rollback_path TEXT,
            test_result TEXT,
            verifier_result TEXT,
            last_error TEXT
        )
    """)


def infer_risk(path: Path, requested: str) -> str:
    req = (requested or "").upper()
    if req in {"LOW", "MEDIUM", "HIGH"}:
        return req
    text = str(path).replace("\\", "/").lower()
    return "HIGH" if any(name.lower() in text for name in HIGH_RISK_NAMES) else "LOW"


def approved_for_high_risk(conn: sqlite3.Connection, task_id: int) -> bool:
    row = conn.execute("SELECT approved_by_operator FROM council_work_queue WHERE id=?", (task_id,)).fetchone()
    return bool(row and int(row["approved_by_operator"] or 0) == 1)


def compile_if_python(path: Path) -> str:
    if path.suffix.lower() != ".py":
        return "SKIP non-python"
    py_compile.compile(str(path), doraise=True)
    return "PASS py_compile"


def run_command(command: str | None) -> str:
    if not command:
        return "SKIP no verifier"
    proc = subprocess.run(command, shell=True, cwd=str(BASE_DIR), text=True, capture_output=True, timeout=180)
    out = (proc.stdout or "")[-1200:]
    err = (proc.stderr or "")[-1200:]
    status = "PASS" if proc.returncode == 0 else f"FAIL rc={proc.returncode}"
    return f"{status}\nSTDOUT:\n{out}\nSTDERR:\n{err}"


def apply_file(task_id: int, source: Path, target: Path, risk: str, verifier: str | None, summary: str) -> int:
    source = source.resolve()
    target = (BASE_DIR / target).resolve() if not target.is_absolute() else target
    if not source.exists():
        raise FileNotFoundError(f"source not found: {source}")
    if not str(target).startswith(str(BASE_DIR)):
        raise ValueError("target must be inside workspace")

    # CENTRAL GUARD (autonomous_apply_policy): deny-by-default for money files.
    _decision = can_autonomous_apply(str(target), patch_type=str(risk or ''), task_type='safe_patch')
    if not _decision.allowed:
        with connect() as _gconn:
            ensure_journal(_gconn)
            _gmsg = f"BLOCKED by central policy: {_decision.reason}"
            _gconn.execute(
                "INSERT INTO patch_apply_journal(created_at,task_id,file_path,backup_path,"
                "patch_summary,risk_level,precheck_result,final_status) VALUES(?,?,?,?,?,?,?,?)",
                (time.time(), task_id, str(target), None, summary, _decision.risk_level, _gmsg, 'BLOCKED'))
            _gconn.execute("UPDATE council_work_queue SET phase='NEEDS_APPROVAL', "
                           "updated_at=?, last_error=? WHERE id=?", (time.time(), _gmsg, task_id))
            _gconn.commit()
        print(_gmsg)
        return 2

    with connect() as conn:
        ensure_journal(conn)
        risk = infer_risk(target, risk)
        if risk == "HIGH" and not approved_for_high_risk(conn, task_id):
            msg = "BLOCKED high-risk patch requires Golden Lattice/operator approval"
            conn.execute("""
                INSERT INTO patch_apply_journal(created_at,task_id,file_path,backup_path,patch_summary,risk_level,precheck_result,final_status)
                VALUES(?,?,?,?,?,?,?,?)
            """, (time.time(), task_id, str(target), None, summary, risk, msg, "BLOCKED"))
            conn.execute("UPDATE council_work_queue SET phase='NEEDS_APPROVAL', updated_at=?, last_error=? WHERE id=?", (time.time(), msg, task_id))
            conn.commit()
            print(msg)
            return 2

        target.parent.mkdir(parents=True, exist_ok=True)
        backup = None
        if target.exists():
            backup_dir = BASE_DIR / "_patch_backups"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f"{target.name}.bak.{int(time.time())}"
            shutil.copy2(target, backup)
        shutil.copy2(source, target)
        apply_result = f"COPIED {source} -> {target}"
        rollback_result = ""
        final_status = "APPLIED"
        try:
            precheck = compile_if_python(target)
            postcheck = run_command(verifier)
            if "FAIL" in postcheck:
                raise RuntimeError(postcheck)
            final_status = "VERIFIED"
            conn.execute("""
                UPDATE council_work_queue
                SET phase='VERIFIED', updated_at=?, backup_path=?, rollback_path=?, verifier_result=?, verified_at=?
                WHERE id=?
            """, (time.time(), str(backup or ""), str(backup or ""), postcheck, time.time(), task_id))
        except Exception as exc:
            final_status = "ROLLED_BACK" if backup else "FAILED"
            postcheck = f"FAIL {type(exc).__name__}: {exc}"
            if backup and backup.exists():
                shutil.copy2(backup, target)
                rollback_result = f"RESTORED {backup} -> {target}"
            conn.execute("""
                UPDATE council_work_queue
                SET phase=?, updated_at=?, backup_path=?, rollback_path=?, verifier_result=?, last_error=?
                WHERE id=?
            """, (final_status, time.time(), str(backup or ""), str(backup or ""), postcheck, postcheck[:500], task_id))

        conn.execute("""
            INSERT INTO patch_apply_journal(
                created_at, task_id, file_path, backup_path, patch_summary, risk_level,
                precheck_result, apply_result, postcheck_result, rollback_result, final_status
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """, (time.time(), task_id, str(target), str(backup or ""), summary, risk, locals().get("precheck", ""), apply_result, locals().get("postcheck", ""), rollback_result, final_status))
        conn.commit()
        print(final_status)
        if rollback_result:
            print(rollback_result)
        return 0 if final_status == "VERIFIED" else 1


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--task-id", type=int, required=True)
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--risk", default="AUTO")
    ap.add_argument("--verifier", default=None)
    ap.add_argument("--summary", default="safe patch apply")
    args = ap.parse_args()
    raise SystemExit(apply_file(args.task_id, Path(args.source), Path(args.target), args.risk, args.verifier, args.summary))


if __name__ == "__main__":
    main()
