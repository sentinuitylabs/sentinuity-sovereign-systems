"""
services/macro_price_feed.py
=============================
Lane 2: Sovereign Substrate — Price Feed

Fetches BTC, ETH, SOL, XRP, SUI, BNB prices every 60 seconds.
Computes RSI(14) and Bollinger Bands(20) from rolling history.
Writes to substrate_prices table.

No external paid APIs required — uses CoinGecko free tier.
Rate limit: 30 calls/min free. We make 1 call/min. Safe.
"""
from __future__ import annotations
import sys, time, logging
from pathlib import Path
from collections import deque

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [macro_price] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("macro_price_feed")

SERVICE_NAME  = "macro_price_feed"
CYCLE_SECONDS = 60
SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "SUI", "BNB"]
COINGECKO_IDS = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple",  "SUI": "sui",      "BNB": "binancecoin",
}

# Rolling price history for indicator calculation
_price_history: dict[str, deque] = {s: deque(maxlen=50) for s in SYMBOLS}


def _ensure_schema(conn) -> None:
    """Create the canonical Substrate macro price/state surfaces idempotently."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            price_usd REAL NOT NULL,
            change_24h REAL DEFAULT 0,
            volume_24h REAL DEFAULT 0,
            high_24h REAL DEFAULT 0,
            low_24h REAL DEFAULT 0,
            rsi_14 REAL DEFAULT 50,
            bb_upper REAL DEFAULT 0,
            bb_lower REAL DEFAULT 0,
            bb_width REAL DEFAULT 0,
            fetched_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_substrate_prices_symbol_time ON substrate_prices(symbol, fetched_at DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_node_state (
            symbol TEXT PRIMARY KEY,
            state TEXT DEFAULT 'WATCHING',
            edge_score REAL DEFAULT 0,
            council_ranked INTEGER DEFAULT 0,
            compression_type TEXT,
            trigger_fired INTEGER DEFAULT 0,
            updated_at REAL
        )
    """)
    conn.commit()


def _fetch_prices() -> dict[str, dict]:
    """Fetch prices from CoinGecko free API."""
    import requests
    ids = ",".join(COINGECKO_IDS.values())
    try:
        r = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": ids,
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
                "include_24hr_high": "true",
                "include_24hr_low": "true",
            },
            timeout=10,
        )
        if r.status_code != 200:
            log.warning("CoinGecko %d: %s", r.status_code, r.text[:100])
            return {}
        data = r.json()
        result = {}
        for sym, cg_id in COINGECKO_IDS.items():
            d = data.get(cg_id, {})
            if d:
                result[sym] = {
                    "price_usd":   float(d.get("usd", 0)),
                    "change_24h":  float(d.get("usd_24h_change", 0)),
                    "volume_24h":  float(d.get("usd_24h_vol", 0)),
                    "high_24h":    float(d.get("usd_24h_high", d.get("usd", 0))),
                    "low_24h":     float(d.get("usd_24h_low",  d.get("usd", 0))),
                }
        return result
    except Exception as e:
        log.warning("CoinGecko fetch error: %s", e)
        return {}


def _compute_rsi(prices: list[float], period: int = 14) -> float:
    """RSI(14) from list of closing prices."""
    if len(prices) < period + 1:
        return 50.0
    deltas = [prices[i] - prices[i-1] for i in range(1, len(prices))]
    gains  = [d for d in deltas if d > 0]
    losses = [-d for d in deltas if d < 0]
    if not gains:  return 0.0
    if not losses: return 100.0
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period
    if avg_loss == 0: return 100.0
    rs  = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return round(rsi, 2)


def _compute_bollinger(prices: list[float], period: int = 20, std_mult: float = 2.0):
    """Bollinger Bands(20, 2). Returns (upper, lower, width_pct)."""
    if len(prices) < period:
        p = prices[-1] if prices else 0
        return p * 1.05, p * 0.95, 10.0
    window = prices[-period:]
    mean   = sum(window) / period
    std    = (sum((x - mean)**2 for x in window) / period) ** 0.5
    upper  = mean + std_mult * std
    lower  = mean - std_mult * std
    width  = ((upper - lower) / mean * 100) if mean > 0 else 0
    return round(upper, 8), round(lower, 8), round(width, 2)


def _run_cycle() -> dict:
    prices = _fetch_prices()
    if not prices:
        return {"written": 0}

    now = time.time()
    written = 0

    import sqlite3 as _sq
    with get_connection() as conn:
        conn.row_factory = _sq.Row
        _ensure_schema(conn)
        for sym, data in prices.items():
            price = data["price_usd"]
            if price <= 0:
                continue

            # Update rolling history
            _price_history[sym].append(price)
            hist = list(_price_history[sym])

            # Compute indicators
            rsi = _compute_rsi(hist)
            bb_upper, bb_lower, bb_width = _compute_bollinger(hist)

            conn.execute("""
                INSERT INTO substrate_prices
                    (symbol, price_usd, change_24h, volume_24h, high_24h, low_24h,
                     rsi_14, bb_upper, bb_lower, bb_width, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)
            """, (
                sym, price, data["change_24h"], data["volume_24h"],
                data["high_24h"], data["low_24h"],
                rsi, bb_upper, bb_lower, bb_width, now,
            ))
            written += 1
            log.debug("[PRICE] %s $%.4f RSI=%.1f BB_W=%.1f%%", sym, price, rsi, bb_width)

        # Keep substrate_prices table lean — keep last 200 rows per symbol
        for sym in SYMBOLS:
            conn.execute("""
                DELETE FROM substrate_prices
                WHERE symbol=? AND id NOT IN (
                    SELECT id FROM substrate_prices WHERE symbol=?
                    ORDER BY fetched_at DESC LIMIT 200
                )
            """, (sym, sym))

        conn.commit()

    return {"written": written, "symbols": list(prices.keys())}


def run() -> None:
    log.info("Macro price feed started — cycle=%ds symbols=%s",
             CYCLE_SECONDS, SYMBOLS)
    update_heartbeat(SERVICE_NAME, "starting", "macro_price_feed online")

    # Pre-warm history with a few cycles before channel starts analysing
    while True:
        try:
            stats = _run_cycle()
            note = f"written={stats['written']} syms={stats.get('symbols', [])}"
            log.info("[CYCLE] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note,
                             work_processed=stats["written"])
        except Exception as exc:
            log.warning("[FEED_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
