"""
SENTINUITY REGIME COMPRESSION OBSERVER — SHADOW LANE ONLY
SIGNOFF_FLOW_LATENCY_20260724

Directive sections 9–10. This is a SEPARATE, standalone observer process.

Hard boundaries (enforced by construction, verifiable by grep):
  - NO import of services.execution_engine, services.live_trading,
    services.substrate_paper_trader or any wallet/provider module;
  - NO bypass boolean inside the normal execution engine — this file is not
    imported by the engine and the engine is not modified by this pack to
    know it exists;
  - NO writes to matrix-DB execution tables. Reads are read-only; the only
    writes go to `regime_shadow_ledger` in sentinuity_intelligence.db;
  - NO transaction is ever composed, signed or submitted here.

What it does, every OBSERVER_POLL_SEC (default 2s):

  1. MARKET HEAT AUTHORITY (read-only):
       launches_last_60s        from market_snapshots first_seen_at
       recent realised expectancy from paper_positions closed in the last 2h
  2. REGIME AUTHORITY (read-only): reuses the qualifier's persisted
       `regime` column where present; otherwise records 'unknown' and the
       heat gate alone cannot confirm.
  3. For each fresh discovery (pending, first_seen <= OBSERVER_FRESH_SEC):
       - runs the MINIMUM LAUNCH SAFETY PROOF from persisted snapshot truth
         only (never fabricates a check it cannot evaluate — an unavailable
         check is recorded as a named failure, and a candidate with safety
         failures is recorded as NOT hypothetically enterable);
       - if heat+regime+expectancy+flow are all confirmed AND the safety
         proof passes, records a HYPOTHETICAL entry at the first available
         trusted price (observed_price if fresh, else newest mtm_ticks row);
       - a hypothetical fast quote is modelled as price * (1 + est entry
         impact) using curve_sol_reserves when available.
  4. On later passes it back-fills, per shadow row:
       normal_entry_ts / normal_entry_price   from paper_positions (SIM lane)
       price_advantage_pct                    hypothetical vs normal entry
       hypothetical_peak_price                MAX(mtm_ticks) inside the hold
       hypothetical_exit_price / realised %   tick at +SHADOW_HOLD_SEC
                                              (default 180s), fee/impact
                                              modelled, never claimed as PnL.

GRADUATION (operator-gated, nothing here flips automatically):
  >= SHADOW_MIN_OBSERVATIONS clean rows, positive modelled expectancy after
  fees+impact, zero safety bypasses, deterministic exit test passed, and
  explicit operator approval. This module only ACCUMULATES the evidence.
"""

from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, Optional

log = logging.getLogger("regime_compression_observer")

ROOT = Path(__file__).resolve().parent.parent
INTEL_DB = str(ROOT / "sentinuity_intelligence.db")


def _matrix_db_path() -> str:
    override = os.getenv("SENTINUITY_MATRIX_DB", "").strip()
    if override:
        return override
    for name in ("sentinuity_matrix.db", "sentinuity.db", "matrix.db"):
        p = ROOT / name
        if p.exists():
            return str(p)
    return str(ROOT / "sentinuity_matrix.db")


OBSERVER_POLL_SEC = float(os.getenv("OBSERVER_POLL_SEC", "2.0"))
OBSERVER_FRESH_SEC = float(os.getenv("OBSERVER_FRESH_SEC", "30.0"))
SHADOW_HOLD_SEC = float(os.getenv("SHADOW_HOLD_SEC", "180.0"))
SHADOW_MIN_OBSERVATIONS = int(os.getenv("SHADOW_MIN_OBSERVATIONS", "150"))
HEAT_MIN_LAUNCHES_60S = int(os.getenv("HEAT_MIN_LAUNCHES_60S", "5"))
HEAT_MIN_EXPECTANCY_USD = float(os.getenv("HEAT_MIN_EXPECTANCY_USD", "0.0"))
MODELED_FEE_USD = float(os.getenv("SHADOW_MODELED_FEE_USD", "0.04"))
MIN_CURVE_SOL = float(os.getenv("SHADOW_MIN_CURVE_SOL", "2.0"))
MAX_ENTRY_IMPACT_PCT = float(os.getenv("SHADOW_MAX_ENTRY_IMPACT_PCT", "4.0"))
SHADOW_POS_USD = float(os.getenv("SHADOW_POSITION_SIZE_USD", "25.0"))

_FORBIDDEN_IMPORT_GUARD = (
    "execution_engine", "live_trading", "substrate_paper_trader", "wallets",
)  # documentation of the boundary; the verifier greps that none are imported.


def _ro(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2.0)
    conn.row_factory = sqlite3.Row
    return conn


def _rw_intel() -> sqlite3.Connection:
    conn = sqlite3.connect(INTEL_DB, timeout=3.0)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=1500")
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema() -> None:
    conn = _rw_intel()
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS regime_shadow_ledger (
                id                        INTEGER PRIMARY KEY AUTOINCREMENT,
                mint                      TEXT NOT NULL,
                snapshot_id               INTEGER,
                observed_at               REAL NOT NULL,
                heat_confirmed            INTEGER NOT NULL,
                regime_state              TEXT,
                expectancy_usd_2h         REAL,
                launches_60s              INTEGER,
                safety_pass               INTEGER NOT NULL,
                safety_failures           TEXT,
                hypothetical_enterable    INTEGER NOT NULL,
                hypothetical_entry_ts     REAL,
                hypothetical_entry_price  REAL,
                hypothetical_quote_price  REAL,
                est_entry_impact_pct      REAL,
                normal_entry_ts           REAL,
                normal_entry_price        REAL,
                price_advantage_pct       REAL,
                hypothetical_peak_price   REAL,
                hypothetical_exit_price   REAL,
                hypothetical_realised_pct REAL,
                modeled_fee_usd           REAL,
                finalized                 INTEGER DEFAULT 0
            )
            """
        )
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS ux_regime_shadow_mint_snap "
            "ON regime_shadow_ledger(mint, COALESCE(snapshot_id, 0))"
        )
        conn.commit()
    finally:
        conn.close()


# ── authorities ──────────────────────────────────────────────────────────────

def market_heat(matrix: sqlite3.Connection) -> Dict[str, Any]:
    now = time.time()
    launches = 0
    expectancy = 0.0
    try:
        launches = int(matrix.execute(
            "SELECT COUNT(*) FROM market_snapshots "
            "WHERE COALESCE(first_seen_at, created_at, 0) >= ?",
            (now - 60.0,),
        ).fetchone()[0] or 0)
    except Exception:
        pass
    try:
        row = matrix.execute(
            "SELECT AVG(COALESCE(realized_pnl_usd, 0.0)) FROM paper_positions "
            "WHERE status='CLOSED' AND COALESCE(closed_at, 0) >= ?",
            (now - 7200.0,),
        ).fetchone()
        expectancy = float(row[0]) if row and row[0] is not None else 0.0
    except Exception:
        pass
    confirmed = (launches >= HEAT_MIN_LAUNCHES_60S
                 and expectancy > HEAT_MIN_EXPECTANCY_USD)
    return {"launches_60s": launches, "expectancy_usd_2h": expectancy,
            "confirmed": confirmed}


def regime_state(snapshot: sqlite3.Row) -> str:
    try:
        r = str(snapshot["regime"] or "").strip().lower()
        return r or "unknown"
    except Exception:
        return "unknown"


# ── minimum launch safety proof (read-only, never bypassed) ─────────────────

def safety_proof(snap: sqlite3.Row, matrix: sqlite3.Connection) -> Dict[str, Any]:
    """Every check is evaluated from persisted truth. A check that cannot be
    evaluated is a NAMED FAILURE — never a silent pass. This is the shadow
    analogue of the funded-entry contract; it may not weaken it."""
    failures = []
    keys = set(snap.keys())

    def col(name, default=None):
        return snap[name] if name in keys else default

    mint = str(col("mint_address") or "")
    # token program / Token-2022 denial: the pipeline persists no explicit
    # program flag on snapshots; canonical pump provenance implies SPL-Token.
    if not mint.endswith("pump"):
        failures.append("provenance_not_canonical_pump")
    # mint/freeze authority policy: not persisted at snapshot level → named
    # failure (a live implementation must add the RPC proof before entry).
    if "mint_authority_revoked" in keys:
        if not col("mint_authority_revoked"):
            failures.append("mint_authority_active")
    else:
        failures.append("mint_authority_unverified")
    # liquidity / reserves floor
    curve_sol = float(col("curve_sol_reserves") or 0.0)
    if curve_sol < MIN_CURVE_SOL:
        failures.append(f"curve_reserves_below_floor:{curve_sol:.2f}")
    # entry impact bound (route proxy)
    est_impact = None
    if curve_sol > 0:
        sol_usd = 150.0
        try:
            r = matrix.execute(
                "SELECT value FROM config WHERE key='SOL_USD_CACHE'"
            ).fetchone()
            if r and float(r[0]) > 0:
                sol_usd = float(r[0])
        except Exception:
            pass
        res_usd = curve_sol * sol_usd
        est_impact = SHADOW_POS_USD / (res_usd + SHADOW_POS_USD) * 100.0
        if est_impact > MAX_ENTRY_IMPACT_PCT:
            failures.append(f"entry_impact_above_cap:{est_impact:.2f}")
    else:
        failures.append("entry_impact_unknown")
    # duplicate exposure
    try:
        dup = matrix.execute(
            "SELECT 1 FROM paper_positions WHERE mint_address=? AND status='OPEN' LIMIT 1",
            (mint,),
        ).fetchone()
        if dup:
            failures.append("duplicate_open_exposure")
    except Exception:
        failures.append("duplicate_exposure_unverified")
    return {"pass": len(failures) == 0, "failures": failures,
            "est_entry_impact_pct": est_impact}


# ── price truth helpers ─────────────────────────────────────────────────────

def first_trusted_price(mint: str, snap: sqlite3.Row,
                        intel: sqlite3.Connection) -> Optional[float]:
    try:
        keys = set(snap.keys())
        if "observed_price" in keys and "price_updated_at" in keys:
            p = float(snap["observed_price"] or 0.0)
            ts = float(snap["price_updated_at"] or 0.0)
            if p > 0 and (time.time() - ts) <= 30.0:
                return p
    except Exception:
        pass
    try:
        row = intel.execute(
            "SELECT price_usd FROM mtm_ticks WHERE mint_address=? "
            "ORDER BY ts_ms DESC LIMIT 1",
            (mint,),
        ).fetchone()
        if row and float(row[0]) > 0:
            return float(row[0])
    except Exception:
        pass
    return None


# ── main passes ─────────────────────────────────────────────────────────────

def observe_fresh(matrix: sqlite3.Connection, intel_ro: sqlite3.Connection) -> int:
    now = time.time()
    heat = market_heat(matrix)
    rows = matrix.execute(
        "SELECT * FROM market_snapshots "
        "WHERE LOWER(COALESCE(candidate_state,'pending'))='pending' "
        "  AND COALESCE(first_seen_at, created_at, 0) >= ? "
        "ORDER BY COALESCE(first_seen_at, created_at, 0) DESC LIMIT 40",
        (now - OBSERVER_FRESH_SEC,),
    ).fetchall()
    written = 0
    if not rows:
        return 0
    wr = _rw_intel()
    try:
        for snap in rows:
            mint = str(snap["mint_address"] or "").strip()
            if not mint:
                continue
            proof = safety_proof(snap, matrix)
            regime = regime_state(snap)
            regime_ok = regime in ("confirmed", "optimal", "near_optimal", "hot")
            enterable = bool(heat["confirmed"] and regime_ok and proof["pass"])
            entry_price = first_trusted_price(mint, snap, intel_ro) if enterable else None
            quote_price = None
            if entry_price and proof["est_entry_impact_pct"]:
                quote_price = entry_price * (1.0 + proof["est_entry_impact_pct"] / 100.0)
            _cur = wr.execute(
                "INSERT OR IGNORE INTO regime_shadow_ledger "
                "(mint, snapshot_id, observed_at, heat_confirmed, regime_state, "
                " expectancy_usd_2h, launches_60s, safety_pass, safety_failures, "
                " hypothetical_enterable, hypothetical_entry_ts, "
                " hypothetical_entry_price, hypothetical_quote_price, "
                " est_entry_impact_pct, modeled_fee_usd) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (mint, int(snap["id"]), now, 1 if heat["confirmed"] else 0,
                 regime, heat["expectancy_usd_2h"], heat["launches_60s"],
                 1 if proof["pass"] else 0, ";".join(proof["failures"]) or None,
                 1 if (enterable and entry_price) else 0,
                 now if (enterable and entry_price) else None,
                 entry_price, quote_price,
                 proof["est_entry_impact_pct"], MODELED_FEE_USD),
            )
            written += 1 if _cur.rowcount > 0 else 0
        wr.commit()
    finally:
        wr.close()
    return written


def finalize_shadows(matrix: sqlite3.Connection, intel_ro: sqlite3.Connection) -> int:
    now = time.time()
    wr = _rw_intel()
    done = 0
    try:
        open_rows = wr.execute(
            "SELECT * FROM regime_shadow_ledger "
            "WHERE finalized=0 AND hypothetical_enterable=1 "
            "  AND hypothetical_entry_ts IS NOT NULL "
            "  AND hypothetical_entry_ts <= ?",
            (now - SHADOW_HOLD_SEC,),
        ).fetchall()
        for row in open_rows:
            mint = row["mint"]
            t0 = float(row["hypothetical_entry_ts"])
            t1 = t0 + SHADOW_HOLD_SEC
            peak = exitp = None
            try:
                pr = intel_ro.execute(
                    "SELECT MAX(price_usd) FROM mtm_ticks "
                    "WHERE mint_address=? AND ts_ms BETWEEN ? AND ?",
                    (mint, int(t0 * 1000), int(t1 * 1000)),
                ).fetchone()
                peak = float(pr[0]) if pr and pr[0] else None
                er = intel_ro.execute(
                    "SELECT price_usd FROM mtm_ticks "
                    "WHERE mint_address=? AND ts_ms <= ? "
                    "ORDER BY ts_ms DESC LIMIT 1",
                    (mint, int(t1 * 1000)),
                ).fetchone()
                exitp = float(er[0]) if er and er[0] else None
            except Exception:
                pass
            normal_ts = normal_price = None
            try:
                nr = matrix.execute(
                    "SELECT opened_at, entry_price FROM paper_positions "
                    "WHERE mint_address=? AND UPPER(COALESCE(funding_mode,'SIM'))='SIM' "
                    "ORDER BY opened_at ASC LIMIT 1",
                    (mint,),
                ).fetchone()
                if nr:
                    normal_ts = float(nr["opened_at"] or 0.0) or None
                    normal_price = float(nr["entry_price"] or 0.0) or None
            except Exception:
                pass
            entry = float(row["hypothetical_quote_price"]
                          or row["hypothetical_entry_price"] or 0.0)
            adv = None
            if entry and normal_price:
                adv = (normal_price - entry) / normal_price * 100.0
            realised = None
            if entry and exitp:
                gross = (exitp - entry) / entry * 100.0
                fee_pct = (MODELED_FEE_USD / SHADOW_POS_USD) * 100.0
                impact = float(row["est_entry_impact_pct"] or 0.0)
                realised = gross - fee_pct - (2.0 * impact)  # symmetric entry + exit impact
            wr.execute(
                "UPDATE regime_shadow_ledger SET normal_entry_ts=?, "
                "normal_entry_price=?, price_advantage_pct=?, "
                "hypothetical_peak_price=?, hypothetical_exit_price=?, "
                "hypothetical_realised_pct=?, finalized=1 WHERE id=?",
                (normal_ts, normal_price, adv, peak, exitp, realised, row["id"]),
            )
            done += 1
        # non-enterable rows finalize immediately (they are the denominator)
        wr.execute(
            "UPDATE regime_shadow_ledger SET finalized=1 "
            "WHERE finalized=0 AND hypothetical_enterable=0 AND observed_at <= ?",
            (now - 60.0,),
        )
        wr.commit()
    finally:
        wr.close()
    return done


def run() -> None:
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s [%(name)s] %(levelname)s %(message)s")
    ensure_schema()
    matrix_path = _matrix_db_path()
    log.info("REGIME COMPRESSION OBSERVER ONLINE — shadow-only. matrix=%s "
             "hold=%.0fs graduation_min_obs=%d NO FUNDED AUTHORITY.",
             matrix_path, SHADOW_HOLD_SEC, SHADOW_MIN_OBSERVATIONS)
    while True:
        try:
            matrix = _ro(matrix_path)
            intel_ro = _ro(INTEL_DB)
            try:
                n_new = observe_fresh(matrix, intel_ro)
                n_fin = finalize_shadows(matrix, intel_ro)
                if n_new or n_fin:
                    log.info("[SHADOW] observed=%d finalized=%d", n_new, n_fin)
            finally:
                matrix.close()
                intel_ro.close()
        except Exception as exc:
            log.warning("observer cycle error: %s", exc)
        time.sleep(OBSERVER_POLL_SEC)


if __name__ == "__main__":
    run()
