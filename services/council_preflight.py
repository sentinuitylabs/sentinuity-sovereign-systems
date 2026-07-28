#!/usr/bin/env python3
"""
council_preflight.py
====================
Read-only provider/model smoke test. Never prints secrets.
Run from trading-bot root:

    python council_preflight.py
"""

from __future__ import annotations

import json
import os
import urllib.request
import urllib.error
from pathlib import Path

try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore

ROOT = Path(__file__).resolve().parents[1]
if load_dotenv:
    load_dotenv(ROOT / ".env", override=True)

OPENAI_KEY = os.getenv("OPENAI_API_KEY", "").strip()
NIM_KEY = os.getenv("NVIDIA_NIM_API_KEY", "").strip()
ANTHROPIC_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
XAI_KEY = os.getenv("XAI_API_KEY", "").strip()
BRAVE_KEY = os.getenv("BRAVE_SEARCH_API_KEY", "").strip()

POLARIS_MODELS = [
    ("POLARIS budget", os.getenv("POLARIS_BUDGET_MODEL", "gpt-5.4-nano")),
    ("POLARIS signoff", os.getenv("POLARIS_SIGNOFF_MODEL", "gpt-5.4-mini")),
    ("POLARIS critical", os.getenv("POLARIS_CRITICAL_MODEL", "gpt-5.5")),
]
try:
    from services.nvidia_model_registry import get_assignment, scan_and_align
except Exception:
    from nvidia_model_registry import get_assignment, scan_and_align
NIM_MODELS = [
    ("IVARIS NIM", get_assignment("IVARIS", "qwen/qwen3.5-397b-a17b")),
    ("NUGGET NIM", get_assignment("NUGGET", "nvidia/nemotron-3-super-120b-a12b")),
    ("AXIOM NIM", get_assignment("AXIOM", "moonshotai/kimi-k2.6")),
]
XAI_MODEL = os.getenv("RHIZA_GROK_MODEL", "grok-4.3")
ANTHROPIC_MODEL = os.getenv("IVARIS_ANTHROPIC_MODEL", "claude-haiku-4-5-20251001")


def _post_json(url: str, headers: dict, payload: dict, timeout: int = 20) -> tuple[int, str]:
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _get(url: str, headers: dict, timeout: int = 15) -> tuple[int, str]:
    req = urllib.request.Request(url, headers=headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return int(r.status), r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return int(e.code), e.read().decode("utf-8", "replace")
    except Exception as e:
        return 0, f"{type(e).__name__}: {e}"


def _status_from(code: int, body: str) -> tuple[str, str]:
    b = (body or "").replace("\n", " ")[:180]
    if code == 200:
        return "ALIVE", "OK"
    if code in (401, 403):
        return "BAD_KEY", f"HTTP {code}: {b}"
    if code in (404, 410):
        return "BAD_MODEL", f"HTTP {code}: {b}"
    if code == 429:
        return "RATE_LIMIT", f"HTTP {code}: {b}"
    if code == 400 and ("max_tokens" in body or "max_completion_tokens" in body):
        return "PARAM_ERROR", f"HTTP 400: {b}"
    return "ERROR", f"HTTP {code}: {b}" if code else b


def check_openai(name: str, model: str) -> tuple[str, str, str]:
    if not OPENAI_KEY:
        return name, "MISSING_KEY", model
    code, body = _post_json(
        "https://api.openai.com/v1/chat/completions",
        {"Authorization": f"Bearer {OPENAI_KEY}", "Content-Type": "application/json"},
        {
            "model": model,
            # GPT-5-style chat models require max_completion_tokens, not max_tokens.
            "max_completion_tokens": 8,
            "messages": [{"role": "user", "content": "Reply OK."}],
        },
    )
    st, detail = _status_from(code, body)
    return name, st, f"{model} | {detail}"


def check_nim(name: str, model: str) -> tuple[str, str, str]:
    if not NIM_KEY:
        return name, "MISSING_KEY", model
    code, body = _post_json(
        "https://integrate.api.nvidia.com/v1/chat/completions",
        {"Authorization": f"Bearer {NIM_KEY}", "Content-Type": "application/json"},
        {"model": model, "max_tokens": 8, "messages": [{"role": "user", "content": "Reply OK."}]},
    )
    st, detail = _status_from(code, body)
    return name, st, f"{model} | {detail}"


def check_anthropic() -> tuple[str, str, str]:
    if not ANTHROPIC_KEY:
        return "IVARIS fallback", "MISSING_KEY", ANTHROPIC_MODEL
    code, body = _post_json(
        "https://api.anthropic.com/v1/messages",
        {"x-api-key": ANTHROPIC_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
        {"model": ANTHROPIC_MODEL, "max_tokens": 8, "messages": [{"role": "user", "content": "Reply OK."}]},
    )
    st, detail = _status_from(code, body)
    return "IVARIS fallback", st, f"{ANTHROPIC_MODEL} | {detail}"


def check_xai() -> tuple[str, str, str]:
    if not XAI_KEY:
        return "RHIZA/GROK", "MISSING_KEY", XAI_MODEL
    code, body = _post_json(
        "https://api.x.ai/v1/chat/completions",
        {"Authorization": f"Bearer {XAI_KEY}", "Content-Type": "application/json"},
        {"model": XAI_MODEL, "max_tokens": 8, "messages": [{"role": "user", "content": "Reply OK."}]},
    )
    st, detail = _status_from(code, body)
    return "RHIZA/GROK", st, f"{XAI_MODEL} | {detail}"


def check_brave() -> tuple[str, str, str]:
    if not BRAVE_KEY:
        return "ORACLE Brave", "MISSING_KEY", "BRAVE_SEARCH_API_KEY"
    code, body = _get(
        "https://api.search.brave.com/res/v1/web/search?q=sentinuity&count=1",
        {"Accept": "application/json", "X-Subscription-Token": BRAVE_KEY},
    )
    st, detail = _status_from(code, body)
    return "ORACLE Brave", st, detail


def main() -> int:
    try:
        scan = scan_and_align(probe=True)
        print(f"NVIDIA catalogue aligned: {scan.get('count', 0)} models; version={scan.get('catalogue_version', 'unknown')}")
    except Exception as exc:
        print(f"NVIDIA catalogue alignment warning: {exc}")
    rows = []
    for name, model in POLARIS_MODELS:
        rows.append(check_openai(name, model))
    for name, model in NIM_MODELS:
        rows.append(check_nim(name, model))
    rows.append(check_anthropic())
    rows.append(check_xai())
    rows.append(check_brave())

    print("COUNCIL PREFLIGHT — provider/model smoke test")
    print(f"{'AGENT':<18} {'STATUS':<13} DETAIL")
    print("-" * 88)
    bad = 0
    for agent, status, detail in rows:
        ok = status == "ALIVE"
        bad += 0 if ok else 1
        mark = "OK " if ok else "!! "
        print(f"{agent:<18} {mark}{status:<10} {detail[:150]}")
    print("-" * 88)
    print("PASS" if bad == 0 else f"{bad} check(s) need attention.")
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
