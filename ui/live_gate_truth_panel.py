"""
LIVE GATE TRUTH panel — SIGNOFF_LIVE_LANE_REPAIR_20260715.

Renders canonical persisted truth only:
  * mode_b_decision_ledger  — every live-gate verdict with exact veto reasons,
    candidate score vs threshold, confidence vs floor, curve band, price age,
    round-trip impact, oracle authority + envelope telemetry, regime state,
    half-size flag and preflight route reason;
  * system_config           — WS_ORACLE_* envelope + MARKET_REGIME_* state;
  * paper_positions (REAL)  — open REAL exposure and reconciliation surface,
    strictly separated from the SIM lane.

No state is inferred from labels or caches. A missing source renders as
NOT WIRED, matching the house contract style. Progressive disclosure:
SUMMARY / DETAIL / FORENSICS.
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import streamlit as st

C_GREEN = "#14F195"
C_VIOLET = "#9945FF"
C_CYAN = "#8EF9FF"
C_GOLD = "#FFD700"
C_RED = "#FF073A"
C_DIM = "#8FA89B"

_MONO = "font-family:'Share Tech Mono',monospace;"


def _db_path() -> Path | None:
    try:
        from core.schema import DB_PATH  # type: ignore
        return Path(str(DB_PATH))
    except Exception:
        try:
            import sys
            sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
            from core.schema import DB_PATH  # type: ignore
            return Path(str(DB_PATH))
        except Exception:
            return None


def _ro(db: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except sqlite3.Error:
        return set()


def _cfg(conn: sqlite3.Connection, key: str, default: str = "—") -> str:
    try:
        row = conn.execute(
            "SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        return str(row[0]) if row and row[0] is not None else default
    except sqlite3.Error:
        return default


def _chip(label: str, value: str, color: str) -> str:
    return (f"<span style='{_MONO}font-size:0.62rem;letter-spacing:1px;"
            f"border:1px solid {color}55;border-radius:999px;padding:3px 10px;"
            f"margin-right:6px;color:{color};background:rgba(5,7,6,.6);'>"
            f"{label} <b>{value}</b></span>")


def _not_wired(what: str) -> None:
    st.markdown(
        f"<div style='text-align:center;padding:26px;{_MONO}font-size:0.65rem;"
        f"color:#555;letter-spacing:2px;'>// {what} NOT WIRED //</div>",
        unsafe_allow_html=True)


def render_live_gate_truth() -> None:
    db = _db_path()
    if not db or not db.exists():
        _not_wired("DATABASE")
        return
    try:
        conn = _ro(db)
    except Exception as exc:
        _not_wired(f"DATABASE ({type(exc).__name__})")
        return

    now = time.time()

    # ── header strip: oracle envelope + regime — canonical config truth ─────
    o_state = _cfg(conn, "WS_ORACLE_STATE", "UNKNOWN").upper()
    o_hot = _cfg(conn, "WS_ORACLE_HOT_AGE_SEC", "—")
    o_any = _cfg(conn, "WS_ORACLE_ANY_AGE_SEC", "—")
    o_wpm = _cfg(conn, "WS_ORACLE_WPM", "—")
    o_at = _cfg(conn, "WS_ORACLE_SAMPLED_AT", "0")
    regime = _cfg(conn, "MARKET_REGIME_STATE", "STANDARD").upper()
    regime_why = _cfg(conn, "MARKET_REGIME_REASON", "not published yet")
    o_col = {"HEALTHY": C_GREEN, "DEGRADED": C_GOLD}.get(o_state, C_RED)
    r_col = C_GOLD if regime == "RUNNER_RICH" else C_CYAN
    try:
        tel_age = now - float(o_at)
        tel_note = f"{tel_age:.0f}s ago" if tel_age < 1e6 else "never"
    except Exception:
        tel_note = "never"
    st.markdown(
        "<div style='margin:2px 0 10px;'>"
        + _chip("ORACLE", o_state, o_col)
        + _chip("hot", f"{o_hot}s", C_CYAN)
        + _chip("any-feed", f"{o_any}s", C_CYAN)
        + _chip("w/min", o_wpm, C_CYAN)
        + _chip("sampled", tel_note, C_DIM)
        + _chip("REGIME", regime, r_col)
        + "</div>"
        f"<div style='{_MONO}font-size:0.6rem;color:{C_DIM};margin:-4px 0 12px;'>"
        f"regime basis: {regime_why}</div>",
        unsafe_allow_html=True)

    depth = st.radio("depth", ["SUMMARY", "DETAIL", "FORENSICS"],
                     horizontal=True, label_visibility="collapsed")

    led_cols = _cols(conn, "mode_b_decision_ledger")
    if not led_cols:
        _not_wired("MODE B DECISION LEDGER")
        return

    want = ["id", "evaluated_at", "token_name", "mint_address", "verdict", "reasons",
            "live_safe_score", "score_threshold", "adjusted_confidence",
            "confidence_floor", "curve_sol_reserves", "curve_band",
            "round_trip_impact_pct", "price_age_sec", "signal_age_sec",
            "oracle_state", "oracle_authority", "oracle_hot_age_sec",
            "oracle_any_age_sec", "oracle_wpm", "regime_state", "half_size",
            "preflight_reason", "smart_money_tier"]
    sel = ", ".join(c if c in led_cols else f"NULL AS {c}" for c in want)
    rows = [dict(r) for r in conn.execute(
        f"SELECT {sel} FROM mode_b_decision_ledger "
        "ORDER BY evaluated_at DESC LIMIT ?",
        (12 if depth == "SUMMARY" else 40 if depth == "DETAIL" else 150,)
    ).fetchall()]

    # ── REAL lane: exposure + reconciliation, strictly separated from SIM ───
    pp_cols = _cols(conn, "paper_positions")
    if pp_cols:
        try:
            real = conn.execute(
                "SELECT COUNT(*) AS n, COALESCE(SUM(position_size_usd),0) AS exp "
                "FROM paper_positions WHERE status='OPEN' "
                "AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'").fetchone()
            sim = conn.execute(
                "SELECT COUNT(*) AS n FROM paper_positions WHERE status='OPEN' "
                "AND UPPER(COALESCE(funding_mode,'SIM'))='SIM'").fetchone()
            recon = None
            if "source_note" in pp_cols:
                recon = conn.execute(
                    "SELECT token_name, position_size_usd, opened_at, source_note "
                    "FROM paper_positions WHERE status='OPEN' "
                    "AND UPPER(COALESCE(funding_mode,'SIM'))='REAL' "
                    "ORDER BY opened_at DESC LIMIT 3").fetchall()
            st.markdown(
                "<div style='display:flex;gap:10px;margin:6px 0 14px;'>"
                f"<div style='flex:1;border:1px solid {C_GREEN}33;border-radius:12px;"
                f"padding:10px 14px;background:rgba(5,7,6,.7);'>"
                f"<div style='{_MONO}font-size:0.58rem;letter-spacing:2px;color:{C_DIM};'>SIM LANE</div>"
                f"<div style='{_MONO}font-size:0.95rem;color:{C_CYAN};'>{int(sim['n'])} open</div></div>"
                f"<div style='flex:1;border:1px solid {C_GOLD}44;border-radius:12px;"
                f"padding:10px 14px;background:rgba(5,7,6,.7);'>"
                f"<div style='{_MONO}font-size:0.58rem;letter-spacing:2px;color:{C_DIM};'>REAL LANE</div>"
                f"<div style='{_MONO}font-size:0.95rem;color:{C_GOLD};'>{int(real['n'])} open · "
                f"${float(real['exp']):.2f} exposure</div></div></div>",
                unsafe_allow_html=True)
            if recon:
                for r in recon:
                    st.markdown(
                        f"<div style='{_MONO}font-size:0.6rem;color:{C_DIM};margin:-8px 0 8px;'>"
                        f"REAL {r['token_name']} ${float(r['position_size_usd'] or 0):.2f} — "
                        f"tx/recon: {r['source_note'] or 'n/a'}</div>",
                        unsafe_allow_html=True)
        except sqlite3.Error:
            pass

    if not rows:
        st.caption("No live-gate decisions recorded yet.")
        return

    # ── decision cards ───────────────────────────────────────────────────────
    for d in rows:
        passed = str(d["verdict"]).upper() == "PASS"
        edge = C_GREEN if passed else C_RED
        age = now - float(d["evaluated_at"] or now)
        title = d["token_name"] or (d["mint_address"] or "?")[:12]
        score = float(d["live_safe_score"] or 0)
        thr = float(d["score_threshold"] or 0)
        conf = float(d["adjusted_confidence"] or 0)
        floor = float(d["confidence_floor"] or 0)
        head = (
            f"<div style='border:1px solid {edge}44;border-left:3px solid {edge};"
            f"border-radius:10px;padding:8px 12px;margin-bottom:8px;"
            f"background:rgba(5,7,6,.72);'>"
            f"<div style='display:flex;justify-content:space-between;align-items:baseline;'>"
            f"<span style='{_MONO}font-size:0.72rem;color:{edge};'>"
            f"{'PASS' if passed else 'BLOCKED'} · {title}</span>"
            f"<span style='{_MONO}font-size:0.56rem;color:{C_DIM};'>{age/60:.0f}m ago</span></div>"
            f"<div style='margin-top:5px;'>"
            + _chip("score", f"{score:.1f}/{thr:.0f}", C_VIOLET)
            + _chip("conf", f"{conf:.2f}/{floor:.2f}", C_CYAN)
            + _chip("curve", f"{float(d['curve_sol_reserves'] or 0):.2f} SOL"
                    + (f" · {d['curve_band']}" if d["curve_band"] else ""), C_GREEN)
            + _chip("price age", f"{float(d['price_age_sec'] or -1):.0f}s", C_CYAN)
            + (_chip("rt impact", f"{float(d['round_trip_impact_pct']):.1f}%", C_GOLD)
               if d["round_trip_impact_pct"] is not None else "")
            + (_chip("HALF SIZE", "on", C_GOLD) if d["half_size"] else "")
            + "</div>")
        if not passed:
            head += (f"<div style='{_MONO}font-size:0.62rem;color:{C_RED};"
                     f"margin-top:6px;'>veto: {d['reasons']}</div>")
        if depth in ("DETAIL", "FORENSICS"):
            head += (
                f"<div style='{_MONO}font-size:0.58rem;color:{C_DIM};margin-top:5px;'>"
                f"oracle {d['oracle_state'] or '—'}"
                f" · authority {d['oracle_authority'] or 'GLOBAL'}"
                f" · env hot={d['oracle_hot_age_sec'] if d['oracle_hot_age_sec'] is not None else '—'}s"
                f" any={d['oracle_any_age_sec'] if d['oracle_any_age_sec'] is not None else '—'}s"
                f" wpm={d['oracle_wpm'] if d['oracle_wpm'] is not None else '—'}"
                f" · regime {d['regime_state'] or '—'}"
                f" · sm {d['smart_money_tier'] or '—'}"
                + (f" · preflight {d['preflight_reason']}" if d["preflight_reason"] else "")
                + f" · signal age {float(d['signal_age_sec'] or 0):.0f}s</div>")
        head += "</div>"
        st.markdown(head, unsafe_allow_html=True)

    if depth == "FORENSICS":
        st.caption(
            "FORENSICS: every field above is read verbatim from "
            "mode_b_decision_ledger / system_config (read-only). "
            "Replay tooling: launch/replay_gate_variants.py")
