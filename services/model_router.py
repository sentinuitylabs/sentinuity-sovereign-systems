"""
services/model_router.py
========================
SINGLE BRAIN-GATE for Polaris model selection.

Routes every Polaris LLM call to a model tier based on task type and risk:

  DEFAULT (gpt-5.4-nano):
    Routine heartbeat, log summaries, world-feed narration, low-risk
    commentary, classification, UI narrative, non-live diagnostics.

  SIGN-OFF / ESCALATION (gpt-5.4-mini):
    Live trade approval, execution gate reasoning, patch sign-off,
    logic battles, agent stalemate, stale-signal contradiction,
    unexplained PnL anomaly, anything touching execution_engine,
    neural_supervisor, market_intelligence, sovereign_governor,
    prelaunch, periodic_refresh, launch_config, or live config.

  CRITICAL (gpt-5.5):
    Major code review or catastrophic contradiction only.
    Not used continuously.

This module ALSO writes every routing decision to model_router_log
(if available) so operator can audit cost burn and tier patterns.

HARD CONSTRAINTS:
  - Never alters trading thresholds, latch logic, entry rules,
    position size, or live/paper mode.
  - Only decides which model the agent will reason with, and logs why.
  - Fails safe: if config missing, defaults to nano.
"""
from __future__ import annotations

import os
import sys
import time
import hashlib
import logging
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

logger = logging.getLogger("model_router")

# ── DEFAULT MODEL CONFIG ──────────────────────────────────────────────────────
# These can be overridden via system_config table (preferred) or env vars.
DEFAULT_BUDGET_MODEL     = "gpt-5.4-nano"
DEFAULT_SIGNOFF_MODEL    = "gpt-5.4-mini"
DEFAULT_CRITICAL_MODEL   = "gpt-5.5"

# Cost guardrails (per-day USD ceiling per tier — soft warn only)
DEFAULT_DAILY_BUDGET_USD_BUDGET     = 5.0
DEFAULT_DAILY_BUDGET_USD_SIGNOFF    = 25.0
DEFAULT_DAILY_BUDGET_USD_CRITICAL   = 10.0

# Per-minute burst caps
DEFAULT_PER_MIN_BUDGET_CALLS   = 30
DEFAULT_PER_MIN_SIGNOFF_CALLS  = 6
DEFAULT_PER_MIN_CRITICAL_CALLS = 1

# Files where touching them triggers automatic escalation
ESCALATION_FILE_TRIGGERS = {
    "execution_engine",
    "neural_supervisor",
    "market_intelligence",
    "sovereign_governor",
    "prelaunch",
    "periodic_refresh",
    "launch_config",
    "freshness_enforcer",
    "rolling_eviction",
}

# Task types that force escalation regardless of caller
ESCALATION_TASK_TRIGGERS = {
    "live_trade_signoff",
    "exec_approval",
    "patch_signoff",
    "stale_signal_conflict",
    "pnl_anomaly",
    "agent_stalemate",
    "logic_battle",
    "live_config_change",
    "safety_review",
}

# Task types that force critical tier
CRITICAL_TASK_TRIGGERS = {
    "catastrophic_review",
    "execution_engine_patch",
    "supervisor_patch",
    "market_intelligence_patch",
}

# Tasks that must NEVER use mini/critical (decorative only)
BUDGET_ONLY_TASKS = {
    "world_feed",
    "npc_chatter",
    "ui_narrative",
    "log_summary",
    "heartbeat_summary",
    "routine_classification",
    "decorative_commentary",
}


def _get_cfg(key: str, default):
    """Best-effort config read. Falls back to env, then default. Never raises."""
    try:
        from core.schema import get_config_value
        val = get_config_value(key, None)
        if val is not None and str(val).strip() != "":
            return val
    except Exception:
        pass
    return os.environ.get(key, default)


def _request_id(task_type: str, prompt_hint: str = "") -> str:
    seed = f"{task_type}|{prompt_hint[:80]}|{time.time():.3f}"
    return hashlib.md5(seed.encode()).hexdigest()[:12]


def choose_model(
    task_type: str,
    risk_level: str = "low",
    confidence_gap: float = 0.0,
    live_trade: bool = False,
    code_touch: bool = False,
    code_touch_file: Optional[str] = None,
    stalemate: bool = False,
    prompt_hint: str = "",
) -> dict:
    """
    Decide which model tier and exact model ID a Polaris call should use.

    Args:
        task_type: short identifier ("world_feed", "exec_approval", etc).
        risk_level: "low" | "medium" | "high" | "critical".
        confidence_gap: 0.0 to 1.0 — gap between competing agent verdicts.
        live_trade: True if this decision affects live capital.
        code_touch: True if Polaris is about to propose/sign-off a code change.
        code_touch_file: filename (without .py) the code change touches.
        stalemate: True if agent debate is deadlocked.
        prompt_hint: optional short text used to hash request_id.

    Returns:
        {
          "model":      "<model_id>",
          "tier":       "budget" | "signoff" | "critical",
          "reason":     "<short why>",
          "cost_guard": "<budget remaining hint>",
          "request_id": "<12-char hash>",
        }
    """
    task = (task_type or "").strip().lower()
    risk = (risk_level or "low").strip().lower()
    touch_file = (code_touch_file or "").strip().lower().replace(".py", "")

    budget_model   = str(_get_cfg("POLARIS_DEFAULT_MODEL", DEFAULT_BUDGET_MODEL))
    signoff_model  = str(_get_cfg("POLARIS_SIGNOFF_MODEL", DEFAULT_SIGNOFF_MODEL))
    critical_model = str(_get_cfg("POLARIS_CRITICAL_MODEL", DEFAULT_CRITICAL_MODEL))

    # ── DECISION TREE ─────────────────────────────────────────────────────────
    # 1. Budget-only tasks: never escalate, decorative
    if task in BUDGET_ONLY_TASKS:
        return {
            "model": budget_model,
            "tier": "budget",
            "reason": f"budget-only task: {task}",
            "cost_guard": "decorative",
            "request_id": _request_id(task, prompt_hint),
        }

    # 2. Critical triggers force gpt-5.5
    if task in CRITICAL_TASK_TRIGGERS or risk == "critical":
        return {
            "model": critical_model,
            "tier": "critical",
            "reason": f"critical task or risk: task={task} risk={risk}",
            "cost_guard": "rate-limited",
            "request_id": _request_id(task, prompt_hint),
        }

    # 3. Live trade forces sign-off tier
    if live_trade:
        return {
            "model": signoff_model,
            "tier": "signoff",
            "reason": "live_trade=True",
            "cost_guard": "per-trade",
            "request_id": _request_id(task, prompt_hint),
        }

    # 4. Code touching critical files forces sign-off
    if code_touch and touch_file in ESCALATION_FILE_TRIGGERS:
        return {
            "model": signoff_model,
            "tier": "signoff",
            "reason": f"code_touch on critical file: {touch_file}",
            "cost_guard": "per-patch",
            "request_id": _request_id(task, prompt_hint),
        }

    # 5. Task in escalation list
    if task in ESCALATION_TASK_TRIGGERS:
        return {
            "model": signoff_model,
            "tier": "signoff",
            "reason": f"escalation task: {task}",
            "cost_guard": "per-call",
            "request_id": _request_id(task, prompt_hint),
        }

    # 6. Stalemate or wide confidence gap escalates
    if stalemate or confidence_gap >= 0.25:
        return {
            "model": signoff_model,
            "tier": "signoff",
            "reason": f"stalemate={stalemate} conf_gap={confidence_gap:.2f}",
            "cost_guard": "per-call",
            "request_id": _request_id(task, prompt_hint),
        }

    # 7. High risk without other triggers still escalates
    if risk == "high":
        return {
            "model": signoff_model,
            "tier": "signoff",
            "reason": f"risk_level=high task={task}",
            "cost_guard": "per-call",
            "request_id": _request_id(task, prompt_hint),
        }

    # 8. Default: budget
    return {
        "model": budget_model,
        "tier": "budget",
        "reason": f"default routing for task={task} risk={risk}",
        "cost_guard": "unlimited (cheap tier)",
        "request_id": _request_id(task, prompt_hint),
    }


def log_routing_decision(decision: dict, extra: Optional[dict] = None) -> None:
    """Write decision to model_router_log table if it exists. Never raises."""
    try:
        from core.schema import get_connection
        with get_connection() as conn:
            # Best-effort create-table-if-missing — defensive only
            conn.execute("""
                CREATE TABLE IF NOT EXISTS model_router_log (
                    id           INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at   REAL NOT NULL,
                    request_id   TEXT NOT NULL,
                    model        TEXT NOT NULL,
                    tier         TEXT NOT NULL,
                    reason       TEXT,
                    task_type    TEXT,
                    extra_json   TEXT
                )
            """)
            import json as _json
            conn.execute("""
                INSERT INTO model_router_log
                (created_at, request_id, model, tier, reason, task_type, extra_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                time.time(),
                decision.get("request_id", ""),
                decision.get("model", ""),
                decision.get("tier", ""),
                decision.get("reason", ""),
                (extra or {}).get("task_type", ""),
                _json.dumps(extra or {})[:2000],
            ))
            conn.commit()
    except Exception as exc:
        logger.debug("router log write skipped: %s", exc)


def current_model_summary() -> dict:
    """For the UI status strip — what model would a routine call use right now?"""
    d = choose_model("heartbeat_summary", risk_level="low")
    return {
        "active_default":  d["model"],
        "tier":            d["tier"],
        "signoff_model":   str(_get_cfg("POLARIS_SIGNOFF_MODEL", DEFAULT_SIGNOFF_MODEL)),
        "critical_model":  str(_get_cfg("POLARIS_CRITICAL_MODEL", DEFAULT_CRITICAL_MODEL)),
    }


if __name__ == "__main__":
    # Quick self-test
    tests = [
        ("routine_summary",            {"risk_level": "low"}),
        ("world_feed",                 {"risk_level": "low"}),
        ("npc_chatter",                {"risk_level": "low"}),
        ("exec_approval",              {"risk_level": "high", "live_trade": True}),
        ("patch_signoff",              {"risk_level": "high", "code_touch": True,
                                        "code_touch_file": "execution_engine"}),
        ("stale_signal_conflict",      {"risk_level": "high"}),
        ("agent_stalemate",            {"stalemate": True}),
        ("catastrophic_review",        {"risk_level": "critical"}),
        ("decorative_commentary",      {"risk_level": "low"}),
    ]
    print(f"{'TASK':<28} {'TIER':<10} {'MODEL':<18} REASON")
    print("-" * 90)
    for task, kw in tests:
        d = choose_model(task, **kw)
        print(f"{task:<28} {d['tier']:<10} {d['model']:<18} {d['reason']}")
