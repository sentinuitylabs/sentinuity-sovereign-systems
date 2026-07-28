
# SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
try:
    from birdeye_quota_guard import install_birdeye_requests_guard as _install_birdeye_guard
    _install_birdeye_guard()
except Exception:
    pass
# /SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
from pathlib import Path
import os
import re
import sqlite3
import sys
import time

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE / "sentinuity_matrix.db").exists() else HERE.parent
DB = ROOT / "sentinuity_matrix.db"
ENV = ROOT / ".env"

DUAL_CONFIG = {
    # Main mode flags
    "TRADING_MODE": "live",
    "LIVE_MONEY_MODE": "1",
    "PAPER_TRADING_ENABLED": "1",
    "LIVE_TRADING_ENABLED": "1",

    # Live is armed, but still gated by Mode B + wallet keys.
    "SMART_WALLET_LIVE_ENABLED": "1",
    "WALLET_COPY_TRADE_ENABLED": "1",
    "RUNNER_LIVE_SCALE_ENABLED": "0",

    # Slots
    "PAPER_MAX_OPEN_POSITIONS": "3",
    "LIVE_MAX_OPEN_POSITIONS": "1",
    "MAX_OPEN_POSITIONS": "3",
    "EXECUTOR_MAX_OPEN_POSITIONS": "3",

    # Sizing
    "POSITION_SIZE_USD": "20.0",
    "BASE_POSITION_SIZE_USD": "20.0",
    "MAX_PAPER_POSITION_USD": "30",

    # Separate calibres
    "CONFIDENCE_FLOOR": "0.50",
    "PAPER_CONFIDENCE_FLOOR": "0.50",
    "SUPERVISOR_MIN_MINT_CONFIDENCE": "0.50",
    "SUPERVISOR_FRESHNESS_FLOOR": "0.20",

    # Live Mode B calibre
    "MODE_B_CONF_FLOOR": "0.80",
    "LIVE_CONFIDENCE_FLOOR": "0.80",

    # Exceptional fire armed
    "EXCEPTIONAL_FIRE_ENABLED": "1",
    "EXCEPTIONAL_FIRE_MODE": "1",
}

def env_has(key: str) -> bool:
    if not ENV.exists():
        return False
    text = ENV.read_text(encoding="utf-8", errors="replace")
    m = re.search(rf"^\s*{re.escape(key)}\s*=\s*(.+?)\s*$", text, re.M)
    if not m:
        return False
    val = m.group(1).strip().strip('"').strip("'")
    return bool(val)

def main() -> int:
    print("=" * 72)
    print("SENTINUITY DUAL MODE LAUNCH CONFIG (LIVE + PAPER)")
    print("=" * 72)
    print("ROOT:", ROOT)
    print("DB:", DB)

    if not DB.exists():
        print("[FAIL] sentinuity_matrix.db missing")
        return 1

    backup = ROOT / f"sentinuity_matrix.backup_before_dual_mode_launch_{int(time.time())}.db"
    try:
        import shutil
        shutil.copy2(DB, backup)
        print("[OK] backup:", backup.name)
    except Exception as e:
        print("[WARN] backup failed:", e)

    con = sqlite3.connect(str(DB))
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY, value TEXT, description TEXT)")
    for k, v in DUAL_CONFIG.items():
        cur.execute(
            "INSERT OR REPLACE INTO system_config (key,value,description) VALUES (?,?,?)",
            (k, v, "dual mode live+paper launch enforced"),
        )
    con.commit()

    wallet = env_has("SOLANA_WALLET_ADDRESS")
    private_key = env_has("PRIVATE_KEY")
    helius = env_has("HELIUS_API_KEY")
    birdeye = env_has("BIRDEYE_API_KEY")

    print("\n[CONFIG SET]")
    for k in sorted(DUAL_CONFIG):
        print(f"{k:<36} {DUAL_CONFIG[k]}")

    print("\n[ENV LIVE KEY CHECK - no secrets printed]")
    print("SOLANA_WALLET_ADDRESS:", "PRESENT" if wallet else "MISSING")
    print("PRIVATE_KEY:", "PRESENT" if private_key else "MISSING")
    print("HELIUS_API_KEY:", "PRESENT" if helius else "MISSING")
    print("BIRDEYE_API_KEY:", "PRESENT" if birdeye else "MISSING")

    if wallet and private_key:
        cur.execute("INSERT OR REPLACE INTO system_config (key,value,description) VALUES (?,?,?)", ("LIVE_WALLET_KEYS_READY", "1", "dual mode live+paper launch check"))
        print("\n[LIVE ARM] Wallet keys present. Live can fire only if Mode B passes.")
    else:
        cur.execute("INSERT OR REPLACE INTO system_config (key,value,description) VALUES (?,?,?)", ("LIVE_WALLET_KEYS_READY", "0", "dual mode live+paper launch check"))
        print("\n[LIVE ARM] Dual mode armed, but real live fire cannot pass until wallet keys exist.")

    con.commit()
    con.close()

    print("\nDUAL MODE CONFIG COMPLETE")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
