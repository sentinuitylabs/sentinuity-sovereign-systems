"""
services/substrate_price_feed.py
===============================================================================
SENTINUITY V3 — CANONICAL SUBSTRATE PRICE CONTRACT (SUBSTRATE_REAL_PRICE_20260721)

Replaces the fabricated constants (SOL=150, WETH=3500, cbBTC=100000) that the
V2 scanner stamped with price_updated_at=now on every cycle — fantasy prices
wearing fresh timestamps.

THE CONTRACT
------------
Every price observation is a dict with exactly these keys (directive Phase 5):

    asset            e.g. "SOL"
    chain            e.g. "solana"
    quote_currency   "USD"
    price            float or None
    source           "coingecko" | "jupiter" | "last_persisted" | "seed_mock" | None
    source_ts        the PROVIDER's timestamp for the price (epoch), 0 for mock
    observed_ts      when this process observed it (epoch)
    age_sec          observed_ts - source_ts (None when price is None)
    confidence       0.0 .. 1.0
    status           FRESH | DEGRADED | STALE | UNAVAILABLE | SEED_MOCK
    error            None or a short sanitised reason

HONESTY RULES (non-negotiable)
------------------------------
* source_ts is NEVER invented. CoinGecko supplies last_updated_at and that is
  used verbatim. Jupiter supplies no timestamp, so source_ts = observed_ts and
  confidence is reduced — the observation is honest about what it knows.
* A fallback to the last persisted mark keeps the ORIGINAL source_ts and is
  reported STALE (or UNAVAILABLE if no history exists). No fallback may stamp
  an old price as current.
* SEED_MOCK is an explicit, opt-in state with source_ts=0 and confidence=0.
  It exists for empty-database display only; it can never open a position or
  qualify a strategy for promotion (enforced in substrate_paper_ledger).

Sources are keyless public endpoints already consistent with the project's
dependency set (urllib only; api.jup.ag is already used by live_trading.py).
Network calls accept an injectable ``fetch_json`` so every consumer is
deterministically testable offline.
"""
from __future__ import annotations

import json
import os
import time
import urllib.parse
import urllib.request
from typing import Callable, Dict, Iterable, Optional

from wallets.substrate_wallet_schema import connect, _table_exists  # noqa: F401

FRESH_MAX_AGE_SEC = float(os.getenv("SUBSTRATE_PRICE_FRESH_SEC", "180"))
DEGRADED_MAX_AGE_SEC = float(os.getenv("SUBSTRATE_PRICE_DEGRADED_SEC", "900"))
HTTP_TIMEOUT_SEC = float(os.getenv("SUBSTRATE_PRICE_HTTP_TIMEOUT_SEC", "8"))

STATUS_FRESH = "FRESH"
STATUS_DEGRADED = "DEGRADED"
STATUS_STALE = "STALE"
STATUS_UNAVAILABLE = "UNAVAILABLE"
STATUS_SEED_MOCK = "SEED_MOCK"

# Statuses a paper entry or a mark-driven exit may act on.
ACTIONABLE_STATUSES = {STATUS_FRESH, STATUS_DEGRADED}

# Asset registry — the initially approved spot universe already represented in
# the system. Extend here; never hard-code prices anywhere else.
ASSETS: Dict[str, dict] = {
    "SOL": {
        "chain": "solana",
        "coingecko_id": "solana",
        "jupiter_mint": "So11111111111111111111111111111111111111112",
        "seed_reference_price": 150.0,
    },
    "WETH": {
        "chain": "base",
        "coingecko_id": "weth",
        "jupiter_mint": None,
        "seed_reference_price": 3500.0,
    },
    "cbBTC": {
        "chain": "base",
        "coingecko_id": "coinbase-wrapped-btc",
        "jupiter_mint": None,
        "seed_reference_price": 100000.0,
    },
}

FetchJson = Callable[[str, float], dict]


def _default_fetch_json(url: str, timeout: float) -> dict:
    req = urllib.request.Request(url, headers={
        "User-Agent": "sentinuity-substrate/1.0",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def ensure_price_schema(con) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS substrate_price_marks("
        " id INTEGER PRIMARY KEY AUTOINCREMENT,"
        " asset TEXT, chain TEXT, quote_currency TEXT DEFAULT 'USD',"
        " price REAL, source TEXT, source_ts REAL, observed_ts REAL,"
        " status TEXT, confidence REAL, error TEXT, created_at REAL)"
    )
    con.execute(
        "CREATE INDEX IF NOT EXISTS spm_asset_ts "
        "ON substrate_price_marks(asset, observed_ts DESC)"
    )


def _status_for_age(age_sec: Optional[float]) -> str:
    if age_sec is None:
        return STATUS_UNAVAILABLE
    if age_sec <= FRESH_MAX_AGE_SEC:
        return STATUS_FRESH
    if age_sec <= DEGRADED_MAX_AGE_SEC:
        return STATUS_DEGRADED
    return STATUS_STALE


def _contract(asset: str, *, price=None, source=None, source_ts=0.0,
              observed_ts=None, confidence=0.0, status=STATUS_UNAVAILABLE,
              error=None) -> dict:
    spec = ASSETS.get(asset, {})
    observed_ts = float(observed_ts if observed_ts is not None else time.time())
    age = (observed_ts - float(source_ts)) if (price is not None and source_ts) else None
    return {
        "asset": asset,
        "chain": spec.get("chain", ""),
        "quote_currency": "USD",
        "price": (float(price) if price is not None else None),
        "source": source,
        "source_ts": float(source_ts or 0.0),
        "observed_ts": observed_ts,
        "age_sec": (round(age, 3) if age is not None else None),
        "confidence": float(confidence),
        "status": status,
        "error": error,
    }


def seed_mock_contract(asset: str) -> dict:
    """Explicit mock observation. Never live truth; never promotion-eligible."""
    spec = ASSETS.get(asset, {})
    return _contract(
        asset,
        price=spec.get("seed_reference_price"),
        source="seed_mock",
        source_ts=0.0,
        confidence=0.0,
        status=STATUS_SEED_MOCK,
        error="explicit seed/mock reference price — not market data",
    )


# ── providers ────────────────────────────────────────────────────────────────

def _from_coingecko(assets: Iterable[str], fetch_json: FetchJson) -> Dict[str, dict]:
    ids = {a: ASSETS[a]["coingecko_id"] for a in assets if a in ASSETS}
    if not ids:
        return {}
    url = ("https://api.coingecko.com/api/v3/simple/price?"
           + urllib.parse.urlencode({
               "ids": ",".join(sorted(set(ids.values()))),
               "vs_currencies": "usd",
               "include_last_updated_at": "true",
           }))
    payload = fetch_json(url, HTTP_TIMEOUT_SEC)
    now = time.time()
    out: Dict[str, dict] = {}
    for asset, cg_id in ids.items():
        node = payload.get(cg_id) or {}
        price = node.get("usd")
        src_ts = float(node.get("last_updated_at") or 0.0)
        if price is None or src_ts <= 0:
            continue
        status = _status_for_age(now - src_ts)
        out[asset] = _contract(
            asset, price=price, source="coingecko", source_ts=src_ts,
            observed_ts=now,
            confidence=(0.9 if status == STATUS_FRESH else
                        0.6 if status == STATUS_DEGRADED else 0.3),
            status=status,
        )
    return out


def _from_jupiter(assets: Iterable[str], fetch_json: FetchJson) -> Dict[str, dict]:
    mints = {a: ASSETS[a].get("jupiter_mint") for a in assets
             if a in ASSETS and ASSETS[a].get("jupiter_mint")}
    if not mints:
        return {}
    url = ("https://api.jup.ag/price/v3?"
           + urllib.parse.urlencode({"ids": ",".join(mints.values())}))
    payload = fetch_json(url, HTTP_TIMEOUT_SEC)
    now = time.time()
    out: Dict[str, dict] = {}
    for asset, mint in mints.items():
        node = payload.get(mint) or {}
        price = node.get("usdPrice")
        if price is None:
            continue
        # Jupiter provides no source timestamp; the observation is honest about
        # that: source_ts = observed_ts, but confidence is capped below the
        # timestamped provider. Status is FRESH by construction (age 0) — the
        # provider is a live quote endpoint, not a cache.
        out[asset] = _contract(
            asset, price=price, source="jupiter", source_ts=now,
            observed_ts=now, confidence=0.7, status=STATUS_FRESH,
        )
    return out


def _last_persisted(con, asset: str) -> Optional[dict]:
    ensure_price_schema(con)
    row = con.execute(
        "SELECT * FROM substrate_price_marks WHERE asset=? AND price IS NOT NULL "
        "AND status IN (?,?,?) ORDER BY observed_ts DESC LIMIT 1",
        (asset, STATUS_FRESH, STATUS_DEGRADED, STATUS_STALE),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    # ORIGINAL source_ts retained. Status is FORCED to STALE regardless of
    # age: a persisted mark is a cache read, not a live provider read — no
    # provider currently stands behind it, so it must never be actionable
    # for entries, marks, or exits, even seconds after it was written.
    now = time.time()
    return _contract(
        asset, price=d.get("price"), source="last_persisted",
        source_ts=float(d.get("source_ts") or 0.0), observed_ts=now,
        confidence=0.1, status=STATUS_STALE,
        error="live providers unavailable; last persisted mark shown with its "
              "original timestamp",
    )


def persist_mark(con, contract: dict) -> None:
    ensure_price_schema(con)
    con.execute(
        "INSERT INTO substrate_price_marks(asset,chain,quote_currency,price,"
        "source,source_ts,observed_ts,status,confidence,error,created_at) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (contract["asset"], contract["chain"], contract["quote_currency"],
         contract["price"], contract["source"], contract["source_ts"],
         contract["observed_ts"], contract["status"], contract["confidence"],
         contract["error"], time.time()),
    )


def get_prices(assets: Iterable[str], *, fetch_json: Optional[FetchJson] = None,
               con=None, persist: bool = True) -> Dict[str, dict]:
    """Resolve the canonical contract for each asset.

    Provider order: CoinGecko (real provider timestamp) then Jupiter (Solana
    only, live quote). Any asset both providers miss falls back to the last
    persisted mark (original timestamp, STALE) or UNAVAILABLE. Only FRESH and
    DEGRADED observations from live providers are persisted as new marks.
    """
    fetch = fetch_json or _default_fetch_json
    wanted = [a for a in assets if a in ASSETS]
    results: Dict[str, dict] = {}
    errors: Dict[str, str] = {}

    try:
        results.update(_from_coingecko(wanted, fetch))
    except Exception as exc:  # noqa: BLE001 — provider failure is data, not crash
        errors["coingecko"] = f"{type(exc).__name__}: {str(exc)[:120]}"

    missing = [a for a in wanted if a not in results]
    if missing:
        try:
            results.update(_from_jupiter(missing, fetch))
        except Exception as exc:
            errors["jupiter"] = f"{type(exc).__name__}: {str(exc)[:120]}"

    own_con = False
    if con is None:
        con = connect()
        own_con = True
    try:
        for asset in wanted:
            if asset in results:
                if persist and results[asset]["status"] in ACTIONABLE_STATUSES:
                    persist_mark(con, results[asset])
                continue
            fallback = _last_persisted(con, asset)
            if fallback is not None:
                if errors:
                    fallback["error"] = (fallback["error"] + " | "
                                        + "; ".join(f"{k}={v}" for k, v in errors.items()))
                results[asset] = fallback
            else:
                results[asset] = _contract(
                    asset, status=STATUS_UNAVAILABLE,
                    error=("; ".join(f"{k}={v}" for k, v in errors.items())
                           or "no provider returned a price and no history exists"),
                )
        if persist:
            con.commit()
    finally:
        if own_con:
            con.close()
    return results


def get_price(asset: str, *, fetch_json: Optional[FetchJson] = None,
              con=None, persist: bool = True) -> dict:
    return get_prices([asset], fetch_json=fetch_json, con=con,
                      persist=persist).get(asset) or _contract(
        asset, status=STATUS_UNAVAILABLE, error="unknown asset")
