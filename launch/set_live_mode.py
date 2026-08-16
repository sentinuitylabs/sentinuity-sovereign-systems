"""Canonical DUAL/live request compatibility entrypoint.

Historically this script independently rewrote the live latch after the final
safety pins. That inverted launch authority. It now delegates to the one final
posture writer and performs no direct DB mutation.
"""
from __future__ import annotations
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PIN = ROOT / "tools" / "apply_launch_safety_pins.py"

def main() -> int:
    size = sys.argv[1] if len(sys.argv) > 1 else "3"
    if not PIN.exists():
        print(f"[FAIL] missing canonical posture writer: {PIN}")
        return 2
    rc = subprocess.call([sys.executable, str(PIN), "--requested", "dual", "--size", str(size)], cwd=str(ROOT))
    if rc == 0:
        print("[OK] DUAL request resolved by canonical final posture authority")
    return rc

if __name__ == "__main__":
    raise SystemExit(main())
