#!/usr/bin/env python3
"""DISARM_EXECUTION_FLAG.py — zeroes the vestigial EXECUTION_ARMED flag.
Nothing in the merged codebase reads this flag (verified: only writer is
wallet_separation_guard, which sets it to 0 in paper mode). Zeroing it
cannot block paper opens; it clears the audit FAIL.
Run from repo root:   python launch\\DISARM_EXECUTION_FLAG.py"""
import sqlite3, os, sys

# locate the DB whether run from root or from inside launch/
for p in ("sentinuity_matrix.db", os.path.join("..", "sentinuity_matrix.db")):
    if os.path.exists(p):
        DB = p; break
else:
    print("sentinuity_matrix.db not found — run from the trading-bot root"); sys.exit(1)

c = sqlite3.connect(DB)
before = c.execute("SELECT value FROM system_config WHERE key='EXECUTION_ARMED'").fetchone()
c.execute("UPDATE system_config SET value='0' WHERE key='EXECUTION_ARMED'")
c.commit()
after = c.execute("SELECT value FROM system_config WHERE key='EXECUTION_ARMED'").fetchone()
c.close()
print(f"EXECUTION_ARMED: {before[0] if before else '(absent)'} -> {after[0] if after else '(absent)'}")
print("Done. Re-run the audit — this FAIL clears.")
