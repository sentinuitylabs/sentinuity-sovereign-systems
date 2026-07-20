"""
live_trading.py
───────────────────────────────────────────────────────────────────────────────
SENTINUITY LIVE TRADING EXECUTION LAYER

Wraps the paper trading engine with real on-chain execution via Jupiter swap API.
Controlled by TRADING_MODE in system_config:
  - 'paper'  → existing behaviour, no change (default)
  - 'live'   → real SOL transactions signed with SOLANA_PRIVATE_KEY

SAFETY DESIGN:
  - Paper positions are ALWAYS written regardless of mode (audit trail + meter)
  - Live mode additionally submits a real Jupiter swap transaction
  - If the swap fails, position is marked as PAPER_ONLY with a note
  - Position size is capped by the operator amount stamped during launcher startup
  - All live trades are flagged in paper_positions.entry_price_source = 'live_tx'
  - Never touches paper trading wallet_balance — live mode tracks SOL balance separately

LIFECYCLE HARDENING (SIGNOFF_LIVE_LEDGER_20260716 — NTO regression fixes):
  - Every submitted transaction signature is persisted to live_tx_ledger BEFORE
    confirmation is awaited. A local confirmation timeout or a process crash can
    no longer orphan an on-chain fill: services/live_settlement_recovery.py
    re-resolves BUY_SUBMITTED / SELL_SUBMITTED / *_CONFIRMED_UNRESOLVED states
    idempotently from chain truth on restart and on a rolling cycle.
  - live_tx_ledger.tx_sig is UNIQUE: duplicate reconciliation callbacks cannot
    duplicate fills.
  - Slippage chasing is REMOVED. Sells use one primary quote plus at most one
    bounded fallback (LIVE_SELL_SLIPPAGE_BPS / LIVE_SELL_FALLBACK_SLIPPAGE_BPS).
    There is no 10%→30%→50%→100% ladder and Jupiter autoSlippage is disabled.
  - Quote freshness is enforced (LIVE_MAX_QUOTE_AGE_SEC) and the total
    decision-to-submit budget is enforced (LIVE_ENTRY_DECISION_TO_SUBMIT_MAX_SEC,
    default 5s). The system prefers missing a trade over forcing a top entry.
  - Token decimals are always resolved from chain (no hard-coded 10**6).
  - Latency telemetry is persisted per signature into live_latency_telemetry.
  - blockTime remains the canonical ownership boundary; local observed /
    submitted / acknowledged / reconciled timestamps are recorded separately.

DEPLOY:
  1. Add to .env:
       SOLANA_PRIVATE_KEY=your_base58_private_key
       Optional: LIVE_MAX_POSITION_USD=<secondary environment ceiling>
  2. Set in DB:
       UPDATE system_config SET value='live' WHERE key='TRADING_MODE';
  3. Import in execution_engine.py:
       from services.live_trading import execute_live_buy, execute_live_sell, get_live_wallet_balance
  4. Call after paper INSERT in scan_for_entries:
       if _TRADING_MODE == 'live':
           live_result = execute_live_buy(mint, pos_size_usd, entry_price, position_id)
  5. Call inside close_position_canonical before conn.commit():
       if _TRADING_MODE == 'live':
           live_result = execute_live_sell(mint, quantity, position_id)
"""

from __future__ import annotations

import json
import logging
import os
import time
from decimal import Decimal, InvalidOperation
from typing import Any, Optional

log = logging.getLogger("live_trading")

# ── CONFIG ────────────────────────────────────────────────────────────────────

# Load .env before reading keys
try:
    from dotenv import load_dotenv as _ld
    from pathlib import Path as _P
    _ld(_P(__file__).resolve().parent.parent / ".env", override=False)
except Exception:
    pass

_PRIVATE_KEY_B58  = os.getenv("SOLANA_PRIVATE_KEY", "").strip()
_LIVE_MAX_POS_USD_ENV = os.getenv("LIVE_MAX_POSITION_USD", "").strip()
_JUPITER_KEY      = os.getenv("JUPITER_PRICE_API_KEY", "").strip()
_RPC_URL          = os.getenv("QUICKNODE_RPC") or os.getenv("SOLANA_RPC_URL", "https://api.mainnet-beta.solana.com")

# SOL mint address (always the input token for buys)
_SOL_MINT  = "So11111111111111111111111111111111111111112"
_WSOL_MINT = "So11111111111111111111111111111111111111112"

# Canonical on-chain provenance. A base58 mint ending in ``pump`` is merely a
# vanity suffix and is NEVER evidence that Pump created the coin.
_PUMP_PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
_SPL_TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
_TOKEN_2022_PROGRAM_ID = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"

# Slippage for pump.fun tokens — needs to be aggressive
_BUY_SLIPPAGE_BPS  = 2000   # 20% — pump.fun tokens move fast
# ── SOL price cache — refreshes every 60s, not every trade ───────────────────
_sol_price_cache: dict = {"price": 0.0, "ts": 0.0}
_SOL_CACHE_TTL = 60.0


def _get_cached_sol_price() -> float:
    """Return cached SOL/USD price, refreshing if older than 60s."""
    import requests as _req
    now = time.time()
    if now - _sol_price_cache["ts"] < _SOL_CACHE_TTL and _sol_price_cache["price"] > 0:
        return _sol_price_cache["price"]
    try:
        r = _req.get(
            "https://api.jup.ag/price/v3",
            params={"ids": _SOL_MINT},
            headers={},
            timeout=4,
        )
        price = float((r.json().get(_SOL_MINT) or {}).get("usdPrice") or 0)
        if price > 0:
            _sol_price_cache["price"] = price
            _sol_price_cache["ts"]    = now
            log.info("[LIVE] SOL price refreshed: $%.2f", price)
        return price
    except Exception as e:
        log.warning("[LIVE] SOL price fetch failed: %s", e)
        return _sol_price_cache["price"]


# ── BOUNDED SELL SLIPPAGE (SIGNOFF_LIVE_LEDGER_20260716) ─────────────────────
# One primary tier plus at most ONE bounded fallback. No chasing ladder.
def _sell_slippage_tiers() -> list[int]:
    try:
        primary = int(float(os.getenv("LIVE_SELL_SLIPPAGE_BPS", "1500")))
    except (TypeError, ValueError):
        primary = 1500
    try:
        fallback = int(float(os.getenv("LIVE_SELL_FALLBACK_SLIPPAGE_BPS", "3000")))
    except (TypeError, ValueError):
        fallback = 3000
    ceiling = 3000  # hard ceiling — chasing beyond this is forbidden by directive
    primary = max(100, min(primary, ceiling))
    fallback = max(primary, min(fallback, ceiling))
    return [primary] if fallback == primary else [primary, fallback]


# Gas reserve — never use more than wallet_sol - GAS_RESERVE_SOL
_GAS_RESERVE_SOL = 0.05


# ── LIVE TX LEDGER + LATENCY TELEMETRY (SIGNOFF_LIVE_LEDGER_20260716) ────────
# Append-authoritative record of every submitted live transaction. Written at
# submit time so a crash or local timeout can never orphan an on-chain fill.

_LEDGER_SCHEMA = """
CREATE TABLE IF NOT EXISTS live_tx_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_sig          TEXT NOT NULL UNIQUE,
    side            TEXT NOT NULL,              -- BUY | SELL
    mint_address    TEXT NOT NULL,
    position_id     INTEGER,                    -- SIM parent (BUY) or REAL row (SELL)
    state           TEXT NOT NULL,              -- SUBMITTED | CONFIRMED_UNRESOLVED |
                                                -- RESOLVED | FAILED_ON_CHAIN |
                                                -- MANUAL_INTERVENTION
    quote_out_amount TEXT,
    slippage_bps    INTEGER,
    submitted_at    REAL,                       -- local wall clock at broadcast
    local_ack_at    REAL,                       -- RPC acknowledged signature
    confirmed_at    REAL,                       -- local wall clock at confirmation
    block_time      REAL,                       -- CANONICAL chain ownership boundary
    reconciled_at   REAL,                       -- local wall clock at fill resolution
    error           TEXT,
    latency_warning TEXT,
    quote_age_before_broadcast_sec REAL,
    compose_sign_duration_sec REAL,
    submit_to_confirm_sec REAL,
    submit_to_reconcile_sec REAL,
    fill_meta_json  TEXT,
    created_at      REAL NOT NULL,
    updated_at      REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ltl_state ON live_tx_ledger(state, side);
CREATE INDEX IF NOT EXISTS ltl_pos   ON live_tx_ledger(position_id);
CREATE TABLE IF NOT EXISTS live_latency_telemetry (
    id                          INTEGER PRIMARY KEY AUTOINCREMENT,
    tx_sig                      TEXT NOT NULL,
    side                        TEXT NOT NULL,
    position_id                 INTEGER,
    mint_address                TEXT,
    quote_request_sec           REAL,
    compose_sign_broadcast_sec  REAL,
    confirmation_sec            REAL,
    fill_reconciliation_sec     REAL,
    decision_to_submit_sec      REAL,
    quote_age_before_broadcast_sec REAL,
    compose_sign_duration_sec   REAL,
    submit_to_confirm_sec       REAL,
    submit_to_reconcile_sec     REAL,
    latency_warning             TEXT,
    total_sec                   REAL,
    created_at                  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS llt_sig ON live_latency_telemetry(tx_sig);
"""


def _ensure_live_ledger_schema(conn) -> None:
    """Idempotent schema guard, including additive sign-off telemetry columns."""
    for stmt in _LEDGER_SCHEMA.strip().split(";"):
        stmt = stmt.strip()
        if stmt:
            try:
                conn.execute(stmt)
            except Exception:
                pass
    additions = {
        "live_tx_ledger": {
            "latency_warning": "TEXT",
            "quote_age_before_broadcast_sec": "REAL",
            "compose_sign_duration_sec": "REAL",
            "submit_to_confirm_sec": "REAL",
            "submit_to_reconcile_sec": "REAL",
        },
        "live_latency_telemetry": {
            "quote_age_before_broadcast_sec": "REAL",
            "compose_sign_duration_sec": "REAL",
            "submit_to_confirm_sec": "REAL",
            "submit_to_reconcile_sec": "REAL",
            "latency_warning": "TEXT",
        },
    }
    for table, columns in additions.items():
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except Exception:
            existing = set()
        for name, ddl in columns.items():
            if name not in existing:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                except Exception:
                    pass


def _ledger_upsert(tx_sig: str, *, side: str, mint: str,
                   position_id: Optional[int] = None,
                   state: Optional[str] = None,
                   **fields) -> None:
    """
    Persist/advance a ledger row keyed by UNIQUE tx_sig. Idempotent:
    a duplicate callback for the same signature updates the same row and can
    never create a second fill record. Never raises — capital-safety writes
    must not take down the trading path, failures are logged CRITICAL.
    """
    if not tx_sig:
        return
    try:
        from core.schema import get_connection
        now = time.time()
        cols, vals = [], []
        for k in ("quote_out_amount", "slippage_bps", "submitted_at",
                  "local_ack_at", "confirmed_at", "block_time",
                  "reconciled_at", "error", "latency_warning",
                  "quote_age_before_broadcast_sec", "compose_sign_duration_sec",
                  "submit_to_confirm_sec", "submit_to_reconcile_sec",
                  "fill_meta_json"):
            if k in fields and fields[k] is not None:
                cols.append(k)
                vals.append(fields[k])
        with get_connection() as conn:
            _ensure_live_ledger_schema(conn)
            conn.execute(
                "INSERT INTO live_tx_ledger (tx_sig,side,mint_address,position_id,"
                "state,created_at,updated_at) VALUES (?,?,?,?,?,?,?) "
                "ON CONFLICT(tx_sig) DO NOTHING",
                (tx_sig, side, mint, position_id, state or "SUBMITTED", now, now),
            )
            sets = ["updated_at=?"]
            params: list = [now]
            if state:
                sets.append("state=?")
                params.append(state)
            for k, v in zip(cols, vals):
                sets.append(f"{k}=?")
                params.append(v)
            params.append(tx_sig)
            conn.execute(
                f"UPDATE live_tx_ledger SET {', '.join(sets)} WHERE tx_sig=?",
                params,
            )
            conn.commit()
    except Exception as exc:
        log.critical("[LIVE_LEDGER_WRITE_FAIL] sig=%s state=%s err=%s",
                     str(tx_sig)[:20], state, exc)


def _record_latency_telemetry(tx_sig: str, side: str, position_id: Optional[int],
                              mint: str, timings: dict) -> None:
    """Persist per-signature latency telemetry. Never raises."""
    if not tx_sig:
        return
    try:
        from core.schema import get_connection
        with get_connection() as conn:
            _ensure_live_ledger_schema(conn)
            conn.execute(
                "INSERT INTO live_latency_telemetry (tx_sig,side,position_id,"
                "mint_address,quote_request_sec,compose_sign_broadcast_sec,"
                "confirmation_sec,fill_reconciliation_sec,decision_to_submit_sec,"
                "quote_age_before_broadcast_sec,compose_sign_duration_sec,"
                "submit_to_confirm_sec,submit_to_reconcile_sec,latency_warning,"
                "total_sec,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (tx_sig, side, position_id, mint,
                 timings.get("quote_request_sec"),
                 timings.get("compose_sign_broadcast_sec"),
                 timings.get("confirmation_sec"),
                 timings.get("fill_reconciliation_sec"),
                 timings.get("decision_to_submit_sec"),
                 timings.get("quote_age_before_broadcast_sec"),
                 timings.get("compose_sign_duration_sec"),
                 timings.get("submit_to_confirm_sec"),
                 timings.get("submit_to_reconcile_sec"),
                 timings.get("latency_warning"),
                 timings.get("total_sec"), time.time()),
            )
            conn.commit()
    except Exception as exc:
        log.warning("[LIVE_LATENCY_WRITE_FAIL] sig=%s err=%s", str(tx_sig)[:20], exc)


# ── KEYPAIR LOADER ────────────────────────────────────────────────────────────

def _load_keypair():
    """
    Load Solana keypair from base58 private key string.
    Returns solders.keypair.Keypair or None if unavailable.
    """
    if not _PRIVATE_KEY_B58:
        log.error("[LIVE] SOLANA_PRIVATE_KEY not set in .env")
        return None
    try:
        from solders.keypair import Keypair
        import base58
        secret = base58.b58decode(_PRIVATE_KEY_B58)
        return Keypair.from_bytes(secret)
    except ImportError:
        log.error("[LIVE] Missing dependencies. Run: pip install solders base58 solana")
        return None
    except Exception as e:
        log.error("[LIVE] Failed to load keypair: %s", e)
        return None


# ── SOL BALANCE ───────────────────────────────────────────────────────────────

def get_live_wallet_balance() -> Optional[float]:
    """
    Fetch real SOL balance from chain for the trading wallet.
    Returns balance in USD (SOL * current price) or None on failure.
    """
    kp = _load_keypair()
    if not kp:
        return None
    try:
        import requests
        wallet_address = str(kp.pubkey())
        # Get SOL balance in lamports
        r = requests.post(_RPC_URL, json={
            "jsonrpc": "2.0", "id": 1,
            "method": "getBalance",
            "params": [wallet_address, {"commitment": "confirmed"}]
        }, timeout=5)
        sol_lamports = r.json()["result"]["value"]
        sol_balance  = sol_lamports / 1_000_000_000

        # Get SOL/USD price from Jupiter
        r2 = requests.get(
            "https://api.jup.ag/price/v3",
            params={"ids": _SOL_MINT},
            headers={},
            timeout=4
        )
        sol_usd = float((r2.json().get(_SOL_MINT) or {}).get("usdPrice") or 0)

        usable_sol = max(0.0, sol_balance - _GAS_RESERVE_SOL)
        balance_usd = usable_sol * sol_usd if sol_usd > 0 else 0.0
        log.info("[LIVE] Wallet %s: %.4f SOL (usable %.4f = ~$%.2f)",
                 wallet_address[:8], sol_balance, usable_sol, balance_usd)
        return balance_usd
    except Exception as e:
        log.error("[LIVE] get_live_wallet_balance failed: %s", e)
        return None


# ── JUPITER SWAP ──────────────────────────────────────────────────────────────

def _get_jupiter_quote(
    input_mint: str,
    output_mint: str,
    amount_lamports: int,
    slippage_bps: int,
) -> Optional[dict]:
    """Get a swap quote from Jupiter v6 API."""
    try:
        import requests
        r = requests.get(
            "https://api.jup.ag/swap/v1/quote",
            params={
                "inputMint":   input_mint,
                "outputMint":  output_mint,
                "amount":      amount_lamports,
                "slippageBps": slippage_bps,
            },
            headers={},
            timeout=5,
        )
        if r.status_code != 200:
            log.error("[LIVE] Jupiter quote failed: %s %s", r.status_code, r.text[:200])
            return None
        return r.json()
    except Exception as e:
        log.error("[LIVE] Jupiter quote error: %s", e)
        return None


def _execute_jupiter_swap(
    quote: dict,
    wallet_pubkey: str,
    keypair,
    *,
    quote_received_mono: float,
    max_quote_age_sec: float,
) -> dict:
    """Build, sign and submit a Jupiter swap under a hard quote deadline.

    The final freshness gate is inside this function immediately before RPC
    broadcast. No caller-side check can substitute for this because Jupiter's
    transaction-build request occurs after the caller check.
    """
    outcome = {"tx_sig": None, "error": None, "timings": {}}
    try:
        import requests
        from solders.transaction import VersionedTransaction
        import base64

        def quote_age() -> float:
            return max(0.0, time.perf_counter() - float(quote_received_mono))

        age_before_build = quote_age()
        if age_before_build > max_quote_age_sec:
            outcome["error"] = f"quote_stale_before_build:{age_before_build:.2f}>{max_quote_age_sec:.2f}"
            return outcome

        build_started = time.perf_counter()
        r = requests.post(
            "https://api.jup.ag/swap/v1/swap",
            json={
                "quoteResponse": quote, "userPublicKey": wallet_pubkey,
                "wrapAndUnwrapSol": True, "dynamicComputeUnitLimit": True,
                "prioritizationFeeLamports": int(float(os.getenv("LIVE_PRIORITY_FEE_LAMPORTS", "50000"))),
            }, headers={}, timeout=10,
        )
        outcome["timings"]["jupiter_swap_build_sec"] = round(time.perf_counter() - build_started, 6)
        if r.status_code != 200:
            outcome["error"] = f"jupiter_swap_build_failed:{r.status_code}"
            log.error("[LIVE] Jupiter swap tx failed: %s", r.text[:300])
            return outcome

        tx_bytes = base64.b64decode(r.json()["swapTransaction"] )

        # Re-check after Jupiter build and before creating any local signature.
        age_before_sign = quote_age()
        outcome["timings"]["quote_age_before_sign_sec"] = round(age_before_sign, 6)
        if age_before_sign > max_quote_age_sec:
            outcome["error"] = f"quote_stale_before_sign:{age_before_sign:.2f}>{max_quote_age_sec:.2f}"
            log.warning("[PRE_SIGN_QUOTE_EXPIRED] age=%.2fs limit=%.2fs", age_before_sign, max_quote_age_sec)
            return outcome

        sign_started = time.perf_counter()
        tx = VersionedTransaction.from_bytes(tx_bytes)
        signed_tx = VersionedTransaction(tx.message, [keypair])
        tx_bytes_signed = bytes(signed_tx)
        outcome["timings"]["compose_sign_duration_sec"] = round(time.perf_counter() - sign_started, 6)

        # CAPITAL-SAFETY AUTHORITY: this is the last instruction before the RPC
        # send. A stale quote exits without a signature or network submission.
        age_before_broadcast = quote_age()
        outcome["timings"]["quote_age_before_broadcast_sec"] = round(age_before_broadcast, 6)
        if age_before_broadcast > max_quote_age_sec:
            outcome["error"] = f"quote_stale_before_broadcast:{age_before_broadcast:.2f}>{max_quote_age_sec:.2f}"
            log.warning("[PRE_BROADCAST_QUOTE_EXPIRED] age=%.2fs limit=%.2fs", age_before_broadcast, max_quote_age_sec)
            return outcome

        send_started = time.perf_counter()
        r2 = requests.post(_RPC_URL, json={
            "jsonrpc": "2.0", "id": 1, "method": "sendTransaction",
            "params": [base64.b64encode(tx_bytes_signed).decode(),
                       {"encoding": "base64", "preflightCommitment": "confirmed"}],
        }, timeout=10)
        outcome["timings"]["rpc_send_sec"] = round(time.perf_counter() - send_started, 6)
        result = r2.json()
        if "error" in result:
            outcome["error"] = f"send_transaction_error:{result['error']}"
            log.error("[LIVE] sendTransaction error: %s", result["error"])
            return outcome
        outcome["tx_sig"] = result["result"]
        log.info("[LIVE] Transaction submitted: %s", outcome["tx_sig"])
        return outcome
    except ImportError:
        outcome["error"] = "missing_solders_dependency"
        return outcome
    except Exception as exc:
        outcome["error"] = f"swap_exception:{type(exc).__name__}:{exc}"
        log.error("[LIVE] _execute_jupiter_swap error: %s", exc)
        return outcome



def _rpc_get_account_info(address: str, *, encoding: str = "jsonParsed") -> Optional[dict]:
    """Read one account without mutating chain state."""
    try:
        import requests
        response = requests.post(
            _RPC_URL,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [address, {"encoding": encoding, "commitment": "confirmed"}],
            },
            timeout=7,
        )
        payload = response.json()
        if payload.get("error"):
            return None
        return (payload.get("result") or {}).get("value")
    except Exception as exc:
        log.warning("[LIVE_TOKEN_PROVENANCE_RPC_FAIL] address=%s err=%s", str(address)[:16], exc)
        return None


def _canonical_pump_curve_address(mint: str) -> Optional[str]:
    """Derive Pump's canonical [b'bonding-curve', mint] PDA."""
    try:
        from solders.pubkey import Pubkey
        program = Pubkey.from_string(_PUMP_PROGRAM_ID)
        mint_key = Pubkey.from_string(str(mint))
        pda, _ = Pubkey.find_program_address([b"bonding-curve", bytes(mint_key)], program)
        return str(pda)
    except Exception as exc:
        log.warning("[LIVE_TOKEN_PROVENANCE_PDA_FAIL] mint=%s err=%s", str(mint)[:16], exc)
        return None


def inspect_live_token_safety(mint: str) -> dict:
    """Hard funded-entry provenance contract.

    Sentinuity's live lane is specialised for canonical Pump coins.  A vanity
    ``...pump`` suffix is not trusted.  The mint must be an ordinary SPL mint,
    have no remaining mint/freeze authority, and have the exact Pump bonding
    curve PDA owned by the official Pump program.  Token-2022 is denied in the
    live lane because extensions can add transfer fees, hooks, default-frozen
    accounts and permanent delegates that this executor does not fully model.
    """
    result = {
        "safe": False,
        "reason": "unknown",
        "mint": str(mint),
        "suffix_pump": str(mint).lower().endswith("pump"),
        "canonical_pump": False,
        "curve_address": None,
        "mint_owner": None,
        "mint_authority": None,
        "freeze_authority": None,
    }
    if not mint:
        result["reason"] = "missing_mint"
        return result

    mint_account = _rpc_get_account_info(str(mint), encoding="jsonParsed")
    if not mint_account:
        result["reason"] = "mint_account_unavailable"
        return result
    owner = str(mint_account.get("owner") or "")
    result["mint_owner"] = owner
    if owner == _TOKEN_2022_PROGRAM_ID:
        result["reason"] = "token_2022_live_denied"
        return result
    if owner != _SPL_TOKEN_PROGRAM_ID:
        result["reason"] = f"unsupported_mint_owner:{owner[:12]}"
        return result

    parsed = (((mint_account.get("data") or {}).get("parsed") or {}).get("info") or {})
    result["mint_authority"] = parsed.get("mintAuthority")
    result["freeze_authority"] = parsed.get("freezeAuthority")
    if str(os.getenv("LIVE_REQUIRE_REVOKED_MINT_AUTHORITY", "1")).lower() not in {"0","false","off","no"}:
        if result["mint_authority"] not in (None, ""):
            result["reason"] = "mint_authority_not_revoked"
            return result
    if str(os.getenv("LIVE_REQUIRE_REVOKED_FREEZE_AUTHORITY", "1")).lower() not in {"0","false","off","no"}:
        if result["freeze_authority"] not in (None, ""):
            result["reason"] = "freeze_authority_not_revoked"
            return result

    curve_address = _canonical_pump_curve_address(str(mint))
    result["curve_address"] = curve_address
    curve_account = _rpc_get_account_info(curve_address, encoding="base64") if curve_address else None
    canonical = bool(curve_account and str(curve_account.get("owner") or "") == _PUMP_PROGRAM_ID)
    result["canonical_pump"] = canonical

    if result["suffix_pump"] and not canonical:
        result["reason"] = "fake_pump_suffix_no_canonical_curve"
        return result
    require_canonical = str(os.getenv("LIVE_REQUIRE_CANONICAL_PUMP", "1")).lower() not in {"0","false","off","no"}
    if require_canonical and not canonical:
        result["reason"] = "noncanonical_pump_provenance"
        return result

    result["safe"] = True
    result["reason"] = "canonical_pump_verified" if canonical else "standard_spl_verified"
    return result


def _reverse_sellability_probe(mint: str, quoted_token_raw: int, input_lamports: int) -> dict:
    """Require a fresh reverse Jupiter route for the quantity the buy would receive."""
    result = {"viable": False, "reason": "unknown", "round_trip_retention": 0.0,
              "sell_price_impact_pct": None, "recommended_bps": None}
    probe_raw = max(1, int(int(quoted_token_raw) * 0.995))
    best = None
    for bps in _sell_slippage_tiers():
        quote = _get_jupiter_quote(mint, _SOL_MINT, probe_raw, bps)
        if not quote or not quote.get("outAmount") or not validate_jupiter_route(quote):
            continue
        try:
            out_lamports = int(quote.get("outAmount") or 0)
            impact_pct = abs(float(quote.get("priceImpactPct") or 0.0))
        except (TypeError, ValueError):
            continue
        if out_lamports <= 0:
            continue
        candidate = (out_lamports, impact_pct, bps, quote)
        if best is None or candidate[0] > best[0]:
            best = candidate
    if best is None:
        result["reason"] = "no_reverse_sell_route"
        return result

    out_lamports, impact_pct, bps, _ = best
    retention = out_lamports / max(1, int(input_lamports))
    result.update({"round_trip_retention": retention,
                   "sell_price_impact_pct": impact_pct,
                   "recommended_bps": bps})
    min_retention = float(os.getenv("LIVE_MIN_ROUND_TRIP_RETENTION", "0.65"))
    max_sell_impact = float(os.getenv("LIVE_PREFLIGHT_MAX_SELL_IMPACT_PCT", "20.0"))
    if retention < min_retention:
        result["reason"] = f"round_trip_retention_{retention:.3f}_lt_{min_retention:.3f}"
        return result
    if impact_pct > max_sell_impact:
        result["reason"] = f"reverse_sell_impact_{impact_pct:.2f}_gt_{max_sell_impact:.2f}"
        return result
    result["viable"] = True
    result["reason"] = "reverse_sell_route_verified"
    return result


def preflight_live_buy(mint: str, pos_size_usd: float) -> dict:
    """Mandatory funded-entry contract: provenance + buy route + reverse route.

    This is read-only.  It proves that the mint is a canonical Pump coin rather
    than a vanity-suffix imitation and that Jupiter currently exposes both the
    proposed buy and a bounded reverse liquidation route at the expected size.
    """
    result = {
        "viable": False, "reason": "unknown", "price_impact_pct": None,
        "quote_out_amount": None, "sol_usd": None, "wallet_usable_usd": None,
        "token_safety": None, "reverse_sellability": None,
    }
    try:
        notional = float(pos_size_usd or 0.0)
        if not mint or notional <= 0:
            result["reason"] = "invalid_preflight_request"
            return result

        safety = inspect_live_token_safety(str(mint))
        result["token_safety"] = safety
        if not safety.get("safe"):
            result["reason"] = f"token_safety:{safety.get('reason','unknown')}"
            blacklist_mint(str(mint), result["reason"])
            return result

        usable = get_live_wallet_balance()
        usable_usd = float(usable.get("sol_usd", 0.0) if isinstance(usable, dict) else usable or 0.0)
        result["wallet_usable_usd"] = usable_usd
        if usable_usd + 1e-9 < notional:
            result["reason"] = f"insufficient_usable_wallet_{usable_usd:.2f}_lt_{notional:.2f}"
            return result

        sol_usd = float(_get_cached_sol_price() or 0.0)
        result["sol_usd"] = sol_usd
        if sol_usd <= 0:
            result["reason"] = "sol_price_unavailable"
            return result
        lamports_in = int((notional / sol_usd) * 1_000_000_000)
        if lamports_in <= 0:
            result["reason"] = "quote_amount_zero"
            return result

        quote = _get_jupiter_quote(_SOL_MINT, str(mint), lamports_in, _BUY_SLIPPAGE_BPS)
        if not quote or not validate_jupiter_route(quote):
            result["reason"] = "buy_route_invalid_or_unavailable"
            return result
        try:
            out_amount = int(quote.get("outAmount") or 0)
        except (TypeError, ValueError):
            out_amount = 0
        if out_amount <= 0:
            result["reason"] = "buy_quote_zero_output"
            return result

        impact_pct = None
        try:
            impact_pct = abs(float(quote.get("priceImpactPct") or 0.0))
        except (TypeError, ValueError):
            pass
        max_buy_impact = float(os.getenv("LIVE_PREFLIGHT_MAX_BUY_IMPACT_PCT", "15.0"))
        if impact_pct is None or impact_pct > max_buy_impact:
            result["reason"] = f"buy_impact_unacceptable:{impact_pct}"
            return result

        reverse = _reverse_sellability_probe(str(mint), out_amount, lamports_in)
        result["reverse_sellability"] = reverse
        if not reverse.get("viable"):
            result["reason"] = f"sellability:{reverse.get('reason','unknown')}"
            blacklist_mint(str(mint), result["reason"])
            return result

        result.update({
            "viable": True,
            "reason": "canonical_provenance_and_round_trip_verified",
            "price_impact_pct": impact_pct,
            "quote_out_amount": str(out_amount),
        })
        return result
    except Exception as exc:
        result["reason"] = f"preflight_exception_{type(exc).__name__}:{exc}"
        log.exception("[LIVE_PREFLIGHT_FAIL] mint=%s", str(mint)[:16])
        return result


def execute_live_buy(
    mint: str,
    pos_size_usd: float,
    entry_price_usd: float,
    position_id: int,
) -> dict:
    """Execute and reconcile a live buy.  Success means chain fill resolved."""
    result = {
        "success": False, "confirmed": False, "tx_sig": None,
        "actual_price": None, "actual_qty": None, "error": None,
        "mode": "live", "reconciliation_state": "NOT_SUBMITTED",
        "timings": {},
    }
    started = time.perf_counter()

    # FINAL FUNDED-ENTRY CHOKEPOINT.  Upstream Mode-B checks are useful but may
    # be conditional; no caller can bypass this provenance + reverse-route gate.
    _entry_preflight = preflight_live_buy(str(mint), float(pos_size_usd or 0.0))
    result["preflight"] = _entry_preflight
    if not _entry_preflight.get("viable"):
        result["error"] = "live_entry_preflight_blocked:" + str(_entry_preflight.get("reason") or "unknown")
        log.critical("[LIVE_BUY_BLOCKED_SAFETY] pos=%d mint=%s reason=%s",
                     position_id, str(mint)[:16], result["error"])
        return result

    try:
        if _LIVE_MAX_POS_USD_ENV:
            env_cap = float(_LIVE_MAX_POS_USD_ENV)
            if env_cap > 0:
                pos_size_usd = min(float(pos_size_usd), env_cap)
    except (TypeError, ValueError):
        result["error"] = "invalid_live_max_position_env"
        return result

    kp = _load_keypair()
    if not kp:
        result["error"] = "keypair_unavailable"
        return result
    wallet_pubkey = str(kp.pubkey())

    try:
        usable = get_live_wallet_balance()
        usable_usd = float(usable.get("sol_usd", 0.0) if isinstance(usable, dict) else usable or 0.0)
        if usable_usd + 1e-9 < float(pos_size_usd):
            result["error"] = f"insufficient_usable_wallet:{usable_usd:.2f}<{float(pos_size_usd):.2f}"
            return result

        sol_usd = _get_cached_sol_price()
        if sol_usd <= 0:
            result["error"] = "cannot_get_sol_price"
            return result
        lamports_in = int((Decimal(str(pos_size_usd)) / Decimal(str(sol_usd))) * Decimal(1_000_000_000))

        t0 = time.perf_counter()
        quote = _get_jupiter_quote(_SOL_MINT, mint, lamports_in, _BUY_SLIPPAGE_BPS)
        quote_received_mono = time.perf_counter()
        result["timings"]["quote_request_sec"] = round(quote_received_mono - t0, 6)
        if not quote or not validate_jupiter_route(quote):
            result["error"] = "jupiter_quote_failed_or_invalid"
            return result
        impact = float(quote.get("priceImpactPct") or 0.0) * 100.0
        max_impact = float(os.getenv("LIVE_PREFLIGHT_MAX_ENTRY_IMPACT_PCT", "4.0"))
        if impact > max_impact:
            result["error"] = f"entry_impact_too_high:{impact:.2f}>{max_impact:.2f}"
            return result

        # The expiry budget must measure the age of the executable quote, not
        # wallet/RPC/SOL-price preflight time that happened before the quote existed.
        # The previous total-function timer caused healthy quotes to be rejected as
        # entry_expired_before_submit after a slow balance RPC, even though the quote
        # had just arrived. Keep the strict no-chasing doctrine, but apply it to the
        # actual quote-to-broadcast interval.
        quote_age_limit = float(os.getenv("LIVE_ENTRY_DECISION_TO_SUBMIT_MAX_SEC", "5.0"))
        quote_age_before_submit = time.perf_counter() - quote_received_mono
        if quote_age_before_submit > quote_age_limit:
            result["error"] = (
                f"entry_quote_expired_before_submit:{quote_age_before_submit:.2f}>"
                f"{quote_age_limit:.2f}"
            )
            return result

        t0 = time.perf_counter()
        max_quote_age = float(os.getenv("LIVE_MAX_QUOTE_AGE_SEC", "3.0"))
        swap = _execute_jupiter_swap(
            quote, wallet_pubkey, kp,
            quote_received_mono=quote_received_mono,
            max_quote_age_sec=max_quote_age,
        )
        submitted_wall = time.time()
        result["timings"].update(swap.get("timings") or {})
        result["timings"]["compose_sign_broadcast_sec"] = round(time.perf_counter() - t0, 6)
        sig = swap.get("tx_sig")
        if not sig:
            result["error"] = swap.get("error") or "swap_submission_failed"
            return result
        result.update({"tx_sig": sig, "reconciliation_state": "BUY_SUBMITTED"})
        result["timings"]["decision_to_submit_sec"] = round(time.perf_counter() - started, 6)
        # CAPITAL-SAFETY CRITICAL: the signature is persisted BEFORE any
        # confirmation wait. A crash or timeout from here on is recoverable by
        # live_settlement_recovery — the fill can never be silently orphaned.
        _ledger_upsert(
            sig, side="BUY", mint=mint, position_id=position_id,
            state="SUBMITTED",
            quote_out_amount=str(quote.get("outAmount") or ""),
            slippage_bps=_BUY_SLIPPAGE_BPS,
            submitted_at=submitted_wall, local_ack_at=time.time(),
            quote_age_before_broadcast_sec=result["timings"].get("quote_age_before_broadcast_sec"),
            compose_sign_duration_sec=result["timings"].get("compose_sign_duration_sec"),
            latency_warning=None, error=None,
        )

        t0 = time.perf_counter()
        confirmed = _confirm_transaction(sig)
        result["timings"]["confirmation_sec"] = round(time.perf_counter() - t0, 6)
        if not confirmed:
            # NOT a terminal failure: the transaction may confirm after our
            # local window. The ledger row remains SUBMITTED and the recovery
            # service will resolve it from chain truth (directive Part 1:
            # "confirmation after a local timeout is recovered").
            result["error"] = "confirmation_timeout_recoverable"
            result["recoverable"] = True
            _ledger_upsert(sig, side="BUY", mint=mint, position_id=position_id,
                           state="SUBMITTED", error="local_confirmation_timeout")
            return result
        result["confirmed"] = True
        result["reconciliation_state"] = "BUY_CONFIRMED_UNRESOLVED"
        _ledger_upsert(sig, side="BUY", mint=mint, position_id=position_id,
                       state="CONFIRMED_UNRESOLVED", confirmed_at=time.time())

        t0 = time.perf_counter()
        fill = resolve_confirmed_fill(sig, wallet_pubkey, mint)
        result["timings"]["fill_reconciliation_sec"] = round(time.perf_counter() - t0, 6)
        if not fill or int(fill["token_delta_raw"]) <= 0 or int(fill["native_delta_lamports"]) >= 0:
            result["error"] = "confirmed_buy_fill_unresolved"
            result["fill_meta"] = fill
            _ledger_upsert(sig, side="BUY", mint=mint, position_id=position_id,
                           state="CONFIRMED_UNRESOLVED",
                           error="fill_unresolved_after_confirmation",
                           fill_meta_json=json.dumps(fill or {}, sort_keys=True))
            return result

        actual_qty = Decimal(fill["token_delta"])
        net_spent_sol = -Decimal(fill["native_delta_sol"])
        actual_cost_usd = net_spent_sol * Decimal(str(sol_usd))
        actual_price = actual_cost_usd / actual_qty
        fill["sol_usd"] = str(sol_usd)
        fill["net_spent_sol"] = str(net_spent_sol)
        fill["actual_cost_usd"] = str(actual_cost_usd)
        result.update({
            "success": True,
            "actual_qty": float(actual_qty),
            "actual_price": float(actual_price),
            "actual_cost_usd": float(actual_cost_usd),
            "net_spent_sol": float(net_spent_sol),
            "fee_sol": float(Decimal(fill["fee_sol"])),
            "chain_confirmed_at": float(fill.get("block_time") or time.time()),
            "reconciled_at": time.time(),
            "fill_meta": fill,
            "reconciliation_state": "OPEN_REAL",
        })
        result["timings"]["total_sec"] = round(time.perf_counter() - started, 6)
        _ledger_upsert(sig, side="BUY", mint=mint, position_id=position_id,
                       state="RESOLVED",
                       block_time=float(fill.get("block_time") or 0.0),
                       reconciled_at=result["reconciled_at"],
                       fill_meta_json=json.dumps(fill, sort_keys=True, default=str))
        _record_latency_telemetry(sig, "BUY", position_id, mint, result["timings"])
        log.info("[LIVE] BUY RECONCILED pos=%d sig=%s qty=%s cost_sol=%s total=%.3fs",
                 position_id, sig[:20], actual_qty, net_spent_sol, result["timings"]["total_sec"])
        _sync_wallet_to_system_state()
        return result
    except Exception as exc:
        log.exception("[LIVE] execute_live_buy error pos=%d", position_id)
        result["error"] = f"{type(exc).__name__}:{exc}"
        return result


# ── SELL HARDENING LAYER ─────────────────────────────────────────────────────


def _get_quote_with_retry(input_mint: str, output_mint: str, amount: int, *, emergency: bool = False) -> tuple:
    """
    SIGNOFF_LIVE_LEDGER_20260716: the 10%→30%→50%→100% chasing ladder is
    REMOVED (directive Part 2). One primary quote, at most ONE bounded
    fallback, both governed by env and hard-capped at 30%. A sell whose
    executable impact exceeds LIVE_MAX_SELL_IMPACT_PCT is refused rather than
    forced — the position stays open and honest instead of being liquidated
    into a hole the marks never showed.
    Returns (quote_dict, used_bps) or (None, None).
    """
    max_impact_pct = float(os.getenv(
        "LIVE_EMERGENCY_MAX_SELL_IMPACT_PCT" if emergency else "LIVE_MAX_SELL_IMPACT_PCT",
        "95.0" if emergency else "40.0",
    ))
    for bps in _sell_slippage_tiers():
        quote = _get_jupiter_quote(input_mint, output_mint, amount, bps)
        if not quote or not quote.get("outAmount"):
            log.warning("[SELL_QUOTE] no route at %d BPS", bps)
            continue
        impact = abs(float(quote.get("priceImpactPct") or 0.0)) * 100.0
        if impact > max_impact_pct:
            log.error("[SELL_QUOTE] route at %d BPS refused: impact %.2f%% > cap %.2f%%",
                      bps, impact, max_impact_pct)
            continue
        log.info("[SELL_QUOTE] route ok at %d BPS impact=%.2f%%", bps, impact)
        return quote, bps
    log.error("[SELL_QUOTE] no acceptable route within bounded tiers — refusing to chase")
    return None, None


def can_sell(mint: str, quantity: float) -> tuple:
    """
    Pre-flight exit check — verify Jupiter can route a sell BEFORE we buy.
    Returns (can_sell: bool, recommended_slippage_bps: int).
    """
    try:
        # SIGNOFF_LIVE_LEDGER_20260716: decimals resolved from chain, never
        # assumed to be 6 (directive test 15: decimal mismatch).
        decimals = _get_token_decimals(mint)
        raw_amount = int(Decimal(str(quantity)) * (Decimal(10) ** decimals))
        if raw_amount <= 0:
            return False, 0
        for bps in _sell_slippage_tiers():
            quote = _get_jupiter_quote(mint, _SOL_MINT, raw_amount, bps)
            if quote and quote.get("outAmount"):
                log.info("[PREFLIGHT] Exit confirmed at %d BPS for %s", bps, mint[:16])
                return True, bps
        log.warning("[PREFLIGHT] No exit route for %s — BLOCKING ENTRY", mint[:16])
        return False, 0
    except Exception as e:
        log.error("[PREFLIGHT] can_sell error: %s", e)
        return False, 0


def evaluate_exit_quality(mint: str, quantity: float) -> dict:
    """
    Deep exit viability — checks price impact to detect fake/thin liquidity.
    Returns dict: viable, recommended_bps, impact, reason.

    No token is exempt based on its address suffix.
    """
    try:
        decimals = _get_token_decimals(mint)
        raw_amount = int(Decimal(str(quantity)) * (Decimal(10) ** decimals))
        if raw_amount <= 0:
            return {"viable": False, "reason": "zero_quantity"}
        results = []
        for bps in _sell_slippage_tiers():
            quote = _get_jupiter_quote(mint, _SOL_MINT, raw_amount, bps)
            if not quote:
                continue
            out_amt = int(quote.get("outAmount", 0))
            if out_amt <= 0 or not validate_jupiter_route(quote):
                continue
            # Jupiter's priceImpactPct is already normalized across differing
            # token decimals. Comparing raw input/output integers is invalid.
            impact = abs(float(quote.get("priceImpactPct") or 0.0)) / 100.0
            results.append({"bps": bps, "out": out_amt, "impact": impact})
        if not results:
            return {"viable": False, "reason": "no_routes"}
        best = max(results, key=lambda x: x["out"])
        if best["impact"] > 0.60:
            return {"viable": False, "reason": "extreme_price_impact",
                    "impact": best["impact"]}
        if best["bps"] >= _sell_slippage_tiers()[-1] and best["impact"] > 0.40:
            return {"viable": False, "reason": "requires_max_bounded_slippage_high_impact",
                    "impact": best["impact"]}
        return {"viable": True, "recommended_bps": best["bps"],
                "impact": best["impact"], "reason": "ok"}
    except Exception as e:
        log.error("[EXIT_QUALITY] error: %s", e)
        return {"viable": False, "reason": f"exception:{e}"}


def score_liquidity(mint: str, quantity: float) -> float:
    """
    Liquidity scoring 0.0 (terrible) → 1.0 (excellent).
    Penalises high slippage impact and multi-hop routes.
    Block entries scoring < 0.4.

    No token is exempt based on a vanity suffix.
    """
    try:
        decimals = _get_token_decimals(mint)
        raw_amount = int(Decimal(str(quantity)) * (Decimal(10) ** decimals))
        quote = _get_jupiter_quote(mint, _SOL_MINT, raw_amount, _sell_slippage_tiers()[-1])
        if not quote:
            return 0.0
        out_amt = int(quote.get("outAmount", 0))
        if out_amt <= 0 or not validate_jupiter_route(quote):
            return 0.0
        impact = abs(float(quote.get("priceImpactPct") or 0.0)) / 100.0
        route_len = len(quote.get("routePlan", []))
        score     = 1.0 - (impact * 1.5) - ((route_len - 1) * 0.1)
        return max(0.0, min(score, 1.0))
    except Exception as e:
        log.error("[LIQUIDITY_SCORE] error: %s", e)
        return 0.0


def rug_score(mint: str) -> float:
    """
    Rug probability heuristic 0.0 (safe) → 1.0 (likely rug).
    Uses DexScreener liquidity/volume ratio.
    Block entries scoring > 0.8.
    """
    try:
        import requests as _req
        r = _req.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{mint}",
            timeout=4
        )
        pairs = r.json().get("pairs", [])
        if not pairs:
            return 1.0
        p          = pairs[0]
        liquidity  = float((p.get("liquidity") or {}).get("usd") or 0)
        volume     = float((p.get("volume") or {}).get("h24") or 0)
        if liquidity < 2000:
            return 0.9
        if volume < liquidity * 0.2:
            return 0.7
        return 0.2
    except Exception:
        return 1.0


def validate_jupiter_route(quote: dict) -> bool:
    """Reject suspicious routes — too many hops or missing AMM keys."""
    try:
        route_plan = quote.get("routePlan", [])
        if not route_plan:
            return False
        if len(route_plan) > 3:
            log.warning("[ROUTE] Rejected: %d hops (max 3)", len(route_plan))
            return False
        for hop in route_plan:
            if not hop.get("swapInfo", {}).get("ammKey"):
                log.warning("[ROUTE] Rejected: hop missing ammKey")
                return False
        return True
    except Exception:
        return False


def blacklist_mint(mint: str, reason: str) -> None:
    """Add mint to mint_blacklist — never trade again."""
    try:
        import time as _t
        from core.schema import get_connection
        with get_connection() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO mint_blacklist "
                "(mint_address, reason, blacklisted_at) VALUES (?, ?, ?)",
                (mint, reason[:120], _t.time())
            )
            conn.commit()
        log.warning("[BLACKLIST] %s — reason: %s", mint[:16], reason)
    except Exception as e:
        log.error("[BLACKLIST] Failed to blacklist %s: %s", mint[:16], e)


def _get_token_decimals(mint: str) -> int:
    try:
        result = _rpc_call("getTokenSupply", [mint, {"commitment": "confirmed"}], timeout=8.0)
        return int(((result or {}).get("value") or {}).get("decimals") or 0)
    except Exception as exc:
        log.error("[TOKEN_DECIMALS] mint=%s error=%s", mint[:16], exc)
        raise RuntimeError("token_decimals_unresolved") from exc


def verify_token_balance(mint: str, wallet_pubkey: str) -> float:
    """Check actual token balance using raw integer amounts and Decimal."""
    try:
        result = _rpc_call(
            "getTokenAccountsByOwner",
            [wallet_pubkey, {"mint": mint}, {"encoding": "jsonParsed", "commitment": "confirmed"}],
            timeout=8.0,
        )
        total_raw = 0
        decimals = None
        for acc in (result or {}).get("value") or []:
            amount = (((acc.get("account") or {}).get("data") or {}).get("parsed") or {}).get("info", {}).get("tokenAmount", {})
            total_raw += int(str(amount.get("amount") or "0"))
            dec = int(amount.get("decimals") or 0)
            if decimals is None:
                decimals = dec
            elif decimals != dec:
                raise RuntimeError("wallet_token_decimal_mismatch")
        scale = Decimal(10) ** int(decimals or 0)
        return float(Decimal(total_raw) / scale) if scale else 0.0
    except Exception as exc:
        log.error("[BALANCE_CHECK] %s", exc)
        return -1.0


def execute_live_sell(
    mint: str,
    quantity: float,
    position_id: int,
    exit_price_usd: float,
    emergency: bool = False,
) -> dict:
    """Execute and reconcile a live sell. No theoretical close fallback exists."""
    result = {
        "success": False, "confirmed": False, "tx_sig": None,
        "actual_exit_price": None, "actual_qty_sold": None,
        "net_sol_received": None, "fee_sol": None, "error": None,
        "reconciliation_state": "NOT_SUBMITTED", "timings": {},
    }
    kp = _load_keypair()
    if not kp:
        result["error"] = "keypair_unavailable"
        return result
    wallet_pubkey = str(kp.pubkey())
    started = time.perf_counter()
    try:
        wallet_qty = verify_token_balance(mint, wallet_pubkey)
        if wallet_qty < 0:
            result["error"] = "wallet_balance_check_failed"
            return result
        if wallet_qty <= 0.001:
            result["error"] = "wallet_zero_without_reconciled_sell_signature"
            result["reconciliation_state"] = "MANUAL_INTERVENTION"
            return result

        sell_qty = min(float(quantity or wallet_qty), float(wallet_qty)) * 0.9975
        decimals = _get_token_decimals(mint)
        raw_token_amount = int(Decimal(str(sell_qty)) * (Decimal(10) ** decimals))
        if raw_token_amount <= 0:
            result["error"] = "zero_quantity_after_wallet_truth"
            return result

        t0 = time.perf_counter()
        quote, used_bps = _get_quote_with_retry(mint, _SOL_MINT, raw_token_amount, emergency=bool(emergency))
        quote_received_mono = time.perf_counter()
        result["timings"]["quote_request_sec"] = round(quote_received_mono - t0, 6)
        if not quote or not validate_jupiter_route(quote):
            result["error"] = "no_valid_liquidation_route"
            return result

        t0 = time.perf_counter()
        max_quote_age = float(os.getenv("LIVE_MAX_QUOTE_AGE_SEC", "3.0"))
        swap = _execute_jupiter_swap(
            quote, wallet_pubkey, kp,
            quote_received_mono=quote_received_mono,
            max_quote_age_sec=max_quote_age,
        )
        result["timings"].update(swap.get("timings") or {})
        result["timings"]["compose_sign_broadcast_sec"] = round(time.perf_counter() - t0, 6)
        sig = swap.get("tx_sig")
        if not sig:
            result["error"] = swap.get("error") or "swap_submission_failed"
            return result
        result.update({"tx_sig": sig, "reconciliation_state": "SELL_SUBMITTED"})
        result["timings"]["decision_to_submit_sec"] = round(time.perf_counter() - started, 6)
        # CAPITAL-SAFETY CRITICAL: persist the sell signature into BOTH the
        # ledger and the position row BEFORE waiting for confirmation. A crash
        # during SELL_SUBMITTED (directive test 5) or confirmation after a
        # local timeout (test 3) is now recoverable from chain truth.
        _ledger_upsert(sig, side="SELL", mint=mint, position_id=position_id,
                       state="SUBMITTED",
                       quote_out_amount=str(quote.get("outAmount") or ""),
                       slippage_bps=int(used_bps or 0),
                       submitted_at=time.time(), local_ack_at=time.time(),
                       quote_age_before_broadcast_sec=result["timings"].get("quote_age_before_broadcast_sec"),
                       compose_sign_duration_sec=result["timings"].get("compose_sign_duration_sec"),
                       latency_warning=None, error=None)
        try:
            from core.schema import get_connection
            with get_connection() as _sconn:
                _sconn.execute(
                    "UPDATE paper_positions SET sell_tx_sig=?, live_state='SELL_SUBMITTED' "
                    "WHERE id=? AND UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
                    "AND COALESCE(live_state,'') NOT IN ('SETTLED')",
                    (sig, position_id),
                )
                _sconn.commit()
        except Exception as _swf:
            log.critical("[LIVE_SELL_SIG_PERSIST_FAIL] pos=%d sig=%s err=%s",
                         position_id, sig[:20], _swf)

        t0 = time.perf_counter()
        confirmed = _confirm_transaction(sig)
        result["timings"]["confirmation_sec"] = round(time.perf_counter() - t0, 6)
        if not confirmed:
            # Recoverable — signature is already persisted; the recovery
            # service settles this position from chain truth when the
            # transaction lands (or reverts state if it errs on chain).
            result["error"] = "confirmation_timeout_recoverable"
            result["recoverable"] = True
            _ledger_upsert(sig, side="SELL", mint=mint, position_id=position_id,
                           state="SUBMITTED", error="local_confirmation_timeout")
            return result
        result["confirmed"] = True
        result["reconciliation_state"] = "SELL_CONFIRMED_UNRESOLVED"
        _ledger_upsert(sig, side="SELL", mint=mint, position_id=position_id,
                       state="CONFIRMED_UNRESOLVED", confirmed_at=time.time())

        t0 = time.perf_counter()
        fill = resolve_confirmed_fill(sig, wallet_pubkey, mint)
        result["timings"]["fill_reconciliation_sec"] = round(time.perf_counter() - t0, 6)
        if not fill or int(fill["token_delta_raw"]) >= 0 or int(fill["native_delta_lamports"]) <= 0:
            result["error"] = "confirmed_sell_fill_unresolved"
            result["fill_meta"] = fill
            _ledger_upsert(sig, side="SELL", mint=mint, position_id=position_id,
                           state="CONFIRMED_UNRESOLVED",
                           error="fill_unresolved_after_confirmation",
                           fill_meta_json=json.dumps(fill or {}, sort_keys=True))
            return result

        qty_sold = -Decimal(fill["token_delta"])
        net_received_sol = Decimal(fill["native_delta_sol"])
        sol_usd = Decimal(str(_get_cached_sol_price() or 0.0))
        actual_exit_price = (net_received_sol * sol_usd / qty_sold) if qty_sold > 0 and sol_usd > 0 else Decimal(0)
        fill["net_received_sol"] = str(net_received_sol)
        fill["sol_usd"] = str(sol_usd)
        result.update({
            "success": True,
            "actual_qty_sold": float(qty_sold),
            "net_sol_received": float(net_received_sol),
            "fee_sol": float(Decimal(fill["fee_sol"])),
            "actual_exit_price": float(actual_exit_price),
            "chain_confirmed_at": float(fill.get("block_time") or time.time()),
            "reconciled_at": time.time(),
            "fill_meta": fill,
            "used_slippage_bps": used_bps,
            "reconciliation_state": "SETTLED",
        })
        result["timings"]["total_sec"] = round(time.perf_counter() - started, 6)
        _ledger_upsert(sig, side="SELL", mint=mint, position_id=position_id,
                       state="RESOLVED",
                       block_time=float(fill.get("block_time") or 0.0),
                       reconciled_at=result["reconciled_at"],
                       fill_meta_json=json.dumps(fill, sort_keys=True, default=str))
        _record_latency_telemetry(sig, "SELL", position_id, mint, result["timings"])
        log.info("[LIVE] SELL RECONCILED pos=%d sig=%s qty=%s net_sol=%s total=%.3fs",
                 position_id, sig[:20], qty_sold, net_received_sol, result["timings"]["total_sec"])
        _sync_wallet_to_system_state()
        return result
    except Exception as exc:
        log.exception("[LIVE] execute_live_sell error pos=%d", position_id)
        result["error"] = f"{type(exc).__name__}:{exc}"
        return result


def is_live_mode() -> bool:
    """True when the independent dual live lane is fully armed."""
    try:
        from core.schema import get_config_value
        on = lambda k: str(get_config_value(k, "0")).strip().lower() in {"1","true","yes","on"}
        return all(on(k) for k in ("DUAL_MODE_ENABLED","DUAL_MODE_ARMED",
                                    "LIVE_TRADING_ENABLED","LIVE_MODE_B_ENABLED","LIVE_ARMED"))
    except Exception:
        return False