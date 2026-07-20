"""
services/theme.py — COMPATIBILITY SHIM (SIGNOFF_TIER1_DOCTRINE_20260716)

This file previously carried an independently evolving copy of the glassbox
theme doctrine. It had already drifted from ui/theme.py (container radius
14px vs 8px, chip font 0.72rem vs 0.68rem, border treatments) — two doctrine
definitions, no owner. Tier 1 audit finding: NEITHER module was imported by
any active code path, so the drift was silent.

ui/theme.py is now the single canonical doctrine module. This shim exists only
so any historical `from services.theme import ...` keeps working and receives
the CANONICAL definitions. Do not add doctrine here. Do not edit tokens here.
Edit ui/theme.py.
"""
from ui.theme import *          # noqa: F401,F403 — deliberate re-export
from ui.theme import (          # explicit re-exports for tooling/IDE clarity
    inject, health_color, semantic_css, heartbeat_class,
    SENT_HERO, SENT_VALUE, SENT_BODY, SENT_LABEL, SENT_MICRO,
    HEARTBEAT_FRESH_SEC, HEARTBEAT_AGING_SEC,
    GOLD_THRESHOLD, GOLD, SOL_GREEN, SOL_PURPLE, EMBER, BLOOD, CYAN,
    FOREST_DEEP, FOREST_MOSS, VAULT_STEEL, VAULT_EDGE, MIST, STEEL_TXT,
)
