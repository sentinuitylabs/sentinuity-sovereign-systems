"""
ui/substrate_node.py — SIGNOFF_SUBSTRATE_UPGRADE_20260613
==========================================================
SENTINUITY SUBSTRATE NODE — Quant Command Layer v4.0

New sections added this session:
  1. MACRO TRADE BOOK    — live paper/live buy-sell feed for BTC/ETH/SOL/XRP/SUI/BNB
  2. COUNCIL DECISION    — what the 6-node council must verify before capital deploys
  3. COPY TRADE STATION  — standalone alt-asset wallet copy-trade lane
  4. All existing tabs preserved: Council, Golden Lattice, Runner Radar, Velocity,
     Smart Wallets, Strategy Lab

Read-only UI. No capital writes. Height-safe (no widget called with height=None).
Colour system: #9945FF purple / #14F195 green / #8EF9FF cyan / #FFD700 gold / #FF5577 red
Typography: Orbitron display, Share Tech Mono data, Rajdhani body — as per codebase.
"""
from __future__ import annotations

import html
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
import streamlit as st

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"

# ── Colour hierarchy (matches sovereign_hub.py exactly) ──────────────────────
C_VOID   = "#050210"
C_PANEL  = "rgba(12,4,30,0.82)"
C_PANEL2 = "rgba(28,10,50,0.55)"
C_PURPLE = "#9945FF"
C_CYAN   = "#8EF9FF"
C_GREEN  = "#14F195"
C_GOLD   = "#FFD700"
C_RED    = "#FF073A"
C_AMBER  = "#FFB347"
C_DIM    = "rgba(180,160,255,0.48)"
C_BORDER = "rgba(153,69,255,0.28)"

COUNCIL_ORDER = ["POLARIS", "IVARIS", "NUGGET", "ORACLE", "AXON", "RHIZA"]
COUNCIL_EMOJI = {"POLARIS": "❄️", "IVARIS": "🔥", "NUGGET": "🔮",
                 "ORACLE": "🌐", "AXON": "⚡", "RHIZA": "🕸️"}
PHASE_COLORS  = {
    "DISCOVER": C_CYAN, "DESIGN": C_PURPLE, "PATCH_READY": C_GOLD,
    "TESTING": C_AMBER, "NEEDS_APPROVAL": C_RED, "APPROVED": C_GREEN,
    "APPLIED": C_GREEN, "POST_VERIFY": C_CYAN, "VERIFIED": C_GREEN,
    "FAILED": C_RED, "ROLLED_BACK": C_AMBER,
}

SUBSTRATE_ASSETS  = ["BTC", "ETH", "SOL", "XRP", "SUI", "BNB"]
ASSET_TIER        = {"BTC": 1, "ETH": 1, "SOL": 1, "XRP": 2, "SUI": 2, "BNB": 2}
ASSET_ICONS       = {"BTC": "₿", "ETH": "Ξ", "SOL": "◎", "XRP": "✕", "SUI": "◈", "BNB": "⬡"}

# ── Helpers ───────────────────────────────────────────────────────────────────
def _now() -> float: return time.time()
def _esc(v: Any) -> str: return html.escape("" if v is None else str(v), quote=True)
def _short(v: Any, n: int = 90) -> str:
    t = "" if v is None else str(v); return t if len(t) <= n else t[:n-1] + "…"
def _num(v: Any, d: float = 0.0) -> float:
    try: return float(v) if v is not None else d
    except: return d
def _int(v: Any, d: int = 0) -> int:
    try: return int(float(v)) if v is not None else d
    except: return d
def _safe_height(rows=None, *, row_px=34, header_px=42, minimum=118, maximum=360) -> int:
    r = max(1, _int(rows, 1))
    return max(minimum, min(maximum, header_px + r * row_px))
def _df_height(df, *, minimum=136, maximum=360) -> int:
    return _safe_height(len(df.index) if isinstance(df, pd.DataFrame) else 1,
                        minimum=minimum, maximum=maximum)
def _age_str(ts: float) -> str:
    age = _now() - ts if ts > 0 else 9999
    if age < 60:   return f"{int(age)}s"
    if age < 3600: return f"{int(age/60)}m"
    return f"{int(age/3600)}h"
def _status_color(s: str) -> str:
    s = str(s or "").upper()
    if any(x in s for x in ("ALIVE", "OK", "ACTIVE", "APPROVED", "GREEN", "CLEAN")): return C_GREEN
    if any(x in s for x in ("WARN", "DEGRADED", "STALE", "PENDING")): return C_GOLD
    if any(x in s for x in ("ERROR", "DEAD", "FAIL", "RED", "BLOCK")): return C_RED
    return C_CYAN

def _connect() -> sqlite3.Connection:
    """Open a read-only UI connection. Never negotiate journal mode in render."""
    uri = f"file:{DB_PATH.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True, timeout=8.0, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA busy_timeout=8000")
        conn.execute("PRAGMA query_only=ON")
    except Exception:
        pass
    return conn

@st.cache_data(ttl=20, show_spinner=False)
def _table_exists(table: str) -> bool:
    if not table or not table.replace("_","").isalnum(): return False
    try:
        conn = _connect()
        r = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1",
                         (table,)).fetchone()
        conn.close(); return r is not None
    except: return False

@st.cache_data(ttl=20, show_spinner=False)
def _columns(table: str) -> set[str]:
    if not _table_exists(table): return set()
    try:
        conn = _connect()
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        conn.close(); return {r[1] for r in rows}
    except: return set()

def _select(table: str, cols=None, where="", order="", limit=50,
            params=(), query_db=None) -> pd.DataFrame:
    if not _table_exists(table): return pd.DataFrame()
    available = _columns(table)
    if cols:
        col_sql = ", ".join(c for c in cols if c in available) or "*"
    else:
        col_sql = "*"
    sql = f"SELECT {col_sql} FROM {table}"
    if where: sql += f" WHERE {where}"
    if order: sql += f" ORDER BY {order}"
    sql += f" LIMIT {limit}"
    try:
        if query_db:
            return query_db(sql, params) if params else query_db(sql)
        conn = _connect()
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        if not rows: return pd.DataFrame()
        return pd.DataFrame([dict(r) for r in rows])
    except: return pd.DataFrame()

def _safe_dataframe(df: pd.DataFrame, *, key: str) -> None:
    if not isinstance(df, pd.DataFrame) or df.empty:
        st.markdown("<div class='sn-muted'>// no data //</div>", unsafe_allow_html=True)
        return
    h = _df_height(df)
    st.dataframe(df, use_container_width=True, height=h, key=key)

def _empty(msg: str) -> None:
    st.markdown(f"<div class='sn-mini'><div class='sn-muted'>// {_esc(msg)} //</div></div>",
                unsafe_allow_html=True)

def _metric_chip(label: str, value: str, color: str = C_CYAN, border: str = "") -> str:
    bc = border or f"{color}44"
    return (f"<div style='display:inline-flex;flex-direction:column;align-items:center;"
            f"padding:5px 10px;border-radius:8px;border:1px solid {bc};"
            f"background:rgba(0,0,0,0.3);margin:3px;min-width:70px;'>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:.52rem;"
            f"letter-spacing:1.2px;color:rgba(255,255,255,0.5);'>{_esc(label)}</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:.76rem;"
            f"font-weight:800;color:{color};margin-top:2px;'>{_esc(value)}</span>"
            f"</div>")

def _pnl_color(pnl: float) -> str:
    return C_GREEN if pnl > 0 else (C_RED if pnl < 0 else C_DIM)

# ── Data loaders ──────────────────────────────────────────────────────────────
@st.cache_data(ttl=30, show_spinner=False)
def _load(cache_key: str) -> dict[str, pd.DataFrame]:
    tables = {
        "roles":            ("council_agent_registry",     "agent_name ASC"),
        "tasks":            ("council_work_queue",          "created_at DESC"),
        "assignments":      ("council_model_assignments",   "assigned_at DESC"),
        "support":          ("council_support_registry",    "updated_at DESC"),
        "proposals":        ("polaris_proposals",           "created_at DESC"),
        "patterns":         ("polaris_learned_patterns",    "created_at DESC"),
        "scores":           ("runner_likelihood_scores",    "scored_at DESC"),
        "wallets":          ("wallet_entry_likelihood_signals", "signal_time DESC"),
        "strategies":       ("substrate_strategy_registry", "updated_at DESC"),
        "strategy_signals": ("substrate_strategy_signals",  "created_at DESC"),
        "strategy_results": ("substrate_strategy_results",  "created_at DESC"),
    }
    out: dict[str, pd.DataFrame] = {}
    for key, (table, order) in tables.items():
        out[key] = _select(table, order=order, limit=80)
    return out

def _load_live(query_db=None) -> dict[str, pd.DataFrame]:
    data = _load("v4_substrate_upgrade")
    return data

def _load_substrate_live() -> dict:
    """Live substrate/macro data — short TTL, not cached."""
    d = {
        "prices": {}, "nodes": {}, "positions": [],
        "closed_trades": [], "live_orders": [], "proposals": [], "has_data": False,
    }
    try:
        conn = _connect()
        for sym in SUBSTRATE_ASSETS:
            row = conn.execute(
                "SELECT price_usd, change_24h, rsi_14, bb_upper, bb_lower, bb_width, fetched_at "
                "FROM substrate_prices WHERE symbol=? ORDER BY fetched_at DESC LIMIT 1",
                (sym,)).fetchone()
            if row: d["prices"][sym] = dict(row); d["has_data"] = True
        for row in conn.execute("SELECT * FROM substrate_node_state ORDER BY symbol"):
            d["nodes"][str(row["symbol"])] = dict(row)
        # Read both known substrate position ledgers. Older paper trader builds write
        # substrate_paper_positions; newer UI builds read substrate_positions. Support both
        # so the node does not look idle while the paper engine is actually writing elsewhere.
        for _tbl, _label in (("substrate_positions", "substrate_positions"),
                             ("substrate_paper_positions", "substrate_paper_positions")):
            _open, _closed = _load_position_table(conn, _tbl, _label)
            d["positions"].extend(_open)
            d["closed_trades"].extend(_closed)
        if _table_exists("substrate_live_orders"):
            try:
                for row in conn.execute(
                    "SELECT id, opportunity_id, state, chain, asset_symbol, provider, size_usd, created_at "
                    "FROM substrate_live_orders ORDER BY created_at DESC LIMIT 250"):
                    d["live_orders"].append(dict(row)); d["has_data"] = True
            except Exception:
                pass
        for row in conn.execute(
            "SELECT proposal_text, confidence, status, created_at "
            "FROM polaris_proposals WHERE proposal_domain='SUBSTRATE' "
            "ORDER BY created_at DESC LIMIT 8"):
            d["proposals"].append(dict(row))
        conn.close()
    except: pass
    return d

def _load_copytrade_alts() -> dict:
    """Load alt-asset copytrade data from DB."""
    d = {"wallets": [], "signals": [], "positions": [], "ledger": []}
    try:
        conn = _connect()
        # Substrate copytrade wallets (separate table for alts)
        for tbl, key in [
            ("substrate_copytrade_wallets", "wallets"),
            ("substrate_copytrade_signals", "signals"),
            ("substrate_copytrade_positions", "positions"),
        ]:
            if _table_exists(tbl):
                for row in conn.execute(f"SELECT * FROM {tbl} ORDER BY rowid DESC LIMIT 30"):
                    d[key].append(dict(row))
        # Fallback: read from standard copytrade tables if substrate-specific don't exist
        if not d["wallets"] and _table_exists("wallet_entry_fingerprints"):
            for row in conn.execute(
                "SELECT wallet_address, chain, wallet_quality_score, copyability_score, "
                "median_safe_x, hit_rate_2x, late_copy_failure_rate, updated_at "
                "FROM wallet_entry_fingerprints ORDER BY wallet_quality_score DESC LIMIT 250"):
                r = dict(row); r["asset_class"] = "SOL_MEME"
                d["wallets"].append(r)
        if not d["signals"] and _table_exists("wallet_entry_likelihood_signals"):
            for row in conn.execute(
                "SELECT token_mint, signal_time, matched_wallet_count, "
                "copy_conviction_score, veto_reason, mode "
                "FROM wallet_entry_likelihood_signals "
                "ORDER BY signal_time DESC LIMIT 250"):
                d["signals"].append(dict(row))
        if _table_exists("copytrade_influence_ledger"):
            for row in conn.execute(
                "SELECT ts, token_mint, symbol, wallet_count, baseline_confidence, "
                "copytrade_bonus, final_confidence, decision, reason "
                "FROM copytrade_influence_ledger ORDER BY ts DESC LIMIT 250"):
                d["ledger"].append(dict(row))
        conn.close()
    except: pass
    return d

# ── CSS ───────────────────────────────────────────────────────────────────────
def _inject_css() -> None:
    st.markdown(f"""
        <style>
        .sn-root{{border:1px solid {C_BORDER};border-radius:16px;padding:14px 14px 16px;
            background:linear-gradient(180deg,rgba(9,2,18,.92),rgba(4,1,10,.92));
            box-shadow:0 0 28px rgba(153,69,255,.12) inset;margin-bottom:12px;}}
        .sn-title{{font-family:Orbitron,Rajdhani,sans-serif;color:{C_CYAN};font-weight:900;
            letter-spacing:7px;font-size:1.05rem;text-shadow:0 0 14px rgba(142,249,255,.55);}}
        .sn-sub{{font-family:Share Tech Mono,monospace;color:rgba(255,255,255,.45);
            font-size:.62rem;letter-spacing:2px;margin-top:5px;}}
        .sn-card{{border:1px solid {C_BORDER};border-radius:12px;padding:12px;
            background:{C_PANEL};margin:8px 0;}}
        .sn-mini{{border:1px solid rgba(153,69,255,.24);border-radius:10px;padding:10px;
            background:{C_PANEL2};margin:6px 0;}}
        .sn-h{{font-family:Share Tech Mono,monospace;color:{C_PURPLE};letter-spacing:2px;
            font-size:.72rem;font-weight:900;text-transform:uppercase;margin-bottom:6px;}}
        .sn-copy{{font-family:Rajdhani,sans-serif;color:rgba(230,242,255,.76);
            font-size:.84rem;line-height:1.38;letter-spacing:.015em;}}
        .sn-pill{{display:inline-flex;align-items:center;gap:6px;padding:3px 8px;
            margin:2px 3px 2px 0;border-radius:999px;border:1px solid rgba(153,69,255,.35);
            color:{C_DIM};font-family:Share Tech Mono,monospace;font-size:.61rem;
            letter-spacing:1px;background:rgba(153,69,255,.08);}}
        .sn-muted{{color:rgba(220,232,255,.54);font-family:Rajdhani,sans-serif;font-size:.76rem;line-height:1.32;letter-spacing:.02em;}}
        .sn-ok{{color:{C_GREEN};}} .sn-warn{{color:{C_GOLD};}} .sn-bad{{color:{C_RED};}}
        .sn-trade-row{{display:flex;align-items:center;gap:8px;padding:5px 8px;
            border-radius:7px;margin:3px 0;font-family:Share Tech Mono,monospace;font-size:.62rem;}}
        .sn-buy{{background:rgba(20,241,149,0.07);border-left:2px solid {C_GREEN};}}
        .sn-sell{{background:rgba(255,7,58,0.07);border-left:2px solid {C_RED};}}
        .sn-neutral{{background:rgba(153,69,255,0.05);border-left:2px solid {C_PURPLE};}}
        .sn-balance-bar{{display:flex;gap:10px;flex-wrap:wrap;padding:8px 12px;
            border:1px solid rgba(153,69,255,0.2);border-radius:10px;
            background:rgba(5,2,16,0.6);margin-bottom:10px;}}
        .sn-meter-wrap{{border:1px solid rgba(142,249,255,.24);border-radius:12px;padding:10px 12px;
            background:linear-gradient(135deg,rgba(142,249,255,.055),rgba(153,69,255,.075));margin:8px 0;}}
        .sn-meter-track{{height:8px;border-radius:999px;background:rgba(255,255,255,.055);overflow:hidden;
            border:1px solid rgba(255,255,255,.08);}}
        .sn-meter-fill{{height:100%;border-radius:999px;background:linear-gradient(90deg,{C_PURPLE},{C_CYAN},{C_GREEN});
            box-shadow:0 0 16px rgba(142,249,255,.25);}}
        .sn-feed-title{{display:flex;align-items:center;justify-content:space-between;gap:8px;
            font-family:Share Tech Mono,monospace;color:{C_CYAN};font-size:.68rem;letter-spacing:2px;
            text-transform:uppercase;margin:9px 0 5px;}}
        .sn-asset-node{{border:1px solid rgba(153,69,255,0.3);border-radius:10px;
            padding:8px 10px;min-width:90px;background:rgba(9,2,18,0.8);text-align:center;}}
        .sn-council-gate{{border:1px solid;border-radius:8px;padding:6px 10px;
            margin:4px 0;font-family:Share Tech Mono,monospace;font-size:.62rem;
            display:flex;align-items:center;gap:8px;}}
        .sn-ct-wallet{{border:1px solid rgba(255,215,0,0.3);border-radius:9px;
            padding:7px 10px;margin:4px 0;background:rgba(255,215,0,0.03);}}
        @media(max-width:760px){{
            .sn-title{{font-size:.83rem;letter-spacing:4px;}}
            .sn-sub{{font-size:.55rem;letter-spacing:1px;}}
            .sn-card,.sn-mini{{padding:9px;}}
        }}
        </style>""", unsafe_allow_html=True)

# ── Header ────────────────────────────────────────────────────────────────────
def _header(data: dict) -> None:
    _inject_css()
    st.markdown(
        f"<div class='sn-root'>"
        f"<div class='sn-title'>◈ SUBSTRATE NODE</div>"
        f"<div class='sn-sub'>SIX-NODE COUNCIL · ALTS + NATIVES · PAPER + MANUAL-SIGN LIVE GATE</div>"
        f"</div>",
        unsafe_allow_html=True,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Substrate execution strip + feed helpers (read-only UI)
# ─────────────────────────────────────────────────────────────────────────────
def _row_symbol(row: dict) -> str:
    for k in ("symbol", "asset", "token_symbol", "token_name", "mint", "token_mint"):
        v = row.get(k)
        if v is not None and str(v).strip().lower() not in ("", "none", "null", "n/a", "na", "unknown"):
            t = str(v).strip()
            return t if len(t) <= 18 else f"{t[:6]}…{t[-5:]}"
    return "—"

def _load_position_table(conn: sqlite3.Connection, table: str, source_label: str) -> tuple[list[dict], list[dict]]:
    """Read OPEN/CLOSED rows from either substrate_positions or substrate_paper_positions."""
    open_rows, closed_rows = [], []
    if not _table_exists(table):
        return open_rows, closed_rows
    cols = _columns(table)
    wanted = [
        "id", "position_id", "symbol", "asset", "asset_symbol", "token_symbol", "token_name", "side", "mode",
        "entry_price", "entry_price_usd", "exit_price", "current_price", "last_price", "position_size", "size_usd", "qty",
        "unrealized_pnl", "unrealized_pnl_usd", "pnl_usd", "pnl_pct", "realized_pnl", "realized_pnl_usd",
        "opened_at", "closed_at", "last_price_at", "status", "state", "source", "price_source", "exit_reason",
        "tp_pct", "sl_pct", "max_hold_sec", "peak_price", "target_id",
    ]
    sel = [c for c in wanted if c in cols]
    if not sel:
        return open_rows, closed_rows
    order_col = "opened_at" if "opened_at" in cols else ("created_at" if "created_at" in cols else "rowid")
    try:
        rows = conn.execute(f"SELECT {','.join(sel)} FROM {table} ORDER BY {order_col} DESC LIMIT 80").fetchall()
    except Exception:
        return open_rows, closed_rows
    for row in rows:
        r = dict(row)
        r.setdefault("mode", "paper" if "paper" in table else str(r.get("mode") or "paper"))
        if not r.get("symbol") and r.get("asset_symbol"):
            r["symbol"] = r.get("asset_symbol")
        if not r.get("entry_price") and r.get("entry_price_usd"):
            r["entry_price"] = r.get("entry_price_usd")
        if not r.get("current_price") and r.get("last_price"):
            r["current_price"] = r.get("last_price")
        if not r.get("position_size") and r.get("size_usd"):
            r["position_size"] = r.get("size_usd")
        if not r.get("unrealized_pnl") and r.get("pnl_usd"):
            r["unrealized_pnl"] = r.get("pnl_usd")
        if not r.get("realized_pnl") and r.get("pnl_usd") and str(r.get("status") or "").upper() != "OPEN":
            r["realized_pnl"] = r.get("pnl_usd")
        r["source_table"] = source_label
        status = str(r.get("status") or r.get("state") or "").upper()
        # Do not turn placeholders/proposals into fake closed rows. Only completed statuses are closed.
        if status == "OPEN":
            open_rows.append(r)
        elif status in ("CLOSED", "EXITED", "COMPLETE", "COMPLETED", "CUT", "TAKE_PROFIT", "STOP_LOSS"):
            # keep only rows with at least an honest symbol or non-zero realized PnL/close time
            if _row_symbol(r) != "—" or _num(r.get("realized_pnl")) or _num(r.get("realized_pnl_usd")) or _num(r.get("closed_at")):
                closed_rows.append(r)
    return open_rows, closed_rows

def _render_substrate_execution_meter(sd: dict, n_paper: int, n_live: int) -> None:
    prices = sd.get("prices", {})
    nodes = sd.get("nodes", {})
    now = _now()
    fresh = 0
    expanding = 0
    compressing = 0
    ranked = 0
    edge_scores = []
    for sym in SUBSTRATE_ASSETS:
        p = prices.get(sym, {})
        node = nodes.get(sym, {})
        age = now - _num(p.get("fetched_at"), 0)
        if p and age <= 180:
            fresh += 1
        state = str(node.get("state", "")).upper()
        if state == "EXPANDING":
            expanding += 1
        if state == "COMPRESSING":
            compressing += 1
        if bool(node.get("council_ranked", 0)):
            ranked += 1
        edge_scores.append(_num(node.get("edge_score"), 0))
    readiness = min(100, int((fresh / max(1, len(SUBSTRATE_ASSETS))) * 45 + min(2, compressing + expanding) * 18 + min(2, ranked) * 9 + min(1, max(edge_scores or [0])) * 10))
    status = "PAPER SCANNING" if readiness < 60 else ("PAPER READY" if n_live == 0 else "LIVE SHADOWING")
    chips = [
        _metric_chip("fresh prices", f"{fresh}/{len(SUBSTRATE_ASSETS)}", C_GREEN if fresh >= 4 else C_GOLD),
        _metric_chip("compression", str(compressing), C_PURPLE),
        _metric_chip("expanding", str(expanding), C_GOLD if expanding else C_DIM),
        _metric_chip("ranked", str(ranked), C_CYAN),
        _metric_chip("open paper", str(n_paper), C_CYAN),
    ]
    st.markdown(
        f"<div class='sn-meter-wrap'>"
        f"<div class='sn-feed-title'><span>SUBSTRATE EXECUTION METER</span><span style='color:{C_GREEN if readiness >= 60 else C_GOLD};'>{status} · {readiness}%</span></div>"
        f"<div class='sn-meter-track'><div class='sn-meter-fill' style='width:{max(4, readiness)}%;'></div></div>"
        f"<div style='display:flex;flex-wrap:wrap;gap:4px;margin-top:8px;'>" + "".join(chips) + "</div>"
        f"<div class='sn-muted' style='margin-top:5px;'>Meter is display-only: fresh macro prices + compression/expansion + council ranking + current paper slot usage.</div>"
        f"</div>",
        unsafe_allow_html=True,
    )

def _render_substrate_execution_feed(sd: dict) -> None:
    open_pos = sd.get("positions", []) or []
    closed_pos = sd.get("closed_trades", []) or []
    proposals = sd.get("proposals", []) or []
    live_orders = sd.get("live_orders", []) or []
    rows = []
    for o in live_orders[:8]:
        sym = str(o.get("asset_symbol") or "SUB")
        state = str(o.get("state") or "READY").upper()
        provider = str(o.get("provider") or "wallet")
        size = _num(o.get("size_usd"))
        detail = f"{state} · {provider} · {str(o.get('chain') or '').upper()}"
        rows.append(("LIVE", "LIVE", sym, "SIGN", detail, f"${size:.2f}", C_GOLD, _num(o.get("created_at"))))
    for p in open_pos[:8]:
        sym = _row_symbol(p)
        mode = str(p.get("mode", "PAPER")).upper()
        side = str(p.get("side", "LONG")).upper()
        entry = _num(p.get("entry_price"))
        cur = _num(p.get("current_price"), entry)
        upnl = _num(p.get("unrealized_pnl"), _num(p.get("unrealized_pnl_usd")))
        rows.append(("OPEN", mode, sym, side, f"@ ${entry:.6g} → ${cur:.6g}", f"{upnl:+.2f} USD", _pnl_color(upnl), _num(p.get("opened_at"))))
    for p in closed_pos[:8]:
        sym = _row_symbol(p)
        if sym == "—":
            continue
        mode = str(p.get("mode", "PAPER")).upper()
        side = str(p.get("side", "LONG")).upper()
        rpnl = _num(p.get("realized_pnl"), _num(p.get("realized_pnl_usd")))
        reason = str(p.get("exit_reason") or p.get("status") or "CLOSED").upper()
        rows.append(("CLOSE", mode, sym, side, reason, f"{rpnl:+.2f} USD", _pnl_color(rpnl), _num(p.get("closed_at"))))
    if not rows and proposals:
        for p in proposals[:4]:
            txt = _short(p.get("proposal_text", "SUBSTRATE SIGNAL"), 54)
            conf = _num(p.get("confidence"))
            status = str(p.get("status", "PROPOSED")).upper()
            rows.append(("SIGNAL", "PAPER", "SUB", status, txt, f"conf {conf:.2f}", C_GOLD if conf >= .6 else C_CYAN, _num(p.get("created_at"))))
    rows.sort(key=lambda x: x[-1] or 0, reverse=True)
    st.markdown(f"<div class='sn-feed-title'><span>BUY / SELL / PAPER FEED</span><span>{len(rows)} visible</span></div>", unsafe_allow_html=True)
    if not rows:
        st.markdown(
            f"<div class='sn-mini'><div class='sn-muted'>No Substrate paper execution rows yet. This should change once substrate_paper_trader is alive and promotes proposed targets into OPEN paper positions.</div></div>",
            unsafe_allow_html=True,
        )
        return
    for kind, mode, sym, side, detail, pnl, col, ts in rows[:12]:
        cls = "sn-buy" if kind in ("OPEN", "SIGNAL") and col != C_RED else "sn-sell" if kind == "CLOSE" and col == C_RED else "sn-neutral"
        mode_col = C_GOLD if mode == "LIVE" else C_CYAN
        st.markdown(
            f"<div class='sn-trade-row {cls}'>"
            f"<span style='color:{C_GREEN if kind=='OPEN' else C_RED if kind=='CLOSE' else C_GOLD};min-width:52px;font-weight:800;'>{kind}</span>"
            f"<span style='color:{mode_col};min-width:44px;'>{mode}</span>"
            f"<span style='color:{C_CYAN};min-width:46px;font-weight:800;'>{_esc(sym)}</span>"
            f"<span style='color:{C_AMBER if side == 'LONG' else C_PURPLE};min-width:40px;'>{_esc(side)}</span>"
            f"<span style='color:rgba(230,242,255,.72);'>{_esc(detail)}</span>"
            f"<span style='color:{col};margin-left:auto;font-weight:800;'>{_esc(pnl)}</span>"
            f"<span style='color:{C_DIM};min-width:42px;text-align:right;'>{_age_str(ts)}</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

# ─────────────────────────────────────────────────────────────────────────────
# TAB: MACRO TRADE BOOK
# ─────────────────────────────────────────────────────────────────────────────
def _render_macro_trade_book() -> None:
    """Live paper + live buy/sell feed for substrate assets. Balance bar + trade rows."""
    sd = _load_substrate_live()
    now = _now()

    # ── Balance bar ───────────────────────────────────────────────────────────
    open_pos   = sd["positions"]
    closed_pos = sd["closed_trades"]
    paper_pnl  = sum(_num(p.get("unrealized_pnl")) for p in open_pos
                     if str(p.get("mode","")).lower() in ("paper","sim",""))
    realized   = sum(_num(p.get("realized_pnl")) for p in closed_pos
                     if str(p.get("mode","")).lower() in ("paper","sim",""))
    live_pnl   = sum(_num(p.get("unrealized_pnl")) for p in open_pos
                     if str(p.get("mode","")).lower() == "live")
    n_paper = sum(1 for p in open_pos if str(p.get("mode","")).lower() in ("paper","sim",""))
    n_live  = sum(1 for p in open_pos if str(p.get("mode","")).lower() == "live")
    n_live_orders = len(sd.get("live_orders", []) or [])

    chips = [
        _metric_chip("PAPER OPEN", str(n_paper), C_CYAN),
        _metric_chip("PAPER P&L", f"${paper_pnl:+.2f}", _pnl_color(paper_pnl)),
        _metric_chip("REALIZED", f"${realized:+.2f}", _pnl_color(realized)),
        _metric_chip("LIVE OPEN", str(n_live), C_GOLD if n_live > 0 else C_DIM),
        _metric_chip("LIVE P&L", f"${live_pnl:+.2f}", _pnl_color(live_pnl)),
        _metric_chip("LIVE ORDERS", str(n_live_orders), C_GOLD if n_live_orders else C_DIM),
    ]
    st.markdown(
        f"<div class='sn-card'><div class='sn-h'>SUBSTRATE BALANCE — PAPER ALWAYS LEARNING UNDERNEATH</div>"
        f"<div style='display:flex;flex-wrap:wrap;gap:4px;margin-bottom:6px;'>"
        + "".join(chips) +
        f"</div>"
        f"<div class='sn-muted'>Paper runs continuously under all market conditions. "
        f"Live gates stage manual-sign orders only: council sign-off + provider quorum + wallet gate; autosend remains off.</div>"
        f"</div>", unsafe_allow_html=True)

    # ── Execution meter + cadence + feed (Solana parity, read-only) ───────────
    _render_substrate_execution_meter(sd, n_paper, n_live)
    try:
        from ui.glass_cadence_chart import render_glass_cadence as _render_glass_cadence
        _render_glass_cadence(
            str(DB_PATH),
            table="substrate_paper_positions" if _table_exists("substrate_paper_positions") else "substrate_positions",
            key_prefix="sub",
            st=st,
            empty_label="No closed Substrate paper trades yet",
        )
    except Exception as _cadence_err:
        st.caption(f"Substrate cadence unavailable: {type(_cadence_err).__name__}: {_cadence_err}")
    _render_substrate_execution_feed(sd)

    # ── Asset price row ───────────────────────────────────────────────────────
    st.markdown("<div class='sn-h'>ASSET PRICES — MACRO LAYER</div>", unsafe_allow_html=True)
    price_cols = st.columns(len(SUBSTRATE_ASSETS), gap="small")
    for i, sym in enumerate(SUBSTRATE_ASSETS):
        p = sd["prices"].get(sym, {})
        node = sd["nodes"].get(sym, {})
        price = _num(p.get("price_usd"))
        chg   = _num(p.get("change_24h"))
        rsi   = _num(p.get("rsi_14"), 50)
        state = str(node.get("state", "NO_DATA"))
        score = _num(node.get("edge_score"))
        ranked = bool(node.get("council_ranked", 0))
        age   = _num(p.get("fetched_at"))

        state_colors = {"EXPANDING": C_GOLD, "COMPRESSING": C_PURPLE,
                        "DEBATING": C_PURPLE, "SUSTAINING": C_AMBER,
                        "WATCHING": C_CYAN, "NO_DATA": C_DIM}
        sc = state_colors.get(state, C_DIM)
        tier_badge = "T1" if ASSET_TIER.get(sym) == 1 else "T2"
        ranked_badge = f"<span style='color:{C_GOLD};font-size:.55rem;'>★RANKED</span>" if ranked else ""
        chg_col = C_GREEN if chg >= 0 else C_RED
        price_str = (f"${price:,.0f}" if price > 100 else
                     f"${price:,.4f}" if price < 1 else f"${price:,.2f}") if price else "—"

        with price_cols[i]:
            st.markdown(
                f"<div class='sn-asset-node' style='border-color:{sc}44;'>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:.9rem;"
                f"color:{sc};font-weight:900;'>{ASSET_ICONS.get(sym,'◈')}</div>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:.65rem;"
                f"color:{C_CYAN};letter-spacing:1px;'>{sym} <span style='color:{C_DIM};font-size:.5rem;'>{tier_badge}</span></div>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:.7rem;"
                f"color:#fff;margin:3px 0;'>{price_str}</div>"
                f"<div style='font-size:.58rem;color:{chg_col};font-family:Share Tech Mono,monospace;'>"
                f"{chg:+.2f}% 24h</div>"
                f"<div style='font-size:.54rem;color:{sc};font-family:Share Tech Mono,monospace;"
                f"margin-top:3px;letter-spacing:1px;'>{state}</div>"
                f"<div style='font-size:.52rem;color:{C_DIM};font-family:Share Tech Mono,monospace;'>"
                f"RSI {rsi:.0f} · edge {score:.2f}</div>"
                f"{ranked_badge}"
                f"</div>", unsafe_allow_html=True)

    # ── Exit authority / expected capture window ──────────────────────────────
    st.markdown(
        f"<div class='sn-card' style='border-color:rgba(255,215,0,.22);'>"
        f"<div class='sn-h' style='color:{C_GOLD};'>EXIT AUTHORITY · THESIS ENVELOPE</div>"
        f"<div class='sn-muted'>Paper positions are sell-capable through the substrate paper trader: "
        f"take-profit, stop-loss and maximum-hold exits are evaluated on every fresh mark. "
        f"Live positions remain manual-sign only. Each open row below exposes its expected capture window, "
        f"mark freshness and an OVERDUE state when an offline service or unavailable price prevented timely evaluation.</div>"
        f"</div>", unsafe_allow_html=True)

    # ── Open positions ────────────────────────────────────────────────────────
    st.markdown("<div style='margin-top:12px;'></div>", unsafe_allow_html=True)
    st.markdown("<div class='sn-h'>OPEN POSITIONS</div>", unsafe_allow_html=True)
    if open_pos:
        for p in open_pos:
            sym = _row_symbol(p)
            side = str(p.get("side", "LONG")).upper()
            mode = str(p.get("mode", "PAPER")).upper()
            entry = _num(p.get("entry_price"), _num(p.get("entry_price_usd")))
            cur   = _num(p.get("current_price"), _num(p.get("last_price"), entry))
            upnl  = _num(p.get("unrealized_pnl"), _num(p.get("unrealized_pnl_usd"), _num(p.get("pnl_usd"))))
            size  = _num(p.get("position_size"), _num(p.get("size_usd")))
            src   = str(p.get("source", p.get("price_source", "")))
            opened = _num(p.get("opened_at"))
            pnl_pct = (cur - entry) / entry * 100 if entry > 0 else 0
            row_cls = "sn-buy" if side == "LONG" else "sn-sell"
            mode_col = C_GOLD if mode == "LIVE" else C_CYAN
            pnl_col  = _pnl_color(upnl)
            max_hold = _num(p.get("max_hold_sec"))
            age_sec = max(0.0, now - opened) if opened else 0.0
            remaining = max_hold - age_sec if max_hold > 0 else 0.0
            last_mark = _num(p.get("last_price_at"))
            mark_age = max(0.0, now - last_mark) if last_mark else 999999.0
            overdue = bool(max_hold > 0 and remaining <= 0)
            offline_risk = bool(mark_age > 300)
            horizon = (f"due in {int(remaining//60)}m" if max_hold > 0 and remaining > 0
                       else f"OVERDUE {int(abs(remaining)//60)}m" if overdue else "adaptive")
            exit_col = C_RED if overdue or offline_risk else C_GREEN
            tp = _num(p.get("tp_pct")); sl = _num(p.get("sl_pct"))
            exit_contract = f"TP {tp:.1f}% · SL {sl:.1f}% · {horizon}"
            freshness = f"mark {int(mark_age)}s" if mark_age < 999999 else "mark unavailable"
            st.markdown(
                f"<div class='sn-trade-row {row_cls}' style='flex-wrap:wrap;'>"
                f"<span style='color:{mode_col};min-width:44px;'>{mode}</span>"
                f"<span style='color:{C_CYAN};min-width:36px;font-weight:800;'>{sym}</span>"
                f"<span style='color:{C_AMBER if side == 'LONG' else C_RED};min-width:36px;'>{side}</span>"
                f"<span style='color:#999;'>@ ${entry:.6g}</span>"
                f"<span style='color:#ccc;'>→ ${cur:.6g}</span>"
                f"<span style='color:{pnl_col};margin-left:auto;'>{upnl:+.2f} USD ({pnl_pct:+.1f}%)</span>"
                f"<span style='color:{C_DIM};min-width:55px;text-align:right;'>${size:.0f} pos</span>"
                f"<span style='color:{C_DIM};min-width:40px;text-align:right;'>{_age_str(opened)}</span>"
                f"<span style='flex-basis:100%;height:0;'></span>"
                f"<span style='font-size:.52rem;color:{exit_col};margin-left:80px;'>AUTO EXIT · {exit_contract}</span>"
                f"<span style='font-size:.52rem;color:{C_RED if offline_risk else C_DIM};margin-left:auto;'>{freshness}</span>"
                f"</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='sn-mini'><div class='sn-muted'>"
            f"No open substrate positions. Paper engine runs continuously — "
            f"positions open when BB compression + RSI structure + breakout align "
            f"AND council ranking clears at least 1 of the top-2 slots.</div></div>",
            unsafe_allow_html=True)

    # ── Recent closed trades ──────────────────────────────────────────────────
    if closed_pos:
        with st.expander(f"📋 Recent closed trades ({len(closed_pos)})", expanded=False):
            for p in closed_pos[:15]:
                sym  = _row_symbol(p)
                side = str(p.get("side", "LONG")).upper()
                mode = str(p.get("mode", "PAPER")).upper()
                rpnl = _num(p.get("realized_pnl"), _num(p.get("realized_pnl_usd"), _num(p.get("pnl_usd"))))
                pnl_col = _pnl_color(rpnl)
                mode_col = C_GOLD if mode == "LIVE" else C_CYAN
                cls = "sn-buy" if rpnl >= 0 else "sn-sell"
                opened = _num(p.get("opened_at"))
                closed = _num(p.get("closed_at"))
                held = (closed - opened) if closed > opened else 0
                st.markdown(
                    f"<div class='sn-trade-row {cls}'>"
                    f"<span style='color:{mode_col};min-width:44px;'>{mode}</span>"
                    f"<span style='color:{C_CYAN};min-width:36px;font-weight:800;'>{sym}</span>"
                    f"<span style='color:{C_AMBER if side == 'LONG' else C_RED};'>{side}</span>"
                    f"<span style='color:{pnl_col};margin-left:auto;font-weight:700;'>{rpnl:+.2f} USD</span>"
                    f"<span style='color:{C_DIM};min-width:40px;text-align:right;'>"
                    f"held {int(held//60)}m</span>"
                    f"</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB: COUNCIL DECISION ENGINE
# ─────────────────────────────────────────────────────────────────────────────
def _render_council_decision() -> None:
    """The 6 verifications the council must complete before capital is deployed."""
    sd  = _load_substrate_live()
    now = _now()

    st.markdown(
        f"<div class='sn-card'>"
        f"<div class='sn-h'>HOW THE COUNCIL DECIDES — CAPITAL DEPLOYMENT GATE</div>"
        f"<div class='sn-copy'>All six nodes must reach consensus before the substrate engine "
        f"stages a live manual-sign order. Paper positions open freely to build outcome history. "
        f"Each gate maps to a real DB field — no gate can be fabricated.</div>"
        f"</div>", unsafe_allow_html=True)

    # Get live state for gate evaluation
    gate_data = {}
    try:
        conn = _connect()
        # Check BB compression
        for sym in SUBSTRATE_ASSETS:
            p = sd["prices"].get(sym, {})
            n = sd["nodes"].get(sym, {})
            gate_data[sym] = {
                "rsi": _num(p.get("rsi_14"), 50),
                "bb_width": _num(p.get("bb_width")),
                "state": str(n.get("state", "WATCHING")),
                "edge_score": _num(n.get("edge_score")),
                "council_ranked": bool(n.get("council_ranked", 0)),
            }
        conn.close()
    except: pass

    # The six council gates
    GATES = [
        {
            "id": "G1", "node": "ORACLE", "emoji": "🌐",
            "name": "MARKET STRUCTURE CONFIRMED",
            "description": "Bollinger Band width < 4% on at least one tracked asset. "
                           "Compression = consolidation range. Without this, there is no setup.",
            "indicators": ["BB width %", "price vs BB midline", "24h volatility"],
            "check_key": "bb_width", "threshold": 4.0, "below": True,
        },
        {
            "id": "G2", "node": "AXON", "emoji": "⚡",
            "name": "RSI STRUCTURE AT EXTREME",
            "description": "RSI(14) below 35 (oversold) or above 65 (overbought) "
                           "confirms directional momentum before breakout. Neutral RSI = no edge.",
            "indicators": ["RSI(14)", "RSI divergence", "momentum trend"],
            "check_key": "rsi", "threshold_lo": 35.0, "threshold_hi": 65.0,
        },
        {
            "id": "G3", "node": "POLARIS", "emoji": "❄️",
            "name": "BREAKOUT TRIGGER FIRED",
            "description": "Price broke outside BB by ≥1.5%. This is the entry signal — "
                           "not the compression itself. EXPANDING state required.",
            "indicators": ["BB breakout %", "candle close vs BB", "volume on breakout"],
            "state_required": "EXPANDING",
        },
        {
            "id": "G4", "node": "IVARIS", "emoji": "🔥",
            "name": "COUNCIL RANKING CLEARED",
            "description": "Asset must be in top-2 by edge score from COMPRESSING or "
                           "EXPANDING assets. Only top-2 receive capital. All others remain WATCHING.",
            "indicators": ["edge score rank", "council_ranked flag", "competing setups"],
            "ranked_required": True,
        },
        {
            "id": "G5", "node": "NUGGET", "emoji": "🔮",
            "name": "PROVIDER QUORUM HEALTHY",
            "description": "Price source must have delivered fresh data in last 120s. "
                           "Stale prices block entry. No 429s from price APIs in last 5 min.",
            "indicators": ["price_age", "DexScreener/CoinGecko 429s", "last_success"],
            "always_manual": True,
        },
        {
            "id": "G6", "node": "RHIZA", "emoji": "🕸️",
            "name": "REGIME MEMORY SUPPORTS",
            "description": "Historical breakouts of this compression type on this asset "
                           "must have ≥40% win rate in regime memory. Regime memory builds from closed trades.",
            "indicators": ["historical win rate", "compression type match", "outcome count"],
            "always_manual": True,
        },
    ]

    for g in GATES:
        # Evaluate gate
        passed = None
        detail = ""
        sym_best = ""

        if g.get("check_key") == "bb_width":
            best_sym = min(gate_data.items(), key=lambda x: x[1]["bb_width"] if x[1]["bb_width"] > 0 else 99, default=(None, {}))
            if best_sym[0]:
                bw = best_sym[1]["bb_width"]
                passed = bw > 0 and bw < g["threshold"]
                sym_best = best_sym[0]
                detail = f"{sym_best} BB width = {bw:.2f}% (threshold < {g['threshold']}%)"
        elif g.get("check_key") == "rsi":
            extremes = [(sym, d["rsi"]) for sym, d in gate_data.items()
                        if d["rsi"] < g["threshold_lo"] or d["rsi"] > g["threshold_hi"]]
            if extremes:
                sym_best, rsi_val = extremes[0]
                passed = True
                pos = "oversold" if rsi_val < g["threshold_lo"] else "overbought"
                detail = f"{sym_best} RSI = {rsi_val:.1f} ({pos})"
            else:
                passed = False
                all_rsis = ", ".join(f"{s}:{d['rsi']:.0f}" for s, d in gate_data.items())
                detail = f"No extreme RSI — all neutral ({all_rsis})"
        elif g.get("state_required") == "EXPANDING":
            expanding = [s for s, d in gate_data.items() if d["state"] == "EXPANDING"]
            passed = bool(expanding)
            detail = f"EXPANDING: {', '.join(expanding)}" if expanding else "No asset in EXPANDING state"
        elif g.get("ranked_required"):
            ranked = [s for s, d in gate_data.items() if d["council_ranked"]]
            passed = bool(ranked)
            detail = f"Council-ranked: {', '.join(ranked)}" if ranked else "No asset ranked — all below edge threshold"
        else:
            passed = None  # manual / unknown
            detail = "Requires live provider/history check"

        gate_col = C_GREEN if passed is True else (C_RED if passed is False else C_GOLD)
        gate_sym = "✓" if passed is True else ("✗" if passed is False else "?")
        gate_cls = "sn-ok" if passed is True else ("sn-bad" if passed is False else "sn-warn")

        st.markdown(
            f"<div class='sn-council-gate' style='border-color:{gate_col}55;background:rgba(0,0,0,0.2);'>"
            f"<span style='font-size:1rem;min-width:28px;'>{g['emoji']}</span>"
            f"<span style='color:{gate_col};font-weight:900;min-width:26px;font-size:.8rem;'>{gate_sym}</span>"
            f"<div style='flex:1;'>"
            f"<div style='color:{gate_col};font-size:.64rem;letter-spacing:1px;font-weight:700;'>"
            f"{g['id']} · {g['node']} · {g['name']}</div>"
            f"<div style='color:rgba(255,255,255,0.55);font-size:.59rem;margin-top:2px;'>{g['description']}</div>"
            f"<div style='color:{gate_col};font-size:.58rem;margin-top:3px;font-style:italic;'>{detail}</div>"
            f"<div style='margin-top:3px;'>"
            + "".join(f"<span class='sn-pill'>{_esc(ind)}</span>" for ind in g["indicators"])
            + "</div></div></div>", unsafe_allow_html=True)

    # Council proposals
    if sd["proposals"]:
        st.markdown("<div class='sn-h' style='margin-top:12px;'>RECENT COUNCIL PROPOSALS</div>",
                    unsafe_allow_html=True)
        for prop in sd["proposals"][:5]:
            status = str(prop.get("status", "open"))
            conf   = _num(prop.get("confidence"))
            sc     = C_GREEN if status == "approved" else (C_RED if status == "rejected" else C_PURPLE)
            st.markdown(
                f"<div class='sn-mini' style='border-left:2px solid {sc};'>"
                f"<div style='font-family:Share Tech Mono,monospace;font-size:.58rem;color:{sc};'>"
                f"[{status.upper()}] conf={conf:.0%}</div>"
                f"<div class='sn-copy' style='font-size:.72rem;'>{_esc(_short(prop.get('proposal_text',''),120))}</div>"
                f"</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# TAB: COPY TRADE STATION (Alts & Natives)
# ─────────────────────────────────────────────────────────────────────────────
def _render_copytrade_station() -> None:
    """Standalone alt-asset copy trade lane — wallet monitoring, signals, ledger."""
    ct = _load_copytrade_alts()
    now = _now()

    st.markdown(
        f"<div class='sn-card'>"
        f"<div class='sn-h'>COPY TRADE STATION — ALTS + NATIVES</div>"
        f"<div class='sn-copy'>Observes profiled smart wallets across BTC, ETH, SOL and alt assets. "
        f"Paper-influence only until conviction is proven over ≥50 calibration trades. "
        f"Live copy never fires without explicit operator gate. "
        f"Separate lane from the pump.fun meme scanner — different wallet profiles, "
        f"different conviction thresholds, different position sizing.</div>"
        f"</div>", unsafe_allow_html=True)

    # ── Lane status ───────────────────────────────────────────────────────────
    n_wallets  = len(ct["wallets"])
    n_signals  = len(ct["signals"])
    n_ledger   = len(ct["ledger"])
    has_bonus  = sum(1 for r in ct["ledger"] if str(r.get("decision","")) == "BONUS_APPLIED")

    try:
        from services.copytrade_influence import get_lane_state as _ct_lane
        ls = _ct_lane()
    except:
        try:
            from copytrade_influence import get_lane_state as _ct_lane
            ls = _ct_lane()
        except:
            ls = {"state": "DISABLED_CONFIG_MISSING", "detail": "module not found",
                  "live_influence": "OFF"}

    lane_state = str(ls.get("state", "UNKNOWN"))
    lane_detail = str(ls.get("detail", ""))
    lane_col = {"PAPER_BONUS_ELIGIBLE": C_GOLD, "PAPER_SHADOW_READY": C_GREEN,
                "OBSERVING": C_CYAN, "LIVE_OBSERVE_ONLY": C_PURPLE,
                "NO_DATA": C_AMBER, "NO_WALLETS": C_AMBER}.get(lane_state, C_RED)

    chips = [
        _metric_chip("LANE STATE", lane_state, lane_col),
        _metric_chip("WALLETS TRACKED", str(n_wallets), C_CYAN),
        _metric_chip("RECENT SIGNALS", str(n_signals), C_GREEN),
        _metric_chip("LEDGER ROWS", str(n_ledger), C_PURPLE),
        _metric_chip("BONUSES APPLIED", str(has_bonus), C_GOLD if has_bonus > 0 else C_DIM),
        _metric_chip("LIVE INFLUENCE", ls.get("live_influence", "OFF"), C_RED),
    ]
    st.markdown(
        f"<div style='display:flex;flex-wrap:wrap;gap:4px;margin-bottom:8px;'>"
        + "".join(chips) + "</div>"
        f"<div class='sn-muted'>{_esc(lane_detail)}</div>",
        unsafe_allow_html=True)

    col_w, col_s = st.columns(2, gap="medium")

    # ── Wallet roster ─────────────────────────────────────────────────────────
    with col_w:
        st.markdown("<div class='sn-h'>TRACKED WALLETS</div>", unsafe_allow_html=True)
        if ct["wallets"]:
            for w in ct["wallets"][:12]:
                addr = str(w.get("wallet_address", "?"))[:20]
                chain = str(w.get("chain", "sol"))
                quality = _num(w.get("wallet_quality_score"))
                copy_score = _num(w.get("copyability_score"))
                median_x = _num(w.get("median_safe_x"))
                hit2x = _num(w.get("hit_rate_2x"))
                asset_class = str(w.get("asset_class", "SOL_MEME"))
                updated = _num(w.get("updated_at"))

                q_col = C_GREEN if quality >= 70 else (C_GOLD if quality >= 50 else C_RED)
                q_grade = "ELITE" if quality >= 80 else ("PASS" if quality >= 50 else "SUB-ELITE")

                st.markdown(
                    f"<div class='sn-ct-wallet'>"
                    f"<div style='display:flex;align-items:center;gap:6px;margin-bottom:4px;'>"
                    f"<span style='font-family:Share Tech Mono,monospace;font-size:.62rem;"
                    f"color:{C_CYAN};'>{addr}…</span>"
                    f"<span class='sn-pill' style='color:{q_col};border-color:{q_col}44;'>{q_grade}</span>"
                    f"<span class='sn-pill'>{chain.upper()}</span>"
                    f"<span class='sn-pill'>{asset_class}</span>"
                    f"<span style='margin-left:auto;font-family:Share Tech Mono,monospace;"
                    f"font-size:.56rem;color:{C_DIM};'>{_age_str(updated)} ago</span>"
                    f"</div>"
                    f"<div style='display:flex;gap:6px;'>"
                    f"<span class='sn-pill'>quality {quality:.0f}</span>"
                    f"<span class='sn-pill'>copy {copy_score:.0f}</span>"
                    f"<span class='sn-pill'>median {median_x:.1f}x</span>"
                    f"<span class='sn-pill'>2x rate {hit2x:.0%}</span>"
                    f"</div></div>", unsafe_allow_html=True)
        else:
            st.markdown(
                f"<div class='sn-mini' style='border-color:{C_AMBER}44;'>"
                f"<div class='sn-h' style='color:{C_AMBER};'>NO WALLETS CONFIGURED</div>"
                f"<div class='sn-copy'>To activate copy trading:</div>"
                f"<ol style='font-family:Share Tech Mono,monospace;font-size:.62rem;"
                f"color:rgba(255,255,255,0.6);margin:6px 0 0 16px;'>"
                f"<li>Add wallet addresses to system_config as MANUAL_WALLET_ADDRESS_1…_5</li>"
                f"<li>Restart wallet_scout (it checks MANUAL_WALLET_LIST on boot)</li>"
                f"<li>Wait 2–3 cycles for trade ingester to populate smart_wallet_trades</li>"
                f"<li>Once ≥3 completed trades per wallet, fingerprints build automatically</li>"
                f"<li>Set COPYTRADE_PAPER_BONUS_ENABLED=1 when ≥50 calibration rows exist</li>"
                f"</ol></div>", unsafe_allow_html=True)

    # ── Recent signals ────────────────────────────────────────────────────────
    with col_s:
        st.markdown("<div class='sn-h'>CONVICTION SIGNALS</div>", unsafe_allow_html=True)
        if ct["signals"]:
            for sig in ct["signals"][:12]:
                mint = str(sig.get("token_mint", "?"))[:14]
                conv = _num(sig.get("copy_conviction_score"))
                wallets = _int(sig.get("matched_wallet_count"))
                veto = str(sig.get("veto_reason", ""))
                mode = str(sig.get("mode", "OBSERVE"))
                sig_time = _num(sig.get("signal_time"))

                conv_col = C_GREEN if conv >= 0.70 else (C_GOLD if conv >= 0.40 else C_DIM)
                veto_badge = (f"<span class='sn-pill' style='color:{C_RED};border-color:{C_RED}44;'>"
                              f"{_esc(veto[:20])}</span>") if veto else ""

                st.markdown(
                    f"<div class='sn-mini' style='padding:6px 8px;margin:3px 0;'>"
                    f"<div style='display:flex;align-items:center;gap:6px;'>"
                    f"<span style='font-family:Share Tech Mono,monospace;font-size:.62rem;"
                    f"color:{C_CYAN};'>{mint}</span>"
                    f"<span class='sn-pill' style='color:{conv_col};'>{conv:.2f}</span>"
                    f"<span class='sn-pill'>{wallets}W</span>"
                    f"<span class='sn-pill'>{mode}</span>"
                    f"{veto_badge}"
                    f"<span style='margin-left:auto;font-family:Share Tech Mono,monospace;"
                    f"font-size:.54rem;color:{C_DIM};'>{_age_str(sig_time)}</span>"
                    f"</div></div>", unsafe_allow_html=True)
        else:
            _empty("No conviction signals yet — scanner running in OBSERVE mode")

    # ── Influence ledger ──────────────────────────────────────────────────────
    if ct["ledger"]:
        with st.expander(f"📒 Influence ledger ({len(ct['ledger'])} recent decisions)", expanded=False):
            st.markdown("<div class='sn-h'>COPYTRADE INFLUENCE LEDGER — A/B MEASUREMENT</div>",
                        unsafe_allow_html=True)
            for row in ct["ledger"][:15]:
                ts = _num(row.get("ts"))
                mint = str(row.get("token_mint","?"))[:14]
                sym = str(row.get("symbol", mint[:8]))
                dec = str(row.get("decision", ""))
                reason = str(row.get("reason", ""))
                bonus = _num(row.get("copytrade_bonus"))
                baseline = _num(row.get("baseline_confidence"))
                final = _num(row.get("final_confidence", baseline))
                wallets = _int(row.get("wallet_count"))

                dec_col = C_GREEN if dec == "BONUS_APPLIED" else (C_RED if dec == "DENIED" else C_DIM)
                st.markdown(
                    f"<div class='sn-trade-row' style='border-left:2px solid {dec_col};"
                    f"background:rgba(0,0,0,0.2);'>"
                    f"<span style='color:{dec_col};min-width:110px;font-size:.6rem;'>{dec}</span>"
                    f"<span style='color:{C_CYAN};'>{sym}</span>"
                    f"<span style='color:{C_DIM};'>base {baseline:.2f}</span>"
                    f"<span style='color:{C_GOLD};'>+{bonus:.3f}</span>"
                    f"<span style='color:#fff;'>→ {final:.2f}</span>"
                    f"<span style='color:{C_DIM};'>{wallets}W</span>"
                    f"<span style='margin-left:auto;color:{C_DIM};font-size:.56rem;'>{_age_str(ts)}</span>"
                    f"</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            f"<div class='sn-mini'><div class='sn-muted'>Influence ledger empty — "
            f"decisions populate once COPYTRADE_PAPER_BONUS_ENABLED=1 and signals exist.</div></div>",
            unsafe_allow_html=True)

    # ── How copy trading works on alts ────────────────────────────────────────
    with st.expander("ℹ️ How alt copy trading works in this system", expanded=False):
        st.markdown(
            f"<div class='sn-copy' style='padding:8px;'>"
            f"<div class='sn-h'>COPY TRADE ARCHITECTURE — ALTS + NATIVES</div>"
            f"The copy trade lane is architecturally separate from the pump.fun meme scanner lane. "
            f"While the meme lane targets sub-$35k mcap pre-graduation bonding curve tokens, "
            f"this lane tracks wallet behaviour across BTC, ETH, SOL, and Tier-2 assets.<br><br>"
            f"<strong style='color:{C_GOLD};'>Wallet scoring:</strong> Each tracked wallet "
            f"accumulates a quality score (0–100) from observed completed trades. Score components: "
            f"hit_rate_2x (did it 2x?), late_copy_failure_rate (did it buy after the spike?), "
            f"median_safe_x (typical realised multiple), rug_exposure_rate.<br><br>"
            f"<strong style='color:{C_GOLD};'>Signal generation:</strong> When a tracked wallet "
            f"buys a token that's in the current candidate pipeline, a conviction signal is created "
            f"with a score 0–1. ELITE wallets (quality≥80, copy≥70) create stronger signals.<br><br>"
            f"<strong style='color:{C_GOLD};'>Paper influence gate:</strong> Conviction signals "
            f"can add a bounded bonus (+0.00 to +0.03) to a near-qualified token's confidence score. "
            f"This only fires if COPYTRADE_PAPER_BONUS_ENABLED=1 and at least 2 independent wallets "
            f"or 1 elite wallet has a fresh buy. Sell imbalance vetoes the bonus immediately.<br><br>"
            f"<strong style='color:{C_RED};'>Live influence: always OFF.</strong> "
            f"The bonus only affects paper mode. Live copy execution is not implemented "
            f"until the paper lane has ≥200 calibration rows proving positive outcome uplift."
            f"</div>", unsafe_allow_html=True)


# ─────────────────────────────────────────────────────────────────────────────
# Existing tabs (preserved exactly)
# ─────────────────────────────────────────────────────────────────────────────
def _render_council(data: dict) -> None:
    roles       = data.get("roles", pd.DataFrame())
    tasks       = data.get("tasks", pd.DataFrame())
    assignments = data.get("assignments", pd.DataFrame())
    support     = data.get("support", pd.DataFrame())

    st.markdown("<div class='sn-card'><div class='sn-h'>SIX-NODE COUNCIL</div>"
                "<div class='sn-muted'>Names stay fixed. Models evolve/devolve per task; "
                "support systems are shown separately.</div></div>", unsafe_allow_html=True)
    grid = st.columns(3, gap="small")
    for i, name in enumerate(COUNCIL_ORDER):
        row = None
        if isinstance(roles, pd.DataFrame) and not roles.empty and "agent_name" in roles.columns:
            hit = roles[roles["agent_name"].astype(str).str.upper() == name]
            if not hit.empty: row = hit.iloc[0].to_dict()
        if row is None:
            row = {"agent_name": name, "current_model": "awaiting registry",
                   "model_tier": "unknown", "evolution_state": "pending", "heartbeat_status": "MISSING"}
        hb    = row.get("heartbeat_status", row.get("status", "MISSING"))
        color = _status_color(hb)
        model = row.get("current_model", row.get("default_model", "unknown"))
        tier  = row.get("model_tier", "unknown")
        evo   = row.get("evolution_state", "baseline")
        role  = row.get("role", "council node")
        with grid[i % 3]:
            st.markdown(
                f"<div class='sn-mini' style='border-color:{color}66;'>"
                f"<div class='sn-h' style='color:{color};'>{COUNCIL_EMOJI.get(name,'◈')} {_esc(name)}</div>"
                f"<div class='sn-copy'>{_esc(_short(role, 88))}</div>"
                f"<div style='margin-top:8px;'>"
                f"<span class='sn-pill'>model: {_esc(model)}</span>"
                f"<span class='sn-pill'>tier: {_esc(tier)}</span>"
                f"<span class='sn-pill'>evo: {_esc(evo)}</span>"
                f"<span class='sn-pill' style='color:{color};border-color:{color}44;'>hb: {_esc(hb)}</span>"
                f"</div></div>", unsafe_allow_html=True)
    st.markdown("<div class='sn-h' style='margin-top:12px;'>OPEN BUILD QUEUE</div>", unsafe_allow_html=True)
    if isinstance(tasks, pd.DataFrame) and not tasks.empty:
        cols = [c for c in ["id","phase","status","risk_level","agent_name","target_tab","task_type","title"]
                if c in tasks.columns]
        _safe_dataframe(tasks[cols].head(24) if cols else tasks.head(24), key="sn_tasks")
    else:
        _empty("Council work queue empty. Run council_build_orchestrator.")
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        st.markdown("<div class='sn-h'>MODEL ASSIGNMENTS</div>", unsafe_allow_html=True)
        if isinstance(assignments, pd.DataFrame) and not assignments.empty:
            cols = [c for c in ["task_id","agent_name","selected_model","model_tier","evolution_direction","reason"]
                    if c in assignments.columns]
            _safe_dataframe(assignments[cols].head(12) if cols else assignments.head(12), key="sn_assignments")
        else: _empty("No model assignments yet.")
    with c2:
        st.markdown("<div class='sn-h'>SUPPORT SYSTEMS</div>", unsafe_allow_html=True)
        if isinstance(support, pd.DataFrame) and not support.empty:
            cols = [c for c in ["name","service_name","role","updated_at"] if c in support.columns]
            _safe_dataframe(support[cols].head(12) if cols else support.head(12), key="sn_support")
        else: _empty("No support system registry yet.")

def _render_lattice(data: dict) -> None:
    proposals = data.get("proposals", pd.DataFrame())
    patterns  = data.get("patterns", pd.DataFrame())
    st.markdown("<div class='sn-card'><div class='sn-h'>GOLDEN LATTICE</div>"
                "<div class='sn-muted'>Active Polaris proposals and learned doctrine patterns.</div></div>",
                unsafe_allow_html=True)
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        st.markdown("<div class='sn-h'>RECENT PROPOSALS</div>", unsafe_allow_html=True)
        if isinstance(proposals, pd.DataFrame) and not proposals.empty:
            cols = [c for c in ["id","proposal_type","status","confidence","created_at"] if c in proposals.columns]
            _safe_dataframe(proposals[cols].head(250) if cols else proposals.head(250), key="sn_proposals")
        else: _empty("No Polaris proposals yet.")
    with c2:
        st.markdown("<div class='sn-h'>LEARNED PATTERNS</div>", unsafe_allow_html=True)
        if isinstance(patterns, pd.DataFrame) and not patterns.empty:
            cols = [c for c in ["id","pattern_type","confidence","outcome_weight","created_at"] if c in patterns.columns]
            _safe_dataframe(patterns[cols].head(250) if cols else patterns.head(250), key="sn_patterns")
        else: _empty("No learned patterns yet.")

def _render_runner_radar(data: dict) -> None:
    scores = data.get("scores", pd.DataFrame())
    st.markdown("<div class='sn-card'><div class='sn-h'>RUNNER RADAR</div>"
                "<div class='sn-muted'>Post-entry runner velocity scoring — MONSTER/STRONG/NEUTRAL/DUD tiers.</div></div>",
                unsafe_allow_html=True)
    if isinstance(scores, pd.DataFrame) and not scores.empty:
        cols = [c for c in ["position_id","token_name","tier","likelihood","velocity_per_min","peak_mult","recommend","scored_at"]
                if c in scores.columns]
        _safe_dataframe(scores[cols].head(40) if cols else scores.head(40), key="sn_scores")
    else: _empty("No runner scores yet. Scoring populates from open positions.")

def _render_velocity(data: dict) -> None:
    st.markdown("<div class='sn-card'><div class='sn-h'>VELOCITY ENGINE</div>"
                "<div class='sn-muted'>Momentum tracking — velocity_per_min per open position.</div></div>",
                unsafe_allow_html=True)
    scores = data.get("scores", pd.DataFrame())
    if isinstance(scores, pd.DataFrame) and not scores.empty and "velocity_per_min" in scores.columns:
        recent = scores[scores["velocity_per_min"] > 0].head(250)
        if not recent.empty:
            cols = [c for c in ["token_name","velocity_per_min","peak_mult","tier","age_sec"] if c in recent.columns]
            _safe_dataframe(recent[cols] if cols else recent, key="sn_velocity")
        else: _empty("No positive velocity signals in recent scores.")
    else: _empty("No velocity data yet.")

def _render_wallets(data: dict) -> None:
    wallets = data.get("wallets", pd.DataFrame())
    st.markdown("<div class='sn-card'><div class='sn-h'>SMART WALLET CONVERGENCE</div>"
                "<div class='sn-muted'>Profiled-wallet convergence — observe/paper until inference is populated and measured.</div></div>",
                unsafe_allow_html=True)
    if isinstance(wallets, pd.DataFrame) and not wallets.empty:
        cols = [c for c in ["id","token_mint","matched_wallet_count","copy_conviction_score","veto_reason","mode","signal_time"]
                if c in wallets.columns]
        _safe_dataframe(wallets[cols].head(40) if cols else wallets.head(40), key="sn_wallets")
    else:
        st.markdown(
            f"<div class='sn-mini' style='border-color:{C_GOLD}55;'>"
            f"<div class='sn-h' style='color:{C_GOLD};'>OBSERVE-ONLY</div>"
            f"<div class='sn-copy'>wallet_entry_likelihood_signals is empty. "
            f"Correctly blocks live copy-trade influence until convergence rows exist.</div></div>",
            unsafe_allow_html=True)

def _render_strategy_lab(data: dict) -> None:
    strategies = data.get("strategies", pd.DataFrame())
    signals    = data.get("strategy_signals", pd.DataFrame())
    results    = data.get("strategy_results", pd.DataFrame())
    st.markdown("<div class='sn-card'><div class='sn-h'>STRATEGY LAB</div>"
                "<div class='sn-muted'>Paper-only registry for runner ladder, smart wallet convergence, and grid quant.</div></div>",
                unsafe_allow_html=True)
    c1, c2 = st.columns(2, gap="medium")
    with c1:
        st.markdown("<div class='sn-h'>REGISTRY</div>", unsafe_allow_html=True)
        if isinstance(strategies, pd.DataFrame) and not strategies.empty:
            cols = [c for c in ["name","strategy_type","enabled","mode","risk_level","description"] if c in strategies.columns]
            _safe_dataframe(strategies[cols].head(250) if cols else strategies.head(250), key="sn_strats")
        else: _empty("No substrate_strategy_registry rows yet.")
    with c2:
        st.markdown("<div class='sn-h'>RECENT SIGNALS</div>", unsafe_allow_html=True)
        if isinstance(signals, pd.DataFrame) and not signals.empty:
            cols = [c for c in ["id","strategy_name","mint_address","signal","confidence","created_at"] if c in signals.columns]
            _safe_dataframe(signals[cols].head(250) if cols else signals.head(250), key="sn_strategy_signals")
        else: _empty("No strategy signals yet.")
    st.markdown("<div class='sn-h'>RESULTS</div>", unsafe_allow_html=True)
    if isinstance(results, pd.DataFrame) and not results.empty:
        cols = [c for c in ["id","strategy_name","mint_address","outcome","pnl_usd","pnl_pct","created_at"] if c in results.columns]
        _safe_dataframe(results[cols].head(24) if cols else results.head(24), key="sn_strategy_results")
    else: _empty("No substrate strategy results yet.")


# ─────────────────────────────────────────────────────────────────────────────
# MAIN ENTRY POINT
# ─────────────────────────────────────────────────────────────────────────────
def render_substrate_tab(query_db=None) -> None:
    """Main render entrypoint. Read-only. Height-safe."""
    _inject_css()

    # ── Balance truth capsule ─────────────────────────────────────────────────
    try:
        from ui.state_contract import get_balance_truth, render_balance_capsule
        render_balance_capsule(get_balance_truth(str(DB_PATH)))
    except Exception as _bt_err:
        st.caption(f"⬡ balance truth not wired — ui/state_contract.py: {type(_bt_err).__name__}: {_bt_err}")

    _header(_load_live(query_db=query_db))
    data = _load_live(query_db=query_db)

    tabs = st.tabs([
        "📈 Trade Book",
        "⚖️ Council Decision",
        "🕵️ Copy Trade",
        "🔐 Wallet Gate",
        "🏛️ Council",
        "🔮 Golden Lattice",
        "🚀 Runner Radar",
        "⚡ Velocity",
        "🧬 Smart Wallets",
        "🧪 Strategy Lab",
    ])
    with tabs[0]: _render_macro_trade_book()
    with tabs[1]: _render_council_decision()
    with tabs[2]: _render_copytrade_station()
    with tabs[3]:
        try:
            from ui.substrate_wallet_panel import render_substrate_wallet_panel as _wallet_panel
        except Exception:
            try:
                from services.substrate_wallet_panel import render_substrate_wallet_panel as _wallet_panel
            except Exception:
                try:
                    from substrate_wallet_panel import render_substrate_wallet_panel as _wallet_panel
                except Exception as _wallet_err:
                    _wallet_panel = None
                    st.caption(f"Substrate wallet gate unavailable: {type(_wallet_err).__name__}: {_wallet_err}")
        if _wallet_panel:
            _wallet_panel()
    with tabs[4]: _render_council(data)
    with tabs[5]: _render_lattice(data)
    with tabs[6]: _render_runner_radar(data)
    with tabs[7]: _render_velocity(data)
    with tabs[8]: _render_wallets(data)
    with tabs[9]: _render_strategy_lab(data)
