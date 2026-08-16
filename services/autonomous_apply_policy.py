# coding: utf-8
"""Compatibility facade over services.golden_latch_gate. DENY BY DEFAULT."""
from __future__ import annotations
from dataclasses import dataclass, asdict
from services.golden_latch_gate import classify_path, TIER_A, TIER_B, TIER_C

@dataclass
class ApplyDecision:
    allowed: bool
    requires_human: bool
    reason: str
    risk_level: str
    category: str
    backup_required: bool = True
    compile_required: bool = True
    audit_required: bool = True
    def to_dict(self):
        return asdict(self)

def can_autonomous_apply(target_file: str, patch_type: str = "",
                         task_type: str = "") -> ApplyDecision:
    tier, why = classify_path(target_file, patch_type=patch_type)
    if tier == TIER_A:
        return ApplyDecision(True, False, f"Tier A: {why}", "safe", "tier_a")
    if tier == TIER_B:
        return ApplyDecision(False, True, f"Tier B: {why}", "review", "tier_b")
    return ApplyDecision(False, True, f"Tier C: {why}", "money", "tier_c")
