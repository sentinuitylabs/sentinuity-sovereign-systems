"""
Retired compatibility module.

Gemini is not connected to Sentinuity.  This filename remains only so stale
imports fail safely during migration; all calls route through the approved
NVIDIA NIM primary / OpenAI fallback client.
"""
from __future__ import annotations

from services.llm_client import get_last_error, polaris_complete


def gemini_query(prompt: str) -> str:
    """Legacy alias; never reads GEMINI_API_KEY and never calls Google."""
    result = polaris_complete(
        "You are NUGGET, an advisory Sentinuity council reviewer.",
        str(prompt or ""),
        task_type="routine_summary",
        risk_level="low",
        max_tokens=900,
        temperature=0.4,
    )
    if result and str(result.get("text") or "").strip():
        return str(result["text"]).strip()
    return f"[NUGGET_ERROR] {get_last_error() or 'approved providers unavailable'}"
