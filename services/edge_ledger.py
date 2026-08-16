#!/usr/bin/env python3
"""
SENTINUITY — EDGE CONFIDENCE LEDGER (paper-only measurement spine)

Purpose
-------
Record one durable, truthful audit row for every candidate that reaches market
qualification -- admitted or rejected -- so that the relationship between the
latest *calibrated* confidence and realised outcome can be measured.

Hard contracts (enforced by tests):
  * mint_confidence is recorded for FORENSIC purposes only and is never
    promoted into confidence / confidence_score / calibrated_confidence.
  * No source timestamp is ever written, refreshed or mutated by this module.
  * No trading table is written by this module. It owns exactly one table.
  * Nothing here can open, size, mutate or close a position.
  * Every public entry point is fail-safe: it must never raise into the
    qualification path. A measurement failure must never become a trade failure.

This module is additive. It changes no gate, threshold, or entry behaviour.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Tuple

SERVICE = "edge_ledger"
LEDGER_TABLE = "edge_confidence_ledger"

# Resolved lazily so the module never fights the launcher over CWD.
_DB_ENV = "SENTINUITY_DB"
_DB_DEFAULT = "sentinuity_matrix.db"

# Set false to make the spine inert without removing it.
ENABLED = os.environ.get("EDGE_LEDGER_ENABLED", "1").strip() != "0"

_schema_ready = False


# ─────────────────────────────────────────────────────────────────────────────
# Canonical feature specification
#
# Aliases MUST mirror services/tx_resolver.py exactly. If the calibrator's
# accepted aliases change, this table must change with it -- otherwise the
# ledger will report a feature as "missing" that the calibrator actually read
# (or vice versa), silently corrupting the completeness analysis.
# ─────────────────────────────────────────────────────────────────────────────
FEATURE_SPEC: Tuple[Tuple[str, Tuple[str, ...], str], ...] = (
    ("momentum_1m",     ("price_change_1m", "price_change_1m_pct", "pct_1m"),          "ratio"),
    ("momentum_5m",     ("price_change_5m", "price_change_5m_pct", "pct_5m"),          "ratio"),
    ("momentum_10m",    ("price_change_10m", "price_change_10m_pct", "pct_10m"),       "ratio"),
    ("momentum_score",  ("momentum_score", "velocity_score", "runner_score",
                         "curve_momentum_score"),                                       "score01"),
    ("buys_5m",         ("buys_5m", "buy_count_5m", "buys"),                            "count"),
    ("sells_5m",        ("sells_5m", "sell_count_5m", "sells"),                         "count"),
    ("buy_sell_ratio",  ("buy_sell_ratio", "buys_sells_ratio", "buy_to_sell_ratio"),    "ratio"),
    ("buy_velocity",    ("buy_velocity", "buy_velocity_per_min", "vel_buy"),            "per_min"),
    ("volume_5m_usd",   ("volume_5m_usd", "volume_5m", "vol_5m", "volume_usd",
                         "volume_1m_usd"),                                              "usd"),
    ("liquidity_usd",   ("token_liquidity_usd", "liquidity_usd", "liq_usd",
                         "pool_liquidity_usd", "liquidity", "pool_liquidity"),          "usd"),
    ("market_cap_usd",  ("market_cap_usd", "mcap_usd", "market_cap", "fdv"),            "usd"),
    ("curve_progress_pct", ("curve_progress_pct", "curve_progress",
                            "bonding_curve_pct"),                                       "pct"),
    ("holder_count",    ("holder_count", "holders", "n_holders"),                       "count"),
    ("top10_holder_pct", ("top10_holder_pct", "top10_concentration", "top10_pct"),      "pct"),
    ("smart_money_score", ("smart_money_score", "wallet_score", "wallet_entry_score",
                           "copytrade_score"),                                          "score01"),
    ("smart_money_tier", ("smart_money_tier", "wallet_tier", "tier"),                   "enum"),
    ("signal_age_seconds", ("signal_age_seconds", "signal_age", "age_seconds"),         "seconds"),
    ("price_age_seconds", ("price_age_seconds", "price_age", "price_age_sec",
                           "oracle_price_age", "oracle_age"),                           "seconds"),
    ("token_age_seconds", ("token_age_seconds", "token_birth_age_seconds"),             "seconds"),
    ("freshness_score", ("freshness_score", "freshness"),                               "score01"),
)

# Fields the enrichment layer COMPUTES AND PERSISTS but which calibrate_confidence
# never reads under any alias. Verified by grep against tx_resolver.py: each of
# these returns zero matches. They are captured in features_json so the analysis
# can quantify how much collected evidence is currently discarded before scoring
# -- a live question given how thin the feature set is for very young tokens.
#
# Guarded by the "no alias drift" test: if a future calibrator starts reading one
# of these, the test fails and forces it to be promoted into FEATURE_SPEC.
UNREAD_BY_CALIBRATOR: Tuple[str, ...] = (
    "price_change_1h",          # momentum_1h -- produced by market_intelligence
    "vol_acceleration",         # produced and persisted, never scored
    "vol_24h",
    "regime_classification",
)

AGE_COHORTS: Tuple[Tuple[str, float, float], ...] = (
    ("0-15s",    0.0,    15.0),
    ("15-30s",   15.0,   30.0),
    ("30-60s",   30.0,   60.0),
    ("1-2m",     60.0,   120.0),
    ("2-5m",     120.0,  300.0),
    ("5-10m",    300.0,  600.0),
    ("10m+",     600.0,  float("inf")),
)


def age_cohort(token_age_seconds: Optional[float]) -> str:
    if token_age_seconds is None:
        return "unknown"
    try:
        a = float(token_age_seconds)
    except (TypeError, ValueError):
        return "unknown"
    if a < 0:
        return "unknown"
    for label, lo, hi in AGE_COHORTS:
        if lo <= a < hi:
            return label
    return "10m+"


# ─────────────────────────────────────────────────────────────────────────────
# Connection
# ─────────────────────────────────────────────────────────────────────────────
def _db_path() -> Path:
    return Path(os.environ.get(_DB_ENV, _DB_DEFAULT))


def _connect(timeout: float = 8.0) -> sqlite3.Connection:
    con = sqlite3.connect(_db_path(), timeout=timeout)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
        con.execute("PRAGMA busy_timeout=8000")
        con.execute("PRAGMA synchronous=NORMAL")
    except Exception:
        pass
    return con


# ─────────────────────────────────────────────────────────────────────────────
# Additive schema
# ─────────────────────────────────────────────────────────────────────────────
_DDL = f"""
CREATE TABLE IF NOT EXISTS {LEDGER_TABLE} (
    id                     INTEGER PRIMARY KEY AUTOINCREMENT,
    runtime_id             TEXT,
    snapshot_id            INTEGER,
    mint_address           TEXT NOT NULL,
    token_name             TEXT,
    token_symbol           TEXT,

    discovered_at          REAL,
    signal_generated_at    REAL,
    qualified_at           REAL,
    evaluated_at           REAL NOT NULL,

    token_age_seconds      REAL,
    signal_age_seconds     REAL,
    price_age_seconds      REAL,
    age_cohort             TEXT,

    mint_confidence        REAL,
    confidence             REAL,
    confidence_score       REAL,
    calibrated_confidence  REAL,
    confidence_source      TEXT,
    evidence_count         INTEGER,
    risk_penalty           REAL,

    feature_count          INTEGER,
    missing_feature_count  INTEGER,
    zero_feature_count     INTEGER,
    missing_features_json  TEXT,
    features_json          TEXT,

    buys_5m                REAL,
    sells_5m               REAL,
    buy_sell_ratio         REAL,
    volume_5m_usd          REAL,
    vol_acceleration       REAL,
    momentum_5m            REAL,
    momentum_1h            REAL,
    liquidity_usd          REAL,
    market_cap_usd         REAL,
    curve_progress_pct     REAL,

    quality_status         TEXT,
    quality_reason         TEXT,
    supervisor_eligible    INTEGER,
    supervisor_reason      TEXT,
    latched                INTEGER,
    latch_reason           TEXT,

    mode_b_score           REAL,
    mode_b_threshold       REAL,
    gate_approved          INTEGER,
    gate_reasons_json      TEXT,

    paper_opened           INTEGER DEFAULT 0,
    paper_position_id      INTEGER,
    paper_entry_at         REAL,
    paper_entry_price      REAL,

    shadow_tracked         INTEGER DEFAULT 0,
    shadow_ref_price       REAL,
    shadow_ref_at          REAL,
    shadow_last_price      REAL,
    shadow_last_at         REAL,
    shadow_peak_price      REAL,
    shadow_peak_at         REAL,
    shadow_trough_price    REAL,
    shadow_tick_count      INTEGER DEFAULT 0,
    shadow_complete        INTEGER DEFAULT 0,
    shadow_state           TEXT DEFAULT 'pending',

    peak_return_pct        REAL,
    adverse_return_pct     REAL,
    realised_return_pct    REAL,
    exit_reason            TEXT,
    closed_at              REAL,
    hold_seconds           REAL,

    runner_10              INTEGER DEFAULT 0,
    runner_25              INTEGER DEFAULT 0,
    runner_50              INTEGER DEFAULT 0,
    runner_100             INTEGER DEFAULT 0,

    oracle_state           TEXT,
    price_source           TEXT,
    mtm_health             TEXT,
    price_completeness     REAL,

    created_at             REAL NOT NULL
);
"""

_INDEXES = (
    f"CREATE INDEX IF NOT EXISTS idx_ecl_eval    ON {LEDGER_TABLE}(evaluated_at)",
    f"CREATE INDEX IF NOT EXISTS idx_ecl_mint    ON {LEDGER_TABLE}(mint_address, evaluated_at)",
    f"CREATE INDEX IF NOT EXISTS idx_ecl_snap    ON {LEDGER_TABLE}(snapshot_id)",
    f"CREATE INDEX IF NOT EXISTS idx_ecl_shadow  ON {LEDGER_TABLE}(shadow_state, evaluated_at)",
    f"CREATE INDEX IF NOT EXISTS idx_ecl_cohort  ON {LEDGER_TABLE}(age_cohort, calibrated_confidence)",
)


def ensure_schema(con: Optional[sqlite3.Connection] = None) -> bool:
    """Additive only. Safe to call repeatedly. Returns True on success."""
    global _schema_ready
    own = con is None
    try:
        c = con or _connect()
        c.execute(_DDL)
        for ix in _INDEXES:
            c.execute(ix)
        # Forward-compatible additive column top-up.
        have = {r[1] for r in c.execute(f"PRAGMA table_info({LEDGER_TABLE})")}
        for col, decl in _EXPECTED_COLUMNS.items():
            if col not in have:
                try:
                    c.execute(f"ALTER TABLE {LEDGER_TABLE} ADD COLUMN {col} {decl}")
                except Exception:
                    pass
        c.commit()
        if own:
            c.close()
        _schema_ready = True
        return True
    except Exception:
        return False


# Used only for additive top-up on pre-existing installs.
_EXPECTED_COLUMNS: Dict[str, str] = {
    "zero_feature_count": "INTEGER",
    "price_completeness": "REAL",
    "adverse_return_pct": "REAL",
    "hold_seconds": "REAL",
    "age_cohort": "TEXT",
}


# ─────────────────────────────────────────────────────────────────────────────
# Feature completeness
# ─────────────────────────────────────────────────────────────────────────────
def _lookup(metrics: Mapping[str, Any], aliases: Tuple[str, ...]) -> Tuple[Any, str, Optional[str]]:
    """
    Return (value, state, alias_used).

    state is one of:
      present  -- a usable non-zero value was supplied
      zero     -- the producer supplied a genuine measured zero
      missing  -- no alias present, or present but None/blank/unparseable

    The distinction between `zero` and `missing` is the entire point of this
    function. A token with genuinely zero buys in its first 5 seconds is not
    the same as a token whose buy count was never fetched, and collapsing them
    is how a calibrator ends up silently penalising young tokens for the
    pipeline's own gaps.
    """
    for a in aliases:
        if a not in metrics:
            continue
        v = metrics[a]
        if v is None:
            continue
        if isinstance(v, str):
            s = v.strip()
            if not s:
                continue
            # Enum-ish fields stay as strings.
            try:
                fv = float(s)
            except ValueError:
                return s, "present", a
            return fv, ("zero" if fv == 0.0 else "present"), a
        if isinstance(v, bool):
            return float(v), ("zero" if not v else "present"), a
        if isinstance(v, (int, float)):
            fv = float(v)
            return fv, ("zero" if fv == 0.0 else "present"), a
    return None, "missing", None


def analyse_features(metrics: Mapping[str, Any]) -> Dict[str, Any]:
    """Classify every calibrator input. Never raises."""
    present: Dict[str, Any] = {}
    states: Dict[str, str] = {}
    aliases_used: Dict[str, str] = {}
    missing: list = []
    zeros: list = []
    try:
        for canon, aliases, _unit in FEATURE_SPEC:
            val, state, alias = _lookup(metrics, aliases)
            states[canon] = state
            if state == "missing":
                missing.append(canon)
            else:
                present[canon] = val
                if alias:
                    aliases_used[canon] = alias
                if state == "zero":
                    zeros.append(canon)
        unread: Dict[str, Any] = {}
        for k in UNREAD_BY_CALIBRATOR:
            if k in metrics and metrics[k] is not None:
                unread[k] = metrics[k]
    except Exception:
        return {
            "values": {}, "states": {}, "missing": [], "zeros": [],
            "feature_count": 0, "missing_count": len(FEATURE_SPEC),
            "zero_count": 0, "aliases_used": {}, "unread_by_calibrator": {},
        }
    return {
        "values": present,
        "states": states,
        "missing": missing,
        "zeros": zeros,
        "feature_count": len(present),
        "missing_count": len(missing),
        "zero_count": len(zeros),
        "aliases_used": aliases_used,
        "unread_by_calibrator": unread,
    }


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, str) and not v.strip():
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Public write path
# ─────────────────────────────────────────────────────────────────────────────
def record_candidate(
    *,
    snapshot_id: Optional[int],
    metrics: Mapping[str, Any],
    quality_status: str,
    quality_reason: str,
    calibrated_confidence: Optional[float] = None,
    confidence_source: Optional[str] = None,
    evidence_count: Optional[int] = None,
    risk_penalty: Optional[float] = None,
    runtime_id: Optional[str] = None,
) -> Optional[int]:
    """
    Write exactly one ledger row for a qualified-or-rejected candidate.

    FAIL-SAFE: returns None on any error. Never raises. The caller is the
    qualification path and must never be destabilised by measurement.

    This function writes ONLY to edge_confidence_ledger. It never touches
    market_snapshots, paper_positions, or any timestamp owned by a producer.
    """
    if not ENABLED:
        return None
    try:
        global _schema_ready
        con = _connect()
        try:
            if not _schema_ready:
                ensure_schema(con)

            fa = analyse_features(metrics)
            vals = fa["values"]

            mint = str(
                metrics.get("mint_address")
                or metrics.get("mint")
                or metrics.get("token_mint")
                or ""
            ).strip()
            if not mint and snapshot_id:
                # metrics does not always carry the mint. Read-only lookup,
                # mirroring the existing stage-telemetry fallback.
                try:
                    r = con.execute(
                        "SELECT mint_address FROM market_snapshots WHERE id=?",
                        (int(snapshot_id),),
                    ).fetchone()
                    if r and r[0]:
                        mint = str(r[0]).strip()
                except Exception:
                    mint = ""
            if not mint:
                return None

            now = time.time()
            token_age = _f(vals.get("token_age_seconds"))

            # Identity: display name only. Execution identity is, and remains,
            # the full mint address -- this column is never read by the executor.
            token_name, token_symbol = _resolve_display_identity(metrics, mint)

            payload = {
                "runtime_id": runtime_id or os.environ.get("SENTINUITY_RUNTIME_ID") or "",
                "snapshot_id": snapshot_id,
                "mint_address": mint,
                "token_name": token_name,
                "token_symbol": token_symbol,

                # Timestamps are COPIED from the producer, never generated here.
                "discovered_at": _f(metrics.get("discovered_at") or metrics.get("created_at")),
                "signal_generated_at": _f(metrics.get("signal_generated_at")
                                          or metrics.get("signal_ts")),
                "qualified_at": _f(metrics.get("qualified_at")),
                "evaluated_at": now,

                "token_age_seconds": token_age,
                "signal_age_seconds": _f(vals.get("signal_age_seconds")),
                "price_age_seconds": _f(vals.get("price_age_seconds")),
                "age_cohort": age_cohort(token_age),

                # Forensic only. Never promoted into any trading-confidence field.
                "mint_confidence": _f(metrics.get("mint_confidence")),
                "confidence": _f(calibrated_confidence),
                "confidence_score": _f(calibrated_confidence),
                "calibrated_confidence": _f(calibrated_confidence),
                "confidence_source": confidence_source or "",
                "evidence_count": evidence_count,
                "risk_penalty": _f(risk_penalty),

                "feature_count": fa["feature_count"],
                "missing_feature_count": fa["missing_count"],
                "zero_feature_count": fa["zero_count"],
                "missing_features_json": json.dumps(fa["missing"]),
                "features_json": json.dumps({
                    "states": fa["states"],
                    "values": {k: v for k, v in vals.items()
                               if isinstance(v, (int, float, str))},
                    "aliases_used": fa["aliases_used"],
                    "unread_by_calibrator": fa["unread_by_calibrator"],
                }, default=str)[:8000],

                "buys_5m": _f(vals.get("buys_5m")),
                "sells_5m": _f(vals.get("sells_5m")),
                "buy_sell_ratio": _f(vals.get("buy_sell_ratio")),
                "volume_5m_usd": _f(vals.get("volume_5m_usd")),
                # Persisted for analysis even though the calibrator ignores it.
                "vol_acceleration": _f(metrics.get("vol_acceleration")),
                "momentum_5m": _f(vals.get("momentum_5m")),
                "momentum_1h": _f(metrics.get("price_change_1h")),
                "liquidity_usd": _f(vals.get("liquidity_usd")),
                "market_cap_usd": _f(vals.get("market_cap_usd")),
                "curve_progress_pct": _f(vals.get("curve_progress_pct")),

                "quality_status": str(quality_status or "")[:64],
                "quality_reason": str(quality_reason or "")[:200],

                "oracle_state": str(metrics.get("oracle_state") or "")[:32],
                "price_source": str(metrics.get("source_note") or "")[:64],

                # Rejected candidates are observed read-only; admitted ones are
                # tracked too, so both populations share one measurement basis.
                "shadow_state": "pending",
                "shadow_ref_price": _f(metrics.get("token_price_usd")
                                       or metrics.get("observed_price")),
                "shadow_ref_at": now,
                "created_at": now,
            }

            cols = ", ".join(payload.keys())
            qs = ", ".join("?" for _ in payload)
            cur = con.execute(
                f"INSERT INTO {LEDGER_TABLE} ({cols}) VALUES ({qs})",
                list(payload.values()),
            )
            con.commit()
            return int(cur.lastrowid)
        finally:
            try:
                con.close()
            except Exception:
                pass
    except Exception:
        return None


def _resolve_display_identity(metrics: Mapping[str, Any], mint: str) -> Tuple[str, str]:
    """
    Display identity only. Falls back to a shortened mint ONLY as last resort,
    matching the token_identity_guard contract. Never returns a value that
    could be mistaken for, or used as, execution identity.
    """
    raw_name = str(metrics.get("token_name") or "").strip()
    sym = str(metrics.get("token_symbol") or "").strip()
    name = ""
    try:
        from services.token_identity_guard import (  # type: ignore
            looks_like_mint_fragment, safe_display,
        )
        # Keep the true name in token_name and the true symbol in token_symbol.
        # safe_display prefers symbol over name, which is right for a UI label
        # but wrong for a forensic ledger -- it would silently collapse the two
        # distinct fields the analysis needs to keep apart.
        if raw_name and not looks_like_mint_fragment(raw_name, mint):
            name = raw_name
        if sym and looks_like_mint_fragment(sym, mint):
            sym = ""
        if not name:
            # No usable name: fall back to the guard's resolution, which will
            # consult the identity cache before degrading to a shortened mint.
            name = str(safe_display(symbol=sym or None,
                                    token_name=None, mint=mint) or "")
    except Exception:
        name = raw_name
    if not name:
        name = f"{mint[:4]}..{mint[-4:]}" if len(mint) > 10 else mint
    return name[:128], sym[:32]


# ─────────────────────────────────────────────────────────────────────────────
# Canonical linkage (called by execution_engine immediately after paper open)
# ─────────────────────────────────────────────────────────────────────────────
def attach_paper_open(mint: str, position_id: int, entry_at: float,
                      entry_price: Optional[float]) -> bool:
    """Link the most recent unlinked ledger row for a mint to its paper position."""
    try:
        con = _connect()
        try:
            row = con.execute(
                f"""SELECT id FROM {LEDGER_TABLE}
                    WHERE mint_address=? AND paper_opened=0
                      AND evaluated_at <= ?
                    ORDER BY evaluated_at DESC LIMIT 1""",
                (mint, float(entry_at) + 5.0),
            ).fetchone()
            if not row:
                return False
            con.execute(
                f"""UPDATE {LEDGER_TABLE}
                    SET paper_opened=1, paper_position_id=?, paper_entry_at=?,
                        paper_entry_price=?
                    WHERE id=?""",
                (int(position_id), float(entry_at), entry_price, int(row["id"])),
            )
            con.commit()
            return True
        finally:
            con.close()
    except Exception:
        return False


def health() -> Dict[str, Any]:
    """Cheap status probe for the guardian/console. Never raises."""
    out = {"service": SERVICE, "enabled": ENABLED, "rows": 0,
           "db": str(_db_path()), "ok": False}
    try:
        con = _connect(timeout=3.0)
        try:
            out["rows"] = con.execute(f"SELECT COUNT(*) FROM {LEDGER_TABLE}").fetchone()[0]
            out["ok"] = True
        finally:
            con.close()
    except Exception:
        pass
    return out


if __name__ == "__main__":
    ok = ensure_schema()
    print(json.dumps({"schema_ok": ok, **health()}, indent=2))
