#!/usr/bin/env python3
"""
SIGNOFF_LIVE_LANE_REPAIR_20260715 — GATE VARIANT REPLAY HARNESS (READ-ONLY)

Replays every recorded Mode B live-gate decision (mode_b_decision_ledger)
against four gate variants and joins each decision to its actual SIM outcome
(paper_positions), so gate changes are judged on the operator's own executed
history rather than on theory.

VARIANTS
  1. CURRENT          — verdicts exactly as recorded.
  2. ORACLE_CORRECTED — global oracle=STALLED is no longer an unconditional
                        veto. Where envelope telemetry was recorded, the
                        override applies only inside the profitable envelope
                        (hot p90<=44.5s, any p90<=10.9s, wpm>=8.6, candidate
                        price age<=30s). Rows predating telemetry are counted
                        under an OPTIMISTIC assumption and reported separately.
  3. SOFT_CURVE       — curve<floor is not a veto when curve>=ABS_MIN
                        (default 0.30 SOL); cohort modeled at HALF live size
                        with a 6% round-trip impact cap.
  4. ADAPTIVE_REGIME  — trailing-3h runner-rate regime adds +4 score in
                        RUNNER_RICH windows (equivalent to threshold-4).
                        Hard blocks are never relaxed by regime.

USAGE (from the trading-bot workspace root):
  python launch/replay_gate_variants.py            # uses core.schema DB_PATH
  python launch/replay_gate_variants.py --db C:\\path\\to\\sentinuity.db
  python launch/replay_gate_variants.py --live-size 25 --hours 336

This script NEVER writes to the database. It opens the DB in read-only mode.
"""
from __future__ import annotations

import argparse
import re
import sqlite3
import sys
import time
from pathlib import Path

# ── configuration defaults (mirror the live gate) ───────────────────────────
ENV_HOT_MAX = 44.5
ENV_ANY_MAX = 10.9
ENV_WPM_MIN = 8.6
ENV_CAND_PRICE_MAX = 30.0
CURVE_ABS_MIN_SOL = 0.30
SOFT_CURVE_MAX_RT_PCT = 6.0
REGIME_WINDOW_H = 3.0
REGIME_MIN_TRADES = 8
REGIME_RUNNER_RATE_PCT = 22.0
REGIME_SCORE_BONUS = 4.0
RUNNER_PEAK_PCT = 80.0
ORDINARY_LOSS_PCT = -25.0
CATASTROPHIC_LOSS_PCT = -75.0
OUTCOME_JOIN_TOL_SEC = 180.0

SCORE_RE = re.compile(r"^score=([\d.]+)<([\d.]+)$")
CURVE_RE = re.compile(r"^curve=([\d.]+)SOL<([\d.]+)SOL$")
ORACLE_STALLED_RE = re.compile(r"^oracle=STALLED")


def resolve_db(cli_db: str | None) -> Path:
    if cli_db:
        return Path(cli_db)
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        from core.schema import DB_PATH  # type: ignore
        return Path(str(DB_PATH))
    except Exception:
        print("ERROR: could not import core.schema.DB_PATH — pass --db explicitly.")
        sys.exit(2)


def ro_connect(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def table_cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def load_decisions(conn: sqlite3.Connection, since: float) -> list[dict]:
    cols = table_cols(conn, "mode_b_decision_ledger")
    if not cols:
        print("ERROR: mode_b_decision_ledger not found. Run the engine first.")
        sys.exit(2)
    want = ["id", "mint_address", "token_name", "evaluated_at", "verdict", "reasons",
            "live_safe_score", "score_threshold", "curve_sol_reserves",
            "oracle_state", "price_age_sec", "oracle_hot_age_sec",
            "oracle_any_age_sec", "oracle_wpm", "half_size", "regime_state"]
    sel = ", ".join(c if c in cols else f"NULL AS {c}" for c in want)
    rows = conn.execute(
        f"SELECT {sel} FROM mode_b_decision_ledger "
        "WHERE evaluated_at >= ? ORDER BY evaluated_at ASC", (since,)
    ).fetchall()
    return [dict(r) for r in rows]


def load_outcomes(conn: sqlite3.Connection, since: float) -> list[dict]:
    cols = table_cols(conn, "paper_positions")
    peak = "held_peak_pct" if "held_peak_pct" in cols else "NULL"
    rows = conn.execute(
        f"SELECT id, mint_address, opened_at, closed_at, status, "
        f"CAST(COALESCE(position_size_usd,0) AS REAL) AS size_usd, "
        f"CAST(COALESCE(realized_pnl_usd,0) AS REAL) AS pnl_usd, "
        f"CAST(COALESCE({peak},0) AS REAL) AS peak_pct "
        "FROM paper_positions "
        "WHERE UPPER(COALESCE(funding_mode,'SIM'))='SIM' AND opened_at >= ? "
        "ORDER BY opened_at ASC", (since - 3600,)
    ).fetchall()
    return [dict(r) for r in rows]


def join_outcome(dec: dict, outcomes: list[dict]) -> dict | None:
    best, best_dt = None, OUTCOME_JOIN_TOL_SEC + 1
    t = float(dec["evaluated_at"] or 0)
    for o in outcomes:
        if o["mint_address"] != dec["mint_address"]:
            continue
        dt = abs(float(o["opened_at"] or 0) - t)
        if dt < best_dt:
            best, best_dt = o, dt
    return best if best_dt <= OUTCOME_JOIN_TOL_SEC else None


def parse_reasons(reasons: str) -> list[str]:
    return [t.strip() for t in (reasons or "").split("|") if t.strip()]


def trailing_regime(outcomes: list[dict], at_ts: float) -> bool:
    """RUNNER_RICH iff trailing-window closed SIM trades meet the rate floor."""
    lo = at_ts - REGIME_WINDOW_H * 3600.0
    closed = [o for o in outcomes
              if o["status"] == "CLOSED" and o["closed_at"]
              and lo <= float(o["closed_at"]) <= at_ts]
    if len(closed) < REGIME_MIN_TRADES:
        return False
    runners = sum(1 for o in closed if float(o["peak_pct"] or 0) >= RUNNER_PEAK_PCT)
    return runners / len(closed) * 100.0 >= REGIME_RUNNER_RATE_PCT


def evaluate_variant(dec: dict, variant: str, outcomes: list[dict],
                     stats: dict) -> tuple[bool, bool, float]:
    """Return (passes, half_size, approx_flag_used)."""
    tokens = parse_reasons(dec["reasons"])
    if dec["verdict"] == "PASS" or not tokens:
        return True, bool(dec.get("half_size") or 0), False

    score = float(dec["live_safe_score"] or 0.0)
    threshold = float(dec["score_threshold"] or 74.0)
    half = bool(dec.get("half_size") or 0)
    approx = False
    remaining: list[str] = []

    for tok in tokens:
        m_score = SCORE_RE.match(tok)
        if m_score:
            continue  # score re-tested at the end against (possibly) new values

        if variant in ("ORACLE_CORRECTED", "ALL") and ORACLE_STALLED_RE.match(tok):
            hot = dec.get("oracle_hot_age_sec")
            any_a = dec.get("oracle_any_age_sec")
            wpm = dec.get("oracle_wpm")
            page = dec.get("price_age_sec")
            if hot is None or any_a is None or wpm is None or page is None:
                approx = True  # pre-telemetry row: optimistic override
                score -= 5.0
                continue
            inside = (float(hot) <= ENV_HOT_MAX and float(any_a) <= ENV_ANY_MAX
                      and float(wpm) >= ENV_WPM_MIN
                      and float(page) <= ENV_CAND_PRICE_MAX)
            if inside:
                score -= 5.0
                continue
            remaining.append(tok)
            continue

        m_curve = CURVE_RE.match(tok)
        if variant in ("SOFT_CURVE", "ALL") and m_curve:
            curve = float(m_curve.group(1))
            if curve >= CURVE_ABS_MIN_SOL:
                # cohort fires at half size; model impact at half live notional
                half = True
                reserve_usd = curve * stats["sol_usd"]
                sz = stats["live_size"] * 0.5
                rt = (sz / (reserve_usd + sz) * 100.0) * 2.0
                if rt > SOFT_CURVE_MAX_RT_PCT:
                    remaining.append(f"rt_impact_modeled={rt:.1f}%>{SOFT_CURVE_MAX_RT_PCT}%")
                else:
                    score += 6.0
                continue
            remaining.append(tok)
            continue

        remaining.append(tok)

    if variant in ("ADAPTIVE_REGIME", "ALL") and trailing_regime(
            outcomes, float(dec["evaluated_at"] or 0)):
        score += REGIME_SCORE_BONUS

    if remaining:
        return False, half, approx
    if score < threshold:
        return False, half, approx
    return True, half, approx


def classify(pnl_pct: float, peak_pct: float) -> str:
    if peak_pct >= RUNNER_PEAK_PCT:
        return "runner"
    if pnl_pct <= CATASTROPHIC_LOSS_PCT:
        return "catastrophic"
    if pnl_pct <= ORDINARY_LOSS_PCT:
        return "major"
    if pnl_pct < 0:
        return "ordinary_loss"
    return "ordinary_win"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default=None, help="path to sentinuity sqlite db")
    ap.add_argument("--hours", type=float, default=336.0, help="lookback window (default 14d)")
    ap.add_argument("--live-size", type=float, default=25.0,
                    help="modeled live notional USD per trade")
    ap.add_argument("--sol-usd", type=float, default=150.0)
    args = ap.parse_args()

    db = resolve_db(args.db)
    if not db.exists():
        print(f"ERROR: db not found: {db}")
        sys.exit(2)
    since = time.time() - args.hours * 3600.0
    conn = ro_connect(db)
    decisions = load_decisions(conn, since)
    outcomes = load_outcomes(conn, since)
    print(f"DB: {db}\nDecisions: {len(decisions)}  SIM outcomes: {len(outcomes)}  "
          f"window: last {args.hours:.0f}h\n")
    if not decisions:
        print("No gate decisions in window — nothing to replay.")
        return

    stats = {"live_size": args.live_size, "sol_usd": args.sol_usd}
    variants = ["CURRENT", "ORACLE_CORRECTED", "SOFT_CURVE", "ADAPTIVE_REGIME", "ALL"]
    header = (f"{'VARIANT':<18}{'PASS':>6}{'RUNNERS':>9}{'ORD-L':>7}{'MAJ-L':>7}"
              f"{'CAT-L':>7}{'NET$':>10}{'MAXLOSS$':>10}{'MAXEXP$':>9}{'~APPROX':>9}")
    print(header)
    print("-" * len(header))
    newly_captured: dict[str, list[str]] = {}
    for v in variants:
        n_pass = runners = ord_l = maj_l = cat_l = approx_n = 0
        net = 0.0
        max_loss = 0.0
        intervals: list[tuple[float, float, float]] = []
        captured: list[str] = []
        for d in decisions:
            if v == "CURRENT":
                ok = d["verdict"] == "PASS"
                half = bool(d.get("half_size") or 0)
                ap_used = False
            else:
                ok, half, ap_used = evaluate_variant(d, v, outcomes, stats)
            if not ok:
                continue
            n_pass += 1
            approx_n += int(ap_used)
            o = join_outcome(d, outcomes)
            if not o or o["status"] != "CLOSED" or not o["size_usd"]:
                continue
            pnl_pct = o["pnl_usd"] / o["size_usd"] * 100.0
            size = stats["live_size"] * (0.5 if half else 1.0)
            pnl_usd = pnl_pct / 100.0 * size
            net += pnl_usd
            max_loss = min(max_loss, pnl_usd)
            cls = classify(pnl_pct, float(o["peak_pct"] or 0))
            if cls == "runner":
                runners += 1
                if v != "CURRENT" and d["verdict"] != "PASS":
                    captured.append(
                        f"  + {d['token_name'] or d['mint_address'][:10]} "
                        f"peak={o['peak_pct']:.0f}% pnl={pnl_pct:+.1f}% "
                        f"blocked_by=[{d['reasons']}]")
            elif cls == "catastrophic":
                cat_l += 1
            elif cls == "major":
                maj_l += 1
            elif cls == "ordinary_loss":
                ord_l += 1
            if o["closed_at"]:
                intervals.append((float(o["opened_at"]), float(o["closed_at"]), size))
        # peak concurrent exposure
        events = sorted([(a, s) for a, _, s in intervals] +
                        [(b, -s) for _, b, s in intervals])
        cur = peak_exp = 0.0
        for _, delta in events:
            cur += delta
            peak_exp = max(peak_exp, cur)
        print(f"{v:<18}{n_pass:>6}{runners:>9}{ord_l:>7}{maj_l:>7}{cat_l:>7}"
              f"{net:>10.2f}{max_loss:>10.2f}{peak_exp:>9.2f}{approx_n:>9}")
        newly_captured[v] = captured

    print("\nNEWLY CAPTURED RUNNERS (blocked under CURRENT, pass under variant):")
    for v, lst in newly_captured.items():
        if v == "CURRENT" or not lst:
            continue
        print(f" {v}:")
        for line in lst[:25]:
            print(line)

    print("\nHONESTY NOTES:")
    print(" * SIM realized %% is used as the live outcome model; live fills will")
    print("   differ by real slippage/fees. Treat NET$ as comparative, not absolute.")
    print(" * ~APPROX counts pre-telemetry rows where the oracle envelope could")
    print("   not be verified and the override was assumed. Re-run after 24-48h of")
    print("   telemetry-stamped decisions for a strict, fully-verified comparison.")
    print(" * Nothing was written to the database (opened read-only).")


if __name__ == "__main__":
    main()
