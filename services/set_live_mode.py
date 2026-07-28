"""Arm LIVE mode and synchronise the canonical Phantom wallet truth safely."""
from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"


def main() -> int:
    with sqlite3.connect(str(DB), timeout=15) as db:
        db.execute("INSERT INTO system_config(key,value) VALUES('TRADING_MODE','live') "
                   "ON CONFLICT(key) DO UPDATE SET value='live'")
        db.execute("INSERT INTO system_config(key,value) VALUES('LIVE_TRADING_ENABLED','1') "
                   "ON CONFLICT(key) DO UPDATE SET value='1'")
        db.commit()
    from services.live_wallet_sync import sync_once
    if not sync_once(force=True):
        print("LIVE mode armed, but Phantom wallet sync failed. Check .env/RPC and do not fund-fire until synced.")
        return 2
    from services.live_wallet_truth import read_live_wallet_truth
    truth = read_live_wallet_truth(DB)
    print(f"TRADING_MODE: live")
    print(f"Live wallet:  ${float(truth['balance_usd']):.2f}")
    print(f"Available:    ${float(truth['available_usd']):.2f}")
    print(f"Address:      {truth['wallet_address']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
