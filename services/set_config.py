"""
set_config.py — update system_config values
Usage: python set_config.py sentinuity_matrix.db KEY VALUE
   or: python set_config.py sentinuity_matrix.db  (applies latency audit fixes)
"""
import sqlite3, sys, time

DB = sys.argv[1] if len(sys.argv) > 1 else "sentinuity_matrix.db"

def set_val(c, key, val, reason=""):
    old = c.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
    old_val = old[0] if old else "(not set)"
    c.execute("INSERT OR REPLACE INTO system_config (key,value) VALUES (?,?)", (key, str(val)))
    print(f"  {key}: {old_val} → {val}  {reason}")

if len(sys.argv) == 4:
    # Single key set
    c = sqlite3.connect(DB)
    set_val(c, sys.argv[2], sys.argv[3])
    c.commit(); c.close()
else:
    # Apply latency audit fixes
    print(f"\n  LATENCY AUDIT CONFIG FIXES — {DB}")
    c = sqlite3.connect(DB)
    set_val(c, "EXECUTOR_MAX_PRICE_AGE_SEC", "600",
            "# was 300s — 31% stale executions at P95=201s")
    set_val(c, "EXECUTOR_MAX_SIGNAL_AGE_SEC", "900",
            "# was 600s — prevents entering dead signals")
    c.commit(); c.close()
    print("\n  ✅ Done. Restart execution_engine to pick up.\n")
