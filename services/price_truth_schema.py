from __future__ import annotations

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "sentinuity_price_truth.db"

DDL = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS price_truth_snapshots (
 id INTEGER PRIMARY KEY AUTOINCREMENT, decision_id TEXT NOT NULL, position_id INTEGER,
 funding_mode TEXT, mint_address TEXT NOT NULL, raw_quantity TEXT NOT NULL,
 decimals INTEGER NOT NULL, observed_at REAL NOT NULL, reference_price REAL,
 reference_source TEXT, reference_age_sec REAL, executable_price REAL,
 executable_source TEXT, executable_age_sec REAL, executable_can_exit INTEGER,
 executable_warning TEXT, executable_pnl_pct REAL, reference_pnl_pct REAL,
 divergence_pct REAL, quorum_state TEXT NOT NULL, shadow_only INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_pts_pos_time ON price_truth_snapshots(position_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_pts_mint_time ON price_truth_snapshots(mint_address, observed_at DESC);
CREATE TABLE IF NOT EXISTS price_truth_health (
 id INTEGER PRIMARY KEY CHECK(id=1), last_cycle_at REAL, positions_seen INTEGER,
 snapshots_written INTEGER, last_error TEXT, cycle_ms REAL
);
INSERT OR IGNORE INTO price_truth_health(id,positions_seen,snapshots_written) VALUES(1,0,0);
CREATE TABLE IF NOT EXISTS pump_curve_shadow (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 decision_id TEXT NOT NULL,
 position_id INTEGER,
 mint_address TEXT NOT NULL,
 raw_quantity TEXT NOT NULL,
 token_decimals INTEGER NOT NULL,
 observed_at REAL NOT NULL,
 curve_address TEXT,
 context_slot INTEGER,
 account_hash TEXT,
 rpc_label TEXT,
 rpc_latency_ms REAL,
 account_len INTEGER,
 complete INTEGER,
 virtual_token_reserves TEXT,
 virtual_quote_reserves TEXT,
 real_token_reserves TEXT,
 real_quote_reserves TEXT,
 fee_bps INTEGER,
 fee_source TEXT,
 theoretical_gross_quote_raw TEXT,
 payable_gross_quote_raw TEXT,
 fee_quote_raw TEXT,
 net_quote_raw TEXT,
 marginal_quote_raw TEXT,
 curve_impact_bps INTEGER,
 real_reserve_coverage_bps INTEGER,
 reserve_bounded INTEGER,
 pump_executable_price_usd REAL,
 pump_executable_pnl_pct REAL,
 jupiter_executable_price_usd REAL,
 pump_vs_jupiter_divergence_pct REAL,
 reference_price_usd REAL,
 reference_vs_pump_divergence_pct REAL,
 shadow_state TEXT NOT NULL,
 shadow_reason TEXT NOT NULL,
 shadow_only INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_pcs_pos_time ON pump_curve_shadow(position_id, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_pcs_mint_time ON pump_curve_shadow(mint_address, observed_at DESC);
CREATE INDEX IF NOT EXISTS idx_pcs_state_time ON pump_curve_shadow(shadow_state, observed_at DESC);
"""


def connect(path: Path = DB_PATH):
    connection = sqlite3.connect(str(path), timeout=0.2)
    connection.execute("PRAGMA busy_timeout=100")
    connection.row_factory = sqlite3.Row
    return connection


def migrate(path: Path = DB_PATH):
    connection = connect(path)
    connection.executescript(DDL)
    connection.commit()
    connection.close()


if __name__ == "__main__":
    migrate()
    print(f"PRICE_TRUTH_SCHEMA_OK {DB_PATH}")
