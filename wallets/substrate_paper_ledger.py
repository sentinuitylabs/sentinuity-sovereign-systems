from __future__ import annotations

import json
import time
from typing import Any, Dict
from .substrate_wallet_schema import connect, ensure_schema, cfg_float, cfg_int, cfg_get, cfg_set


def _audit(con, allowed: bool, reason: str, opp: dict | None = None, source: str = "paper_ledger") -> None:
    opp = opp or {}
    con.execute(
        "INSERT INTO substrate_execution_audit(created_at,allowed,reason,source,asset_symbol,chain,confidence,raw_json) "
        "VALUES(?,?,?,?,?,?,?,?)",
        (
            time.time(), 1 if allowed else 0, reason, source,
            opp.get("asset_symbol", ""), opp.get("chain", ""), float(opp.get("confidence") or 0),
            json.dumps({"opportunity_id": opp.get("id"), "mode": "PAPER"}, sort_keys=True),
        ),
    )


def open_paper_position_from_opportunity(opportunity_id: int) -> Dict[str, Any]:
    ensure_schema()
    con = connect()
    try:
        opp_row = con.execute("SELECT * FROM substrate_opportunities WHERE id=?", (int(opportunity_id),)).fetchone()
        if not opp_row:
            return {"ok": False, "reason": "opportunity_not_found"}
        opp = dict(opp_row)
        state = str(opp.get("state") or "NEW").upper()
        if state not in ("NEW", "READY", "PROMOTED"):
            _audit(con, False, f"state_not_openable:{state}", opp)
            con.commit()
            return {"ok": False, "reason": f"state_not_openable:{state}"}
        max_open = cfg_int(con, "SUBSTRATE_MAX_OPEN", 3)
        open_n = con.execute("SELECT COUNT(*) c FROM substrate_positions WHERE mode='PAPER' AND state='OPEN'").fetchone()["c"]
        if int(open_n or 0) >= max_open:
            _audit(con, False, "paper_max_open_reached", opp)
            con.commit()
            return {"ok": False, "reason": "paper_max_open_reached"}
        size = min(cfg_float(con, "SUBSTRATE_POSITION_SIZE_USD", 25.0), cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0))
        if size <= 0:
            _audit(con, False, "paper_cash_empty", opp)
            con.commit()
            return {"ok": False, "reason": "paper_cash_empty"}
        px = float(opp.get("price_usd") or 0)
        if px <= 0:
            _audit(con, False, "no_price", opp)
            con.commit()
            return {"ok": False, "reason": "no_price"}
        now = time.time()
        qty = size / px
        cur = con.execute(
            "INSERT INTO substrate_positions(opportunity_id,mode,state,status,chain,asset_symbol,symbol,side,size_usd,position_size,"
            "entry_price_usd,entry_price,current_price,quantity,source,opened_at,updated_at,raw_json) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                int(opportunity_id), "PAPER", "OPEN", "OPEN", opp.get("chain"), opp.get("asset_symbol"),
                opp.get("asset_symbol"), "LONG", size, size, px, px, px, qty,
                opp.get("source") or "SUBSTRATE", now, now,
                json.dumps({"opportunity_id": opportunity_id, "quote_asset": opp.get("quote_asset", "USDC")}, sort_keys=True),
            ),
        )
        position_id = int(cur.lastrowid)
        try:
            from services.substrate_position_persistence import connect as _jconnect, journal_open
            jc = _jconnect("sentinuity_matrix.db")
            try:
                journal_open(jc, str(position_id), str(opp.get("asset_symbol") or ""), "LONG", px, size,
                             intended_hold_seconds=cfg_float(con, "SUBSTRATE_MAX_HOLD_SEC", 86400.0),
                             thesis=f"opportunity_id={opportunity_id};source={opp.get('source') or 'SUBSTRATE'}")
            finally:
                jc.close()
        except Exception:
            pass
        cash = cfg_float(con, "SUBSTRATE_PAPER_CASH_USD", 0.0) - size
        cfg_set(con, "SUBSTRATE_PAPER_CASH_USD", f"{cash:.4f}")
        con.execute("UPDATE substrate_opportunities SET state='PAPER_OPENED', updated_at=? WHERE id=?", (now, int(opportunity_id)))
        _audit(con, True, "paper_opened", opp)
        con.commit()
        return {"ok": True, "position_id": position_id, "position_size_usd": size, "price_usd": px}
    finally:
        con.close()
