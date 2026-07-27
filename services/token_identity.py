"""Best-effort token name/symbol enrichment with a persistent local cache.

Identity is display/research metadata only. Failure never blocks qualification,
entry, exit, pricing, or safety gates.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

DB_PATH = Path("sentinuity_intelligence.db")
DEX_URL = "https://api.dexscreener.com/latest/dex/tokens/{mint}"
PUMP_URL = "https://frontend-api-v3.pump.fun/coins/{mint}"


def _clean(value: Any, max_len: int = 160) -> str:
    text = str(value or "").strip().replace("\x00", "")
    return text[:max_len]


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=5000")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS token_identity_cache (
            mint TEXT PRIMARY KEY,
            symbol TEXT NOT NULL DEFAULT '',
            name TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL DEFAULT '',
            image_uri TEXT NOT NULL DEFAULT '',
            pair_created_at REAL,
            resolved_at REAL NOT NULL DEFAULT 0
        )
    """)
    return conn


def cached_identity(mint: str) -> Dict[str, Any]:
    mint = _clean(mint, 128)
    if not mint:
        return {}
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT symbol,name,source,image_uri,pair_created_at,resolved_at "
                "FROM token_identity_cache WHERE mint=?", (mint,)
            ).fetchone()
        if not row:
            return {}
        return {"mint": mint, "symbol": row[0], "name": row[1], "source": row[2],
                "image_uri": row[3], "pair_created_at": row[4], "resolved_at": row[5]}
    except Exception:
        return {}


def _persist(mint: str, result: Dict[str, Any]) -> None:
    try:
        with _connect() as conn:
            conn.execute("""
                INSERT INTO token_identity_cache
                    (mint,symbol,name,source,image_uri,pair_created_at,resolved_at)
                VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(mint) DO UPDATE SET
                    symbol=excluded.symbol, name=excluded.name, source=excluded.source,
                    image_uri=excluded.image_uri,
                    pair_created_at=COALESCE(excluded.pair_created_at,token_identity_cache.pair_created_at),
                    resolved_at=excluded.resolved_at
            """, (mint, result.get("symbol", ""), result.get("name", ""),
                    result.get("source", ""), result.get("image_uri", ""),
                    result.get("pair_created_at"), time.time()))
    except Exception:
        pass


def resolve_token_identity(mint: str, session: Optional[requests.Session] = None,
                           timeout_sec: float = 0.40) -> Dict[str, Any]:
    """Resolve identity without ever raising or becoming trading authority."""
    mint = _clean(mint, 128)
    if not mint:
        return {}
    cached = cached_identity(mint)
    if cached and (cached.get("name") or cached.get("symbol")):
        return cached
    http = session or requests.Session()
    try:
        payload = http.get(DEX_URL.format(mint=mint), timeout=timeout_sec).json()
        pairs = payload.get("pairs") or []
        pair = next((p for p in pairs if str((p.get("baseToken") or {}).get("address") or "") == mint), None)
        pair = pair or (pairs[0] if pairs else None)
        if pair:
            base = pair.get("baseToken") or {}
            result = {"mint": mint, "symbol": _clean(base.get("symbol"), 40),
                      "name": _clean(base.get("name"), 120), "source": "DEXSCREENER",
                      "image_uri": "", "pair_created_at": pair.get("pairCreatedAt")}
            if result["name"] or result["symbol"]:
                _persist(mint, result)
                return result
    except Exception:
        pass
    if mint.endswith("pump"):
        try:
            payload = http.get(PUMP_URL.format(mint=mint), timeout=timeout_sec).json()
            result = {"mint": mint, "symbol": _clean(payload.get("symbol"), 40),
                      "name": _clean(payload.get("name"), 120), "source": "PUMPFUN",
                      "image_uri": _clean(payload.get("image_uri"), 500),
                      "pair_created_at": payload.get("created_timestamp")}
            if result["name"] or result["symbol"]:
                _persist(mint, result)
                return result
        except Exception:
            pass
    return {"mint": mint, "symbol": "", "name": "", "source": "UNRESOLVED"}
