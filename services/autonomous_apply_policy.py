# SENTINUITY_AUTONOMOUS_APPLY_POLICY_20260624
# ============================================================================
# ONE central guard that every code-apply path must call before writing a file.
# This is the prerequisite that was missing: multiple apply paths
# (safe_patch_apply, forge_code_writer, sovereign_governor, polaris) each had
# their own ad-hoc allow/deny logic. This module unifies them so "autonomous
# apply" can be enabled for safe non-money files WITHOUT any path being able to
# silently touch live-money code.
#
# DESIGN PRINCIPLE: DENY BY DEFAULT.
#   - A file is allowed ONLY if it matches an explicit safe pattern AND does not
#     match any money pattern. Money patterns always win over safe patterns.
#   - Unknown / unmatched files => requires_human=True. New money code added
#     later is therefore human-gated until someone explicitly classifies it.
#
# This module has NO side effects, NO DB writes, NO file writes. Pure policy.

from __future__ import annotations
import re
from dataclasses import dataclass, asdict
from pathlib import PurePosixPath
from typing import Optional


# ── MONEY / SIGNING DENYLIST — always requires human approval ────────────────
# Matched against the normalised (forward-slash, lowercase) path. If ANY of
# these match, the file is human-only, full stop — even if a safe pattern also
# matches. Substring patterns so a path anywhere in the tree is caught.
_MONEY_PATTERNS = [
    r"execution_engine\.py$",
    r"live_trading\.py$",
    r"live_wallet_sync\.py$",
    r"system_guardian\.py$",          # can close/modify positions
    r"set_live_mode\.py$",
    r"arm_dual_mode\.py$",
    r"kill_live\.py$",
    r"\.env$",
    r"launch_sentinuity\.bat$",
    r"restart_sentinuity\.bat$",
    r"launch_config\.py$",
    r"prelaunch\.py$",
    # generic danger tokens anywhere in the filename
    r"wallet_write",
    r"wallet_sync",
    r"signer",
    r"signing",
    r"keypair",
    r"private_key",
    r"\bswap\b",
    r"send_tx",
    r"order_route",
    r"live_route",
    r"tax_allocator\.py$",            # touches real fund movement accounting
]

# ── SAFE ALLOWLIST — autonomous apply permitted (if no money pattern matches) ─
# Each entry: (regex, category). Order doesn't matter; first match wins for the
# category label.
_SAFE_PATTERNS = [
    (r"tools/audit_[a-z0-9_]+\.py$",        "diagnostics"),
    (r"tools/build_[a-z0-9_]+\.py$",        "diagnostics"),
    (r"find_runners\.py$",                  "diagnostics"),
    (r"runner_deep_dive\.py$",              "diagnostics"),
    (r"audit_[a-z0-9_]+\.py$",              "diagnostics"),
    (r"ui/[a-z0-9_]*panel[a-z0-9_]*\.py$",  "ui"),
    (r"ui/[a-z0-9_]*tab[a-z0-9_]*\.py$",    "intelligence_tab"),
    (r"theme\.py$",                         "css_theme"),
    (r"token_display\.py$",                 "ui"),
    (r"substrate_wallet_panel\.py$",        "substrate_ui"),
    (r"substrate_opportunity_scanner\.py$", "substrate_paper"),
    (r"substrate_paper_trader\.py$",        "substrate_paper"),
    (r"substrate_portfolio_supervisor\.py$","substrate_paper"),
    (r"substrate_copytrade_bridge\.py$",    "copytrade_paper"),
    (r"copytrade_shadow_scanner\.py$",      "copytrade_paper"),
    (r"copytrade_influence\.py$",           "copytrade_paper"),
    (r"copytrade_entry_influence\.py$",     "copytrade_paper"),
    (r"smart_wallet_conviction\.py$",       "copytrade_paper"),
    (r"world_tasks\.py$",                   "world_display"),
    (r"holo_help\.py$",                     "docs"),
    (r"docs?/",                             "docs"),
]

# sovereign_hub.py is special: it's a 500KB file mixing UI rendering with some
# balance reads. We allow autonomous apply to it ONLY for clearly-UI-section
# patches, signalled by patch_type. A whole-file rewrite is never autonomous.
_HUB_FILE = re.compile(r"sovereign_hub\.py$")
_HUB_SAFE_PATCH_TYPES = {
    "ui_section", "css", "market_tide", "feed_display", "meter_bar",
    "substrate_ui", "intelligence_tab", "copytrade_display", "diagnostics_panel",
}


@dataclass
class ApplyDecision:
    allowed: bool
    requires_human: bool
    reason: str
    risk_level: str          # "safe" | "review" | "money"
    category: str
    backup_required: bool = True
    compile_required: bool = True
    audit_required: bool = True

    def to_dict(self):
        return asdict(self)


def _norm(path: str) -> str:
    return PurePosixPath(str(path).replace("\\", "/")).as_posix().lower()


def _matches_money(p: str) -> Optional[str]:
    for pat in _MONEY_PATTERNS:
        if re.search(pat, p):
            return pat
    return None


def can_autonomous_apply(target_file: str,
                         patch_type: str = "",
                         task_type: str = "") -> ApplyDecision:
    """Single decision point. DENY BY DEFAULT.

    Returns an ApplyDecision. allowed=True means an autonomous path may write
    the file (after backup+compile+audit). allowed=False with
    requires_human=True means a human must approve.
    """
    p = _norm(target_file)
    pt = (patch_type or "").strip().lower()

    # 1. Money / signing always wins.
    money_hit = _matches_money(p)
    if money_hit:
        return ApplyDecision(
            allowed=False, requires_human=True,
            reason=f"money/signing file (matched '{money_hit}') — human approval required",
            risk_level="money", category="live_money",
        )

    # 2. sovereign_hub.py — UI sections only, and only for UI-ish patch types.
    if _HUB_FILE.search(p):
        if pt in _HUB_SAFE_PATCH_TYPES:
            return ApplyDecision(
                allowed=True, requires_human=False,
                reason=f"sovereign_hub UI-section patch ({pt})",
                risk_level="review", category="hub_ui",
            )
        return ApplyDecision(
            allowed=False, requires_human=True,
            reason="sovereign_hub.py non-UI patch — human review (balance/render mix)",
            risk_level="review", category="hub_other",
        )

    # 3. Safe allowlist.
    for pat, cat in _SAFE_PATTERNS:
        if re.search(pat, p):
            return ApplyDecision(
                allowed=True, requires_human=False,
                reason=f"safe non-money file (category: {cat})",
                risk_level="safe", category=cat,
            )

    # 4. Default deny — unknown file, treat as human-gated.
    return ApplyDecision(
        allowed=False, requires_human=True,
        reason="unclassified file — default-deny, human approval required",
        risk_level="review", category="unknown",
    )


# Self-test when run directly: prints decisions for a representative set so a
# human can eyeball that the guard does the right thing.
if __name__ == "__main__":
    cases = [
        ("services/execution_engine.py", "", "core"),
        ("services/live_trading.py", "", "core"),
        ("services/live_wallet_sync.py", "", "core"),
        ("services/system_guardian.py", "", "core"),
        (".env", "", ""),
        ("Launch_Sentinuity.bat", "", ""),
        ("services/substrate_paper_trader.py", "", "substrate"),
        ("services/substrate_wallet_panel.py", "", "substrate_ui"),
        ("services/copytrade_influence.py", "", "copytrade"),
        ("tools/audit_substrate_status.py", "", "diagnostics"),
        ("ui/intelligence_tab.py", "", "ui"),
        ("services/sovereign_hub.py", "market_tide", "ui"),
        ("services/sovereign_hub.py", "balance_logic", "core"),
        ("services/some_new_signer.py", "", ""),
        ("services/random_unknown.py", "", ""),
    ]
    print(f"{'FILE':45s} {'patch':14s} -> allowed human  risk     category")
    print("-" * 100)
    for f, pt, tt in cases:
        d = can_autonomous_apply(f, pt, tt)
        print(f"{f:45s} {pt:14s} -> {str(d.allowed):5s}  {str(d.requires_human):5s}  "
              f"{d.risk_level:7s}  {d.category}")
