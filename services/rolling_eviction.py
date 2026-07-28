"""
services/rolling_eviction.py
=============================
Temporal Garbage Collection — Phase: Freshness Stabilization

Evicts stale unpriced qualified rows every 90 seconds.
No deletions. Preserves forensic history.
Schema-safe. Heartbeat-aware.

Canonical stale candidate:
    (candidate_state='qualified' OR
     (candidate_state='pending' AND quality_status='qualified'))
    AND latched=0
    AND candidate_state NOT IN ('vetoed','exited','executed','latched')
    AND price_status != 'priced'
    AND effective_timestamp < now - stale_threshold

Effective timestamp: max(price_updated_at, created_at, first_seen_at, timestamp)
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
    format="%(asctime)s [rolling_eviction] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("rolling_eviction")

SERVICE_NAME   = "rolling_eviction"
CYCLE_SECONDS  = 90
STALE_THRESHOLD_SEC = 600   # 10 min — tokens older than this are dead weight


def _safe_ts(val) -> float:
    """Shared safe timestamp parser.
    Handles: float epochs, ISO strings '2026-05-12 08:00:00', NULL values.
    Returns 0.0 on any failure — never raises.
    """
    if val is None:
        return 0.0
    try:
        f = float(val)
        return f if f > 1_000_000_000 else 0.0   # reject non-epoch floats
    except (TypeError, ValueError):
        pass
    try:
        import datetime as _dt
        s = str(val).strip()
        for fmt in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                return _dt.datetime.strptime(s, fmt).timestamp()
            except ValueError:
                continue
    except Exception:
        pass
    return 0.0


def _effective_ts(row) -> float:
    """
    SIGNAL AGE — freshness of information, not token age.
    Uses MAX(price_updated_at, qualified_at, created_at, first_seen_at).
    A token re-priced 8s ago is PRIME regardless of how old the token is.
    """
    keys = set(row.keys()) if hasattr(row, "keys") else set()
    return max(
        _safe_ts(row["price_updated_at"] if "price_updated_at" in keys else None),
        _safe_ts(row["qualified_at"]     if "qualified_at"     in keys else None),
        _safe_ts(row["created_at"]       if "created_at"       in keys else None),
        _safe_ts(row["first_seen_at"]    if "first_seen_at"     in keys else None),
        _safe_ts(row["timestamp"]        if "timestamp"         in keys else None),
    )


def _count_stale(conn, now: float, threshold: float) -> int:
    """Count rows matching the canonical stale candidate definition."""
    return conn.execute("""
        SELECT COUNT(*) FROM market_snapshots
        WHERE (
            candidate_state = 'qualified'
            OR (candidate_state = 'pending' AND quality_status = 'qualified')
        )
        AND latched = 0
        AND candidate_state NOT IN ('vetoed','exited','executed','latched',
                                    'expired_stale','EXECUTOR_STALE_GATE')
        AND price_status != 'priced'
        AND (
            MAX(
                COALESCE(price_updated_at, 0),
                COALESCE(created_at, 0),
                COALESCE(first_seen_at, 0),
                COALESCE(timestamp, 0)
            ) < ?
            OR MAX(
                COALESCE(price_updated_at, 0),
                COALESCE(created_at, 0),
                COALESCE(first_seen_at, 0),
                COALESCE(timestamp, 0)
            ) = 0
        )
    """, (now - threshold,)).fetchone()[0]


def _run_eviction_cycle() -> dict:
    """Run one eviction cycle. Returns stats dict."""
    now = time.time()
    threshold = float(get_config_value("ROLLING_EVICTION_STALE_SEC", STALE_THRESHOLD_SEC))

    with get_connection() as conn:
        import sqlite3 as _sq3
        conn.row_factory = _sq3.Row

        stale_before = _count_stale(conn, now, threshold)

        if stale_before == 0:
            return {"stale_before": 0, "evicted": 0, "stale_after": 0, "sample_ids": []}

        # Fetch candidates for Python-side timestamp validation
        # No row limit — freshness_enforcer handles 8s cleanup,
        # rolling_eviction does the deeper sweep including priced rows
        candidates = conn.execute("""
            SELECT id, price_updated_at, created_at, first_seen_at, timestamp,
                   quality_reason, candidate_state, quality_status, qualified_at
            FROM market_snapshots
            WHERE (
                candidate_state = 'qualified'
                OR (candidate_state = 'pending' AND quality_status = 'qualified')
            )
            AND latched = 0
            AND candidate_state NOT IN ('vetoed','exited','executed','latched',
                                        'expired_stale','EXECUTOR_STALE_GATE')
        """).fetchall()

        evict_ids = []
        for row in candidates:
            eff = _effective_ts(row)
            age = now - eff if eff > 0 else float("inf")
            if age > threshold:
                evict_ids.append(row["id"])

        if not evict_ids:
            return {"stale_before": stale_before, "evicted": 0,
                    "stale_after": stale_before, "sample_ids": []}

        # Veto stale signals — hard removal from executor view
        # COLD demotion alone doesn't stop executor seeing them
        for row_id in evict_ids:
            conn.execute("""
                UPDATE market_snapshots
                SET candidate_state  = 'vetoed',
                    tier             = 'COLD',
                    freshness_score  = 0.0,
                    execution_ready  = 0,
                    active_cognition = 0,
                    quality_reason   = CASE
                        WHEN quality_reason IS NULL OR quality_reason = ''
                        THEN 'ROLLING_EVICTION_SIGNAL_STALE'
                        WHEN quality_reason LIKE '%ROLLING_EVICTION%'
                        THEN quality_reason
                        ELSE quality_reason || '|ROLLING_EVICTION_SIGNAL_STALE'
                    END
                WHERE id = ?
            """, (row_id,))
        conn.commit()

        stale_after = _count_stale(conn, now, threshold)

    return {
        "stale_before": stale_before,
        "evicted":      len(evict_ids),
        "stale_after":  stale_after,
        "sample_ids":   evict_ids[:5],
    }


def run() -> None:
    log.info("Rolling eviction service started — cycle=%ds stale_threshold=%ds",
             CYCLE_SECONDS, STALE_THRESHOLD_SEC)
    update_heartbeat(SERVICE_NAME, "starting", "rolling_eviction online")

    while True:
        try:
            stats = _run_eviction_cycle()

            # Prune stale MTM rows every cycle — prevents oracle saturation
            try:
                with get_connection() as _mc:
                    _mtm_n = _mc.execute("""
                        UPDATE market_snapshots
                        SET candidate_state='vetoed', quality_reason='MTM_EVICTED'
                        WHERE candidate_state='mtm'
                        AND (
                            mint_address NOT IN (
                                SELECT mint_address FROM paper_positions
                                WHERE status='OPEN'
                            )
                            OR mint_address IS NULL
                        )
                    """).rowcount
                    _mc.commit()
                if _mtm_n > 0:
                    log.info("[EVICTION] Pruned %d stale MTM rows", _mtm_n)
            except Exception as _me:
                log.debug("MTM prune skipped: %s", _me)

            note = (
                f"before={stats['stale_before']} "
                f"evicted={stats['evicted']} "
                f"after={stats['stale_after']}"
            )
            if stats["evicted"] > 0:
                log.info("[EVICTION] %s | sample_ids=%s", note, stats["sample_ids"])
            else:
                log.debug("[EVICTION] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note, work_processed=stats["evicted"])

            # ── RAW DNA LIVE PURGE — runs every cycle ─────────────────────
            # Clears stale unprocessed raw_dna rows that pile up during live
            # sessions. Without this, ingest chokes on old dead-momentum rows.
            # Boot-time prelaunch.py handles startup; this handles live state.
            try:
                with get_connection() as _rd_conn:
                    _cutoff = time.time() - 600  # 10 min — same as prelaunch
                    # Skip unresolved rows older than 10 min
                    _rd1 = _rd_conn.execute(
                        "UPDATE raw_dna SET processed_state=2 "
                        "WHERE processed_state=0 "
                        "AND COALESCE(first_seen_at,created_at,timestamp,processed_at,0) < ? "
                        "AND COALESCE(first_seen_at,created_at,timestamp,processed_at,0) > 0",
                        (_cutoff,)
                    ).rowcount
                    # Kill stuck state=1 resolver rows older than 10 min
                    _rd2 = _rd_conn.execute(
                        "UPDATE raw_dna SET processed_state=-1, "
                        "resolution_note='LIVE_PURGE_STALE_STATE1' "
                        "WHERE processed_state=1 "
                        "AND COALESCE(first_seen_at,created_at,timestamp,processed_at,0) < ? "
                        "AND COALESCE(first_seen_at,created_at,timestamp,processed_at,0) > 0",
                        (_cutoff,)
                    ).rowcount
                    _rd_conn.commit()
                if _rd1 + _rd2 > 0:
                    log.info("[EVICTION] raw_dna purge: skipped=%d killed_state1=%d",
                             _rd1, _rd2)
            except Exception as _rde:
                log.debug("raw_dna live purge skipped: %s", _rde)
        except Exception as exc:
            log.warning("[EVICTION_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
