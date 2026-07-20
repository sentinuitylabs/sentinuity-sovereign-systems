"""
services/llm_client.py
======================
Council/Polaris model client using NVIDIA NIM first and OpenAI second.

Public contract:
- polaris_complete(...) -> routing/result dict or None
- get_last_error() -> stable diagnostic for last failed call

Safety: model routing never changes trading configuration or authorises live execution.
"""
from __future__ import annotations

import logging
import os
import sys
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).resolve().parent.parent
try:
    from dotenv import load_dotenv
    load_dotenv(BASE_DIR / ".env", override=True)
except Exception:
    pass
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from services.model_router import choose_model, log_routing_decision

logger = logging.getLogger("llm_client")
_LAST_ERROR: Optional[str] = None

def _reload_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(BASE_DIR / ".env", override=True)
    except Exception:
        pass

def _set_last_error(value: Optional[str]) -> None:
    global _LAST_ERROR
    _LAST_ERROR = value

def get_last_error() -> Optional[str]:
    return _LAST_ERROR

def available_providers() -> list[str]:
    _reload_env()
    out=[]
    if os.getenv("NVIDIA_NIM_API_KEY", "").strip(): out.append("nim")
    if os.getenv("OPENAI_API_KEY", "").strip(): out.append("openai")
    return out

def _http_error(resp) -> str:
    status = getattr(resp, "status_code", None)
    detail = ""
    try:
        body = resp.json()
        err = body.get("error", {}) if isinstance(body, dict) else {}
        detail = str(err.get("code") or err.get("type") or err.get("message") or "")
    except Exception:
        detail = str(getattr(resp, "text", ""))
    detail = " ".join(detail.split())[:220]
    return f"HTTP_{status}:{detail}" if detail else f"HTTP_{status}"

def _post_chat(url: str, key: str, model: str, system_prompt: str, user_message: str,
               max_tokens: int, temperature: float, provider: str) -> tuple[Optional[str], Optional[str]]:
    try:
        import requests
        body={
            "model": model,
            "messages": [
                {"role":"system","content":system_prompt},
                {"role":"user","content":user_message},
            ],
            "temperature": temperature,
        }
        if provider == "openai":
            body["max_completion_tokens"] = max_tokens
        else:
            body["max_tokens"] = max_tokens
        resp=requests.post(url, headers={"Authorization":f"Bearer {key}","Content-Type":"application/json"},
                           json=body, timeout=35)
        if not resp.ok:
            return None, _http_error(resp)
        payload=resp.json(); choices=payload.get("choices") or []
        text=str(((choices[0].get("message") or {}).get("content") if choices else "") or "").strip()
        return (text, None) if text else (None, "EMPTY_CONTENT_200")
    except Exception as exc:
        name=exc.__class__.__name__.upper()
        code="TIMEOUT" if "TIMEOUT" in name or "timed out" in str(exc).lower() else "MODEL_REQUEST_ERROR"
        return None, f"{code}:{str(exc)[:220]}"

def polaris_complete(system_prompt: str, user_message: str, *, task_type: str="routine_summary",
                     risk_level: str="low", live_trade: bool=False, code_touch: bool=False,
                     code_touch_file: Optional[str]=None, stalemate: bool=False, confidence_gap: float=0.0,
                     max_tokens: int=600, temperature: float=1.0) -> Optional[dict]:
    _set_last_error(None); _reload_env()
    try:
        decision=choose_model(task_type=task_type, risk_level=risk_level, confidence_gap=confidence_gap,
                              live_trade=live_trade, code_touch=code_touch, code_touch_file=code_touch_file,
                              stalemate=stalemate, prompt_hint=(user_message or "")[:120])
    except Exception as exc:
        _set_last_error(f"MODEL_ROUTER_ERROR:{str(exc)[:220]}"); return None
    try:
        log_routing_decision(decision, extra={"task_type":task_type,"risk_level":risk_level,
            "live_trade":live_trade,"code_touch":code_touch,"code_touch_file":code_touch_file or "",
            "stalemate":stalemate})
    except Exception:
        pass

    errors=[]
    nim_key=os.getenv("NVIDIA_NIM_API_KEY", "").strip()
    openai_key=os.getenv("OPENAI_API_KEY", "").strip()
    nim_model=os.getenv("COUNCIL_NIM_MODEL", os.getenv("FAST_NIM_MODEL", "meta/llama-3.3-70b-instruct")).strip()
    openai_model=decision["model"]

    # Polaris is the final planner/coordinator, so Council build/rebuttal work
    # prefers OpenAI when available. NIM remains a real automatic fallback.
    # Other routine workloads retain NIM-first economics unless overridden.
    default_order = "openai,nim" if task_type in {"council_build", "council_rebuttal", "signoff"} else "nim,openai"
    raw_order = os.getenv("POLARIS_PROVIDER_ORDER", default_order)
    order=[]
    for name in (x.strip().lower() for x in raw_order.split(",")):
        if name in {"openai","nim"} and name not in order:
            order.append(name)
    for name in ("openai","nim"):
        if name not in order:
            order.append(name)

    logger.info("POLARIS_PROVIDER_ROUTE task_type=%s order=%s openai=%s nim=%s",
                task_type, ",".join(order), bool(openai_key), bool(nim_key))

    for provider in order:
        if provider == "openai" and openai_key:
            text,err=_post_chat("https://api.openai.com/v1/chat/completions",openai_key,openai_model,
                                system_prompt,user_message,max_tokens,temperature,"openai")
            if text:
                logger.info("POLARIS_PROVIDER_SUCCESS provider=openai model=%s task_type=%s", openai_model, task_type)
                return {"text":text,"model":openai_model,"tier":decision.get("tier"),"reason":decision.get("reason"),
                        "request_id":decision.get("request_id"),"provider":"openai"}
            errors.append(f"openai={err}")
            logger.warning("POLARIS_PROVIDER_FAIL provider=openai model=%s error=%s", openai_model, err)
        elif provider == "nim" and nim_key:
            text,err=_post_chat(os.getenv("NVIDIA_NIM_BASE_URL","https://integrate.api.nvidia.com/v1").rstrip("/")+"/chat/completions",
                                nim_key,nim_model,system_prompt,user_message,max_tokens,temperature,"nim")
            if text:
                logger.info("POLARIS_PROVIDER_SUCCESS provider=nim model=%s task_type=%s", nim_model, task_type)
                return {"text":text,"model":nim_model,"tier":decision.get("tier"),"reason":"nim fallback-safe",
                        "request_id":decision.get("request_id"),"provider":"nim"}
            errors.append(f"nim={err}")
            logger.warning("POLARIS_PROVIDER_FAIL provider=nim model=%s error=%s", nim_model, err)

    if not errors:
        errors.append("NO_PROVIDER_KEYS:NVIDIA_NIM_API_KEY and OPENAI_API_KEY both missing")
    _set_last_error("MODEL_PROVIDERS_UNAVAILABLE:"+" | ".join(errors)[:500])
    logger.warning("polaris_complete unavailable: %s", _LAST_ERROR)
    return None
