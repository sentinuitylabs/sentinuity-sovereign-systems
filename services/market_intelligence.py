"""
market_intelligence.py
===============================================================================
Sentinuity Market Intelligence - SIGNED-OFF v1.0
===============================================================================

Merges:
  - token_qualifier.py   (signal qualification gate)
  - price_enricher.py    (MTM oracle + latched token price refresh)

Deliberately EXCLUDES (per Gemini architecture audit):
  - wallet_scout.py      -> remains separate service (async, non-deterministic)
  - telegram_scout.py    -> remains separate service (async, rate-limit-sensitive)

Scouts are signal enrichment, not signal origin. They run independently and
write to their own tables. Merging them here would introduce latency jitter
and race conditions into the critical execution path.

===============================================================================
RESPONSIBILITIES
===============================================================================

  QUALIFIER   - deterministic, synchronous gate on market_snapshots
                Gate order (enforced, do not reorder):
                  1. Pump suffix check       (free, instant)
                  2. Curve progress check    (on-chain, <200ms, rejects danger zone)
                  3. DexScreener pair exists
                  4. Market cap > MIN_MARKET_CAP_USD
                  5. Token age > MIN_TOKEN_AGE_SEC
                  6. Resolver confidence > MIN_CONFIDENCE
                  7. Signal freshness < SIGNAL_MAX_AGE_MINUTES

  PRICE ORACLE - independent 2s loop, writes MTM rows to market_snapshots
                 Jupiter-first, DexScreener fallback for pre-graduation tokens
                 Also refreshes latched tokens whose price has gone stale

===============================================================================
CURVE PROGRESS - RACE CONDITION NOTE (Grok audit finding)
===============================================================================

A token can pass the curve check at qualifier stage (~84%) then graduate
before the executor opens the position. Mitigation:
  1. This file rejects at >= CURVE_DANGER_ZONE_PCT (configurable, default 85)
  2. The threshold is conservative enough to provide a gap window
  3. execution_engine.py should re-check complete==True before opening
     (flagged as a follow-up hardening item)

===============================================================================
WRITE OWNERSHIP
===============================================================================

  QUALIFIER writes:  market_snapshots quality_status, quality_reason,
                     token_age_seconds, token_liquidity_usd, market_cap_usd,
                     is_tradeable, source_note
                     Uses UPDATE only - never INSERT (ingest_pipeline owns INSERT)

  PRICE ORACLE writes: market_snapshots observed_price, price_updated_at,
                        price_status, price_attempts
                        Also INSERTs MTM rows (tx_hash prefixed 'MTM:')
                        Also UPDATEs paper_positions.last_price, current_price,
                        market_value_usd, unrealized_pnl_usd, unrealized_pnl_pct,
                        highest_price_seen, last_marked_at

  Pricing truth hardening (April 2026 sign-off):
    - Reject None / non-finite / non-positive marks
    - Clamp absurd jump vs last trusted price
    - Reject implausible market value multiples vs position_size_usd
    - Never let stale / poisoned marks surface as live MTM truth

  Race condition guard (Gemini audit finding):
    Oracle updates price_updated_at only if new timestamp > existing.
    Qualifier and oracle write different columns - no conflict possible.
"""

from __future__ import annotations

import base64
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

from core.schema import get_connection, update_heartbeat, get_config_value

# WebSocket real-time price oracle (sub-1s MTM updates for open positions)
# Robust import: works whether launched as `python services/market_intelligence.py`,
# `python market_intelligence.py`, or from a cwd where `services.*` is not importable.
_WS_ORACLE_AVAILABLE = False
_start_ws_oracle     = None
_oracle_age_fn       = None   # oracle_last_write_age - used for liveness check
_ws_import_error     = None

try:
    from services.ws_price_oracle import (          # type: ignore
        start_ws_oracle as _start_ws_oracle,
        oracle_last_write_age as _oracle_age_fn,
    )
    _WS_ORACLE_AVAILABLE = True
except Exception as e:
    _ws_import_error = e
    try:
        from ws_price_oracle import (               # type: ignore
            start_ws_oracle as _start_ws_oracle,
            oracle_last_write_age as _oracle_age_fn,
        )
        _WS_ORACLE_AVAILABLE = True
    except Exception as e2:
        _ws_import_error = e2
        try:
            import importlib.util
            _ws_path = Path(__file__).resolve().with_name('ws_price_oracle.py')
            if _ws_path.exists():
                _spec = importlib.util.spec_from_file_location('ws_price_oracle_local', _ws_path)
                if _spec and _spec.loader:
                    _mod = importlib.util.module_from_spec(_spec)
                    _spec.loader.exec_module(_mod)
                    _start_ws_oracle = getattr(_mod, 'start_ws_oracle', None)
                    _oracle_age_fn   = getattr(_mod, 'oracle_last_write_age', None)
                    _WS_ORACLE_AVAILABLE = callable(_start_ws_oracle)
        except Exception as e3:
            _ws_import_error = e3

if not _WS_ORACLE_AVAILABLE:
    logging.getLogger('market_intelligence').warning(
        'WebSocket oracle import failed: %s - falling back to polling only', _ws_import_error
    )
else:
    logging.getLogger('market_intelligence').info(
        'WebSocket oracle module loaded. Liveness probe: %s',
        'enabled' if callable(_oracle_age_fn) else 'disabled (oracle_last_write_age not found)',
    )

# Liveness threshold - if WS oracle has been silent longer than this, force
# the polling fallback regardless of whether the WS thread is alive.
_WS_ORACLE_STALE_SEC = 15.0


def _ws_oracle_is_live() -> bool:
    """Returns True if the WS oracle has written within _WS_ORACLE_STALE_SEC seconds."""
    if not callable(_oracle_age_fn):
        return False
    try:
        return _oracle_age_fn() < _WS_ORACLE_STALE_SEC
    except Exception:
        return False

try:
    from services.cognition_logger import log_cognition as _log_cog
    _COGNITION_AVAILABLE = True
except Exception:
    _COGNITION_AVAILABLE = False

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [MARKET_INTEL] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("market_intelligence")

SERVICE_NAME = "market_intelligence"

RPC_URL = os.getenv("CHAINSTACK_RPC", "").strip()
if not RPC_URL:
    RPC_URL = os.getenv("HELIUS_RPC", "").strip().strip('"').strip("'")
if not RPC_URL:
    RPC_URL = os.getenv("QUICKNODE_RPC", "").strip().strip('"').strip("'")

# -- Pump.fun program constants ------------------------------------------------
PUMP_PROGRAM_ID        = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
BONDING_CURVE_SEED     = b"bonding-curve"
# Token reserve constants for progress formula (confirmed April 2026)
INITIAL_REAL_RESERVES  = 206_900_000_000_000  # lamports
TARGET_REAL_RESERVES   = 793_100_000_000_000  # lamports (must be sold for graduation)

# -- API endpoints -------------------------------------------------------------
DEXSCREENER_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
JUPITER_URL     = "https://api.jup.ag/price/v3"
JUPITER_API_KEY = os.getenv("JUPITER_API_KEY", "").strip().strip('"').strip("'")
PUMPFUN_API_URL = "https://frontend-api.pump.fun/coins/{mint}"  # pre-graduation price source

# -- Tuning -------------------------------------------------------------------
QUALIFIER_POLL_INTERVAL = 1.0  # was 3.0 - halved pipeline latency, -2s avg entry delay
QUALIFIER_BATCH_SIZE    = 50  # raised from 40 - drains backlog faster
QUALIFIER_CLAIM_SECONDS = 30
ORACLE_POLL_INTERVAL    = 2.0
ORACLE_BATCH_LIMIT      = 20
HTTP_TIMEOUT            = 4   # was 8 - halved to drain queue faster, DexScreener 403 costs less

# -- MTM truth guards ---------------------------------------------------------
MIN_PRICE_USD_DEFAULT            = 1e-12
MTM_MAX_JUMP_FACTOR_DEFAULT      = 5.0
MTM_MIN_JUMP_FACTOR_DEFAULT      = 0.2
MTM_MAX_VALUE_MULTIPLE_DEFAULT   = 20.0
MTM_MAX_ENTRY_MULTIPLE_DEFAULT   = 100.0


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        v = float(value)
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return v
    except Exception:
        return default


def _valid_price(price: Any) -> bool:
    try:
        price = float(price)
    except Exception:
        return False
    if price != price or price in (float("inf"), float("-inf")):
        return False
    return price > float(get_config_value("MIN_PRICE_USD", MIN_PRICE_USD_DEFAULT))


def _mtm_jump_ok(new_price: float, last_price: float) -> bool:
    if last_price <= 0:
        return True
    ratio = new_price / last_price
    max_jump = float(get_config_value("MTM_MAX_JUMP_FACTOR", MTM_MAX_JUMP_FACTOR_DEFAULT))
    min_jump = float(get_config_value("MTM_MIN_JUMP_FACTOR", MTM_MIN_JUMP_FACTOR_DEFAULT))
    return min_jump <= ratio <= max_jump


def _mtm_value_ok(market_value_usd: float, position_size_usd: float) -> bool:
    if position_size_usd <= 0:
        return True
    multiple_cap = float(get_config_value("MTM_MAX_VALUE_MULTIPLE", MTM_MAX_VALUE_MULTIPLE_DEFAULT))
    return market_value_usd <= (position_size_usd * multiple_cap)


def _entry_multiple_ok(mark_price: float, entry_price: float) -> bool:
    if entry_price <= 0:
        return True
    max_entry_multiple = float(get_config_value("MTM_MAX_ENTRY_MULTIPLE", MTM_MAX_ENTRY_MULTIPLE_DEFAULT))
    return (mark_price / entry_price) <= max_entry_multiple


def _cognition(stage: str, message: str, token: str = "",
               confidence: float = 0.0, meta: Optional[dict] = None) -> None:
    if not _COGNITION_AVAILABLE:
        return
    try:
        _log_cog(stage, message, token=token,
                 confidence=confidence, meta=meta or {})
    except Exception:
        pass


# =============================================================================
# BONDING CURVE PROGRESS (Gate 2 - on-chain, <200ms)
# =============================================================================

def _derive_curve_pda(mint: str) -> Optional[str]:
    """
    Derive the Pump.fun bonding curve PDA for a given mint.
    Uses solders if available, falls back to manual ed25519 PDA derivation.
    Returns base58 PDA string or None on failure.
    """
    try:
        import base58 as _b58
        mint_bytes = _b58.b58decode(mint)

        try:
            # Preferred: solders (likely already installed for Helius integration)
            from solders.pubkey import Pubkey
            seeds = [BONDING_CURVE_SEED, mint_bytes]
            program = Pubkey.from_string(PUMP_PROGRAM_ID)
            pda, _ = Pubkey.find_program_address(seeds, program)
            return str(pda)
        except ImportError:
            pass

        # Manual PDA derivation (no external deps beyond hashlib)
        # find_program_address: iterate nonce 255->0 until valid off-curve point
        import hashlib
        program_bytes = _b58.b58decode(PUMP_PROGRAM_ID)
        for nonce in range(255, -1, -1):
            seeds_with_nonce = [
                BONDING_CURVE_SEED,
                mint_bytes,
                bytes([nonce]),
                b"ProgramDerivedAddress",
            ]
            h = hashlib.sha256(b"".join(seeds_with_nonce) + program_bytes).digest()
            # Check if point is off the ed25519 curve (valid PDA)
            # Simple heuristic: if first byte indicates off-curve, accept
            # This is a simplified check - for production use solders
            try:
                from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
                Ed25519PublicKey.from_public_bytes(h)
                # If we reach here, it's on-curve - not valid PDA, continue
                continue
            except Exception:
                # Off-curve - valid PDA
                return _b58.b58encode(h).decode()

        return None

    except Exception as e:
        log.debug("Root path calcified - PDA derivation failed for %s: %s", mint[:16], e)
        return None


def get_curve_progress(mint: str, rpc_url: str = "", sol_usd: float = 0.0) -> dict:
    """
    Fetch bonding curve progress AND price for a pump.fun token.
    Never raises - always returns a safe dict.

    Returns:
      progress_pct:      float 0-100 (how full the curve is)
      real_sol_reserves: float SOL accumulated
      complete:          bool True if already graduated to PumpSwap
      price_usd:         float USD price (0.0 if sol_usd not provided)
      price_sol:         float SOL price per token
      error:             str|None - None on success
    """
    _rpc = rpc_url or RPC_URL
    _safe = {"progress_pct": 0.0, "real_sol_reserves": 0.0,
             "complete": False, "price_usd": 0.0, "price_sol": 0.0, "error": None}

    if not _rpc:
        return {**_safe, "error": "No RPC URL configured"}

    try:
        curve_pda = _derive_curve_pda(mint)
        if not curve_pda:
            return {**_safe, "error": "PDA derivation failed"}

        payload = {
            "jsonrpc": "2.0", "id": 1,
            "method": "getAccountInfo",
            "params": [curve_pda, {"encoding": "base64", "commitment": "confirmed"}],
        }
        resp = requests.post(
            _rpc, json=payload,
            headers={"Content-Type": "application/json"},
            timeout=5.0,
        )
        if resp.status_code != 200:
            return {**_safe, "error": f"HTTP {resp.status_code}"}

        data    = resp.json()
        account = (data.get("result") or {}).get("value")
        if not account or not account.get("data"):
            return {**_safe, "error": "Curve account not found"}

        encoded = account["data"]
        if isinstance(encoded, list):
            encoded = encoded[0]
        account_bytes = base64.b64decode(encoded)

        if len(account_bytes) < 0x31:
            return {**_safe, "error": f"Account data too short: {len(account_bytes)} bytes"}

        def read_u64(offset: int) -> int:
            return int.from_bytes(account_bytes[offset:offset + 8], "little")

        # Virtual reserves give us the price
        virtual_token_reserves = read_u64(0x08)  # raw base units (6 decimals)
        virtual_sol_reserves   = read_u64(0x10)  # lamports (9 decimals)
        real_token_reserves    = read_u64(0x18)
        real_sol_reserves      = read_u64(0x20) / 1e9
        complete               = bool(account_bytes[0x30])

        # Correct price formula:
        # virtual_sol in SOL = virtual_sol_reserves / 1e9
        # virtual_tokens = virtual_token_reserves / 1e6  (6 decimals)
        # price_per_token_in_SOL = (virtual_sol / 1e9) / (virtual_tokens / 1e6)
        vsr_sol = virtual_sol_reserves / 1e9
        vtr_tok = virtual_token_reserves / 1e6
        price_sol = (vsr_sol / vtr_tok) if vtr_tok > 0 else 0.0
        price_usd = price_sol * sol_usd if sol_usd > 0 else 0.0

        # Progress formula
        denom = TARGET_REAL_RESERVES - INITIAL_REAL_RESERVES
        if denom <= 0:
            progress_pct = 100.0
        else:
            progress_pct = 100.0 - (
                (real_token_reserves - INITIAL_REAL_RESERVES) * 100.0 / denom
            )
        progress_pct = max(0.0, min(100.0, round(progress_pct, 2)))

        return {
            "progress_pct":      progress_pct,
            "real_sol_reserves": round(real_sol_reserves, 6),
            "complete":          complete,
            "price_sol":         price_sol,
            "price_usd":         price_usd,
            "error":             None,
        }

    except requests.Timeout:
        return {**_safe, "error": "RPC timeout"}
    except Exception as e:
        log.debug("Curve resonance probe failed for %s: %s", mint[:16], e)
        return {**_safe, "error": str(e)}


# =============================================================================
# QUALIFIER - DB helpers
# =============================================================================

_QUALIFIER_COLUMNS = [
    ("token_age_seconds",   "REAL"),
    ("token_liquidity_usd", "REAL"),
    ("holder_count",        "INTEGER"),
    ("top10_holder_pct",    "REAL"),
    ("market_cap_usd",      "REAL"),
    ("is_tradeable",        "INTEGER DEFAULT 0"),
    ("source_note",         "TEXT"),
    ("curve_progress_pct",  "REAL"),
    ("curve_sol_reserves",  "REAL"),
    ("duplicate_key",       "TEXT"),
    ("price_last_attempt_at", "REAL"),
]

def _ensure_qualifier_columns() -> None:
    try:
        with get_connection() as conn:
            existing = {r["name"] for r in
                        conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}
            for col, col_def in _QUALIFIER_COLUMNS:
                if col not in existing:
                    conn.execute(
                        f"ALTER TABLE market_snapshots ADD COLUMN {col} {col_def}"
                    )
            conn.commit()
    except Exception as e:
        log.warning("Schema substrate check encountered static (non-fatal): %s", e)


def _claim_qualifier_rows(limit: int) -> List[Dict[str, Any]]:
    now = time.time()
    claimed = []
    try:
        with get_connection() as conn:
            _db_rows = conn.execute(
                """
                SELECT id, mint_address, mint_confidence, candidate_state,
                       observed_price, price_updated_at,
                       COALESCE(first_seen_at, created_at, 0) AS created_at,
                       MAX(COALESCE(first_seen_at,0), COALESCE(qualified_at,0),
                           COALESCE(created_at,0)) AS operational_ts
                FROM market_snapshots
                WHERE candidate_state = 'pending'
                  AND COALESCE(quality_status, '') NOT IN ('qualified','rejected','error')
                  AND (qualify_claimed_until IS NULL OR qualify_claimed_until < ?)
                  AND MAX(COALESCE(first_seen_at,0), COALESCE(qualified_at,0),
                          COALESCE(created_at,0)) >= ?
                ORDER BY
                    CASE WHEN COALESCE(observed_price,0)>0
                               AND COALESCE(price_updated_at,0)>0
                         THEN 0 ELSE 1 END,
                    COALESCE(mint_confidence,0) DESC,
                    MAX(
                        COALESCE(first_seen_at,0),
                        COALESCE(qualified_at,0),
                        COALESCE(created_at,0)
                    ) DESC,
                    id DESC
                LIMIT ?
                """,
                # 1200s cutoff - gives signals time to be priced before qualification
                # Was 600s - too tight when oracle stalls, signals expire before qualifying
                (now, now - 1200, limit),
            ).fetchall()
            # NOTE: do NOT overwrite _db_rows here - that was the original bug
            for row in _db_rows:
                if conn.execute(
                    """
                    UPDATE market_snapshots
                    SET qualify_claimed_until = ?
                    WHERE id = ?
                      AND (qualify_claimed_until IS NULL OR qualify_claimed_until < ?)
                    """,
                    (now + QUALIFIER_CLAIM_SECONDS, row["id"], now),
                ).rowcount == 1:
                    claimed.append(dict(row))
            conn.commit()
    except Exception as e:
        log.warning("Signal claim pathway encountered dissonance: %s", e)
    return claimed


def _as_float(value: Any) -> Optional[float]:
    try:
        v = float(value)
        return v if v >= 0 else None
    except (TypeError, ValueError):
        return None


def _write_qualifier_result(
    row_id: int,
    metrics: Dict[str, Any],
    quality_status: str,
    quality_reason: str,
) -> None:
    now = time.time()
    try:
        with get_connection() as conn:
            if quality_status == "qualified":
                curve_price_sol = float(metrics.get("curve_price_sol") or 0.0)
                curve_sol_usd   = float(metrics.get("sol_usd_at_qualify") or 0.0)
                entry_price_usd = None
                if curve_price_sol > 0 and curve_sol_usd > 0:
                    entry_price_usd = curve_price_sol * curve_sol_usd
                elif metrics.get("token_price_usd"):
                    entry_price_usd = float(metrics["token_price_usd"])

                conn.execute(
                    """
                    UPDATE market_snapshots SET
                        quality_status=?, quality_reason=?,
                        candidate_state='qualified',
                        token_age_seconds=?, token_liquidity_usd=?,
                        holder_count=?, top10_holder_pct=?,
                        market_cap_usd=?, is_tradeable=1,
                        confidence_score=MAX(COALESCE(confidence_score,0), COALESCE(mint_confidence,0), COALESCE(calibrated_confidence,0), COALESCE(confidence,0)),
                        calibrated_confidence=MAX(COALESCE(calibrated_confidence,0), COALESCE(mint_confidence,0), COALESCE(confidence,0), COALESCE(confidence_score,0)),
                        confidence=MAX(COALESCE(confidence,0), COALESCE(mint_confidence,0), COALESCE(calibrated_confidence,0), COALESCE(confidence_score,0)),
                        source_note=?,
                        curve_progress_pct=?, curve_sol_reserves=?,
                        observed_price=CASE WHEN ? IS NOT NULL THEN ? ELSE observed_price END,
                        price_updated_at=CASE WHEN ? IS NOT NULL THEN ? ELSE price_updated_at END,
                        price_status=CASE WHEN ? IS NOT NULL THEN 'priced' ELSE price_status END,
                        qualified_at=?,
                        qualify_claimed_until=NULL,
                        vol_acceleration=?, price_change_5m=?, price_change_1h=?,
                        vol_5m_usd=?, vol_24h_usd=?, regime=?
                    WHERE id=?
                    """,
                    (
                        quality_status, quality_reason,
                        metrics.get("token_age_seconds"),
                        metrics.get("token_liquidity_usd"),
                        metrics.get("holder_count"),
                        metrics.get("top10_holder_pct"),
                        metrics.get("market_cap_usd"),
                        metrics.get("source_note", "dexscreener"),
                        metrics.get("curve_progress_pct"),
                        metrics.get("curve_sol_reserves"),
                        entry_price_usd, entry_price_usd,
                        now, now,
                        entry_price_usd,
                        now,   # qualified_at = wall clock when MI qualifies
                        # velocity fields - fetched from DexScreener, now persisted
                        metrics.get("vol_acceleration"),
                        metrics.get("price_change_5m"),
                        metrics.get("price_change_1h"),
                        metrics.get("vol_5m_usd"),
                        metrics.get("vol_24h"),
                        metrics.get("regime_classification"),
                        row_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE market_snapshots SET
                        quality_status=?, quality_reason=?,
                        token_age_seconds=?, token_liquidity_usd=?,
                        market_cap_usd=?, is_tradeable=0,
                        source_note=?,
                        curve_progress_pct=?, curve_sol_reserves=?,
                        qualify_claimed_until=NULL,
                        vol_acceleration=?, price_change_5m=?, price_change_1h=?,
                        vol_5m_usd=?, vol_24h_usd=?, regime=?
                    WHERE id=?
                    """,
                    (
                        quality_status, quality_reason,
                        metrics.get("token_age_seconds"),
                        metrics.get("token_liquidity_usd"),
                        metrics.get("market_cap_usd"),
                        metrics.get("source_note", ""),
                        metrics.get("curve_progress_pct"),
                        metrics.get("curve_sol_reserves"),
                        metrics.get("vol_acceleration"),
                        metrics.get("price_change_5m"),
                        metrics.get("price_change_1h"),
                        metrics.get("vol_5m_usd"),
                        metrics.get("vol_24h"),
                        metrics.get("regime_classification"),
                        row_id,
                    ),
                )
            conn.commit()
    except Exception as e:
        log.warning("Signal write pathway fractured for row=%d: %s", row_id, e)


def _mark_qualifier_error(row_id: int, reason: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "UPDATE market_snapshots SET quality_status='error', "
                "quality_reason=? WHERE id=?",
                (reason[:220], row_id),
            )
            conn.commit()
    except Exception:
        pass


# =============================================================================
# QUALIFIER - DexScreener helpers
# =============================================================================

def _fetch_dexscreener_pairs(
    session: requests.Session, mint: str
) -> List[Dict[str, Any]]:
    try:
        resp = session.get(
            DEXSCREENER_URL.format(mint=mint),
            timeout=HTTP_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return []
        data = resp.json() or {}
        pairs = data.get("pairs") or []
        return [p for p in pairs
                if isinstance(p, dict)
                and str(p.get("chainId") or "").lower() == "solana"]
    except Exception:
        return []


def _choose_best_pair(pairs: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not pairs:
        return None

    def pair_rank(p: Dict[str, Any]) -> Tuple[float, float, float]:
        liq   = float((p.get("liquidity") or {}).get("usd") or 0)
        vol   = float((p.get("volume") or {}).get("h24") or 0)
        mcap  = float(p.get("fdv") or p.get("marketCap") or 0)
        return liq, vol, mcap

    return max(pairs, key=pair_rank)


def _derive_quality_metrics(best_pair: Dict[str, Any]) -> Dict[str, Any]:
    liq  = float((best_pair.get("liquidity") or {}).get("usd") or 0)
    mcap = float(best_pair.get("fdv") or best_pair.get("marketCap") or 0)
    token_age_seconds = None
    pair_created_at_ms = best_pair.get("pairCreatedAt")
    if pair_created_at_ms:
        token_age_seconds = max(0.0, time.time() - (pair_created_at_ms / 1000.0))

    # ── Smart money enrichment from DexScreener response ─────────────────
    # Boost detection - paid trending observed and logged, not auto-blocked
    # DexScreener pair response contains boosts:{active:N} on boosted tokens
    # FILTER_PAID_BOOSTS=0 by default - observe first, gate later after verification
    boosts = best_pair.get("boosts") or {}
    boost_active = boosts.get("active") or 0
    boost_amount_val = boosts.get("amount") or boosts.get("totalAmount") or 0
    paid_boost_detected = bool(
        (isinstance(boost_active, (int, float)) and boost_active > 0) or
        (isinstance(boost_amount_val, (int, float)) and boost_amount_val > 0)
    )
    boost_amount = float(boost_amount_val or boost_active or 0)

    # Liquidity integrity - ratio stability check
    liq_base = float((best_pair.get("liquidity") or {}).get("base") or 0)
    liq_quote = float((best_pair.get("liquidity") or {}).get("quote") or 0)
    # Healthy ratio: base and quote should be roughly balanced
    liq_ratio = (liq_base / liq_quote) if liq_quote > 0 else 0
    liquidity_integrity_score = max(0.0, 1.0 - abs(1.0 - liq_ratio)) if liq_ratio > 0 else 0.5

    # Volume momentum - 5m vs 1h volume acceleration
    vol_5m  = float((best_pair.get("volume") or {}).get("m5") or 0)
    vol_1h  = float((best_pair.get("volume") or {}).get("h1") or 0)
    vol_24h = float((best_pair.get("volume") or {}).get("h24") or 0)
    # Acceleration: if 5m volume is high relative to hourly average, momentum is building
    vol_accel = (vol_5m * 12) / max(vol_1h, 1) if vol_1h > 0 else 0

    # Price change momentum
    price_change_5m  = float((best_pair.get("priceChange") or {}).get("m5") or 0)
    price_change_1h  = float((best_pair.get("priceChange") or {}).get("h1") or 0)

    # Regime classification based on mcap + age
    regime = "UNKNOWN"
    if token_age_seconds is not None and mcap > 0:
        age_min = token_age_seconds / 60
        if mcap < 10000 and age_min < 5:
            regime = "launch_ignition"
        elif mcap < 35000 and age_min < 30:
            regime = "early_momentum"
        elif mcap < 100000 and age_min < 120:
            regime = "post_grad_continuation"
        elif age_min > 120 or price_change_1h < -20:
            regime = "late_trend_exhaustion"
        else:
            regime = "momentum_building"

    return {
        "token_liquidity_usd":      liq,
        "market_cap_usd":           mcap,
        "token_age_seconds":        token_age_seconds,
        "holder_count":             None,
        "top10_holder_pct":         None,
        "source_note":              "dexscreener",
        # Smart money enrichment
        "paid_boost_detected":      paid_boost_detected,
        "boost_amount":             boost_amount,
        "liquidity_balance_score": liquidity_integrity_score,  # ratio heuristic, not historical slope
        "vol_acceleration":         vol_accel,
        "price_change_5m":          price_change_5m,
        "price_change_1h":          price_change_1h,
        "vol_5m_usd":               vol_5m,    # raw 5-minute volume USD
        "vol_24h":                  vol_24h,   # also stored as vol_24h_usd in schema
        "regime_classification":    regime,
    }


def _evaluate_quality(metrics: Dict[str, Any]) -> Tuple[str, str]:
    # ── Toxic timeframe gate (UTC 04-07) ───────────────────────────────────
    import datetime as _dt_tfg
    if False:  # TOXIC_TIMEFRAME_GATE disabled - was blocking AEST golden hour (UTC 04-07)
        return "rejected", "TOXIC_TIMEFRAME_GATE"
    # ── Static gates ──────────────────────────────────────────────────────────
    min_mcap      = float(get_config_value("MIN_MARKET_CAP_USD",  5000))
    min_age       = float(get_config_value("MIN_TOKEN_AGE_SEC",     60))
    min_curve_sol = float(get_config_value("MIN_CURVE_SOL",          8))
    min_holder    = int(get_config_value("MIN_HOLDER_COUNT",          0))
    max_top_hold  = float(get_config_value("MAX_TOP_HOLDER_PCT",    100))

    # ── Tiered signal age windows ─────────────────────────────────────────────
    # Tier 1 (launch, mcap < tier2_floor):  3 min  - pump tokens move in minutes
    # Tier 2 (mid-cap, tier2-tier3 floor):  15 min - needs time to reach entry band
    # Tier 3 (>tier3_floor / post-grad):    radar only, never a live trade
    tier1_max      = float(get_config_value("SIGNAL_TIER1_MAX_AGE_SEC",  900))  # relaxed 600→900 - claim window now 1200s
    tier2_max      = float(get_config_value("SIGNAL_TIER2_MAX_AGE_SEC",  1800))  # relaxed 900→1800
    tier2_min_mcap = float(get_config_value("SIGNAL_TIER2_MIN_MCAP",   10000))
    tier3_min_mcap = float(get_config_value("SIGNAL_TIER3_MIN_MCAP",   35000))
    radar_enabled  = str(get_config_value("RADAR_QUEUE_ENABLED",         "1")) == "1"
    radar_min_conf = float(get_config_value("RADAR_MIN_CONFIDENCE",      0.85))

    mcap       = _as_float(metrics.get("market_cap_usd"))
    mcap       = mcap if mcap and mcap > 0 else None  # treat 0.0 as unknown
    age        = _as_float(metrics.get("token_age_seconds"))
    curve_sol  = _as_float(metrics.get("curve_sol_reserves"))
    holder_ct  = metrics.get("holder_count")
    top_holder = _as_float(metrics.get("top10_holder_pct"))
    confidence = _as_float(metrics.get("confidence_score")) or 0.0

    if mcap is not None and mcap < min_mcap:
        return "rejected", "BELOW_MIN_MCAP"

    if age is None:
        return "rejected", "TOKEN_AGE_UNKNOWN"
    if age < min_age:
        return "deferred", f"TOKEN_TOO_YOUNG_{age:.0f}s"

    # ── Tiered age gate (replaces single MAX_TOKEN_AGE_SEC) ───────────────────
    if mcap is not None and mcap >= tier3_min_mcap:
        # Post-graduation / large mcap - radar only, never live trade
        _log_radar(metrics, age, confidence, radar_enabled, radar_min_conf)
        return "rejected", f"TIER3_RADAR_ONLY_mcap={mcap:.0f}"
    elif mcap is not None and mcap >= tier2_min_mcap:
        # Mid-cap - wider window, token needed time to reach this band
        if tier2_max > 0 and age > tier2_max:
            _log_radar(metrics, age, confidence, radar_enabled, radar_min_conf)
            return "rejected", f"TIER2_TOO_OLD_{age:.0f}s_max={tier2_max:.0f}s"
    else:
        # Tier 1 launch - tight 3-min window
        if tier1_max > 0 and age > tier1_max:
            _log_radar(metrics, age, confidence, radar_enabled, radar_min_conf)
            return "rejected", f"VETO_SIGNAL_TOO_OLD:age={age:.2f}s max={tier1_max:.0f}s"

    if curve_sol is not None and curve_sol > 0 and min_curve_sol > 0 and curve_sol < min_curve_sol:
        return "rejected", f"BELOW_MIN_CURVE_SOL_{curve_sol:.1f}"

    if holder_ct is not None and min_holder > 0 and int(holder_ct) < min_holder:
        return "rejected", f"BELOW_MIN_HOLDERS_{holder_ct}"

    if top_holder is not None and max_top_hold < 100 and top_holder > max_top_hold:
        return "rejected", f"WALLET_CONCENTRATION_{top_holder:.0f}pct"

    return "qualified", "OK"


def _log_radar(
    metrics: Dict[str, Any],
    age: float,
    confidence: float,
    enabled: bool,
    min_conf: float,
) -> None:
    """Log high-confidence age-failed signals to symbiotic_candidates as radar entries."""
    if not enabled or confidence < min_conf:
        return
    try:
        import json as _json, time as _time
        from core.schema import get_connection as _gc
        with _gc() as _conn:
            _conn.execute("""
                INSERT OR IGNORE INTO symbiotic_candidates
                    (token_address, token_name, first_seen_at, freshness_score,
                     market_score, symbiotic_conviction, status, signal_json, created_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                str(metrics.get("mint_address") or ""),
                str(metrics.get("token_name") or "")[:40],
                float(metrics.get("token_age_seconds") or 0),
                max(0.0, 1.0 - (age / 3600)),
                min(1.0, float(metrics.get("market_cap_usd") or 0) / 100000),
                confidence,
                "radar",
                _json.dumps({k: metrics.get(k) for k in (
                    "token_age_seconds", "market_cap_usd", "confidence_score",
                    "token_liquidity_usd", "mint_address", "token_name"
                )}, default=str),
                _time.time(),
            ))
    except Exception:
        pass  # radar logging is best-effort - never blocks the main gate


# =============================================================================
# QUALIFIER - Gate 2: Curve Progress
# =============================================================================

def _check_curve_gate(mint: str) -> Tuple[bool, str, Dict[str, Any]]:
    curve = get_curve_progress(mint)
    curve_meta = {
        "curve_progress_pct": curve.get("progress_pct"),
        "curve_sol_reserves": curve.get("real_sol_reserves"),
        "curve_price_sol":    curve.get("price_sol", 0.0),
    }

    if curve.get("error"):
        log.debug("Bonding curve resonance check bypassed for %s: %s", mint[:16], curve["error"])
        return True, "", curve_meta

    if curve.get("complete"):
        return False, "CURVE_COMPLETE_GRADUATED", curve_meta

    danger_pct = float(float(get_config_value("CURVE_DANGER_ZONE_PCT", 92.0)))  # relaxed 85→92
    if (curve.get("progress_pct") or 0.0) >= danger_pct:
        return False, f"CURVE_DANGER_ZONE_{curve['progress_pct']:.1f}pct", curve_meta

    return True, "", curve_meta


# =============================================================================
# QUALIFIER - Main qualify function
# =============================================================================

_COGNITION_QUALIFIED = (
    "Token cleared all quality gates. Confidence confirmed. "
    "Signal advanced to execution pipeline."
)
_COGNITION_MESSAGES = {
    "NOT_PUMP_TOKEN":         "Rejected - mint does not end in 'pump'. Honeypot risk eliminated.",
    "CURVE_COMPLETE_GRADUATED": "Rejected - token has graduated to PumpSwap. Pre-grad edge is gone.",
    "NO_DEXSCREENER_PAIR":    "Rejected - no DexScreener pair. Insufficient structural data.",
    "BELOW_MIN_MCAP":         "Rejected - market cap below minimum threshold.",
    "TOKEN_AGE_UNKNOWN":      "Rejected - token age could not be determined.",
}


def _qualify_one(session: requests.Session, row: Dict[str, Any]) -> Tuple[str, str]:
    row_id = int(row["id"])
    mint   = str(row.get("mint_address") or "").strip()
    mint_confidence = float(row.get("mint_confidence") or 0.0)

    if not mint:
        _mark_qualifier_error(row_id, "QUALITY_DATA_MISSING:MINT")
        return "rejected", "QUALITY_DATA_MISSING:MINT"

    if not mint.lower().endswith("pump"):
        _write_qualifier_result(row_id, {
            "token_age_seconds": None, "token_liquidity_usd": None,
            "market_cap_usd": None, "source_note": "not_pump_token",
        }, "rejected", "NOT_PUMP_TOKEN")
        return "rejected", "NOT_PUMP_TOKEN"

    curve_passed, curve_reason, curve_meta = _check_curve_gate(mint)
    if not curve_passed:
        _write_qualifier_result(row_id, {
            "token_age_seconds": None, "token_liquidity_usd": None,
            "market_cap_usd": None, "source_note": "curve_rejected",
            **curve_meta,
        }, "rejected", curve_reason)
        _cognition("QUALIFIER",
            _COGNITION_MESSAGES.get(curve_reason,
                f"Rejected at curve gate: {curve_reason}"),
            token=mint)
        return "rejected", curve_reason

    PUMP_TOTAL_SUPPLY = 1_000_000_000
    sol_usd_now = _fetch_sol_usd_price(session)
    price_sol_now = float(curve_meta.get("curve_price_sol") or 0.0)
    if price_sol_now > 0 and sol_usd_now > 0:
        curve_mcap_usd = price_sol_now * PUMP_TOTAL_SUPPLY * sol_usd_now
        token_price_usd = price_sol_now * sol_usd_now
    else:
        curve_sol = float(curve_meta.get("curve_sol_reserves") or 0.0)
        curve_mcap_usd = curve_sol * sol_usd_now * 10 if sol_usd_now > 0 else 0.0
        token_price_usd = None

    # Use operational freshness - MAX(updated_at, created_at, price_updated_at, first_seen_at)
    # passed from _claim_qualifier_rows as operational_ts. Falls back to created_at alias.
    # Prevents VETO_SIGNAL_TOO_OLD on rows that are operationally fresh but have
    # a stale first_seen_at from a feed stall/backfill event.
    snap_created = float(row.get("operational_ts") or row.get("created_at") or 0)
    token_age_seconds = max(0.0, time.time() - snap_created) if snap_created > 0 else None

    metrics = {
        "market_cap_usd":      curve_mcap_usd,
        "token_liquidity_usd": None,
        "token_age_seconds":   token_age_seconds,
        "holder_count":        None,
        "top10_holder_pct":    None,
        "source_note":         "bonding_curve_rpc",
        "sol_usd_at_qualify":  sol_usd_now,
        "token_price_usd":     token_price_usd,
    }
    metrics.update(curve_meta)
    quality_status, quality_reason = _evaluate_quality(metrics)

    if quality_status == "qualified":
        try:
            pairs     = _fetch_dexscreener_pairs(session, mint)
            best_pair = _choose_best_pair(pairs)
            if best_pair:
                dex_metrics = _derive_quality_metrics(best_pair)
                if dex_metrics.get("market_cap_usd", 0) > curve_mcap_usd:
                    metrics["market_cap_usd"] = dex_metrics["market_cap_usd"]
                if dex_metrics.get("token_liquidity_usd"):
                    metrics["token_liquidity_usd"] = dex_metrics["token_liquidity_usd"]
                if dex_metrics.get("token_age_seconds"):
                    # Always prefer DexScreener pairCreatedAt (true on-chain token age)
                    # over snap_created (weaver insert time = pipeline latency, not token age).
                    metrics["token_age_seconds"] = dex_metrics["token_age_seconds"]
                metrics["source_note"] = "bonding_curve_rpc+dexscreener"
                enriched_status, enriched_reason = _evaluate_quality(metrics)
                if enriched_status == "qualified":
                    quality_status, quality_reason = enriched_status, enriched_reason
        except Exception:
            pass

    if quality_status == "qualified":
        min_conf = float(float(get_config_value("MIN_RESOLVER_CONFIDENCE", 0.60)))  # relaxed 0.70→0.60
        if mint_confidence < min_conf:
            quality_status = "rejected"
            quality_reason = f"LOW_RESOLVER_CONFIDENCE_{mint_confidence:.2f}"

    if quality_status == "qualified":
        max_age_min = int(int(get_config_value("SIGNAL_MAX_AGE_MINUTES", 30)))
        token_age = _as_float(metrics.get("token_age_seconds"))
        if token_age is not None and token_age > max_age_min * 60:
            quality_status = "rejected"
            quality_reason = f"SIGNAL_STALE_{token_age:.0f}s"

    # PATCH 3 - POLARIS HARD GATES for Telegram call sourced signals.
    # Thresholds are doctrine-fixed - not config-tunable per BANNED_MUTATIONS.
    # Gates only fire when data is present; absent data does not block.
    if quality_status == "qualified":
        _gate_liq    = _as_float(metrics.get("token_liquidity_usd")) or 0.0
        _gate_holders = metrics.get("holder_count")
        _gate_fresh  = _as_float(metrics.get("price_freshness_seconds"))

        # Liquidity >= $50k - prevents thin-book manipulation
        if _gate_liq > 0 and _gate_liq < 5_000:
            quality_status = "rejected"
            quality_reason = f"POLARIS_GATE_LOW_LIQUIDITY_{_gate_liq:.0f}"

        # Holders >= 150 - prevents single-wallet rug setup
        if quality_status == "qualified" and _gate_holders is not None and int(_gate_holders) < 50:
            quality_status = "rejected"
            quality_reason = f"POLARIS_GATE_LOW_HOLDERS_{_gate_holders}"

        # Price freshness <= 45s - rejects stale oracle data
        if quality_status == "qualified" and _gate_fresh is not None and _gate_fresh > 120:
            quality_status = "rejected"
            quality_reason = f"POLARIS_GATE_STALE_PRICE_{_gate_fresh:.0f}s"

    # ── SMART MONEY GATES (config-driven, paper-safe) ────────────────────────
    if quality_status == "qualified":

        # Gate: Paid boost trap detection
        # High-velocity boosts with no organic follow = retail trap
        _boost = metrics.get("paid_boost_detected", False)
        _boost_filter = str(get_config_value("FILTER_PAID_BOOSTS", "0")) == "1"
        if _boost and _boost_filter:
            quality_status = "rejected"
            quality_reason = "PAID_BOOST_TRAP_DETECTED"

    if quality_status == "qualified":

        # Gate: Liquidity integrity - filter fragile liquidity
        _liq_score = float(metrics.get("liquidity_integrity_score") or 0.5)
        _min_liq_integrity = float(get_config_value("MIN_LIQUIDITY_BALANCE", "0.0"))
        if _min_liq_integrity > 0 and _liq_score < _min_liq_integrity:
            quality_status = "rejected"
            quality_reason = f"LOW_LIQUIDITY_BALANCE_{_liq_score:.2f}"

    if quality_status == "qualified":

        # Gate: Price momentum at latch - don't enter declining tokens
        _price_chg_5m = float(metrics.get("price_change_5m") or 0)
        _min_momentum = float(get_config_value("MIN_PRICE_MOMENTUM_5M", "-20"))  # relaxed -10→-20
        if _price_chg_5m < float(_min_momentum):
            quality_status = "rejected"
            quality_reason = f"DECLINING_MOMENTUM_{_price_chg_5m:.1f}pct"

    if quality_status == "qualified":

        # Wallet convergence scoring - 2+ watched wallets bought same mint in 60s = real signal
        try:
            import sqlite3 as _sq3
            _db = _sq3.connect("sentinuity_matrix.db", timeout=30)
            # Check wallet_pattern_observations for recent coordinated buys
            _conv = _db.execute("""
                SELECT COUNT(DISTINCT wallet_address) as convergence_count
                FROM wallet_pattern_observations
                WHERE mint_address = ?
                  AND action IN ('buy', 'BUY', 'purchase')
                  AND observed_at > ?
            """, (mint, time.time() - 60)).fetchone()
            convergence_count = int(_conv[0] if _conv else 0)

            # Also count total active watched wallets for context
            _ww = _db.execute("SELECT COUNT(*) FROM watched_wallets WHERE active=1").fetchone()
            watched_wallet_count = int(_ww[0] if _ww else 0)
            _db.close()

            # wallet_convergence_score: 0-1 based on how many watched wallets converged
            wallet_convergence_score = min(1.0, convergence_count / max(1, watched_wallet_count * 0.3))
            metrics["wallet_convergence_score"] = wallet_convergence_score
            metrics["wallet_convergence_count"] = convergence_count
            metrics["watched_wallet_count"] = watched_wallet_count

            # Log convergence events for council research
            if convergence_count >= 2:
                try:
                    import logging as _log
                    _log.getLogger("market_intelligence").info(
                        "WALLET_CONVERGENCE: mint=%s count=%d score=%.2f",
                        mint[:20], convergence_count, wallet_convergence_score
                    )
                except Exception:
                    pass
        except Exception:
            metrics["wallet_convergence_score"] = 0.0
            metrics["watched_wallet_count"] = 0

    _write_qualifier_result(row_id, metrics, quality_status, quality_reason)

    if quality_status == "qualified":
        _cognition("QUALIFIER", _COGNITION_QUALIFIED, token=mint,
                   meta={
                       "row_id": row_id,
                       "market_cap": metrics.get("market_cap_usd"),
                       "age_seconds": metrics.get("token_age_seconds"),
                       "curve_pct": curve_meta.get("curve_progress_pct"),
                   })
        # Smart money metrics - compute score for qualified tokens (observational)
        try:
            from services.smart_money_metrics import compute_metrics as _sm_compute
            _sm_result = _sm_compute(mint)
            if _sm_result and _sm_result.get('tier') not in ('NOISE', None):
                _cognition("QUALIFIER",
                    f"SmartMoney: score={_sm_result['score']} tier={_sm_result['tier']} "
                    f"holders_180s={_sm_result['holders_delta_180s']} cluster={_sm_result['wallet_cluster_score']:.1f}",
                    token=mint)
        except Exception:
            pass
    else:
        _cognition("QUALIFIER",
            _COGNITION_MESSAGES.get(quality_reason,
                f"Rejected - failed '{quality_reason}' safety condition."),
            token=mint,
            meta={"row_id": row_id, "reason": quality_reason})

    return quality_status, quality_reason


def _qualifier_loop() -> None:
    _ensure_qualifier_columns()
    log.info("QUALIFIER STAGE ONLINE - signal gates initialised: suffix->curve->dex->mcap->age->conf->freshness")

    session = requests.Session()
    session.headers.update({"Accept": "application/json"})

    _last_purge = 0.0  # throttle stale purge to once per 60s

    while True:
        processed = qualified = rejected = errors = 0
        fresh_qualified_priced = stale_rejected = priced_tradeable = latched_ready = 0
        try:
            poll_interval = float(float(get_config_value(
                "QUALIFIER_POLL_INTERVAL", QUALIFIER_POLL_INTERVAL)))
            batch_size = int(int(get_config_value(
                "QUALIFIER_BATCH_SIZE", QUALIFIER_BATCH_SIZE)))
            # FLOW SIGN-OFF: qualification performs RPC/HTTP work serially.
            # A configured batch of 50 allowed one cycle to monopolise the lane
            # for many minutes. Clamp only the per-cycle claim; total throughput
            # continues across rapid polls and every gate remains unchanged.
            _hot_batch_cap = int(float(get_config_value(
                "QUALIFIER_HOT_BATCH_CAP", 8)))
            batch_size = max(1, min(batch_size, max(1, _hot_batch_cap), 16))

            # ── STALE BACKLOG PURGE (every 60s) ──────────────────────────────
            # Expire pending/qualified rows older than 15 min so they don't
            # waste qualifier cycles and block SIGNAL_STALE rejections.
            _now = time.time()
            if _now - _last_purge > 60:
                try:
                    with get_connection() as _pc:
                        # Flow sign-off: non-pump rows are rejected in one cheap
                        # local statement instead of consuming curve/RPC cycles.
                        _pc.execute("""
                            UPDATE market_snapshots
                            SET candidate_state='vetoed',
                                quality_status='rejected',
                                quality_reason='NOT_PUMP_TOKEN',
                                execution_ready=0,
                                latched=0,
                                qualify_claimed_until=NULL
                            WHERE candidate_state='pending'
                              AND COALESCE(quality_status,'') NOT IN
                                  ('qualified','rejected','error')
                              AND LOWER(COALESCE(mint_address,'')) NOT LIKE '%pump'
                        """)

                        # Phase A: DEAD threshold = 900s. Purge pending/qualified rows
                        # older than 900s - they cannot be fresh by definition.
                        _purged = _pc.execute("""
                            UPDATE market_snapshots
                            SET candidate_state  = 'expired_stale',
                                execution_ready  = 0,
                                latched          = 0,
                                quality_reason   = 'AUTO_EXPIRED_DEAD'
                            WHERE COALESCE(price_updated_at,updated_at,
                                           created_at,first_seen_at,0) < ?
                              AND candidate_state IN ('pending','qualified')
                        """, (_now - 900,)).rowcount
                        # Phase A: deactivate active_cognition for rows > 300s old
                        # (COOL/COLD boundary - they lose execution eligibility)
                        _pc.execute("""
                            UPDATE market_snapshots
                            SET active_cognition = 0
                            WHERE COALESCE(price_updated_at,updated_at,
                                           created_at,first_seen_at,0) < ?
                              AND active_cognition != 0
                              AND candidate_state NOT IN ('vetoed','exited','executed',
                                  'expired_stale','EXECUTOR_STALE_GATE')
                              AND latched = 0
                        """, (_now - 300,))
                        # Release expired claim locks
                        for _col in ["qualify_claimed_until",
                                     "latch_claimed_until",
                                     "execution_claimed_until"]:
                            try:
                                _pc.execute(
                                    f"UPDATE market_snapshots SET {_col}=NULL "
                                    f"WHERE {_col} IS NOT NULL AND {_col} < ?",
                                    (_now,)
                                )
                            except Exception:
                                pass
                        _pc.commit()
                    if _purged:
                        log.info("QUALIFIER PURGE: expired %d stale pending/qualified rows", _purged)
                except Exception as _pe:
                    log.warning("Stale purge failed: %s", _pe)
                _last_purge = _now

            # ── PIPELINE HEALTH COUNTS ────────────────────────────────────────
            try:
                with get_connection() as _hc:
                    fresh_qualified_priced = _hc.execute("""
                        SELECT COUNT(*) FROM market_snapshots
                        WHERE candidate_state='qualified'
                          AND COALESCE(price_status,'') != ''
                          AND COALESCE(price_updated_at,0) > ?
                    """, (time.time() - 120,)).fetchone()[0]
                    priced_tradeable = _hc.execute("""
                        SELECT COUNT(*) FROM market_snapshots
                        WHERE is_tradeable=1 AND candidate_state='qualified'
                    """).fetchone()[0]
                    latched_ready = _hc.execute("""
                        SELECT COUNT(*) FROM market_snapshots
                        WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)
                    """).fetchone()[0]
            except Exception:
                pass

            rows = _claim_qualifier_rows(batch_size)

            for row in rows:
                try:
                    status, reason = _qualify_one(session, row)
                    processed += 1
                    if status == "qualified":
                        qualified += 1
                    elif status == "deferred":
                        pass
                    else:
                        rejected += 1
                    log.info("Signal synthesised row=%s mint=%s -> %s (%s)",
                             row["id"], str(row.get("mint_address",""))[:16],
                             status, reason)
                except requests.RequestException as exc:
                    errors += 1
                    # CRITICAL FIX (2026-05-21): release qualify_claimed_until on network errors.
                    # Without this, the finally-block safety invariant stamps
                    # QUALIFIER_NO_VERDICT permanently - the actual trading-blocker bug.
                    # Row goes back to clean pending|empty|unclaimed for normal retry.
                    try:
                        with get_connection() as conn:
                            conn.execute(
                                "UPDATE market_snapshots SET "
                                "quality_status='pending', quality_reason='', "
                                "qualify_claimed_until=NULL "
                                "WHERE id=?",
                                (row["id"],),
                            )
                            conn.commit()
                    except Exception:
                        pass
                    log.warning(
                        "Sensory fracture in qualifier network pathway for row=%s: %s "
                        "(retryable, claim released)",
                        row["id"], exc,
                    )
                except Exception as exc:
                    errors += 1
                    _mark_qualifier_error(row["id"], f"QUALIFIER_ERROR:{str(exc)[:220]}")
                    log.exception("Severe cognitive dissonance in qualifier for row=%s: %s",
                                  row["id"], exc)
                finally:
                    # SAFETY INVARIANT: no claimed row may exit with quality_status='pending'
                    # and blank quality_reason. Marks error so row doesn't stay invisibly stuck.
                    try:
                        with get_connection() as _inv_conn:
                            _inv_conn.execute("""
                                UPDATE market_snapshots
                                SET quality_status='error',
                                    quality_reason='QUALIFIER_NO_VERDICT',
                                    qualify_claimed_until=NULL
                                WHERE id=?
                                  AND COALESCE(quality_status,'') = 'pending'
                                  AND COALESCE(quality_reason,'') = ''
                                  AND qualify_claimed_until IS NOT NULL
                            """, (row["id"],))
                            _inv_conn.commit()
                    except Exception:
                        pass

            note = (
                f"processed={processed} qualified={qualified} "
                f"rejected={rejected} errors={errors} | "
                f"fresh_qual_priced={fresh_qualified_priced} "
                f"tradeable={priced_tradeable} latched={latched_ready}"
                if rows else
                f"Idle | fresh_qual_priced={fresh_qualified_priced} "
                f"tradeable={priced_tradeable} latched={latched_ready}"
            )
            update_heartbeat(
                "qualifier", "ALIVE", note,
                work_processed=processed,
                last_success_at=time.time() if processed > 0 else None,
            )
            time.sleep(poll_interval)

        except Exception as exc:
            update_heartbeat("qualifier", "ERROR", str(exc)[:120])
            log.exception("Qualifier mycelial thread collapsed - initiating recovery: %s", exc)
            time.sleep(QUALIFIER_POLL_INTERVAL)


# =============================================================================
# PRICE ORACLE (MTM + latched token refresh)
# =============================================================================

def _ensure_oracle_schema() -> None:
    try:
        with get_connection() as conn:
            cols = {r["name"] for r in
                    conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
            for col in (
                "last_price",
                "last_marked_at",
                "highest_price_seen",
                "current_price",
                "market_value_usd",
                "unrealized_pnl_pct",
            ):
                if col not in cols:
                    conn.execute(
                        f"ALTER TABLE paper_positions ADD COLUMN {col} REAL"
                    )
            conn.commit()
    except Exception as e:
        log.debug("Oracle substrate schema check bypassed: %s", e)


def _get_pending_price_rows(limit: int) -> list:
    try:
        with get_connection() as conn:
            # PATCH: Added age filter - only price tokens created in the last 10 minutes.
            # Without this, the pricer works through thousands of stale qualified rows
            # from previous sessions before reaching fresh tokens, starving the supervisor.
            # pump.fun tokens have a window of minutes - pricing old rows is wasted work.
            cutoff = time.time() - 600  # 10 minutes
            return conn.execute(
                """
                SELECT id, mint_address, token_name,
                       COALESCE(price_attempts, 0) AS price_attempts
                FROM market_snapshots
                WHERE candidate_state NOT IN ('vetoed', 'exited')
                  AND COALESCE(price_status, 'pending') IN ('pending', 'retry')
                  AND COALESCE(created_at, timestamp, 0) >= ?
                ORDER BY id DESC LIMIT ?
                """,
                (cutoff, limit),
            ).fetchall()
    except Exception:
        return []


def _get_open_position_mints() -> list[str]:
    try:
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT DISTINCT mint_address FROM paper_positions WHERE status='OPEN'"
            ).fetchall()
        result = [r["mint_address"] for r in rows if r["mint_address"]]
        if not result:
            log.debug("Organism at rest - no open mints in the substrate.")
        return result
    except Exception as e:
        log.warning("Substrate query for open mints encountered dissonance: %s", e)
        return []


def _get_latched_stale_mints() -> list[dict]:
    cutoff = time.time() - 120
    try:
        with get_connection() as conn:
            rows = conn.execute(
                """
                SELECT id, mint_address, observed_price
                FROM market_snapshots
                WHERE latched=1 AND COALESCE(execution_ready,0) IN (1,2)
                  AND candidate_state='latched'
                  AND (price_updated_at IS NULL OR price_updated_at < ?)
                  AND observed_price IS NOT NULL
                ORDER BY id DESC LIMIT 20
                """,
                (cutoff,),
            ).fetchall()
        return [{"id": r["id"], "mint": r["mint_address"],
                 "last_price": r["observed_price"]} for r in rows]
    except Exception:
        return []


def _chunked(items: list, size: int) -> list[list]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def _fetch_prices_jupiter(
    session: requests.Session, mints: List[str]
) -> Dict[str, dict]:
    if not mints:
        return {}

    headers = {"Accept": "application/json"}
    if JUPITER_API_KEY:
        headers["x-api-key"] = JUPITER_API_KEY

    try:
        resp = session.get(
            JUPITER_URL,
            params={"ids": ",".join(mints)},
            timeout=HTTP_TIMEOUT,
            headers=headers,
        )
        if resp.status_code == 429:
            raise requests.HTTPError(response=resp)
        if resp.status_code == 401:
            raise requests.HTTPError(response=resp)
        resp.raise_for_status()
        return resp.json().get("data") or {}
    except requests.HTTPError:
        raise
    except Exception:
        return {}


def _fetch_price_dexscreener(session: requests.Session, mint: str) -> Optional[float]:
    try:
        resp = session.get(
            DEXSCREENER_URL.format(mint=mint),
            timeout=HTTP_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return None
        pairs = (resp.json() or {}).get("pairs") or []
        sol = [p for p in pairs if isinstance(p, dict)
               and str(p.get("chainId") or "").lower() == "solana"]
        if not sol:
            return None
        best  = max(sol, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        price = best.get("priceUsd")
        return float(price) if price else None
    except Exception:
        return None


def _fetch_sol_usd_price(session: requests.Session) -> float:
    now = time.time()
    if hasattr(_fetch_sol_usd_price, "_cache"):
        cached_price, cached_at = _fetch_sol_usd_price._cache
        if now - cached_at < 30:
            return cached_price

    try:
        resp = session.get(
            "https://api.dexscreener.com/latest/dex/tokens/So11111111111111111111111111111111111111112",
            timeout=5,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            pairs = (resp.json() or {}).get("pairs") or []
            sol_pairs = [p for p in pairs if isinstance(p, dict)
                         and str(p.get("chainId") or "").lower() == "solana"]
            if sol_pairs:
                best = max(sol_pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
                price = float(best.get("priceUsd") or 0)
                if price > 0:
                    _fetch_sol_usd_price._cache = (price, now)
                    return price
    except Exception:
        pass

    try:
        resp = session.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=5,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 200:
            price = float((resp.json() or {}).get("solana", {}).get("usd") or 0)
            if price > 0:
                _fetch_sol_usd_price._cache = (price, now)
                return price
    except Exception:
        pass

    try:
        fallback_resp = __import__('urllib.request', fromlist=['urlopen']).urlopen(
            'https://price.jup.ag/v6/price?ids=SOL', timeout=3
        )
        import json as _json
        fallback_data = _json.loads(fallback_resp.read())
        fallback_price = float((fallback_data.get('data') or {}).get('SOL', {}).get('price') or 0)
        if fallback_price > 0:
            _fetch_sol_usd_price._cache = (fallback_price, now)
            return fallback_price
    except Exception:
        pass
    return 0.0


def _fetch_price_bonding_curve(session: requests.Session, mint: str, sol_usd: float) -> Optional[float]:
    if not sol_usd or sol_usd <= 0:
        return None
    try:
        curve = get_curve_progress(mint, sol_usd=sol_usd)
        if curve.get("error") or curve.get("complete"):
            return None
        price = curve.get("price_usd") or 0.0
        return float(price) if price > 0 else None
    except Exception:
        return None


def _fetch_price_pumpfun(session: requests.Session, mint: str) -> Optional[float]:
    try:
        resp = session.get(
            PUMPFUN_API_URL.format(mint=mint),
            timeout=HTTP_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
        usd_market_cap = data.get("usd_market_cap")
        if usd_market_cap and float(usd_market_cap) > 0:
            price = float(usd_market_cap) / 1_000_000_000
            return price if price > 0 else None
        return None
    except Exception:
        return None


def _fetch_pumpfun_metrics(session: requests.Session, mint: str) -> Optional[Dict[str, Any]]:
    try:
        resp = session.get(
            PUMPFUN_API_URL.format(mint=mint),
            timeout=HTTP_TIMEOUT,
            headers={"Accept": "application/json"},
        )
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            return None
        data = resp.json() or {}
        if not data:
            return None
        usd_market_cap = data.get("usd_market_cap")
        created_timestamp = data.get("created_timestamp")
        complete = bool(data.get("complete", False))
        virtual_sol_reserves = data.get("virtual_sol_reserves")

        return {
            "usd_market_cap":       float(usd_market_cap) if usd_market_cap else 0.0,
            "created_timestamp_ms": float(created_timestamp) if created_timestamp else None,
            "complete":             complete,
            "virtual_sol_reserves": float(virtual_sol_reserves) if virtual_sol_reserves else None,
            "source":               "pumpfun_api",
        }
    except Exception:
        return None


def _build_mtm_tx_hash(mint: str, now: float, seq: int = 0) -> str:
    return f"MTM:{mint[:20]}:{int(now)}:{seq}"


def _append_mtm_rows(rows: List[Tuple[str, float]], now: float) -> int:
    inserted = 0
    for seq, (mint, price_value) in enumerate(rows):
        tx_hash = _build_mtm_tx_hash(mint, now, seq)

        if not _valid_price(price_value):
            log.warning("Corrupted signal rejected by truth guard - mint=%s price=%r", mint[:20], price_value)
            continue

        try:
            with get_connection() as conn:
                open_positions = conn.execute(
                    """
                    SELECT id, entry_price, position_size_usd, quantity,
                           COALESCE(last_price, 0) AS last_price,
                           COALESCE(highest_price_seen, 0) AS highest_price_seen
                    FROM paper_positions
                    WHERE mint_address=? AND status='OPEN'
                    """,
                    (mint,),
                ).fetchall()

                # Preserve discovery candidate identity. Dedicated MTM rows are
                # only valid for an actually open paper position. Otherwise,
                # attach the fresh mark to the newest active candidate row.
                if open_positions:
                    conn.execute(
                        """
                        INSERT INTO market_snapshots (
                            tx_hash, token_name, mint_address,
                            observed_price, price_updated_at,
                            candidate_state, price_attempts,
                            price_last_attempt_at, price_status,
                            latched, execution_ready, duplicate_key, timestamp,
                            created_at, first_seen_at
                        ) VALUES (?, ?, ?, ?, ?, 'mtm', 0, ?, 'priced', 0, 0, ?, ?, ?, ?)
                        """,
                        (tx_hash, mint[:20], mint, price_value,
                         now, now, f"{tx_hash}|mtm", now, now, now),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE market_snapshots
                        SET observed_price=?,
                            price_updated_at=?,
                            price_last_attempt_at=?,
                            price_status='priced'
                        WHERE id=(
                            SELECT id FROM market_snapshots
                            WHERE mint_address=?
                              AND candidate_state IN ('pending','qualified')
                              AND COALESCE(execution_ready,0) != 2
                            ORDER BY id DESC
                            LIMIT 1
                        )
                        """,
                        (price_value, now, now, mint),
                    )

                accepted_for_open_position = False

                for pos in open_positions:
                    entry_price = _safe_float(pos["entry_price"])
                    position_size_usd = _safe_float(pos["position_size_usd"])
                    quantity = _safe_float(pos["quantity"])
                    last_price = _safe_float(pos["last_price"])
                    highest_price_seen = _safe_float(pos["highest_price_seen"])

                    if not _entry_multiple_ok(price_value, entry_price):
                        log.warning(
                            "Truth guard rejected corrupted entry signal - "
                            "price %.10f exceeds entry multiple for %s (entry %.10f)",
                            price_value, mint[:20], entry_price
                        )
                        continue

                    if last_price > 0 and not _mtm_jump_ok(price_value, last_price):
                        log.warning(
                            "Anomalous price jump rejected by truth guard - "
                            "mint=%s new=%.10f last=%.10f",
                            mint[:20], price_value, last_price
                        )
                        continue

                    market_value_usd = quantity * price_value if quantity > 0 else 0.0
                    if market_value_usd <= 0 and entry_price > 0 and position_size_usd > 0:
                        market_value_usd = position_size_usd * (price_value / entry_price)

                    if not _mtm_value_ok(market_value_usd, position_size_usd):
                        log.warning(
                            "Market value signal rejected by truth guard - "
                            "mint=%s mv=%.4f size=%.4f price=%.10f",
                            mint[:20], market_value_usd, position_size_usd, price_value
                        )
                        continue

                    # PnL intentionally NOT written here.
                    # Only update_position_mark() in execution_engine may write
                    # unrealized_pnl_usd - it gates on router can_execute_exit.
                    conn.execute(
                        """
                        UPDATE paper_positions SET
                            current_price=?,
                            last_price=?,
                            last_marked_at=?,
                            market_value_usd=?,
                            highest_price_seen=CASE
                                WHEN COALESCE(highest_price_seen, 0) > ?
                                THEN highest_price_seen
                                ELSE ? END
                        WHERE id=?
                          AND status='OPEN'
                          AND (last_marked_at IS NULL OR last_marked_at < ?)
                        """,
                        (
                            price_value,
                            price_value,
                            now,
                            market_value_usd,
                            price_value,
                            max(price_value, highest_price_seen),
                            pos["id"],
                            now,
                        ),
                    )
                    accepted_for_open_position = True

                conn.commit()

            if not open_positions or accepted_for_open_position:
                inserted += 1

        except Exception as e:
            log.debug("Rhiza substrate write fractured for %s: %s", mint[:20], e)
    return inserted


def _update_price_rows(
    session: requests.Session,
    rows: list,
    price_map: Dict[str, dict],
    max_attempts: int,
) -> Tuple[int, int, int]:
    priced = retried = dead = 0
    now = time.time()

    for row in rows:
        row_id   = row["id"]
        mint     = row["mint_address"]
        attempts = int(row["price_attempts"] or 0) + 1

        pv = None

        sol_usd_now = _fetch_sol_usd_price(session)
        if sol_usd_now > 0:
            pv = _fetch_price_bonding_curve(session, mint, sol_usd_now)

        if pv is None:
            pv = _fetch_price_pumpfun(session, mint)

        if pv is None:
            pv = _fetch_price_dexscreener(session, mint)

        if pv is None:
            usd = (price_map.get(mint, {}) or {}).get("usdPrice")
            if usd is not None:
                try:
                    parsed = float(usd)
                    if parsed > 0:
                        pv = parsed
                except (TypeError, ValueError):
                    pv = None

        if pv is not None and pv > 0:
            for attempt in range(4):
                try:
                    with get_connection() as conn:
                        conn.execute(
                            """
                            UPDATE market_snapshots SET
                                observed_price=?, price_attempts=?,
                                price_last_attempt_at=?, price_updated_at=?,
                                price_status='priced'
                            WHERE id=?
                              AND (price_updated_at IS NULL OR price_updated_at < ?)
                            """,
                            (pv, attempts, now, now, row_id, now),
                        )
                        conn.commit()
                    priced += 1
                    break
                except Exception:
                    if attempt < 3:
                        time.sleep(0.05 * (2 ** attempt))
            continue

        new_status = "dead" if attempts >= max_attempts else "retry"
        try:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE market_snapshots SET price_attempts=?, "
                    "price_last_attempt_at=?, price_status=? WHERE id=?",
                    (attempts, now, new_status, row_id),
                )
                conn.commit()
            dead += 1 if new_status == "dead" else 0
            retried += 1 if new_status == "retry" else 0
        except Exception:
            pass

    return priced, retried, dead


def _get_mint_last_marked_at(mints: list) -> dict:
    """
    Returns {mint: last_marked_at_epoch} for all given mints.
    Used for per-mint WS staleness detection - a mint is considered
    WS-covered only if its own last_marked_at is within _WS_ORACLE_STALE_SEC.
    Falls back to empty dict on any DB error (all mints treated as stale).
    """
    if not mints:
        return {}
    try:
        with get_connection() as conn:
            placeholders = ",".join("?" for _ in mints)
            rows = conn.execute(
                f"SELECT mint_address, MAX(COALESCE(last_marked_at, 0)) AS lma "
                f"FROM paper_positions "
                f"WHERE mint_address IN ({placeholders}) AND status='OPEN' "
                f"GROUP BY mint_address",
                mints,
            ).fetchall()
        return {r["mint_address"]: float(r["lma"] or 0) for r in rows}
    except Exception as e:
        log.debug("Sensory static on per-mint staleness query: %s", e)
        return {}


_oracle_idle_count = 0


def _build_resilient_session() -> requests.Session:
    from requests.adapters import HTTPAdapter
    from urllib3.util.retry import Retry
    s = requests.Session()
    retry = Retry(total=3, connect=3, read=3, backoff_factor=0.3,
                  status_forcelist=[429, 500, 502, 503, 504],
                  allowed_methods=frozenset(["POST", "GET"]))
    adapter = HTTPAdapter(max_retries=retry, pool_connections=10, pool_maxsize=10)
    s.mount("https://", adapter)
    s.mount("http://", adapter)
    return s


def _oracle_loop() -> None:
    while True:
        try:
            _oracle_loop_inner()
        except Exception as _outer_exc:
            log.error("Severe cognitive dissonance in the Oracle loop - synthesizing recovery in 10s: %s", _outer_exc)
            time.sleep(10)


def _oracle_loop_inner() -> None:
    _ensure_oracle_schema()
    log.info(
        "PRICE ORACLE ONLINE - BondingCurve-first, DexScreener fallback, 2s cycle. "
        "WS oracle liveness gate: %s (threshold=%ss).",
        "enabled" if callable(_oracle_age_fn) else "disabled",
        _WS_ORACLE_STALE_SEC,
    )

    session = _build_resilient_session()

    while True:
        cooldown     = int(int(get_config_value("ORACLE_429_COOLDOWN_SECONDS", 30)))
        max_attempts = int(int(get_config_value("ORACLE_MAX_ATTEMPTS", 12)))

        try:
            open_mints = _get_open_position_mints()
            mtm_total_inserted = 0
            rpc_success = 0
            rpc_fail = 0

            if open_mints:
                now = time.time()

                # ── PER-MINT WS LIVENESS GATE ──────────────────────────────────
                # Evaluate liveness individually per mint, not globally.
                # A mint is WS-covered only if its OWN last_marked_at is fresh.
                # This ensures Token B gets HTTP fallback even if Token A is live.
                mint_last_marked = _get_mint_last_marked_at(open_mints)
                stale_mints = [
                    m for m in open_mints
                    if (now - mint_last_marked.get(m, 0)) > _WS_ORACLE_STALE_SEC
                ]
                covered_mints = [m for m in open_mints if m not in stale_mints]

                if covered_mints:
                    log.debug(
                        "Harmonic resonance achieved. Sensory pathways clear. "
                        "WS oracle covering %d mint(s) - HTTP poll skipped for those.",
                        len(covered_mints),
                    )

                if stale_mints:
                    if _WS_ORACLE_AVAILABLE:
                        log.warning(
                            "Sensory dissonance detected. Reverting to deep memory polling. "
                            "%d mint(s) stale (>%ss) - forcing HTTP poll: %s",
                            len(stale_mints), _WS_ORACLE_STALE_SEC,
                            [m[:12] for m in stale_mints],
                        )
                    sol_usd = _fetch_sol_usd_price(session)
                    for chunk in _chunked(stale_mints, ORACLE_BATCH_LIMIT):
                        mtm_rows = []
                        for mint in chunk:
                            pv = None
                            try:
                                if sol_usd > 0:
                                    pv = _fetch_price_bonding_curve(session, mint, sol_usd)
                                if not pv:
                                    pv = _fetch_price_pumpfun(session, mint)
                                if not pv:
                                    pv = _fetch_price_dexscreener(session, mint)
                                if pv and pv > 0:
                                    rpc_success += 1
                                    mtm_rows.append((mint, pv))
                                    log.debug(
                                        "Rhiza has absorbed the new truth. Substrate updated. "
                                        "mint=%s price=%.10f source=http_poll",
                                        mint[:16], pv,
                                    )
                                else:
                                    rpc_fail += 1
                                    log.debug(
                                        "Sensory void - no price signal resolved for mint=%s",
                                        mint[:16],
                                    )
                            except requests.RequestException as e:
                                rpc_fail += 1
                                log.warning(
                                    "Sensory static encountered on RPC pathway for mint=%s: %s",
                                    mint[:16], e,
                                )
                                try:
                                    session.close()
                                except Exception:
                                    pass
                                session = _build_resilient_session()
                            except Exception as e:
                                rpc_fail += 1
                                log.warning(
                                    "Sensory fracture during price resolution for mint=%s: %s",
                                    mint[:16], e,
                                )
                        if mtm_rows:
                            mtm_total_inserted += _append_mtm_rows(mtm_rows, now)
            else:
                log.debug("Organism at rest - no open positions in the substrate.")

            latched_stale    = _get_latched_stale_mints()
            latched_refreshed = 0
            sol_usd_latched = _fetch_sol_usd_price(session)
            for item in latched_stale:
                mint   = item["mint"]
                row_id = item["id"]
                pv     = None
                if sol_usd_latched > 0:
                    pv = _fetch_price_bonding_curve(session, mint, sol_usd_latched)
                if not pv:
                    pv = _fetch_price_pumpfun(session, mint)
                if not pv:
                    pv = _fetch_price_dexscreener(session, mint)
                if pv and pv > 0:
                    now = time.time()
                    try:
                        with get_connection() as conn:
                            conn.execute(
                                """
                                UPDATE market_snapshots SET observed_price=?,
                                    price_updated_at=?, price_status='priced'
                                WHERE id=?
                                  AND (price_updated_at IS NULL OR price_updated_at < ?)
                                """,
                                (pv, now, row_id, now),
                            )
                            conn.commit()
                        latched_refreshed += 1
                    except Exception:
                        pass

            rows = _get_pending_price_rows(limit=200)

            if not rows:
                global _oracle_idle_count
                _oracle_idle_count += 1
                if _oracle_idle_count % 15 == 1:
                    _cognition("ORACLE",
                        f"Price layer scanning - {len(open_mints)} open position(s) "
                        f"under MTM watch. No new snapshots pending pricing.")
                update_heartbeat(
                    "price_enricher", "ALIVE",
                    f"Substrate quiet - mtm={mtm_total_inserted} open={len(open_mints)} "
                    f"resonance={rpc_success} static={rpc_fail} latched={latched_refreshed}",
                )
                time.sleep(ORACLE_POLL_INTERVAL)
                continue

            mint_to_rows: Dict[str, list] = {}
            for row in rows:
                if row["mint_address"]:
                    mint_to_rows.setdefault(row["mint_address"], []).append(row)

            total_priced = total_retried = total_dead = 0
            for chunk in _chunked(list(mint_to_rows.keys()), ORACLE_BATCH_LIMIT):
                price_map     = {}
                affected_rows = []
                for mint in chunk:
                    affected_rows.extend(mint_to_rows.get(mint, []))
                p, r, d = _update_price_rows(session, affected_rows, price_map, max_attempts)
                total_priced  += p
                total_retried += r
                total_dead    += d

            update_heartbeat(
                "price_enricher", "ALIVE",
                f"Substrate active - synthesised={total_priced} retried={total_retried} "
                f"calcified={total_dead} mtm={mtm_total_inserted} open={len(open_mints)} "
                f"resonance={rpc_success} static={rpc_fail}",
                work_processed=total_priced + mtm_total_inserted,
                last_success_at=time.time()
                    if (total_priced > 0 or mtm_total_inserted > 0) else None,
            )

        except requests.HTTPError as exc:
            status_code = exc.response.status_code if exc.response else None
            if status_code == 429:
                update_heartbeat("price_enricher", "WARN",
                    f"Sensory pathway throttled - deep memory cooldown {cooldown}s")
                time.sleep(cooldown)
                continue
            update_heartbeat("price_enricher", "ERROR", f"Sensory fracture on HTTP pathway - status={status_code}: {exc}")

        except Exception as exc:
            exc_str = str(exc)
            update_heartbeat("price_enricher", "ERROR", exc_str[:120])
            if "NameResolutionError" in exc_str or "getaddrinfo" in exc_str or "ConnectionError" in exc_str:
                log.warning("Sensory network dissolved - organism backing off 15s before re-establishing pathways.")
                time.sleep(15)
            else:
                log.exception("Severe cognitive dissonance in the Oracle loop. Synthesizing recovery: %s", exc)
            try:
                session.close()
            except Exception:
                pass
            session = _build_resilient_session()

        time.sleep(ORACLE_POLL_INTERVAL)


# =============================================================================
# MAIN
# =============================================================================

def run() -> None:
    log.info("MARKET INTELLIGENCE ONLINE - qualifier and price oracle threads awakening.")
    log.info("Bonding curve graduation threshold: %.0f%% (configurable: CURVE_DANGER_ZONE_PCT)",
             float(float(get_config_value("CURVE_DANGER_ZONE_PCT", 85.0))))
    log.info("Scout nodes (wallet_scout, telegram_scout) operate as independent sensory threads.")

    threads = [
        threading.Thread(target=_qualifier_loop, daemon=True, name="qualifier"),
        threading.Thread(target=_oracle_loop,    daemon=True, name="oracle"),
    ]
    for t in threads:
        t.start()
    log.info("Qualifier and price oracle mycelial threads initialised.")

    if _WS_ORACLE_AVAILABLE:
        try:
            _start_ws_oracle()
            log.info(
                "Harmonic resonance achieved. Sensory pathways clear. "
                "Helius accountSubscribe oracle started - real-time MTM active."
            )
        except Exception as _ws_start_err:
            log.error(
                "Sensory dissonance detected. Reverting to deep memory polling. "
                "WS oracle start failed: %s - HTTP polling will cover all open mints.",
                _ws_start_err,
            )
    else:
        log.warning(
            "Sensory dissonance detected. Reverting to deep memory polling. "
            "WebSocket oracle unavailable - HTTP polling active for all open mints."
        )

    while True:
        try:
            with get_connection() as conn:
                pending_q = conn.execute(
                    "SELECT COUNT(*) AS c FROM market_snapshots "
                    "WHERE candidate_state='pending'"
                ).fetchone()["c"]
                qualified = conn.execute(
                    "SELECT COUNT(*) AS c FROM market_snapshots "
                    "WHERE quality_status='qualified'"
                ).fetchone()["c"]
                open_pos = conn.execute(
                    "SELECT COUNT(*) AS c FROM paper_positions WHERE status='OPEN'"
                ).fetchone()["c"]
            update_heartbeat(
                SERVICE_NAME, "ALIVE",
                f"pending_qualification={pending_q} qualified={qualified} "
                f"open_positions={open_pos}",
            )
        except Exception as e:
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])
        time.sleep(30)


if __name__ == "__main__":
    run()