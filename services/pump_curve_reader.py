# coding: utf-8
"""Minimal Pump bonding-curve reader for the shadow price-truth observer."""
from __future__ import annotations

import base64
import hashlib
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

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


def _rpc_candidates() -> list[tuple[str, str]]:
    keys = ("QUICKNODE_RPC", "HELIUS_RPC", "SOLANA_RPC_URL", "CHAINSTACK_RPC")
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


def read_curve(mint: str, timeout_sec: float = 2.5) -> CurveRead:
    started = time.perf_counter()
    observed_at = time.time()
    curve = derive_curve_pda(mint)
    if not curve:
        return CurveRead(False, "PDA_DERIVATION_FAILED", mint, None, None, observed_at, None, None, None, None, 0.0)

    last_reason = "NO_RPC_RESPONSE"
    for label, endpoint in _rpc_candidates():
        try:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "getAccountInfo",
                "params": [curve, {"encoding": "base64", "commitment": "confirmed"}],
            }
            response = requests.post(endpoint, json=payload, timeout=timeout_sec)
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
            return CurveRead(
                True,
                "OK",
                mint,
                curve,
                int(slot) if slot is not None else None,
                observed_at,
                hashlib.sha256(raw).hexdigest(),
                owner,
                state,
                label,
                latency,
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
    )
