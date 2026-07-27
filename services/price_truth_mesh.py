from __future__ import annotations

import logging
import os
import sqlite3
import time
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
    from services.live_trading import _get_token_decimals
    from services.price_router import get_live_liquidation_price, get_reference_price_details

    started = time.time()
    rows = _open_positions()
    wrote = 0
    pump_wrote = 0
    error = ""
    db = connect()
    legacy_sql = (
        "INSERT INTO price_truth_snapshots "
        "(decision_id,position_id,funding_mode,mint_address,raw_quantity,decimals,observed_at,"
        "reference_price,reference_source,reference_age_sec,executable_price,executable_source,"
        "executable_age_sec,executable_can_exit,executable_warning,executable_pnl_pct,"
        "reference_pnl_pct,divergence_pct,quorum_state,shadow_only) "
        "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1)"
    )

    for position in rows:
        try:
            mint = str(position.get("mint_address") or "")
            quantity = Decimal(str(position.get("quantity") or 0))
            entry = float(position.get("entry_price") or 0)
            if not mint or quantity <= 0 or entry <= 0:
                continue
            decimals = int(_get_token_decimals(mint))
            raw_quantity = int(quantity * (Decimal(10) ** decimals))
            reference = get_reference_price_details(mint, float(position.get("opened_at") or 0), entry)
            executable = get_live_liquidation_price(mint, float(quantity), entry, float(position.get("opened_at") or 0))
            reference_price = float(reference.get("price") or 0)
            executable_price = float(executable.get("price") or 0)
            divergence = _spread_pct(reference_price, executable_price)
            quorum = "EXECUTABLE_SINGLE_SOURCE" if executable_price > 0 else "NO_EXECUTABLE"
            if executable_price > 0 and reference_price > 0:
                quorum = "DIVERGENCE_ALARM" if divergence and divergence > 10 else "SHADOW_AGREEMENT"
            db.execute(
                legacy_sql,
                (
                    f"position:{position.get('id')}", position.get("id"), str(position.get("funding_mode") or "SIM"),
                    mint, str(raw_quantity), decimals, time.time(), reference_price or None,
                    reference.get("actual_source") or reference.get("source"), reference.get("age_sec"),
                    executable_price or None, executable.get("source"), executable.get("age_sec"),
                    1 if executable.get("can_execute_exit") else 0, executable.get("warning"),
                    _pct(executable_price, entry), _pct(reference_price, entry), divergence, quorum,
                ),
            )
            wrote += 1
            _observe_pump_shadow(
                db, position, mint, raw_quantity, decimals, entry,
                reference_price, executable_price,
            )
            pump_wrote += 1
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            log.warning("shadow position failed id=%s %s", position.get("id"), error)

    db.execute(
        "UPDATE price_truth_health SET last_cycle_at=?,positions_seen=?,"
        "snapshots_written=snapshots_written+?,last_error=?,cycle_ms=? WHERE id=1",
        (time.time(), len(rows), wrote, error, (time.time() - started) * 1000),
    )
    db.commit()
    db.close()
    return {
        "positions": len(rows),
        "written": wrote,
        "pump_written": pump_wrote,
        "error": error,
        "cycle_ms": round((time.time() - started) * 1000, 1),
        "authority": "NONE",
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
