"""Compatibility entrypoint for the canonical launch posture writer.

This file no longer writes a contradictory paper/live mixture. It delegates to
``tools/apply_launch_safety_pins.py`` which is the final atomic posture authority.
Real submission remains fail-closed unless both the private marker and the
session acknowledgement are present.
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
    return subprocess.call([sys.executable, str(PIN), "--requested", "dual", "--size", str(size)], cwd=str(ROOT))

if __name__ == "__main__":
    raise SystemExit(main())
