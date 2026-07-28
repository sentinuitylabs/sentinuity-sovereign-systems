#!/usr/bin/env python3
"""
ui/rainbow_trajectory_tab.py — RAINBOW TRAJECTORY MAP (Task G UI)

Renders trajectory_score_history from sentinuity_intelligence.db as an SVG
line/band chart. The rainbow is the DOCTRINE COLOUR SCALE of the y-axis —
the line itself follows the measured score and will bend DOWN on regression.

Colour doctrine (matches ui/theme.py rails):
  RED     #FF073A / BLOOD  — regression / risk / dead state
  ORANGE  #FF6B35 / EMBER  — unstable but learning
  GOLD    #FFD700          — profitable paper logic (earned, never default)
  GREEN   #14F195          — clean profitable repeatability
  CYAN    #38E1FF          — predictive confidence improving
  VIOLET  #9945FF          — multi-lane autonomy leverage

No projections. Missing history renders as MISSING, never faked.
Pure renderer (_rainbow_svg) is import-safe and testable without streamlit.

Integration (sovereign_hub.py):
    def _sec_rainbow() -> None:
        try:
            from ui.rainbow_trajectory_tab import render_rainbow_trajectory
            render_rainbow_trajectory()
        except Exception as e:
            st.caption(f"Rainbow Trajectory unavailable: {e}")
    # then add to _HUB_SECTIONS:
    #   "rainbow": (" RAINBOW TRAJECTORY - MEASURED", _sec_rainbow),
"""
from __future__ import annotations

import html
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

BAND_COLOURS = {
    "RED":    "#FF073A",
    "ORANGE": "#FF6B35",
    "GOLD":   "#FFD700",
    "GREEN":  "#14F195",
    "CYAN":   "#38E1FF",
    "VIOLET": "#9945FF",
}
# y-axis bands (score lo, hi, band)
BANDS = [(0, 20, "RED"), (20, 40, "ORANGE"), (40, 55, "GOLD"),
         (55, 70, "GREEN"), (70, 85, "CYAN"), (85, 100, "VIOLET")]

COMPONENT_LABELS = [
    ("c1_paper_net_pnl",        "paper net PnL"),
    ("c2_profit_factor",        "profit factor"),
    ("c3_monster_capture",      "monster capture"),
    ("c4_bad_loss_suppression", "bad-loss suppression"),
    ("c5_clean_price_ratio",    "clean-price ratio"),
    ("c6_shadow_expectancy",    "shadow expectancy"),
    ("c7_prediction_coverage",  "prediction coverage"),
    ("c8_calibration_quality",  "calibration quality"),
    ("c9_service_uptime",       "service uptime"),
    ("c10_history_continuity",  "history continuity"),
    ("c11_build_quality",       "build quality"),
    ("c12_substrate_contrib",   "substrate/copytrade"),
]


def _find_intel_db(explicit: Optional[str] = None) -> Optional[Path]:
    for p in [Path(explicit) if explicit else None,
              Path(os.environ.get("SENTINUITY_INTEL_DB", "")) if
              os.environ.get("SENTINUITY_INTEL_DB") else None,
              ROOT / "sentinuity_intelligence.db",
              ROOT / "services" / "sentinuity_intelligence.db"]:
        if p and p.exists():
            return p
    return None


def _load_history(db: Path, hours: float = 72.0, limit: int = 500) -> List[Dict[str, Any]]:
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
    except Exception:
        return []
    try:
        rows = con.execute(
            "SELECT * FROM trajectory_score_history "
            "WHERE computed_at >= ? ORDER BY computed_at ASC LIMIT ?",
            (time.time() - hours * 3600, limit)).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []
    finally:
        con.close()


# ---------------------------------------------------------------- renderer

def _rainbow_svg(history: List[Dict[str, Any]], w: int = 980, h: int = 340) -> str:
    pad_l, pad_r, pad_t, pad_b = 54, 16, 14, 30
    cw, ch = w - pad_l - pad_r, h - pad_t - pad_b

    def y_for(score: float) -> float:
        return pad_t + ch * (1.0 - max(0.0, min(100.0, score)) / 100.0)

    parts = [f'<svg viewBox="0 0 {w} {h}" xmlns="http://www.w3.org/2000/svg" '
             f'style="width:100%;height:auto;background:#080B09;border-radius:12px;">']

    # rainbow doctrine bands (translucent, labelled)
    for lo, hi, band in BANDS:
        c = BAND_COLOURS[band]
        y1, y0 = y_for(hi), y_for(lo)
        parts.append(f'<rect x="{pad_l}" y="{y1:.1f}" width="{cw}" '
                     f'height="{(y0 - y1):.1f}" fill="{c}" opacity="0.07"/>')
        parts.append(f'<line x1="{pad_l}" y1="{y1:.1f}" x2="{pad_l + cw}" y2="{y1:.1f}" '
                     f'stroke="{c}" stroke-width="0.6" opacity="0.35"/>')
        parts.append(f'<text x="8" y="{(y0 + y1) / 2 + 3:.1f}" fill="{c}" '
                     f'font-size="9" font-family="Share Tech Mono,monospace" '
                     f'opacity="0.9">{band}</text>')

    if not history:
        parts.append(f'<text x="{w/2}" y="{h/2}" text-anchor="middle" fill="#3b2d5e" '
                     f'font-size="13" font-family="Share Tech Mono,monospace">'
                     f'MISSING — no trajectory_score_history rows yet '
                     f'(run services/trajectory_score.py --once)</text>')
        parts.append("</svg>")
        return "".join(parts)

    t0 = history[0]["computed_at"]
    t1 = history[-1]["computed_at"]
    span = max(1.0, t1 - t0)

    def x_for(ts: float) -> float:
        return pad_l + cw * ((ts - t0) / span) if len(history) > 1 else pad_l + cw / 2

    # measured line — segments coloured by the band they sit in
    pts = [(x_for(r["computed_at"]), y_for(r["trajectory_score"] or 0.0),
            r["trajectory_score"] or 0.0, r) for r in history]
    for i in range(1, len(pts)):
        x1, y1, s1, _ = pts[i - 1]
        x2, y2, s2, _ = pts[i]
        band = next(b for lo, hi, b in BANDS if lo <= (s1 + s2) / 2 <= max(hi, 0.001)
                    or (s1 + s2) / 2 < 20 and b == "RED")
        c = BAND_COLOURS.get(band, "#9DB5A8")
        parts.append(f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" '
                     f'stroke="{c}" stroke-width="2.2" stroke-linecap="round"/>')
    for x, y, s, r in pts:
        band = next(b for lo, hi, b in BANDS if lo <= s <= hi) if s <= 100 else "VIOLET"
        c = BAND_COLOURS.get(band, "#9DB5A8")
        cov = r.get("components_measured") or 0
        # low measurement coverage renders hollow — honesty marker
        fill = c if cov >= 6 else "none"
        parts.append(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="3.2" fill="{fill}" '
                     f'stroke="{c}" stroke-width="1.4">'
                     f'<title>score {s:.1f} — {cov}/12 components measured</title>'
                     f'</circle>')

    # time labels
    for frac, ts in ((0.0, t0), (1.0, t1)):
        lbl = time.strftime("%d %b %H:%M", time.localtime(ts))
        anchor = "start" if frac == 0 else "end"
        parts.append(f'<text x="{pad_l + cw * frac}" y="{h - 10}" fill="#5C6F66" '
                     f'font-size="9" text-anchor="{anchor}" '
                     f'font-family="Share Tech Mono,monospace">{lbl}</text>')

    last = history[-1]
    parts.append(f'<text x="{pad_l + cw}" y="{pad_t + 12}" text-anchor="end" '
                 f'fill="{BAND_COLOURS.get(last.get("band") or "RED", "#9DB5A8")}" '
                 f'font-size="12" font-family="Orbitron,sans-serif" font-weight="900">'
                 f'{(last.get("trajectory_score") or 0):.1f} '
                 f'{html.escape(str(last.get("band") or ""))}</text>')
    parts.append("</svg>")
    return "".join(parts)


def _component_grid_html(last: Dict[str, Any]) -> str:
    cells = []
    for key, label in COMPONENT_LABELS:
        v = last.get(key)
        if v is None:
            col, txt = "#3b2d5e", "not measured"
        else:
            col = ("#FF073A" if v < 0.3 else "#FF6B35" if v < 0.5
                   else "#FFD700" if v < 0.65 else "#14F195" if v < 0.8
                   else "#38E1FF" if v < 0.92 else "#9945FF")
            txt = f"{v:.2f}"
        cells.append(
            f"<div style='border:1px solid {col}44;border-radius:8px;padding:6px 8px;"
            f"background:rgba(5,7,6,.92);'>"
            f"<div style='color:#5C6F66;font-size:.58rem;'>{html.escape(label)}</div>"
            f"<div style='color:{col};font-size:.78rem;"
            f"font-family:Share Tech Mono,monospace;'>{txt}</div></div>")
    return ("<div style='display:grid;grid-template-columns:repeat(auto-fit,"
            "minmax(140px,1fr));gap:8px;margin-top:10px;'>" + "".join(cells) + "</div>")


# ---------------------------------------------------------------- streamlit

def render_rainbow_trajectory(intel_db: Optional[str] = None,
                              hours: float = 72.0) -> None:
    import streamlit as st  # imported here so the renderer stays testable

    st.markdown(
        "<div style='font-family:Orbitron,sans-serif;font-weight:900;"
        "letter-spacing:.14em;font-size:.8rem;color:#9DB5A8;'>"
        "RAINBOW TRAJECTORY MAP — MEASURED SYSTEM EVOLUTION</div>",
        unsafe_allow_html=True)
    st.caption("Composite of 12 measured components. The line bends down on "
               "regression — nothing here is projected or hardcoded upward. "
               "Hollow points = fewer than 6/12 components measurable.")

    db = _find_intel_db(intel_db)
    if db is None:
        st.caption("intel DB not found — trajectory MISSING (never faked).")
        return
    history = _load_history(db, hours=hours)
    st.markdown(_rainbow_svg(history), unsafe_allow_html=True)
    if history:
        st.markdown(_component_grid_html(history[-1]), unsafe_allow_html=True)
    else:
        st.caption("No trajectory_score_history rows yet. Run: "
                   "`python services/trajectory_score.py --once`")


if __name__ == "__main__":
    # headless self-test: render SVG from whatever history exists
    db = _find_intel_db()
    hist = _load_history(db, hours=24 * 30) if db else []
    svg = _rainbow_svg(hist)
    out = ROOT / "rainbow_trajectory_selftest.svg"
    out.write_text(svg, encoding="utf-8")
    print(f"[selftest] {len(hist)} history rows → {out}")
