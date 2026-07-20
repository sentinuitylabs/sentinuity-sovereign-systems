"""
services/wallet_scout.py

SENTINUITY WALLET SCOUT
========================
Watches profitable Solana wallets and reverse-engineers their entry patterns.
NOT copy trading — this is quantitative research on public blockchain data.
Legal in Australia for personal trading research (ASIC does not regulate
analysis of public on-chain data for personal use).

WHAT IT DOES:
  1. Maintains a watchlist of high-profit-score wallets (watched_wallets table)
  2. Every cycle, fetches recent transactions for each watched wallet via your configured RPC
  3. For each transaction that acquired a new token, records timing and metrics
     in wallet_pattern_observations table
  4. Uses Grok's exact field paths for accurate time-from-launch calculation:
       - result.blockTime for timestamps
       - result.meta.postTokenBalances / preTokenBalances for acquisition detection
       - result.meta.preTokenBalances[*].owner for wallet identification
  5. POLARIS standing task WALLET_PATTERN_LEARNING reads these observations
     and correlates them with our own trade outcomes via polaris_trade_reviews

HOW TO ADD WALLETS:
  Run this SQL to add a wallet to the watchlist:
    INSERT INTO watched_wallets (wallet_address, label)
    VALUES ('wallet_address_here', 'Profitable wallet #1');

  Or run: python services/wallet_scout.py --add <wallet_address> [label]

WHAT MAKES A WALLET WORTH WATCHING:
  Per Grok's analysis: consistent profit over >= 20 trades, low rug exposure,
  entry timing 30-180s after launch (not dev/sniper, not late bag-holder),
  healthy holder distribution at entry.

FIELD PATHS (Grok's exact specification):
  Mint launch timestamp: result.blockTime
  Wallet acquisition: result.meta.postTokenBalances[*].mint + owner + uiTokenAmount.amount
  Pre-balance (zero if new account): result.meta.preTokenBalances[*].uiTokenAmount.amount
  Account pubkey lookup: result.transaction.message.accountKeys[accountIndex].pubkey
  Token delta: post_amount - pre_amount (if no pre row, pre = 0)

Run from trading-bot root:
  python -m services.wallet_scout

Runs every 10 minutes. Lightweight — one getSignaturesForAddress call per wallet
plus selective getTransaction calls for token acquisition txs only.
"""
from __future__ import annotations


import json
import logging
import sqlite3
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
import requests

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

from core.schema import get_connection, update_heartbeat

# ── Smart Wallet Conviction integration (OBSERVE/TRAINING only) ──────────────
try:
    from services.smart_wallet_conviction import (
        ensure_smart_wallet_schema,
        build_fingerprint_for_wallet,
        _connect as _smart_wallet_connect,
    )
    _SMART_WALLET_CONVICTION_AVAILABLE = True
except Exception:
    try:
        from smart_wallet_conviction import (
            ensure_smart_wallet_schema,
            build_fingerprint_for_wallet,
            _connect as _smart_wallet_connect,
        )
        _SMART_WALLET_CONVICTION_AVAILABLE = True
    except Exception:
        _SMART_WALLET_CONVICTION_AVAILABLE = False
        ensure_smart_wallet_schema = None
        build_fingerprint_for_wallet = None
        _smart_wallet_connect = None

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [WALLET_SCOUT] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("wallet_scout")

SERVICE_NAME   = "wallet_scout"
POLL_INTERVAL  = 600   # 10 minutes between full cycles
HTTP_TIMEOUT   = 10
MAX_WALLETS    = 50    # never watch more than 50 — keeps RPC cost bounded
MAX_SIG_FETCH  = 20    # signatures to fetch per wallet per cycle

# ── GMGN Smart Wallet source intake (OBSERVE/TRAINING, read-only) ─────────────
# This does NOT copy trade and does NOT influence live entries. It only imports
# public leaderboard wallet profiles into the Smart Wallet Conviction tables so
# the hub can show dataset absorption/fingerprint progress.
GMGN_ENABLED = os.getenv("GMGN_WALLET_SCOUT_ENABLED", "1").strip().lower() not in {"0", "false", "no", "off"}
GMGN_TOP_WALLETS_URL = os.getenv(
    "GMGN_TOP_WALLETS_URL",
    "https://gmgn.ai/defi/quotation/v1/rank/sol/wallets/top_traders",
).strip()
GMGN_SOURCE_NAME = os.getenv("GMGN_SOURCE_NAME", "gmgn_api").strip() or "gmgn_api"
GMGN_INTERVAL_SECONDS = int(os.getenv("GMGN_WALLET_SCOUT_INTERVAL_SECONDS", "3600") or "3600")
GMGN_LIMIT = max(1, min(100, int(os.getenv("GMGN_WALLET_SCOUT_LIMIT", "20") or "20")))
GMGN_FINGERPRINT_LIMIT = max(0, min(50, int(os.getenv("GMGN_FINGERPRINT_LIMIT", "10") or "10")))

# Prefer Helius first so wallet intelligence can ride your spare endpoint cleanly.
# Fallback order keeps compatibility with the rest of the organism:
#   1. HELIUS_RPC
#   2. QUICKNODE_RPC
#   3. QUICKNODE_WSS converted to HTTPS
RPC_URL = os.getenv("HELIUS_RPC", "").strip().strip('"').strip("'")
RPC_PROVIDER = "HELIUS_RPC" if RPC_URL else ""

if not RPC_URL:
    RPC_URL = os.getenv("QUICKNODE_RPC", "").strip().strip('"').strip("'")
    if RPC_URL:
        RPC_PROVIDER = "QUICKNODE_RPC"

if not RPC_URL:
    RPC_URL = os.getenv("QUICKNODE_WSS", "").strip().replace("wss://", "https://").replace("ws://", "http://")
    if RPC_URL:
        RPC_PROVIDER = "QUICKNODE_WSS->HTTPS"

# Base58 addresses to always exclude (native SOL, USDC, USDT etc.)
STABLE_MINTS = {
    "So11111111111111111111111111111111111111112",   # SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB", # USDT
    "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263", # BONK
}


# ── RPC HELPERS ───────────────────────────────────────────────────────────────

def _rpc_post(session: requests.Session, method: str, params: list) -> Optional[Any]:
    """Generic Solana RPC call. Returns result or None on failure."""
    try:
        resp = session.post(
            RPC_URL,
            json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            timeout=HTTP_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        if "error" in data:
            log.debug("RPC error %s: %s", method, data["error"])
            return None
        return data.get("result")
    except Exception as e:
        log.debug("RPC %s failed: %s", method, e)
        return None


def get_signatures_for_address(session: requests.Session, wallet: str, limit: int = MAX_SIG_FETCH) -> list[str]:
    """
    Fetch recent confirmed transaction signatures for a wallet.
    Uses getSignaturesForAddress — 1 RPC credit per call (cheap).
    """
    result = _rpc_post(session, "getSignaturesForAddress", [
        wallet,
        {"limit": limit, "commitment": "confirmed"},
    ])
    if not result:
        return []
    return [r["signature"] for r in result if not r.get("err")]


def get_transaction_parsed(session: requests.Session, sig: str) -> Optional[dict]:
    """
    Fetch full parsed transaction by signature.
    Uses getTransaction with jsonParsed encoding — 1 RPC credit per call.
    Uses Grok's exact field paths for token balance analysis.
    """
    return _rpc_post(session, "getTransaction", [
        sig,
        {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        },
    ])


# ── TOKEN ACQUISITION DETECTION ──────────────────────────────────────────────

def extract_token_acquisitions(wallet: str, tx_result: dict) -> list[dict]:
    """
    Detect token acquisitions by the wallet in a transaction.
    Uses Grok's exact field paths:
      - result.blockTime for timestamp
      - result.meta.postTokenBalances[*].mint + owner + accountIndex + uiTokenAmount.amount
      - result.meta.preTokenBalances[*].mint + owner + accountIndex + uiTokenAmount.amount
      - result.transaction.message.accountKeys[accountIndex].pubkey

    Returns list of {mint, delta, block_time, tx_sig} for each acquired token.
    Excludes stablecoins and SOL. Excludes if wallet was seller not buyer.
    """
    acquisitions = []

    try:
        block_time = tx_result.get("blockTime")
        if not block_time:
            return []

        meta = tx_result.get("meta") or {}
        if meta.get("err"):
            return []

        tx = tx_result.get("transaction") or {}
        msg = tx.get("message") or {}
        account_keys = msg.get("accountKeys") or []

        # Build lookup: accountIndex -> pubkey
        key_map = {}
        for i, ak in enumerate(account_keys):
            if isinstance(ak, dict):
                key_map[i] = ak.get("pubkey", "")
            elif isinstance(ak, str):
                key_map[i] = ak

        pre_balances  = meta.get("preTokenBalances")  or []
        post_balances = meta.get("postTokenBalances") or []

        # Build pre-balance lookup: (mint, owner) -> amount
        pre_map = {}
        for pb in pre_balances:
            mint  = pb.get("mint", "")
            owner = pb.get("owner") or key_map.get(pb.get("accountIndex", -1), "")
            amt   = int((pb.get("uiTokenAmount") or {}).get("amount") or 0)
            pre_map[(mint, owner)] = amt

        # Check each post-balance entry for wallet acquisition
        for pb in post_balances:
            mint  = pb.get("mint", "")
            owner = pb.get("owner") or key_map.get(pb.get("accountIndex", -1), "")
            post_amt = int((pb.get("uiTokenAmount") or {}).get("amount") or 0)

            if owner != wallet:
                continue
            if mint in STABLE_MINTS:
                continue
            if not mint or len(mint) < 32:
                continue

            # Pre-balance (zero if new account — wallet's first receive of this token)
            pre_amt = pre_map.get((mint, owner), 0)

            delta = post_amt - pre_amt
            if delta <= 0:
                continue  # Wallet sent, not received

            acquisitions.append({
                "mint":       mint,
                "delta":      delta,
                "block_time": block_time,
                "pre_amt":    pre_amt,
                "post_amt":   post_amt,
            })

    except Exception as e:
        log.debug("extract_token_acquisitions failed: %s", e)

    return acquisitions


# ── MINT LAUNCH TIME LOOKUP ───────────────────────────────────────────────────

_mint_launch_cache: dict[str, Optional[int]] = {}  # in-memory cache, resets each run

def get_mint_launch_time(session: requests.Session, mint: str) -> Optional[int]:
    """
    Find the timestamp when a mint was initialized on-chain.
    Uses getSignaturesForAddress on the mint itself, then fetches the oldest
    (first) transaction which should be the initializeMint instruction.

    Per Grok's spec: look for instruction where
      program == "spl-token" AND parsed.type in ("initializeMint", "initializeMint2")
      AND parsed.info.mint == mint_address

    Caches results to avoid repeated RPC calls for same mint.
    Returns blockTime of mint init tx, or None if not found.
    """
    if mint in _mint_launch_cache:
        return _mint_launch_cache[mint]

    try:
        # Get oldest signatures for the mint address (limit small, we want the first)
        result = _rpc_post(session, "getSignaturesForAddress", [
            mint,
            {"limit": 5, "commitment": "confirmed", "before": None},
        ])
        if not result:
            _mint_launch_cache[mint] = None
            return None

        # The initializeMint tx should be the oldest — fetch the last signature
        oldest_sig = result[-1]["signature"] if result else None
        if not oldest_sig:
            _mint_launch_cache[mint] = None
            return None

        tx = get_transaction_parsed(session, oldest_sig)
        if not tx:
            _mint_launch_cache[mint] = None
            return None

        launch_time = tx.get("blockTime")

        # Verify it's actually an initializeMint instruction
        msg = (tx.get("transaction") or {}).get("message") or {}
        instructions = msg.get("instructions") or []
        is_mint_init = False
        for inst in instructions:
            if not isinstance(inst, dict):
                continue
            prog = inst.get("program", "")
            parsed = inst.get("parsed") or {}
            ptype = parsed.get("type", "") if isinstance(parsed, dict) else ""
            info  = parsed.get("info") or {} if isinstance(parsed, dict) else {}
            if prog in ("spl-token", "spl-token-2022") and ptype in ("initializeMint", "initializeMint2"):
                if isinstance(info, dict) and info.get("mint") == mint:
                    is_mint_init = True
                    break

        if not is_mint_init:
            # Fallback: use blockTime of oldest tx as approximate launch time
            # This happens when initializeMint was bundled with other instructions
            pass

        _mint_launch_cache[mint] = launch_time
        return launch_time

    except Exception as e:
        log.debug("get_mint_launch_time failed for %s: %s", mint, e)
        _mint_launch_cache[mint] = None
        return None



# ── GMGN SMART WALLET PROFILE INGESTION ──────────────────────────────────────

def _smart_wallet_db_path() -> Path:
    return BASE_DIR / "sentinuity_matrix.db"


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        if isinstance(value, str):
            value = value.replace("%", "").replace(",", "").strip()
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(float(str(value).replace(",", "").strip()))
    except Exception:
        return default


def _first_value(row: dict, keys: tuple[str, ...], default: Any = None) -> Any:
    for key in keys:
        if key in row and row.get(key) not in (None, ""):
            return row.get(key)
    return default


def _normalise_rate(value: Any, default: float = 0.0) -> float:
    """Return 0..1 for rates whether source provides 0.72, 72, or '72%'."""
    raw = _safe_float(value, default)
    if raw > 1.0:
        raw = raw / 100.0
    return max(0.0, min(1.0, raw))


def _extract_wallet_address(row: dict) -> str:
    value = _first_value(row, (
        "wallet_address", "wallet", "address", "addr", "account", "owner", "maker", "trader", "user_address",
        "walletAddress", "smart_wallet", "smartWallet", "holder_address", "address_name",
    ), "")
    if isinstance(value, dict):
        value = _first_value(value, ("address", "wallet", "wallet_address", "addr"), "")
    return str(value or "").strip()


def _looks_like_wallet_record(row: Any) -> bool:
    return isinstance(row, dict) and bool(_extract_wallet_address(row))


def _find_wallet_records(payload: Any, limit: int) -> list[dict]:
    """Schema-tolerant search through GMGN responses for wallet-like dicts."""
    found: list[dict] = []

    def walk(obj: Any) -> None:
        if len(found) >= limit:
            return
        if isinstance(obj, list):
            # Prefer lists that directly contain wallet rows.
            for item in obj:
                if len(found) >= limit:
                    break
                if _looks_like_wallet_record(item):
                    found.append(item)
                else:
                    walk(item)
            return
        if isinstance(obj, dict):
            if _looks_like_wallet_record(obj):
                found.append(obj)
                return
            # Prioritise common data containers before recursive scan.
            for key in ("data", "list", "rank", "ranks", "items", "wallets", "top_traders", "result", "rows"):
                if key in obj:
                    walk(obj.get(key))
                    if len(found) >= limit:
                        return
            for value in obj.values():
                walk(value)
                if len(found) >= limit:
                    return

    walk(payload)

    # Dedupe by wallet address while preserving order.
    deduped: list[dict] = []
    seen: set[str] = set()
    for row in found:
        addr = _extract_wallet_address(row)
        if addr and addr not in seen:
            seen.add(addr)
            deduped.append(row)
        if len(deduped) >= limit:
            break
    return deduped


def _prepare_gmgn_session(base_session: requests.Session) -> tuple[requests.Session, dict]:
    """Attach Cloudflare/browser clearance if operator provided it.

    GMGN often rejects bare Python requests. This is intentionally simple and
    explicit: paste GMGN_CF_CLEARANCE + GMGN_CF_UA into .env, or GMGN_COOKIE for
    a full cookie header. No secrets are logged.
    """
    diag = {"cf_bridge": "not_configured"}
    cookie = os.getenv("GMGN_COOKIE", "").strip()
    clearance = os.getenv("GMGN_CF_CLEARANCE", "").strip()
    ua = os.getenv("GMGN_CF_UA", "").strip()

    if cookie:
        base_session.headers.update({
            "User-Agent": ua or base_session.headers.get("User-Agent") or "Mozilla/5.0",
            "Cookie": cookie,
            "Referer": "https://gmgn.ai/",
            "Origin": "https://gmgn.ai",
            "Accept": "application/json,text/plain,*/*",
        })
        diag.update({"cf_bridge": "full_cookie", "ua": "custom" if ua else "default"})
        return base_session, diag

    if clearance:
        base_session.cookies.set("cf_clearance", clearance, domain=".gmgn.ai", path="/")
        base_session.headers.update({
            "User-Agent": ua or base_session.headers.get("User-Agent") or "Mozilla/5.0",
            "Referer": "https://gmgn.ai/",
            "Origin": "https://gmgn.ai",
            "Accept": "application/json,text/plain,*/*",
        })
        diag.update({"cf_bridge": "cf_clearance", "ua": "custom" if ua else "default"})
        return base_session, diag

    # Optional drop-in module, if present. This keeps compatibility with the
    # earlier bridge file without making wallet_scout depend on pywin32/DPAPI.
    try:
        try:
            from services.gmgn_cf_bridge import build_gmgn_session  # type: ignore
        except Exception:
            from gmgn_cf_bridge import build_gmgn_session  # type: ignore
        bridged, bridge_diag = build_gmgn_session(base_session)
        if bridged is not None:
            return bridged, {"cf_bridge": "module", **(bridge_diag or {})}
    except Exception as exc:
        diag.update({"cf_bridge": "missing", "bridge_error": str(exc)[:120]})

    return base_session, diag


def fetch_gmgn_top_wallets(session: requests.Session, limit: int = GMGN_LIMIT) -> tuple[list[dict], dict]:
    """
    Fetch GMGN top-wallet leaderboard rows.
    Returns (wallet_rows, debug_info). Network/read-only only; no DB writes.
    """
    if not GMGN_TOP_WALLETS_URL:
        return [], {"status_code": 0, "error": "GMGN_TOP_WALLETS_URL_EMPTY", "keys": []}

    headers = {
        "Accept": "application/json,text/plain,*/*",
        "User-Agent": "Mozilla/5.0 SentinuityWalletScout/1.0",
        "Referer": "https://gmgn.ai/",
    }
    attempts = [
        {"limit": limit, "orderby": "pnl_7d", "direction": "desc"},
        {"limit": limit, "timeframe": "7d"},
        {"limit": limit},
        {},
    ]
    last_debug: dict = {"status_code": 0, "error": "NOT_RUN", "keys": []}
    session, bridge_diag = _prepare_gmgn_session(session)

    for params in attempts:
        try:
            merged_headers = dict(headers)
            # Preserve Cookie/User-Agent/Origin injected by _prepare_gmgn_session.
            for _h in ("Cookie", "User-Agent", "Origin", "Referer", "Accept"):
                if session.headers.get(_h):
                    merged_headers[_h] = session.headers.get(_h)
            resp = session.get(GMGN_TOP_WALLETS_URL, params=params, headers=merged_headers, timeout=(8, HTTP_TIMEOUT))
            last_debug = {"status_code": resp.status_code, "params": params, "keys": [], **bridge_diag}
            if resp.status_code != 200:
                last_debug["error"] = f"HTTP_{resp.status_code}"
                continue
            try:
                payload = resp.json()
            except Exception as exc:
                last_debug["error"] = f"JSON_{type(exc).__name__}"
                continue
            if isinstance(payload, dict):
                last_debug["keys"] = list(payload.keys())[:20]
            elif isinstance(payload, list):
                last_debug["keys"] = ["<list>"]
            rows = _find_wallet_records(payload, limit)
            if rows:
                last_debug["records"] = len(rows)
                return rows, last_debug
            last_debug["error"] = "NO_WALLET_ROWS_FOUND"
        except Exception as exc:
            last_debug = {"status_code": 0, "params": params, "error": f"{type(exc).__name__}: {str(exc)[:120]}", "keys": []}

    return [], last_debug


def _gmgn_profile_from_row(row: dict, rank: int) -> dict:
    """Map a schema-variable GMGN row into smart_wallet_profiles columns."""
    wallet = _extract_wallet_address(row)
    pnl = _first_value(row, ("pnl_7d", "realized_pnl_7d", "realized_pnl", "profit", "profit_7d", "pnl", "total_profit"), 0)
    win_rate = _first_value(row, ("win_rate", "winrate", "winRate", "winning_rate", "profit_rate"), 0)
    total_trades = _first_value(row, ("total_trades", "trades", "trade_count", "buy_count", "tx_count", "swap_count"), 0)
    p50 = _first_value(row, ("p50_x", "median_x", "median_winner_x", "avg_profit_multiplier", "avg_x"), 0)
    p70 = _first_value(row, ("p70_x", "p70", "percentile_70_x"), p50)
    p90 = _first_value(row, ("p90_x", "p90", "percentile_90_x", "max_x"), p70)
    hit2 = _first_value(row, ("hit_rate_2x", "profit_2x_rate", "rate_2x", "two_x_rate", "x2_rate"), 0)
    hit3 = _first_value(row, ("hit_rate_3x", "profit_3x_rate", "rate_3x", "three_x_rate", "x3_rate"), 0)
    hit5 = _first_value(row, ("hit_rate_5x", "profit_5x_rate", "rate_5x", "five_x_rate", "x5_rate"), 0)
    rug = _first_value(row, ("rug_exposure_rate", "rug_rate", "risk_rate", "loss_rate", "fail_rate"), 0)

    return {
        "wallet_address": wallet,
        "chain": "solana",
        "source_name": GMGN_SOURCE_NAME,
        "source_rank": rank,
        "realized_pnl": _safe_float(pnl, 0.0),
        "win_rate": _normalise_rate(win_rate, 0.0),
        "total_trades": _safe_int(total_trades, 0),
        "median_winner_x": _safe_float(p50, 0.0),
        "p50_x": _safe_float(p50, 0.0),
        "p70_x": _safe_float(p70, _safe_float(p50, 0.0)),
        "p90_x": _safe_float(p90, _safe_float(p70, 0.0)),
        "hit_rate_2x": _normalise_rate(hit2, 0.0),
        "hit_rate_3x": _normalise_rate(hit3, 0.0),
        "hit_rate_5x": _normalise_rate(hit5, 0.0),
        "late_entry_failure_rate": 0.0,
        "rug_exposure_rate": _normalise_rate(rug, 0.0),
        "last_seen": time.time(),
        "ingested_at": time.time(),
        "raw_json": json.dumps(row, ensure_ascii=False)[:6000],
    }


def _update_smart_wallet_source(status: str, *, seen: int = 0, inserted: int = 0, updated: int = 0, error: str = "") -> None:
    if not _SMART_WALLET_CONVICTION_AVAILABLE:
        return
    ensure_smart_wallet_schema(_smart_wallet_db_path())
    now = time.time()
    try:
        with _smart_wallet_connect(_smart_wallet_db_path()) as conn:
            last_success = now if status == "OK" else 0
            conn.execute("""
                INSERT INTO smart_wallet_sources(
                    source_name, status, last_run_at, last_success_at, last_error,
                    records_seen, records_inserted, records_updated
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_name) DO UPDATE SET
                    status=excluded.status,
                    last_run_at=excluded.last_run_at,
                    last_success_at=CASE WHEN excluded.last_success_at > 0 THEN excluded.last_success_at ELSE smart_wallet_sources.last_success_at END,
                    last_error=excluded.last_error,
                    records_seen=excluded.records_seen,
                    records_inserted=excluded.records_inserted,
                    records_updated=excluded.records_updated
            """, (GMGN_SOURCE_NAME, status, now, last_success, error[:250], seen, inserted, updated))
            conn.commit()
    except Exception as exc:
        log.debug("smart_wallet_sources update failed: %s", exc)


def _seed_profile_fingerprint(conn, profile: dict) -> bool:
    """
    Seed a profile-derived fingerprint from real GMGN leaderboard metrics when no
    trade-derived fingerprint can be built yet. This is explicitly OBSERVE/TRAINING
    metadata, not execution authority.
    """
    wallet = profile.get("wallet_address", "")
    if not wallet:
        return False
    try:
        existing = conn.execute(
            "SELECT wallet_quality_score, updated_at FROM wallet_entry_fingerprints WHERE wallet_address=? AND chain='solana'",
            (wallet,),
        ).fetchone()
        # Do not overwrite a better trade-derived fingerprint unless stale/empty.
        if existing and float(existing[0] or 0) > 0:
            return False

        hit2 = float(profile.get("hit_rate_2x") or 0)
        hit3 = float(profile.get("hit_rate_3x") or 0)
        hit5 = float(profile.get("hit_rate_5x") or 0)
        win = float(profile.get("win_rate") or 0)
        rug = float(profile.get("rug_exposure_rate") or 0)
        p50 = float(profile.get("p50_x") or profile.get("median_winner_x") or 0)
        total = int(profile.get("total_trades") or 0)

        quality = 0.0
        quality += min(35.0, hit2 * 35.0)
        quality += min(20.0, hit3 * 20.0)
        quality += min(15.0, hit5 * 15.0)
        quality += min(15.0, win * 15.0)
        quality += 10.0 if rug <= 0.10 else 5.0 if rug <= 0.25 else 0.0
        quality += 5.0 if total >= 20 else 0.0
        quality = max(0.0, min(100.0, quality))
        copyability = max(0.0, min(100.0, 100.0 - (rug * 100.0)))
        if rug > 0.30:
            style = "PROFILE_RISKY_OR_BUNDLE_EXPOSED"
        elif hit2 >= 0.45 or p50 >= 2.0:
            style = "PROFILE_EARLY_WINNER"
        else:
            style = "PROFILE_OBSERVE_ONLY"
        reasons = [
            "GMGN_PROFILE_SEED",
            f"rank={profile.get('source_rank')}",
            f"quality={quality:.0f}",
            f"copyable={copyability:.0f}",
        ]
        conn.execute("""
            INSERT OR REPLACE INTO wallet_entry_fingerprints(
                wallet_address, chain, wallet_style, wallet_quality_score,
                copyability_score, median_safe_x, hit_rate_2x, hit_rate_3x,
                hit_rate_5x, late_copy_failure_rate, rug_exposure_rate,
                updated_at, reasons_json
            ) VALUES (?, 'solana', ?, ?, ?, ?, ?, ?, ?, 0, ?, ?, ?)
        """, (
            wallet, style, quality, copyability, p50, hit2, hit3, hit5, rug,
            time.time(), json.dumps(reasons),
        ))
        return True
    except Exception as exc:
        log.debug("profile fingerprint seed failed for %s: %s", wallet[:12], exc)
        return False


def ingest_gmgn_top_wallets(session: requests.Session, limit: int = GMGN_LIMIT) -> dict:
    """
    Import GMGN top trader wallet profiles into smart wallet tables.
    OBSERVE/TRAINING only. Returns run stats.
    """
    stats = {"enabled": GMGN_ENABLED, "seen": 0, "inserted": 0, "updated": 0, "fingerprints": 0, "status": "SKIPPED"}
    if not GMGN_ENABLED:
        return stats
    if not _SMART_WALLET_CONVICTION_AVAILABLE:
        log.warning("GMGN smart wallet ingestion skipped — smart_wallet_conviction unavailable")
        return stats

    ensure_smart_wallet_schema(_smart_wallet_db_path())
    _update_smart_wallet_source("RUNNING")

    rows, debug = fetch_gmgn_top_wallets(session, limit=limit)
    if not rows:
        err = str(debug.get("error") or "NO_ROWS")
        # A GMGN Cloudflare/API refusal must not erase or silently disable the
        # restored, previously observed wallet roster. Keep it explicitly
        # observe-only and report the retained profile/fingerprint counts.
        retained_profiles = retained_fingerprints = 0
        try:
            with _smart_wallet_connect(_smart_wallet_db_path()) as conn:
                retained_profiles = int(conn.execute(
                    "SELECT COUNT(*) FROM smart_wallet_profiles WHERE chain='solana'"
                ).fetchone()[0] or 0)
                retained_fingerprints = int(conn.execute(
                    "SELECT COUNT(*) FROM wallet_entry_fingerprints WHERE chain='solana'"
                ).fetchone()[0] or 0)
        except Exception as exc:
            log.debug("retained roster count failed: %s", exc)
        status = "RESTORED_ROSTER_ACTIVE" if retained_profiles > 0 else "FAILED"
        _update_smart_wallet_source(status, seen=retained_profiles, error=err)
        stats.update({
            "status": status, "error": err, "debug": debug,
            "retained_profiles": retained_profiles,
            "retained_fingerprints": retained_fingerprints,
            "mode": "OBSERVE_ONLY", "fresh_gmgn": False,
        })
        if retained_profiles > 0:
            log.warning("GMGN fresh intake unavailable (%s); retained restored roster profiles=%d fingerprints=%d OBSERVE_ONLY",
                        err, retained_profiles, retained_fingerprints)
        else:
            log.warning("GMGN ingestion failed: %s", err)
        return stats

    profiles = [_gmgn_profile_from_row(row, rank=i + 1) for i, row in enumerate(rows)]
    profiles = [p for p in profiles if p.get("wallet_address")]
    stats["seen"] = len(profiles)

    inserted = updated = fingerprints = 0
    try:
        with _smart_wallet_connect(_smart_wallet_db_path()) as conn:
            for profile in profiles:
                existed = conn.execute("""
                    SELECT 1 FROM smart_wallet_profiles
                    WHERE wallet_address=? AND chain='solana' AND source_name=? LIMIT 1
                """, (profile["wallet_address"], GMGN_SOURCE_NAME)).fetchone()
                conn.execute("""
                    INSERT OR REPLACE INTO smart_wallet_profiles(
                        wallet_address, chain, source_name, source_rank, realized_pnl,
                        win_rate, total_trades, median_winner_x, p50_x, p70_x, p90_x,
                        hit_rate_2x, hit_rate_3x, hit_rate_5x, late_entry_failure_rate,
                        rug_exposure_rate, last_seen, ingested_at, raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    profile["wallet_address"], profile["chain"], profile["source_name"], profile["source_rank"],
                    profile["realized_pnl"], profile["win_rate"], profile["total_trades"], profile["median_winner_x"],
                    profile["p50_x"], profile["p70_x"], profile["p90_x"], profile["hit_rate_2x"], profile["hit_rate_3x"],
                    profile["hit_rate_5x"], profile["late_entry_failure_rate"], profile["rug_exposure_rate"],
                    profile["last_seen"], profile["ingested_at"], profile["raw_json"],
                ))
                if existed:
                    updated += 1
                else:
                    inserted += 1

                # Append-only weekly performance truth. This survives roster
                # replacement and powers the 7D-first wallet comparison graph.
                # captured_at is rounded to the minute to make retries idempotent.
                captured_at = float(int(time.time() // 60) * 60)
                conn.execute("""
                    INSERT OR IGNORE INTO smart_wallet_performance_snapshots(
                        wallet_address, captured_at, period, source_name, source_rank,
                        realized_pnl, win_rate, total_trades, median_winner_x,
                        p50_x, p70_x, p90_x, hit_rate_2x, hit_rate_3x, hit_rate_5x, raw_json
                    ) VALUES (?, ?, '7d', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    profile["wallet_address"], captured_at, profile["source_name"],
                    profile["source_rank"], profile["realized_pnl"], profile["win_rate"],
                    profile["total_trades"], profile["median_winner_x"], profile["p50_x"],
                    profile["p70_x"], profile["p90_x"], profile["hit_rate_2x"],
                    profile["hit_rate_3x"], profile["hit_rate_5x"], profile["raw_json"],
                ))

                if len(profiles) <= GMGN_FINGERPRINT_LIMIT or profile["source_rank"] <= GMGN_FINGERPRINT_LIMIT:
                    if _seed_profile_fingerprint(conn, profile):
                        fingerprints += 1
            conn.commit()

        # Also attempt trade-derived fingerprints if trade history exists locally.
        for profile in profiles[:GMGN_FINGERPRINT_LIMIT]:
            try:
                fp = build_fingerprint_for_wallet(profile["wallet_address"], _smart_wallet_db_path())
                if fp:
                    fingerprints += 1
            except Exception:
                pass

        _update_smart_wallet_source("OK", seen=len(profiles), inserted=inserted, updated=updated)
        stats.update({"status": "OK", "inserted": inserted, "updated": updated, "fingerprints": fingerprints, "debug": debug})
        log.info("GMGN ingestion OK — %d profiles (%d inserted, %d updated, %d fingerprints)", len(profiles), inserted, updated, fingerprints)
        return stats
    except Exception as exc:
        err = f"{type(exc).__name__}: {str(exc)[:180]}"
        _update_smart_wallet_source("ERROR", seen=len(profiles), inserted=inserted, updated=updated, error=err)
        stats.update({"status": "ERROR", "error": err})
        log.warning("GMGN ingestion error: %s", err)
        return stats


def should_run_gmgn_ingestion() -> bool:
    if not GMGN_ENABLED:
        return False
    if not _SMART_WALLET_CONVICTION_AVAILABLE:
        return False
    try:
        ensure_smart_wallet_schema(_smart_wallet_db_path())
        with _smart_wallet_connect(_smart_wallet_db_path()) as conn:
            row = conn.execute(
                "SELECT last_run_at FROM smart_wallet_sources WHERE source_name=?",
                (GMGN_SOURCE_NAME,),
            ).fetchone()
            last_run = float(row[0] or 0) if row else 0.0
        return (time.time() - last_run) >= GMGN_INTERVAL_SECONDS
    except Exception:
        return True

# ── DB OPERATIONS ─────────────────────────────────────────────────────────────

def get_active_wallets() -> list[dict]:
    """Fetch active watched wallets across both legacy and current schemas.

    The restored runtime can contain either the old columns
    (active/win_rate/added_at) or the newer scout columns
    (status/profitable_count/rug_count/last_seen_at).  Build the SELECT from
    PRAGMA truth so a missing optional column can never disable the roster.
    """
    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            existing = {str(r[1]) for r in conn.execute(
                "PRAGMA table_info(watched_wallets)"
            ).fetchall()}
            if "wallet_address" not in existing:
                return []

            label_expr = "label" if "label" in existing else "'' AS label"
            if "profit_score" in existing:
                score_expr = "COALESCE(profit_score,0) AS profit_score"
                order_expr = "COALESCE(profit_score,0)"
            elif "win_rate" in existing:
                score_expr = "COALESCE(win_rate,0) AS profit_score"
                order_expr = "COALESCE(win_rate,0)"
            elif "wallet_quality_score" in existing:
                score_expr = "COALESCE(wallet_quality_score,0) AS profit_score"
                order_expr = "COALESCE(wallet_quality_score,0)"
            else:
                score_expr = "0.0 AS profit_score"
                order_expr = "0.0"

            trade_expr = "COALESCE(trade_count,0) AS trade_count" if "trade_count" in existing else "0 AS trade_count"
            profitable_expr = ("COALESCE(profitable_count,0) AS profitable_count" if "profitable_count" in existing
                               else "0 AS profitable_count")
            rug_expr = "COALESCE(rug_count,0) AS rug_count" if "rug_count" in existing else "0 AS rug_count"

            if "status" in existing:
                active_where = "UPPER(COALESCE(status,'ACTIVE'))='ACTIVE'"
            elif "active" in existing:
                active_where = "COALESCE(active,1)=1"
            else:
                active_where = "1=1"

            sql = f"""
                SELECT wallet_address, {label_expr}, {score_expr}, {trade_expr},
                       {profitable_expr}, {rug_expr}
                FROM watched_wallets
                WHERE {active_where}
                ORDER BY {order_expr} DESC, wallet_address ASC
                LIMIT ?
            """
            rows = conn.execute(sql, (MAX_WALLETS,)).fetchall()
        return [dict(r) for r in rows]
    except Exception as e:
        log.warning("get_active_wallets failed: %s", e)
        return []


def already_observed(wallet: str, tx_sig: str) -> bool:
    """Check if we already recorded an observation for this tx."""
    try:
        with get_connection() as conn:
            row = conn.execute(
                "SELECT 1 FROM wallet_pattern_observations WHERE wallet_address=? AND tx_hash=? LIMIT 1",
                (wallet, tx_sig)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def record_observation(
    wallet: str,
    mint: str,
    tx_sig: str,
    block_time: int,
    launch_time: Optional[int],
    token_delta: float,
    entry_price_usd: Optional[float] = None,
    entry_liq_usd: Optional[float] = None,
    entry_mcap_usd: Optional[float] = None,
) -> bool:
    """
    Write one wallet acquisition observation to wallet_pattern_observations.
    Calculates time_from_launch_sec from block_time - launch_time per Grok's formula:
      time_from_launch_sec = first_buy_tx.result.blockTime - mint_init_tx.result.blockTime
    """
    try:
        time_from_launch = None
        if launch_time and block_time and launch_time > 0:
            time_from_launch = float(block_time - launch_time)
            if time_from_launch < 0:
                time_from_launch = None  # data anomaly

        now = time.time()
        with get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO wallet_pattern_observations (
                    wallet_address, mint_address, tx_hash,
                    observed_at, time_from_launch_sec,
                    entry_price_usd, entry_liq_usd, entry_mcap_usd,
                    token_balance_delta, outcome, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
            """, (
                wallet, mint, tx_sig,
                float(block_time), time_from_launch,
                entry_price_usd, entry_liq_usd, entry_mcap_usd,
                float(token_delta),
            ))
            conn.commit()
        return True
    except Exception as e:
        log.debug("record_observation failed: %s", e)
        return False


def update_wallet_stats(wallet: str, new_obs_count: int) -> None:
    """Refresh aggregated stats for a watched wallet."""
    try:
        now = time.time()
        with get_connection() as conn:
            stats = conn.execute("""
                SELECT
                    COUNT(*) as total,
                    AVG(time_from_launch_sec) as avg_timing,
                    AVG(entry_liq_usd) as avg_liq,
                    AVG(entry_mcap_usd) as avg_mcap,
                    SUM(CASE WHEN outcome='profitable' THEN 1 ELSE 0 END) as profitable,
                    SUM(CASE WHEN outcome='rug' THEN 1 ELSE 0 END) as rug_count
                FROM wallet_pattern_observations
                WHERE wallet_address = ?
            """, (wallet,)).fetchone()

            if stats and stats["total"] > 0:
                conn.execute("""
                    UPDATE watched_wallets
                    SET trade_count = ?,
                        profitable_count = ?,
                        rug_count = ?,
                        avg_time_from_launch_sec = ?,
                        avg_entry_liq_usd = ?,
                        avg_entry_mcap_usd = ?,
                        last_seen_at = ?,
                        profit_score = ROUND(
                            CAST(? AS REAL) / MAX(CAST(? AS REAL), 1), 4
                        )
                    WHERE wallet_address = ?
                """, (
                    stats["total"],
                    stats["profitable"] or 0,
                    stats["rug_count"] or 0,
                    stats["avg_timing"],
                    stats["avg_liq"],
                    stats["avg_mcap"],
                    now,
                    stats["profitable"] or 0,
                    stats["total"],
                    wallet,
                ))
                conn.commit()
    except Exception as e:
        log.debug("update_wallet_stats failed for %s: %s", wallet, e)


# ── MAIN SCOUT LOOP ───────────────────────────────────────────────────────────

def scout_wallet(session: requests.Session, wallet_info: dict) -> int:
    """
    Scout one wallet. Returns count of new observations recorded.
    Conservative RPC usage:
      1 getSignaturesForAddress call (cheap)
      N getTransaction calls only for txs that show token acquisitions (selective)
    """
    wallet = wallet_info["wallet_address"]
    label  = wallet_info.get("label") or wallet[:12]
    new_obs = 0

    signatures = get_signatures_for_address(session, wallet)
    if not signatures:
        log.debug("No signatures for %s (%s)", label, wallet[:12])
        return 0

    for sig in signatures:
        if already_observed(wallet, sig):
            continue

        tx = get_transaction_parsed(session, sig)
        if not tx:
            continue

        acquisitions = extract_token_acquisitions(wallet, tx)
        if not acquisitions:
            continue

        for acq in acquisitions:
            mint       = acq["mint"]
            block_time = acq["block_time"]
            delta      = acq["delta"]

            # Get mint launch time (cached after first lookup)
            launch_time = get_mint_launch_time(session, mint)

            # Record observation
            recorded = record_observation(
                wallet=wallet,
                mint=mint,
                tx_sig=sig,
                block_time=block_time,
                launch_time=launch_time,
                token_delta=float(delta),
            )
            if recorded:
                new_obs += 1
                timing_str = (
                    f"{int(block_time - launch_time)}s after launch"
                    if launch_time else "timing unknown"
                )
                log.info(
                    "WALLET OBS: %s acquired %s... %s (delta=%d)",
                    label, mint[:12], timing_str, delta
                )

    if new_obs > 0:
        update_wallet_stats(wallet, new_obs)

    return new_obs


def run() -> None:
    log.info("WALLET SCOUT ONLINE — on-chain intelligence + smart-wallet profile layer active")
    if RPC_URL:
        log.info("RPC provider: %s", RPC_PROVIDER or "UNKNOWN")
        log.info("RPC: %s...", RPC_URL[:40])
        update_heartbeat(SERVICE_NAME, "ALIVE", f"Wallet scout online via {RPC_PROVIDER or 'RPC'}")
    else:
        log.warning("No RPC endpoint set — manual watched_wallets scouting disabled, GMGN profile intake can still run")
        update_heartbeat(SERVICE_NAME, "DEGRADED", "No RPC configured — GMGN profile intake only")

    session = requests.Session()
    session.headers.update({"Content-Type": "application/json"})

    while True:
        try:
            gmgn_note = ""
            if should_run_gmgn_ingestion():
                gmgn_stats = ingest_gmgn_top_wallets(session, GMGN_LIMIT)
                gmgn_note = f" | GMGN {gmgn_stats.get('status')} seen={gmgn_stats.get('seen', 0)} fp={gmgn_stats.get('fingerprints', 0)}"

            if not RPC_URL:
                update_heartbeat(SERVICE_NAME, "DEGRADED", f"No RPC configured — GMGN profile intake only{gmgn_note}")
                time.sleep(POLL_INTERVAL)
                continue

            wallets = get_active_wallets()

            if not wallets:
                log.info("No manual wallets in watchlist. GMGN profile intake status:%s", gmgn_note or " not due")
                update_heartbeat(SERVICE_NAME, "ALIVE", f"No manual wallets configured — GMGN standing task active{gmgn_note}")
                time.sleep(POLL_INTERVAL)
                continue

            total_new = 0
            for wallet_info in wallets:
                try:
                    new = scout_wallet(session, wallet_info)
                    total_new += new
                    # Brief pause between wallets to be kind to RPC
                    time.sleep(1.0)
                except Exception as e:
                    log.warning("scout_wallet failed for %s: %s",
                                wallet_info.get("wallet_address", "?")[:12], e)

            update_heartbeat(
                SERVICE_NAME, "ALIVE",
                f"Scouted {len(wallets)} wallets — {total_new} new observations{gmgn_note}",
                work_processed=total_new,
                last_success_at=time.time() if total_new > 0 else None,
            )
            log.info("Scout cycle complete: %d wallets, %d new observations%s", len(wallets), total_new, gmgn_note)

        except Exception as e:
            log.error("Wallet scout cycle error: %s", e)
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:120])

        time.sleep(POLL_INTERVAL)


# ── CLI: ADD WALLET ───────────────────────────────────────────────────────────

def add_wallet(address: str, label: str = "") -> None:
    """Add a wallet to the watchlist via CLI."""
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT OR IGNORE INTO watched_wallets (wallet_address, label, first_seen_at)
                VALUES (?, ?, ?)
            """, (address, label or address[:12], time.time()))
            conn.commit()
        print(f"Added wallet: {address} ({label or 'no label'})")
        print("Wallet scout will begin observing it on next cycle.")
    except Exception as e:
        print(f"Failed to add wallet: {e}")


if __name__ == "__main__":
    import sys as _sys
    if len(_sys.argv) >= 3 and _sys.argv[1] == "--add":
        address = _sys.argv[2]
        label   = _sys.argv[3] if len(_sys.argv) >= 4 else ""
        add_wallet(address, label)
    elif len(_sys.argv) >= 2 and _sys.argv[1] == "--gmgn-once":
        _session = requests.Session()
        _stats = ingest_gmgn_top_wallets(_session, GMGN_LIMIT)
        print(json.dumps(_stats, indent=2, default=str))
    elif len(_sys.argv) >= 2 and _sys.argv[1] == "--gmgn-fetch-test":
        _session = requests.Session()
        _rows, _debug = fetch_gmgn_top_wallets(_session, min(GMGN_LIMIT, 5))
        print(json.dumps({
            "debug": _debug,
            "count": len(_rows),
            "first_wallet": _extract_wallet_address(_rows[0]) if _rows else "",
            "first_keys": list(_rows[0].keys())[:30] if _rows else [],
        }, indent=2, default=str))
    else:
        run()
