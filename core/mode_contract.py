"""
SENTINUITY — OPERATOR MODE CONTRACT (single authority)
======================================================
SOVEREIGNTY_MODE_CONTRACT_20260804

Purpose
-------
One place that answers: "what did the operator actually select, and is the
runtime coherent with that selection?"

This module deliberately does NOT arm anything. It resolves, validates and
reports. Arming remains the job of the launcher's config writer. The reason
for that split is the root cause this module exists to prevent: Sentinuity
currently has three separate writers that each claim to configure DUAL and
disagree with each other (launch/launch_config.py, launch/arm_dual_mode.py,
launch/dual_mode_launch_config.py). Adding a fourth writer would make the
problem worse. What was missing was an ARBITER.

Doctrine implemented here
-------------------------
  1. PAPER  -> paper_enabled=True,  live_enabled=False
  2. LIVE   -> paper_enabled=False, live_enabled=True
  3. DUAL   -> paper_enabled=True,  live_enabled=True
  4. DUAL must never silently resolve to paper-only.
  5. The explicit launcher selection for THIS process session is authoritative.
  6. A contradiction between the selection and the live flags is a hard
     configuration error, not a downgrade.

Precedence (highest first)
--------------------------
  1. SENTINUITY_OPERATOR_SELECTION   (process env, stamped by the launcher)
  2. system_config.OPERATOR_SELECTION (durable, last launcher write)
  3. Inference from the legacy five-flag latch (compat only)

Rationale for env-over-DB: the DB is written by several tools and by long-lived
services; the process environment is stamped once, by the launcher, for this
session. The operator's most recent explicit act must win for that session.

Refusal classes are separated so that a strategy veto can never impersonate a
capital or transaction-integrity failure.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

# ── Canonical vocabulary ──────────────────────────────────────────────────────

PAPER = "PAPER"
LIVE = "LIVE"
DUAL = "DUAL"
VALID_MODES = (PAPER, LIVE, DUAL)

MIRROR_NORMAL = "NORMAL"
MIRROR_ALL = "ALL_PAPER_ADMISSIONS"
CALIBRATION_PROFILE = "DUAL_CALIBRATION"
VALID_MIRROR_POLICIES = (MIRROR_NORMAL, MIRROR_ALL)

# Refusal domains. A refusal MUST carry exactly one of these.
GATE_STRATEGY = "STRATEGY_GATE"
GATE_CAPITAL = "CAPITAL_LIMIT"
GATE_TXN = "TRANSACTION_INTEGRITY"
GATE_DATA = "DATA_AVAILABILITY"
GATE_MODE = "OPERATOR_MODE"
VALID_GATE_DOMAINS = (GATE_STRATEGY, GATE_CAPITAL, GATE_TXN, GATE_DATA, GATE_MODE)

# The legacy conjunctive latch read by execution_engine._live_lane_armed().
LIVE_LATCH_KEYS = (
    "DUAL_MODE_ENABLED",
    "DUAL_MODE_ARMED",
    "LIVE_TRADING_ENABLED",
    "LIVE_MODE_B_ENABLED",
    "LIVE_ARMED",
)

ENV_SELECTION = "SENTINUITY_OPERATOR_SELECTION"
ENV_MIRROR = "SENTINUITY_LIVE_MIRROR_POLICY"

_TRUE = {"1", "true", "yes", "on"}


class ModeContractError(RuntimeError):
    """Raised when the runtime contradicts the operator's explicit selection."""


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in _TRUE


@dataclass
class ModeContract:
    operator_selection: str
    paper_enabled: bool
    live_enabled: bool
    live_execution_armed: bool
    live_mirror_policy: str
    selection_source: str
    latch: Dict[str, bool] = field(default_factory=dict)
    contradictions: List[str] = field(default_factory=list)

    @property
    def coherent(self) -> bool:
        return not self.contradictions

    def required_latch(self) -> Dict[str, str]:
        """The latch values the selection demands. Used by the launcher writer."""
        on = "1" if self.live_enabled else "0"
        return {k: on for k in LIVE_LATCH_KEYS}

    def banner(self, *, position_size: Any = "?", max_open: Any = "?",
               max_exposure: Any = "?", daily_loss_cap: Any = "?",
               wallet: Any = "?", rpc: Any = "?", canary: Any = "?") -> str:
        """The one authoritative startup contract print."""
        def yn(b: bool) -> str:
            return "ACTIVE" if b else "INACTIVE"
        lines = [
            "=" * 66,
            "SENTINUITY OPERATOR MODE CONTRACT",
            "=" * 66,
            f"  OPERATOR_SELECTION    {self.operator_selection}  (source: {self.selection_source})",
            f"  PAPER_ENABLED         {yn(self.paper_enabled)}",
            f"  LIVE_ENABLED          {yn(self.live_enabled)}",
            f"  LIVE_EXECUTION_ARMED  {yn(self.live_execution_armed)}",
            f"  LIVE_MIRROR_POLICY    {self.live_mirror_policy}",
            f"  POSITION_SIZE         {position_size}",
            f"  MAX_OPEN_POSITIONS    {max_open}",
            f"  MAX_TOTAL_EXPOSURE    {max_exposure}",
            f"  DAILY_LOSS_CAP        {daily_loss_cap}",
            f"  WALLET                {wallet}",
            f"  RPC                   {rpc}",
            f"  CANARY/BUILD STATE    {canary}",
            "-" * 66,
        ]
        if self.contradictions:
            lines.append("  *** CONFIGURATION CONTRADICTION — LAUNCH REFUSED ***")
            for c in self.contradictions:
                lines.append(f"    - {c}")
        else:
            lines.append("  CONTRACT COHERENT")
        lines.append("=" * 66)
        return "\n".join(lines)


def _read_selection(cfg_get: Callable[[str, Any], Any],
                    env: Dict[str, str]) -> tuple[Optional[str], str]:
    raw = (env.get(ENV_SELECTION) or "").strip().upper()
    if raw in VALID_MODES:
        return raw, "process_env"
    raw = str(cfg_get("OPERATOR_SELECTION", "") or "").strip().upper()
    if raw in VALID_MODES:
        return raw, "system_config"
    return None, "unset"


def _read_mirror(cfg_get: Callable[[str, Any], Any],
                 env: Dict[str, str]) -> str:
    raw = (env.get(ENV_MIRROR) or "").strip().upper()
    if raw in VALID_MIRROR_POLICIES:
        return raw
    raw = str(cfg_get("LIVE_MIRROR_POLICY", "") or "").strip().upper()
    if raw in VALID_MIRROR_POLICIES:
        return raw
    # Fail safe: mirror-all is never the default and never sticky.
    return MIRROR_NORMAL


def resolve(cfg_get: Callable[[str, Any], Any],
            env: Optional[Dict[str, str]] = None) -> ModeContract:
    """Resolve the contract. Never raises; records contradictions instead.

    cfg_get(key, default) is the durable config reader (schema.get_config_value).
    """
    env = dict(os.environ) if env is None else env

    latch = {k: _truthy(cfg_get(k, "0")) for k in LIVE_LATCH_KEYS}
    latch_all = all(latch.values())

    selection, source = _read_selection(cfg_get, env)
    if selection is None:
        # Compat inference for a runtime launched before this contract existed.
        selection = DUAL if latch_all else PAPER
        source = "inferred_from_latch"

    paper_enabled = selection in (PAPER, DUAL)
    live_enabled = selection in (LIVE, DUAL)
    mirror = _read_mirror(cfg_get, env)

    contradictions: List[str] = []

    if live_enabled and not latch_all:
        missing = [k for k, v in latch.items() if not v]
        contradictions.append(
            f"{selection} selected but live latch incomplete; "
            f"disarmed keys: {', '.join(missing)}"
        )
    if (not live_enabled) and latch_all:
        contradictions.append(
            f"{selection} selected but the full live latch is armed; "
            "a stale live arming survived a paper selection"
        )
    if selection == PAPER and _truthy(cfg_get("LIVE_MONEY_MODE", "0")):
        contradictions.append("PAPER selected but LIVE_MONEY_MODE=1")
    if mirror == MIRROR_ALL and not live_enabled:
        contradictions.append(
            "LIVE_MIRROR_POLICY=ALL_PAPER_ADMISSIONS requires a live-enabled mode"
        )

    if live_enabled:
        # Capital limits must be explicit and positive before live is coherent.
        for key in ("LIVE_POSITION_SIZE_USD", "LIVE_MAX_TOTAL_EXPOSURE_USD"):
            try:
                if float(cfg_get(key, 0.0) or 0.0) <= 0.0:
                    contradictions.append(f"{selection} selected but {key} is not positive")
            except (TypeError, ValueError):
                contradictions.append(f"{selection} selected but {key} is not numeric")

    return ModeContract(
        operator_selection=selection,
        paper_enabled=paper_enabled,
        live_enabled=live_enabled,
        live_execution_armed=bool(live_enabled and latch_all and not contradictions),
        live_mirror_policy=mirror,
        selection_source=source,
        latch=latch,
        contradictions=contradictions,
    )


def enforce(contract: ModeContract) -> ModeContract:
    """Fail closed and loudly. DUAL never silently degrades to PAPER."""
    if not contract.coherent:
        raise ModeContractError(
            "Operator mode contract violated:\n  - "
            + "\n  - ".join(contract.contradictions)
        )
    return contract


# ── Strategy-gate arbitration for mirror-all ──────────────────────────────────

def calibration_profile_active(contract: ModeContract) -> bool:
    """True only for the explicit mirror-all calibration profile."""
    return contract.live_enabled and contract.live_mirror_policy == MIRROR_ALL


def strategy_gates_are_advisory(contract: ModeContract) -> bool:
    """True when the operator explicitly bought out of strategy selectivity.

    Applies ONLY to strategy vetoes (pattern arming, council votes, confidence
    calibration, Mode B selectivity). It must never be consulted by capital,
    transaction-integrity or data-availability checks.
    """
    return contract.live_enabled and contract.live_mirror_policy == MIRROR_ALL


def classify_refusal(domain: str, reason: str) -> Dict[str, str]:
    """Every live refusal carries exactly one domain. Guards against a strategy
    veto being reported as a capital or transaction failure."""
    if domain not in VALID_GATE_DOMAINS:
        raise ValueError(f"unknown refusal domain: {domain}")
    return {"refusal_domain": domain, "refusal_reason": str(reason)[:200]}
