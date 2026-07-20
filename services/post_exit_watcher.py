#!/usr/bin/env python3
"""
services/post_exit_watcher.py — the missing instrument.

PROVEN GAP: mtm_ticks marks OPEN positions only. Median post-exit tick horizon
is 47.7s. We have never observed a token after selling it, so "did we snip a
runner?" has never been answerable.

SCOPE (deliberately cheap):
  * only positions whose exit_pct >= WATCH_MIN_EXIT_PCT (default 75)
  * only for WATCH_WINDOW_MIN minutes after close (default 30)
  * one batched Jupiter call per cycle, DexScreener fallback per mint
  * writes ONLY to intelligence.post_exit_ticks. Touches no trading table.

    python services/post_exit_watcher.py --once
    python services/post_exit_watcher.py --loop --interval 20
"""
from __future__ import annotations
import sqlite3, time, sys, os, argparse

HOT   = "sentinuity_matrix.db"
INTEL = "sentinuity_intelligence.db"

DDL = """CREATE TABLE IF NOT EXISTS post_exit_ticks(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    position_id INTEGER, mint_address TEXT, ts REAL,
    price_usd REAL, source TEXT, secs_after_exit REAL,
    UNIQUE(position_id, ts))"""


def _watchlist(conn, min_exit_pct: float, window_min: float):
    now = time.time()
    rows = conn.execute("""
        SELECT id, mint_address, entry_price, exit_price, closed_at, position_size_usd, exit_reason
        FROM paper_positions
        WHERE status='CLOSED' AND closed_at IS NOT NULL
          AND closed_at >= ? AND entry_price>0 AND exit_price>0""",
        (now - window_min*60,)).fetchall()
    out = []
    for r in rows:
        e, x = float(r[2]), float(r[3])
        pct = (x - e) / e * 100.0
        if pct >= min_exit_pct:
            out.append({"pid": r[0], "mint": r[1], "entry": e, "exit": x,
                        "closed_at": float(r[4]), "size": float(r[5] or 0),
                        "exit_pct": pct, "reason": r[6]})
    return out


def _fetch(mints):
    """Batched price fetch. Reuses the enricher's Jupiter path; DexScreener fallback."""
    prices = {}
    try:
        import requests
        sys.path.insert(0, "services"); sys.path.insert(0, ".")
        from services.price_enricher import fetch_prices, refresh_price_dexscreener
        s = requests.Session()
        try:
            data = fetch_prices(s, mints)
            for m in mints:
                d = data.get(m) or {}
                p = d.get("usdPrice") or d.get("price") or d.get("usd_price")
                if p: prices[m] = (float(p), "jupiter")
        except Exception:
            pass
        for m in mints:
            if m not in prices:
                try:
                    p = refresh_price_dexscreener(s, m)
                    if p: prices[m] = (float(p), "dexscreener")
                except Exception:
                    pass
    except Exception as e:
        print(f"  [warn] price fetch unavailable: {e}")
    return prices


def cycle(min_exit_pct=75.0, window_min=30.0, verbose=True):
    hot = sqlite3.connect(f"file:{HOT}?mode=ro", uri=True)
    watch = _watchlist(hot, min_exit_pct, window_min)
    hot.close()
    if not watch:
        if verbose: print(f"  no closed trades >= +{min_exit_pct:.0f}% in last {window_min:.0f}m")
        return 0
    mints = sorted({w["mint"] for w in watch})
    if verbose:
        print(f"  watching {len(watch)} trades / {len(mints)} mints "
              f"(exit >= +{min_exit_pct:.0f}%, window {window_min:.0f}m)")
    prices = _fetch(mints)
    if not prices:
        if verbose: print("  no prices returned this cycle")
        return 0
    ic = sqlite3.connect(INTEL); ic.execute(DDL)
    now = time.time(); wrote = 0
    for w in watch:
        pr = prices.get(w["mint"])
        if not pr: continue
        px, src = pr
        try:
            ic.execute("INSERT OR IGNORE INTO post_exit_ticks"
                       "(position_id,mint_address,ts,price_usd,source,secs_after_exit)"
                       " VALUES(?,?,?,?,?,?)",
                       (w["pid"], w["mint"], now, px, src, now - w["closed_at"]))
            wrote += 1
            if verbose:
                vs_exit = (px - w["exit"]) / w["exit"] * 100
                vs_entry = (px - w["entry"]) / w["entry"] * 100
                print(f"    id={w['pid']:<5} exit +{w['exit_pct']:.0f}%  now {vs_entry:+.0f}% vs entry"
                      f"  ({vs_exit:+.0f}% vs our exit)  {(now-w['closed_at'])/60:.0f}m after")
        except Exception:
            pass
    ic.commit(); ic.close()

    # ---- honour the EXISTING UI contract: trade_afterlife_metrics (hot db) ----
    # sovereign_hub's violet "how far it ran" bar reads this table via
    # trade_afterlife_tracker.get_afterlife(). The tracker was never launched and
    # read market_snapshots (dies ~48s after exit). We fill it from live polls.
    try:
        hc = sqlite3.connect(HOT, timeout=8)
        hc.execute("""CREATE TABLE IF NOT EXISTS trade_afterlife_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT, source_trade_id INTEGER UNIQUE,
            mint TEXT, closed_at REAL, close_price REAL,
            max_price_after_close REAL, min_price_after_close REAL,
            max_pct_after_close REAL, min_pct_after_close REAL,
            time_to_post_exit_peak_sec REAL, observation_window_sec REAL,
            complete INTEGER DEFAULT 0, created_at REAL, updated_at REAL)""")
        hc.execute("CREATE INDEX IF NOT EXISTS idx_afterlife_trade "
                   "ON trade_afterlife_metrics(source_trade_id)")
        ic2 = sqlite3.connect(f"file:{INTEL}?mode=ro", uri=True)
        for w in watch:
            r = ic2.execute(
                "SELECT MAX(price_usd), MIN(price_usd), COUNT(*) FROM post_exit_ticks "
                "WHERE position_id=?", (w["pid"],)).fetchone()
            if not r or r[0] is None: continue
            mx, mn = float(r[0]), float(r[1])
            pk = ic2.execute("SELECT secs_after_exit FROM post_exit_ticks "
                             "WHERE position_id=? ORDER BY price_usd DESC LIMIT 1",
                             (w["pid"],)).fetchone()
            secs = float(pk[0]) if pk else 0.0
            age = now - w["closed_at"]
            done = 1 if age >= window_min*60 else 0
            hc.execute("""INSERT INTO trade_afterlife_metrics
                (source_trade_id,mint,closed_at,close_price,max_price_after_close,
                 min_price_after_close,max_pct_after_close,min_pct_after_close,
                 time_to_post_exit_peak_sec,observation_window_sec,complete,created_at,updated_at)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(source_trade_id) DO UPDATE SET
                  max_price_after_close=excluded.max_price_after_close,
                  min_price_after_close=excluded.min_price_after_close,
                  max_pct_after_close=excluded.max_pct_after_close,
                  min_pct_after_close=excluded.min_pct_after_close,
                  time_to_post_exit_peak_sec=excluded.time_to_post_exit_peak_sec,
                  observation_window_sec=excluded.observation_window_sec,
                  complete=excluded.complete, updated_at=excluded.updated_at""",
                (w["pid"], w["mint"], w["closed_at"], w["exit"], mx, mn,
                 (mx - w["exit"]) / w["exit"] * 100.0,
                 (mn - w["exit"]) / w["exit"] * 100.0,
                 secs, age, done, now, now))
        ic2.close(); hc.commit(); hc.close()
    except Exception as e:
        if verbose: print(f"  [warn] afterlife upsert: {e}")
    return wrote


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--loop", action="store_true")
    ap.add_argument("--interval", type=float, default=20.0)
    ap.add_argument("--min-exit-pct", type=float, default=75.0)
    ap.add_argument("--window-min", type=float, default=30.0)
    a = ap.parse_args()
    print("="*66); print("  POST-EXIT WATCHER — observes tokens AFTER we sell"); print("="*66)
    if a.loop:
        print(f"  loop every {a.interval:.0f}s. Ctrl-C to stop.\n")
        try:
            while True:
                cycle(a.min_exit_pct, a.window_min)
                time.sleep(a.interval)
        except KeyboardInterrupt:
            print("\n  stopped.")
    else:
        n = cycle(a.min_exit_pct, a.window_min)
        print(f"\n  wrote {n} post-exit ticks -> {INTEL}.post_exit_ticks")
