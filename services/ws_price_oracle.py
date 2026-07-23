
# SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
from __future__ import annotations
try:
    from birdeye_quota_guard import install_birdeye_requests_guard as _install_birdeye_guard
    _install_birdeye_guard()
except Exception:
    pass
# /SENTINUITY_BIRDEYE_QUOTA_GUARD_V2


def get_active_position_mints(conn):
    try:
        rows = conn.execute(
            "SELECT DISTINCT mint_address FROM paper_positions WHERE status='OPEN'"
        ).fetchall()
        return {r[0] for r in rows if r[0]}
    except Exception:
        return set()

"""
ws_price_oracle.py - Real-time MTM pricing via Helius accountSubscribe (V4.1)
Patched by Grok 4.20 - PDA-first + verified fallback. No more silent subscription failures.
V12 Oracle Alignment - active paper_positions mints are always injected into MTM coverage.
"""


import asyncio
import base64
import hashlib
import json
import logging
import os
import sqlite3
import struct
import threading
import time
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set

log = logging.getLogger("ws_oracle")

# SENTINUITY_NETWORK_OUTAGE_TRUTH_20260722
# Rate-limit repeated transport failures and expose recent DNS-failure density
# to the oracle health classifier. This keeps outages distinct from quiet markets.
_err_last: Dict[str, float] = {}
_dns_error_times: List[float] = []
_dns_error_lock = threading.Lock()

def _log_rl(key: str, level, msg: str, *args, every: float = 60.0) -> None:
    now = time.time()
    if now - float(_err_last.get(key, 0.0)) >= every:
        _err_last[key] = now
        level(msg, *args)

def _record_dns_error(now: Optional[float] = None) -> None:
    ts = float(now if now is not None else time.time())
    with _dns_error_lock:
        _dns_error_times.append(ts)
        cutoff = ts - 300.0
        while _dns_error_times and _dns_error_times[0] < cutoff:
            _dns_error_times.pop(0)

def _recent_dns_error_count(window_sec: float = 120.0) -> int:
    now = time.time()
    cutoff = now - float(window_sec)
    with _dns_error_lock:
        while _dns_error_times and _dns_error_times[0] < now - 300.0:
            _dns_error_times.pop(0)
        return sum(1 for ts in _dns_error_times if ts >= cutoff)
# /SENTINUITY_NETWORK_OUTAGE_TRUTH_20260722

# ── CONFIG ─────────────────────────────────────────────────────────────────────
HELIUS_WSS_URL   = os.getenv("HELIUS_WSS_URL", "")
CHAINSTACK_WSS   = os.getenv("CHAINSTACK_WSS", "").strip()
CHAINSTACK_RPC   = os.getenv("CHAINSTACK_RPC", "").strip()
QUICKNODE_WSS    = os.getenv("QUICKNODE_WSS", "")

# WSS provider priority - updated May 14 2026:
# Chainstack SSL drops silently on long-lived connections (UNEXPECTED_EOF).
# Helius promoted to primary - stable, Solana-native, free tier.
# Chainstack demoted to fallback for HTTP RPC only.
_PREFERRED_WSS = (
    QUICKNODE_WSS
    or HELIUS_WSS_URL
    or os.getenv("SOLANA_WSS_URL", "")
    or CHAINSTACK_WSS
    or "wss://api.mainnet-beta.solana.com"
)
# QuickNode primary: 80M credits/month - original stable baseline
# Helius second: Solana-native, reliable WSS
# Chainstack third: HTTP OK but WSS SSL drops on long-lived connections

POLL_OPEN_MINTS_SEC  = 1.0
PING_INTERVAL_SEC    = 15.0
RECONNECT_DELAY_SEC  = 2.0
FORCE_RESUB_INTERVAL = 45.0

MIN_PRICE_USD    = 1e-15
MAX_PRICE_USD    = 100_000.0   # no artificial cap - meme coins can be any price
MAX_MTM_MULTIPLIER = 100.0

MTM_STALE_WARN_SEC      = 3.0
DEXSCREENER_FALLBACK_SEC = 5.0
MTM_STALE_DEGRADE_SEC   = 8.0

PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_BC_DISCRIMINATOR = bytes.fromhex("17b7f83760d8ac60")

VSOL_OFFSET = 16
VTOK_OFFSET = 8

# ── DB (unified with core.schema) ─────────────────────────────────────────────
try:
    from core.schema import get_connection, update_heartbeat, get_intel_connection, DB_PATH
    _USE_SCHEMA = True
except Exception:
    DB_PATH = Path("sentinuity_matrix.db")
    _USE_SCHEMA = False


def _db_connect() -> sqlite3.Connection:
    if _USE_SCHEMA:
        return get_connection()
    conn = sqlite3.connect(str(DB_PATH), timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


# ── SOL/USD CACHE ─────────────────────────────────────────────────────────────
_sol_usd_cache: float = 0.0
_sol_usd_ts: float = 0.0
_SOL_USD_TTL = 20.0

# PRICE HEARTBEAT: per-mint write throttle
# Watches sub-second but only writes to DB every MTM_MIN_WRITE_INTERVAL_SEC
# OR when price moves more than MTM_MIN_DELTA_PCT - whichever comes first.
# Prevents WAL lock contention from pump.fun's high-frequency account notifications
# (can fire 10-50x/sec per token) while ensuring execution engine always has fresh data.
MTM_ACTIVE_WRITE_INTERVAL_SEC = 2.0    # open-position mints: write every 2s max
MTM_IDLE_WRITE_INTERVAL_SEC   = 20.0   # idle/background mints: write every 20s
MTM_MIN_WRITE_INTERVAL_SEC    = 5.0    # reduced from 20s - tighter meter updates
MTM_MIN_DELTA_PCT           = 1.0   # always write if price moves >= 1%
_mtm_last_write:  dict = {}         # mint -> last_write_epoch
_mtm_last_price:  dict = {}         # mint -> last_written_price_usd
_mtm_throttle_lock = threading.Lock()

# ── HOT SET - tier-aware oracle refresh cadence ────────────────────────────
# Phase A.1: only HOT-tier tokens receive rapid oracle updates.
# Reduces DB contention and ensures fresh prices land before execution gates fire.
# HOT  (age <= 45s):  write every 3s max
# WARM (age <= 120s): write every 15s max
# COOL (age <= 300s): write every 45s max
# COLD/DEAD:          write every 120s max (background only)
MTM_HOT_WRITE_INTERVAL_SEC  = 3.0
MTM_WARM_WRITE_INTERVAL_SEC = 15.0
MTM_COOL_WRITE_INTERVAL_SEC = 45.0
MTM_COLD_WRITE_INTERVAL_SEC = 120.0

_hot_set: set = set()          # mints currently in HOT tier
_hot_set_lock = threading.Lock()
_hot_set_last_refresh: float = 0.0
_HOT_SET_REFRESH_INTERVAL = 10.0  # rebuild HOT_SET every 10s from DB

# ── ORACLE STALL DETECTION ────────────────────────────────────────────────────
# Phase A.1: track write activity to classify oracle health state.
# HEALTHY:  last HOT write < 10s ago
# DEGRADED: last HOT write 10-30s ago
# STALLED:  last HOT write > 30s ago → auto-trigger fallback + resub
_last_global_write: float = 0.0   # timestamp of most recent _write_mtm call (any mint)
_last_hot_write:    float = 0.0   # timestamp of most recent write for a HOT-set mint
_writes_this_minute: list = []    # ring of write timestamps for rate calc
_oracle_state: str = "INITIALIZING"
_stall_lock = threading.Lock()


def _refresh_hot_set() -> None:
    """Rebuild in-memory HOT_SET from DB. Runs every 10s via oracle loop.

    Fix 1 (freshness continuity): includes recently-qualified rows
    (first_seen_at/created_at within 120s) even if price_updated_at predates
    the 45s HOT window. This ensures the oracle immediately fast-refreshes
    newly qualified tokens so their price is current before the executor gate
    fires - closing the launch-vs-runtime freshness gap.
    """
    global _hot_set, _hot_set_last_refresh
    now = time.time()
    if now - _hot_set_last_refresh < _HOT_SET_REFRESH_INTERVAL:
        return
    try:
        with _db_connect() as conn:
            rows = conn.execute("""
                SELECT mint_address FROM market_snapshots
                WHERE price_status = 'priced'
                  AND candidate_state IN ('qualified','latched','pending')
                  AND latched = 0
                  AND is_tradeable = 1
                  AND (
                      COALESCE(price_updated_at, 0) > ?
                      OR COALESCE(first_seen_at, created_at, 0) > ?
                  )
                LIMIT 50
            """, (now - 45, now - 120)).fetchall()
            # Always include open position mints (HOT regardless of age)
            open_rows = conn.execute(
                "SELECT DISTINCT mint_address FROM paper_positions WHERE status='OPEN'"
            ).fetchall()
        new_hot = {r[0] for r in rows if r[0]} | {r[0] for r in open_rows if r[0]}
        with _hot_set_lock:
            _hot_set = new_hot
        _hot_set_last_refresh = now
    except Exception:
        pass


def _get_tier_write_interval(mint: str, price_updated_at: float) -> float:
    """Return the appropriate write interval for a mint based on its current tier."""
    # Open positions always get active (2s) cadence
    with _hot_set_lock:
        if mint in _hot_set:
            return MTM_ACTIVE_WRITE_INTERVAL_SEC
    age = time.time() - price_updated_at if price_updated_at > 0 else 9999.0
    if age <= 45:
        return MTM_HOT_WRITE_INTERVAL_SEC
    if age <= 120:
        return MTM_WARM_WRITE_INTERVAL_SEC
    if age <= 300:
        return MTM_COOL_WRITE_INTERVAL_SEC
    return MTM_COLD_WRITE_INTERVAL_SEC


def _get_sol_usd() -> float:
    global _sol_usd_cache, _sol_usd_ts
    if time.time() - _sol_usd_ts < _SOL_USD_TTL and _sol_usd_cache > 0:
        return _sol_usd_cache
    try:
        import requests as _req
        r = _req.get(
            "https://api.dexscreener.com/latest/dex/pairs/solana/"
            "So11111111111111111111111111111111111111112",
            timeout=5,
        )
        pairs = r.json().get("pairs") or []
        if pairs:
            price = float(pairs[0].get("priceUsd", 0))
            if price > 0:
                _sol_usd_cache = price
                _sol_usd_ts = time.time()
                return price
    except Exception:
        pass
    return _sol_usd_cache if _sol_usd_cache > 0 else 150.0




# SENTINUITY_0707_MARK_TRUTH_SOURCE_GUARD
# Unconfirmed fallback/stall sources may refresh last/current price, but may not
# raise highest_price_seen. Only WSS/native or explicitly confirmed sources are
# allowed to raise peak. This prevents phantom ~1.84x shelf peaks from UI/exit logic.
def _sent0707_peak_trusted_source(source: str) -> bool:
    s = str(source or "").lower()
    bad = (
        "stall_recovery", "coverage_failsafe", "wss_fail_fallback",
        "cold_recovery", "dexscreener", "jupiter", "birdeye",
        "keepalive_", "fallback", "recovery",
    )
    if any(x in s for x in bad):
        return "confirmed" in s or "tick_confirmed" in s
    return ("helius" in s) or (s in {"ws", "wss", "native", "accountsubscribe"}) or ("confirmed" in s)
# /SENTINUITY_0707_MARK_TRUTH_SOURCE_GUARD

# ── OPEN MINTS / POSITION COVERAGE ────────────────────────────────────────────
def _table_columns(conn: sqlite3.Connection, table: str) -> Set[str]:
    try:
        return {str(r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        try:
            return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            return set()


def _get_open_mints() -> List[str]:
    """
    HARD FIX V2: oracle tracks open positions + latched signals.
    Prevents oracle going blind between trades, which caused mtm_ticks to go
    stale and triggered the entry scan blocked gate permanently.
    No fallback to historical mints - that caused 1011 WSS floods.
    """
    conn = None
    try:
        conn = _db_connect()
        mints: set = set()

        # Priority 1: OPEN positions (must always be tracked)
        try:
            rows = conn.execute(
                "SELECT DISTINCT mint_address FROM paper_positions "
                "WHERE mint_address IS NOT NULL AND TRIM(mint_address) != '' "
                "AND UPPER(COALESCE(status, 'OPEN')) = 'OPEN'"
            ).fetchall()
            mints.update(str(r["mint_address"]).strip() for r in rows if str(r["mint_address"] or "").strip())
        except Exception as e:
            log.debug("ws_oracle: open positions query error: %s", e)

        # Priority 2: latched signals ready for execution (keep oracle warm)
        try:
            rows = conn.execute(
                "SELECT DISTINCT mint_address FROM market_snapshots "
                "WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2) "
                "AND mint_address IS NOT NULL AND TRIM(mint_address) != ''"
            ).fetchall()
            mints.update(str(r["mint_address"]).strip() for r in rows if str(r["mint_address"] or "").strip())
        except Exception as e:
            log.debug("ws_oracle: latched mints query error: %s", e)

        # Priority 3: recent MTM candidates (keeps oracle warm during idle periods
        # so mtm_ticks age stays low and the liveness gate never fires spuriously)
        if not mints:
            try:
                rows = conn.execute(
                    "SELECT DISTINCT mint_address FROM market_snapshots "
                    "WHERE candidate_state='mtm' "
                    "AND mint_address IS NOT NULL AND TRIM(mint_address) != '' "
                    "AND price_updated_at > 0 "
                    "ORDER BY price_updated_at DESC LIMIT 20"
                ).fetchall()
                mints.update(str(r["mint_address"]).strip() for r in rows if str(r["mint_address"] or "").strip())
            except Exception as e:
                log.debug("ws_oracle: mtm mints query error: %s", e)

        if mints:
            log.debug("ws_oracle: tracking %d mints (open+latched+mtm)", len(mints))
            return sorted(mints)

        log.warning("ws_oracle: no trackable mints - oracle idle")
        return []

    except Exception as e:
        log.warning("ws_oracle: get_open_mints error: %s", e)
        return []
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


# ── LIVENESS TRACKING ─────────────────────────────────────────────────────────
_oracle_last_write_ts: float = 0.0
_oracle_last_write_lock = threading.Lock()


def oracle_last_write_age() -> float:
    with _oracle_last_write_lock:
        ts = _oracle_last_write_ts
    return (time.time() - ts) if ts > 0 else 9999.0


def _record_write_ts() -> None:
    global _oracle_last_write_ts
    with _oracle_last_write_lock:
        _oracle_last_write_ts = time.time()


_mint_last_event_ts: Dict[str, float] = {}
_mint_event_lock = threading.Lock()


def _record_mint_event_ts(mint: str) -> None:
    with _mint_event_lock:
        _mint_last_event_ts[mint] = time.time()


def get_mint_event_age(mint: str) -> float:
    with _mint_event_lock:
        ts = _mint_last_event_ts.get(mint, 0.0)
    return (time.time() - ts) if ts > 0 else 9999.0


def _remove_mint_event_ts(mint: str) -> None:
    with _mint_event_lock:
        _mint_last_event_ts.pop(mint, None)


# ── BONDING CURVE LOOKUP (FIXED) ──────────────────────────────────────────────
import base58 as _b58_mod

_bc_cache: Dict[str, str] = {}
_bc_cache_lock = threading.Lock()


def _b58decode(s: str) -> bytes:
    return _b58_mod.b58decode(s)


def _b58encode(b: bytes) -> str:
    return _b58_mod.b58encode(b).decode()


def _rpc_call(method: str, params: list) -> Optional[dict]:
    rpc_url = (os.getenv("CHAINSTACK_RPC", "").strip()
               or os.getenv("QUICKNODE_RPC", "")
               or os.getenv("HELIUS_RPC", ""))
    if not rpc_url:
        return None
    try:
        import requests as _req
        r = _req.post(
            rpc_url,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            headers={"Content-Type": "application/json"},
            timeout=10,
        )
        return r.json().get("result")
    except Exception as e:
        log.debug("ws_oracle: _rpc_call %s error: %s", method, e)
        return None


def get_bonding_curve_pda(mint: str) -> Optional[str]:
    """Correct PDA derivation - solders preferred, pure Python fallback."""
    try:
        from solders.pubkey import Pubkey
        mint_pk = Pubkey.from_string(mint)
        program = Pubkey.from_string(PUMP_PROGRAM_ID)
        seeds = [b"bonding-curve", bytes(mint_pk)]
        pda, _ = Pubkey.find_program_address(seeds, program)
        return str(pda)
    except ImportError:
        pass
    except Exception as e:
        log.debug("solders PDA failed mint=%s: %s", mint[:16], e)

    # Pure Python fallback
    try:
        import hashlib
        program_b = _b58decode(PUMP_PROGRAM_ID)
        mint_b = _b58decode(mint)
        seeds = [b"bonding-curve", mint_b]
        for nonce in range(255, -1, -1):
            h = hashlib.sha256()
            for seed in seeds:
                h.update(seed)
            h.update(bytes([nonce]))
            h.update(program_b)
            h.update(b"ProgramDerivedAddress")
            candidate = h.digest()[:32]
            return _b58encode(candidate)
    except Exception as e:
        log.debug("pure Python PDA failed mint=%s: %s", mint[:16], e)
    return None


def get_bonding_curve_address_fallback(mint: str, max_sigs: int = 15) -> Optional[str]:
    """Limited tx scan fallback - only when PDA fails."""
    log.info("ws_oracle: BC_TX_SCAN_START mint=%s limit=%d", mint[:16], max_sigs)
    sigs = _rpc_call("getSignaturesForAddress", [mint, {"limit": max_sigs}])
    if not sigs:
        log.warning("ws_oracle: BC_TX_SCAN_NO_SIGS mint=%s", mint[:16])
        return None

    for sig_info in sigs:
        sig = sig_info.get("signature")
        if not sig:
            continue
        tx = _rpc_call("getTransaction", [sig, {"encoding": "jsonParsed", "maxSupportedTransactionVersion": 0}])
        if not tx:
            continue
        accs = tx.get("transaction", {}).get("message", {}).get("accountKeys", [])
        acc_list = [a.get("pubkey", a) if isinstance(a, dict) else a for a in accs]
        for addr in acc_list:
            if addr == mint:
                continue
            try:
                info = _rpc_call("getAccountInfo", [addr, {"encoding": "base64"}])
                val = (info or {}).get("value")
                if not val or val.get("owner") != PUMP_PROGRAM_ID:
                    continue
                raw = base64.b64decode(val["data"][0])
                if len(raw) >= 8 and raw[:8] == _BC_DISCRIMINATOR:
                    log.info("ws_oracle: BC_TX_SCAN_FOUND mint=%s bc=%s", mint[:16], addr[:16])
                    return addr
            except Exception:
                continue
    log.warning("ws_oracle: BC_TX_SCAN_NOT_FOUND mint=%s", mint[:16])
    return None


def get_bonding_curve_pda_verified(mint: str) -> Optional[str]:
    """Single source of truth: PDA first → limited tx scan → None."""
    with _bc_cache_lock:
        if mint in _bc_cache:
            return _bc_cache[mint]

    pda = get_bonding_curve_pda(mint)
    if pda:
        log.info("BC_PDA_DERIVED mint=%s pda=%s", mint[:16], pda[:16])
        with _bc_cache_lock:
            _bc_cache[mint] = pda
        return pda

    log.warning("PDA failed for mint=%s - falling back to tx scan", mint[:16])
    bc = get_bonding_curve_address_fallback(mint)
    if bc:
        with _bc_cache_lock:
            _bc_cache[mint] = bc
        return bc

    log.warning("BC_LOOKUP_FAILED mint=%s - DexScreener fallback will be used", mint[:16])
    return None


# ── DECODER ───────────────────────────────────────────────────────────────────
def decode_bonding_curve(data_b64: str) -> Optional[tuple[float, float]]:
    try:
        raw = base64.b64decode(data_b64)
        if len(raw) < 48 or raw[:8] != _BC_DISCRIMINATOR:
            return None
        vtok = struct.unpack_from("<Q", raw, VTOK_OFFSET)[0]
        vsol = struct.unpack_from("<Q", raw, VSOL_OFFSET)[0]
        if vtok == 0 or vsol == 0:
            return None
        return float(vsol), float(vtok)
    except Exception as e:
        log.debug("decode_bonding_curve error: %s", e)
        return None


# ── DEXSCREENER FALLBACK ──────────────────────────────────────────────────────
def _fetch_dexscreener_fallback(mint: str) -> Optional[float]:
    try:
        import requests as _req
        r = _req.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=6)
        if r.status_code != 200:
            return None
        pairs = r.json().get("pairs") or []
        if not pairs:
            return None
        best = max(pairs, key=lambda p: float(p.get("liquidity", {}).get("usd", 0) or 0))
        price = float(best.get("priceUsd", 0) or 0)
        return price if price > 0 else None
    except Exception as e:
        log.debug("_fetch_dexscreener_fallback error mint=%s: %s", mint[:16], e)
        return None


# ── BIRDEYE FETCH ─────────────────────────────────────────────────────────────
# Load .env before reading API keys - keys are read at module level
# so dotenv must be called here, not just in __main__
try:
    from dotenv import load_dotenv as _load_dotenv
    from pathlib import Path as _DotenvPath
    _load_dotenv(_DotenvPath(__file__).resolve().parent.parent / ".env", override=False)
except Exception:
    pass

_BIRDEYE_KEY  = os.getenv("BIRDEYE_API_KEY", "").strip()
_JUPITER_KEY  = os.getenv("JUPITER_PRICE_API_KEY", "").strip()
_JUPITER_DNS_FAIL_UNTIL = 0.0  # epoch - retry Jupiter after cooldown expires (not permanent)
if _BIRDEYE_KEY:
    log.info("[API_KEYS] Birdeye key loaded (%s...)", _BIRDEYE_KEY[:8])
if _JUPITER_KEY:
    log.info("[API_KEYS] Jupiter key loaded (%s...)", _JUPITER_KEY[:8])

def _fetch_birdeye_price(mint: str) -> Optional[float]:
    """Birdeye /defi/price - covers ALL pump.fun tokens including bonding curve phase.
    Best source for pre-graduation tokens (<$69k mcap). 60 rpm free tier."""
    if not _BIRDEYE_KEY:
        return None
    try:
        import requests as _req
        r = _req.get(
            "https://public-api.birdeye.so/defi/price",
            params={"address": mint},
            headers={"X-API-KEY": _BIRDEYE_KEY, "x-chain": "solana"},
            timeout=4,
        )
        if r.status_code != 200:
            return None
        data = r.json().get("data") or {}
        price = float(data.get("value") or 0)
        return price if price > 0 else None
    except Exception as e:
        log.debug("_fetch_birdeye_price error mint=%s: %s", mint[:16], e)
        return None


def _fetch_jupiter_price(mint: str) -> Optional[float]:
    """Jupiter /price/v3 - best for post-graduation tokens (PumpSwap/Raydium).
    Returns None for pre-graduation bonding curve tokens (not indexed by Jupiter).
    Sets _JUPITER_DNS_FAIL_UNTIL to 5-minute cooldown on DNS failure - auto-retried after cooldown."""
    global _JUPITER_DNS_FAIL_UNTIL
    if not _JUPITER_KEY:
        return None
    try:
        import requests as _req
        r = _req.get(
            "https://api.jup.ag/price/v3",
            params={"ids": mint},
            headers={"x-api-key": _JUPITER_KEY},
            timeout=4,
        )
        if r.status_code != 200:
            return None
        data = r.json() or {}
        item = data.get(mint) or {}
        price = float(item.get("usdPrice") or 0)
        return price if price > 0 else None
    except Exception as e:
        _err = str(e)
        if "getaddrinfo" in _err or "NameResolution" in _err or "Failed to resolve" in _err:
            _JUPITER_DNS_FAIL_UNTIL = time.time() + 300  # 5-min cooldown, not permanent
            log.warning("_fetch_jupiter_price: DNS failure - Jupiter cooldown 300s (will retry)")
        else:
            log.debug("_fetch_jupiter_price error mint=%s: %s", mint[:16], e)
        return None


def _fetch_best_fallback_price(mint: str) -> tuple[Optional[float], str]:
    """Fallback price waterfall - priority matches current provider health.

    Priority order (reflects confirmed working state May 2026):
      1. DexScreener  - confirmed HTTP 200, no key, no DNS issues
      2. Jupiter      - only attempted if _JUPITER_KEY set AND DNS healthy
      3. Birdeye      - only attempted if BIRDEYE_DISABLED != 1 (CU quota)

    Provider health is checked inline using module-level flags updated
    by _mark_provider_fail() on repeated failures.
    """
    # ── 1. DexScreener - confirmed working, try first ───────────────────
    p = _fetch_dexscreener_fallback(mint)
    if p:
        return p, "dexscreener"

    # ── 2. Jupiter - only if key set and DNS not known-failed ───────────
    if _JUPITER_KEY and time.time() >= _JUPITER_DNS_FAIL_UNTIL:
        p = _fetch_jupiter_price(mint)
        if p:
            return p, "jupiter"

    # ── 3. Birdeye - only if not CU-exhausted ───────────────────────────
    _birdeye_ok = bool(_BIRDEYE_KEY)
    if _birdeye_ok:
        try:
            from schema import get_config_value as _gcv
            if str(_gcv("BIRDEYE_DISABLED", "0")).strip() == "1":
                _birdeye_ok = False
        except Exception:
            try:
                from core.schema import get_config_value as _gcv2
                if str(_gcv2("BIRDEYE_DISABLED", "0")).strip() == "1":
                    _birdeye_ok = False
            except Exception:
                pass
    if _birdeye_ok:
        p = _fetch_birdeye_price(mint)
        if p:
            return p, "birdeye"

    return None, "none"


# ── MTM WRITE ─────────────────────────────────────────────────────────────────
def _write_mtm(mint: str, price_usd: float, source: str = "helius") -> None:
    if price_usd <= MIN_PRICE_USD or price_usd > MAX_PRICE_USD:
        return

    # HOT CACHE: write to in-memory price cache immediately, before any DB work.
    # execution_engine reads this via price_cache.get_price() - sub-ms latency.
    try:
        from services.price_cache import set_price as _set_hot_price
        _set_hot_price(mint, price_usd)
    except Exception:
        pass  # never block on cache failure

    # PRICE HEARTBEAT: throttle gate - sub-second observation, controlled DB writes
    # Passes through if: (a) >= MTM_MIN_WRITE_INTERVAL_SEC since last write for this mint
    #                OR  (b) price moved >= MTM_MIN_DELTA_PCT since last written price
    #                OR  (c) OPEN_POSITION_PRIORITY - open position mints bypass throttle
    # This prevents WAL lock storms from high-frequency pump.fun account notifications.
    now = time.time()

    # OPEN_POSITION_PRIORITY: bypass throttle entirely for mints with open positions
    # Allows ~2-5s effective update rate via WSS events without changing poll interval
    _is_open_pos = _is_open_position_mint(mint)

    with _mtm_throttle_lock:
        _last_t = _mtm_last_write.get(mint, 0.0)
        _last_p = _mtm_last_price.get(mint, 0.0)
        _elapsed = now - _last_t
        _delta_pct = (abs(price_usd - _last_p) / _last_p * 100.0) if _last_p > 0 else 100.0
        # Phase A.1 tier-aware interval: HOT=3s, WARM=15s, COOL=45s, COLD=120s.
        # Open-position mints always use 1.2s (burst mode, existing behaviour).
        _last_price_ts = 0.0
        try:
            _last_price_ts = _mtm_last_write.get(mint, 0.0)
        except Exception:
            pass
        _interval = 1.2 if _is_open_pos else _get_tier_write_interval(mint, _last_price_ts)
        _should_write = (
            (_elapsed >= _interval)                     # rate-limited per mint
            or (_delta_pct >= MTM_MIN_DELTA_PCT)        # always write on significant move
            or (_is_open_pos and _delta_pct > 0.4)      # force on any meaningful move
        )
        if not _should_write:
            return
        # Mark intent before releasing lock - prevents concurrent duplicate writes
        _mtm_last_write[mint] = now
        _mtm_last_price[mint] = price_usd

    # Log active-mint writes at most once every 10s per mint to avoid log spam
    _log_ts_key = f"_mtm_log_{mint}"
    _last_log   = getattr(_write_mtm, _log_ts_key, 0.0)
    if _is_open_pos and (now - _last_log) >= 10.0:
        log.info("[MTM_ACTIVE_WRITE] mint=%s price=%.10f interval=%.1fs delta=%.4f%%",
                 mint[:16], price_usd, _elapsed, _delta_pct)
        setattr(_write_mtm, _log_ts_key, now)

    tx_hash = f"WS:{mint[:20]}:{int(now * 1000)}:{uuid.uuid4().hex[:6]}"

    # Update stall detection timestamps
    global _last_global_write, _last_hot_write, _writes_this_minute
    with _stall_lock:
        _last_global_write = now
        _writes_this_minute = [t for t in _writes_this_minute if now - t < 60]
        _writes_this_minute.append(now)
        with _hot_set_lock:
            if mint in _hot_set:
                _last_hot_write = now

    # ── INTEL DB: direct write to sentinuity_intelligence.db ───────────────
    global _intel_last_commit
    try:
        import sqlite3 as _sq3
        _idb_path = str(Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db")
        _ic = _sq3.connect(_idb_path, timeout=5.0)
        _ic.execute("PRAGMA journal_mode=WAL")
        _ic.execute("""
            CREATE TABLE IF NOT EXISTS mtm_ticks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                mint_address TEXT NOT NULL,
                price_usd    REAL NOT NULL,
                ts_ms        INTEGER NOT NULL,
                source       TEXT DEFAULT 'ws'
            )
        """)
        _ic.execute(
            "INSERT INTO mtm_ticks (mint_address, price_usd, ts_ms, source) "
            "VALUES (?, ?, ?, ?)",
            (mint, price_usd, int(now * 1000), source),
        )
        _ic.commit()
        _ic.close()
        log.info(
            "[MTM_INTEL_WRITE_OK] mint=%s price=%.10f source=%s",
            mint[:16], price_usd, source,
        )
    except Exception as _ie:
        log.warning("[MTM_INTEL_WRITE_FAIL] mint=%s error=%s", mint[:16], _ie)
    # ─────────────────────────────────────────────────────────────────────────

    try:
        conn = _db_connect()

        if _is_open_pos:
            conn.execute(
                """
                INSERT OR IGNORE INTO market_snapshots (
                    tx_hash, token_name, mint_address, observed_price, price_updated_at,
                    candidate_state, price_attempts, price_last_attempt_at, price_status,
                    latched, execution_ready, duplicate_key, timestamp,
                    created_at, first_seen_at
                ) VALUES (?, ?, ?, ?, ?, 'mtm', 0, ?, 'priced', 0, 0, ?, ?, ?, ?)
                """,
                (tx_hash, mint[:20], mint, price_usd, now, now, f"{tx_hash}|ws", now, now, now),
            )

            # Open-position MTM only: refresh the latest dedicated MTM row.
            conn.execute(
                """
                UPDATE market_snapshots
                SET observed_price = ?, price_updated_at = ?, price_status='priced'
                WHERE id = (
                    SELECT id FROM market_snapshots
                    WHERE mint_address = ? AND candidate_state = 'mtm'
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (price_usd, now, mint),
            )
        else:
            # Candidate pricing must update the original ingest row in place.
            conn.execute(
                """
                UPDATE market_snapshots
                SET observed_price = ?,
                    price_updated_at = ?,
                    price_last_attempt_at = ?,
                    price_status = 'priced'
                WHERE id = (
                    SELECT id FROM market_snapshots
                    WHERE mint_address = ?
                      AND candidate_state IN ('pending','qualified')
                      AND COALESCE(execution_ready,0) != 2
                    ORDER BY id DESC LIMIT 1
                )
                """,
                (price_usd, now, now, mint),
            )

        _sent0707_peak_ok = 1 if _sent0707_peak_trusted_source(source) else 0
        _pp_cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}

        if "mark_source" in _pp_cols:
            conn.execute(
                """
                UPDATE paper_positions SET
                    last_price = ?, last_marked_at = ?, mark_source = ?,
                    highest_price_seen = CASE WHEN ? = 1 AND COALESCE(highest_price_seen, 0) <= ? THEN ? ELSE COALESCE(highest_price_seen, 0) END
                WHERE mint_address = ? AND status = 'OPEN'
                """,
                (price_usd, now, source, _sent0707_peak_ok, price_usd, price_usd, mint),
            )
        else:
            conn.execute(
                """
                UPDATE paper_positions SET
                    last_price = ?, last_marked_at = ?,
                    highest_price_seen = CASE WHEN ? = 1 AND COALESCE(highest_price_seen, 0) <= ? THEN ? ELSE COALESCE(highest_price_seen, 0) END
                WHERE mint_address = ? AND status = 'OPEN'
                """,
                (price_usd, now, _sent0707_peak_ok, price_usd, price_usd, mint),
            )
        # PnL intentionally NOT written here.
        # Only update_position_mark() in execution_engine may write
        # unrealized_pnl_usd - it gates on router can_execute_exit.

        tick_count = conn.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE mint_address=? AND candidate_state='mtm'",
            (mint,)
        ).fetchone()[0]

        conn.commit()
        # NOTE: conn.close() moved to after oracle pulse - was silently failing the pulse

        _record_write_ts()
        _record_mint_event_ts(mint)

        log.info(
            "Rhiza has absorbed the new truth. Memory integrated. MTM_WRITE mint=%s price=%.10f source=%s tick=%d",
            mint[:16], price_usd, source, tick_count,
        )

        # ORACLE PULSE - update price_updated_at on active open-position rows only
        # Scoped to most-recent execution_ready row to avoid touching historical latched rows
        # This prevents guardian from thinking the snapshot is stale and resetting it
        if _is_open_pos:
            try:
                conn.execute("""
                    UPDATE market_snapshots
                    SET price_updated_at = ?,
                        price_status = 'oracle_pulse',
                        tick_count = COALESCE(tick_count, 0) + 1
                    WHERE id = (
                        SELECT id FROM market_snapshots
                        WHERE mint_address = ?
                          AND COALESCE(execution_ready,0) IN (1,2)
                        ORDER BY id DESC LIMIT 1
                    )
                """, (now, mint))
                conn.commit()
            except Exception as _pe:
                pass  # pulse failure never blocks a price write

        conn.close()

    except Exception as e:
        log.warning("ws_oracle: _write_mtm error mint=%s: %s", mint[:12], e)


# ── OPEN POSITION PRIORITY ───────────────────────────────────────────────────
_open_position_mints_cache: set = set()
_open_position_mints_last_check = 0.0
_OPEN_MINTS_CACHE_TTL = 1.0  # refresh every 1s - new positions visible within 1s


def _is_open_position_mint(mint: str) -> bool:
    """
    Returns True if mint has an OPEN paper_position.
    Cached for 5s to avoid per-tick DB reads.
    """
    global _open_position_mints_cache, _open_position_mints_last_check
    now = time.time()
    if now - _open_position_mints_last_check > _OPEN_MINTS_CACHE_TTL:
        try:
            _c = sqlite3.connect(str(DB_PATH), timeout=2.0)
            rows = _c.execute(
                "SELECT DISTINCT mint_address FROM paper_positions WHERE status='OPEN'"
            ).fetchall()
            _open_position_mints_cache = {str(r[0]).strip() for r in rows if r[0]}
            _c.close()
            _open_position_mints_last_check = now
        except Exception:
            pass  # keep stale cache on error
    return mint in _open_position_mints_cache
# ─────────────────────────────────────────────────────────────────────────────


# ── HELIUS ORACLE CORE ────────────────────────────────────────────────────────
class HeliusOracle:
    def __init__(self, wss_url: str) -> None:
        self._wss_url = wss_url
        self._stop = False
        self._ws = None
        self._sub_id_for_mint: Dict[str, int] = {}
        self._mint_for_sub_id: Dict[int, str] = {}
        self._subscribed: Set[str] = set()
        self._req_id = 1
        self._req_lock = threading.Lock()
        self._pending_reqs: Dict[int, str] = {}
        # Position coverage audit: logs only newly injected position mints so
        # the operator can see when the oracle reconnects price truth to held
        # positions without flooding logs every poll cycle.
        self._position_coverage_logged: Set[str] = set()
        self._wss_pre_throttle: Dict[str, float] = {}  # mint -> last WSS event ts

    def _next_id(self) -> int:
        with self._req_lock:
            rid = self._req_id
            self._req_id += 1
            return rid

    async def run(self) -> None:
        backoff = RECONNECT_DELAY_SEC
        # WSS fallback list - rotate on SSL errors (Chainstack drops silently)
        _wss_candidates = [u for u in [
            QUICKNODE_WSS,
            HELIUS_WSS_URL,
            os.getenv("SOLANA_WSS_URL",""),
            CHAINSTACK_WSS,
            "wss://api.mainnet-beta.solana.com",
        ] if u and u.startswith("wss")]
        _wss_idx = 0
        _ssl_fail_count = 0

        while not self._stop:
            try:
                await self._connect_and_stream()
                backoff = RECONNECT_DELAY_SEC  # reset on clean exit
                _ssl_fail_count = 0
            except asyncio.CancelledError:
                break
            except Exception as e:
                err_str = str(e).lower()
                is_dns = any(x in err_str for x in (
                    "getaddrinfo", "name resolution", "temporary failure in name",
                    "nodename nor servname", "no such host"
                ))
                is_transport = is_dns or any(x in err_str for x in (
                    "ssl", "eof", "unexpected_eof", "certificate",
                    "handshake", "connection reset", "broken pipe"
                ))
                if is_dns:
                    _record_dns_error()
                if is_transport:
                    _ssl_fail_count += 1
                    # Rotate on DNS and transport failures, not only SSL drops.
                    if len(_wss_candidates) > 1:
                        _wss_idx = (_wss_idx + 1) % len(_wss_candidates)
                        new_url = _wss_candidates[_wss_idx]
                        _log_rl(
                            "transport_rotate", log.warning,
                            "ws_oracle: TRANSPORT_DROP rotating WSS endpoint: %s (fail #%d class=%s)",
                            new_url[:60], _ssl_fail_count, "DNS" if is_dns else "TRANSPORT",
                            every=15.0,
                        )
                        self._wss_url = new_url
                    else:
                        _log_rl("no_wss_fallback", log.error,
                                "ws_oracle: TRANSPORT_DROP no fallback endpoints available",
                                every=60.0)
                else:
                    _log_rl("connection_supervisor_fatal", log.error,
                            "ws_oracle: CONNECTION_SUPERVISOR_FATAL %s", e, every=60.0)

            if not self._stop:
                jitter = backoff * (0.8 + 0.4 * (os.urandom(1)[0] / 255.0))
                _log_rl("reconnect_scheduled", log.warning,
                        "ws_oracle: RECONNECT_SCHEDULED backoff=%.2fs url=%s",
                        jitter, self._wss_url[:60], every=15.0)
                await asyncio.sleep(jitter)
                backoff = min(backoff * 2.0, 30.0)  # max 30s not 45s

    async def _connect_and_stream(self) -> None:
        try:
            import websockets
            from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
        except ImportError:
            log.error("ws_oracle: 'websockets' not installed - pip install websockets")
            await asyncio.sleep(30)
            return

        log.info("ws_oracle: ESTABLISHING_WSS endpoint=%s", self._wss_url[:70])

        async with websockets.connect(
            self._wss_url,
            ping_interval=None,
            ping_timeout=None,
            max_size=16 * 1024 * 1024,
            open_timeout=20.0,
            close_timeout=10.0,
        ) as ws:
            self._ws = ws
            # Clean state
            self._sub_id_for_mint.clear()
            self._mint_for_sub_id.clear()
            self._subscribed.clear()
            self._pending_reqs.clear()
            self._req_id = 1

            log.info("ws_oracle: WSS_LIVE")

            # GOLD MASTER: open-position auto-resubscribe on restart
            # Subscribe preseed mints immediately after connection - don't wait
            # for the first subscription_manager poll cycle (can take seconds).
            preseed = getattr(self, "_preseed_mints", set())
            if preseed:
                log.info("ws_oracle: PRESEED_SUBSCRIBE firing for %d mints", len(preseed))
                for _pm in list(preseed):
                    await self._subscribe_mint(ws, _pm)
                self._preseed_mints = set()

            message_task   = asyncio.create_task(self._message_loop(ws))
            manager_task   = asyncio.create_task(self._subscription_manager(ws))
            ping_task      = asyncio.create_task(self._ping_loop(ws))
            stale_task     = asyncio.create_task(self._staleness_monitor())
            failsafe_task  = asyncio.create_task(self._coverage_failsafe())
            keepalive_task = asyncio.create_task(self._price_keepalive())
            watchdog_task  = asyncio.create_task(self._oracle_stall_watchdog())

            all_tasks = (message_task, manager_task, ping_task, stale_task,
                         failsafe_task, keepalive_task, watchdog_task)
            try:
                await asyncio.gather(*all_tasks, return_exceptions=True)
            finally:
                for t in all_tasks:
                    if not t.done():
                        t.cancel()
                await asyncio.gather(*all_tasks, return_exceptions=True)

    async def _message_loop(self, ws):
        from websockets.exceptions import ConnectionClosed, ConnectionClosedOK
        try:
            async for raw in ws:
                if not raw or not isinstance(raw, str):
                    continue
                try:
                    msg = json.loads(raw)
                    await self._handle_message(msg)
                except Exception:
                    pass
        except (ConnectionClosed, ConnectionClosedOK):
            raise
        except Exception as e:
            log.error("ws_oracle: MESSAGE_LOOP_FATAL %s", e)
            raise

    async def _handle_message(self, msg: dict):
        if "result" in msg and isinstance(msg.get("result"), int):
            req_id = msg.get("id")
            sub_id = msg["result"]
            mint = self._pending_reqs.pop(req_id, None)
            if mint:
                self._sub_id_for_mint[mint] = sub_id
                self._mint_for_sub_id[sub_id] = mint
                log.info("SUBSCRIBED_TO_MINT mint=%s sub_id=%d", mint[:16], sub_id)
            return

        if msg.get("method") == "accountNotification":
            params = msg.get("params", {})
            sub_id = params.get("subscription")
            value = params.get("result", {}).get("value", {})
            data = value.get("data")
            if not data or not isinstance(data, list) or len(data) < 1:
                return

            mint = self._mint_for_sub_id.get(sub_id)
            if not mint:
                return

            decoded = decode_bonding_curve(data[0])
            if not decoded:
                return

            vsol, vtok = decoded
            sol_usd = _get_sol_usd()
            price_sol = (vsol / 1e9) / (vtok / 1e6)
            price_usd = price_sol * sol_usd

            # PRE-THROTTLE: WSS can fire 10-50x/sec per mint.
            # TIERED: open positions gate at 0.1s (fast meter), others at 0.5s.
            # staleness_monitor + coverage_failsafe handle slower fallback.
            _now = time.time()
            _throttle = 0.1 if _is_open_position_mint(mint) else 0.5
            if _now - self._wss_pre_throttle.get(mint, 0.0) < _throttle:
                return
            self._wss_pre_throttle[mint] = _now

            loop = asyncio.get_running_loop()
            await loop.run_in_executor(None, _write_mtm, mint, price_usd, "helius")

    async def _subscribe_mint(self, ws, mint: str):
        if mint in self._subscribed:
            return
        pda = get_bonding_curve_pda_verified(mint)
        if not pda:
            log.warning("[ORACLE_SUBSCRIBE_FAIL] mint=%s reason=no_bonding_curve - will use DexScreener fallback", mint[:16])
            # No PDA = graduated or unknown token. Add to subscribed set with a
            # sentinel so staleness_monitor can provide DexScreener coverage.
            # Mark as sub_skipped so we know it's dex-only, not WSS.
            self._subscribed.add(mint)
            self._sub_id_for_mint[mint] = -1  # sentinel: dex-only, no WSS sub_id
            return

        req_id = self._next_id()
        self._pending_reqs[req_id] = mint

        try:
            await ws.send(json.dumps({
                "jsonrpc": "2.0",
                "id": req_id,
                "method": "accountSubscribe",
                "params": [pda, {"encoding": "base64", "commitment": "confirmed"}],
            }))
            self._subscribed.add(mint)
            log.info("[ORACLE_SUBSCRIBE_OK] mint=%s pda=%s provider=wss", mint[:16], pda[:16])
        except Exception as e:
            self._pending_reqs.pop(req_id, None)
            log.warning("[ORACLE_SUBSCRIBE_FAIL] mint=%s reason=send_failed: %s", mint[:16], e)
            # WSS dead - fetch HTTP fallback price so qualified rows get priced.
            # RATE GUARD: max one fallback attempt per mint per 30s.
            # Without this, a reconnect storm fires Birdeye 1200x/min and burns quota.
            _now_fb = time.time()
            _last_fb = getattr(self, "_wss_fail_fallback_ts", {})
            if _now_fb - _last_fb.get(mint, 0) >= 30.0:
                _last_fb[mint] = _now_fb
                self._wss_fail_fallback_ts = _last_fb
                try:
                    loop = asyncio.get_event_loop()
                    price, src = await loop.run_in_executor(
                        None, _fetch_best_fallback_price, mint
                    )
                    if price and price > 0:
                        await loop.run_in_executor(
                            None, _write_mtm, mint, price, f"wss_fail_fallback_{src}"
                        )
                        log.info("[WSS_FAIL_FALLBACK] mint=%s price=%.10f src=%s",
                                 mint[:16], price, src)
                except Exception as _fe:
                    log.debug("[WSS_FAIL_FALLBACK_ERR] mint=%s: %s", mint[:16], _fe)

    async def _unsubscribe_mint(self, ws, mint: str):
        sub_id = self._sub_id_for_mint.pop(mint, None)
        self._subscribed.discard(mint)
        self._pending_reqs = {k: v for k, v in self._pending_reqs.items() if v != mint}
        if sub_id is not None:
            self._mint_for_sub_id.pop(sub_id, None)
            try:
                req_id = self._next_id()
                await ws.send(json.dumps({
                    "jsonrpc": "2.0", "id": req_id,
                    "method": "accountUnsubscribe", "params": [sub_id],
                }))
            except Exception:
                pass

    async def _ping_loop(self, ws):
        """Ping disabled - QuickNode manages keepalive at protocol level."""
        while True:
            await asyncio.sleep(60)

    async def _subscription_manager(self, ws):
        cycle = 0
        force_resub_every = max(1, int(FORCE_RESUB_INTERVAL / POLL_OPEN_MINTS_SEC))
        while True:
            await asyncio.sleep(POLL_OPEN_MINTS_SEC)
            cycle += 1
            force = (cycle % force_resub_every == 0)

            # Phase A.1: refresh HOT_SET every 10s - tier-aware cadence
            try:
                loop_ref = asyncio.get_running_loop()
                await loop_ref.run_in_executor(None, _refresh_hot_set)
            except Exception:
                pass

            try:
                loop = asyncio.get_running_loop()

                # Root-cause fix: every cycle, derive oracle coverage from the
                # active paper_positions book. This keeps MTM truth aligned with
                # the positions actually shown/managed by the hub and execution
                # engine.
                # Drain any mints queued while WS was reconnecting
                if hasattr(self, "_pending_force_mints") and self._pending_force_mints and ws:
                    _drain = list(self._pending_force_mints)
                    self._pending_force_mints.clear()
                    for _dm in _drain:
                        if _dm not in self._subscribed:
                            await self._subscribe_mint(ws, _dm)
                            log.info("[ORACLE_DRAIN_LOOP] subscribed queued mint=%s", _dm[:16])

                open_mints = await loop.run_in_executor(None, _get_open_mints)
                open_set = set(open_mints)

                # Visible coverage log every poll cycle
                log.info(
                    "[OPEN_MINTS_TRACKED] open=%d subscribed=%d mints=%s",
                    len(open_set), len(self._subscribed),
                    [m[:10] for m in sorted(open_set)[:6]],
                )

                newly_injected = sorted(open_set - self._subscribed)
                if newly_injected:
                    not_logged = [m for m in newly_injected if m not in self._position_coverage_logged]
                    if not_logged:
                        log.warning(
                            "POSITION_MINT_COVERAGE_INJECTED count=%d mints=%s",
                            len(not_logged),
                            [m[:12] for m in not_logged[:20]],
                        )
                        self._position_coverage_logged.update(not_logged)

                for mint in open_mints:
                    if mint not in self._subscribed:
                        log.info("[ORACLE_SUBSCRIBE_ATTEMPT] mint=%s", mint[:16])
                        await self._subscribe_mint(ws, mint)

                if force:
                    for mint in list(self._subscribed):
                        if mint in open_set:
                            await self._unsubscribe_mint(ws, mint)
                            await asyncio.sleep(0.05)
                            await self._subscribe_mint(ws, mint)

                # Keep the oracle focused on the position set. Non-position
                # mints are allowed to arrive through notify_new_mint(), but the
                # persistent coverage contract is active positions first.
                for mint in list(self._subscribed):
                    if mint not in open_set:
                        await self._unsubscribe_mint(ws, mint)
                        _remove_mint_event_ts(mint)

            except Exception as e:
                log.error("SUBSCRIPTION_MANAGER_FATAL %s", e)
                return

    async def _staleness_monitor(self):
        """Fires every MTM_STALE_WARN_SEC. Uses Birdeye→Jupiter→DexScreener waterfall."""
        while True:
            await asyncio.sleep(MTM_STALE_WARN_SEC)
            loop = asyncio.get_running_loop()
            open_mints = await loop.run_in_executor(None, _get_open_mints)

            for mint in list(open_mints):
                age = get_mint_event_age(mint)
                if age >= DEXSCREENER_FALLBACK_SEC:
                    if age >= MTM_STALE_DEGRADE_SEC:
                        log.warning("[MTM_STALE] mint=%s age=%.1fs - fetching fallback price", mint[:16], age)
                    try:
                        price, src = await loop.run_in_executor(None, _fetch_best_fallback_price, mint)
                        if price and price > 0:
                            await loop.run_in_executor(None, _write_mtm, mint, price, src)
                            log.info("[FALLBACK_WRITE] mint=%s price=%.10f source=%s", mint[:16], price, src)
                    except Exception:
                        pass
                elif age >= MTM_STALE_WARN_SEC:
                    log.warning("[MTM_STALE] mint=%s age=%.1fs", mint[:16], age)

    async def _price_keepalive(self):
        """
        Dedicated 2s polling loop for all open position mints.
        Birdeye → Jupiter → DexScreener waterfall.
        Fires independently of WSS events - guarantees meter updates every ~2s
        regardless of on-chain activity sparsity.
        Only writes if age >= 2s to avoid hammering DB when WSS is already firing.

        FIX (keepalive bug): removed the ws-drain block that referenced `ws`
        as a free variable - _price_keepalive() has no ws in scope.
        Drain is handled correctly by _subscription_manager() and _force_subscribe().
        """
        while True:
            await asyncio.sleep(2.0)
            try:
                loop = asyncio.get_running_loop()

                open_mints = await loop.run_in_executor(None, _get_open_mints)
                if not open_mints:
                    continue
                for mint in list(open_mints):
                    # Skip if WSS already wrote a fresh tick recently
                    age = get_mint_event_age(mint)
                    if age < 2.0:
                        continue
                    try:
                        price, src = await loop.run_in_executor(
                            None, _fetch_best_fallback_price, mint
                        )
                        if price and price > 0:
                            await loop.run_in_executor(
                                None, _write_mtm, mint, price, "keepalive_" + src
                            )
                            log.info(
                                "[PRICE_KEEPALIVE] mint=%s price=%.10f source=%s age_before=%.1fs",
                                mint[:16], price, src, age,
                            )
                    except Exception as e:
                        log.debug("[PRICE_KEEPALIVE_ERR] mint=%s: %s", mint[:16], e)
            except Exception as e:
                log.warning("[PRICE_KEEPALIVE_LOOP_ERR] %s", e)

    async def _oracle_stall_watchdog(self):
        """
        Phase A.1: Oracle health state classification and auto-recovery.

        Runs every 10s. Classifies state based on last HOT-tier write:
          HEALTHY:  last HOT write < 10s ago
          DEGRADED: last HOT write 10-30s ago  (logs warning)
          STALLED:  last HOT write > 30s ago   (triggers fallback + resub)

        On STALLED:
          - forces HTTP fallback price fetch for all HOT-set mints
          - requests subscription rebuild for HOT mints
          - emits ORACLE_STALLED heartbeat so guardian can react
        """
        global _oracle_state
        _stall_consecutive = 0

        while True:
            await asyncio.sleep(10.0)
            try:
                now = time.time()
                loop = asyncio.get_running_loop()

                with _hot_set_lock:
                    hot_mints = set(_hot_set)
                with _stall_lock:
                    last_hot   = _last_hot_write
                    last_any   = _last_global_write
                    wpm        = len(_writes_this_minute)

                hot_age = (now - last_hot) if last_hot > 0 else float("inf")
                any_age = (now - last_any) if last_any > 0 else float("inf")

                # Classify state.
                # KEY FIX: if HOT set is empty AND any_age > 90s, that IS a stall.
                # Previously: _no_hot_mints → HEALTHY regardless of price age.
                # Problem: after a cluster, all tokens age out of HOT, oracle sees
                # "nothing to write" and reports HEALTHY while supervisor starves.
                _no_hot_mints = len(hot_mints) == 0
                _price_stale  = any_age > 90.0

                _dns_recent = _recent_dns_error_count(120.0)
                if _dns_recent >= 3:
                    # Infrastructure outage is not a quiet market. Keep the raw
                    # envelope telemetry, but publish an explicit outage state.
                    new_state = "NETWORK_OUTAGE"
                    _stall_consecutive += 1
                elif _no_hot_mints and _price_stale:
                    # HOT set empty AND prices haven't been written in 90s = stalled
                    new_state = "STALLED"
                    _stall_consecutive += 1
                elif _no_hot_mints or hot_age < 10.0:
                    new_state = "HEALTHY"
                    _stall_consecutive = 0
                elif hot_age < 30.0:
                    new_state = "DEGRADED"
                    _stall_consecutive = 0
                else:
                    new_state = "STALLED"
                    _stall_consecutive += 1

                prev_state = _oracle_state
                _oracle_state = new_state

                note = (
                    f"state={new_state} hot_age={hot_age:.0f}s any_age={any_age:.0f}s "
                    f"wpm={wpm} hot_set={len(hot_mints)}"
                )

                # Write oracle state to system_config for Mode B gate reads
                # ORACLE_ENVELOPE_TELEMETRY_20260715: the bare state string is not
                # enough for candidate-specific oracle authority. Publish the raw
                # envelope measurements (hot-set age, any-feed age, writes/min and
                # sample timestamp) so the live gate can verify a STALLED global
                # hot-set against the empirically profitable envelope instead of
                # treating STALLED as an unconditional capital veto.
                try:
                    with _db_connect() as _sc2:
                        for _tk, _tv in (
                            ("WS_ORACLE_STATE", new_state),
                            ("WS_ORACLE_HOT_AGE_SEC",
                             f"{hot_age:.1f}" if hot_age != float("inf") else "999999"),
                            ("WS_ORACLE_ANY_AGE_SEC",
                             f"{any_age:.1f}" if any_age != float("inf") else "999999"),
                            ("WS_ORACLE_WPM", str(int(wpm))),
                            ("WS_ORACLE_SAMPLED_AT", f"{now:.1f}"),
                        ):
                            _sc2.execute(
                                "INSERT OR REPLACE INTO system_config(key,value) "
                                "VALUES(?,?)",
                                (_tk, _tv),
                            )
                        _sc2.commit()
                except Exception:
                    pass

                if new_state == "HEALTHY":
                    if prev_state != "HEALTHY":
                        log.info("[ORACLE_HEALTHY] %s", note)
                    try:
                        update_heartbeat("ws_price_oracle", "ALIVE", note)
                    except Exception:
                        pass

                elif new_state == "DEGRADED":
                    log.warning("[ORACLE_DEGRADED] %s", note)
                    try:
                        update_heartbeat("ws_price_oracle", "DEGRADED", note)
                    except Exception:
                        pass

                elif new_state == "NETWORK_OUTAGE":
                    _log_rl(
                        "oracle_network_outage", log.error,
                        "[ORACLE_NETWORK_OUTAGE] %s dns_errors_120s=%d",
                        note, _dns_recent, every=60.0,
                    )
                    try:
                        update_heartbeat(
                            "ws_price_oracle", "NETWORK_OUTAGE",
                            f"NETWORK_OUTAGE {note} dns_errors_120s={_dns_recent}",
                        )
                    except Exception:
                        pass

                elif new_state == "STALLED":
                    _log_rl("oracle_stalled", log.error,
                            "[ORACLE_STALLED] %s - triggering fallback+resub",
                            note, every=30.0)
                    try:
                        update_heartbeat("ws_price_oracle", "STALLED",
                                         f"STALLED {note}")

                        # Write state to system_config for Mode B gate
                        try:
                            with _db_connect() as _sc:
                                _sc.execute(
                                    "INSERT OR REPLACE INTO system_config(key,value) "
                                    "VALUES('WS_ORACLE_STATE',?)",
                                    (new_state,)
                                )
                                _sc.commit()
                        except Exception:
                            pass

                        # Self-restart after 6 consecutive stall cycles (~60s)
                        if _stall_consecutive >= 6:
                            log.error(
                                "[ORACLE_SELF_RESTART] %d consecutive stalls - "
                                "restarting process to recover WSS connection",
                                _stall_consecutive,
                            )
                            try:
                                update_heartbeat("ws_price_oracle", "RESTARTING",
                                                 f"SELF_RESTART after {_stall_consecutive} stalls")
                            except Exception:
                                pass
                            import os as _os, sys as _sys
                            _os.execv(_sys.executable, [_sys.executable] + _sys.argv)
                    except Exception:
                        pass

                    # Auto-recovery: HTTP fallback for all HOT mints
                    if hot_mints:
                        for _mint in list(hot_mints)[:10]:  # cap at 10 to avoid API flood
                            try:
                                price, src = await loop.run_in_executor(
                                    None, _fetch_best_fallback_price, _mint
                                )
                                if price and price > 0:
                                    await loop.run_in_executor(
                                        None, _write_mtm, _mint, price, f"stall_recovery_{src}"
                                    )
                                    log.info(
                                        "[ORACLE_STALL_RECOVERY] mint=%s price=%.10f src=%s",
                                        _mint[:16], price, src,
                                    )
                            except Exception as _fe:
                                log.debug("stall fallback error mint=%s: %s", _mint[:16], _fe)

                    # KEY FIX: when HOT collapses to 0, fetch prices for
                    # qualified COLD tokens so they can re-enter the HOT set.
                    # This is what prevents post-cluster starvation.
                    try:
                        _cold_mints = await loop.run_in_executor(None, lambda: [
                            r["mint_address"] for r in
                            __import__("sqlite3").connect(str(__import__("pathlib").Path(__file__).resolve().parent.parent / "sentinuity_matrix.db"), timeout=2).execute(
                                "SELECT mint_address FROM market_snapshots "
                                "WHERE (quality_status='qualified' OR candidate_state='qualified') "
                                "AND latched=0 AND candidate_state NOT IN ('executed','vetoed','expired_stale') "
                                "ORDER BY COALESCE(qualified_at,0) DESC LIMIT 15"
                            ).fetchall()
                        ])
                        if _cold_mints:
                            log.info("[ORACLE_COLD_RECOVERY] fetching prices for %d qualified-COLD mints", len(_cold_mints))
                            for _mint in _cold_mints:
                                try:
                                    price, src = await loop.run_in_executor(
                                        None, _fetch_best_fallback_price, _mint
                                    )
                                    if price and price > 0:
                                        await loop.run_in_executor(
                                            None, _write_mtm, _mint, price, f"cold_recovery_{src}"
                                        )
                                except Exception:
                                    pass
                    except Exception as _cr:
                        log.debug("cold recovery error: %s", _cr)

                    # Force subscription rebuild for HOT mints
                    if hasattr(self, "_ws") and self._ws:
                        for _mint in list(hot_mints)[:10]:
                            try:
                                await self._subscribe_mint(self._ws, _mint)
                            except Exception:
                                pass
                        log.info("[ORACLE_STALL_RESUB] re-subscribed %d HOT mints", min(len(hot_mints), 10))

            except Exception as _we:
                log.debug("stall watchdog error: %s", _we)

    async def _coverage_failsafe(self):
        """
        Hard failsafe: for every OPEN position with zero post-entry Intel ticks,
        immediately fetch DexScreener and write a coverage_failsafe tick.
        Runs every 5s independently of WSS subscription state.
        This ensures the meter always has SOME price even when WSS/PDA fails.
        """
        import sqlite3 as _sq3
        while True:
            await asyncio.sleep(5.0)
            try:
                loop = asyncio.get_running_loop()
                open_positions = await loop.run_in_executor(None, self._get_open_positions_with_opened_at)
                if not open_positions:
                    continue
                for mint, opened_at in open_positions:
                    # Check post-entry tick count in Intel DB
                    tick_count = await loop.run_in_executor(
                        None, self._count_post_entry_ticks, mint, opened_at
                    )
                    if tick_count == 0:
                        log.warning(
                            "[COVERAGE_FAILSAFE] mint=%s opened_at=%.0f ZERO post-entry ticks - waterfall fetch",
                            mint[:16], opened_at,
                        )
                        try:
                            price, src = await loop.run_in_executor(None, _fetch_best_fallback_price, mint)
                            if price and price > 0:
                                await loop.run_in_executor(None, _write_mtm, mint, price, "coverage_failsafe_" + src)
                                log.info(
                                    "[COVERAGE_FAILSAFE_OK] mint=%s price=%.10f source=%s",
                                    mint[:16], price, src,
                                )
                            else:
                                log.warning("[COVERAGE_FAILSAFE_FAIL] mint=%s all sources returned no price", mint[:16])
                        except Exception as e:
                            log.warning("[COVERAGE_FAILSAFE_ERROR] mint=%s: %s", mint[:16], e)
            except Exception as e:
                log.warning("[COVERAGE_FAILSAFE_LOOP_ERROR] %s", e)

    def _get_open_positions_with_opened_at(self) -> list:
        """Returns list of (mint, opened_at) for all OPEN positions."""
        try:
            conn = _db_connect()
            rows = conn.execute(
                "SELECT mint_address, opened_at FROM paper_positions "
                "WHERE status='OPEN' AND mint_address IS NOT NULL"
            ).fetchall()
            conn.close()
            return [(str(r[0]).strip(), float(r[1] or 0)) for r in rows if r[0]]
        except Exception:
            return []

    def _count_post_entry_ticks(self, mint: str, opened_at: float) -> int:
        """Count Intel DB ticks after position open time."""
        try:
            import sqlite3 as _sq3
            idb_path = str(Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db")
            ic = _sq3.connect(idb_path, timeout=2.0)
            count = ic.execute(
                "SELECT COUNT(*) FROM mtm_ticks WHERE mint_address=? AND ts_ms>=?",
                (mint, opened_at * 1000)
            ).fetchone()[0]
            ic.close()
            return int(count)
        except Exception:
            return 0

    async def _force_subscribe(self, mint: str):
        if self._ws is None:
            # WS not ready - queue mint for immediate subscription on next connect
            if not hasattr(self, "_pending_force_mints"):
                self._pending_force_mints = set()
            self._pending_force_mints.add(mint)
            log.info("[ORACLE_QUEUE] mint=%s queued for immediate subscribe (ws not ready)", mint[:16])
            return
        await self._subscribe_mint(self._ws, mint)
        # Drain any queued mints now that WS is ready
        if hasattr(self, "_pending_force_mints") and self._pending_force_mints:
            _queued = list(self._pending_force_mints)
            self._pending_force_mints.clear()
            for _qm in _queued:
                if _qm not in self._subscribed:
                    await self._subscribe_mint(self._ws, _qm)
                    log.info("[ORACLE_DRAIN_QUEUE] subscribed queued mint=%s", _qm[:16])

    def stop(self):
        self._stop = True


# ── THREAD CONTROL ────────────────────────────────────────────────────────────
_oracle_instance: Optional[HeliusOracle] = None
_oracle_thread: Optional[threading.Thread] = None
_oracle_loop: Optional[asyncio.AbstractEventLoop] = None


def start_ws_oracle() -> None:
    global _oracle_instance, _oracle_thread, _oracle_loop
    if _oracle_thread and _oracle_thread.is_alive():
        return

    wss_url = _PREFERRED_WSS or HELIUS_WSS_URL or QUICKNODE_WSS
    if not wss_url:
        log.error("ws_oracle: no WSS URL configured - set HELIUS_WSS_URL or QUICKNODE_WSS")
        return

    def _run():
        global _oracle_loop, _oracle_instance
        loop = asyncio.new_event_loop()
        _oracle_loop = loop
        asyncio.set_event_loop(loop)
        oracle = HeliusOracle(wss_url)
        _oracle_instance = oracle

        # GOLD MASTER: open-position auto-resubscribe on restart
        # Pre-seed the oracle with all currently open positions so existing
        # trades are tracked immediately on restart without waiting for the
        # first subscription_manager poll cycle.
        try:
            open_mints = _get_open_mints()
            if open_mints:
                log.warning(
                    "POSITION_MINT_COVERAGE_INJECTED restart_preseed count=%d mints=%s",
                    len(open_mints),
                    [m[:12] for m in open_mints[:20]],
                )
                oracle._preseed_mints = set(open_mints)
                oracle._position_coverage_logged.update(open_mints)
            else:
                oracle._preseed_mints = set()
        except Exception as _pe:
            log.warning("ws_oracle: preseed scan failed: %s", _pe)
            oracle._preseed_mints = set()

        try:
            loop.run_until_complete(oracle.run())
        finally:
            loop.close()

    _oracle_thread = threading.Thread(target=_run, daemon=True, name="ws_price_oracle")
    _oracle_thread.start()
    log.info("ws_oracle: daemon thread started")


def notify_new_mint(mint: str) -> None:
    global _oracle_instance, _oracle_loop
    if not _oracle_instance or not _oracle_loop or not _oracle_loop.is_running():
        return
    try:
        asyncio.run_coroutine_threadsafe(_oracle_instance._force_subscribe(mint), _oracle_loop)
    except Exception:
        pass


def stop_ws_oracle() -> None:
    if _oracle_instance:
        _oracle_instance.stop()


# ── STANDALONE ────────────────────────────────────────────────────────────────

# SENTINUITY_SIGNOFF_ORACLE_HOT_CANDIDATE_UNIVERSE_V1
# ---------------------------------------------------------------------------
# Narrow sign-off patch payload for services/ws_price_oracle.py.
#
# Purpose:
#   Expand the oracle tracking/subscription universe so fresh, high-confidence
#   market_snapshots mints are warmed/priced before they are already open or
#   already latched.
#
# This is intentionally oracle-only and truth-only:
#   - does NOT fake/synthesize price
#   - does NOT set qualified=1
#   - does NOT set execution_ready=1
#   - does NOT set latched=1
#   - does NOT touch wallet/accounting/order execution
# ---------------------------------------------------------------------------
import os
import time


def _sentinuity_signoff_cfg_float(key: str, default: float) -> float:
    try:
        gv = globals().get("get_config_value")
        if callable(gv):
            return float(gv(key, str(default)))
    except Exception:
        pass
    try:
        return float(os.getenv(key, str(default)))
    except Exception:
        return float(default)


def _sentinuity_signoff_cfg_int(key: str, default: int) -> int:
    try:
        gv = globals().get("get_config_value")
        if callable(gv):
            return int(float(gv(key, str(default))))
    except Exception:
        pass
    try:
        return int(float(os.getenv(key, str(default))))
    except Exception:
        return int(default)


def _sentinuity_signoff_open_conn():
    gc = globals().get("get_connection")
    if callable(gc):
        return gc()
    import sqlite3 as _sqlite3
    from pathlib import Path as _Path
    root = _Path(__file__).resolve().parent.parent
    return _sqlite3.connect(str(root / "sentinuity_matrix.db"), timeout=5.0)


def _sentinuity_signoff_table_cols(conn, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _sentinuity_signoff_coalesce(cols: set[str], names: tuple[str, ...], default_sql: str) -> str:
    present = [n for n in names if n in cols]
    if not present:
        return default_sql
    return "COALESCE(" + ", ".join(present + [default_sql]) + ")"


def _sentinuity_signoff_hot_candidate_mints(existing: list[str]) -> list[str]:
    min_conf = _sentinuity_signoff_cfg_float("ORACLE_HOT_CANDIDATE_MIN_CONF", 0.80)
    max_age  = _sentinuity_signoff_cfg_float("ORACLE_HOT_CANDIDATE_MAX_AGE_SEC", 900.0)
    limit    = _sentinuity_signoff_cfg_int("ORACLE_HOT_CANDIDATE_LIMIT", 50)
    now      = time.time()
    cutoff   = now - max_age
    stale_price_cutoff = now - _sentinuity_signoff_cfg_float("ORACLE_HOT_PRICE_STALE_SEC", 60.0)

    out: list[str] = []
    conn = None
    try:
        conn = _sentinuity_signoff_open_conn()
        cols = _sentinuity_signoff_table_cols(conn, "market_snapshots")
        if "mint_address" not in cols:
            return out

        conf_expr    = _sentinuity_signoff_coalesce(cols, ("mint_confidence", "confidence", "confidence_score"), "0")
        created_expr = _sentinuity_signoff_coalesce(cols, ("created_at", "updated_at", "first_seen_at", "timestamp"), "0")
        state_expr   = _sentinuity_signoff_coalesce(cols, ("candidate_state",), "'pending'")

        where = [
            "mint_address IS NOT NULL",
            "TRIM(mint_address) != ''",
            f"CAST({conf_expr} AS REAL) >= ?",
            f"CAST({created_expr} AS REAL) >= ?",
            f"LOWER(COALESCE({state_expr}, 'pending')) NOT IN ('vetoed','exited','closed','reset_archived')",
        ]
        params: list[object] = [min_conf, cutoff]

        if "price_status" in cols:
            # Include stale `priced` qualified rows too. They already have a truth
            # price, but if it ages out before latch/execution the supervisor and
            # pre-entry firewall starve them. The oracle may refresh them; it still
            # never fakes price or sets execution flags.
            where.append("LOWER(COALESCE(price_status, 'pending')) IN ('pending','retry','','stale','unpriced','priced','oracle_pulse')")
        if "price_updated_at" in cols:
            where.append("(price_updated_at IS NULL OR CAST(COALESCE(price_updated_at,0) AS REAL) < ?)")
            params.append(stale_price_cutoff)

        sql = f"""
            SELECT DISTINCT mint_address
            FROM market_snapshots
            WHERE {' AND '.join(where)}
            ORDER BY CAST({created_expr} AS REAL) DESC
            LIMIT ?
        """
        params.append(limit)
        rows = conn.execute(sql, tuple(params)).fetchall()
        already = {str(x).strip() for x in existing if str(x).strip()}
        for r in rows:
            mint = str(r[0]).strip() if r and r[0] is not None else ""
            if mint and mint not in already and mint not in out:
                out.append(mint)
    except Exception as e:
        lg = globals().get("log") or globals().get("logger")
        try:
            if lg:
                lg.warning("[SIGNOFF_ORACLE_UNIVERSE] candidate query skipped: %s", e)
        except Exception:
            pass
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass
    return out


def _sentinuity_signoff_wrap_oracle_universe(fn_name: str) -> None:
    orig = globals().get(fn_name)
    if not callable(orig) or getattr(orig, "_sentinuity_signoff_wrapped", False):
        return

    def _wrapped(*args, **kwargs):
        try:
            base = orig(*args, **kwargs) or []
        except Exception:
            base = []

        try:
            mints = list(base)
        except Exception:
            mints = []

        seen = set()
        merged: list[str] = []
        for m in mints:
            s = str(m).strip()
            if s and s not in seen:
                merged.append(s)
                seen.add(s)

        for m in _sentinuity_signoff_hot_candidate_mints(merged):
            if m not in seen:
                merged.append(m)
                seen.add(m)

        max_total = _sentinuity_signoff_cfg_int("ORACLE_MAX_TRACKED_MINTS", 90)
        return merged[:max_total]

    _wrapped._sentinuity_signoff_wrapped = True  # type: ignore[attr-defined]
    _wrapped._sentinuity_original = orig          # type: ignore[attr-defined]
    globals()[fn_name] = _wrapped


# Support historical/current names seen across the Sentinuity oracle builds.
for _sent_fn in (
    "_get_open_mints",
    "get_open_mints",
    "_get_active_position_mints",
    "get_active_position_mints",
    "_get_tracked_mints",
    "get_tracked_mints",
):
    _sentinuity_signoff_wrap_oracle_universe(_sent_fn)
# /SENTINUITY_SIGNOFF_ORACLE_HOT_CANDIDATE_UNIVERSE_V1

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    from dotenv import load_dotenv
    load_dotenv()
    start_ws_oracle()
    try:
        _hb_counter = 0
        while True:
            time.sleep(10)
            _hb_counter += 1
            # Register heartbeat every 30s so guardian sees oracle state
            if _hb_counter % 3 == 0:
                try:
                    with _stall_lock:
                        _wpm = len(_writes_this_minute)
                    with _hot_set_lock:
                        _hss = len(_hot_set)
                    update_heartbeat(
                        "ws_price_oracle",
                        "ALIVE" if _oracle_state in ("HEALTHY", "INITIALIZING") else _oracle_state,
                        f"state={_oracle_state} wpm={_wpm} hot_set={_hss} open_mints={len(_get_open_mints())}"
                    )
                except Exception:
                    pass
    except KeyboardInterrupt:
        stop_ws_oracle()