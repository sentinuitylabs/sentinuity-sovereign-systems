"""
services/code_vault.py

SENTINUITY SOVEREIGN CODE VAULT
=================================

The organism's memory of its own evolution.

Every code change is recorded permanently:
  - SHA256 hash of old and new file
  - Full content of previous version (enables revert)
  - Reason for change (manual / polaris_proposal / debate_consensus)
  - Linked proposal ID if change came from the debate engine
  - Timestamp and operator confirmation

POLARIS and IVARIS can read this log to understand:
  - What changed recently
  - Whether a performance change correlates with a code change
  - Whether to propose a revert

Commands:
  python services/code_vault.py snapshot         — hash all current service files
  python services/code_vault.py log              — show full change history
  python services/code_vault.py revert <file>    — revert a file to previous version
  python services/code_vault.py diff <file>      — show what changed in a file

Run as a service: continuously watches services/ for file changes and logs them.

File location: trading-bot/services/code_vault.py
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import logging
import argparse
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [VAULT] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("code_vault")

SERVICE_NAME   = "code_vault"
SERVICES_DIR   = BASE_DIR / "services"
CORE_DIR       = BASE_DIR / "core"
WATCH_DIRS     = [SERVICES_DIR, CORE_DIR]
WATCH_INTERVAL = 86400  # seconds between file scans (5min — was 30s, caused cursor thrash)

# mtime cache — only re-hash files whose modification time actually changed
# This prevents reading 65+ file contents every scan cycle
_MTIME_CACHE: dict[str, float] = {}

# Files to track
TRACKED_EXTENSIONS = {".py"}
IGNORED_FILES      = {"__pycache__", ".pyc", "__init__"}


# ── SCHEMA ────────────────────────────────────────────────────────────────────
def ensure_tables() -> None:
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS code_vault_snapshots (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path       TEXT NOT NULL,
                file_name       TEXT NOT NULL,
                sha256_hash     TEXT NOT NULL,
                file_size_bytes INTEGER DEFAULT 0,
                snapshotted_at  REAL NOT NULL,
                content         TEXT,
                is_baseline     INTEGER DEFAULT 0,
                notes           TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vault_snap_file
            ON code_vault_snapshots(file_name, snapshotted_at DESC)
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS code_vault_changes (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                file_path       TEXT NOT NULL,
                file_name       TEXT NOT NULL,
                old_hash        TEXT,
                new_hash        TEXT NOT NULL,
                old_snapshot_id INTEGER,
                new_snapshot_id INTEGER,
                changed_at      REAL NOT NULL,
                change_reason   TEXT DEFAULT 'file_modified',
                proposal_id     INTEGER,
                applied_by      TEXT DEFAULT 'system',
                reverted        INTEGER DEFAULT 0,
                notes           TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vault_changes_file
            ON code_vault_changes(file_name, changed_at DESC)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_vault_changes_proposal
            ON code_vault_changes(proposal_id)
        """)
        conn.commit()
    log.info("Code vault tables ready")


# ── HASHING ───────────────────────────────────────────────────────────────────
def hash_file(path: Path) -> str:
    """SHA256 hash of file contents."""
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def read_file_safe(path: Path) -> str:
    """Read file content safely — truncate if very large."""
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
        # Store up to 500KB per file — enough for any service file
        if len(content) > 512000:
            content = content[:512000] + "\n\n[TRUNCATED — FILE EXCEEDS 500KB]"
        return content
    except Exception:
        return ""


# ── SNAPSHOT ──────────────────────────────────────────────────────────────────
def snapshot_file(
    path: Path,
    is_baseline: bool = False,
    notes: str = "",
) -> int:
    """Store a snapshot of a file. Returns snapshot id."""
    sha   = hash_file(path)
    size  = path.stat().st_size if path.exists() else 0
    content = read_file_safe(path)
    now   = time.time()

    try:
        with get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO code_vault_snapshots
                    (file_path, file_name, sha256_hash, file_size_bytes,
                     snapshotted_at, content, is_baseline, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(path.relative_to(BASE_DIR)),
                path.name,
                sha,
                size,
                now,
                content,
                1 if is_baseline else 0,
                notes,
            ))
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        log.error("snapshot_file failed %s: %s", path.name, e)
        return 0


def get_last_snapshot(file_name: str) -> dict | None:
    """Get the most recent snapshot for a file."""
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT id, sha256_hash, snapshotted_at, content, file_path
                FROM code_vault_snapshots
                WHERE file_name = ?
                ORDER BY snapshotted_at DESC
                LIMIT 1
            """, (file_name,)).fetchone()
        if row:
            return dict(row)
    except Exception:
        pass
    return None


def get_all_tracked_hashes() -> dict[str, dict]:
    """Return {file_name: {id, hash, snapshotted_at}} for all tracked files."""
    result = {}
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT file_name, id, sha256_hash, snapshotted_at
                FROM code_vault_snapshots
                WHERE id IN (
                    SELECT MAX(id) FROM code_vault_snapshots GROUP BY file_name
                )
            """).fetchall()
        for row in rows:
            result[row["file_name"]] = dict(row)
    except Exception as e:
        log.warning("get_all_tracked_hashes failed: %s", e)
    return result


# ── CHANGE LOG ────────────────────────────────────────────────────────────────
def log_change(
    path: Path,
    old_hash: str | None,
    new_hash: str,
    old_snapshot_id: int | None,
    new_snapshot_id: int,
    reason: str = "file_modified",
    proposal_id: int | None = None,
    applied_by: str = "system",
    notes: str = "",
) -> None:
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO code_vault_changes
                    (file_path, file_name, old_hash, new_hash,
                     old_snapshot_id, new_snapshot_id, changed_at,
                     change_reason, proposal_id, applied_by, notes)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(path.relative_to(BASE_DIR)),
                path.name,
                old_hash,
                new_hash,
                old_snapshot_id,
                new_snapshot_id,
                time.time(),
                reason,
                proposal_id,
                applied_by,
                notes,
            ))
            conn.commit()
        log.info(
            "CHANGE LOGGED: %s | reason=%s%s",
            path.name, reason,
            f" proposal_id={proposal_id}" if proposal_id else "",
        )
    except Exception as e:
        log.error("log_change failed: %s", e)


# ── FILE WATCHER ──────────────────────────────────────────────────────────────
def get_watched_files() -> list[Path]:
    """Return all .py files in watched directories."""
    files = []
    for directory in WATCH_DIRS:
        if not directory.exists():
            continue
        for f in directory.rglob("*.py"):
            if any(ign in f.name for ign in IGNORED_FILES):
                continue
            files.append(f)
    return files


def scan_for_changes(known_hashes: dict[str, dict]) -> dict[str, dict]:
    """
    Scan all watched files. If hash changed, log it and take new snapshot.
    Uses mtime pre-check to avoid reading file contents when unchanged.
    Returns updated known_hashes.
    """
    changed_count = 0

    for path in get_watched_files():
        fname = path.name

        # Fast mtime check — skip full SHA256 read if file not modified
        try:
            cur_mtime = path.stat().st_mtime
        except Exception:
            continue
        last_mtime = _MTIME_CACHE.get(str(path), 0.0)
        if last_mtime and cur_mtime == last_mtime and fname in known_hashes:
            continue  # file unchanged — skip expensive hash read
        _MTIME_CACHE[str(path)] = cur_mtime

        cur_hash = hash_file(path)
        if not cur_hash:
            continue

        last = known_hashes.get(fname)

        if last is None:
            # First time seeing this file — snapshot as baseline
            snap_id = snapshot_file(path, is_baseline=True, notes="initial_baseline")
            known_hashes[fname] = {
                "id": snap_id, "sha256_hash": cur_hash, "snapshotted_at": time.time()
            }
            log.info("BASELINE: %s", fname)

        elif last["sha256_hash"] != cur_hash:
            # File changed — snapshot new version and log the change
            old_snap_id = last["id"]
            old_hash    = last["sha256_hash"]
            new_snap_id = snapshot_file(path, notes="auto_detected_change")
            log_change(
                path       = path,
                old_hash   = old_hash,
                new_hash   = cur_hash,
                old_snapshot_id = old_snap_id,
                new_snapshot_id = new_snap_id,
                reason     = "file_modified",
                applied_by = "operator",
                notes      = f"Detected by code_vault watcher at {datetime.now(timezone.utc).isoformat()}",
            )
            known_hashes[fname] = {
                "id": new_snap_id, "sha256_hash": cur_hash, "snapshotted_at": time.time()
            }
            changed_count += 1

    return known_hashes, changed_count


# ── REVERT ────────────────────────────────────────────────────────────────────
def revert_file(file_name: str, steps_back: int = 1) -> bool:
    """
    Revert a file to a previous snapshot.
    steps_back=1 means the version before current.
    steps_back=2 means two versions back. Etc.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT id, sha256_hash, snapshotted_at, content, file_path
                FROM code_vault_snapshots
                WHERE file_name = ?
                ORDER BY snapshotted_at DESC
                LIMIT ?
            """, (file_name, steps_back + 1)).fetchall()

        if len(rows) <= steps_back:
            log.error(
                "Cannot revert %s — only %d snapshots exist, need %d",
                file_name, len(rows), steps_back + 1
            )
            return False

        target = rows[steps_back]
        content = target["content"]

        if not content:
            log.error("Target snapshot has no stored content — cannot revert")
            return False

        # Find the actual file path
        target_path = BASE_DIR / target["file_path"]
        if not target_path.parent.exists():
            log.error("Target path does not exist: %s", target_path)
            return False

        # Snapshot current state before overwriting
        current_path = target_path
        old_snap_id  = 0
        old_hash     = ""
        if current_path.exists():
            old_hash    = hash_file(current_path)
            old_snap_id = snapshot_file(current_path, notes="pre_revert_backup")

        # Write the reverted content
        target_path.write_text(content, encoding="utf-8")

        new_hash    = hash_file(target_path)
        new_snap_id = snapshot_file(target_path, notes=f"reverted_to_snapshot_{target['id']}")

        log_change(
            path            = target_path,
            old_hash        = old_hash,
            new_hash        = new_hash,
            old_snapshot_id = old_snap_id,
            new_snapshot_id = new_snap_id,
            reason          = f"manual_revert_steps_back={steps_back}",
            applied_by      = "operator",
            notes           = f"Reverted to snapshot id={target['id']} from {datetime.fromtimestamp(target['snapshotted_at']).isoformat()}",
        )

        log.info(
            "REVERTED: %s → version from %s",
            file_name,
            datetime.fromtimestamp(target["snapshotted_at"]).strftime("%Y-%m-%d %H:%M:%S"),
        )
        return True

    except Exception as e:
        log.error("revert_file failed: %s", e)
        return False


# ── SHOW LOG ──────────────────────────────────────────────────────────────────
def show_change_log(file_name: str | None = None, limit: int = 20) -> None:
    """Print the change history to terminal."""
    try:
        with get_connection() as conn:
            if file_name:
                rows = conn.execute("""
                    SELECT file_name, changed_at, change_reason,
                           old_hash, new_hash, proposal_id, applied_by, notes
                    FROM code_vault_changes
                    WHERE file_name = ?
                    ORDER BY changed_at DESC LIMIT ?
                """, (file_name, limit)).fetchall()
            else:
                rows = conn.execute("""
                    SELECT file_name, changed_at, change_reason,
                           old_hash, new_hash, proposal_id, applied_by, notes
                    FROM code_vault_changes
                    ORDER BY changed_at DESC LIMIT ?
                """, (limit,)).fetchall()

        if not rows:
            print("No changes logged yet.")
            return

        print(f"\n{'═'*70}")
        print(f"  CODE VAULT CHANGE LOG{' — ' + file_name if file_name else ''}")
        print(f"{'═'*70}")
        for row in rows:
            ts     = datetime.fromtimestamp(row["changed_at"]).strftime("%Y-%m-%d %H:%M:%S")
            old_h  = (row["old_hash"] or "NEW")[:12]
            new_h  = (row["new_hash"] or "?")[:12]
            pid    = f"proposal #{row['proposal_id']}" if row["proposal_id"] else "manual"
            print(f"\n  {ts}  {row['file_name']}")
            print(f"  Reason:  {row['change_reason']}")
            print(f"  By:      {row['applied_by']} ({pid})")
            print(f"  Hash:    {old_h}... → {new_h}...")
            if row["notes"]:
                print(f"  Notes:   {row['notes'][:80]}")
        print(f"\n{'═'*70}\n")

    except Exception as e:
        print(f"Error reading change log: {e}")


def show_snapshot_list(file_name: str) -> None:
    """List all snapshots for a file."""
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT id, sha256_hash, snapshotted_at, file_size_bytes, is_baseline, notes
                FROM code_vault_snapshots
                WHERE file_name = ?
                ORDER BY snapshotted_at DESC
                LIMIT 20
            """, (file_name,)).fetchall()

        if not rows:
            print(f"No snapshots found for {file_name}")
            return

        print(f"\n{'═'*60}")
        print(f"  SNAPSHOTS: {file_name}")
        print(f"{'═'*60}")
        for i, row in enumerate(rows):
            ts   = datetime.fromtimestamp(row["snapshotted_at"]).strftime("%Y-%m-%d %H:%M:%S")
            size = row["file_size_bytes"] / 1024
            base = " [BASELINE]" if row["is_baseline"] else ""
            curr = " ← CURRENT" if i == 0 else ""
            prev = f" ← revert with: python code_vault.py revert {file_name} {i}" if i > 0 else ""
            print(f"\n  [{row['id']}] {ts}{base}{curr}{prev}")
            print(f"       Hash: {row['sha256_hash'][:20]}... | Size: {size:.1f}KB")
            if row["notes"]:
                print(f"       Notes: {row['notes'][:60]}")
        print(f"\n{'═'*60}\n")

    except Exception as e:
        print(f"Error: {e}")


# ── SNAPSHOT ALL NOW ──────────────────────────────────────────────────────────
def snapshot_all_now(is_baseline: bool = False) -> None:
    """Take a snapshot of every watched file right now."""
    files = get_watched_files()
    print(f"\nSnapshotting {len(files)} files...")
    for path in files:
        snap_id = snapshot_file(
            path,
            is_baseline=is_baseline,
            notes="manual_snapshot" if not is_baseline else "manual_baseline",
        )
        print(f"   {path.name} → snapshot id={snap_id}")
    print(f"\nDone. {len(files)} files snapshotted.")


# ── POLARIS CONTEXT ───────────────────────────────────────────────────────────
def get_recent_changes_for_polaris(hours: int = 24) -> list[dict]:
    """
    Return recent code changes in a format POLARIS/IVARIS can reason about.
    Called by debate_engine.py to give AIs context about what changed recently.
    """
    since = time.time() - (hours * 3600)
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT file_name, changed_at, change_reason,
                       proposal_id, applied_by, notes
                FROM code_vault_changes
                WHERE changed_at > ?
                ORDER BY changed_at DESC
                LIMIT 20
            """, (since,)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def get_healthy_baseline() -> dict | None:
    """
    Return the most recent snapshot set that was taken when system was healthy.
    Used by system_health_monitor for restoration.
    """
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT id, snapshotted_at, notes
                FROM code_vault_snapshots
                WHERE is_baseline = 1
                ORDER BY snapshotted_at DESC
                LIMIT 1
            """).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


# ── MAIN SERVICE LOOP ─────────────────────────────────────────────────────────
def run_service() -> None:
    """Run as a background service — watch for file changes continuously."""
    ensure_tables()
    log.info("CODE VAULT ONLINE — watching services/ and core/")
    log.info("Tracking: %s", ", ".join(str(d.name) for d in WATCH_DIRS if d.exists()))

    # Load existing hashes
    known_hashes = get_all_tracked_hashes()
    log.info("Loaded %d existing file fingerprints", len(known_hashes))

    update_heartbeat(SERVICE_NAME, "ALIVE", f"Watching {len(get_watched_files())} files")

    while True:
        try:
            known_hashes, changed = scan_for_changes(known_hashes)
            if changed:
                log.info("%d file(s) changed this cycle", changed)
            update_heartbeat(
                SERVICE_NAME, "ALIVE",
                f"tracking={len(known_hashes)} changed_this_cycle={changed}",
            )
        except Exception as e:
            log.exception("VAULT ERROR: %s", e)
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])
        time.sleep(WATCH_INTERVAL)


# ── CLI ───────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Sentinuity Code Vault")
    parser.add_argument("command", nargs="?", default="service",
        choices=["service", "snapshot", "baseline", "log", "revert", "list"],
        help="Command to run")
    parser.add_argument("file", nargs="?", default=None,
        help="File name (e.g. neural_supervisor.py)")
    parser.add_argument("steps", nargs="?", type=int, default=1,
        help="Steps back for revert (default: 1 = previous version)")
    args = parser.parse_args()

    ensure_tables()

    if args.command == "service":
        run_service()

    elif args.command == "snapshot":
        snapshot_all_now(is_baseline=False)

    elif args.command == "baseline":
        print("Taking BASELINE snapshot of all service files...")
        print("This marks the current state as a known-good reference point.")
        snapshot_all_now(is_baseline=True)
        print("\nBaseline saved. POLARIS and system_health_monitor can restore to this point.")

    elif args.command == "log":
        show_change_log(file_name=args.file, limit=20)

    elif args.command == "list":
        if not args.file:
            print("Usage: python code_vault.py list <filename>")
            print("Example: python code_vault.py list neural_supervisor.py")
        else:
            show_snapshot_list(args.file)

    elif args.command == "revert":
        if not args.file:
            print("Usage: python code_vault.py revert <filename> [steps_back]")
            print("Example: python code_vault.py revert neural_supervisor.py 1")
        else:
            steps = args.steps or 1
            print(f"Reverting {args.file} {steps} version(s) back...")
            success = revert_file(args.file, steps_back=steps)
            if success:
                print(f" {args.file} reverted successfully.")
                print(f"Restart the affected service to apply the change.")
            else:
                print(f" Revert failed — check logs above.")


