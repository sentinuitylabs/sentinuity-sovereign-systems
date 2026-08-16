#!/usr/bin/env python3
"""
SENTINUITY — PEAK AUTHORITY AND EXPLICIT RUNNER CONFIRMATION
================================================================================

WHY THIS IS A NEW FILE
----------------------
The audited archive (claudit_04_08_26) does NOT contain the pool-corroboration
advancement described in the sign-off directive sections 1.D and 1.E. Shipping a
whole-file replacement for execution_engine.py or mark_provenance.py against that
baseline would overwrite whatever advancement does exist in the running tree.

Everything here is therefore ADDITIVE. It defines no strategy, changes no
threshold, and imports nothing from execution_engine. Callers opt in.

WHAT THIS MODULE OWNS
---------------------
1. Quarantine ledger persistence that survives schema drift.
   Reproduced defect: `CREATE TABLE IF NOT EXISTS mark_quarantine` does not add
   columns to a table created by an older build. record_quarantine() then fails
   its INSERT, returns False, and both it and its caller swallow the exception.
   Result: quarantined mark_tape rows with zero ledger rows -- exactly the
   11-vs-0 split in the three-hour window.

2. The five distinct peak concepts required by directive section 5, kept
   separate and individually labelled.

3. Explicit runner confirmation state: runner_confirmed,
   runner_integrity_status, runner_confirmation_source, runner_confirmed_at,
   runner_confirmation_evidence. No consumer may infer confirmation from a
   percentage.

4. A pool-aware corroboration function implementing the directive section 7
   contract, so pool quotes can confirm a runner on two compatible observations
   while single spikes and representation transitions still cannot.

CONTRACTS
---------
  * Never raises into the mark path. Every public function is exception-safe.
  * Never widens authority. Every ambiguous case resolves to "not authorised".
  * Never deletes raw diagnostics. Legacy peaks stay visible, labelled.
  * No sizing, gating, threshold, close-truth or Substrate surface is touched.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

SERVICE = "peak_authority"

QUARANTINE_TABLE = "mark_quarantine"
QUARANTINE_OVERFLOW_TABLE = "mark_quarantine_overflow"

# ── Peak classes (directive section 5) ───────────────────────────────────────
PEAK_RAW_OBSERVED = "RAW_OBSERVED_PEAK"
PEAK_TRUSTED_NON_RUNNER = "TRUSTED_NON_RUNNER_PEAK"
PEAK_CONFIRMED_RUNNER = "CONFIRMED_RUNNER_PEAK"
PEAK_EXECUTABLE = "EXECUTABLE_PEAK"
PEAK_LEGACY_UNCONFIRMED = "LEGACY_PEAK_WITHOUT_CONFIRMATION"

AUTHORITATIVE_PEAK_CLASSES = frozenset({PEAK_CONFIRMED_RUNNER, PEAK_EXECUTABLE})

# ── Runner integrity states ──────────────────────────────────────────────────
RUNNER_UNCONFIRMED = "RUNNER_UNCONFIRMED"
RUNNER_CONFIRMED = "RUNNER_CONFIRMED"
RUNNER_QUARANTINED = "RUNNER_QUARANTINED"

# ── Confirmation sources ─────────────────────────────────────────────────────
CONF_POOL_SEQUENCE = "corroborated_pool_sequence"
CONF_TRUSTED_PAIR = "trusted_mark_pair"
CONF_EXECUTABLE_QUOTE = "executable_quote"

# ── Source families that may corroborate WITHIN themselves ───────────────────
# A family may self-corroborate only if two observations are separated in time,
# share subtype and precision class, and agree within tolerance. Fallback,
# unknown and market-cap families may never self-confirm (directive section 7).
# SIGNOFF_RUNNER_SUBTYPE_POLICY_20260812: confirm_runner_pool_aware() is the UNGATED path used by
# execution_engine when three-layer authority is disabled, and compatible_pair()
# vetoed on this set. It held a third, independent copy of the corroboration
# policy that also omitted the executable families, so correcting only
# mark_provenance would have left the veto in place.
SELF_CORROBORATING_FAMILIES = frozenset(
    {"pool_quote", "router_executable", "pool_executable"}
)
# PRICE_FAMILY_TRUTH_20260803: curve_reserve is synthetic and currently
# observed on a materially different scale from executable pool quotes. It is
# retained for diagnostics but may not self-confirm runner authority.
NON_CONFIRMING_FAMILIES = frozenset(
    {"fallback_quote", "market_cap_derived", "unknown", "curve_reserve"}
)

# Default corroboration parameters. Deliberately identical to the values already
# used by the tree so this module changes no behaviour it does not have to.
DEFAULT_MIN_INTERVAL_SEC = 5.0
DEFAULT_AGREEMENT_TOLERANCE = 0.12
DEFAULT_RUNNER_THRESHOLD_PCT = 20.0


# =============================================================================
# 1. QUARANTINE LEDGER PERSISTENCE (directive Defect C)
# =============================================================================
_QUARANTINE_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("ts", "REAL"),
    ("position_id", "INTEGER"),
    ("mint_address", "TEXT"),
    ("raw_price", "REAL"),
    ("prev_trusted_price", "REAL"),
    ("ratio", "REAL"),
    ("sigfigs", "INTEGER"),
    ("precision_class", "TEXT"),
    ("prev_precision_class", "TEXT"),
    ("nominal_source", "TEXT"),
    ("raw_source", "TEXT"),
    ("qualified_source", "TEXT"),
    ("source_subtype", "TEXT"),
    ("prev_source_subtype", "TEXT"),
    ("migration_state", "TEXT"),
    ("integrity_state", "TEXT"),
    ("confirmation_source", "TEXT"),
    ("quarantine_reason", "TEXT"),
)


def ensure_quarantine_ledger(conn) -> Dict[str, Any]:
    """
    Create the quarantine ledger, and repair it if an older build already
    created it with fewer columns.

    `CREATE TABLE IF NOT EXISTS` is a no-op against a pre-existing table. That
    is the silent failure that produced quarantined tape rows with an empty
    ledger. Every missing column is added explicitly.
    """
    out = {"ok": False, "created": False, "added": [], "error": ""}
    try:
        existing = {
            r[1] for r in conn.execute(
                f"PRAGMA table_info({QUARANTINE_TABLE})").fetchall()
        }
        if not existing:
            cols = ",\n    ".join(f"{n} {t}" for n, t in _QUARANTINE_COLUMNS)
            conn.execute(
                f"CREATE TABLE IF NOT EXISTS {QUARANTINE_TABLE} (\n"
                f"    id INTEGER PRIMARY KEY AUTOINCREMENT,\n    {cols}\n)"
            )
            out["created"] = True
        else:
            for name, typ in _QUARANTINE_COLUMNS:
                if name not in existing:
                    try:
                        conn.execute(
                            f"ALTER TABLE {QUARANTINE_TABLE} "
                            f"ADD COLUMN {name} {typ}")
                        out["added"].append(name)
                    except Exception:
                        pass
        try:
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_mq_ts "
                f"ON {QUARANTINE_TABLE}(ts)")
            conn.execute(
                f"CREATE INDEX IF NOT EXISTS idx_mq_pos "
                f"ON {QUARANTINE_TABLE}(position_id, ts)")
        except Exception:
            pass
        out["ok"] = True
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _ensure_overflow(conn) -> bool:
    try:
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {QUARANTINE_OVERFLOW_TABLE} ("
            " id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL,"
            " position_id INTEGER, mint_address TEXT, payload_json TEXT,"
            " failure TEXT)")
        return True
    except Exception:
        return False


def record_quarantine_strict(
    conn,
    verdict: Mapping[str, Any],
    *,
    position_id: Optional[int] = None,
    mint_address: str = "",
    raw_source: str = "",
    qualified_source: str = "",
) -> Dict[str, Any]:
    """
    Persist a quarantined mark. Returns a result dict rather than a bare bool so
    the caller can log a real failure instead of discarding it.

    A persistence failure never promotes the mark. The mark's integrity_state in
    mark_tape remains QUARANTINED_*, and the observation is additionally written
    to an overflow table so nothing is lost.
    """
    res = {"persisted": False, "overflow": False, "repaired": [], "error": ""}
    try:
        ledger = ensure_quarantine_ledger(conn)
        res["repaired"] = ledger.get("added") or []
        if not ledger.get("ok"):
            res["error"] = ledger.get("error") or "ledger_unavailable"

        cols = {r[1] for r in conn.execute(
            f"PRAGMA table_info({QUARANTINE_TABLE})").fetchall()}

        values: Dict[str, Any] = {
            "ts": float(verdict.get("ts") or time.time()),
            "position_id": position_id,
            "mint_address": str(mint_address or ""),
            "raw_price": verdict.get("price"),
            "prev_trusted_price": verdict.get("prev_trusted_price"),
            "ratio": verdict.get("ratio"),
            "sigfigs": verdict.get("sigfigs"),
            "precision_class": verdict.get("precision_class"),
            "prev_precision_class": verdict.get("prev_precision_class"),
            "nominal_source": verdict.get("nominal_source"),
            "raw_source": str(raw_source or verdict.get("raw_source") or ""),
            "qualified_source": str(
                qualified_source or verdict.get("qualified_source") or ""),
            "source_subtype": verdict.get("source_subtype"),
            "prev_source_subtype": verdict.get("prev_source_subtype"),
            "migration_state": verdict.get("migration_state"),
            "integrity_state": verdict.get("integrity_state"),
            "confirmation_source": verdict.get("confirmation_source"),
            "quarantine_reason": str(
                verdict.get("quarantine_reason") or "")[:400],
        }
        usable = [k for k in values if k in cols]
        if usable:
            placeholders = ",".join("?" for _ in usable)
            try:
                conn.execute(
                    f"INSERT INTO {QUARANTINE_TABLE} ({','.join(usable)}) "
                    f"VALUES ({placeholders})",
                    tuple(values[k] for k in usable),
                )
            except sqlite3.IntegrityError as exc:
                # Backward compatibility for the legacy UNIQUE(mint_address)
                # quarantine table seen in the 2026-08-09 runtime. Preserve its
                # one-row-per-mint contract by refreshing the latest evidence
                # rather than retrying an impossible INSERT every sweep.
                if ("UNIQUE constraint failed" not in str(exc)
                        or "mint_address" not in str(exc)
                        or "mint_address" not in usable
                        or not str(values.get("mint_address") or "")):
                    raise
                set_cols = [k for k in usable if k not in ("id", "mint_address")]
                if not set_cols:
                    raise
                conn.execute(
                    f"UPDATE {QUARANTINE_TABLE} SET "
                    + ",".join(f"{k}=?" for k in set_cols)
                    + " WHERE mint_address=?",
                    tuple(values[k] for k in set_cols)
                    + (str(values.get("mint_address") or ""),),
                )
            res["persisted"] = True
            return res
        res["error"] = res["error"] or "no_writable_columns"
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"

    # Ledger write failed. Retain the observation rather than lose it.
    try:
        if _ensure_overflow(conn):
            conn.execute(
                f"INSERT INTO {QUARANTINE_OVERFLOW_TABLE}"
                " (ts, position_id, mint_address, payload_json, failure)"
                " VALUES (?,?,?,?,?)",
                (float(verdict.get("ts") or time.time()), position_id,
                 str(mint_address or ""),
                 json.dumps({k: _jsonable(v) for k, v in dict(verdict).items()}),
                 res["error"][:400]),
            )
            res["overflow"] = True
    except Exception:
        pass
    return res


def _jsonable(v: Any) -> Any:
    if isinstance(v, (str, int, float, bool)) or v is None:
        return v
    return str(v)


def quarantine_ledger_health(conn) -> Dict[str, Any]:
    """
    Compare quarantined mark_tape rows against ledger rows. Any positive
    `missing` value is a live persistence defect.
    """
    out = {"tape_quarantined": 0, "ledger_rows": 0, "overflow_rows": 0,
           "missing": 0, "healthy": False, "error": ""}
    try:
        try:
            out["tape_quarantined"] = int(conn.execute(
                "SELECT COUNT(*) FROM mark_tape "
                "WHERE COALESCE(integrity_state,'') LIKE 'QUARANTINED_%'"
            ).fetchone()[0] or 0)
        except Exception:
            pass
        try:
            out["ledger_rows"] = int(conn.execute(
                f"SELECT COUNT(*) FROM {QUARANTINE_TABLE}").fetchone()[0] or 0)
        except Exception:
            pass
        try:
            out["overflow_rows"] = int(conn.execute(
                f"SELECT COUNT(*) FROM {QUARANTINE_OVERFLOW_TABLE}"
            ).fetchone()[0] or 0)
        except Exception:
            pass
        out["missing"] = max(
            0, out["tape_quarantined"] - out["ledger_rows"] - out["overflow_rows"])
        out["healthy"] = out["missing"] == 0
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


# =============================================================================
# 2. EXPLICIT RUNNER CONFIRMATION STATE (directive section 1.E / 4.D)
# =============================================================================
_RUNNER_COLUMNS: Tuple[Tuple[str, str], ...] = (
    ("runner_confirmed", "INTEGER DEFAULT 0"),
    ("runner_integrity_status", "TEXT"),
    ("runner_confirmation_source", "TEXT"),
    ("runner_confirmed_at", "REAL"),
    ("runner_confirmation_evidence", "TEXT"),
    ("peak_authority_class", "TEXT"),
    ("legacy_peak_label", "TEXT"),
)


def ensure_runner_confirmation_columns(conn, table: str = "paper_positions") -> bool:
    try:
        have = {r[1] for r in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        if not have:
            return False
        for name, typ in _RUNNER_COLUMNS:
            if name not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {typ}")
                except Exception:
                    pass
        return True
    except Exception:
        return False


def persist_runner_confirmation(
    conn,
    position_id: int,
    *,
    confirmed: bool,
    integrity_status: str,
    confirmation_source: str = "",
    evidence: Optional[Mapping[str, Any]] = None,
    confirmed_at: Optional[float] = None,
    table: str = "paper_positions",
) -> bool:
    """
    Write explicit confirmation state. Confirmation is latched: once a position
    is confirmed it is never silently demoted by a later unconfirmed evaluation.
    """
    try:
        ensure_runner_confirmation_columns(conn, table)
        payload = json.dumps({k: _jsonable(v)
                              for k, v in dict(evidence or {}).items()})[:2000]
        if confirmed:
            conn.execute(
                f"UPDATE {table} SET runner_confirmed=1,"
                " runner_integrity_status=?,"
                " runner_confirmation_source=?,"
                " runner_confirmed_at=CASE WHEN COALESCE(runner_confirmed_at,0)>0"
                "   THEN runner_confirmed_at ELSE ? END,"
                " runner_confirmation_evidence=?"
                " WHERE id=?",
                (str(integrity_status or RUNNER_CONFIRMED),
                 str(confirmation_source or ""),
                 float(confirmed_at or time.time()), payload, int(position_id)),
            )
        else:
            # Never clear an existing confirmation from a later weaker read.
            conn.execute(
                f"UPDATE {table} SET"
                " runner_integrity_status=CASE WHEN COALESCE(runner_confirmed,0)=1"
                "   THEN runner_integrity_status ELSE ? END"
                " WHERE id=?",
                (str(integrity_status or RUNNER_UNCONFIRMED), int(position_id)),
            )
        return True
    except Exception:
        return False


def read_runner_confirmation(conn, position_id: int,
                             table: str = "paper_positions") -> Dict[str, Any]:
    """
    The ONLY sanctioned way to ask whether a position has a confirmed runner.
    Absence of the columns, or absence of the row, is not confirmation.
    """
    out = {"confirmed": False, "integrity_status": RUNNER_UNCONFIRMED,
           "confirmation_source": "", "confirmed_at": None, "evidence": "",
           "available": False}
    try:
        have = {r[1] for r in conn.execute(
            f"PRAGMA table_info({table})").fetchall()}
        if "runner_confirmed" not in have:
            return out
        out["available"] = True
        row = conn.execute(
            f"SELECT COALESCE(runner_confirmed,0),"
            f" COALESCE(runner_integrity_status,''),"
            f" COALESCE(runner_confirmation_source,''),"
            f" runner_confirmed_at,"
            f" COALESCE(runner_confirmation_evidence,'')"
            f" FROM {table} WHERE id=?", (int(position_id),)).fetchone()
        if not row:
            return out
        out["confirmed"] = bool(int(row[0] or 0) == 1)
        out["integrity_status"] = str(row[1] or (
            RUNNER_CONFIRMED if out["confirmed"] else RUNNER_UNCONFIRMED))
        out["confirmation_source"] = str(row[2] or "")
        out["confirmed_at"] = float(row[3]) if row[3] else None
        out["evidence"] = str(row[4] or "")
    except Exception:
        pass
    return out


# =============================================================================
# 3. POOL-AWARE CORROBORATION (directive sections 1.D and 7)
# =============================================================================
def _f(x: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        v = float(x)
        return v if v == v and v not in (float("inf"), float("-inf")) else default
    except (TypeError, ValueError):
        return default


def _family(mark: Mapping[str, Any]) -> str:
    sub = str(mark.get("source_subtype") or "").strip().lower()
    if sub:
        return sub
    # Derive from raw_source only if the subtype was not carried through.
    try:
        from services.mark_provenance import classify_subtype
        return str(classify_subtype(mark.get("raw_source")) or "unknown")
    except Exception:
        return "unknown"


def _precision(mark: Mapping[str, Any]) -> str:
    pc = str(mark.get("precision_class") or "").strip().lower()
    if pc:
        return pc
    try:
        from services.mark_provenance import precision_class
        return str(precision_class(mark.get("price")) or "unknown")
    except Exception:
        return "unknown"


def compatible_pair(
    a: Mapping[str, Any],
    b: Mapping[str, Any],
    *,
    min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
    agreement_tolerance: float = DEFAULT_AGREEMENT_TOLERANCE,
) -> Tuple[bool, str]:
    """
    Directive section 7 "genuine pool runner" contract, applied to any
    self-corroborating family.

    Requires: same position, same mint, compatible source family, compatible
    subtype, compatible precision class, minimum time separation, both
    non-quarantined, prices within tolerance.
    """
    try:
        if a.get("quarantined") or b.get("quarantined"):
            return False, "quarantined_member"
        pa, pb = _f(a.get("price")), _f(b.get("price"))
        if not pa or not pb or pa <= 0 or pb <= 0:
            return False, "non_positive_price"
        if (a.get("position_id") is not None and b.get("position_id") is not None
                and a.get("position_id") != b.get("position_id")):
            return False, "position_mismatch"
        if (a.get("mint") and b.get("mint")
                and str(a.get("mint")) != str(b.get("mint"))):
            return False, "mint_mismatch"
        fa, fb = _family(a), _family(b)
        if fa != fb:
            return False, f"subtype_mismatch={fa}->{fb}"
        if fa in NON_CONFIRMING_FAMILIES:
            return False, f"family_cannot_self_confirm={fa}"
        if fa not in SELF_CORROBORATING_FAMILIES:
            return False, f"family_not_self_corroborating={fa}"
        ca, cb = _precision(a), _precision(b)
        if ca != cb:
            return False, f"precision_mismatch={ca}->{cb}"
        ia = str(a.get("upstream_tick_id") or "").strip()
        ib = str(b.get("upstream_tick_id") or "").strip()
        if not ia or not ib:
            return False, "missing_upstream_tick_identity"
        if ia == ib:
            return False, "duplicate_upstream_tick"
        ua = _f(a.get("upstream_ts_ms"), 0.0) or 0.0
        ub = _f(b.get("upstream_ts_ms"), 0.0) or 0.0
        if ua <= 0 or ub <= 0:
            return False, "missing_upstream_tick_time"
        if abs(ub - ua) < float(min_interval_sec) * 1000.0:
            return False, "insufficient_upstream_time_separation"
        ta, tb = _f(a.get("ts"), 0.0) or 0.0, _f(b.get("ts"), 0.0) or 0.0
        if abs(tb - ta) < float(min_interval_sec):
            return False, "insufficient_time_separation"
        hi = max(pa, pb)
        if hi <= 0 or abs(pa - pb) / hi > float(agreement_tolerance):
            return False, "prices_outside_tolerance"
        return True, "compatible"
    except Exception as exc:
        return False, f"compare_error={type(exc).__name__}"


def confirm_runner_three_layer(
    conn,
    *,
    position_id: int,
    mint_address: str,
    entry_price: float,
    threshold_pct: float = DEFAULT_RUNNER_THRESHOLD_PCT,
) -> Dict[str, Any]:
    """
    Phase-2 authority bridge.

    The new authority is opt-in and fail-closed. Until the operator enables
    PEAK_TRUTH_AUTHORITY_ENABLED after paper validation, callers receive an
    explicit disabled result and the legacy path remains available unchanged.
    """
    try:
        from services import peak_truth as _pt
        if not _pt.authority_enabled():
            return {
                "confirmed": False,
                "reason": "three_layer_authority_disabled",
                "runner_integrity": RUNNER_UNCONFIRMED,
                "confirmation_source": None,
                "confirmed_peak_price": None,
                "confirmed_peak_pct": None,
                "confirmation_ts": None,
                "evidence": {"authority_enabled": False},
            }
        result = _pt.evaluate_position(
            conn,
            position_id=int(position_id),
            mint_address=str(mint_address),
            entry_price=float(entry_price),
            threshold_pct=float(threshold_pct),
        )
        return {
            "confirmed": bool(result.get("confirmed")),
            "reason": result.get("reason"),
            "runner_integrity": (
                RUNNER_CONFIRMED if result.get("confirmed") else RUNNER_UNCONFIRMED
            ),
            "confirmation_source": result.get("confirmation_source"),
            "confirmed_peak_price": result.get("trusted_peak_price"),
            "confirmed_peak_pct": result.get("trusted_peak_pct"),
            "confirmation_ts": result.get("confirmation_ts"),
            "evidence": result.get("evidence") or {},
            "peak_state": result.get("state"),
        }
    except Exception as exc:
        return {
            "confirmed": False,
            "reason": f"three_layer_error={type(exc).__name__}",
            "runner_integrity": RUNNER_UNCONFIRMED,
            "confirmation_source": None,
            "confirmed_peak_price": None,
            "confirmed_peak_pct": None,
            "confirmation_ts": None,
            "evidence": {},
        }


def confirm_runner_pool_aware(
    *,
    marks: Sequence[Mapping[str, Any]],
    entry_price: float,
    threshold_pct: float = DEFAULT_RUNNER_THRESHOLD_PCT,
    executable_quote: Optional[float] = None,
    min_interval_sec: float = DEFAULT_MIN_INTERVAL_SEC,
    agreement_tolerance: float = DEFAULT_AGREEMENT_TOLERANCE,
) -> Dict[str, Any]:
    """
    Confirm a runner on corroborated evidence.

    Accepts when EITHER
      (a) two non-quarantined observations of the SAME self-corroborating family
          (pool_quote or curve_reserve), same subtype and precision class,
          separated by >= min_interval_sec, both above threshold, agreeing
          within tolerance;  or
      (b) executable quotes remain diagnostic until they carry a complete
          upstream-identity and price-family contract.

    Rejects:
      * a single observation, however large
      * any pair spanning a precision or subtype transition
      * fallback / unknown / market-cap families
      * any pair containing a quarantined mark

    The confirmed peak is the LOWER of the corroborating pair. Two marks prove
    the price was at least the lower of them; they do not prove the higher.
    """
    out = {
        "confirmed": False,
        "reason": "insufficient_evidence",
        "runner_integrity": RUNNER_UNCONFIRMED,
        "confirmation_source": None,
        "confirmed_peak_price": None,
        "confirmed_peak_pct": None,
        "confirmation_ts": None,
        "evidence": {},
    }
    try:
        entry = _f(entry_price, 0.0) or 0.0
        if entry <= 0:
            out["reason"] = "no_entry_price"
            return out

        clean: List[Dict[str, Any]] = []
        for m in marks or []:
            price = _f(m.get("price"))
            if not price or price <= 0:
                continue
            pct = (price - entry) / entry * 100.0
            clean.append({
                "price": price,
                "ts": _f(m.get("ts"), 0.0) or 0.0,
                "pct": pct,
                "quarantined": bool(m.get("quarantined")),
                "source_subtype": m.get("source_subtype"),
                "precision_class": m.get("precision_class"),
                "raw_source": m.get("raw_source"),
                "qualified_source": m.get("qualified_source"),
                "position_id": m.get("position_id"),
                "mint": m.get("mint"),
                "upstream_tick_id": m.get("upstream_tick_id"),
                "upstream_ts_ms": m.get("upstream_ts_ms"),
            })
        clean.sort(key=lambda x: x["ts"])
        above = [m for m in clean
                 if m["pct"] >= float(threshold_pct) and not m["quarantined"]]

        # PRICE_TRUTH_SIGNOFF_20260803:
        # The former observation+executable_quote branch bypassed compatible_pair(),
        # so it did not prove upstream-tick independence, price-family compatibility,
        # precision compatibility, or a reconstructable second observation. Until an
        # executable quote carries the same full provenance contract as a tape mark,
        # it is diagnostic only and cannot confirm runner authority.
        q = _f(executable_quote)
        if above and q and q > 0:
            out["quote_diagnostic"] = {
                "available": True,
                "price": q,
                "reason": "quote_confirmation_disabled_missing_full_provenance",
            }

        # (a) two compatible same-family observations
        best: Optional[Dict[str, Any]] = None
        for i in range(len(above)):
            for j in range(i + 1, len(above)):
                a, b = above[i], above[j]
                ok, why = compatible_pair(
                    a, b, min_interval_sec=min_interval_sec,
                    agreement_tolerance=agreement_tolerance)
                if not ok:
                    continue
                peak = min(a["price"], b["price"])
                if best is None or peak > best["peak"]:
                    best = {"peak": peak, "a": a, "b": b, "why": why}
        if best:
            fam = _family(best["a"])
            peak = best["peak"]
            out.update(
                confirmed=True,
                reason="two_compatible_observations",
                runner_integrity=RUNNER_CONFIRMED,
                confirmation_source=(CONF_POOL_SEQUENCE if fam == "pool_quote"
                                     else CONF_TRUSTED_PAIR),
                confirmed_peak_price=peak,
                confirmed_peak_pct=(peak - entry) / entry * 100.0,
                confirmation_ts=max(best["a"]["ts"], best["b"]["ts"]),
                evidence={"mark_1": best["a"]["price"],
                          "mark_2": best["b"]["price"],
                          "subtype": fam,
                          "precision_class": _precision(best["a"]),
                          "upstream_tick_id_1": best["a"].get("upstream_tick_id"),
                          "upstream_tick_id_2": best["b"].get("upstream_tick_id"),
                          "upstream_ts_ms_1": best["a"].get("upstream_ts_ms"),
                          "upstream_ts_ms_2": best["b"].get("upstream_ts_ms"),
                          "separation_sec": abs(best["b"]["ts"] - best["a"]["ts"])},
            )
            return out

        if above:
            out["reason"] = "single_uncorroborated_observation"
        raw_above = [m for m in clean if m["pct"] >= float(threshold_pct)]
        if raw_above and all(m["quarantined"] for m in raw_above):
            out["runner_integrity"] = RUNNER_QUARANTINED
            out["reason"] = "all_threshold_marks_quarantined"
    except Exception as exc:
        out["reason"] = f"confirm_error={type(exc).__name__}"
    return out


def trusted_non_runner_peak(
    marks: Sequence[Mapping[str, Any]],
    entry_price: float,
    threshold_pct: float = DEFAULT_RUNNER_THRESHOLD_PCT,
) -> Tuple[Optional[float], Optional[float], str]:
    """
    Highest non-quarantined observation strictly BELOW the runner threshold.
    Directive section 5: a trusted non-runner peak may be updated only from
    valid trusted observations below runner threshold.
    """
    try:
        entry = _f(entry_price, 0.0) or 0.0
        if entry <= 0:
            return None, None, ""
        best_px, best_src = None, ""
        for m in marks or []:
            if m.get("quarantined"):
                continue
            px = _f(m.get("price"))
            if not px or px <= 0:
                continue
            pct = (px - entry) / entry * 100.0
            if pct >= float(threshold_pct):
                continue
            if best_px is None or px > best_px:
                best_px = px
                best_src = str(m.get("qualified_source")
                               or m.get("source") or "")
        if best_px is None:
            return None, None, ""
        return best_px, (best_px - entry) / entry * 100.0, best_src
    except Exception:
        return None, None, ""


def raw_observed_peak(marks: Sequence[Mapping[str, Any]]) -> Optional[float]:
    """Diagnostic only. Includes quarantined observations by design."""
    try:
        vals = [v for v in (_f(m.get("price")) for m in marks or []) if v and v > 0]
        return max(vals) if vals else None
    except Exception:
        return None


# =============================================================================
# 4. PEAK CLASSIFICATION AND EXIT AUTHORITY (directive sections 5 and 6)
# =============================================================================
def classify_peak(
    *,
    peak_pct: Optional[float],
    runner_threshold_pct: float = DEFAULT_RUNNER_THRESHOLD_PCT,
    from_confirmed_runner: bool = False,
    from_executable_quote: bool = False,
    from_trusted_observation: bool = False,
    has_linked_marks: bool = True,
) -> str:
    """
    Assign exactly one label to a peak value. Ambiguity resolves downward.
    """
    try:
        if from_executable_quote:
            return PEAK_EXECUTABLE
        if from_confirmed_runner:
            return PEAK_CONFIRMED_RUNNER
        pct = _f(peak_pct, 0.0) or 0.0
        if not has_linked_marks:
            return PEAK_LEGACY_UNCONFIRMED
        if from_trusted_observation and pct < float(runner_threshold_pct):
            return PEAK_TRUSTED_NON_RUNNER
        if pct >= float(runner_threshold_pct):
            # At or above runner threshold with no explicit confirmation.
            return PEAK_LEGACY_UNCONFIRMED
        return PEAK_RAW_OBSERVED
    except Exception:
        return PEAK_LEGACY_UNCONFIRMED


def peak_is_authoritative(peak_class: str) -> bool:
    return str(peak_class or "") in AUTHORITATIVE_PEAK_CLASSES


def runner_exit_authorised(
    conn,
    position_id: int,
    *,
    linked_confirmation: Optional[Mapping[str, Any]] = None,
    table: str = "paper_positions",
) -> Tuple[bool, str]:
    """
    Directive section 6. RUNNER_PROFIT_LOCK, runner trailing floor, runner latch
    and runner harvest mode require explicit confirmation.

    Authorised only when one of:
      * persisted runner_confirmed = 1
      * persisted runner_integrity_status = RUNNER_CONFIRMED
      * a linked confirmation record from confirm_runner_pool_aware() in this
        evaluation whose `confirmed` flag is True

    Everything else -- including a very high trusted_peak_pct, runner_peak_pct,
    highest_price_seen, held_peak_pct or peak_pnl_pct -- is not authorisation.

    Hard stop, max hold, stagnation, ordinary profit lock and safety exits are
    outside this function's scope and are unaffected by it.
    """
    try:
        if linked_confirmation and bool(linked_confirmation.get("confirmed")):
            src = str(linked_confirmation.get("confirmation_source") or "linked")
            return True, f"linked_confirmation:{src}"
        state = read_runner_confirmation(conn, position_id, table)
        if state.get("confirmed"):
            return True, ("persisted_runner_confirmed:"
                          + (state.get("confirmation_source") or "unknown"))
        if str(state.get("integrity_status") or "") == RUNNER_CONFIRMED:
            return True, "persisted_runner_integrity_status"
        if not state.get("available"):
            return False, "confirmation_columns_absent"
        return False, "no_explicit_confirmation"
    except Exception as exc:
        return False, f"authority_error={type(exc).__name__}"


def legacy_peak_view(
    *,
    trusted_peak_pct: Any = None,
    runner_peak_pct: Any = None,
    runner_latch_peak_pct: Any = None,
    peak_pnl_pct: Any = None,
    held_peak_pct: Any = None,
    highest_price_seen: Any = None,
    entry_price: Any = None,
    confirmed: bool = False,
) -> Dict[str, Any]:
    """
    Build a labelled, non-authoritative view of every historical peak field.

    Directive section 5: legacy peaks may remain visible but cannot influence
    authority. This is the single place a UI, report or audit should read them
    from, so the label always travels with the number.
    """
    vals = {
        "trusted_peak_pct": _f(trusted_peak_pct),
        "runner_peak_pct": _f(runner_peak_pct),
        "runner_latch_peak_pct": _f(runner_latch_peak_pct),
        "peak_pnl_pct": _f(peak_pnl_pct),
        "held_peak_pct": _f(held_peak_pct),
    }
    hp, ep = _f(highest_price_seen), _f(entry_price)
    if hp and ep and ep > 0:
        vals["highest_price_seen_pct"] = (hp - ep) / ep * 100.0
    present = {k: v for k, v in vals.items() if v is not None}
    best = max(present.values()) if present else None
    return {
        "fields": present,
        "max_pct": best,
        "authoritative": bool(confirmed),
        "label": (PEAK_CONFIRMED_RUNNER if confirmed else PEAK_LEGACY_UNCONFIRMED),
        "display_suffix": ("" if confirmed else " (unconfirmed)"),
    }


__all__ = [
    "PEAK_RAW_OBSERVED", "PEAK_TRUSTED_NON_RUNNER", "PEAK_CONFIRMED_RUNNER",
    "PEAK_EXECUTABLE", "PEAK_LEGACY_UNCONFIRMED", "AUTHORITATIVE_PEAK_CLASSES",
    "RUNNER_UNCONFIRMED", "RUNNER_CONFIRMED", "RUNNER_QUARANTINED",
    "CONF_POOL_SEQUENCE", "CONF_TRUSTED_PAIR", "CONF_EXECUTABLE_QUOTE",
    "ensure_quarantine_ledger", "record_quarantine_strict",
    "quarantine_ledger_health", "ensure_runner_confirmation_columns",
    "persist_runner_confirmation", "read_runner_confirmation",
    "compatible_pair", "confirm_runner_pool_aware", "confirm_runner_three_layer", "trusted_non_runner_peak",
    "raw_observed_peak", "classify_peak", "peak_is_authoritative",
    "runner_exit_authorised", "legacy_peak_view",
]


# ─────────────────────────────────────────────────────────────────────────────
# EXTERNAL MARKET CORROBORATION — PRICE_TRUTH_SIGNOFF_20260808
#
# NEW AUTHORITY CLASS. NOT A RELABELLING.
#
# The 2026-08-07/08 failure chain was:
#     native websocket degrades
#       -> mark carries fallback_quote subtype
#       -> confirm_runner_pool_aware() refuses it (CORRECTLY)
#       -> no trusted peak
#       -> no floor arms
#       -> every runner decays into MAX_HOLD or HARD_STOP
#
# The refusal is right and stays. What is added here is a SECOND, INDEPENDENT
# route to authority: if genuinely independent external markets agree that the
# move happened, that is real evidence the degraded native mark never supplied.
#
# WHAT THIS DELIBERATELY DOES NOT DO
#   * It does not reclassify the fallback mark as curve_reserve or pool_quote.
#     The original mark keeps its true provenance forever. Laundering the
#     subtype would corrupt every downstream consumer of price family.
#   * It does not let external corroboration arm a LIVE floor. External
#     indexers publish on caches of tens of seconds. They can prove a move was
#     REAL; they cannot prove it was REACHABLE at our size, and they cannot
#     supply the reaction latency a live stop needs. The measured -31% median
#     stop realisation is a latency problem, and corroborating a stale mark
#     with more stale marks does not improve it.
#
# So: paper may harvest on EXTERNAL_MARKET_CORROBORATED. Live may not.
# ─────────────────────────────────────────────────────────────────────────────

AUTHORITY_EXTERNAL_CORROBORATED = "EXTERNAL_MARKET_CORROBORATED"
AUTHORITY_EXECUTABLE_CONFIRMED = "EXECUTABLE_CONFIRMED"
AUTHORITY_EXECUTABLE_CROSS_SOURCE = "EXECUTABLE_CROSS_SOURCE_CONFIRMED"
# Every authority class that may arm a LIVE runner floor.
LIVE_AUTHORITY_CLASSES = (AUTHORITY_EXECUTABLE_CONFIRMED,
                          AUTHORITY_EXECUTABLE_CROSS_SOURCE)


def confirm_runner_externally_corroborated(
    *,
    mint_address: str,
    entry_price: float,
    native_mark: Optional[Mapping[str, Any]] = None,
    external_observations: Optional[Sequence[Mapping[str, Any]]] = None,
    executable_quote: Optional[Mapping[str, Any]] = None,
    is_live: bool = False,
    threshold_pct: float = DEFAULT_RUNNER_THRESHOLD_PCT,
) -> Dict[str, Any]:
    """
    Adjudicate a runner peak from independent market evidence.

    Returns the same contract shape as confirm_runner_pool_aware() so callers
    can treat the two interchangeably, plus `authority_class` and `verdict`.

    Never raises.
    """
    out = {
        "confirmed": False,
        "reason": "no_external_evidence",
        "runner_integrity": RUNNER_UNCONFIRMED,
        "confirmation_source": None,
        "confirmed_peak_price": None,
        "confirmed_peak_pct": None,
        "confirmation_ts": None,
        "authority_class": "NONE",
        "evidence": {},
    }
    try:
        from services.price_truth_adjudicator import (
            adjudicate, authority_for_mode, Observation,
        )
        obs = []
        if native_mark and native_mark.get("price"):
            obs.append({
                "source": "native_curve",
                "price": native_mark.get("price"),
                "age_sec": native_mark.get("age_sec"),
                # Provenance is preserved, never overwritten. A degraded mark
                # is carried INTO the adjudicator labelled degraded so it can be
                # outlier-tested rather than trusted.
                "degraded": bool(native_mark.get("degraded")),
            })
        for o in (external_observations or []):
            if o and o.get("price"):
                obs.append(dict(o))
        # PRICE_TRUTH_SIGNOFF_20260809: executable observations now carry the
        # full peak_truth evidence contract. The adjudicator fails closed on any
        # missing field, so a partially-populated row cannot arm a live floor.
        for _eq in (executable_quote if isinstance(executable_quote, (list, tuple))
                    else [executable_quote]):
            if not _eq or not _eq.get("price"):
                continue
            obs.append({
                "source": str(_eq.get("source") or "jupiter_executable"),
                "price": _eq.get("price"),
                "age_sec": _eq.get("age_sec"),
                "sellable": bool(_eq.get("sellable")),
                "integrity_status": _eq.get("integrity_status"),
                "price_impact_pct": _eq.get("price_impact_pct"),
                "raw_amount": _eq.get("raw_amount"),
                "quote_out_raw": _eq.get("quote_out_raw"),
                "min_out_raw": _eq.get("min_out_raw"),
                "context_slot": _eq.get("context_slot"),
                "route": _eq.get("route"),
            })
        if not obs:
            return out

        verdict = adjudicate(mint=str(mint_address), entry_price=entry_price,
                             observations=obs)
        auth = authority_for_mode(verdict, is_live=bool(is_live))
        out["evidence"] = {
            "state": verdict.get("state"),
            "market_consensus_price": verdict.get("market_consensus_price"),
            "market_consensus_spread_pct": verdict.get("market_consensus_spread_pct"),
            "corroborating_families": verdict.get("corroborating_families"),
            "native_is_outlier": verdict.get("native_is_outlier"),
            "observed_price": verdict.get("observed_price"),
            "confirmed_market_price": verdict.get("confirmed_market_price"),
            "executable_confirmed_price": verdict.get("executable_confirmed_price"),
            "authority_reason": verdict.get("authority_reason"),
            "reason_code": verdict.get("reason_code"),
            "executable_reject_code": verdict.get("executable_reject_code"),
            "warnings": verdict.get("warnings"),
            # PRICE_TRUTH_SIGNOFF_20260809 (blocker 2): this previously
            # hardcoded subtype="rpc_direct" purely to satisfy the downstream
            # family gate. That is provenance laundering: it asserted a native
            # RPC origin for evidence that came from a router quote or an
            # external indexer. The evidence now declares what it actually is,
            # and the gate in execution_engine was widened to understand the
            # real authority families instead of being lied to.
            "evidence_family": verdict.get("executable_family")
                               or ("market_indexer" if verdict.get("market_move_confirmed")
                                   else "observed"),
            "executable_family": verdict.get("executable_family"),
            "executable_cross_source": bool(verdict.get("executable_cross_source")),
            "executable_raw_amount": verdict.get("executable_raw_amount"),
            "executable_context_slot": verdict.get("executable_context_slot"),
            "executable_price_impact_pct": verdict.get("executable_price_impact_pct"),
        }
        out["verdict"] = verdict
        out["reason"] = auth.get("reason") or verdict.get("authority_reason")

        peak = auth.get("peak_price")
        if not auth.get("may_arm") or not peak:
            return out

        pct = (float(peak) - float(entry_price)) / float(entry_price) * 100.0
        if pct < float(threshold_pct):
            out["reason"] = f"corroborated_peak_{pct:.1f}pct_below_threshold_{threshold_pct:.1f}pct"
            return out

        out.update(
            confirmed=True,
            runner_integrity=RUNNER_CONFIRMED,
            confirmation_source=str(auth.get("authority_class")),
            confirmed_peak_price=float(peak),
            confirmed_peak_pct=pct,
            confirmation_ts=time.time(),
            authority_class=str(auth.get("authority_class")),
        )
        return out
    except Exception as exc:
        out["reason"] = f"external_corroboration_error={type(exc).__name__}"
        return out
