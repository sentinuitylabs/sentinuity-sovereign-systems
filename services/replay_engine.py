"""
services/replay_engine.py

SENTINUITY ADVERSARIAL REPLAY ENGINE
=======================================
Intercepts proposals before they reach the Debate Chamber.
Runs deterministic backtests against real historical trades.
Attaches mathematical proof to every proposal before Ivaris sees it.

Pipeline position:
  polaris_researcher → [proposal status='pending_replay']
  → replay_engine    → [proposal status='open' + evidence_json populated]
  → debate_engine    → IVARIS attacks simulation evidence, not just theory

This eliminates LLM hallucination from parameter tuning.
Ivaris cannot approve a proposal that failed its own historical data.

WHAT IT REPLAYS:
  Parameter proposals: simulates the new value against past qualified tokens
  Logic proposals:     estimates impact using polaris_trade_reviews history

OUTPUTS (written to proposal metrics_json):
  - trades_affected: how many past trades would be impacted
  - hypothetical_win_rate: simulated win rate under new parameter
  - hypothetical_pnl: simulated total PnL
  - current_win_rate: baseline for comparison
  - current_pnl: baseline
  - win_rate_delta: change in win rate
  - pnl_delta: change in total PnL
  - missed_wins: winning trades that would have been excluded
  - avoided_losses: losing trades that would have been excluded
  - sample_size: number of trades in replay window
  - verdict: MATERIAL_IMPROVEMENT | MARGINAL | REGRESSION | INSUFFICIENT_DATA
"""

import sys
import time
import json
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from core.schema import get_connection, update_heartbeat
from services.cognition_logger import log_cognition

SERVICE_NAME   = "replay_engine"
POLL_INTERVAL  = 30    # seconds between sweeps for pending_replay proposals
REPLAY_WINDOW  = 200   # number of recent trades to replay against
MIN_SAMPLE     = 10    # minimum trades required to produce a verdict

# Material impact thresholds (must beat at least one to be MATERIAL)
MATERIAL_WIN_RATE_DELTA = 2.5   # percentage points
MATERIAL_PNL_DELTA      = 5.0   # USD

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [REPLAY] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("replay_engine")


# ── DATA FETCHING ───────────────────────────────────────────────────────────────

def fetch_pending_proposals() -> list[dict]:
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT id, proposal_type, proposal_text, suggested_action,
                       confidence, metrics_json, created_at
                FROM polaris_proposals
                WHERE status = 'pending_replay'
                ORDER BY created_at ASC
                LIMIT 5
            """).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("fetch_pending_proposals failed: %s", e)
        return []


def fetch_trade_history(limit: int = REPLAY_WINDOW) -> list[dict]:
    """Fetch real closed trades with full context for replay.

    SIGNOFF_10H_REPLAY_PROVENANCE_20260811: also project the canonical
    calibrated/entry confidence when the review ledger carries it, so a
    trading-confidence replay never has to fall back to the mint identity prior.
    The projection is schema-safe: absent columns surface as NULL (which the
    provenance guard then reports honestly) rather than raising.
    """
    try:
        with get_connection() as conn:
            try:
                cols = {r[1] for r in conn.execute(
                    "PRAGMA table_info(polaris_trade_reviews)").fetchall()}
            except Exception:
                cols = set()
            for cand in ("entry_calibrated_confidence", "entry_confidence",
                         "calibrated_confidence", "confidence"):
                if cand in cols:
                    conf_expr = f"ptr.{cand}"
                    break
            else:
                conf_expr = "NULL"
            rows = conn.execute(f"""
                SELECT
                    ptr.position_id,
                    ptr.win_loss,
                    ptr.realized_pnl_usd,
                    ptr.entry_liquidity_usd,
                    ptr.entry_market_cap_usd,
                    ptr.entry_token_age_sec,
                    ptr.entry_mint_confidence,
                    {conf_expr} AS entry_calibrated_confidence,
                    ptr.entry_quality_status,
                    ptr.exit_reason,
                    ptr.exit_category,
                    ptr.hold_seconds,
                    ptr.pnl_pct
                FROM polaris_trade_reviews ptr
                ORDER BY ptr.reviewed_at DESC
                LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        log.warning("fetch_trade_history failed: %s", e)
        return []


def fetch_current_config() -> dict:
    try:
        with get_connection() as conn:
            rows = conn.execute("SELECT key, value FROM system_config").fetchall()
            return {r["key"]: r["value"] for r in rows}
    except Exception:
        return {}


# ── REPLAY SIMULATION ───────────────────────────────────────────────────────────

def simulate_parameter_change(
    trades: list[dict],
    target_param: str,
    new_value: str,
    current_config: dict,
) -> dict:
    """
    Core simulation: apply the proposed parameter change to each historical trade.
    Determine which trades would have been excluded, kept, or unchanged.
    Return the statistical delta.
    """
    if not trades:
        return {"verdict": "INSUFFICIENT_DATA", "sample_size": 0}

    try:
        new_val = float(new_value)
    except (TypeError, ValueError):
        return {"verdict": "INSUFFICIENT_DATA", "reason": f"Cannot parse new_value: {new_value}"}

    # Map parameter to the trade field it gates
    #
    # SIGNOFF_10H_REPLAY_PROVENANCE_20260811 (directive §4, §5):
    # SUPERVISOR_MIN_MINT_CONFIDENCE was previously mapped to entry_mint_confidence.
    # mint_confidence answers "is this the right mint address" (historically a
    # near-constant ~0.89 for every *pump mint), NOT "how good is this trade".
    # The rest of the tree is explicit that it must never qualify capital
    # (edge_ledger: "recorded for FORENSIC purposes only and is never promoted
    # into confidence / confidence_score / calibrated_confidence";
    # execution_engine SIGNOFF_EDGE_RESTORE_20260731 R2a). Simulating a change to
    # the TRADING confidence floor against that column produced verdicts with no
    # relationship to the parameter under test, and would have silently corrupted
    # any July 25-27 comparative replay.
    #
    # The trading-confidence floor is therefore replayed against the canonical
    # calibrated/entry confidence only. If the review row does not carry it, the
    # replay refuses rather than substituting the identity prior.
    PARAM_TO_FIELD = {
        "MIN_LIQUIDITY_USD":               "entry_liquidity_usd",
        "MIN_MARKET_CAP_USD":              "entry_market_cap_usd",
        "MIN_TOKEN_AGE_SEC":               "entry_token_age_sec",
        "SUPERVISOR_MIN_TRADING_CONFIDENCE": "entry_calibrated_confidence",
    }
    # Retained for forensic replay ONLY. Never a capital gate.
    FORENSIC_ONLY_FIELDS = {"entry_mint_confidence"}

    # Fields whose absence is indistinguishable from a real zero. paper_positions
    # /polaris_trade_reviews carry 0 (not NULL) when the writer never populated
    # them, so `float(x or 0) < threshold` silently excludes the entire cohort and
    # reports a large favourable PnL delta for a losing sample. Refuse instead.
    PROVENANCE_REQUIRED = {
        "entry_liquidity_usd", "entry_market_cap_usd",
        "entry_token_age_sec", "entry_calibrated_confidence",
    }
    MIN_PROVENANCE_COVERAGE = 0.80

    EXIT_PARAM_MAP = {
        "TAKE_PROFIT_PCT":   "pnl_pct",
        "STOP_LOSS_PCT":     "pnl_pct",
        "EXECUTOR_MAX_HOLD_SECONDS": "hold_seconds",
    }

    baseline_wins  = sum(1 for t in trades if str(t.get("win_loss","")).upper() == "WIN")
    baseline_total = len(trades)
    baseline_pnl   = sum(float(t.get("realized_pnl_usd") or 0) for t in trades)
    baseline_wr    = (baseline_wins / max(baseline_total, 1)) * 100

    # Entry filter parameters — would exclude trades that don't meet new threshold
    if target_param in PARAM_TO_FIELD:
        field = PARAM_TO_FIELD[target_param]
        current_val = float(current_config.get(target_param, 0) or 0)

        # SIGNOFF_10H_REPLAY_PROVENANCE_20260811:
        # Absent provenance must never be read as "value 0, therefore below the
        # threshold, therefore excluded". On the 10-hour cohort every
        # entry_market_cap_usd / entry_liquidity_usd was 0 because the executor
        # never persisted them, which made a stricter filter appear to exclude
        # 100% of trades and — since the cohort was net negative — report a large
        # POSITIVE pnl_delta. Refuse with a specific, actionable verdict.
        if field in PROVENANCE_REQUIRED:
            populated = sum(
                1 for t in trades
                if t.get(field) is not None and float(t.get(field) or 0) > 0
            )
            coverage = populated / max(baseline_total, 1)
            if coverage < MIN_PROVENANCE_COVERAGE:
                return {
                    "verdict": "PROVENANCE_UNAVAILABLE",
                    "reason": (
                        f"{field} is populated for only {populated}/{baseline_total} "
                        f"({coverage:.0%}) of the replay cohort; minimum is "
                        f"{MIN_PROVENANCE_COVERAGE:.0%}. Replaying {target_param} "
                        f"against absent provenance would treat every unpopulated "
                        f"row as below threshold and report a false improvement. "
                        f"Restore the entry-provenance writer before replaying."
                    ),
                    "sample_size": baseline_total,
                    "provenance_coverage": round(coverage, 4),
                    "field": field,
                }

        if new_val > current_val:
            # Stricter filter — some trades would have been excluded.
            # Rows lacking provenance are held OUT of the simulation entirely
            # rather than being counted as failures of the threshold.
            known    = [t for t in trades if t.get(field) is not None and float(t.get(field) or 0) > 0]
            excluded = [t for t in known if float(t.get(field) or 0) < new_val]
            included = [t for t in known if float(t.get(field) or 0) >= new_val]
        else:
            # Looser filter — some additional trades would have been included
            # (We can't retroactively add trades we never took, so use same set)
            excluded = []
            included = trades

        hyp_wins  = sum(1 for t in included if str(t.get("win_loss","")).upper() == "WIN")
        hyp_total = len(included)
        hyp_pnl   = sum(float(t.get("realized_pnl_usd") or 0) for t in included)
        hyp_wr    = (hyp_wins / max(hyp_total, 1)) * 100

        avoided_losses = sum(1 for t in excluded if str(t.get("win_loss","")).upper() != "WIN")
        missed_wins    = sum(1 for t in excluded if str(t.get("win_loss","")).upper() == "WIN")

    elif target_param == "STOP_LOSS_PCT":
        current_sl = float(current_config.get("STOP_LOSS_PCT", 10) or 10)
        # Tighter SL — some losses would have been smaller
        sl_trades = [t for t in trades if str(t.get("exit_category","")).upper() == "SL"]
        non_sl    = [t for t in trades if str(t.get("exit_category","")).upper() != "SL"]

        # Simulate: tighter SL reduces loss magnitude
        sl_factor = new_val / max(current_sl, 1)
        hyp_sl_pnl = sum(float(t.get("realized_pnl_usd") or 0) * sl_factor for t in sl_trades)
        hyp_pnl = sum(float(t.get("realized_pnl_usd") or 0) for t in non_sl) + hyp_sl_pnl

        hyp_wins  = baseline_wins
        hyp_total = baseline_total
        hyp_wr    = baseline_wr
        avoided_losses = 0
        missed_wins    = 0

    elif target_param == "TAKE_PROFIT_PCT":
        current_tp = float(current_config.get("TAKE_PROFIT_PCT", 25) or 25)
        tp_trades  = [t for t in trades if str(t.get("exit_category","")).upper() == "TP"]
        non_tp     = [t for t in trades if str(t.get("exit_category","")).upper() != "TP"]

        tp_factor = new_val / max(current_tp, 1)
        hyp_tp_pnl = sum(float(t.get("realized_pnl_usd") or 0) * tp_factor for t in tp_trades)
        hyp_pnl = sum(float(t.get("realized_pnl_usd") or 0) for t in non_tp) + hyp_tp_pnl

        hyp_wins  = baseline_wins
        hyp_total = baseline_total
        hyp_wr    = baseline_wr
        avoided_losses = 0
        missed_wins    = 0

    else:
        # Unknown parameter — can't simulate, mark insufficient
        return {
            "verdict": "INSUFFICIENT_DATA",
            "reason": f"No replay model for parameter: {target_param}",
            "sample_size": baseline_total,
        }

    win_rate_delta = hyp_wr - baseline_wr
    pnl_delta      = hyp_pnl - baseline_pnl

    if hyp_total < MIN_SAMPLE:
        verdict = "INSUFFICIENT_DATA"
    elif pnl_delta < -5.0 or win_rate_delta < -2.0:
        verdict = "REGRESSION"
    elif win_rate_delta >= MATERIAL_WIN_RATE_DELTA or pnl_delta >= MATERIAL_PNL_DELTA:
        verdict = "MATERIAL_IMPROVEMENT"
    elif win_rate_delta > 0 or pnl_delta > 0:
        verdict = "MARGINAL"
    else:
        verdict = "REGRESSION"

    return {
        "verdict":              verdict,
        "sample_size":          baseline_total,
        "replay_window":        hyp_total,
        "current_win_rate":     round(baseline_wr, 2),
        "hypothetical_win_rate": round(hyp_wr, 2),
        "win_rate_delta":       round(win_rate_delta, 2),
        "current_pnl":          round(baseline_pnl, 4),
        "hypothetical_pnl":     round(hyp_pnl, 4),
        "pnl_delta":            round(pnl_delta, 4),
        "avoided_losses":       avoided_losses if 'avoided_losses' in dir() else 0,
        "missed_wins":          missed_wins if 'missed_wins' in dir() else 0,
        "target_parameter":     target_param,
        "current_value":        current_config.get(target_param, "unknown"),
        "proposed_value":       str(new_value),
    }


# ── PROPOSAL PROCESSING ─────────────────────────────────────────────────────────

def process_proposal(proposal: dict, trades: list[dict], config: dict) -> None:
    proposal_id = proposal["id"]

    try:
        metrics = json.loads(proposal.get("metrics_json") or "{}")
    except Exception:
        metrics = {}

    target_param   = metrics.get("target_parameter")
    suggested_value = metrics.get("suggested_value")

    if not target_param or suggested_value is None:
        # Can't replay — open it for debate without evidence
        log.info("Proposal %d has no target parameter — opening without replay", proposal_id)
        _mark_open(proposal_id, metrics, {"verdict": "NO_PARAMETER_TARGET"})
        return

    log.info("Replaying proposal %d: %s → %s", proposal_id, target_param, suggested_value)

    result = simulate_parameter_change(trades, target_param, suggested_value, config)
    metrics["replay_evidence"] = result

    verdict = result.get("verdict", "INSUFFICIENT_DATA")

    if verdict == "REGRESSION":
        # Hard veto — evidence proves this would hurt performance
        log_cognition(
            "RESEARCH",
            f"REPLAY VETO: Proposal to change {target_param} to {suggested_value} "
            f"shows regression. Win rate delta: {result.get('win_rate_delta',0):+.1f}%, "
            f"PnL delta: ${result.get('pnl_delta',0):+.2f} over {result.get('sample_size',0)} trades. "
            f"Proposal rejected before IVARIS debate.",
            confidence=0.95,
        )
        _mark_vetoed(proposal_id, metrics, "REPLAY_REGRESSION")

    elif verdict == "PROVENANCE_UNAVAILABLE":
        # SIGNOFF_10H_REPLAY_PROVENANCE_20260811: the replay could not run because
        # the gating field was never persisted for this cohort. This is NOT
        # evidence for or against the proposal and must never be logged as proof.
        # Treated exactly like INSUFFICIENT_DATA (open for debate, evidence
        # caveat) -- it grants no approval authority and vetoes nothing.
        log_cognition(
            "RESEARCH",
            f"REPLAY UNAVAILABLE: cannot simulate {target_param} change — "
            f"{result.get('reason', 'entry provenance missing')} "
            f"Opening for debate with NO replay evidence.",
            confidence=0.4,
        )
        _mark_open(proposal_id, metrics, result)

    elif verdict == "INSUFFICIENT_DATA":
        log_cognition(
            "RESEARCH",
            f"REPLAY: Insufficient trade history to simulate {target_param} change "
            f"(sample={result.get('sample_size',0)}, need {MIN_SAMPLE}). "
            f"Opening for debate with evidence caveat.",
            confidence=0.5,
        )
        _mark_open(proposal_id, metrics, result)

    else:
        # MATERIAL_IMPROVEMENT or MARGINAL — send to debate with evidence
        log_cognition(
            "RESEARCH",
            f"REPLAY PROOF: {target_param} → {suggested_value}. "
            f"Verdict: {verdict}. "
            f"Win rate: {result.get('current_win_rate',0):.1f}% → {result.get('hypothetical_win_rate',0):.1f}% "
            f"({result.get('win_rate_delta',0):+.1f}pp). "
            f"PnL: ${result.get('current_pnl',0):.2f} → ${result.get('hypothetical_pnl',0):.2f} "
            f"({result.get('pnl_delta',0):+.2f}). "
            f"Sample: {result.get('sample_size',0)} trades. Now routing to IVARIS.",
            confidence=0.85 if verdict == "MATERIAL_IMPROVEMENT" else 0.6,
        )
        _mark_open(proposal_id, metrics, result)


def _mark_open(proposal_id: int, metrics: dict, result: dict) -> None:
    metrics["replay_evidence"] = result
    try:
        with get_connection() as conn:
            conn.execute("""
                UPDATE polaris_proposals
                SET status = 'open', metrics_json = ?
                WHERE id = ?
            """, (json.dumps(metrics), proposal_id))
            conn.commit()
    except Exception as e:
        log.warning("_mark_open failed for proposal %d: %s", proposal_id, e)


def _mark_vetoed(proposal_id: int, metrics: dict, reason: str) -> None:
    metrics["replay_evidence"] = metrics.get("replay_evidence", {})
    try:
        with get_connection() as conn:
            conn.execute("""
                UPDATE polaris_proposals
                SET status = 'replay_vetoed', metrics_json = ?
                WHERE id = ?
            """, (json.dumps(metrics), proposal_id))
            conn.commit()
    except Exception as e:
        log.warning("_mark_vetoed failed for proposal %d: %s", proposal_id, e)


# ── MAIN LOOP ───────────────────────────────────────────────────────────────────

def run() -> None:
    log.info("REPLAY ENGINE ONLINE — Adversarial simulation harness active")

    while True:
        try:
            proposals = fetch_pending_proposals()

            if proposals:
                trades = fetch_trade_history()
                config = fetch_current_config()

                log.info(
                    "Processing %d pending proposals against %d trade history",
                    len(proposals), len(trades)
                )

                for proposal in proposals:
                    process_proposal(proposal, trades, config)

                update_heartbeat(
                    SERVICE_NAME, "ALIVE",
                    f"replayed={len(proposals)} trades_in_window={len(trades)}",
                    work_processed=len(proposals),
                )
            else:
                update_heartbeat(SERVICE_NAME, "ALIVE", "Idle — no proposals pending replay")

        except Exception as e:
            log.error("Replay cycle error: %s", e)
            update_heartbeat(SERVICE_NAME, "ERROR", str(e)[:100])

        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    run()
