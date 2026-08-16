from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from typing import Any, Dict, Mapping, Optional

SCHEMA_VERSION = 1
AUTHORITY_FLAG = "PEAK_TRUTH_AUTHORITY_ENABLED"
MAX_AGE_SEC = float(os.getenv("PEAK_TRUTH_MAX_AGE_SEC", "3.0"))
MAX_DIVERGENCE_PCT = float(os.getenv("PEAK_TRUTH_MAX_DIVERGENCE_PCT", "8.0"))
MIN_CONFIRMATION_GAP_SEC = float(os.getenv("PEAK_TRUTH_MIN_CONFIRMATION_GAP_SEC", "0.25"))

# PACK_B_PEAK_EVIDENCE_TRUTH_20260806
# Layer C is a TIME SERIES of quotes, not a set. Two quotes taken seconds apart
# for the same mint and route are two observations, not one. The identity below
# therefore carries the request size and a coarse time bucket for kind "C".
# Without this, INSERT OR IGNORE silently discards every quote after the first
# whenever the provider omits contextSlot and outAmount (observed 2026-08-06:
# 136 quotes obtained, 15 persisted).
WITNESS_TS_QUANTUM_SEC = float(os.getenv("PEAK_TRUTH_WITNESS_TS_QUANTUM_SEC", "0.25"))

# Wall-clock freshness alone does not prove slot freshness. A writer can restamp
# an old account read with time.time() and pass MAX_AGE_SEC while the underlying
# state is hundreds of slots stale. Authority requires that the independent
# witness and the executable quote agree on WHEN, in slot terms, not just that
# both rows were written recently.
MAX_WITNESS_SLOT_SKEW = int(os.getenv("PEAK_TRUTH_MAX_WITNESS_SLOT_SKEW", "12"))

# PACK_C_PRICE_PEAK_EXIT_TRUTH_20260806
# Formula registry. A Pump bonding curve and a PumpSwap/AMM pool are different
# markets with different state layouts; one formula may not serve both. The
# curve account's virtual reserves are zeroed by the migrate instruction, so a
# curve formula applied post-migration reads zeros, not a price.
# Ref: pump-fun/pump-public-docs PUMP_PROGRAM_README.md — a completed curve has
# complete == true and real_token_reserves == 0; migrate() moves liquidity to
# PumpSwap (pAMMBay6oceH9fJKBRHGP5D4bD4sWpmSwMn52FMfXEA).
CURVE_MIGRATION_STATES = {"curve", "pre_migration", "bonding", "incomplete"}
POOL_MIGRATION_STATES = {"complete", "migrated", "pool", "amm", "pumpswap", "raydium"}
CURVE_FORMULA_PREFIXES = ("pump_curve", "bonding_curve", "curve_v")
POOL_FORMULA_PREFIXES = ("pumpswap", "amm_cp", "raydium", "pool_cp", "cpmm")

# Quote failure taxonomy. Collapsing these into a single "no quote" is how a
# provider outage gets misread as illiquidity and vice versa.
QUOTE_ERROR_CLASSES = (
    "OK", "NO_ROUTE", "AUTH_FAILURE", "RATE_LIMIT", "TIMEOUT",
    "STALE_RESPONSE", "IMPACT_BLOCK", "ILLIQUID", "PROVIDER_ERROR",
    "MALFORMED_RESPONSE", "UNCLASSIFIED",
    # CURVE_VERDICT_20260816: a bonding-curve mark whose executable authority
    # was withdrawn by the price-truth quorum. Deliberately NOT a market class
    # (see below): it says "we do not trust this number", not "the asset is
    # unsellable". Because it is != "OK", record_executable_quote() below
    # stamps the row DIAGNOSTIC_ONLY -- the observation survives as evidence
    # and carries no authority.
    "CURVE_DISPUTED",
)

# Only these classes describe the MARKET. The rest describe our own plumbing and
# must never be read as evidence about sellability.
MARKET_TRUTH_ERROR_CLASSES = {"OK", "NO_ROUTE", "IMPACT_BLOCK", "ILLIQUID"}


def _f(v: Any) -> Optional[float]:
    try:
        x = float(v)
        return x if x == x else None
    except (TypeError, ValueError):
        return None


def authority_enabled() -> bool:
    return str(os.getenv(AUTHORITY_FLAG, "0")).strip() == "1"


def _witness_id(kind: str, payload: Mapping[str, Any]) -> str:
    stable = {
        "kind": kind,
        "mint": payload.get("mint_address") or payload.get("mint"),
        "slot": payload.get("context_slot") or payload.get("slot"),
        "signature": payload.get("signature"),
        "account_hash": payload.get("account_hash"),
        "route": payload.get("route"),
        "quote_out_raw": payload.get("quote_out_raw"),
    }
    if kind == "C":
        # PACK_B: a quote is identified by (mint, route, size, out, slot, when).
        # Dropping size and time collapsed an entire quote series into one row.
        ts = _f(payload.get("quote_ts")) or time.time()
        q = WITNESS_TS_QUANTUM_SEC if WITNESS_TS_QUANTUM_SEC > 0 else 0.25
        stable["raw_amount"] = str(payload.get("raw_amount") or "")
        stable["ts_bucket"] = int(ts / q)
    return hashlib.sha256(
        json.dumps(stable, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _pos_int(v: Any) -> int:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return 0


def classify_quote_error(
    *, http_status: Any = None, exception: Any = None,
    provider_message: Any = None, route_present: Any = None,
    price_impact_pct: Any = None, impact_cap_pct: Any = None,
    quote_age_sec: Any = None, max_age_sec: Any = None,
    out_amount_raw: Any = None,
) -> str:
    """
    PACK_C: map a quote attempt to exactly one class from QUOTE_ERROR_CLASSES.

    The distinction that matters: PROVIDER_ERROR / RATE_LIMIT / TIMEOUT /
    AUTH_FAILURE describe OUR pipeline. NO_ROUTE / IMPACT_BLOCK / ILLIQUID
    describe THE MARKET. Only the latter are evidence about sellability; the
    former mean we simply do not know, and must not be recorded as if the
    position were unsellable.
    """
    msg = str(provider_message or "").strip().lower()
    st = _pos_int(http_status)
    if exception is not None:
        name = type(exception).__name__.lower() if not isinstance(exception, str) else str(exception).lower()
        if "timeout" in name or "timedout" in name:
            return "TIMEOUT"
        return "PROVIDER_ERROR"
    if st in (401, 403):
        return "AUTH_FAILURE"
    if st == 429:
        return "RATE_LIMIT"
    if st in (408, 504):
        return "TIMEOUT"
    if st >= 500:
        return "PROVIDER_ERROR"
    if "rate limit" in msg or "too many requests" in msg:
        return "RATE_LIMIT"
    if "unauthor" in msg or "forbidden" in msg or "invalid api key" in msg:
        return "AUTH_FAILURE"
    if "timeout" in msg or "timed out" in msg:
        return "TIMEOUT"
    age, cap = _f(quote_age_sec), _f(max_age_sec)
    if age is not None and cap is not None and age > cap:
        return "STALE_RESPONSE"
    imp, icap = _f(price_impact_pct), _f(impact_cap_pct)
    if imp is not None and icap is not None and imp > icap:
        return "IMPACT_BLOCK" if imp < 99.0 else "ILLIQUID"
    if route_present is False or "no route" in msg or "could not find any route" in msg:
        return "NO_ROUTE"
    if _pos_int(out_amount_raw) > 0:
        return "OK"
    if route_present is True:
        return "MALFORMED_RESPONSE"
    return "UNCLASSIFIED"


def _provenance():
    """Soft import. Absence is fail-closed: independence cannot be asserted."""
    try:
        from services import mark_provenance as mp  # type: ignore
        return mp
    except Exception:
        return None


def source_family(label: Any) -> str:
    """
    PACK_C: canonical witness family for a source label, via mark_provenance.

    Returns 'UNVERIFIABLE' when the provenance module is unavailable, which
    callers must treat as "cannot prove independence", not as "independent".
    """
    mp = _provenance()
    if mp is None:
        return "UNVERIFIABLE"
    try:
        return str(mp.witness_id(label))
    except Exception:
        return "UNVERIFIABLE"


def is_degraded(label: Any) -> bool:
    """True when the label names a recovery/failsafe/synthetic path."""
    mp = _provenance()
    if mp is None:
        return True  # fail closed
    try:
        return bool(mp.is_degraded_source(label))
    except Exception:
        return True


def _formula_matches_migration(formula_version: Any, migration_state: Any) -> bool:
    """
    PACK_C: a curve formula may not price a migrated pool, and vice versa.

    Unknown state or unregistered formula returns False — an unrecognised
    combination is not evidence, and silently trusting it is exactly the
    migration blindness this guard exists to close.
    """
    f = str(formula_version or "").strip().lower()
    m = str(migration_state or "").strip().lower()
    if not f or not m:
        return False
    if m in CURVE_MIGRATION_STATES:
        return f.startswith(CURVE_FORMULA_PREFIXES)
    if m in POOL_MIGRATION_STATES:
        return f.startswith(POOL_FORMULA_PREFIXES)
    return False


def _layer_a_state_derived(conn: sqlite3.Connection, row: Any) -> bool:
    """
    True when this Layer A price plausibly follows from the account state it
    cites. If an EARLIER row carries the SAME account_hash at a DIFFERENT slot
    but a DIFFERENT price, the price moved while the curve did not, so the
    number depends on an input outside the account (typically a SOL/USD rate
    from an indexer). Such a row is an observed mark wearing Layer A's label
    and must not act as the independent witness.

    PACK_C: the comparison uses native_price_sol when present. A USD figure
    legitimately moves with the SOL/USD rate, so only the native price is a
    valid test of state derivation. A row with no native price is tested on
    USD and will fail this check the moment the SOL rate moves — which is the
    correct outcome, since such a row cannot be shown to be state-derived.
    """
    try:
        h = str(row["account_hash"] or "")
        native = _f(_col(row, "native_price_sol"))
        col = "native_price_sol" if native else "derived_price_usd"
        p = native or _f(row["derived_price_usd"])
        if not h or not p:
            return False
        prior = conn.execute(
            f"""
            SELECT {col} FROM peak_onchain_state
            WHERE account_hash=? AND mint_address=? AND context_slot<>?
              AND {col} IS NOT NULL
            ORDER BY observed_at DESC LIMIT 8
            """,
            (h, str(row["mint_address"]), int(row["context_slot"] or 0)),
        ).fetchall()
        for r in prior:
            q = _f(r[0])
            if q and abs(q - p) / max(q, p) > 1e-9:
                return False
        return True
    except Exception:
        return False


def _col(row: Any, name: str) -> Any:
    """Read an optional column from a sqlite3.Row without raising."""
    try:
        return row[name]
    except (IndexError, KeyError, TypeError):
        return None


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS peak_onchain_state (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER NOT NULL,
          mint_address TEXT NOT NULL,
          source_kind TEXT NOT NULL,
          account_address TEXT,
          context_slot INTEGER,
          commitment TEXT,
          account_hash TEXT,
          formula_version TEXT NOT NULL,
          migration_state TEXT NOT NULL,
          raw_token_reserves TEXT,
          raw_quote_reserves TEXT,
          derived_price_usd REAL,
          executable_curve_price_usd REAL,
          observed_at REAL NOT NULL,
          rpc_label TEXT,
          latency_ms REAL,
          witness_id TEXT NOT NULL UNIQUE,
          integrity_status TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_peak_a_pos_time
          ON peak_onchain_state(position_id, observed_at DESC);

        CREATE TABLE IF NOT EXISTS peak_trade_tape (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER,
          mint_address TEXT NOT NULL,
          signature TEXT NOT NULL UNIQUE,
          context_slot INTEGER NOT NULL,
          block_time INTEGER,
          side TEXT,
          raw_sol_amount TEXT,
          raw_token_amount TEXT,
          effective_price_usd REAL,
          source TEXT NOT NULL,
          observed_at REAL NOT NULL,
          arrival_latency_ms REAL,
          witness_id TEXT NOT NULL UNIQUE,
          reconciliation_status TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_peak_b_pos_slot
          ON peak_trade_tape(position_id, context_slot DESC);

        CREATE TABLE IF NOT EXISTS peak_executable_quotes (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER NOT NULL,
          mint_address TEXT NOT NULL,
          raw_amount TEXT NOT NULL,
          quote_out_raw TEXT,
          min_out_raw TEXT,
          effective_price_usd REAL,
          price_impact_pct REAL,
          route TEXT,
          sellable INTEGER NOT NULL DEFAULT 0,
          quote_ts REAL NOT NULL,
          context_slot INTEGER,
          latency_ms REAL,
          provider_identity TEXT,
          witness_id TEXT NOT NULL UNIQUE,
          integrity_status TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_peak_c_pos_time
          ON peak_executable_quotes(position_id, quote_ts DESC);

        CREATE TABLE IF NOT EXISTS peak_truth_candidates (
          id INTEGER PRIMARY KEY AUTOINCREMENT,
          position_id INTEGER NOT NULL,
          mint_address TEXT NOT NULL,
          candidate_ts REAL NOT NULL,
          layer_a_id INTEGER,
          layer_b_id INTEGER,
          layer_c_id INTEGER,
          context_slot INTEGER,
          trusted_price_usd REAL,
          divergence_pct REAL,
          state TEXT NOT NULL,
          reason TEXT NOT NULL,
          evidence_json TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_peak_candidate_pos
          ON peak_truth_candidates(position_id, candidate_ts DESC);
        """
    )
    _ensure_pack_c_columns(conn)


# PACK_C additive columns. Purely additive: no table is dropped, no column is
# removed or retyped, no existing row is rewritten. Rolling back the module
# leaves these columns in place, unread and harmless.
_PACK_C_COLUMNS = {
    "peak_onchain_state": {
        # Native truth is separate from display conversion. The USD figure is a
        # DERIVED DISPLAY VALUE and carries its own rate provenance.
        "native_price_sol": "REAL",
        "usd_conversion_rate": "REAL",
        "usd_rate_source": "TEXT",
        "usd_rate_observed_at": "REAL",
        "pool_program_id": "TEXT",
        "causative_signature": "TEXT",
        "causative_slot": "INTEGER",
    },
    "peak_trade_tape": {
        "native_price_sol": "REAL",
        "raw_sol_delta": "TEXT",
        "raw_token_delta": "TEXT",
        "decimals": "INTEGER",
        "pool_program_id": "TEXT",
    },
    "peak_executable_quotes": {
        "native_price_sol": "REAL",
        "error_class": "TEXT",
        "provider_family": "TEXT",
        "quote_age_sec": "REAL",
        "request_ts": "REAL",
        "causative_slot": "INTEGER",
    },
    "peak_truth_candidates": {
        "causative_slot": "INTEGER",
        "causative_signature": "TEXT",
        "causative_block_time": "REAL",
        "slot_to_layer_a_ms": "REAL",
        "slot_to_layer_b_ms": "REAL",
        "slot_to_layer_c_ms": "REAL",
        "slot_to_candidate_ms": "REAL",
        "native_trusted_price_sol": "REAL",
        "witness_family": "TEXT",
        "quote_family": "TEXT",
    },
}


def _ensure_pack_c_columns(conn: sqlite3.Connection) -> None:
    for table, cols in _PACK_C_COLUMNS.items():
        try:
            have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        except sqlite3.Error:
            continue
        if not have:
            continue
        for name, ddl in cols.items():
            if name not in have:
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")
                except sqlite3.Error:
                    pass


def record_onchain_state(conn: sqlite3.Connection, **payload: Any) -> Optional[int]:
    ensure_schema(conn)
    required = (
        int(payload.get("position_id") or 0) > 0
        and bool(payload.get("mint_address"))
        and int(payload.get("context_slot") or 0) > 0
        and bool(payload.get("account_hash"))
        and bool(payload.get("formula_version"))
    )
    # PACK_C: a curve formula may not price a migrated pool. Applying
    # pump_curve_* to a PumpSwap pool reads zeroed virtual reserves, so the
    # combination is recorded but never promoted to VALID.
    formula_ok = _formula_matches_migration(
        payload.get("formula_version"), payload.get("migration_state")
    )
    native = _f(payload.get("native_price_sol"))
    usd = _f(payload.get("derived_price_usd"))
    status = "VALID" if (required and formula_ok and (native or usd)) else "DIAGNOSTIC_ONLY"
    wid = _witness_id("A", payload)
    conn.execute(
        """
        INSERT OR IGNORE INTO peak_onchain_state(
          position_id,mint_address,source_kind,account_address,context_slot,
          commitment,account_hash,formula_version,migration_state,
          raw_token_reserves,raw_quote_reserves,derived_price_usd,
          executable_curve_price_usd,observed_at,rpc_label,latency_ms,
          witness_id,integrity_status,
          native_price_sol,usd_conversion_rate,usd_rate_source,
          usd_rate_observed_at,pool_program_id,causative_signature,causative_slot
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.get("position_id"), payload.get("mint_address"),
            payload.get("source_kind") or "bonding_curve",
            payload.get("account_address"), payload.get("context_slot"),
            payload.get("commitment") or "confirmed", payload.get("account_hash"),
            payload.get("formula_version") or "unknown",
            payload.get("migration_state") or "unknown",
            str(payload.get("raw_token_reserves") or "") or None,
            str(payload.get("raw_quote_reserves") or "") or None,
            usd,
            _f(payload.get("executable_curve_price_usd")),
            _f(payload.get("observed_at")) or time.time(),
            payload.get("rpc_label"), _f(payload.get("latency_ms")),
            wid, status,
            native,
            _f(payload.get("usd_conversion_rate")),
            payload.get("usd_rate_source"),
            _f(payload.get("usd_rate_observed_at")),
            payload.get("pool_program_id"),
            payload.get("causative_signature"),
            payload.get("causative_slot"),
        ),
    )
    row = conn.execute(
        "SELECT id FROM peak_onchain_state WHERE witness_id=?", (wid,)
    ).fetchone()
    return int(row[0]) if row else None


def record_trade(conn: sqlite3.Connection, **payload: Any) -> Optional[int]:
    ensure_schema(conn)
    sig = str(payload.get("signature") or "").strip()
    slot = int(payload.get("context_slot") or payload.get("slot") or 0)
    # PACK_C: CHAIN_RECONCILED is a claim that raw integer balance deltas were
    # decoded from a real signature at a real slot. A signature string alone is
    # not reconciliation — an unverified or fabricated signature must stay
    # PENDING and can never act as an independent witness.
    sol_delta = str(payload.get("raw_sol_delta") or payload.get("raw_sol_amount") or "").strip()
    tok_delta = str(payload.get("raw_token_delta") or payload.get("raw_token_amount") or "").strip()
    deltas_ok = _pos_int(sol_delta.lstrip("-")) > 0 and _pos_int(tok_delta.lstrip("-")) > 0
    verified = bool(payload.get("chain_verified", False))
    reconciled = bool(
        sig and slot > 0 and payload.get("mint_address") and deltas_ok and verified
    )
    status = "CHAIN_RECONCILED" if reconciled else "PENDING"
    wid = _witness_id("B", payload)
    conn.execute(
        """
        INSERT OR IGNORE INTO peak_trade_tape(
          position_id,mint_address,signature,context_slot,block_time,side,
          raw_sol_amount,raw_token_amount,effective_price_usd,source,
          observed_at,arrival_latency_ms,witness_id,reconciliation_status,
          native_price_sol,raw_sol_delta,raw_token_delta,decimals,pool_program_id
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.get("position_id"), payload.get("mint_address"), sig,
            slot, payload.get("block_time"), payload.get("side"),
            str(payload.get("raw_sol_amount") or "") or None,
            str(payload.get("raw_token_amount") or "") or None,
            _f(payload.get("effective_price_usd")),
            payload.get("source") or "chain",
            _f(payload.get("observed_at")) or time.time(),
            _f(payload.get("arrival_latency_ms")), wid, status,
            _f(payload.get("native_price_sol")),
            sol_delta or None, tok_delta or None,
            payload.get("decimals"), payload.get("pool_program_id"),
        ),
    )
    row = conn.execute(
        "SELECT id FROM peak_trade_tape WHERE witness_id=?", (wid,)
    ).fetchone()
    return int(row[0]) if row else None


def record_executable_quote(conn: sqlite3.Connection, **payload: Any) -> Optional[int]:
    ensure_schema(conn)
    sellable = bool(payload.get("sellable"))
    price = _f(payload.get("effective_price_usd"))
    raw_amount = str(payload.get("raw_amount") or "")
    # PACK_B: "executable" is a claim about a route that can be signed, not a
    # number. A row with no out amount, no minimum-out and no context slot is a
    # price of unknown provenance and may not carry executable authority.
    # Observed 2026-08-06: 12/15 rows stamped VALID with all three fields NULL.
    complete = bool(
        _pos_int(payload.get("quote_out_raw")) > 0
        and _pos_int(payload.get("min_out_raw")) > 0
        and _pos_int(payload.get("context_slot")) > 0
        and str(payload.get("route") or "").strip()
        and str(payload.get("route") or "").strip().lower() != "none"
    )
    status = (
        "VALID"
        if sellable and price and price > 0 and raw_amount and complete
        else "DIAGNOSTIC_ONLY"
    )
    # PACK_C: classify WHY a quote is not usable, and refuse to let a degraded
    # or unidentifiable provider label carry executable authority.
    error_class = str(payload.get("error_class") or "").strip().upper()
    if error_class not in QUOTE_ERROR_CLASSES:
        error_class = classify_quote_error(
            http_status=payload.get("http_status"),
            exception=payload.get("exception"),
            provider_message=payload.get("provider_message"),
            route_present=payload.get("route_present"),
            price_impact_pct=payload.get("price_impact_pct"),
            impact_cap_pct=payload.get("impact_cap_pct"),
            quote_age_sec=payload.get("quote_age_sec"),
            max_age_sec=payload.get("max_age_sec") or MAX_AGE_SEC,
            out_amount_raw=payload.get("quote_out_raw"),
        )
    provider = payload.get("provider_identity") or payload.get("route")
    family = source_family(provider)
    if error_class != "OK" or is_degraded(provider) or family == "UNVERIFIABLE":
        status = "DIAGNOSTIC_ONLY"
    wid = _witness_id("C", payload)
    conn.execute(
        """
        INSERT OR IGNORE INTO peak_executable_quotes(
          position_id,mint_address,raw_amount,quote_out_raw,min_out_raw,
          effective_price_usd,price_impact_pct,route,sellable,quote_ts,
          context_slot,latency_ms,provider_identity,witness_id,integrity_status,
          native_price_sol,error_class,provider_family,quote_age_sec,
          request_ts,causative_slot
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            payload.get("position_id"), payload.get("mint_address"), raw_amount,
            str(payload.get("quote_out_raw") or "") or None,
            str(payload.get("min_out_raw") or "") or None,
            price, _f(payload.get("price_impact_pct")), payload.get("route"),
            1 if sellable else 0, _f(payload.get("quote_ts")) or time.time(),
            payload.get("context_slot"), _f(payload.get("latency_ms")),
            payload.get("provider_identity"), wid, status,
            _f(payload.get("native_price_sol")), error_class, family,
            _f(payload.get("quote_age_sec")), _f(payload.get("request_ts")),
            payload.get("causative_slot"),
        ),
    )
    row = conn.execute(
        "SELECT id FROM peak_executable_quotes WHERE witness_id=?", (wid,)
    ).fetchone()
    return int(row[0]) if row else None


def _latest(conn: sqlite3.Connection, table: str, position_id: int, time_col: str):
    return conn.execute(
        f"SELECT * FROM {table} WHERE position_id=? ORDER BY {time_col} DESC LIMIT 1",
        (int(position_id),),
    ).fetchone()


def evaluate_position(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    mint_address: str,
    entry_price: float,
    threshold_pct: float = 20.0,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    ensure_schema(conn)
    conn.row_factory = sqlite3.Row
    now = float(now or time.time())
    out: Dict[str, Any] = {
        "confirmed": False,
        "state": "OBSERVED_PEAK",
        "reason": "missing_authority_evidence",
        "trusted_peak_price": None,
        "trusted_peak_pct": None,
        "confirmation_source": None,
        "confirmation_ts": None,
        "evidence": {},
    }
    a = _latest(conn, "peak_onchain_state", position_id, "observed_at")
    b = _latest(conn, "peak_trade_tape", position_id, "observed_at")
    c = _latest(conn, "peak_executable_quotes", position_id, "quote_ts")

    valid_a = bool(
        a and a["integrity_status"] == "VALID"
        and now - float(a["observed_at"]) <= MAX_AGE_SEC
        and str(a["mint_address"]) == str(mint_address)
    )
    valid_b = bool(
        b and b["reconciliation_status"] == "CHAIN_RECONCILED"
        and now - float(b["observed_at"]) <= MAX_AGE_SEC
        and str(b["mint_address"]) == str(mint_address)
    )
    valid_c = bool(
        c and c["integrity_status"] == "VALID" and int(c["sellable"] or 0) == 1
        and now - float(c["quote_ts"]) <= MAX_AGE_SEC
        and str(c["mint_address"]) == str(mint_address)
    )
    out["evidence"] = {
        "layer_a_id": int(a["id"]) if a else None,
        "layer_b_id": int(b["id"]) if b else None,
        "layer_c_id": int(c["id"]) if c else None,
        "layer_a_valid": valid_a,
        "layer_b_valid": valid_b,
        "layer_c_valid": valid_c,
    }
    if not valid_c:
        out["state"] = "CORROBORATED_MARK_PEAK" if (valid_a and valid_b) else "OBSERVED_PEAK"
        out["reason"] = "actual_size_executable_quote_required"
        return out
    out["state"] = "EXECUTABLE_PEAK"
    if not (valid_a or valid_b):
        out["reason"] = "independent_onchain_or_trade_witness_required"
        return out

    independent_price = None
    witness_kind = None
    witness_slot = None
    if valid_a:
        # PACK_B: a Layer A row only witnesses the chain if its price follows
        # from the account state it cites.
        if not _layer_a_state_derived(conn, a):
            out["evidence"]["layer_a_valid"] = False
            valid_a = False
            if not valid_b:
                out["reason"] = "layer_a_price_not_state_derived"
                return out
    if valid_a:
        independent_price = _f(a["executable_curve_price_usd"]) or _f(a["derived_price_usd"])
        witness_kind = "A"
        witness_slot = int(a["context_slot"] or 0)
    elif valid_b:
        independent_price = _f(b["effective_price_usd"])
        witness_kind = "B"
        witness_slot = int(b["context_slot"] or 0)

    # PACK_B: the quote and the independent witness must describe the same
    # moment in slot terms. Fresh wall-clock timestamps on stale reads are the
    # laundering path this closes.
    quote_slot = _pos_int(c["context_slot"])
    out["evidence"]["quote_slot"] = quote_slot
    if quote_slot <= 0:
        out["reason"] = "quote_slot_proof_required"
        return out
    if witness_slot and abs(quote_slot - int(witness_slot)) > MAX_WITNESS_SLOT_SKEW:
        out["evidence"]["slot_skew"] = abs(quote_slot - int(witness_slot))
        out["reason"] = "witness_quote_slot_skew_exceeded"
        return out

    executable_price = _f(c["effective_price_usd"])
    if not independent_price or not executable_price:
        out["reason"] = "positive_prices_required"
        return out

    # PACK_C: the independent witness and the executable quote must be
    # DIFFERENT witnesses. Jupiter agreeing with Jupiter is one observation
    # wearing two labels; it corroborates nothing. Independence is a property
    # of the witness, not of the row.
    quote_family = str(_col(c, "provider_family") or source_family(
        c["provider_identity"] or c["route"]))
    witness_label = (
        (_col(a, "rpc_label") or a["source_kind"]) if witness_kind == "A"
        else (b["source"] if b is not None else None)
    )
    witness_family = source_family(witness_label)
    out["evidence"].update({
        "witness_family": witness_family,
        "quote_family": quote_family,
    })
    if (
        witness_family in ("UNVERIFIABLE", "")
        or quote_family in ("UNVERIFIABLE", "")
        or witness_family.startswith("unknown:")
        or quote_family.startswith("unknown:")
    ):
        out["reason"] = "witness_family_unverifiable"
        return out
    if witness_family.startswith("degraded:") or quote_family.startswith("degraded:"):
        out["reason"] = "degraded_source_cannot_witness"
        return out
    if witness_family == quote_family:
        out["reason"] = "correlated_witness_and_quote_family"
        return out

    divergence = abs(independent_price - executable_price) / max(independent_price, executable_price) * 100.0
    out["evidence"].update({
        "independent_witness": witness_kind,
        "context_slot": witness_slot,
        "independent_price": independent_price,
        "executable_price": executable_price,
        "divergence_pct": divergence,
    })
    if divergence > MAX_DIVERGENCE_PCT:
        out["reason"] = "family_divergence_quarantine"
        return out
    trusted = min(independent_price, executable_price)
    # PACK_C: native truth is carried alongside, never replaced by, the USD
    # display figure. Both sides must be native for a native trusted price to
    # exist; a mixed pair would be a unit error, not a price.
    nat_ind = _f(_col(a, "native_price_sol")) if witness_kind == "A" else _f(_col(b, "native_price_sol"))
    nat_exec = _f(_col(c, "native_price_sol"))
    native_trusted = min(nat_ind, nat_exec) if (nat_ind and nat_exec) else None
    out["evidence"]["native_trusted_price_sol"] = native_trusted
    pct = (trusted - float(entry_price)) / float(entry_price) * 100.0
    if pct < float(threshold_pct):
        out["reason"] = "trusted_price_below_runner_threshold"
        return out
    if witness_slot <= 0:
        out["reason"] = "slot_proof_required"
        return out

    previous = conn.execute(
        """
        SELECT context_slot,candidate_ts,trusted_price_usd
        FROM peak_truth_candidates
        WHERE position_id=? AND state='CANDIDATE'
        ORDER BY candidate_ts DESC LIMIT 1
        """,
        (int(position_id),),
    ).fetchone()
    evidence_json = json.dumps(out["evidence"], sort_keys=True)

    # PACK_C: causative-slot telemetry. The chain that must be measurable is
    # causative slot -> Layer A/B write -> Layer C quote -> candidate. Without
    # it there is no evidence about how much of a fast peak is being missed.
    causative_slot = (
        _pos_int(_col(a, "causative_slot")) if witness_kind == "A"
        else _pos_int(b["context_slot"] if b is not None else 0)
    ) or int(witness_slot or 0)
    causative_sig = (
        _col(a, "causative_signature") if witness_kind == "A"
        else (b["signature"] if b is not None else None)
    )
    causative_bt = _f(b["block_time"]) if (witness_kind == "B" and b is not None) else None
    base_ts = causative_bt or (
        _f(a["observed_at"]) - (_f(a["latency_ms"]) or 0.0) / 1000.0
        if witness_kind == "A" and a is not None else None
    )
    def _ms(t: Any) -> Optional[float]:
        v = _f(t)
        return None if (v is None or base_ts is None) else (v - base_ts) * 1000.0

    conn.execute(
        """
        INSERT INTO peak_truth_candidates(
          position_id,mint_address,candidate_ts,layer_a_id,layer_b_id,layer_c_id,
          context_slot,trusted_price_usd,divergence_pct,state,reason,evidence_json,
          causative_slot,causative_signature,causative_block_time,
          slot_to_layer_a_ms,slot_to_layer_b_ms,slot_to_layer_c_ms,
          slot_to_candidate_ms,native_trusted_price_sol,witness_family,quote_family
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            int(position_id), str(mint_address), now,
            out["evidence"]["layer_a_id"], out["evidence"]["layer_b_id"],
            out["evidence"]["layer_c_id"], witness_slot, trusted, divergence,
            "CANDIDATE", "independent_candidate", evidence_json,
            causative_slot or None, causative_sig, causative_bt,
            _ms(a["observed_at"]) if a is not None else None,
            _ms(b["observed_at"]) if b is not None else None,
            _ms(c["quote_ts"]),
            _ms(now),
            native_trusted, witness_family, quote_family,
        ),
    )
    if not previous:
        out["reason"] = "first_independent_candidate"
        return out
    if int(previous[0] or 0) == witness_slot:
        out["reason"] = "second_distinct_slot_required"
        return out
    if now - float(previous[1] or 0.0) < MIN_CONFIRMATION_GAP_SEC:
        out["reason"] = "confirmation_gap_too_short"
        return out

    out.update(
        confirmed=True,
        state="TRUSTED_PEAK",
        reason="two_slot_actual_size_independent_confirmation",
        trusted_peak_price=trusted,
        trusted_peak_pct=pct,
        confirmation_source=f"layer_c_plus_{witness_kind.lower()}",
        confirmation_ts=now,
    )
    return out


__all__ = [
    "classify_quote_error", "source_family", "is_degraded",
    "QUOTE_ERROR_CLASSES", "MARKET_TRUTH_ERROR_CLASSES",
    "authority_enabled", "ensure_schema", "record_onchain_state",
    "record_trade", "record_executable_quote", "evaluate_position",
]
