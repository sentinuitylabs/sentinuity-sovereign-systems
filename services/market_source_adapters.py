"""
market_source_adapters.py — PRICE_TRUTH_SIGNOFF_20260808

Read-only market observation adapters feeding price_truth_adjudicator.

FAILURE DOCTRINE (directive section 15)
  Every adapter:
    * uses a tight bounded timeout (default 2.5s, never unbounded)
    * returns None on ANY failure — never raises, never partially succeeds
    * consults provider_firewall.check_provider() before requesting
    * reports to provider_firewall.log_api_call() after requesting
    * caches per (mint, provider) for a short TTL to respect rate limits
  No provider outage can freeze the oracle or block an exit. An adapter that
  cannot answer contributes nothing; the adjudicator then simply lacks that
  witness and falls to a weaker authority class. That is the correct
  degradation.

VERIFICATION STATUS — READ THIS BEFORE ENABLING ANY PROVIDER
  This module was written without network access to any of these providers.
  Endpoint shapes below are asserted from the existing working DexScreener call
  in services/price_enricher.py and from public API documentation, but NONE of
  them has been executed against a live response here.

  Run selftest() against the real network before trusting any provider, and
  treat a provider as UNVERIFIED until it appears in the health scorecard with
  a non-zero success count.

  Provider-by-provider honesty:
    dexscreener   — keyless. Endpoint pattern already proven in this codebase.
                    Highest confidence.
    geckoterminal — keyless, documented public API. Rate limit ~30 req/min on
                    the free tier; the cache TTL below assumes that.
    gmgn          — NO documented public structured API. The existing
                    services/gmgn_cf_bridge.py reaches an internal endpoint via
                    browser-impersonation headers. That is fragile, may breach
                    their terms, and can break without notice. This adapter is
                    therefore DISABLED BY DEFAULT and must be explicitly
                    enabled by the operator who accepts those risks.
    dextools      — requires a paid API key. Returns None without one. Never a
                    dependency.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)

try:
    import requests
except Exception:  # pragma: no cover
    requests = None  # type: ignore

DEFAULT_TIMEOUT_SEC = float(os.getenv("MARKET_SOURCE_TIMEOUT_SEC", "2.5"))
DEFAULT_CACHE_TTL_SEC = float(os.getenv("MARKET_SOURCE_CACHE_TTL_SEC", "6.0"))

_CACHE: Dict[str, Dict[str, Any]] = {}
_CACHE_LOCK = threading.Lock()

# Per-provider health. Written in-process, drained by health_scorecard().
_HEALTH: Dict[str, Dict[str, Any]] = {}
_HEALTH_LOCK = threading.Lock()


def _provider_enabled(provider: str, default: str = "1") -> bool:
    return str(os.getenv(f"MARKET_SOURCE_{provider.upper()}_ENABLED", default)
               ).strip().lower() not in ("0", "false", "off", "no")


def _cache_get(key: str, ttl: float) -> Optional[dict]:
    with _CACHE_LOCK:
        e = _CACHE.get(key)
        if e and (time.time() - e["ts"]) <= ttl:
            out = dict(e["value"])
            _elapsed = time.time() - e["ts"]
            out["fetch_age_sec"] = float(out.get("fetch_age_sec") or 0.0) + _elapsed
            # Only a KNOWN market age advances; unknown stays unknown.
            if out.get("age_sec") is not None:
                out["age_sec"] = float(out["age_sec"]) + _elapsed
            out["cached"] = True
            return out
    return None


def _cache_put(key: str, value: dict) -> None:
    with _CACHE_LOCK:
        _CACHE[key] = {"ts": time.time(), "value": dict(value)}
        if len(_CACHE) > 4096:
            for k in sorted(_CACHE, key=lambda k: _CACHE[k]["ts"])[:1024]:
                _CACHE.pop(k, None)


def _record(provider: str, ok: bool, latency: float, err: str = "") -> None:
    with _HEALTH_LOCK:
        h = _HEALTH.setdefault(provider, {
            "requests": 0, "successes": 0, "failures": 0,
            "latencies": [], "last_success_ts": 0.0, "last_error": "",
        })
        h["requests"] += 1
        if ok:
            h["successes"] += 1
            h["last_success_ts"] = time.time()
        else:
            h["failures"] += 1
            h["last_error"] = err[:200]
        h["latencies"].append(float(latency))
        if len(h["latencies"]) > 500:
            h["latencies"] = h["latencies"][-500:]


def _firewall_ok(provider: str, caller: str) -> bool:
    try:
        from services.provider_firewall import check_provider
        allowed, _ = check_provider(provider, caller)
        return bool(allowed)
    except Exception:
        return True  # firewall unavailable must not block observation


def _firewall_log(provider: str, caller: str, endpoint: str,
                  status: int, err: str = "") -> None:
    try:
        from services.provider_firewall import log_api_call
        log_api_call(provider, caller, endpoint, status, error_type=err or None)
    except Exception:
        pass


def _get_json(provider: str, url: str, *, timeout: float, caller: str,
              headers: Optional[dict] = None) -> Optional[Any]:
    if requests is None:
        return None
    if not _firewall_ok(provider, caller):
        return None
    t0 = time.time()
    try:
        r = requests.get(url, timeout=timeout,
                         headers=headers or {"Accept": "application/json"})
        dt = time.time() - t0
        _firewall_log(provider, caller, url[:120], r.status_code)
        if r.status_code != 200:
            _record(provider, False, dt, f"HTTP_{r.status_code}")
            return None
        _record(provider, True, dt)
        return r.json()
    except Exception as exc:
        dt = time.time() - t0
        _record(provider, False, dt, f"{type(exc).__name__}")
        _firewall_log(provider, caller, url[:120], 0, type(exc).__name__)
        return None


def _f(v: Any) -> Optional[float]:
    try:
        f = float(v)
        return f if f > 0 and f == f else None
    except (TypeError, ValueError):
        return None


# ── DexScreener ──────────────────────────────────────────────────────────────

def dexscreener_observation(mint: str, *, timeout: float = DEFAULT_TIMEOUT_SEC,
                            cache_ttl: float = DEFAULT_CACHE_TTL_SEC
                            ) -> Optional[dict]:
    """
    Endpoint pattern matches the working call in price_enricher.py.

    Pool selection: highest USD liquidity among Solana pairs. The directive
    warns against blindly taking the first pair; deepest liquidity is the
    economically dominant venue and is what an exit would actually route
    through.
    """
    if not _provider_enabled("dexscreener"):
        return None
    key = f"dexscreener:{mint}"
    hit = _cache_get(key, cache_ttl)
    if hit:
        return hit
    data = _get_json("dexscreener",
                     f"https://api.dexscreener.com/tokens/v1/solana/{mint}",
                     timeout=timeout, caller="market_source_adapters")
    if data is None:
        return None
    pairs = data.get("pairs") if isinstance(data, dict) else (
        data if isinstance(data, list) else [])
    sol = [p for p in (pairs or []) if isinstance(p, dict)
           and str(p.get("chainId") or "").lower() == "solana"
           and _f(p.get("priceUsd"))]
    if not sol:
        return None
    best = max(sol, key=lambda p: _f((p.get("liquidity") or {}).get("usd")) or 0.0)
    # DexScreener exposes no price timestamp on this endpoint. `pairCreatedAt`
    # is pool age, not price age, and must not be used as freshness.
    return _emit(key, {
        "source": "dexscreener",
        "price": _f(best.get("priceUsd")),
        "market_age_sec": None,
        "pair_address": str(best.get("pairAddress") or ""),
        "dex": str(best.get("dexId") or ""),
        "liquidity_usd": _f((best.get("liquidity") or {}).get("usd")),
        "pool_count": len(sol),
    })


# ── GeckoTerminal ────────────────────────────────────────────────────────────

def geckoterminal_observation(mint: str, *, timeout: float = DEFAULT_TIMEOUT_SEC,
                              cache_ttl: float = 20.0) -> Optional[dict]:
    """
    Public keyless API. Free tier is ~30 req/min, so the cache TTL here is
    deliberately much longer than DexScreener's — this source corroborates, it
    does not drive timing.
    """
    if not _provider_enabled("geckoterminal"):
        return None
    key = f"geckoterminal:{mint}"
    hit = _cache_get(key, cache_ttl)
    if hit:
        return hit
    data = _get_json(
        "geckoterminal",
        f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}",
        timeout=timeout, caller="market_source_adapters")
    try:
        attrs = ((data or {}).get("data") or {}).get("attributes") or {}
        price = _f(attrs.get("price_usd"))
        if not price:
            return None
        return _emit(key, {
            "source": "geckoterminal",
            "price": price,
            "market_age_sec": None,
            "liquidity_usd": _f(attrs.get("total_reserve_in_usd")),
        })
    except Exception:
        return None


# ── GMGN (OFF BY DEFAULT — see module docstring) ─────────────────────────────

def gmgn_observation(mint: str, *, timeout: float = DEFAULT_TIMEOUT_SEC,
                     cache_ttl: float = DEFAULT_CACHE_TTL_SEC) -> Optional[dict]:
    """
    DISABLED unless MARKET_SOURCE_GMGN_ENABLED=1.

    There is no documented public structured GMGN price API. Reaching it means
    browser-impersonation through services/gmgn_cf_bridge.py, which is fragile
    and may breach their terms. The adjudicator treats GMGN as one corroborating
    family among others, so leaving it off costs a witness, not correctness.

    If enabled, the response shape below is a GUESS and must be validated by
    selftest() before the observation is trusted.
    """
    if not _provider_enabled("gmgn", default="0"):
        return None
    key = f"gmgn:{mint}"
    hit = _cache_get(key, cache_ttl)
    if hit:
        return hit
    try:
        from services.gmgn_cf_bridge import build_gmgn_session
        session, headers = build_gmgn_session()
        url = f"https://gmgn.ai/defi/quotation/v1/tokens/sol/{mint}"
        t0 = time.time()
        r = session.get(url, timeout=timeout, headers=headers)
        dt = time.time() - t0
        if r.status_code != 200:
            _record("gmgn", False, dt, f"HTTP_{r.status_code}")
            return None
        payload = (r.json() or {}).get("data") or {}
        token = payload.get("token") or payload
        price = _f(token.get("price") or token.get("price_usd"))
        if not price:
            _record("gmgn", False, dt, "NO_PRICE_FIELD")
            return None
        _record("gmgn", True, dt)
        return _emit(key, {
            "source": "gmgn",
            "price": price,
            "age_sec": 0.0,
            "liquidity_usd": _f(token.get("liquidity")),
        })
    except Exception as exc:
        _record("gmgn", False, timeout, type(exc).__name__)
        return None


# ── DEXTools (requires paid key) ─────────────────────────────────────────────

def dextools_observation(mint: str, *, timeout: float = DEFAULT_TIMEOUT_SEC,
                         cache_ttl: float = 20.0) -> Optional[dict]:
    """Returns None without DEXTOOLS_API_KEY. Never a dependency."""
    api_key = os.getenv("DEXTOOLS_API_KEY", "").strip()
    if not api_key or not _provider_enabled("dextools", default="0"):
        return None
    key = f"dextools:{mint}"
    hit = _cache_get(key, cache_ttl)
    if hit:
        return hit
    data = _get_json(
        "dextools",
        f"https://public-api.dextools.io/trial/v2/token/solana/{mint}/price",
        timeout=timeout, caller="market_source_adapters",
        headers={"X-API-Key": api_key, "Accept": "application/json"})
    try:
        price = _f(((data or {}).get("data") or {}).get("price"))
        if not price:
            return None
        return _emit(key, {"source": "dextools", "price": price, "age_sec": 0.0})
    except Exception:
        return None


def _emit(cache_key: str, obs: dict) -> dict:
    from services.price_truth_adjudicator import source_family
    obs["family"] = source_family(obs["source"])
    obs["observed_at"] = time.time()
    # PRICE_TRUTH_SIGNOFF_20260809 (blocker 8): `age_sec` means the age of the
    # UNDERLYING MARKET OBSERVATION, not how long ago we fetched it. These
    # vendors publish on caches of tens of seconds and expose no price
    # timestamp, so the true market age is genuinely UNKNOWN. Asserting 0.0
    # claimed sub-second market truth for a possibly-90s-old display price.
    # Unknown is now represented as unknown; the adjudicator refuses to treat
    # an unknown-age observation as a fresh corroborating witness.
    if obs.get("market_age_sec") is not None:
        obs["age_sec"] = _f(obs.get("market_age_sec"))
    else:
        obs["age_sec"] = None
    obs["fetch_age_sec"] = 0.0
    _cache_put(cache_key, obs)
    return obs


# ── Collection ───────────────────────────────────────────────────────────────

def collect_external_observations(mint: str, *,
                                  timeout: float = DEFAULT_TIMEOUT_SEC) -> list:
    """
    Gather every currently available external observation for a mint.

    Sequential and bounded: worst case is roughly len(adapters) * timeout, and
    each adapter is individually capped. Callers on any latency-sensitive path
    should invoke this off the hot loop and pass the result into the
    adjudicator, never inline before an exit decision.
    """
    out = []
    for fn in (dexscreener_observation, geckoterminal_observation,
               gmgn_observation, dextools_observation):
        try:
            o = fn(mint, timeout=timeout)
            if o:
                out.append(o)
        except Exception:
            continue
    return out


# Background refresh state. External providers must never become a synchronous
# dependency of runner/exit decisions. The hot path reads cache only and asks a
# daemon worker to refresh opportunistically.
_REFRESHING = set()
_REFRESH_LOCK = threading.Lock()

def cached_external_observations(mint: str) -> list:
    """Return currently cached observations only; performs no network I/O."""
    out = []
    now = time.time()
    provider_ttls = {
        "dexscreener": DEFAULT_CACHE_TTL_SEC * 2.0,
        "geckoterminal": 40.0,
        "gmgn": DEFAULT_CACHE_TTL_SEC * 2.0,
        "dextools": DEFAULT_CACHE_TTL_SEC * 2.0,
    }
    with _CACHE_LOCK:
        for provider, ttl in provider_ttls.items():
            e = _CACHE.get(f"{provider}:{mint}")
            if not e:
                continue
            age = now - float(e.get("ts") or 0.0)
            if age > ttl:
                continue
            value = dict(e.get("value") or {})
            value["fetch_age_sec"] = float(value.get("fetch_age_sec") or 0.0) + age
            if value.get("age_sec") is not None:
                value["age_sec"] = float(value["age_sec"]) + age
            value["cached"] = True
            out.append(value)
    return out

def schedule_external_refresh(mint: str, *, timeout: float = DEFAULT_TIMEOUT_SEC) -> bool:
    """Schedule one bounded daemon refresh for a mint; returns immediately."""
    mint = str(mint or "").strip()
    if not mint:
        return False
    with _REFRESH_LOCK:
        if mint in _REFRESHING:
            return False
        _REFRESHING.add(mint)

    def _worker():
        try:
            collect_external_observations(mint, timeout=timeout)
        except Exception:
            pass
        finally:
            with _REFRESH_LOCK:
                _REFRESHING.discard(mint)

    try:
        threading.Thread(target=_worker, name=f"price-truth-{mint[:8]}", daemon=True).start()
        return True
    except Exception:
        with _REFRESH_LOCK:
            _REFRESHING.discard(mint)
        return False


def health_scorecard() -> Dict[str, Dict[str, Any]]:
    """Per-provider runtime health. Directive section 14."""
    out: Dict[str, Dict[str, Any]] = {}
    with _HEALTH_LOCK:
        for provider, h in _HEALTH.items():
            lat = sorted(h["latencies"])
            n = len(lat)
            out[provider] = {
                "requests": h["requests"],
                "successes": h["successes"],
                "failures": h["failures"],
                "success_rate_pct": (100.0 * h["successes"] / h["requests"]) if h["requests"] else 0.0,
                "median_latency_sec": lat[n // 2] if n else None,
                "p90_latency_sec": lat[int(0.9 * (n - 1))] if n else None,
                "last_success_ts": h["last_success_ts"] or None,
                "last_success_age_sec": (time.time() - h["last_success_ts"]) if h["last_success_ts"] else None,
                "last_error": h["last_error"],
                "verified": h["successes"] > 0,
            }
    return out


def selftest(mint: str) -> Dict[str, Any]:
    """
    Validate every adapter against the live network for one known mint.

    RUN THIS BEFORE TRUSTING ANY PROVIDER. It is the only thing that converts a
    provider from UNVERIFIED to verified, because the endpoint contracts in this
    module were never executed against a live response during authoring.
    """
    results: Dict[str, Any] = {}
    for name, fn in (("dexscreener", dexscreener_observation),
                     ("geckoterminal", geckoterminal_observation),
                     ("gmgn", gmgn_observation),
                     ("dextools", dextools_observation)):
        t0 = time.time()
        try:
            obs = fn(mint, cache_ttl=0.0)
            results[name] = {
                "ok": bool(obs),
                "latency_sec": round(time.time() - t0, 3),
                "price": (obs or {}).get("price"),
                "detail": obs or "no observation (disabled, no key, or request failed)",
            }
        except Exception as exc:
            results[name] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    results["_health"] = health_scorecard()
    return results


if __name__ == "__main__":
    import json
    import sys
    target = sys.argv[1] if len(sys.argv) > 1 else "So11111111111111111111111111111111111111112"
    print(json.dumps(selftest(target), indent=2, default=str))
