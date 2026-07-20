"""
wallet_intelligence_gate.py
Dropped into services/ — enhances signal scoring using smart money data.
Runs as part of market_intelligence qualification pipeline.
Not a standalone service — imported by market_intelligence.
"""
import sqlite3, time, logging
from pathlib import Path

log = logging.getLogger("wallet_gate")

def get_smart_money_boost(mint_address: str, db_path: str) -> dict:
    """
    Check if any tracked smart money wallet has recently bought this token.
    Returns confidence boost and metadata.
    
    Called during qualification — if a top wallet bought in last 120s,
    boost confidence score and flag as SMART_MONEY_CONFIRMED.
    """
    result = {
        "boost": 0.0,
        "confirmed": False,
        "wallet_label": None,
        "wallet_win_rate": None,
        "entry_age_sec": None,
    }
    try:
        db = sqlite3.connect(db_path, timeout=3)
        db.row_factory = sqlite3.Row
        now = time.time()
        cutoff = now - 300  # 5 min window

        # Check if any tracked wallet has a transaction on this mint recently
        row = db.execute("""
            SELECT tw.label, tw.win_rate, tw.avg_pnl_pct,
                   wt.timestamp as tx_time
            FROM tracked_wallets tw
            JOIN wallet_transactions wt ON wt.wallet_address = tw.wallet_address
            WHERE wt.mint_address = ?
              AND wt.timestamp > ?
              AND tw.active = 1
              AND tw.win_rate >= 0.55
            ORDER BY wt.timestamp DESC
            LIMIT 1
        """, (mint_address, cutoff)).fetchone()

        if row:
            age = now - float(row['tx_time'])
            win_rate = float(row['win_rate'] or 0)
            # Boost scales with win rate and recency
            # 70%+ win rate wallet buying 30s ago = +0.25 boost
            recency_factor = max(0, 1 - (age / 300))
            boost = (win_rate - 0.5) * recency_factor * 0.5
            result.update({
                "boost": round(min(boost, 0.30), 3),  # cap at +0.30
                "confirmed": True,
                "wallet_label": row['label'],
                "wallet_win_rate": win_rate,
                "entry_age_sec": round(age, 1),
            })
        db.close()
    except Exception as e:
        log.debug("wallet gate error: %s", e)
    return result


def get_smart_money_avg_params(db_path: str) -> dict:
    """
    Get average entry parameters from tracked wallets.
    Used to calibrate qualification thresholds.
    """
    params = {
        "avg_curve_pct": None,
        "avg_mcap_usd": None,
        "avg_hold_sec": None,
    }
    try:
        db = sqlite3.connect(db_path, timeout=3)
        db.row_factory = sqlite3.Row
        for key, param in [
            ('SMART_MONEY_AVG_CURVE', 'avg_curve_pct'),
            ('SMART_MONEY_AVG_MCAP_ENTRY', 'avg_mcap_usd'),
            ('SMART_MONEY_AVG_HOLD_SEC', 'avg_hold_sec'),
        ]:
            row = db.execute(
                "SELECT value FROM system_config WHERE key=?", (key,)
            ).fetchone()
            if row and row['value']:
                try:
                    params[param] = float(row['value'])
                except ValueError:
                    pass
        db.close()
    except Exception:
        pass
    return params


def store_wallet_transaction(wallet_address: str, mint_address: str,
                              tx_type: str, db_path: str) -> None:
    """Record a wallet transaction for correlation tracking."""
    try:
        db = sqlite3.connect(db_path, timeout=3)
        db.execute("""
            CREATE TABLE IF NOT EXISTS wallet_transactions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                wallet_address TEXT,
                mint_address TEXT,
                tx_type TEXT,
                timestamp REAL DEFAULT (strftime('%s','now')),
                noted INTEGER DEFAULT 0
            )
        """)
        db.execute("""
            INSERT INTO wallet_transactions
            (wallet_address, mint_address, tx_type, timestamp)
            VALUES (?, ?, ?, ?)
        """, (wallet_address, mint_address, tx_type, time.time()))
        db.commit()
        db.close()
    except Exception as e:
        log.debug("store wallet tx error: %s", e)
