# coding: utf-8
"""
services/golden_latch_gate.py — SENTINUITY_PACK_P0_20260814

Single constitutional territory authority for Council-generated code changes.

P0 contract:
- Build/governance authority and funded/live-money surfaces are Tier C.
- Unknown paths are Tier C.
- Tier A is deliberately narrow and non-funded.
- Config/environment may narrow Tier A autonomous territory, never widen it.
- Classification is performed again immediately before a write.
- Optional candidate-content screening can only escalate risk, never lower it.

This module has no DB writes and no file writes.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Iterable, Optional, Sequence, Tuple

ROOT = Path(__file__).resolve().parent.parent

TIER_A = "A"
TIER_B = "B"
TIER_C = "C"

# Entire build/governance plane is constitutional Tier C. A gate that can
# rewrite its own gate is not a gate.
_TIER_C_EXACT = {
    "services/golden_latch_gate.py",
    "services/apply_policy.py",
    "services/autonomous_apply_policy.py",
    "services/council_autobuilder.py",
    "services/council_task_ledger.py",
    "services/council_handler_registry.py",
    "services/debate_quorum.py",
    "services/debate_engine.py",
    "services/council_execution_spine.py",
    "services/council_build_orchestrator.py",
    "services/forge_code_writer.py",
    "services/council_apply.py",
    "services/safe_patch_apply.py",
    "services/polaris_patch_writer.py",
    "services/operator_approval.py",
    "services/build_retrospective.py",
    "core/schema.py",
}

_TIER_C_PREFIXES = (
    "launch/",
    "wallets/providers/",
)

# Funded authority / safety constitution. Substring matching is intentionally
# conservative and can only push a path upward to Tier C.
_TIER_C_MARKERS = (
    "execution_engine",
    "live_trading",
    "live_wallet_sync",
    "live_lane",
    "wallet_sign",
    "signing",
    "signer",
    "private_key",
    "keypair",
    "live_arm",
    "arm_dual_mode",
    "kill_live",
    "order_submit",
    "order_route",
    "live_route",
    "hard_stop",
    "risk_guard",
    "position_sizing",
    "price_integrity_contract",
    "price_truth_adjudicator",
    "ws_price_oracle",
    "system_guardian",
    "schema.py",
    ".env",
)

# Tier A is intentionally a constitutional LOCATION allowlist. Environment
# variables are never allowed to add to this set.
_TIER_A_PREFIXES = (
    "ui/",
    "tests/",
    "docs/",
)

_TIER_A_TOOL_RE = re.compile(r"^tools/(?:audit|build|inspect|report)_[a-z0-9_]+\.py$")

# Non-funded code that may be built/tested but requires operator approval to
# apply. Unknown does NOT fall here; unknown is Tier C.
_TIER_B_PREFIXES = (
    "services/substrate_",
    "services/intelligence_",
    "services/copytrade_",
    "services/smart_wallet_",
    "services/research_",
)

_DANGEROUS_CONTENT = (
    re.compile(r"\bSOLANA_PRIVATE_KEY\b"),
    re.compile(r"\bprivate[_ ]?key\b", re.I),
    re.compile(r"\bkeypair\b", re.I),
    re.compile(r"\bLIVE_ARMED\b"),
    re.compile(r"\bLIVE_TRADING_ENABLED\b"),
    re.compile(r"\bHARD_STOP_LOSS_PCT\b"),
    re.compile(r"\bsend_transaction\b"),
    re.compile(r"\bsign_transaction\b"),
    re.compile(r"\bsystem_config\b.*\b(?:insert|update|delete)\b", re.I | re.S),
    re.compile(r"\b(?:CREATE|ALTER|DROP)\s+TABLE\b", re.I),
)

def contained_rel(path: str) -> Tuple[bool, str]:
    """Resolve inside repo root. Reject traversal, absolute escape and symlink escape."""
    raw = str(path).replace("\\", "/")
    try:
        p = Path(raw)
        cand = p if p.is_absolute() else ROOT / p
        resolved = cand.resolve()
        rel = resolved.relative_to(ROOT.resolve())
    except Exception:
        return False, ""
    return True, str(rel).replace("\\", "/").lower()

def _content_escalation(content: Optional[str]) -> Optional[str]:
    if not content:
        return None
    for pat in _DANGEROUS_CONTENT:
        if pat.search(content):
            return f"semantic_blast_radius:{pat.pattern}"
    return None

def classify_path(path: str, *, candidate_content: Optional[str] = None,
                  patch_type: str = "") -> Tuple[str, str]:
    ok, rel = contained_rel(path)
    if not ok:
        return TIER_C, "path_escapes_repository_root"

    if rel in _TIER_C_EXACT:
        return TIER_C, f"build_or_constitutional_integrity:{rel}"
    if any(rel.startswith(p) for p in _TIER_C_PREFIXES):
        return TIER_C, f"constitutional_prefix:{rel}"
    if any(marker in rel for marker in _TIER_C_MARKERS):
        return TIER_C, f"constitutional_marker:{rel}"

    escalation = _content_escalation(candidate_content)
    if escalation:
        return TIER_C, escalation

    if rel.startswith(_TIER_A_PREFIXES) or _TIER_A_TOOL_RE.match(rel):
        return TIER_A, "explicit_non_funded_tier_a"

    if rel.startswith(_TIER_B_PREFIXES):
        return TIER_B, "explicit_non_funded_tier_b"

    # P0 rule: unknown is Tier C, never "default B".
    return TIER_C, "unknown_default_tier_c"

def classify(paths: Iterable[str], *,
             candidate_contents: Optional[dict[str, str]] = None) -> Tuple[str, str]:
    order = {TIER_A: 0, TIER_B: 1, TIER_C: 2}
    worst, why = TIER_A, "empty"
    seen = False
    for path in paths:
        seen = True
        content = (candidate_contents or {}).get(str(path))
        tier, reason = classify_path(path, candidate_content=content)
        if order[tier] > order[worst]:
            worst, why = tier, f"{path}:{reason}"
    if not seen:
        return TIER_C, "no_target_paths"
    return worst, why

def _configured_roots(get_config_value=None) -> tuple[str, ...]:
    raw = None
    if get_config_value is not None:
        try:
            raw = get_config_value("COUNCIL_BUILD_ALLOWED_ROOTS", "ui")
        except Exception:
            raw = "ui"
    if raw is None:
        raw = os.getenv("COUNCIL_BUILD_ALLOWED_ROOTS", "ui")
    vals = []
    for item in str(raw or "ui").split(","):
        item = item.strip().replace("\\", "/").strip("/")
        if item:
            vals.append(item.lower() + "/")
    return tuple(vals or ("ui/",))

def config_allows_tier_a(path: str, get_config_value=None) -> Tuple[bool, str]:
    """Config can only NARROW constitutional Tier A."""
    tier, why = classify_path(path)
    if tier != TIER_A:
        return False, f"constitutional_tier_{tier}:{why}"
    ok, rel = contained_rel(path)
    if not ok:
        return False, "path_escapes_repository_root"
    roots = _configured_roots(get_config_value)
    if any(rel.startswith(r) for r in roots):
        return True, f"constitutional_A_intersect_config:{roots}"
    return False, f"config_narrows_out:{roots}"

def can_autoapply(paths: Iterable[str], get_config_value=None,
                  *, candidate_contents: Optional[dict[str, str]] = None
                  ) -> Tuple[bool, str, str]:
    tier, why = classify(paths, candidate_contents=candidate_contents)
    if tier == TIER_C:
        return False, tier, f"NEVER_AUTONOMOUS:{why}"
    if tier == TIER_B:
        return False, tier, f"OPERATOR_APPROVAL_REQUIRED:{why}"

    for p in paths:
        ok, reason = config_allows_tier_a(p, get_config_value)
        if not ok:
            return False, TIER_A, f"TIER_A_NARROWED_BY_CONFIG:{p}:{reason}"

    enabled = "1"
    if get_config_value is not None:
        try:
            enabled = str(get_config_value("COUNCIL_TIER_A_AUTOAPPLY", "1")).strip()
        except Exception:
            enabled = "1"
    if enabled != "1":
        return False, TIER_A, "TIER_A_KILLED_BY_OPERATOR"
    return True, TIER_A, f"TIER_A_AUTOAPPLY:{why}"

def write_authorized(path: str, *, candidate_content: Optional[str] = None,
                     get_config_value=None, operator_approved: bool = False
                     ) -> Tuple[bool, str, str]:
    """Final write-site reclassification. This is the TOCTOU backstop."""
    tier, why = classify_path(path, candidate_content=candidate_content)
    if tier == TIER_C:
        return False, tier, f"WRITE_REFUSED_TIER_C:{why}"
    if tier == TIER_B:
        if operator_approved:
            return True, tier, f"WRITE_OPERATOR_APPROVED_TIER_B:{why}"
        return False, tier, f"WRITE_NEEDS_OPERATOR:{why}"
    ok, narrowing = config_allows_tier_a(path, get_config_value)
    if not ok:
        return False, tier, f"WRITE_REFUSED_CONFIG:{narrowing}"
    return True, tier, f"WRITE_TIER_A:{why}"
