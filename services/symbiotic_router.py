"""
services/symbiotic_router.py
=============================
The missing bridge between telegram_scout + wallet_scout and POLARIS.

Architecture:
  telegram_calls + wallet_pattern_observations
        ↓ (every 15s)
  symbiotic_scorer  →  symbiotic_candidates
        ↓ (every 8s)
  symbiotic_router  →  POLARIS context injection

Grok-confirmed conviction formula (Q1):
  conviction = (channel_trust*0.35 + wallet_profit*0.55) * exp(-time_delta/85)

Run as service:
  python -m services.symbiotic_router
"""

import logging
import math
import time
import json
from pathlib import Path

log = logging.getLogger("symbiotic_router")

try:
    from core.schema import get_connection
except ImportError:
    import sqlite3
    DB_PATH = Path("sentinuity_matrix.db")
    class _CM:
        def __enter__(self):
            self.conn = sqlite3.connect(str(DB_PATH), timeout=15)
            self.conn.row_factory = sqlite3.Row
            return self.conn
        def __exit__(self, *a):
            self.conn.close()
    def get_connection(): return _CM()

SERVICE_NAME = "symbiotic_router"
SCORE_INTERVAL   = 15   # seconds between scoring runs
ROUTE_INTERVAL   = 8    # seconds between routing runs
MIN_SCORE        = 0.55  # minimum conviction to enter symbiotic_candidates
ROUTE_THRESHOLD  = 0.62  # minimum to route to POLARIS
STALE_MINUTES    = 5     # drop candidates older than this
DEDUPE_WINDOW    = 1800  # 30 min dedup cache


# ── SCHEMA MIGRATIONS ─────────────────────────────────────────────────────────

def ensure_columns() -> None:
    """Add missing columns to existing tables. Never drops or modifies."""
    with get_connection() as conn:
        # telegram_calls — add actual_multiplier (peak_x already exists as alias)
        tc_cols = {r[1] for r in conn.execute("PRAGMA table_info(telegram_calls)").fetchall()}
        for col, ctype in [
            ("actual_multiplier", "REAL"),   # = peak_x alias for Grok Q2
            ("channel_accuracy_score", "REAL DEFAULT 0.0"),
        ]:
            if col not in tc_cols:
                conn.execute(f"ALTER TABLE telegram_calls ADD COLUMN {col} {ctype}")
                log.info("Added telegram_calls.%s", col)

        # wallet_pattern_observations — add Q3 metrics
        wo_cols = {r[1] for r in conn.execute("PRAGMA table_info(wallet_pattern_observations)").fetchall()}
        for col, ctype in [
            ("buy_delay_seconds",     "REAL"),    # time from launch to wallet buy
            ("realized_x",            "REAL"),    # actual outcome multiplier
            ("first_minute_hit_rate", "REAL DEFAULT 0.0"),
            ("avg_win_multiplier",    "REAL DEFAULT 0.0"),
            ("channel_overlap_score", "REAL DEFAULT 0.0"),
            ("exit_before_migration", "INTEGER DEFAULT 0"),  # Q6 failure mode 3
        ]:
            if col not in wo_cols:
                conn.execute(f"ALTER TABLE wallet_pattern_observations ADD COLUMN {col} {ctype}")
                log.info("Added wallet_pattern_observations.%s", col)

        # symbiotic_candidates — add wallet_address + trade stats for Q5 join
        sc_cols = {r[1] for r in conn.execute("PRAGMA table_info(symbiotic_candidates)").fetchall()}
        for col, ctype in [
            ("wallet_address",  "TEXT"),
            ("win_rate",        "REAL DEFAULT 0.0"),
            ("avg_pnl",         "REAL DEFAULT 0.0"),
            ("sl_rate",         "REAL DEFAULT 0.0"),
            ("channel_trust",   "REAL DEFAULT 0.0"),
            ("time_delta_sec",  "REAL DEFAULT 0.0"),
            ("routed_at",       "REAL"),
            ("route_reason",    "TEXT"),
            ("dev_linked",      "INTEGER DEFAULT 0"),
        ]:
            if col not in sc_cols:
                conn.execute(f"ALTER TABLE symbiotic_candidates ADD COLUMN {col} {ctype}")
                log.info("Added symbiotic_candidates.%s", col)

        # known_rings table (Q6 failure mode 2)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS known_rings (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address  TEXT UNIQUE,
                ring_id         TEXT,
                detected_at     REAL,
                notes           TEXT
            )
        """)

        # linked_wallets view on known_rings
        conn.execute("""
            CREATE TABLE IF NOT EXISTS wallet_links (
                wallet_address  TEXT,
                linked_wallets  TEXT,  -- JSON array
                updated_at      REAL
            )
        """)

        conn.commit()


# ── CONVICTION FORMULA (Grok Q1) ──────────────────────────────────────────────

def conviction_score(channel_trust: float,
                     wallet_profit: float,
                     time_delta_seconds: float) -> float:
    """
    Grok-confirmed formula for pump.fun signal-to-noise:
      weight: 0.35 channel_trust : 0.55 wallet_profit : 0.10 temporal decay
      half-life ≈ 59s (decay constant 85s)
    """
    if time_delta_seconds < 0:
        time_delta_seconds = 0.0
    temporal = math.exp(-time_delta_seconds / 85.0)
    return (channel_trust * 0.35 + wallet_profit * 0.55) * temporal


# ── CHANNEL ACCURACY UPDATE (Grok Q2) ─────────────────────────────────────────

def update_channel_accuracy() -> None:
    """
    Syncs peak_x → actual_multiplier then recomputes channel_accuracy_score.
    peak_x already exists in telegram_calls (telegram_scout enriches it).
    """
    with get_connection() as conn:
        # Sync actual_multiplier from peak_x
        conn.execute("""
            UPDATE telegram_calls
            SET actual_multiplier = peak_x
            WHERE peak_x IS NOT NULL AND actual_multiplier IS NULL
        """)

        # Compute per-channel accuracy and write back to each row
        rows = conn.execute("""
            SELECT
                channel,
                CAST(
                    SUM(CASE
                        WHEN actual_multiplier >= COALESCE(x_multiplier, 0)
                        AND x_multiplier > 0
                        THEN 1 ELSE 0 END)
                    AS FLOAT
                ) / NULLIF(COUNT(*), 0) AS acc
            FROM telegram_calls
            WHERE called_at >= (strftime('%s','now') - 2592000)
              AND x_multiplier IS NOT NULL
              AND x_multiplier > 0
              AND actual_multiplier IS NOT NULL
            GROUP BY channel
            HAVING COUNT(*) >= 5
        """).fetchall()

        for r in rows:
            conn.execute("""
                UPDATE telegram_calls
                SET channel_accuracy_score = ?
                WHERE channel = ?
            """, (round(r["acc"] or 0, 4), r["channel"]))

        conn.commit()
        log.debug("Channel accuracy updated for %d channels", len(rows))


# ── WALLET METRIC UPDATE (Grok Q3) ────────────────────────────────────────────

def update_wallet_metrics() -> None:
    """
    Computes the 3 Q3 metrics per wallet:
      1. first_minute_hit_rate  = buys within 60s of launch / total
      2. avg_win_multiplier     = avg realized_x on profitable trades
      3. channel_overlap_score  = buys within 120s of high-accuracy channel / total
    """
    with get_connection() as conn:
        wallets = conn.execute(
            "SELECT DISTINCT wallet_address FROM wallet_pattern_observations"
        ).fetchall()

        for row in wallets:
            w = row["wallet_address"]

            # Metric 1: first_minute_hit_rate
            r1 = conn.execute("""
                SELECT
                    COUNT(CASE WHEN COALESCE(buy_delay_seconds, time_from_launch_sec, 999) <= 60
                               THEN 1 END) * 1.0 /
                    NULLIF(COUNT(*), 0) AS fmhr
                FROM wallet_pattern_observations
                WHERE wallet_address = ?
            """, (w,)).fetchone()

            # Metric 2: avg_win_multiplier
            r2 = conn.execute("""
                SELECT AVG(CASE WHEN outcome='profitable' AND realized_x > 0
                               THEN realized_x END) AS awm
                FROM wallet_pattern_observations
                WHERE wallet_address = ?
            """, (w,)).fetchone()

            # Metric 3: channel_overlap_score
            r3 = conn.execute("""
                SELECT COUNT(DISTINCT wo.tx_hash) * 1.0 /
                       NULLIF((SELECT COUNT(*) FROM wallet_pattern_observations
                               WHERE wallet_address = ?), 0) AS cos
                FROM wallet_pattern_observations wo
                JOIN telegram_calls tc
                  ON wo.mint_address = tc.token_address
                 AND ABS(wo.created_at - tc.called_at) <= 120
                 AND tc.channel_accuracy_score >= 0.65
                WHERE wo.wallet_address = ?
            """, (w, w)).fetchone()

            conn.execute("""
                UPDATE wallet_pattern_observations
                SET first_minute_hit_rate = ?,
                    avg_win_multiplier    = ?,
                    channel_overlap_score = ?
                WHERE wallet_address = ?
            """, (
                round(r1["fmhr"] or 0, 4),
                round(r2["awm"]  or 0, 4),
                round(r3["cos"]  or 0, 4),
                w,
            ))

        conn.commit()
        log.debug("Wallet metrics updated for %d wallets", len(wallets))


# ── SYMBIOTIC SCORER (populates symbiotic_candidates) ─────────────────────────

def run_scorer() -> int:
    """
    Joins telegram_calls + wallet_pattern_observations on token_address.
    Calculates conviction_score and inserts qualifying rows.
    Returns count of new candidates inserted.
    """
    now   = time.time()
    cutoff = now - (10 * 60)  # last 10 minutes
    inserted = 0

    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                tc.token_address,
                tc.channel,
                tc.channel_accuracy_score     AS channel_trust,
                tc.called_at,
                ww.wallet_address,
                ww.profit_score               AS wallet_profit,
                wo.created_at                 AS wallet_buy_at,
                ww.profitable_count,
                ww.trade_count
            FROM telegram_calls tc
            JOIN wallet_pattern_observations wo
              ON tc.token_address = wo.mint_address
             AND wo.created_at >= ? 
            JOIN watched_wallets ww
              ON wo.wallet_address = ww.wallet_address
             AND ww.profit_score >= 0.5
            WHERE tc.called_at >= ?
              AND tc.token_address IS NOT NULL
              AND tc.channel_accuracy_score >= 0.3
        """, (cutoff, cutoff)).fetchall()

        for r in rows:
            time_delta = abs(float(r["wallet_buy_at"] or 0) -
                             float(r["called_at"] or 0))
            channel_trust = float(r["channel_trust"] or 0.3)
            wallet_profit = float(r["wallet_profit"] or 0)

            score = conviction_score(channel_trust, wallet_profit, time_delta)

            if score < MIN_SCORE:
                continue

            # Check not already routed recently
            existing = conn.execute("""
                SELECT id FROM symbiotic_candidates
                WHERE token_address = ?
                  AND created_at >= ?
                  AND status IN ('fresh','routed')
            """, (r["token_address"], now - DEDUPE_WINDOW)).fetchone()

            if existing:
                continue

            conn.execute("""
                INSERT INTO symbiotic_candidates
                    (token_address, token_name, symbiotic_conviction,
                     wallet_address, channel_trust, time_delta_sec,
                     status, signal_json, created_at, updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)
            """, (
                r["token_address"],
                "",
                round(score, 4),
                r["wallet_address"],
                round(channel_trust, 4),
                round(time_delta, 1),
                "fresh",
                json.dumps({
                    "channel": r["channel"],
                    "wallet_profit": wallet_profit,
                    "time_delta_sec": round(time_delta, 1),
                    "channel_trust": round(channel_trust, 4),
                }),
                now,
                now,
            ))
            inserted += 1

        conn.commit()

    if inserted:
        log.info("SCORER: %d new symbiotic candidates inserted", inserted)
    return inserted


# ── ENTRY SAFETY CHECKS (Grok Q6) ─────────────────────────────────────────────

def check_signal_farming(token_address: str) -> bool:
    """Q6 Failure 1: multiple low-accuracy channels calling same token."""
    with get_connection() as conn:
        r = conn.execute("""
            SELECT COUNT(DISTINCT channel) AS n
            FROM telegram_calls
            WHERE token_address = ?
              AND called_at > (strftime('%s','now') - 180)
              AND COALESCE(channel_accuracy_score, 0) < 0.45
        """, (token_address,)).fetchone()
        return (r["n"] or 0) >= 2


def check_wash_ring(token_address: str, wallet_address: str) -> bool:
    """Q6 Failure 2: wallet is in a known wash-trading ring."""
    with get_connection() as conn:
        r = conn.execute("""
            SELECT COUNT(*) AS n
            FROM wallet_pattern_observations
            WHERE token_address = ?
              AND wallet_address IN (
                  SELECT wallet_address FROM known_rings
              )
              AND COALESCE(first_minute_hit_rate, 0) > 0.8
              AND COALESCE(avg_win_multiplier, 0) < 1.5
        """, (token_address,)).fetchone()
        return (r["n"] or 0) > 0


def check_dev_exit_pattern(wallet_address: str) -> bool:
    """Q6 Failure 3: wallet routinely exits before migration on dev-linked tokens."""
    with get_connection() as conn:
        r = conn.execute("""
            SELECT AVG(COALESCE(exit_before_migration, 0)) AS avg_exit
            FROM wallet_pattern_observations
            WHERE wallet_address = ?
              AND mint_address IN (
                  SELECT token_address FROM symbiotic_candidates
                  WHERE COALESCE(dev_linked, 0) = 1
              )
        """, (wallet_address,)).fetchone()
        avg = r["avg_exit"] or 0
        return avg > 0.65


def is_entry_safe(token_address: str, wallet_address: str) -> tuple:
    """Returns (safe: bool, reason: str)."""
    if check_signal_farming(token_address):
        return False, "SIGNAL_FARMING_DETECTED"
    if check_wash_ring(token_address, wallet_address):
        return False, "WASH_RING_DETECTED"
    if check_dev_exit_pattern(wallet_address):
        return False, "DEV_EXIT_PATTERN"
    return True, "OK"


# ── SYMBIOTIC ROUTER (forwards to POLARIS context) ────────────────────────────

def get_polaris_context() -> str:
    """
    Returns scout intelligence for POLARIS context injection.
    Includes: symbiotic candidates (telegram+wallet) + X scout community intel.
    """
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT
                s.token_address,
                s.channel_trust,
                s.symbiotic_conviction   AS conviction,
                s.wallet_address,
                s.signal_json,
                ww.profit_score          AS wallet_profit,
                ww.trade_count,
                ww.profitable_count
            FROM symbiotic_candidates s
            LEFT JOIN watched_wallets ww ON s.wallet_address = ww.wallet_address
            WHERE s.created_at >= (strftime('%s','now') - 2592000)
              AND s.symbiotic_conviction > 0.55
            ORDER BY s.symbiotic_conviction DESC
            LIMIT 10
        """).fetchall()

        # X scout community intel — what the world is building and discussing
        x_rows = conn.execute("""
            SELECT token, message, confidence, timestamp
            FROM cognition_log
            WHERE stage = 'X_SCOUT'
              AND timestamp >= (strftime('%s','now') - 86400)
            ORDER BY confidence DESC, timestamp DESC
            LIMIT 15
        """).fetchall()

    lines = []

    if rows:
        lines.append("=== SYMBIOTIC INTELLIGENCE (top signals, last 30d) ===")
        for r in rows:
            sig = json.loads(r["signal_json"] or "{}")
            lines.append(
                f"Token: {str(r['token_address'] or '')[:12]}  "
                f"Conviction: {float(r['conviction'] or 0):.2f}  "
                f"Channel trust: {float(r['channel_trust'] or 0):.2f}  "
                f"Wallet profit: {float(r['wallet_profit'] or 0):.2f}  "
                f"Channel: {sig.get('channel','?')}  "
                f"Delta: {sig.get('time_delta_sec','?')}s"
            )

    if x_rows:
        lines.append("")
        lines.append("=== X/TWITTER COMMUNITY INTELLIGENCE (last 24h) ===")
        lines.append("What the community is building and discussing:")
        for r in x_rows:
            hashtag = str(r["token"] or "")
            msg     = str(r["message"] or "")
            # Strip the X_SCOUT prefix for cleaner context
            clean = msg.replace("[X_SCOUT] ", "").strip()
            lines.append(f"  [{hashtag}] {clean[:180]}")

    return "\n".join(lines) if lines else ""


def run_router() -> int:
    """Routes fresh high-conviction candidates, applies safety checks."""
    now   = time.time()
    stale = now - (STALE_MINUTES * 60)
    routed = 0

    with get_connection() as conn:
        # Mark stale
        conn.execute("""
            UPDATE symbiotic_candidates
            SET status = 'stale'
            WHERE status = 'fresh'
              AND updated_at < ?
        """, (stale,))

        # Fetch fresh candidates above route threshold
        candidates = conn.execute("""
            SELECT * FROM symbiotic_candidates
            WHERE status = 'fresh'
              AND symbiotic_conviction >= ?
              AND updated_at >= ?
            ORDER BY symbiotic_conviction DESC
        """, (ROUTE_THRESHOLD, stale)).fetchall()

        for c in candidates:
            token   = c["token_address"]
            wallet  = c["wallet_address"] or ""

            safe, reason = is_entry_safe(token, wallet)
            if not safe:
                conn.execute("""
                    UPDATE symbiotic_candidates
                    SET status='blocked', route_reason=?, routed_at=?
                    WHERE id=?
                """, (reason, now, c["id"]))
                log.warning("ROUTER: blocked token=%s reason=%s", str(token)[:12], reason)
                continue

            conn.execute("""
                UPDATE symbiotic_candidates
                SET status='routed', routed_at=?, route_reason='OK'
                WHERE id=?
            """, (now, c["id"]))
            routed += 1
            log.info("ROUTER: routed token=%s conviction=%.2f",
                     str(token)[:12], float(c["symbiotic_conviction"] or 0))

        conn.commit()

    return routed


# ── QUALITY SCORE INTEGRATION (Grok Q4) ───────────────────────────────────────

def apply_symbiotic_boost(base_confidence: float,
                          token_address: str) -> float:
    """
    Grok Q4: multiply pattern.
    quality_score = base_confidence * (0.55 + 0.45 * symbiotic_conviction)
    Never drops below 55% of base. Strongly boosts aligned signals.
    """
    with get_connection() as conn:
        row = conn.execute("""
            SELECT MAX(symbiotic_conviction) AS sc
            FROM symbiotic_candidates
            WHERE token_address = ?
              AND status IN ('fresh', 'routed')
              AND created_at >= (strftime('%s','now') - 300)
        """, (token_address,)).fetchone()

    sc = float(row["sc"] or 0) if row and row["sc"] else 0.0
    boosted = base_confidence * (0.55 + 0.45 * sc)
    return round(boosted, 4)


# ── HEARTBEAT ─────────────────────────────────────────────────────────────────

def update_heartbeat(status: str, note: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO service_heartbeats
                    (service_name, status, last_seen_at, note)
                VALUES (?,?,?,?)
            """, (SERVICE_NAME, status, time.time(), note[:200]))
            conn.commit()
    except Exception:
        pass


# ── MAIN SERVICE LOOP ─────────────────────────────────────────────────────────

def run() -> None:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s %(message)s"
    )
    log.info("SYMBIOTIC ROUTER ONLINE")

    ensure_columns()

    score_tick  = 0
    route_tick  = 0
    metric_tick = 0

    while True:
        now = time.time()

        try:
            # Every 15s: score new signals
            if now - score_tick >= SCORE_INTERVAL:
                update_channel_accuracy()
                n = run_scorer()
                score_tick = now
                if n:
                    log.info("Scored %d new candidates", n)

            # Every 8s: route qualified candidates
            if now - route_tick >= ROUTE_INTERVAL:
                n = run_router()
                route_tick = now

            # Every 5min: update wallet metrics (heavier computation)
            if now - metric_tick >= 300:
                update_wallet_metrics()
                metric_tick = now

            update_heartbeat("ALIVE", f"scored={score_tick:.0f} routed={route_tick:.0f}")

        except Exception as e:
            log.exception("SYMBIOTIC ROUTER ERROR: %s", e)
            update_heartbeat("ERROR", str(e)[:200])

        time.sleep(4)


if __name__ == "__main__":
    run()
