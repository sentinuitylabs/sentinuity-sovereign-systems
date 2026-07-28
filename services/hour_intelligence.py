#!/usr/bin/env python3
"""
services/hour_intelligence.py — HOUR COLOURMAP + PRESSURE + SUB-100 (Tasks 2/3/4)

Canonical trade source: paper_positions. NEVER paper_wallet or
paper_learning_state (that mis-selection is what produced the fake one-row
hourly audit). Market source: market_snapshots.

Builds three evidence-based, ROLLING tables in sentinuity_intelligence.db —
no hardcoded golden hours; a cold hour can warm and a golden hour can decay
because every refresh recomputes from the trailing 7d window:

  hourly_performance_profile   (Task 2)
  hourly_market_pressure       (Task 3)
  sub100_hour_profile          (Task 4)

Local hour = AEST (operator timezone doctrine).

Run:
  python services/hour_intelligence.py --once
  python services/hour_intelligence.py            # loop every 5 min
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from statistics import median
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_lane_common import (  # noqa: E402
    find_matrix_db, find_intel_db, connect_ro, connect_intel_rw,
    table_exists, cols, pick, fnum, aest_hour, profit_factor,
)

FORBIDDEN_SOURCES = ("paper_wallet", "paper_learning_state")

COLOUR_BANDS = [  # (min_score, band)  — sample gate applied first
    (80, "BRIGHT_GOLD_PRIME"),
    (65, "SOFT_GOLD_STRONG"),
    (50, "EMERALD_WARM"),
    (35, "CYAN_WATCH"),
    (20, "DEEP_VIOLET_COLD"),
    (0,  "RED_VIOLET_DANGER"),
]
LOW_SAMPLE_N = 3   # below this → VIOLET_GLASS_LOW_SAMPLE regardless of score

DDL = [
    """CREATE TABLE IF NOT EXISTS hourly_performance_profile (
        local_hour INTEGER PRIMARY KEY,
        sample_count_3h INTEGER, sample_count_12h INTEGER,
        sample_count_24h INTEGER, sample_count_72h INTEGER,
        sample_count_7d INTEGER,
        net_pnl REAL, win_rate REAL, median_pnl REAL,
        avg_winner REAL, avg_loser REAL, profit_factor REAL,
        runner_50_count INTEGER, runner_100_count INTEGER,
        runner_300_count INTEGER, runner_600_count INTEGER,
        hard_stop_rate REAL, max_hold_rate REAL,
        lilypad_full_exit_rate REAL, missed_runner_count INTEGER,
        stale_entry_count INTEGER, avg_executor_latency_sec REAL,
        avg_oracle_price_age_sec REAL, signal_starved_count INTEGER,
        score REAL, colour_band TEXT, confidence_level TEXT,
        updated_at REAL)""",
    """CREATE TABLE IF NOT EXISTS hourly_market_pressure (
        local_hour INTEGER PRIMARY KEY,
        buy_pressure_proxy REAL, sell_pressure_proxy REAL,
        buy_sell_ratio REAL, volume_acceleration REAL,
        liquidity_change_proxy REAL, market_tide_state TEXT,
        pressure_score REAL, sample_count INTEGER, updated_at REAL)""",
    """CREATE TABLE IF NOT EXISTS sub100_hour_profile (
        local_hour INTEGER PRIMARY KEY,
        sub100_entries INTEGER, sub100_net_pnl REAL, sub100_win_rate REAL,
        sub100_lilypad_rate REAL, sub100_hard_stop_rate REAL,
        sub100_avg_hold_seconds REAL,
        sub100_runner_rate_50 REAL, sub100_runner_rate_100 REAL,
        sub100_runner_rate_300 REAL,
        sub100_hot_potato_score REAL, recommended_mode TEXT,
        updated_at REAL)""",
]


# ---------------------------------------------------------------- load

def load_trades_7d(mcon: sqlite3.Connection, now: float) -> List[Dict[str, Any]]:
    """Closed paper_positions rows in trailing 7d, with the Task-2 fields."""
    if not table_exists(mcon, "paper_positions"):
        return []
    out = []
    for r in mcon.execute("SELECT * FROM paper_positions"):
        keys = r.keys()
        status = str((r["status"] if "status" in keys else "") or "").upper()
        closed_at = fnum(r["closed_at"] if "closed_at" in keys else None)
        if status != "CLOSED" and not closed_at:
            continue
        ct = closed_at or fnum(r["opened_at"] if "opened_at" in keys else None)
        if not ct or now - ct > 7 * 86400:
            continue
        d = {
            "closed_at": ct,
            "opened_at": fnum(pick(r, "opened_at")),
            "pnl": fnum(pick(r, "realized_pnl_usd"), 0.0) or 0.0,
            "exit_reason": str(pick(r, "exit_reason", default="") or "").upper(),
            "exit_category": str(pick(r, "exit_category", default="") or "").upper(),
            "win_loss": str(pick(r, "win_loss", default="") or "").upper(),
            "peak_pct": fnum(pick(r, "peak_pnl_pct", "runner_peak_pct")),
            "final_exec_pct": fnum(pick(r, "final_exec_pct")),
            "exit_gap_from_peak_pct": fnum(pick(r, "exit_gap_from_peak_pct")),
            "entry_mcap": fnum(pick(r, "entry_market_cap_usd", "market_cap_usd")),
            "exec_age": fnum(pick(r, "live_exec_age_sec")),
            "oracle_age": fnum(pick(r, "qualify_price_age_sec")),
            "integrity": str(pick(r, "price_integrity_status", default="") or "").upper(),
            "exit_quality": str(pick(r, "exit_quality_tag", default="") or "").upper(),
            "hold": None,
        }
        if d["opened_at"] and d["closed_at"]:
            d["hold"] = max(0.0, d["closed_at"] - d["opened_at"])
        d["hour"] = aest_hour(d["opened_at"] or d["closed_at"])
        d["win"] = d["win_loss"] == "WIN" or (d["win_loss"] != "LOSS" and d["pnl"] > 0)
        out.append(d)
    return out


# ---------------------------------------------------------------- task 2

def _band(score: float, n: int) -> str:
    if n < LOW_SAMPLE_N:
        return "VIOLET_GLASS_LOW_SAMPLE"
    for mn, band in COLOUR_BANDS:
        if score >= mn:
            return band
    return "RED_VIOLET_DANGER"


def build_hourly_performance(icon, trades: List[Dict[str, Any]], now: float) -> None:
    for h in range(24):
        T = [t for t in trades if t["hour"] == h]
        def within(hrs):
            return sum(1 for t in T if now - t["closed_at"] <= hrs * 3600)
        pnls = [t["pnl"] for t in T]
        wins = [t["pnl"] for t in T if t["win"]]
        losses = [t["pnl"] for t in T if not t["win"] and t["pnl"] < 0]
        n = len(T)
        wr = (100.0 * len(wins) / n) if n else None
        pf = profit_factor(pnls) if pnls else None
        pf_v = None if pf is None else (99.0 if pf == float("inf") else pf)
        def peak_ge(x):
            return sum(1 for t in T if (t["peak_pct"] or 0) >= x)
        hard = sum(1 for t in T if "HARD_STOP" in t["exit_reason"] or
                   "HARD_STOP" in t["exit_category"])
        mxh = sum(1 for t in T if "MAX_HOLD" in t["exit_reason"] or
                  "MAX_HOLD" in t["exit_category"])
        lily = sum(1 for t in T if "LILYPAD" in t["exit_reason"] or
                   "LILYPAD" in t["exit_quality"])
        # missed runner: peaked >=50% but closed <= +10% (gap collapse)
        missed = sum(1 for t in T if (t["peak_pct"] or 0) >= 50 and
                     (t["final_exec_pct"] if t["final_exec_pct"] is not None
                      else (t["pnl"])) is not None and
                     ((t["final_exec_pct"] or 0) <= 10 if t["final_exec_pct"]
                      is not None else t["pnl"] <= 2))
        stale = sum(1 for t in T if (t["oracle_age"] or 0) > 90 or
                    "UNSTABLE" in t["integrity"])
        lat = [t["exec_age"] for t in T if t["exec_age"] is not None]
        oage = [t["oracle_age"] for t in T if t["oracle_age"] is not None]

        # rolling evidence score 0-100: PnL sign+size, WR, PF, bad-exit drag
        score = 50.0
        if n:
            net = sum(pnls)
            score += max(-25.0, min(25.0, net / 4.0))
            if wr is not None:
                score += (wr - 50.0) * 0.4
            if pf_v is not None:
                score += max(-10.0, min(10.0, (pf_v - 1.0) * 5.0))
            score -= (hard / n) * 15.0 + (mxh / n) * 10.0
            score += min(10.0, peak_ge(100) * 3.0)
        score = max(0.0, min(100.0, score))
        conf = ("HIGH" if n >= 10 else "MEDIUM" if n >= LOW_SAMPLE_N else "LOW")

        icon.execute(
            """INSERT INTO hourly_performance_profile VALUES
               (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(local_hour) DO UPDATE SET
               sample_count_3h=excluded.sample_count_3h,
               sample_count_12h=excluded.sample_count_12h,
               sample_count_24h=excluded.sample_count_24h,
               sample_count_72h=excluded.sample_count_72h,
               sample_count_7d=excluded.sample_count_7d,
               net_pnl=excluded.net_pnl, win_rate=excluded.win_rate,
               median_pnl=excluded.median_pnl, avg_winner=excluded.avg_winner,
               avg_loser=excluded.avg_loser, profit_factor=excluded.profit_factor,
               runner_50_count=excluded.runner_50_count,
               runner_100_count=excluded.runner_100_count,
               runner_300_count=excluded.runner_300_count,
               runner_600_count=excluded.runner_600_count,
               hard_stop_rate=excluded.hard_stop_rate,
               max_hold_rate=excluded.max_hold_rate,
               lilypad_full_exit_rate=excluded.lilypad_full_exit_rate,
               missed_runner_count=excluded.missed_runner_count,
               stale_entry_count=excluded.stale_entry_count,
               avg_executor_latency_sec=excluded.avg_executor_latency_sec,
               avg_oracle_price_age_sec=excluded.avg_oracle_price_age_sec,
               signal_starved_count=excluded.signal_starved_count,
               score=excluded.score, colour_band=excluded.colour_band,
               confidence_level=excluded.confidence_level,
               updated_at=excluded.updated_at""",
            (h, within(3), within(12), within(24), within(72), n,
             sum(pnls) if pnls else 0.0, wr,
             median(pnls) if pnls else None,
             (sum(wins) / len(wins)) if wins else None,
             (sum(losses) / len(losses)) if losses else None,
             pf_v,
             peak_ge(50), peak_ge(100), peak_ge(300), peak_ge(600),
             (hard / n) if n else None, (mxh / n) if n else None,
             (lily / n) if n else None, missed, stale,
             (sum(lat) / len(lat)) if lat else None,
             (sum(oage) / len(oage)) if oage else None,
             0,  # signal_starved_count joined later by signal_gate_sensor
             round(score, 2), _band(score, n), conf, now))


# ---------------------------------------------------------------- task 3

def build_market_pressure(icon, mcon, now: float) -> None:
    if not table_exists(mcon, "market_snapshots"):
        return
    c = set(cols(mcon, "market_snapshots"))
    tcol = "created_at" if "created_at" in c else ("timestamp" if "timestamp" in c else None)
    if not tcol:
        return
    rows = mcon.execute(
        f"SELECT * FROM market_snapshots WHERE COALESCE({tcol},0) >= ?",
        (now - 7 * 86400,)).fetchall()
    byh: Dict[int, List[sqlite3.Row]] = {h: [] for h in range(24)}
    for r in rows:
        h = aest_hour(fnum(pick(r, tcol)))
        if h is not None:
            byh[h].append(r)
    for h in range(24):
        R = byh[h]
        n = len(R)
        buy, sell, volacc, liqd = [], [], [], []
        for r in R:
            bv = fnum(pick(r, "buy_velocity"))
            p5 = fnum(pick(r, "price_change_5m"))
            p1h = fnum(pick(r, "price_change_1h"))
            va = fnum(pick(r, "vol_acceleration"))
            v5 = fnum(pick(r, "volume_5m_usd", "vol_5m_usd"))
            if bv is not None:
                buy.append(max(0.0, bv))
            if p5 is not None:
                (buy if p5 > 0 else sell).append(abs(p5))
            elif p1h is not None:
                (buy if p1h > 0 else sell).append(abs(p1h) / 12.0)
            if va is not None:
                volacc.append(va)
            elif v5 is not None:
                volacc.append(v5 / 1000.0)
            lq = fnum(pick(r, "liquidity_usd", "token_liquidity_usd"))
            if lq is not None:
                liqd.append(lq)
        bp = (sum(buy) / len(buy)) if buy else None
        sp = (sum(sell) / len(sell)) if sell else None
        ratio = (bp / sp) if (bp and sp and sp > 0) else None
        va_avg = (sum(volacc) / len(volacc)) if volacc else None
        liq_proxy = None
        if len(liqd) >= 2:
            liq_proxy = (liqd[-1] - liqd[0]) / max(1.0, abs(liqd[0])) * 100.0
        if ratio is None:
            tide = "UNKNOWN"
        elif ratio >= 1.5:
            tide = "FLOOD"
        elif ratio >= 1.05:
            tide = "RISING"
        elif ratio > 0.95:
            tide = "SLACK"
        elif ratio > 0.6:
            tide = "EBBING"
        else:
            tide = "DRAINING"
        pscore = None
        if ratio is not None:
            pscore = max(0.0, min(100.0, 50.0 + (ratio - 1.0) * 40.0
                                  + (min(va_avg, 10.0) if va_avg else 0.0)))
        icon.execute(
            """INSERT INTO hourly_market_pressure VALUES (?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(local_hour) DO UPDATE SET
               buy_pressure_proxy=excluded.buy_pressure_proxy,
               sell_pressure_proxy=excluded.sell_pressure_proxy,
               buy_sell_ratio=excluded.buy_sell_ratio,
               volume_acceleration=excluded.volume_acceleration,
               liquidity_change_proxy=excluded.liquidity_change_proxy,
               market_tide_state=excluded.market_tide_state,
               pressure_score=excluded.pressure_score,
               sample_count=excluded.sample_count,
               updated_at=excluded.updated_at""",
            (h, bp, sp, ratio, va_avg, liq_proxy, tide, pscore, n, now))


# ---------------------------------------------------------------- task 4

def build_sub100(icon, trades: List[Dict[str, Any]], now: float) -> None:
    for h in range(24):
        # never guess: only trades with a real mcap field qualify
        T = [t for t in trades if t["hour"] == h and t["entry_mcap"] is not None
             and t["entry_mcap"] < 100_000]
        n = len(T)
        wins = sum(1 for t in T if t["win"])
        wr = (100.0 * wins / n) if n else None
        net = sum(t["pnl"] for t in T)
        lily = sum(1 for t in T if "LILYPAD" in t["exit_reason"] or
                   "LILYPAD" in t["exit_quality"])
        hard = sum(1 for t in T if "HARD_STOP" in t["exit_reason"] or
                   "HARD_STOP" in t["exit_category"])
        holds = [t["hold"] for t in T if t["hold"] is not None]
        def rr(x):
            return (100.0 * sum(1 for t in T if (t["peak_pct"] or 0) >= x) / n) \
                if n else None
        # hot-potato score: high = must exit fast (danger of holding)
        hp = None
        if n:
            hp = 50.0
            hp += (hard / n) * 30.0
            hp -= ((wr or 0) - 50.0) * 0.3
            hp -= min(20.0, net / 2.0) if net > 0 else min(20.0, -net / 2.0) * -1 * -1
            hp = max(0.0, min(100.0, hp + (0 if net >= 0 else 15.0)))
        if n == 0:
            mode = "PAPER_ONLY"       # no evidence → never live-assume
        elif n < LOW_SAMPLE_N:
            mode = "PAPER_ONLY"
        elif hp is not None and hp >= 75:
            mode = "BLOCK"
        elif hp is not None and hp >= 55:
            mode = "HOT_POTATO"
        elif (wr or 0) >= 55 and net > 0:
            mode = "PRIME_RUNNER_ALLOWED"
        else:
            mode = "NORMAL"
        icon.execute(
            """INSERT INTO sub100_hour_profile VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(local_hour) DO UPDATE SET
               sub100_entries=excluded.sub100_entries,
               sub100_net_pnl=excluded.sub100_net_pnl,
               sub100_win_rate=excluded.sub100_win_rate,
               sub100_lilypad_rate=excluded.sub100_lilypad_rate,
               sub100_hard_stop_rate=excluded.sub100_hard_stop_rate,
               sub100_avg_hold_seconds=excluded.sub100_avg_hold_seconds,
               sub100_runner_rate_50=excluded.sub100_runner_rate_50,
               sub100_runner_rate_100=excluded.sub100_runner_rate_100,
               sub100_runner_rate_300=excluded.sub100_runner_rate_300,
               sub100_hot_potato_score=excluded.sub100_hot_potato_score,
               recommended_mode=excluded.recommended_mode,
               updated_at=excluded.updated_at""",
            (h, n, net if n else None, wr,
             (lily / n) if n else None, (hard / n) if n else None,
             (sum(holds) / len(holds)) if holds else None,
             rr(50), rr(100), rr(300), hp, mode, now))


# ---------------------------------------------------------------- runner

def run_once(matrix_db: Optional[Path] = None,
             intel_db: Optional[Path] = None, verbose: bool = False) -> int:
    now = time.time()
    matrix = find_matrix_db(str(matrix_db) if matrix_db else None)
    if matrix is None:
        print("[hour_intelligence] matrix DB not found")
        return 1
    intel = find_intel_db(str(intel_db) if intel_db else None)
    mcon = connect_ro(matrix)
    icon = connect_intel_rw(intel)
    try:
        for d in DDL:
            icon.execute(d)
        trades = load_trades_7d(mcon, now)
        build_hourly_performance(icon, trades, now)
        build_market_pressure(icon, mcon, now)
        build_sub100(icon, trades, now)
        icon.commit()
        if verbose:
            print(f"[hour_intelligence] source=paper_positions "
                  f"(forbidden: {', '.join(FORBIDDEN_SOURCES)})")
            print(f"[hour_intelligence] 7d closed trades: {len(trades)}")
            for r in icon.execute(
                "SELECT local_hour, sample_count_7d, net_pnl, win_rate, "
                "colour_band FROM hourly_performance_profile "
                "WHERE sample_count_7d > 0 ORDER BY local_hour"):
                print(f"   {r[0]:02d}:00 n={r[1]:3} net={r[2] or 0:+8.2f} "
                      f"wr={'-' if r[3] is None else f'{r[3]:.0f}%':>4} {r[4]}")
    finally:
        mcon.close()
        icon.close()
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=300.0)
    ap.add_argument("--matrix-db", default=None)
    ap.add_argument("--intel-db", default=None)
    ap.add_argument("--verbose", action="store_true")
    a = ap.parse_args()
    while True:
        run_once(Path(a.matrix_db) if a.matrix_db else None,
                 Path(a.intel_db) if a.intel_db else None,
                 verbose=(a.verbose or a.once))
        if a.once:
            sys.exit(0)
        time.sleep(max(60.0, a.interval))
