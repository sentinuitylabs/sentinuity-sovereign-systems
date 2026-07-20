from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Iterable


def _root() -> Path:
    env = os.getenv("SENTINUITY_ROOT") or os.getenv("OPENCLAW_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    p = Path(__file__).resolve()
    for parent in [p.parent, *p.parents]:
        if (parent / "sentinuity_matrix.db").exists():
            return parent
    # wallets/ lives under repo root in the patched pack
    return Path(__file__).resolve().parent.parent


def db_path() -> Path:
    env = os.getenv("SENTINUITY_DB")
    return Path(env).expanduser().resolve() if env else (_root() / "sentinuity_matrix.db")


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(str(db_path()), timeout=12, check_same_thread=False)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=5000")
    except Exception:
        pass
    return con


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    try:
        return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    except Exception:
        return False


def _cols(con: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in con.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _ensure_col(con: sqlite3.Connection, table: str, col: str, typ: str) -> None:
    try:
        if col not in _cols(con, table):
            con.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
    except Exception:
        pass


def _ensure_system_config(con: sqlite3.Connection) -> None:
    if not _table_exists(con, "system_config"):
        con.execute("CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY, value TEXT)")
    else:
        cols = _cols(con, "system_config")
        if "key" not in cols or "value" not in cols:
            # Do not mutate a strange production config table. The later writes will fail loudly.
            return


def cfg_get(con: sqlite3.Connection, key: str, default: Any = "") -> Any:
    try:
        row = con.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return row[0] if row and row[0] is not None else default
    except Exception:
        return default


def cfg_set(con: sqlite3.Connection, key: str, value: Any) -> None:
    con.execute(
        "INSERT INTO system_config(key,value) VALUES(?,?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(key), str(value)),
    )


def cfg_bool(con: sqlite3.Connection, key: str, default: bool = False) -> bool:
    v = cfg_get(con, key, "1" if default else "0")
    return str(v).strip().lower() in ("1", "true", "yes", "on", "enabled", "armed", "live")


def cfg_float(con: sqlite3.Connection, key: str, default: float = 0.0) -> float:
    try:
        return float(cfg_get(con, key, default))
    except Exception:
        return float(default)


def cfg_int(con: sqlite3.Connection, key: str, default: int = 0) -> int:
    try:
        return int(float(cfg_get(con, key, default)))
    except Exception:
        return int(default)


CONFIG_DEFAULTS: Dict[str, str] = {
    "SUBSTRATE_NODE_ENABLED": "1",
    "SUBSTRATE_PAPER_BALANCE_USD": "500",
    "SUBSTRATE_PAPER_CASH_USD": "500",
    "SUBSTRATE_AUTO_DEPLOY_PAPER": "1",
    "SUBSTRATE_POSITION_SIZE_USD": "25",
    "SUBSTRATE_MAX_OPEN": "3",
    "SUBSTRATE_LIVE_ENABLED": "0",
    "SUBSTRATE_LIVE_ARMED": "0",
    "SUBSTRATE_LIVE_PROVIDER": "coinbase_wallet",
    "SUBSTRATE_LIVE_WALLET_FAMILY": "evm",
    "SUBSTRATE_LIVE_WALLET_ADDRESS": "",
    "SUBSTRATE_LIVE_ALLOWED_CHAINS": "base,ethereum,arbitrum,optimism,polygon",
    "SUBSTRATE_LIVE_POSITION_SIZE_USD": "10",
    "SUBSTRATE_LIVE_MAX_POSITION_USD": "25",
    "SUBSTRATE_LIVE_MAX_OPEN": "1",
    "SUBSTRATE_LIVE_MIN_COUNCIL_CONVICTION": "0.80",
    "SUBSTRATE_LIVE_MAX_PRICE_AGE_SEC": "180",
    "SUBSTRATE_LIVE_MAX_RISK_SCORE": "0.55",
    "SUBSTRATE_LIVE_MIN_LIQUIDITY_USD": "1000000",
    "SUBSTRATE_LIVE_REQUIRE_PAPER_SHADOW": "1",
    "SUBSTRATE_LIVE_EXECUTION_MODE": "manual_sign",
    "SUBSTRATE_LIVE_AUTOSEND_ENABLED": "0",
    "SUBSTRATE_COPYTRADE_PAPER_INFLUENCE": "0",
    "SUBSTRATE_COPYTRADE_DEMO_MODE": "0",
}


SCHEMA = """
CREATE TABLE IF NOT EXISTS substrate_wallet_state(
  id INTEGER PRIMARY KEY CHECK(id=1),
  updated_at REAL,
  mode TEXT,
  wallet_family TEXT,
  provider TEXT,
  wallet_address TEXT,
  chain TEXT,
  network TEXT,
  live_enabled INTEGER DEFAULT 0,
  live_armed INTEGER DEFAULT 0,
  live_max_position_usd REAL DEFAULT 10,
  live_max_open INTEGER DEFAULT 1,
  live_block_reason TEXT
);
CREATE TABLE IF NOT EXISTS substrate_provider_health(
  provider TEXT PRIMARY KEY,
  mode TEXT,
  ready INTEGER DEFAULT 0,
  last_error TEXT,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS substrate_opportunities(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  source TEXT,
  chain TEXT,
  asset_symbol TEXT,
  asset_address TEXT,
  asset_type TEXT,
  native_or_wrapped TEXT,
  quote_asset TEXT DEFAULT 'USDC',
  confidence REAL DEFAULT 0,
  expected_edge REAL DEFAULT 0,
  liquidity_usd REAL DEFAULT 0,
  volume_5m_usd REAL DEFAULT 0,
  price_usd REAL DEFAULT 0,
  price_updated_at REAL DEFAULT 0,
  risk_score REAL DEFAULT 1,
  route_provider TEXT,
  raw_json TEXT,
  state TEXT DEFAULT 'NEW',
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS substrate_council_votes(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  council_member TEXT,
  phase TEXT,
  chain TEXT,
  asset_symbol TEXT,
  allocation_pct REAL,
  confidence REAL,
  thesis TEXT,
  created_at REAL
);
CREATE TABLE IF NOT EXISTS substrate_copytrade_signals(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  wallet_address TEXT,
  chain TEXT,
  asset_symbol TEXT,
  asset_address TEXT,
  action TEXT,
  confidence REAL,
  observed_size_usd REAL,
  pnl_hint TEXT,
  state TEXT DEFAULT 'NEW',
  raw_json TEXT,
  created_at REAL,
  updated_at REAL
);
CREATE TABLE IF NOT EXISTS substrate_positions(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER,
  mode TEXT DEFAULT 'PAPER',
  state TEXT DEFAULT 'OPEN',
  status TEXT DEFAULT 'OPEN',
  chain TEXT,
  asset_symbol TEXT,
  symbol TEXT,
  side TEXT DEFAULT 'LONG',
  size_usd REAL,
  position_size REAL,
  entry_price_usd REAL,
  entry_price REAL,
  current_price REAL,
  quantity REAL,
  source TEXT,
  opened_at REAL,
  updated_at REAL,
  closed_at REAL,
  exit_price REAL,
  exit_reason TEXT,
  unrealized_pnl REAL DEFAULT 0,
  realized_pnl REAL DEFAULT 0,
  raw_json TEXT
);
CREATE TABLE IF NOT EXISTS substrate_execution_audit(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  created_at REAL,
  allowed INTEGER,
  reason TEXT,
  source TEXT,
  asset_symbol TEXT,
  chain TEXT,
  confidence REAL,
  raw_json TEXT
);
CREATE TABLE IF NOT EXISTS substrate_live_orders(
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  opportunity_id INTEGER,
  state TEXT,
  chain TEXT,
  asset_symbol TEXT,
  wallet_address TEXT,
  provider TEXT,
  size_usd REAL,
  quote_asset TEXT,
  route_provider TEXT,
  order_payload_json TEXT,
  created_at REAL,
  updated_at REAL
);
"""


def ensure_schema(con: sqlite3.Connection | None = None) -> None:
    own = con is None
    con = con or connect()
    try:
        _ensure_system_config(con)
        con.executescript(SCHEMA)
        # Existing databases may have older table shapes.
        for table, additions in {
            "substrate_wallet_state": {
                "wallet_family": "TEXT", "provider": "TEXT", "wallet_address": "TEXT", "chain": "TEXT",
                "network": "TEXT", "live_enabled": "INTEGER DEFAULT 0", "live_armed": "INTEGER DEFAULT 0",
                "live_max_position_usd": "REAL DEFAULT 10", "live_max_open": "INTEGER DEFAULT 1",
                "live_block_reason": "TEXT", "updated_at": "REAL", "mode": "TEXT",
            },
            "substrate_opportunities": {
                "asset_type": "TEXT", "native_or_wrapped": "TEXT", "quote_asset": "TEXT", "volume_5m_usd": "REAL DEFAULT 0",
                "price_usd": "REAL DEFAULT 0", "price_updated_at": "REAL DEFAULT 0", "risk_score": "REAL DEFAULT 1",
                "route_provider": "TEXT", "raw_json": "TEXT", "state": "TEXT DEFAULT 'NEW'", "updated_at": "REAL",
            },
            "substrate_positions": {
                "status": "TEXT DEFAULT 'OPEN'", "symbol": "TEXT", "position_size": "REAL", "entry_price": "REAL",
                "current_price": "REAL", "exit_price": "REAL", "exit_reason": "TEXT", "unrealized_pnl": "REAL DEFAULT 0",
                "realized_pnl": "REAL DEFAULT 0", "raw_json": "TEXT",
            },
        }.items():
            for col, typ in additions.items():
                _ensure_col(con, table, col, typ)
        for k, v in CONFIG_DEFAULTS.items():
            try:
                cur = con.execute("SELECT value FROM system_config WHERE key=?", (k,)).fetchone()
                if cur is None:
                    cfg_set(con, k, v)
            except Exception:
                pass
        # Keep one canonical state row; never arm live by default.
        now = time.time()
        con.execute(
            "INSERT OR IGNORE INTO substrate_wallet_state"
            "(id,updated_at,mode,wallet_family,provider,wallet_address,chain,network,live_enabled,live_armed,live_max_position_usd,live_max_open,live_block_reason) "
            "VALUES(1,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                now, "PAPER", cfg_get(con, "SUBSTRATE_LIVE_WALLET_FAMILY", "evm"),
                cfg_get(con, "SUBSTRATE_LIVE_PROVIDER", "coinbase_wallet"),
                cfg_get(con, "SUBSTRATE_LIVE_WALLET_ADDRESS", ""),
                "base", "mainnet", 0, 0,
                cfg_float(con, "SUBSTRATE_LIVE_MAX_POSITION_USD", 25.0),
                cfg_int(con, "SUBSTRATE_LIVE_MAX_OPEN", 1),
                "live disabled by default",
            ),
        )
        con.commit()
    finally:
        if own:
            con.close()


def heartbeat(service_name: str, status: str = "OK", message: str = "", count: int | float = 0) -> None:
    con = connect()
    try:
        now = time.time()
        if not _table_exists(con, "service_heartbeats"):
            con.execute(
                "CREATE TABLE IF NOT EXISTS service_heartbeats("
                "service_name TEXT PRIMARY KEY, status TEXT, last_seen REAL, message TEXT, count REAL)"
            )
        cols = _cols(con, "service_heartbeats")
        if "service_name" in cols:
            fields = ["service_name"]
            vals: list[Any] = [service_name]
            update = []
            for col, val in (("status", status), ("last_seen", now), ("last_heartbeat", now), ("message", message), ("detail", message), ("count", count)):
                if col in cols:
                    fields.append(col); vals.append(val); update.append(f"{col}=excluded.{col}")
            sql = f"INSERT INTO service_heartbeats({','.join(fields)}) VALUES({','.join(['?']*len(fields))}) "
            if update:
                sql += "ON CONFLICT(service_name) DO UPDATE SET " + ",".join(update)
            con.execute(sql, vals)
        con.commit()
    except Exception:
        pass
    finally:
        con.close()
