"""Permanent real Phantom/Solana wallet synchronisation service.

Every cycle it derives the public address from ``SOLANA_PRIVATE_KEY``, reads the
confirmed on-chain SOL balance, obtains a fresh SOL/USD price and publishes one
canonical wallet snapshot.  It runs in LIVE *and* DUAL mode and never mutates
paper equity or a paper starting balance.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Optional

from services.live_wallet_truth import (
    record_live_wallet_error,
    write_live_wallet_truth,
)

log = logging.getLogger("live_wallet_sync")
ROOT = Path(__file__).resolve().parent.parent
SOL_MINT = "So11111111111111111111111111111111111111112"
SYNC_INTERVAL = max(15, int(float(os.getenv("LIVE_WALLET_SYNC_INTERVAL_SEC", "60"))))
GAS_RESERVE = max(0.0, float(os.getenv("LIVE_GAS_RESERVE_SOL", "0.05")))
_started = False
_start_lock = threading.Lock()


def _load_env() -> None:
    try:
        from dotenv import load_dotenv
        load_dotenv(ROOT / ".env", override=False)
    except Exception:
        pass


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "live", "dual"}


def live_lane_enabled() -> bool:
    """True when the funded lane is requested in LIVE or DUAL operation."""
    env_mode = os.getenv("TRADING_MODE", "")
    env_flags = any(
        _truthy(os.getenv(k, "0"))
        for k in (
            "DUAL_MODE_ENABLED",
            "LIVE_TRADING_ENABLED",
            "LIVE_MODE_B_ENABLED",
            "LIVE_ARMED",
        )
    )
    try:
        from core.schema import get_connection
        with get_connection() as db:
            rows = db.execute(
                "SELECT key,value FROM system_config WHERE key IN ("
                "'TRADING_MODE','DUAL_MODE_ENABLED','LIVE_TRADING_ENABLED',"
                "'LIVE_MODE_B_ENABLED','LIVE_ARMED')"
            ).fetchall()
            cfg = {str(r["key"]): str(r["value"] or "") for r in rows}
        mode = cfg.get("TRADING_MODE", env_mode).strip().lower()
        return mode in {"live", "dual"} or env_flags or any(
            _truthy(cfg.get(k, "0"))
            for k in (
                "DUAL_MODE_ENABLED",
                "LIVE_TRADING_ENABLED",
                "LIVE_MODE_B_ENABLED",
                "LIVE_ARMED",
            )
        )
    except Exception:
        return str(env_mode).strip().lower() in {"live", "dual"} or env_flags


def _derive_wallet_address() -> tuple[str, Any]:
    _load_env()
    private_key = os.getenv("SOLANA_PRIVATE_KEY", "").strip()
    if not private_key:
        raise RuntimeError("SOLANA_PRIVATE_KEY_NOT_SET")
    import base58
    from solders.keypair import Keypair
    raw = base58.b58decode(private_key)
    if len(raw) == 64:
        kp = Keypair.from_bytes(raw)
    elif len(raw) == 32:
        kp = Keypair.from_seed(raw)
    else:
        raise RuntimeError(f"SOLANA_PRIVATE_KEY_LENGTH_{len(raw)}")
    return str(kp.pubkey()), kp


def _rpc_url() -> str:
    _load_env()
    return (
        os.getenv("QUICKNODE_RPC")
        or os.getenv("SOLANA_RPC_URL")
        or os.getenv("HELIUS_RPC")
        or ""
    ).strip()


def _fetch_sol_balance(address: str) -> float:
    import requests
    rpc = _rpc_url()
    if not rpc:
        raise RuntimeError("SOLANA_RPC_NOT_SET")
    response = requests.post(
        rpc,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "method": "getBalance",
            "params": [address, {"commitment": "confirmed"}],
        },
        timeout=8,
    )
    response.raise_for_status()
    payload = response.json()
    if payload.get("error"):
        raise RuntimeError(f"RPC_GET_BALANCE:{payload['error']}")
    return float(payload["result"]["value"]) / 1_000_000_000.0


def _fetch_sol_usd() -> tuple[float, str]:
    import requests
    _load_env()
    jkey = os.getenv("JUPITER_PRICE_API_KEY", "").strip()
    attempts: list[str] = []

    try:
        headers = {"x-api-key": jkey} if jkey else {}
        r = requests.get(
            "https://api.jup.ag/price/v3",
            params={"ids": SOL_MINT},
            headers=headers,
            timeout=6,
        )
        r.raise_for_status()
        price = float((r.json().get(SOL_MINT) or {}).get("usdPrice") or 0.0)
        if price > 0:
            return price, "JUPITER_V3"
        attempts.append("JUPITER_ZERO")
    except Exception as exc:
        attempts.append(f"JUPITER:{exc}")

    try:
        r = requests.get(
            f"https://api.dexscreener.com/latest/dex/tokens/{SOL_MINT}",
            timeout=6,
        )
        r.raise_for_status()
        pairs = r.json().get("pairs") or []
        prices = [float(p.get("priceUsd") or 0.0) for p in pairs]
        prices = [p for p in prices if p > 0]
        if prices:
            prices.sort()
            return prices[len(prices) // 2], "DEXSCREENER_MEDIAN"
        attempts.append("DEXSCREENER_ZERO")
    except Exception as exc:
        attempts.append(f"DEXSCREENER:{exc}")

    try:
        from core.schema import get_connection
        with get_connection() as db:
            row = db.execute(
                "SELECT value FROM system_config WHERE key='SOL_PRICE_USD'"
            ).fetchone()
            price = float(row["value"] or 0.0) if row else 0.0
        if price > 0:
            return price, "SYSTEM_CONFIG_SOL_PRICE_USD"
    except Exception as exc:
        attempts.append(f"DB_PRICE:{exc}")

    raise RuntimeError("SOL_USD_UNAVAILABLE|" + "|".join(attempts)[-400:])


def fetch_real_wallet_snapshot() -> dict[str, Any]:
    address, _ = _derive_wallet_address()
    sol_balance = _fetch_sol_balance(address)
    sol_usd, price_source = _fetch_sol_usd()
    return {
        "wallet_address": address,
        "sol_balance": sol_balance,
        "gas_reserve_sol": GAS_RESERVE,
        "sol_usd_price": sol_usd,
        "source": f"CHAIN_RPC+{price_source}",
    }


def fetch_real_wallet_usd() -> Optional[float]:
    """Compatibility helper returning total chain wallet USD."""
    try:
        snap = fetch_real_wallet_snapshot()
        return float(snap["sol_balance"]) * float(snap["sol_usd_price"])
    except Exception as exc:
        log.debug("[WALLET_SYNC] fetch failed: %s", exc)
        return None


def sync_once(*, force: bool = False) -> bool:
    if not force and not live_lane_enabled():
        return False
    try:
        snap = fetch_real_wallet_snapshot()
        truth = write_live_wallet_truth(**snap)
        log.info(
            "[WALLET_SYNC] %s %.6f SOL × $%.2f = $%.2f total; $%.2f available",
            str(truth["wallet_address"])[:8],
            truth["sol_balance"],
            truth["sol_usd_price"],
            truth["balance_usd"],
            truth["available_usd"],
        )
        return True
    except Exception as exc:
        record_live_wallet_error(str(exc))
        log.warning("[WALLET_SYNC] sync failed: %s", exc)
        return False


def _sync_loop() -> None:
    log.info("[WALLET_SYNC] canonical sync thread started interval=%ss", SYNC_INTERVAL)
    while True:
        try:
            sync_once()
        except Exception as exc:
            log.warning("[WALLET_SYNC] loop error: %s", exc)
        time.sleep(SYNC_INTERVAL)


def start_live_wallet_sync() -> threading.Thread:
    global _started
    with _start_lock:
        if _started:
            for thread in threading.enumerate():
                if thread.name == "live_wallet_sync" and thread.is_alive():
                    return thread
        thread = threading.Thread(
            target=_sync_loop,
            daemon=True,
            name="live_wallet_sync",
        )
        thread.start()
        _started = True
        return thread


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(message)s")
    ok = sync_once(force=True)
    raise SystemExit(0 if ok else 1)
