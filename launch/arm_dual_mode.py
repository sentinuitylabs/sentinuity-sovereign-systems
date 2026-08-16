"""Compatibility entrypoint for explicit DUAL selection.

No partial flags are written here. The canonical safety-pin writer either
produces one coherent profile or refuses. It cannot silently claim live is
armed while only two of the five live latch flags are set.
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
