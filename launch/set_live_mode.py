from __future__ import annotations
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"


def _read(cur, key: str) -> str | None:
    row = cur.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
    return str(row[0]).strip() if row and row[0] is not None else None


def main() -> int:
    con = sqlite3.connect(DB, timeout=20)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT)")

    # Optional CLI amount; otherwise preserve the amount chosen in the launcher.
    raw = sys.argv[1] if len(sys.argv) > 1 else (
        _read(cur, "OPERATOR_LIVE_POSITION_SIZE_USD")
        or _read(cur, "LIVE_POSITION_SIZE_USD")
        or _read(cur, "LIVE_TRADE_AMOUNT_USD")
    )
    try:
        size = float(raw) if raw is not None else 0.0
    except (TypeError, ValueError):
        size = 0.0
    if size <= 0:
        print("[BLOCKED] No positive operator live size is stored. Use Launch_Sentinuity.bat and enter the amount.")
        con.close()
        return 2
    size_s = format(size, ".8g")

    pairs = {
        "TRADING_MODE":"paper", "PAPER_TRADING_ENABLED":"1",
        "DUAL_MODE_ENABLED":"1", "DUAL_MODE_ARMED":"1",
        "LIVE_TRADING_ENABLED":"1", "LIVE_MODE_B_ENABLED":"1", "LIVE_ARMED":"1",
        "LIVE_POSITION_SIZE_USD":size_s, "LIVE_TRADE_AMOUNT_USD":size_s,
        "LIVE_MAX_POSITION_USD":size_s, "MAX_LIVE_POSITION_USD":size_s,
        "LIVE_MAX_TOTAL_EXPOSURE_USD":size_s, "LIVE_MAX_OPEN_POSITIONS":"1",
        "LIVE_DAILY_LOSS_LIMIT_USD":size_s, "LIVE_CONSECUTIVE_LOSS_LIMIT":"1",
        "LIVE_REENTRY_COOLDOWN_SECONDS":"900", "LIVE_MAX_PRICE_AGE_SEC":"30",
        "MAX_ROUND_TRIP_SLIPPAGE_PCT":"8", "LIVE_PAPER_SHADOW_ON_BLOCK":"1",
        "OPERATOR_LIVE_POSITION_SIZE_USD":size_s,
    }
    for k,v in pairs.items():
        cur.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(k,v))
    con.commit()
    for k in sorted(pairs):
        print(f"{k:36} {pairs[k]}")
    con.close()
    print(f"TRUE DUAL ARMED: paper primary + independent ${size_s} Mode B live mirror")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
