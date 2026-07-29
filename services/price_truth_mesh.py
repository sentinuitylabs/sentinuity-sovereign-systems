from __future__ import annotations

import logging
import os
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from decimal import Decimal
from pathlib import Path

from services.price_truth_schema import connect, migrate

log = logging.getLogger("price_truth_mesh")
ROOT = Path(__file__).resolve().parent.parent
MATRIX = ROOT / "sentinuity_matrix.db"
INTERVAL = float(os.getenv("PRICE_TRUTH_SHADOW_INTERVAL_SEC", "12"))
MAX_POS = int(os.getenv("PRICE_TRUTH_SHADOW_MAX_POSITIONS", "8"))
PUMP_FEE_BPS = int(os.getenv("PUMP_SHADOW_FEE_BPS", "125"))
PUMP_FEE_SOURCE = "ENV" if os.getenv("PUMP_SHADOW_FEE_BPS") else "SHADOW_DEFAULT_125BPS"
MARK_WORKERS = max(1, int(os.getenv("PRICE_TRUTH_MARK_WORKERS", "3")))
CYCLE_BUDGET_SEC = max(5.0, float(os.getenv("PRICE_TRUTH_CYCLE_BUDGET_SEC", "15")))


def _cols(connection, table):
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _open_positions():
    connection = sqlite3.connect(f"file:{MATRIX}?mode=ro", uri=True, timeout=0.1)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA query_only=ON")
    connection.execute("PRAGMA busy_timeout=50")
    cols = _cols(connection, "paper_positions")
    wanted = [
        name for name in (
            "id", "mint_address", "entry_price", "quantity", "opened_at",
            "funding_mode", "status", "is_open", "position_size_usd",
            "token_decimals", "decimals", "current_price", "updated_at",
        ) if name in cols
    ]
    if not wanted:
        connection.close()
        return []
    where = "status='OPEN'" if "status" in cols else ("is_open=1" if "is_open" in cols else "closed_at IS NULL")
    rows = connection.execute(
        f"SELECT {','.join(wanted)} FROM paper_positions WHERE {where} ORDER BY id DESC LIMIT ?",
        (MAX_POS,),
    ).fetchall()
    connection.close()
    return [dict(row) for row in rows]


def _ensure_decimal_cache(db):
    db.execute(
        """
        CREATE TABLE IF NOT EXISTS token_decimals_cache (
            mint_address TEXT PRIMARY KEY,
            decimals INTEGER NOT NULL,
            source TEXT NOT NULL,
            resolved_at REAL NOT NULL
        )
        """
    )


def _resolve_token_decimals(db, position, mint):
    # Prefer durable position truth, then the persistent cache, and only then RPC.
    for key in ("token_decimals", "decimals"):
        value = position.get(key)
        if value is not None:
            try:
                dec = int(value)
                if 0 <= dec <= 18:
                    db.execute(
                        "INSERT OR REPLACE INTO token_decimals_cache "
                        "(mint_address,decimals,source,resolved_at) VALUES(?,?,?,?)",
                        (mint, dec, f"paper_positions.{key}", time.time()),
                    )
                    return dec
            except Exception:
                pass

    row = db.execute(
        "SELECT decimals FROM token_decimals_cache WHERE mint_address=?", (mint,)
    ).fetchone()
    if row is not None:
        dec = int(row[0])
        if 0 <= dec <= 18:
            return dec

    from services.live_trading import _get_token_decimals
    dec = int(_get_token_decimals(mint))
    if not 0 <= dec <= 18:
        raise RuntimeError(f"token_decimals_invalid:{dec}")
    db.execute(
        "INSERT OR REPLACE INTO token_decimals_cache "
        "(mint_address,decimals,source,resolved_at) VALUES(?,?,?,?)",
        (mint, dec, "chain_rpc", time.time()),
    )
    db.commit()
    return dec


def _mark_position(position_id, price, source, observed_at):
    if not price or not position_id:
        return
    con = sqlite3.connect(MATRIX, timeout=2.0)
    try:
        con.execute("PRAGMA busy_timeout=2000")
        cols = _cols(con, "paper_positions")
        sets, args = [], []
        for col, value in (
            ("current_price", float(price)),
            ("updated_at", float(observed_at)),
            ("mark_timestamp", float(observed_at)),
            ("mark_source", str(source or "price_truth_mesh")),
            ("mark_status", "FRESH"),
        ):
            if col in cols:
                sets.append(f"{col}=?")
                args.append(value)
        if sets:
            args.append(position_id)
            con.execute(f"UPDATE paper_positions SET {','.join(sets)} WHERE id=?", args)
            con.commit()
    finally:
        con.close()


def _quote_position(position, decimals):
    from services.price_router import get_live_liquidation_price, get_reference_price_details
    mint = str(position.get("mint_address") or "")
    quantity = Decimal(str(position.get("quantity") or 0))
    entry = float(position.get("entry_price") or 0)
    opened_at = float(position.get("opened_at") or 0)
    raw_quantity = int(quantity * (Decimal(10) ** decimals))
    reference = get_reference_price_details(mint, opened_at, entry)
    executable = get_live_liquidation_price(mint, float(quantity), entry, opened_at)
    return raw_quantity, reference, executable


def _pct(price, entry):
    return ((price - entry) / entry * 100.0) if price and entry else None


def _spread_pct(a, b):
    return abs(a - b) / b * 100.0 if a and b and b > 0 else None


def _observe_pump_shadow(db, position, mint, raw_quantity, decimals, entry, reference_price, jupiter_price):
    from services.live_trading import _get_cached_sol_price
    from services.pump_curve_math import simulate_sell_exact, unit_price_usd_from_sol_quote
    from services.pump_curve_reader import read_curve

    read = read_curve(mint)
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
            sol_usd = float(_get_cached_sol_price() or 0.0)
            pump_price = unit_price_usd_from_sol_quote(quote, raw_quantity, decimals, sol_usd)
            values["pump_price"] = pump_price
            values["pump_pnl"] = _pct(pump_price, entry)
            if quote.ok and pump_price:
                state_name = "PUMP_RESERVE_BOUNDED" if quote.reserve_bounded else "PUMP_SHADOW_VALID"
                reason = quote.reason
            else:
                state_name = "PUMP_NO_EXECUTABLE"
                reason = quote.reason

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
            f"position:{position.get('id')}", position.get("id"), mint, str(raw_quantity), decimals, time.time(),
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
    started = time.time()
    rows = _open_positions()
    wrote = 0
    pump_wrote = 0
    errors = []
    db = connect()
    _ensure_decimal_cache(db)
    legacy_sql = (
        "INSERT INTO price_truth_snapshots "
        "(decision_id,position_id,funding_mode,mint_address,raw_quantity,decimals,observed_at,"
        "reference_price,reference_source,reference_age_sec,executable_price,executable_source,"
        "executable_age_sec,executable_can_exit,executable_warning,executable_pnl_pct,"
        "reference_pnl_pct,divergence_pct,quorum_state,shadow_only) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)"
    )

    prepared = []
    for position in rows:
        try:
            mint = str(position.get("mint_address") or "")
            quantity = Decimal(str(position.get("quantity") or 0))
            entry = float(position.get("entry_price") or 0)
            if not mint or quantity <= 0 or entry <= 0:
                continue
            decimals = _resolve_token_decimals(db, position, mint)
            prepared.append((position, decimals))
        except Exception as exc:
            err = f"id={position.get('id')}:{type(exc).__name__}:{exc}"
            errors.append(err)
            log.warning("shadow metadata failed %s", err)

    futures = {}
    pool = ThreadPoolExecutor(max_workers=min(MARK_WORKERS, max(1, len(prepared))))
    try:
        for position, decimals in prepared:
            futures[pool.submit(_quote_position, position, decimals)] = (position, decimals)
        deadline = started + CYCLE_BUDGET_SEC
        pending = set(futures)
        while pending and time.time() < deadline:
            timeout = max(0.05, deadline - time.time())
            completed = []
            try:
                for future in as_completed(pending, timeout=timeout):
                    completed.append(future)
                    position, decimals = futures[future]
                    try:
                        raw_quantity, reference, executable = future.result()
                        mint = str(position.get("mint_address") or "")
                        entry = float(position.get("entry_price") or 0)
                        observed_at = time.time()
                        reference_price = float(reference.get("price") or 0)
                        executable_price = float(executable.get("price") or 0)
                        divergence = _spread_pct(reference_price, executable_price)
                        quorum = "EXECUTABLE_SINGLE_SOURCE" if executable_price > 0 else "NO_EXECUTABLE"
                        if executable_price > 0 and reference_price > 0:
                            quorum = "DIVERGENCE_ALARM" if divergence and divergence > 10 else "SHADOW_AGREEMENT"
                        db.execute(
                            legacy_sql,
                            (
                                f"position:{position.get('id')}", position.get("id"),
                                str(position.get("funding_mode") or "SIM"), mint,
                                str(raw_quantity), decimals, observed_at, reference_price or None,
                                reference.get("actual_source") or reference.get("source"), reference.get("age_sec"),
                                executable_price or None, executable.get("source"), executable.get("age_sec"),
                                1 if executable.get("can_execute_exit") else 0, executable.get("warning"),
                                _pct(executable_price, entry), _pct(reference_price, entry), divergence, quorum,
                            ),
                        )
                        db.commit()  # mark truth survives even if optional Pump shadow fails later
                        wrote += 1
                        mark_price = executable_price or reference_price
                        _mark_position(position.get("id"), mark_price, executable.get("source") or reference.get("source"), observed_at)

                        # Pump shadow is diagnostic. Never let it block canonical marking.
                        if time.time() < deadline:
                            try:
                                _observe_pump_shadow(
                                    db, position, mint, raw_quantity, decimals, entry,
                                    reference_price, executable_price,
                                )
                                db.commit()
                                pump_wrote += 1
                            except Exception as exc:
                                errors.append(f"pump_id={position.get('id')}:{type(exc).__name__}:{exc}")
                    except Exception as exc:
                        err = f"id={position.get('id')}:{type(exc).__name__}:{exc}"
                        errors.append(err)
                        log.warning("shadow position failed %s", err)
                pending.difference_update(completed)
            except TimeoutError:
                break
        for future in pending:
            future.cancel()
        if pending:
            errors.append(f"cycle_budget_exceeded:pending={len(pending)}")
    finally:
        pool.shutdown(wait=False, cancel_futures=True)

    cycle_ms = (time.time() - started) * 1000
    error = " | ".join(errors[-5:])
    db.execute(
        "UPDATE price_truth_health SET last_cycle_at=?,positions_seen=?,"
        "snapshots_written=snapshots_written+?,last_error=?,cycle_ms=? WHERE id=1",
        (time.time(), len(rows), wrote, error, cycle_ms),
    )
    db.commit()
    db.close()
    return {
        "positions": len(rows),
        "written": wrote,
        "pump_written": pump_wrote,
        "error": error,
        "cycle_ms": round(cycle_ms, 1),
        "authority": "MARK_FIRST_PUMP_OPTIONAL",
    }


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    migrate()
    log.info(
        "PRICE_TRUTH_SHADOW_START interval=%.1f authority=NONE pump_native=SHADOW fee_bps=%d fee_source=%s",
        INTERVAL, PUMP_FEE_BPS, PUMP_FEE_SOURCE,
    )
    while True:
        try:
            log.info("PRICE_TRUTH_SHADOW_CYCLE %s", observe_once())
        except Exception as exc:
            log.exception("PRICE_TRUTH_SHADOW_CYCLE_FAIL %s", exc)
        time.sleep(max(5.0, INTERVAL))


if __name__ == "__main__":
    main()
