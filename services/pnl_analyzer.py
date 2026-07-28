"""
Sentinuity PnL Analyzer — sign-off service module.

Drop into:
    services/pnl_analyzer.py

Purpose:
    - Closed-trade PnL summary
    - Best/worst cluster hours
    - Entry/exit slippage estimates where execution columns exist
    - Gas/fee summary where fee columns exist
    - Position-size suggestion from observed edge

Safe design:
    - Does not trade.
    - Does not write to DB.
    - Introspects SQLite schema before querying optional columns.
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"


def _connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return bool(row)


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _first_col(cols: set[str], candidates: List[str]) -> Optional[str]:
    for c in candidates:
        if c in cols:
            return c
    return None


def load_closed_trades(days: int = 5, db_path: Path = DB_PATH) -> pd.DataFrame:
    """
    Returns recent closed positions with best-effort normalized columns.

    Normalized output columns include:
        token_name, mint_address, opened_at, closed_at, position_size_usd,
        entry_price, exit_price, live_entry_price, live_exit_price,
        realized_pnl_usd, roi_pct, entry_slippage_pct, exit_slippage_pct,
        total_fee_usd, hold_time_sec, hour_utc
    """
    since = int(time.time()) - int(days) * 86400

    with _connect(db_path) as conn:
        if not _table_exists(conn, "paper_positions"):
            return pd.DataFrame()

        c = _cols(conn, "paper_positions")

        token_col = _first_col(c, ["token_name", "symbol", "name"])
        mint_col = _first_col(c, ["mint_address", "mint", "token_address"])
        opened_col = _first_col(c, ["opened_at", "created_at", "entry_ts", "timestamp"])
        closed_col = _first_col(c, ["closed_at", "exit_at", "updated_at"])
        status_col = _first_col(c, ["status"])
        size_col = _first_col(c, ["position_size_usd", "size_usd", "amount_usd", "notional_usd"])

        entry_col = _first_col(c, ["entry_price", "intended_entry_price", "quoted_entry_price"])
        exit_col = _first_col(c, ["exit_price", "intended_exit_price", "quoted_exit_price", "close_price"])
        live_entry_col = _first_col(c, ["live_exec_price", "entry_exec_price", "actual_entry_price", "buy_exec_price"])
        live_exit_col = _first_col(c, ["live_exit_price", "exit_exec_price", "actual_exit_price", "sell_exec_price"])

        pnl_col = _first_col(c, ["realized_pnl_usd", "pnl_usd", "profit_usd"])
        fee_cols = [x for x in ["gas_usd", "fee_usd", "network_fee_usd", "priority_fee_usd", "jito_tip_usd", "tx_fee_usd"] if x in c]

        select_exprs = [
            f"{token_col} AS token_name" if token_col else "NULL AS token_name",
            f"{mint_col} AS mint_address" if mint_col else "NULL AS mint_address",
            f"{opened_col} AS opened_at" if opened_col else "NULL AS opened_at",
            f"{closed_col} AS closed_at" if closed_col else "NULL AS closed_at",
            f"{size_col} AS position_size_usd" if size_col else "NULL AS position_size_usd",
            f"{entry_col} AS entry_price" if entry_col else "NULL AS entry_price",
            f"{exit_col} AS exit_price" if exit_col else "NULL AS exit_price",
            f"{live_entry_col} AS live_entry_price" if live_entry_col else "NULL AS live_entry_price",
            f"{live_exit_col} AS live_exit_price" if live_exit_col else "NULL AS live_exit_price",
            f"{pnl_col} AS realized_pnl_usd" if pnl_col else "NULL AS realized_pnl_usd",
        ]

        if fee_cols:
            select_exprs.append(" + ".join([f"COALESCE({fc},0)" for fc in fee_cols]) + " AS total_fee_usd")
        else:
            select_exprs.append("0.0 AS total_fee_usd")

        where = []
        if status_col:
            where.append(f"{status_col}='CLOSED'")
        if opened_col:
            where.append(f"COALESCE({opened_col},0) >= {int(since)}")
        where_sql = "WHERE " + " AND ".join(where) if where else ""

        sql = f"""
            SELECT {", ".join(select_exprs)}
            FROM paper_positions
            {where_sql}
            ORDER BY COALESCE({closed_col or opened_col or 'rowid'}, rowid) DESC
            LIMIT 5000
        """
        df = pd.read_sql_query(sql, conn)

    if df.empty:
        return df

    for col in [
        "opened_at", "closed_at", "position_size_usd", "entry_price", "exit_price",
        "live_entry_price", "live_exit_price", "realized_pnl_usd", "total_fee_usd"
    ]:
        if col in df:
            df[col] = pd.to_numeric(df[col], errors="coerce")

    # ROI based on actual realized PnL and position size.
    df["roi_pct"] = (df["realized_pnl_usd"] / df["position_size_usd"].replace(0, pd.NA)) * 100.0

    # Entry slippage: actual/live entry vs intended/quoted entry.
    df["entry_slippage_pct"] = pd.NA
    mask = df["entry_price"].notna() & df["live_entry_price"].notna() & (df["entry_price"] != 0)
    df.loc[mask, "entry_slippage_pct"] = ((df.loc[mask, "live_entry_price"] - df.loc[mask, "entry_price"]) / df.loc[mask, "entry_price"]) * 100.0

    # Exit slippage: actual/live exit vs intended/quoted exit. Negative means worse received price.
    df["exit_slippage_pct"] = pd.NA
    mask = df["exit_price"].notna() & df["live_exit_price"].notna() & (df["exit_price"] != 0)
    df.loc[mask, "exit_slippage_pct"] = ((df.loc[mask, "live_exit_price"] - df.loc[mask, "exit_price"]) / df.loc[mask, "exit_price"]) * 100.0

    df["hold_time_sec"] = df["closed_at"] - df["opened_at"]
    df["hour_utc"] = pd.to_datetime(df["opened_at"], unit="s", errors="coerce").dt.hour

    return df


def summarize(days: int = 5, db_path: Path = DB_PATH) -> Dict[str, Any]:
    df = load_closed_trades(days=days, db_path=db_path)
    if df.empty:
        return {"ok": False, "message": "No closed trades found for requested window.", "days": days}

    wins = df["realized_pnl_usd"] > 0
    summary = {
        "ok": True,
        "days": days,
        "trades": int(len(df)),
        "total_pnl_usd": float(df["realized_pnl_usd"].sum(skipna=True)),
        "avg_pnl_usd": float(df["realized_pnl_usd"].mean(skipna=True)),
        "median_pnl_usd": float(df["realized_pnl_usd"].median(skipna=True)),
        "win_rate_pct": float(wins.mean() * 100.0),
        "avg_roi_pct": float(df["roi_pct"].mean(skipna=True)),
        "median_roi_pct": float(df["roi_pct"].median(skipna=True)),
        "avg_hold_sec": float(df["hold_time_sec"].mean(skipna=True)),
        "avg_entry_slippage_pct": _safe_float(df["entry_slippage_pct"].mean(skipna=True)),
        "avg_exit_slippage_pct": _safe_float(df["exit_slippage_pct"].mean(skipna=True)),
        "avg_total_fee_usd": float(df["total_fee_usd"].mean(skipna=True)),
        "total_fees_usd": float(df["total_fee_usd"].sum(skipna=True)),
    }
    summary["suggested_position_size_usd"] = suggest_position_size(df)
    return summary


def _safe_float(x: Any) -> Optional[float]:
    try:
        if pd.isna(x):
            return None
        return float(x)
    except Exception:
        return None


def hourly_map(days: int = 5, db_path: Path = DB_PATH) -> pd.DataFrame:
    df = load_closed_trades(days=days, db_path=db_path)
    if df.empty or "hour_utc" not in df:
        return pd.DataFrame()

    out = (
        df.dropna(subset=["hour_utc"])
        .groupby("hour_utc", dropna=True)
        .agg(
            trades=("realized_pnl_usd", "count"),
            total_pnl_usd=("realized_pnl_usd", "sum"),
            avg_pnl_usd=("realized_pnl_usd", "mean"),
            median_pnl_usd=("realized_pnl_usd", "median"),
            win_rate_pct=("realized_pnl_usd", lambda s: float((s > 0).mean() * 100.0)),
            avg_roi_pct=("roi_pct", "mean"),
            avg_entry_slippage_pct=("entry_slippage_pct", "mean"),
            avg_exit_slippage_pct=("exit_slippage_pct", "mean"),
            avg_fee_usd=("total_fee_usd", "mean"),
        )
        .reset_index()
        .sort_values(["avg_roi_pct", "total_pnl_usd"], ascending=False)
    )
    out["hour_utc"] = out["hour_utc"].astype(int)
    out["hour_melbourne"] = (out["hour_utc"] + 10) % 24  # AEST baseline; adjust manually during AEDT if needed.
    return out


def suggest_position_size(df: pd.DataFrame) -> float:
    """
    Conservative position-size suggestion:
    - Needs enough observations
    - Starts from median observed position size
    - Reduces if slippage/fee drag is ugly or win rate weak
    """
    if df.empty:
        return 10.0

    current_median = float(df["position_size_usd"].median(skipna=True) or 10.0)
    win_rate = float((df["realized_pnl_usd"] > 0).mean())
    avg_roi = float(df["roi_pct"].mean(skipna=True) or 0.0)

    entry_slip = df["entry_slippage_pct"].dropna()
    exit_slip = df["exit_slippage_pct"].dropna()
    avg_abs_slip = 0.0
    if not entry_slip.empty:
        avg_abs_slip += abs(float(entry_slip.mean()))
    if not exit_slip.empty:
        avg_abs_slip += abs(float(exit_slip.mean()))

    # Default guardrails while sample is small.
    suggested = current_median

    if len(df) < 30:
        suggested = min(suggested, 15.0)
    elif win_rate >= 0.45 and avg_roi > 5 and avg_abs_slip < 8:
        suggested = min(max(current_median * 1.25, 10.0), 25.0)
    elif win_rate < 0.35 or avg_roi < 0 or avg_abs_slip > 12:
        suggested = max(min(current_median * 0.65, 10.0), 5.0)

    return round(float(suggested), 2)


def print_report(days: int = 5) -> None:
    summary = summarize(days)
    print("\n=== SENTINUITY PNL / SLIPPAGE REPORT ===")
    for k, v in summary.items():
        print(f"{k}: {v}")

    print("\n=== BEST CLUSTER HOURS ===")
    hm = hourly_map(days)
    if hm.empty:
        print("No hourly data.")
    else:
        print(hm.head(10).to_string(index=False))


if __name__ == "__main__":
    print_report(days=5)
