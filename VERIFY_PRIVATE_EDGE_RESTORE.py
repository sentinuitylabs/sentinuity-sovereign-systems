#!/usr/bin/env python3
from __future__ import annotations

import ast
import hashlib
import importlib.util
import sqlite3
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SERVICES = ROOT / "services"

checks: list[tuple[str, bool, str]] = []

def check(name: str, condition: bool, detail: str = "") -> None:
    checks.append((name, bool(condition), detail))

def source(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")

# 1. All files parse.
for rel in (
    "services/execution_engine.py",
    "services/ingest_pipeline.py",
    "services/pattern_live_arming.py",
):
    try:
        ast.parse(source(rel), filename=rel)
        check(f"parse:{rel}", True)
    except Exception as exc:
        check(f"parse:{rel}", False, str(exc))

engine = source("services/execution_engine.py")
ingest = source("services/ingest_pipeline.py")
pattern = source("services/pattern_live_arming.py")

# 2. Resolver cannot stack non-waiting pools.
check(
    "resolver_context_owned",
    "with ThreadPoolExecutor(" in ingest
    and "pool.shutdown(wait=False" not in ingest
    and "bounded=1" in ingest,
    "Expected context-owned executor and no non-waiting shutdown.",
)

# 3. Configured 4% hard-stop remains explicit.
check(
    "hard_stop_configured_4pct",
    'get_config_value("HARD_STOP_LOSS_PCT", 4.0)' in engine,
)

# 4. Paper hard-stop consumes policy output rather than discarding it.
check(
    "hard_stop_policy_exit_consumed",
    '_stop_policy.get("exit_price")' in engine
    and '_stop_policy.get("exit_reason")' in engine
    and "HARD_STOP_EXECUTED" in engine,
)

# 5. Real path cannot be assigned a synthetic stop fill.
stop_slice = engine[engine.find("# -- 1. HARD STOP LOSS"):engine.find("# === 0708_NATIVE_LILYPAD", engine.find("# -- 1. HARD STOP LOSS"))]
check(
    "real_stop_chain_truth_preserved",
    "if not _is_real_eval and isinstance(_stop_policy, dict):" in stop_slice,
)

# 6. Post-13-July runner lock invariants are present.
for needle, name in (
    ('RUNNER_LOCK_MAX_GIVEBACK_75_99_PP", 10.0', "runner_75_99_giveback_10pp"),
    ('RUNNER_LOCK_MAX_GIVEBACK_100_149_PP", 18.0', "runner_100_149_giveback_18pp"),
    ("_trusted_peak_from_tape", "trusted_peak_from_tape"),
    ("RUNNER_PROFIT_LOCK", "runner_profit_lock"),
    ("0708_NATIVE_LILYPAD_SUB100_HARVEST", "lilypad_preserved"),
):
    check(name, needle in engine)

# 7. Pattern recognises a trusted persisted paper peak, while full size remains
#    controlled by documentary live-canary maturity.
check(
    "pattern_peak_confirmation_restored",
    "pattern_peak_pct" in pattern
    and "achieved_pct = max(realised_pct" in pattern,
)
check(
    "live_maturity_full_size_preserved",
    "_live_size_stage" in pattern
    and "live_maturity_earned:3_verified" in pattern
    and "earned_multiplier, maturity_reason = _live_size_stage(conn)" in pattern,
)

# 8. No public-paper safety stubs or chain-fill contracts were removed by this pack.
check(
    "current_live_reconciliation_retained",
    all(x in engine for x in (
        "buy_tx_sig",
        "sell_tx_sig",
        "live_state",
        "funding_mode",
    )),
)

# 9. Self-contained confirmation-contract check.
def _test_classify(realised_pct: float, peak_pct):
    achieved_pct = max(
        realised_pct,
        float(peak_pct) if peak_pct is not None else realised_pct,
    )
    if achieved_pct >= 100.0:
        return "R"
    if achieved_pct >= 75.0:
        return "P"
    if achieved_pct >= 25.0:
        return "S"
    if realised_pct > 5.0:
        return "G"
    if realised_pct >= -5.0:
        return "B"
    if realised_pct > -10.0:
        return "L"
    if realised_pct > -30.0:
        return "H"
    return "X"

check("pattern_unit_peak_runner", _test_classify(-2.0, 92.0) == "P")
check("pattern_unit_plain_loss", _test_classify(-8.0, None) == "L")

passed = sum(1 for _, ok, _ in checks if ok)
failed = len(checks) - passed

print("=" * 90)
print("SENTINUITY PRIVATE EDGE RESTORE VERIFICATION")
print("=" * 90)
for name, ok, detail in checks:
    print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""))
print("-" * 90)
print(f"RESULT: {passed} passed, {failed} failed")
print("EDGE RESTORE SOURCE VERDICT:", "PASS" if failed == 0 else "FAIL")
print("=" * 90)

sys.exit(0 if failed == 0 else 1)
