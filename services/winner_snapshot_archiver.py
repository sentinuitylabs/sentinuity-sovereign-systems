"""
services/winner_snapshot_archiver.py
======================================
FORENSIC PRESERVATION LAYER

Runs every 60 seconds.

When a paper_position CLOSES with realized_mult >= 1.5x (a "runner"),
this service copies all related market_snapshots rows into a permanent
archive table BEFORE system_guardian deletes them (1 hour after close).

Without this, every runner's pipeline-timing forensics gets wiped within
60 minutes of the trade closing, and we lose the ground-truth data we
need to train the runner-likelihood detector.

Archive schema:
    winner_snapshot_archive(
        id, position_id, token_name, mint_address,
        snap_id, candidate_state, quality_status, quality_reason,
        tier, confidence, execution_ready, latched,
        price_status, freshness_score,
        first_seen_at, created_at, qualified_at, price_updated_at,
        price, archived_at, realized_mult, peak_mult
    )

Cycle: 60 seconds (fast enough to beat the 1-hour guardian sweep)
"""
from __future__ import annotations

import sys
import time
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [winner_archiver] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("winner_archiver")

SERVICE_NAME    = "winner_snapshot_archiver"
CYCLE_SECONDS   = 60         # run once a minute
MIN_RUNNER_MULT = 1.5        # archive any close with >= 1.5x exit_price/entry_price


def _ensure_archive_table(conn) -> None:
    """Create archive table on first run."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS winner_snapshot_archive (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id       INTEGER NOT NULL,
            token_name        TEXT,
            mint_address      TEXT NOT NULL,
            snap_id           INTEGER,
            candidate_state   TEXT,
            quality_status    TEXT,
            quality_reason    TEXT,
            tier              TEXT,
            confidence        REAL,
            execution_ready   INTEGER,
            latched           INTEGER,
            price_status      TEXT,
            freshness_score   REAL,
            first_seen_at     REAL,
            created_at        REAL,
            qualified_at      REAL,
            price_updated_at  REAL,
            price             REAL,
            archived_at       REAL NOT NULL,
            realized_mult     REAL,
            peak_mult         REAL
        )
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_winner_archive_mint
        ON winner_snapshot_archive(mint_address)
    """)
    conn.execute("""
        CREATE INDEX IF NOT EXISTS idx_winner_archive_position
        ON winner_snapshot_archive(position_id)
    """)
    conn.commit()


def _find_recent_runners(conn, lookback_sec: int = 86400) -> list[dict]:
    """
    Find paper_positions that closed as runners in the last 24h
    AND haven't been archived yet.
    """
    now = time.time()
    cutoff = now - lookback_sec

    # Find runners
    rows = conn.execute("""
        SELECT id, token_name, mint_address, entry_price, exit_price,
               highest_price_seen, opened_at, closed_at, realized_pnl_usd
        FROM paper_positions
        WHERE status = 'CLOSED'
          AND closed_at > ?
          AND entry_price > 0
          AND exit_price > 0
          AND (exit_price / entry_price) >= ?
    """, (cutoff, MIN_RUNNER_MULT)).fetchall()

    runners = []
    for r in rows:
        # Skip if already archived
        already = conn.execute(
            "SELECT 1 FROM winner_snapshot_archive WHERE position_id = ? LIMIT 1",
            (r["id"],)
        ).fetchone()
        if already:
            continue
        runners.append({
            "id":            r["id"],
            "token_name":    r["token_name"],
            "mint_address":  r["mint_address"],
            "entry_price":   float(r["entry_price"]),
            "exit_price":    float(r["exit_price"]),
            "peak":          float(r["highest_price_seen"] or 0),
            "opened_at":     float(r["opened_at"] or 0),
            "closed_at":     float(r["closed_at"] or 0),
            "pnl":           float(r["realized_pnl_usd"] or 0),
        })
    return runners


def _archive_runner_snapshots(conn, runner: dict) -> int:
    """Copy all snapshots for a runner mint into the archive. Returns count.

    The active market_snapshots schema uses observed_price. Older archives used
    price. Resolve the live column once and alias it to ``price`` so the archive
    contract remains stable across both schemas.
    """
    snap_cols = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)")}
    price_col = next((c for c in ("observed_price", "price", "price_usd", "last_price") if c in snap_cols), None)
    if not price_col:
        log.warning("[ARCHIVE] market_snapshots has no recognised price column")
        return 0

    snaps = conn.execute(f"""
        SELECT id, token_name, mint_address, candidate_state, quality_status,
               quality_reason, tier, confidence, execution_ready, latched,
               price_status, freshness_score,
               first_seen_at, created_at, qualified_at, price_updated_at,
               {price_col} AS price
        FROM market_snapshots
        WHERE mint_address = ?
        ORDER BY id ASC
    """, (runner["mint_address"],)).fetchall()

    if not snaps:
        return 0

    now = time.time()
    realized_mult = runner["exit_price"] / runner["entry_price"]
    peak_mult     = runner["peak"] / runner["entry_price"] if runner["peak"] > 0 else realized_mult

    n = 0
    for s in snaps:
        conn.execute("""
            INSERT INTO winner_snapshot_archive
                (position_id, token_name, mint_address, snap_id,
                 candidate_state, quality_status, quality_reason,
                 tier, confidence, execution_ready, latched,
                 price_status, freshness_score,
                 first_seen_at, created_at, qualified_at, price_updated_at,
                 price, archived_at, realized_mult, peak_mult)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            runner["id"], s["token_name"], s["mint_address"], s["id"],
            s["candidate_state"], s["quality_status"], s["quality_reason"],
            s["tier"], s["confidence"], s["execution_ready"], s["latched"],
            s["price_status"], s["freshness_score"],
            s["first_seen_at"], s["created_at"], s["qualified_at"], s["price_updated_at"],
            s["price"], now, realized_mult, peak_mult,
        ))
        n += 1
    return n


def _run_archive_cycle() -> dict:
    """One archive cycle: find new runners, archive their snapshots."""
    stats = {"runners_found": 0, "runners_archived": 0, "snaps_archived": 0}

    with get_connection() as conn:
        import sqlite3 as _sq
        conn.row_factory = _sq.Row

        _ensure_archive_table(conn)

        runners = _find_recent_runners(conn, lookback_sec=86400)
        stats["runners_found"] = len(runners)

        for r in runners:
            n = _archive_runner_snapshots(conn, r)
            if n > 0:
                stats["snaps_archived"] += n
                stats["runners_archived"] += 1
                log.info("[ARCHIVE] pos=%d %s %.2fx — archived %d snapshots",
                         r["id"], (r["token_name"] or "?")[:16],
                         r["exit_price"]/r["entry_price"], n)
            else:
                # Position closed as runner but no snapshots exist anymore.
                # Insert a synthetic placeholder so we don't keep re-trying.
                conn.execute("""
                    INSERT INTO winner_snapshot_archive
                        (position_id, token_name, mint_address,
                         archived_at, realized_mult, peak_mult)
                    VALUES (?, ?, ?, ?, ?, ?)
                """, (
                    r["id"], r["token_name"], r["mint_address"],
                    time.time(),
                    r["exit_price"] / r["entry_price"],
                    (r["peak"] / r["entry_price"]) if r["peak"] > 0 else 0,
                ))
                log.warning("[ARCHIVE] pos=%d %s closed as runner but snapshots already gone — placeholder saved",
                            r["id"], (r["token_name"] or "?")[:16])

        conn.commit()

    return stats


def run() -> None:
    log.info("Winner snapshot archiver started — cycle=%ds threshold=%.1fx",
             CYCLE_SECONDS, MIN_RUNNER_MULT)
    update_heartbeat(SERVICE_NAME, "starting", "winner_snapshot_archiver online")

    # Wait briefly so DB is ready
    time.sleep(10)

    while True:
        try:
            stats = _run_archive_cycle()
            note = (f"runners_found={stats['runners_found']} "
                    f"runners_archived={stats['runners_archived']} "
                    f"snaps_archived={stats['snaps_archived']}")
            if stats["snaps_archived"] > 0:
                log.info("[ARCHIVE_CYCLE] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note,
                             work_processed=stats["snaps_archived"])
        except Exception as exc:
            log.warning("[ARCHIVE_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
