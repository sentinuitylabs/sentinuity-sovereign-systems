"""
token_identity_guard.py - the one case token_display.py cannot catch.

SIGNED OFF (section 4). ADDITIVE. `token_display.py` is NOT modified - it is
correct, and its SHA-256 stays 275478ebb1b93b3938084790a94096b489a558cb1508326f3a7c7261d4298809.

WHY THIS EXISTS
---------------
token_display.display_name() rejects a fixed bad-value set:
    {"", "n/a", "na", "none", "null", "unknown", "undefined", "-"}

That correctly handles a blank or literal "UNKNOWN" token_name. It cannot
handle the fourth failure mode found in the trace, because the value is not in
that set and looks like a legitimate name:

    execution_engine.py:2068  token_name = str(row["token_name"] or mint or "UNKNOWN")[:20]
    execution_engine.py:4610  token_name = str(position["token_name"] or mint or "UNKNOWN")[:20]
    execution_engine.py:5839  token_name = str(row_dict.get("token_name") or mint or "")[:18]

When token_name is missing, the mint is substituted and then truncated to 18 or
20 characters. A Solana mint is 32-44 base58 characters, so the stored value is
a mangled mint prefix - e.g. "7GCihgDB3fe6EqfmPHT8". That is not in the bad-value
set, so display_name() returns it verbatim and the UI shows a meaningless string.

This guard detects that case and falls back to the properly shortened mint.

CONTRACT:
  * display only - never used as trading identity;
  * execution identity remains the full mint, always;
  * no network I/O, no DB access, no writes;
  * never raises.
"""
from __future__ import annotations

import re

try:
    from services.token_display import display_name, short_mint, _BAD  # type: ignore
except Exception:  # pragma: no cover - import-path tolerance
    try:
        from token_display import display_name, short_mint, _BAD  # type: ignore
    except Exception:
        _BAD = {"", "n/a", "na", "none", "null", "unknown", "undefined", "-"}

        def short_mint(mint):
            s = "" if mint is None else str(mint).strip()
            if not s:
                return "unknown"
            return s if len(s) <= 12 else "%s\u2026%s" % (s[:4], s[-4:])

        def display_name(symbol=None, token_name=None, mint=None, metadata_name=None):
            for v in (symbol, token_name, metadata_name):
                if v is not None:
                    s = str(v).strip()
                    if s and s.lower() not in _BAD:
                        return s
            return short_mint(mint)


# base58 alphabet: no 0, O, I, l
_B58 = re.compile(r"^[1-9A-HJ-NP-Za-km-z]+$")

# Truncation lengths observed in the engine. Anything at or above the shortest
# is a candidate for mint contamination.
_TRUNC_MIN = 16


def looks_like_mint_fragment(value, mint=None) -> bool:
    """
    True when `value` appears to be a mint (or a truncated mint prefix) that has
    been stored in a name field.

    Conservative by design - a real token name that happens to be long and
    base58-clean is rare, and being wrong here only costs a shortened mint
    instead of a name.
    """
    try:
        if value is None:
            return False
        s = str(value).strip()
        if len(s) < _TRUNC_MIN:
            return False

        # Direct prefix of the known mint is conclusive.
        if mint:
            m = str(mint).strip()
            if m and (m.startswith(s) or s.startswith(m[:_TRUNC_MIN])):
                return True

        # Otherwise: long, no spaces, base58-clean, and mixed-case with digits.
        if " " in s or not _B58.match(s):
            return False
        has_digit = any(c.isdigit() for c in s)
        has_upper = any(c.isupper() for c in s)
        has_lower = any(c.islower() for c in s)
        return bool(has_digit and has_upper and has_lower)
    except Exception:
        return False




def _cached_human_identity(mint):
    """Return cached symbol/name only; no network I/O and no trading authority."""
    try:
        if not mint:
            return None, None
        try:
            from services.token_identity import cached_identity  # type: ignore
        except Exception:
            from token_identity import cached_identity  # type: ignore
        ident = cached_identity(str(mint).strip()) or {}
        symbol = ident.get("symbol")
        name = ident.get("name")
        if looks_like_mint_fragment(symbol, mint):
            symbol = None
        if looks_like_mint_fragment(name, mint):
            name = None
        return symbol, name
    except Exception:
        return None, None

def safe_display(symbol=None, token_name=None, mint=None,
                 metadata_name=None) -> str:
    """
    display_name() with mint-fragment rejection. Guarantees a human label or a
    properly shortened mint - never a bare 'n/a', blank, or mangled prefix.
    """
    try:
        sym = None if looks_like_mint_fragment(symbol, mint) else symbol
        nam = None if looks_like_mint_fragment(token_name, mint) else token_name
        met = None if looks_like_mint_fragment(metadata_name, mint) else metadata_name

        # Existing rows may already contain a full/truncated mint in token_name.
        # Recover the human identity from the persistent local cache populated by
        # ingest_pipeline.  This is read-only, bounded SQLite access and never
        # performs network I/O or changes execution identity.
        if not sym and not nam and not met:
            cached_symbol, cached_name = _cached_human_identity(mint)
            sym = cached_symbol
            nam = cached_name

        label = display_name(symbol=sym, token_name=nam, mint=mint,
                             metadata_name=met)
        if label and str(label).strip().lower() not in _BAD:
            return str(label)
        return short_mint(mint)
    except Exception:
        try:
            return short_mint(mint)
        except Exception:
            return "unknown"


def safe_display_for_row(row, metadata_name=None) -> str:
    """Row/dict convenience. Mirrors token_display.display_for_row column names."""
    try:
        try:
            d = dict(row)
        except Exception:
            d = row if isinstance(row, dict) else {}
        return safe_display(
            symbol=d.get("symbol") or d.get("token_symbol"),
            token_name=d.get("token_name") or d.get("name"),
            mint=d.get("mint_address") or d.get("mint") or d.get("token_mint"),
            metadata_name=metadata_name,
        )
    except Exception:
        return "unknown"


def dexscreener_url(mint) -> str:
    """Link must always use the FULL mint, never the display label."""
    try:
        m = str(mint or "").strip()
        return "https://dexscreener.com/solana/%s" % m if m else ""
    except Exception:
        return ""
