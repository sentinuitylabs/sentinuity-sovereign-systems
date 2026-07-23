from __future__ import annotations
import sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"

PAPER_SETTINGS = {
    "TRADING_MODE": "paper",
    "PAPER_TRADING_ENABLED": "1",
    "DUAL_MODE_ENABLED": "0",
    "DUAL_MODE_ARMED": "0",
    "LIVE_TRADING_ENABLED": "0",
    "LIVE_MODE_B_ENABLED": "0",
    "LIVE_ARMED": "0",
    "LIVE_MONEY_MODE": "0",
    "EXECUTION_ARMED": "0",
    "LIVE_MAX_OPEN_POSITIONS": "0",
    "PUBLIC_LIVE_EXECUTION_AVAILABLE": "0",
}


def _upsert(cur, key: str, value: str, description: str) -> None:
    cols = {r[1] for r in cur.execute("PRAGMA table_info(system_config)")}
    if "description" in cols and "updated_at" in cols:
        cur.execute("INSERT INTO system_config(key,value,description,updated_at) VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description,updated_at=excluded.updated_at", (key, value, description, time.time()))
    elif "description" in cols:
        cur.execute("INSERT INTO system_config(key,value,description) VALUES(?,?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description", (key, value, description))
    else:
        cur.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def main() -> int:
    requested = (sys.argv[1] if len(sys.argv) > 1 else "paper").strip().lower()
    size = str(float(sys.argv[2] if len(sys.argv) > 2 else "25"))
    max_pos = str(int(float(sys.argv[3] if len(sys.argv) > 3 else "3")))
    conf = str(float(sys.argv[4] if len(sys.argv) > 4 else "0.65"))
    if requested != "paper":
        print(f"[PUBLIC BUILD] Requested mode '{requested}' was clamped to paper.")
    if not DB.exists():
        print("[FAIL] DB missing:", DB)
        return 3
    con = sqlite3.connect(DB, timeout=20)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS system_config (key TEXT PRIMARY KEY,value TEXT,description TEXT,updated_at REAL)")
    settings = dict(PAPER_SETTINGS)
    settings.update({"PAPER_MAX_OPEN_POSITIONS": max_pos, "PAPER_POSITION_SIZE_USD": size, "PAPER_TRADE_SIZE_USD": size, "POSITION_SIZE_USD": size, "PAPER_CONFIDENCE_FLOOR": conf, "PAPER_SUPERVISOR_CONF_FLOOR": conf})
    for key, value in settings.items():
        _upsert(cur, key, value, "public community paper-only clamp")
    con.commit()
    qc = cur.execute("PRAGMA quick_check").fetchone()[0]
    con.close()
    print(f"[OK] Public Community Edition configured paper-only size={size} max_pos={max_pos} conf={conf}")
    print("[OK] quick_check=", qc)
    return 0 if qc == "ok" else 4


if __name__ == "__main__":
    raise SystemExit(main())
