"""
services/macro_channel.py
=========================
Sovereign Substrate channel intelligence and council-target bridge.

This service:
- Reads real macro prices from substrate_prices.
- Detects compression plus breakout structure.
- Writes substrate_node_state and council proposals.
- Writes real PAPER candidates into substrate_targets for the existing
  substrate_paper_trader to auto-approve/deploy under its own risk gates.
- Never creates fake trades and never promises a daily trade.
- Does not implement short execution. Downside breakouts remain council-visible
  SHORT_WATCH proposals until a real short-capable venue and risk contract exist.
"""
from __future__ import annotations

import json
import logging
import os
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [macro_channel] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("macro_channel")

SERVICE_NAME = "macro_channel"
CYCLE_SECONDS = max(15, int(os.getenv("SUBSTRATE_CHANNEL_CYCLE_SEC", "30")))
SYMBOLS = ["BTC", "ETH", "SOL", "XRP", "SUI", "BNB"]
PRICE_SOURCE = {
    "BTC": "coingecko:bitcoin",
    "ETH": "coingecko:ethereum",
    "SOL": "coingecko:solana",
    "XRP": "coingecko:ripple",
    "SUI": "coingecko:sui",
    "BNB": "coingecko:binancecoin",
}

BB_COMPRESSION_PCT = float(os.getenv("SUBSTRATE_BB_COMPRESSION_PCT", "4.0"))
BB_BREAKOUT_PCT = float(os.getenv("SUBSTRATE_BB_BREAKOUT_PCT", "1.5"))
RSI_OVERSOLD = float(os.getenv("SUBSTRATE_RSI_OVERSOLD", "35"))
RSI_OVERBOUGHT = float(os.getenv("SUBSTRATE_RSI_OVERBOUGHT", "65"))
MIN_HISTORY_ROWS = max(10, int(os.getenv("SUBSTRATE_MIN_HISTORY_ROWS", "20")))
TARGET_DEDUPE_SEC = max(300, int(os.getenv("SUBSTRATE_TARGET_DEDUPE_SEC", "1800")))


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_prices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            price_usd REAL NOT NULL,
            change_24h REAL DEFAULT 0,
            volume_24h REAL DEFAULT 0,
            high_24h REAL DEFAULT 0,
            low_24h REAL DEFAULT 0,
            rsi_14 REAL DEFAULT 50,
            bb_upper REAL DEFAULT 0,
            bb_lower REAL DEFAULT 0,
            bb_width REAL DEFAULT 0,
            fetched_at REAL NOT NULL
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_substrate_prices_symbol_time ON substrate_prices(symbol, fetched_at DESC)")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_node_state (
            symbol TEXT PRIMARY KEY,
            state TEXT DEFAULT 'WATCHING',
            edge_score REAL DEFAULT 0,
            council_ranked INTEGER DEFAULT 0,
            compression_type TEXT,
            trigger_fired INTEGER DEFAULT 0,
            updated_at REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS substrate_targets (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL DEFAULT 'COUNCIL',
            asset_symbol TEXT NOT NULL,
            mint_address TEXT,
            side TEXT NOT NULL DEFAULT 'LONG',
            price_source TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            conviction REAL NOT NULL DEFAULT 0,
            signal_ref_price REAL,
            thesis TEXT,
            council_votes TEXT,
            review_note TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        )
    """)
    # Additive compatibility for older tables.
    wanted = {
        "source": "TEXT DEFAULT 'COUNCIL'",
        "asset_symbol": "TEXT",
        "mint_address": "TEXT",
        "side": "TEXT DEFAULT 'LONG'",
        "price_source": "TEXT",
        "status": "TEXT DEFAULT 'proposed'",
        "conviction": "REAL DEFAULT 0",
        "signal_ref_price": "REAL",
        "thesis": "TEXT",
        "council_votes": "TEXT",
        "review_note": "TEXT",
        "created_at": "REAL",
        "updated_at": "REAL",
    }
    existing = _columns(conn, "substrate_targets")
    for name, spec in wanted.items():
        if name not in existing:
            conn.execute(f"ALTER TABLE substrate_targets ADD COLUMN {name} {spec}")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_substrate_targets_state_time ON substrate_targets(status, created_at DESC)")
    conn.commit()


def _load_latest_prices(conn: sqlite3.Connection, sym: str) -> list[dict]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT price_usd, rsi_14, bb_upper, bb_lower, bb_width, fetched_at
        FROM substrate_prices
        WHERE symbol=?
        ORDER BY fetched_at DESC LIMIT 30
    """, (sym,)).fetchall()
    return [dict(r) for r in rows]


def _detect_state(rows: list[dict]) -> tuple[str, float, str, str]:
    """Return state, edge score, structure label, direction."""
    if len(rows) < MIN_HISTORY_ROWS:
        return "WATCHING", 0.0, "", "NONE"
    latest = rows[0]
    price = float(latest.get("price_usd") or 0)
    rsi = float(latest.get("rsi_14") or 50)
    upper = float(latest.get("bb_upper") or price * 1.05)
    lower = float(latest.get("bb_lower") or price * 0.95)
    width = float(latest.get("bb_width") or 10)
    if price <= 0:
        return "WATCHING", 0.0, "", "NONE"

    edge = 0.0
    label = ""
    if width < BB_COMPRESSION_PCT:
        edge += 0.30
        label = f"BB_SQUEEZE_{width:.1f}pct"
        if rsi < RSI_OVERSOLD:
            edge += 0.30
            label += "_OVERSOLD"
        elif rsi > RSI_OVERBOUGHT:
            edge += 0.20
            label += "_OVERBOUGHT"

        if price > upper * (1 + BB_BREAKOUT_PCT / 100):
            edge += 0.40
            return "EXPANDING", min(edge, 1.0), label + "_UP", "LONG"
        if price < lower * (1 - BB_BREAKOUT_PCT / 100):
            edge += 0.30
            return "EXPANDING", min(edge, 1.0), label + "_DOWN", "SHORT_WATCH"
        return ("COMPRESSING" if edge >= 0.5 else "WATCHING"), min(edge, 1.0), label, "NONE"
    return "WATCHING", 0.0, "", "NONE"


def _proposal_exists(conn: sqlite3.Connection, sym: str, since: float) -> bool:
    try:
        return conn.execute("""
            SELECT 1 FROM polaris_proposals
            WHERE proposal_domain='SUBSTRATE' AND project_key=? AND created_at>?
            LIMIT 1
        """, (sym, since)).fetchone() is not None
    except Exception:
        return False


def _write_proposal(conn: sqlite3.Connection, sym: str, text: str, confidence: float, now: float) -> bool:
    try:
        conn.execute("""
            INSERT INTO polaris_proposals
                (proposal_type, proposal_domain, project_key, proposal_text,
                 status, confidence, created_at)
            VALUES ('SUBSTRATE_BREAKOUT','SUBSTRATE',?,?,'open',?,?)
        """, (sym, text, round(confidence, 4), now))
        return True
    except Exception as exc:
        log.warning("proposal insert failed %s: %s", sym, exc)
        return False


def _write_long_target(conn: sqlite3.Connection, sym: str, price: float, edge: float,
                       structure: str, now: float) -> bool:
    """Bridge a real upside breakout into the paper trader's target table."""
    existing = conn.execute("""
        SELECT 1 FROM substrate_targets
        WHERE asset_symbol=? AND side='LONG'
          AND status IN ('proposed','approved_paper','deployed_paper')
          AND created_at>?
        LIMIT 1
    """, (sym, now - TARGET_DEDUPE_SEC)).fetchone()
    if existing:
        return False
    thesis = (
        f"Compression + upside breakout: {structure}; edge={edge:.2f}. "
        "Paper-first spot target. Existing trader enforces conviction, freshness, "
        "entry drift, capacity, TP, SL and max-hold gates."
    )
    votes = json.dumps({
        "source": "macro_channel",
        "direction": "LONG",
        "edge_score": round(edge, 4),
        "paper_only": True,
        "short_execution_supported": False,
    }, sort_keys=True)
    conn.execute("""
        INSERT INTO substrate_targets
            (source, asset_symbol, mint_address, side, price_source, status,
             conviction, signal_ref_price, thesis, council_votes, review_note,
             created_at, updated_at)
        VALUES ('MACRO_CHANNEL',?,?,?,?, 'proposed',?,?,?,?,?,?,?)
    """, (
        sym, "native" if sym == "SOL" else "wrapped", "LONG",
        PRICE_SOURCE[sym], round(edge, 4), price, thesis, votes,
        "Awaiting council auto-approval threshold; paper-only", now, now,
    ))
    return True


def _run_cycle() -> dict:
    now = time.time()
    states_written = proposals_created = targets_created = short_watches = 0
    active: list[tuple[str, float]] = []
    with get_connection() as conn:
        conn.row_factory = sqlite3.Row
        _ensure_schema(conn)
        scored: list[tuple[str, str, float]] = []
        for sym in SYMBOLS:
            rows = _load_latest_prices(conn, sym)
            if not rows:
                continue
            state, edge, structure, direction = _detect_state(rows)
            prev = conn.execute("SELECT state FROM substrate_node_state WHERE symbol=?", (sym,)).fetchone()
            prev_state = str(prev[0]) if prev else "WATCHING"
            conn.execute("""
                INSERT INTO substrate_node_state
                    (symbol,state,edge_score,council_ranked,compression_type,trigger_fired,updated_at)
                VALUES (?,?,?,0,?,?,?)
                ON CONFLICT(symbol) DO UPDATE SET
                    state=excluded.state, edge_score=excluded.edge_score,
                    compression_type=excluded.compression_type,
                    trigger_fired=excluded.trigger_fired, updated_at=excluded.updated_at
            """, (sym, state, round(edge, 4), structure, int(state == "EXPANDING"), now))
            states_written += 1
            scored.append((sym, state, edge))
            if state != "WATCHING":
                active.append((sym, round(edge, 2)))
            if state != prev_state:
                log.info("[STATE] %s: %s -> %s edge=%.2f direction=%s", sym, prev_state, state, edge, direction)

            if state == "EXPANDING" and prev_state != "EXPANDING":
                latest = rows[0]
                text = (
                    f"SUBSTRATE {direction}: {sym} price=${float(latest['price_usd']):.6g} "
                    f"BB_width={float(latest['bb_width'] or 0):.2f}% "
                    f"RSI={float(latest['rsi_14'] or 50):.1f} structure={structure} edge={edge:.2f}. "
                    + ("Evaluate PAPER LONG target." if direction == "LONG" else
                       "Downside structure detected. SHORT execution is not implemented; research/watch only.")
                )
                if not _proposal_exists(conn, sym, now - TARGET_DEDUPE_SEC):
                    proposals_created += int(_write_proposal(conn, sym, text, edge, now))
                if direction == "LONG":
                    targets_created += int(_write_long_target(conn, sym, float(latest['price_usd']), edge, structure, now))
                elif direction == "SHORT_WATCH":
                    short_watches += 1

        ranked = [(s, e) for s, st, e in scored if st in ("COMPRESSING", "EXPANDING", "DEBATING")]
        ranked.sort(key=lambda x: x[1], reverse=True)
        top = {s for s, _ in ranked[:2]}
        for sym in SYMBOLS:
            conn.execute("UPDATE substrate_node_state SET council_ranked=? WHERE symbol=?", (int(sym in top), sym))
        conn.commit()
    return {
        "states_written": states_written,
        "proposals": proposals_created,
        "targets": targets_created,
        "short_watches": short_watches,
        "active": active,
    }


def run() -> None:
    log.info("Macro channel started — real prices, paper target bridge, no forced trades")
    update_heartbeat(SERVICE_NAME, "starting", "macro channel online")
    while True:
        try:
            stats = _run_cycle()
            note = (
                f"states={stats['states_written']} proposals={stats['proposals']} "
                f"targets={stats['targets']} short_watch={stats['short_watches']} active={stats['active']}"
            )
            if stats["active"] or stats["targets"]:
                log.info("[CYCLE] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note, work_processed=stats["states_written"] + stats["targets"])
        except Exception as exc:
            log.exception("[CHANNEL_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")
        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
