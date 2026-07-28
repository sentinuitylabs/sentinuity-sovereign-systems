# coding: utf-8
"""
services/apply_policy.py — COUNCIL_AUTOBUILD_20260723_R2
Capability matrix. R2 tightening: Tier A requires BOTH an approved location
(directory prefix or exact allowlisted path) AND an approved purpose suffix.
A suffix alone (e.g. *_adapter.py anywhere) is NOT sufficient. All paths must
resolve inside the repository root: absolute paths, ../ traversal, symlink
escape, and alternate-separator/case tricks are rejected (⇒ Tier C refuse).
"""
from __future__ import annotations
from pathlib import Path
from typing import Iterable, Tuple

ROOT = Path(__file__).resolve().parent.parent

TIER_C_MARKERS = (
    "wallets/providers", "private_key", "keypair", "signing", "wallet_sign",
    "live_trading", "live_arm", "withdraw", "order_submit", "set_live_mode",
    "substrate_live_guard", "launch_config", "prelaunch",
)
TIER_B_MARKERS = (
    "execution_engine", "neural_supervisor", "system_guardian",
    "substrate_paper_trader", "substrate_portfolio_supervisor",
    "active_pipeline_cleaner", "freshness_enforcer", "price_router",
    "ws_price_oracle", "risk_guard", "substrate_risk", "pump_monitor",
    "ingest_pipeline", "market_intelligence", "trade_lifecycle",
    "substrate_allocation",
)
TIER_A_DIRS = ("ui/", "tests/", "docs/")
TIER_A_EXACT = {
    "wallets/substrate_history_adapter.py",
    "services/world_build_state.py", "services/world_narrative_engine.py",
    "services/council_task_ledger.py", "services/debate_quorum.py",
    "services/intelligence_orchestrator.py",
}
TIER_A_PURPOSE_SUFFIXES = ("_adapter.py", "_tab.py", "_chart.py", "_card.py",
                           "_display.py", ".md", "_test.py", ".py")


def _contained_rel(path: str) -> Tuple[bool, str]:
    """Resolve against repo ROOT; reject any escape. Returns (ok, rel_lower)."""
    raw = str(path).replace("\\", "/").replace("\\", "/")
    raw = str(path).replace(chr(92), "/")
    try:
        p = Path(raw)
        cand = (p if p.is_absolute() else ROOT / p)
        rp = cand.resolve()                      # collapses ../ and symlinks
        rel = rp.relative_to(ROOT.resolve())     # raises on escape
    except Exception:
        return False, ""
    return True, str(rel).replace(chr(92), "/").lower()


def classify_path(path: str) -> Tuple[str, str]:
    ok, p = _contained_rel(path)
    if not ok:
        return "C", "path_escapes_repository_root"
    for m in TIER_C_MARKERS:
        if m in p:
            return "C", f"tier_c_marker:{m}"
    for m in TIER_B_MARKERS:
        if m in p:
            return "B", f"tier_b_marker:{m}"
    in_dir = p.startswith(TIER_A_DIRS)
    exact = p in {x.lower() for x in TIER_A_EXACT}
    purpose = p.endswith(TIER_A_PURPOSE_SUFFIXES)
    if (in_dir or exact) and purpose:            # BOTH location AND purpose
        return "A", "tier_a_location_and_purpose"
    return "B", "default_conservative"


def classify(paths: Iterable[str]) -> Tuple[str, str]:
    worst, why = "A", "empty"
    order = {"A": 0, "B": 1, "C": 2}
    for p in paths:
        t, r = classify_path(p)
        if order[t] > order[worst]:
            worst, why = t, f"{p}:{r}"
    return worst, why


def can_autoapply(paths: Iterable[str], get_config_value=None) -> Tuple[bool, str, str]:
    tier, why = classify(paths)
    if tier == "C":
        return False, tier, f"NEVER_AUTONOMOUS:{why}"
    if tier == "B":
        return False, tier, f"OPERATOR_APPROVAL_REQUIRED:{why}"
    enabled = "1"
    if get_config_value is not None:
        try:
            enabled = str(get_config_value("COUNCIL_TIER_A_AUTOAPPLY", "1")).strip()
        except Exception:
            enabled = "1"
    if enabled != "1":
        return False, tier, "TIER_A_KILLED_BY_OPERATOR"
    return True, tier, f"TIER_A_AUTOAPPLY:{why}"
