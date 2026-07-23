from __future__ import annotations
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parent
required = [
    ROOT / "services/live_trading.py",
    ROOT / "services/live_wallet_sync.py",
    ROOT / "services/set_live_mode.py",
    ROOT / "launch/set_live_mode.py",
    ROOT / "launch/arm_dual_mode.py",
    ROOT / "launch/dual_mode_launch_config.py",
    ROOT / "launch/launch_config.py",
]
errors = []
for path in required:
    if not path.exists():
        errors.append(f"missing: {path.relative_to(ROOT)}")
        continue
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except Exception as exc:
        errors.append(f"syntax: {path.relative_to(ROOT)}: {exc}")

lt = (ROOT / "services/live_trading.py").read_text(encoding="utf-8", errors="ignore") if (ROOT / "services/live_trading.py").exists() else ""
for forbidden in ("sendTransaction", "swapTransaction", "Keypair.from", "SOLANA_PRIVATE_KEY"):
    if forbidden in lt:
        errors.append(f"live stub contains forbidden implementation marker: {forbidden}")
for marker in ("PUBLIC_LIVE_EXECUTION_AVAILABLE = False", "def execute_live_buy", "def execute_live_sell", "def is_live_mode"):
    if marker not in lt:
        errors.append(f"live stub missing marker: {marker}")

cfg = (ROOT / "launch/launch_config.py").read_text(encoding="utf-8", errors="ignore") if (ROOT / "launch/launch_config.py").exists() else ""
for marker in ('"LIVE_TRADING_ENABLED": "0"', '"LIVE_MONEY_MODE": "0"', '"EXECUTION_ARMED": "0"'):
    if marker not in cfg:
        errors.append(f"paper clamp missing: {marker}")

if errors:
    print("PUBLIC PAPER-ONLY VERIFICATION: FAIL")
    for error in errors:
        print(" -", error)
    raise SystemExit(1)
print("PUBLIC PAPER-ONLY VERIFICATION: PASS")
print("Real-money signing/submission is absent from the public execution boundary.")
