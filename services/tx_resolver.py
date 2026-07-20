#!/usr/bin/env python3
r"""
Sentinuity tx_resolver.py

Purpose:
    Resolve transaction / market-snapshot confidence WITHOUT the old 0.890
    rubber-stamp ceiling.

Design goals:
    - Preserve raw confidence for forensics.
    - Produce calibrated_confidence from multiple independent features.
    - Produce runner_conviction separately from trade confidence.
    - Never force every passing signal to 0.890.
    - Remain conservative if optional fields are missing.
    - Be import-compatible with several likely legacy call styles.

Important:
    This module does NOT execute trades. It only scores / resolves signals.
    Live-fire gates should remain in execution_engine.py.

Suggested location:
    <sentinuity-root>\tx_resolver.py

If your code imports services.tx_resolver instead, place the same file at:
    <sentinuity-root>\services\tx_resolver.py
or make a small services/tx_resolver.py shim that imports from root.
"""
from __future__ import annotations


from dataclasses import dataclass, asdict
from math import exp, log10
from typing import Any, Dict, Mapping, Optional, Tuple
import json
import sqlite3
import time


RESOLVER_VERSION = "tx_resolver_v2_no_089_rubber_stamp"

# Old bug was effectively max/min forcing viable signals to 0.890.
# This file intentionally does NOT use 0.89 as a global cap.
MIN_CONFIDENCE = 0.05
MAX_CONFIDENCE = 0.985

# Conservative default when there is not enough evidence.
DEFAULT_FALLBACK_CONFIDENCE = 0.62


@dataclass(frozen=True)
class ResolverResult:
    token: str
    confidence: float
    calibrated_confidence: float
    raw_confidence: Optional[float]
    runner_conviction: float
    runner_tier: str
    evidence_count: int
    risk_penalty: float
    confidence_source: str
    resolver_version: str = RESOLVER_VERSION
    resolved_at: float = 0.0
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        out = asdict(self)
        if not out.get("resolved_at"):
            out["resolved_at"] = time.time()
        return out


def _coalesce(*values: Any, default: Any = None) -> Any:
    for value in values:
        if value is not None:
            return value
    return default


def _as_float(value: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if value is None:
            return default
        if isinstance(value, bool):
            return float(int(value))
        if isinstance(value, str):
            s = value.strip().replace(",", "")
            if not s:
                return default
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
            return float(s)
        return float(value)
    except Exception:
        return default


def _clamp(value: float, lo: float = MIN_CONFIDENCE, hi: float = MAX_CONFIDENCE) -> float:
    return max(lo, min(hi, float(value)))


def _sigmoid(x: float) -> float:
    if x >= 40:
        return 1.0
    if x <= -40:
        return 0.0
    return 1.0 / (1.0 + exp(-x))


def _norm_pct(value: Any, center: float = 0.0, scale: float = 0.30) -> Optional[float]:
    """
    Normalize percentage-like values to 0..1.
    Accepts either 0.25 or 25 for 25%.
    """
    v = _as_float(value, None)
    if v is None:
        return None
    if abs(v) > 2.0:
        v = v / 100.0
    return _clamp(_sigmoid((v - center) / max(scale, 0.0001)), 0.0, 1.0)


def _norm_positive_log(value: Any, low: float, high: float) -> Optional[float]:
    """
    Log-normalize positive values where low/high are meaningful dollar/count ranges.
    """
    v = _as_float(value, None)
    if v is None or v <= 0:
        return None

    lo = max(low, 1e-9)
    hi = max(high, lo * 10)
    score = (log10(v) - log10(lo)) / (log10(hi) - log10(lo))
    return _clamp(score, 0.0, 1.0)


def _first_present(snapshot: Mapping[str, Any], keys: Tuple[str, ...]) -> Any:
    for key in keys:
        if key in snapshot and snapshot[key] is not None:
            return snapshot[key]
    return None


def _token_id(snapshot: Mapping[str, Any]) -> str:
    return str(
        _coalesce(
            _first_present(snapshot, ("mint_address", "mint", "token_mint", "address", "contract", "token")),
            default="UNKNOWN_TOKEN",
        )
    )


def _extract_raw_confidence(snapshot: Mapping[str, Any]) -> Optional[float]:
    """
    Preserve upstream/raw confidence, but do not blindly trust it as final confidence.
    """
    raw = _first_present(
        snapshot,
        (
            "raw_confidence",
            "model_confidence",
            "upstream_confidence",
            "resolver_confidence",
            "confidence",
            "conf",
        ),
    )
    val = _as_float(raw, None)
    if val is None:
        return None

    if val > 1.0 and val <= 100.0:
        val = val / 100.0

    return _clamp(val, 0.0, 1.0)


def _freshness_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    # SIGN-OFF FIX 2026-05-26:
    # stored freshness_score=0.0 is a DB default/un-set value, NOT a real "completely stale"
    # measurement. The old guard `0.0 <= pre <= 1.0` accepted 0.0 as truth and returned
    # (0.0, True), which caused fresh tokens to score 0.0 fresh → structural cap at 0.000
    # calibrated_confidence for the entire fresh-runner class.
    #
    # Fix: treat pre=0.0 as missing (fall through to timestamp recompute).
    # Only trust pre if it is STRICTLY positive (a real computed value).
    pre = _as_float(_first_present(snapshot, ("freshness_score", "freshness")), None)
    if pre is not None and pre > 0.0 and pre <= 1.0:
        return float(pre), True

    # Recompute freshness from available timestamp/age fields.
    # Try signal age first (most precise for execution decisions), then price age,
    # then token age as a last resort.
    age = _as_float(
        _first_present(
            snapshot,
            (
                "signal_age_seconds",
                "price_age_seconds",
                "price_age",
                "price_age_sec",
                "oracle_price_age",
                "oracle_age",
                "signal_age",
                "age_seconds",
            ),
        ),
        None,
    )

    # Also try computing age from absolute timestamps if age fields missing
    if age is None:
        now = time.time()
        for ts_field in ("price_updated_at", "qualified_at", "signal_discovered_at",
                         "created_at", "first_seen_at", "timestamp"):
            ts = _as_float(snapshot.get(ts_field), None)
            if ts is not None and ts > 1_000_000_000:  # valid epoch
                age = now - ts
                break

    # Token age (birth time) is NOT a proxy for signal freshness.
    # Only use it as a last resort — and cap its score conservatively.
    if age is None:
        token_age = _as_float(_first_present(snapshot, ("token_age_seconds", "token_birth_age_seconds")), None)
        if token_age is not None:
            # Fresh token ≠ fresh signal. Give a neutral score.
            return 0.55, False
        return 0.55, False  # no age info at all — neutral, not penalised

    if age <= 0:
        return 0.65, True
    if age <= 15:
        return 1.0, True
    if age <= 30:
        return 0.86, True
    if age <= 60:
        return 0.68, True
    if age <= 180:
        return 0.42, True
    return 0.15, True


def _liquidity_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    """Calibrated for pump.fun bonding curve liquidity scale.

    Pump.fun tokens have orders of magnitude smaller liquidity than
    established DEX tokens. $500-$5000 is normal; >$50k is graduating.
    """
    liquidity = _as_float(_first_present(
        snapshot,
        ("token_liquidity_usd", "liquidity_usd", "liq_usd", "pool_liquidity_usd", "liquidity", "pool_liquidity"),
    ), None)
    if liquidity is None or liquidity <= 0:
        return 0.48, False
    # Pump.fun calibration:
    #   <$500   = risky/dead         -> 0.20
    #   $500-2k = early/active       -> 0.55
    #   $2k-10k = healthy sweet spot -> 0.75
    #   $10k-50k= maturing           -> 0.85
    #   >$50k   = near-graduation    -> 0.78 (still good but less upside)
    if liquidity < 500:
        return 0.20, True
    if liquidity < 2_000:
        return 0.55, True
    if liquidity < 10_000:
        return 0.75, True
    if liquidity < 50_000:
        return 0.85, True
    if liquidity < 250_000:
        return 0.80, True
    return 0.62, True


def _buy_pressure_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    ratio = _as_float(
        _first_present(snapshot, ("buy_sell_ratio", "buys_sells_ratio", "buy_to_sell_ratio")),
        None,
    )
    buys = _as_float(_first_present(snapshot, ("buys_5m", "buy_count_5m", "buys")), None)
    sells = _as_float(_first_present(snapshot, ("sells_5m", "sell_count_5m", "sells")), None)

    if ratio is None and buys is not None and sells is not None:
        ratio = buys / max(sells, 1.0)

    if ratio is None:
        # Fallback to buy_velocity from live schema (Sentinuity-specific)
        # buy_velocity > 5 = strong buying pressure
        bv = _as_float(_first_present(snapshot, ("buy_velocity", "buy_velocity_per_min", "vel_buy")), None)
        if bv is not None and bv > 0:
            # Map buy_velocity to [0..1] using sigmoid centered at 5.0
            score = _sigmoid((bv - 5.0) / 4.0)
            return _clamp(score, 0.0, 1.0), True
        return 0.50, False

    score = _sigmoid((ratio - 1.15) / 0.55)
    return _clamp(score, 0.0, 1.0), True


def _volume_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    volume = _first_present(
        snapshot,
        ("volume_5m_usd", "volume_5m", "vol_5m", "volume_usd", "volume_1m_usd"),
    )
    score = _norm_positive_log(volume, low=1_000, high=500_000)
    if score is None:
        return 0.48, False
    return score, True


def _market_cap_structure_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    mcap = _as_float(_first_present(snapshot, ("market_cap_usd", "mcap_usd", "market_cap", "fdv")), None)
    if mcap is None or mcap <= 0:
        return 0.50, False

    if 8_000 <= mcap <= 450_000:
        return 0.78, True
    if 3_000 <= mcap < 8_000:
        return 0.55, True
    if 450_000 < mcap <= 2_000_000:
        return 0.58, True
    return 0.38, True


def _curve_structure_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    """Sentinuity-specific: bonding curve progress + holder distribution.

    curve_progress_pct: how far along the bonding curve the token is.
      - 50-95% sweet spot (still room to graduate, momentum proven)
      - >95% near graduation (less upside)
      - <50% too early (statistically more rugs)

    holder_count + top10_holder_pct: distribution health.
      - >50 holders, <40% top10 = healthy
      - <30 holders OR >70% top10 = concentration risk
    """
    curve = _as_float(_first_present(snapshot, ("curve_progress_pct", "curve_progress", "bonding_curve_pct")), None)
    holders = _as_float(_first_present(snapshot, ("holder_count", "holders", "n_holders")), None)
    top10 = _as_float(_first_present(snapshot, ("top10_holder_pct", "top10_concentration", "top10_pct")), None)

    if curve is None and holders is None and top10 is None:
        return 0.50, False

    components = []
    if curve is not None and curve > 0:
        if 50 <= curve <= 95:
            components.append(0.80)
        elif 30 <= curve < 50:
            components.append(0.62)
        elif curve > 95:
            components.append(0.55)
        else:
            components.append(0.40)

    if holders is not None and holders > 0:
        if holders >= 80:
            components.append(0.78)
        elif holders >= 40:
            components.append(0.62)
        elif holders >= 20:
            components.append(0.48)
        else:
            components.append(0.32)

    if top10 is not None:
        # Lower top10% = healthier distribution
        if top10 <= 30:
            components.append(0.80)
        elif top10 <= 45:
            components.append(0.62)
        elif top10 <= 65:
            components.append(0.42)
        else:
            components.append(0.22)

    if not components:
        return 0.50, False
    return _clamp(sum(components) / len(components), 0.0, 1.0), True


def _momentum_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    explicit = _first_present(
        snapshot,
        ("momentum_score", "velocity_score", "runner_score", "curve_momentum_score"),
    )
    explicit_f = _as_float(explicit, None)
    if explicit_f is not None:
        if explicit_f > 1.0 and explicit_f <= 100.0:
            explicit_f = explicit_f / 100.0
        return _clamp(explicit_f, 0.0, 1.0), True

    p1 = _norm_pct(_first_present(snapshot, ("price_change_1m", "price_change_1m_pct", "pct_1m")), scale=0.18)
    p5 = _norm_pct(_first_present(snapshot, ("price_change_5m", "price_change_5m_pct", "pct_5m")), scale=0.45)
    p10 = _norm_pct(_first_present(snapshot, ("price_change_10m", "price_change_10m_pct", "pct_10m")), scale=0.70)

    vals = [v for v in (p1, p5, p10) if v is not None]
    if not vals:
        return 0.50, False

    if p1 is not None and p5 is not None:
        return _clamp((p1 * 0.58) + (p5 * 0.42), 0.0, 1.0), True
    return _clamp(sum(vals) / len(vals), 0.0, 1.0), True


def _smart_money_score(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    explicit = _first_present(
        snapshot,
        ("smart_money_score", "wallet_score", "wallet_entry_score", "copytrade_score"),
    )
    val = _as_float(explicit, None)
    if val is not None:
        if val > 1.0 and val <= 100.0:
            val = val / 100.0
        return _clamp(val, 0.0, 1.0), True

    tier = str(_first_present(snapshot, ("smart_money_tier", "wallet_tier", "tier")) or "").upper()
    if tier in {"ELITE_RUNNER", "ELITE", "MONSTER", "RUNNER"}:
        return 0.88, True
    if tier in {"STRONG", "WATCH", "B_TIER"}:
        return 0.70, True
    if tier in {"DUD", "BAD", "AVOID"}:
        return 0.20, True

    return 0.50, False


def _risk_penalty(snapshot: Mapping[str, Any]) -> Tuple[float, bool]:
    """
    Returns a 0..1 penalty where higher is worse.
    """
    vals = []

    for key in ("risk_score", "rug_risk", "honeypot_risk", "dev_risk", "bundle_risk"):
        v = _as_float(snapshot.get(key), None)
        if v is not None:
            if v > 1.0 and v <= 100.0:
                v = v / 100.0
            vals.append(_clamp(v, 0.0, 1.0))

    flags = (
        "is_honeypot",
        "honeypot",
        "mint_authority",
        "freeze_authority",
        "blacklist_risk",
        "dev_dump_risk",
    )
    for key in flags:
        if key in snapshot:
            val = snapshot.get(key)
            if bool(val) and str(val).lower() not in {"0", "false", "none", "no"}:
                vals.append(0.85)

    if not vals:
        return 0.10, False
    return _clamp(max(vals), 0.0, 1.0), True


def _historical_calibration(snapshot: Mapping[str, Any], db_path: Optional[str] = None) -> Tuple[Optional[float], str]:
    """
    Optional lightweight historical calibration.

    This tolerates missing DB/tables/columns. If it cannot find useful history,
    it returns (None, reason) and the resolver continues using feature calibration.
    """
    if not db_path:
        db_path = snapshot.get("db_path") or snapshot.get("database_path")

    if not db_path:
        return None, "no_db_path"

    token = _token_id(snapshot)
    if not token or token == "UNKNOWN_TOKEN":
        return None, "no_token"

    tables = ("trade_reviews", "polaris_trade_reviews", "paper_positions", "positions", "resolved_trades")
    token_cols = ("mint_address", "mint", "token_mint", "address", "contract", "token")
    pnl_cols = ("realized_pnl_percent", "pnl_percent", "pnl_pct", "roi_percent", "realized_pnl_usd", "pnl_usd")

    try:
        conn = sqlite3.connect(str(db_path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        try:
            table_rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            existing_tables = {str(r["name"]) for r in table_rows}
            usable_tables = [t for t in tables if t in existing_tables]
            if not usable_tables:
                return None, "no_history_tables"

            wins = 0
            losses = 0

            for table in usable_tables:
                cols_rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
                cols = {str(r["name"]) for r in cols_rows}

                tcol = next((c for c in token_cols if c in cols), None)
                pcol = next((c for c in pnl_cols if c in cols), None)
                outcome_col = next((c for c in ("outcome", "result", "status") if c in cols), None)
                if not tcol or not (pcol or outcome_col):
                    continue

                query = f"SELECT * FROM {table} WHERE {tcol} = ? ORDER BY rowid DESC LIMIT 200"
                for row in conn.execute(query, (token,)).fetchall():
                    won = None
                    if pcol:
                        pnl = _as_float(row[pcol], None)
                        if pnl is not None:
                            won = pnl > 0
                    if won is None and outcome_col:
                        out = str(row[outcome_col] or "").lower()
                        if any(x in out for x in ("win", "profit", "closed_green", "take_profit")):
                            won = True
                        elif any(x in out for x in ("loss", "red", "stop", "rug")):
                            won = False

                    if won is True:
                        wins += 1
                    elif won is False:
                        losses += 1

            scored = wins + losses
            if scored < 5:
                return None, f"insufficient_history_{scored}"

            rate = (wins + 2.0) / (scored + 4.0)
            return _clamp(rate, 0.0, 1.0), f"history_{wins}w_{losses}l"
        finally:
            conn.close()
    except Exception as exc:
        return None, f"history_error:{type(exc).__name__}"


def compute_runner_conviction(snapshot: Mapping[str, Any]) -> Tuple[float, str]:
    """
    Runner conviction is separate from trade confidence.

    It answers: "does this look like a runner structure/velocity?" not
    "should live buy happen right now?"
    """
    momentum, _ = _momentum_score(snapshot)
    buy_pressure, _ = _buy_pressure_score(snapshot)
    liquidity, _ = _liquidity_score(snapshot)
    volume, _ = _volume_score(snapshot)
    mcap, _ = _market_cap_structure_score(snapshot)
    curve_struct, _ = _curve_structure_score(snapshot)
    smart, _ = _smart_money_score(snapshot)
    risk, _ = _risk_penalty(snapshot)
    fresh, _ = _freshness_score(snapshot)

    conviction = (
        momentum * 0.26
        + buy_pressure * 0.16
        + volume * 0.14
        + liquidity * 0.09
        + mcap * 0.08
        + curve_struct * 0.10
        + smart * 0.10
        + fresh * 0.07
    )
    conviction = conviction - (risk * 0.22)
    conviction = _clamp(conviction, 0.0, 1.0)

    if conviction >= 0.88:
        tier = "MONSTER"
    elif conviction >= 0.78:
        tier = "STRONG_RUNNER"
    elif conviction >= 0.68:
        tier = "RUNNER_WATCH"
    elif conviction >= 0.55:
        tier = "NEUTRAL"
    else:
        tier = "DUD_OR_UNPROVEN"

    return round(conviction, 4), tier


def calibrate_confidence(snapshot: Mapping[str, Any], db_path: Optional[str] = None) -> ResolverResult:
    """
    Main resolver.

    Returns a ResolverResult object. Use resolve_transaction(...), resolve(...),
    or resolve_confidence(...) wrappers if legacy code expects a dict or float.
    """
    if snapshot is None:
        snapshot = {}
    if not isinstance(snapshot, Mapping):
        snapshot = {
            k: getattr(snapshot, k)
            for k in dir(snapshot)
            if not k.startswith("_") and not callable(getattr(snapshot, k, None))
        }

    token = _token_id(snapshot)
    raw_conf = _extract_raw_confidence(snapshot)
    raw_was_089 = raw_conf is not None and abs(raw_conf - 0.89) <= 0.0005

    momentum, m_ok = _momentum_score(snapshot)
    buy_pressure, b_ok = _buy_pressure_score(snapshot)
    liquidity, l_ok = _liquidity_score(snapshot)
    volume, v_ok = _volume_score(snapshot)
    mcap, c_ok = _market_cap_structure_score(snapshot)
    curve_struct, cs_ok = _curve_structure_score(snapshot)
    smart, s_ok = _smart_money_score(snapshot)
    fresh, f_ok = _freshness_score(snapshot)
    risk, r_ok = _risk_penalty(snapshot)
    hist, hist_note = _historical_calibration(snapshot, db_path=db_path)

    evidence_count = sum(bool(x) for x in (m_ok, b_ok, l_ok, v_ok, c_ok, cs_ok, s_ok, f_ok, r_ok, hist is not None))

    feature_score = (
        momentum * 0.20
        + buy_pressure * 0.13
        + liquidity * 0.11
        + volume * 0.12
        + mcap * 0.08
        + curve_struct * 0.10
        + smart * 0.10
        + fresh * 0.09
    )

    if hist is not None:
        feature_score = feature_score * 0.82 + hist * 0.18

    # Raw upstream confidence is useful but should not dominate, especially
    # when it equals the known old rubber-stamp value of 0.890.
    if raw_conf is not None:
        raw_weight = 0.08 if raw_was_089 else 0.18
        calibrated = feature_score * (1.0 - raw_weight) + raw_conf * raw_weight
    else:
        calibrated = feature_score

    calibrated = calibrated - (risk * 0.24)

    # If evidence is thin, do not manufacture high conviction.
    if evidence_count <= 2:
        calibrated = min(calibrated, 0.72)
        confidence_source = f"thin_evidence:{hist_note}"
    elif evidence_count <= 4:
        calibrated = min(calibrated, 0.84)
        confidence_source = f"partial_evidence:{hist_note}"
    else:
        confidence_source = f"multi_factor:{hist_note}"

    # If the only reason this would be high is a suspicious 0.890 raw input,
    # pull it below live-latched threshold until independent evidence supports it.
    if raw_was_089 and evidence_count < 5:
        calibrated = min(calibrated, 0.79)
        confidence_source += ":old_089_demoted"

    confidence = _clamp(calibrated, MIN_CONFIDENCE, MAX_CONFIDENCE)
    runner_conviction, runner_tier = compute_runner_conviction(snapshot)

    notes = []
    if raw_was_089:
        notes.append("raw_confidence_equal_old_0.890_ceiling")
    if risk >= 0.60:
        notes.append("high_risk_penalty")
    if fresh <= 0.42:
        notes.append("stale_or_aging_price_signal")
    if evidence_count <= 2:
        notes.append("insufficient_independent_evidence")

    return ResolverResult(
        token=token,
        confidence=round(confidence, 4),
        calibrated_confidence=round(confidence, 4),
        raw_confidence=None if raw_conf is None else round(raw_conf, 4),
        runner_conviction=runner_conviction,
        runner_tier=runner_tier,
        evidence_count=evidence_count,
        risk_penalty=round(risk, 4),
        confidence_source=confidence_source,
        resolved_at=time.time(),
        notes=";".join(notes),
    )


def resolve_transaction(tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    """
    Dict-returning compatibility wrapper.
    """
    snapshot: Dict[str, Any] = {}
    if tx is not None:
        if isinstance(tx, Mapping):
            snapshot.update(tx)
        else:
            snapshot.update(
                {
                    k: getattr(tx, k)
                    for k in dir(tx)
                    if not k.startswith("_") and not callable(getattr(tx, k, None))
                }
            )
    snapshot.update(kwargs)
    return calibrate_confidence(snapshot).to_dict()


def resolve(tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return resolve_transaction(tx, **kwargs)


def resolve_tx(tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return resolve_transaction(tx, **kwargs)


def resolve_market_snapshot(snapshot: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return resolve_transaction(snapshot, **kwargs)


def score_signal(signal: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return resolve_transaction(signal, **kwargs)


def score_market_snapshot(snapshot: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
    return resolve_transaction(snapshot, **kwargs)


def resolve_confidence(tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> float:
    """
    Float-returning compatibility wrapper for legacy callers.
    """
    return float(resolve_transaction(tx, **kwargs)["confidence"])


def confidence_for(tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> float:
    return resolve_confidence(tx, **kwargs)


def get_confidence(tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> float:
    return resolve_confidence(tx, **kwargs)


class TxResolver:
    """
    Class wrapper for codebases that instantiate a resolver.
    """

    version = RESOLVER_VERSION

    def __init__(self, db_path: Optional[str] = None) -> None:
        self.db_path = db_path

    def resolve(self, tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> Dict[str, Any]:
        if self.db_path and "db_path" not in kwargs:
            kwargs["db_path"] = self.db_path
        return resolve_transaction(tx, **kwargs)

    def confidence(self, tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> float:
        return float(self.resolve(tx, **kwargs)["confidence"])

    def runner_conviction(self, tx: Optional[Mapping[str, Any]] = None, **kwargs: Any) -> float:
        return float(self.resolve(tx, **kwargs)["runner_conviction"])


def _self_test() -> None:
    samples = [
        {
            "token": "OLD_RUBBER_STAMP_ONLY",
            "confidence": 0.89,
        },
        {
            "token": "GOOD_MULTI_FACTOR",
            "confidence": 0.89,
            "price_age": 8,
            "buy_sell_ratio": 2.7,
            "liquidity_usd": 55_000,
            "volume_5m_usd": 220_000,
            "market_cap_usd": 75_000,
            "price_change_1m": 0.32,
            "price_change_5m": 0.90,
            "smart_money_tier": "RUNNER",
            "risk_score": 0.08,
        },
        {
            "token": "RISKY_STALE",
            "confidence": 0.89,
            "price_age": 220,
            "buy_sell_ratio": 1.1,
            "liquidity_usd": 1_500,
            "volume_5m_usd": 700,
            "market_cap_usd": 2_500,
            "price_change_5m": -0.15,
            "risk_score": 0.72,
        },
    ]

    for sample in samples:
        print(json.dumps(resolve_transaction(sample), indent=2, sort_keys=True))


if __name__ == "__main__":
    _self_test()
