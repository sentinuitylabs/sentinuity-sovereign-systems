"""Sentinuity idempotent boot seed — paper intelligence only.

Restores the standing-task contract and explicitly arms the bounded copytrade
paper bonus. Live remains observe-only because copytrade_influence enforces a
hard zero bonus whenever TRADING_MODE=live.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "sentinuity_matrix.db"

def _set_paper_copytrade_config() -> None:
    if not DB.exists():
        print("[FORGE] matrix DB not present yet; config deferred")
        return
    con = sqlite3.connect(str(DB), timeout=5)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT)")
        con.execute("INSERT INTO system_config(key,value) VALUES('COPYTRADE_PAPER_BONUS_ENABLED','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        con.execute("INSERT INTO system_config(key,value) VALUES('SWTI_ENABLED','1') ON CONFLICT(key) DO UPDATE SET value='1'")
        settings = {
            'GMGN_WALLET_REFRESH_ENABLED': '1',
            'COPYTRADE_PAPER_BONUS_ENABLED': '1',
            'COPYTRADE_PAPER_BONUS_MAX': '0.03',
            'COPYTRADE_SIGNAL_MAX_AGE_SEC': '600',
            'COPYTRADE_SHADOW_SCANNER_ENABLED': '1',
            'COPYTRADE_MAX_PER_CYCLE': '5',
            'SWTI_ENABLED': '1',
        }
        for key, value in settings.items():
            con.execute(
                "INSERT INTO system_config(key,value) VALUES(?,?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (key, value),
            )
        con.commit()
        print("[FORGE] smart-wallet paper lane armed; live influence remains hard-disabled")
    finally:
        con.close()

def main() -> None:
    _set_paper_copytrade_config()
    try:
        from core.standing_tasklist_contract import sync
        sync(quiet=True)
        print("[FORGE] standing tasklist synced")
    except Exception as exc:
        print(f"[FORGE] standing task sync deferred: {exc}")
    try:
        from services.copytrade_influence import ensure_influence_ledger
        ensure_influence_ledger()
        print("[FORGE] copytrade influence ledger ready")
    except Exception as exc:
        print(f"[FORGE] influence ledger deferred: {exc}")

if __name__ == "__main__":
    main()
