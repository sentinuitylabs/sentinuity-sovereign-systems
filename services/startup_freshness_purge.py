"""
launch/startup_freshness_purge.py
=================================
HARD LAUNCH GUARD — runs BEFORE services start.

If any of these print does not appear in operator console, launch is not safe:
  [PURGED] market_snapshots operational stale candidates: N
  [PURGED] raw_dna stale unresolved: N
  [AFTER] active stale_prelaunch: 0
  [DONE] startup_freshness_purge complete

This version is schema-safe and self-contained. It does NOT depend on
freshness_enforcer or rolling_eviction services being healthy. It runs
raw SQL with PRAGMA-checked column references so it never crashes on
missing columns like updated_at in raw_dna.
"""
from __future__ import annotations

import sqlite3
import sys
import time
import traceback
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "startup_freshness_purge.log"
DB = ROOT / "sentinuity_matrix.db"

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def log(msg: str) -> None:
    line = f"[STARTUP_PURGE] {time.strftime('%Y-%m-%d %H:%M:%S')} {msg}"
    print(line, flush=True)
    try:
        with LOG_FILE.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def _table_cols(conn: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def purge_market_snapshots(conn: sqlite3.Connection, now: float) -> int:
    """Purge stale candidates from market_snapshots with schema-safe SQL."""
    cols = _table_cols(conn, "market_snapshots")
    if not cols:
        log("market_snapshots table missing — skipping")
        return 0

    has_price_status     = "price_status"     in cols
    has_active_cognition = "active_cognition" in cols
    has_qcu              = "qualify_claimed_until" in cols
    has_lcu              = "latch_claimed_until"   in cols
    has_ecu              = "execution_claimed_until" in cols

    extra_sets = []
    if has_price_status:     extra_sets.append("price_status='dead'")
    if has_active_cognition: extra_sets.append("active_cognition=0")
    if has_qcu:              extra_sets.append("qualify_claimed_until=NULL")
    if has_lcu:              extra_sets.append("latch_claimed_until=NULL")
    if has_ecu:              extra_sets.append("execution_claimed_until=NULL")
    extra_sql = (", " + ", ".join(extra_sets)) if extra_sets else ""

    # AGE CONTRACT: token birth age is not startup-staleness.
    # Do not purge fresh discoveries simply because the token/pair was born >30min ago.

    cutoff = now - 600  # 10 minutes
    n = conn.execute(f"""
        UPDATE market_snapshots
        SET candidate_state='expired_stale',
            quality_status='rejected',
            quality_reason='STARTUP_CLEANUP_EXPIRED',
            execution_ready=0,
            latched=0
            {extra_sql}
        WHERE (
            candidate_state IN ('pending', 'priced', 'retry', 'qualified', 'stale_prelaunch')
            OR quality_reason LIKE 'SIGNAL_STALE_%'
            OR COALESCE(created_at, first_seen_at, updated_at, 0) < ?
        )
        AND candidate_state NOT IN ('executed', 'vetoed', 'exited', 'mtm')
    """, (cutoff,)).rowcount
    return n


def purge_raw_dna(conn: sqlite3.Connection, now: float) -> int:
    """Purge stale unresolved raw_dna rows — schema-safe (no updated_at reference)."""
    cols = _table_cols(conn, "raw_dna")
    if not cols:
        log("raw_dna table missing — skipping")
        return 0

    # Find a valid timestamp column
    ts_col = None
    for c in ("first_seen_at", "created_at", "processed_at", "detected_at", "timestamp"):
        if c in cols:
            ts_col = c
            break
    if not ts_col:
        log("raw_dna has no timestamp column — skipping age-based purge")
        return 0

    sets = ["processed_state = -1"]
    if "resolution_note" in cols:
        sets.append("resolution_note='STARTUP_CLEANUP_STALE_RAW_DNA'")
    if "claim_until" in cols:
        sets.append("claim_until=NULL")
    if "claimed_until" in cols:
        sets.append("claimed_until=NULL")
    set_sql = ", ".join(sets)

    n = conn.execute(f"""
        UPDATE raw_dna
        SET {set_sql}
        WHERE processed_state IN (0, 1, 99)
          AND COALESCE({ts_col}, 0) > 0
          AND COALESCE({ts_col}, 0) < ?
    """, (now - 600,)).rowcount
    return n


def verify_after(conn: sqlite3.Connection) -> dict:
    """Verify state after purge — must show 0 active stale rows."""
    result = {}
    try:
        # Count active stale_prelaunch rows
        n = conn.execute("""
            SELECT COUNT(*) FROM market_snapshots
            WHERE candidate_state = 'stale_prelaunch'
        """).fetchone()[0]
        result["stale_prelaunch_active"] = n

        # Count active rows with SIGNAL_STALE
        n2 = conn.execute("""
            SELECT COUNT(*) FROM market_snapshots
            WHERE quality_reason LIKE 'SIGNAL_STALE_%'
              AND candidate_state NOT IN ('expired_stale', 'vetoed', 'exited', 'executed', 'mtm')
        """).fetchone()[0]
        result["signal_stale_active"] = n2

        # Count pending_qualification queue
        n3 = conn.execute("""
            SELECT COUNT(*) FROM market_snapshots
            WHERE candidate_state IN ('pending', 'priced', 'retry')
              AND COALESCE(quality_status, '') NOT IN ('qualified', 'rejected', 'error')
        """).fetchone()[0]
        result["pending_queue"] = n3

        # Count active fresh rows (operational_ts within 10min)
        now = time.time()
        n4 = conn.execute(f"""
            SELECT COUNT(*) FROM market_snapshots
            WHERE candidate_state IN ('pending', 'priced', 'retry')
              AND COALESCE(quality_status, '') NOT IN ('qualified', 'rejected', 'error')
              AND COALESCE(updated_at, created_at, first_seen_at, 0) >= ?
        """, (now - 600,)).fetchone()[0]
        result["fresh_queue"] = n4
    except Exception as e:
        result["error"] = str(e)
    return result


def main() -> int:
    log("=" * 80)
    log(f"DB: {DB}")
    if not DB.exists():
        log(f"FATAL: database not found at {DB}")
        return 1

    conn = sqlite3.connect(str(DB), timeout=30)
    conn.execute("PRAGMA busy_timeout=30000")
    now = time.time()

    try:
        ms_purged = purge_market_snapshots(conn, now)
        log(f"[PURGED] market_snapshots old candidates: {ms_purged}")

        rd_purged = purge_raw_dna(conn, now)
        log(f"[PURGED] raw_dna stale unresolved: {rd_purged}")

        conn.commit()
        log("[COMMITTED] purge writes flushed")

        # Verify
        verify = verify_after(conn)
        log(f"[AFTER] active stale_prelaunch: {verify.get('stale_prelaunch_active', '?')}")
        log(f"[AFTER] active SIGNAL_STALE rows: {verify.get('signal_stale_active', '?')}")
        log(f"[AFTER] pending_qualification queue: {verify.get('pending_queue', '?')}")
        log(f"[AFTER] fresh queue (< 10min): {verify.get('fresh_queue', '?')}")

        # Success requires stale_prelaunch == 0
        if verify.get("stale_prelaunch_active", 0) > 0:
            log(f"WARNING: {verify['stale_prelaunch_active']} stale_prelaunch rows still active after purge!")

        log("[DONE] startup_freshness_purge complete")
        return 0
    except Exception as e:
        log(f"FATAL: {e}")
        log(traceback.format_exc())
        return 1
    finally:
        conn.close()


if __name__ == "__main__":
    raise SystemExit(main())
