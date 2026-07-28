"""
RUNNER INTELLIGENCE — SIGNOFF_LIVE_LANE_REPAIR_20260715.

For every 80%+ runner (durable held_peak_pct stamp), renders:
  * entry features: confidence, entry price source, signal age, curve reserve
    at decision time (via mode_b_decision_ledger join);
  * time-to-25/50/80/100% and peak, computed from the persisted
    market_snapshots mtm price timeline — never from labels;
  * exit price/reason and realized PnL;
  * post-exit continuation: best price seen within 15 minutes after close;
  * holder/creator structure where those columns are wired, NOT WIRED where
    they are not;
  * why REAL passed or was blocked, verbatim from the decision ledger.

Read-only. SIM lane only (runner learning is a SIM concern; REAL rows are the
execution mirror and are shown by the Live Gate Truth panel).
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


def _timeline(conn: sqlite3.Connection, mint: str, t0: float, t1: float) -> list[tuple[float, float]]:
    try:
        rows = conn.execute(
            "SELECT price_updated_at, observed_price FROM market_snapshots "
            "WHERE mint_address=? AND candidate_state='mtm' AND observed_price>0 "
            "AND price_updated_at BETWEEN ? AND ? ORDER BY price_updated_at ASC",
            (mint, t0, t1)).fetchall()
        return [(float(r[0]), float(r[1])) for r in rows]
    except sqlite3.Error:
        return []


def _time_to(timeline: list[tuple[float, float]], entry: float,
             opened: float, pct: float) -> float | None:
    target = entry * (1.0 + pct / 100.0)
    for ts, px in timeline:
        if px >= target:
            return ts - opened
    return None


def _fmt_dt(sec: float | None) -> str:
    if sec is None:
        return "—"
    if sec < 90:
        return f"{sec:.0f}s"
    return f"{sec/60:.1f}m"


def _chip(label: str, value: str, color: str) -> str:
    return (f"<span style='{_MONO}font-size:0.6rem;letter-spacing:1px;"
            f"border:1px solid {color}55;border-radius:999px;padding:2px 9px;"
            f"margin-right:5px;color:{color};background:rgba(5,7,6,.6);'>"
            f"{label} <b>{value}</b></span>")


def render_runner_intelligence() -> None:
    db = _db_path()
    if not db or not db.exists():
        st.markdown(f"<div style='text-align:center;padding:26px;{_MONO}"
                    "font-size:0.65rem;color:#555;'>// DATABASE NOT WIRED //</div>",
                    unsafe_allow_html=True)
        return
    conn = _ro(db)
    pp = _cols(conn, "paper_positions")
    if "held_peak_pct" not in pp:
        st.caption("held_peak_pct not stamped yet — runners will appear after the "
                   "next closes under the current engine.")
        return

    hours = st.slider("lookback (hours)", 6, 336, 72, step=6)
    since = time.time() - hours * 3600

    opt = [c for c in ("holder_count", "creator_share_pct", "top10_share_pct",
                       "creator_wallet", "entry_price_source", "confidence",
                       "exit_reason", "exit_price") if c in pp]
    sel = ", ".join(["id", "mint_address", "token_name", "opened_at", "closed_at",
                     "entry_price",
                     "CAST(COALESCE(position_size_usd,0) AS REAL) AS size_usd",
                     "CAST(COALESCE(realized_pnl_usd,0) AS REAL) AS pnl_usd",
                     "CAST(COALESCE(held_peak_pct,0) AS REAL) AS peak_pct"] + opt)
    runners = [dict(r) for r in conn.execute(
        f"SELECT {sel} FROM paper_positions WHERE status='CLOSED' "
        "AND UPPER(COALESCE(funding_mode,'SIM'))='SIM' "
        "AND CAST(COALESCE(held_peak_pct,0) AS REAL)>=80 "
        "AND closed_at>=? ORDER BY closed_at DESC LIMIT 40", (since,)).fetchall()]

    led = _cols(conn, "mode_b_decision_ledger")
    st.markdown(
        f"<div style='{_MONO}font-size:0.68rem;color:{C_GOLD};letter-spacing:2px;"
        f"margin-bottom:10px;'>{len(runners)} RUNNERS (peak ≥ 80%) in last {hours}h</div>",
        unsafe_allow_html=True)

    for r in runners:
        opened = float(r["opened_at"] or 0)
        closed = float(r["closed_at"] or opened)
        entry = float(r["entry_price"] or 0)
        tl = _timeline(conn, r["mint_address"], opened, closed) if entry > 0 else []
        t25 = _time_to(tl, entry, opened, 25)
        t50 = _time_to(tl, entry, opened, 50)
        t80 = _time_to(tl, entry, opened, 80)
        t100 = _time_to(tl, entry, opened, 100)
        # post-exit continuation: best mark within 15m after close
        post = _timeline(conn, r["mint_address"], closed, closed + 900)
        exit_px = float(r.get("exit_price") or 0)
        cont = None
        if post and exit_px > 0:
            best_post = max(px for _, px in post)
            cont = (best_post - exit_px) / exit_px * 100.0
        pnl_pct = (r["pnl_usd"] / r["size_usd"] * 100.0) if r["size_usd"] else 0.0

        # why REAL passed/blocked — verbatim ledger join
        real_line = "decision ledger: NOT WIRED"
        if led:
            try:
                d = conn.execute(
                    "SELECT verdict, reasons, live_safe_score, score_threshold "
                    "FROM mode_b_decision_ledger WHERE mint_address=? "
                    "AND ABS(evaluated_at-?)<=180 ORDER BY ABS(evaluated_at-?) LIMIT 1",
                    (r["mint_address"], opened, opened)).fetchone()
                if d:
                    if str(d["verdict"]).upper() == "PASS":
                        real_line = (f"REAL PASS · score "
                                     f"{float(d['live_safe_score'] or 0):.1f}/"
                                     f"{float(d['score_threshold'] or 0):.0f}")
                    else:
                        real_line = f"REAL BLOCKED · {d['reasons']}"
                else:
                    real_line = "no gate decision within ±180s of entry"
            except sqlite3.Error:
                pass

        struct = []
        for k, lab in (("holder_count", "holders"), ("creator_share_pct", "creator%"),
                       ("top10_share_pct", "top10%")):
            if k in r and r[k] is not None:
                struct.append(f"{lab}={r[k]}")
        struct_line = " · ".join(struct) if struct else "holder/creator structure: NOT WIRED"

        blocked = real_line.startswith("REAL BLOCKED")
        edge = C_RED if blocked else C_GREEN
        st.markdown(
            f"<div style='border:1px solid {C_VIOLET}33;border-left:3px solid {edge};"
            f"border-radius:10px;padding:9px 12px;margin-bottom:9px;background:rgba(5,7,6,.72);'>"
            f"<div style='display:flex;justify-content:space-between;align-items:baseline;'>"
            f"<span style='{_MONO}font-size:0.72rem;color:{C_GOLD};'>"
            f"{r['token_name'] or r['mint_address'][:10]} · peak +{r['peak_pct']:.0f}%</span>"
            f"<span style='{_MONO}font-size:0.6rem;color:{C_CYAN};'>realized {pnl_pct:+.1f}%</span></div>"
            f"<div style='margin-top:5px;'>"
            + _chip("t→25%", _fmt_dt(t25), C_CYAN)
            + _chip("t→50%", _fmt_dt(t50), C_CYAN)
            + _chip("t→80%", _fmt_dt(t80), C_GREEN)
            + _chip("t→100%", _fmt_dt(t100), C_GREEN)
            + _chip("post-exit", f"{cont:+.0f}%" if cont is not None else "—",
                    C_GOLD if (cont or 0) > 0 else C_DIM)
            + (_chip("conf", f"{float(r['confidence']):.2f}", C_VIOLET)
               if r.get("confidence") is not None else "")
            + (_chip("src", str(r["entry_price_source"]), C_DIM)
               if r.get("entry_price_source") else "")
            + "</div>"
            f"<div style='{_MONO}font-size:0.58rem;color:{C_DIM};margin-top:5px;'>"
            f"{struct_line} · exit: {r.get('exit_reason') or '—'}</div>"
            f"<div style='{_MONO}font-size:0.6rem;color:{edge};margin-top:4px;'>{real_line}</div>"
            "</div>",
            unsafe_allow_html=True)

    if not runners:
        st.caption("No 80%+ runners closed in this window.")
