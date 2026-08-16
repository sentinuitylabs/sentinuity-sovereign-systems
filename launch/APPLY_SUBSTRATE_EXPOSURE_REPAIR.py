from __future__ import annotations

"""
launch/APPLY_SUBSTRATE_EXPOSURE_REPAIR.py
===============================================================================
PACK 1 APPLY / VERIFY / ROLLBACK (SUBSTRATE_EXPOSURE_REPAIR_20260802)

Applies the Substrate exposure repair as one reversible unit:

    core/asset_identity.py                        (new)
    wallets/substrate_paper_ledger.py             (replaced)
    services/substrate_opportunity_scanner.py     (replaced)
    tools/audit_substrate_council_truth.py        (new, read-only)
    tests/test_substrate_lifecycle_contract.py    (new)
    launch/migrate_substrate_exposure_contract.py (new)

Order is deliberate: FILES → COMPILE → MIGRATE → TEST. Any failure rolls the
whole thing back to the pre-apply backup and leaves the database untouched by
the code that failed to compile.

This pack does NOT:
  * enable live capital or change any live flag
  * alter Solana Mode B
  * enforce would_veto
  * change live sizing
  * weaken the canary governor
  * modify services/debate_engine.py (that is a separate, operator-reviewed
    patch — see launch/apply_debate_quorum_routing.py)

Stop the runtime before applying. The ledger is imported by a running
supervisor; replacing it underneath a live process is not supported.

Usage:
    python launch/APPLY_SUBSTRATE_EXPOSURE_REPAIR.py --check
    python launch/APPLY_SUBSTRATE_EXPOSURE_REPAIR.py --apply --pack ./pack1
    python launch/APPLY_SUBSTRATE_EXPOSURE_REPAIR.py --rollback
"""

import argparse
import json
import py_compile
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKUP_ROOT = ROOT / "backups" / "substrate_exposure_repair"
TAG = "SUBSTRATE_EXPOSURE_REPAIR_20260802"

TARGETS = [
    "core/asset_identity.py",
    "wallets/substrate_paper_ledger.py",
    "services/substrate_opportunity_scanner.py",
    "tools/audit_substrate_council_truth.py",
    "tests/test_substrate_lifecycle_contract.py",
    "launch/migrate_substrate_exposure_contract.py",
]

# Files whose replacement changes runtime behaviour and must compile.
COMPILE_CRITICAL = [
    "core/asset_identity.py",
    "wallets/substrate_paper_ledger.py",
    "services/substrate_opportunity_scanner.py",
]

LIVE_KEYS_THAT_MUST_NOT_MOVE = [
    "LIVE_TRADING_ENABLED", "SUBSTRATE_LIVE_ENABLED", "SUBSTRATE_LIVE_ARMED",
    "SUBSTRATE_LIVE_AUTOSEND_ENABLED", "SUBSTRATE_LIVE_POSITION_SIZE_USD",
    "SUBSTRATE_LIVE_MAX_POSITION_USD", "SUBSTRATE_LIVE_MAX_OPEN",
]

SUBSTRATE_CONFIG_KEYS_TO_RESTORE = [
    "SUBSTRATE_MAX_ASSET_EXPOSURE_USD",
    "SUBSTRATE_POSITION_SIZE_USD",
    "SUBSTRATE_EXPOSURE_CONFIG_FAULT",
]


def _log(message: str) -> None:
    print(f"[{TAG}] {message}", flush=True)


def _live_flag_snapshot(db: Path) -> dict:
    import sqlite3
    if not db.exists():
        return {}
    try:
        con = sqlite3.connect(f"file:{db.as_posix()}?mode=ro", uri=True, timeout=8)
        con.execute("PRAGMA query_only=ON")
        out = {}
        for key in LIVE_KEYS_THAT_MUST_NOT_MOVE:
            row = con.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
            out[key] = row[0] if row else None
        con.close()
        return out
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)[:120]}



def _config_snapshot(db: Path, keys: list[str]) -> dict:
    import sqlite3
    if not db.exists():
        return {}
    con = sqlite3.connect(str(db), timeout=8)
    try:
        present = {r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        if "system_config" not in present:
            return {}
        out = {}
        for key in keys:
            row = con.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
            out[key] = {"existed": bool(row), "value": row[0] if row else None}
        return out
    finally:
        con.close()


def _restore_config_snapshot(db: Path, snapshot: dict) -> None:
    import sqlite3
    if not db.exists() or not snapshot:
        return
    con = sqlite3.connect(str(db), timeout=12)
    try:
        con.execute("BEGIN IMMEDIATE")
        for key, state in snapshot.items():
            if state.get("existed"):
                con.execute(
                    "INSERT INTO system_config(key,value) VALUES(?,?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (key, state.get("value")),
                )
            else:
                con.execute("DELETE FROM system_config WHERE key=?", (key,))
        con.commit()
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def backup(session: str, db: Path) -> Path:
    destination = BACKUP_ROOT / session
    destination.mkdir(parents=True, exist_ok=True)
    manifest = []
    for relative in TARGETS:
        source = ROOT / relative
        if not source.exists():
            manifest.append({"path": relative, "existed": False})
            continue
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        manifest.append({"path": relative, "existed": True})
    substrate_config = _config_snapshot(db, SUBSTRATE_CONFIG_KEYS_TO_RESTORE)
    (destination / "MANIFEST.json").write_text(
        json.dumps({
            "tag": TAG,
            "created_at": time.time(),
            "database": str(db),
            "files": manifest,
            "substrate_config": substrate_config,
        }, indent=2))
    _log(f"backup written to {destination}")
    return destination


def restore(session_dir: Path) -> bool:
    manifest_path = session_dir / "MANIFEST.json"
    if not manifest_path.exists():
        _log(f"no MANIFEST.json in {session_dir}")
        return False
    manifest = json.loads(manifest_path.read_text())
    for entry in manifest["files"]:
        relative, target = entry["path"], ROOT / entry["path"]
        if entry["existed"]:
            source = session_dir / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, target)
            _log(f"restored {relative}")
        elif target.exists():
            target.unlink()
            _log(f"removed {relative} (did not exist before apply)")
    db = Path(manifest.get("database") or (ROOT / "sentinuity_matrix.db"))
    _restore_config_snapshot(db, manifest.get("substrate_config") or {})
    _log("restored Substrate exposure configuration snapshot")
    return True


def latest_session() -> Path | None:
    if not BACKUP_ROOT.exists():
        return None
    sessions = sorted((p for p in BACKUP_ROOT.iterdir() if p.is_dir()),
                      key=lambda p: p.name)
    return sessions[-1] if sessions else None


def compile_check() -> tuple[bool, list]:
    failures = []
    for relative in COMPILE_CRITICAL:
        path = ROOT / relative
        if not path.exists():
            failures.append(f"{relative}: MISSING")
            continue
        try:
            py_compile.compile(str(path), doraise=True)
        except Exception as exc:  # noqa: BLE001
            failures.append(f"{relative}: {exc}")
    return (not failures), failures


def run_tests() -> tuple[bool, str]:
    suite = ROOT / "tests" / "test_substrate_lifecycle_contract.py"
    if not suite.exists():
        return False, "contract suite missing"
    proc = subprocess.run([sys.executable, str(suite)], cwd=str(ROOT),
                          capture_output=True, text=True, timeout=300)
    return proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]


def apply_pack(pack_dir: Path, db: Path, fix_sizing: bool) -> int:
    session = time.strftime("%Y%m%d_%H%M%S")
    before = _live_flag_snapshot(db)
    session_dir = backup(session, db)

    for relative in TARGETS:
        source = pack_dir / relative
        if not source.exists():
            _log(f"ABORT: pack is missing {relative}")
            restore(session_dir)
            return 2
        target = ROOT / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        _log(f"wrote {relative}")

    ok, failures = compile_check()
    if not ok:
        _log("COMPILE FAILED — rolling back")
        for failure in failures:
            _log(f"  {failure}")
        restore(session_dir)
        return 3

    _log("compile OK — running migration")
    migrate_cmd = [sys.executable, str(ROOT / "launch" / "migrate_substrate_exposure_contract.py"),
                   "--db", str(db)]
    if fix_sizing:
        migrate_cmd.append("--fix-sizing")
    proc = subprocess.run(migrate_cmd, cwd=str(ROOT), capture_output=True, text=True)
    print(proc.stdout[-3000:])
    if proc.returncode == 2:
        _log("MIGRATION BLOCKED — rolling back code")
        restore(session_dir)
        return 4

    _log("running behavioural contract suite")
    passed, output = run_tests()
    print(output)
    if not passed:
        _log("CONTRACT SUITE FAILED — rolling back code (schema additions are "
             "additive and harmless if left in place)")
        restore(session_dir)
        return 5

    after = _live_flag_snapshot(db)
    drifted = {k: (before.get(k), after.get(k)) for k in LIVE_KEYS_THAT_MUST_NOT_MOVE
               if before.get(k) != after.get(k)}
    if drifted:
        _log(f"ABORT: live flags moved during apply: {drifted} — rolling back")
        restore(session_dir)
        return 6
    _log(f"live flags unchanged: {json.dumps(after)}")

    _log(f"APPLIED. rollback with: python {Path(__file__).name} --rollback")
    _log("SUBSTRATE_EXPOSURE_REPAIRED")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--pack", default=str(Path(__file__).resolve().parent.parent),
                        help="directory containing the replacement files")
    parser.add_argument("--db", default=str(ROOT / "sentinuity_matrix.db"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--fix-sizing", action="store_true",
                        help="passed through to the migration; never raises a risk cap")
    args = parser.parse_args()

    if args.rollback:
        session_dir = latest_session()
        if not session_dir:
            _log("no backup session found")
            return 1
        _log(f"rolling back from {session_dir}")
        return 0 if restore(session_dir) else 1

    if args.check:
        ok, failures = compile_check()
        _log(f"compile: {'OK' if ok else failures}")
        passed, output = run_tests()
        print(output)
        _log(f"contract suite: {'PASS' if passed else 'FAIL'}")
        _log(f"live flags: {json.dumps(_live_flag_snapshot(Path(args.db)))}")
        return 0 if (ok and passed) else 1

    if args.apply:
        return apply_pack(Path(args.pack), Path(args.db), args.fix_sizing)

    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
