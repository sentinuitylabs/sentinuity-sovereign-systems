#!/usr/bin/env python3
"""
SENTINUITY — MARK PROVENANCE AND DISCONTINUITY QUARANTINE

Root cause this module addresses
--------------------------------
`sentinuity_intelligence.mtm_ticks` HAS a `source` column carrying the real
computation subtype (helius / native reserve reads, dexscreener quotes, jupiter
quotes, stall_recovery, wss_fail_fallback, ...).

`price_router._read_intel()` selected only `price_usd, ts_ms` and returned a
hardcoded `source: "intel-mtm"`. Every subtype was flattened into one label, so
a reserve-derived price and a rounded quote arrived indistinguishable.

Measured consequence over six hours of runtime (2,098 marks, 72 positions):
  * 55 discontinuities beyond +/-1.85x
  * ZERO within 1% of exactly 2.0x   (median 2.065x, range 1.89x - 3.93x)
  * higher value carried 10-11 significant figures in 53 of 55 cases
  * lower value carried <=4 significant figures in 48 of 55 cases
  * label `intel-mtm` carried 1,708 four-sig-fig marks AND 87 high-precision

An exact-2x detector catches NONE of these. The real signal is a
representation change inside one nominal source: reserve maths emits full
float precision, quote APIs emit rounded values.

`ws_price_oracle._sent0707_peak_trusted_source()` already knew which subtypes
were trustworthy. That classification simply never reached the consumer. This
module carries it across the boundary.

Contracts:
  * Pure functions. No database writes except the quarantine telemetry table.
  * Never raises into the mark path.
  * Never discards an observation: suspect marks are retained and labelled.
  * Never changes live authority, sizing, gates or thresholds.
"""
from __future__ import annotations

import math
import sqlite3
import time
from typing import Any, Dict, Mapping, Optional, Tuple

SERVICE = "mark_provenance"
QUARANTINE_TABLE = "mark_quarantine"

# Discontinuity band. Deliberately NOT 2.0 -- see module docstring.
RATIO_HIGH = 1.80
RATIO_LOW = 1.0 / RATIO_HIGH

# A significant-figure count at or below this indicates a rounded quote API.
ROUNDED_SIGFIG_MAX = 5
# At or above this indicates a computed (reserve-derived) value.
COMPUTED_SIGFIG_MIN = 8

# Subtype families. Mirrors ws_price_oracle._sent0707_peak_trusted_source().
_UNTRUSTED_FRAGMENTS = (
    "stall_recovery", "coverage_failsafe", "wss_fail_fallback",
    "cold_recovery", "dexscreener", "jupiter", "birdeye",
    "keepalive_", "fallback", "recovery",
)
_TRUSTED_EXACT = {"ws", "wss", "native", "accountsubscribe", "helius"}

SUBTYPE_CURVE = "curve_reserve"
SUBTYPE_POOL = "pool_quote"
SUBTYPE_MCAP = "market_cap_derived"
SUBTYPE_FALLBACK = "fallback_quote"
SUBTYPE_UNKNOWN = "unknown"
# SIGNOFF_RUNNER_SUBTYPE_POLICY_20260812: the executable families actually emitted into mark_tape by
# the exact-size router path. They were never named here, so every retained mark
# of these subtypes fell through to the "not in CORROBORATABLE_SUBTYPES" clause.
SUBTYPE_ROUTER_EXEC = "router_executable"
SUBTYPE_POOL_EXEC = "pool_executable"

# Canonical integrity states (directive section 3).
INTEGRITY_TRUSTED = "TRUSTED"
INTEGRITY_UNCONFIRMED = "UNCONFIRMED"
INTEGRITY_Q_DISCONTINUITY = "QUARANTINED_DISCONTINUITY"
INTEGRITY_Q_SOURCE = "QUARANTINED_SOURCE_TRANSITION"
INTEGRITY_Q_PRECISION = "QUARANTINED_PRECISION_SHIFT"
INTEGRITY_Q_MIGRATION = "QUARANTINED_MIGRATION_CONFLICT"
INTEGRITY_LEGACY = "LEGACY_PROVENANCE_UNAVAILABLE"

# SIGNOFF_RUNNER_SUBTYPE_POLICY_20260812
# DEFECT: every retained mark_tape row carried source_subtype='router_executable',
# which was absent from CORROBORATABLE_SUBTYPES. The first clause of the runner
# corroboration predicate therefore skipped EVERY mark, making canonical
# runner_confirmed mathematically unreachable for any position at any peak size,
# independent of market regime and of upstream event identity (verified: 1000/1000
# marks had populated and distinct upstream_tick_id / upstream_ts_ms).
#
# CORRECTION, in both directions:
#   * ADMIT the executable families. An exact-size router quote is strictly
#     stronger evidence than a vendor pool quote, which was already admitted.
#   * DEMOTE curve_reserve to non-self-confirming. It is synthetic, was observed
#     at roughly 1.9-2.3x pool_quote, and two mutually consistent curve marks
#     could manufacture a false +100% runner against a pool_quote entry.
#     PRICE_FAMILY_TRUTH_20260803 already reached this conclusion in
#     peak_authority.NON_CONFIRMING_FAMILIES; this aligns the canonical policy.
#
# Every other confirmation gate is unchanged: distinct upstream tick, upstream
# timestamp presence, >=5s event separation, <=12% pair disagreement, subtype and
# family compatibility, precision class, integrity state and the 20% runner
# threshold all still apply.
CORROBORATABLE_SUBTYPES = {SUBTYPE_POOL, SUBTYPE_ROUTER_EXEC, SUBTYPE_POOL_EXEC}
NO_SELF_CONFIRM_SUBTYPES = {SUBTYPE_FALLBACK, SUBTYPE_MCAP, SUBTYPE_UNKNOWN,
                            SUBTYPE_CURVE}

RUNNER_UNCONFIRMED = "RUNNER_UNCONFIRMED"
RUNNER_CONFIRMED = "RUNNER_CONFIRMED"
RUNNER_QUARANTINED = "RUNNER_QUARANTINED"


# ─────────────────────────────────────────────────────────────────────────────
# Classification
# ─────────────────────────────────────────────────────────────────────────────
def significant_figures(x: Any) -> int:
    """Significant figures in the decimal representation of a float."""
    try:
        v = float(x)
    except (TypeError, ValueError):
        return 0
    if v == 0 or not math.isfinite(v):
        return 0
    mant = f"{abs(v):.12e}".split("e")[0].replace(".", "").rstrip("0")
    return len(mant.lstrip("0")) or 1


def precision_class(x: Any) -> str:
    """'rounded' (quote API) | 'computed' (reserve maths) | 'mid'."""
    sf = significant_figures(x)
    if sf == 0:
        return "unknown"
    if sf <= ROUNDED_SIGFIG_MAX:
        return "rounded"
    if sf >= COMPUTED_SIGFIG_MIN:
        return "computed"
    return "mid"


# PACK_A_PROVENANCE_TRUTH_20260805
# Degraded-mode fragments. A source label containing any of these describes a
# recovery, failsafe or synthetic path. It is diagnostic evidence and may never
# be promoted into an authoritative family, whatever vendor name it also
# carries. Previously the dexscreener/birdeye/jupiter test ran first, so
# "coverage_failsafe_runner_dexscreener" classified as pool_quote - the one
# family permitted to self-confirm runner authority.
_DEGRADED_FRAGMENTS = (
    "stall_recovery", "coverage_failsafe", "failsafe", "wss_fail_fallback",
    "cold_recovery", "keepalive_", "fallback", "recovery", "stale",
    "backfill", "synthetic", "last_known", "carry_forward", "reuse",
)

# Canonical witness identities. Independence is a property of the witness, not
# of the tick. Two observations sharing a witness_id are ONE witness.
_WITNESS_PATTERNS = (
    ("dexscreener", "vendor:dexscreener"),
    ("birdeye", "vendor:birdeye"),
    ("jupiter", "vendor:jupiter"),
    ("pumpapi", "vendor:pumpapi"),
    ("pump_api", "vendor:pumpapi"),
    ("helius", "rpc:helius"),
    ("accountsubscribe", "rpc:helius"),
    ("quicknode", "rpc:quicknode"),
    ("triton", "rpc:triton"),
    ("raydium", "vendor:raydium"),
    ("pumpswap", "chain:pumpswap"),
    ("curve", "chain:pump_curve"),
)


def is_degraded_source(raw_source: Any) -> bool:
    """True when the label describes a recovery/failsafe/synthetic path."""
    s = str(raw_source or "").strip().lower()
    return bool(s) and any(f in s for f in _DEGRADED_FRAGMENTS)


def witness_id(raw_source: Any) -> str:
    """
    Canonical witness identity for independence tests.

    Returns a stable "kind:name" string. Unrecognised labels return
    "unknown:<label>" so an unmapped provider is visible rather than silently
    counting as a fresh witness. Degraded labels are prefixed "degraded:" so
    they can never collide with, and therefore never corroborate, the healthy
    witness of the same vendor.
    """
    s = str(raw_source or "").strip().lower()
    if not s:
        return "unknown:"
    prefix = "degraded:" if is_degraded_source(s) else ""
    for frag, ident in _WITNESS_PATTERNS:
        if frag in s:
            return prefix + ident
    if s in _TRUSTED_EXACT or s.startswith("ws"):
        return prefix + "rpc:websocket"
    return prefix + "unknown:" + s[:48]


def classify_subtype(raw_source: Any) -> str:
    """
    Map a raw mtm_ticks.source value to a canonical provenance subtype.

    This is the value that must accompany every mark instead of the flat
    'intel-mtm' label.

    ORDERING CONTRACT (PACK_A_PROVENANCE_TRUTH_20260805):
    degraded  ->  market-cap  ->  confirmed  ->  vendor  ->  rpc/curve.
    Degraded classification MUST remain first. Any reordering re-opens the
    family-laundering defect and must fail tools/verify_pack_a.py test P1.
    """
    s = str(raw_source or "").strip().lower()
    if not s or s in ("unknown", "none"):
        return SUBTYPE_UNKNOWN
    # 1. Degraded/failsafe/synthetic paths first, unconditionally.
    if any(f in s for f in _DEGRADED_FRAGMENTS):
        return SUBTYPE_FALLBACK
    # 2. Market-cap derivation is never a price observation.
    if "market_cap" in s or "mcap" in s:
        return SUBTYPE_MCAP
    # 3. Chain-confirmed observations.
    if "confirmed" in s:
        return SUBTYPE_CURVE
    # 4. Vendor pool quotes. All of these read the same pools; they are one
    #    family and, per witness_id(), frequently one witness.
    if any(f in s for f in ("dexscreener", "birdeye", "jupiter", "raydium")):
        return SUBTYPE_POOL
    # 5. Direct RPC / curve state.
    if s in _TRUSTED_EXACT or "helius" in s or s.startswith("ws"):
        return SUBTYPE_CURVE
    return SUBTYPE_UNKNOWN


def is_trusted_subtype(raw_source: Any) -> bool:
    """Trust classification, mirroring the oracle's own guard."""
    s = str(raw_source or "").strip().lower()
    if not s:
        return False
    if any(f in s for f in _UNTRUSTED_FRAGMENTS):
        return "confirmed" in s or "tick_confirmed" in s
    return (s in _TRUSTED_EXACT) or ("helius" in s) or ("confirmed" in s)


def qualified_source(nominal: str, raw_source: Any) -> str:
    """
    'intel-mtm' + subtype -> 'intel-mtm:curve_reserve'.

    An unrecognised raw source is never silently folded into a known family.
    It surfaces as 'intel-mtm:unknown:<raw_source>' so an unmapped provider
    is visible in telemetry rather than inheriting another family's trust.
    """
    base = str(nominal or "intel-mtm")
    sub = classify_subtype(raw_source)
    if sub == SUBTYPE_UNKNOWN:
        raw = str(raw_source or "").strip().lower() or "none"
        raw = "".join(ch for ch in raw if ch.isalnum() or ch in "._-")[:48]
        return f"{base}:unknown:{raw}"
    return f"{base}:{sub}"


# ─────────────────────────────────────────────────────────────────────────────
# Discontinuity evaluation
# ─────────────────────────────────────────────────────────────────────────────
def evaluate_mark(
    *,
    price: float,
    prev_trusted_price: Optional[float],
    raw_source: Any,
    prev_raw_source: Any = None,
    migration_state: Optional[str] = None,
    prev_migration_state: Optional[str] = None,
    independent_price: Optional[float] = None,
    independent_tolerance: float = 0.15,
) -> Dict[str, Any]:
    """
    Decide whether a mark is trustworthy enough to move trusted peak / runner
    state. Returns a verdict dict; never raises.

    quarantined=True means: keep the observation, but it may NOT update the
    trusted peak, promote runner state, or affect live eligibility.
    """
    out: Dict[str, Any] = {
        "price": price,
        "prev_trusted_price": prev_trusted_price,
        "ratio": None,
        "sigfigs": significant_figures(price),
        "precision_class": precision_class(price),
        "prev_precision_class": (precision_class(prev_trusted_price)
                                 if prev_trusted_price else "unknown"),
        "nominal_source": "intel-mtm",
        "source_subtype": classify_subtype(raw_source),
        "prev_source_subtype": (classify_subtype(prev_raw_source)
                                if prev_raw_source is not None else None),
        "trusted_source": is_trusted_subtype(raw_source),
        "migration_state": migration_state,
        "confirmation_source": None,
        "quarantined": False,
        "quarantine_reason": "",
        "integrity_state": INTEGRITY_UNCONFIRMED,
        "ts": time.time(),
    }
    try:
        p = float(price)
        if not math.isfinite(p) or p <= 0:
            out["quarantined"] = True
            out["quarantine_reason"] = "non_positive_or_nonfinite_price"
            return out

        reasons = []

        prev = None
        try:
            prev = float(prev_trusted_price) if prev_trusted_price else None
        except (TypeError, ValueError):
            prev = None

        if prev and prev > 0:
            ratio = p / prev
            out["ratio"] = ratio
            if ratio >= RATIO_HIGH or ratio <= RATIO_LOW:
                reasons.append(f"discontinuity_ratio={ratio:.4f}")

            # Representation change inside one nominal source. This is the
            # signature that an exact-2x rule misses entirely.
            pc_now, pc_prev = out["precision_class"], out["prev_precision_class"]
            if (pc_now in ("rounded", "computed") and pc_prev in ("rounded", "computed")
                    and pc_now != pc_prev):
                reasons.append(f"precision_class_change={pc_prev}->{pc_now}")

        if (out["prev_source_subtype"] is not None
                and out["source_subtype"] != out["prev_source_subtype"]):
            reasons.append(f"subtype_change={out['prev_source_subtype']}"
                           f"->{out['source_subtype']}")

        if (migration_state is not None and prev_migration_state is not None
                and migration_state != prev_migration_state):
            reasons.append(f"migration_change={prev_migration_state}->{migration_state}")

        # NOTE: an untrusted subtype alone is NOT a quarantine cause. A stable
        # price from a quote API is a valid observation -- it simply cannot by
        # itself confirm a runner. Quarantine is reserved for evidence that the
        # observation is wrong (discontinuity, precision shift, source or
        # migration transition). Conflating "untrusted" with "suspect" would
        # quarantine the entire tape and destroy the signal it exists to
        # protect. Trust is enforced separately, in confirm_runner().

        # A large move from the same trusted representation is not proof of a
        # bad mark. Genuine runners and bad ticks can have the same ratio. Keep
        # it as a valid but UNCONFIRMED observation and require corroboration in
        # confirm_runner(). Representation changes remain quarantined.
        _requires_confirmation = False
        if reasons:
            _representation_reasons = [r for r in reasons if (
                r.startswith("precision_class_change=")
                or r.startswith("subtype_change=")
                or r.startswith("migration_change=")
            )]
            _ratio_only = all(r.startswith("discontinuity_ratio=") for r in reasons)
            _same_subtype = (out.get("prev_source_subtype") in (None, out.get("source_subtype")))
            # Ratio alone cannot distinguish a genuine runner from a bad tick.
            # Key this exception on representation stability, not individual
            # source trust: pool quotes are untrusted singly but can be
            # corroborated as a stable sequence.
            _stable_representation = (
                _same_subtype
                and out.get("source_subtype") in CORROBORATABLE_SUBTYPES
                and out.get("precision_class") == out.get("prev_precision_class")
            )
            if (_ratio_only and not _representation_reasons
                    and _stable_representation):
                reasons = []
                _requires_confirmation = True

        # An independent source that agrees clears a suspect mark.
        if reasons and independent_price:
            try:
                ip = float(independent_price)
                if ip > 0 and abs(p - ip) / ip <= independent_tolerance:
                    out["confirmation_source"] = "independent_quote"
                    reasons = []
            except (TypeError, ValueError):
                pass

        if reasons:
            out["quarantined"] = True
            out["quarantine_reason"] = "; ".join(reasons)[:400]
            # Most specific cause wins, so telemetry names the real signal
            # rather than collapsing everything to "big jump".
            joined = out["quarantine_reason"]
            if "precision_class_change" in joined:
                out["integrity_state"] = INTEGRITY_Q_PRECISION
            elif "migration_change" in joined:
                out["integrity_state"] = INTEGRITY_Q_MIGRATION
            elif "subtype_change" in joined:
                out["integrity_state"] = INTEGRITY_Q_SOURCE
            else:
                out["integrity_state"] = INTEGRITY_Q_DISCONTINUITY
        else:
            out["integrity_state"] = (
                INTEGRITY_UNCONFIRMED if _requires_confirmation
                else (INTEGRITY_TRUSTED if out["trusted_source"]
                      else INTEGRITY_UNCONFIRMED)
            )
            out["requires_confirmation"] = bool(_requires_confirmation)
    except Exception as exc:
        out["quarantined"] = True
        out["quarantine_reason"] = f"evaluate_error={type(exc).__name__}"
        out["integrity_state"] = INTEGRITY_Q_DISCONTINUITY
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Runner confirmation
# ─────────────────────────────────────────────────────────────────────────────
def _pct_of(mark: Mapping[str, Any], entry: float) -> Optional[float]:
    try:
        p = float(mark.get("price"))
        return (p - entry) / entry * 100.0 if (p > 0 and entry > 0) else None
    except (TypeError, ValueError):
        return None


def _mark_subtype(mark: Mapping[str, Any]) -> str:
    explicit = str(mark.get("source_subtype") or "").strip().lower()
    return explicit or classify_subtype(mark.get("raw_source"))


def _mark_precision(mark: Mapping[str, Any]) -> str:
    explicit = str(mark.get("precision_class") or "").strip().lower()
    return explicit or precision_class(mark.get("price"))


def _source_family(mark: Mapping[str, Any]) -> str:
    # qualified_source keeps the nominal family stable across provider labels.
    src = str(mark.get("source") or mark.get("qualified_source") or "").lower()
    if ":" in src:
        return src.split(":", 1)[0]
    return src or "unknown"


def confirm_runner(
    *,
    marks: list,
    threshold_pct: float,
    entry_price: float,
    executable_quote: Optional[float] = None,
    min_interval_sec: float = 5.0,
    agreement_tolerance: float = 0.12,
) -> Dict[str, Any]:
    """Confirm runner authority from corroborated evidence.

    A pool quote remains individually UNCONFIRMED. Two compatible,
    non-quarantined observations from a recognised corroboratable subtype may
    establish runner authority. Fallback, market-cap-derived, unknown and
    legacy flattened sources cannot self-confirm.
    """
    out = {
        "confirmed": False, "reason": "insufficient_evidence",
        "trusted_peak": None, "promotion_mark_1": None,
        "promotion_mark_2": None, "promotion_ts_1": None,
        "promotion_ts_2": None, "confirmation_source": None,
        "confirmation_ts": None, "runner_integrity": RUNNER_UNCONFIRMED,
    }
    try:
        e = float(entry_price or 0)
        if e <= 0:
            out["reason"] = "no_entry_price"
            return out

        usable = []
        for m in marks or []:
            try:
                p = float(m.get("price"))
                if p <= 0 or m.get("quarantined"):
                    continue
                subtype = _mark_subtype(m)
                state = str(m.get("integrity_state") or "")
                if state.startswith("QUARANTINED_") or state == INTEGRITY_LEGACY:
                    continue
                usable.append({
                    "price": p,
                    "ts": float(m.get("ts") or 0),
                    "pct": (p - e) / e * 100.0,
                    "subtype": subtype,
                    "precision": _mark_precision(m),
                    "family": _source_family(m),
                    "raw_source": str(m.get("raw_source") or ""),
                    "individually_trusted": is_trusted_subtype(m.get("raw_source")),
                })
            except (TypeError, ValueError):
                continue
        usable.sort(key=lambda x: x["ts"])
        above = [m for m in usable if m["pct"] >= threshold_pct]

        # One valid observation plus an independent executable quote.
        if above and executable_quote:
            try:
                q = float(executable_quote)
                for m in above:
                    if (m["subtype"] not in NO_SELF_CONFIRM_SUBTYPES and q > 0
                            and abs(m["price"] - q) / q <= agreement_tolerance):
                        out.update(
                            confirmed=True, reason="mark_plus_executable_quote",
                            trusted_peak=min(m["price"], q),
                            promotion_mark_1=m["price"], promotion_mark_2=q,
                            promotion_ts_1=m["ts"], promotion_ts_2=m["ts"],
                            confirmation_source="executable_quote",
                            confirmation_ts=m["ts"],
                            runner_integrity=RUNNER_CONFIRMED,
                        )
                        return out
            except (TypeError, ValueError):
                pass

        # Two compatible observations. Individually trusted curve/reserve marks
        # and corroboratable pool sequences are eligible; unsafe subtypes are not.
        for i in range(len(above)):
            for j in range(i + 1, len(above)):
                a, b = above[i], above[j]
                if b["ts"] - a["ts"] < min_interval_sec:
                    continue
                if a["family"] != b["family"] or a["subtype"] != b["subtype"]:
                    continue
                if a["precision"] != b["precision"]:
                    continue
                if a["subtype"] in NO_SELF_CONFIRM_SUBTYPES:
                    continue
                if a["subtype"] not in CORROBORATABLE_SUBTYPES:
                    continue
                hi = max(a["price"], b["price"])
                if hi <= 0 or abs(a["price"] - b["price"]) / hi > agreement_tolerance:
                    continue
                source = (
                    "corroborated_pool_sequence"
                    if a["subtype"] == SUBTYPE_POOL
                    else "trusted_mark_pair"
                )
                out.update(
                    confirmed=True, reason="two_consistent_marks",
                    trusted_peak=min(a["price"], b["price"]),
                    promotion_mark_1=a["price"], promotion_mark_2=b["price"],
                    promotion_ts_1=a["ts"], promotion_ts_2=b["ts"],
                    confirmation_source=source, confirmation_ts=b["ts"],
                    runner_integrity=RUNNER_CONFIRMED,
                )
                return out

        if above:
            out["reason"] = "single_or_incompatible_unconfirmed_mark"
        raw_above = [m for m in (marks or [])
                     if _pct_of(m, e) is not None and _pct_of(m, e) >= threshold_pct]
        if raw_above and all(m.get("quarantined") for m in raw_above):
            out["runner_integrity"] = RUNNER_QUARANTINED
            out["reason"] = "all_threshold_marks_quarantined"
    except Exception as exc:
        out["reason"] = f"confirm_error={type(exc).__name__}"
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Quarantine telemetry
# ─────────────────────────────────────────────────────────────────────────────
_DDL = f"""
CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ts REAL NOT NULL,
    position_id INTEGER,
    mint_address TEXT,
    raw_price REAL,
    prev_trusted_price REAL,
    ratio REAL,
    sigfigs INTEGER,
    precision_class TEXT,
    prev_precision_class TEXT,
    nominal_source TEXT,
    source_subtype TEXT,
    prev_source_subtype TEXT,
    migration_state TEXT,
    confirmation_source TEXT,
    quarantine_reason TEXT
);
"""


def ensure_quarantine_table(conn) -> bool:
    try:
        conn.execute(_DDL)
        conn.execute(f"CREATE INDEX IF NOT EXISTS idx_mq_ts ON {QUARANTINE_TABLE}(ts)")
        return True
    except Exception:
        return False


def record_quarantine(conn, verdict: Mapping[str, Any], *,
                      position_id: Optional[int] = None,
                      mint_address: str = "") -> bool:
    """Retain the suspect observation. Never raises, never discards silently."""
    try:
        ensure_quarantine_table(conn)
        conn.execute(
            f"""INSERT INTO {QUARANTINE_TABLE}
                (ts, position_id, mint_address, raw_price, prev_trusted_price,
                 ratio, sigfigs, precision_class, prev_precision_class,
                 nominal_source, source_subtype, prev_source_subtype,
                 migration_state, confirmation_source, quarantine_reason)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (float(verdict.get("ts") or time.time()), position_id, mint_address,
             verdict.get("price"), verdict.get("prev_trusted_price"),
             verdict.get("ratio"), verdict.get("sigfigs"),
             verdict.get("precision_class"), verdict.get("prev_precision_class"),
             verdict.get("nominal_source"), verdict.get("source_subtype"),
             verdict.get("prev_source_subtype"), verdict.get("migration_state"),
             verdict.get("confirmation_source"),
             str(verdict.get("quarantine_reason") or "")[:400]),
        )
        return True
    except Exception:
        return False


# ─────────────────────────────────────────────────────────────────────────────
# TAPE QUALITY  —  ORACLE_CONTINUITY_SIGNOFF_20260808
#
# ROOT CAUSE THIS ADDRESSES
#
# A runner floor can only arm if confirm_runner_pool_aware() finds two
# compatible marks whose subtype is in RUNNER_QUALIFYING_SUBTYPES. Only the
# clean websocket path in ws_price_oracle._write_mtm(mint, price, "helius")
# produces such a subtype. Every degraded path -- wss_fail_fallback_*,
# keepalive_*, stall_recovery_*, cold_recovery_*, coverage_failsafe* -- is
# correctly classified as fallback_quote and correctly refused runner
# authority.
#
# That refusal is right. What was wrong is that it happened SILENTLY. Across
# 2026-08-07 20:16 -> 2026-08-08 08:16 the tape was dominated by degraded
# sources (Mode-B price ages of 31s-141s against a websocket cadence), so
# _trusted_peak_from_tape() returned None on every evaluation, no floor ever
# armed, and all 57 positions decayed into MAX_HOLD_TIME or HARD_STOP. Eight
# positions that touched +100% finished flat or at the stop.
#
# There was no log line anywhere saying "runner harvesting is currently
# impossible". This module supplies it. It is a MEASUREMENT ONLY: it never
# relabels a degraded source as trusted, because doing so is exactly the
# family-laundering defect that produces false floors on stale prices.
# ─────────────────────────────────────────────────────────────────────────────

# Subtypes that can carry runner authority. Must stay in sync with the
# _approved_families set in execution_engine._trusted_peak_from_tape().
RUNNER_QUALIFYING_SUBTYPES = frozenset({SUBTYPE_CURVE, SUBTYPE_POOL})

TAPE_HEALTHY = "HEALTHY"
TAPE_DEGRADED = "DEGRADED"
TAPE_INERT = "RUNNER_MACHINERY_INERT"
TAPE_UNKNOWN = "UNKNOWN"

# Below this share of qualifying marks, treat runner harvesting as inoperable.
TAPE_INERT_BELOW_PCT = 20.0
TAPE_DEGRADED_BELOW_PCT = 60.0


def tape_quality(conn, window_sec: float = 900.0) -> dict:
    """
    Share of recent mark_tape rows that could contribute to runner confirmation.

    Returns:
        {
          "verdict":            HEALTHY | DEGRADED | RUNNER_MACHINERY_INERT | UNKNOWN,
          "qualifying_pct":     float,
          "n":                  int,
          "by_subtype":         {subtype: count},
          "can_qualify_runners": bool,
          "note":               str,
        }

    Never raises. Read-only.
    """
    out = {
        "verdict": TAPE_UNKNOWN,
        "qualifying_pct": 0.0,
        "n": 0,
        "by_subtype": {},
        "can_qualify_runners": False,
        "note": "",
    }
    try:
        cutoff = time.time() - max(60.0, float(window_sec))
        rows = conn.execute(
            "SELECT COALESCE(source_subtype,'unknown') AS st, COUNT(*) AS c "
            "FROM mark_tape WHERE ts >= ? GROUP BY st",
            (cutoff,),
        ).fetchall()
        counts = {}
        for r in rows:
            try:
                counts[str(r[0])] = int(r[1])
            except Exception:
                continue
        total = sum(counts.values())
        out["by_subtype"] = counts
        out["n"] = total
        if not total:
            out["note"] = "no marks in window"
            return out
        good = sum(c for st, c in counts.items() if st in RUNNER_QUALIFYING_SUBTYPES)
        pct = 100.0 * good / total
        out["qualifying_pct"] = pct
        if pct < TAPE_INERT_BELOW_PCT:
            out["verdict"] = TAPE_INERT
            out["note"] = (
                "runner confirmation is effectively impossible; expect every "
                "position to terminate on MAX_HOLD or HARD_STOP regardless of peak"
            )
        elif pct < TAPE_DEGRADED_BELOW_PCT:
            out["verdict"] = TAPE_DEGRADED
            out["note"] = "runner confirmation intermittent"
        else:
            out["verdict"] = TAPE_HEALTHY
            out["note"] = "ok"
        out["can_qualify_runners"] = pct >= TAPE_INERT_BELOW_PCT
    except Exception as exc:
        out["note"] = f"tape_quality_error={type(exc).__name__}"
    return out
