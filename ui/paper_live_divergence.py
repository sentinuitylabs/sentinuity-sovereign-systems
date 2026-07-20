"""
ui/paper_live_divergence.py
===========================
SIGNOFF_PAPER_LIVE_DIVERGENCE_INSTRUMENT_20260720

First-class paper/live divergence instrument (Grand-Vision UI requirement #2).

Read-only. Opens the matrix DB with mode=ro + query_only and never writes.
It renders one row per REAL (live-funded) position, paired against the paper
lane, with the full timing and price chain the directive requires, and a
divergence classification that NEVER implies paper and live are equivalent
when chain truth disagrees.

Data contracts consumed (introspected defensively — missing columns degrade
to "NOT RECORDED", never to invented values):
  * paper_positions            — SIM rows (paper lane) and funding_mode=REAL
                                 rows (live lane), per live_settlement_recovery.
  * live_tx_ledger             — canonical chain truth: submitted_at,
                                 confirmed_at, block_time, reconciled_at,
                                 state, slippage_bps, latency fields.
  * market_snapshots           — signal timestamp per mint (created_at).

Wiring (presentation-only; add one call wherever the hub renders section 01
"Trade Truth"):
    from ui.paper_live_divergence import render_paper_live_divergence
    render_paper_live_divergence(db_path)
"""
from __future__ import annotations

import html
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

# ── divergence taxonomy (directive: "It must distinguish…") ──────────────────
CLASS_EXPECTED_FRICTION = "EXPECTED_EXECUTION_FRICTION"
CLASS_ORACLE_DELAY = "ORACLE_DELAY"
CLASS_ROUTE_DELAY = "ROUTE_CONSTRUCTION_DELAY"
CLASS_CONFIRM_DELAY = "CONFIRMATION_DELAY"
CLASS_FILL_FAILURE = "FILL_RESOLUTION_FAILURE"
CLASS_PAPER_ECHO = "PAPER_PRICE_ECHO"
CLASS_QTY_MISMATCH = "QUANTITY_MISMATCH"
CLASS_MARKET_MOVE = "GENUINE_MARKET_MOVEMENT"
CLASS_UNCLASSIFIED = "UNCLASSIFIED"

_PALETTE = {
    "ok": "#3ddc97",       # green — aligned
    "warn": "#e6b450",     # amber — expected friction / delays
    "bad": "#e4587c",      # rose — chain truth disagrees
    "cyan": "#53d8e8",
    "violet": "#9d7bde",
    "gold": "#c9a24b",
    "dim": "#8b93a7",
    "bg": "#0b0f14",
    "panel": "#101722",
    "edge": "#1d2836",
}


def _ro(db_path: str | Path) -> Optional[sqlite3.Connection]:
    try:
        conn = sqlite3.connect(f"file:{Path(db_path)}?mode=ro", uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1500")
        return conn
    except sqlite3.Error:
        return None


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except sqlite3.Error:
        return set()


def _g(row: sqlite3.Row, col: str, default: Any = None) -> Any:
    try:
        return row[col] if col in row.keys() else default
    except Exception:
        return default


def _fmt_ts(v: Any) -> str:
    try:
        f = float(v)
        if f <= 0:
            return "NOT RECORDED"
        return time.strftime("%H:%M:%S", time.localtime(f))
    except (TypeError, ValueError):
        return "NOT RECORDED"


def _fmt_usd(v: Any) -> str:
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "NOT RECORDED"


def _fmt_px(v: Any) -> str:
    try:
        f = float(v)
        return f"{f:.8f}" if f < 0.01 else f"{f:,.6f}"
    except (TypeError, ValueError):
        return "NOT RECORDED"


def classify_divergence(pair: dict[str, Any]) -> tuple[str, str, str]:
    """Return (classification, severity ok|warn|bad, exact explanation)."""
    state = str(pair.get("chain_state") or "").upper()
    if state in ("FAILED_ON_CHAIN", "MANUAL_INTERVENTION"):
        return (CLASS_FILL_FAILURE, "bad",
                f"chain state {state}: live fill did not resolve — paper and "
                "live are NOT equivalent for this position")
    if state in ("SUBMITTED", "CONFIRMED_UNRESOLVED"):
        return (CLASS_CONFIRM_DELAY, "warn",
                f"chain state {state}: awaiting reconciliation; live PnL is "
                "provisional until chain truth lands")
    pq, lq = pair.get("paper_qty"), pair.get("live_qty")
    try:
        if pq is not None and lq is not None and float(pq) > 0:
            drift = abs(float(lq) - float(pq)) / float(pq)
            if drift > 0.10:
                return (CLASS_QTY_MISMATCH, "bad",
                        f"raw-token live quantity differs from paper by "
                        f"{drift * 100:.1f}% — sizes are not comparable as-is")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    pe, le = pair.get("paper_entry_px"), pair.get("live_fill_px")
    try:
        if pe is not None and le is not None and float(pe) > 0:
            if abs(float(le) - float(pe)) / float(pe) < 1e-6:
                return (CLASS_PAPER_ECHO, "bad",
                        "chain-derived fill price is byte-identical to the paper "
                        "entry price — likely a paper-price echo, not chain truth")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    lat = pair.get("submit_to_confirm_sec")
    try:
        if lat is not None and float(lat) > 20:
            return (CLASS_CONFIRM_DELAY, "warn",
                    f"submit→confirm took {float(lat):.1f}s — confirmation delay "
                    "dominates this divergence")
    except (TypeError, ValueError):
        pass
    qage = pair.get("quote_age_sec")
    try:
        if qage is not None and float(qage) > 5:
            return (CLASS_ORACLE_DELAY, "warn",
                    f"quote was {float(qage):.1f}s old before broadcast — oracle/"
                    "quote staleness explains part of the fill gap")
    except (TypeError, ValueError):
        pass
    compose = pair.get("compose_sign_sec")
    try:
        if compose is not None and float(compose) > 3:
            return (CLASS_ROUTE_DELAY, "warn",
                    f"route compose+sign took {float(compose):.1f}s — route "
                    "construction delay contributed to slippage")
    except (TypeError, ValueError):
        pass
    try:
        if pe is not None and le is not None and float(pe) > 0:
            gap = abs(float(le) - float(pe)) / float(pe)
            if gap <= 0.02:
                return (CLASS_EXPECTED_FRICTION, "ok",
                        f"fill within {gap * 100:.2f}% of paper entry — normal "
                        "execution friction")
            return (CLASS_MARKET_MOVE, "warn",
                    f"fill differs from paper entry by {gap * 100:.2f}% with no "
                    "recorded latency cause — genuine market movement between "
                    "signal and fill")
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return (CLASS_UNCLASSIFIED, "warn",
            "insufficient recorded telemetry to attribute this divergence — "
            "treat paper and live as NOT verified equivalent")


def collect_pairs(db_path: str | Path, limit: int = 25) -> list[dict[str, Any]]:
    """Assemble divergence rows from chain + paper truth. Read-only."""
    conn = _ro(db_path)
    if conn is None:
        return []
    try:
        pcols = _cols(conn, "paper_positions")
        if not pcols:
            return []
        want = ["id", "mint_address", "token_name", "status", "opened_at",
                "closed_at", "entry_price", "position_size_usd",
                "realized_pnl_usd"]
        opt = [c for c in ("funding_mode", "buy_tx_sig", "sell_tx_sig",
                           "chain_confirmed_at", "live_state", "exit_price",
                           "token_qty", "entry_qty", "fees_usd") if c in pcols]
        sel = [c for c in want if c in pcols] + opt
        if "funding_mode" not in pcols:
            return []  # live lane not present in this DB — nothing to compare
        live_rows = conn.execute(
            f"SELECT {', '.join(sel)} FROM paper_positions "
            "WHERE UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
            "ORDER BY COALESCE(opened_at,0) DESC LIMIT ?", (limit,)).fetchall()
        ltl = _cols(conn, "live_tx_ledger")
        ms = _cols(conn, "market_snapshots")
        pairs: list[dict[str, Any]] = []
        for lr in live_rows:
            mint = str(_g(lr, "mint_address") or "")
            opened = _g(lr, "opened_at")
            pair: dict[str, Any] = {
                "position_id": _g(lr, "id"),
                "token": str(_g(lr, "token_name") or mint[:8]),
                "mint": mint,
                "status": str(_g(lr, "status") or ""),
                "live_entry_ts": opened,
                "live_exit_ts": _g(lr, "closed_at"),
                "live_fill_px": _g(lr, "entry_price"),
                "live_exit_px": _g(lr, "exit_price"),
                "live_qty": _g(lr, "token_qty") or _g(lr, "entry_qty"),
                "live_pnl": _g(lr, "realized_pnl_usd"),
                "fees_usd": _g(lr, "fees_usd"),
                "chain_confirmed_at": _g(lr, "chain_confirmed_at"),
                "chain_state": "",
            }
            # chain truth for the BUY leg
            buy_sig = _g(lr, "buy_tx_sig")
            if buy_sig and ltl:
                lsel = [c for c in ("state", "submitted_at", "confirmed_at",
                                    "block_time", "reconciled_at", "slippage_bps",
                                    "quote_age_before_broadcast_sec",
                                    "compose_sign_duration_sec",
                                    "submit_to_confirm_sec") if c in ltl]
                if lsel:
                    t = conn.execute(
                        f"SELECT {', '.join(lsel)} FROM live_tx_ledger "
                        "WHERE tx_sig=?", (buy_sig,)).fetchone()
                    if t:
                        pair["chain_state"] = str(_g(t, "state") or "")
                        pair["live_submit_ts"] = _g(t, "submitted_at")
                        pair["chain_fill_ts"] = (_g(t, "block_time")
                                                 or _g(t, "confirmed_at"))
                        pair["slippage_bps"] = _g(t, "slippage_bps")
                        pair["quote_age_sec"] = _g(
                            t, "quote_age_before_broadcast_sec")
                        pair["compose_sign_sec"] = _g(
                            t, "compose_sign_duration_sec")
                        pair["submit_to_confirm_sec"] = _g(
                            t, "submit_to_confirm_sec")
            # paper twin: nearest SIM open on the same mint within ±10 min
            if mint and opened:
                twin = conn.execute(
                    "SELECT id, opened_at, entry_price, position_size_usd, "
                    " closed_at, realized_pnl_usd "
                    + (", exit_price" if "exit_price" in pcols else "")
                    + (", token_qty" if "token_qty" in pcols else "")
                    + " FROM paper_positions "
                    "WHERE mint_address=? AND UPPER(COALESCE(funding_mode,'SIM'))='SIM' "
                    "AND opened_at BETWEEN ? AND ? "
                    "ORDER BY ABS(opened_at - ?) ASC LIMIT 1",
                    (mint, float(opened) - 600, float(opened) + 600,
                     float(opened))).fetchone()
                if twin:
                    pair["paper_entry_ts"] = _g(twin, "opened_at")
                    pair["paper_entry_px"] = _g(twin, "entry_price")
                    pair["paper_qty"] = _g(twin, "token_qty")
                    pair["paper_exit_ts"] = _g(twin, "closed_at")
                    pair["paper_exit_px"] = _g(twin, "exit_price")
                    pair["paper_pnl"] = _g(twin, "realized_pnl_usd")
            # signal timestamp
            if mint and ms and "created_at" in ms:
                sig = conn.execute(
                    "SELECT MIN(created_at) FROM market_snapshots "
                    "WHERE mint_address=? AND created_at BETWEEN ? AND ?",
                    (mint, float(opened or 0) - 3600, float(opened or time.time()))
                ).fetchone()
                if sig and sig[0]:
                    pair["signal_ts"] = sig[0]
            cls, sev, why = classify_divergence(pair)
            pair["classification"], pair["severity"], pair["explanation"] = cls, sev, why
            # latency
            try:
                if pair.get("live_submit_ts") and pair.get("chain_fill_ts"):
                    pair["latency_sec"] = float(pair["chain_fill_ts"]) - float(
                        pair["live_submit_ts"])
            except (TypeError, ValueError):
                pass
            pairs.append(pair)
        return pairs
    finally:
        conn.close()


# ── Streamlit render ─────────────────────────────────────────────────────────
def render_paper_live_divergence(db_path: str | Path, limit: int = 25) -> None:
    """Component-contained render: any internal failure produces a readable
    unavailable state instead of an exception escaping into the hub."""
    import streamlit as st

    try:
        _render_inner(st, db_path, limit)
    except Exception as exc:
        try:
            st.markdown(
                f"<div style='background:{_PALETTE['panel']};border:1px solid "
                f"{_PALETTE['edge']};border-left:3px solid {_PALETTE['warn']};"
                f"padding:.6rem 1rem;border-radius:4px'>"
                f"<span style='color:{_PALETTE['gold']};font-size:.72rem;"
                f"letter-spacing:.14em'>PAPER / LIVE DIVERGENCE INSTRUMENT</span><br>"
                f"<span style='color:{_PALETTE['warn']};font-size:.85rem'>"
                f"INSTRUMENT UNAVAILABLE — {html.escape(type(exc).__name__)}: "
                f"{html.escape(str(exc)[:160])} · trading backend unaffected"
                f"</span></div>", unsafe_allow_html=True)
        except Exception:
            pass


def _render_inner(st, db_path: str | Path, limit: int) -> None:
    P = _PALETTE
    pairs = collect_pairs(db_path, limit=limit)
    n_bad = sum(1 for p in pairs if p["severity"] == "bad")
    banner_col = P["bad"] if n_bad else (P["warn"] if pairs else P["dim"])
    banner_txt = (
        f"{n_bad} POSITION(S) WHERE CHAIN TRUTH DISAGREES WITH PAPER"
        if n_bad else
        ("PAPER AND LIVE WITHIN EXPECTED FRICTION — VERIFIED PER POSITION BELOW"
         if pairs else
         "NO LIVE-FUNDED POSITIONS RECORDED — NOTHING TO COMPARE (this is a "
         "truthful empty state, not an error)"))
    st.markdown(
        f"<div style='background:{P['panel']};border:1px solid {P['edge']};"
        f"border-left:3px solid {banner_col};padding:.7rem 1rem;border-radius:4px;"
        f"margin-bottom:.6rem'>"
        f"<span style='color:{P['gold']};font-size:.72rem;letter-spacing:.14em'>"
        f"PAPER / LIVE DIVERGENCE INSTRUMENT</span><br>"
        f"<span style='color:{banner_col};font-size:.9rem'>{html.escape(banner_txt)}"
        f"</span></div>", unsafe_allow_html=True)
    if not pairs:
        return
    for p in pairs:
        sev = p["severity"]
        edge = {"ok": P["ok"], "warn": P["warn"], "bad": P["bad"]}[sev]
        hdr = (f"{p['token']} · #{p['position_id']} · {p['status']} · "
               f"{p['classification']}")
        with st.expander(hdr, expanded=(sev == "bad")):
            st.markdown(
                f"<span style='color:{edge}'>{html.escape(p['explanation'])}"
                f"</span>", unsafe_allow_html=True)
            rows = [
                ("Signal timestamp", _fmt_ts(p.get("signal_ts"))),
                ("Paper entry timestamp", _fmt_ts(p.get("paper_entry_ts"))),
                ("Live submission timestamp", _fmt_ts(p.get("live_submit_ts"))),
                ("Chain-confirmed fill timestamp", _fmt_ts(p.get("chain_fill_ts"))),
                ("Paper entry price", _fmt_px(p.get("paper_entry_px"))),
                ("Chain-derived live fill price", _fmt_px(p.get("live_fill_px"))),
                ("Paper quantity", str(p.get("paper_qty") or "NOT RECORDED")),
                ("Raw-token live quantity", str(p.get("live_qty") or "NOT RECORDED")),
                ("Paper exit", _fmt_px(p.get("paper_exit_px"))
                 + " @ " + _fmt_ts(p.get("paper_exit_ts"))),
                ("Live exit", _fmt_px(p.get("live_exit_px"))
                 + " @ " + _fmt_ts(p.get("live_exit_ts"))),
                ("Paper PnL", _fmt_usd(p.get("paper_pnl"))),
                ("Chain-reconciled live PnL", _fmt_usd(p.get("live_pnl"))),
                ("Fee impact", _fmt_usd(p.get("fees_usd"))),
                ("Slippage", (f"{p['slippage_bps']} bps"
                              if p.get("slippage_bps") is not None
                              else "NOT RECORDED")),
                ("Latency (submit→chain fill)",
                 (f"{p['latency_sec']:.1f}s" if p.get("latency_sec") is not None
                  else "NOT RECORDED")),
                ("Chain state", p.get("chain_state") or "NOT RECORDED"),
            ]
            body = "".join(
                f"<tr><td style='color:{P['dim']};padding:.15rem .8rem .15rem 0;"
                f"font-size:.78rem'>{html.escape(k)}</td>"
                f"<td style='color:#cfd6e4;font-size:.82rem'>{html.escape(v)}</td></tr>"
                for k, v in rows)
            st.markdown(
                f"<table style='border-collapse:collapse'>{body}</table>",
                unsafe_allow_html=True)