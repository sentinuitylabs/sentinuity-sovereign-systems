#!/usr/bin/env python3
"""
open_position_mark_truth_guard_0707.py
============================================================
Paper-only open-position mark truth guard.

Purpose:
- Keep OPEN paper positions refreshed when ws_price_oracle stalls.
- Write trade_lifecycle_events ticks for audit/verification.
- Refresh last_price/current_price/unrealized PnL from fallback only.
- DO NOT raise highest_price_seen from unconfirmed fallback marks.

This is intentionally conservative. It restores mark freshness without trusting
phantom fallback peaks. Shelf/banker logic must come later and use tick-confirmed
movement only.
"""
from __future__ import annotations

import json
import math
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

import requests

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"
SERVICE = "open_position_mark_truth_guard_0707"

CYCLE_SEC = float(os.getenv("MARK_TRUTH_GUARD_CYCLE_SEC", "3.0"))
HTTP_TIMEOUT = float(os.getenv("MARK_TRUTH_HTTP_TIMEOUT_SEC", "6.0"))
MAX_REF_JUMP_PCT = float(os.getenv("MARK_TRUTH_MAX_REF_JUMP_PCT", "250.0"))
CONFIRM_TOLERANCE_PCT = float(os.getenv("MARK_TRUTH_CONFIRM_TOLERANCE_PCT", "8.0"))
CONFIRM_MIN_AGE_SEC = float(os.getenv("MARK_TRUTH_CONFIRM_MIN_AGE_SEC", "4.0"))
CONFIRM_MAX_AGE_SEC = float(os.getenv("MARK_TRUTH_CONFIRM_MAX_AGE_SEC", "90.0"))


def connect() -> sqlite3.Connection:
    con = sqlite3.connect(DB, timeout=30)
    con.row_factory = sqlite3.Row
    return con


def cols(cur: sqlite3.Cursor, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in cur.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def table_exists(cur: sqlite3.Cursor, table: str) -> bool:
    return cur.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def ensure_tables(cur: sqlite3.Cursor) -> None:
    cur.execute("""
        CREATE TABLE IF NOT EXISTS system_heartbeat (
            service_name TEXT PRIMARY KEY,
            status TEXT,
            last_pulse REAL,
            note TEXT
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS trade_lifecycle_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            position_id INTEGER,
            mint_address TEXT,
            event_type TEXT,
            price REAL,
            pct_from_entry REAL,
            age_sec REAL,
            source TEXT,
            can_execute INTEGER,
            tick_count INTEGER,
            coverage_score REAL,
            first_tick_delay_sec REAL,
            created_at REAL
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS mark_truth_candidate_peaks (
            mint_address TEXT PRIMARY KEY,
            position_id INTEGER,
            candidate_price REAL,
            candidate_pct REAL,
            first_seen_at REAL,
            source TEXT
        )
    """)


def heartbeat(cur: sqlite3.Cursor, status: str, note: str) -> None:
    ensure_tables(cur)
    cur.execute("""
        INSERT INTO system_heartbeat(service_name,status,last_pulse,note)
        VALUES(?,?,?,?)
        ON CONFLICT(service_name) DO UPDATE SET
            status=excluded.status,
            last_pulse=excluded.last_pulse,
            note=excluded.note
    """, (SERVICE, status, time.time(), note[:500]))


def fetch_dexscreener_price(mint: str) -> tuple[Optional[float], str]:
    url = f"https://api.dexscreener.com/latest/dex/tokens/{mint}"
    r = requests.get(url, timeout=HTTP_TIMEOUT, headers={"Accept": "application/json"})
    r.raise_for_status()
    data = r.json()
    pairs = data.get("pairs") or []
    best = None
    best_liq = -1.0
    for p in pairs:
        try:
            px = float(p.get("priceUsd") or 0)
            liq = float(((p.get("liquidity") or {}).get("usd")) or 0)
            chain = str(p.get("chainId") or "").lower()
            if px > 0 and ("sol" in chain or chain == "") and liq >= best_liq:
                best = px
                best_liq = liq
        except Exception:
            continue
    return best, "mark_truth_guard_dexscreener"


def pct(entry: float, price: float) -> float:
    return ((price / entry) - 1.0) * 100.0 if entry > 0 and price > 0 else 0.0


def sane_against_ref(price: float, ref: float) -> bool:
    if price <= 0 or not math.isfinite(price):
        return False
    if ref <= 0:
        return True
    move = abs((price / ref - 1.0) * 100.0)
    return move <= MAX_REF_JUMP_PCT


def maybe_confirm_peak(cur: sqlite3.Cursor, *, position_id: int, mint: str, entry: float, price: float, source: str, now: float) -> bool:
    """Return True only when a candidate high is corroborated by repeated fallback marks."""
    candidate_pct = pct(entry, price)
    if candidate_pct < 35.0:
        return True  # small highs may pass if caller wants, but we still don't force high here

    old = cur.execute(
        "SELECT candidate_price,candidate_pct,first_seen_at FROM mark_truth_candidate_peaks WHERE mint_address=?",
        (mint,),
    ).fetchone()
    if not old:
        cur.execute(
            "INSERT OR REPLACE INTO mark_truth_candidate_peaks(mint_address,position_id,candidate_price,candidate_pct,first_seen_at,source) VALUES(?,?,?,?,?,?)",
            (mint, position_id, price, candidate_pct, now, source),
        )
        return False

    old_px = float(old["candidate_price"] or 0)
    old_ts = float(old["first_seen_at"] or 0)
    age = now - old_ts
    if old_px <= 0 or age > CONFIRM_MAX_AGE_SEC:
        cur.execute(
            "INSERT OR REPLACE INTO mark_truth_candidate_peaks(mint_address,position_id,candidate_price,candidate_pct,first_seen_at,source) VALUES(?,?,?,?,?,?)",
            (mint, position_id, price, candidate_pct, now, source),
        )
        return False

    agree = abs((price / old_px - 1.0) * 100.0) <= CONFIRM_TOLERANCE_PCT
    if agree and age >= CONFIRM_MIN_AGE_SEC:
        cur.execute("DELETE FROM mark_truth_candidate_peaks WHERE mint_address=?", (mint,))
        return True
    return False


def mark_position(cur: sqlite3.Cursor, p: sqlite3.Row, price: float, source: str) -> tuple[bool, bool, str]:
    pc = cols(cur, "paper_positions")
    now = time.time()
    pid = int(p["id"])
    mint = str(p["mint_address"])
    entry = float(p["entry_price"] or 0)
    qty = float(p["quantity"] or 0) if "quantity" in p.keys() else 0.0
    size = float(p["position_size_usd"] or 0) if "position_size_usd" in p.keys() else 0.0
    ref = float((p["last_price"] or p["entry_price"] or 0))

    if entry <= 0 or price <= 0 or not sane_against_ref(price, ref):
        return False, False, "rejected_unsane"

    pnl_pct = pct(entry, price)
    unreal = (qty * price - size) if qty and size else (size * pnl_pct / 100.0 if size else 0.0)
    market_value = qty * price if qty else (size * price / entry if size and entry else 0.0)
    old_high = float(p["highest_price_seen"] or 0) if "highest_price_seen" in p.keys() and p["highest_price_seen"] else 0.0
    will_raise = price > old_high and price > entry
    confirmed_peak = maybe_confirm_peak(cur, position_id=pid, mint=mint, entry=entry, price=price, source=source, now=now) if will_raise else True

    sets, vals = [], []
    for col, val in [
        ("last_price", price),
        ("current_price", price),
        ("last_marked_at", now),
        ("updated_at", now),
        ("unrealized_pnl_pct", pnl_pct),
        ("unrealized_pnl_usd", unreal),
        ("market_value_usd", market_value),
        ("mark_source", source + ("_peak_confirmed" if confirmed_peak and will_raise else "_no_peak")),
    ]:
        if col in pc:
            sets.append(f"{col}=?")
            vals.append(val)

    # Only raise high-water mark on confirmed repeated fallback high.
    if will_raise and confirmed_peak and "highest_price_seen" in pc:
        sets.append("highest_price_seen=?")
        vals.append(price)
    if will_raise and confirmed_peak and "peak_pnl_pct" in pc:
        old_peak = float(p["peak_pnl_pct"] or 0) if "peak_pnl_pct" in p.keys() else 0.0
        sets.append("peak_pnl_pct=?")
        vals.append(max(old_peak, pnl_pct))

    if sets:
        vals.append(pid)
        cur.execute(f"UPDATE paper_positions SET {', '.join(sets)} WHERE id=?", vals)

    tick_count = cur.execute("SELECT COUNT(*) FROM trade_lifecycle_events WHERE position_id=?", (pid,)).fetchone()[0] + 1
    cur.execute("""
        INSERT INTO trade_lifecycle_events(position_id,mint_address,event_type,price,pct_from_entry,age_sec,source,can_execute,tick_count,coverage_score,first_tick_delay_sec,created_at)
        VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        pid, mint, "MARK_TRUTH_TICK", price, pnl_pct,
        now - float(p["opened_at"] or now) if "opened_at" in p.keys() else None,
        source + ("_confirmed" if confirmed_peak else "_unconfirmed_peak"),
        1 if confirmed_peak or pnl_pct < 35 else 0,
        tick_count, 1.0 if confirmed_peak else 0.5, None, now,
    ))
    return True, bool(will_raise and confirmed_peak), "ok"


def open_positions(cur: sqlite3.Cursor) -> list[sqlite3.Row]:
    pc = cols(cur, "paper_positions")
    needed = {"id", "mint_address", "status", "entry_price"}
    if not needed.issubset(pc):
        return []
    select = [c for c in [
        "id","mint_address","status","opened_at","entry_price","last_price","current_price",
        "highest_price_seen","peak_pnl_pct","unrealized_pnl_pct","position_size_usd","quantity"
    ] if c in pc]
    return cur.execute(f"SELECT {','.join(select)} FROM paper_positions WHERE UPPER(COALESCE(status,''))='OPEN' ORDER BY id DESC").fetchall()


def main() -> None:
    print("=" * 94)
    print("OPEN POSITION MARK TRUTH GUARD 0707")
    print("paper-only; refreshes current marks; quarantines unconfirmed fallback peaks")
    print("DB:", DB)
    print("Ctrl+C to stop")
    print("=" * 94)
    while True:
        marked = confirmed = rejected = errors = 0
        notes = []
        try:
            con = connect()
            cur = con.cursor()
            ensure_tables(cur)
            positions = open_positions(cur)
            for p in positions:
                mint = str(p["mint_address"] or "").strip()
                if not mint:
                    continue
                try:
                    price, source = fetch_dexscreener_price(mint)
                    if not price:
                        rejected += 1
                        continue
                    ok, conf, reason = mark_position(cur, p, price, source)
                    if ok:
                        marked += 1
                    else:
                        rejected += 1
                    if conf:
                        confirmed += 1
                    notes.append(f"{mint[:8]}:{reason}:{price:.8g}")
                except Exception as e:
                    errors += 1
                    notes.append(f"{mint[:8]}:ERR:{type(e).__name__}")
            status = "ALIVE" if errors == 0 else "WARN"
            note = f"open={len(positions)} marked={marked} confirmed_peaks={confirmed} rejected={rejected} errors={errors} cycle={CYCLE_SEC}s " + " | ".join(notes[:6])
            heartbeat(cur, status, note)
            con.commit()
            con.close()
            print(time.strftime("%H:%M:%S"), note[:700])
        except KeyboardInterrupt:
            print("stopped")
            break
        except Exception as e:
            print("ERROR", type(e).__name__, e)
        time.sleep(CYCLE_SEC)


if __name__ == "__main__":
    main()
