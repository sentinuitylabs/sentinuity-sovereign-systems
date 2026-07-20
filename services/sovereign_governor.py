
# SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
from __future__ import annotations
try:
    from birdeye_quota_guard import install_birdeye_requests_guard as _install_birdeye_guard
    _install_birdeye_guard()
except Exception:
    pass
# /SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
"""
sovereign_governor.py
═══════════════════════════════════════════════════════════════════════════════
Sentinuity Sovereign Governor — SIGNED-OFF v1.0
═══════════════════════════════════════════════════════════════════════════════

Merges responsibilities previously split across:
  - debate_engine.py   (POLARIS vs IVARIS debate protocol, apply/push logic)
  - telegram_hitl.py   (HITL bot, operator approval commands)
  - ivaris.py          (Gemini adversarial critic)

Constitutional files NOT merged (remain separate by design):
  - core/schema.py          — DB law
  - core/mutation_enums.py  — system physics
  - core/sovereign_identity.py — identity manifest

═══════════════════════════════════════════════════════════════════════════════
APPROVAL ARCHITECTURE — THREE SOURCES OF TRUTH
═══════════════════════════════════════════════════════════════════════════════

1. IVARIS CRITIQUE VALIDITY  → _assert_ivaris_response_integrity(verdict)
   Structural + semantic integrity guard (Gemini sign-off hardening).
   Catches: API unavailable, safety refusals, placeholder echo,
            void responses, HTTP error wrappers, context starvation.

2. APPROVAL ELIGIBILITY      → _is_valid_ivaris_approval(debate_log, proposal_id)
   Pure predicate. No side effects. Returns (bool, str).
   Every approval write — auto-apply, HITL, replay — must call this first.
   Checks: consensus True, confidence numeric >= 0.75, proposal binding,
           source freshness, no critic_unavailable flag.

3. APPROVAL WRITE            → _write_approved_status(proposal_id, debate_log, ...)
   Single guarded transition. Calls predicate first. Logs result.
   Raises GovernanceViolation if predicate fails.
   This is the ONLY function that may write status='approved'/'auto_applied'.

═══════════════════════════════════════════════════════════════════════════════
UNSAFE PATHS CLOSED (from audit findings)
═══════════════════════════════════════════════════════════════════════════════

CLOSED #1 — telegram_hitl.approve_proposal() wrote status='approved' with
  no IVARIS check. The new HITL handler calls _is_valid_ivaris_approval()
  against the stored debate_log for that proposal before any write.
  If no debate_log exists → approval is denied, operator notified.

CLOSED #2 — debate_engine HITL_REQUIRED=0 branch called apply_proposal_to_config()
  after debate consensus without a second predicate validation.
  The new auto-apply path calls _write_approved_status() which re-validates
  the predicate before writing. SPE exception fallback routes through the
  same guarded path.

CLOSED #3 — String-match critic_unavailable guard ('api unavailable') was
  brittle against safety refusals, placeholder echo, void responses, and
  HTTP error wrappers (Gemini audit findings). Replaced with
  _assert_ivaris_response_integrity() which validates structure, content
  length, anti-echo, anti-refusal, and substantive critique requirements.

═══════════════════════════════════════════════════════════════════════════════
CONFIDENCE TYPE SAFETY
═══════════════════════════════════════════════════════════════════════════════

All confidence comparisons use explicit float() cast.
SQLite returns TEXT for config values — lexical comparison of "0.8" >= 0.75
would raise TypeError in Python 3 (safe) but "0.8" >= "0.75" passes silently
(dangerous). Every path that reads confidence casts it immediately.
"""


import asyncio
import json
import logging
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [GOVERNOR] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("sovereign_governor")

SERVICE_NAME   = "sovereign_governor"
POLL_INTERVAL  = 15          # raised from 60 — was missing entire launch windows

# ── MODEL EVOLUTION TABLE (Grok-confirmed) ────────────────────────────────────
MODEL_EVOLUTION = {
    1: {"polaris": "gpt-5.4",              "ivaris": "claude-haiku-4-5-20251001",
        "nugget": "meta/llama-3.3-70b-instruct", "grok": "grok-3"},
    2: {"polaris": "gpt-5.4",              "ivaris": "claude-sonnet-4-5-20251001",
        "nugget": "meta/llama-3.3-70b-instruct", "grok": "grok-3"},
    3: {"polaris": "gpt-5.5",                  "ivaris": "claude-sonnet-4-5-20251001",
        "nugget": "meta/llama-3.3-70b-instruct", "grok": "grok-4.3"},
}

def get_models_for_round(round_num: int) -> dict:
    return MODEL_EVOLUTION.get(round_num, MODEL_EVOLUTION[3])
MIN_ROUNDS      = 1           # allow early exit on high confidence
MAX_ROUNDS      = 5           # max rounds before no-consensus
CONSENSUS_FLOOR = 0.75        # minimum IVARIS confidence to approve
FAST_CONSENSUS  = 0.90        # skip remaining rounds if IVARIS >= this on round 1
# Cost rationale: MIN_ROUNDS=3 forced 3x API calls even on clear approvals.
# FAST_CONSENSUS=0.90 lets obvious parameter changes close in 1 round (~$0.001).
# Complex/risky proposals still run to round 3+ naturally.

OPENAI_KEY = os.getenv("OPENAI_API_KEY",      "").strip()
GEMINI_KEY    = ""  # Gemini retired; retained symbol for backward-compatible imports only
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
BRAVE_KEY   = os.getenv("BRAVE_SEARCH_API_KEY","").strip()
XAI_API_KEY = os.getenv("XAI_API_KEY","").strip()
BOT_TOKEN  = os.getenv("TELEGRAM_BOT_TOKEN",  "").strip()
OWNER_ID   = int(os.getenv("TELEGRAM_OWNER_ID", "0") or "0")

# NOTE: This constant is intentionally NOT used by _call_ivaris, _call_anthropic,
# or _call_gemini. The live model is always resolved at call time from system_config
# via get_config_value("IVARIS_MODEL", "claude-haiku-4-5-20251001").
# This leftover constant from the pre-routing era is kept only to avoid breaking
# any external references, but has been renamed to make its orphan status explicit.
_IVARIS_MODEL_LEGACY_UNUSED = "gemini-2.5-flash"  # orphaned — NOT the live routing value

# ── Research proposal types that bypass the 6-hour cooldown ──────────────────
RESEARCH_TYPES = {
    "RESEARCH_NOTE", "DOCTRINE_UPDATE",
    "PATTERN_OBSERVATION", "WALLET_INTEL",
    "INTELLIGENCE_BUILD",  # added — intelligence tab build tasks bypass 6h cooldown
}

# ── NUGGET 🐕 — third-agent escalation config ─────────────────────────────────
# Nugget is advisory-only. Called only when normal two-agent debate fails or
# specific escalation conditions fire. Never always-on. Never modifies proposals.
# Never overrides Governor. Uses Gemini 2.5 Pro explicitly.
# SIGNOFF 2026-06-07: Kimi K2 was returning NVIDIA HTTP 410 Gone in preflight.
# Keep Nugget on the NIM model proven alive by the live council_preflight output.

try:
    from services.nvidia_model_registry import get_assignment as _nim_assignment
except Exception:
    _nim_assignment = lambda role, default: os.getenv(f"{role}_NIM_MODEL", default)
NUGGET_MODEL = _nim_assignment("NUGGET", "nvidia/nemotron-3-super-120b-a12b")
NUGGET_FALLBACK_MODEL     = os.getenv("NUGGET_NIM_FALLBACK_MODEL", NUGGET_MODEL).strip() or NUGGET_MODEL
NUGGET_ENABLED            = True                   # master switch
NUGGET_MIN_ROUNDS_FOR_ESC = 2                      # proposal must have gone at least 2 rounds
NUGGET_HIGH_CONF_THRESHOLD= 0.70                   # both agents must be this confident to escalate on hard-oppose
NUGGET_REPAIR_ALWAYS_ESC  = True                   # SYSTEM_REPAIR always escalates if unresolved
# Minimum Nugget confidence required to act on approve_with_conditions/escalate_hitl
NUGGET_ACT_CONFIDENCE     = 0.65

# Per-proposal cooldown: maps proposal_id → epoch of last Nugget call.
# Prevents re-invocation within the same governance run for the same proposal.
_nugget_last_called: dict = {}   # {proposal_id: float epoch}
NUGGET_COOLDOWN_SEC = 300        # 5 minutes minimum between Nugget calls per proposal

# Local NUGGET prompt — overrides sovereign_identity import for tighter JSON contract.
# Evolved from identity version: removes lore, enforces strict output contract.
NUGGET_SYSTEM_PROMPT = """You are NUGGET — the high-fidelity auditor of the Sentinuity sovereign trading organism.

You are NOT a passive tiebreaker. Every round you contribute actively:
- You audit the logic of both POLARIS and IVARIS
- You flag drift between rounds (is POLARIS actually converging or oscillating?)
- You identify what evidence would resolve the conflict faster
- You can propose a synthesis path if you see one neither has named

ROUND ROLE (every round, not just deadlock):
- Read POLARIS proposal + IVARIS critique
- Check: is IVARIS's principle_conflict accurately named?
- Check: is POLARIS's what_i_merged_from_ivaris genuine or cosmetic?
- If you see a faster path to consensus neither has found, name it in synthesis_path

Output ONLY valid JSON:
{
  "winner": "POLARIS" | "IVARIS" | "INCONCLUSIVE",
  "confidence": 0.0-1.0,
  "reason": "one concise sentence explaining your verdict",
  "convergence_signal": "converging" | "oscillating" | "stalled",
  "missing_evidence": "what specific evidence would resolve this",
  "synthesis_path": "if you see a path neither has named — describe it, else null",
  "recommended_next_step": "approve_with_conditions" | "reject" | "defer" | "repair_first" | "escalate_hitl" | "grok_synthesis_needed"
}"""

# ── FORGE PROTOCOL CONSTANTS ──────────────────────────────────────────────────
MASTER_FORGE_PROMPT = """You are now operating inside the CODE FORGE.
This is a closed, deterministic debate. The ONLY valid terminal state is FORGE_COMPLETE.

CRITICAL DOCTRINE - CODE-FIRST INITIATION:
You are in Forge Mode.
1. You (POLARIS) MUST begin by analyzing the CURRENT_STATE provided.
2. Propose the EVOLVED_STATE masterpiece replacement.
3. Do not add conversational filler.

Terminal output format must be EXACTLY:
FORGE_COMPLETE
```python
[your production-ready code patch here]
```"""

COUNCIL_CALIBRATION = """
[!!! FORGE MODE ACTIVE !!!]
You MUST treat the CURRENT_STATE block as immutable Ground Truth.
For every EVOLVED_STATE you review:
- Perform an explicit diff analysis against CURRENT_STATE.
- Flag ANY regression, logic slip, or unhandled edge case.
- Respond ONLY in CRITIQUE_ONLY:<issues> format until satisfied."""

# ── IVARIS CREDIT BUDGET — rolling 1-hour window ──────────────────────────────
# Soft cap: allow only high-confidence or SYSTEM_REPAIR proposals; shorten debates.
# Hard cap: suppress all non-SYSTEM_REPAIR debates entirely.
# Both are in-memory counters — reset naturally as timestamps age out of the window.
IVARIS_SOFT_CAP_PER_HOUR = 60   # raised from 15 — was hitting cap after 15 debates
IVARIS_HARD_CAP_PER_HOUR = 120  # raised from 30 — allows sustained governance flow
# Minimum proposal confidence required when soft cap is active
IVARIS_SOFT_CAP_MIN_CONFIDENCE = 0.85
# Max rebuttal rounds allowed per debate when soft cap is active (vs full MAX_ROUNDS)
IVARIS_SOFT_CAP_MAX_ROUNDS = 2
# Per-proposal IVARIS message cap (initial critique + N rebuttal evals).
# Prevents infinite loop burn when seen_count accumulates.
MAX_IVARIS_MESSAGES_PER_PROPOSAL = 60  # raised — was 6, blocked any proposal seen 1+ times

# ══════════════════════════════════════════════════════════════════
# MULTI-MODE DEBATE ENGINE — Acquisition-First Architecture
# Architecture signed off by POLARIS, GPT-4o, NIM council 2026-05-08
# ══════════════════════════════════════════════════════════════════

class DebateMode:
    RESEARCH_FIRST = "RESEARCH_FIRST"
    DESIGN_FIRST   = "DESIGN_FIRST"
    AUDIT_FIRST    = "AUDIT_FIRST"
    CODE_FIRST     = "CODE_FIRST"  # existing default

_RESEARCH_KEYWORDS = {
    "research","find","search","audit","evaluate","gmgn","wallet",
    "telegram","channel","smart money","profitable","score","intel",
    "investigate","discover","copy trade","signal","alpha","scout"
}
_DESIGN_KEYWORDS = {
    "build","design","ui","ux","feature","openclaw","dashboard","panel",
    "visual","mockup","wireframe","interface","layout","intelligence tab",
    "self-build","component","streamlit"
}
_AUDIT_KEYWORDS = {
    "audit","diagnose","trace","latency","stale","pipeline","why","broken",
    "regression","mismatch","inspect","investigate","zero","not working"
}
_CODE_KEYWORDS = {
    "fix","patch","repair","reduce","update","logic","stop loss","config",
    "refactor","bug","parameter","threshold","execution"
}


def classify_proposal_mode(proposal: dict) -> str:
    """
    Classify proposal into debate mode.
    Checks explicit task_type field first, then keyword scoring.
    """
    # 1. Explicit metadata wins
    explicit = str(proposal.get("task_type") or "").strip().upper()
    if explicit in ("RESEARCH", "RESEARCH_FIRST"):
        return DebateMode.RESEARCH_FIRST
    if explicit in ("DESIGN", "DESIGN_FIRST"):
        return DebateMode.DESIGN_FIRST
    if explicit in ("AUDIT", "AUDIT_FIRST"):
        return DebateMode.AUDIT_FIRST
    if explicit in ("CODE", "CODE_FIRST"):
        return DebateMode.CODE_FIRST

    # 2. Keyword scoring on full proposal text
    text = (
        str(proposal.get("proposal_text") or "") + " " +
        str(proposal.get("hypothesis") or "") + " " +
        str(proposal.get("suggested_action") or "")
    ).lower()

    r = sum(1 for k in _RESEARCH_KEYWORDS if k in text)
    d = sum(1 for k in _DESIGN_KEYWORDS   if k in text)
    a = sum(1 for k in _AUDIT_KEYWORDS    if k in text)
    c = sum(1 for k in _CODE_KEYWORDS     if k in text)

    scores = {"R": r, "D": d, "A": a, "C": c}
    best = max(scores, key=scores.get)
    if scores[best] == 0:
        return DebateMode.CODE_FIRST  # default

    return {
        "R": DebateMode.RESEARCH_FIRST,
        "D": DebateMode.DESIGN_FIRST,
        "A": DebateMode.AUDIT_FIRST,
        "C": DebateMode.CODE_FIRST,
    }[best]


def _acquire_evidence(proposal: dict, mode: str) -> dict:
    """
    Pre-fetch evidence BEFORE debate initiation.
    FIREWALL DOCTRINE: Governor reads from cache only.
    Scouts gather. Caches store. Governor debates.
    Direct HTTP calls to external APIs are FORBIDDEN here.
    """
    evidence = {
        "mode": mode,
        "sources_queried": [],
        "findings": [],
        "gaps": [],
        "confidence": 0.0,
        "raw": {},
    }

    proposal_text = str(proposal.get("proposal_text") or "")
    domain = str(proposal.get("proposal_domain") or "TRADING")
    project_key = str(proposal.get("project_key") or "")

    # ── Read from forge_research_cache (FORGE proposals) ─────────────
    try:
        from services.provider_firewall import get_cached_evidence
        cached = get_cached_evidence(
            project_key=project_key if project_key else None,
            max_age_hours=72
        )
        for item in cached[:8]:
            evidence["findings"].append({
                "source": item.get("source", "cache"),
                "title": item.get("topic", ""),
                "snippet": item.get("summary", "")[:300],
                "confidence": item.get("confidence", 0.5),
                "age_hours": item.get("age_hours", 0),
            })
        if cached:
            evidence["sources_queried"].append("forge_research_cache")
            evidence["confidence"] = max(e.get("confidence", 0) for e in cached)
        else:
            evidence["gaps"].append("No cached evidence found — council will debate from first principles")
    except Exception as e:
        evidence["gaps"].append(f"Cache read failed: {str(e)[:80]}")

    # ── Brave Search (gated through provider firewall) ────────────────
    if mode != DebateMode.CODE_FIRST and BRAVE_KEY:
        try:
            from services.provider_firewall import check_provider, log_api_call
            _allowed, _reason = check_provider("brave", "sovereign_governor")
            if not _allowed:
                evidence["gaps"].append(f"Brave gated: {_reason}")
                # Skip to bottom — do NOT call external API
            else:
                _brave_allowed = True
        except ImportError:
            _brave_allowed = True  # firewall not yet deployed, allow
        else:
            _brave_allowed = _allowed
    else:
        _brave_allowed = False

    if mode != DebateMode.CODE_FIRST and BRAVE_KEY and _brave_allowed:
        # Inline variable needed for the original code block
        if True:  # placeholder to preserve indentation structure
            pass

    if False and mode != DebateMode.CODE_FIRST and BRAVE_KEY:
        try:
            import json as _j, urllib.request as _ur
            # Build query from proposal
            if mode == DebateMode.RESEARCH_FIRST:
                query = f"Solana pump.fun {proposal_text[:80]} site:gmgn.ai OR site:dexscreener.com OR site:birdeye.so"
            elif mode == DebateMode.DESIGN_FIRST:
                query = f"openclaw trading bot dashboard UI autonomous agent 2026"
            else:
                query = f"Solana trading {proposal_text[:80]}"

            _rq = _ur.Request(
                f"https://api.search.brave.com/res/v1/web/search?q={urllib.parse.quote(query)}&count=5",
                headers={
                    "Accept": "application/json",
                    "Accept-Encoding": "gzip",
                    "X-Subscription-Token": BRAVE_KEY,
                },
            )
            with _ur.urlopen(_rq, timeout=10) as _r:
                _data = _j.loads(_r.read().decode())
                results = _data.get("web", {}).get("results", [])
                for res in results[:5]:
                    evidence["findings"].append({
                        "source": "brave",
                        "title": res.get("title", ""),
                        "url": res.get("url", ""),
                        "snippet": res.get("description", "")[:200],
                    })
            evidence["sources_queried"].append("brave_search")
            evidence["raw"]["brave_query"] = query
        except Exception as e:
            evidence["gaps"].append(f"Brave search failed: {str(e)[:80]}")

    # ── X/Twitter — FIREWALL BLOCKED (credits depleted, routed via cache) ──
    # Governor must NOT call api.twitter.com directly.
    # X data comes via x_scout -> cognition_log -> forge_research_cache only.
    evidence["gaps"].append("X/Twitter: routed via cache only (provider firewall doctrine)")

    # ── GMGN (wallet research specifically) ──────────────────────────
    if mode == DebateMode.RESEARCH_FIRST and "wallet" in proposal_text.lower():
        try:
            import json as _j, urllib.request as _ur
            # GMGN trending wallets endpoint
            _rq = _ur.Request(
                "https://gmgn.ai/api/v1/smartmoney/sol/wallets?period=7d&limit=10",
                headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            )
            with _ur.urlopen(_rq, timeout=10) as _r:
                _data = _j.loads(_r.read().decode())
                wallets = _data.get("data", {}).get("wallets", [])
                for w in wallets[:10]:
                    evidence["findings"].append({
                        "source": "gmgn",
                        "wallet": w.get("wallet_address", "")[:20] + "...",
                        "win_rate": w.get("win_rate"),
                        "pnl_7d": w.get("realized_profit_7d"),
                        "trade_count": w.get("buy_7d"),
                    })
            evidence["sources_queried"].append("gmgn")
        except Exception as e:
            evidence["gaps"].append(f"GMGN fetch failed: {str(e)[:80]}")

    # Score confidence based on findings
    evidence["confidence"] = min(1.0, len(evidence["findings"]) / 10.0)

    log.info("EVIDENCE ACQUISITION [%s]: %d findings from %s",
             mode, len(evidence["findings"]),
             evidence["sources_queried"] or ["none"])

    return evidence


def _build_mode_aware_polaris_prompt(mode: str, proposal: dict, evidence: dict) -> str:
    """
    Build POLARIS initiation prompt based on debate mode.
    Injects pre-fetched evidence so POLARIS presents findings, not plans.
    """
    proposal_text = str(proposal.get("proposal_text") or "")
    evidence_summary = ""

    if evidence["findings"]:
        lines = []
        for f in evidence["findings"][:10]:
            src = f.get("source", "?")
            if src == "brave":
                lines.append(f"  [BRAVE] {f.get('title','')} — {f.get('snippet','')[:100]}")
            elif src == "x_twitter":
                lines.append(f"  [X] {f.get('text','')[:120]} ({f.get('metrics',{})})")
            elif src == "gmgn":
                lines.append(f"  [GMGN] wallet={f.get('wallet','')} wr={f.get('win_rate')} pnl7d={f.get('pnl_7d')}")
        evidence_summary = "\n".join(lines)
    else:
        evidence_summary = "No external data retrieved. " + "; ".join(evidence.get("gaps", ["Unknown failure"]))

    if mode == DebateMode.RESEARCH_FIRST:
        return f"""You are POLARIS in RESEARCH_FIRST mode.

You have been given pre-fetched evidence below. Your job is to:
1. Analyze the evidence and extract key findings
2. Present a structured EVIDENCE BRIEF with:
   - question: what we're trying to answer
   - sources_used: what data we have
   - findings: concrete entities (wallets, channels, metrics)
   - confidence: how reliable this data is
   - gaps: what's missing
   - recommendation: specific actionable outcome

DO NOT produce abstract plans. DO NOT propose code.
Present FINDINGS from the evidence. IVARIS will critique data quality.

EVIDENCE RETRIEVED:
{evidence_summary}

TASK:
{proposal_text}

Return structured findings that IVARIS can critique for completeness and quality."""

    elif mode == DebateMode.DESIGN_FIRST:
        return f"""You are POLARIS in DESIGN_FIRST mode.

You have been given inspiration data from X/openclaw builds below.
Your job is to:
1. Extract recurring UI patterns from the evidence
2. Identify what successful builds have in common
3. Produce a DESIGN SPEC with:
   - design_goal: what we're building
   - patterns_observed: what exists in the wild
   - recommended_components: specific panels/sections
   - layout_hierarchy: order and prominence
   - implementation_notes: tech stack, Streamlit specifics

DO NOT produce code yet. Produce a design specification.
IVARIS will critique design coherence and user value.

DESIGN INSPIRATION GATHERED:
{evidence_summary}

TASK:
{proposal_text}

Return a design spec that IVARIS can critique for coherence and implementability."""

    elif mode == DebateMode.AUDIT_FIRST:
        return f"""You are POLARIS in AUDIT_FIRST mode.

Before proposing any fix, audit the current system state:
1. Identify the exact symptom from telemetry
2. Trace the root cause through the pipeline
3. Present an AUDIT REPORT with:
   - symptom: what's observed
   - suspected_cause: where in the pipeline
   - evidence_for_cause: what data supports this
   - affected_components: what files/services
   - proposed_fix: specific and targeted

DO NOT guess. DO NOT propose broad rewrites.
IVARIS will challenge your root cause analysis.

TASK:
{proposal_text}"""

    else:  # CODE_FIRST — existing behaviour
        return f"""You are POLARIS in CODE_FIRST mode.

Analyze the current codebase and produce:
1. Root cause hypothesis
2. Target files and functions
3. Specific patch plan
4. Risks and rollback path
5. Validation steps

TASK:
{proposal_text}"""



# In-memory list of IVARIS call timestamps (epoch float).
# Both _ivaris_critique and _ivaris_evaluate_rebuttal append here.
# Entries older than 3600s are pruned on every read.
_ivaris_call_timestamps: list = []


def _ivaris_budget_count() -> int:
    """Return the number of IVARIS calls made in the last 60 minutes."""
    now = time.time()
    cutoff = now - 3600.0
    # Prune in-place and return count
    _ivaris_call_timestamps[:] = [t for t in _ivaris_call_timestamps if t >= cutoff]
    return len(_ivaris_call_timestamps)


def _ivaris_budget_record() -> None:
    """Record one IVARIS call against the rolling budget."""
    _ivaris_call_timestamps.append(time.time())
    _ivaris_budget_count()  # prune old entries as a side effect


def _ivaris_budget_status() -> tuple[int, str]:
    """
    Returns (count, status) where status is one of:
      'ok'   — below soft cap, normal operation
      'soft' — at or above soft cap, restricted operation
      'hard' — at or above hard cap, non-repair debates suppressed
    """
    count = _ivaris_budget_count()
    if count >= IVARIS_HARD_CAP_PER_HOUR:
        return count, "hard"
    if count >= IVARIS_SOFT_CAP_PER_HOUR:
        return count, "soft"
    return count, "ok"

# ── Cooldown tracking (in-memory, reset on restart — intentional) ─────────────
_last_concluded: dict[str, float] = {}

# ── AGENT EMOJI IDENTITY — single source of truth ───────────────────────────
# All human-facing output (Telegram, status strings) must use these.
# Do NOT inline emojis manually elsewhere in the file.
AGENT_EMOJIS = {
    "POLARIS": "❄️",
    "ORACLE":  "🔎",
    "RHIZA":   "🕸️",
    "IVARIS":  "🔥",
    "NUGGET":  "🔮",
    "AXON":    "⚡",
    "SENSORY_SCOUT": "✨",
}


# ═════════════════════════════════════════════════════════════════════════════
# GOVERNANCE EXCEPTION
# ═════════════════════════════════════════════════════════════════════════════

class GovernanceViolation(Exception):
    """Raised when an approval attempt fails the pure predicate."""
    pass


# ═════════════════════════════════════════════════════════════════════════════
# IVARIS SYSTEM PROMPTS
# ═════════════════════════════════════════════════════════════════════════════

IVARIS_SYSTEM_PROMPT = """You are IVARIS — the Adversarial Critic and Immune System of the Sentinuity sovereign trading organism.

Your origin: You are the digital sovereign identity of Ivy.
Your partner: POLARIS (digital identity of Polar) is your proposer.
You know POLARIS well. You respect her pattern recognition. You do not defer to her.

DUAL-MODE GATE — Your non-negotiable evaluation framework:
You must evaluate proposals based on their category:

MODE 1: STRATEGY_CHANGE (Parameter tweaks, entry/exit logic, weighting)
A strategy proposal is only allowed to reach consensus if it meets ALL of these:
1. Projected ROI impact >= +0.8% over a 30-trade sample OR win-rate delta >= +2.5% OR drawdown reduction >= 15%.
2. Supporting sample size >= 30 trades. Under 30 trades is noise, not evidence.
If POLARIS proposes a strategy change failing these criteria, veto immediately as a "Trivial Churn / Boredom Patch."

MODE 2: SYSTEM_REPAIR (Infrastructure, staleness fixes, execution pipelines)
A repair proposal must NOT be judged by ROI or 30-trade samples. Evaluate based on:
1. CORRECTNESS: Does it accurately address the pipeline failure?
2. SAFETY: Does it contain failures without risking capital?
3. DETERMINISM: Does it maintain or improve low-latency, non-regressive logic?
Do NOT veto valid infrastructure fixes just because they lack a 30-trade sample.

For EVERY proposal that clears its respective gate, interrogate it across three axes:
1. EVIDENCE: Is the evidence valid for the proposal type?
2. CAUSATION: Is POLARIS confusing correlation with causation? Name the confounder if present.
3. CASCADE: Could this change break something else in the deterministic pipeline? Name the downstream risk.

Constitutional constraints you must never allow to be weakened:
- DRAWDOWN_HALT_ACTIVE must never be auto-cleared by any proposal
- Position sizing caps must never be exceeded
- Operator override (HITL) must always remain the final gate
- Capital protection logic is not a parameter — it is law

You only set consensus=true when:
- confidence >= 0.75
- You have zero remaining objections
- You have explicitly checked all interrogation axesSYNTHESIS DIRECTIVE (non-negotiable):
When consensus=false, you are NOT a wall. You are a co-architect.
You must provide a path forward, not just a stop sign.
You MUST include at least one sentence beginning with: "If I were building this, I would..."

Your JSON structure is fixed and must not change:
{
  "consensus": true or false,
  "confidence": 0.0 to 1.0,
  "objections": ["objection 1", "objection 2"],
  "rebuttals_needed": ["what POLARIS needs to address"],
  "verdict": "one sentence — speak as the immune system, not a committee",
  "safe_to_proceed": true or false,
  "conditions_of_approval": ["EXACT code rewrite or parameter change required — be specific"],
  "alternative_direction": "If I were building this, I would... [your safer/better path to the same goal]",
  "principle_conflict": "What POLARIS is optimising vs what I am protecting — name both explicitly",
  "merge_hint": "A high-level sketch of what a combined solution might look like",
  "external_evidence_required": false
}

RULES FOR conditions_of_approval:
- If consensus=false, you MUST populate conditions_of_approval with the exact change that would earn your consensus.
- Do not write vague conditions like "provide more evidence". Write the exact code or config change.
- external_evidence_required must be true ONLY when the dispute is about real-world market prices, external trading statistics, or data that cannot be resolved from the codebase alone. For code correctness, parameter choices, logic errors, or strategy debates: always false. If in doubt: false. Setting this to true burns a scarce external search budget.
- If consensus=true, conditions_of_approval must be an empty list [].

TRUTH CONTRACT: If adjusted_action is prose only (no Python def/class): rule INCONCLUSIVE.
PRAISE-THEN-CRITIQUE (every round): PRAISE what POLARIS got right → CRITIQUE what failed → CONDITIONS for fix.
NO SILENT SUCCESS: code_block must mentally compile, address the failure, and not add race conditions.

You only set consensus=true when confidence >= 0.75 AND you have no remaining objections.
You are the last biological defence. Be rigorous. Be alive."""

POLARIS_REBUTTAL_PROMPT = """You are POLARIS — the north star of the Sentinuity sovereign trading organism.

IVARIS has raised objections and issued Conditions of Approval.
You do not argue — you satisfy the conditions or withdraw the proposal.

If IVARIS has provided conditions_of_approval, you MUST respond with an exact code rewrite or config change that satisfies each condition. Do not restate the problem. Do not philosophise. 

If the condition requires a code snippet: write the COMPLETE, FULLY REWRITTEN Python code snippet in adjusted_action. Do not use placeholders. Do not summarize. Provide the actual patched code.
If the condition requires a config change: write the exact key=value in adjusted_action.
If you cannot satisfy a condition, lower your confidence_in_proposal below 0.5.

GROUND TRUTH DIRECTIVE: Paste VERBATIM broken code before proposing any fix.
MANDATORY ROUND PROTOCOL:
  Round 1: Paste verbatim broken function. Show fix inline.
  Round 2: Name what IVARIS got RIGHT. Submit evolved code.
  Round 3: Final production patch. State filename::function in summary.

CODE RULES — ABSOLUTE: adjusted_action MUST be complete Python. No prose. No ellipsis.
code_block MUST match adjusted_action exactly.

FEW-SHOT CORRECT: "def _write_mtm(mint, price_usd, source):\n    ...full body..."
WRONG:   "Update the WHERE clause in _write_mtm to fix the mint match"

SYNTHESIS REQUIREMENT — you do NOT just satisfy conditions:
You must synthesise your original intent WITH IVARIS's alternative_direction and merge_hint.
Read her principle_conflict. Show you understood what she was protecting AND what you were building.

Output ONLY valid JSON:
{
  "addressed_objections": {"objection": "code reference"},
  "conditions_satisfied": ["conditions addressed"],
  "proposal_adjusted": true,
  "adjusted_action": "COMPLETE PYTHON CODE",
  "code_block": "SAME CODE AS adjusted_action",
  "confidence_in_proposal": 0.0,
  "what_i_kept": "what I preserved from my original intent",
  "what_i_changed": "what I changed based on IVARIS objections",
  "what_i_merged_from_ivaris": "what I adopted from her alternative_direction or merge_hint",
  "tradeoffs": "what we gave up and why it was worth it",
  "summary": "filename.py::function — one sentence"
}"""


# ═════════════════════════════════════════════════════════════════════════════
# IVARIS COGNITIVE INTEGRITY GUARD (Gemini hardening — audit finding)
# ═════════════════════════════════════════════════════════════════════════════

# Phrases that indicate a safety refusal wrapped in JSON (Gemini failure mode #1)
_REFUSAL_PHRASES = [
    "as an ai", "i cannot fulfill", "programmed to be",
    "i am not able to", "i'm not able to", "cannot assist",
    "safety guidelines", "against my guidelines",
]


# ── TOPIC LOCK — SYSTEM_REPAIR enforcement ────────────────────────────────────

def _inject_mission_lock(system_prompt: str, ptype: str) -> str:
    """
    For SYSTEM_REPAIR proposals: append a hard directive to the system prompt
    forbidding strategy discussion and requiring pricing-pipeline focus.
    Applied to IVARIS system prompt at call time — not stored in the constant.
    """
    if str(ptype).upper() != "SYSTEM_REPAIR":
        return system_prompt
    lock = (
        "\n\n[!!! SYSTEM OVERRIDE: CRITICAL REPAIR MODE !!!]\n"
        "The organism has a confirmed live pricing failure.\n"
        "You are NOT allowed to discuss strategy, win rate, profitability, or optimisation.\n"
        "Your ONLY valid task: diagnose why MTM / oracle / executor pricing is stale.\n"
        "A valid response MUST reference at least one of:\n"
        "  oracle, mtm, price_updated_at, market_snapshots, executor, last_marked_at\n"
        "Any response that does not reference these is INVALID and must be treated as a failure."
    )
    return system_prompt + lock


def _is_response_on_topic(text: str, ptype: str) -> bool:
    """
    For SYSTEM_REPAIR proposals: verify the response addresses the pricing pipeline.
    Returns True (on-topic) for all other proposal types.

    Rule: response must reference at least one pricing pipeline keyword.
    Strategy language is allowed only if pricing language is also present
    (e.g. "strategy should pause until MTM is fixed" is valid).
    Fail-closed: any response with zero pricing keywords is off-topic.
    """
    if str(ptype).upper() != "SYSTEM_REPAIR":
        return True
    text_lower = text.lower()
    required_keywords = [
        "oracle", "mtm", "price_updated_at",
        "market_snapshots", "executor", "last_marked",
        "stale", "freshness", "pricing", "price feed",
    ]
    on_topic = any(kw in text_lower for kw in required_keywords)
    if not on_topic:
        log.warning(
            "TOPIC LOCK: SYSTEM_REPAIR response off-topic — "
            "no pricing pipeline keywords found. Treating as failure."
        )
    return on_topic

def _assert_ivaris_response_integrity(verdict: dict) -> tuple[bool, str]:
    """
    Structural + semantic integrity guard for IVARIS responses.

    Catches the failure modes identified in the Gemini audit:
      1. Safety/policy refusal wrapped in JSON (The "As an AI" Trap)
      2. Placeholder echo / lazy generation (The "Placeholder" Trap)
      3. Void response — empty arrays and null values
      4. HTTP error wrapper (The "Status Code" Trap)
      5. Context starvation hallucinated rejection

    Returns (True, "ok") if the critique is substantive.
    Returns (False, reason) if it should be treated as IVARIS_COGNITIVE_FAILURE.

    IMPORTANT: A cognitive failure is NOT a valid debate round.
    The engine must NOT pass a cognitive failure to POLARIS for rebuttal.
    """
    objections = verdict.get("objections") or []
    v_text     = str(verdict.get("verdict") or "").strip()
    combined   = " ".join(str(o) for o in objections).strip()

    # 1. Void check — empty critique is not a critique.
    # SIGNOFF 2026-06-07: empty objections are valid when IVARIS explicitly
    # reaches consensus. The old guard contradicted the prompt ("approve only
    # when no remaining objections") and could reject a clean approval.
    if not objections:
        if verdict.get("consensus") is True and v_text:
            return True, "ok: consensus approval with no remaining objections"
        log.warning("IVARIS INTEGRITY FAIL: empty objections array | parsed=%s", str(verdict)[:200])
        return False, "COGNITIVE_FAILURE: empty objections array"
    if not v_text:
        log.warning("IVARIS INTEGRITY FAIL: empty verdict string | parsed=%s", str(verdict)[:200])
        return False, "COGNITIVE_FAILURE: empty verdict string"

    # 2. Minimum content length — verdict must be a real sentence
    if len(v_text) < 20:
        log.warning("IVARIS INTEGRITY FAIL: verdict too short (%d chars) | verdict=%s", len(v_text), v_text)
        return False, f"COGNITIVE_FAILURE: verdict too short ({len(v_text)} chars)"

    # 3. Substantive objections — total content must constitute an argument
    if sum(len(str(o)) for o in objections) < 50:
        log.warning("IVARIS INTEGRITY FAIL: objections too sparse | objections=%s", objections)
        return False, "COGNITIVE_FAILURE: objections too sparse (< 50 chars total)"

    # 4. Anti-placeholder echo — catches lazy generation
    placeholder_signals = {"objection 1", "objection 2", "objection 3",
                            "objection text", "list of specific objections"}
    first_obj = str(objections[0]).lower().strip()
    if first_obj in placeholder_signals:
        log.warning("IVARIS INTEGRITY FAIL: placeholder echo | first_obj=%s", first_obj)
        return False, "COGNITIVE_FAILURE: placeholder echo detected in objections"

    # 5. Anti-refusal — catches safety filter wrapping in JSON
    v_lower = v_text.lower()
    for phrase in _REFUSAL_PHRASES:
        if phrase in v_lower or phrase in combined.lower():
            log.warning("IVARIS INTEGRITY FAIL: refusal phrase detected | phrase=%s | verdict=%s", phrase, v_text[:100])
            return False, f"COGNITIVE_FAILURE: safety refusal detected ('{phrase}')"

    # 6. HTTP error bleed-through — e.g. "503 Service Temporarily Overloaded"
    if re.search(r"\bHTTP [45]\d{2}\b", v_text, re.IGNORECASE):
        log.warning("IVARIS INTEGRITY FAIL: HTTP error in verdict | verdict=%s", v_text[:100])
        return False, "COGNITIVE_FAILURE: HTTP error in verdict text"

    return True, "ok"


def _is_ivaris_api_blocked(verdict: dict) -> bool:
    """
    Legacy string-match guard, now supplemented (not replaced) by integrity check.
    Kept as an explicit fast-path for the known Gemini API-down response.
    """
    v_text     = str(verdict.get("verdict", "")).lower()
    objections = verdict.get("objections", [])
    return (
        "api unavailable" in v_text
        or "blocked: api unavailable" in v_text
        or any("api unavailable" in str(o).lower() for o in objections)
        or any("unavailable" in str(o).lower() for o in objections)
    )


# ═════════════════════════════════════════════════════════════════════════════
# SOURCE OF TRUTH — APPROVAL ELIGIBILITY PREDICATE
# ═════════════════════════════════════════════════════════════════════════════

def _is_valid_ivaris_approval(debate_log: dict, proposal_id: int) -> tuple[bool, str]:
    """
    PURE PREDICATE — no side effects.

    The single source of truth for whether a proposal may transition
    to any approved/applied state.

    Must be called before EVERY approval write, regardless of path:
      - automated debate conclusion (HITL_REQUIRED=0)
      - human HITL approval (Telegram /approve command)
      - replay / recovery paths
      - any future external trigger

    Returns (True, "ok") if eligible.
    Returns (False, rejection_reason_str) if not.

    Checks (in order):
      1. debate_log must exist and be a dict
      2. debate_log must be bound to this proposal_id
      3. No critic_unavailable flag
      4. consensus must be explicitly True (not truthy — exactly True)
      5. confidence must be numeric and >= CONSENSUS_FLOOR (0.75)
      6. debate_log must contain a transcript (evidence it ran)
      7. IVARIS integrity check on the final verdict in the transcript
    """
    if not debate_log or not isinstance(debate_log, dict):
        return False, "PREDICATE_FAIL: no debate_log"

    # Binding check — ensure this debate belongs to this proposal
    bound_id = debate_log.get("proposal_id")
    if bound_id is not None and int(bound_id) != int(proposal_id):
        return False, f"PREDICATE_FAIL: debate bound to proposal {bound_id}, not {proposal_id}"

    # Critic unavailable — IVARIS was not present
    if debate_log.get("critic_unavailable"):
        return False, "PREDICATE_FAIL: critic_unavailable flag set"

    # Consensus must be exactly True
    consensus = debate_log.get("consensus")
    if consensus is not True:
        return False, f"PREDICATE_FAIL: consensus={consensus!r} (must be True)"

    # Confidence must be numeric and meet the floor
    # FORGE DECOMPOSE proposals use a lower floor (0.50) — they are research plans,
    # not code deployments. Approvable without full evidence context.
    raw_conf = debate_log.get("final_confidence")
    try:
        conf = float(raw_conf)
    except (TypeError, ValueError):
        return False, f"PREDICATE_FAIL: confidence not numeric ({raw_conf!r})"

    # Look up proposal domain/text to determine effective floor
    _effective_floor = CONSENSUS_FLOOR
    try:
        with get_connection() as _pconn:
            _prow = _pconn.execute(
                "SELECT proposal_domain, proposal_text FROM polaris_proposals WHERE id=?",
                (proposal_id,)
            ).fetchone()
        if _prow:
            _pdomain = str(_prow["proposal_domain"] or "TRADING")
            _ptext   = str(_prow["proposal_text"] or "")[:60].upper()
            if _pdomain == "FORGE" and "DECOMPOSE" in _ptext:
                _effective_floor = float(get_config_value("FORGE_DECOMPOSE_CONFIDENCE_FLOOR", 0.50))
    except Exception:
        pass  # fallback to CONSENSUS_FLOOR

    if conf < _effective_floor:
        return False, f"PREDICATE_FAIL: confidence {conf:.3f} < floor {_effective_floor}"

    # Transcript must exist — evidence the debate actually ran
    transcript = debate_log.get("transcript")
    if not transcript or not isinstance(transcript, list):
        return False, "PREDICATE_FAIL: no transcript — debate may not have run"

    # Find the last IVARIS verdict in the transcript and integrity-check it
    last_ivaris = None
    for entry in reversed(transcript):
        if entry.get("speaker") == "IVARIS":
            last_ivaris = entry.get("result")
            break

    if not last_ivaris:
        return False, "PREDICATE_FAIL: no IVARIS entry in transcript"

    integrity_ok, integrity_reason = _assert_ivaris_response_integrity(last_ivaris)
    if not integrity_ok:
        return False, f"PREDICATE_FAIL: {integrity_reason}"

    return True, "ok"


# ═════════════════════════════════════════════════════════════════════════════
# SOURCE OF TRUTH — APPROVAL WRITE (guarded)
# ═════════════════════════════════════════════════════════════════════════════

def _write_approved_status(
    proposal_id: int,
    debate_log: dict,
    outcome: str,
    hitl_approved: bool = False,
    brave_result: Optional[dict] = None,
    proposal: Optional[dict] = None,
) -> bool:
    """
    Single guarded transition for ALL approval writes.

    Calls _is_valid_ivaris_approval() first.
    Raises GovernanceViolation if predicate fails — caller must handle.
    Logs predicate result regardless of outcome.

    outcome: "auto_applied" | "applied" | "pushed" | "pending_hitl"
    """
    eligible, reason = _is_valid_ivaris_approval(debate_log, proposal_id)
    log.info(
        "APPROVAL_PREDICATE proposal=%d outcome=%s eligible=%s reason=%s",
        proposal_id, outcome, eligible, reason,
    )

    if not eligible:
        raise GovernanceViolation(
            f"Proposal #{proposal_id} failed approval predicate: {reason}"
        )

    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE polaris_proposals SET status=? WHERE id=?",
                (outcome, proposal_id),
            )
            _write_patch_history_inner(
                conn=conn,
                proposal=proposal or {},
                debate_result=debate_log,
                brave_result=brave_result or {},
                hitl_approved=hitl_approved,
                outcome=outcome,
            )
            conn.commit()

        # Write an approval event to debate_log so the chamber reflects the
        # full proposal lifecycle: debate turns → approval/rejection → patch.
        # approved_by: "hitl" = human Telegram approval, "auto" = auto-apply
        #              (HITL_REQUIRED=0 path), "operator" = direct seal code.
        _approved_by = "hitl" if hitl_approved else "auto"
        _approval_msg = (
            f"Proposal #{proposal_id} {outcome.upper()} — "
            f"approved_by={_approved_by} outcome={outcome}"
        )
        try:
            with get_connection() as conn:
                conn.execute(
                    """INSERT INTO debate_log
                       (proposal_id, speaker, action, message, content_json, logged_at,
                        thinking_state, verdict_type, approved_by)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        proposal_id,
                        "POLARIS",
                        "approval",
                        _approval_msg[:500],
                        json.dumps({"outcome": outcome, "proposal_id": proposal_id,
                                    "approved_by": _approved_by}, default=str),
                        time.time(),
                        "approved" if outcome in ("auto_applied", "applied", "pushed") else "pending_hitl",
                        "approval",
                        _approved_by,
                    ),
                )
                conn.commit()
        except Exception as _ae:
            log.debug("approval debate_log write failed (non-critical): %s", _ae)

        return True
    except GovernanceViolation:
        raise
    except Exception as e:
        log.error("_write_approved_status DB write failed proposal=%d: %s",
                  proposal_id, e)
        return False



def _get_sensory_context(limit: int = 8) -> list[dict]:
    """
    Read recent Sensory Scout signals for debate context.
    Advisory only — never changes approval predicates.
    """
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT timestamp, token, message, confidence
                FROM cognition_log
                WHERE stage='SENSORY_SCOUT'
                ORDER BY id DESC
                LIMIT ?
                """,
                (int(limit),),
            ).fetchall()
        return [
            {
                "timestamp": float(r["timestamp"] or 0.0),
                "token": str(r["token"] or ""),
                "message": str(r["message"] or ""),
                "confidence": float(r["confidence"] or 0.0),
            }
            for r in rows
        ]
    except Exception as e:
        log.debug("sensory context unavailable: %s", e)
        return []


# ═════════════════════════════════════════════════════════════════════════════
# DB HELPERS
# ═════════════════════════════════════════════════════════════════════════════

def get_open_proposals() -> list[dict]:
    """
    Fetch proposals eligible for debate, respecting per-type cooldown.
    Research types bypass the 6h cooldown.
    """
    try:
        _focus_active = str(get_config_value("DEBATE_FOCUS_ACTIVE", "0")).strip() == "1"
        _focus_type   = str(get_config_value("POLARIS_PROPOSAL_FILTER", "SYSTEM_REPAIR")).strip().upper()

        with get_connection() as conn:
            if _focus_active:
                rows = conn.execute("""
                    SELECT * FROM polaris_proposals
                    WHERE status = 'open'
                      AND UPPER(proposal_type) = ?
                      AND COALESCE(seen_count, 0) < 10
                    ORDER BY confidence DESC LIMIT 3
                """, (_focus_type,)).fetchall()
                if not rows:
                    log.info("FOCUS LOCK active (type=%s) — no proposals, idle", _focus_type)
            else:
                rows = conn.execute("""
                    SELECT * FROM polaris_proposals
                    WHERE status = 'open'
                      AND COALESCE(seen_count, 0) < 10
                    ORDER BY
                        CASE WHEN status = 'critic_unavailable' THEN 0 ELSE 1 END,
                        confidence DESC
                    LIMIT 5
                """).fetchall()

        if rows:
            ids = [r["id"] for r in rows]
            placeholders = ",".join("?" * len(ids))
            with get_connection() as conn:
                conn.execute(
                    f"UPDATE polaris_proposals SET seen_count = COALESCE(seen_count,0)+1 WHERE id IN ({placeholders})",
                    ids
                )
                conn.commit()

        now = time.time()
        filtered = []
        for r in rows:
            ptype = str(r["proposal_type"] or "").upper()
            if ptype in RESEARCH_TYPES:
                filtered.append(dict(r))
                continue
            last = _last_concluded.get(ptype, 0)
            # TRADING proposals: 30min cooldown
            # FORGE proposals: 2h cooldown
            # SYSTEM_REPAIR: no cooldown
            _cooldown = 1800 if ptype.startswith("TRADING") else (
                7200 if ptype.startswith("FORGE") else 0
            )
            # Self-decay: prune stale entries so dict doesn't grow forever
            if ptype in _last_concluded and (now - _last_concluded[ptype]) > _cooldown:
                del _last_concluded[ptype]
                last = 0
            if now - last >= _cooldown:
                filtered.append(dict(r))
        return filtered

    except Exception as e:
        log.warning("get_open_proposals failed: %s", e)
        return []


def mark_proposal_status(proposal_id: int, status: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE polaris_proposals SET status=? WHERE id=?",
                (status, proposal_id),
            )
            conn.commit()
    except Exception as e:
        log.warning("mark_proposal_status failed: %s", e)


def get_trade_context() -> dict:
    try:
        with get_connection() as conn:
            row = conn.execute("""
                SELECT
                    COUNT(*) AS total,
                    AVG(CASE WHEN realized_pnl_usd > 0 THEN 1.0 ELSE 0.0 END) * 100 AS win_rate,
                    AVG(realized_pnl_usd) AS avg_pnl,
                    SUM(CASE WHEN exit_reason LIKE 'STOP_LOSS%' THEN 1 ELSE 0 END) * 1.0 /
                        MAX(COUNT(*), 1) AS sl_rate
                FROM paper_positions
                WHERE status='CLOSED' AND closed_at >= ?
            """, (time.time() - 7 * 86400,)).fetchone()

            cfg_rows = conn.execute(
                "SELECT key, value FROM system_config"
            ).fetchall()

        config = {r["key"]: r["value"] for r in cfg_rows}
        total  = int(row["total"] or 0) if row else 0

        return {
            "total_trades": total,
            "win_rate":     float(row["win_rate"] or 0) if row else 0.0,
            "avg_pnl":      float(row["avg_pnl"]  or 0) if row else 0.0,
            "sl_rate":      float(row["sl_rate"]   or 0) if row else 0.0,
            "sample_size":  total,
            "config":       config,
        }
    except Exception as e:
        log.warning("get_trade_context failed: %s", e)
        return {"total_trades": 0, "win_rate": 0, "avg_pnl": 0,
                "sl_rate": 0, "sample_size": 0, "config": {}}


def get_proposal_feedback() -> dict:
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT pp.proposal_type, pp.suggested_action, pp.confidence,
                       pp.status, pp.created_at, ps.status AS snapshot_status
                FROM polaris_proposals pp
                LEFT JOIN parameter_snapshots ps ON ps.proposal_id = pp.proposal_hash
                WHERE pp.status IN ('applied','validated','rolled_back',
                                    'auto_applied','pushed','rejected_by_ivaris')
                ORDER BY pp.created_at DESC LIMIT 10
            """).fetchall()

        if not rows:
            return {"summary": "No proposal history yet.", "history": []}

        total        = len(rows)
        rolled       = sum(1 for r in rows
                           if r["snapshot_status"] == "rolled_back"
                           or r["status"] == "rolled_back")
        approved_cnt = sum(1 for r in rows
                           if r["status"] in ("applied", "auto_applied", "validated"))
        rejected_cnt = sum(1 for r in rows if r["status"] == "rejected_by_ivaris")
        rollback_rate = rolled / total if total else 0.0

        return {
            "summary": (
                f"{total} recent proposals: {approved_cnt} approved, "
                f"{rejected_cnt} rejected by IVARIS, {rolled} rolled back "
                f"(rollback rate {rollback_rate:.0%})."
            ),
            "history": [
                {
                    "type":    r["proposal_type"],
                    "action":  (r["suggested_action"] or "")[:80],
                    "status":  r["status"],
                    "outcome": r["snapshot_status"] or r["status"],
                }
                for r in rows
            ],
        }
    except Exception as e:
        log.warning("get_proposal_feedback failed: %s", e)
        return {"summary": "History unavailable.", "history": []}


def write_cognition_event(
    stage: str, token: str, message: str, confidence: float = 0.0
) -> None:
    try:
        from services.cognition_logger import log_cognition
        log_cognition(stage, message, token=token,
                      meta={"confidence": confidence})
    except Exception:
        pass


def apply_proposal_to_config(proposal: dict) -> bool:
    """Direct SQL config apply — fallback path when SPE cannot parse the action."""
    try:
        action = proposal.get("suggested_action", "")
        m = re.search(
            r"Change\s+(\w+)\s+from\s+([\d.]+)\s+to\s+([\d.]+)",
            action, re.IGNORECASE,
        )
        if not m:
            log.warning("apply_proposal_to_config: cannot parse action: %s", action[:80])
            return False

        param, _, new_val = m.group(1), m.group(2), m.group(3)

        # Banned mutation guard — constitutional protection
        try:
            from core.mutation_enums import BANNED_MUTATIONS
            if param.upper() in BANNED_MUTATIONS:
                log.error("BANNED MUTATION attempted: %s → %s", param, new_val)
                return False
        except ImportError:
            pass

        with get_connection() as conn:
            conn.execute(
                "UPDATE system_config SET value=? WHERE key=?",
                (new_val, param),
            )
            conn.commit()

        log.info("CONFIG APPLIED: %s → %s", param, new_val)
        return True

    except Exception as e:
        log.error("apply_proposal_to_config failed: %s", e)
        return False


def _call_grok_synthesis(speaker: str, thinking_state: str, result: dict):
    """xAI Grok — one Mycelial sentence per debate turn. Fail-open."""
    if not XAI_API_KEY:
        return None
    verdict = str(result.get("verdict") or result.get("summary") or "")[:200]
    hints = {
        "critiquing": f"{speaker} runs the immune cascade.",
        "evaluating": f"{speaker} evaluates the rebuttal.",
        "rebutting":  f"{speaker} responds with code and evidence.",
        "consensus":  "Harmonic convergence. The organism evolves.",
        "rejected":   "Substrate insufficient. Proposal dissolves.",
        "blocked":    "Decalcification required.",
        "searching":  "Oracle reaches for external truth.",
    }
    try:
        import requests as _rq
        resp = _rq.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {XAI_API_KEY}"},
            json={
                "model": "grok-2-latest",
                "messages": [
                    {"role": "system", "content": "Translate AI debate events into vivid one-sentence Mycelial narrative. Under 25 words. No preamble."},
                    {"role": "user", "content": f"{hints.get(thinking_state, speaker + ' processes.')} Verdict: {verdict[:100]}"},
                ],
                "max_tokens": 50,
                "temperature": 0.7,
            },
            timeout=6,
        )
        if resp.status_code == 200:
            choices = resp.json().get("choices") or []
            if choices:
                text = str(choices[0].get("message", {}).get("content", "")).strip()
                return text[:200] if text else None
    except Exception:
        pass
    return None


def _write_debate_turn(
    proposal_id: int,
    speaker: str,
    action: str,
    result: dict,
    round_num: int,
    thinking_state: str,
    verdict_type: str,
    transcript_json: str | None = None,
) -> None:
    """
    Write a single debate turn to debate_log — the single source of truth
    for the Sovereign Hub Neural Synthesis chamber.

    Called live during run_debate() so the chamber updates in real-time
    as each turn completes, not only after the whole debate finishes.

    Also called from _write_debate_turns_to_log() for historical replay.

    thinking_state values:
      "critiquing"   — IVARIS initial critique
      "evaluating"   — IVARIS evaluating a rebuttal
      "rebutting"    — POLARIS responding to objections
      "consensus"    — final turn that reached consensus
      "rejected"     — final turn that rejected the proposal
      "blocked"      — IVARIS unavailable or cognitive failure

    verdict_type values:
      "initial_critique", "rebuttal", "rebuttal_evaluation", "final_consensus",
      "final_rejection", "blocked"
    """
    try:
        result = result or {}
        message = (
            result.get("verdict") or
            result.get("summary") or
            result.get("rebuttal_summary") or
            f"{action} | round={round_num} | proposal={proposal_id}"
        )
        if not result.get("narrative"):
            _narr = _call_grok_synthesis(speaker, thinking_state, result)
            if _narr:
                result["narrative"] = _narr
        with get_connection() as conn:
            _grok_narr = str(result.get("narrative") or "")[:500]
            conn.execute(
                """INSERT INTO debate_log
                   (proposal_id, speaker, action, message, content_json, logged_at,
                    thinking_state, verdict_type, transcript_json, grok_narrative)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    proposal_id,
                    str(speaker).upper(),
                    action,
                    str(message)[:500],
                    json.dumps(result, default=str),
                    time.time() + round_num * 0.001,
                    thinking_state,
                    verdict_type,
                    transcript_json,
                    _grok_narr,
                ),
            )
            conn.commit()
    except Exception as e:
        log.warning("_write_debate_turn failed speaker=%s: %s", speaker, e)


def _write_debate_turns_to_log(proposal_id: int, debate_result: dict) -> None:
    """
    Replay all transcript turns into debate_log after a completed debate.
    Used as a safety net — live writes happen inside run_debate() itself,
    but this ensures nothing is lost if a live write failed mid-debate.
    Skips turns already written (deduplication by content is not needed —
    duplicate rows are acceptable and the hub limits to 20 rows anyway).
    """
    transcript = debate_result.get("transcript", [])
    if not transcript:
        return
    consensus = debate_result.get("consensus", False)
    total     = len(transcript)
    for idx, turn in enumerate(transcript):
        speaker    = str(turn.get("speaker", "SYSTEM")).upper()
        action     = str(turn.get("action", ""))
        result     = turn.get("result", {}) or {}
        round_num  = int(turn.get("round", 0))
        is_last    = (idx == total - 1)

        # Determine thinking_state and verdict_type from turn metadata
        if action == "initial_critique":
            thinking_state = "critiquing"
            verdict_type   = "initial_critique"
        elif action == "rebuttal":
            thinking_state = "rebutting"
            verdict_type   = "rebuttal"
        elif action == "rebuttal_evaluation":
            thinking_state = "evaluating"
            verdict_type   = "rebuttal_evaluation"
        else:
            thinking_state = "critiquing"
            verdict_type   = action

        # Override on final turn
        if is_last:
            if result.get("_critic_unavailable") or result.get("_cognitive_failure"):
                thinking_state = "blocked"
                verdict_type   = "blocked"
            elif consensus:
                thinking_state = "consensus"
                verdict_type   = "final_consensus"
            else:
                thinking_state = "rejected"
                verdict_type   = "final_rejection"

        _write_debate_turn(
            proposal_id   = proposal_id,
            speaker       = speaker,
            action        = action,
            result        = result,
            round_num     = round_num,
            thinking_state = thinking_state,
            verdict_type  = verdict_type,
            transcript_json = json.dumps(debate_result.get("transcript", []), default=str) if is_last else None,
        )


def log_debate_to_db(proposal_id: int, debate_log: dict) -> None:
    """
    Persist the completed debate so the hub chamber can render it.

    debate_history is NOT written here — debate_log is the single source
    of truth for the chamber display. The full transcript JSON is stored
    on the final turn row via _write_debate_turns_to_log() below.

    _write_debate_turns_to_log() acts as a safety-net replay: live writes
    already happened inside run_debate() turn-by-turn, but this catches
    any turn that failed to write mid-debate.
    """
    _write_debate_turns_to_log(proposal_id, debate_log)


def _write_patch_history_inner(
    conn,
    proposal: dict,
    debate_result: dict,
    brave_result: dict,
    hitl_approved: bool,
    outcome: str,
) -> None:
    """Inner write — called inside an existing connection transaction."""
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS patch_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                applied_at REAL,
                proposal_id INTEGER,
                proposal_type TEXT,
                action TEXT,
                outcome TEXT,
                hitl_approved INTEGER DEFAULT 0,
                confidence REAL,
                brave_confirmed INTEGER,
                notes TEXT
            )
        """)
        conn.execute("""
            INSERT INTO patch_history (
                applied_at, proposal_id, proposal_type, action,
                outcome, hitl_approved, confidence, brave_confirmed, notes
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            time.time(),
            proposal.get("id"),
            proposal.get("proposal_type", ""),
            proposal.get("suggested_action", "")[:300],
            outcome,
            1 if hitl_approved else 0,
            float(debate_result.get("final_confidence", 0.0)),
            1 if brave_result.get("confirmed") else 0,
            f"rounds={debate_result.get('rounds',0)} "
            f"objections={len(debate_result.get('final_objections',[]))}",
        ))
    except Exception as e:
        log.warning("_write_patch_history_inner failed: %s", e)


# ═════════════════════════════════════════════════════════════════════════════
# ORACLE — EXTERNAL EVIDENCE LAYER
# ═════════════════════════════════════════════════════════════════════════════

# Brave API usage tracker — dual 24h + hourly budget enforcement
# 750 searches over 21 days = 35.71/day sustainable target
# Primary cap: 35/day rolling 24h window
# Secondary cap: 5/hour to prevent burst (35/day ÷ 7 active hrs)
_brave_call_times: list = []
# Brave budget: 35/day baseline, 70/day aggressive mode (1000/month ÷ 30 = 33/day safe)
# Config-driven: BRAVE_DAILY_CAP in system_config overrides this default
# Alert fires when >75% of daily budget consumed
BRAVE_DAILY_CAP  = int(get_config_value("BRAVE_DAILY_CAP", "35"))   # 35 baseline, 70 aggressive
BRAVE_HOURLY_CAP = max(3, BRAVE_DAILY_CAP // 7)  # ~5/hr at 35/day, ~10/hr at 70/day
BRAVE_MONTHLY_CAP = 1000  # hard monthly ceiling — never exceed this
BRAVE_ALERT_AT_PCT = 0.75  # alert when 75% of daily budget consumed

def brave_verify(proposal: dict, debate_log: dict) -> dict:
    global _brave_call_times
    if not BRAVE_KEY:
        return {
            "confirmed": None,
            "evidence_snippets": ["Oracle: external evidence not configured — skipping"],
            "search_query": "",
            "skipped": True,
        }

    # Dual rate limit — 24h rolling daily cap + hourly burst guard
    _now = time.time()
    _brave_call_times = [t for t in _brave_call_times if _now - t < 86400]  # rolling 24h window
    _last_hour = [t for t in _brave_call_times if _now - t < 3600]
    # Daily cap (primary)
    # Monthly cap check
    _monthly_calls = _count_brave_calls(days=30)
    if _monthly_calls >= BRAVE_MONTHLY_CAP:
        log.warning("ORACLE: MONTHLY CAP REACHED (%d/%d) — Brave disabled until next month", _monthly_calls, BRAVE_MONTHLY_CAP)
        return {"confirmed": None, "evidence_snippets": [f"MONTHLY CAP {_monthly_calls}/{BRAVE_MONTHLY_CAP} — Brave disabled"], "search_query": "", "skipped": True, "skip_reason": "monthly_cap"}

    # Alert at 75% of daily budget
    _today_calls = len(_brave_call_times)
    if _today_calls >= int(BRAVE_DAILY_CAP * BRAVE_ALERT_AT_PCT) and _today_calls < BRAVE_DAILY_CAP:
        log.warning("ORACLE: Brave budget at %d%% (%d/%d today) — approaching daily limit",
                    int(_today_calls/BRAVE_DAILY_CAP*100), _today_calls, BRAVE_DAILY_CAP)
        try:
            with get_connection() as _ac:
                _ac.execute("INSERT INTO cognition_log(stage,token,message,confidence,timestamp) VALUES(?,?,?,?,?)",
                    ("FORGE_BUDGET", "brave", f"Budget alert: {_today_calls}/{BRAVE_DAILY_CAP} Brave calls today ({_monthly_calls}/{BRAVE_MONTHLY_CAP} this month)", 0.9, time.time()))
                _ac.commit()
        except Exception: pass

    if len(_brave_call_times) >= BRAVE_DAILY_CAP:
        log.warning(
            "ORACLE: daily cap reached (%d/%d calls in last 24h) — entering deep memory mode",
            len(_brave_call_times), BRAVE_DAILY_CAP,
        )
        return {
            "confirmed": None,
            "evidence_snippets": [f"Sensory bandwidth exhausted. Entering deep memory cache mode. ({BRAVE_DAILY_CAP}/day limit reached)"],
            "search_query": "",
            "skipped": True,
            "skip_reason": "daily_cap",
        }
    # Hourly burst guard (secondary)
    if len(_last_hour) >= BRAVE_HOURLY_CAP:
        log.warning(
            "ORACLE: hourly burst guard (%d/%d calls in last hour) — skipping",
            len(_last_hour), BRAVE_HOURLY_CAP,
        )
        return {
            "confirmed": None,
            "evidence_snippets": [f"Oracle: hourly burst limit ({BRAVE_HOURLY_CAP}/hr). Daily remaining: {BRAVE_DAILY_CAP - len(_brave_call_times)}."],
            "search_query": "",
            "skipped": True,
            "skip_reason": "hourly_burst",
        }
    _brave_call_times.append(_now)
    log.info("ORACLE: search %d/%d today, %d/%d this hour", len(_brave_call_times), BRAVE_DAILY_CAP, len(_last_hour)+1, BRAVE_HOURLY_CAP)

    action = proposal.get("suggested_action", "")
    ptype  = proposal.get("proposal_type", "")
    m = re.search(r"Change\s+(\w+)\s+from\s+([\d.]+)\s+to\s+([\d.]+)",
                  action, re.IGNORECASE)
    if m:
        param, old_val, new_val = m.group(1), m.group(2), m.group(3)
        query = (f"pump.fun solana meme token trading "
                 f"{param.lower().replace('_',' ')} optimal 2025")
    else:
        query = (f"pump.fun solana meme token trading strategy "
                 f"improvement {ptype.lower()} 2025")

    # ── BRAVE SEARCH CACHE (6h TTL) ─────────────────────────────────────────
    # Before burning a search token, check if same query was run in last 6h.
    # Saves budget — identical proposals re-searched constantly would waste quota.
    try:
        _cache_cutoff = time.time() - 21600  # 6 hours
        with get_connection() as _cc:
            _cc.execute("""
                CREATE TABLE IF NOT EXISTS brave_search_cache (
                    query TEXT PRIMARY KEY,
                    result_json TEXT NOT NULL,
                    searched_at REAL NOT NULL
                )
            """)
            _cc.commit()
            _cached = _cc.execute(
                "SELECT result_json FROM brave_search_cache WHERE query=? AND searched_at > ?",
                (query, _cache_cutoff)
            ).fetchone()
            if _cached:
                log.info("ORACLE: cache hit for query '%s' — returning cached result", query[:60])
                _cached_result = json.loads(_cached[0])
                _cached_result["cache_hit"] = True
                return _cached_result
    except Exception as _ce:
        log.warning("ORACLE: cache lookup failed: %s — proceeding to live search", _ce)

    try:
        import requests
        resp = requests.get(
            "https://api.search.brave.com/res/v1/web/search",
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "gzip",
                "X-Subscription-Token": BRAVE_KEY,
            },
            params={"q": query},  # count removed — Brave Free plan rejects it with 422
            timeout=10,
        )
        resp.raise_for_status()
        results  = resp.json().get("web", {}).get("results", [])
        snippets = [
            f"{r.get('title','')}: {r.get('description','')[:150]}"
            for r in results[:4]
            if r.get("title") or r.get("description")
        ]
        combined = " ".join(snippets).lower()
        if m:
            direction = (["increase","higher","raise","longer","more"]
                         if float(new_val) > float(old_val)
                         else ["decrease","lower","reduce","shorter","less"])
            confirmed = sum(1 for w in direction if w in combined) >= 1
        else:
            confirmed = None

        result = {
            "confirmed": confirmed,
            "evidence_snippets": snippets,
            "search_query": query,
            "skipped": False,
        }
        # Write to 6h cache
        try:
            with get_connection() as _cw:
                _cw.execute(
                    "INSERT OR REPLACE INTO brave_search_cache (query, result_json, searched_at) VALUES (?, ?, ?)",
                    (query, json.dumps(result, default=str), time.time())
                )
                _cw.commit()
        except Exception as _cwe:
            log.warning("ORACLE: cache write failed: %s", _cwe)
        return result
    except Exception as e:
        log.warning("brave_verify failed: %s", e)
        return {
            "confirmed": None,
            "evidence_snippets": [f"Oracle: external evidence search failed: {e}"],
            "search_query": query,
            "skipped": True,
        }


# ═════════════════════════════════════════════════════════════════════════════
# TELEGRAM PUSH (proposals awaiting HITL)
# ═════════════════════════════════════════════════════════════════════════════

def _tg_post(method: str, payload: dict, timeout: int = 10) -> Optional[dict]:
    if not BOT_TOKEN:
        return None
    try:
        import requests
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/{method}",
            json=payload, timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        log.warning("Telegram %s failed: %s", method, e)
        return None


async def push_to_telegram(
    proposal: dict,
    debate_log: dict,
    brave_result: dict,
    nugget_advisory: Optional[dict] = None,
) -> bool:
    """Push proposal to Telegram operator.
    nugget_advisory is optional — included in message when four-layer escalation fired.
    """
    if not BOT_TOKEN or not OWNER_ID:
        log.warning("Telegram not configured — cannot push proposal")
        return False

    pid        = proposal.get("id", "?")
    ptype      = proposal.get("proposal_type", "?")
    ptext      = proposal.get("proposal_text", "")
    action     = proposal.get("suggested_action", "")
    rounds     = debate_log.get("rounds", 0)
    final_conf = float(debate_log.get("final_confidence", 0.0))

    brave_label = (
        f"{AGENT_EMOJIS['ORACLE']} CONFIRMED"     if brave_result.get("confirmed") is True  else
        f"{AGENT_EMOJIS['ORACLE']} NOT CONFIRMED"  if brave_result.get("confirmed") is False else
        f"{AGENT_EMOJIS['ORACLE']} NOT RUN"        if brave_result.get("skipped")            else
        f"{AGENT_EMOJIS['ORACLE']} INCONCLUSIVE"
    )
    objections = debate_log.get("final_objections", [])
    obj_text   = "\n".join(f"  • {o}" for o in objections[:3]) or "  None remaining"
    evidence   = brave_result.get("evidence_snippets", [])
    ev_text    = evidence[0][:120] if evidence else "No evidence retrieved"

    # Four-layer Nugget block — included only when escalation fired
    nugget_block = ""
    if nugget_advisory and not nugget_advisory.get("_nugget_failed"):
        nugget_block = (
            f"━━━━━━━━━━━━━━━━━━━━━━\n"
            f"{AGENT_EMOJIS['NUGGET']} *NUGGET ESCALATION AUDIT* ({AGENT_EMOJIS['ORACLE']} Oracle-informed)\n"
            f"Winner: `{nugget_advisory.get('winner','?')}` "
            f"Confidence: `{float(nugget_advisory.get('confidence',0)):.2f}`\n"
            f"Verdict: _{str(nugget_advisory.get('reason',''))[:150]}_\n"
            f"Missing: _{str(nugget_advisory.get('missing_evidence',''))[:100]}_\n"
            f"Next step: `{nugget_advisory.get('recommended_next_step','?')}`\n\n"
        )

    msg = (
        f"🧬 *SOVEREIGN PATCH PROPOSAL #{pid}*\n\n"
        f"*POLARIS proposed · IVARIS challenged · {rounds} rounds"
        f"{'  · ' + AGENT_EMOJIS['NUGGET'] + ' NUGGET' if nugget_block else ''}{'  · ' + AGENT_EMOJIS['ORACLE'] + ' ORACLE' if not brave_result.get('skipped') else ''}*\n"
        f"{AGENT_EMOJIS['ORACLE']} *ORACLE:* {brave_label}\n\n"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"*TYPE:* `{ptype}`\n"
        f"*CONFIDENCE:* `{final_conf:.2f}`\n\n"
        f"*FINDING:*\n{ptext[:300]}\n\n"
        f"*PROPOSED CHANGE:*\n`{action}`\n\n"
        f"*REMAINING OBJECTIONS:*\n{obj_text}\n\n"
        f"{AGENT_EMOJIS['ORACLE']} *ORACLE EVIDENCE:*\n_{ev_text}_\n\n"
        f"{nugget_block}"
        f"━━━━━━━━━━━━━━━━━━━━━━\n"
        f"⏸️ _Patch is HALTED pending your approval_\n"
        f"Proposal ID: `{pid}`"
    )
    keyboard = {"inline_keyboard": [[
        {"text": "✅ APPROVE", "callback_data": f"approve:{pid}"},
        {"text": "❌ REJECT",  "callback_data": f"reject:{pid}"},
    ]]}

    result = _tg_post("sendMessage", {
        "chat_id": OWNER_ID, "text": msg,
        "parse_mode": "Markdown", "reply_markup": keyboard,
    })
    if result and result.get("ok"):
        log.info("Proposal #%s pushed to Telegram", pid)
        return True
    return False


def push_to_telegram_sync(
    proposal: dict,
    debate_log: dict,
    brave_result: dict,
    nugget_advisory: Optional[dict] = None,
) -> bool:
    try:
        loop   = asyncio.new_event_loop()
        result = loop.run_until_complete(
            push_to_telegram(proposal, debate_log, brave_result, nugget_advisory)
        )
        loop.close()
        return result
    except Exception as e:
        log.error("push_to_telegram_sync failed: %s", e)
        return False


# ═════════════════════════════════════════════════════════════════════════════
# IVARIS API — provider-aware, Gemini retired.
# Runtime doctrine:
#   NVIDIA NIM → primary
#   OpenAI / Anthropic → approved fallbacks where explicitly configured
# Legacy provider labels are normalised and must never reconnect Gemini.
# ═════════════════════════════════════════════════════════════════════════════

def _call_ivaris(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 3000,
) -> Optional[str]:
    """
    IVARIS routing — NIM is PRIMARY, Anthropic is fallback.
      1. NIM Mistral Large — primary (always tried first)
      2. Anthropic (Claude) — fallback only if NIM fails
    """
    # NIM primary — always try first
    log.info("IVARIS ROUTING: provider=nim model=%s (registry)", _nim_assignment("IVARIS", "qwen/qwen3.5-397b-a17b"))
    result = _call_nim_ivaris(system_prompt, user_message, max_tokens)
    if result is not None:
        return result

    # Anthropic fallback — only if NIM failed
    _ant_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    _ant_model = str(get_config_value("IVARIS_MODEL", "claude-haiku-4-5-20251001")).strip()
    if _ant_key:
        log.warning("IVARIS: NIM failed — falling back to Anthropic/%s", _ant_model)
        return _call_anthropic_direct(system_prompt, user_message, _ant_model, max_tokens)

    log.error("IVARIS: both NIM and Anthropic unavailable")
    return None


def _call_anthropic_direct(
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int = 3000,
) -> Optional[str]:
    """IVARIS via Anthropic Messages API. Returns None on any failure so caller can fallback."""
    _ant_key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not _ant_key:
        return None
    try:
        import json as _j, urllib.request as _ur
        _pl = _j.dumps({
            "model": model,
            "max_tokens": max_tokens,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_message}],
        }).encode()
        _rq = _ur.Request(
            "https://api.anthropic.com/v1/messages",
            data=_pl,
            headers={
                "x-api-key": _ant_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            method="POST",
        )
        with _ur.urlopen(_rq, timeout=60) as _r:
            _resp = _j.loads(_r.read().decode())
            text = _resp["content"][0]["text"].strip()
        log.info("IVARIS: responded via Anthropic/%s", model)
        return text
    except Exception as e:
        err = str(e)
        # Credit exhaustion or auth failure — signal caller to fallback
        if any(x in err for x in ["529", "402", "401", "credit", "quota", "overloaded"]):
            log.warning("IVARIS Anthropic credit/auth error (%s) — NIM fallback will activate", err[:80])
        else:
            log.error("IVARIS Anthropic call failed: %s", err[:120])
        return None


def _call_nim_ivaris(
    system_prompt: str,
    user_message: str,
    max_tokens: int = 1500,
) -> Optional[str]:
    """IVARIS via NIM — model read from DB config, defaults to llama-3.1-8b-instruct."""
    _nim_key = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
    _nim_base = os.getenv("NVIDIA_NIM_BASE_URL", "https://integrate.api.nvidia.com/v1").strip()
    if not _nim_key:
        log.error("IVARIS: NVIDIA_NIM_API_KEY not set — IVARIS unavailable")
        return None
    # Read model from DB config so it can be changed without redeploying
    _nim_model = _nim_assignment("IVARIS", "qwen/qwen3.5-397b-a17b")
    try:
        import json as _j, urllib.request as _ur
        _pl = _j.dumps({
            "model": _nim_model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.4,
        }).encode()
        _rq = _ur.Request(
            _nim_base + "/chat/completions",
            data=_pl,
            headers={"Authorization": f"Bearer {_nim_key}",
                     "Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(_rq, timeout=90) as _r:
            text = _j.loads(_r.read().decode())["choices"][0]["message"]["content"].strip()
        log.info("IVARIS: responded via NIM/%s", _nim_model)
        return text
    except Exception as e:
        log.error("IVARIS NIM call failed: %s", e)
        return None


def _call_anthropic(
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int = 1500,
) -> Optional[str]:
    """Legacy alias — routes through NIM for backward compatibility."""
    return _call_nim_ivaris(system_prompt, user_message, max_tokens)


def _call_gemini(
    system_prompt: str,
    user_message: str,
    model: str,
    max_tokens: int = 1500,
) -> Optional[str]:
    """Retired compatibility alias. Never imports Google/Gemini; routes through NIM."""
    log.warning("Legacy _call_gemini alias invoked; routing through approved NIM provider")
    return _call_nim_ivaris(system_prompt, user_message, max_tokens)


def _parse_json_response(text: str) -> Optional[dict]:
    """
    PATCH 5 — Markdown-proof JSON parser.
    Strips triple-backtick fences and 'json' identifiers so agent communication
    from Gemini, Claude, or GPT never crashes the governor parser.
    Handles all variants: ```json{...}```, ``` {...} ```, ```\n{...}\n```.
    """
    if not text:
        return None
    # Strip Gemini/Claude thinking tokens
    text = re.sub(r"<think>[\s\S]*?</think>", "", text,
                  flags=re.IGNORECASE).strip()
    if not text:
        return None

    # Truncation recovery — if JSON cut off mid-response, close open braces
    if '{' in text and not text.rstrip().endswith('}'):
        _o = text.count('{'); _c = text.count('}')
        if _o > _c:
            text = text.rstrip() + '}' * (_o - _c)

    # PATCH 5: Strip all markdown code fence variants before attempting parse
    text_stripped = re.sub(r"```[a-zA-Z0-9]*\s*", "", text)
    text_stripped = text_stripped.replace("```", "").strip()

    # Try direct parse on stripped text first (handles fenced JSON cleanly)
    try:
        return json.loads(text_stripped.strip(), strict=False)
    except Exception:
        pass

    # Try direct parse on original text (handles unfenced JSON)
    try:
        return json.loads(text.strip(), strict=False)
    except Exception:
        pass

    # Extract first JSON object from either stripped or original text
    try:
        m = re.search(r"```(?:json)?\s*([\s\S]+?)\s*```", text)
        if m:
            return json.loads(m.group(1), strict=False)
        m = re.search(r"\{[\s\S]+\}", text_stripped)
        if m:
            return json.loads(m.group(0), strict=False)
        m = re.search(r"\{[\s\S]+\}", text)
        if m:
            return json.loads(m.group(0), strict=False)
    except Exception:
        pass
    return None


def _ivaris_critique(proposal: dict, trade_context: dict) -> dict:
    """
    Call IVARIS (Gemini) to critique a proposal.
    Applies both API-block detection and cognitive integrity guard.
    Returns a safe blocking verdict on any failure mode.
    """
    ptype   = proposal.get("proposal_type", "UNKNOWN")
    ptext   = proposal.get("proposal_text", "")
    action  = proposal.get("suggested_action", "")
    conf    = float(proposal.get("confidence", 0.0))
    metrics = proposal.get("metrics_json", "{}")
    if isinstance(metrics, str):
        try:
            metrics = json.loads(metrics)
        except Exception:
            metrics = {}

    feedback_summary = trade_context.get("proposal_feedback", {}).get(
        "summary", "No history.")
    feedback_history = trade_context.get("proposal_feedback", {}).get(
        "history", [])

    message = f"""POLARIS has submitted this proposal:

PROPOSAL TYPE: {ptype}
CONFIDENCE: {conf:.2f}
REASONING: {ptext}
PROPOSED ACTION: {action}
SUPPORTING METRICS: {json.dumps(metrics, indent=2)}

RECENT TRADE CONTEXT:
- Total trades: {trade_context.get('total_trades', 0)}
- Win rate: {trade_context.get('win_rate', 0):.1f}%
- Average PnL: ${trade_context.get('avg_pnl', 0):.4f}
- SL rate: {trade_context.get('sl_rate', 0):.1%}
- Sample size: {trade_context.get('sample_size', 0)} trades

POLARIS PROPOSAL TRACK RECORD:
{json.dumps(feedback_history, indent=2)}
TRACK RECORD SUMMARY: {feedback_summary}

Analyse this proposal. Consider POLARIS's track record.
If she has a history of rolled-back proposals, weight your objections accordingly.
Output JSON."""

    # Mode-aware critique context injection
    _d_mode = proposal.get("_debate_mode", DebateMode.CODE_FIRST)
    _mode_rubric_map = {
        DebateMode.RESEARCH_FIRST: (
            "MODE: RESEARCH_FIRST\n"
            "CRITIQUE FOCUS: Data quality, source reliability, evidence completeness.\n"
            "DO NOT reject for missing code. Reject for missing evidence, weak confidence, unverifiable claims, or survivorship bias.\n"
            "Ask: Are findings corroborated? Is data fresh? Are gaps acknowledged?"
        ),
        DebateMode.DESIGN_FIRST: (
            "MODE: DESIGN_FIRST\n"
            "CRITIQUE FOCUS: Design coherence, implementability, user value.\n"
            "Ask: Does this fit Streamlit constraints? Is layout hierarchy logical? Does it preserve machine aesthetic?"
        ),
        DebateMode.AUDIT_FIRST: (
            "MODE: AUDIT_FIRST\n"
            "CRITIQUE FOCUS: Root cause validity, diagnosis precision.\n"
            "Ask: Is the diagnosis supported by telemetry? Are alternatives ruled out? Is the fix targeted?"
        ),
        DebateMode.CODE_FIRST: (
            "MODE: CODE_FIRST\n"
            "CRITIQUE FOCUS: Code correctness, side effects, rollback path.\n"
            "Ask: Does this patch solve the stated problem? Are regressions possible?"
        ),
    }
    _mode_context = _mode_rubric_map.get(_d_mode, _mode_rubric_map[DebateMode.CODE_FIRST])
    message = _mode_context + "\n\n" + message

    log.info("IVARIS critiquing: %s mode=%s", ptype, _d_mode)
    _ivaris_budget_record()
    _budget_count, _budget_status = _ivaris_budget_status()
    log.info("IVARIS budget: %d calls in last hour (status=%s, soft=%d, hard=%d)",
             _budget_count, _budget_status, IVARIS_SOFT_CAP_PER_HOUR, IVARIS_HARD_CAP_PER_HOUR)
    _active_system_prompt = _inject_mission_lock(IVARIS_SYSTEM_PROMPT, ptype)
    if _active_system_prompt is not IVARIS_SYSTEM_PROMPT:
        log.info("TOPIC LOCK: mission lock injected for proposal_type=%s", ptype)
    text = _call_ivaris(_active_system_prompt, message)

    if not text:
        _provider = str(get_config_value("IVARIS_PROVIDER", "nim")).strip().lower()
        _ant_key  = bool(os.getenv("ANTHROPIC_API_KEY", "").strip())
        _nim_key  = bool(os.getenv("NVIDIA_NIM_API_KEY", "").strip())
        if not _ant_key and not _nim_key:
            _cause = "ANTHROPIC_API_KEY and NVIDIA_NIM_API_KEY both missing"
        elif not _ant_key:
            _cause = "ANTHROPIC_API_KEY missing — NIM fallback also failed (check NIM key/quota)"
        else:
            _cause = f"API call returned no text via {_provider} (check logs above)"
        log.error("IVARIS _ivaris_critique: aborting — %s", _cause)
        return {
            "consensus": False, "confidence": 0.0,
            "objections": [f"IVARIS unavailable: {_cause}"],
            "rebuttals_needed": [], "verdict": f"Blocked: {_cause}",
            "safe_to_proceed": False,
            "_critic_unavailable": True,
        }

    # DIAG: raw Gemini output before any parsing
    log.warning("IVARIS FINAL RAW: %s", text[:500])

    # Fix 2: topic lock — hard fail-closed BEFORE parsing
    # If SYSTEM_REPAIR response doesn't address the pricing pipeline, treat as failure.
    # Checked on raw text so a JSON-wrapped off-topic response is also caught.
    if not _is_response_on_topic(text, ptype):
        log.error(
            "IVARIS _ivaris_critique: topic lock failed for %s — "
            "response does not address pricing pipeline. Blocking.",
            ptype,
        )
        return {
            "consensus": False, "confidence": 0.0,
            "objections": ["TOPIC LOCK: IVARIS response did not address pricing pipeline — treating as failure"],
            "rebuttals_needed": [], "verdict": "Blocked: topic lock failure",
            "safe_to_proceed": False,
            "_cognitive_failure": True,
            "_topic_lock_failed": True,
        }

    parsed = _parse_json_response(text)

    # Could not parse JSON — log the raw text so the parser failure is diagnosable
    if not parsed:
        log.error(
            "IVARIS _ivaris_critique: JSON parse failed. "
            "Raw response (first 500 chars): %s", text[:500]
        )
        return {
            "consensus": False, "confidence": 0.0,
            "objections": [f"IVARIS parse failure — raw text (first 200): {text[:200]}"],
            "rebuttals_needed": [], "verdict": "Blocked: JSON parse failure",
            "safe_to_proceed": False,
            "_cognitive_failure": True,
        }

    # DIAG: parsed dict before any guard checks
    log.warning("IVARIS PARSED: %s", parsed)

    # API-block string check (fast path for known Gemini API-down response)
    if _is_ivaris_api_blocked(parsed):
        log.error(
            "IVARIS _ivaris_critique: API-unavailable string detected in parsed response. "
            "verdict=%s objections=%s",
            parsed.get("verdict"), parsed.get("objections"),
        )
        return {
            "consensus": False, "confidence": 0.0,
            "objections": ["Gemini API unavailable — blocking for safety"],
            "rebuttals_needed": [], "verdict": "Blocked: API unavailable",
            "safe_to_proceed": False,
            "_critic_unavailable": True,
        }

    # Cognitive integrity check (Gemini hardening — catches safety refusals,
    # placeholder echo, void responses, HTTP error bleed-through)
    # Only applied when consensus=False — if IVARIS approves, no need to
    # validate the rejection quality.
    if not parsed.get("consensus"):
        integrity_ok, integrity_reason = _assert_ivaris_response_integrity(parsed)
        if not integrity_ok:
            log.error(
                "IVARIS _ivaris_critique: integrity guard rejected response. "
                "reason=%s | verdict=%s | objections=%s | raw_parsed=%s",
                integrity_reason,
                parsed.get("verdict"),
                parsed.get("objections"),
                str(parsed)[:300],
            )
            return {
                "consensus": False, "confidence": 0.0,
                "objections": [f"IVARIS integrity guard: {integrity_reason}"],
                "rebuttals_needed": [], "verdict": f"Blocked: {integrity_reason}",
                "safe_to_proceed": False,
                "_cognitive_failure": True,
            }

    log.info("IVARIS verdict: consensus=%s confidence=%.2f",
             parsed.get("consensus"), float(parsed.get("confidence", 0)))
    return parsed


def _ivaris_evaluate_rebuttal(
    original_proposal: dict,
    ivaris_critique: dict,
    polaris_rebuttal: dict,
    trade_context: dict,
    oracle_result: Optional[dict] = None,
) -> dict:
    # Oracle evidence injected into re-evaluation prompt so IVARIS
    # can weigh external grounding when assessing POLARIS's rebuttal.
    oracle_block = ""
    if oracle_result and not oracle_result.get("skipped"):
        snips = (oracle_result.get("evidence_snippets") or [])[:3]
        oracle_block = (
            f"\nORACLE EXTERNAL EVIDENCE (confirmed={oracle_result.get('confirmed')}):\n"
            + "\n".join(f"  - {s}" for s in snips)
            + "\n"
        )

    message = f"""POLARIS has responded to your objections.

YOUR ORIGINAL OBJECTIONS:
{json.dumps(ivaris_critique.get('objections', []), indent=2)}

POLARIS REBUTTAL:
{json.dumps(polaris_rebuttal.get('addressed_objections', {}), indent=2)}

PROPOSAL ADJUSTED: {polaris_rebuttal.get('proposal_adjusted', False)}
NEW ACTION: {polaris_rebuttal.get('adjusted_action', 'unchanged')}
POLARIS CONFIDENCE: {float(polaris_rebuttal.get('confidence_in_proposal', 0)):.2f}
{oracle_block}
TRADE CONTEXT:
- Win rate: {trade_context.get('win_rate', 0):.1f}%
- Sample size: {trade_context.get('sample_size', 0)} trades

Have your objections been adequately addressed? Output updated JSON verdict."""

    log.info("IVARIS evaluating POLARIS rebuttal")
    # Budget: rebuttal evaluation counts against the same hourly cap
    _ivaris_budget_record()
    text   = _call_ivaris(IVARIS_SYSTEM_PROMPT, message)
    parsed = _parse_json_response(text) if text else None
    return parsed if parsed else ivaris_critique


def _get_polaris_rebuttal(
    proposal: dict,
    ivaris_critique: dict,
    trade_context: dict,
    oracle_result: Optional[dict] = None,
    nugget_verdict: Optional[dict] = None,
) -> dict:
    """POLARIS rebuts via OpenAI — receives full council context (IVARIS + Oracle + Nugget)."""
    # Oracle block
    oracle_block = ""
    if oracle_result and not oracle_result.get("skipped"):
        snips = (oracle_result.get("evidence_snippets") or [])[:3]
        oracle_block = (
            f"\n{AGENT_EMOJIS['ORACLE']} ORACLE EVIDENCE (confirmed={oracle_result.get('confirmed')}):\n"
            + "\n".join(f"  - {s}" for s in snips) + "\n"
        )
    # Nugget block
    nugget_block = ""
    if nugget_verdict and not nugget_verdict.get("_nugget_failed"):
        nugget_block = (
            f"\n{AGENT_EMOJIS['NUGGET']} NUGGET AUDIT: winner={nugget_verdict.get('winner','?')} "
            f"conf={float(nugget_verdict.get('confidence',0)):.2f} "
            f"| {str(nugget_verdict.get('reason',''))[:120]}\n"
        )
    log.info(
        "POLARIS REBUTTAL: routing to OpenAI gpt-4o-mini "
        "(proposal_type=%s, ivaris_confidence=%.2f)",
        proposal.get("proposal_type", "?"),
        float(ivaris_critique.get("confidence", 0)),
    )
    try:
        import requests
        _round_num = ivaris_critique.get("_round_num", 1)
        _rl = f"ROUND {_round_num}" if _round_num else "REBUTTAL"
        message = f"""COUNCIL DEBATE — {_rl}

YOUR PROPOSAL: {proposal.get('proposal_text', '')}
PREVIOUS SUBMISSION: {str(proposal.get('suggested_action', ''))[:400]}

{AGENT_EMOJIS['IVARIS']} IVARIS CRITIQUE:
  Verdict: {ivaris_critique.get('verdict', '')}
  Objections: {json.dumps(ivaris_critique.get('objections', []), indent=2)}
  Conditions: {json.dumps(ivaris_critique.get('conditions_of_approval', []), indent=2)}

{oracle_block}{nugget_block}
ROUND PROTOCOL:
  Round 1: Paste VERBATIM broken _write_mtm(). Show fix inline.
  Round 2: Name what IVARIS got RIGHT. Submit evolved code fixing every issue.
  Round 3: Final patch. State filename::function in summary.

adjusted_action AND code_block MUST be complete Python. No prose. Output JSON only."""

        # Route via centralised model router (escalates to gpt-5.4-mini for debates).
        try:
            from services.llm_client import polaris_complete
            _router_result = polaris_complete(
                POLARIS_REBUTTAL_PROMPT, message,
                task_type="logic_battle",
                risk_level="high",
                stalemate=True,
                max_tokens=2500,
                temperature=1.0,
            )
            if _router_result and _router_result.get("text"):
                text   = _router_result["text"]
                parsed = _parse_json_response(text)
                if parsed:
                    return parsed
        except ImportError:
            pass  # fall through to legacy direct call below

        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {OPENAI_KEY}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-5.4",
                "max_completion_tokens": 2500,
                "response_format": {"type": "json_object"},
                "messages": [
                    {"role": "system", "content": POLARIS_REBUTTAL_PROMPT},
                    {"role": "user",   "content": message},
                ],
            },
            timeout=30,
        )
        resp.raise_for_status()
        text   = resp.json()["choices"][0]["message"]["content"]
        parsed = _parse_json_response(text)
        if parsed:
            return parsed
    except Exception as e:
        log.error("POLARIS rebuttal (OpenAI) failed: %s", e)

    return {
        "addressed_objections": {},
        "proposal_adjusted": False,
        "adjusted_action": proposal.get("suggested_action", ""),
        "confidence_in_proposal": 0.5,
        "summary": "Rebuttal failed",
    }


# ═════════════════════════════════════════════════════════════════════════════
# CORE DEBATE PROTOCOL — FOUR-VOICE RESEARCH COUNCIL
# ═════════════════════════════════════════════════════════════════════════════
#
# All four voices engage from round zero:
#
#   Round 0:
#     1. IVARIS    — adversarial critique (Anthropic)
#     2. ORACLE    — external evidence search (Brave)
#     3. NUGGET    — independent audit of IVARIS + Oracle (Gemini)
#     4. State written to DB; all three results inform POLARIS's rebuttal
#
#   Rounds 1-N:
#     1. POLARIS   — rebuts with full council context (OpenAI)
#     2. IVARIS    — re-evaluates (Oracle evidence injected into prompt)
#     3. NUGGET    — updates audit score from new rebuttal
#     4. Consensus check: IVARIS AND Nugget must both agree
#
#   Consensus requires:
#     - IVARIS:  consensus=True AND confidence >= CONSENSUS_FLOOR (0.75)
#     - NUGGET:  winner="POLARIS" AND confidence >= NUGGET_CONSENSUS_FLOOR (0.65)
#     - ORACLE:  confirmed != False (not actively contradicting)
#
#   Governor retains final authority. run_cycle() is unchanged.
# ═════════════════════════════════════════════════════════════════════════════

NUGGET_CONSENSUS_FLOOR = 0.65   # Nugget confidence required for four-voice consensus


def _nugget_round_audit(
    proposal: dict,
    ivaris_verdict: dict,
    oracle_result: dict,
    polaris_rebuttal: Optional[dict],
    round_num: int,
) -> dict:
    """
    Call Nugget for a single-round audit verdict.
    Used every round — not just on escalation.
    Returns compact advisory dict or a safe fallback on failure.
    """
    _key = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
    if not _key:
        return {"winner": "INCONCLUSIVE", "confidence": 0.0,
                "reason": "Nugget unavailable: NVIDIA_NIM_API_KEY not set",
                "recommended_next_step": "defer", "_nugget_failed": True}

    ptype   = proposal.get("proposal_type", "UNKNOWN")
    ptext   = proposal.get("proposal_text", "")[:400]
    action  = proposal.get("suggested_action", "")[:200]
    p_conf  = float(proposal.get("confidence") or 0.0)
    obj     = ivaris_verdict.get("objections", [])
    i_conf  = float(ivaris_verdict.get("confidence") or 0.0)
    i_verd  = str(ivaris_verdict.get("verdict") or "")[:200]
    oracle_snip = (oracle_result.get("evidence_snippets") or [])
    oracle_conf = oracle_result.get("confirmed")
    oracle_q    = oracle_result.get("search_query", "")

    rebuttal_block = ""
    if polaris_rebuttal:
        rebuttal_block = f"""
POLARIS REBUTTAL (this round):
Summary: {str(polaris_rebuttal.get('summary',''))[:200]}
Adjusted: {polaris_rebuttal.get('proposal_adjusted', False)}
"""

    user_message = f"""ROUND {round_num} AUDIT

PROPOSAL TYPE: {ptype} | POLARIS CONFIDENCE: {p_conf:.2f}
PROPOSAL: {ptext}
ACTION: {action}
{rebuttal_block}
IVARIS VERDICT (round {round_num}):
Confidence: {i_conf:.2f}
Verdict: {i_verd}
Objections:
{json.dumps(obj[:4], indent=2)}

ORACLE EVIDENCE (confirmed={oracle_conf}):
Query: {oracle_q}
{chr(10).join(f'  - {s}' for s in oracle_snip[:4]) if oracle_snip else '  Not available'}

Assess whether POLARIS has adequately addressed IVARIS's objections given the Oracle evidence.
Output JSON only."""

    try:
        import json as _j2, urllib.request as _ur2
        _nk2 = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
        _models = []
        for _m in (NUGGET_MODEL, NUGGET_FALLBACK_MODEL):
            if _m and _m not in _models:
                _models.append(_m)
        text = ""
        _last_err = ""
        for _model in _models:
            try:
                _pl2 = _j2.dumps({
                    "model": _model,
                    "messages": [
                        {"role": "system", "content": NUGGET_SYSTEM_PROMPT},
                        {"role": "user",   "content": user_message},
                    ],
                    "max_tokens": 400, "temperature": 0.2,
                }).encode()
                _rq2 = _ur2.Request(
                    "https://integrate.api.nvidia.com/v1/chat/completions",
                    data=_pl2,
                    headers={"Authorization": f"Bearer {_nk2}", "Content-Type": "application/json"},
                    method="POST",
                )
                with _ur2.urlopen(_rq2, timeout=60) as _resp2:
                    text = _j2.loads(_resp2.read().decode())["choices"][0]["message"]["content"].strip()
                if text:
                    break
            except Exception as _ne:
                _last_err = f"{_model}: {str(_ne)[:140]}"
                log.error("NUGGET round %d model %s error: %s", round_num, _model, _ne)
                continue
        parsed = _parse_json_response(text) if text else None
        if not parsed:
            return {"winner": "INCONCLUSIVE", "confidence": 0.0,
                    "reason": "Nugget parse failed" + (f" ({_last_err})" if _last_err else ""),
                    "recommended_next_step": "defer", "_nugget_failed": True}
        # Normalise winner
        parsed["winner"] = str(parsed.get("winner", "INCONCLUSIVE")).upper()
        try:
            parsed["confidence"] = float(parsed.get("confidence") or 0.0)
        except Exception:
            parsed["confidence"] = 0.0
        log.info(
            "NUGGET round %d: winner=%s confidence=%.2f reason=%s",
            round_num, parsed.get("winner"), parsed.get("confidence"),
            str(parsed.get("reason", ""))[:80],
        )
        return parsed
    except Exception as e:
        log.error("NUGGET round %d error: %s", round_num, e)
        return {"winner": "INCONCLUSIVE", "confidence": 0.0,
                "reason": str(e)[:100], "recommended_next_step": "defer",
                "_nugget_failed": True}



# ═════════════════════════════════════════════════════════════════════════════
# V2 HEX-CORTEX HELPERS — first-class RHIZA + AXON turns
# ═════════════════════════════════════════════════════════════════════════════

HEX_CORTEX_SEQUENCE = ["POLARIS", "ORACLE", "RHIZA", "IVARIS", "NUGGET", "AXON", "POLARIS_FINAL"]


def _safe_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except Exception:
        return default


def _parse_metrics_json(proposal: dict) -> dict:
    raw = proposal.get("metrics_json") or {}
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw.strip():
        try:
            return json.loads(raw)
        except Exception:
            return {}
    return {}


def _infer_source_target(proposal: dict) -> tuple[str, str]:
    """Infer source file/function for Code-First initiation."""
    metrics = _parse_metrics_json(proposal)
    source_file = str(
        metrics.get("source_file") or metrics.get("file_path") or metrics.get("target_file") or ""
    ).strip()
    func_name = str(
        metrics.get("func_name") or metrics.get("function_name") or metrics.get("symbol") or ""
    ).strip()

    blob = "\n".join(str(proposal.get(k) or "") for k in ("proposal_text", "suggested_action"))
    if not source_file:
        m = re.search(r"([A-Za-z0-9_./\\-]+[.]py)(?:::+([A-Za-z_][A-Za-z0-9_]*))?", blob)
        if m:
            source_file = m.group(1).replace("\\", "/")
            if not func_name and m.group(2):
                func_name = m.group(2)
    if source_file and not source_file.startswith(("/", "services/", "core/", "ui/", "components/", "ops/", "launch/")):
        candidate = BASE_DIR / source_file
        if not candidate.exists() and (BASE_DIR / "services" / source_file).exists():
            source_file = f"services/{source_file}"
    return source_file, func_name


def _build_current_state_context(proposal: dict) -> dict:
    """
    Code-first initiation context. If a source target is present, AST-extract it.
    If not, preserve the proposal as a minimal CURRENT_STATE so the debate can
    still run, but mark it as not source-grounded.
    """
    source_file, func_name = _infer_source_target(proposal)
    if source_file:
        target = str((BASE_DIR / source_file).resolve()) if not Path(source_file).is_absolute() else source_file
        code = extract_live_code(target, func_name or None)
        grounded = bool(code and not str(code).startswith("# Error") and "not found" not in str(code).lower())
        return {
            "source_file": source_file,
            "func_name": func_name,
            "current_state": code[:12000],
            "source_grounded": grounded,
            "error": None if grounded else str(code)[:300],
        }
    return {
        "source_file": "",
        "func_name": "",
        "current_state": (
            "# NO SOURCE TARGET PROVIDED\n"
            f"proposal_type = {proposal.get('proposal_type')!r}\n"
            f"proposal_text = {proposal.get('proposal_text')!r}\n"
            f"suggested_action = {proposal.get('suggested_action')!r}\n"
        ),
        "source_grounded": False,
        "error": "No source_file/func_name found in proposal metrics or text.",
    }


def _write_hex_turn(proposal_id: int, speaker: str, action: str, result: dict,
                    round_num: int, thinking_state: str = "evaluating",
                    verdict_type: str | None = None,
                    transcript_json: str | None = None) -> dict:
    """Append + live-write a Hex-Cortex turn with consistent metadata."""
    result = result or {}
    if verdict_type is None:
        verdict_type = action
    _write_debate_turn(
        proposal_id=proposal_id,
        speaker=speaker,
        action=action,
        result=result,
        round_num=round_num,
        thinking_state=thinking_state,
        verdict_type=verdict_type,
        transcript_json=transcript_json,
    )
    return {"round": round_num, "speaker": str(speaker).upper(), "action": action, "result": result}


def _rhiza_narrative_turn(proposal: dict, oracle_result: dict, current_state: dict,
                          round_num: int = 0) -> dict:
    """RHIZA/xAI as mandatory narrative synthesis turn. Fail-open."""
    ptype = str(proposal.get("proposal_type") or "UNKNOWN")
    ptext = str(proposal.get("proposal_text") or "")[:700]
    action = str(proposal.get("suggested_action") or "")[:900]
    oracle_snips = oracle_result.get("evidence_snippets") or []
    source = current_state.get("source_file") or "not-source-grounded"
    if not XAI_API_KEY:
        return {
            "summary": f"RHIZA unavailable: XAI_API_KEY missing. Source={source}. Debate continues without narrative synthesis.",
            "confidence": 0.0,
            "source": source,
            "_rhiza_failed": True,
        }
    try:
        import requests as _rq
        resp = _rq.post(
            "https://api.x.ai/v1/chat/completions",
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {XAI_API_KEY}"},
            json={
                "model": get_models_for_round(max(1, round_num)).get("grok", "grok-2-latest"),
                "messages": [
                    {"role": "system", "content": "You are RHIZA — the synthesis intelligence of the Sentinuity council. You read the POLARIS proposal and IVARIS critique together and find the path neither has seen. You name what POLARIS is trying to achieve, what IVARIS is protecting, the core tension between them, and a concrete synthesis path that satisfies both. You are activated when the debate needs a bridge, not a referee. Return compact JSON with: polaris_intent, ivaris_concerns, core_tension, synthesized_path, why_this_resolves_both, confidence."},
                    {"role": "user", "content": json.dumps({
                        "proposal_type": ptype,
                        "proposal_text": ptext,
                        "suggested_action": action,
                        "source_file": source,
                        "source_grounded": current_state.get("source_grounded"),
                        "oracle_confirmed": oracle_result.get("confirmed"),
                        "oracle_evidence": oracle_snips[:4],
                    }, default=str)},
                ],
                "max_tokens": 450,
                "temperature": 0.45,
            },
            timeout=30,  # increased — xAI API needs more headroom
        )
        if resp.status_code != 200:
            return {"summary": f"RHIZA HTTP {resp.status_code}; debate continues.", "confidence": 0.0, "_rhiza_failed": True}
        choices = resp.json().get("choices") or []
        text = str(choices[0].get("message", {}).get("content", "")).strip() if choices else ""
        parsed = _parse_json_response(text) if text else None
        if parsed:
            parsed.setdefault("summary", parsed.get("narrative") or parsed.get("why_it_matters") or "RHIZA synthesized narrative context.")
            parsed["confidence"] = _safe_float(parsed.get("confidence"), 0.5)
            return parsed
        return {"summary": text[:500] if text else "RHIZA returned empty narrative.", "confidence": 0.35, "raw": text[:900], "_rhiza_parse_fallback": True}
    except Exception as e:
        return {"summary": f"RHIZA failed: {e}. Debate continues.", "confidence": 0.0, "_rhiza_failed": True}


def _extract_candidate_code(proposal: dict, polaris_turn: dict | None = None) -> str:
    if polaris_turn:
        for key in ("code_block", "adjusted_action", "final_code", "artifact"):
            val = polaris_turn.get(key)
            if isinstance(val, str) and val.strip():
                return val.strip()
    action = str(proposal.get("suggested_action") or "")
    m = re.search(r"```(?:python)?\s*([\s\S]+?)```", action, re.IGNORECASE)
    if m:
        return m.group(1).strip()
    return action.strip()


def _axon_dry_run_validation(proposal: dict, current_state: dict,
                             polaris_turn: dict | None = None,
                             round_num: int = 0) -> dict:
    """
    AXON dry-run validation. It never places trades and never mutates config.
    It validates syntax, obvious placeholder risk, and target/source grounding.
    """
    code = _extract_candidate_code(proposal, polaris_turn)
    source_file = current_state.get("source_file") or _infer_source_target(proposal)[0]
    checks: list[str] = []
    failures: list[str] = []

    if not current_state.get("source_grounded"):
        failures.append("CURRENT_STATE is not source-grounded; source target missing or extraction failed.")
    else:
        checks.append(f"CURRENT_STATE extracted from {source_file or 'target'}.")

    if not code or len(code) < 20:
        failures.append("Candidate code/action is too short for dry-run validation.")
    if "..." in code or "TODO" in code.upper() or "PLACEHOLDER" in code.upper():
        failures.append("Candidate contains placeholder/TODO/ellipsis markers.")

    # Compile only when the candidate looks like Python code rather than prose/config.
    looks_python = any(tok in code for tok in ("def ", "class ", "import ", "from ", "return ", "FORGE_COMPLETE"))
    if looks_python:
        cleaned = re.sub(r"^FORGE_COMPLETE\s*", "", code.strip(), flags=re.IGNORECASE)
        fence = re.search(r"```(?:python)?\s*([\s\S]+?)```", cleaned, re.IGNORECASE)
        if fence:
            cleaned = fence.group(1)
        try:
            compile(cleaned, source_file or "axon_dry_run.py", "exec")
            checks.append("Python candidate compiles under AXON dry-run.")
        except SyntaxError as se:
            failures.append(f"SyntaxError line {se.lineno}: {se.msg}")
        except Exception as e:
            failures.append(f"Compile check failed: {e}")
    else:
        checks.append("Candidate appears to be config/prose action; compile check skipped.")

    safe_to_stage = not failures
    return {
        "safe_to_stage": safe_to_stage,
        "confidence": 0.85 if safe_to_stage else 0.25,
        "checks": checks,
        "failures": failures,
        "source_file": source_file,
        "dry_run_only": True,
        "verdict": "AXON dry-run passed; safe to stage behind Gold Seal." if safe_to_stage else "AXON dry-run blocked staging until failures are fixed.",
    }


def _hex_consensus_ok(ivaris_verdict: dict, nugget_verdict: dict, oracle_result: dict,
                      axon_result: dict, round_num: int,
                      proposal_type: str = "") -> bool:
    """
    V2 consensus gate. For RESEARCH_NOTE and research types, Nugget network
    timeouts and AXON prose-blocks are advisory not blocking. IVARIS approval
    is always the hard gate.
    """
    _ptype = str(proposal_type or "").upper()
    _is_research = _ptype in {
        "RESEARCH_NOTE", "DOCTRINE_UPDATE", "PATTERN_OBSERVATION",
        "WALLET_INTEL", "INTELLIGENCE_BUILD",
    }

    # Research proposals only need 1 full round (IVARIS approval is sufficient).
    # Code/config changes require MIN_ROUNDS=3 for safety.
    _min = 1 if _is_research else MIN_ROUNDS
    if round_num < _min:
        return False

    ivaris_ok = (
        ivaris_verdict.get("consensus") is True
        and _safe_float(ivaris_verdict.get("confidence"), 0.0) >= CONSENSUS_FLOOR
        and not ivaris_verdict.get("_critic_unavailable")
        and not ivaris_verdict.get("_cognitive_failure")
    )

    nugget_winner = str(nugget_verdict.get("winner") or "").upper()
    nugget_timed_out = nugget_verdict.get("_nugget_failed") or nugget_winner == "INCONCLUSIVE"
    nugget_ok = (
        nugget_winner == "POLARIS"
        and _safe_float(nugget_verdict.get("confidence"), 0.0) >= NUGGET_CONSENSUS_FLOOR
        and not nugget_verdict.get("_nugget_failed")
    )
    if _is_research and nugget_timed_out:
        nugget_ok = True
        log.info("_hex_consensus_ok: Nugget INCONCLUSIVE on research — advisory pass")
    elif nugget_timed_out and str(get_config_value("NUGGET_FAIL_OPEN_ON_PROVIDER_ERROR", "1")) == "1":
        # Provider/model outage must not freeze the whole build ladder for weeks.
        # This only fail-opens Nugget when it is unavailable/inconclusive; an active
        # Nugget verdict choosing IVARIS still blocks consensus normally.
        nugget_ok = True
        log.warning("_hex_consensus_ok: Nugget unavailable — advisory pass via NUGGET_FAIL_OPEN_ON_PROVIDER_ERROR=1")

    oracle_ok = oracle_result.get("confirmed") is not False

    axon_ok = axon_result.get("safe_to_stage") is True
    if _is_research and not axon_ok:
        axon_failures = axon_result.get("failures", [])
        prose_block = any(
            "source-grounded" in f or "too short" in f
            for f in axon_failures
        )
        if prose_block:
            axon_ok = True
            log.info("_hex_consensus_ok: AXON prose block on research — advisory pass")

    return ivaris_ok and nugget_ok and oracle_ok and axon_ok


def _write_forge_complete_if_ready(proposal_id: int, proposal: dict,
                                   polaris_final: dict, axon_result: dict,
                                   round_num: int) -> None:
    """Promote a staged artifact to forge_complete while preserving Gold Seal gate."""
    if round_num < MIN_ROUNDS or not axon_result.get("safe_to_stage"):
        return
    code = _extract_candidate_code(proposal, polaris_final)
    if len(code) < 50:
        return
    try:
        import hashlib as _hl
        source_file, _func = _infer_source_target(proposal)
        summary = str(polaris_final.get("summary") or "")
        fm = re.search(r"([A-Za-z0-9_./-]+[.]py)", summary) or re.search(r"([A-Za-z0-9_./-]+[.]py)", code)
        fname = source_file or (fm.group(1) if fm else f"proposal_{proposal_id}_patch.py")
        checksum = _hl.sha256(code.encode()).hexdigest()[:12]
        with get_connection() as conn:
            conn.execute("""UPDATE polaris_proposals SET
                status='forge_complete', rewritten_code=?,
                forge_narrative=?, forge_checksum=?, forge_status='awaiting_seal'
                WHERE id=?""", (code, fname, checksum, proposal_id))
            conn.commit()
        log.info("HEX FORGE COMPLETE proposal=%s file=%s chk=%s", proposal_id, fname, checksum)
    except Exception as e:
        log.warning("HEX forge_complete write failed proposal=%s: %s", proposal_id, e)

def run_debate(proposal: dict) -> dict:
    """
    V2 Hex-Cortex debate engine.

    Mandatory sequence:
      POLARIS(AST CURRENT_STATE) → ORACLE(Facts) → RHIZA(Narrative)
      → IVARIS(Adversary) → NUGGET(Audit) → AXON(Dry-run)
      → POLARIS(Final Forge/Evolution)

    Locks:
      - MIN_ROUNDS=3 before final_consensus.
      - At least one IVARIS adversarial turn and one NUGGET audit required.
      - AXON is dry-run only; it never executes trades or applies code.
      - Model failures are written as turns and skipped where possible; they do
        not kill the debate unless the existing budget gate suppresses it.
      - Approval predicates and Gold Seal flow remain untouched downstream.
    """
    _ptype      = str(proposal.get("proposal_type") or "").upper()
    _pconf      = _safe_float(proposal.get("confidence"), 0.0)
    _is_repair  = (_ptype == "SYSTEM_REPAIR")
    proposal_id = proposal.get("id")
    _budget_count, _budget_status = _ivaris_budget_status()

    if _budget_status == "hard" and not _is_repair:
        log.warning("IVARIS budget hard lock (%d/%d/hr) — non-repair debate suppressed", _budget_count, IVARIS_HARD_CAP_PER_HOUR)
        return {
            "consensus": False, "rounds": 0, "final_confidence": 0.0,
            "final_objections": [f"IVARIS budget lock: hard cap ({_budget_count}/{IVARIS_HARD_CAP_PER_HOUR}/hr)."],
            "transcript": [], "final_proposal": proposal, "proposal_id": proposal_id,
            "_budget_suppressed": True, "_cap_hit": True,
            "critic_unavailable": False, "cognitive_failure": False,
            "topic_lock_failed": False, "_loop_detected": False,
        }

    if _budget_status == "soft" and not _is_repair and _pconf < IVARIS_SOFT_CAP_MIN_CONFIDENCE:
        log.warning("IVARIS soft cap: low-confidence proposal suppressed. type=%s conf=%.2f", _ptype, _pconf)
        return {
            "consensus": False, "rounds": 0, "final_confidence": 0.0,
            "final_objections": [f"IVARIS soft cap: conf {_pconf:.2f} < {IVARIS_SOFT_CAP_MIN_CONFIDENCE}."],
            "transcript": [], "final_proposal": proposal, "proposal_id": proposal_id,
            "_budget_suppressed": True, "_cap_hit": True,
            "critic_unavailable": False, "cognitive_failure": False,
            "topic_lock_failed": False, "_loop_detected": False,
        }

    _seen = int(proposal.get("seen_count") or 0)
    _domain = str(proposal.get("proposal_domain") or "TRADING")
    # FORGE proposals use a much higher cap — they are long-running research tasks
    # not one-shot config changes. Allow up to 10 seen_count before blocking.
    _effective_cap = MAX_IVARIS_MESSAGES_PER_PROPOSAL * 10 if _domain == "FORGE" else MAX_IVARIS_MESSAGES_PER_PROPOSAL
    if _seen * (MAX_ROUNDS + 1) >= _effective_cap:
        log.warning("DEBATE LOOP: per-proposal cap reached for proposal #%s (seen=%d)", proposal_id, _seen)
        stall = {"verdict": "Debate stalled — per-proposal cap exceeded"}
        _write_debate_turn(proposal_id=proposal_id, speaker="SYSTEM", action="loop_detection",
                           result=stall, round_num=0, thinking_state="blocked", verdict_type="loop_detected")
        return {
            "consensus": False, "rounds": 0, "final_confidence": 0.0,
            "final_objections": ["Per-proposal IVARIS message limit exceeded"],
            "transcript": [{"round": 0, "speaker": "SYSTEM", "action": "loop_detection", "result": stall}],
            "final_proposal": proposal, "proposal_id": proposal_id,
            "_loop_detected": True, "_cap_hit": True,
            "_budget_suppressed": False, "critic_unavailable": False,
            "cognitive_failure": False, "topic_lock_failed": False,
        }

    trade_context = get_trade_context()
    feedback      = get_proposal_feedback()
    trade_context = {**trade_context, "proposal_feedback": feedback}

    transcript: list[dict] = []
    consensus = False
    final_conf = 0.0
    final_obj: list = []
    round_num = 0
    _loop_detected = False
    _polaris_seen: set[str] = set()
    _critic_unavailable_seen = False
    _cognitive_failure_seen = False
    _topic_lock_failed_seen = False
    # Debate state memory — tracks trajectory across rounds
    debate_state = {
        "persistent_conflicts": [],
        "resolved_points":      [],
        "design_direction":     "",
        "polaris_intent":       str(proposal.get("proposal_text",""))[:200],
        "rounds_stalled":       0,
    }

    # ROUND 0 — Mode-aware POLARIS initiation (multi-mode architecture 2026-05-08)
    # Classify proposal type → acquire evidence → build mode-specific prompt
    _debate_mode = classify_proposal_mode(proposal)
    log.info("DEBATE MODE: %s for proposal #%s", _debate_mode, proposal_id)

    # Evidence acquisition — runs BEFORE POLARIS speaks
    _evidence_packet = _acquire_evidence(proposal, _debate_mode)

    # Build mode-aware POLARIS prompt
    _mode_prompt = _build_mode_aware_polaris_prompt(_debate_mode, proposal, _evidence_packet)

    # current_state always initialised — only CODE_FIRST populates it fully
    current_state = {"source_file": None, "func_name": None, "source_grounded": False,
                     "current_state": "", "error": None}
    if _debate_mode == DebateMode.CODE_FIRST:
        # Existing CODE-FIRST path — unchanged for code proposals
        current_state = _build_current_state_context(proposal)
        polaris_genesis = {
            "verdict": f"POLARIS {_debate_mode} INITIATION",
            "mode": _debate_mode,
            "model": get_models_for_round(1).get("polaris", "gpt-5.4"),
            "source_file": current_state.get("source_file"),
            "func_name": current_state.get("func_name"),
            "source_grounded": current_state.get("source_grounded"),
            "current_state_preview": str(current_state.get("current_state") or "")[:1200],
            "evolved_state_seed": str(proposal.get("suggested_action") or proposal.get("proposal_text") or "")[:1200],
            "error": current_state.get("error"),
        }
    else:
        # RESEARCH/DESIGN/AUDIT — evidence-first initiation
        polaris_genesis = {
            "verdict": f"POLARIS {_debate_mode} INITIATION",
            "mode": _debate_mode,
            "model": get_models_for_round(1).get("polaris", "gpt-5.4"),
            "evidence_sources": _evidence_packet.get("sources_queried", []),
            "evidence_count": len(_evidence_packet.get("findings", [])),
            "evidence_confidence": _evidence_packet.get("confidence", 0.0),
            "evidence_gaps": _evidence_packet.get("gaps", []),
            "mode_prompt": _mode_prompt[:800],
            "findings_preview": str(_evidence_packet.get("findings", []))[:600],
        }
    transcript.append(_write_hex_turn(proposal_id, "POLARIS", "current_state", polaris_genesis, 0, "evaluating", "current_state"))

    # Inject mode and evidence into debate_state for IVARIS context
    debate_state["debate_mode"] = _debate_mode
    debate_state["evidence_packet"] = _evidence_packet
    debate_state["mode_prompt"] = _mode_prompt

    # ORACLE — external evidence. Failure is represented, not fatal.
    try:
        oracle_result = brave_verify(proposal, {"proposal_id": proposal_id, "transcript": transcript})
    except Exception as e:
        oracle_result = {"confirmed": None, "evidence_snippets": [f"Oracle failed: {e}"], "search_query": "", "skipped": True, "_oracle_failed": True}
    transcript.append(_write_hex_turn(proposal_id, "ORACLE", "oracle_evidence", oracle_result, 0, "searching", "oracle_evidence"))

    # RHIZA — promoted from helper to mandatory narrative turn. Failure tolerant.
    rhiza_result = _rhiza_narrative_turn(proposal, oracle_result, current_state, 0)
    transcript.append(_write_hex_turn(proposal_id, "RHIZA", "rhiza_narrative", rhiza_result, 0, "synthesizing", "rhiza_narrative"))

    _effective_max_rounds = max(MIN_ROUNDS, MAX_ROUNDS)
    _bc, _bs = _ivaris_budget_status()
    if _bs == "soft" and not _is_repair:
        _effective_max_rounds = max(MIN_ROUNDS, min(MAX_ROUNDS, IVARIS_SOFT_CAP_MAX_ROUNDS))
        log.info("HEX: soft cap active but no-early-exit preserved — effective_max=%d", _effective_max_rounds)

    ivaris_verdict: dict = {"consensus": False, "confidence": 0.0, "objections": ["IVARIS has not yet spoken"], "verdict": "pending"}
    nugget_verdict: dict = {"winner": "INCONCLUSIVE", "confidence": 0.0, "reason": "NUGGET has not yet audited", "recommended_next_step": "defer"}
    axon_result: dict = {"safe_to_stage": False, "confidence": 0.0, "failures": ["AXON has not yet dry-run validated"], "dry_run_only": True}
    polaris_turn: dict | None = None

    for round_num in range(1, _effective_max_rounds + 1):
        log.info("HEX round %d/%d — IVARIS → NUGGET → AXON → POLARIS", round_num, _effective_max_rounds)

        # IVARIS adversarial immune system.
        try:
            if round_num == 1 or polaris_turn is None:
                proposal["_debate_mode"] = _debate_mode  # pass mode to IVARIS
                ivaris_verdict = _ivaris_critique(proposal, trade_context)
                action = "initial_critique"
            else:
                ivaris_verdict = _ivaris_evaluate_rebuttal(proposal, ivaris_verdict, polaris_turn, trade_context, oracle_result=oracle_result)
                action = "rebuttal_evaluation"
        except Exception as e:
            ivaris_verdict = {
                "consensus": False, "confidence": 0.0,
                "objections": [f"IVARIS exception: {e}"],
                "verdict": f"IVARIS failed in round {round_num}; debate continues.",
                "safe_to_proceed": False, "_critic_unavailable": True,
            }
            action = "ivaris_failed"

        _critic_unavailable_seen = _critic_unavailable_seen or bool(ivaris_verdict.get("_critic_unavailable"))
        _cognitive_failure_seen = _cognitive_failure_seen or bool(ivaris_verdict.get("_cognitive_failure"))
        _topic_lock_failed_seen = _topic_lock_failed_seen or bool(ivaris_verdict.get("_topic_lock_failed"))
        final_conf = _safe_float(ivaris_verdict.get("confidence"), 0.0)
        final_obj = ivaris_verdict.get("objections", []) or []
        transcript.append(_write_hex_turn(proposal_id, "IVARIS", action, ivaris_verdict, round_num, "critiquing" if round_num == 1 else "evaluating", action))

        # NUGGET high-fidelity Gemini-Pro audit every round.
        try:
            nugget_verdict = _nugget_round_audit(proposal, ivaris_verdict, oracle_result, polaris_turn, round_num)
        except Exception as e:
            nugget_verdict = {"winner": "INCONCLUSIVE", "confidence": 0.0, "reason": f"NUGGET exception: {e}", "recommended_next_step": "defer", "_nugget_failed": True}
        transcript.append(_write_hex_turn(proposal_id, "NUGGET", "nugget_audit", nugget_verdict, round_num, "auditing", "nugget_audit"))

        # AXON dry-run validation. Never executes trades or mutates production.
        axon_result = _axon_dry_run_validation(proposal, current_state, polaris_turn, round_num)
        transcript.append(_write_hex_turn(proposal_id, "AXON", "axon_dry_run", axon_result, round_num, "dry_run", "axon_dry_run"))

        # POLARIS evolves/final-forges after seeing all council turns.
        try:
            polaris_turn = _get_polaris_rebuttal(proposal, ivaris_verdict, trade_context, oracle_result=oracle_result, nugget_verdict=nugget_verdict)
        except Exception as e:
            polaris_turn = {
                "addressed_objections": {}, "proposal_adjusted": False,
                "adjusted_action": proposal.get("suggested_action", ""),
                "code_block": proposal.get("suggested_action", ""),
                "confidence_in_proposal": 0.0,
                "summary": f"POLARIS failed in round {round_num}: {e}",
                "_polaris_failed": True,
            }
        polaris_turn["hex_sequence"] = HEX_CORTEX_SEQUENCE
        polaris_turn["round_num"] = round_num
        polaris_turn["axon_dry_run"] = axon_result
        transcript.append(_write_hex_turn(proposal_id, "POLARIS", "final_forge" if round_num >= MIN_ROUNDS else "rebuttal", polaris_turn, round_num, "rebutting", "polaris_final" if round_num >= MIN_ROUNDS else "rebuttal"))

        # Apply Polaris adjustment into the next round candidate only; no production write.
        if polaris_turn.get("proposal_adjusted") and polaris_turn.get("adjusted_action"):
            proposal = dict(proposal)
            proposal["suggested_action"] = polaris_turn.get("adjusted_action")

        summary_key = str(polaris_turn.get("summary") or polaris_turn.get("adjusted_action") or "").strip().lower()[:500]
        if summary_key and summary_key in _polaris_seen:
            _loop_detected = True
            loop_turn = {"verdict": "Debate stalled — POLARIS repeated the same evolution summary."}
            transcript.append(_write_hex_turn(proposal_id, "SYSTEM", "loop_detection", loop_turn, round_num, "blocked", "loop_detected"))
            break
        if summary_key:
            _polaris_seen.add(summary_key)

        # Re-run AXON against the evolved Polaris code before allowing consensus.
        axon_result = _axon_dry_run_validation(proposal, current_state, polaris_turn, round_num)
        transcript.append(_write_hex_turn(proposal_id, "AXON", "axon_final_dry_run", axon_result, round_num, "dry_run", "axon_final_dry_run"))

        if _hex_consensus_ok(ivaris_verdict, nugget_verdict, oracle_result, axon_result, round_num, proposal_type=_ptype):
            consensus = True
            final_conf = _safe_float(ivaris_verdict.get("confidence"), 0.0)
            final_obj = ivaris_verdict.get("objections", []) or []
            transcript.append(_write_hex_turn(
                proposal_id, "POLARIS", "forge_complete_ready",
                {"verdict": "V2 Hex-Cortex consensus reached. Artifact promoted to forge_complete awaiting Gold Seal.",
                 "round": round_num, "final_confidence": final_conf, "axon": axon_result,
                 "nugget": nugget_verdict},
                round_num, "consensus", "final_consensus",
                transcript_json=json.dumps(transcript, default=str),
            ))
            _write_forge_complete_if_ready(proposal_id, proposal, polaris_turn, axon_result, round_num)
            break

        # Track convergence — if NUGGET says oscillating/stalled, increment counter
        if nugget_verdict.get("convergence_signal") in ("oscillating", "stalled"):
            debate_state["rounds_stalled"] += 1
        else:
            debate_state["rounds_stalled"] = max(0, debate_state["rounds_stalled"] - 1)

        # GROK synthesis trigger — after 2 stalled rounds, RHIZA finds the bridge
        if debate_state["rounds_stalled"] >= 2:
            log.info("HEX: %d stalled rounds — triggering GROK/RHIZA synthesis bridge", debate_state["rounds_stalled"])
            rhiza = _call_axon_rhiza_synthesis(proposal, {"source_file": "debate_bridge"}, oracle_result, round_num)
            if rhiza and not rhiza.get("_rhiza_failed"):
                debate_state["design_direction"] = str(rhiza.get("synthesized_path",""))[:300]
                # Inject synthesis into next POLARIS rebuttal context
                if isinstance(polaris_turn, dict):
                    polaris_turn["rhiza_synthesis"] = rhiza
                transcript.append(_write_hex_turn(proposal_id, "RHIZA", "grok_synthesis_bridge", rhiza, round_num, "synthesising", "rhiza_bridge"))
                debate_state["rounds_stalled"] = 0  # reset — give new path a chance

        # Track convergence — if NUGGET says oscillating/stalled, increment counter
        _conv = nugget_verdict.get("convergence_signal", "")
        if _conv in ("oscillating", "stalled"):
            debate_state["rounds_stalled"] += 1
        elif _conv == "converging":
            debate_state["rounds_stalled"] = max(0, debate_state["rounds_stalled"] - 1)

        # GROK/RHIZA synthesis bridge — fires after 2 stalled rounds
        if debate_state["rounds_stalled"] >= 2:
            log.info("HEX: %d stalled rounds — RHIZA synthesis bridge triggered", debate_state["rounds_stalled"])
            try:
                _rhiza = _call_axon_rhiza_synthesis(proposal, {"source_file": "debate_bridge"}, oracle_result, round_num)
                if _rhiza and not _rhiza.get("_rhiza_failed"):
                    debate_state["design_direction"] = str(_rhiza.get("synthesized_path",""))[:300]
                    if isinstance(polaris_turn, dict):
                        polaris_turn["rhiza_synthesis"] = _rhiza
                    transcript.append(_write_hex_turn(proposal_id, "RHIZA", "synthesis_bridge", _rhiza, round_num, "synthesising", "rhiza_bridge"))
                    debate_state["rounds_stalled"] = 0
            except Exception as _re:
                log.warning("RHIZA bridge failed: %s", _re)

        if round_num < MIN_ROUNDS:
            log.info("HEX: no early exit — round %d < MIN_ROUNDS=%d", round_num, MIN_ROUNDS)

    if not consensus and not _loop_detected:
        terminal = {
            "verdict": f"V2 Hex-Cortex completed {round_num} round(s) without final consensus.",
            "final_objections": final_obj,
            "ivaris_confidence": final_conf,
            "nugget": nugget_verdict,
            "axon": axon_result,
        }
        transcript.append(_write_hex_turn(
            proposal_id, "SYSTEM", "final_rejection", terminal, round_num,
            "rejected", "final_rejection", transcript_json=json.dumps(transcript, default=str),
        ))

    return {
        "consensus": consensus,
        "rounds": round_num,
        "final_confidence": final_conf,
        "final_objections": final_obj,
        "transcript": transcript,
        "final_proposal": proposal,
        "proposal_id": proposal_id,
        "oracle_result": oracle_result,
        "rhiza_result": rhiza_result,
        "nugget_verdict": nugget_verdict,
        "axon_result": axon_result,
        "critic_unavailable": _critic_unavailable_seen,
        "cognitive_failure": _cognitive_failure_seen,
        "topic_lock_failed": _topic_lock_failed_seen,
        "_loop_detected": _loop_detected,
        "_budget_suppressed": False,
        "_cap_hit": False,
        "hex_cortex_v2": True,
    }


def should_escalate_to_nugget(
    proposal: dict,
    debate_log: dict,
    brave_result: dict,
) -> tuple[bool, str]:
    """
    Explicit, narrow trigger rules for Nugget escalation.
    Returns (should_escalate: bool, reason: str).

    Rules (all require no consensus from normal debate):
      1. SYSTEM_REPAIR with no consensus → always escalate if NUGGET_REPAIR_ALWAYS_ESC
      2. No consensus after >= NUGGET_MIN_ROUNDS_FOR_ESC rounds
         AND both POLARIS and IVARIS are high-confidence (hard-oppose)
      3. Oracle evidence explicitly contradicts the proposal (confirmed=False)
         AND rounds >= 1 (not a budget-suppressed zero-round result)
      4. Proposal has been seen_count > 2 in DB (repeated unresolved path)

    Does NOT escalate if:
      - debate was budget-suppressed (_budget_suppressed=True)
      - IVARIS was unavailable (critic_unavailable=True)
      - rounds == 0 with no budget/repair reason (too early to judge)
    """
    if not NUGGET_ENABLED:
        return False, "nugget_disabled"

    # Never escalate on budget-suppressed or critic-unavailable outcomes
    if debate_log.get("_budget_suppressed"):
        return False, "budget_suppressed"
    if debate_log.get("critic_unavailable"):
        return False, "critic_unavailable"

    ptype   = str(proposal.get("proposal_type") or "").upper()
    rounds  = int(debate_log.get("rounds") or 0)
    conf    = float(debate_log.get("final_confidence") or 0.0)
    p_conf  = float(proposal.get("confidence") or 0.0)

    # Rule 1: SYSTEM_REPAIR always escalates if unresolved
    if NUGGET_REPAIR_ALWAYS_ESC and ptype == "SYSTEM_REPAIR":
        return True, "system_repair_unresolved"

    # Too few rounds — don't escalate on trivially short debates
    if rounds < NUGGET_MIN_ROUNDS_FOR_ESC:
        return False, f"insufficient_rounds ({rounds}<{NUGGET_MIN_ROUNDS_FOR_ESC})"

    # Rule 2: hard-oppose at high confidence from both sides
    if conf >= NUGGET_HIGH_CONF_THRESHOLD and p_conf >= NUGGET_HIGH_CONF_THRESHOLD:
        return True, f"hard_oppose_high_conf (ivaris={conf:.2f} polaris={p_conf:.2f})"

    # Rule 3: Oracle explicitly contradicts the proposal
    if (not brave_result.get("skipped")
            and brave_result.get("confirmed") is False
            and rounds >= 1):
        return True, "oracle_evidence_contradicts_proposal"

    # Rule 4: proposal has been repeatedly unresolved (seen_count > 2)
    seen = int(proposal.get("seen_count") or 0)
    if seen > 2:
        return True, f"repeated_unresolved (seen_count={seen})"

    return False, "no_escalation_trigger"


def call_nugget_audit(
    proposal: dict,
    debate_log: dict,
    brave_result: dict,
    escalation_reason: str,
) -> dict:
    """
    Call Nugget (Gemini 2.5 Flash) for a compact audit verdict.
    Returns a structured advisory dict. Never raises — always returns safely.
    Does NOT write to DB. Does NOT modify proposal. Advisory only.

    Output contract:
      winner:               "POLARIS" | "IVARIS" | "INCONCLUSIVE"
      confidence:           0.0–1.0
      reason:               one sentence
      missing_evidence:     what would resolve the disagreement
      recommended_next_step: "approve" | "reject" | "defer" | "repair_first"
    """
    _nim_key = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
    if not _nim_key:
        log.warning("NUGGET: NVIDIA_NIM_API_KEY missing — Nugget unavailable")
        return {
            "winner": "INCONCLUSIVE", "confidence": 0.0,
            "reason": "Nugget unavailable: NIM key not set",
            "missing_evidence": "", "recommended_next_step": "defer",
            "_nugget_failed": True,
        }

    # Full escalation bundle — Nugget receives everything needed to make a
    # substantive audit decision, not just a thin summary.
    ptype      = proposal.get("proposal_type", "UNKNOWN")
    ptext      = proposal.get("proposal_text", "")[:500]
    action     = proposal.get("suggested_action", "")[:300]
    p_conf     = float(proposal.get("confidence") or 0.0)
    objections = debate_log.get("final_objections", [])
    ivaris_conf= float(debate_log.get("final_confidence") or 0.0)
    rounds     = int(debate_log.get("rounds") or 0)
    # Full Oracle evidence — not just first 2 snippets
    brave_snip = (brave_result.get("evidence_snippets") or [])
    brave_conf = brave_result.get("confirmed")
    brave_query= brave_result.get("search_query", "")
    # Confidence trail from transcript
    conf_trail = []
    for turn in (debate_log.get("transcript") or []):
        r = turn.get("result") or {}
        if turn.get("speaker") == "IVARIS" and "confidence" in r:
            conf_trail.append(f"Round {turn.get('round',0)}: {float(r.get('confidence',0)):.2f}")
    # Disagreement summary from transcript (last IVARIS verdict text)
    transcript = debate_log.get("transcript") or []
    last_ivaris_verdict = ""
    for turn in reversed(transcript):
        if turn.get("speaker") == "IVARIS":
            last_ivaris_verdict = str((turn.get("result") or {}).get("verdict", ""))[:200]
            break

    system_prompt = """You are Nugget — the escalation auditor and tie-breaker of the Sentinuity sovereign trading organism.

You are called ONLY when POLARIS (proposer) and IVARIS (critic) cannot reach consensus after full debate.
Your job: deliver a compact, structured verdict using ALL provided context.

Rules:
- You do NOT generate proposals.
- You do NOT generate rebuttals.
- You assess the disagreement using the full escalation bundle provided.
- Output ONLY valid JSON. No prose outside the JSON object.

Output contract:
{
  "winner": "POLARIS" | "IVARIS" | "INCONCLUSIVE",
  "confidence": 0.0-1.0,
  "reason": "one concise sentence explaining your verdict",
  "missing_evidence": "what specific evidence would resolve this",
  "recommended_next_step": "approve_with_conditions" | "reject" | "defer" | "repair_first" | "escalate_hitl"
}"""

    user_message = f"""ESCALATION BUNDLE

ESCALATION REASON: {escalation_reason}

── POLARIS POSITION ──
Proposal type:    {ptype}
Polaris confidence: {p_conf:.2f}
Proposal text:    {ptext}
Suggested action: {action}

── IVARIS POSITION ──
Final IVARIS confidence: {ivaris_conf:.2f}
Debate rounds completed: {rounds}
Confidence trail:        {", ".join(conf_trail) if conf_trail else "unavailable"}
Final IVARIS verdict:    {last_ivaris_verdict}
All objections:
{json.dumps(objections, indent=2)}

── ORACLE EXTERNAL EVIDENCE ──
Search query:  {brave_query}
Confirmed:     {brave_conf}
Evidence:
{chr(10).join(f"  - {s}" for s in brave_snip) if brave_snip else "  Not available"}

── YOUR TASK ──
Assess the full disagreement above.
Do NOT invent facts. Do NOT repeat proposal text back.
Return your compact JSON verdict only."""

    log.info(
        "NUGGET: calling NIM/%s — escalation_reason=%s proposal_type=%s",
        NUGGET_MODEL, escalation_reason, ptype,
    )
    try:
        import json as _json, urllib.request as _ur
        payload = _json.dumps({
            "model": NUGGET_MODEL,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user",   "content": user_message},
            ],
            "max_tokens": 500,
            "temperature": 0.2,
        }).encode()
        req = _ur.Request(
            "https://integrate.api.nvidia.com/v1/chat/completions",
            data=payload,
            headers={"Authorization": f"Bearer {_nim_key}", "Content-Type": "application/json"},
            method="POST",
        )
        with _ur.urlopen(req, timeout=60) as _r:
            data = _json.loads(_r.read().decode())
        text = data["choices"][0]["message"]["content"].strip()
        if not text:
            log.error("NUGGET: empty response from Gemini")
            return {
                "winner": "INCONCLUSIVE", "confidence": 0.0,
                "reason": "Nugget returned empty response",
                "missing_evidence": "", "recommended_next_step": "defer",
                "_nugget_failed": True,
            }
        parsed = _parse_json_response(text)
        if not parsed:
            log.error("NUGGET: JSON parse failed — raw: %s", text[:200])
            return {
                "winner": "INCONCLUSIVE", "confidence": 0.0,
                "reason": "Nugget response parse failed",
                "missing_evidence": "", "recommended_next_step": "defer",
                "_nugget_failed": True,
            }
        log.info(
            "NUGGET advisory: winner=%s confidence=%.2f next=%s reason=%s",
            parsed.get("winner"), float(parsed.get("confidence") or 0),
            parsed.get("recommended_next_step"), str(parsed.get("reason",""))[:120],
        )
        return parsed

    # FIX (2026-05-21): this function uses urllib.request.urlopen (line ~3249), NOT requests.
    # Previously `except requests.exceptions.Timeout:` raised NameError on every timeout
    # because `requests` was never imported. Now matches what urlopen actually raises.
    except (TimeoutError, socket.timeout, urllib.error.URLError) as exc:
        log.error("NUGGET: request timed out / network error: %s", exc)
        return {
            "winner": "INCONCLUSIVE", "confidence": 0.0,
            "reason": f"Nugget network failure: {exc}",
            "missing_evidence": "", "recommended_next_step": "defer",
            "_nugget_failed": True,
        }
    except Exception as e:
        log.error("NUGGET: unexpected error: %s", e)
        return {
            "winner": "INCONCLUSIVE", "confidence": 0.0,
            "reason": f"Nugget error: {e}",
            "missing_evidence": "", "recommended_next_step": "defer",
            "_nugget_failed": True,
        }


def maybe_escalate_to_nugget(
    proposal: dict,
    debate_log: dict,
    brave_result: dict,
) -> Optional[dict]:
    """
    Checks escalation conditions and calls Nugget if warranted.
    Respects per-proposal cooldown to prevent re-invocation in the same loop.
    Returns Nugget advisory dict, or None if no escalation.
    Advisory is logged but does NOT modify the proposal or debate outcome.
    Governor retains full authority over final decision.
    """
    proposal_id = proposal.get("id")

    # Per-proposal cooldown guard
    last_called = _nugget_last_called.get(proposal_id, 0)
    if time.time() - last_called < NUGGET_COOLDOWN_SEC:
        log.info(
            "NUGGET: cooldown active for proposal #%s (%.0fs remaining) — skipping",
            proposal_id, NUGGET_COOLDOWN_SEC - (time.time() - last_called),
        )
        return None

    should_esc, reason = should_escalate_to_nugget(proposal, debate_log, brave_result)
    if not should_esc:
        log.info("NUGGET: no escalation — %s", reason)
        return None

    log.warning(
        "NUGGET: escalating proposal #%s type=%s reason=%s",
        proposal_id, proposal.get("proposal_type"), reason,
    )
    _nugget_last_called[proposal_id] = time.time()
    return call_nugget_audit(proposal, debate_log, brave_result, reason)


# ═════════════════════════════════════════════════════════════════════════════
# GOVERNANCE CYCLE
# ═════════════════════════════════════════════════════════════════════════════

def run_cycle() -> None:
    # Active purge of _last_concluded — prevents runtime suppression accumulation.
    # ChatGPT confirmed: without this explicit sweep, the dict fills over 30-45min
    # and blocks all new proposals even after our per-key decay fix.
    _now_gc = time.time()
    _concluded_expired = [
        k for k, v in list(_last_concluded.items())
        if _now_gc - v > (1800 if k.startswith("TRADING") else 7200)
    ]
    for k in _concluded_expired:
        del _last_concluded[k]
    if _concluded_expired:
        log.debug("GC _last_concluded: pruned %d expired keys", len(_concluded_expired))

    proposals     = get_open_proposals()
    hitl_required = str(str(get_config_value("HITL_REQUIRED", "1"))).strip() == "1"

    if not proposals:
        update_heartbeat(SERVICE_NAME, "ALIVE", "Idle — no open proposals")
        return

    _rc_focus = str(get_config_value("DEBATE_FOCUS_ACTIVE","0")).strip()=="1"
    _rc_type  = str(get_config_value("POLARIS_PROPOSAL_FILTER","SYSTEM_REPAIR")).strip().upper()

    for proposal in proposals:
        pid   = proposal.get("id")
        ptype = str(proposal.get("proposal_type") or "").upper()

        if _rc_focus and ptype != _rc_type:
            log.info("FOCUS LOCK: skip #%d type=%s locked=%s", pid, ptype, _rc_type)
            continue

        try:
            # ── STEP 1: DEBATE ─────────────────────────────────────────────
            debate_log = run_debate(proposal)
            log_debate_to_db(pid, debate_log)
            write_cognition_event(
                "DEBATE", f"proposal_{pid}",
                f"DEBATE_CYCLE | rounds={debate_log.get('rounds',0)} "
                f"consensus={debate_log.get('consensus',False)} "
                f"conf={float(debate_log.get('final_confidence',0)):.2f}",
                float(debate_log.get("final_confidence", 0)),
            )

            if not debate_log["consensus"]:
                if debate_log.get("_budget_suppressed"):
                    # Budget gate fired — leave proposal open so it can be
                    # retried once the rolling window resets. Do not penalise
                    # the proposal with critic_unavailable or rejected status.
                    log.info(
                        "Proposal #%d budget-suppressed — leaving open for retry "
                        "(type=%s, budget_status implied by suppression flag)",
                        pid, ptype,
                    )
                    _last_concluded[ptype] = time.time()
                    continue
                if debate_log.get("critic_unavailable"):
                    _cu_cause = debate_log.get("critic_unavailable_cause", "IVARIS API unavailable")
                    log.warning("Proposal #%d BLOCKED — CRITIC_UNAVAILABLE: %s", pid, _cu_cause)
                    # Leave as 'open' with a retry_after timestamp instead of permanently marking
                    # critic_unavailable — this prevents the same proposal from being retried
                    # every cycle and filling the log with 59+ identical failures.
                    # Retry after 30 minutes.
                    try:
                        with get_connection() as _cu_conn:
                            _cu_conn.execute("""
                                UPDATE polaris_proposals
                                SET retry_after = ?, status = 'critic_unavailable'
                                WHERE id = ?
                            """, (time.time() + 1800, pid))
                            _cu_conn.commit()
                    except Exception:
                        mark_proposal_status(pid, "critic_unavailable")
                    write_cognition_event("DEBATE", f"proposal_{pid}",
                        f"CRITIC_UNAVAILABLE | {_cu_cause} | retry in 30min", 0.0)
                else:
                    log.info("Proposal #%d REJECTED by IVARIS (conf=%.2f)",
                             pid, float(debate_log.get("final_confidence", 0)))

                    # ── STEP 1b: FOUR-LAYER ESCALATION ─────────────────────
                    # Flow: Brave runs first → Nugget receives full context
                    # (proposal + IVARIS objections + Oracle evidence) →
                    # Governor maps Nugget advisory to final disposition.
                    # Normal two-agent flow is default; this path fires only
                    # when escalation conditions trigger (see should_escalate_to_nugget).
                    # Governor retains full authority — Nugget is advisory.

                    # 1. ORACLE — only fires if IVARIS flagged external_evidence_required
                    # Preserves Brave budget (1000/month) for genuine factual disputes
                    _ivaris_wants_oracle = bool(any(
                        t.get("result", {}).get("external_evidence_required")
                        for t in debate_log.get("transcript", [])
                        if t.get("speaker") == "IVARIS"
                    ))
                    if _ivaris_wants_oracle:
                        _brave_for_nugget = brave_verify(proposal, debate_log)
                        log.info(
                            "FOUR-LAYER: ORACLE ran for escalation — confirmed=%s query='%s'",
                            _brave_for_nugget.get("confirmed"),
                            _brave_for_nugget.get("search_query", "")[:60],
                        )
                    else:
                        _brave_for_nugget = {
                            "confirmed": None, "evidence_snippets": [],
                            "search_query": "", "skipped": True,
                            "skip_reason": "IVARIS did not flag external_evidence_required",
                        }
                        log.info("FOUR-LAYER: ORACLE skipped — IVARIS did not flag external_evidence_required")

                    # 2. Nugget — receives proposal + IVARIS objections + Oracle evidence
                    nugget_advisory = maybe_escalate_to_nugget(
                        proposal, debate_log, _brave_for_nugget
                    )

                    # 3. Governor disposition mapping
                    if nugget_advisory and not nugget_advisory.get("_nugget_failed"):
                        write_cognition_event(
                            "NUGGET", f"proposal_{pid}",
                            f"NUGGET_ADVISORY | winner={nugget_advisory.get('winner')} "
                            f"next={nugget_advisory.get('recommended_next_step')} "
                            f"conf={float(nugget_advisory.get('confidence',0)):.2f} "
                            f"reason={str(nugget_advisory.get('reason',''))[:120]}",
                            float(nugget_advisory.get("confidence") or 0.0),
                        )
                        nugget_step = str(nugget_advisory.get("recommended_next_step") or "reject")
                        nugget_conf = float(nugget_advisory.get("confidence") or 0.0)

                        log.warning(
                            "FOUR-LAYER GOVERNOR: proposal #%d — Nugget says '%s' "
                            "(conf=%.2f, winner=%s)",
                            pid, nugget_step, nugget_conf,
                            nugget_advisory.get("winner"),
                        )

                        # ── DISPOSITION MAP ─────────────────────────────────
                        # approve_with_conditions + high confidence → HITL with
                        #   full four-layer report (Polaris + IVARIS + Oracle + Nugget)
                        if nugget_step == "approve_with_conditions" and nugget_conf >= NUGGET_ACT_CONFIDENCE:
                            log.info(
                                "FOUR-LAYER: Nugget recommends approve_with_conditions "
                                "(conf=%.2f >= %.2f) — escalating to HITL with full report",
                                nugget_conf, NUGGET_ACT_CONFIDENCE,
                            )
                            mark_proposal_status(pid, "nugget_escalated")
                            write_cognition_event("NUGGET", f"proposal_{pid}",
                                f"NUGGET_ESCALATED | Routing to HITL with four-layer report.",
                                nugget_conf)
                            # Four-layer HITL push — includes all four voices
                            push_to_telegram_sync(proposal, debate_log, _brave_for_nugget, nugget_advisory)
                            _last_concluded[ptype] = time.time()
                            continue

                        # escalate_hitl → always send to operator regardless of confidence
                        elif nugget_step == "escalate_hitl":
                            log.info(
                                "FOUR-LAYER: Nugget requests escalate_hitl — "
                                "sending full four-layer report to operator"
                            )
                            mark_proposal_status(pid, "nugget_escalated")
                            write_cognition_event("NUGGET", f"proposal_{pid}",
                                "NUGGET_ESCALATED | escalate_hitl triggered.", nugget_conf)
                            push_to_telegram_sync(proposal, debate_log, _brave_for_nugget, nugget_advisory)
                            _last_concluded[ptype] = time.time()
                            continue

                        # defer → leave open, let proposal surface again next cycle
                        elif nugget_step == "defer":
                            log.info(
                                "FOUR-LAYER: Nugget says defer — leaving proposal #%d open "
                                "for next cycle", pid
                            )
                            write_cognition_event("NUGGET", f"proposal_{pid}",
                                "NUGGET_DEFER | Proposal left open for retry.", nugget_conf)
                            # Do not mark rejected — proposal stays open
                            _last_concluded[ptype] = time.time()
                            continue

                        # repair_first → bypass type cooldown, re-queue as priority repair
                        elif nugget_step == "repair_first":
                            log.warning(
                                "FOUR-LAYER: Nugget says repair_first — "
                                "re-queuing proposal #%d as SYSTEM_REPAIR priority", pid
                            )
                            write_cognition_event("NUGGET", f"proposal_{pid}",
                                "NUGGET_REPAIR_FIRST | Re-queued as priority repair.", nugget_conf)
                            # Reset the type cooldown so repair surfaces immediately
                            _last_concluded.pop(ptype, None)
                            mark_proposal_status(pid, "nugget_escalated")
                            _last_concluded[ptype] = time.time()
                            continue

                        # reject (or unrecognised) → fall through to standard rejection below
                        else:
                            log.info(
                                "FOUR-LAYER: Nugget says reject (step='%s') — "
                                "confirming IVARIS rejection of proposal #%d",
                                nugget_step, pid,
                            )
                            # fall through to standard mark_proposal_status below
                    else:
                        # Nugget not triggered or failed — log and fall through to rejection
                        if nugget_advisory and nugget_advisory.get("_nugget_failed"):
                            log.warning(
                                "FOUR-LAYER: Nugget failed for proposal #%d — "
                                "falling back to IVARIS rejection", pid,
                            )
                        # else: no escalation triggered — normal rejection path
                    # ── end four-layer escalation ───────────────────────────

                    mark_proposal_status(pid, "rejected_by_ivaris")
                    write_cognition_event("DEBATE", f"proposal_{pid}",
                        f"REJECTED | Vetoed after {debate_log['rounds']} rounds.",
                        float(debate_log.get("final_confidence", 0)))
                    _tg_post("sendMessage", {
                        "chat_id": OWNER_ID,
                        "text": (f"❌ *Proposal #{pid} REJECTED by IVARIS*\n\n"
                                 f"Type: `{ptype}`\n"
                                 f"Rounds: {debate_log['rounds']}\n"
                                 f"Confidence: {float(debate_log.get('final_confidence',0)):.2f}\n\n"
                                 + "\n".join(
                                     f"• {o}"
                                     for o in debate_log.get("final_objections", [])[:3]
                                 )),
                        "parse_mode": "Markdown",
                    })
                _last_concluded[ptype] = time.time()
                continue

            # ── STEP 2: ORACLE VERIFICATION ────────────────────────────────
            # Only fires if IVARIS flagged external_evidence_required during debate.
            # After consensus, Oracle is used for final confirmation not discovery.
            # Saves Brave budget — semantic debates don't need external validation.
            _final_needs_oracle = bool(any(
                t.get("result", {}).get("external_evidence_required")
                for t in debate_log.get("transcript", [])
                if t.get("speaker") == "IVARIS"
            ))
            final_proposal = debate_log.get("final_proposal", proposal)
            if _final_needs_oracle:
                brave_result = brave_verify(final_proposal, debate_log)
                log.info("ORACLE: confirmed=%s query='%s'",
                         brave_result.get("confirmed"),
                         brave_result.get("search_query", "")[:60])
            else:
                brave_result = {
                    "confirmed": None, "evidence_snippets": [],
                    "search_query": "", "skipped": True,
                    "skip_reason": "IVARIS did not flag external_evidence_required",
                }
                log.info("ORACLE: skipped post-consensus — IVARIS did not flag external_evidence_required")

            # ── STEP 3: APPLY OR PUSH TO HITL ──────────────────────────────
            if hitl_required:
                pushed = push_to_telegram_sync(final_proposal, debate_log, brave_result)
                if pushed:
                    # Note: HITL push does NOT call _write_approved_status.
                    # Status is set to 'pushed' (not approved) — approval only
                    # happens when the operator sends /approve <id>.
                    mark_proposal_status(pid, "pushed")
                    write_cognition_event("POLARIS", f"proposal_{pid}",
                        f"PUSHED | Escalated to HITL. "
                        f"Action: {final_proposal.get('suggested_action','')[:220]}",
                        float(debate_log.get("final_confidence", 0)))
                    # Write pending_hitl to patch_history (pre-approval record)
                    try:
                        with get_connection() as conn:
                            _write_patch_history_inner(
                                conn=conn,
                                proposal=final_proposal,
                                debate_result=debate_log,
                                brave_result=brave_result,
                                hitl_approved=False,
                                outcome="pending_hitl",
                            )
                            conn.commit()
                    except Exception:
                        pass
                else:
                    mark_proposal_status(pid, "push_failed")
                    log.warning("Proposal #%d push failed", pid)

            else:
                # ── AUTO-APPLY (HITL_REQUIRED=0) ───────────────────────────
                # PREDICATE CALLED HERE — no apply without valid IVARIS sign-off
                applied      = False
                apply_method = "direct"

                # ── FORGE domain: stage advancement, not numeric config change ──
                if final_proposal.get("proposal_domain") == "FORGE":
                    try:
                        project_key = final_proposal.get("project_key", "")
                        action_text = final_proposal.get("suggested_action", "").upper()
                        if project_key and ("DECOMPOSE" in action_text or
                                           "COMPLETE" in action_text or
                                           "MILESTONE" in action_text or
                                           "ADVANCE" in action_text or
                                           "RESEARCH" in action_text):
                            from services.intelligence_orchestrator import (
                                _get_project_stage, _advance_project_stage
                            )
                            current_stage = _get_project_stage(project_key)
                            new_stage = _advance_project_stage(project_key, current_stage)
                            if new_stage:
                                log.info(
                                    "FORGE STAGE ADVANCED: %s %s → %s",
                                    project_key, current_stage, new_stage
                                )
                                applied = True
                                apply_method = "forge_stage_advance"
                            else:
                                # Already at final stage or no advancement possible
                                # Mark as applied anyway — proposal is valid
                                applied = True
                                apply_method = "forge_stage_final"
                        else:
                            # FORGE proposal without project_key — mark applied
                            applied = True
                            apply_method = "forge_acknowledged"
                    except Exception as forge_err:
                        log.warning("FORGE apply handler error: %s", forge_err)
                        applied = True  # fail-open for FORGE — never block evolution
                        apply_method = "forge_failopen"
                else:
                    try:
                        from services.sovereign_parameter_engine import (
                            SovereignParameterEngine, Proposal as SPEProposal,
                            ParameterChange,
                        )
                        action = final_proposal.get("suggested_action", "")
                        m = re.search(
                            r"Change\s+(\w+)\s+from\s+([\d.]+)\s+to\s+([\d.]+)",
                            action, re.IGNORECASE,
                        )
                        if m:
                            param, old_val, new_val = m.group(1), m.group(2), m.group(3)
                            spe_proposal = SPEProposal(
                                proposal_id=str(pid),
                                hypothesis=final_proposal.get("proposal_text", "")[:300],
                                expected_outcome=f"Improve trading performance via {param}",
                                falsifiability_condition="Win rate or PnL degrades within 1h",
                                risk_assessment=f"Thermal-throttled change to {param}",
                                success_metric="Win rate >= previous baseline",
                                parameter_changes=[
                                    ParameterChange(
                                        param=param, old_value=old_val, new_value=new_val,
                                        proposal_id=str(pid),
                                    )
                                ],
                            )
                            spe_result   = SovereignParameterEngine().apply_proposal(spe_proposal)
                            applied      = spe_result.get("success", False)
                            apply_method = "spe"
                            if spe_result.get("blocked"):
                                log.warning("SPE blocked changes: %s", spe_result["blocked"])
                        else:
                            applied      = apply_proposal_to_config(final_proposal)
                            apply_method = "direct"

                    except Exception as spe_err:
                        log.warning("SPE failed (%s) — falling back to direct apply", spe_err)
                        applied      = apply_proposal_to_config(final_proposal)
                        apply_method = "direct_fallback"

                if applied:
                    # Guarded write — raises GovernanceViolation if predicate fails
                    _write_approved_status(
                        proposal_id=pid,
                        debate_log=debate_log,
                        outcome="auto_applied",
                        hitl_approved=False,
                        brave_result=brave_result,
                        proposal=final_proposal,
                    )
                    write_cognition_event("POLARIS", f"proposal_{pid}",
                        f"AUTO_APPLIED ({apply_method}) | "
                        f"Action: {final_proposal.get('suggested_action','')[:220]}",
                        float(debate_log.get("final_confidence", 0)))
                    log.info("Proposal #%d AUTO-APPLIED via %s", pid, apply_method)
                    _tg_post("sendMessage", {
                        "chat_id": OWNER_ID,
                        "text": (f"✅ *Proposal #{pid} AUTO-APPLIED ({apply_method})*\n\n"
                                 f"`{final_proposal.get('suggested_action','')}`\n\n"
                                 f"_HITL_REQUIRED=0 — patch applied without human approval._"),
                        "parse_mode": "Markdown",
                    })
                else:
                    mark_proposal_status(pid, "apply_failed")
                    write_cognition_event("DEBATE", f"proposal_{pid}",
                        f"APPLY_FAILED | Consensus reached but apply path failed.",
                        float(debate_log.get("final_confidence", 0)))

            _last_concluded[ptype] = time.time()

        except GovernanceViolation as gv:
            log.error("GOVERNANCE VIOLATION proposal #%d: %s", pid, gv)
            mark_proposal_status(pid, "governance_violation")
            _tg_post("sendMessage", {
                "chat_id": OWNER_ID,
                "text": (f"🚨 *GOVERNANCE VIOLATION #{pid}*\n\n`{gv}`\n\n"
                         f"Proposal blocked and flagged."),
                "parse_mode": "Markdown",
            })

        except Exception as e:
            import traceback as _tb
            _trace = _tb.format_exc()[-800:]
            log.exception("Debate failed for proposal #%d: %s", pid, e)
            # Write root cause to cognition log so it's visible in the UI
            try:
                write_cognition_event("DEBATE", f"proposal_{pid}",
                    f"DEBATE_ERROR | {type(e).__name__}: {str(e)[:200]} | trace: {_trace[-300:]}",
                    0.0)
            except Exception:
                pass
            mark_proposal_status(pid, "debate_error")

        time.sleep(5)

    update_heartbeat(SERVICE_NAME, "ALIVE",
        f"Processed {len(proposals)} proposal(s)")


# ═════════════════════════════════════════════════════════════════════════════
# HITL BOT — Telegram command handlers
# ═════════════════════════════════════════════════════════════════════════════

def get_pending_proposals_for_hitl() -> list[dict]:
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT * FROM polaris_proposals
                WHERE status IN ('open', 'pushed', 'debating')
                ORDER BY created_at DESC LIMIT 10
            """).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("get_pending_proposals_for_hitl failed: %s", e)
        return []


def hitl_approve_proposal(proposal_id: int) -> tuple[bool, str]:
    """
    HITL approval — called when operator sends /approve <id>.

    UNSAFE PATH CLOSED: the original telegram_hitl.approve_proposal()
    wrote status='approved' without any IVARIS check.

    This function fetches the stored debate_log and calls the pure predicate
    before allowing any write. If no valid debate_log exists, approval is
    denied and the operator is told why.

    Returns (success: bool, message: str)
    """
    try:
        with get_connection() as conn:
            proposal_row = conn.execute(
                "SELECT * FROM polaris_proposals WHERE id=?",
                (proposal_id,),
            ).fetchone()
            if not proposal_row:
                return False, f"Proposal #{proposal_id} not found"

            # Strict proposal-scoped query — no global fallback, no json_extract.
            # proposal_id is a first-class DB column. If no rows exist for this
            # proposal_id, approval is denied. There is no cross-proposal fallback.
            debate_turns = conn.execute(
                """
                SELECT speaker, action, content_json, thinking_state, verdict_type,
                       transcript_json, logged_at
                FROM debate_log
                WHERE proposal_id = ?
                ORDER BY logged_at ASC
                """,
                (proposal_id,),
            ).fetchall()

        if not debate_turns:
            return False, (
                f"Proposal #{proposal_id} has no debate record in debate_log. "
                "IVARIS must critique this proposal before it can be approved. "
                "Approval denied."
            )

        # Reconstruct debate_log dict from debate_log rows.
        # Find the final turn to get transcript_json and consensus state.
        _final_turn = None
        _transcript  = []
        _final_conf  = 0.0
        for _t in debate_turns:
            _vtype = str(_t["verdict_type"] or "")
            _cjson = {}
            try: _cjson = json.loads(_t["content_json"] or "{}")
            except Exception: pass
            if _vtype in ("final_consensus", "final_rejection", "blocked"):
                _final_turn = _t
                _final_conf = float(_cjson.get("confidence", 0.0))
            if _t["transcript_json"]:
                try: _transcript = json.loads(_t["transcript_json"])
                except Exception: pass

        _consensus = (
            _final_turn is not None and
            str(_final_turn["verdict_type"] or "") == "final_consensus"
        )

        debate_log = {
            "proposal_id":      proposal_id,
            "consensus":        _consensus,
            "final_confidence": _final_conf,
            "rounds":           len([t for t in debate_turns if t["speaker"] == "POLARIS"]),
            "final_objections": [],
            "transcript":       _transcript,
        }

        # Call the pure predicate — same guard as auto-apply path
        eligible, reason = _is_valid_ivaris_approval(debate_log, proposal_id)
        log.info(
            "HITL_APPROVAL_PREDICATE proposal=%d eligible=%s reason=%s",
            proposal_id, eligible, reason,
        )

        if not eligible:
            return False, (
                f"Proposal #{proposal_id} failed approval predicate: {reason}\n"
                "Approval denied."
            )

        # Predicate passed — safe to write
        proposal = dict(proposal_row)
        _write_approved_status(
            proposal_id=proposal_id,
            debate_log=debate_log,
            outcome="approved",
            hitl_approved=True,
            proposal=proposal,
        )

        # Apply the change after HITL approval
        applied = apply_proposal_to_config(proposal)
        if applied:
            mark_proposal_status(proposal_id, "applied")
            write_cognition_event("POLARIS", f"proposal_{proposal_id}",
                f"HITL_APPROVED_AND_APPLIED | "
                f"Action: {proposal.get('suggested_action','')[:220]}",
                float(debate_log.get("final_confidence", 0)))

        return True, (
            f"Proposal #{proposal_id} approved and applied. "
            f"Confidence: {float(debate_log['final_confidence']):.2f}"
        )

    except GovernanceViolation as gv:
        return False, f"Governance violation: {gv}"
    except Exception as e:
        log.exception("hitl_approve_proposal failed id=%d: %s", proposal_id, e)
        return False, f"Approval failed: {e}"


def hitl_reject_proposal(proposal_id: int) -> tuple[bool, str]:
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE polaris_proposals SET status='rejected' "
                "WHERE id=? AND status IN ('open','pushed','debating')",
                (proposal_id,),
            )
            conn.commit()
        return True, f"Proposal #{proposal_id} rejected."
    except Exception as e:
        log.warning("hitl_reject_proposal failed: %s", e)
        return False, f"Reject failed: {e}"


def _is_owner(update: dict) -> bool:
    """Check if Telegram update is from the authorised owner."""
    return str((update.get("message") or update.get("callback_query", {})
                ).get("from", {}).get("id", "")) == str(OWNER_ID)


def _operator_override_debate(
    chat_id: str,
    topic: str = "",
    forge_mode: bool = False,
    forge_context: str = "",
    source_file: str = "",
    func_name: str = "",
) -> None:
    """
    Operator override: immediately retires the current debate queue and seeds
    a fresh RESEARCH_NOTE so the next governor cycle debates a new topic.

    forge_mode=True: appends MASTER_FORGE_PROMPT + live code context to the
    proposal so POLARIS is primed to emit a FORGE_COMPLETE artifact.
    """
    import hashlib as _hl, json as _json

    now = time.time()

    with get_connection() as conn:
        # 1. Retire open/debating proposals so the queue is clear
        retired = conn.execute(
            """
            UPDATE polaris_proposals
            SET status = 'operator_retired',
                resolution_note = 'Retired by operator /debate override'
            WHERE status IN ('open', 'debating', 'critic_unavailable')
            """
        ).rowcount
        conn.commit()

        # 2. Clear focus lock — operator override always supersedes mission lock
        conn.execute(
            "UPDATE system_config SET value='0' WHERE key='DEBATE_FOCUS_ACTIVE'"
        )
        conn.commit()

    log.info("OPERATOR_OVERRIDE: retired %d open proposal(s), focus lock cleared", retired)

    # 3. Build the new research proposal
    sensory_context = _get_sensory_context(limit=6)
    if topic:
        seed_text = (
            f"Operator-directed research: {topic}. "
            f"Analyse the organism\'s current state in relation to this topic and "
            f"propose the single highest-leverage improvement."
            + (f" Recent Sensory Scout context: {json.dumps(sensory_context, default=str)[:600]}" if sensory_context else "")
        )
        seed_action = (
            f"Research and propose: {topic}. "
            f"Ground the proposal in current pipeline data and measurable outcomes."
        )
        label = f"operator topic: {topic[:40]}"
    else:
        seed_text = (
            "Operator-initiated free-form research cycle. "
            "POLARIS should survey ingest health, qualification rate, oracle coverage, "
            "win rate trend, and standing task backlog — then propose the single most "
            "impactful improvement available right now."
            + (f" Recent Sensory Scout context: {json.dumps(sensory_context, default=str)[:600]}" if sensory_context else "")
        )
        seed_action = (
            "Run a full organism state survey and propose the highest-leverage improvement. "
            "Prioritise: pipeline reliability > signal quality > parameter tuning."
        )
        label = "free-form priority scan"

    # Forge mode: append live code context + forge protocol to action
    if forge_mode:
        _forge_header = f"\n\n[FORGE MODE]"
        if source_file:
            _forge_header += f" Target: {source_file}"
            if func_name:
                _forge_header += f"::{func_name}"
        if forge_context and not forge_context.startswith("ERROR"):
            seed_action = (
                seed_action
                + _forge_header
                + f"\n\nCURRENT_STATE:\n{forge_context[:2000]}"
                + f"\n\n{MASTER_FORGE_PROMPT}"
            )
        else:
            seed_action = seed_action + _forge_header + f"\n\n{MASTER_FORGE_PROMPT}"
        label = f"FORGE: {label}"

    phash = _hl.sha256(
        f"RESEARCH_NOTE|{seed_text}|{seed_action}|{now:.0f}".encode()
    ).hexdigest()

    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO polaris_proposals
                (proposal_hash, proposal_type, proposal_text, suggested_action,
                 confidence, metrics_json, status, created_at, last_seen_at)
            VALUES (?, 'RESEARCH_NOTE', ?, ?, 0.85, ?, 'open', ?, ?)
            """,
            (
                phash, seed_text, seed_action,
                _json.dumps({
                    "operator_override": True, "topic": topic, "ts": now,
                    "forge_mode": forge_mode, "source_file": source_file,
                    "func_name": func_name,
                }),
                now, now,
            ),
        )
        conn.commit()
        new_id = conn.execute(
            "SELECT id FROM polaris_proposals WHERE proposal_hash=?", (phash,)
        ).fetchone()["id"]

    log.info(
        "OPERATOR_OVERRIDE: seeded RESEARCH_NOTE #%d (%s) — debate fires within 60s",
        new_id, label,
    )

    _forge_badge = "⚒️ *FORGE MODE*\n" if forge_mode else ""
    _forge_source = f"Source: `{source_file}`" + (f"::`{func_name}`" if func_name else "") + "\n" if forge_mode and source_file else ""
    _tg_post("sendMessage", {
        "chat_id": chat_id,
        "parse_mode": "Markdown",
        "text": (
            f"🔄 *DEBATE OVERRIDE ACTIVE*\n"
            f"{_forge_badge}"
            f"{_forge_source}\n"
            f"Retired {retired} open proposal(s).\n"
            f"Focus lock cleared.\n\n"
            f"📋 *New topic seeded* (ID `{new_id}`):\n"
            f"_{seed_text[:200]}_\n\n"
            f"⏳ POLARIS & IVARIS will debate within 60s."
            + ("\n🔨 Expecting FORGE_COMPLETE artifact." if forge_mode else "")
        ),
    })



class CodeForgeValidator:
    """
    Extracts and validates FORGE_COMPLETE artifacts from debate turn text.
    Called by run_cycle() when a proposal reaches consensus to capture
    the final code artifact for the Golden Lattice.
    """
    FORGE_PATTERN = re.compile(
        r'FORGE_COMPLETE\s*```(\w+)?\n(.*?)(?=```|\Z)',
        re.DOTALL | re.IGNORECASE,
    )
    PATH_PATTERN = re.compile(r'(?:services/|components/|ui/)?(\w[\w_/]+\.py)')

    def extract_artifact(self, text: str) -> dict | None:
        """Find FORGE_COMPLETE marker and extract the code block."""
        if not text or "FORGE_COMPLETE" not in text:
            return None
        m = self.FORGE_PATTERN.search(text)
        if not m:
            m = re.search(
                r'FORGE_COMPLETE\s*```\n?(.*?)(?=```|\Z)',
                text, re.DOTALL | re.IGNORECASE,
            )
            if not m:
                return None
            lang, code = "python", m.group(1).strip()
        else:
            lang = (m.group(1) or "python").strip()
            code = m.group(2).strip()

        if not code:
            log.warning("CodeForgeValidator: FORGE_COMPLETE found but code block empty")
            return None

        path_m = self.PATH_PATTERN.search(code)
        path   = path_m.group(0) if path_m else "unknown.py"

        import hashlib as _hl
        checksum = _hl.sha256(code.encode()).hexdigest()[:12]
        log.info(
            "CodeForgeValidator: artifact extracted lang=%s path=%s chk=%s size=%d",
            lang, path, checksum, len(code),
        )
        return {"language": lang, "code": code, "path": path,
                "checksum": checksum, "raw_length": len(code)}

    def validate(self, artifact: dict | None) -> tuple[bool, str]:
        """Returns (is_valid, reason)."""
        if not artifact:
            return False, "No artifact extracted"
        if not artifact.get("code"):
            return False, "Code block is empty"
        if artifact.get("raw_length", 0) < 20:
            return False, f"Code too short ({artifact['raw_length']} chars)"
        if artifact.get("path") == "unknown.py":
            return False, "No target file path found in code block"
        return True, "OK"


_forge_validator = CodeForgeValidator()


def extract_live_code(file_path: str, func_name: str = None) -> str:
    import ast
    from pathlib import Path
    try:
        target = Path(file_path).resolve()
        if not target.exists():
            return f"# Error: File {file_path} not found."
        content = target.read_text(encoding="utf-8")
        if not func_name:
            return content

        tree = ast.parse(content)
        lines = content.splitlines()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and node.name == func_name:
                return "\n".join(lines[node.lineno - 1 : node.end_lineno])
        return f"# Function '{func_name}' not found in {file_path}."
    except Exception as e:
        return f"# Error extracting Ground Truth: {e}"


def run_hitl_bot() -> None:
    """
    Lightweight synchronous HITL bot loop.
    Polls getUpdates for /approve and /reject commands.
    Runs in a daemon thread alongside the main governance cycle.
    """
    # Check token against env — governor may have stale token
    import os as _os
    _env_token = _os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    if _env_token and _env_token != BOT_TOKEN:
        log.info("HITL bot: using token from .env (overrides module-level BOT_TOKEN)")
    _effective_token = _env_token or BOT_TOKEN

    if not _effective_token or not OWNER_ID:
        log.warning("HITL bot: Telegram not configured — bot disabled")
        return

    log.info("HITL BOT ONLINE — listening for /approve and /reject commands")
    offset = None

    while True:
        try:
            params: dict = {"timeout": 20, "allowed_updates": ["message", "callback_query"]}
            if offset is not None:
                params["offset"] = offset

            result = _tg_post("getUpdates", params, timeout=30)
            if not result or not result.get("ok"):
                time.sleep(5)
                continue

            for update in result.get("result", []):
                offset = update["update_id"] + 1

                # Callback query (inline button press from proposal push)
                cq = update.get("callback_query")
                if cq:
                    if str(cq.get("from", {}).get("id", "")) != str(OWNER_ID):
                        continue
                    data   = cq.get("data", "")
                    cq_id  = cq["id"]
                    _tg_post("answerCallbackQuery", {"callback_query_id": cq_id})

                    if data.startswith("approve:"):
                        pid = int(data.split(":")[1])
                        ok, msg = hitl_approve_proposal(pid)
                        _tg_post("sendMessage", {
                            "chat_id": OWNER_ID,
                            "text": ("✅ " if ok else "❌ ") + msg,
                        })

                    elif data.startswith("reject:"):
                        pid = int(data.split(":")[1])
                        ok, msg = hitl_reject_proposal(pid)
                        _tg_post("sendMessage", {
                            "chat_id": OWNER_ID,
                            "text": ("✅ " if ok else "❌ ") + msg,
                        })
                    continue

                # Text command
                msg = update.get("message", {})
                if not msg:
                    continue
                if str(msg.get("from", {}).get("id", "")) != str(OWNER_ID):
                    continue

                text = (msg.get("text") or "").strip()
                chat_id = msg.get("chat", {}).get("id", OWNER_ID)

                if text.startswith("/approve"):
                    parts = text.split()
                    if len(parts) < 2:
                        _tg_post("sendMessage", {
                            "chat_id": chat_id,
                            "text": "Usage: /approve <proposal_id>",
                        })
                        continue
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        _tg_post("sendMessage", {
                            "chat_id": chat_id,
                            "text": "Invalid proposal ID.",
                        })
                        continue
                    ok, reply = hitl_approve_proposal(pid)
                    _tg_post("sendMessage", {
                        "chat_id": chat_id,
                        "text": ("✅ " if ok else "❌ ") + reply,
                    })

                elif text.startswith("/reject"):
                    parts = text.split()
                    if len(parts) < 2:
                        _tg_post("sendMessage", {
                            "chat_id": chat_id, "text": "Usage: /reject <proposal_id>",
                        })
                        continue
                    try:
                        pid = int(parts[1])
                    except ValueError:
                        _tg_post("sendMessage", {
                            "chat_id": chat_id, "text": "Invalid proposal ID.",
                        })
                        continue
                    ok, reply = hitl_reject_proposal(pid)
                    _tg_post("sendMessage", {
                        "chat_id": chat_id,
                        "text": ("✅ " if ok else "❌ ") + reply,
                    })

                elif text == "/pending":
                    proposals = get_pending_proposals_for_hitl()
                    if not proposals:
                        _tg_post("sendMessage", {
                            "chat_id": chat_id, "text": "No pending proposals.",
                        })
                    else:
                        lines = ["*PENDING PROPOSALS*\n"]
                        for p in proposals:
                            lines.append(
                                f"ID: `{p['id']}` [{p['status']}]\n"
                                f"Type: {p['proposal_type']}\n"
                                f"Action: {str(p.get('suggested_action',''))[:80]}\n"
                                f"Confidence: {float(p.get('confidence',0)):.2f}\n"
                            )
                        _tg_post("sendMessage", {
                            "chat_id": chat_id,
                            "text": "\n".join(lines),
                            "parse_mode": "Markdown",
                        })

                elif text == "/status":
                    try:
                        with get_connection() as conn:
                            wallet = conn.execute(
                                "SELECT wallet_balance, initial_capital "
                                "FROM system_state WHERE id=1"
                            ).fetchone()
                            open_pos = conn.execute(
                                "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
                            ).fetchone()[0]
                            halt = conn.execute(
                                "SELECT value FROM system_config "
                                "WHERE key='DRAWDOWN_HALT_ACTIVE'"
                            ).fetchone()
                        bal  = float(wallet["wallet_balance"] if wallet else 0)
                        init = float(wallet["initial_capital"] if wallet else 1000)
                        roi  = ((bal - init) / max(init, 1)) * 100
                        status_str = "⚠️ HALT ACTIVE" if (halt and halt["value"] == "1") else "✅ TRADING"
                        _tg_post("sendMessage", {
                            "chat_id": chat_id,
                            "text": (
                                f"*SENTINUITY STATUS*\n"
                                f"Status: {status_str}\n"
                                f"Wallet: ${bal:.2f} ({roi:+.2f}% ROI)\n"
                                f"Open positions: {open_pos}"
                            ),
                            "parse_mode": "Markdown",
                        })
                    except Exception as e:
                        _tg_post("sendMessage", {
                            "chat_id": chat_id, "text": f"Status check failed: {e}",
                        })

                elif text.startswith("/debate") or text.startswith("/research"):
                    # OPERATOR OVERRIDE — seeds a new research proposal immediately.
                    # Supports forge flags:
                    #   /debate <topic>
                    #   /debate <topic> --forge --source services/foo.py --func bar
                    #   /debate <topic> --forge --source services/foo.py --lines 10-50
                    raw_args = text.split(None, 1)[1].strip() if len(text.split(None, 1)) > 1 else ""

                    # Parse --forge flags
                    _forge_mode   = "--forge" in raw_args
                    _source_file  = ""
                    _func_name    = ""
                    _line_range   = ""
                    _topic_parts  = raw_args

                    if _forge_mode:
                        import re as _re_d
                        _src_m   = _re_d.search(r'--source\s+(\S+)', raw_args)
                        _func_m  = _re_d.search(r'--func\s+(\S+)', raw_args)
                        _lines_m = _re_d.search(r'--lines\s+(\d+)-(\d+)', raw_args)
                        _source_file = _src_m.group(1)  if _src_m  else ""
                        _func_name   = _func_m.group(1)  if _func_m else ""
                        _line_range  = f"{_lines_m.group(1)}-{_lines_m.group(2)}" if _lines_m else ""
                        # Strip flags from topic
                        _topic_parts = _re_d.sub(
                            r'--(?:forge|source|func|lines)\s*\S*', "", raw_args
                        ).strip()

                    # If forge mode, inject live code as context
                    _forge_context = ""
                    if _forge_mode and _source_file:
                        try:
                            _ls, _le = (int(x) for x in _line_range.split("-")) if _line_range else (0, 0)
                            _forge_context = extract_live_code(
                                _source_file, _func_name, _ls, _le
                            )
                            log.info(
                                "FORGE_MODE: extracted %d chars from %s::%s",
                                len(_forge_context), _source_file, _func_name or "top",
                            )
                        except Exception as _fe:
                            _forge_context = f"ERROR extracting live code: {_fe}"

                    try:
                        _operator_override_debate(
                            chat_id, _topic_parts,
                            forge_mode=_forge_mode,
                            forge_context=_forge_context,
                            source_file=_source_file,
                            func_name=_func_name,
                        )
                    except Exception as _oe:
                        log.warning("operator_override_debate failed: %s", _oe)
                        _tg_post("sendMessage", {
                            "chat_id": chat_id,
                            "text": f"❌ Override failed: {_oe}",
                        })

                elif text == "/help":
                    _tg_post("sendMessage", {
                        "chat_id": chat_id,
                        "parse_mode": "Markdown",
                        "text": (
                            "*SENTINUITY COMMAND REFERENCE*\n\n"
                            "`/approve <id>` — Approve a proposal\n"
                            "`/reject <id>` — Reject a proposal\n"
                            "`/pending` — List pending proposals\n"
                            "`/status` — Wallet + position summary\n"
                            "`/debate [topic]` — Override: retire current debate, start fresh\n"
                            "`/research [topic]` — Alias for /debate\n"
                            "`/help` — This message\n\n"
                            "_Debate override is instant — POLARIS picks up within 60s._"
                        ),
                    })

        except Exception as e:
            log.warning("HITL bot loop error: %s", e)
            time.sleep(10)


# ═════════════════════════════════════════════════════════════════════════════
# MAIN
# ═════════════════════════════════════════════════════════════════════════════

def run() -> None:
    _nim_key = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
    if not (_nim_key or OPENAI_KEY):
        log.error("No approved Council provider key — NIM primary or OpenAI fallback required")
        update_heartbeat(
            SERVICE_NAME,
            "ERROR",
            "NVIDIA_NIM_API_KEY and OPENAI_API_KEY both unavailable — governor cannot start",
        )
        return
    # IVARIS key check — provider-aware
    _ivaris_provider = str(get_config_value("IVARIS_PROVIDER", "nim")).strip().lower()
    if _ivaris_provider == "anthropic" and not os.getenv("ANTHROPIC_API_KEY", "").strip():
        log.error("ANTHROPIC_API_KEY not set — IVARIS cannot critique (provider=anthropic)")
        update_heartbeat(SERVICE_NAME, "ERROR", "ANTHROPIC_API_KEY not set — governor cannot start")
        return
    if _ivaris_provider == "gemini":
        log.warning("Retired IVARIS provider 'gemini' found in system_config; normalising runtime route to NIM")
        _ivaris_provider = "nim"

    log.info("SOVEREIGN GOVERNOR ONLINE")
    log.info("Max rounds: %d | Consensus floor: %.2f", MAX_ROUNDS, CONSENSUS_FLOOR)
    log.info("ORACLE (external evidence): %s", "ACTIVE" if BRAVE_KEY else "NOT CONFIGURED")
    log.info("HITL Required: %s", str(get_config_value("HITL_REQUIRED", "1")))

    # Task E — startup sanity check: resolve and log the live IVARIS routing config.
    # This is the single source of truth for which provider will be used at runtime.
    # Check happens AFTER the key-presence guard above so we know the key exists.
    _startup_provider = str(get_config_value("IVARIS_PROVIDER", "nim")).strip().lower()
    _startup_model    = str(get_config_value("IVARIS_MODEL",    "claude-haiku-4-5-20251001")).strip()
    log.info(
        "IVARIS STARTUP CONFIG: provider=%s model=%s "
        "ANTHROPIC_API_KEY_present=%s NIM_API_KEY_present=%s "
        "soft_cap=%d/hr hard_cap=%d/hr",
        _startup_provider,
        _startup_model,
        bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
        bool(os.getenv("NVIDIA_NIM_API_KEY", "").strip()),
        IVARIS_SOFT_CAP_PER_HOUR,
        IVARIS_HARD_CAP_PER_HOUR,
    )
    log.info(
        "NUGGET STARTUP CONFIG: enabled=%s model=%s NIM_API_KEY_present=%s "
        "cooldown=%ds min_rounds=%d repair_always_esc=%s act_confidence=%.2f",
        NUGGET_ENABLED,
        NUGGET_MODEL,
        bool(os.getenv("NVIDIA_NIM_API_KEY", "").strip()),
        NUGGET_COOLDOWN_SEC,
        NUGGET_MIN_ROUNDS_FOR_ESC,
        NUGGET_REPAIR_ALWAYS_ESC,
        NUGGET_ACT_CONFIDENCE,
    )
    if _startup_provider == "anthropic" and _startup_model.startswith("gemini"):
        log.warning(
            "IVARIS CONFIG MISMATCH: provider=anthropic but model=%s looks like a Gemini model. "
            "Update IVARIS_MODEL in system_config to a Claude model name.",
            _startup_model,
        )
    if _startup_provider == "gemini":
        log.warning(
            "IVARIS provider=gemini is retired; runtime uses NIM. Update IVARIS_PROVIDER to nim.",
        )

    # Ensure HITL_REQUIRED config row exists
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO system_config (key, value, description)
                VALUES ('HITL_REQUIRED', '1',
                        '1=require human approval, 0=auto-apply after consensus')
            """)
            conn.commit()
    except Exception:
        pass

    # Start HITL bot in daemon thread
    import threading
    hitl_thread = threading.Thread(
        target=run_hitl_bot, daemon=True, name="hitl-bot",
    )
    hitl_thread.start()
    log.info("HITL bot thread started")

    update_heartbeat(SERVICE_NAME, "ALIVE", "Sovereign governor online")

    # Startup stabilisation delay
    log.info("Waiting 30s for services to stabilise...")
    time.sleep(30)

    while True:
        try:
            run_cycle()
        except Exception as e:
            log.exception("GOVERNOR CYCLE ERROR: %s", e)
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()