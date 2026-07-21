from __future__ import annotations
from pathlib import Path
import py_compile
import re
import sys

ROOT = Path(__file__).resolve().parent
replay = (ROOT / "launch" / "replay_nto_case.py").read_text(encoding="utf-8")
canary = (ROOT / "launch" / "live_canary_fixtures.py").read_text(encoding="utf-8")

checks = {
    "replay token balances carry accountIndex": replay.count('"accountIndex": wi') == 2,
    "canary token balances carry accountIndex": canary.count('"accountIndex": wallet_combined_index') == 2,
    "replay uses wallet_account_indexes": 'wallet_account_indexes' in replay and '["wallet_index"]' not in replay,
    "canary uses wallet_account_indexes": 'wallet_account_indexes' in canary and '.get("wallet_index")' not in canary,
    "legacy native_delta_ex_fee alias removed": "native_delta_ex_fee" not in replay and "native_delta_ex_fee" not in canary,
}

for path in [
    ROOT / "launch" / "replay_nto_case.py",
    ROOT / "launch" / "live_canary_fixtures.py",
]:
    py_compile.compile(str(path), doraise=True)

failed = 0
for name, ok in checks.items():
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    failed += not ok

sys.exit(1 if failed else 0)
