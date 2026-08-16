from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

WINDOW_SECONDS = {
    "2h": 2 * 3600,
    "6h": 6 * 3600,
    "24h": 24 * 3600,
    "72h": 72 * 3600,
    "7d": 7 * 24 * 3600,
    "30d": 30 * 24 * 3600,
    "All": None,
}


def fetch_windowed_trade_metrics(
    db_path: str,
    window: str = "72h",
) -> dict[str, Any]:
    if window not in WINDOW_SECONDS:
        window = "72h"

    db = sqlite3.connect(db_path, timeout=10)
    db.row_factory = sqlite3.Row

    cutoff_seconds = WINDOW_SECONDS[window]
    params: list[Any] = []

    where = "status='CLOSED'"

    if cutoff_seconds is not None:
        where += """
        AND (
            CASE
                WHEN typeof(closed_at) IN ('integer','real') THEN
                    CASE
                        WHEN CAST(closed_at AS REAL) > 1000000000000
                            THEN CAST(closed_at AS REAL) / 1000.0
                        ELSE CAST(closed_at AS REAL)
                    END
                ELSE CAST(strftime('%s', closed_at) AS REAL)
            END
        ) >= ?
        """
        params.append(time.time() - cutoff_seconds)

    row = db.execute(
        f"""
        SELECT
            COUNT(*) AS closed,
            SUM(CASE WHEN realized_pnl_usd > 0 THEN 1 ELSE 0 END) AS wins,
            SUM(CASE WHEN realized_pnl_usd < 0 THEN 1 ELSE 0 END) AS losses,
            SUM(CASE WHEN realized_pnl_usd = 0 OR realized_pnl_usd IS NULL
                     THEN 1 ELSE 0 END) AS scratches,
            SUM(COALESCE(realized_pnl_usd, 0)) AS net_pnl,
            AVG(COALESCE(realized_pnl_usd, 0)) AS avg_pnl,
            SUM(CASE WHEN realized_pnl_usd > 0
                     THEN realized_pnl_usd ELSE 0 END) AS gross_profit,
            ABS(SUM(CASE WHEN realized_pnl_usd < 0
                         THEN realized_pnl_usd ELSE 0 END)) AS gross_loss
        FROM paper_positions
        WHERE {where}
        """,
        params,
    ).fetchone()

    closed = int(row["closed"] or 0)
    wins = int(row["wins"] or 0)
    losses = int(row["losses"] or 0)
    scratches = int(row["scratches"] or 0)
    gross_profit = float(row["gross_profit"] or 0)
    gross_loss = float(row["gross_loss"] or 0)

    db.close()

    return {
        "window": window,
        "closed": closed,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "win_rate": wins / closed * 100.0 if closed else 0.0,
        "net_pnl": float(row["net_pnl"] or 0),
        "avg_pnl": float(row["avg_pnl"] or 0),
        "gross_profit": gross_profit,
        "gross_loss": gross_loss,
        "profit_factor": (
            gross_profit / gross_loss
            if gross_loss > 0
            else (999.0 if gross_profit > 0 else 0.0)
        ),
    }


def fetch_window_comparison(db_path: str) -> list[dict[str, Any]]:
    return [
        fetch_windowed_trade_metrics(db_path, label)
        for label in ("24h", "72h", "7d", "All")
    ]
