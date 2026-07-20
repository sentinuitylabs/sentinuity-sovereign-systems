"""
core/mutation_enums.py

Single source of truth for all status, type, and action enums
used across the Sovereign Mutation Engine.

Import from here — never hardcode these strings elsewhere.
"""

PATTERN_STATUSES = {"emerging", "validated", "contradicted", "retired"}

PATTERN_TYPES = {
    "channel_quality", "entry_timing", "exit_behavior",
    "liquidity_regime", "confidence_calibration", "token_class",
    "source_reliability", "market_regime", "failure_signature",
}

PROPOSAL_STATUSES = {
    "drafted", "under_debate", "debate_rejected",
    "replay_pending", "replay_failed", "replay_passed",
    "shadow_pending", "shadow_failed", "shadow_passed",
    "awaiting_human", "approved", "applied", "rolled_back", "retired",
}

PROPOSAL_TYPES = {
    "parameter", "scoring_logic", "entry_logic", "exit_logic",
    "supervisor_gate", "qualifier_logic", "risk_control",
    "cleanup_logic", "telemetry_only",
}

REPLAY_TYPES = {
    "parameter_replay", "logic_replay",
    "exit_replay", "full_pipeline_replay",
}

REPLAY_STATUSES = {"queued", "running", "completed", "failed"}

REPLAY_VERDICTS = {"pending", "pass", "conditional_pass", "fail"}

ACTION_TYPES = {"buy", "sell", "hold", "skip", "no_action"}

# ── NON-NEGOTIABLE CONSTANTS ───────────────────────────────────────────────────
# These values MUST NEVER be changed by any mutation proposal.
# Any proposal touching these constants must be auto-rejected.
#
# SIGN-OFF FIX 9: Exit-path thresholds are now banned from Polaris mutation.
# Previously only confidence ceiling/floor values were protected. All live-config
# keys read inside evaluate_exit_for_position() (trail, time-cut, max-hold,
# stale-kill) were freely mutable mid-position via apply_proposal_to_config(),
# allowing Polaris to silently disable trailing-stop or extend max-hold on live
# positions. None → banned unconditionally (the check is `key in BANNED_MUTATIONS`,
# not a value comparison, so None works correctly as a sentinel).
BANNED_MUTATIONS = {
    # Confidence gates — original protections
    "CONFIDENCE_CEILING":              0.87,   # supervisor gate 2 ceiling
    "RESOLVER_HARD_CAP":               0.89,   # resolver confidence cap
    "CONFIDENCE_FLOOR_MIN":            0.65,   # floor must never drop below this
    # Exit-path thresholds — mutating these mid-position can silently disable SL/TP
    "TRAIL_ACTIVATE_PCT":              None,   # trailing stop activation threshold
    "TRAIL_STOP_PCT":                  None,   # trailing stop drawdown floor
    "TIME_CUT_SECONDS":                None,   # time-based loss cut
    "STALE_WINNER_CUT_SECONDS":        None,   # stagnant winner cut
    "EXECUTOR_MAX_HOLD_SECONDS":       None,   # absolute max hold time
    "STALE_PRICE_FORCE_CLOSE_SECONDS": None,   # oracle-dark force-close threshold
    "ZOMBIE_PRICE_STALE_SECONDS":      None,   # zombie detection threshold
}

BANNED_FILES = {
    "core/schema.py",          # schema is sacred — no mutation
    "core/mutation_enums.py",  # enums are sacred — no mutation
    "patch_services.py",       # patcher is sacred — no mutation
}

BANNED_FUNCTIONS = {
    "init_db", "startup_cleanup", "get_connection",
    "_get_conn", "run_emergency_heal",
}

# ── VALIDATION HELPERS ─────────────────────────────────────────────────────────
def validate_pattern(p: dict) -> list[str]:
    errors = []
    if p.get("supporting_sample_size", 0) < 5:
        errors.append("supporting_sample_size must be >= 5")
    if not 0.0 <= p.get("confidence_score", -1) <= 1.0:
        errors.append("confidence_score must be 0.0-1.0")
    if p.get("status") not in PATTERN_STATUSES:
        errors.append(f"status must be one of {PATTERN_STATUSES}")
    if not p.get("pattern_key"):
        errors.append("pattern_key required")
    if not p.get("evidence_json"):
        errors.append("evidence_json required")
    return errors

def validate_proposal(p: dict) -> list[str]:
    errors = []
    if not p.get("linked_pattern_ids_json"):
        errors.append("Must link at least one learned pattern")
    if not p.get("unified_diff"):
        errors.append("unified_diff required")
    target_files = p.get("target_files_json", [])
    if isinstance(target_files, str):
        import json
        target_files = json.loads(target_files)
    for f in target_files:
        if f in BANNED_FILES:
            errors.append(f"Cannot mutate banned file: {f}")
    if p.get("proposal_type") != "telemetry_only":
        if not p.get("replay_policy_json"):
            errors.append("replay_policy_json required for non-telemetry proposals")
    return errors
