"""Canonical close-truth writer for Sentinuity paper/live position closes.

This module is intentionally fail-open for the close itself: evidence-writing
errors are logged and returned as False, but never prevent a position from
closing. It records observed market truth separately from credited/modelled PnL.
"""
from __future__ import annotations

import logging
import math
import time
from typing import Any, Mapping

log = logging.getLogger(__name__)

_ADDITIVE_COLUMNS = {
    "raw_pnl_usd_preclamp": "REAL",
    "raw_pnl_pct_preclamp": "REAL",
    "raw_realized_pnl_usd": "REAL",
    "raw_realized_pnl_pct": "REAL",
    "credited_realized_pnl_usd": "REAL",
    "accounting_credit_usd": "REAL",
    "observed_exit_price": "REAL",
    "observed_exit_at": "REAL",
    "fill_model": "TEXT",
    "pnl_integrity_status": "TEXT",
    "quote_source": "TEXT",
    "quote_age": "REAL",
    "estimated_executable_pnl_usd": "REAL",
}


def _num(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _get(row: Mapping[str, Any] | Any, key: str, default: Any = None) -> Any:
    try:
        if hasattr(row, "get"):
            return row.get(key, default)
        return row[key]
    except Exception:
        return default


def ensure_close_truth_columns(conn) -> None:
    """Add evidence columns without modifying existing values or consumers."""
    existing = {str(r[1]) for r in conn.execute("PRAGMA table_info(paper_positions)")}
    for name, sql_type in _ADDITIVE_COLUMNS.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {name} {sql_type}")


def _pnl_for_price(entry_price: float, position_size_usd: float,
                   exit_price: float | None, fee_usd: float) -> float | None:
    if entry_price <= 0 or position_size_usd < 0 or exit_price is None or exit_price <= 0:
        return None
    pct = (exit_price - entry_price) / entry_price
    return position_size_usd * pct - max(0.0, fee_usd)


def record_canonical_close_truth(
    conn,
    *,
    position_id: int,
    position: Mapping[str, Any] | Any,
    final_exit_price: float,
    credited_pnl_usd: float,
    exit_reason: str,
    closed_at: float | None = None,
    is_real_position: bool = False,
    force_scratch: bool = False,
    synthetic_stop_floor: bool = False,
    total_fee_usd: float = 0.0,
    chain_source: str | None = None,
    quote_source: str | None = None,
    quote_age: float | None = None,
) -> bool:
    """Write observed and credited truth for every canonical close.

    Observed truth is captured from the pre-update position snapshot. We never
    substitute credited PnL when no observed mark exists; absence remains NULL
    with ``NO_MARKET_TRUTH`` so coverage cannot be fabricated.
    """
    try:
        ensure_close_truth_columns(conn)

        # ACCOUNTING_TRUTH_SPLIT_20260805:
        # Preserve the honest pre-clamp close result before any synthetic paper
        # credit can overwrite or masquerade as raw/executable truth.
        _db_truth = conn.execute(
            """
            SELECT raw_pnl_pct_preclamp, raw_pnl_usd_preclamp,
                   live_exec_can_exit, live_exec_source, live_exec_updated_at
            FROM paper_positions WHERE id=?
            """,
            (int(position_id),),
        ).fetchone()
        _db_raw_pct = _num(_db_truth[0]) if _db_truth else None
        _db_raw_usd = _num(_db_truth[1]) if _db_truth else None
        _db_can_exit = bool(int(_db_truth[2] or 0)) if _db_truth else False
        _db_exec_source = str(_db_truth[3] or "") if _db_truth else ""

        entry_price = _num(_get(position, "entry_price")) or 0.0
        position_size_usd = _num(_get(position, "position_size_usd")) or 0.0
        # CLOSE_TRUTH_OBSERVED_FIX_20260803 -- OBSERVED EXIT PRICE.
        # Previously this was unconditionally position["last_price"]. When the
        # final mark was a contaminated outlier, that inflated value became
        # "market truth" and produced positive raw PnL on closes whose actual
        # execution was negative (runtime positions 3423 and 3429).
        #
        # For a real-mark close the canonical exit price IS the observed exit.
        # last_price is a fallback only for synthetic fills, and the peak is
        # never eligible as observed truth.
        _ct_exit_reason = str(_get(position, "exit_reason", "") or "")
        _ct_final = _num(final_exit_price)
        _ct_last = _num(_get(position, "last_price"))
        _ct_peak = (_num(_get(position, "highest_price_seen"))
                    or _num(_get(position, "peak_price")))
        _ct_synthetic = any(
            m in _ct_exit_reason.upper()
            for m in ("MODELLED_FLOOR", "CAPPED_STOP_FLOOR", "ASSUMED_STOP")
        )

        if _ct_synthetic:
            observed_price = _ct_last
            # If the last mark IS the peak it is not independent evidence.
            if (observed_price is not None and _ct_peak
                    and abs(observed_price - _ct_peak) <= abs(_ct_peak) * 1e-9):
                observed_price = None
        else:
            observed_price = _ct_final

        if observed_price is None or observed_price <= 0:
            observed_price = None

        final_price = _num(final_exit_price)
        credited = _num(credited_pnl_usd)
        when = _num(closed_at) or time.time()

        if is_real_position:
            observed_price = final_price
            raw_pnl = credited
            executable_pnl = credited
            fill_model = "ACTUAL_FILL"
            integrity = "CHAIN_FILL_TRUTH"
            unresolved = False
        else:
            raw_pnl = _pnl_for_price(
                entry_price, position_size_usd, observed_price, total_fee_usd
            )
            # The hard-stop path stamps the honest raw percentage before close.
            # Re-derive USD from stake because legacy close-truth rows may have
            # already collapsed raw_pnl_usd_preclamp onto credited PnL.
            if _db_raw_pct is not None and position_size_usd > 0:
                raw_pnl = position_size_usd * _db_raw_pct / 100.0
            elif _db_raw_usd is not None:
                raw_pnl = _db_raw_usd
            # SIGN-CONSISTENCY CONTRACT (CLOSE_TRUTH_OBSERVED_FIX_20260803):
            # a negative final execution can never yield positive raw market
            # PnL. Final execution is authoritative because it is the price the
            # close actually used; on conflict, recompute from it and flag.
            _ct_fe = _num(_get(position, "final_exec_pct"))
            _ct_sign_fixed = False
            if raw_pnl is not None and _ct_fe is not None:
                if (_ct_fe < 0 and raw_pnl > 0) or (_ct_fe > 0 and raw_pnl < 0):
                    if position_size_usd:
                        raw_pnl = position_size_usd * _ct_fe / 100.0
                        observed_price = _ct_final
                        _ct_sign_fixed = True
                    else:
                        raw_pnl = None
                    try:
                        import logging as _ct_log
                        _ct_log.getLogger("EXEC_ENGINE").warning(
                            "[PNL_SIGN_CONTRADICTION] pos=%s final_exec=%.2f%% "
                            "mark_derived_raw=%s -> recomputed_from_final_exec=%s",
                            position_id, _ct_fe,
                            "contaminated", raw_pnl,
                        )
                    except Exception:
                        pass
            executable_pnl = _pnl_for_price(
                entry_price, position_size_usd, final_price, total_fee_usd
            )
            unresolved = raw_pnl is None

            if force_scratch:
                fill_model = "OBSERVED_MARK" if observed_price is not None else "UNKNOWN"
                integrity = "SCRATCH_CLOSE" if raw_pnl is not None else "NO_MARKET_TRUTH"
            elif synthetic_stop_floor:
                fill_model = "CAPPED_STOP_FLOOR"
                integrity = "CAPPED_STOP_FLOOR" if raw_pnl is not None else "NO_MARKET_TRUTH"
            elif raw_pnl is None:
                fill_model = "UNKNOWN"
                integrity = "NO_MARKET_TRUTH"
            elif credited is not None and abs(credited - raw_pnl) > 1e-9:
                fill_model = "MODELLED_FLOOR" if final_price and observed_price and final_price > observed_price else "EXECUTABLE_QUOTE"
                integrity = "MODELLED_FILL_UPLIFT" if credited > raw_pnl else "MODELLED_FILL_DIFFERENCE"
            else:
                fill_model = "OBSERVED_MARK"
                integrity = "OBSERVED_MARK_TRUTH"
            # CLOSE_TRUTH_OBSERVED_FIX_20260803: a contradiction is never
            # silently accepted; it is stamped so audits can find every row
            # whose mark stream disagreed with its own execution.
            if _ct_sign_fixed:
                integrity = "PNL_SIGN_CONTRADICTION"

        raw_pct = (
            (raw_pnl / position_size_usd) * 100.0
            if raw_pnl is not None and position_size_usd > 0 else None
        )

        # ENGINE_MARK is observed market evidence, not an executable quote.
        _quote_label = str(quote_source or chain_source or "")
        _route_backed = bool(
            is_real_position
            or (
                _db_can_exit
                and _quote_label
                and _quote_label.upper() not in {"ENGINE_MARK", "CURRENT_EVALUATOR_MARK"}
                and "NO_EXECUTABLE_ROUTE" not in _quote_label.upper()
            )
        )
        if not _route_backed:
            executable_pnl = None

        # A synthetic paper credit is bookkeeping only. It is never raw,
        # executable, or trusted market truth.
        trusted_pct = raw_pct if not synthetic_stop_floor else None
        trusted_usd = raw_pnl if not synthetic_stop_floor else None

        conn.execute(
            """
            UPDATE paper_positions SET
                raw_pnl_usd_preclamp=?,
                raw_pnl_pct_preclamp=?,
                raw_realized_pnl_usd=?,
                raw_realized_pnl_pct=?,
                credited_realized_pnl_usd=?,
                accounting_credit_usd=?,
                observed_exit_price=?,
                observed_exit_at=?,
                fill_model=?,
                pnl_integrity_status=?,
                quote_source=?,
                quote_age=?,
                estimated_executable_pnl_usd=?,
                trusted_realized_pnl_pct=?,
                trusted_realized_pnl_usd=?,
                live_exec_can_exit=CASE WHEN ? THEN live_exec_can_exit ELSE 0 END
            WHERE id=?
            """,
            (
                raw_pnl,
                raw_pct,
                raw_pnl,
                raw_pct,
                credited,
                credited,
                observed_price,
                when,
                fill_model,
                integrity,
                _quote_label[:64] or None,
                _num(quote_age),
                executable_pnl,
                trusted_pct,
                trusted_usd,
                1 if _route_backed else 0,
                int(position_id),
            ),
        )

        # Reuse the existing additive provenance ledger. It never changes the
        # canonical realised_pnl_usd column and safely ignores missing schema.
        try:
            from services.fill_provenance import record_close
            credited_pct = (
                (credited / position_size_usd) * 100.0
                if credited is not None and position_size_usd > 0 else None
            )
            record_close(
                conn=conn,
                position_id=int(position_id),
                ts_utc=when,
                fill_model=fill_model,
                observed_exit_price=observed_price,
                modelled_exit_price=final_price,
                raw_realized_pnl_usd=raw_pnl,
                recorded_pnl_usd=credited,
                raw_pnl_pct=raw_pct,
                recorded_pnl_pct=credited_pct,
                cap_applied=(raw_pnl is not None and credited is not None and abs(raw_pnl - credited) > 1e-9),
                cap_reason=integrity,
                quote_source=quote_source,
                chain_source=chain_source,
                unresolved=unresolved,
                exit_reason=str(exit_reason or ""),
                ab_arm=_get(position, "ab_arm"),
            )
        except Exception as exc:
            log.debug("fill provenance mirror skipped pos=%s: %s", position_id, exc)

        return True
    except Exception as exc:
        log.warning("canonical close truth write failed pos=%s: %s", position_id, exc)
        return False
