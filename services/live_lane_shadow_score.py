#!/usr/bin/env python3
"""
services/live_lane_shadow_score.py — LIVE-LANE SHADOW SCORER (NON-EXECUTING)

What it does, every poll cycle:
  1. Task B — reads market_snapshots + paper_positions from the matrix DB
     (READ-ONLY connection, PRAGMA query_only=ON) and writes a pre-entry
     feature fingerprint per candidate stage transition into
     sentinuity_intelligence.db::live_lane_feature_snapshots.
  2. Task D — scores each candidate 0–100 using ONLY pre-entry / early
     in-trade data, and writes score + reasons.
  3. Task F — maintains the live handball contract in
     live_lane_shadow_candidates with live_status limited to
     SHADOW_ONLY / PROMOTABLE / BLOCKED.

What it will NEVER do:
  * open a connection to the matrix DB in write mode
  * touch keys, signing, swaps, or any live flag
  * execute anything — even a PROMOTABLE row is information, not action.
    Promotion to real live fire requires the operator to arm
    LIVE_ARMED / LIVE_TRADING_ENABLED / LIVE_MONEY_MODE elsewhere; this
    module does not know how to do that by design.

Run standalone:
  python services/live_lane_shadow_score.py            # loop mode
  python services/live_lane_shadow_score.py --once     # single cycle
  python services/live_lane_shadow_score.py --once --verbose
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_lane_common import (  # noqa: E402
    find_matrix_db, find_intel_db, connect_ro, connect_intel_rw,
    table_exists, cols, pick, fnum, aest_hour, integrity_bucket,
)
from core.intelligence_schema_live_lane import ensure_live_lane_schema  # noqa: E402

SERVICE_NAME  = "live_lane_shadow_score"
POLL_SECONDS  = 5.0
CANDIDATE_TTL = 30 * 60          # contract rows expire after 30 min
PROMOTE_SCORE = 75.0             # informational threshold only

STAGE_ORDER = ["QUALIFIED", "LATCHED", "EXEC_READY", "OPENED"]

# candidate_state values that map to stages (defensive superset)
STATE_TO_STAGE = {
    "qualified": "QUALIFIED", "qualify": "QUALIFIED",
    "latched": "LATCHED",
    "exec_ready": "EXEC_READY", "execution_ready": "EXEC_READY",
    "executed": "OPENED", "opened": "OPENED",
}

FEATURE_FIELDS_FOR_COVERAGE = [
    "entry_confidence", "price_integrity_status", "qualify_price_age_sec",
    "oracle_price_age_sec", "entry_vs_qualify_pct", "market_cap_usd",
    "liquidity_usd", "source_route", "strategy", "hour_aest",
]


# ---------------------------------------------------------------- heartbeat

def _heartbeat(status: str, note: str = "") -> None:
    """Best-effort heartbeat via core.schema; never blocks or raises."""
    try:
        from core.schema import update_heartbeat  # type: ignore
        update_heartbeat(SERVICE_NAME, status, note=note)
    except Exception:
        pass


# ---------------------------------------------------------------- capture

def _daypart(hour: Optional[int]) -> Optional[str]:
    if hour is None:
        return None
    if 5 <= hour < 12:  return "MORNING"
    if 12 <= hour < 17: return "AFTERNOON"
    if 17 <= hour < 22: return "EVENING"
    return "NIGHT"


def _stage_of(row: sqlite3.Row) -> Optional[str]:
    keys = row.keys()
    if "execution_ready" in keys and row["execution_ready"]:
        stage = "EXEC_READY"
    elif "latched" in keys and row["latched"]:
        stage = "LATCHED"
    else:
        stage = None
    st = str(pick(row, "candidate_state", default="") or "").lower()
    stage = STATE_TO_STAGE.get(st, stage)
    if st in ("executed", "opened"):
        stage = "OPENED"
    return stage


def _snapshot_from_candidate(row: sqlite3.Row, now: float) -> Dict[str, Any]:
    price_updated_at = fnum(pick(row, "price_updated_at"))
    qualify_price    = fnum(pick(row, "qualify_price", "qualified_price", "observed_price"))
    observed_price   = fnum(pick(row, "observed_price", "last_price"))
    qualified_at     = fnum(pick(row, "qualified_at", "created_at"))
    latched_at       = fnum(pick(row, "latched_at"))
    created_at       = fnum(pick(row, "created_at"))

    entry_vs_qual = None
    if qualify_price and observed_price and qualify_price > 0:
        entry_vs_qual = (observed_price - qualify_price) / qualify_price * 100.0

    hour_a = aest_hour(now)
    snap: Dict[str, Any] = {
        "candidate_id":  pick(row, "id"),
        "mint_address":  pick(row, "mint_address"),
        "token_name":    pick(row, "token_name"),
        "token_symbol":  pick(row, "token_symbol", "symbol"),
        "stage":         _stage_of(row) or "QUALIFIED",
        "observed_at":   created_at,
        "qualified_at":  qualified_at,
        "latched_at":    latched_at,
        "snapped_at":    now,
        "entry_confidence":      fnum(pick(row, "entry_confidence", "confidence")),
        "raw_confidence":        fnum(pick(row, "confidence")),
        "calibrated_confidence": fnum(pick(row, "calibrated_confidence", "mint_confidence")),
        "price_integrity_status": pick(row, "price_integrity_status"),
        "price_integrity_reason": pick(row, "price_integrity_reason"),
        "price_updated_at":       price_updated_at,
        "qualify_price":          qualify_price,
        "qualify_price_age_sec":  (now - qualified_at) if qualified_at else None,
        "oracle_price_age_sec":   (now - price_updated_at) if price_updated_at else None,
        "entry_vs_qualify_pct":   entry_vs_qual,
        "same_mint_price_spread_pct": fnum(pick(row, "same_mint_price_spread_pct")),
        "observed_price":   observed_price,
        "market_cap_usd":   fnum(pick(row, "market_cap_usd")),
        "liquidity_usd":    fnum(pick(row, "token_liquidity_usd", "liquidity_usd")),
        "curve_sol":        fnum(pick(row, "curve_sol_reserves", "curve_sol")),
        "curve_progress_pct": fnum(pick(row, "curve_progress_pct")),
        "holder_count":     pick(row, "holder_count"),
        "top10_holder_pct": fnum(pick(row, "top10_holder_pct")),
        "buy_velocity":     fnum(pick(row, "buy_velocity")),
        "freshness_score":  fnum(pick(row, "freshness_score")),
        "source_route":     pick(row, "source_route", "source", "origin",
                                 default="pump_monitor"),
        "strategy":         pick(row, "strategy", "entry_reason", "logic_breakdown"),
        "reason_labels":    pick(row, "quality_reason", "reason"),
        "hour_utc":         int(now // 3600 % 24),
        "hour_aest":        hour_a,
        "daypart":          _daypart(hour_a),
        "signal_age_sec":   (now - created_at) if created_at else None,
        "latch_to_open_sec": None,
        "exec_ready_age_sec": None,
        "vetoes_seen":      pick(row, "veto_reason", "vetoes"),
        "guardian_warnings": pick(row, "guardian_warning", "warnings"),
        "trade_mode":       "paper",
        "intended_lane":    "PAPER",
    }
    filled = sum(1 for f in FEATURE_FIELDS_FOR_COVERAGE if snap.get(f) not in (None, "", 0))
    snap["feature_completeness_pct"] = round(100.0 * filled / len(FEATURE_FIELDS_FOR_COVERAGE), 1)
    snap["created_at"] = now
    return snap


# ---------------------------------------------------------------- scoring

def score_candidate(snap: Dict[str, Any],
                    early_move_pct: Optional[float] = None) -> Tuple[float, List[str]]:
    """
    0–100 shadow score from pre-entry / early-in-trade data ONLY.
    Weights derive from the overnight audit doctrine: price integrity is
    the only proven edge so far and carries the largest weight.
    """
    score = 0.0
    reasons: List[str] = []

    # 1. CLEAN price integrity — proven live-lane filter (30)
    bucket = integrity_bucket(snap.get("price_integrity_status"))
    if bucket == "CLEAN":
        score += 30; reasons.append("+30 CLEAN price integrity")
    elif bucket == "UNSTABLE":
        reasons.append("+0 UNSTABLE/OUTLIER integrity (hard drag)")
        score -= 15; reasons.append("-15 unstable penalty")
    else:
        score += 8; reasons.append("+8 integrity unknown (partial credit)")

    # 2. Fresh oracle age (12)
    age = fnum(snap.get("oracle_price_age_sec"))
    if age is not None:
        if age <= 10:   score += 12; reasons.append("+12 oracle fresh <=10s")
        elif age <= 30: score += 8;  reasons.append("+8 oracle <=30s")
        elif age <= 90: score += 3;  reasons.append("+3 oracle <=90s")
        else:           reasons.append("+0 oracle stale >90s")

    # 3. No guardian/veto warnings pre-open (10)
    if not snap.get("vetoes_seen") and not snap.get("guardian_warnings"):
        score += 10; reasons.append("+10 no vetoes/guardian warnings")
    else:
        reasons.append("+0 pre-open warnings present")

    # 4. Favourable early movement (15) — only when in-trade data exists
    if early_move_pct is not None:
        if early_move_pct >= 25:   score += 15; reasons.append("+15 early move >=+25%")
        elif early_move_pct >= 10: score += 10; reasons.append("+10 early move >=+10%")
        elif early_move_pct >= 0:  score += 4;  reasons.append("+4 early move flat/positive")
        else:                      reasons.append("+0 early move negative")

    # 5. Runner/lilypad shape proxy: curve momentum + buy velocity (10)
    bv = fnum(snap.get("buy_velocity"), 0.0) or 0.0
    cp = fnum(snap.get("curve_progress_pct"), 0.0) or 0.0
    if bv > 0 and 5.0 <= cp <= 85.0:
        score += 10; reasons.append("+10 runner-shape proxy (velocity + live curve)")
    elif bv > 0 or cp > 0:
        score += 4;  reasons.append("+4 partial runner-shape signal")

    # 6. Hour bucket quality (8) — placeholder-neutral until promotion audit
    #    proves specific hours; never negative to avoid unproven bias.
    if snap.get("hour_aest") is not None:
        score += 4; reasons.append("+4 hour bucket recorded (quality TBD by audit)")

    # 7. Copytrade / smart wallet convergence (10)
    route = str(snap.get("source_route") or "").lower()
    if "copytrade" in route or "smart" in route:
        score += 10; reasons.append("+10 smart-wallet convergence route")

    # 8. Liquidity / mcap sanity (10)
    liq = fnum(snap.get("liquidity_usd"), 0.0) or 0.0
    mc  = fnum(snap.get("market_cap_usd"), 0.0) or 0.0
    if liq >= 5000 and mc >= 3000:
        score += 10; reasons.append("+10 liquidity+mcap sane")
    elif liq > 0 or mc > 0:
        score += 5;  reasons.append("+5 partial liquidity/mcap data")
    else:
        reasons.append("+0 liquidity/mcap missing — coverage gap")

    # 9. Dead-token shape guard (up to -20)
    fresh = fnum(snap.get("freshness_score"))
    if fresh is not None and fresh <= 0.05:
        score -= 20; reasons.append("-20 dead-token shape (freshness ~0)")

    score = max(0.0, min(100.0, score))
    return score, reasons


# ---------------------------------------------------------------- cycle

def run_cycle(matrix_db: Path, intel_db: Path, verbose: bool = False) -> Dict[str, int]:
    now = time.time()
    stats = {"snapshots": 0, "scored": 0, "contracts": 0, "expired": 0}

    mcon = connect_ro(matrix_db)
    icon = connect_intel_rw(intel_db)
    try:
        # ── which candidates already snapped at which stage?
        seen: set = set()
        for r in icon.execute(
            "SELECT candidate_id, stage FROM live_lane_feature_snapshots "
            "WHERE snapped_at > ?", (now - 6 * 3600,)):
            seen.add((r["candidate_id"], r["stage"]))

        # ── pull active candidates from matrix (read-only)
        cand_rows: List[sqlite3.Row] = []
        if table_exists(mcon, "market_snapshots"):
            c = set(cols(mcon, "market_snapshots"))
            tcol = "created_at" if "created_at" in c else "timestamp"
            try:
                cand_rows = mcon.execute(
                    f"SELECT * FROM market_snapshots "
                    f"WHERE COALESCE({tcol},0) > ? "
                    f"ORDER BY COALESCE({tcol},0) DESC LIMIT 400",
                    (now - 2 * 3600,)).fetchall()
            except Exception:
                cand_rows = []

        # ── open positions → early-move + OPENED stage joins
        open_by_mint: Dict[str, sqlite3.Row] = {}
        if table_exists(mcon, "paper_positions"):
            try:
                for r in mcon.execute(
                    "SELECT * FROM paper_positions "
                    "WHERE UPPER(COALESCE(status,''))='OPEN'"):
                    m = pick(r, "mint_address")
                    if m:
                        open_by_mint[str(m)] = r
            except Exception:
                pass

        for row in cand_rows:
            stage = _stage_of(row)
            if stage is None:
                continue
            key = (pick(row, "id"), stage)
            snap = _snapshot_from_candidate(row, now)

            # join open position if present
            pos = open_by_mint.get(str(snap.get("mint_address") or ""))
            early_move = None
            if pos is not None:
                snap["paper_position_id"] = pick(pos, "id")
                snap["opened_at"] = fnum(pick(pos, "opened_at"))
                if snap.get("latched_at") and snap.get("opened_at"):
                    snap["latch_to_open_sec"] = snap["opened_at"] - snap["latched_at"]
                ep = fnum(pick(pos, "entry_price"))
                lp = fnum(pick(pos, "last_price"))
                if ep and lp and ep > 0:
                    early_move = (lp - ep) / ep * 100.0
                if stage != "OPENED":
                    stage = "OPENED"
                    snap["stage"] = "OPENED"
                    key = (pick(row, "id"), "OPENED")

            if key in seen:
                continue

            # write snapshot (Task B)
            fields = [k for k in snap.keys()]
            icon.execute(
                f"INSERT INTO live_lane_feature_snapshots ({','.join(fields)}) "
                f"VALUES ({','.join('?' for _ in fields)})",
                [json.dumps(snap[k]) if isinstance(snap[k], (dict, list)) else snap[k]
                 for k in fields])
            snap_id = icon.execute("SELECT last_insert_rowid()").fetchone()[0]
            stats["snapshots"] += 1
            seen.add(key)

            # score (Task D)
            s, reasons = score_candidate(snap, early_move_pct=early_move)
            stats["scored"] += 1

            blocked_reason = None
            if integrity_bucket(snap.get("price_integrity_status")) == "UNSTABLE":
                status, blocked_reason = "BLOCKED", "UNSTABLE_PRICE_INTEGRITY"
            elif s >= PROMOTE_SCORE:
                status = "PROMOTABLE"   # informational only — nothing executes
            else:
                status = "SHADOW_ONLY"

            icon.execute(
                """INSERT INTO live_lane_shadow_candidates
                   (mint_address, candidate_id, paper_position_id, snap_id,
                    score, score_reasons, rule_name, pass_fail, blocked_reason,
                    live_status, created_at, expires_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (snap.get("mint_address"), snap.get("candidate_id"),
                 snap.get("paper_position_id"), snap_id,
                 round(s, 2), json.dumps(reasons), "SHADOW_SCORE_V1",
                 "PASS" if s >= PROMOTE_SCORE else "FAIL",
                 blocked_reason, status, now, now + CANDIDATE_TTL))
            stats["contracts"] += 1

            if verbose:
                print(f"  [{stage:10}] {str(snap.get('token_name'))[:18]:18} "
                      f"score={s:5.1f} status={status} "
                      f"coverage={snap['feature_completeness_pct']}%")

        # expire stale contract rows
        cur = icon.execute(
            "UPDATE live_lane_shadow_candidates SET live_status='SHADOW_ONLY', "
            "blocked_reason=COALESCE(blocked_reason,'EXPIRED') "
            "WHERE expires_at < ? AND live_status='PROMOTABLE'", (now,))
        stats["expired"] = cur.rowcount if cur.rowcount and cur.rowcount > 0 else 0
        icon.commit()
    finally:
        mcon.close()
        icon.close()
    return stats


def main() -> int:
    ap = argparse.ArgumentParser(description="Live-lane shadow scorer (non-executing)")
    ap.add_argument("--matrix-db", default=None)
    ap.add_argument("--intel-db", default=None)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--poll", type=float, default=POLL_SECONDS)
    a = ap.parse_args()

    matrix = find_matrix_db(a.matrix_db)
    if matrix is None:
        print("[FAIL] matrix DB not found — pass --matrix-db")
        return 1
    intel = find_intel_db(a.intel_db)
    ensure_live_lane_schema(intel)

    print(f"[live_lane_shadow_score] matrix={matrix} (READ-ONLY)")
    print(f"[live_lane_shadow_score] intel ={intel} (writes)")
    print("[live_lane_shadow_score] SHADOW ONLY — no signing, no swaps, no live flags")

    while True:
        try:
            st = run_cycle(matrix, intel, verbose=a.verbose)
            _heartbeat("RUNNING",
                       f"snaps+{st['snapshots']} scored+{st['scored']}")
            if a.once or a.verbose:
                print(f"[cycle] {st}")
        except sqlite3.OperationalError as e:
            _heartbeat("DEGRADED", str(e)[:120])
            print(f"[warn] sqlite busy/locked: {e}")
        except Exception as e:
            _heartbeat("ERROR", str(e)[:120])
            print(f"[error] {type(e).__name__}: {e}")
        if a.once:
            return 0
        time.sleep(max(1.0, a.poll))


if __name__ == "__main__":
    sys.exit(main())
