#!/usr/bin/env python3
"""VERIFY_LAUNCH_READY.py — READ ONLY. Corrected launch-readiness check that
looks at the REAL launcher location (launch/Launch_Sentinuity.bat), not the
repo root. Replaces the two path-mismatched checks in AUDIT_FINAL_MERGE_CONTRACT.
Run from repo root:   python launch\\VERIFY_LAUNCH_READY.py"""
import os, sqlite3, sys
from pathlib import Path

ok, bad = [], []
def check(cond, good, fail):
    (ok if cond else bad).append(good if cond else fail)

# locate root
# FIX (20260728): root was inferred from database presence in the CURRENT
# WORKING DIRECTORY -- `root = "." if os.path.exists("sentinuity_matrix.db")
# else ".."`. With no database, root flipped to ".." and the launcher was
# searched for ONE LEVEL ABOVE the repo, producing
# "launcher NOT FOUND in launch/ or root" even though the correctly-cased file
# was present. That coupled two unrelated checks to one heuristic and made the
# verifier report a phantom launch blocker.
#
# Root is now anchored to THIS FILE's location, which is stable regardless of
# the working directory. Launcher lookup is also case-insensitive so the same
# check is valid on Windows, Linux and macOS.
root = str(Path(__file__).resolve().parent.parent)
bat = None
_WANT = "launch_sentinuity.bat"
for _dir in (os.path.join(root, "launch"), root):
    if not os.path.isdir(_dir):
        continue
    # exact match first (fast path), then case-insensitive scan
    _exact = os.path.join(_dir, "Launch_Sentinuity.bat")
    if os.path.exists(_exact):
        bat = _exact
        break
    _hit = next((f for f in os.listdir(_dir) if f.lower() == _WANT), None)
    if _hit:
        bat = os.path.join(_dir, _hit)
        break

check(bat is not None, f"launcher found: {bat}", "launcher NOT FOUND in launch/ or root")
if bat:
    txt = open(bat, encoding="utf-8", errors="ignore").read()
    check("wallet_name=?" in txt,
          f"paper-balance restore fix present in {bat}",
          f"paper-balance fix MISSING in {bat}")

db = os.path.join(root, "sentinuity_matrix.db")
if os.path.exists(db):
    c = sqlite3.connect(db)
    def _safe(fn, default="0"):
        try:
            return fn()
        except Exception:
            return default
    g = lambda k: _safe(lambda: (lambda r: r[0] if r else "0")(c.execute(
        "SELECT value FROM system_config WHERE key=?", (k,)).fetchone()))
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
