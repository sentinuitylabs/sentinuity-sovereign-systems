"""Canonical read/write contract for the funded Solana wallet.

The live wallet is a single real Phantom/Solana account derived from
``SOLANA_PRIVATE_KEY``.  This module keeps that chain truth separate from paper
capital and exposes one consistent snapshot to the UI, console and live gates.

It never stores a private key, RPC URL or transaction signature.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Mapping, Optional

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = ROOT / "sentinuity_matrix.db"

LIVE_BALANCE_KEYS = (
    "LIVE_WALLET_BALANCE_USD",
    "SOLANA_LIVE_WALLET_USD",
    "LIVE_WALLET_USD",
    "REAL_LIVE_WALLET_USD",
    "LAST_REAL_WALLET_USD",
)
LIVE_AVAILABLE_KEYS = (
    "LIVE_AVAILABLE_USD",
    "SOLANA_LIVE_AVAILABLE_USD",
    "PHANTOM_AVAILABLE_USD",
)


def _connect(db_path: str | Path | None = None) -> sqlite3.Connection:
    path = Path(db_path or DEFAULT_DB)
    conn = sqlite3.connect(str(path), timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=15000")
    return conn


def ensure_live_wallet_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS live_wallet_state (
            id                  INTEGER PRIMARY KEY CHECK(id=1),
            wallet_address      TEXT NOT NULL DEFAULT '',
            sol_balance         REAL NOT NULL DEFAULT 0,
            gas_reserve_sol     REAL NOT NULL DEFAULT 0,
            usable_sol          REAL NOT NULL DEFAULT 0,
            sol_usd_price       REAL NOT NULL DEFAULT 0,
            balance_usd         REAL NOT NULL DEFAULT 0,
            available_usd       REAL NOT NULL DEFAULT 0,
            source              TEXT NOT NULL DEFAULT 'UNSYNCED',
            sync_status         TEXT NOT NULL DEFAULT 'UNSYNCED',
            last_error          TEXT,
            synced_at           REAL NOT NULL DEFAULT 0,
            updated_at          REAL NOT NULL DEFAULT 0
        )
        """
    )
    conn.execute("INSERT OR IGNORE INTO live_wallet_state(id) VALUES(1)")


def _upsert_config(conn: sqlite3.Connection, key: str, value: Any) -> None:
    conn.execute(
        "INSERT INTO system_config(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, str(value)),
    )


def write_live_wallet_truth(
    *,
    wallet_address: str,
    sol_balance: float,
    gas_reserve_sol: float,
    sol_usd_price: float,
    source: str,
    db_path: str | Path | None = None,
    synced_at: Optional[float] = None,
) -> dict[str, Any]:
    now = float(synced_at or time.time())
    sol_balance = max(0.0, float(sol_balance or 0.0))
    gas_reserve_sol = max(0.0, float(gas_reserve_sol or 0.0))
    usable_sol = max(0.0, sol_balance - gas_reserve_sol)
    price = max(0.0, float(sol_usd_price or 0.0))
    balance_usd = sol_balance * price
    available_usd = usable_sol * price

    with _connect(db_path) as conn:
        ensure_live_wallet_schema(conn)
        conn.execute(
            """
            UPDATE live_wallet_state
               SET wallet_address=?, sol_balance=?, gas_reserve_sol=?, usable_sol=?,
                   sol_usd_price=?, balance_usd=?, available_usd=?, source=?,
                   sync_status='SYNCED', last_error=NULL, synced_at=?, updated_at=?
             WHERE id=1
            """,
            (
                wallet_address,
                sol_balance,
                gas_reserve_sol,
                usable_sol,
                price,
                balance_usd,
                available_usd,
                source,
                now,
                now,
            ),
        )
        for key in LIVE_BALANCE_KEYS:
            _upsert_config(conn, key, f"{balance_usd:.8f}")
        for key in LIVE_AVAILABLE_KEYS:
            _upsert_config(conn, key, f"{available_usd:.8f}")
        _upsert_config(conn, "LIVE_WALLET_SOL", f"{sol_balance:.9f}")
        _upsert_config(conn, "LIVE_WALLET_USABLE_SOL", f"{usable_sol:.9f}")
        _upsert_config(conn, "LIVE_WALLET_ADDRESS", wallet_address)
        _upsert_config(conn, "LIVE_WALLET_SOL_USD", f"{price:.8f}")
        _upsert_config(conn, "LIVE_WALLET_SYNC_SOURCE", source)
        _upsert_config(conn, "LIVE_WALLET_SYNCED_AT", f"{now:.3f}")
        _upsert_config(conn, "LIVE_WALLET_SYNC_STATUS", "SYNCED")
        conn.commit()

    return {
        "wallet_address": wallet_address,
        "sol_balance": sol_balance,
        "usable_sol": usable_sol,
        "sol_usd_price": price,
        "balance_usd": balance_usd,
        "available_usd": available_usd,
        "source": source,
        "sync_status": "SYNCED",
        "synced_at": now,
        "age_sec": 0.0,
        "synced": True,
    }


def record_live_wallet_error(
    error: str,
    *,
    db_path: str | Path | None = None,
    source: str = "CHAIN_RPC",
) -> None:
    now = time.time()
    try:
        with _connect(db_path) as conn:
            ensure_live_wallet_schema(conn)
            conn.execute(
                "UPDATE live_wallet_state SET source=?, sync_status='ERROR', "
                "last_error=?, updated_at=? WHERE id=1",
                (source, str(error)[:500], now),
            )
            _upsert_config(conn, "LIVE_WALLET_SYNC_STATUS", "ERROR")
            _upsert_config(conn, "LIVE_WALLET_SYNC_ERROR", str(error)[:500])
            conn.commit()
    except Exception:
        pass


def read_live_wallet_truth(
    db_path: str | Path | None = None,
    *,
    max_age_sec: float = 180.0,
) -> dict[str, Any]:
    now = time.time()
    empty = {
        "wallet_address": "",
        "sol_balance": 0.0,
        "usable_sol": 0.0,
        "sol_usd_price": 0.0,
        "balance_usd": 0.0,
        "available_usd": 0.0,
        "source": "UNSYNCED",
        "sync_status": "UNSYNCED",
        "last_error": None,
        "synced_at": 0.0,
        "age_sec": None,
        "synced": False,
    }
    try:
        with _connect(db_path) as conn:
            ensure_live_wallet_schema(conn)
            row = conn.execute("SELECT * FROM live_wallet_state WHERE id=1").fetchone()
            if row:
                out = dict(row)
                synced_at = float(out.get("synced_at") or 0.0)
                age = max(0.0, now - synced_at) if synced_at else None
                out["age_sec"] = age
                out["synced"] = bool(
                    str(out.get("sync_status") or "").upper() == "SYNCED"
                    and synced_at > 0
                    and (age is not None and age <= float(max_age_sec))
                    and float(out.get("sol_usd_price") or 0.0) > 0
                )
                return {**empty, **out}
    except Exception as exc:
        empty["last_error"] = str(exc)
    return empty
