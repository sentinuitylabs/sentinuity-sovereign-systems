#!/usr/bin/env python3
"""
services/winrate_truth.py — CANONICAL WIN RATE (Task 1)

Single source of truth for win rate. Reads paper_positions ONLY — never
paper_wallet, never paper_learning_state, never polaris_trade_reviews.

Definition (directive):
  closed  : status in ('CLOSED','closed') OR closed_at IS NOT NULL,
            excluding CANCELLED / INVALID / N/A style rows
  winner  : win_loss='WIN' OR realized_pnl_usd>0 OR final_exec_pct>0
  loser   : win_loss='LOSS' OR realized_pnl_usd<0 OR final_exec_pct<0
  breakeven otherwise

The UI card and the CLI call the SAME compute_winrate() so they can never
disagree. Also persists a snapshot row into
sentinuity_intelligence.db::winrate_truth for history/no-restart refresh.

CLI:
  python services/winrate_truth.py            # print + store
  python services/winrate_truth.py --no-store # print only (pure read)
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_lane_common import (  # noqa: E402
    find_matrix_db, find_intel_db, connect_ro, connect_intel_rw,
    table_exists, cols, fnum,
)

EXCLUDE_STATUSES = ("CANCELLED", "CANCELED", "INVALID", "N/A", "NA", "VOID")

DDL = """
CREATE TABLE IF NOT EXISTS winrate_truth (
    id INTEGER PRIMARY KEY CHECK (id=1),
    win_rate_all_time REAL, win_rate_24h REAL, win_rate_72h REAL,
    closed_count_all_time INTEGER, closed_count_24h INTEGER,
    closed_count_72h INTEGER,
    winner_count INTEGER, loser_count INTEGER, breakeven_count INTEGER,
    source_table TEXT DEFAULT 'paper_positions',
    latest_winrate_updated_at REAL
)
"""


def _classify_row(r: sqlite3.Row) -> Optional[str]:
    """WIN / LOSS / BREAKEVEN for a closed row; None if not a closed trade."""
    keys = r.keys()
    status = str((r["status"] if "status" in keys else "") or "").upper()
    closed_at = r["closed_at"] if "closed_at" in keys else None
    if status in EXCLUDE_STATUSES:
        return None
    if status != "CLOSED" and not closed_at:
        return None
    wl = str((r["win_loss"] if "win_loss" in keys else "") or "").upper()
    pnl = fnum(r["realized_pnl_usd"] if "realized_pnl_usd" in keys else None)
    fep = fnum(r["final_exec_pct"] if "final_exec_pct" in keys else None)
    if wl == "WIN":
        return "WIN"
    if wl == "LOSS":
        return "LOSS"
    if pnl is not None and pnl != 0:
        return "WIN" if pnl > 0 else "LOSS"
    if fep is not None and fep != 0:
        return "WIN" if fep > 0 else "LOSS"
    return "BREAKEVEN"


def compute_winrate(matrix_db: Optional[Path] = None) -> Dict[str, Any]:
    """The one true calculation. Read-only. UI and CLI both call this."""
    now = time.time()
    out: Dict[str, Any] = {
        "win_rate_all_time": None, "win_rate_24h": None, "win_rate_72h": None,
        "closed_count_all_time": 0, "closed_count_24h": 0, "closed_count_72h": 0,
        "winner_count": 0, "loser_count": 0, "breakeven_count": 0,
        "source_table": "paper_positions",
        "latest_winrate_updated_at": now, "error": None,
    }
    matrix = find_matrix_db(str(matrix_db) if matrix_db else None)
    if matrix is None:
        out["error"] = "matrix DB not found"
        return out
    con = connect_ro(matrix)
    try:
        if not table_exists(con, "paper_positions"):
            out["error"] = "paper_positions table missing"
            return out
        rows = con.execute("SELECT * FROM paper_positions").fetchall()
    finally:
        con.close()

    buckets = {"all": [0, 0, 0], "24h": [0, 0, 0], "72h": [0, 0, 0]}  # w,l,b
    for r in rows:
        cls = _classify_row(r)
        if cls is None:
            continue
        keys = r.keys()
        ct = fnum(r["closed_at"] if "closed_at" in keys else None) or \
             fnum(r["opened_at"] if "opened_at" in keys else None) or 0
        idx = {"WIN": 0, "LOSS": 1, "BREAKEVEN": 2}[cls]
        buckets["all"][idx] += 1
        if now - ct <= 24 * 3600:
            buckets["24h"][idx] += 1
        if now - ct <= 72 * 3600:
            buckets["72h"][idx] += 1

    def rate(b):
        n = sum(b)
        decided = b[0] + b[1]
        return (100.0 * b[0] / decided) if decided else None, n

    out["win_rate_all_time"], out["closed_count_all_time"] = rate(buckets["all"])
    out["win_rate_24h"], out["closed_count_24h"] = rate(buckets["24h"])
    out["win_rate_72h"], out["closed_count_72h"] = rate(buckets["72h"])
    out["winner_count"] = buckets["all"][0]
    out["loser_count"] = buckets["all"][1]
    out["breakeven_count"] = buckets["all"][2]
    return out


def store(w: Dict[str, Any], intel_db: Optional[Path] = None) -> None:
    path = find_intel_db(str(intel_db) if intel_db else None)
    con = connect_intel_rw(path)
    try:
        con.execute(DDL)
        con.execute(
            """INSERT INTO winrate_truth
               (id, win_rate_all_time, win_rate_24h, win_rate_72h,
                closed_count_all_time, closed_count_24h, closed_count_72h,
                winner_count, loser_count, breakeven_count,
                source_table, latest_winrate_updated_at)
               VALUES (1,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(id) DO UPDATE SET
                win_rate_all_time=excluded.win_rate_all_time,
                win_rate_24h=excluded.win_rate_24h,
                win_rate_72h=excluded.win_rate_72h,
                closed_count_all_time=excluded.closed_count_all_time,
                closed_count_24h=excluded.closed_count_24h,
                closed_count_72h=excluded.closed_count_72h,
                winner_count=excluded.winner_count,
                loser_count=excluded.loser_count,
                breakeven_count=excluded.breakeven_count,
                source_table=excluded.source_table,
                latest_winrate_updated_at=excluded.latest_winrate_updated_at""",
            (w["win_rate_all_time"], w["win_rate_24h"], w["win_rate_72h"],
             w["closed_count_all_time"], w["closed_count_24h"],
             w["closed_count_72h"], w["winner_count"], w["loser_count"],
             w["breakeven_count"], w["source_table"],
             w["latest_winrate_updated_at"]))
        con.commit()
    finally:
        con.close()


def fmt(v: Optional[float], n: int) -> str:
    """Directive: zero sample shows n/a, never 0.0%."""
    if v is None or n == 0:
        return "n/a"
    return f"{v:.1f}%"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--matrix-db", default=None)
    ap.add_argument("--intel-db", default=None)
    ap.add_argument("--no-store", action="store_true")
    a = ap.parse_args()

    w = compute_winrate(Path(a.matrix_db) if a.matrix_db else None)
    if w["error"]:
        print(f"[winrate_truth] ERROR: {w['error']}")
        return 1
    print("WINRATE TRUTH — source: paper_positions (canonical)")
    print(f"  all time : {fmt(w['win_rate_all_time'], w['closed_count_all_time'])} "
          f"({w['closed_count_all_time']} closed: {w['winner_count']}W / "
          f"{w['loser_count']}L / {w['breakeven_count']}BE)")
    print(f"  24h      : {fmt(w['win_rate_24h'], w['closed_count_24h'])} "
          f"({w['closed_count_24h']} closed)")
    print(f"  72h      : {fmt(w['win_rate_72h'], w['closed_count_72h'])} "
          f"({w['closed_count_72h']} closed)")
    if not a.no_store:
        store(w, Path(a.intel_db) if a.intel_db else None)
        print("  [stored → winrate_truth]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
