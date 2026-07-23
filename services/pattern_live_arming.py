#!/usr/bin/env python3
"""Causal pattern permission gate for controlled funded canaries.

Sign-off doctrine (2026-07-17 research validation):
- Pattern evidence grants permission; execution parity grants size.
- Two independent realised successes among the last six causal outcomes may arm
  a capped half-canary.
- Paper confirmation never independently unlocks full size.
- The launch-selected amount is the full target size. Live begins at 0.5x and
  earns 1.0x only after three documentary, reconciled closed live canaries show
  positive cumulative PnL, at least two profitable closes, no >=10% loss, and
  no unresolved/open real transaction. Qualification is re-evaluated each fire.
- Confirmations must be distinct by mint, position and discovery cohort.
- A trusted persisted paper peak may confirm the historical Gold Runner cycle
  and permit only the capped half-canary. Full size still requires the current
  documentary live-canary maturity contract.
- One L/H/X resets the active sequence (conservative until decay is validated).
- This module cannot bypass oracle, wallet, route, capacity or executor gates.
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Optional

from core.outcome_taxonomy import classify_realised, is_reset, is_success

@dataclass(frozen=True)
class PatternPermission:
    state: str
    armed: bool
    size_multiplier: float
    confirmations: int
    anchor_ts: Optional[float]
    expires_ts: Optional[float]
    reason: str


def _f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value if value is not None else default)
    except (TypeError, ValueError):
        return default


def _cohort_key(row: Any) -> str:
    """Prefer cluster lineage, then raw-DNA lineage, then snapshot identity."""
    for name, prefix in (("cluster_id", "cluster"), ("raw_dna_id", "dna"), ("snapshot_id", "snapshot")):
        try:
            value = row[name]
        except Exception:
            value = None
        if value not in (None, "", 0, "0"):
            return f"{prefix}:{value}"
    # Missing lineage must not accidentally merge unrelated candidates. Position
    # identity is a safe fallback, while the reason marks lineage as unavailable.
    try:
        return f"position:{row['id']}"
    except Exception:
        return "unknown"


def _row_outcome(row: Any) -> str:
    """Reproduce the post-13-July Gold Runner confirmation contract.

    The profitable pattern-upgrade state classified a completed SIM outcome from
    the better of realised return and its trusted persisted high-water mark.
    This restores pattern recognition when a genuine runner was observed but
    exit leakage reduced the final realised result. Full funded size remains
    governed separately by `_live_size_stage`.
    """
    size = abs(_f(row["position_size_usd"], 0.0))
    pnl = _f(row["realized_pnl_usd"], 0.0)
    realised_pct = (pnl / size * 100.0) if size > 1e-12 else 0.0

    peak_pct = None
    try:
        if "pattern_peak_pct" in row.keys() and row["pattern_peak_pct"] is not None:
            peak_pct = _f(row["pattern_peak_pct"], realised_pct)
    except Exception:
        peak_pct = None

    achieved_pct = max(realised_pct, peak_pct if peak_pct is not None else realised_pct)
    return classify_realised(achieved_pct)



def _live_size_stage(conn) -> tuple[float, str]:
    """Return the earned live-size multiplier and documentary reason.

    The operator-selected LIVE_POSITION_SIZE_USD is the 1.0x target. Until the
    lane proves itself through closed, chain-documented canaries, funded entries
    remain capped at 0.5x. Full size is earned dynamically and is revoked when
    the recent verified-live window no longer satisfies the contract.
    """
    try:
        cols = {str(r[1]) for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
        needed = {"status", "funding_mode", "realized_pnl_usd", "position_size_usd",
                  "buy_tx_sig", "sell_tx_sig"}
        if not needed.issubset(cols):
            return 0.5, "live_maturity_schema_incomplete"

        unresolved_clause = (
            "UPPER(COALESCE(live_state,'')) IN "
            "('BUY_SUBMITTED','BUY_CONFIRMED_UNRESOLVED','OPEN_REAL','SELL_TRIGGERED',"
            "'EXIT_INTENT','SELL_SUBMITTED','SELL_CONFIRMED_UNRESOLVED')"
            if "live_state" in cols else "UPPER(COALESCE(status,''))='OPEN'"
        )
        unresolved = int(conn.execute(
            "SELECT COUNT(*) FROM paper_positions "
            "WHERE UPPER(COALESCE(funding_mode,'SIM'))='REAL' AND (" + unresolved_clause + ")"
        ).fetchone()[0] or 0)
        if unresolved:
            return 0.5, f"live_maturity_unresolved={unresolved}"

        live_state_select = ", live_state" if "live_state" in cols else ""
        rows = conn.execute(
            "SELECT realized_pnl_usd, position_size_usd, buy_tx_sig, sell_tx_sig"
            + live_state_select +
            " FROM paper_positions WHERE UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
            "AND UPPER(COALESCE(status,''))='CLOSED' ORDER BY CAST(COALESCE(closed_at,0) AS REAL) DESC, id DESC LIMIT 3"
        ).fetchall()
        if len(rows) < 3:
            return 0.5, f"live_maturity_canaries={len(rows)}/3"

        pnl_total = 0.0
        profitable = 0
        worst_pct = 0.0
        for row in rows:
            pnl = _f(row[0], 0.0)
            size = abs(_f(row[1], 0.0))
            buy_sig = str(row[2] or "").strip()
            sell_sig = str(row[3] or "").strip()
            if len(buy_sig) < 32 or len(sell_sig) < 32:
                return 0.5, "live_maturity_missing_chain_signature"
            if live_state_select and str(row[4] or "").strip().upper() not in {"SETTLED", "CLOSED", ""}:
                return 0.5, f"live_maturity_unsettled_state={row[4]}"
            pct = (pnl / size * 100.0) if size > 1e-12 else 0.0
            pnl_total += pnl
            profitable += int(pnl > 0.0)
            worst_pct = min(worst_pct, pct)

        if pnl_total <= 0.0:
            return 0.5, f"live_maturity_net_not_positive={pnl_total:.4f}"
        if profitable < 2:
            return 0.5, f"live_maturity_profitable={profitable}/3"
        if worst_pct <= -10.0:
            return 0.5, f"live_maturity_worst_pct={worst_pct:.2f}"
        return 1.0, (f"live_maturity_earned:3_verified;profitable={profitable};"
                     f"net={pnl_total:.4f};worst_pct={worst_pct:.2f}")
    except Exception as exc:
        return 0.5, f"live_maturity_error:{type(exc).__name__}:{exc}"

def evaluate_pattern_permission(
    conn,
    candidate_entry_ts: Optional[float] = None,
    *,
    window_sec: float = 900.0,
    lookback_outcomes: int = 6,
    min_open_separation_sec: float = 120.0,
) -> PatternPermission:
    """Evaluate permission from causal realised outcomes only.

    Compatibility contract: this signature and PatternPermission shape are kept
    exactly compatible with execution_engine.py. The active executor/config may
    continue to pass the existing 900-second horizon. The validated authority is
    the last-six count plus breadth; no unvalidated time constant is forced here.
    """
    now = float(candidate_entry_ts or time.time())
    try:
        def table_cols(table: str) -> set[str]:
            return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}

        pp_cols = table_cols("paper_positions")
        d_cols = table_cols("mode_b_decision_ledger")
        s_cols = table_cols("market_snapshots")
        required = {"id", "mint_address", "opened_at", "closed_at", "position_size_usd", "realized_pnl_usd"}
        if not required.issubset(pp_cols):
            missing = sorted(required - pp_cols)
            return PatternPermission("UNAVAILABLE", False, 0.0, 0, None, None,
                                     f"pattern_schema_missing:{','.join(missing)}")
        if not {"id", "position_id", "snapshot_id"}.issubset(d_cols):
            return PatternPermission("UNAVAILABLE", False, 0.0, 0, None, None,
                                     "pattern_schema_missing:mode_b_lineage")
        if "id" not in s_cols:
            return PatternPermission("UNAVAILABLE", False, 0.0, 0, None, None,
                                     "pattern_schema_missing:market_snapshot_id")

        funding_filter = (
            "AND UPPER(COALESCE(p.funding_mode,'SIM'))='SIM'"
            if "funding_mode" in pp_cols else ""
        )
        peak_candidates = [
            name for name in ("held_peak_pct", "peak_pnl_pct", "max_pnl_pct", "final_exec_pct")
            if name in pp_cols
        ]
        peak_expr = (
            "COALESCE(" + ",".join(f"p.{name}" for name in peak_candidates) + ")"
            if peak_candidates else "NULL"
        )
        d_order = "d2.evaluated_at DESC, d2.id DESC" if "evaluated_at" in d_cols else "d2.id DESC"
        cluster_expr = "s.cluster_id" if "cluster_id" in s_cols else "NULL"
        dna_expr = "s.raw_dna_id" if "raw_dna_id" in s_cols else "NULL"
        discovery_parts = []
        if "timestamp" in s_cols:
            discovery_parts.append("s.timestamp")
        if "created_at" in s_cols:
            discovery_parts.append("s.created_at")
        if "updated_at" in s_cols:
            discovery_parts.append("s.updated_at")
        if "evaluated_at" in d_cols:
            discovery_parts.append("d.evaluated_at")
        discovery_parts.append("p.opened_at")
        discovery_expr = "COALESCE(" + ",".join(discovery_parts) + ")"

        sql = f"""
            SELECT p.id, p.mint_address, p.opened_at, p.closed_at,
                   p.position_size_usd, p.realized_pnl_usd,
                   {peak_expr} AS pattern_peak_pct,
                   d.snapshot_id,
                   {cluster_expr} AS cluster_id,
                   {dna_expr} AS raw_dna_id,
                   {discovery_expr} AS discovery_ts
              FROM paper_positions p
              LEFT JOIN mode_b_decision_ledger d
                ON d.id = (SELECT d2.id FROM mode_b_decision_ledger d2
                            WHERE d2.position_id=p.id ORDER BY {d_order} LIMIT 1)
              LEFT JOIN market_snapshots s ON s.id=d.snapshot_id
             WHERE UPPER(COALESCE(p.status,''))='CLOSED'
               {funding_filter}
               AND CAST(COALESCE(p.closed_at,0) AS REAL) <= ?
               AND CAST(COALESCE(p.closed_at,0) AS REAL) >= ?
             ORDER BY CAST(p.closed_at AS REAL) DESC, p.id DESC
             LIMIT ?
        """
        rows = conn.execute(
            sql,
            (now, now - max(1.0, float(window_sec)), max(1, int(lookback_outcomes))),
        ).fetchall()
        rows = list(reversed(rows))
    except Exception as exc:
        return PatternPermission("UNAVAILABLE", False, 0.0, 0, None, None,
                                 f"pattern_query_error:{type(exc).__name__}:{exc}")

    if not rows:
        return PatternPermission("DORMANT", False, 0.0, 0, None, None, "no_causal_outcomes")

    successes = []
    last_outcome = "NONE"
    reset_at = None
    for row in rows:
        outcome = _row_outcome(row)
        last_outcome = outcome
        if is_reset(outcome):
            successes.clear()
            reset_at = _f(row["closed_at"], 0.0)
            continue
        if not is_success(outcome):
            continue

        mint = str(row["mint_address"] or "")
        position_id = int(row["id"])
        opened_at = _f(row["opened_at"], 0.0)
        cohort = _cohort_key(row)

        independent = True
        for prev in successes:
            if mint == prev["mint"] or position_id == prev["position_id"] or cohort == prev["cohort"]:
                independent = False
                break
            if abs(opened_at - prev["opened_at"]) < float(min_open_separation_sec):
                independent = False
                break
        if independent:
            successes.append({
                "mint": mint,
                "position_id": position_id,
                "opened_at": opened_at,
                "closed_at": _f(row["closed_at"], 0.0),
                "cohort": cohort,
                "outcome": outcome,
            })

    confirmations = len(successes)
    if confirmations == 0:
        reason = f"no_independent_success:last={last_outcome}"
        if reset_at:
            reason += f":reset_at={reset_at:.3f}"
        return PatternPermission("DORMANT", False, 0.0, 0, None, None, reason)

    anchor = successes[0]["closed_at"]
    expiry = max(x["closed_at"] for x in successes) + max(1.0, float(window_sec))
    lineage = ",".join(f"{x['position_id']}:{x['cohort']}" for x in successes)

    if confirmations >= 2:
        state = "CONFIRMED" if confirmations >= 3 else "ARMED"
        earned_multiplier, maturity_reason = _live_size_stage(conn)
        return PatternPermission(
            state, True, earned_multiplier, confirmations, anchor, expiry,
            f"{confirmations}_independent_successes_last_{int(lookback_outcomes)};"
            f"lineage={lineage};{maturity_reason};size={earned_multiplier:.2f}x",
        )
    return PatternPermission(
        "WATCHING", False, 0.0, confirmations, anchor, expiry,
        f"first_independent_success;lineage={lineage}",
    )
