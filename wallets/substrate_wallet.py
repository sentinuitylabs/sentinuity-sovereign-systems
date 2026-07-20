from __future__ import annotations

import time
from typing import Any, Dict, List
from .substrate_wallet_schema import connect, ensure_schema, cfg_get, cfg_bool, cfg_float, cfg_int, cfg_set


def _rows(con, sql: str, args=()) -> List[dict]:
    try:
        return [dict(r) for r in con.execute(sql, args).fetchall()]
    except Exception:
        return []


def _wallet_ready_for_family(address: str, family: str) -> bool:
    a = (address or "").strip()
    f = (family or "").lower()
    if not a:
        return False
    if f in ("evm", "coinbase", "metamask"):
        return a.startswith("0x") and len(a) >= 42
    if f in ("phantom", "solana"):
        return not a.startswith("0x") and len(a) >= 32
    return bool(a)


def refresh_wallet_state() -> Dict[str, Any]:
    ensure_schema()
    con = connect()
    try:
        now = time.time()
        family = str(cfg_get(con, "SUBSTRATE_LIVE_WALLET_FAMILY", "evm") or "evm")
        provider = str(cfg_get(con, "SUBSTRATE_LIVE_PROVIDER", "coinbase_wallet") or "coinbase_wallet")
        address = str(cfg_get(con, "SUBSTRATE_LIVE_WALLET_ADDRESS", "") or "")
        chain = str(cfg_get(con, "SUBSTRATE_LIVE_DEFAULT_CHAIN", "base") or "base")
        live_enabled = cfg_bool(con, "SUBSTRATE_LIVE_ENABLED", False)
        live_armed = cfg_bool(con, "SUBSTRATE_LIVE_ARMED", False)
        max_size = cfg_float(con, "SUBSTRATE_LIVE_MAX_POSITION_USD", 25.0)
        max_open = cfg_int(con, "SUBSTRATE_LIVE_MAX_OPEN", 1)
        wallet_ready = _wallet_ready_for_family(address, family)
        if not live_enabled:
            mode = "PAPER"
            block = "SUBSTRATE_LIVE_ENABLED=0"
        elif not live_armed:
            mode = "LIVE_CONFIGURED"
            block = "SUBSTRATE_LIVE_ARMED=0"
        elif not wallet_ready:
            mode = "LIVE_BLOCKED"
            block = "wallet address missing or wrong family"
        else:
            mode = "LIVE_MANUAL_SIGN_READY"
            block = "manual signature required; autosend disabled"
        con.execute(
            "UPDATE substrate_wallet_state SET updated_at=?, mode=?, wallet_family=?, provider=?, wallet_address=?, chain=?, network=?, "
            "live_enabled=?, live_armed=?, live_max_position_usd=?, live_max_open=?, live_block_reason=? WHERE id=1",
            (now, mode, family, provider, address, chain, "mainnet", int(live_enabled), int(live_armed), max_size, max_open, block),
        )
        providers = [
            ("phantom", "solana", provider == "phantom" and wallet_ready),
            ("coinbase_wallet", "evm", provider == "coinbase_wallet" and wallet_ready),
            ("metamask", "evm", provider == "metamask" and wallet_ready),
        ]
        for name, p_mode, ready in providers:
            con.execute(
                "INSERT INTO substrate_provider_health(provider,mode,ready,last_error,updated_at) VALUES(?,?,?,?,?) "
                "ON CONFLICT(provider) DO UPDATE SET mode=excluded.mode,ready=excluded.ready,last_error=excluded.last_error,updated_at=excluded.updated_at",
                (name, p_mode, 1 if ready else 0, "" if ready else "not selected/connected", now),
            )
        con.commit()
        return {"mode": mode, "provider": provider, "wallet_family": family, "wallet_address": address, "live_block_reason": block}
    finally:
        con.close()


def snapshot() -> Dict[str, Any]:
    ensure_schema()
    con = connect()
    try:
        state = dict(con.execute("SELECT * FROM substrate_wallet_state WHERE id=1").fetchone() or {})
        cash = cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0)
        start = cfg_float(con, "SUBSTRATE_PAPER_BALANCE_USD", cash)
        live_orders = _rows(con, "SELECT * FROM substrate_live_orders ORDER BY created_at DESC LIMIT 20")
        return {
            "state": state,
            "balance": {"cash_usd": cash, "start_usd": start, "live_orders": len(live_orders)},
            "votes": _rows(con, "SELECT * FROM substrate_council_votes ORDER BY created_at DESC LIMIT 50"),
            "opportunities": _rows(con, "SELECT * FROM substrate_opportunities ORDER BY created_at DESC LIMIT 50"),
            "open_positions": _rows(con, "SELECT * FROM substrate_positions WHERE state='OPEN' ORDER BY opened_at DESC LIMIT 50"),
            "provider_health": _rows(con, "SELECT * FROM substrate_provider_health ORDER BY updated_at DESC LIMIT 20"),
            "audit": _rows(con, "SELECT * FROM substrate_execution_audit ORDER BY created_at DESC LIMIT 50"),
            "live_orders": live_orders,
        }
    finally:
        con.close()
