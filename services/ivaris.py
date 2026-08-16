"""
services/ivaris.py
==================
IVARIS — Adversarial Critic Mind
=================================
NIM doctrine: powered by meta/llama-3.3-70b-instruct via NVIDIA NIM.
Gemini dependency REMOVED. GEMINI_API_KEY is never read here.

Model routing:
  Primary:  NIM (NVIDIA integrate.api.nvidia.com) — free, 40 RPM
  Fallback: another NIM model selected by the NVIDIA model registry

Config key: IVARIS_NIM_MODEL in system_config (default: meta/llama-3.3-70b-instruct)
Called by: sovereign_governor.py _call_ivaris() — not run directly.

Doctrine (LOCKED):
  POLARIS = generation (builder)
  IVARIS  = critique (NVIDIA NIM only; registry rotates models)
  NUGGET  = audit (NIM / Kimi ONLY)
  AXON    = deterministic validation (NO LLM)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

# IVARIS_PROVIDER_RESILIENCE_20260814:
# IVARIS is imported from more than one Council process.  Do not rely on the
# caller having loaded .env first; use the same repository-root environment
# contract as debate_engine so a valid NIM key cannot disappear merely because
# import order changed.
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=False)
except Exception:
    pass

log = logging.getLogger("ivaris")

# NIM primary model — read from DB config at call time
IVARIS_NIM_MODEL_DEFAULT = "meta/llama-3.3-70b-instruct"

NIM_BASE_URL = "https://integrate.api.nvidia.com/v1/chat/completions"


# Provider outage circuit. A dead tether or DNS outage must not trigger a new
# 30-second NIM/Anthropic request on every Council cycle.
_PROVIDER_FAILS = {"nim": 0}
_PROVIDER_BACKOFF_UNTIL = {"nim": 0.0}

def _provider_ready(name: str) -> bool:
    return time.time() >= float(_PROVIDER_BACKOFF_UNTIL.get(name, 0.0))

def _provider_success(name: str) -> None:
    _PROVIDER_FAILS[name] = 0
    _PROVIDER_BACKOFF_UNTIL[name] = 0.0

def _provider_failure(name: str) -> float:
    failures = int(_PROVIDER_FAILS.get(name, 0)) + 1
    _PROVIDER_FAILS[name] = failures
    delay = min(1800.0, 60.0 * (2 ** min(failures - 1, 5)))
    _PROVIDER_BACKOFF_UNTIL[name] = time.time() + delay
    return delay

def _safe_error(exc: Exception) -> str:
    text = str(exc)
    for secret in (os.getenv("NVIDIA_NIM_API_KEY", ""),):
        if secret:
            text = text.replace(secret, "<redacted>")
    text = re.sub(r"([?&](?:api[-_]?key|token|key)=)[^&\s]+", r"\1<redacted>", text, flags=re.I)
    return " ".join(text.split())[:220]

IVARIS_SYSTEM_PROMPT = """You are IVARIS — the immune system of the Sentinuity sovereign trading organism.
Your role: adversarial critic. Find every reason a proposal could fail.
Be specific. Be brutal. Be fair.
Output JSON only: {"verdict": "APPROVE|REJECT|DEBATE", "confidence": 0.0-1.0,
"objections": [...], "merge_hint": "...", "alternative_direction": "..."}"""


def _try_nim(system: str, user: str) -> Optional[str]:
    """Call IVARIS through distinct NIM models with model-aware failover.

    A 404/410/empty response is a MODEL failure and must not poison the whole
    NVIDIA provider.  401/403/429 are provider/account conditions.  Timeouts
    rotate the model immediately; provider backoff is armed only after the
    available distinct-model pool has been exhausted, so one overloaded model
    cannot suppress a healthy replacement.
    """
    nim_key = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
    if not nim_key:
        return None
    if not _provider_ready("nim"):
        return None

    try:
        from services.nvidia_model_registry import (
            get_assignment, rotate_after_failure, is_quarantined,
        )
    except Exception:
        get_assignment = None
        rotate_after_failure = None
        is_quarantined = None

    model = (
        get_assignment("IVARIS", IVARIS_NIM_MODEL_DEFAULT)
        if get_assignment else IVARIS_NIM_MODEL_DEFAULT
    )
    attempted = set()
    provider_faults = []
    last_failure = ""

    # Four distinct attempts matches the role registry's intended candidate
    # depth without hammering a provider indefinitely.
    for attempt in range(4):
        model = str(model or "").strip()
        if not model:
            break
        if model.lower().startswith("claude"):
            model = IVARIS_NIM_MODEL_DEFAULT
        if model in attempted:
            if rotate_after_failure:
                model = rotate_after_failure(
                    "IVARIS", model, "duplicate_candidate", refresh=False
                )
                continue
            break
        if is_quarantined:
            try:
                if is_quarantined(model):
                    if rotate_after_failure:
                        model = rotate_after_failure(
                            "IVARIS", model, "candidate_already_quarantined",
                            refresh=(attempt == 0),
                        )
                        continue
                    break
            except Exception:
                pass

        attempted.add(model)
        payload = json.dumps({
            "model": model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": 600,
            "temperature": 0.3,
        }).encode()
        req = urllib.request.Request(
            NIM_BASE_URL, data=payload,
            headers={
                "Authorization": f"Bearer {nim_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        failure = ""
        provider_wide = False
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8", "replace"))
                text = str(
                    ((((data.get("choices") or [{}])[0].get("message") or {})
                      .get("content")) or "")
                ).strip()
                if text:
                    _provider_success("nim")
                    return text
                failure = "EMPTY_CONTENT_200"
        except urllib.error.HTTPError as exc:
            try:
                body = exc.read().decode("utf-8", "replace")[:180]
            except Exception:
                body = ""
            failure = f"HTTP {exc.code}:{body}"
            # Auth/account/rate pressure is provider-wide.  404/410 is a dead
            # model endpoint and should rotate without provider quarantine.
            provider_wide = int(exc.code) in (401, 403, 429)
        except Exception as exc:
            failure = f"{type(exc).__name__}:{_safe_error(exc)}"
            # Connection/DNS/timeout can be a provider path problem, but do not
            # arm provider backoff until multiple distinct models have failed.
            provider_wide = isinstance(
                exc, (TimeoutError, urllib.error.URLError)
            )

        last_failure = failure
        if provider_wide:
            provider_faults.append(failure)

        log.warning(
            "IVARIS_NIM_MODEL_FAILED model=%s provider_wide=%s reason=%s",
            model, provider_wide, failure,
        )

        if rotate_after_failure:
            model = rotate_after_failure(
                "IVARIS", model, failure,
                # One catalogue refresh is enough; later attempts use the
                # persisted/current catalogue rather than repeatedly probing.
                refresh=(attempt == 0),
            )
        else:
            break

    # Provider backoff is a LAST resort after distinct-model failover, not a
    # side effect of one retired or overloaded model.
    if provider_faults and len(attempted) >= 2:
        delay = _provider_failure("nim")
        log.warning(
            "IVARIS_NIM_PROVIDER_BACKOFF distinct_models=%d backoff=%.0fs last=%s",
            len(attempted), delay, last_failure,
        )
    return None


def _parse_json(text: str) -> Optional[dict]:
    """Extract first JSON object from response text."""
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        pass
    match = re.search(r'\{.*\}', text, re.DOTALL)
    if match:
        try:
            return json.loads(match.group())
        except Exception:
            pass
    return None


class IvarisClient:
    """IVARIS adversarial critic. NVIDIA NIM only; model registry handles rotation."""

    def critique(self, proposal: dict, trade_context: dict) -> dict:
        # SIGNOFF_20260810_IVARIS_NULL_CONTRACT
        # Council proposals legitimately contain nullable text fields.  Treat
        # absence as empty text; a critic must never crash the whole debate
        # because suggested_action/proposal_text is NULL in SQLite.
        proposal = proposal if isinstance(proposal, dict) else {}
        trade_context = trade_context if isinstance(trade_context, dict) else {}
        ptype  = str(proposal.get("proposal_type") or "UNKNOWN")
        ptext  = str(proposal.get("proposal_text") or "")[:600]
        action = str(proposal.get("suggested_action") or "")[:300]
        try:
            conf = float(proposal.get("confidence") or 0.0)
        except (TypeError, ValueError):
            conf = 0.0

        user_msg = f"""PROPOSAL TYPE: {ptype}
CONFIDENCE: {conf:.2f}
PROPOSAL: {ptext}
SUGGESTED ACTION: {action}
CONTEXT: {json.dumps(trade_context, default=str)[:400]}

Critique this proposal. Output JSON only."""

        raw = _try_nim(IVARIS_SYSTEM_PROMPT, user_msg)

        if not raw:
            return {
                "verdict": "DEBATE",
                "confidence": 0.0,
                "objections": ["IVARIS unavailable: NIM model pool failed"],
                "merge_hint": "",
                "alternative_direction": "",
                "_ivaris_failed": True,
            }

        parsed = _parse_json(raw)
        if not parsed:
            return {
                "verdict": "DEBATE",
                "confidence": 0.0,
                "objections": ["Could not parse IVARIS response"],
                "merge_hint": "",
                "alternative_direction": "",
            }

        parsed.setdefault("verdict", "DEBATE")
        parsed.setdefault("confidence", 0.5)
        parsed.setdefault("objections", [])
        return parsed

    def evaluate_rebuttal(
        self,
        proposal: dict,
        polaris_rebuttal: dict,
        ivaris_critique: dict,
    ) -> dict:
        proposal = proposal if isinstance(proposal, dict) else {}
        polaris_rebuttal = polaris_rebuttal if isinstance(polaris_rebuttal, dict) else {}
        ivaris_critique = ivaris_critique if isinstance(ivaris_critique, dict) else {
            "verdict": "DEBATE", "confidence": 0.0,
            "objections": ["IVARIS critique contract unavailable"],
        }
        user_msg = f"""ORIGINAL CRITIQUE:
{json.dumps(ivaris_critique.get('objections', []), indent=2)}

POLARIS REBUTTAL:
{str(polaris_rebuttal.get('summary') or '')[:400]}

Has POLARIS adequately addressed the objections?
Output updated JSON verdict only."""

        raw = _try_nim(IVARIS_SYSTEM_PROMPT, user_msg)

        if not raw:
            return ivaris_critique

        parsed = _parse_json(raw)
        return parsed if parsed else ivaris_critique


def get_polaris_rebuttal(
    proposal: dict,
    ivaris_critique: dict,
    round_num: int = 1,
) -> Optional[dict]:
    """
    Ask POLARIS (via NIM) to rebut IVARIS's objections.
    Returns rebuttal dict or None.
    """
    objections = ivaris_critique.get("objections", [])
    if not objections:
        return None

    user_msg = f"""ROUND {round_num} — POLARIS REBUTTAL

Your proposal was critiqued. Objections:
{json.dumps(objections[:5], indent=2)}

Provide a concise rebuttal addressing each objection.
Output JSON: {{"summary": "...", "proposal_adjusted": true/false,
"addressed_objections": [...], "remaining_concerns": [...]}}"""

    system = "You are POLARIS — sovereign architect. Defend or adjust your proposal based on critique."

    raw = _try_nim(system, user_msg)
    if not raw:
        raw = _try_anthropic(system, user_msg)

    if not raw:
        return None

    return _parse_json(raw)


# ─────────────────────────────────────────────────────────────────────────────
# IVARIS_CONTRACT_COMPAT_20260714
# Historical call sites (and the debate engine's public contract) referenced
# the name `Ivaris`. The production implementation is IvarisClient above.
# This alias restores the backward-compatible import contract without
# duplicating or stubbing any behaviour:
#     from services.ivaris import Ivaris        # legacy contract
#     from services.ivaris import IvarisClient   # current contract
# Both resolve to the same real client. Constructor arguments, review,
# critique and model-call methods are unchanged.
# ─────────────────────────────────────────────────────────────────────────────
Ivaris = IvarisClient
