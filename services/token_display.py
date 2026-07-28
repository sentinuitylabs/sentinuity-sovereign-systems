"""Public-safe token identity display helper.

Trading identity remains the mint address. Human-readable metadata is display-only.
Resolution order: supplied symbol/name -> persistent cache -> bounded public metadata
lookup -> shortened mint. Resolver failures never gate or mutate trading decisions.
"""
from __future__ import annotations

from functools import lru_cache
from typing import Any, Dict

_BAD = {"", "n/a", "na", "none", "null", "unknown", "undefined", "-", "?"}


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().replace("\x00", "")
    return text if text and text.lower() not in _BAD else None


def short_mint(mint: Any) -> str:
    text = _clean(mint)
    if not text:
        return "unknown"
    return text if len(text) <= 12 else f"{text[:4]}…{text[-4:]}"


@lru_cache(maxsize=4096)
def _identity_for_mint(mint: str) -> Dict[str, Any]:
    """Read cached metadata first, then perform one bounded best-effort lookup."""
    mint = _clean(mint) or ""
    if not mint:
        return {}
    try:
        from services.token_identity import cached_identity, resolve_token_identity
        cached = cached_identity(mint)
        if _clean(cached.get("symbol")) or _clean(cached.get("name")):
            return cached
        return resolve_token_identity(mint, timeout_sec=0.45) or {}
    except Exception:
        return {}


def display_name(symbol=None, token_name=None, mint=None, metadata_name=None) -> str:
    """Return the best human label while preserving mint as canonical identity."""
    supplied = _clean(symbol) or _clean(token_name) or _clean(metadata_name)
    if supplied:
        return supplied
    mint_text = _clean(mint)
    if mint_text:
        identity = _identity_for_mint(mint_text)
        resolved = _clean(identity.get("symbol")) or _clean(identity.get("name"))
        if resolved:
            return resolved
    return short_mint(mint_text)


def display_for_row(row, *, metadata_name=None) -> str:
    """Resolve common sqlite Row/dict token fields without raising."""
    try:
        data = dict(row)
    except Exception:
        return display_name(mint=getattr(row, "mint_address", None), metadata_name=metadata_name)
    return display_name(
        symbol=data.get("symbol") or data.get("token_symbol"),
        token_name=data.get("token_name") or data.get("name"),
        mint=data.get("mint_address") or data.get("mint") or data.get("token_mint"),
        metadata_name=metadata_name,
    )


def clear_identity_display_cache() -> None:
    _identity_for_mint.cache_clear()


if __name__ == "__main__":
    assert display_name(symbol="WIF") == "WIF"
    assert display_name(token_name="dogwifhat") == "dogwifhat"
    assert display_name(mint="ABCDEFGHJKLMNPQRSTUV").startswith("ABCD")
    assert display_name() == "unknown"
    print("TOKEN DISPLAY SELF-TEST: PASS")
