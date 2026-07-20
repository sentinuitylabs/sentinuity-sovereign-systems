"""
services/smart_money_metrics.py
================================
Computes per-token smart money metrics and scores.
Schema-safe: PRAGMA-checks all columns before use.
Defaults to 0 for any missing column — never crashes.
Called by market_intelligence on every qualified token.
"""
import time
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB = str(BASE_DIR / 'sentinuity_matrix.db')


def _conn():
    c = sqlite3.connect(DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _get_cols(c, table: str) -> set:
    """Return set of column names for a table. Empty set if table missing."""
    try:
        return {r[1] for r in c.execute(f'PRAGMA table_info({table})').fetchall()}
    except Exception:
        return set()


def ensure_tables():
    """Create score_performance and token_metrics if absent."""
    c = _conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS score_performance (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            score_bucket INTEGER UNIQUE,
            trades       INTEGER DEFAULT 0,
            wins         INTEGER DEFAULT 0,
            total_pnl    REAL    DEFAULT 0.0
        )
    """)
    c.execute("""
        CREATE TABLE IF NOT EXISTS token_metrics (
            token_name           TEXT,
            ts                   REAL,
            holders              INTEGER,
            holders_delta_60s    INTEGER,
            holders_delta_180s   INTEGER,
            unique_wallets_120s  INTEGER,
            wallet_cluster_score REAL,
            volume               REAL,
            volume_std_5m        REAL,
            top10_sell_ratio     REAL,
            price                REAL,
            smart_money_score    INTEGER DEFAULT 0,
            tier                 TEXT DEFAULT 'NOISE',
            PRIMARY KEY (token_name, ts)
        )
    """)
    c.commit()
    c.close()


def compute_metrics(token: str) -> dict | None:
    """
    Compute smart money metrics for a token.
    Schema-safe: all column references are PRAGMA-checked first.
    Returns dict with score/tier or None if no data available.
    """
    c = _conn()
    now = time.time()

    # ── PRAGMA check market_snapshots columns ─────────────────────────────────
    ms_cols = _get_cols(c, 'market_snapshots')
    if not ms_cols:
        c.close()
        return None

    # Choose holder column — prefer holder_count, fall back to holders
    if 'holder_count' in ms_cols:
        holder_col = 'holder_count'
    elif 'holders' in ms_cols:
        holder_col = 'holders'
    else:
        holder_col = None

    # Choose price column
    price_col = 'observed_price' if 'observed_price' in ms_cols else None

    # Choose volume column
    if 'volume_usd' in ms_cols:
        vol_col = 'volume_usd'
    elif 'volume' in ms_cols:
        vol_col = 'volume'
    else:
        vol_col = None

    # Choose timestamp column
    for ts_candidate in ('price_updated_at', 'created_at', 'timestamp'):
        if ts_candidate in ms_cols:
            ts_col = ts_candidate
            break
    else:
        ts_col = None

    if not ts_col:
        c.close()
        return None

    # ── Fetch latest snapshot ─────────────────────────────────────────────────
    select_cols = [f'COALESCE({ts_col}, 0) AS snap_ts']
    if holder_col:
        select_cols.append(f'COALESCE({holder_col}, 0) AS holders_now')
    if price_col:
        select_cols.append(f'COALESCE({price_col}, 0) AS price')
    if vol_col:
        select_cols.append(f'COALESCE({vol_col}, 0) AS vol_now')

    try:
        snap = c.execute(
            f"SELECT {', '.join(select_cols)} FROM market_snapshots "
            f"WHERE mint_address=? OR token_name=? "
            f"ORDER BY {ts_col} DESC LIMIT 1",
            (token, token)
        ).fetchone()
    except Exception:
        c.close()
        return None

    if not snap:
        c.close()
        return None

    price       = float(snap['price'])        if 'price'       in snap.keys() else 0.0
    holders_now = int(snap['holders_now'])    if 'holders_now' in snap.keys() else 0
    vol_now     = float(snap['vol_now'])      if 'vol_now'     in snap.keys() else 0.0

    # ── Holder deltas ─────────────────────────────────────────────────────────
    h_60 = 0
    h_180 = 0
    if holder_col and holders_now > 0:
        def holders_at(secs):
            try:
                r = c.execute(
                    f"SELECT COALESCE({holder_col}, 0) FROM market_snapshots "
                    f"WHERE (mint_address=? OR token_name=?) AND {ts_col} < ? "
                    f"ORDER BY {ts_col} DESC LIMIT 1",
                    (token, token, now - secs)
                ).fetchone()
                return int(r[0]) if r else holders_now
            except Exception:
                return holders_now
        h_60  = holders_now - holders_at(60)
        h_180 = holders_now - holders_at(180)

    # ── Volume history ────────────────────────────────────────────────────────
    vol_total = vol_now
    vol_std   = 0.0
    if vol_col:
        try:
            vols = [float(r[0] or 0) for r in c.execute(
                f"SELECT {vol_col} FROM market_snapshots "
                f"WHERE (mint_address=? OR token_name=?) AND {ts_col} > ?",
                (token, token, now - 300)
            ).fetchall()]
            if vols:
                vol_total = sum(vols)
                mean = vol_total / len(vols)
                vol_std = (sum((v - mean)**2 for v in vols) / len(vols)) ** 0.5
        except Exception:
            pass

    # ── Wallet cluster + sell pressure ────────────────────────────────────────
    # PRAGMA-check wallet source tables before querying
    wallets      = 0
    sell_ratio   = 0.0

    # Try wallet_pattern_observations (has wallet_address)
    wpo_cols = _get_cols(c, 'wallet_pattern_observations')
    if 'wallet_address' in wpo_cols and 'observed_at' in wpo_cols:
        try:
            wallets = c.execute(
                "SELECT COUNT(DISTINCT wallet_address) FROM wallet_pattern_observations "
                "WHERE mint_address=? AND observed_at > ?",
                (token, now - 120)
            ).fetchone()[0] or 0
        except Exception:
            wallets = 0

    # Try wallet_write_log if it has wallet-trade columns
    wwl_cols = _get_cols(c, 'wallet_write_log')
    if 'action' in wwl_cols and 'amount_sol' in wwl_cols:
        try:
            sells = float(c.execute(
                "SELECT COALESCE(SUM(amount_sol),0) FROM wallet_write_log "
                "WHERE mint_address=? AND action='sell'", (token,)
            ).fetchone()[0] or 0)
            buys = float(c.execute(
                "SELECT COALESCE(SUM(amount_sol),0) FROM wallet_write_log "
                "WHERE mint_address=? AND action='buy'", (token,)
            ).fetchone()[0] or 0.001)
            sell_ratio = sells / max(buys, 0.001)
        except Exception:
            sell_ratio = 0.0

    cluster_score = float(wallets)

    # ── Score calculation ─────────────────────────────────────────────────────
    score = 0

    # Holder growth (0-25 pts)
    if h_180 > 50:    score += 25
    elif h_180 > 25:  score += 15
    elif h_180 > 10:  score += 5

    # Wallet cluster (0-20 pts)
    if cluster_score >= 10:   score += 20
    elif cluster_score >= 5:  score += 12
    elif cluster_score >= 3:  score += 6

    # Sell pressure (0-15 pts)
    if sell_ratio < 0.3:   score += 15
    elif sell_ratio < 0.6: score += 8

    # Volume stability (0-15 pts)
    if vol_total > 0 and vol_std < 0.2 * vol_total:  score += 15
    elif vol_total > 0 and vol_std < 0.5 * vol_total: score += 8

    # Raw volume (0-10 pts)
    if vol_total > 50000:  score += 10
    elif vol_total > 10000: score += 5

    # Momentum (0-15 pts)
    if h_60 > 20:    score += 15
    elif h_60 > 10:  score += 8

    # ── Tier ─────────────────────────────────────────────────────────────────
    if score >= 70:   tier = 'ELITE_RUNNER'
    elif score >= 50: tier = 'RUNNER'
    elif score >= 30: tier = 'MOMENTUM'
    else:             tier = 'NOISE'

    # ── Persist ──────────────────────────────────────────────────────────────
    try:
        c.execute("""
            INSERT OR REPLACE INTO token_metrics VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (token, now, holders_now, h_60, h_180,
              wallets, cluster_score, vol_total, vol_std,
              sell_ratio, price, score, tier))
        c.commit()
    except Exception:
        pass

    c.close()
    return {
        'token': token, 'score': score, 'tier': tier,
        'holders_delta_60s': h_60, 'holders_delta_180s': h_180,
        'wallet_cluster_score': cluster_score,
        'top10_sell_ratio': sell_ratio,
        'volume': vol_total, 'volume_std_5m': vol_std, 'price': price
    }


def get_score(token: str) -> dict:
    """Get latest score from DB, or compute fresh."""
    c = _conn()
    try:
        r = c.execute("""
            SELECT smart_money_score, tier, holders_delta_60s, holders_delta_180s,
                   wallet_cluster_score, top10_sell_ratio, volume, ts
            FROM token_metrics WHERE token_name=?
            ORDER BY ts DESC LIMIT 1
        """, (token,)).fetchone()
        c.close()
        if r and (time.time() - float(r['ts'] or 0)) < 60:
            return dict(r)
    except Exception:
        c.close()
    return compute_metrics(token) or {'score': 0, 'tier': 'NOISE'}


def classify(score: int) -> str:
    if score >= 70: return 'ELITE_RUNNER'
    if score >= 50: return 'RUNNER'
    if score >= 30: return 'MOMENTUM'
    return 'NOISE'


def bucket_score(score: int) -> int:
    """Map score to nearest 10-point bucket."""
    return int((score or 0) // 10) * 10


def record_trade_outcome(score: int, pnl: float) -> None:
    """Called when a position closes — updates score_performance table."""
    if score is None:
        return
    bucket = bucket_score(int(score or 0))
    try:
        c = _conn()
        win = 1 if pnl > 0 else 0
        c.execute("""
            INSERT INTO score_performance (score_bucket, trades, wins, total_pnl)
            VALUES (?, 1, ?, ?)
            ON CONFLICT(score_bucket) DO UPDATE SET
                trades    = trades + 1,
                wins      = wins + excluded.wins,
                total_pnl = total_pnl + excluded.total_pnl
        """, (bucket, win, pnl))
        c.commit()
        c.close()
    except Exception:
        pass


def get_best_thresholds() -> dict:
    """Return dynamically tuned thresholds from trade performance data."""
    defaults = {'elite': 70, 'runner': 50, 'momentum': 30}
    try:
        c = _conn()
        rows = c.execute("""
            SELECT score_bucket,
                   trades,
                   wins * 1.0 / trades AS win_rate,
                   total_pnl / trades  AS avg_pnl
            FROM score_performance
            WHERE trades >= 5
            ORDER BY avg_pnl DESC
        """).fetchall()
        c.close()
        if len(rows) >= 2:
            defaults['elite']  = int(rows[0][0])
            defaults['runner'] = int(rows[1][0])
        return defaults
    except Exception:
        return defaults


# Ensure tables exist on import
try:
    ensure_tables()
except Exception:
    pass
