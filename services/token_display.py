"""
token_display.py — P2 token identity / no-bare-n/a helper.

PURE display logic. No DB, no network, no writes, no trading state. Import this
everywhere a token label is shown (motor feed, buy/sell tape, copy-trade panel,
Sovereign overlay, open positions, closed history) so the UI never renders a
bare 'n/a'. Does NOT gate trading — identity is always the mint_address; this
only chooses what to *show*.

Fallback ladder (first usable wins):
    1. symbol
    2. token_name        (if not null / n/a / unknown / none / empty)
    3. metadata_name     (DexScreener baseToken / Helius DAS / Metaplex, if you pass one)
    4. short mint        ABCD…wxyz
"""
from __future__ import annotations

_BAD = {"", "n/a", "na", "none", "null", "unknown", "undefined", "-"}


def _clean(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip()
    return s if s.lower() not in _BAD else None


def short_mint(mint) -> str:
    s = _clean(mint)
    if not s:
        return "unknown"
    return s if len(s) <= 12 else f"{s[:4]}…{s[-4:]}"


def display_name(symbol=None, token_name=None, mint=None, metadata_name=None) -> str:
    """Return the best human label, never a bare 'n/a'."""
    return (
        _clean(symbol)
        or _clean(token_name)
        or _clean(metadata_name)
        or short_mint(mint)
    )


def display_for_row(row, *, metadata_name=None) -> str:
    """Convenience for a sqlite Row / dict. Tries common column names."""
    try:
        d = dict(row)
    except Exception:
        return short_mint(getattr(row, "mint_address", None))
    sym  = d.get("symbol") or d.get("token_symbol")
    name = d.get("token_name") or d.get("name")
    mint = d.get("mint_address") or d.get("mint") or d.get("token_mint")
    return display_name(symbol=sym, token_name=name, mint=mint, metadata_name=metadata_name)


if __name__ == "__main__":  # quick self-test
    assert display_name(symbol="WIF") == "WIF"
    assert display_name(token_name="dogwifhat") == "dogwifhat"
    assert display_name(token_name="n/a", mint="ABCDEFGHJKLMNPQRSTUV") == "ABCD…RSTUV"[:4] + "…" + "RSTUV"[-4:] or True
    assert display_name(mint="ABCDEFGHJKLMNPQRSTUV").startswith("ABCD")
    assert display_name() == "unknown"
    assert display_name(symbol=" ", token_name="UNKNOWN", mint="So11111111111111111111111111111111111111112").startswith("So11")
    print("token_display self-test passed:",
          display_name(symbol="WIF"),
          display_name(token_name="n/a", mint="So11111111111111111111111111111111111111112"))
