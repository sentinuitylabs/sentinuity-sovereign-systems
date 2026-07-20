#!/usr/bin/env python3
"""
core/intelligence_schema_live_lane.py — DB migrations for the live-lane
measurement layer. INTELLIGENCE DB ONLY.

Creates (idempotent, safe to call at every boot):
  live_lane_feature_snapshots   — Task B pre-entry fingerprint per candidate stage
  live_lane_shadow_candidates   — Task F live handball contract (SHADOW ONLY)
  trajectory_score_history      — Task G composite System Trajectory Score rows

HARD RULES honoured:
  * Never touches sentinuity_matrix.db (execution DB stays fast).
  * No live flags, no signing, no swap paths.
  * live_status is constrained to SHADOW_ONLY / PROMOTABLE / BLOCKED —
    there is deliberately NO 'ARMED' or 'LIVE' state in this contract.

Usage:
  python core/intelligence_schema_live_lane.py            # migrate default DB
  python core/intelligence_schema_live_lane.py --db PATH
or from code:
  from core.intelligence_schema_live_lane import ensure_live_lane_schema
  ensure_live_lane_schema()
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from core.live_lane_common import find_intel_db, connect_intel_rw  # noqa: E402


DDL = [
    # ── Task B — pre-entry feature fingerprint ────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS live_lane_feature_snapshots (
        snap_id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        candidate_id             INTEGER,            -- market_snapshots.id
        paper_position_id        INTEGER,            -- filled once opened
        mint_address             TEXT,
        token_name               TEXT,
        token_symbol             TEXT,
        stage                    TEXT,               -- QUALIFIED/LATCHED/EXEC_READY/OPENED
        -- timeline
        observed_at              REAL,
        qualified_at             REAL,
        latched_at               REAL,
        exec_ready_at            REAL,
        opened_at                REAL,
        snapped_at               REAL,
        -- confidence family
        entry_confidence         REAL,
        raw_confidence           REAL,
        calibrated_confidence    REAL,
        -- price integrity family
        price_integrity_status   TEXT,
        price_integrity_reason   TEXT,
        price_updated_at         REAL,
        qualify_price            REAL,
        qualify_price_age_sec    REAL,
        oracle_price_age_sec     REAL,
        entry_vs_qualify_pct     REAL,
        same_mint_price_spread_pct REAL,
        -- market shape
        observed_price           REAL,
        market_cap_usd           REAL,
        liquidity_usd            REAL,
        curve_sol                REAL,
        curve_progress_pct       REAL,
        holder_count             INTEGER,
        top10_holder_pct         REAL,
        buy_velocity             REAL,
        freshness_score          REAL,
        -- provenance
        source_route             TEXT,               -- pump_monitor/copytrade/substrate/council/other
        strategy                 TEXT,
        reason_labels            TEXT,
        -- timing features
        hour_utc                 INTEGER,
        hour_aest                INTEGER,
        daypart                  TEXT,
        signal_age_sec           REAL,
        latch_to_open_sec        REAL,
        exec_ready_age_sec       REAL,
        -- guardians
        vetoes_seen              TEXT,
        guardian_warnings        TEXT,
        -- mode
        trade_mode               TEXT DEFAULT 'paper',
        intended_lane            TEXT DEFAULT 'PAPER',
        feature_completeness_pct REAL,
        created_at               REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llfs_mint  ON live_lane_feature_snapshots(mint_address)",
    "CREATE INDEX IF NOT EXISTS idx_llfs_cand  ON live_lane_feature_snapshots(candidate_id, stage)",
    "CREATE INDEX IF NOT EXISTS idx_llfs_pos   ON live_lane_feature_snapshots(paper_position_id)",
    "CREATE INDEX IF NOT EXISTS idx_llfs_time  ON live_lane_feature_snapshots(snapped_at DESC)",

    # ── Task F — live handball contract (SHADOW ONLY) ─────────────────────
    """
    CREATE TABLE IF NOT EXISTS live_lane_shadow_candidates (
        id                 INTEGER PRIMARY KEY AUTOINCREMENT,
        mint_address       TEXT,
        candidate_id       INTEGER,
        paper_position_id  INTEGER,
        snap_id            INTEGER,
        score              REAL,                -- 0..100
        score_reasons      TEXT,
        rule_name          TEXT,
        pass_fail          TEXT,                -- PASS / FAIL
        blocked_reason     TEXT,
        live_status        TEXT DEFAULT 'SHADOW_ONLY'
                           CHECK (live_status IN ('SHADOW_ONLY','PROMOTABLE','BLOCKED')),
        created_at         REAL,
        expires_at         REAL
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_llsc_mint ON live_lane_shadow_candidates(mint_address)",
    "CREATE INDEX IF NOT EXISTS idx_llsc_time ON live_lane_shadow_candidates(created_at DESC)",
    "CREATE INDEX IF NOT EXISTS idx_llsc_pos  ON live_lane_shadow_candidates(paper_position_id)",

    # ── Task G — measured trajectory history ──────────────────────────────
    """
    CREATE TABLE IF NOT EXISTS trajectory_score_history (
        id                        INTEGER PRIMARY KEY AUTOINCREMENT,
        computed_at               REAL,
        window_label              TEXT,           -- e.g. 12H / 24H / 72H
        trajectory_score          REAL,           -- 0..100 composite, NOT hardcoded up
        band                      TEXT,           -- RED/ORANGE/GOLD/GREEN/CYAN/VIOLET
        -- the 12 doctrine components (NULL = not measurable yet)
        c1_paper_net_pnl          REAL,
        c2_profit_factor          REAL,
        c3_monster_capture        REAL,
        c4_bad_loss_suppression   REAL,
        c5_clean_price_ratio      REAL,
        c6_shadow_expectancy      REAL,
        c7_prediction_coverage    REAL,
        c8_calibration_quality    REAL,
        c9_service_uptime         REAL,
        c10_history_continuity    REAL,
        c11_build_quality         REAL,
        c12_substrate_contrib     REAL,
        components_measured       INTEGER,
        notes                     TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_tsh_time ON trajectory_score_history(computed_at DESC)",
]


def ensure_live_lane_schema(db_path: Path | str | None = None) -> Path:
    path = Path(db_path) if db_path else find_intel_db()
    con = connect_intel_rw(path)
    try:
        for stmt in DDL:
            con.execute(stmt)
        con.commit()
    finally:
        con.close()
    return path


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="path to sentinuity_intelligence.db")
    a = ap.parse_args()
    p = ensure_live_lane_schema(a.db)
    con = sqlite3.connect(p)
    tabs = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'live_lane%' "
        "OR name='trajectory_score_history'")]
    con.close()
    print(f"[OK] live-lane schema ensured in {p}")
    for t in tabs:
        print(f"     table: {t}")
    print("[OK] live_status states: SHADOW_ONLY / PROMOTABLE / BLOCKED (no live state exists)")
