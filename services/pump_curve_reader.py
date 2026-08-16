# coding: utf-8
"""Minimal Pump bonding-curve reader for the shadow price-truth observer.

CURVE_READ_ECONOMY_20260816
---------------------------
Two measured defects, both network-only. Neither changes what a read means.

1. A fresh TLS handshake per call.  `requests.post` at module scope opens a new
   connection every time, and `read_curve` fails over across up to three RPC
   endpoints sequentially.  `requests` treats a scalar `timeout` as BOTH the
   connect and the read budget, so the worst case is 3 endpoints x 2 phases x
   `timeout_sec` -- ~15s at the 2.5s default.  That brackets the observed
   11.7-18.0s executable-quote latency exactly.  A module-level pooled Session
   with a retry-free HTTPAdapter removes the handshake from the common path.

2. The same curve is read twice per position per cycle.  price_router's
   `_try_pump_exact_liquidation` reads it, and price_truth_mesh's `_curve_fetch`
   reads it again -- same mint, same PDA, same slot, and both are submitted
   concurrently to the same 2-worker pool.  `_try_pump_exact_liquidation`
   bypasses `t3_coalesce` entirely, which is why provider telemetry has shown
   `quotes_coalesced: 0, coalesce_ratio: 0.0` since it was introduced.

The fix for (2) is strict single-flight, NOT a cache.  Concurrent identical
questions share one round-trip and every caller receives the *same* CurveRead
object -- carrying its own `context_slot`, `account_hash` and `observed_at`, so
every downstream freshness and authority check still applies to the true
observation moment.  A follower that arrives after the leader has finished
issues its own network call.  Nothing is reused across a state boundary,
because nothing is retained past the in-flight window.

`PUMP_CURVE_READ_COALESCE_WINDOW_SEC` (default 0.0) can extend the join window
past leader completion.  It is deliberately OFF by default: at 0.0 this module
cannot serve a single byte that was not observed inside a live request.
"""
from __future__ import annotations

import base64
import hashlib
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests
from requests.adapters import HTTPAdapter

from services.pump_curve_math import CurveState, decode_curve

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"


@dataclass(frozen=True)
class CurveRead:
    ok: bool
    reason: str
    mint: str
    curve_address: Optional[str]
    context_slot: Optional[int]
    observed_at: float
    account_hash: Optional[str]
    owner: Optional[str]
    state: Optional[CurveState]
    rpc_label: Optional[str]
    latency_ms: float
    # CURVE_READ_ECONOMY_20260816. Appended with a default so any positional
    # construction of the historical field order still works.
    served_by: str = "network"


# ── Pooled transport ─────────────────────────────────────────────────────────
# One Session for the process. max_retries=0 because failover is this module's
# own explicit endpoint loop; urllib3 retrying underneath would multiply the
# worst-case latency by the retry count without the caller ever seeing why.
_session_lock = threading.Lock()
_session: Optional[requests.Session] = None


def _get_session() -> requests.Session:
    global _session
    if _session is not None:
        return _session
    with _session_lock:
        if _session is None:
            s = requests.Session()
            pool = max(4, int(float(os.getenv("PUMP_CURVE_RPC_POOL_SIZE", "16"))))
            adapter = HTTPAdapter(
                pool_connections=pool, pool_maxsize=pool, max_retries=0
            )
            s.mount("https://", adapter)
            s.mount("http://", adapter)
            s.headers.update({"Content-Type": "application/json"})
            _session = s
    return _session


# ── Single-flight ────────────────────────────────────────────────────────────
_flight_lock = threading.Lock()
_flights: dict[str, dict] = {}


def _coalesce_window_sec() -> float:
    try:
        return max(0.0, float(os.getenv("PUMP_CURVE_READ_COALESCE_WINDOW_SEC", "0")))
    except (TypeError, ValueError):
        return 0.0


def _rpc_candidates() -> list[tuple[str, str]]:
    keys = ("HELIUS_RPC_URL", "QUICKNODE_RPC", "HELIUS_RPC", "SOLANA_RPC_URL", "CHAINSTACK_RPC")
    seen: set[str] = set()
    out: list[tuple[str, str]] = []
    for key in keys:
        value = os.getenv(key, "").strip()
        if value and value not in seen:
            out.append((key, value))
            seen.add(value)
    if not out:
        out.append(("PUBLIC_RPC", "https://api.mainnet-beta.solana.com"))
    return out[:3]


def derive_curve_pda(mint: str) -> Optional[str]:
    try:
        from solders.pubkey import Pubkey
        mint_pk = Pubkey.from_string(mint)
        program_pk = Pubkey.from_string(PUMP_PROGRAM_ID)
        pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint_pk)], program_pk)
        return str(pda)
    except Exception:
        return None


def _read_curve_network(mint: str, timeout_sec: float) -> CurveRead:
    """Perform the actual RPC failover read. One network attempt per endpoint."""
    started = time.perf_counter()
    observed_at = time.time()
    curve = derive_curve_pda(mint)
    if not curve:
        return CurveRead(False, "PDA_DERIVATION_FAILED", mint, None, None,
                         observed_at, None, None, None, None, 0.0, "network")

    session = _get_session()
    last_reason = "NO_RPC_RESPONSE"
    for label, endpoint in _rpc_candidates():
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [curve, {"encoding": "base64", "commitment": "confirmed"}],
            }
            # Explicit (connect, read) split. A scalar timeout applies the full
            # budget to each phase independently, which is how a 2.5s setting
            # produced a ~15s worst case across three endpoints.
            response = session.post(
                endpoint, json=payload,
                timeout=(min(timeout_sec, 1.5), timeout_sec),
            )
            response.raise_for_status()
            body = response.json()
            result = body.get("result") or {}
            value = result.get("value")
            slot = (result.get("context") or {}).get("slot")
            if not value:
                last_reason = "CURVE_ACCOUNT_MISSING"
                continue
            owner = str(value.get("owner") or "")
            if owner != PUMP_PROGRAM_ID:
                last_reason = "OWNER_MISMATCH"
                continue
            encoded = value.get("data")
            encoded = encoded[0] if isinstance(encoded, list) and encoded else encoded
            if not encoded:
                last_reason = "ACCOUNT_DATA_MISSING"
                continue
            raw = base64.b64decode(encoded)
            state = decode_curve(raw)
            if state is None:
                last_reason = "PUMP_LAYOUT_UNSUPPORTED"
                continue
            latency = (time.perf_counter() - started) * 1000.0
            # The evidence timestamp is when the account response was actually
            # received, not when a potentially slow network request began.
            # Pre-request stamping made a healthy state row arrive already stale
            # whenever RPC latency exceeded the peak-truth freshness budget.
            response_observed_at = time.time()
            return CurveRead(
                True,
                "OK",
                mint,
                curve,
                int(slot) if slot is not None else None,
                response_observed_at,
                hashlib.sha256(raw).hexdigest(),
                owner,
                state,
                label,
                latency,
                "network",
            )
        except Exception as exc:
            last_reason = f"{label}:{type(exc).__name__}"
            continue

    return CurveRead(
        False,
        last_reason,
        mint,
        curve,
        None,
        observed_at,
        None,
        None,
        None,
        None,
        (time.perf_counter() - started) * 1000.0,
        "network",
    )


def read_curve(mint: str, timeout_sec: float = 2.5) -> CurveRead:
    """Read a Pump bonding curve, de-duplicating concurrent identical reads.

    Two callers asking for the same mint at the same moment share one network
    round-trip and receive the identical observation. This is de-duplication,
    not caching: the returned CurveRead carries its own `observed_at`,
    `context_slot` and `account_hash`, so every downstream freshness and
    authority check still measures the true observation.

    A follower is marked `served_by='coalesced'`. Nothing is retained beyond
    the in-flight window unless PUMP_CURVE_READ_COALESCE_WINDOW_SEC is
    explicitly raised above its 0.0 default.
    """
    key = str(mint or "")
    if not key:
        return CurveRead(False, "PDA_DERIVATION_FAILED", key, None, None,
                         time.time(), None, None, None, None, 0.0, "network")

    window = _coalesce_window_sec()
    now = time.time()
    leader = False
    with _flight_lock:
        entry = _flights.get(key)
        if entry is not None and window > 0.0 and entry.get("done_at"):
            if (now - float(entry["done_at"])) > window:
                entry = None
                _flights.pop(key, None)
        if entry is None:
            entry = {"event": threading.Event(), "value": None, "done_at": None}
            _flights[key] = entry
            leader = True

    if not leader:
        # Someone is already asking this exact question. Wait for their answer
        # rather than opening a second connection to the same account.
        finished = entry["event"].wait(
            timeout=max(0.25, float(timeout_sec) * 3.0 + 1.0)
        )
        value = entry.get("value")
        if finished and isinstance(value, CurveRead):
            return CurveRead(
                value.ok, value.reason, value.mint, value.curve_address,
                value.context_slot, value.observed_at, value.account_hash,
                value.owner, value.state, value.rpc_label, value.latency_ms,
                "coalesced",
            )
        # The leader never produced a usable answer inside our budget. Fall
        # through to our own read rather than inventing an absence.
        return _read_curve_network(key, timeout_sec)

    try:
        result = _read_curve_network(key, timeout_sec)
        entry["value"] = result
        entry["done_at"] = time.time()
        return result
    finally:
        entry["event"].set()
        if window <= 0.0:
            with _flight_lock:
                if _flights.get(key) is entry:
                    _flights.pop(key, None)
        else:
            def _expire(k=key, e=entry):
                with _flight_lock:
                    if _flights.get(k) is e:
                        _flights.pop(k, None)
            t = threading.Timer(window, _expire)
            t.daemon = True
            t.start()


def read_stats() -> dict:
    """In-flight telemetry. Purely observational."""
    with _flight_lock:
        return {"inflight": len(_flights),
                "coalesce_window_sec": _coalesce_window_sec()}
