#!/usr/bin/env python3
"""VERIFY_LAUNCH_READY.py — READ ONLY. Corrected launch-readiness check that
looks at the REAL launcher location (launch/Launch_Sentinuity.bat), not the
repo root. Replaces the two path-mismatched checks in AUDIT_FINAL_MERGE_CONTRACT.
Run from repo root:   python launch\\VERIFY_LAUNCH_READY.py"""
import os, sqlite3, sys

ok, bad = [], []
def check(cond, good, fail):
    (ok if cond else bad).append(good if cond else fail)

# locate root
root = "." if os.path.exists("sentinuity_matrix.db") else ".."
bat = None
for p in (os.path.join(root, "launch", "Launch_Sentinuity.bat"),
          os.path.join(root, "Launch_Sentinuity.bat")):
    if os.path.exists(p): bat = p; break

check(bat is not None, f"launcher found: {bat}", "launcher NOT FOUND in launch/ or root")
if bat:
    txt = open(bat, encoding="utf-8", errors="ignore").read()
    check("wallet_name=?" in txt,
          f"paper-balance restore fix present in {bat}",
          f"paper-balance fix MISSING in {bat}")

db = os.path.join(root, "sentinuity_matrix.db")
if os.path.exists(db):
    c = sqlite3.connect(db)
    g = lambda k: (lambda r: r[0] if r else "0")(c.execute(
        "SELECT value FROM system_config WHERE key=?", (k,)).fetchone())
    check(str(g("TRADING_MODE")).lower() == "paper", "TRADING_MODE=paper", f"TRADING_MODE={g('TRADING_MODE')}")
    for k in ("LIVE_TRADING_ENABLED","LIVE_ARMED","EXECUTION_ARMED","LIVE_MONEY_MODE"):
        v = str(g(k))
        check(v in ("0","","None"), f"{k}={v or '0'}", f"{k}={v} — run launch\\DISARM_EXECUTION_FLAG.py" if k=="EXECUTION_ARMED" else f"{k}={v} (LIVE FLAG)")
    c.close()
else:
    bad.append("sentinuity_matrix.db not found (run from repo root)")

print("="*60); print("  LAUNCH READINESS"); print("="*60)
for m in ok:  print("  [PASS]", m)
for m in bad: print("  [FAIL]", m)
print("-"*60)
print("  VERDICT:", "READY — launch with .\\launch\\Launch_Sentinuity.bat (option 1 = paper)" if not bad else "NOT READY — fix FAILs above")
