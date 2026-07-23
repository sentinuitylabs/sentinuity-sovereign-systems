"""Public Community Edition: live arming is intentionally unavailable."""
from __future__ import annotations


def main() -> int:
    print("[BLOCKED] Public Community Edition does not include real-money execution.")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
