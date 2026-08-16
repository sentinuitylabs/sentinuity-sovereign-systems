from __future__ import annotations

"""
core/asset_identity.py
===============================================================================
CANONICAL ASSET IDENTITY (SUBSTRATE_ASSET_IDENTITY_20260802)

Audit finding A1.4: `wallets/substrate_paper_ledger.py` keyed exposure on the
raw `asset_symbol`, so SOL and WSOL were two independent exposure identities.
A behavioural probe against the shipped tree opened a $25 SOL position AND a
$25 WSOL position under a $30 aggregate cap — $50 of real single-asset risk
inside a $30 ceiling.

This module is the single place where "which real asset is this" is decided.
It is deliberately:

  * pure (no DB, no network, no imports from services/ or wallets/);
  * conservative (an unknown symbol maps to itself, never to a guess);
  * explicit (every family is declared, nothing is inferred from substrings).

Substring inference is specifically rejected. "WBTC" ends in "BTC" and is a
BTC claim; "BTCST" also contains "BTC" and is not. Membership is by table.
"""

from typing import Dict, FrozenSet, Set

SCHEMA_TAG = "SUBSTRATE_ASSET_IDENTITY_20260802"

# ── Exposure families ────────────────────────────────────────────────────────
# key   = canonical settlement identity used for aggregate exposure
# value = every symbol that represents an economically equivalent claim
#
# Only add a symbol here when a position in it carries substantially the same
# price risk as the canonical asset. Liquid-staking derivatives (mSOL, stETH)
# are INTENTIONALLY EXCLUDED: they carry validator/protocol risk and can
# depeg, so treating them as identical would understate, not overstate, risk.
_FAMILIES: Dict[str, FrozenSet[str]] = {
    "SOL": frozenset({"SOL", "WSOL"}),
    "ETH": frozenset({"ETH", "WETH"}),
    "BTC": frozenset({"BTC", "WBTC", "CBBTC"}),
    "USDC": frozenset({"USDC", "USDBC"}),
    "USDT": frozenset({"USDT"}),
}

_SYMBOL_TO_CANONICAL: Dict[str, str] = {
    symbol: canonical
    for canonical, symbols in _FAMILIES.items()
    for symbol in symbols
}

# Symbols whose canonical form differs from themselves — i.e. the wrapped
# representations. Used by the migration to report what it is about to merge.
WRAPPED_ALIASES: Dict[str, str] = {
    symbol: canonical
    for symbol, canonical in _SYMBOL_TO_CANONICAL.items()
    if symbol != canonical
}


def normalise(symbol) -> str:
    """Upper-case, whitespace-stripped symbol. Never returns None."""
    return str(symbol or "").strip().upper()


def canonical_asset(symbol) -> str:
    """Return the exposure identity for `symbol`.

    Unknown symbols map to their own normalised form — a symbol this module has
    never heard of must not be silently merged into a family.

        canonical_asset("wsol")   -> "SOL"
        canonical_asset("cbBTC")  -> "BTC"
        canonical_asset("mSOL")   -> "MSOL"   (not merged: depeg risk)
        canonical_asset("")       -> ""
    """
    sym = normalise(symbol)
    return _SYMBOL_TO_CANONICAL.get(sym, sym)


def family_symbols(symbol) -> Set[str]:
    """Every symbol sharing an exposure identity with `symbol`, inclusive."""
    canonical = canonical_asset(symbol)
    return set(_FAMILIES.get(canonical, frozenset({canonical})))


def same_exposure(a, b) -> bool:
    """True when two symbols must share one aggregate exposure budget."""
    return canonical_asset(a) == canonical_asset(b) and canonical_asset(a) != ""


def is_wrapped_alias(symbol) -> bool:
    """True when `symbol` is a wrapped representation of another canonical."""
    sym = normalise(symbol)
    return sym in WRAPPED_ALIASES


def describe(symbol) -> dict:
    """Diagnostic record for audit output and rejection payloads."""
    sym = normalise(symbol)
    canonical = canonical_asset(sym)
    return {
        "symbol": sym,
        "canonical_asset": canonical,
        "is_wrapped_alias": sym != canonical and sym in _SYMBOL_TO_CANONICAL,
        "is_known": sym in _SYMBOL_TO_CANONICAL,
        "family": sorted(family_symbols(sym)),
    }


if __name__ == "__main__":
    import json
    for probe in ("SOL", "wsol", "WETH", "cbBTC", "mSOL", "BTCST", ""):
        print(json.dumps(describe(probe), sort_keys=True))
