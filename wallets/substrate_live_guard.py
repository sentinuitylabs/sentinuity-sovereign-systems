from __future__ import annotations

import json
import time
from typing import Any, Dict
from .substrate_wallet_schema import connect, ensure_schema, cfg_bool, cfg_float, cfg_int, cfg_get

TRUTHY = {"1", "true", "yes", "on", "enabled", "armed", "live"}


def _is_demo_or_simulated(raw: Any) -> bool:
    text = str(raw or "").lower()
    return "simulated" in text or "demo" in text or "mock_live" in text


def _audit(con, allowed: bool, reason: str, opp: dict | None = None, source: str = "live_guard") -> None:
    opp = opp or {}
    con.execute(
        "INSERT INTO substrate_execution_audit(created_at,allowed,reason,source,asset_symbol,chain,confidence,raw_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            time.time(), 1 if allowed else 0, reason, source,
            opp.get("asset_symbol", ""), opp.get("chain", ""), float(opp.get("confidence") or 0),
            json.dumps({"opportunity_id": opp.get("id"), "mode": "LIVE_MANUAL_SIGN"}, sort_keys=True),
        ),
    )


def _has_paper_shadow(con, opp: dict) -> bool:
    oid = int(opp.get("id") or 0)
    sym = str(opp.get("asset_symbol") or "")
    if oid and con.execute("SELECT 1 FROM substrate_positions WHERE opportunity_id=? AND mode='PAPER' LIMIT 1", (oid,)).fetchone():
        return True
    if sym and con.execute("SELECT 1 FROM substrate_paper_positions WHERE asset_symbol=? AND status IN ('OPEN','CLOSED') LIMIT 1", (sym,)).fetchone():
        return True
    return False


def stage_live_order_from_opportunity(opportunity_id: int) -> Dict[str, Any]:
    """Stage a live Substrate order for manual wallet signature.

    This deliberately does NOT hold a private key and does NOT autosend. The first
    live-money test is a READY_FOR_MANUAL_SIGN row plus an execution audit trail;
    the wallet extension still has to present and sign the transaction.
    """
    ensure_schema()
    con = connect()
    try:
        row = con.execute("SELECT * FROM substrate_opportunities WHERE id=?", (int(opportunity_id),)).fetchone()
        if not row:
            return {"ok": False, "reason": "opportunity_not_found"}
        opp = dict(row)
        now = time.time()
        live_enabled = cfg_bool(con, "SUBSTRATE_LIVE_ENABLED", False)
        live_armed = cfg_bool(con, "SUBSTRATE_LIVE_ARMED", False)
        if not live_enabled or not live_armed:
            reason = "live_disabled_or_not_armed"
            _audit(con, False, reason, opp)
            con.commit()
            return {"ok": False, "reason": reason}
        wallet = str(cfg_get(con, "SUBSTRATE_LIVE_WALLET_ADDRESS", "") or "").strip()
        provider = str(cfg_get(con, "SUBSTRATE_LIVE_PROVIDER", "coinbase_wallet") or "coinbase_wallet").strip()
        family = str(cfg_get(con, "SUBSTRATE_LIVE_WALLET_FAMILY", "evm") or "evm").strip().lower()
        chain = str(opp.get("chain") or "").strip().lower()
        allowed_chains = {x.strip().lower() for x in str(cfg_get(con, "SUBSTRATE_LIVE_ALLOWED_CHAINS", "base,ethereum,arbitrum,optimism,polygon")).split(",") if x.strip()}
        if not wallet:
            reason = "missing_live_wallet_address"
        elif chain not in allowed_chains:
            reason = f"chain_not_allowed:{chain}"
        elif family in ("evm", "coinbase", "metamask") and chain == "solana":
            reason = "evm_wallet_cannot_sign_solana_route"
        elif family in ("phantom", "solana") and chain != "solana":
            reason = "phantom_solana_lane_cannot_sign_evm_route"
        elif float(opp.get("confidence") or 0) < cfg_float(con, "SUBSTRATE_LIVE_MIN_COUNCIL_CONVICTION", 0.80):
            reason = "below_live_confidence_floor"
        elif float(opp.get("expected_edge") or 0) <= 0:
            reason = "expected_edge_not_positive"
        elif float(opp.get("risk_score") or 1) > cfg_float(con, "SUBSTRATE_LIVE_MAX_RISK_SCORE", 0.55):
            reason = "risk_score_too_high"
        elif float(opp.get("liquidity_usd") or 0) < cfg_float(con, "SUBSTRATE_LIVE_MIN_LIQUIDITY_USD", 1_000_000):
            reason = "liquidity_below_live_floor"
        elif now - float(opp.get("price_updated_at") or 0) > cfg_float(con, "SUBSTRATE_LIVE_MAX_PRICE_AGE_SEC", 180):
            reason = "price_stale_for_live"
        elif _is_demo_or_simulated(opp.get("raw_json")):
            reason = "demo_or_simulated_signal_blocked"
        elif str(opp.get("route_provider") or "").strip().lower() in ("mock", "demo", "sim", "simulated"):
            reason = "mock_route_provider_blocked_for_live"
        elif cfg_bool(con, "SUBSTRATE_LIVE_REQUIRE_PAPER_SHADOW", True) and not _has_paper_shadow(con, opp):
            reason = "paper_shadow_required_first"
        else:
            active = con.execute(
                "SELECT COUNT(*) c FROM substrate_live_orders WHERE state IN ('READY_FOR_MANUAL_SIGN','SIGNED','SENT','OPEN')"
            ).fetchone()["c"]
            if int(active or 0) >= cfg_int(con, "SUBSTRATE_LIVE_MAX_OPEN", 1):
                reason = "live_max_open_reached"
            else:
                reason = "OK"
        if reason != "OK":
            _audit(con, False, reason, opp)
            con.commit()
            return {"ok": False, "reason": reason}
        desired = cfg_float(con, "SUBSTRATE_LIVE_POSITION_SIZE_USD", 10.0)
        cap = cfg_float(con, "SUBSTRATE_LIVE_MAX_POSITION_USD", 25.0)
        size = max(0.0, min(desired, cap))
        if size <= 0:
            _audit(con, False, "live_size_zero", opp)
            con.commit()
            return {"ok": False, "reason": "live_size_zero"}
        payload = {
            "execution_mode": "manual_sign",
            "wallet_provider": provider,
            "wallet_family": family,
            "wallet_address": wallet,
            "chain": chain,
            "asset_symbol": opp.get("asset_symbol"),
            "asset_address": opp.get("asset_address"),
            "quote_asset": opp.get("quote_asset") or "USDC",
            "price_usd": float(opp.get("price_usd") or 0),
            "size_usd": size,
            "autosend_enabled": False,
            "operator_note": "Review route, slippage, gas, and wallet prompt before signing.",
        }
        con.execute(
            "INSERT INTO substrate_live_orders(opportunity_id,state,chain,asset_symbol,wallet_address,provider,size_usd,quote_asset,route_provider,order_payload_json,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(opportunity_id), "READY_FOR_MANUAL_SIGN", chain, opp.get("asset_symbol"), wallet, provider,
                size, opp.get("quote_asset") or "USDC", opp.get("route_provider"), json.dumps(payload, sort_keys=True), now, now,
            ),
        )
        _audit(con, True, "live_order_ready_for_manual_sign", opp)
        con.commit()
        return {"ok": True, "reason": "live_order_ready_for_manual_sign", "size_usd": size, "provider": provider, "chain": chain}
    finally:
        con.close()
