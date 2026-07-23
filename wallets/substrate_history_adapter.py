# coding: utf-8
"""
wallets/substrate_history_adapter.py — COUNCIL_AUTOBUILD_20260723

ONE canonical adapter for Substrate position history. Fixes the audited chart
defect: table EXISTENCE was mistaken for table AUTHORITY (empty legacy
substrate_paper_positions hid populated substrate_positions).

Authority rule: the canonical source is the table with VALID POPULATED rows
(entry_price>0) under the schema contract — preferring substrate_positions
when both are populated. Returns one stable normalized contract, deduplicates
test/legacy copies, honors PnL quarantine columns, and detects stale-mark gaps.
"""
from __future__ import annotations
import json, sqlite3, time
from typing import Any, Dict, List, Optional

CONTRACT_FIELDS = ["id", "symbol", "side", "status", "entry_ts", "entry_px",
                   "exit_ts", "exit_px", "last_mark_px", "last_mark_ts",
                   "peak_px", "stop_px", "target_px", "pnl_usd", "pnl_pct",
                   "strategy", "thesis", "source", "source_fresh",
                   "stale_gap", "pnl_eligible", "is_legacy", "is_test",
                   "entry_truth_status", "table_origin"]

STALE_MARK_SEC = 900.0


def _ro(db_path: str) -> sqlite3.Connection:
    c = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _cols(c: sqlite3.Connection, t: str) -> set:
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({t})")}
    except Exception:
        return set()


def _valid_rows(c: sqlite3.Connection, t: str) -> int:
    cols = _cols(c, t)
    if not cols or "entry_price" not in cols:
        return 0
    try:
        return c.execute(
            f"SELECT COUNT(*) FROM {t} WHERE COALESCE(entry_price,0)>0"
        ).fetchone()[0]
    except Exception:
        return 0


def select_cadence_table(db_path: str) -> str:
    """Authority = valid populated rows under the schema contract, never mere
    existence. substrate_positions wins ties (canonical schema)."""
    try:
        c = _ro(db_path)
    except Exception:
        return "substrate_positions"
    try:
        canon = _valid_rows(c, "substrate_positions")
        legacy = _valid_rows(c, "substrate_paper_positions")
        if canon > 0:
            return "substrate_positions"
        if legacy > 0:
            return "substrate_paper_positions"
        return "substrate_positions"
    finally:
        c.close()


def _g(row: sqlite3.Row, keys: set, *names, default=None):
    for n in names:
        if n in keys and row[n] is not None:
            return row[n]
    return default


def _normalize(row: sqlite3.Row, origin: str, now: float) -> Dict[str, Any]:
    k = set(row.keys())
    entry_px = float(_g(row, k, "entry_price", default=0) or 0)
    last_px = float(_g(row, k, "last_price", "last_mark_price", "mark_price",
                       default=0) or 0)
    last_ts = float(_g(row, k, "last_price_at", "last_mark_at", "marked_at",
                       default=0) or 0)
    exit_px = _g(row, k, "exit_price")
    status = str(_g(row, k, "status", default="OPEN") or "OPEN").upper()
    pnl_usd = _g(row, k, "pnl_usd", "realized_pnl_usd")
    qty = float(_g(row, k, "qty", "quantity", default=0) or 0)
    if pnl_usd is None and entry_px > 0 and qty > 0:
        ref = float(exit_px or last_px or 0)
        pnl_usd = (ref - entry_px) * qty if ref > 0 else None
    pnl_pct = _g(row, k, "pnl_pct")
    if pnl_pct is None and pnl_usd is not None and entry_px > 0 and qty > 0:
        pnl_pct = pnl_usd / (entry_px * qty) * 100
    stale_gap = bool(status == "OPEN" and last_ts > 0
                     and (now - last_ts) > STALE_MARK_SEC)
    return {
        "id": _g(row, k, "id"),
        "symbol": str(_g(row, k, "asset_symbol", "symbol", default="?") or "?"),
        "side": str(_g(row, k, "side", default="LONG") or "LONG").upper(),
        "status": status,
        "entry_ts": float(_g(row, k, "opened_at", "entry_ts", default=0) or 0),
        "entry_px": entry_px,
        "exit_ts": _g(row, k, "closed_at", "exit_ts"),
        "exit_px": exit_px,
        "last_mark_px": last_px or None,
        "last_mark_ts": last_ts or None,
        "peak_px": _g(row, k, "peak_price"),
        "stop_px": _g(row, k, "sl_price", "stop_price"),
        "target_px": _g(row, k, "tp_price", "target_price"),
        "pnl_usd": (round(float(pnl_usd), 4) if pnl_usd is not None else None),
        "pnl_pct": (round(float(pnl_pct), 3) if pnl_pct is not None else None),
        "strategy": _g(row, k, "strategy", "strategy_key", default=""),
        "thesis": _g(row, k, "thesis", "council_thesis", "notes", default=""),
        "source": _g(row, k, "price_source", "source", default=""),
        "source_fresh": (not stale_gap) if status == "OPEN" else None,
        "stale_gap": stale_gap,
        "pnl_eligible": int(_g(row, k, "pnl_eligible", default=1) or 0),
        "is_legacy": int(_g(row, k, "is_legacy", default=0) or 0),
        "is_test": int(_g(row, k, "is_test", default=0) or 0),
        "entry_truth_status": str(_g(row, k, "entry_truth_status",
                                     default="UNVERIFIED") or "UNVERIFIED"),
        "table_origin": origin,
    }


def load_substrate_position_history(db_path: str,
                                    include_quarantined: bool = True
                                    ) -> Dict[str, Any]:
    """The single chart contract. Inspects both schemas, selects populated
    canonical records, normalizes, dedupes test/legacy copies, and separates
    canonical PnL (pnl_eligible=1) from quarantined records."""
    now = time.time()
    out = {"table": select_cadence_table(db_path), "positions": [],
           "quarantined": [], "realised_pnl_usd": 0.0,
           "unrealised_pnl_usd": 0.0, "open": 0, "closed": 0}
    try:
        c = _ro(db_path)
    except Exception:
        return out
    try:
        seen = set()
        for origin in ("substrate_positions", "substrate_paper_positions"):
            if _valid_rows(c, origin) == 0:
                continue
            for r in c.execute(f"SELECT * FROM {origin} "
                               f"WHERE COALESCE(entry_price,0)>0"):
                n = _normalize(r, origin, now)
                key = (n["symbol"], round(n["entry_ts"], 1), n["entry_px"])
                if key in seen:                       # dedupe test/legacy copies
                    continue
                seen.add(key)
                quarantined = (n["pnl_eligible"] == 0 or n["is_test"] == 1
                               or n["is_legacy"] == 1)
                (out["quarantined"] if quarantined
                 else out["positions"]).append(n)
        for n in out["positions"]:
            if n["status"] == "OPEN":
                out["open"] += 1
                if n["pnl_usd"] is not None:
                    out["unrealised_pnl_usd"] += n["pnl_usd"]
            else:
                out["closed"] += 1
                if n["pnl_usd"] is not None:
                    out["realised_pnl_usd"] += n["pnl_usd"]
        out["realised_pnl_usd"] = round(out["realised_pnl_usd"], 4)
        out["unrealised_pnl_usd"] = round(out["unrealised_pnl_usd"], 4)
        if not include_quarantined:
            out["quarantined"] = []
        return out
    finally:
        c.close()
