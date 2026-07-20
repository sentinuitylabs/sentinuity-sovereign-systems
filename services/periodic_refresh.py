"""
services/periodic_refresh.py
==============================
Replicates critical cold-start state resets every 10 minutes.

Root cause of launch-window degradation (confirmed May 2026):
  1. qualify_claimed_until locks accumulate → MI stops claiming new rows
  2. _last_concluded dict in sovereign_governor → proposal types suppressed for 6h
  3. price_status='dead' rows pile up → oracle stops pricing qualified rows
  4. raw_dna processed_state=1 stuck rows → ingest pipeline gets blocked

Prelaunch clears all of these on cold boot.
Rolling eviction only demotes stale rows — it does NOT clear claim locks.

This service periodically replicates the state resets that only cold boot
previously performed, restoring continuous launch-state freshness.

Cycle: 10 minutes (configurable via PERIODIC_REFRESH_INTERVAL_SEC)
"""
from __future__ import annotations

import sys, time, logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [periodic_refresh] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("periodic_refresh")

SERVICE_NAME    = "periodic_refresh"
DEFAULT_CYCLE   = 600    # 10 minutes


def _run_refresh_cycle() -> dict:
    now   = time.time()
    stats = {}

    with get_connection() as conn:
        import sqlite3 as _sq
        conn.row_factory = _sq.Row

        # ── 1. RELEASE EXPIRED CLAIM LOCKS ────────────────────────────────────
        # qualify_claimed_until is set when MI claims a row for qualification.
        # If MI crashes or the row fails to process, the lock never releases.
        # Over 10 minutes enough locks accumulate that MI finds 0 claimable rows
        # and goes silent — even with fresh discoveries flowing in.
        n1 = conn.execute("""
            UPDATE market_snapshots
            SET qualify_claimed_until = NULL
            WHERE qualify_claimed_until IS NOT NULL
              AND qualify_claimed_until < ?
        """, (now,)).rowcount

        # Also clear latch locks for non-open positions
        n1b = conn.execute("""
            UPDATE market_snapshots
            SET latch_claimed_until = NULL,
                execution_ready = 0,
                latched = 0
            WHERE latch_claimed_until IS NOT NULL
              AND latch_claimed_until < ?
              AND latched = 1
              AND mint_address NOT IN (
                  SELECT mint_address FROM paper_positions WHERE status='OPEN'
              )
        """, (now,)).rowcount

        stats["claim_locks_released"]  = n1
        stats["latch_locks_released"]  = n1b

        # ── 2. RESET DEAD-PRICED ROWS IN RECENT QUALIFIED ─────────────────────
        # price_status='dead' means oracle gave up after 12 attempts.
        # Guardian's Fix 3 resets these but only every 60s with a 5min recency guard.
        # After 10min the dead pool is large enough to starve the oracle's HOT_SET.
        # Reset dead rows from the last 20 minutes for a fresh pricing attempt.
        n2 = conn.execute("""
            UPDATE market_snapshots
            SET price_status = 'pending',
                price_attempts = 0,
                price_last_attempt_at = NULL
            WHERE price_status = 'dead'
              AND candidate_state NOT IN ('vetoed','exited','executed',
                                          'expired_stale','EXECUTOR_STALE_GATE')
              AND latched = 0
              AND COALESCE(qualified_at, created_at, first_seen_at, 0) > ?
        """, (now - 1200,)).rowcount
        stats["dead_rows_reset"] = n2

        # ── 3. RELEASE STUCK raw_dna RESOLVER CLAIMS ──────────────────────────
        # processed_state=1 means ingest claimed the row for resolution.
        # If the resolver crashes or the transaction times out, the claim sticks.
        # These rows block new discoveries from entering the pipeline.
        n3 = conn.execute("""
            UPDATE raw_dna
            SET processed_state = 0,
                claim_until = NULL
            WHERE processed_state = 1
              AND (claim_until IS NULL OR claim_until < ?)
        """, (now - 300,)).rowcount
        stats["dna_claims_released"] = n3

        # ── 4. EVICT ANCIENT PENDING ROWS THAT SNUCK THROUGH ROLLING EVICTION ─
        # Rolling eviction uses effective_ts which can be wrong for rows with
        # all-NULL timestamps. Do a safety sweep using row ID age proxy.
        n4 = conn.execute("""
            UPDATE market_snapshots
            SET candidate_state = 'expired_stale',
                quality_reason = CASE
                    WHEN quality_reason IS NULL OR quality_reason = ''
                    THEN 'PERIODIC_REFRESH_STALE'
                    ELSE quality_reason || '|PERIODIC_REFRESH_STALE'
                END
            WHERE candidate_state = 'pending'
              AND quality_status NOT IN ('qualified','rejected','error')
              AND price_status != 'priced'
              AND COALESCE(qualified_at, first_seen_at, created_at, timestamp, 0) = 0
              AND id < (SELECT MAX(id) - 5000 FROM market_snapshots)
        """).rowcount
        stats["null_ts_evicted"] = n4

        # ── 4b. HARD PURGE — qualified+priced signals older than 600s ─────────
        # This is the 26k problem: signals that got a price but are ancient.
        # rolling_eviction skips priced rows. freshness_enforcer runs every 8s
        # but may miss burst accumulation. This 10-min sweep is the backstop.
        n4b = conn.execute("""
            UPDATE market_snapshots
            SET candidate_state = 'vetoed',
                execution_ready = 0,
                tier = 'COLD',
                quality_reason = CASE
                    WHEN quality_reason IS NULL OR quality_reason = ''
                    THEN 'PERIODIC_HARD_PURGE_600S'
                    ELSE quality_reason || '|PERIODIC_HARD_PURGE_600S'
                END
            WHERE candidate_state NOT IN ('vetoed','exited','executed',
                                          'expired_stale','EXECUTOR_STALE_GATE','mtm')
              AND latched = 0
              AND (
                  MAX(
                      COALESCE(CAST(price_updated_at AS REAL), 0),
                      COALESCE(CAST(qualified_at AS REAL), 0),
                      COALESCE(CAST(created_at AS REAL), 0),
                      COALESCE(CAST(first_seen_at AS REAL), 0)
                  ) > 0
                  AND MAX(
                      COALESCE(CAST(price_updated_at AS REAL), 0),
                      COALESCE(CAST(qualified_at AS REAL), 0),
                      COALESCE(CAST(created_at AS REAL), 0),
                      COALESCE(CAST(first_seen_at AS REAL), 0)
                  ) < ?
              )
        """, (now - 600,)).rowcount
        stats["hard_purge_600s"] = n4b

        # ── 5. RESET TOKEN_TOO_YOUNG REJECTED ROWS ────────────────────────────
        # Tokens rejected as TOO_YOUNG at discovery can qualify 10-20s later.
        # Without this reset they sit as rejected permanently, blocking that mint.
        n5 = conn.execute("""
            UPDATE market_snapshots
            SET quality_status='pending', quality_reason='', is_tradeable=0,
                qualify_claimed_until=NULL, candidate_state='pending'
            WHERE quality_status='rejected'
              AND quality_reason LIKE 'TOKEN_TOO_YOUNG%'
        """).rowcount
        stats["too_young_reset"] = n5

        # ── 6. VETO REJECTED+PENDING BLOCKING ROWS ────────────────────────────
        # Rows that are quality_status=rejected but still candidate_state=pending
        # block the dedup window for that mint, preventing fresh entries.
        n6 = conn.execute("""
            UPDATE market_snapshots
            SET candidate_state='vetoed'
            WHERE quality_status='rejected'
              AND candidate_state='pending'
        """).rowcount
        stats["rejected_pending_vetoed"] = n6

        conn.commit()

    return stats


def run() -> None:
    cycle = int(get_config_value("PERIODIC_REFRESH_INTERVAL_SEC", DEFAULT_CYCLE))
    log.info("Periodic refresh started — cycle=%ds", cycle)
    log.info("Replicating cold-start claim lock releases and dead-row resets")
    update_heartbeat(SERVICE_NAME, "starting", "periodic_refresh online")

    # Run immediately on startup too
    time.sleep(20)  # Give other services 20s to start first

    while True:
        try:
            stats = _run_refresh_cycle()
            note = (
                f"claims={stats['claim_locks_released']} "
                f"latches={stats['latch_locks_released']} "
                f"dead_reset={stats['dead_rows_reset']} "
                f"dna={stats['dna_claims_released']} "
                f"null_evict={stats['null_ts_evicted']} "
                f"hard_purge={stats.get('hard_purge_600s',0)} "
                f"too_young={stats.get('too_young_reset',0)} "
                f"rej_vetoed={stats.get('rejected_pending_vetoed',0)}"
            )
            if any(v > 0 for v in stats.values()):
                log.info("[REFRESH] %s", note)
            else:
                log.debug("[REFRESH] clean cycle — nothing to reset")
            update_heartbeat(SERVICE_NAME, "alive", note,
                             work_processed=sum(stats.values()))
        except Exception as exc:
            log.warning("[REFRESH_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(cycle)


if __name__ == "__main__":
    run()
