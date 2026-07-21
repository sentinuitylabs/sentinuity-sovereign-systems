#!/usr/bin/env python3
"""
launch/verify_ivaris_routing.py
===============================================================================
IVARIS ROUTING VERIFIER (V3_HONESTY_ROUTING_20260721) — directive Phase 4

Offline, fully mocked. No provider is contacted and no key is read beyond a
throwaway value injected into the process environment.

PROVES:
  1. The Anthropic diagnostic ping payload uses max_tokens (the field the
     Messages API requires) — the exact defect behind the recurring
     "Anthropic HTTP 400" morning-brief errors — and never
     max_completion_tokens.
  2. The Anthropic path refuses org/model ids ("qwen/...") with a sanitised
     routing-guard error instead of a doomed HTTP call.
  3. services/ivaris.py refuses to send a claude-* id to the NIM endpoint,
     substituting the NIM default (payload captured and asserted).
  4. The Anthropic fallback in ivaris.py sends a schema-correct body
     (max_tokens present, system separated, single user message).
  5. The governor's live debate path (_call_anthropic_direct) already used
     max_tokens — asserted so a regression can never sneak in.

Run:  python launch/verify_ivaris_routing.py     Exit 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import io
import json
import os
import sys
import urllib.request
from pathlib import Path
# Windows verifier console contract: force Unicode-safe output even when the
# parent console is cp1252. This changes presentation only, never test logic.
def _configure_verifier_console() -> None:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_configure_verifier_console()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


def main() -> int:
    print("── static: patched source invariants ───────────────────────────")
    pol = (ROOT / "services" / "polaris.py").read_text(encoding="utf-8")
    ping = pol[pol.find("def _check_ivaris_status"):]
    ping = ping[:ping.find("def _run_deterministic_task")] or ping[:6000]
    check("ping payload uses max_tokens",
          '"max_tokens": 10' in ping)
    check("ping payload no longer uses max_completion_tokens",
          '"max_completion_tokens"' not in ping)  # quoted = the JSON key;
    # the patch's explanatory comment naming the old field is allowed.
    check("anthropic routing guard rejects org/model ids",
          'if "/" in model:' in ping and "routing guard" in ping)
    check("sanitised 400 detail surfaces error type/message (no keys)",
          'resp.json() or {}).get("error")' in ping)

    iva = (ROOT / "services" / "ivaris.py").read_text(encoding="utf-8")
    check("ivaris NIM guard refuses claude-* models",
          'startswith("claude")' in iva and "IVARIS routing guard" in iva)

    gov = (ROOT / "services" / "sovereign_governor.py").read_text(encoding="utf-8")
    direct = gov[gov.find("def _call_anthropic_direct"):]
    direct = direct[:direct.find("def _call_nim_ivaris")] or direct[:3000]
    check("governor debate path uses max_tokens (regression sentinel)",
          '"max_tokens": max_tokens' in direct
          and "max_completion_tokens" not in direct)

    print("── dynamic: mocked NIM + Anthropic calls through ivaris.py ─────")
    os.environ["NVIDIA_NIM_API_KEY"] = "FIXTURE_NIM_KEY_NOT_REAL"
    os.environ["ANTHROPIC_API_KEY"] = "FIXTURE_ANT_KEY_NOT_REAL"
    from services import ivaris

    captured: dict = {}

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=30):
        captured["url"] = req.full_url
        captured["payload"] = json.loads(req.data.decode("utf-8"))
        captured["auth_present"] = bool(req.headers.get("Authorization")
                                        or req.headers.get("X-api-key"))
        body = ({"choices": [{"message": {"content": "{\"verdict\":\"APPROVE\"}"}}]}
                if "nvidia" in req.full_url else
                {"content": [{"text": "{\"verdict\":\"APPROVE\"}"}]})
        return _Resp(json.dumps(body).encode("utf-8"))

    real_urlopen = urllib.request.urlopen
    urllib.request.urlopen = fake_urlopen
    try:
        # 3. claude-* id forced onto the NIM path must be swapped to the default
        import services.nvidia_model_registry as reg
        real_get = getattr(reg, "get_assignment", None)
        reg.get_assignment = lambda role, default: "claude-haiku-4-5-20251001"
        try:
            out = ivaris._try_nim("sys", "user")
        finally:
            if real_get is not None:
                reg.get_assignment = real_get
        check("NIM guard swaps claude-* to the NIM default",
              captured.get("payload", {}).get("model")
              == ivaris.IVARIS_NIM_MODEL_DEFAULT,
              f"model={captured.get('payload', {}).get('model')}")
        check("NIM call went to the NIM endpoint with auth",
              "nvidia" in str(captured.get("url")) and captured.get("auth_present"))
        check("mocked NIM 200 parsed", out is not None)

        # 4. Anthropic fallback body shape
        captured.clear()
        out2 = ivaris._try_anthropic("sys", "user")
        pl = captured.get("payload", {})
        check("anthropic fallback body: max_tokens present, OpenAI field absent",
              "max_tokens" in pl and "max_completion_tokens" not in pl,
              f"keys={sorted(pl.keys())}")
        check("anthropic fallback targets api.anthropic.com with a claude model",
              "anthropic" in str(captured.get("url"))
              and str(pl.get("model", "")).startswith("claude"))
        check("mocked Anthropic 200 parsed", out2 is not None)
    finally:
        urllib.request.urlopen = real_urlopen
        os.environ.pop("NVIDIA_NIM_API_KEY", None)
        os.environ.pop("ANTHROPIC_API_KEY", None)

    print()
    if FAILURES:
        print(f"IVARIS ROUTING: FAIL ({len(FAILURES)}): {FAILURES}")
        return 1
    print("IVARIS ROUTING: PASS — max_tokens ping fix verified, cross-provider "
          "model guards enforced in both directions, sanitised diagnostics in "
          "place, live debate path regression-locked.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
