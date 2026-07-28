#!/usr/bin/env python3
"""
services/trajectory_score.py — System Trajectory Score (Task G backend)

Computes a composite 0–100 score from twelve REAL measured components and
writes it to sentinuity_intelligence.db::trajectory_score_history. The UI
rainbow chart reads only this table — it cannot invent an incline.

DOCTRINE:
  * NOT hardcoded "up only". Every component is measured each run; if the
    system regresses, the score drops and the chart bends down.
  * A component that cannot be measured contributes NOTHING and its weight
    is redistributed across measured components; components_measured is
    recorded so coverage itself is visible.
  * Matrix DB is read-only. Writes go to the intelligence DB only.

Components (weights sum to 100):
   1. paper net PnL (window)            12
   2. profit factor                     12
   3. monster capture count             10
   4. bad-loss suppression              10
   5. clean-price ratio                 10
   6. live-lane shadow expectancy        8
   7. prediction coverage                10
   8. calibration quality (score sep.)   8
   9. service health uptime              6
  10. archive/history continuity         5
  11. autonomous build quality           5
  12. substrate/copytrade contribution   4

Usage:
  python services/trajectory_score.py --once        # compute + store one row
  python services/trajectory_score.py --once --hours 24
  python services/trajectory_score.py               # loop every 10 min
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.live_lane_common import (  # noqa: E402
    find_matrix_db, find_intel_db, connect_ro, connect_intel_rw,
    table_exists, load_closed_positions, profit_factor, fnum,
)
from core.intelligence_schema_live_lane import ensure_live_lane_schema  # noqa: E402

WEIGHTS = {
    "c1": 12, "c2": 12, "c3": 10, "c4": 10, "c5": 10, "c6": 8,
    "c7": 10, "c8": 8, "c9": 6, "c10": 5, "c11": 5, "c12": 4,
}

BANDS = [
    (0,  20, "RED"),      # regression / risk / dead state
    (20, 40, "ORANGE"),   # unstable but learning
    (40, 55, "GOLD"),     # profitable paper logic
    (55, 70, "GREEN"),    # clean profitable repeatability
    (70, 85, "CYAN"),     # predictive confidence improving
    (85, 101, "VIOLET"),  # multi-lane autonomy leverage
]


def band_for(score: float) -> str:
    for lo, hi, name in BANDS:
        if lo <= score < hi:
            return name
    return "RED"


def _clamp01(x: Optional[float]) -> Optional[float]:
    if x is None:
        return None
    return max(0.0, min(1.0, x))


# ---------------------------------------------------------------- components
# Each returns a value in [0,1] or None (= not measurable).

def comp_trading(trades: List[Dict[str, Any]]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {"c1": None, "c2": None, "c3": None,
                                       "c4": None, "c5": None}
    if not trades:
        return out
    pnls = [t["pnl_usd"] or 0.0 for t in trades]
    net = sum(pnls)
    # c1: net PnL — 0 at <=-$200, 0.5 at $0, 1.0 at >= +$1500 (piecewise)
    if net <= 0:
        out["c1"] = _clamp01(0.5 + net / 400.0)
    else:
        out["c1"] = _clamp01(0.5 + 0.5 * min(1.0, net / 1500.0))
    # c2: profit factor — 1.0 PF→0.4, 2.0 PF→0.8, >=3 PF→1.0
    pf = profit_factor(pnls)
    if pf is None:
        out["c2"] = None
    elif pf == float("inf"):
        out["c2"] = 1.0
    else:
        out["c2"] = _clamp01(pf / 3.0 if pf < 3 else 1.0) * (1.0 if pf >= 1 else 0.5)
        if pf < 1.0:
            out["c2"] = _clamp01(pf * 0.4)
    # c3: monster capture — 0 monsters→0.2 floor if net>0, scale to 1.0 at 13+
    monsters = sum(1 for t in trades if t["is_monster"])
    out["c3"] = _clamp01((monsters / 13.0) if monsters else (0.2 if net > 0 else 0.0))
    # c4: bad-loss suppression — share of trades that are bad losses, inverted
    bad = sum(1 for t in trades if t["is_bad_loss"])
    out["c4"] = _clamp01(1.0 - (bad / max(1, len(trades))) * 10.0)
    # c5: clean-price ratio
    known = [t for t in trades if t["integrity"] != "UNKNOWN"]
    if known:
        out["c5"] = _clamp01(sum(1 for t in known if t["integrity"] == "CLEAN") / len(known))
    return out


def comp_shadow(icon, since: float) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {"c6": None, "c7": None, "c8": None}
    if not table_exists(icon, "live_lane_shadow_candidates"):
        return out
    rows = icon.execute(
        "SELECT c.score, c.paper_position_id FROM live_lane_shadow_candidates c "
        "WHERE c.created_at >= ?", (since,)).fetchall()
    if not rows:
        return out
    # c7: prediction coverage — fraction of recent snapshots with completeness data
    if table_exists(icon, "live_lane_feature_snapshots"):
        snaps = icon.execute(
            "SELECT feature_completeness_pct FROM live_lane_feature_snapshots "
            "WHERE snapped_at >= ?", (since,)).fetchall()
        if snaps:
            vals = [fnum(s[0], 0.0) or 0.0 for s in snaps]
            out["c7"] = _clamp01((sum(vals) / len(vals)) / 100.0)
    # c6/c8 need joined outcomes — computed only where positions joined & closed
    # (deferred to matrix join by caller; kept None here if not derivable)
    return out


def comp_shadow_outcomes(icon, mcon, since: float) -> Dict[str, Optional[float]]:
    """c6 shadow expectancy + c8 calibration (winner/loser score separation)."""
    out: Dict[str, Optional[float]] = {"c6": None, "c8": None}
    if not (table_exists(icon, "live_lane_shadow_candidates")
            and table_exists(mcon, "paper_positions")):
        return out
    cand = icon.execute(
        "SELECT paper_position_id, MAX(score) AS score "
        "FROM live_lane_shadow_candidates "
        "WHERE created_at >= ? AND paper_position_id IS NOT NULL "
        "GROUP BY paper_position_id", (since,)).fetchall()
    if not cand:
        return out
    ids = [int(r["paper_position_id"]) for r in cand]
    score_by_id = {int(r["paper_position_id"]): fnum(r["score"], 0.0) or 0.0 for r in cand}
    q = ",".join("?" for _ in ids)
    try:
        rows = mcon.execute(
            f"SELECT id, realized_pnl_usd FROM paper_positions "
            f"WHERE id IN ({q}) AND UPPER(COALESCE(status,''))='CLOSED'", ids).fetchall()
    except Exception:
        return out
    if not rows:
        return out
    hi = [fnum(r["realized_pnl_usd"], 0.0) or 0.0 for r in rows
          if score_by_id.get(int(r["id"]), 0) >= 75]
    win_scores  = [score_by_id[int(r["id"])] for r in rows
                   if (fnum(r["realized_pnl_usd"], 0.0) or 0.0) > 0]
    loss_scores = [score_by_id[int(r["id"])] for r in rows
                   if (fnum(r["realized_pnl_usd"], 0.0) or 0.0) <= 0]
    if hi:
        exp = sum(hi) / len(hi)          # $ expectancy of high-score cohort
        out["c6"] = _clamp01(0.5 + exp / 40.0)
    if win_scores and loss_scores:
        sep = (sum(win_scores)/len(win_scores)) - (sum(loss_scores)/len(loss_scores))
        out["c8"] = _clamp01(0.5 + sep / 40.0)   # +20pt separation → 1.0
    return out


def comp_health(mcon, since: float) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {"c9": None, "c10": None,
                                       "c11": None, "c12": None}
    now = time.time()
    # c9: service uptime — core heartbeats fresh
    if table_exists(mcon, "system_heartbeat"):
        try:
            rows = mcon.execute("SELECT * FROM system_heartbeat").fetchall()
            core = ("ws_price_oracle", "ingest_pipeline",
                    "market_intelligence", "execution_engine")
            fresh = 0; found = 0
            for r in rows:
                name = str(dict(zip([c[0] for c in mcon.execute(
                    "SELECT * FROM system_heartbeat LIMIT 0").description], r)).get(
                    "service_name", "") if not hasattr(r, "keys") else
                    (r["service_name"] if "service_name" in r.keys() else ""))
                if any(cname in name for cname in core):
                    found += 1
                    ts = None
                    for k in ("updated_at", "timestamp", "last_success_at"):
                        if hasattr(r, "keys") and k in r.keys():
                            ts = fnum(r[k]); break
                    if ts and now - ts < 120:
                        fresh += 1
            if found:
                out["c9"] = _clamp01(fresh / found)
        except Exception:
            pass
    # c10: history continuity — closed trades visible over trailing 72h
    try:
        t72 = load_closed_positions(mcon, since_epoch=now - 72 * 3600)
        out["c10"] = _clamp01(min(1.0, len(t72) / 50.0)) if t72 else 0.0
    except Exception:
        pass
    # c11: autonomous build quality — patches applied without rollback
    if table_exists(mcon, "patch_history"):
        try:
            rows = mcon.execute(
                "SELECT status FROM patch_history WHERE COALESCE(applied_at, created_at, 0) >= ?",
                (since,)).fetchall()
            if rows:
                good = sum(1 for r in rows if "ROLL" not in str(r[0] or "").upper()
                           and "FAIL" not in str(r[0] or "").upper())
                out["c11"] = _clamp01(good / len(rows))
        except Exception:
            pass
    # c12: substrate/copytrade contribution
    if table_exists(mcon, "smart_wallet_trades"):
        try:
            n = mcon.execute(
                "SELECT COUNT(*) FROM smart_wallet_trades "
                "WHERE COALESCE(created_at, timestamp, 0) >= ?", (since,)).fetchone()[0]
            out["c12"] = _clamp01(min(1.0, n / 20.0))
        except Exception:
            pass
    return out


# ---------------------------------------------------------------- composite

def compute(window_hours: float = 12.0,
            matrix_db: Optional[Path] = None,
            intel_db: Optional[Path] = None) -> Dict[str, Any]:
    since = time.time() - window_hours * 3600
    comps: Dict[str, Optional[float]] = {f"c{i}": None for i in range(1, 13)}

    matrix = find_matrix_db(str(matrix_db) if matrix_db else None)
    intel = find_intel_db(str(intel_db) if intel_db else None)

    mcon = connect_ro(matrix) if matrix else None
    icon = connect_ro(intel) if intel.exists() else None
    try:
        if mcon:
            trades = load_closed_positions(mcon, since_epoch=since)
            comps.update(comp_trading(trades))
            comps.update(comp_health(mcon, since))
        if icon:
            comps.update({k: v for k, v in comp_shadow(icon, since).items()
                          if v is not None})
            if mcon:
                comps.update({k: v for k, v in
                              comp_shadow_outcomes(icon, mcon, since).items()
                              if v is not None})
    finally:
        if mcon: mcon.close()
        if icon: icon.close()

    measured = {k: v for k, v in comps.items() if v is not None}
    if measured:
        wsum = sum(WEIGHTS[k] for k in measured)
        score = sum(v * WEIGHTS[k] for k, v in measured.items()) / wsum * 100.0
    else:
        score = 0.0

    # DOCTRINE GATE: CYAN (>=70) means "predictive confidence improving" and
    # VIOLET (>=85) means "multi-lane autonomy". Those bands may only be
    # reached when predictive components are actually measured — profitable
    # paper trading alone caps at the top of GREEN.
    predictive_measured = sum(1 for k in ("c6", "c7", "c8") if comps.get(k) is not None)
    if predictive_measured < 2:
        score = min(score, 69.9)          # cannot enter CYAN
    if comps.get("c6") is None or comps.get("c8") is None:
        score = min(score, 84.9)          # cannot enter VIOLET
    score = round(max(0.0, min(100.0, score)), 2)
    return {
        "computed_at": time.time(),
        "window_label": f"{window_hours:.0f}H",
        "trajectory_score": score,
        "band": band_for(score),
        "components": comps,
        "components_measured": len(measured),
    }


def store(result: Dict[str, Any], intel_db: Optional[Path] = None) -> None:
    path = find_intel_db(str(intel_db) if intel_db else None)
    ensure_live_lane_schema(path)
    con = connect_intel_rw(path)
    try:
        c = result["components"]
        con.execute(
            """INSERT INTO trajectory_score_history
               (computed_at, window_label, trajectory_score, band,
                c1_paper_net_pnl, c2_profit_factor, c3_monster_capture,
                c4_bad_loss_suppression, c5_clean_price_ratio,
                c6_shadow_expectancy, c7_prediction_coverage,
                c8_calibration_quality, c9_service_uptime,
                c10_history_continuity, c11_build_quality,
                c12_substrate_contrib, components_measured, notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (result["computed_at"], result["window_label"],
             result["trajectory_score"], result["band"],
             c["c1"], c["c2"], c["c3"], c["c4"], c["c5"], c["c6"],
             c["c7"], c["c8"], c["c9"], c["c10"], c["c11"], c["c12"],
             result["components_measured"],
             "measured composite; unmeasured components excluded from weighting"))
        con.commit()
    finally:
        con.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=float, default=12.0)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=600.0)
    ap.add_argument("--matrix-db", default=None)
    ap.add_argument("--intel-db", default=None)
    a = ap.parse_args()

    while True:
        r = compute(a.hours,
                    Path(a.matrix_db) if a.matrix_db else None,
                    Path(a.intel_db) if a.intel_db else None)
        store(r, Path(a.intel_db) if a.intel_db else None)
        print(f"[trajectory] score={r['trajectory_score']:.2f} band={r['band']} "
              f"measured={r['components_measured']}/12 window={r['window_label']}")
        for k in sorted(r["components"], key=lambda x: int(x[1:])):
            v = r["components"][k]
            print(f"   {k:4} = {'—' if v is None else f'{v:.3f}'}")
        if a.once:
            return 0
        time.sleep(max(30.0, a.interval))


if __name__ == "__main__":
    sys.exit(main())
