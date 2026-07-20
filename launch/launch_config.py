from __future__ import annotations
import sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"

def upsert(cur, key, value, description):
    cols = {r[1] for r in cur.execute("PRAGMA table_info(system_config)")}
    if "updated_at" in cols and "description" in cols:
        cur.execute(
            "INSERT INTO system_config(key,value,description,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description,updated_at=excluded.updated_at",
            (key, value, description, time.time()),
        )
    elif "description" in cols:
        cur.execute(
            "INSERT INTO system_config(key,value,description) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description",
            (key, value, description),
        )
    else:
        cur.execute(
            "INSERT INTO system_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )

def main():
    mode = (sys.argv[1] if len(sys.argv) > 1 else "paper").strip().lower()
    size = str(float(sys.argv[2] if len(sys.argv) > 2 else "25"))
    max_pos = str(int(float(sys.argv[3] if len(sys.argv) > 3 else "3")))
    conf = str(float(sys.argv[4] if len(sys.argv) > 4 else ("0.80" if mode == "live" else "0.65")))
    exceptional = "1" if str(sys.argv[5] if len(sys.argv) > 5 else "1").lower() in {"1","true","yes","on"} else "0"

    if mode not in {"paper","live","dual"}:
        print("[FAIL] unsupported mode:", mode)
        return 2
    if not DB.exists():
        print("[FAIL] DB missing:", DB)
        return 3

    con = sqlite3.connect(DB, timeout=20)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,description TEXT,updated_at REAL)")

    if mode in {"live","dual"}:
        settings = {
            "TRADING_MODE":"paper","DUAL_MODE_ENABLED":"1","DUAL_MODE_ARMED":"1",
            "PAPER_TRADING_ENABLED":"1","LIVE_TRADING_ENABLED":"1",
            "LIVE_MODE_B_ENABLED":"1","LIVE_ARMED":"1","LIVE_MONEY_MODE":"1","EXECUTION_ARMED":"1",
            "PAPER_MAX_OPEN_POSITIONS":max_pos,"LIVE_MAX_OPEN_POSITIONS":"1",
            "LIVE_POSITION_SIZE_USD":size,"LIVE_TRADE_AMOUNT_USD":size,
            "LIVE_MAX_POSITION_USD":size,"MAX_LIVE_POSITION_USD":size,
            "LIVE_MAX_TOTAL_EXPOSURE_USD":size,"LIVE_DAILY_LOSS_LIMIT_USD":size,
            "LIVE_CONSECUTIVE_LOSS_LIMIT":"1","LIVE_REENTRY_COOLDOWN_SECONDS":"900",
            "OPERATOR_LIVE_POSITION_SIZE_USD":size,"MODE_B_CONF_FLOOR":conf,
            "LIVE_CONFIDENCE_FLOOR":conf,"LIVE_MAX_PRICE_AGE_SEC":"30",
            "MAX_ROUND_TRIP_SLIPPAGE_PCT":"8","EXCEPTIONAL_FIRE_ENABLED":exceptional,
            "LATCHED_OVERRIDE_ENABLED":"0","RUNNER_LIVE_ESCALATION_ENABLED":"0",
            "LIVE_PAPER_SHADOW_ON_BLOCK":"1",
            "PATTERN_LIVE_ARMING_MODE":"required",
            "PATTERN_LIVE_ARMING_REQUIRED":"1",
        }
        desc = "operator-confirmed dual mode: paper alongside gated live Mode B"
    else:
        settings = {
            "TRADING_MODE":"paper","DUAL_MODE_ENABLED":"0","DUAL_MODE_ARMED":"0","PAPER_TRADING_ENABLED":"1",
            "LIVE_TRADING_ENABLED":"0","LIVE_MODE_B_ENABLED":"0","LIVE_ARMED":"0","LIVE_MONEY_MODE":"0","EXECUTION_ARMED":"0",
            "PAPER_MAX_OPEN_POSITIONS":max_pos,"LIVE_MAX_OPEN_POSITIONS":"0",
            "PAPER_POSITION_SIZE_USD":size,"PAPER_TRADE_SIZE_USD":size,"POSITION_SIZE_USD":size,
            "PAPER_CONFIDENCE_FLOOR":conf,"PAPER_SUPERVISOR_CONF_FLOOR":conf,
            "EXCEPTIONAL_FIRE_ENABLED":exceptional,"LIVE_PAPER_SHADOW_ON_BLOCK":"1",
        }
        desc = "paper-only launch clamp"

    for k,v in settings.items():
        upsert(cur, k, v, desc)
    con.commit()
    qc = cur.execute("PRAGMA quick_check").fetchone()[0]
    print(f"[OK] launch_config applied mode={mode} size={size} max_pos={max_pos} conf={conf}")
    print("[OK] quick_check=", qc)
    con.close()
    return 0 if qc == "ok" else 4

if __name__ == "__main__":
    raise SystemExit(main())
