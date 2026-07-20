
import sqlite3, time
from pathlib import Path

DB = Path(__file__).resolve().parent.parent / "sentinuity_matrix.db"

safe = {
    "TRADING_MODE": "paper",
    "PAPER_TRADING_ENABLED": "1",
    "LIVE_TRADING_ENABLED": "0",
    "LIVE_ARMED": "0",
    "LIVE_MONEY_MODE": "0",
    "EXECUTION_ARMED": "0",
    "LIVE_MAX_OPEN_POSITIONS": "0",
    "PAPER_MAX_OPEN_POSITIONS": "3",
    "PAPER_POSITION_SIZE_USD": "25",
    "POSITION_SIZE_USD": "25",
    "PAPER_CONFIDENCE_FLOOR": "0.65",
    "SUPERVISOR_ADMISSION_MIN_CONF": "0.65",
    "SUPERVISOR_MIN_MINT_CONF": "0.65",
    "CONVICTION_GATE_ENABLED": "0",
    "ROUTE_MODEL_REQUIRED": "0",
    "SUPERVISOR_REQUIRE_CONVICTION": "0",
    "SUPERVISOR_REQUIRE_CALIBRATED_CONFIDENCE": "0",
    "PAPER_REQUIRE_CALIBRATED_CONFIDENCE": "0",
    "REQUIRE_POSITIVE_CONVICTION": "0",
    "MOMENTUM_GATE_ENABLED": "0",
    "SUPERVISOR_REQUIRE_POSITIVE_MTM": "0",
    "LIVE_PAPER_SHADOW_ON_BLOCK": "1",
}

con = sqlite3.connect(DB)
cur = con.cursor()

tabs = {r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")}
if "system_config" not in tabs:
    cur.execute("CREATE TABLE system_config (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)")

cols = {r[1] for r in cur.execute("PRAGMA table_info(system_config)")}
kc = "key" if "key" in cols else ("name" if "name" in cols else "key")
vc = "value" if "value" in cols else ("val" if "val" in cols else "value")

cols = {r[1] for r in cur.execute("PRAGMA table_info(system_config)")}
if "updated_at" not in cols:
    cur.execute("ALTER TABLE system_config ADD COLUMN updated_at REAL")

for k, v in safe.items():
    cur.execute(f"SELECT COUNT(*) FROM system_config WHERE {kc}=?", (k,))
    if cur.fetchone()[0]:
        cur.execute(f"UPDATE system_config SET {vc}=?, updated_at=? WHERE {kc}=?", (v, time.time(), k))
    else:
        cur.execute(f"INSERT INTO system_config ({kc},{vc},updated_at) VALUES (?,?,?)", (k, v, time.time()))
    print(f"[PAPER_SAFE] {k}={v}")

con.commit()
print("[PAPER_SAFE] integrity_check =", cur.execute("PRAGMA integrity_check").fetchone()[0])
con.close()
