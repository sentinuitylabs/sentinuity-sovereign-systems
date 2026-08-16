#!/usr/bin/env python3
"""Quarantined historical promotion bridge.

The former implementation copied mint-identity confidence into trading
confidence and refreshed qualification timestamps. That behavior is disabled.
This module intentionally performs no database writes and exits non-zero if
manually invoked, preventing accidental restoration of manufactured admission.
"""
from __future__ import annotations

DISABLED_REASON = (
    "promotion_signoff_bridge_0707 is quarantined: mint confidence is identity "
    "evidence, not trading conviction; qualification timestamps are immutable"
)

def main() -> int:
    print(f"[DISABLED] {DISABLED_REASON}")
    return 2

if __name__ == "__main__":
    raise SystemExit(main())
