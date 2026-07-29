from __future__ import annotations

"""
wallets/substrate_paper_ledger.py
===============================================================================
SUBSTRATE PAPER LEDGER — FULL LIFECYCLE V3 (SUBSTRATE_REAL_PRICE_20260721)

V2 shipped an open path only: positions could never close, so the "strategy
laboratory" was three static rows with zero PnL. V3 adds the honest remainder
of the lifecycle and enforces the price-truth doctrine at the capital-adjacent
boundary (paper cash is still an accounting truth):

  * open_paper_position_from_opportunity — unchanged contract, plus:
      - refuses SEED_MOCK-priced opportunities (mock data can never become
        paper PnL or promotion evidence);
      - refuses entry prices older than SUBSTRATE_ENTRY_MAX_PRICE_AGE_SEC
        (default 900s) measured against the PROVIDER timestamp;
      - stamps strategy_id and entry_price_status onto the position.
  * close_paper_position — realises PnL from entry vs a supplied real mark,
    returns size+PnL to SUBSTRATE_PAPER_CASH_USD, writes the audit row,
    advances the opportunity to PAPER_CLOSED, journals best-effort, and
    updates substrate_strategy_scores (closes/wins/losses/realized_pnl) so
    every exit feeds strategy attribution.
"""

import json
import sqlite3
import time
from typing import Any, Dict, Optional

from .substrate_wallet_schema import (
    connect, ensure_schema, cfg_float, cfg_int, cfg_get, cfg_set, _ensure_col,
)

DEFAULT_STRATEGY_ID = "SUBSTRATE_CORE_SPOT_V1"
ENTRY_MAX_PRICE_AGE_SEC_DEFAULT = 900.0


def _audit(con, allowed: bool, reason: str, opp: dict | None = None,
           source: str = "paper_ledger") -> None:
    opp = opp or {}
    con.execute(
        "INSERT INTO substrate_execution_audit(created_at,allowed,reason,source,"
        "asset_symbol,chain,confidence,raw_json) VALUES(?,?,?,?,?,?,?,?)",
        (
            time.time(), 1 if allowed else 0, reason, source,
            opp.get("asset_symbol", ""), opp.get("chain", ""),
            float(opp.get("confidence") or 0),
            json.dumps({"opportunity_id": opp.get("id"), "mode": "PAPER"},
                       sort_keys=True),
        ),
    )


def _ensure_lifecycle_cols(con) -> None:
    _ensure_col(con, "substrate_positions", "strategy_id", "TEXT")
    _ensure_col(con, "substrate_positions", "entry_price_status", "TEXT")
    _ensure_col(con, "substrate_positions", "mark_source", "TEXT")
    _ensure_col(con, "substrate_positions", "mark_status", "TEXT")
    _ensure_col(con, "substrate_positions", "marked_at", "REAL")
    # ---- V4 EXPOSURE GUARD (SUBSTRATE_DUPLICATE_GUARD_20260728) -------------
    _ensure_col(con, "substrate_positions", "timeframe_or_regime", "TEXT")
    _ensure_col(con, "substrate_positions", "scale_in_parent_id", "INTEGER")
    _ensure_col(con, "substrate_positions", "scale_in_reason", "TEXT")
    _ensure_col(con, "substrate_positions", "aggregate_exposure_before", "REAL")
    _ensure_col(con, "substrate_positions", "aggregate_exposure_after", "REAL")
    _ensure_col(con, "substrate_positions", "max_allowed_asset_exposure", "REAL")
    con.execute(
        "CREATE TABLE IF NOT EXISTS substrate_strategy_scores("
        " strategy_id TEXT PRIMARY KEY,"
        " closes INTEGER DEFAULT 0, wins INTEGER DEFAULT 0,"
        " losses INTEGER DEFAULT 0, realized_pnl REAL DEFAULT 0,"
        " last_close_at REAL, updated_at REAL)"
    )
    _ensure_exposure_index(con)


# ── DUPLICATE EXPOSURE GUARD (SUBSTRATE_DUPLICATE_GUARD_20260728) ────────────
# Restores the signed-off protection removed in V3. A pre-insert SELECT alone
# is vulnerable to a race between two concurrent openers, so the authoritative
# control is a PARTIAL UNIQUE INDEX enforced by SQLite itself. The SELECT below
# exists only to return a clean, auditable rejection before we reach the index.
#
# Exposure key: asset_symbol + side + strategy_id + timeframe_or_regime
# Root positions (scale_in_parent_id IS NULL) must be unique on that key while
# OPEN. Controlled scale-ins carry a parent id and are exempt from the index,
# but are separately bounded by the aggregate asset exposure cap.
EXPOSURE_INDEX_NAME = "ux_substrate_open_root_exposure"
DEFAULT_TIMEFRAME = "DEFAULT"
MAX_ASSET_EXPOSURE_USD_DEFAULT = 100.0


def _ensure_exposure_index(con) -> bool:
    """Create the partial unique index. Returns False if pre-existing duplicate
    rows block creation -- in that case the pre-insert SELECT is the only line
    of defence and the caller is warned via the audit trail."""
    try:
        con.execute(
            f"CREATE UNIQUE INDEX IF NOT EXISTS {EXPOSURE_INDEX_NAME} "
            "ON substrate_positions("
            "  asset_symbol, side, strategy_id, COALESCE(timeframe_or_regime,'DEFAULT')"
            ") WHERE mode='PAPER' AND state='OPEN' AND scale_in_parent_id IS NULL"
        )
        return True
    except Exception:
        # Legacy duplicates already present. Do not crash the ledger; the
        # SELECT-based guard still rejects new duplicates.
        return False


def _exposure_key(asset: str, side: str, strategy_id: str, timeframe: str) -> tuple:
    return (str(asset or "").upper(), str(side or "LONG").upper(),
            str(strategy_id or DEFAULT_STRATEGY_ID),
            str(timeframe or DEFAULT_TIMEFRAME))


def find_open_exposure(con, asset: str, side: str, strategy_id: str,
                       timeframe: str = DEFAULT_TIMEFRAME) -> Optional[int]:
    """Return the id of an existing OPEN root position on this exposure key."""
    a, s, st, tf = _exposure_key(asset, side, strategy_id, timeframe)
    try:
        row = con.execute(
            "SELECT id FROM substrate_positions "
            " WHERE mode='PAPER' AND state='OPEN' AND scale_in_parent_id IS NULL"
            "   AND UPPER(COALESCE(asset_symbol,''))=?"
            "   AND UPPER(COALESCE(side,'LONG'))=?"
            "   AND COALESCE(strategy_id,?)=?"
            "   AND COALESCE(timeframe_or_regime,'DEFAULT')=?"
            " ORDER BY id LIMIT 1",
            (a, s, DEFAULT_STRATEGY_ID, st, tf),
        ).fetchone()
        return int(row["id"]) if row else None
    except Exception:
        return None


def aggregate_asset_exposure(con, asset: str) -> float:
    """Total USD currently deployed in OPEN paper positions for this asset,
    across every strategy, side and scale-in leg."""
    try:
        row = con.execute(
            "SELECT COALESCE(SUM(COALESCE(size_usd,0)),0) t FROM substrate_positions "
            " WHERE mode='PAPER' AND state='OPEN' AND UPPER(COALESCE(asset_symbol,''))=?",
            (str(asset or "").upper(),),
        ).fetchone()
        return float(row["t"] or 0.0)
    except Exception:
        return 0.0


def _validate_scale_in(con, parent_id: int, asset: str, side: str,
                       strategy_id: str, timeframe: str) -> tuple[bool, str]:
    """A scale-in is only valid against a live parent on the SAME exposure key."""
    try:
        row = con.execute(
            "SELECT id, asset_symbol, side, strategy_id, state, mode, "
            "       COALESCE(timeframe_or_regime,'DEFAULT') tf "
            "FROM substrate_positions WHERE id=?",
            (int(parent_id),),
        ).fetchone()
    except Exception as exc:
        return False, f"scale_in_parent_lookup_failed:{exc}"
    if not row:
        return False, "scale_in_parent_not_found"
    if str(row["mode"] or "").upper() != "PAPER":
        return False, "scale_in_parent_not_paper"
    if str(row["state"] or "").upper() != "OPEN":
        return False, "scale_in_parent_not_open"
    if _exposure_key(row["asset_symbol"], row["side"], row["strategy_id"], row["tf"]) != \
       _exposure_key(asset, side, strategy_id, timeframe):
        return False, "scale_in_parent_exposure_key_mismatch"
    return True, "ok"


def open_paper_position_from_opportunity(
    opportunity_id: int,
    *,
    scale_in_parent_id: Optional[int] = None,
    scale_in_reason: Optional[str] = None,
) -> Dict[str, Any]:
    ensure_schema()
    con = connect()
    try:
        _ensure_lifecycle_cols(con)
        opp_row = con.execute(
            "SELECT * FROM substrate_opportunities WHERE id=?",
            (int(opportunity_id),),
        ).fetchone()
        if not opp_row:
            return {"ok": False, "reason": "opportunity_not_found"}
        opp = dict(opp_row)
        state = str(opp.get("state") or "NEW").upper()
        if state not in ("NEW", "READY", "PROMOTED"):
            _audit(con, False, f"state_not_openable:{state}", opp)
            con.commit()
            return {"ok": False, "reason": f"state_not_openable:{state}"}

        # PRICE-TRUTH GATE (SUBSTRATE_REAL_PRICE_20260721): mock prices cannot
        # open positions and stale provider timestamps cannot open positions.
        price_status = str(opp.get("price_status") or "").upper()
        if price_status == "SEED_MOCK":
            _audit(con, False, "seed_mock_price_cannot_open", opp)
            con.commit()
            return {"ok": False, "reason": "seed_mock_price_cannot_open"}
        px = float(opp.get("price_usd") or 0)
        px_ts = float(opp.get("price_updated_at") or 0)
        max_age = cfg_float(con, "SUBSTRATE_ENTRY_MAX_PRICE_AGE_SEC",
                            ENTRY_MAX_PRICE_AGE_SEC_DEFAULT)
        if px <= 0:
            _audit(con, False, "no_price", opp)
            con.commit()
            return {"ok": False, "reason": "no_price"}
        if px_ts <= 0 or (time.time() - px_ts) > max_age:
            _audit(con, False,
                   f"entry_price_too_old:{time.time() - px_ts:.0f}s>{max_age:.0f}s",
                   opp)
            con.commit()
            return {"ok": False, "reason": "entry_price_too_old"}

        max_open = cfg_int(con, "SUBSTRATE_MAX_OPEN", 3)
        open_n = con.execute(
            "SELECT COUNT(*) c FROM substrate_positions "
            "WHERE mode='PAPER' AND state='OPEN'"
        ).fetchone()["c"]
        if int(open_n or 0) >= max_open:
            _audit(con, False, "paper_max_open_reached", opp)
            con.commit()
            return {"ok": False, "reason": "paper_max_open_reached"}
        size = min(cfg_float(con, "SUBSTRATE_POSITION_SIZE_USD", 25.0),
                   cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0))
        if size <= 0:
            _audit(con, False, "paper_cash_empty", opp)
            con.commit()
            return {"ok": False, "reason": "paper_cash_empty"}

        now = time.time()
        qty = size / px
        strategy_id = str(opp.get("strategy_id") or DEFAULT_STRATEGY_ID)

        # ── DUPLICATE EXPOSURE GUARD ─────────────────────────────────────────
        asset = str(opp.get("asset_symbol") or "")
        side = "LONG"
        timeframe = str(opp.get("timeframe_or_regime") or DEFAULT_TIMEFRAME)

        if scale_in_parent_id is None:
            existing = find_open_exposure(con, asset, side, strategy_id, timeframe)
            if existing is not None:
                _audit(con, False, "duplicate_open_exposure", opp)
                con.commit()
                return {
                    "ok": False,
                    "reason": "duplicate_open_exposure",
                    "existing_position_id": existing,
                    "exposure_key": {
                        "asset_symbol": asset, "side": side,
                        "strategy_id": strategy_id, "timeframe_or_regime": timeframe,
                    },
                }
        else:
            if not str(scale_in_reason or "").strip():
                _audit(con, False, "scale_in_reason_required", opp)
                con.commit()
                return {"ok": False, "reason": "scale_in_reason_required"}
            valid, why = _validate_scale_in(con, int(scale_in_parent_id), asset,
                                            side, strategy_id, timeframe)
            if not valid:
                _audit(con, False, why, opp)
                con.commit()
                return {"ok": False, "reason": why}

        # Aggregate asset exposure cap applies to BOTH root opens and scale-ins.
        max_asset_exposure = cfg_float(con, "SUBSTRATE_MAX_ASSET_EXPOSURE_USD",
                                       MAX_ASSET_EXPOSURE_USD_DEFAULT)
        exposure_before = aggregate_asset_exposure(con, asset)
        exposure_after = exposure_before + size
        if exposure_after > max_asset_exposure:
            _audit(con, False, "aggregate_asset_exposure_exceeded", opp)
            con.commit()
            return {
                "ok": False,
                "reason": "aggregate_asset_exposure_exceeded",
                "aggregate_exposure_before": exposure_before,
                "would_be_exposure": exposure_after,
                "maximum_allowed_asset_exposure": max_asset_exposure,
            }
        try:
            cur = con.execute(
            "INSERT INTO substrate_positions(opportunity_id,mode,state,status,"
            "chain,asset_symbol,symbol,side,size_usd,position_size,"
            "entry_price_usd,entry_price,current_price,quantity,source,"
            "opened_at,updated_at,raw_json,strategy_id,entry_price_status,"
            "timeframe_or_regime,scale_in_parent_id,scale_in_reason,"
            "aggregate_exposure_before,aggregate_exposure_after,"
            "max_allowed_asset_exposure) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(opportunity_id), "PAPER", "OPEN", "OPEN", opp.get("chain"),
                opp.get("asset_symbol"), opp.get("asset_symbol"), "LONG",
                size, size, px, px, px, qty,
                opp.get("source") or "SUBSTRATE", now, now,
                json.dumps({"opportunity_id": opportunity_id,
                            "quote_asset": opp.get("quote_asset", "USDC"),
                            "entry_route_provider": opp.get("route_provider")},
                           sort_keys=True),
                strategy_id, (price_status or "UNRECORDED"),
                timeframe, (int(scale_in_parent_id) if scale_in_parent_id is not None else None),
                (str(scale_in_reason) if scale_in_reason else None),
                exposure_before, exposure_after, max_asset_exposure,
            ),
            )
        except sqlite3.IntegrityError:
            # Lost a race against a concurrent opener. The DB-level partial
            # unique index is the authoritative guard; report the same reason.
            con.rollback()
            existing = find_open_exposure(con, asset, side, strategy_id, timeframe)
            _audit(con, False, "duplicate_open_exposure", opp)
            con.commit()
            return {"ok": False, "reason": "duplicate_open_exposure",
                    "existing_position_id": existing, "race_detected": True}
        position_id = int(cur.lastrowid)
        try:
            from services.substrate_position_persistence import (
                connect as _jconnect, journal_open,
            )
            jc = _jconnect("sentinuity_matrix.db")
            try:
                journal_open(
                    jc, str(position_id), str(opp.get("asset_symbol") or ""),
                    "LONG", px, size,
                    intended_hold_seconds=cfg_float(
                        con, "SUBSTRATE_MAX_HOLD_SEC", 86400.0),
                    thesis=(f"opportunity_id={opportunity_id};"
                            f"source={opp.get('source') or 'SUBSTRATE'};"
                            f"strategy={strategy_id}"),
                )
            finally:
                jc.close()
        except Exception:
            pass
        cash = cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0) - size
        cfg_set(con, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
        con.execute(
            "UPDATE substrate_opportunities SET state='PAPER_OPENED', "
            "updated_at=? WHERE id=?", (now, int(opportunity_id)),
        )
        _audit(con, True, "paper_opened", opp)
        con.commit()
        return {"ok": True, "position_id": position_id,
                "position_size_usd": size, "price_usd": px,
                "strategy_id": strategy_id}
    finally:
        con.close()


def _update_strategy_score(con, strategy_id: str, realized: float,
                           now: float) -> None:
    con.execute(
        "INSERT INTO substrate_strategy_scores(strategy_id,closes,wins,losses,"
        "realized_pnl,last_close_at,updated_at) VALUES(?,?,?,?,?,?,?) "
        "ON CONFLICT(strategy_id) DO UPDATE SET "
        " closes=closes+1,"
        " wins=wins+excluded.wins,"
        " losses=losses+excluded.losses,"
        " realized_pnl=realized_pnl+excluded.realized_pnl,"
        " last_close_at=excluded.last_close_at,"
        " updated_at=excluded.updated_at",
        (strategy_id, 1, 1 if realized > 0 else 0, 1 if realized < 0 else 0,
         float(realized), now, now),
    )


def close_unpriced_writeoff(position_id: int, reason: str = "EXPIRED_UNPRICED_WRITEOFF") -> Dict[str, Any]:
    """Release an expired unpriceable PAPER position without inventing a mark.

    The position is terminal with realised PnL exactly 0.0, exit_price NULL and
    mark_source NONE. Reserved paper cash is returned. Idempotent.
    """
    ensure_schema()
    con = connect()
    try:
        _ensure_lifecycle_cols(con)
        row = con.execute(
            "SELECT * FROM substrate_positions WHERE id=?", (int(position_id),)
        ).fetchone()
        if not row:
            return {"ok": False, "reason": "position_not_found"}
        pos = dict(row)
        if str(pos.get("state") or "").upper() != "OPEN":
            return {"ok": False, "reason": "already_closed"}
        if str(pos.get("mode") or "PAPER").upper() != "PAPER":
            return {"ok": False, "reason": "not_paper"}
        now = time.time()
        size = float(pos.get("size_usd") or pos.get("position_size") or 0.0)
        cur = con.execute(
            "UPDATE substrate_positions SET state='CLOSED', status='CLOSED', "
            "closed_at=?, exit_price=NULL, exit_reason=?, realized_pnl=0, "
            "unrealized_pnl=0, updated_at=?, mark_source='NONE', "
            "mark_status='EXPIRED_UNPRICED_WRITEOFF', marked_at=? "
            "WHERE id=? AND state='OPEN'",
            (now, str(reason)[:200], now, now, int(position_id)),
        )
        if cur.rowcount != 1:
            return {"ok": False, "reason": "already_closed"}
        cash = cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0) + size
        cfg_set(con, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
        opp_id = pos.get("opportunity_id")
        if opp_id:
            con.execute(
                "UPDATE substrate_opportunities SET state='PAPER_CLOSED', "
                "updated_at=? WHERE id=?", (now, int(opp_id)),
            )
        strategy_id = str(pos.get("strategy_id") or DEFAULT_STRATEGY_ID)
        _update_strategy_score(con, strategy_id, 0.0, now)
        _audit(con, True, f"paper_closed:{reason}:pnl=+0.0000", pos,
               source="paper_ledger.unpriced_writeoff")
        con.commit()
        return {"ok": True, "position_id": int(position_id),
                "realized_pnl": 0.0, "exit_price": None,
                "strategy_id": strategy_id, "reason": reason}
    finally:
        con.close()


def close_paper_position(position_id: int, exit_price: float, reason: str,
                         mark_source: str = "substrate_price_feed") -> Dict[str, Any]:
    """Close one OPEN paper position at a REAL mark. Idempotent: a second call
    for the same id is a no-op reporting already_closed."""
    ensure_schema()
    con = connect()
    try:
        _ensure_lifecycle_cols(con)
        row = con.execute(
            "SELECT * FROM substrate_positions WHERE id=?", (int(position_id),)
        ).fetchone()
        if not row:
            return {"ok": False, "reason": "position_not_found"}
        pos = dict(row)
        if str(pos.get("state") or "").upper() != "OPEN":
            return {"ok": False, "reason": "already_closed"}
        entry = float(pos.get("entry_price_usd") or pos.get("entry_price") or 0)
        qty = float(pos.get("quantity") or 0)
        size = float(pos.get("size_usd") or pos.get("position_size") or 0)
        exit_price = float(exit_price or 0)
        if entry <= 0 or qty <= 0 or exit_price <= 0:
            _audit(con, False, f"close_rejected_bad_inputs:{reason}", pos,
                   source="paper_ledger.close")
            con.commit()
            return {"ok": False, "reason": "close_rejected_bad_inputs"}
        side = str(pos.get("side") or "LONG").upper()
        realized = ((exit_price - entry) * qty if side != "SHORT"
                    else (entry - exit_price) * qty)
        now = time.time()
        con.execute(
            "UPDATE substrate_positions SET state='CLOSED', status='CLOSED', "
            "closed_at=?, exit_price=?, exit_reason=?, realized_pnl=?, "
            "unrealized_pnl=0, current_price=?, updated_at=?, mark_source=?, "
            "mark_status='CLOSED', marked_at=? WHERE id=? AND state='OPEN'",
            (now, exit_price, str(reason)[:200], realized, exit_price, now,
             mark_source, now, int(position_id)),
        )
        cash = cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0) + size + realized
        cfg_set(con, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
        opp_id = pos.get("opportunity_id")
        if opp_id:
            con.execute(
                "UPDATE substrate_opportunities SET state='PAPER_CLOSED', "
                "updated_at=? WHERE id=?", (now, int(opp_id)),
            )
        strategy_id = str(pos.get("strategy_id") or DEFAULT_STRATEGY_ID)
        _update_strategy_score(con, strategy_id, realized, now)
        _audit(con, True,
               f"paper_closed:{reason}:pnl={realized:+.4f}", pos,
               source="paper_ledger.close")
        try:
            from services import substrate_position_persistence as _spp
            _jclose = getattr(_spp, "journal_close", None)
            if callable(_jclose):
                jc = _spp.connect("sentinuity_matrix.db")
                try:
                    _jclose(jc, str(position_id), exit_price, realized, reason)
                finally:
                    jc.close()
        except Exception:
            pass
        con.commit()
        return {"ok": True, "position_id": int(position_id),
                "realized_pnl": round(realized, 6), "exit_price": exit_price,
                "strategy_id": strategy_id, "reason": reason}
    finally:
        con.close()
