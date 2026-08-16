from __future__ import annotations

import json
import sqlite3
import time
import random
from pathlib import Path
from typing import Any, Callable, Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
MATRIX_DB = ROOT / "sentinuity_matrix.db"
INTEL_DB = ROOT / "sentinuity_intelligence.db"
WSOL_MINT = "So11111111111111111111111111111111111111112"
CONFIG_KEY = "SOLANA_USD_PRICE"
MIN_SOL_USD = 5.0
MAX_SOL_USD = 2000.0


def _valid_price(value: Any) -> Optional[float]:
    try:
        price = float(value)
    except (TypeError, ValueError):
        return None
    if not (MIN_SOL_USD <= price <= MAX_SOL_USD):
        return None
    return price


def _request_json(session: Any, url: str, *, params: Optional[dict] = None, timeout: float = 2.5) -> dict:
    response = session.get(url, params=params, headers={"Accept": "application/json"}, timeout=timeout)
    if int(getattr(response, "status_code", 0)) != 200:
        return {}
    payload = response.json()
    return payload if isinstance(payload, dict) else {}


def fetch_sol_usd(session: Any, *, timeout: float = 2.5) -> Tuple[Optional[float], Optional[str]]:
    """Fetch SOL/USD from bounded independent public sources.

    Returns the first valid result. No stale default is invented.
    """
    try:
        data = _request_json(
            session,
            "https://api.jup.ag/price/v3",
            params={"ids": WSOL_MINT},
            timeout=timeout,
        )
        price = _valid_price((data.get(WSOL_MINT) or {}).get("usdPrice"))
        if price is not None:
            return price, "jupiter_price_v3"
    except Exception:
        pass

    try:
        data = _request_json(
            session,
            "https://api.coingecko.com/api/v3/simple/price",
            params={"ids": "solana", "vs_currencies": "usd"},
            timeout=timeout,
        )
        price = _valid_price((data.get("solana") or {}).get("usd"))
        if price is not None:
            return price, "coingecko_simple_price"
    except Exception:
        pass

    try:
        data = _request_json(
            session,
            f"https://api.dexscreener.com/latest/dex/tokens/{WSOL_MINT}",
            timeout=timeout,
        )
        pairs = [p for p in (data.get("pairs") or []) if isinstance(p, dict)]
        pairs = [p for p in pairs if str(p.get("chainId") or "").lower() == "solana"]
        if pairs:
            best = max(pairs, key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0.0))
            price = _valid_price(best.get("priceUsd"))
            if price is not None:
                return price, "dexscreener_wsol"
    except Exception:
        pass

    return None, None


def _is_locked(exc: BaseException) -> bool:
    text = str(exc).lower()
    return "database is locked" in text or "database table is locked" in text


def _retry_write(path: Path, writer: Callable[[sqlite3.Connection], None], *, attempts: int = 4) -> None:
    """Run a tiny SQLite write transaction with bounded lock retry.

    No journal-mode mutation is performed here. The hot path must cooperate
    with the existing database policy rather than taking a schema or WAL lock.
    """
    last: Optional[BaseException] = None
    for attempt in range(attempts):
        conn = None
        try:
            conn = sqlite3.connect(str(path), timeout=1.0)
            conn.execute("PRAGMA busy_timeout=1000")
            writer(conn)
            conn.commit()
            return
        except sqlite3.OperationalError as exc:
            last = exc
            if conn is not None:
                try:
                    conn.rollback()
                except Exception:
                    pass
            if not _is_locked(exc) or attempt + 1 >= attempts:
                raise
            time.sleep((0.04 * (2 ** attempt)) + random.random() * 0.02)
        finally:
            if conn is not None:
                conn.close()
    if last is not None:
        raise last


def _write_matrix_basis(conn: sqlite3.Connection, price: float, source: str, now: float) -> None:
    sql = (
        "INSERT INTO system_config(key,value,updated_at) VALUES(?,?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value,updated_at=excluded.updated_at"
    )
    conn.execute(sql, (CONFIG_KEY, f"{price:.8f}", now))
    conn.execute(sql, ("SOLANA_USD_PRICE_SOURCE", source, now))


def _write_intel_basis(conn: sqlite3.Connection, price: float, source: str, now: float) -> None:
    conn.execute(
        "INSERT INTO mtm_ticks(mint_address,price_usd,ts_ms,source,price,ts,created_at) "
        "VALUES(?,?,?,?,?,?,?)",
        (WSOL_MINT, price, int(now * 1000.0), source, price, now, now),
    )


def _ensure_matrix_schema(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS system_config "
            "(key TEXT PRIMARY KEY, value TEXT, description TEXT, updated_at REAL)"
        )
        cols = {r[1] for r in conn.execute("PRAGMA table_info(system_config)")}
        if "updated_at" not in cols:
            conn.execute("ALTER TABLE system_config ADD COLUMN updated_at REAL")
        conn.commit()
    finally:
        conn.close()


def _ensure_intel_schema(path: Path) -> None:
    conn = sqlite3.connect(str(path), timeout=5.0)
    try:
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mtm_ticks ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, mint_address TEXT, price_usd REAL, "
            "ts_ms INTEGER, source TEXT, price REAL, ts REAL, created_at REAL)"
        )
        conn.commit()
    finally:
        conn.close()


def persist_sol_usd(
    price: float,
    source: str,
    *,
    now: Optional[float] = None,
    matrix_db: Path = MATRIX_DB,
    intel_db: Path = INTEL_DB,
) -> Dict[str, Any]:
    """Persist one fresh basis with minimal lock footprint.

    Normal refreshes execute two small UPSERT/INSERT transactions. DDL is not
    issued on every refresh; schema repair is attempted only when a missing
    table/column error proves it is necessary. Lock retries are bounded and do
    not hold either database while waiting on the other.
    """
    price = _valid_price(price)
    if price is None:
        raise ValueError("invalid_sol_usd_price")
    source = str(source or "").strip()
    if not source:
        raise ValueError("missing_sol_usd_source")
    now = float(now if now is not None else time.time())

    matrix_db.parent.mkdir(parents=True, exist_ok=True)
    intel_db.parent.mkdir(parents=True, exist_ok=True)

    try:
        _retry_write(matrix_db, lambda conn: _write_matrix_basis(conn, price, source, now))
    except sqlite3.OperationalError as exc:
        text = str(exc).lower()
        if "no such table" not in text and "no column named updated_at" not in text:
            raise
        _ensure_matrix_schema(matrix_db)
        _retry_write(matrix_db, lambda conn: _write_matrix_basis(conn, price, source, now))

    try:
        _retry_write(intel_db, lambda conn: _write_intel_basis(conn, price, source, now))
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        _ensure_intel_schema(intel_db)
        _retry_write(intel_db, lambda conn: _write_intel_basis(conn, price, source, now))

    return {"value": price, "source": source, "age_sec": 0.0, "updated_at": now}


def refresh_sol_usd_basis(
    *,
    session: Any = None,
    timeout: float = 2.5,
    matrix_db: Path = MATRIX_DB,
    intel_db: Path = INTEL_DB,
) -> Dict[str, Any]:
    if session is None:
        import requests
        session = requests.Session()
    price, source = fetch_sol_usd(session, timeout=timeout)
    if price is None or source is None:
        return {"value": None, "source": None, "age_sec": None, "error": "all_sol_usd_sources_failed"}
    try:
        return persist_sol_usd(price, source, matrix_db=matrix_db, intel_db=intel_db)
    except Exception as exc:
        return {"value": None, "source": source, "age_sec": None, "error": f"persist_failed:{type(exc).__name__}:{exc}"}


def read_persisted_basis(*, matrix_db: Path = MATRIX_DB) -> Dict[str, Any]:
    if not matrix_db.exists():
        return {"value": None, "source": None, "age_sec": None, "error": "matrix_db_missing"}
    conn = None
    try:
        conn = sqlite3.connect(str(matrix_db), timeout=5.0)
        row = conn.execute(
            "SELECT value,updated_at FROM system_config WHERE key=?", (CONFIG_KEY,)
        ).fetchone()
        src = conn.execute(
            "SELECT value FROM system_config WHERE key='SOLANA_USD_PRICE_SOURCE'"
        ).fetchone()
        if not row:
            return {"value": None, "source": None, "age_sec": None, "error": "basis_missing"}
        price = _valid_price(row[0])
        if price is None:
            return {"value": None, "source": None, "age_sec": None, "error": "basis_invalid"}
        age = None if not row[1] else max(0.0, time.time() - float(row[1]))
        return {"value": price, "source": (src[0] if src else "system_config"), "age_sec": age, "error": None}
    except Exception as exc:
        return {"value": None, "source": None, "age_sec": None, "error": f"read_failed:{type(exc).__name__}:{exc}"}
    finally:
        if conn is not None:
            conn.close()
