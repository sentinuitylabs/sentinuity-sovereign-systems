from __future__ import annotations

import logging
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from pathlib import Path

from services.price_truth_schema import connect, migrate

log = logging.getLogger("price_truth_mesh")
ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "sentinuity_matrix.db"
INTERVAL = max(1.0, float(os.getenv("PRICE_TRUTH_SHADOW_INTERVAL_SEC", "12")))
# Runtime 2026-08-09 measured Layer-C p95 refresh at 57.5s against a 45s
# executable freshness contract.  The old loop slept INTERVAL *after* provider
# work, so a 30-40s cycle plus 12s sleep guaranteed stale evidence.  While
# positions are open, cap the delay between cycle starts without weakening the
# quote-age contract.  Idle cadence still honours INTERVAL.
ACTIVE_MAX_INTERVAL = max(0.75, float(os.getenv("PRICE_TRUTH_ACTIVE_MAX_INTERVAL_SEC", "1.5")))
MAX_POS = max(1, int(os.getenv("PRICE_TRUTH_SHADOW_MAX_POSITIONS", "32")))
WORKERS = max(1, int(os.getenv("PRICE_TRUTH_SHADOW_WORKERS", "12")))
# CURVE_VERDICT_20260816 -- fee-plane unification.
# The shadow plane defaulted to 125 bps while the authoritative router used
# PUMP_EXEC_WITNESS_FEE_BPS (100). Two planes quoting the same curve at the
# same slot could therefore never agree byte-for-byte, which made any
# shadow/authoritative disagreement uninformative. An explicit
# PUMP_SHADOW_FEE_BPS override is still honoured verbatim; only the *default*
# now comes from the shared source of truth in pump_curve_math.
from services.pump_curve_math import curve_fee_bps as _shared_curve_fee_bps

if os.getenv("PUMP_SHADOW_FEE_BPS", "").strip():
    PUMP_FEE_BPS = int(float(os.getenv("PUMP_SHADOW_FEE_BPS")))
    PUMP_FEE_SOURCE = "ENV_SHADOW_OVERRIDE"
else:
    PUMP_FEE_BPS = _shared_curve_fee_bps()
    PUMP_FEE_SOURCE = "SHARED_CURVE_FEE_BPS"

# Divergence at which a CURVE-DERIVED executable mark loses authority.
#
# _spread_pct() divides by the executable price, so a mark that has collapsed
# to near zero produces an astronomically large percentage. Position 5788 sat
# at 2.22e8%; the healthy curve marks in the same cycle sat at 2.1% and 19.5%.
# 5000% (50x) is four orders of magnitude clear of both sides.
#
# This threshold is applied ONLY to curve-derived marks. A real Jupiter -100%
# rug quote keeps full authority and must still be able to execute an exit.
CURVE_DISPUTE_DIVERGENCE_PCT = max(
    100.0, float(os.getenv("CURVE_DISPUTE_DIVERGENCE_PCT", "5000"))
)
CURVE_SOURCE_PREFIXES = ("pump-curve", "pump_curve")


def _cols(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _open_positions():
    # EDGE_AUDIT_20260815 — COVERAGE LOST TO LOCK CONTENTION.
    # This is a read-only reader of the SHARED trading DB, which also carries
    # Council, GitHub, UI and retention writes. A 50ms busy tolerance against
    # the qualifier's 50-row batches loses the read, and because the caller at
    # :181 has no local guard, ONE lock discarded the whole coverage cycle at
    # :416 and left every open position unquoted. That is the most likely
    # mechanism behind the NO_COVERAGE closes in the 2026-08-15 window.
    #
    # Waiting briefly for the writer is strictly better than producing no
    # executable truth at all. The downstream quote-age contract is unchanged,
    # so a quote that ages out is still refused -- this cannot admit a stale
    # mark, only prevent the absence of a fresh one.
    _busy_ms = int(float(os.getenv("PRICE_TRUTH_DB_BUSY_MS", "2000")))
    connection = sqlite3.connect(f"file:{MATRIX}?mode=ro", uri=True,
                                 timeout=max(0.25, _busy_ms / 1000.0))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute(f"PRAGMA busy_timeout={_busy_ms}")
    cols = _cols(connection, "paper_positions")
    wanted = [
        name for name in (
            "id", "mint_address", "entry_price", "quantity", "opened_at",
            "funding_mode", "status", "is_open", "position_size_usd",
        ) if name in cols
    ]
    if not wanted:
        connection.close()
        return []
    where = "status='OPEN'" if "status" in cols else ("is_open=1" if "is_open" in cols else "closed_at IS NULL")
    # Cover every currently open position up to a deliberately generous safety
    # ceiling. The historical default of 8 could silently starve older positions
    # of Layer-C truth when operator config allowed more paper positions.
    rows = connection.execute(
        f"SELECT {','.join(wanted)} FROM paper_positions WHERE {where} ORDER BY id DESC LIMIT ?",
        (MAX_POS,),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def _pct(price, entry):
    return ((price - entry) / entry * 100.0) if price and entry else None


def _spread_pct(a, b):
    return abs(a - b) / b * 100.0 if a and b and b > 0 else None


def _observe_pump_shadow(db, position, mint, raw_quantity, decimals, entry, reference_price, jupiter_price, *, prefetched_read=None, read_completed_at=None, prefetched_sol_usd=None):
    from services.live_trading import _get_cached_sol_price
    from services.pump_curve_math import (
        simulate_sell_exact,
        unit_price_usd_from_sol_quote,
        diagnostic_price_usd,
    )
    from services.pump_curve_reader import read_curve

    read = prefetched_read if prefetched_read is not None else read_curve(mint)
    observed_at = float(read_completed_at or time.time())
    state_name = "PUMP_READ_FAILED"
    reason = read.reason
    values = {
        "account_len": None,
        "complete": None,
        "vtok": None,
        "vquote": None,
        "rtok": None,
        "rquote": None,
        "theoretical": None,
        "payable": None,
        "fee": None,
        "net": None,
        "marginal": None,
        "impact_bps": None,
        "coverage_bps": None,
        "reserve_bounded": None,
        "pump_price": None,
        "pump_pnl": None,
        "verdict": None,
        "realised_impact_bps": None,
        "shortfall": None,
    }

    if read.ok and read.state is not None:
        curve = read.state
        values.update({
            "account_len": curve.account_len,
            "complete": 1 if curve.complete else 0,
            "vtok": str(curve.virtual_token_reserves),
            "vquote": str(curve.virtual_quote_reserves),
            "rtok": str(curve.real_token_reserves),
            "rquote": str(curve.real_quote_reserves),
        })
        if curve.complete:
            state_name = "CURVE_COMPLETE_HISTORICAL_ONLY"
            reason = "PUMPSWAP_HANDOFF_REQUIRED"
        else:
            quote = simulate_sell_exact(curve, raw_quantity, PUMP_FEE_BPS)
            values.update({
                "theoretical": str(quote.theoretical_gross_quote_raw),
                "payable": str(quote.payable_gross_quote_raw),
                "fee": str(quote.fee_quote_raw),
                "net": str(quote.net_quote_raw),
                "marginal": str(quote.marginal_quote_raw),
                "impact_bps": quote.curve_impact_bps,
                "coverage_bps": quote.real_reserve_coverage_bps,
                "reserve_bounded": 1 if quote.reserve_bounded else 0,
            })
            # Never perform provider I/O while the price-truth DB has an open
            # write transaction. observe_once prefetches SOL/USD once per cycle.
            sol_usd = float(prefetched_sol_usd or 0.0)
            # CURVE_VERDICT_20260816: unit_price_usd_from_sol_quote() now
            # returns None for every non-executable verdict, so a refused curve
            # can no longer write a number into a column named
            # pump_executable_price_usd. The raw integers (theoretical /
            # payable / net / coverage_bps / reserve_bounded) are still
            # persisted above and remain a lossless record of what happened --
            # the evidence survives, the authority does not.
            pump_price = unit_price_usd_from_sol_quote(quote, raw_quantity, decimals, sol_usd)
            values.update({
                "verdict": quote.verdict,
                "realised_impact_bps": quote.realised_impact_bps,
                "shortfall": str(quote.reserve_shortfall_raw),
            })
            values["pump_price"] = pump_price
            values["pump_pnl"] = _pct(pump_price, entry)
            if quote.executable and pump_price:
                state_name = "PUMP_SHADOW_VALID"
                reason = quote.reason
            else:
                state_name = quote.verdict
                _diag = diagnostic_price_usd(quote, raw_quantity, decimals, sol_usd)
                reason = (
                    f"{quote.reason} coverage_bps={quote.real_reserve_coverage_bps} "
                    f"shortfall_raw={quote.reserve_shortfall_raw} "
                    f"realised_impact_bps={quote.realised_impact_bps} "
                    f"reported_impact_bps={quote.curve_impact_bps} "
                    f"diagnostic_price_usd={_diag!r}"
                )

    pump_vs_jup = _spread_pct(values["pump_price"], jupiter_price)
    ref_vs_pump = _spread_pct(reference_price, values["pump_price"])

    db.execute(
        """
        INSERT INTO pump_curve_shadow (
          decision_id,position_id,mint_address,raw_quantity,token_decimals,observed_at,
          curve_address,context_slot,account_hash,rpc_label,rpc_latency_ms,account_len,
          complete,virtual_token_reserves,virtual_quote_reserves,real_token_reserves,
          real_quote_reserves,fee_bps,fee_source,theoretical_gross_quote_raw,
          payable_gross_quote_raw,fee_quote_raw,net_quote_raw,marginal_quote_raw,
          curve_impact_bps,real_reserve_coverage_bps,reserve_bounded,
          pump_executable_price_usd,pump_executable_pnl_pct,jupiter_executable_price_usd,
          pump_vs_jupiter_divergence_pct,reference_price_usd,
          reference_vs_pump_divergence_pct,shadow_state,shadow_reason,shadow_only
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)
        """,
        (
            f"position:{position.get('id')}", position.get("id"), mint, str(raw_quantity), decimals, observed_at,
            read.curve_address, read.context_slot, read.account_hash, read.rpc_label, read.latency_ms,
            values["account_len"], values["complete"], values["vtok"], values["vquote"],
            values["rtok"], values["rquote"], PUMP_FEE_BPS, PUMP_FEE_SOURCE,
            values["theoretical"], values["payable"], values["fee"], values["net"],
            values["marginal"], values["impact_bps"], values["coverage_bps"],
            values["reserve_bounded"], values["pump_price"], values["pump_pnl"],
            jupiter_price or None, pump_vs_jup, reference_price or None, ref_vs_pump,
            state_name, reason,
        ),
    )
    return state_name


def observe_once():
    from services.live_trading import _get_token_decimals, _get_cached_sol_price
    from services.price_router import (get_live_liquidation_price,
                                       get_reference_price_details,
                                       PRIORITY_PROTECTION,
                                       t3_sol_usd)

    def _t3_liquidation_protection(_mint, _qty, _entry, _opened, _decimals):
        return get_live_liquidation_price(
            _mint, _qty, _entry, _opened,
            decimals=_decimals, priority=PRIORITY_PROTECTION,
        )
    from services.pump_curve_reader import read_curve

    started = time.time()
    # EDGE_AUDIT_20260815: a contended read must degrade to a named, countable
    # empty cycle -- never take down the coverage pass with an untyped
    # exception 240 lines away.
    try:
        rows = _open_positions()
    except Exception as _pos_exc:
        log.error("PRICE_TRUTH_POSITION_READ_FAILED %s: %s — coverage cycle "
                  "produced NO executable marks this pass",
                  type(_pos_exc).__name__, _pos_exc)
        rows = []
    wrote = 0
    pump_wrote = 0
    error = ""

    # Acquire all network-backed evidence before opening the truth DB writer.
    # Across-position concurrency keeps the refresh period compatible with the
    # 45s PAPER executable-age contract under the normal 3-position workload.
    def _acquire(position):
        mint = str(position.get("mint_address") or "")
        quantity = Decimal(str(position.get("quantity") or 0))
        entry = float(position.get("entry_price") or 0)
        if not mint or quantity <= 0 or entry <= 0:
            return {"position": position, "skip": True}
        decimals = int(_get_token_decimals(mint))
        raw_quantity = int(quantity * (Decimal(10) ** decimals))
        reference = get_reference_price_details(mint, float(position.get("opened_at") or 0), entry)

        def _curve_fetch():
            _r = read_curve(mint)
            return _r, time.time()

        with ThreadPoolExecutor(max_workers=2, thread_name_prefix="price-truth-pair") as _pair:
            # T3: decimals were resolved five lines above. Passing them down
            # removes one redundant token-decimals RPC per position per cycle.
            # This loop is the open-position mark producer, so it declares
            # PRIORITY_PROTECTION: it is never shed and never waits on a slot.
            _exec_future = _pair.submit(
                _t3_liquidation_protection, mint, float(quantity), entry,
                float(position.get("opened_at") or 0), decimals,
            )
            _curve_future = _pair.submit(_curve_fetch)
            executable = _exec_future.result()
            curve_read, curve_completed_at = _curve_future.result()
        return {
            "position": position, "mint": mint, "quantity": quantity, "entry": entry,
            "decimals": decimals, "raw_quantity": raw_quantity, "reference": reference,
            "executable": executable, "curve_read": curve_read,
            "curve_completed_at": curve_completed_at, "skip": False,
        }

    acquired = []
    if rows:
        _workers = min(max(1, WORKERS), len(rows))
        with ThreadPoolExecutor(max_workers=_workers, thread_name_prefix="price-truth-pos") as _pool:
            _futures = [_pool.submit(_acquire, p) for p in rows]
            for p, fut in zip(rows, _futures):
                try:
                    acquired.append(fut.result())
                except Exception as exc:
                    acquired.append({"position": p, "skip": True, "error": f"{type(exc).__name__}:{exc}"})

    # SOL/USD is only needed to convert open-position Pump curve reads.  The old
    # path fetched it even with zero positions and bypassed T3's memoiser, so an
    # idle mesh still consumed provider budget and generated timeouts.
    _cycle_sol_usd = 0.0
    if rows:
        try:
            _cycle_sol_usd = float(t3_sol_usd(_get_cached_sol_price) or 0.0)
        except Exception:
            _cycle_sol_usd = 0.0

    db = connect()
    legacy_sql = (
        "INSERT INTO price_truth_snapshots "
        "(decision_id,position_id,funding_mode,mint_address,raw_quantity,decimals,observed_at,"
        "reference_price,reference_source,reference_age_sec,executable_price,executable_source,"
        "executable_age_sec,executable_can_exit,executable_warning,executable_pnl_pct,"
        "reference_pnl_pct,divergence_pct,quorum_state,shadow_only) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)"
    )

    for bundle in acquired:
        position = bundle.get("position") or {}
        if bundle.get("skip"):
            if bundle.get("error"):
                error = str(bundle.get("error"))
                log.warning("shadow acquisition failed id=%s %s", position.get("id"), error)
            continue
        try:
            mint = bundle["mint"]
            entry = bundle["entry"]
            decimals = bundle["decimals"]
            raw_quantity = bundle["raw_quantity"]
            reference = bundle["reference"]
            executable = bundle["executable"]
            _curve_read = bundle["curve_read"]
            _curve_completed_at = bundle["curve_completed_at"]

            reference_price = float(reference.get("price") or 0)
            executable_price = float(executable.get("price") or 0)
            divergence = _spread_pct(reference_price, executable_price)

            # ── CURVE_VERDICT_20260816: quorum that actually withdraws ───────
            # DIVERGENCE_ALARM was computed, persisted, and read by nobody.
            # It is now load-bearing, but ONLY against curve-derived marks.
            #
            # Why the asymmetry: a pre-graduation Pump curve has a hard price
            # floor -- virtual_quote_reserves never falls below genesis -- so a
            # curve mark that has collapsed by orders of magnitude against an
            # independent reference cannot be a rug and must be a defect. A
            # Jupiter mark has no such floor: a genuine -100% rug is a real,
            # executable market fact and MUST retain authority so the exit
            # still fires. Disputing it here would be the more dangerous bug.
            _exec_source = str(
                executable.get("provider_identity") or executable.get("source") or ""
            ).strip().lower()
            _is_curve_mark = any(
                _exec_source.startswith(p) for p in CURVE_SOURCE_PREFIXES
            )
            _curve_disputed = bool(
                _is_curve_mark
                and executable_price > 0
                and reference_price > 0
                and divergence is not None
                and divergence > CURVE_DISPUTE_DIVERGENCE_PCT
            )

            quorum = "EXECUTABLE_SINGLE_SOURCE" if executable_price > 0 else "NO_EXECUTABLE"
            if executable_price > 0 and reference_price > 0:
                quorum = "DIVERGENCE_ALARM" if divergence and divergence > 10 else "SHADOW_AGREEMENT"
            if _curve_disputed:
                quorum = "CURVE_DISPUTED_DIAGNOSTIC"

            _exec_can_exit = bool(executable.get("can_execute_exit"))
            _exec_warning = executable.get("warning")
            if _curve_disputed:
                _exec_can_exit = False
                _exec_warning = (
                    f"CURVE_AUTHORITY_WITHDRAWN_DIVERGENCE "
                    f"divergence_pct={divergence:.1f} "
                    f"threshold_pct={CURVE_DISPUTE_DIVERGENCE_PCT:.1f} "
                    f"source={_exec_source}"
                )
                log.error(
                    "CURVE_AUTHORITY_WITHDRAWN id=%s mint=%s source=%s "
                    "executable=%r reference=%r divergence=%.1f%% > %.1f%% -- "
                    "mark retained as evidence, executable authority refused",
                    position.get("id"), mint[:12], _exec_source,
                    executable_price, reference_price, divergence or 0.0,
                    CURVE_DISPUTE_DIVERGENCE_PCT,
                )
            db.execute(
                legacy_sql,
                (
                    f"position:{position.get('id')}", position.get("id"), str(position.get("funding_mode") or "SIM"),
                    mint, str(raw_quantity), decimals, time.time(), reference_price or None,
                    reference.get("actual_source") or reference.get("source"), reference.get("age_sec"),
                    executable_price or None, executable.get("source"), executable.get("age_sec"),
                    1 if _exec_can_exit else 0, _exec_warning,
                    _pct(executable_price, entry), _pct(reference_price, entry), divergence, quorum,
                ),
            )
            wrote += 1
            _observe_pump_shadow(
                db, position, mint, raw_quantity, decimals, entry,
                reference_price, executable_price,
                prefetched_read=_curve_read,
                read_completed_at=_curve_completed_at,
                prefetched_sol_usd=_cycle_sol_usd,
            )
            pump_wrote += 1

            try:
                from services import peak_truth as _pt
                _pt.ensure_schema(db)
                _curve_row = db.execute(
                    """
                    SELECT curve_address,context_slot,account_hash,complete,
                           virtual_token_reserves,virtual_quote_reserves,
                           pump_executable_price_usd,observed_at,rpc_label,
                           rpc_latency_ms,shadow_state
                    FROM pump_curve_shadow
                    WHERE position_id=? ORDER BY observed_at DESC LIMIT 1
                    """,
                    (position.get("id"),),
                ).fetchone()
                if _curve_row:
                    _pt.record_onchain_state(
                        db,
                        position_id=position.get("id"), mint_address=mint,
                        source_kind="bonding_curve", account_address=_curve_row[0],
                        context_slot=_curve_row[1], commitment="confirmed",
                        account_hash=_curve_row[2], formula_version="pump_curve_v1_cp",
                        migration_state=("complete" if int(_curve_row[3] or 0) else "curve"),
                        raw_token_reserves=_curve_row[4], raw_quote_reserves=_curve_row[5],
                        derived_price_usd=_curve_row[6], executable_curve_price_usd=_curve_row[6],
                        observed_at=_curve_row[7], rpc_label=_curve_row[8], latency_ms=_curve_row[9],
                    )
                _route_plan_json = str(executable.get("route_plan_json") or "")
                _quote_ts = float(executable.get("response_ts") or time.time())
                _exec_impact_pct = executable.get("price_impact_pct")
                if _exec_impact_pct is None and _curve_row and _curve_row[6]:
                    try:
                        _pool_px = float(_curve_row[6] or 0.0)
                        if _pool_px > 0 and executable_price > 0:
                            _exec_impact_pct = max(0.0, (_pool_px - executable_price) / _pool_px * 100.0)
                    except Exception:
                        _exec_impact_pct = None

                # Unknown router impact is never sellable.  A derived A↔C spread
                # remains useful diagnostic evidence but cannot manufacture the
                # router's missing execution-impact field.
                _router_impact_known = executable.get("price_impact_pct") is not None
                # CURVE_VERDICT_20260816: a disputed curve mark is persisted as
                # evidence but must not be sellable and must not be VALID.
                # error_class != "OK" makes record_executable_quote() stamp the
                # row DIAGNOSTIC_ONLY, which peak_truth.evaluate_position()
                # already refuses as Layer-C authority.
                _quote_error_class = str(
                    executable.get("error_class")
                    or ("IMPACT_UNKNOWN" if not _router_impact_known else "")
                )
                if _curve_disputed:
                    _quote_error_class = "CURVE_DISPUTED"
                _pt.record_executable_quote(
                    db, position_id=position.get("id"), mint_address=mint,
                    raw_amount=str(executable.get("raw_amount") or raw_quantity),
                    quote_out_raw=str(executable.get("quote_out_raw") or ""),
                    min_out_raw=str(executable.get("min_out_raw") or ""),
                    effective_price_usd=executable_price or None,
                    price_impact_pct=_exec_impact_pct, route=_route_plan_json,
                    sellable=bool(_exec_can_exit and _router_impact_known),
                    quote_ts=_quote_ts, context_slot=int(executable.get("context_slot") or 0),
                    latency_ms=float(executable.get("latency_ms") or 0.0),
                    provider_identity=str(executable.get("provider_identity") or executable.get("source") or "unknown"),
                    request_ts=float(executable.get("request_ts") or 0.0),
                    quote_age_sec=max(0.0, time.time() - _quote_ts),
                    error_class=_quote_error_class,
                    route_present=bool(_route_plan_json),
                )

                _truth = _pt.evaluate_position(
                    db, position_id=int(position.get("id") or 0), mint_address=mint, entry_price=entry,
                    threshold_pct=float(os.getenv("MODE3_TRUSTED_RUNNER_THRESHOLD_PCT", "20")), now=time.time(),
                )
                if bool(_truth.get("confirmed")) and float(_truth.get("trusted_peak_price") or 0.0) > 0:
                    try:
                        from services import mode3_peak_continuity as _m3pc
                        _m3pc.record_qualified_peak(
                            int(position.get("id") or 0), mint, entry, float(_truth["trusted_peak_price"]),
                            "onchain_quote", now=float(_truth.get("confirmation_ts") or time.time()),
                            anti_outlier_ok=True,
                        )
                    except Exception as _bridge_exc:
                        log.warning("trusted peak continuity bridge failed id=%s %s", position.get("id"), _bridge_exc)
            except Exception as _pt_exc:
                log.warning("peak truth shadow capture failed id=%s %s", position.get("id"), _pt_exc)

            # Commit each internally complete position bundle immediately. No
            # provider I/O occurs while this write transaction is open.
            db.commit()
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            try:
                db.rollback()
            except Exception:
                pass
            log.warning("shadow position failed id=%s %s", position.get("id"), error)

    db.execute(
        "UPDATE price_truth_health SET last_cycle_at=?,positions_seen=?,"
        "snapshots_written=snapshots_written+?,last_error=?,cycle_ms=? WHERE id=1",
        (time.time(), len(rows), wrote, error, (time.time() - started) * 1000),
    )
    db.commit()
    db.close()
    return {
        "positions": len(rows), "written": wrote, "pump_written": pump_wrote,
        "error": error, "cycle_ms": round((time.time() - started) * 1000, 1),
        "authority": "NONE", "workers": min(max(1, WORKERS), max(1, len(rows))),
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    migrate()
    log.info(
        "PRICE_TRUTH_SHADOW_START interval=%.1f authority=NONE pump_native=SHADOW fee_bps=%d fee_source=%s",
        INTERVAL, PUMP_FEE_BPS, PUMP_FEE_SOURCE,
    )
    while True:
        _cycle_started = time.monotonic()
        _result = None
        try:
            _result = observe_once()
            try:
                from services.price_router import t3_stats as _t3s
                log.info("PRICE_TRUTH_SHADOW_CYCLE %s provider_economy=%s",
                         _result, _t3s())
            except Exception:
                log.info("PRICE_TRUTH_SHADOW_CYCLE %s", _result)
        except Exception as exc:
            log.exception("PRICE_TRUTH_SHADOW_CYCLE_FAIL %s", exc)
        # Target a cycle-start cadence. Do not add a fixed 12s dead period on
        # top of a slow provider cycle. With open positions the safety cap is
        # deliberately shorter than PAPER_EXECUTABLE_QUOTE_MAX_AGE_SEC; if the
        # provider itself takes longer, telemetry exposes that rather than
        # making the freshness threshold decorative.
        _positions = int((_result or {}).get("positions") or 0)
        _target = min(INTERVAL, ACTIVE_MAX_INTERVAL) if _positions > 0 else INTERVAL
        _elapsed = max(0.0, time.monotonic() - _cycle_started)
        time.sleep(max(0.25, _target - _elapsed))


if __name__ == "__main__":
    main()
