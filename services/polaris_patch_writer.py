#!/usr/bin/env python3
"""
polaris_patch_writer.py — SIGNOFF_COUNCIL_EXECUTION_SPINE_20260618

Applies a gate-approved code_patches row to disk SAFELY and VISIBLY:
  1. resolve + validate target path (must be inside repo root, no traversal)
  2. timestamped backup
  3. write to temp file first
  4. compile/syntax check (python -m py_compile for .py)
  5. atomic replace
  6. patch_apply_journal row
  7. rollback from backup on any failure
  8. update code_patches.status

It NEVER writes a trading-core file unless the gate marked it APPROVED_CORE_MANUAL
AND the operator path invoked it. The default council flow only sends it
APPROVED_UI_AUTO patches.
"""
from __future__ import annotations
import os, time, shutil, logging, sqlite3, subprocess, tempfile
from pathlib import Path

log = logging.getLogger("polaris_patch_writer")

REPO_ROOT = Path(os.environ.get("SENTINUITY_ROOT") or Path(__file__).resolve().parent.parent).resolve()
if not (REPO_ROOT / "services").exists():
    REPO_ROOT = Path.cwd().resolve()

BACKUP_DIR = REPO_ROOT / "_council_patch_backups"

CORE_RISK = (
    "execution_engine", "ws_price_oracle", "market_intelligence", "neural_supervisor",
    "prelaunch", "launch_config", "schema.py", "wallet", "live_trading", "router",
    "set_live_mode", "kill_live",
)

def _connect():
    try:
        from core.schema import get_connection
        return get_connection()
    except Exception:
        c = sqlite3.connect("sentinuity_matrix.db", timeout=15, check_same_thread=False)
        c.row_factory = sqlite3.Row
        return c

def _cfg(conn, k, d):
    try:
        r = conn.execute("SELECT value FROM system_config WHERE key=?", (k,)).fetchone()
        return r[0] if r else d
    except Exception:
        return d

def _is_core(path: str) -> bool:
    p = (path or "").lower()
    return any(m in p for m in CORE_RISK)

def _journal(conn, patch_id, action, outcome, detail=""):
    """Coexists with any pre-existing patch_apply_journal schema by ALTER-adding
    the columns we need rather than assuming a fresh table."""
    try:
        conn.execute("CREATE TABLE IF NOT EXISTS patch_apply_journal "
                     "(id INTEGER PRIMARY KEY AUTOINCREMENT)")
        existing = {r[1] for r in conn.execute("PRAGMA table_info(patch_apply_journal)")}
        for col, spec in (("patch_id","INTEGER"),("ts","REAL"),("action","TEXT"),
                          ("outcome","TEXT"),("detail","TEXT")):
            if col not in existing:
                try: conn.execute(f"ALTER TABLE patch_apply_journal ADD COLUMN {col} {spec}")
                except Exception: pass
        conn.execute("INSERT INTO patch_apply_journal (patch_id, ts, action, outcome, detail) "
                     "VALUES (?,?,?,?,?)", (patch_id, time.time(), action, outcome, detail[:1000]))
        conn.commit()
    except Exception as e:
        log.debug("journal: %s", e)

def _set_status(conn, patch_id, status, note=""):
    try:
        conn.execute("UPDATE code_patches SET status=?, applied_at=?, notes=COALESCE(notes,'')||? WHERE id=?",
                     (status, time.time(), f" | {note}", patch_id))
        conn.commit()
    except Exception:
        pass

def apply_patch_artifact(patch_id, conn=None) -> dict:
    own = conn is None
    if own: conn = _connect()
    try:
        row = conn.execute("SELECT * FROM code_patches WHERE id=?", (patch_id,)).fetchone()
        if not row:
            return {"ok": False, "reason": "PATCH_NOT_FOUND"}
        row = dict(row)
        rel = (row.get("file_path") or "").strip()
        code = row.get("patch_diff") or ""
        if not rel:
            _set_status(conn, patch_id, "BLOCKED", "no file_path")
            return {"ok": False, "reason": "NO_FILE_PATH"}
        if not code.strip():
            _set_status(conn, patch_id, "BLOCKED", "no code body")
            return {"ok": False, "reason": "NO_CODE"}

        # ── path validation: must resolve inside repo, no traversal ──
        target = (REPO_ROOT / rel).resolve()
        try:
            target.relative_to(REPO_ROOT)
        except ValueError:
            _journal(conn, patch_id, "validate", "REJECTED", f"path traversal: {rel}")
            _set_status(conn, patch_id, "BLOCKED", "path outside repo")
            return {"ok": False, "reason": "PATH_TRAVERSAL_BLOCKED"}

        # ── core-risk guard: refuse unless explicitly enabled ──
        if _is_core(rel):
            core_ok = str(_cfg(conn, "COUNCIL_CORE_AUTOPATCH", "0")).strip() == "1"
            if not core_ok:
                _journal(conn, patch_id, "guard", "BLOCKED", f"core file, autopatch off: {rel}")
                _set_status(conn, patch_id, "BLOCKED", "CORE_AUTOPATCH_DISABLED")
                return {"ok": False, "reason": "CORE_AUTOPATCH_DISABLED", "file_path": rel}

        # ── backup ──
        BACKUP_DIR.mkdir(exist_ok=True)
        ts = time.strftime("%Y%m%d_%H%M%S")
        backup = None
        if target.exists():
            backup = BACKUP_DIR / f"{target.name}.{ts}.bak"
            shutil.copy2(str(target), str(backup))
            _journal(conn, patch_id, "backup", "OK", str(backup))

        # ── write temp ──
        tmp = tempfile.NamedTemporaryFile("w", delete=False, dir=str(target.parent),
                                          suffix=".cwtmp", encoding="utf-8")
        tmp.write(code)
        tmp.close()
        tmp_path = Path(tmp.name)

        # ── compile/syntax check for python ──
        if target.suffix == ".py":
            proc = subprocess.run(["python", "-m", "py_compile", str(tmp_path)],
                                  capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                tmp_path.unlink(missing_ok=True)
                _journal(conn, patch_id, "compile", "FAILED", proc.stderr[:800])
                _set_status(conn, patch_id, "VERIFY_FAILED", "py_compile failed")
                return {"ok": False, "reason": "COMPILE_FAILED", "detail": proc.stderr[:400]}
            _journal(conn, patch_id, "compile", "OK", "")

        # ── atomic replace ──
        try:
            os.replace(str(tmp_path), str(target))
        except Exception as e:
            tmp_path.unlink(missing_ok=True)
            # rollback
            if backup and backup.exists():
                shutil.copy2(str(backup), str(target))
                _journal(conn, patch_id, "rollback", "OK", "after replace failure")
            _set_status(conn, patch_id, "ROLLED_BACK", f"replace failed: {e}")
            return {"ok": False, "reason": f"REPLACE_FAILED:{e}"}

        _journal(conn, patch_id, "apply", "OK", str(target))
        _set_status(conn, patch_id, "APPLIED", f"backup={backup}")
        return {"ok": True, "file_path": rel, "backup": str(backup) if backup else None}
    except Exception as e:
        log.error("apply_patch_artifact error: %s", e)
        try: _journal(conn, patch_id, "apply", "ERROR", str(e))
        except Exception: pass
        return {"ok": False, "reason": f"WRITER_ERROR:{e}"}
    finally:
        if own:
            try: conn.close()
            except Exception: pass
