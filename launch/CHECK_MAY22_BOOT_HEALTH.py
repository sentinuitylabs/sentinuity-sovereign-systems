
import sqlite3, time, datetime, os, glob
from pathlib import Path
ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"
LOGS = ROOT / "logs"
SERVICES = [
    "ingest_pipeline", "pump_monitor", "market_intelligence", "neural_supervisor",
    "execution_engine", "ws_price_oracle", "freshness_enforcer", "rolling_eviction"
]
print("="*90)
print("MAY22 BOOT HEALTH CHECK")
print("now:", datetime.datetime.now())
print("root:", ROOT)
print("db:", DB, "exists=", DB.exists())
print("="*90)

def tail(path, n=12):
    try:
        lines = Path(path).read_text(errors="replace").splitlines()
        return "\n".join(lines[-n:]) if lines else "(empty log)"
    except Exception as e:
        return f"(cannot read: {e})"

if DB.exists():
    try:
        conn = sqlite3.connect(str(DB)); conn.row_factory = sqlite3.Row
        tabs = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        print("tables_has:", sorted([t for t in tabs if 'heartbeat' in t or t in ('system_config','market_snapshots','paper_positions')]))
        for ht in ["service_heartbeats", "system_heartbeat"]:
            if ht in tabs:
                cols = {r[1] for r in conn.execute(f"PRAGMA table_info({ht})")}
                service_col = "service_name" if "service_name" in cols else ("service" if "service" in cols else None)
                ts_col = None
                for x in ["last_seen", "last_heartbeat", "last_pulse", "heartbeat_at", "last_success_at"]:
                    if x in cols: ts_col = x; break
                if service_col and ts_col:
                    print(f"\nHEARTBEATS from {ht}.{ts_col}:")
                    for r in conn.execute(f"SELECT {service_col} svc, {ts_col} ts FROM {ht} ORDER BY svc"):
                        ts = r['ts']
                        age = None
                        try: age = round(time.time() - float(ts), 1) if ts is not None else None
                        except Exception: pass
                        print(f"  {r['svc']:<28} ts={ts} age={age}")
        if 'market_snapshots' in tabs:
            try:
                n = conn.execute("SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(updated_at,timestamp,created_at,0) > strftime('%s','now')-600").fetchone()[0]
                print("\nmarket_snapshots updated last 10m:", n)
            except Exception as e: print("market_snapshots count err:", e)
        if 'paper_positions' in tabs:
            try:
                n = conn.execute("SELECT COUNT(*) FROM paper_positions WHERE opened_at > strftime('%s','now')-600").fetchone()[0]
                print("paper_positions opened last 10m:", n)
            except Exception as e: print("paper_positions count err:", e)
        conn.close()
    except Exception as e:
        print("DB health error:", e)

print("\nLOG TAILS:")
for svc in SERVICES:
    p = LOGS / f"{svc}.log"
    print("\n---", p.name, "exists=", p.exists(), "size=", p.stat().st_size if p.exists() else 0)
    print(tail(p, 10))
print("\nDONE.")
