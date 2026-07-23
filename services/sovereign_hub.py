# SENTINUITY_UI_CRYSTALLINE_GLASS_SIGNOFF_20260624
# SOV_HUB_TRADE_BALANCE_SIGNOFF_V3_MODE_AWARE_CASHFLOW_20260602
# SOV_HUB_TRADE_BALANCE_SIGNOFF_V2_AST_SAFE_20260528
# External delegate retired - BUY/SELL feed renderer is now inline in this file.


# SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
try:
    from birdeye_quota_guard import install_birdeye_requests_guard as _install_birdeye_guard
    _install_birdeye_guard()
except Exception:
    pass
# /SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
# ===========================================================================
# The organism thinking visibly.
# Psilocybin-Rich Synthetic Singularity // Cybernetic Glass Box // Fungal Bloom
# ===========================================================================

import base64
import hashlib
import json
import sqlite3
import time
import html
import os
import re
from urllib.parse import urlparse
from pathlib import Path

# ── SENTINUITY COLOUR DOCTRINE (SIGNOFF_DOCTRINE_MAP_20260624) ───────────────
# Single source of truth for the patched components (pressure core, runner tape).
# Gold is NEVER a routine base fill - it is the earned apex only.
SENTINUITY_COLORS = {
    "void":   "#050210",  # deep-space ground
    "green":  "#14F195",  # Solana mint - buy, pass, system-go
    "purple": "#9945FF",  # market field / resonance / structure
    "cyan":   "#8EF9FF",  # cognition, execution flow, warm-active
    "blue":   "#378ADD",  # cool - learning / evolution
    "red":    "#FF073A",  # sell, veto, loss, stop, live risk
    "gold":   "#FFD700",  # rare earned apex ONLY: true runners >=2x, sign-off, pinned-max
}
# A bar is allowed to show a gold TIP only when it pins at/near max.
SENTINUITY_GOLD_PIN_PCT = 98

# ── token display helper (P2: never render bare n/a) ─────────────────────────
try:
    from token_display import display_for_row, display_name
except Exception:
    try:
        from ui.token_display import display_for_row, display_name
    except Exception:
        _TD_BAD = {"", "n/a", "na", "none", "null", "unknown", "undefined", "-"}
        def _td_clean(v):
            if v is None: return None
            s = str(v).strip(); return s if s.lower() not in _TD_BAD else None
        def display_name(symbol=None, token_name=None, mint=None, metadata_name=None):
            s = _td_clean(mint)
            sm = s if (s is None or len(s) <= 12) else f"{s[:4]}...{s[-4:]}"
            return _td_clean(symbol) or _td_clean(token_name) or _td_clean(metadata_name) or (sm or "unknown")
        def display_for_row(row, *, metadata_name=None):
            d = dict(row) if hasattr(row, "keys") else (row or {})
            return display_name(d.get("symbol") or d.get("token_symbol"),
                                d.get("token_name") or d.get("name"),
                                d.get("mint_address") or d.get("mint") or d.get("token_mint"),
                                metadata_name)

# ── Smart Wallet Conviction Layer (OBSERVE mode, no live influence) ───────────
try:
    from services.smart_wallet_hub import render_smart_wallet_conviction_matrix
    _SMART_WALLET_AVAILABLE = True
except ImportError:
    _SMART_WALLET_AVAILABLE = False

import pandas as pd
import requests as _requests
import streamlit as st
import streamlit.components.v1 as components
try:
    try:
        from services.council_build_map import get_build_map as _get_build_map
    except Exception:
        from council_build_map import get_build_map as _get_build_map
    _BUILD_MAP_AVAILABLE = True
except Exception:
    _BUILD_MAP_AVAILABLE = False

# ── PERFORMANCE GATE: heavy visuals toggle ────────────────────────────────────
# the 3D mycelial nexus and cognitive canopy canvas on slow/mobile devices.
# All visual code is preserved - only render calls are gated.
def _heavy_visuals_enabled() -> bool:
    """
    Returns True unless operator has explicitly disabled heavy visuals.
    Reads HEAVY_VISUALS_ENABLED from system_config (default: 1 = enabled).
    Set to 0 in DB to disable 3D/canvas renders on slow devices.
    Fail-open: any DB error or missing key → visuals ON.
    """
    try:
        import sqlite3 as _sq3h
        _hc = _sq3h.connect(str(DB_PATH), timeout=2)
        _hc.execute("PRAGMA busy_timeout=1000")
        _hv = _hc.execute(
            "SELECT value FROM system_config WHERE key='HEAVY_VISUALS_ENABLED'"
        ).fetchone()
        _hc.close()
        if _hv and str(_hv[0]).lower() in ("false", "0", "no", "off"):
            return False
    except Exception:
        pass
    return True

try:
    from streamlit import fragment
except ImportError:
    def fragment(run_every=None):
        def decorator(func): return func
        return decorator

import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parent.parent
if str(_ROOT) not in _sys.path:
    _sys.path.insert(0, str(_ROOT))

# mycelial_nexus removed - replaced by sovereign world component

# SIGNOFF_TIER1_DOCTRINE_20260716 — dead import removed. Duplicate-render
# trace proved: this aliased import (_render_intelligence_tab) was never
# called, and _INTELLIGENCE_TAB_AVAILABLE was never read. The one live render
# path is _sec_intel() -> ui.intelligence_tab.render_intelligence_tab,
# dispatched at most once per run by the _HUB_SECTIONS registry.

try:
    from services.price_router import get_ui_price as _router_ui_price
    _PRICE_ROUTER_AVAILABLE = True
except ImportError:
    _PRICE_ROUTER_AVAILABLE = False
    def _router_ui_price(mint, entry_price, opened_at): return None


heartbeat_df = pd.DataFrame()  # defensive fallback for moved render blocks


wallet_df = pd.DataFrame()
raw_dna_df = pd.DataFrame()
snapshots_df = pd.DataFrame()
open_pos_df = pd.DataFrame()
executions_df = pd.DataFrame()
reviews_df = pd.DataFrame()
proposals_df = pd.DataFrame()
debate_df = pd.DataFrame()
calibration_df = pd.DataFrame()
open_count_df = pd.DataFrame()
heal_log_df = pd.DataFrame()
heartbeat_df = pd.DataFrame()
patch_history_df = pd.DataFrame()
autopsy_df = pd.DataFrame()
cognition_df = pd.DataFrame()

st.set_page_config(page_title="SENTINUITY SOVEREIGN HUB", layout="wide", initial_sidebar_state="collapsed")

st.markdown('\n<style>\n/* SIGNOFF_STATUS_HIERARCHY_20260716:\n   Healthy/warning telemetry is static. Motion is reserved for critical,\n   operator-confirmation and real-money events. */\n.next-up,\n.arena-msg-polaris,\n.arena-msg-ivaris,\n.arena-msg-oracle,\n.arena-msg-nugget,\n.arena-patch,\n.crail-sigil i,\n.crail-synapse::after,\n.asc-conn.active {\n    animation: none !important;\n}\n.snty-debate-stage [style*="animation:"],\n.snty-crystal-panel [style*="thermalAlive"],\n.snty-crystal-panel [style*="thermalWarm"] {\n    animation: none !important;\n}\n</style>\n', unsafe_allow_html=True)

st.markdown(r"""
<style id="sentinuity-paired-solana-doctrine-v2">
:root{
  --snty-cyan-hi:#8EF9FF;  /* SIGNOFF_DOCTRINE_CYAN_UNIFY_20260718: ramp anchored on doctrine cyan */
  --snty-cyan-mid:#38E1FF;
  --snty-cyan-deep:#12677C;
  --snty-green-hi:#14F195;
  --snty-green-deep:#0B7650;
  --snty-purple-hi:#9945FF;
  --snty-purple-deep:#51228A;
  --snty-gold-hi:#FFD166;
  --snty-gold-deep:#8B681E;
  --snty-copy:#9EC5D2;
  --snty-copy-dim:#668592;
  --snty-heading:#70D7E8;
}

/* White is reserved for truly critical numeric truth only. */
[data-testid="stAppViewContainer"] p,
[data-testid="stAppViewContainer"] li,
[data-testid="stAppViewContainer"] label,
[data-testid="stAppViewContainer"] .stCaption,
[data-testid="stAppViewContainer"] [data-testid="stMarkdownContainer"]{
  color:var(--snty-copy);
}
[data-testid="stAppViewContainer"] h1,
[data-testid="stAppViewContainer"] h2,
[data-testid="stAppViewContainer"] h3,
[data-testid="stAppViewContainer"] h4{
  color:var(--snty-heading);
  text-shadow:0 0 14px rgba(56,225,255,.12);
}
.snty-diag-summary{
  display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;
  margin:8px 0 10px;
}
.snty-diag-summary span{
  min-width:0;padding:9px 10px;border:1px solid rgba(56,225,255,.14);
  border-radius:10px;background:linear-gradient(180deg,rgba(18,103,124,.16),rgba(8,8,18,.32));
  color:var(--snty-copy-dim);font:600 .66rem/1.35 "Share Tech Mono",monospace;
  overflow-wrap:anywhere;
}
.snty-diag-summary b{color:var(--snty-cyan-hi);font-weight:700}

/* Paired-colour doctrine: bright edge/accent + darker same-hue containment. */
.snty-crystal-panel,.snty-glassbox,.snty-card{
  border-color:rgba(56,225,255,.16)!important;
  background:
    linear-gradient(180deg,rgba(18,103,124,.10),rgba(10,6,26,.58))!important;
}
.snty-debate-stage{
  background:
    radial-gradient(circle at 8% 0%,rgba(153,69,255,.10),transparent 34%),
    linear-gradient(180deg,rgba(18,103,124,.10),rgba(5,2,16,.48))!important;
}

/* Mobile becomes a readable operator view, not a scaled desktop poster. */
@media(max-width:768px){
  html{font-size:16px!important}
  .block-container{padding:.55rem .5rem 3rem!important}
  [data-testid="stAppViewContainer"] p,
  [data-testid="stAppViewContainer"] li,
  [data-testid="stAppViewContainer"] label{
    font-size:.82rem!important;line-height:1.5!important;
  }
  .snty-diag-summary{grid-template-columns:1fr 1fr}
  .snty-debate-stage{max-height:420px!important;padding:8px 6px!important}
  .snty-debate-turn{margin-left:0!important;margin-right:0!important;width:auto!important}
  pre,code,.stCode{font-size:.72rem!important;line-height:1.45!important}
}
@media(max-width:412px){
  html{font-size:17px!important}
  .snty-diag-summary{grid-template-columns:1fr}
  [data-testid="stAppViewContainer"] h1{font-size:1.65rem!important}
  [data-testid="stAppViewContainer"] h2{font-size:1.2rem!important}
  [data-testid="stAppViewContainer"] h3{font-size:1rem!important}
  .snty-command-head,.snty-nav-pills{gap:5px!important}
  .snty-command-head a,.snty-nav-pills a{
    min-height:40px!important;display:flex!important;align-items:center!important;
    justify-content:center!important;font-size:.72rem!important;
  }
}
</style>
""", unsafe_allow_html=True)

# ── SIGNOFF_RESPONSIVE_SHELL_20260715 ────────────────────────────────────────
# Canonical mobile/responsive shell. Injected FIRST so later component CSS can
# override specifics but the structural rules (column stacking, overflow
# containment, chip wrapping, mint wrapping, readable minimum type) always hold.
# Breakpoints designed and reasoned at 768 / 412 / 390 / 360 px widths.
# CSS-only: no data, no trading logic, no backend calls.
# SIGNOFF_TIER1_DOCTRINE_20260716: single-source semantic layer (tokens, glow
# budget, heartbeat freshness) from the canonical theme module. Fallback-safe:
# a theme import failure must never take down the hub — the hub simply renders
# without the semantic classes until the module is restored.
try:
    from ui.theme import semantic_css as _sent_semantic_css
    st.markdown(_sent_semantic_css(), unsafe_allow_html=True)
except Exception:
    pass

# SIGNOFF_MATRIX_LIVE_TELEMETRY_20260718 (supersedes HOLOGRAPHIC_SUBSTRATE_20260717)
# Visual-only Truth Fabric rain. Now streams SANITISED live operating telemetry
# (recent cognition-log lines, heartbeat ages, gate counters, executor contract
# state) merged with the fixed contract vocabulary — read-only DB access with a
# hard sanitiser: key/secret/token lines dropped, base58/hex masked, URLs
# stripped, config VALUES never read. Browser-side animation, no trading
# authority. Installed as a singleton so Streamlit reruns cannot multiply
# canvases or animation loops; each rerun refreshes the telemetry payload.
try:
    from ui.cinematic_overlay import inject_holographic_substrate_rain as _inject_substrate_rain
    _inject_substrate_rain()
except Exception:
    pass

st.markdown("""<style>
/* ── structural overflow containment (all widths) ── */
html, body { overflow-x: hidden !important; }
[data-testid="stAppViewContainer"] .block-container { max-width: 100%; box-sizing: border-box; }
[data-testid="stAppViewContainer"] iframe { max-width: 100% !important; }
/* long mint addresses, tx hashes, paths, raw errors: wrap instead of pushing width */
.snty-wrap-any, code, pre, .stCode, [data-testid="stCaptionContainer"],
.sntMint, .snty-mint { overflow-wrap: anywhere !important; word-break: break-word !important; }
/* status chips/pills always wrap as a group instead of shrinking */
.snty-nav-pills, .snty-command-head, .snty-summary-row { flex-wrap: wrap !important; }
/* dataframes/tables: horizontal scroll INSIDE the component, never page overflow */
[data-testid="stDataFrame"], [data-testid="stTable"] { overflow-x: auto !important; max-width: 100% !important; }

/* ── ≤768px: tablet / large phone ── */
@media (max-width: 768px) {
  /* Streamlit columns stack cleanly - no fixed desktop widths survive */
  [data-testid="stHorizontalBlock"] { flex-wrap: wrap !important; gap: .5rem !important; }
  [data-testid="stHorizontalBlock"] > div[data-testid="stColumn"],
  [data-testid="stHorizontalBlock"] > div[data-testid="column"] {
    flex: 1 1 100% !important; width: 100% !important; min-width: 100% !important;
  }
  .block-container { padding-left: .6rem !important; padding-right: .6rem !important; }
  /* decorative background animation off on small devices - readability + perf */
  html::before { animation: none !important; }
  html, body, [data-testid="stAppViewContainer"], .stApp { animation: none !important; }
  .shine-gold::before, .shine-cyan::before, .shine-lattice::before { animation: none !important; display: none !important; }
  .snty-crystal-panel::before { animation: none !important; }
  /* pipeline flow rows scroll inside their strip rather than compressing labels */
  .snty-flow-row { overflow-x: auto !important; justify-content: flex-start !important; }
  .snty-flow-node { min-width: 88px !important; }
}

/* ── ≤412px: primary phone target ── */
@media (max-width: 412px) {
  .block-container { padding-left: .45rem !important; padding-right: .45rem !important; padding-top: .4rem !important; }
  /* minimum readable body text ≈13-14px */
  [data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li { font-size: 13.5px !important; line-height: 1.5 !important; }
  [data-testid="stCaptionContainer"], .stCaption { font-size: 11px !important; }
  [data-testid="stDataFrame"] *, [data-testid="stTable"] * { font-size: 11.5px !important; }
  /* cards keep readable labels - no letter-spacing crush, no clipped values */
  .snty-command-cell .v { white-space: normal !important; overflow: visible !important; text-overflow: clip !important; font-size: .82rem !important; }
  .snty-command-cell { min-height: 0 !important; }
  .snty-section-title { letter-spacing: .14em !important; font-size: .72rem !important; }
  .snty-metric-grid { grid-template-columns: repeat(2, minmax(0,1fr)) !important; gap: 7px !important; }
  /* buy/sell feed rows: compact-card strategy - meta wraps under the title line */
  .sntRow { flex-wrap: wrap !important; }
  .sntMain { min-width: 0 !important; flex: 1 1 70% !important; }
  .sntAge { flex: 0 0 auto !important; }
  /* chips wrap rather than shrink */
  .snty-nav-pills a { flex: 1 1 calc(50% - 7px) !important; text-align: center; }
  /* charts and embedded components use the available width */
  [data-testid="stVegaLiteChart"], [data-testid="stPlotlyChart"], canvas { max-width: 100% !important; }
  .snty-hero-word { letter-spacing: 5px !important; }
}

/* ── ≤390px ── */
@media (max-width: 390px) {
  .snty-command-grid { grid-template-columns: repeat(2, minmax(0,1fr)) !important; }
  .snty-stat-big { font-size: 1.35rem !important; }
  .snty-flow-node { min-width: 82px !important; padding: 8px 7px !important; }
}

/* ── ≤360px: smallest supported ── */
@media (max-width: 360px) {
  .block-container { padding-left: .35rem !important; padding-right: .35rem !important; }
  .snty-metric-grid { grid-template-columns: 1fr !important; }
  .snty-command-grid { grid-template-columns: repeat(2, minmax(0,1fr)) !important; gap: 6px !important; }
  .snty-hero-word { font-size: 2rem !important; letter-spacing: 4px !important; }
  .snty-nav-pills a { flex: 1 1 100% !important; }
}

/* honour user reduced-motion preference at every width */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation: none !important; transition: none !important; }
}
</style>""", unsafe_allow_html=True)
# /SIGNOFF_RESPONSIVE_SHELL_20260715

# SENTINUITY_UI_COUNCIL_CONSENSUS_20260714
# Gemini: mobile-first hierarchy and progressive disclosure.
# Grok: dense operator telemetry without card-wall clutter.
# ChatGPT: single canonical command deck, explicit truth sources and safer status language.
st.markdown("""
<style>
:root{
  --snty-void:#050210;--snty-panel:rgba(9,6,24,.78);--snty-line:rgba(142,249,255,.18);
  --snty-cyan:#8EF9FF; /* SIGNOFF_DOCTRINE_CYAN_UNIFY_20260718: single doctrine cyan (was #38E1FF) */--snty-green:#14F195;--snty-purple:#9945FF;--snty-gold:#FFD700;
  --snty-red:#FF073A;--snty-muted:rgba(207,233,255,.60);
}
/* The official services/sovereign_hub.py owns the visual shell. */
/* SIGNOFF_HERO_UNIFY_20260718: this block owns ONLY the hero WRAP panel.
   The hero WORD + SUB are defined once, in the crystalline-glass block below.
   The former .snty-hero-word override here (font-size/letter-spacing/text-shadow
   !important) double-defined the heading and its text-shadow rendered a blurry
   halo behind the transparent chroma-clip glyphs. Removed — one heading, one rule. */
.snty-hero-wrap{text-align:center!important;padding:34px 18px 22px!important;margin:4px 0 8px!important;border:1px solid rgba(142,249,255,.12)!important;
 background:radial-gradient(circle at 50% 0%,rgba(153,69,255,.18),transparent 58%),linear-gradient(180deg,rgba(7,3,20,.92),rgba(5,2,16,.55))!important;
 border-radius:22px!important;box-shadow:inset 0 0 55px rgba(153,69,255,.07),0 18px 60px rgba(0,0,0,.20)!important}
.snty-hero-sub{max-width:820px;margin:14px auto 8px!important}
.snty-legal{opacity:.48!important;font-size:0.66rem!important;letter-spacing:1.7px!important;margin-top:12px!important}
.snty-command-deck{border:1px solid rgba(142,249,255,.22);border-radius:18px;padding:14px;margin:10px 0 16px;
 background:linear-gradient(135deg,rgba(153,69,255,.10),rgba(5,2,16,.86) 45%,rgba(20,241,149,.045));
 box-shadow:inset 0 0 36px rgba(142,249,255,.025)}
.snty-command-head{display:flex;justify-content:space-between;gap:12px;align-items:center;flex-wrap:wrap;margin-bottom:10px}
.snty-command-title{font-family:Orbitron,sans-serif;font-size:.70rem;letter-spacing:3px;color:var(--snty-cyan);font-weight:800}
.snty-command-kicker{font-family:'Share Tech Mono',monospace;font-size:0.66rem;letter-spacing:1.5px;color:var(--snty-muted)}
.snty-command-grid{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:8px}
.snty-command-cell{min-height:80px;border:1px solid rgba(142,249,255,.13);border-radius:12px;padding:10px 11px;background:rgba(3,2,10,.52)}
.snty-command-cell .k{font-family:'Share Tech Mono',monospace;font-size:0.66rem;letter-spacing:1.6px;color:rgba(207,233,255,.56);text-transform:uppercase}
.snty-command-cell .v{font-family:Orbitron,sans-serif;font-size:clamp(.78rem,1.2vw,1.02rem);margin-top:6px;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-command-cell .s{font-family:'Share Tech Mono',monospace;font-size:0.66rem;color:rgba(207,233,255,.48);margin-top:4px}
.snty-nav-pills{display:flex;gap:7px;flex-wrap:wrap;margin-top:10px}
.snty-nav-pills a{font-family:'Share Tech Mono',monospace;font-size:0.66rem;letter-spacing:1.4px;text-decoration:none!important;color:rgba(235,244,255,.72)!important;
 border:1px solid rgba(153,69,255,.34);padding:7px 10px;border-radius:999px;background:rgba(153,69,255,.06)}
.snty-nav-pills a:hover{border-color:var(--snty-cyan);color:var(--snty-cyan)!important;background:rgba(142,249,255,.06)}
/* Remove the old box-wall feel while preserving every section and data source. */
.snty-crystal-panel,.snty-cyan-panel{border-radius:16px!important;box-shadow:inset 0 0 34px rgba(142,249,255,.025)!important}
.snty-stat-grid{gap:8px!important}.snty-stat-cell{border-radius:11px!important;background:rgba(5,3,16,.44)!important}
[data-testid="stExpander"]{border-radius:14px!important;border-color:rgba(142,249,255,.13)!important;background:rgba(5,3,16,.28)!important}
[data-testid="stExpander"] details summary{font-family:'Share Tech Mono',monospace!important;letter-spacing:1.2px!important}
@media(max-width:900px){
  .block-container{padding-left:.55rem!important;padding-right:.55rem!important;padding-top:.55rem!important}
  .snty-command-grid{grid-template-columns:repeat(3,minmax(0,1fr))}
  .snty-command-cell{min-height:70px;padding:9px}
  .snty-hero-wrap{padding:24px 10px 18px!important;border-radius:16px!important}
  .snty-hero-sub{padding:0 8px!important}.snty-legal{padding:0 12px!important}
}
@media(max-width:520px){
  .snty-command-grid{grid-template-columns:repeat(2,minmax(0,1fr))}
  .snty-command-head{align-items:flex-start}.snty-command-kicker{width:100%}
  .snty-nav-pills a{flex:1 1 calc(50% - 7px);text-align:center}
}
</style>
""", unsafe_allow_html=True)
# /SENTINUITY_UI_COUNCIL_CONSENSUS_20260714


# ── Premium crystalline glass system (CSS-only, no trading logic) ─────────────
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@500;700;900&family=Rajdhani:wght@500;600;700&family=Share+Tech+Mono&display=swap');
/* ===== SENTINUITY CRYSTALLINE GLASS TOKENS - SIGNOFF_20260624 =====
   Visual-only pass: sharper liquid glass, single red token, unified top typography.
   Do not use random per-card fonts/reds above the fold. */
:root{
  --obsidian:#050210;
  --obsidian-raise:#0A0618;
  --obsidian-glass:rgba(7,4,18,0.74);
  --ink-line:rgba(255,255,255,0.075);
  --cyan:#8EF9FF;
  --cyan-soft:rgba(142,249,255,0.62);
  --violet:#9945FF;
  --green:#14F195;
  --magenta:#E879F9;
  --gold:#FFD700;
  --gold-soft:rgba(255,215,0,0.54);
  --danger:#FF073A;
  --danger-soft:rgba(255,7,58,0.72);
  --glass-fill:linear-gradient(142deg,rgba(255,255,255,0.055),rgba(9,6,24,0.80) 32%,rgba(5,2,16,0.92));
  --glass-fill-gold:linear-gradient(145deg,rgba(255,215,0,0.135),rgba(10,7,20,0.80) 38%,rgba(5,2,16,0.94));
  --glass-fill-cyan:linear-gradient(145deg,rgba(142,249,255,0.105),rgba(9,7,28,0.82) 40%,rgba(5,2,16,0.94));
  --glass-rim:rgba(142,249,255,0.42);
  --glass-rim-gold:rgba(255,215,0,0.48);
  --glass-rim-danger:rgba(255,7,58,0.58);
  --glass-blur:12px;
  --glass-sat:1.32;
  --font-hero:'Orbitron',sans-serif;
  --font-ui:'Rajdhani',sans-serif;
  --font-mono:'Share Tech Mono',monospace;
  --fs-title:.82rem;
  --fs-label:.56rem;
  --fs-body:.66rem;
  --fs-micro:.50rem;
  --radius-panel:16px;
  --radius-card:11px;
}
html, body, [data-testid="stAppViewContainer"]{
  background:radial-gradient(circle at 50% 0%,rgba(153,69,255,.15),transparent 28%),
             radial-gradient(circle at 85% 26%,rgba(20,241,149,.055),transparent 22%),
             var(--obsidian) !important;
}
html, body, .stApp, [class*="css"]{font-family:var(--font-ui);}
@keyframes shine-sweep{0%{transform:translateX(-130%) skewX(-17deg)}100%{transform:translateX(360%) skewX(-17deg)}}
@keyframes lattice-drift{0%{background-position:0 0,0 0}100%{background-position:90px 60px,140px 90px}}
@keyframes spectral-edge{0%,100%{filter:hue-rotate(0deg);opacity:.72}50%{filter:hue-rotate(18deg);opacity:.98}}
@keyframes chromaShift{0%{background-position:0% 50%}100%{background-position:300% 50%}}
@keyframes flicker{0%,100%{opacity:1}50%{opacity:.92}75%{opacity:.97}}
.shine-gold,.shine-cyan,.shine-lattice{position:relative;overflow:hidden;}
.shine-gold::before,.shine-cyan::before,.shine-lattice::before{
  content:'';position:absolute;inset:-12% auto -12% -70%;width:52%;pointer-events:none;
  background:linear-gradient(90deg,transparent,rgba(255,255,255,.16),rgba(255,215,0,.30),transparent);
  animation:shine-sweep 3.8s ease-in-out infinite;
}
.snty-crystal-panel{
  position:relative;isolation:isolate;overflow:hidden;border-radius:var(--radius-panel);
  border:1px solid var(--glass-rim);
  background:var(--glass-fill);
  backdrop-filter:blur(var(--glass-blur)) saturate(var(--glass-sat));
  -webkit-backdrop-filter:blur(var(--glass-blur)) saturate(var(--glass-sat));
  box-shadow:inset 0 1px 0 rgba(255,255,255,.12),inset 0 -1px 0 rgba(153,69,255,.10),0 0 0 1px rgba(255,255,255,.025),0 18px 42px rgba(0,0,0,.46);
}
.snty-crystal-panel::before{
  content:'';position:absolute;inset:0;z-index:-1;pointer-events:none;border-radius:inherit;
  background:linear-gradient(135deg,rgba(255,255,255,.14),transparent 15%,transparent 58%,rgba(255,215,0,.08) 76%,transparent),
             repeating-linear-gradient(122deg,rgba(142,249,255,.07) 0 1px,transparent 1px 22px),
             repeating-linear-gradient(32deg,rgba(255,215,0,.045) 0 1px,transparent 1px 34px);
  opacity:.55;animation:lattice-drift 22s linear infinite;
}
.snty-crystal-panel::after{
  content:'';position:absolute;left:0;right:0;top:0;height:1px;pointer-events:none;
  background:linear-gradient(90deg,transparent,var(--violet),var(--cyan),var(--gold),transparent);
  box-shadow:0 0 13px rgba(142,249,255,.45);animation:spectral-edge 5.5s ease-in-out infinite;
}
.snty-gold-panel{border-color:var(--glass-rim-gold);background:var(--glass-fill-gold);box-shadow:inset 0 1px 0 rgba(255,255,255,.14),0 0 0 1px rgba(255,215,0,.12),0 0 28px rgba(255,215,0,.12),0 18px 42px rgba(0,0,0,.46);}
.snty-cyan-panel{border-color:rgba(142,249,255,.42);background:var(--glass-fill-cyan);}
.snty-danger-panel{border-color:var(--glass-rim-danger);box-shadow:inset 0 1px 0 rgba(255,255,255,.10),0 0 22px rgba(255,7,58,.14),0 16px 35px rgba(0,0,0,.42);}
.snty-title-row{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px;padding-bottom:9px;border-bottom:1px solid rgba(255,255,255,.075);}
.snty-title-left{display:flex;align-items:center;gap:8px;min-width:0;}
.snty-section-title{font-family:var(--font-hero);font-size:var(--fs-title);font-weight:900;letter-spacing:.28em;text-transform:uppercase;line-height:1;color:var(--green);text-shadow:0 0 12px rgba(20,241,149,.32);}
.snty-section-title.gold{color:var(--gold);text-shadow:0 0 12px rgba(255,215,0,.38)}
.snty-section-title.cyan{color:var(--cyan);text-shadow:0 0 12px rgba(142,249,255,.38)}
.snty-section-kicker{font-family:var(--font-mono);font-size:var(--fs-micro);letter-spacing:.18em;color:rgba(207,233,255,.56);text-transform:uppercase;white-space:nowrap;}
.snty-helpbox{position:relative;display:inline-flex;align-items:center;z-index:30;}
.snty-helpbox summary{list-style:none;cursor:pointer;display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:999px;border:1px solid rgba(142,249,255,.55);font-family:var(--font-mono);font-size:0.66rem;line-height:1;color:var(--cyan);background:rgba(142,249,255,.08);box-shadow:0 0 10px rgba(142,249,255,.22);}
.snty-helpbox summary::-webkit-details-marker{display:none}
.snty-helpbox .snty-help-pop{display:none;position:absolute;left:0;top:24px;min-width:210px;max-width:260px;padding:9px 10px;border:1px solid rgba(255,215,0,.35);border-radius:10px;background:rgba(5,2,16,.96);color:#D7EEFF;font-family:var(--font-ui);font-size:.74rem;line-height:1.24;letter-spacing:.02em;box-shadow:0 10px 30px rgba(0,0,0,.55),0 0 14px rgba(255,215,0,.12);}
.snty-helpbox[open] .snty-help-pop{display:block;}
.snty-metric-grid{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;}
.snty-metric-card{position:relative;overflow:hidden;border-radius:var(--radius-card);padding:10px 11px;background:linear-gradient(150deg,rgba(255,215,0,.10),rgba(14,9,22,.76));border:1px solid rgba(255,215,0,.34);box-shadow:inset 0 1px 0 rgba(255,255,255,.10),0 0 14px rgba(255,215,0,.075);}
.snty-metric-card::after{content:'';position:absolute;inset:0;pointer-events:none;background:linear-gradient(120deg,transparent 0%,rgba(255,255,255,.10) 7%,transparent 22%,transparent 72%,rgba(142,249,255,.07));opacity:.6;}
.snty-label{font-family:var(--font-mono);font-size:var(--fs-label);letter-spacing:.20em;text-transform:uppercase;color:rgba(233,213,138,.92);}
.snty-stat-value{font-family:var(--font-hero);font-size:clamp(1.05rem,3.5vw,1.34rem);font-weight:900;letter-spacing:.04em;line-height:1.18;color:var(--gold);font-variant-numeric:tabular-nums;text-shadow:0 0 11px rgba(255,215,0,.25);}
.snty-sub{font-family:var(--font-ui);font-size:var(--fs-body);font-weight:600;color:var(--cyan);line-height:1.15;}
/* SIGNOFF_HERO_UNIFY_20260718: wrap panel is owned by the council block above; word+sub live here only. */
.snty-hero-word{margin:0;font-family:var(--font-hero);font-size:clamp(2.2rem,9vw,4.1rem);letter-spacing:clamp(6px,2vw,14px);white-space:nowrap;background:linear-gradient(90deg,#9945FF 0%,#8EF9FF 23%,#14F195 46%,#FFD700 61%,#E879F9 78%,#9945FF 100%);background-size:300% 100%;-webkit-background-clip:text;-webkit-text-fill-color:transparent;animation:chromaShift 5.5s linear infinite;text-shadow:none;filter:drop-shadow(0 0 9px rgba(142,249,255,.20));}
.snty-hero-sub{margin-top:8px;margin-bottom:4px;color:var(--cyan);font-family:var(--font-ui);font-weight:600;font-size:1rem;letter-spacing:.12em;font-style:italic;text-align:center;}
.snty-legal{font-family:var(--font-mono);font-size:0.66rem;letter-spacing:.25em;color:var(--danger);opacity:.86;text-align:center;width:100%;text-shadow:0 0 10px rgba(255,7,58,.24);}
.snty-truth-strip{backdrop-filter:blur(10px) saturate(1.25);-webkit-backdrop-filter:blur(10px) saturate(1.25);background:linear-gradient(90deg,rgba(255,7,58,.075),rgba(5,2,16,.72),rgba(255,215,0,.045));box-shadow:inset 0 1px 0 rgba(255,255,255,.08),0 0 20px rgba(255,7,58,.13);}
.snty-stat-grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:16px;}
.snty-stat-cell{min-width:0;padding:2px 8px;border-left:1px solid rgba(142,249,255,.075);}
.snty-stat-cell:first-child{border-left:0;}
.snty-stat-big{font-family:var(--font-hero);font-size:clamp(1.45rem,5vw,2.1rem);font-weight:900;line-height:1.08;letter-spacing:.04em;font-variant-numeric:tabular-nums;}
.snty-flow-row{display:flex;align-items:stretch;justify-content:center;gap:0;flex-wrap:nowrap;overflow-x:auto;scrollbar-width:none;}
.snty-flow-row::-webkit-scrollbar{display:none;}
.snty-flow-node{display:flex;flex-direction:column;align-items:center;justify-content:center;padding:10px 9px;min-width:92px;border-radius:12px;border:1px solid color-mix(in srgb,var(--node-color) 48%,transparent);background:linear-gradient(145deg,rgba(255,255,255,.055),rgba(5,2,16,.78));box-shadow:inset 0 1px 0 rgba(255,255,255,.09),0 0 14px color-mix(in srgb,var(--node-color) 24%,transparent);}
.snty-flow-icon{font-size:1rem;margin-bottom:2px;filter:drop-shadow(0 0 7px color-mix(in srgb,var(--node-color) 42%,transparent));}
.snty-flow-title{font-family:var(--font-mono);font-size:0.66rem;letter-spacing:.18em;color:var(--node-color);margin-bottom:4px;text-transform:uppercase;}
.snty-flow-value{font-family:var(--font-hero);font-size:.82rem;font-weight:900;color:var(--node-color);line-height:1.1;text-align:center;}
.snty-flow-sub{font-family:var(--font-ui);font-size:0.66rem;color:rgba(207,233,255,.58);margin-top:3px;text-align:center;}
.snty-flow-arrow{display:flex;align-items:center;justify-content:center;padding:0 4px;font-size:.9rem;opacity:.62;text-shadow:0 0 8px currentColor;}
.snty-summary-row{display:flex;justify-content:center;gap:18px;flex-wrap:wrap;margin-top:11px;font-family:var(--font-mono);font-size:0.66rem;color:rgba(207,233,255,.48);letter-spacing:.13em;text-transform:uppercase;}
@media (max-width:720px){
  .snty-metric-grid{grid-template-columns:repeat(2,minmax(0,1fr));}
  .snty-stat-grid{grid-template-columns:1fr;gap:10px;}
  .snty-stat-cell{border-left:0;border-top:1px solid rgba(142,249,255,.075);padding-top:10px;}
  .snty-stat-cell:first-child{border-top:0;padding-top:0;}
  .snty-section-title{letter-spacing:.20em;}
}

/* SNTY_GLASS_BUTTONS_V1 */
.stButton>button,.stDownloadButton>button,.stFormSubmitButton>button{position:relative;overflow:hidden;font-family:var(--font-mono,'Share Tech Mono',monospace)!important;letter-spacing:.14em!important;text-transform:uppercase!important;font-size:.72rem!important;font-weight:700!important;color:#CFE9FF!important;border-radius:10px!important;border:1px solid rgba(142,249,255,.42)!important;background:linear-gradient(142deg,rgba(142,249,255,.10),rgba(9,6,24,.82) 40%,rgba(5,2,16,.94))!important;backdrop-filter:blur(10px) saturate(1.25);-webkit-backdrop-filter:blur(10px) saturate(1.25);box-shadow:inset 0 1px 0 rgba(255,255,255,.10),0 0 14px rgba(142,249,255,.10),0 8px 22px rgba(0,0,0,.40)!important;transition:transform .16s ease,box-shadow .16s ease,border-color .16s ease!important;}.stButton>button:hover,.stDownloadButton>button:hover,.stFormSubmitButton>button:hover{transform:translateY(-1px);border-color:rgba(142,249,255,.72)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.14),0 0 20px rgba(142,249,255,.22),0 10px 26px rgba(0,0,0,.46)!important;color:#EAFBFF!important;}.stButton>button:active,.stFormSubmitButton>button:active{transform:translateY(0);}.stButton>button::after,.stFormSubmitButton>button::after{content:'';position:absolute;left:0;right:0;top:0;height:1px;background:linear-gradient(90deg,transparent,var(--violet,#9945FF),var(--cyan,#8EF9FF),var(--gold,#FFD700),transparent);opacity:0;transition:opacity .16s ease;}.stButton>button:hover::after,.stFormSubmitButton>button:hover::after{opacity:.9;}.stFormSubmitButton>button{border-color:rgba(255,215,0,.48)!important;color:#F4E8B8!important;background:linear-gradient(145deg,rgba(255,215,0,.135),rgba(10,7,20,.80) 38%,rgba(5,2,16,.94))!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.14),0 0 18px rgba(255,215,0,.16),0 8px 22px rgba(0,0,0,.42)!important;}.stFormSubmitButton>button:hover{border-color:rgba(255,215,0,.80)!important;box-shadow:inset 0 1px 0 rgba(255,255,255,.18),0 0 26px rgba(255,215,0,.30),0 10px 26px rgba(0,0,0,.48)!important;}.snty-danger-btn .stButton>button{border-color:rgba(255,7,58,.58)!important;color:#FFD0D8!important;background:linear-gradient(142deg,rgba(255,7,58,.12),rgba(9,6,24,.82) 40%,rgba(5,2,16,.94))!important;}

/* SNTY_GLASS_CARD_V1 */
.snty-card{position:relative;overflow:hidden;border-radius:13px;border:1px solid rgba(142,249,255,.30);background:linear-gradient(150deg,rgba(255,255,255,.045),rgba(9,6,24,.82) 34%,rgba(5,2,16,.93));backdrop-filter:blur(11px) saturate(1.28);-webkit-backdrop-filter:blur(11px) saturate(1.28);box-shadow:inset 0 1px 0 rgba(255,255,255,.10),inset 0 -1px 0 rgba(153,69,255,.08),0 10px 28px rgba(0,0,0,.42);}.snty-card::before{content:'';position:absolute;inset:0;z-index:0;pointer-events:none;border-radius:inherit;background:linear-gradient(135deg,rgba(255,255,255,.10),transparent 16%,transparent 60%,rgba(255,215,0,.06) 78%,transparent);opacity:.5;}.snty-card>*{position:relative;z-index:1;}.snty-card.gold{border-color:rgba(255,215,0,.46);background:linear-gradient(145deg,rgba(255,215,0,.115),rgba(10,7,20,.80) 38%,rgba(5,2,16,.94));}.snty-card.danger{border-color:rgba(255,7,58,.52);}
</style>
""", unsafe_allow_html=True)



# ── DIRECT INTEL PRICE READ (Same-Eyes v2) ────────────────────────────────────
def _get_fresh_intel_price(mint: str):
    """
    Read the latest oracle tick directly from sentinuity_intelligence.db.
    Bypasses the engine's 2.2s write cycle - gives the meter direct oracle visibility.
    Returns (price_usd, age_seconds) or (None, None).
    """
    try:
        _idb = str(Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db")
        with sqlite3.connect(_idb, timeout=1.0) as _ic:
            _row = _ic.execute(
                "SELECT price_usd, ts_ms FROM mtm_ticks "
                "WHERE mint_address=? ORDER BY ts_ms DESC LIMIT 1",
                (mint,)
            ).fetchone()
            if _row and _row[0]:
                _age = time.time() - float(_row[1]) / 1000.0
                return float(_row[0]), _age
    except Exception:
        pass
    return None, None


# ── PHANTOM WALLET CONNECT ────────────────────────────────────────────────────
def _write_phantom_state(wallet_address: str, trading_mode: str = "") -> None:
    """
    Write Phantom wallet address to DB.
    NEVER overwrites TRADING_MODE - that is set only by set_live_mode.py / operator.
    """
    try:
        import sqlite3
        from pathlib import Path as _P
        _db = str(_P(__file__).resolve().parent.parent / "sentinuity_matrix.db")
        _c = sqlite3.connect(_db, timeout=2.0)
        _c.execute(
            "INSERT OR REPLACE INTO system_config (key,value,description) "
            "VALUES ('PHANTOM_WALLET_ADDRESS',?,'Connected Phantom wallet address')",
            (wallet_address,)
        )
        # NOTE: TRADING_MODE is intentionally NOT written here.
        # Use set_live_mode.py to switch between paper and live.
        _c.commit()
        _c.close()
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# SHARED HEALTH TRUTH - single source for both HUD strip and Vitality table
# Core services (red if stale): executor, supervisor, market_intelligence,
#   oracle_autoheal / ws_price_oracle, polaris, sovereign_governor
# Scout services (amber only): telegram, x_scout, wallet_scout, copytrade
# ─────────────────────────────────────────────────────────────────────────────
CORE_SERVICES = {
    "execution_engine":  "EXECUTOR",
    "neural_supervisor": "SUPERVISOR",
    "market_intelligence": "MARKET_INTEL",
    "oracle_autoheal":   "ORACLE",
    "ws_price_oracle":   "ORACLE",
    "polaris":           "POLARIS",
    "sovereign_governor":"GOVERNOR",
    "system_guardian":   "GUARDIAN",
}
SCOUT_SERVICES = {"telegram_scout","x_scout","wallet_scout","reconnaissance_engine","code_vault"}

def _get_service_health(query_db_fn=None):
    """
    Returns dict: service_name → {status, age_s, is_core, is_stale, label}
    Stale threshold: 120s for core, 420s for scouts.
    """
    import time as _t, sqlite3 as _sq
    now = _t.time()
    result = {}
    try:
        _db = _sq.connect(str(DB_PATH), timeout=2.0)
        _db.row_factory = _sq.Row
        rows = _db.execute(
            "SELECT service_name, last_pulse, status, note FROM system_heartbeat ORDER BY last_pulse DESC"
        ).fetchall()
        _db.close()
        for r in rows:
            svc   = str(r["service_name"] or "")
            pulse = float(r["last_pulse"] or 0)
            age   = now - pulse if pulse else 9999
            stat  = str(r["status"] or "UNKNOWN").upper()
            note  = str(r["note"] or "")[:60]
            is_core  = svc in CORE_SERVICES
            is_scout = svc in SCOUT_SERVICES
            try:
                from ui.theme import service_heartbeat_thresholds
                _fresh_sec, threshold = service_heartbeat_thresholds(svc)
            except Exception:
                threshold = 120 if is_core else 420
            is_stale = age > threshold
            if svc not in result or age < result[svc]["age_s"]:
                result[svc] = {
                    "status":    stat,
                    "age_s":     age,
                    "note":      note,
                    "is_core":   is_core,
                    "is_scout":  is_scout,
                    "is_stale":  is_stale,
                    "label":     CORE_SERVICES.get(svc, svc.upper().replace("_"," ")),
                }
    except Exception:
        pass
    return result

def _hud_health_state(health: dict):
    """
    Returns (is_ok, color, label) for the top HUD strip.
    Only core services can turn HUD red.
    """
    stale_core = [v for v in health.values() if v["is_core"] and v["is_stale"]]
    if not health:
        return False, "#FFD700", "⚠ AWAITING HEARTBEAT - SERVICES STARTING"
    if stale_core:
        names = " · ".join(v["label"] for v in stale_core[:2])
        return False, "#FF073A", f"⚠ SENSORY DISSONANCE - {names} STALE/OFFLINE"
    return True, "#14F195", "✓ ORGANISM SYNCED - ALL FEEDS FRESH"

def render_phantom_connect():
    import streamlit.components.v1 as _components
    _waddr, _tmode = "", "paper"
    try:
        _r = query_db("SELECT key, value FROM system_config WHERE key IN ('PHANTOM_WALLET_ADDRESS','TRADING_MODE')")
        if not _r.empty:
            for _, _row in _r.iterrows():
                if _row["key"] == "PHANTOM_WALLET_ADDRESS": _waddr = str(_row["value"] or "")
                if _row["key"] == "TRADING_MODE": _tmode = str(_row["value"] or "paper")
    except Exception: pass
    _short  = f"{_waddr[:4]}...{_waddr[-4:]}" if len(_waddr) > 8 else ""
    _is_live = _tmode == "live"
    _bc = "#14F195" if _short else "#9945FF"
    _ml, _mc = ("LIVE","#14F195") if _is_live else ("PAPER","#FFD700")
    _bl = f"◉ {_short}" if _short else "Connect Phantom"
    _conn_js = "true" if _short else "false"
    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
    try:
        _runner_gold_pct = 75.0
        try:
            if isinstance(locals().get("row"), dict):
                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
        except Exception:
            _runner_gold_pct = 75.0
        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
            _state = "RUNNER"
            _state_col = "#FFD700"
    except Exception:
        pass

    _html = f"""<style>
*{{box-sizing:border-box;margin:0;padding:0;}}
body{{background:transparent;font-family:'Share Tech Mono',monospace;}}
#w{{display:flex;align-items:center;gap:8px;justify-content:flex-end;padding:4px 0;}}
#mb{{font-size:11px;letter-spacing:2px;padding:3px 10px;border-radius:999px;border:1px solid {_mc}44;color:{_mc};cursor:pointer;}}
#mb:hover{{background:{_mc}22;}}
#pb{{font-family:'Share Tech Mono',monospace;font-size:11px;letter-spacing:1.5px;padding:6px 14px;border-radius:8px;border:1px solid {_bc}88;background:{_bc}18;color:{_bc};cursor:pointer;transition:all .2s;}}
#pb:hover{{background:{_bc}33;border-color:{_bc};}}
#pb:active{{transform:scale(.97);}}
#st{{font-size:11px;color:#8EF9FF;letter-spacing:1px;}}
#er{{font-size:11px;color:#FF073A;letter-spacing:1px;display:none;}}
</style>
<div id="w">
  <span id="er">No Phantom found</span>
  <span id="st">{("Connected ✓" if _short else "")}</span>
  <span id="mb" onclick="toggleMode()">{_ml} MODE</span>
  <button id="pb" onclick="handlePhantom()">{_bl}</button>
</div>
<script>
let CONN={_conn_js},ADDR="{_waddr}",MODE="{_tmode}";
const short=a=>a.slice(0,4)+'...'+a.slice(-4);
const setSt=(m,c)=>{{const s=document.getElementById('st');s.textContent=m;s.style.color=c||'#8EF9FF';}};
const send=v=>window.parent.postMessage({{type:'streamlit:setComponentValue',value:JSON.stringify(v)}},'*');

async function handlePhantom(){{
  const pb=document.getElementById('pb'),er=document.getElementById('er');
  if(!window.solana||!window.solana.isPhantom){{er.style.display='inline';setTimeout(()=>er.style.display='none',3000);return;}}
  if(CONN){{
    try{{await window.solana.disconnect();}}catch(e){{}}
    CONN=false;ADDR='';
    pb.textContent='Connect Phantom';pb.style.borderColor='#9945FF88';pb.style.color='#9945FF';
    setSt('Disconnected','#FF073A');send({{action:'disconnect',value:''}});
  }}else{{
    try{{
      pb.textContent='Connecting...';
      const r=await window.solana.connect();
      ADDR=r.publicKey.toString();CONN=true;
      pb.textContent=short(ADDR);pb.style.borderColor='#14F19588';pb.style.color='#14F195';
      setSt('Connected ✓','#14F195');send({{action:'connect',value:ADDR}});
    }}catch(e){{pb.textContent='Connect Phantom';setSt('Cancelled','#FFD700');}}
  }}
}}

async function toggleMode(){{
  const mb=document.getElementById('mb');
  const newMode=MODE==='live'?'paper':'live';
  if(newMode==='live'&&!ADDR){{setSt('Connect wallet first','#FF073A');setTimeout(()=>setSt(''),2000);return;}}
  MODE=newMode;
  mb.textContent=newMode.toUpperCase()+' MODE';
  const mc=newMode==='live'?'#14F195':'#FFD700';
  mb.style.color=mc;mb.style.borderColor=mc+'44';
  setSt(newMode.toUpperCase()+' mode','#8EF9FF');
  send({{action:'mode',value:newMode}});
}}
</script>"""
    _result = _components.html(_html, height=42, scrolling=False)
    if _result:
        try:
            import json
            _d = json.loads(_result) if isinstance(_result, str) else _result
            _a, _v = _d.get("action",""), _d.get("value","")
            if _a == "connect" and _v: _write_phantom_state(_v, _tmode); st.rerun()
            elif _a == "disconnect": _write_phantom_state("", _tmode); st.rerun()
            elif _a == "mode": _write_phantom_state(_waddr, _v); st.rerun()
        except Exception: pass

C_VOID   = "#050210"
C_GREEN  = "#14F195"
C_GOLD   = "#FFD700"
C_RED    = "#FF073A"
C_PURPLE = "#9945FF"
C_CYAN   = SENTINUITY_COLORS["cyan"]  # SIGNOFF_DOCTRINE_CYAN_UNIFY_20260718: doctrine map is the single cyan; supersedes 20260715 #38E1FF variant
C_IVY    = "#FFB347"
C_NUGGET = "#C19A6B" # Boxer Fawn / Bronze


# ── MODE DISPLAY TRUTH - dual is an effective state, not only TRADING_MODE ──
def _snty_truthy_cfg(v, default: bool = False) -> bool:
    """Parse DB string flags without changing runtime behaviour."""
    if v is None:
        return bool(default)
    return str(v).strip().lower() in ("1", "true", "yes", "on", "enabled", "live", "dual")

def _snty_effective_mode(conf_map: dict | None = None) -> tuple[str, str, str]:
    """
    Return (label, colour, detail) for UI identity only.

    Runtime still owns execution behaviour. This fixes the dashboard bug where
    Launch dual mode can keep TRADING_MODE='paper' for safety while arming a
    separate live Mode-B / shadow gate, causing System Identity to show PAPER.
    """
    c = conf_map or {}
    raw = str(c.get("TRADING_MODE", "paper") or "paper").strip().lower()
    paper_on = _snty_truthy_cfg(c.get("PAPER_TRADING_ENABLED"), raw != "live")
    live_on = _snty_truthy_cfg(c.get("LIVE_TRADING_ENABLED"))
    mode_b = any(_snty_truthy_cfg(c.get(k)) for k in (
        "LIVE_MODE_B_ENABLED", "MODE_B_ENABLED", "LIVE_MODE_B_GATE_ENABLED",
        "MODE_B_LIVE_ENABLED", "DUAL_MODE_ENABLED", "DUAL_MODE_ARMED",
    ))
    shadow_on = _snty_truthy_cfg(c.get("LIVE_PAPER_SHADOW_ON_BLOCK"), True)

    if raw in ("dual", "paper+live", "paper_live", "live_shadow"):
        return "DUAL", C_GOLD, "PAPER LEARNING + LIVE MODE-B SHADOW"
    if raw == "live" and (paper_on or shadow_on or mode_b):
        return "DUAL", C_GOLD, "LIVE GATE ARMED · PAPER SHADOW ON"
    if paper_on and (live_on or mode_b):
        return "DUAL", C_GOLD, "PAPER LEARNING + LIVE MODE-B ARMED"
    if raw == "live":
        return "LIVE", C_RED, "REAL WALLET MODE"
    return "PAPER", C_GREEN, "PAPER LEARNING"

AGENT_EMOJIS = {
    "POLARIS": "❄️",
    "IVARIS":  "🔥",
    "ORACLE":  "🔎",
    "NUGGET":  "🔮",
    "AXON":    "⚡",
    "RHIZA":   "🕸️",
}

THEMATIC_STATES = {
    "loop_detected":       "Calcified Ego",
    "blocked":             "Cognitive Decalcification Required",
    "topic_lock_failed":   "Cognitive Decalcification Required",
    "evaluating":          "Mycelial Network Synthesizing",
    "rebuttal_evaluation": "Mycelial Network Synthesizing",
    "critiquing":          "Immune System Engaged",
    "rebutting":           "Root System Responding",
    "consensus":           "Harmonic Convergence",
    "final_consensus":     "Harmonic Convergence",
    "rejected":            "Signal Rejected - Substrate Insufficient",
    "final_rejection":     "Signal Rejected - Substrate Insufficient",
    "searching":           "Piping External Resonance",
    "oracle_evidence":     "External Truth Integrated",
    "nugget_audit":        "Independent Audit Complete",
    "approval":            "Patch Absorbed Into Memory",
    "pending_hitl":        "Awaiting Operator Nerve Signal",
    "ascending":           "Cognitive Ascension In Progress",
    "golden_mastery":      "Masterpiece Forged - Awaiting Seal",
    "forge_pending":       "Cognitive Ascension In Progress",
    "forge_complete":      "Masterpiece Forged - Awaiting Seal",
}

def _thematic_label(state: str) -> str:
    return THEMATIC_STATES.get(str(state).lower(), str(state).upper())

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"


# ── INTELLIGENCE SUBSTRATE DATABASES ────────────────────────────────────────
# Main trading UI reads sentinuity_matrix.db. These optional read-only substrate DBs
# let Copy Trade, Telegram Scout, and Dataset/Sensory systems write elsewhere so
# the hub can observe them without adding contention to the execution DB.
INTEL_DB_CANDIDATES = [
    Path(os.environ.get("SENTINUITY_INTEL_DB", "")) if os.environ.get("SENTINUITY_INTEL_DB") else None,
    ROOT / "sentinuity_intelligence.db",
    ROOT / "intelligence_substrate.db",
    ROOT / "copytrade_scout.db",
    ROOT / "telegram_scout.db",
    ROOT / "sensory_substrate.db",
    ROOT / "data" / "sentinuity_intelligence.db",
    ROOT / "data" / "copytrade_scout.db",
    ROOT / "data" / "telegram_scout.db",
    ROOT / "data" / "sensory_substrate.db",
]
INTEL_DB_PATHS = []
for _p in INTEL_DB_CANDIDATES:
    try:
        if _p and _p.exists() and _p.is_file() and _p not in INTEL_DB_PATHS:
            INTEL_DB_PATHS.append(_p)
    except Exception:
        pass

TRUSTED_LINK_DOMAINS = {
    "x.com", "twitter.com", "github.com", "raw.githubusercontent.com", "gist.github.com",
    "helius.xyz", "dev.helius.xyz", "solscan.io", "birdeye.so", "dexscreener.com",
    "pump.fun", "pumpdotfun.com", "telegram.org", "t.me",
}

if "organism_awake" not in st.session_state: st.session_state["organism_awake"] = True
if "truth_lens_open" not in st.session_state: st.session_state["truth_lens_open"] = False
if "hub_refresh_seconds" not in st.session_state: st.session_state["hub_refresh_seconds"] = 30
# ── WORLD MODE: default OFF for fast mobile load ─────────────────────────────
# World visuals (iframe, RAF loop, world HTML) are NOT mounted unless toggled ON.
if "world_mode_enabled" not in st.session_state:
    st.session_state["world_mode_enabled"] = False




@st.cache_data(ttl=15, show_spinner=False)


@st.cache_data(ttl=20, show_spinner=False)


def _fix_display_mojibake(text: str) -> str:
    """SIGNOFF_DISPLAY_MOJIBAKE_REPAIR_20260714.

    Rows written while services ran without UTF-8 mode may carry persisted
    UTF-8-as-cp1252 mojibake (e.g. corrupted arrows/icons). Repair is strictly
    conservative: it only runs when corruption signatures are present, only
    accepts a candidate that round-trips losslessly AND reduces the signature
    count, and never mutates the database -- display-side only.
    """
    if not text or not isinstance(text, str):
        return text
    _leads = ("\u00e2", "\u00c3", "\u00c2", "\u00f0\u0178", "\ufffd")
    def _susp(s: str) -> int:
        return sum(s.count(ch) for ch in _leads)
    if not _susp(text):
        return text
    cur = text
    for _ in range(3):
        try:
            cand = cur.encode("cp1252").decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            break
        if _susp(cand) < _susp(cur):
            cur = cand
        else:
            break
    return cur


def _sanitize_exception_text(text: str) -> str:
    """Remove raw Python exceptions from public UI. Internal logging only."""
    if not text or not isinstance(text, str):
        return "awaiting valid payload"
    text = _fix_display_mojibake(text)
    text_lower = text.lower()
    if any(x in text_lower for x in ["exception:", "traceback", "nonetype", "not subscriptable", 
                                       "keyerror", "valueerror", "attributeerror"]):
        return "skipped malformed payload"
    return text[:200]  # cap length


def get_agent_status_snapshot() -> dict:
    """
    CANONICAL six-node council + support-system status.

    Council identity stays stable:
      POLARIS, IVARIS, NUGGET, ORACLE, AXON, RHIZA.

    Model assignment can evolve/devolve per task and is shown separately from identity.
    Returns each node with:
      status, age, note, role, service_name, current_model, model_tier, evolution_state,
      authority_level, is_support.
    """
    import time as _ast, sqlite3 as _asq, json as _json
    _now = _ast.time()

    _council_defaults = {
        "POLARIS": {
            "icon": "❄️", "color": "#8EF9FF", "service_name": "polaris",
            "role": "planner / coordinator", "authority_level": "final_planner",
            "current_model": "gpt-5.4-mini", "model_tier": "signoff", "evolution_state": "baseline",
            "aliases": ["polaris", "neural_supervisor"],
        },
        "IVARIS": {
            "icon": "🔥", "color": "#FF6B35", "service_name": "polaris_auxiliary",
            "role": "adversarial critic / safety reviewer", "authority_level": "reviewer",
            "current_model": "claude-opus", "model_tier": "critic", "evolution_state": "baseline",
            "aliases": ["ivaris", "polaris_auxiliary", "market_intelligence", "intelligence_orchestrator"],
        },
        "NUGGET": {
            "icon": "🔮", "color": "#C19A6B", "service_name": "reconnaissance_engine",
            "role": "auditor / assertion runner", "authority_level": "auditor",
            "current_model": "nim-nano", "model_tier": "fast_scan", "evolution_state": "baseline",
            "aliases": ["nugget", "reconnaissance_engine", "forge_code_writer", "github_scout", "alpha_forge"],
        },
        "ORACLE": {
            "icon": "🔎", "color": "#14F195", "service_name": "ws_price_oracle",
            "role": "external senses / market, wallet, price truth", "authority_level": "sensor",
            "current_model": "oracle-scout", "model_tier": "sensor", "evolution_state": "baseline",
            "aliases": ["oracle", "oracle_autoheal", "ws_price_oracle", "macro_price_feed", "wallet_scout", "x_scout", "telegram_scout"],
        },
        "AXON": {
            "icon": "⚡", "color": "#14F195", "service_name": "execution_engine",
            "role": "execution validator / motor output", "authority_level": "execution_validator",
            "current_model": "axon-runtime", "model_tier": "runtime_guard", "evolution_state": "baseline",
            "aliases": ["axon", "execution_engine", "trade_executor", "swap_executor"],
        },
        "RHIZA": {
            "icon": "🕸️", "color": "#9945FF", "service_name": "symbiotic_router",
            "role": "synthesis / memory / pattern integrator", "authority_level": "synthesizer",
            "current_model": "grok-current", "model_tier": "synthesis", "evolution_state": "baseline",
            "aliases": ["rhiza", "symbiotic_router", "replay_engine", "cognition_engine", "memory_weaver"],
        },
    }

    _support_defaults = {
        "GOVERNOR": {
            "icon": "⬡", "color": "#9945FF", "service_name": "sovereign_governor",
            "role": "constitutional orchestrator / policy controller", "authority_level": "support",
            "current_model": "local-rules", "model_tier": "orchestrator", "evolution_state": "support",
            "aliases": ["sovereign_governor", "governor"],
        },
        "GUARDIAN": {
            "icon": "🛡", "color": "#FFD700", "service_name": "system_guardian",
            "role": "runtime health / watchdog / restart proof", "authority_level": "support",
            "current_model": "runtime-rules", "model_tier": "watchdog", "evolution_state": "support",
            "aliases": ["system_guardian", "guardian", "risk_guardian"],
        },
        "GOLDEN_LATTICE": {
            "icon": "⬡", "color": "#FFD700", "service_name": "golden_lattice",
            "role": "operator approval gate for high-risk apply", "authority_level": "operator_gate",
            "current_model": "human-approval", "model_tier": "operator_gate", "evolution_state": "support",
            "aliases": ["golden_lattice", "code_vault"],
        },
        "AXIOM_NIM": {
            "icon": "◇", "color": "#C19A6B", "service_name": "nim_doctrine",
            "role": "specialist model library, not a council seat", "authority_level": "support",
            "current_model": "nim-library", "model_tier": "specialist_pool", "evolution_state": "support",
            "aliases": ["nim_doctrine", "axiom", "nvidia_nim"],
        },
    }

    def _base_payload(name: str, default: dict, is_support: bool) -> dict:
        return {
            "status": "OFFLINE", "age": 9999, "note": "no heartbeat",
            "role": default.get("role", ""), "service_name": default.get("service_name", ""),
            "current_model": default.get("current_model", "-"),
            "model_tier": default.get("model_tier", "-"),
            "evolution_state": default.get("evolution_state", "baseline"),
            "authority_level": default.get("authority_level", ""),
            "icon": default.get("icon", "◈"), "color": default.get("color", "#8EF9FF"),
            "is_support": is_support,
        }

    snapshot = {k: _base_payload(k, v, False) for k, v in _council_defaults.items()}
    snapshot.update({k: _base_payload(k, v, True) for k, v in _support_defaults.items()})

    try:
        conn = _asq.connect(str(DB_PATH), timeout=2)
        conn.execute("PRAGMA busy_timeout=1000")
        conn.row_factory = _asq.Row

        def _table_exists(_name: str) -> bool:
            try:
                return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_name,)).fetchone() is not None
            except Exception:
                return False

        # Pull registered model/role state if the autonomous pack has seeded it.
        if _table_exists("council_role_registry"):
            for row in conn.execute("SELECT * FROM council_role_registry").fetchall():
                name = str(row["agent_name"] or row.get("display_name", "") if hasattr(row, "get") else row["agent_name"]).upper()
                if name in snapshot:
                    for key in ["role", "service_name", "authority_level", "current_model", "model_tier", "evolution_state"]:
                        try:
                            val = row[key]
                            if val not in (None, ""):
                                snapshot[name][key] = str(val)
                        except Exception:
                            pass

        # Latest model assignment wins for display. Supports both the legacy
        # selected_model schema and the canonical NVIDIA registry model_id schema.
        if _table_exists("council_model_assignments"):
            try:
                _cols = {str(r[1]) for r in conn.execute(
                    'PRAGMA table_info("council_model_assignments")'
                ).fetchall()}
                _model_col = "model_id" if "model_id" in _cols else (
                    "selected_model" if "selected_model" in _cols else None
                )
                if _model_col:
                    _tier_expr = "model_tier" if "model_tier" in _cols else "''"
                    _evo_expr = "evolution_state" if "evolution_state" in _cols else "''"
                    _reason_expr = (
                        "assignment_reason" if "assignment_reason" in _cols
                        else ("reason" if "reason" in _cols else "''")
                    )
                    _assigned_expr = "assigned_at" if "assigned_at" in _cols else "0"
                    rows = conn.execute(f"""
                        SELECT agent_name, {_model_col} AS active_model,
                               {_tier_expr} AS model_tier,
                               {_evo_expr} AS evolution_state,
                               {_assigned_expr} AS assigned_at,
                               {_reason_expr} AS reason
                        FROM council_model_assignments
                        ORDER BY COALESCE({_assigned_expr}, 0) DESC
                    """).fetchall()
                    seen = set()
                    for row in rows:
                        name = str(row["agent_name"] or "").upper()
                        if name in snapshot and name not in seen:
                            seen.add(name)
                            if row["active_model"]:
                                snapshot[name]["current_model"] = str(row["active_model"])
                            if row["model_tier"]:
                                snapshot[name]["model_tier"] = str(row["model_tier"])
                            if row["evolution_state"]:
                                snapshot[name]["evolution_state"] = str(row["evolution_state"])
                            snapshot[name]["note"] = str(row["reason"] or snapshot[name]["note"])[:80]
            except Exception:
                pass

        # Heartbeat by aliases: newest alive alias controls visible status.
        for name, default in {**_council_defaults, **_support_defaults}.items():
            aliases = list(default.get("aliases", []))
            svc = snapshot[name].get("service_name")
            if svc and svc not in aliases:
                aliases.insert(0, svc)
            best_age, best_note, best_status = None, "", "OFFLINE"
            for alias in aliases:
                try:
                    r = conn.execute(
                        "SELECT last_pulse, status, note FROM system_heartbeat WHERE service_name=? ORDER BY last_pulse DESC LIMIT 1",
                        (alias,)
                    ).fetchone()
                    if r:
                        age = _now - float(r["last_pulse"] or 0)
                        if best_age is None or age < best_age:
                            best_age = age
                            best_note = str(r["note"] or "")[:80]
                            best_status = str(r["status"] or "").upper() or "ACTIVE"
                except Exception:
                    pass
            if best_age is not None:
                if best_age <= 90:
                    stat = "ACTIVE"
                elif best_age <= 300:
                    stat = "STALE"
                else:
                    stat = "OFFLINE"
                snapshot[name].update({"status": stat, "age": int(best_age), "note": best_note or best_status or "online"})
        conn.close()
    except Exception:
        for a in snapshot:
            snapshot[a]["status"] = "UNKNOWN"
            snapshot[a]["age"] = 0
            snapshot[a]["note"] = "DB read failed"

    return snapshot

# ── SOV HUB ADDENDUM: safe timestamp helper ──────────────────────────────────
def _to_epoch(value) -> float:
    """Convert epoch float OR ISO string timestamp to a float epoch.
    Never crashes; returns current time as fallback so callers don't silently
    drop events because the timestamp column changed format."""
    import time as _te, re as _re
    if value is None:
        return _te.time()
    try:
        return float(value)
    except (ValueError, TypeError):
        pass
    s = str(value).strip()
    # ISO 8601 variants: 2026-05-26T12:34:56, 2026-05-26 12:34:56[.fff][Z/+offset]
    for fmt in ("%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S",
                "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            import calendar, datetime as _dt
            # Strip timezone suffix for simplicity - treat as local
            clean = _re.sub(r"[Z+]\d{2}:?\d{2}$", "", s).rstrip("Z")
            return float(calendar.timegm(_dt.datetime.strptime(clean, fmt).timetuple()))
        except Exception:
            pass
    return _te.time()
# ─────────────────────────────────────────────────────────────────────────────

def get_live_commentary_events(limit: int = 30) -> list:
    """
    Real live event feed from DB tables.
    Schema-tolerant. Never crashes on missing table/column.
    Sources: cognition_log, polaris_proposals, execution events, freshness.
    """
    import time as _lct, sqlite3 as _lcq, html as _lch
    _now = _lct.time()
    events = []
    
    def _safe_table(conn, table):
        try:
            return conn.execute(f"SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
        except: return False
    
    def _safe_col(conn, table, col):
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()]
            return col in cols
        except: return False
    
    try:
        conn = _lcq.connect(str(DB_PATH), timeout=2.0)
        conn.execute("PRAGMA busy_timeout=1500")
        conn.row_factory = _lcq.Row
        
        # ── 1. COGNITION LOG (health, freshness, execution, debate) ───────────
        if _safe_table(conn, "cognition_log"):
            if _safe_col(conn, "cognition_log", "timestamp") and _safe_col(conn, "cognition_log", "stage"):
                try:
                    for row in conn.execute("""
                        SELECT stage, message, timestamp FROM cognition_log
                        WHERE timestamp > ? AND stage IN ('EXECUTOR','SUPERVISOR','GUARDIAN','ORACLE',
                            'FRESHNESS','HEALTH','HEALER','LATCH','QUALIFIER','DEBATE','SYSTEM')
                        ORDER BY timestamp DESC LIMIT 20
                    """, (_now - 600,)).fetchall():
                        stage = str(row["stage"] or "SYS")
                        msg = str(row["message"] or "")[:100]
                        ts = _to_epoch(row["timestamp"]) if row["timestamp"] else _now
                        sev = "warn" if "reject" in msg.lower() or "veto" in msg.lower() or "fail" in msg.lower() else "info"
                        events.append({"ts": ts, "source": stage, "severity": sev, "message": msg})
                except: pass
        
        # ── 2. POLARIS PROPOSALS (lattice, approval, debate) ──────────────────
        if _safe_table(conn, "polaris_proposals"):
            if _safe_col(conn, "polaris_proposals", "created_at"):
                try:
                    for row in conn.execute("""
                        SELECT proposal_type, status, created_at FROM polaris_proposals
                        WHERE created_at > ?
                        ORDER BY created_at DESC LIMIT 10
                    """, (_now - 1800,)).fetchall():
                        ptype = str(row["proposal_type"] or "proposal")
                        status = str(row["status"] or "")
                        ts = _to_epoch(row["created_at"]) if row["created_at"] else _now
                        sev = "success" if status == "approved" else "warn" if status == "HITL_REQUIRED" else "info"
                        msg = f"[LATTICE] {ptype} → {status}"
                        events.append({"ts": ts, "source": "LATTICE", "severity": sev, "message": msg})
                except: pass
        
        # ── 3. PAPER POSITIONS (execution motor events) ───────────────────────
        if _safe_table(conn, "paper_positions"):
            if _safe_col(conn, "paper_positions", "opened_at"):
                try:
                    for row in conn.execute("""
                        SELECT token_name, mint_address, status, opened_at FROM paper_positions
                        WHERE opened_at > ?
                        ORDER BY opened_at DESC LIMIT 8
                    """, (_now - 1200,)).fetchall():
                        token = display_for_row(row)[:10]
                        status = str(row["status"] or "")
                        ts = _to_epoch(row["opened_at"]) if row["opened_at"] else _now
                        msg = f"[EXEC] {token} → {status}"
                        sev = "success" if status == "OPEN" else "info"
                        events.append({"ts": ts, "source": "EXEC", "severity": sev, "message": msg})
                except: pass
        
        conn.close()
    except Exception:
        pass
    
    # Dedupe + sort by timestamp
    # Key includes the FULL message and a 30s bucket so the same speaker can
    # emit different lines over time without one drowning out the other.
    seen = set()
    unique = []
    for ev in sorted(events, key=lambda x: x["ts"], reverse=True):
        _ts_bucket = int(float(ev.get("ts") or 0) // 30)
        key = f"{ev['source']}:{ev['message']}:{_ts_bucket}"
        if key not in seen:
            seen.add(key)
            unique.append(ev)
            if len(unique) >= limit:
                break
    
    return unique[:limit]





# ── HOLO_HELP_20260611: pre-encoded section rundowns, zero-JS glyphs ──────────
def _holoq(key: str) -> str:
    try:
        from ui.holo_help import glyph as _hg
        return _hg(key)
    except Exception:
        return ""


# ── TOPNAV_20260612: one sticky holographic command nav - ops anchors (no
# rerun) + section openers (?sec= opens the expander and jumps to it). ───────
def render_top_command_nav() -> None:
    """CRYSTALLINE COMMAND RAIL - RAIL_V2_20260612.

    One slim liquid-glass bar, the only thing above the SENTINUITY heading.
    Design rules: single row (scrolls sideways on narrow screens, edges fade);
    the ACTIVE section renders as a filled gold crystal so state is visible;
    a 1px synapse line under the rail carries a traveling light pulse - the
    living-web signature; everything else stays ghost-quiet. No wrapping
    button soup, no emoji, one type voice (Orbitron, .56rem, wide tracking).
    """
    try:
        _active = str(st.query_params.get("sec", "")).lower()
    except Exception:
        _active = ""

    secs = [("FOREST", "forest"), ("SUBSTRATE NODE", "substrate"), ("INTEL", "intel"), ("BIO·3", "bio"),
            ("README", "readme"), ("LAB", "lab"), ("VAULT", "vault"),
            ("POLARIS", "polaris"), ("IVARIS", "ivy")]
    jumps = [("PULSE", "#lore-modules"), ("GLASSBOX", "#glassbox-anchor")]

    def _pill(label, href, on=False, cls="sec"):
        return (f'<a href="{href}" target="_self" '
                f'class="crail-pill {cls}{" on" if on else ""}">{label}</a>')

    pills = _pill("HOME", "?", on=(_active == ""), cls="home")
    pills += '<span class="crail-dot">◆</span>'
    pills += "".join(_pill(n, f"?sec={k}#lore-modules", on=(_active == k)) for n, k in secs)
    pills += '<span class="crail-dot">◆</span>'
    pills += "".join(_pill(n, h, cls="jump") for n, h in jumps)

    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

    try:

        _runner_gold_pct = 75.0

        try:

            if isinstance(locals().get("row"), dict):

                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

        except Exception:

            _runner_gold_pct = 75.0

        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

            _state = "RUNNER"

            _state_col = "#FFD700"

    except Exception:

        pass


    st.markdown(f"""
<style>
/* ── CRYSTALLINE COMMAND RAIL - RAIL_V2_20260612 ─────────────────────── */
.crail{{position:sticky;top:0;z-index:999;margin:-6px 0 4px;border-radius:16px;
 padding:1px; /* hairline frame thickness */
 background:linear-gradient(100deg,rgba(255,215,0,.45),rgba(153,69,255,.40) 35%,
   rgba(20,241,149,.30) 70%,rgba(255,215,0,.45));
 background-size:300% 100%;animation:crailFrame 14s linear infinite;}}
@keyframes crailFrame{{0%{{background-position:0% 0}}100%{{background-position:300% 0}}}}
.crail-inner{{display:flex;align-items:center;gap:6px;border-radius:15px;
 padding:7px 14px;overflow-x:auto;scrollbar-width:none;white-space:nowrap;
 background:linear-gradient(115deg,rgba(255,215,0,.05),rgba(153,69,255,.05) 55%,
   rgba(6,9,7,.96)),rgba(5,7,6,.94);
 backdrop-filter:blur(8px) saturate(1.15);
 box-shadow:inset 0 1px 0 rgba(255,255,255,.07),0 10px 30px rgba(0,0,0,.55);
 -webkit-mask-image:linear-gradient(90deg,transparent,#000 18px,#000 calc(100% - 18px),transparent);
 mask-image:linear-gradient(90deg,transparent,#000 18px,#000 calc(100% - 18px),transparent);}}
.crail-inner::-webkit-scrollbar{{display:none}}
.crail-sigil{{display:inline-flex;align-items:center;gap:7px;margin-right:6px;
 font-family:Orbitron,sans-serif;font-size:0.66rem;font-weight:900;
 letter-spacing:.22em;color:#FFD700;text-shadow:0 0 10px rgba(255,215,0,.5)}}
.crail-sigil i{{width:7px;height:7px;border-radius:50%;background:#14F195;
 box-shadow:0 0 8px #14F195,0 0 16px rgba(20,241,149,.6);
 animation:crailBreath 2.6s ease-in-out infinite}}
@keyframes crailBreath{{0%,100%{{opacity:.55;transform:scale(.85)}}50%{{opacity:1;transform:scale(1.15)}}}}
.crail-pill{{font-family:Orbitron,sans-serif;font-size:0.66rem;font-weight:700;
 letter-spacing:.18em;text-transform:uppercase;text-decoration:none;flex:0 0 auto;
 padding:5px 11px;border-radius:999px;color:#C9952A;border:1px solid transparent;
 text-shadow:0 0 8px rgba(255,215,0,.28);
 transition:color .2s,border-color .2s,background .25s,box-shadow .25s}}
.crail-pill:hover{{color:#FFD700;border-color:rgba(255,215,0,.35)}}
.crail-pill.on{{color:#0B0E0C;font-weight:900;
 background:linear-gradient(135deg,#FFD700,#E8B84C 55%,#C9A227);
 box-shadow:0 0 14px rgba(255,215,0,.45),inset 0 1px 0 rgba(255,255,255,.5)}}
.crail-pill.jump{{color:#5FB8A6}}
.crail-pill.jump:hover{{color:#8EF9FF;border-color:rgba(142,249,255,.35)}}
.crail-dot{{color:rgba(153,69,255,.55);font-size:0.66rem;flex:0 0 auto;
 text-shadow:0 0 8px rgba(153,69,255,.6)}}
.crail-synapse{{height:1px;margin:0 18px 10px;position:relative;overflow:hidden;
 background:linear-gradient(90deg,transparent,rgba(153,69,255,.35) 20%,
   rgba(255,215,0,.35) 50%,rgba(20,241,149,.35) 80%,transparent)}}
.crail-synapse::after{{content:"";position:absolute;top:0;left:-12%;width:12%;height:1px;
 background:linear-gradient(90deg,transparent,#FFD700,#FFF8DC,transparent);
 box-shadow:0 0 8px #FFD700;animation:crailPulse 5.5s ease-in-out infinite}}
@keyframes crailPulse{{0%{{left:-12%}}55%,100%{{left:112%}}}}
@media (prefers-reduced-motion: reduce){{
 .crail,.crail-sigil i,.crail-synapse::after{{animation:none}}}}
</style>
<nav class="crail"><div class="crail-inner">
<span class="crail-sigil"><i></i>SNTY</span>{pills}
</div></nav>
<div class="crail-synapse"></div>""", unsafe_allow_html=True)

def render_crown_navigation_deck() -> None:
    """
    SOVEREIGN RECONNAISSANCE EYE - V2 SIX-FACET CROWN
    -------------------------------------------------
    Premium segmented crown deck. Six facets, one coherent surface.

      GLASSBOX          - full organism state, money, execution, vitals  (green/cyan)
      WORLD ENGINE      - live on-chain world, NPC/event layer, mythos    (purple/cyan)
      MICRO LANES       - micro-cap execution lanes & token flow          (green)
      MACRO LENS        - broader Solana / liquidity / market context     (cyan)
      GENESIS VAULT     - code vault, history, branches, verified state   (gold sweep)
      COGNITION STREAM  - unfiltered agent/council/cognition feed         (purple)

    Single source of truth: st.session_state["active_facet"].
    Atomically syncs legacy flags (world_mode_enabled / genesis_vault_open)
    so downstream mid-page conditionals continue to work without desync bugs.

    Routing strategy: MICRO LANES / MACRO LENS / COGNITION STREAM deep-link
    into the existing hub surface via a hub_anchor session flag - they do NOT
    promise destinations that don't exist yet. They render hub + scroll/filter
    hint, keeping the visible facet count at the directive's six without
    bolting phantom features.
    """
    if "active_facet" not in st.session_state:
        if st.session_state.get("genesis_vault_open", False):
            st.session_state["active_facet"] = "genesis"
        elif st.session_state.get("world_mode_enabled", False):
            st.session_state["active_facet"] = "world"
        else:
            st.session_state["active_facet"] = "glassbox"

    # Legacy alias: anything that used to be "hub" maps to the new "glassbox" facet.
    if st.session_state.get("active_facet") == "hub":
        st.session_state["active_facet"] = "glassbox"

    # ── Premium crown CSS ────────────────────────────────────────────────────
    st.markdown("""
    <style>
    @keyframes goldenSweep {
        0% { background-position: -200% 0; }
        100% { background-position: 200% 0; }
    }
    @keyframes mycelialShimmer {
        0%   { box-shadow: 0 0 10px rgba(153,69,255,0.20), inset 0 0 14px rgba(142,249,255,0.06); }
        50%  { box-shadow: 0 0 18px rgba(153,69,255,0.35), inset 0 0 22px rgba(142,249,255,0.12); }
        100% { box-shadow: 0 0 10px rgba(153,69,255,0.20), inset 0 0 14px rgba(142,249,255,0.06); }
    }
    @keyframes glassPulse {
        0%   { box-shadow: 0 0 8px  rgba(20,241,149,0.18), inset 0 0 12px rgba(142,249,255,0.05); }
        50%  { box-shadow: 0 0 16px rgba(20,241,149,0.32), inset 0 0 18px rgba(142,249,255,0.10); }
        100% { box-shadow: 0 0 8px  rgba(20,241,149,0.18), inset 0 0 12px rgba(142,249,255,0.05); }
    }

    /* ── Crown deck container ─────────────────────────────────────────────── */
    .sovereign-eye-label {
        font-family: 'Orbitron', sans-serif;
        font-size: 0.66rem;
        letter-spacing: 5px;
        color: rgba(142,249,255,0.45);
        text-align: center;
        margin: 4px 0 6px;
        text-transform: uppercase;
    }

    /* ── Streamlit button overrides - turn buttons into facet plates ─────── */
    div[data-testid="stHorizontalBlock"] .crown-facet-deck button,
    .crown-facet-deck button {
        font-family: 'Orbitron', 'Share Tech Mono', sans-serif !important;
        font-size: 0.66rem !important;
        font-weight: 600 !important;
        letter-spacing: 2.5px !important;
        text-transform: uppercase !important;
        background: linear-gradient(180deg,
                    rgba(8,4,20,0.92) 0%,
                    rgba(4,2,12,0.96) 100%) !important;
        border: 1px solid rgba(142,249,255,0.12) !important;
        border-radius: 8px !important;
        color: rgba(220,230,240,0.62) !important;
        /* CONSOLIDATION_PASS_20260611: bulky 58px squares -> slim command rail */
        padding: 5px 10px !important;
        height: 36px !important;
        transition: all 0.35s cubic-bezier(.2,.8,.2,1) !important;
        box-shadow: inset 0 1px 0 rgba(255,255,255,0.04),
                    inset 0 -1px 0 rgba(0,0,0,0.4) !important;
        position: relative !important;
        overflow: hidden !important;
    }
    /* mobile: slim full-width rail items, tighter rhythm */
    @media (max-width: 640px) {
        div[data-testid="column"] button[kind="secondary"] {
            height: 32px !important;
            padding: 3px 8px !important;
            font-size: 0.66rem !important;
        }
    }
    .crown-facet-deck button::before {
        content: "";
        position: absolute;
        top: 0; left: -100%;
        width: 60%; height: 100%;
        background: linear-gradient(110deg,
                    transparent 0%,
                    rgba(255,255,255,0.06) 50%,
                    transparent 100%);
        transition: left 0.8s ease;
    }
    .crown-facet-deck button:hover {
        border-color: rgba(142,249,255,0.4) !important;
        color: #DDE9F2 !important;
        transform: translateY(-1px) !important;
    }
    .crown-facet-deck button:hover::before { left: 120%; }

    /* ── Per-facet active state capsules ──────────────────────────────────── */
    .facet-capsule {
        font-family: 'Orbitron', sans-serif;
        font-size: 0.66rem;
        letter-spacing: 2.5px;
        text-align: center;
        padding: 5px 6px;
        border-radius: 5px;
        background: rgba(5,2,16,0.7);
        color: rgba(180,190,210,0.35);
        border: 1px solid transparent;
        transition: all 0.35s ease;
        margin-top: -4px;
        pointer-events: none;
    }
    .active-glassbox {
        border-color: #14F195;
        color: #14F195 !important;
        background: linear-gradient(180deg, rgba(20,241,149,0.08), rgba(142,249,255,0.04));
        animation: glassPulse 3.2s ease-in-out infinite;
        text-shadow: 0 0 6px rgba(20,241,149,0.6);
    }
    .active-world {
        border-color: #9945FF;
        color: #8EF9FF !important;
        background: linear-gradient(180deg, rgba(153,69,255,0.10), rgba(142,249,255,0.05));
        animation: mycelialShimmer 3.5s ease-in-out infinite;
        text-shadow: 0 0 6px rgba(153,69,255,0.55);
    }
    .active-micro {
        border-color: #14F195;
        color: #14F195 !important;
        background: rgba(20,241,149,0.06);
        box-shadow: 0 0 10px rgba(20,241,149,0.22);
        text-shadow: 0 0 5px rgba(20,241,149,0.55);
    }
    .active-macro {
        border-color: #8EF9FF;
        color: #8EF9FF !important;
        background: rgba(142,249,255,0.06);
        box-shadow: 0 0 10px rgba(142,249,255,0.25);
        text-shadow: 0 0 5px rgba(142,249,255,0.6);
    }
    .active-genesis {
        background: linear-gradient(90deg, #1A120B 0%, #D4AF37 50%, #1A120B 100%);
        background-size: 200% auto;
        animation: goldenSweep 5s linear infinite;
        border-color: #D4AF37;
        box-shadow: 0 0 14px rgba(212,175,55,0.35);
        color: #FFF !important;
        font-weight: 600;
    }
    .active-cognition {
        border-color: #9945FF;
        color: #C29BFF !important;
        background: rgba(153,69,255,0.08);
        box-shadow: 0 0 10px rgba(153,69,255,0.28);
        text-shadow: 0 0 5px rgba(153,69,255,0.6);
    }

    /* Mycelial Connector Filament Line - preserved from V1 */
    .mycelial-artery {
        border-left: 1px dashed rgba(142,249,255,0.2);
        margin-left: 20px;
        padding-left: 15px;
    }
    </style>
    """, unsafe_allow_html=True)

    st.markdown(
        "<div class='sovereign-eye-label'>◈ SOVEREIGN RECONNAISSANCE EYE ◈</div>",
        unsafe_allow_html=True,
    )

    # ── Deck wrapper so CSS scopes to this region ────────────────────────────
    st.markdown("<div class='crown-facet-deck'>", unsafe_allow_html=True)

    current = st.session_state["active_facet"]
    cols = st.columns(6, gap="small")

    # ── Facet 1: GLASSBOX ────────────────────────────────────────────────────
    with cols[0]:
        if st.button("◉ GLASSBOX", key="nav_glassbox_click", use_container_width=True):
            st.session_state["active_facet"] = "glassbox"
            st.session_state["world_mode_enabled"] = False
            st.session_state["genesis_vault_open"] = False
            st.session_state["hub_anchor"] = "glassbox"
            # purge cached world overlay state to free memory on hub
            for _wk in ["_sw_world_slot", "_sw_update_slot", "_sw_world_injected",
                        "_sw_state_hash", "_sw_last_push"]:
                st.session_state.pop(_wk, None)
            st.rerun()
        st.markdown(
            f"<div class='facet-capsule {'active-glassbox' if current=='glassbox' else ''}'>⚙ ORGANISM LIVE</div>",
            unsafe_allow_html=True,
        )

    # ── Facet 2: WORLD ENGINE ────────────────────────────────────────────────
    with cols[1]:
        if st.button("◈ WORLD ENGINE", key="nav_world_click", use_container_width=True):
            st.session_state["active_facet"] = "world"
            st.session_state["world_mode_enabled"] = True
            st.session_state["genesis_vault_open"] = False
            # WORLD_SINGLE_SOURCE_FIX_20260618 / CANONICALIZATION_20260621:
            # the WORLD ENGINE button previously set BOTH world_mode_enabled=True
            # AND routed to ?sec=worldos (the SEPARATE world_os control surface) -
            # so one click mounted TWO different worlds in two sections. The keeper
            # is world_mode_enabled, which now mounts the CANONICAL
            # ui/sovereign_world.html directly. Do NOT route to worldos.
            st.session_state["hub_anchor"] = "world"
            try:
                if "sec" in st.query_params:
                    del st.query_params["sec"]
            except Exception:
                pass
            # SEED FIRST-TICK so legacy world overlays still feel alive if
            # the operator closes World OS and returns to the old world mode.
            st.session_state["sx_last_npc_tick"] = 0.0
            st.rerun()
        st.markdown(
            f"<div class='facet-capsule {'active-world' if current=='world' else ''}'>👁 MYCELIAL FLUX</div>",
            unsafe_allow_html=True,
        )

    # ── Facet 3: MICRO LANES (deep-links into hub) ──────────────────────────
    with cols[2]:
        if st.button("▾ MICRO LANES", key="nav_micro_click", use_container_width=True):
            st.session_state["active_facet"] = "micro"
            st.session_state["world_mode_enabled"] = False
            st.session_state["genesis_vault_open"] = False
            st.session_state["hub_anchor"] = "micro"  # downstream filter hint
            for _wk in ["_sw_world_slot", "_sw_update_slot", "_sw_world_injected",
                        "_sw_state_hash", "_sw_last_push"]:
                st.session_state.pop(_wk, None)
            st.rerun()
        st.markdown(
            f"<div class='facet-capsule {'active-micro' if current=='micro' else ''}'>◇ LANE FLOW</div>",
            unsafe_allow_html=True,
        )

    # ── Facet 4: MACRO LENS (deep-links into hub) ───────────────────────────
    with cols[3]:
        if st.button("◭ MACRO LENS", key="nav_macro_click", use_container_width=True):
            st.session_state["active_facet"] = "macro"
            st.session_state["world_mode_enabled"] = False
            st.session_state["genesis_vault_open"] = False
            st.session_state["hub_anchor"] = "macro"
            for _wk in ["_sw_world_slot", "_sw_update_slot", "_sw_world_injected",
                        "_sw_state_hash", "_sw_last_push"]:
                st.session_state.pop(_wk, None)
            st.rerun()
        st.markdown(
            f"<div class='facet-capsule {'active-macro' if current=='macro' else ''}'>◇ MARKET LENS</div>",
            unsafe_allow_html=True,
        )

    # ── Facet 5: GENESIS VAULT ───────────────────────────────────────────────
    with cols[4]:
        if st.button("⬡ GENESIS VAULT", key="nav_genesis_click", use_container_width=True):
            st.session_state["active_facet"] = "genesis"
            st.session_state["world_mode_enabled"] = False
            st.session_state["genesis_vault_open"] = True
            st.session_state["hub_anchor"] = "genesis"
            st.rerun()
        st.markdown(
            f"<div class='facet-capsule {'active-genesis' if current=='genesis' else ''}'>⬡ DOCTRINE SEAL</div>",
            unsafe_allow_html=True,
        )

    # ── Facet 6: COGNITION STREAM (deep-links into hub) ─────────────────────
    with cols[5]:
        if st.button("∿ COGNITION STREAM", key="nav_cognition_click", use_container_width=True):
            st.session_state["active_facet"] = "cognition"
            st.session_state["world_mode_enabled"] = False
            st.session_state["genesis_vault_open"] = False
            st.session_state["hub_anchor"] = "cognition"
            for _wk in ["_sw_world_slot", "_sw_update_slot", "_sw_world_injected",
                        "_sw_state_hash", "_sw_last_push"]:
                st.session_state.pop(_wk, None)
            st.rerun()
        st.markdown(
            f"<div class='facet-capsule {'active-cognition' if current=='cognition' else ''}'>∿ UNFILTERED</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)


def _render_agent_heartbeat_cards() -> None:
    """Six-node council cards with per-task model evolution/devolution visibility."""
    import html as _ahhtml
    snapshot = get_agent_status_snapshot()

    _COUNCIL_ORDER = ["POLARIS", "IVARIS", "NUGGET", "ORACLE", "AXON", "RHIZA"]
    _SUPPORT_ORDER = ["GOVERNOR", "GUARDIAN", "GOLDEN_LATTICE", "AXIOM_NIM"]

    def _age_str(age):
        try:
            age = int(age)
        except Exception:
            return "?"
        if age < 60:
            return f"{age}s ago"
        if age < 3600:
            return f"{age//60}m ago"
        return f"{age//3600}h ago"

    def _evo_col(evo_raw: str) -> str:
        # Evolution-state accent: identity colors stay fixed, this only tints the EVO pill.
        e = evo_raw.lower()
        if any(t in e for t in ("devolv", "regress", "down", "fallback", "degrad")):
            return "#FFB347"   # amber - model devolved / fell back
        if any(t in e for t in ("evolv", "ascend", "promot", "upgrad", "boost")):
            return "#8EF9FF"   # cyan - model evolved upward
        if "support" in e:
            return "#6a6a6a"   # muted - support systems don't vote
        return "#FFD700"       # gold - baseline / holding

    def _card(name: str, compact: bool = False) -> str:
        d = snapshot.get(name, {})
        status = str(d.get("status", "UNKNOWN"))
        status_col = {"ACTIVE": "#14F195", "STALE": "#FFD700", "OFFLINE": "#FF073A", "UNKNOWN": "#555"}.get(status, "#555")
        col = str(d.get("color", "#8EF9FF"))
        icon = str(d.get("icon", "◈"))
        note = _ahhtml.escape(str(d.get("note", ""))[:90])
        model = _ahhtml.escape(str(d.get("current_model", "-"))[:30])
        tier = _ahhtml.escape(str(d.get("model_tier", "-"))[:22])
        evo_raw = str(d.get("evolution_state", "baseline"))
        evo = _ahhtml.escape(evo_raw[:20])
        evo_c = _evo_col(evo_raw)
        auth = _ahhtml.escape(str(d.get("authority_level", "")).replace("_", " ")[:24])
        role = _ahhtml.escape(str(d.get("role", ""))[:58])
        age = _ahhtml.escape(_age_str(d.get("age", 9999)))
        width = "min-width:158px;max-width:230px;" if not compact else "min-width:132px;max-width:196px;"
        font_main = "0.80rem" if not compact else "0.70rem"  # READABILITY: bumped from 0.62/0.55
        is_active = status == "ACTIVE"
        # SIGNOFF_TIER1_DOCTRINE_20260716: heartbeat dot driven by REAL freshness
        # via semantic classes (ui.theme.heartbeat_class). The class is recomputed
        # from the actual age on every render, so a service that stops
        # heartbeating stops pulsing on the next render — the pulse can never
        # outlive the truth. Only sent-heartbeat-fresh animates; aging is static
        # and desaturated; stale freezes amber (or red if genuinely failed).
        try:
            from ui.theme import service_heartbeat_class as _sent_hb_class
            _hb_cls = _sent_hb_class(name, d.get("age", 9999))
        except Exception:
            _hb_cls = "sent-heartbeat-stale"
        if not is_active:
            # An inactive service must not present as fresh regardless of age.
            _hb_cls = "sent-heartbeat-stale" if _hb_cls == "sent-heartbeat-fresh" else _hb_cls
        if status == "OFFLINE":
            _hb_cls += " sent-failed"       # stale-red: genuine failure, not mere age
        # Glow follows the SEMANTIC class, not raw status: an ACTIVE service
        # whose heartbeat has aged past the fresh threshold is static and
        # non-glowing. Glow and pulse are one truth — heartbeat freshness.
        _hb_glows = _hb_cls == "sent-heartbeat-fresh"
        dot = (
            f'<span class="cncl-dot {_hb_cls}" style="margin-left:auto;width:8px;height:8px;border-radius:50%;'
            f'background:{status_col};'
            f'box-shadow:{"0 0 8px "+status_col if _hb_glows else "none"};display:inline-block;"></span>'
        )
        auth_html = (
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{col}AA;'
            f'letter-spacing:2px;text-transform:uppercase;margin:-2px 0 4px;">{auth}</div>'
        ) if auth else ""
        return (
            f'<div class="cncl-card" style="position:relative;flex:1;{width}padding:10px 11px 9px;margin-bottom:6px;'
            f'background:linear-gradient(150deg,rgba(255,255,255,.045),rgba(9,6,24,0.86) 36%,rgba(5,2,16,0.94));'
            f'backdrop-filter:blur(11px) saturate(1.28);-webkit-backdrop-filter:blur(11px) saturate(1.28);'
            f'border:1px solid {col}3a;border-radius:13px;border-left:3px solid {status_col};'
            f'box-shadow:inset 0 1px 0 rgba(255,255,255,.09),0 10px 26px rgba(0,0,0,0.40);overflow:hidden;">'
            # identity accent bar (fixed color = identity) + faint sheen
            f'<div style="position:absolute;top:0;left:0;right:0;height:2px;'
            f'background:linear-gradient(90deg,{col},{col}00 70%);opacity:.8;"></div>'
            f'<div style="display:flex;align-items:center;gap:7px;margin-bottom:2px;">'
            f'<span style="font-size:1.0rem;filter:drop-shadow(0 0 5px {col}66);">{icon}</span>'
            f'<span style="font-family:Share Tech Mono,monospace;font-size:{font_main};color:{col};'
            f'letter-spacing:2px;font-weight:800;text-shadow:0 0 8px {col}55;">{name}</span>'
            f'{dot}'
            f'</div>'
            f'{auth_html}'
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{status_col};'
            f'letter-spacing:1px;margin-bottom:5px;">{status} · {age}</div>'
            # model pill
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#cfe9ff;'
            f'background:rgba(142,249,255,0.07);border:1px solid rgba(142,249,255,0.14);'
            f'border-radius:5px;padding:2px 6px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
            f'<span style="color:#8EF9FF99;">MODEL</span> {model}</div>'
            # tier + evo pills row
            f'<div style="display:flex;gap:5px;margin-top:4px;flex-wrap:nowrap;overflow:hidden;">'
            f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#FFD700CC;'
            f'background:rgba(255,215,0,0.06);border-radius:4px;padding:1px 5px;white-space:nowrap;'
            f'overflow:hidden;text-overflow:ellipsis;">TIER {tier}</span>'
            f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{evo_c};'
            f'background:{evo_c}14;border:1px solid {evo_c}33;border-radius:4px;padding:1px 5px;'
            f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">EVO {evo}</span>'
            f'</div>'
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#888;'
            f'margin-top:5px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{role}</div>'
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#555;'
            f'margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{note}</div>'
            f'</div>'
        )

    # Live at-a-glance tally (real snapshot states, council seats only)
    _tally = {"ACTIVE": 0, "STALE": 0, "OFFLINE": 0}
    for _n in _COUNCIL_ORDER:
        _s = str(snapshot.get(_n, {}).get("status", "OFFLINE"))
        _tally[_s] = _tally.get(_s, 0) + 1
    _tally_html = (
        f'<span style="color:#14F195;">● {_tally.get("ACTIVE",0)} ACTIVE</span>&nbsp;&nbsp;'
        f'<span style="color:#FFD700;">● {_tally.get("STALE",0)} STALE</span>&nbsp;&nbsp;'
        f'<span style="color:#FF073A;">● {_tally.get("OFFLINE",0)} OFFLINE</span>'
    )

    # Scoped style - unique animation names so they can't collide with other keyframes in the hub.
    council_html = (
        "<style>"
        "@keyframes cnclPulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.45);opacity:.65}}"
        "@media (prefers-reduced-motion: reduce){.cncl-dot{animation:none!important}}"
        "@keyframes cnclSheen{0%{transform:translateX(-120%)}100%{transform:translateX(220%)}}"
        ".cncl-card{transition:transform .18s ease,box-shadow .18s ease,border-color .18s ease;}"
        ".cncl-card:hover{transform:translateY(-2px);box-shadow:0 6px 22px rgba(0,0,0,0.42);}"
        "</style>"
    )
    council_html += (
        '<div style="display:flex;align-items:center;justify-content:space-between;flex-wrap:wrap;gap:6px;margin:8px 0 7px;">'
        '<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;color:#8EF9FF99;">'
        'THE SOVEREIGN COUNCIL · SIX MINDS · IDENTITY FIXED / MODEL EVOLVES PER TASK</span>'
        f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;">{_tally_html}</span>'
        '</div>'
    )
    council_html += '<div style="display:flex;flex-wrap:wrap;gap:7px;margin:0 0 8px;">'
    council_html += ''.join(_card(n, compact=False) for n in _COUNCIL_ORDER)
    council_html += '</div>'

    support_html = '<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;color:#666;margin:2px 0 5px;">SUPPORT SYSTEMS · NOT COUNCIL VOTES</div>'
    support_html += '<div style="display:flex;flex-wrap:wrap;gap:6px;margin:0 0 12px;opacity:.92;">'
    support_html += ''.join(_card(n, compact=True) for n in _SUPPORT_ORDER)
    support_html += '</div>'

    st.markdown(council_html + support_html, unsafe_allow_html=True)
def fetch_coingecko_prices() -> dict:
    _FALLBACK = {"SOL": {"price_usd": 120.0, "price_aud": 187.5, "change_24h": 0.0}, "XRP": {"price_usd": 0.50, "price_aud": 0.78, "change_24h": 0.0}, "SUI": {"price_usd": 1.20, "price_aud": 1.875, "change_24h": 0.0}}
    _IDS = {"SOL": "solana", "XRP": "ripple", "SUI": "sui"}
    try:
        resp = _requests.get("https://api.coingecko.com/api/v3/simple/price", params={"ids": ",".join(_IDS.values()), "vs_currencies": "usd,aud", "include_24hr_change": "true"}, timeout=1.5, headers={"Accept": "application/json"})
        if resp.status_code != 200: return _FALLBACK
        data = resp.json()
        result = {}
        for sym, cg_id in _IDS.items():
            coin = data.get(cg_id, {})
            result[sym] = {"price_usd": float(coin.get("usd", _FALLBACK[sym]["price_usd"])), "price_aud": float(coin.get("aud", _FALLBACK[sym]["price_aud"])), "change_24h": float(coin.get("usd_24h_change", 0.0) or 0.0)}
        return result
    except Exception: return _FALLBACK

@st.cache_data(show_spinner=False)
def get_base64(filename):
    for base in [ROOT / "ui", ROOT / "assets", ROOT]:
        for ext in ["", ".jpg", ".jpeg", ".png", ".webp"]:
            p = base / f"{filename}{ext}"
            if p.exists() and p.is_file():
                try:
                    with open(p, "rb") as f:
                        suf = p.suffix.lower().replace(".", "")
                        mime = "jpeg" if suf in ("jpg","jpeg") else suf
                        return f"data:image/{mime};base64,{base64.b64encode(f.read()).decode()}"
                except Exception: continue
    return None

img_polaris        = get_base64("polarise_teaser")
img_ivy            = get_base64("ivy_origin_artifact")
img_lab            = get_base64("sentinuity_lab_core") or get_base64("sentinuity_lab")
img_command_center = get_base64("command_center")

import logging as _logging
_hub_log = _logging.getLogger("sovereign_hub")
_query_errors: list = []

def _get_conn():
    """Fresh short-lived DB connection. Never cached, never reused.
    Fail-fast timeouts - dashboard shows stale data rather than freezing the browser.
    """
    _c = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=2.0)
    _c.execute("PRAGMA journal_mode=WAL;")
    _c.execute("PRAGMA synchronous=NORMAL;")
    _c.execute("PRAGMA busy_timeout=2000;")   # was 30000 - fail fast, never freeze UI
    return _c


@st.cache_data(ttl=15, show_spinner=False)

def _safe_snapshot_query(conn) -> "pd.DataFrame":
    """PRAGMA-safe snapshot query that pulls calibrated/pricing/radar fields
    where they exist, falling back gracefully on older DBs."""
    import sqlite3 as _ssq
    try:
        _cols = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}
    except Exception:
        _cols = set()
    _base = ["id", "token_name", "mint_address", "mint_confidence", "candidate_state", "latched"]
    _optional = [
        "calibrated_confidence", "raw_confidence", "confidence_source",
        "quality_status", "quality_reason",
        "price_status", "observed_price", "entry_price", "price_updated_at",
        "is_tradeable", "execution_ready",
        "tier", "runner_tier", "runner_likelihood",
        "signal_age_seconds", "token_birth_age_seconds",
        "curve_progress_pct", "curve_sol_reserves",
        "market_cap_usd", "token_liquidity_usd",
    ]
    _select = _base + [c for c in _optional if c in _cols]
    _sql = f"SELECT {', '.join(_select)} FROM market_snapshots WHERE candidate_state NOT IN ('vetoed','dead') ORDER BY id DESC LIMIT 50"
    try:
        return pd.read_sql_query(_sql, conn)
    except Exception:
        return pd.DataFrame()

def get_data_bundle():
    """Central data bundle - fresh connection per call, results cached 15s."""
    _cn = None
    try:
        _cn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=2.0)
        _cn.execute("PRAGMA journal_mode=WAL;")
        _cn.execute("PRAGMA busy_timeout=2000;")
        return {
            "wallet":     pd.read_sql_query("SELECT COALESCE((SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_WALLET_EQUITY_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='SOLANA_PAPER_WALLET_EQUITY_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_EQUITY_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_TRADING_BALANCE_USD'),wallet_balance) AS wallet_balance, COALESCE((SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_INITIAL_CAPITAL_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='SOLANA_PAPER_INITIAL_CAPITAL_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_EQUITY_BASELINE_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_STARTING_BALANCE_USD'),initial_capital) AS initial_capital FROM system_state WHERE id=1 LIMIT 1", _cn),
            "open_pos":   pd.read_sql_query("SELECT id,token_name,mint_address,entry_price,position_size_usd,live_exec_price,live_exec_pct,live_exec_updated_at,opened_at,entry_price_source,COALESCE(funding_mode, CASE WHEN entry_price_source LIKE 'LIVE:%' OR entry_price_source LIKE 'live_tx:%' THEN 'REAL' ELSE 'SIM' END) AS funding_mode, COALESCE(execution_source, CASE WHEN entry_price_source LIKE 'PAPER_ONLY%' THEN 'PAPER_ENGINE' WHEN entry_price_source LIKE 'LIVE:%' OR entry_price_source LIKE 'live_tx:%' THEN 'REAL_TX' ELSE 'PAPER_ENGINE' END) AS execution_source, COALESCE(money_source, CASE WHEN entry_price_source LIKE 'LIVE:%' OR entry_price_source LIKE 'live_tx:%' THEN 'REAL_WALLET' ELSE 'SIM_EQUITY' END) AS money_source, 'OPEN - ' || COALESCE(funding_mode, CASE WHEN entry_price_source LIKE 'LIVE:%' OR entry_price_source LIKE 'live_tx:%' THEN 'REAL' ELSE 'SIM' END) AS trade_type /* TRADE_TYPE_DERIVED_PATCH */ FROM paper_positions WHERE status='OPEN' ORDER BY opened_at DESC", _cn),
            "snapshots":  _safe_snapshot_query(_cn),
            "heartbeat":  pd.read_sql_query("SELECT service_name,status,last_pulse,note FROM system_heartbeat", _cn),
            "config":     pd.read_sql_query("SELECT key,value FROM system_config WHERE key IN ('TRADING_MODE','PAPER_TRADING_ENABLED','LIVE_TRADING_ENABLED','LIVE_MODE_B_ENABLED','MODE_B_ENABLED','DUAL_MODE_ENABLED','LIVE_PAPER_SHADOW_ON_BLOCK','POSITION_SIZE_USD','DEBATES_ENABLED','SUPERVISOR_MIN_MINT_CONFIDENCE','TAKE_PROFIT_PCT','STOP_LOSS_PCT','MAX_HOLD_SECONDS')", _cn),
            "recent_trades": pd.read_sql_query("SELECT id,token_name,realized_pnl_usd,exit_reason,win_loss,closed_at,position_size_usd,entry_price_source,COALESCE(funding_mode, CASE WHEN entry_price_source LIKE 'LIVE:%' OR entry_price_source LIKE 'live_tx:%' OR exit_reason LIKE 'LIVE:%' THEN 'REAL' ELSE 'SIM' END) AS funding_mode, COALESCE(execution_source, CASE WHEN entry_price_source LIKE 'PAPER_ONLY%' THEN 'PAPER_ENGINE' WHEN entry_price_source LIKE 'LIVE:%' OR entry_price_source LIKE 'live_tx:%' OR exit_reason LIKE 'LIVE:%' THEN 'REAL_TX' ELSE 'PAPER_ENGINE' END) AS execution_source, 'CLOSED - ' || COALESCE(funding_mode, CASE WHEN entry_price_source LIKE 'LIVE:%' OR entry_price_source LIKE 'live_tx:%' OR exit_reason LIKE 'LIVE:%' THEN 'REAL' ELSE 'SIM' END) AS trade_type FROM paper_positions WHERE status='CLOSED' ORDER BY closed_at DESC LIMIT 20", _cn),
            "proposals":  pd.read_sql_query("SELECT id,proposal_type,proposal_text,status,confidence,created_at FROM polaris_proposals WHERE status IN ('open','debating','approved','applied') ORDER BY created_at DESC LIMIT 10", _cn),
        }
    except Exception:
        return {k: pd.DataFrame() for k in ["wallet","open_pos","snapshots","heartbeat","config","recent_trades","proposals"]}


def query_db(sql, params=()):
    """Fast query using shared connection."""
    try:
        _conn = _get_conn()
        return pd.read_sql_query(sql, _conn, params=params)
    except Exception:
        try:
            _fb = sqlite3.connect(str(DB_PATH), timeout=2.0, check_same_thread=False)
            result = pd.read_sql_query(sql, _fb, params=params)
            _fb.close()
            return result
        except Exception:
            return pd.DataFrame()

def val(df, col, default=0.0):
    try:
        if df.empty or col not in df.columns: return default
        return default if pd.isna(df.iloc[0][col]) else df.iloc[0][col]
    except Exception: return default


def _domain_allowed(hostname: str) -> bool:
    if not hostname:
        return False
    hostname = hostname.lower().strip(".")
    return any(hostname == d or hostname.endswith("." + d) for d in TRUSTED_LINK_DOMAINS)

def _safe_display_domain(url: str) -> str:
    try:
        return html.escape((urlparse(url).hostname or "unknown").lower())
    except Exception:
        return "unknown"

def classify_url(url: str) -> tuple[str, str]:
    try:
        parsed = urlparse(str(url).strip())
        scheme = parsed.scheme.lower()
        hostname = (parsed.hostname or "").lower()
        if scheme not in {"http", "https"}:
            return "blocked", '<span class="link-blocked">⛔ BLOCKED: unsafe scheme</span>'
        if not _domain_allowed(hostname):
            return "blocked", f'<span class="link-blocked">⛔ BLOCKED: {html.escape(hostname or "unknown")}</span>'
        safe_url = html.escape(str(url), quote=True)
        safe_domain = _safe_display_domain(str(url))
        return "verified", f'<a href="{safe_url}" target="_blank" rel="noopener noreferrer nofollow" class="link-verified">🟢 VERIFIED - {safe_domain}</a>'
    except Exception:
        return "blocked", '<span class="link-blocked">⛔ BLOCKED: malformed URL</span>'

def purify_links(text: str) -> str:
    """Escape all non-link text and only render allowlisted http/https links as clickable badges."""
    if text is None:
        return ""
    raw = str(text)
    url_re = re.compile(r"https?://[^\s<>'\"`]+", re.IGNORECASE)
    out, last = [], 0
    for match in url_re.finditer(raw):
        out.append(html.escape(raw[last:match.start()]))
        url = match.group(0).rstrip(".,);]}")
        trailing = match.group(0)[len(url):]
        _, safe_html = classify_url(url)
        out.append(safe_html)
        out.append(html.escape(trailing))
        last = match.end()
    out.append(html.escape(raw[last:]))
    return "".join(out)

def _sqlite_tables(db_path: Path) -> set[str]:
    try:
        with sqlite3.connect(str(db_path), timeout=1.5) as conn:
            rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
            return {str(r[0]) for r in rows}
    except Exception:
        return set()

@st.cache_data(ttl=15, show_spinner=False)
def query_optional_db(table_candidates: list[str], limit: int = 5) -> tuple[pd.DataFrame, str]:
    """Read the newest matching table from optional substrate DBs without touching execution writes."""
    for db_path in INTEL_DB_PATHS:
        tables = _sqlite_tables(db_path)
        for table in table_candidates:
            if table in tables:
                try:
                    with sqlite3.connect(str(db_path), timeout=1.5) as conn:
                        conn.execute("PRAGMA query_only=ON")
                        cols = pd.read_sql_query(f"PRAGMA table_info({table})", conn)
                        names = set(cols["name"].astype(str)) if not cols.empty and "name" in cols.columns else set()
                        order_col = next((c for c in ["created_at", "timestamp", "logged_at", "updated_at", "id"] if c in names), None)
                        order_sql = f" ORDER BY {order_col} DESC" if order_col else ""
                        df = pd.read_sql_query(f"SELECT * FROM {table}{order_sql} LIMIT {int(limit)}", conn)
                    return df, f"{db_path.name}:{table}"
                except Exception:
                    continue
    return pd.DataFrame(), "not wired"

def _row_text(row, keys, default="-"):
    for k in keys:
        try:
            v = row.get(k, None)
            if v is not None and str(v).strip() != "":
                return str(v)
        except Exception:
            pass
    return default



@st.fragment(run_every=29)

def render_pipeline_truth_panel() -> None:
    """
    SOV HUB ADDENDUM - PIPELINE TRUTH / PRICE HANDOFF PANEL
    ─────────────────────────────────────────────────────────
    Read-only. No DB writes. No trading logic.
    Shows the operator exactly why neural_supervisor has no priced rows.
    """
    import sqlite3 as _ptp_sq, time as _ptp_t
    _now = _ptp_t.time()
    _cutoff = _now - 600  # 10-minute window

    _G  = "#14F195"   # neon green
    _A  = "#FFB347"   # amber
    _R  = "#FF073A"   # red alert
    _B  = "#00D4FF"   # electric blue
    _GD = "#FFD700"   # gold
    _DIM = "#555"

    counts = {
        "fresh_snapshots": 0,
        "qualified_priced": 0,
        "qualified_unpriced": 0,
        "price_pending": 0,
        "price_priced": 0,
        "is_tradeable": 0,
        "execution_ready": 0,
        "calibrated_not_null": 0,
        "runner_classified": 0,
        "provisional": 0,
        "top_quality_reasons": [],
    }
    _err = None

    try:
        _c = _ptp_sq.connect(str(DB_PATH), timeout=2.0)
        _c.execute("PRAGMA busy_timeout=1500")
        _c.row_factory = _ptp_sq.Row
        _ms_cols = {r[1] for r in _c.execute("PRAGMA table_info(market_snapshots)").fetchall()}

        def _has(col): return col in _ms_cols

        # Fresh snapshots (last 10m - use id proxy if no updated_at)
        if _has("price_updated_at"):
            counts["fresh_snapshots"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE price_updated_at > ?", (_cutoff,)
            ).fetchone()[0]
        else:
            counts["fresh_snapshots"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE candidate_state NOT IN ('vetoed','dead','executed')"
            ).fetchone()[0]

        # Qualified + priced
        _q_filter = "quality_status='qualified'" if _has("quality_status") else "candidate_state='qualified'"
        counts["qualified_priced"] = _c.execute(
            f"SELECT COUNT(*) FROM market_snapshots WHERE ({_q_filter}) AND price_status='priced'"
        ).fetchone()[0] if _has("price_status") else 0

        # Qualified but unpriced
        counts["qualified_unpriced"] = _c.execute(
            f"SELECT COUNT(*) FROM market_snapshots WHERE ({_q_filter}) AND (price_status IS NULL OR price_status!='priced')"
        ).fetchone()[0] if _has("price_status") else 0

        # Price status breakdown
        if _has("price_status"):
            counts["price_pending"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE price_status='pending'"
            ).fetchone()[0]
            counts["price_priced"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE price_status='priced'"
            ).fetchone()[0]

        # is_tradeable
        if _has("is_tradeable"):
            counts["is_tradeable"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE is_tradeable=1"
            ).fetchone()[0]

        # execution_ready
        if _has("execution_ready"):
            counts["execution_ready"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(execution_ready,0) IN (1,2)"
            ).fetchone()[0]

        # calibrated_confidence not null
        if _has("calibrated_confidence"):
            counts["calibrated_not_null"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE calibrated_confidence IS NOT NULL"
            ).fetchone()[0]

        # runner classified
        if _has("runner_tier"):
            counts["runner_classified"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE runner_tier IS NOT NULL"
            ).fetchone()[0]

        # provisional state
        if _has("candidate_state"):
            counts["provisional"] = _c.execute(
                "SELECT COUNT(*) FROM market_snapshots WHERE candidate_state='provisional'"
            ).fetchone()[0]

        # top 5 quality_reason values
        if _has("quality_reason"):
            _qr_rows = _c.execute(
                "SELECT quality_reason, COUNT(*) n FROM market_snapshots "
                "WHERE quality_reason IS NOT NULL GROUP BY quality_reason ORDER BY n DESC LIMIT 5"
            ).fetchall()
            counts["top_quality_reasons"] = [(r[0], r[1]) for r in _qr_rows]

        _c.close()
    except Exception as _ex:
        _err = str(_ex)[:80]

    # ── Determine blocker message ──────────────────────────────────────────────
    _q_unp = counts["qualified_unpriced"]
    _q_pr  = counts["qualified_priced"]
    _cal   = counts["calibrated_not_null"]
    _fresh = counts["fresh_snapshots"]
    _ppr   = counts["price_priced"]
    _lat   = counts["is_tradeable"]

    if _q_unp > 0 and _q_pr == 0:
        _blocker_col = _R
        _blocker_msg = (
            f"⛔ PRICE HANDOFF BLOCKED - {_q_unp} qualified row(s) exist, but none are priced. "
            "Supervisor cannot latch until price_status='priced' and price > 0."
        )
    elif _fresh > 0 and _cal == 0:
        _blocker_col = _A
        _blocker_msg = (
            f"⚠ CALIBRATION STARVED - {_fresh} fresh row(s) exist but calibrated_confidence "
            "is not being written. Check market_intelligence calibration path."
        )
    elif _ppr > 0 and _lat == 0:
        _blocker_col = _GD
        _blocker_msg = (
            "🔎 SUPERVISOR / VETO LANE NEXT - pricing is alive but no tradeable rows. "
            "Inspect confidence, evidence_count, market_tide, signal_age, and loss-latch gates."
        )
    elif _err:
        _blocker_col = _DIM
        _blocker_msg = f"⚠ DB READ ERROR: {_err}"
    else:
        _blocker_col = _G
        _blocker_msg = "✓ PIPELINE TRUTH: no critical handoff block detected."

    # ── Render ─────────────────────────────────────────────────────────────────
    _rows = [
        ("FRESH SNAPSHOTS (10m)", counts["fresh_snapshots"], _B),
        ("QUALIFIED + PRICED", counts["qualified_priced"], _G if counts["qualified_priced"] > 0 else _R),
        ("QUALIFIED BUT UNPRICED", counts["qualified_unpriced"], _R if counts["qualified_unpriced"] > 0 else _G),
        ("PRICE STATUS: PENDING", counts["price_pending"], _A if counts["price_pending"] > 0 else _DIM),
        ("PRICE STATUS: PRICED", counts["price_priced"], _G if counts["price_priced"] > 0 else _R),
        ("IS_TRADEABLE = 1", counts["is_tradeable"], _G if counts["is_tradeable"] > 0 else _DIM),
        ("EXECUTION_READY = 1", counts["execution_ready"], _G if counts["execution_ready"] > 0 else _DIM),
        ("CALIBRATED_CONFIDENCE ≠ NULL", counts["calibrated_not_null"], _G if counts["calibrated_not_null"] > 0 else _A),
        ("RUNNER CLASSIFIED", counts["runner_classified"], _GD if counts["runner_classified"] > 0 else _DIM),
        ("CANDIDATE_STATE = PROVISIONAL", counts["provisional"], _B if counts["provisional"] > 0 else _DIM),
    ]

    _cells = "".join(
        f'<div style="display:flex;justify-content:space-between;align-items:center;'
        f'padding:5px 10px;border-left:3px solid {col};background:rgba(5,2,16,0.5);'
        f'margin-bottom:3px;border-radius:4px;">'
        f'<span style="font-family:Share Tech Mono,monospace;font-size:0.68rem;color:#aaa;">{lbl}</span>'
        f'<span style="font-family:Share Tech Mono,monospace;font-size:0.85rem;color:{col};font-weight:bold;">{val}</span>'
        f'</div>'
        for lbl, val, col in _rows
    )

    _qr_html = ""
    if counts["top_quality_reasons"]:
        _qr_html = (
            '<div style="margin-top:8px;font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#888;">'
            '<span style="color:#aaa;">TOP QUALITY REASONS: </span>'
            + " │ ".join(f'<span style="color:#FFB347;">{r}</span>×{n}' for r, n in counts["top_quality_reasons"])
            + '</div>'
        )

    st.markdown(
        f'<div style="margin:6px 0 10px 0;padding:10px 12px 8px 12px;border:1px solid rgba(0,212,255,0.18);'
        f'border-radius:10px;background:rgba(2,5,20,0.7);">'
        f'<div style="font-family:Share Tech Mono,monospace;font-size:0.72rem;letter-spacing:3px;'
        f'color:#00D4FF;margin-bottom:8px;">⬡ PIPELINE TRUTH / PRICE HANDOFF</div>'
        f'{_cells}'
        f'<div style="margin-top:8px;padding:6px 10px;border-radius:6px;border-left:4px solid {_blocker_col};'
        f'background:rgba(5,2,16,0.6);font-family:Share Tech Mono,monospace;font-size:0.7rem;color:{_blocker_col};">'
        f'{_blocker_msg}</div>'
        f'{_qr_html}'
        f'</div>',
        unsafe_allow_html=True
    )

def render_convergence_gate() -> None:
    """
    POLARIS CONVERGENCE GATE - unmissable HITL alert.
    Appears at the TOP of the page when Polaris cannot proceed without operator.
    Collapses to nothing when clear - never cries wolf.
    """
    import time as _ct
    _now = _ct.time()
    _gates = []

    try:
        _gc = sqlite3.connect(str(DB_PATH), timeout=2.0)
        _gc.row_factory = sqlite3.Row

        # Check: DRAWDOWN_HALT_ACTIVE
        _halt = _gc.execute("SELECT value FROM system_config WHERE key='DRAWDOWN_HALT_ACTIVE'").fetchone()
        if _halt and str(_halt["value"]).strip() in ("1", "true"):
            _gates.append(("HALT", "TRADING HALTED - OPERATOR ACTION REQUIRED",
                "Drawdown protection active - organism cannot open positions",
                "python fix_blockers.py  OR  set DRAWDOWN_HALT_ACTIVE=0 in system_config"))

        # Check: HITL_REQUIRED proposals waiting
        _hitl = _gc.execute("""
            SELECT id, proposal_type, proposal_text, created_at FROM polaris_proposals
            WHERE status IN ('HITL_REQUIRED','nugget_escalated')
              AND (created_at IS NULL OR created_at < ?)
            ORDER BY created_at ASC LIMIT 3
        """, (_now - 600,)).fetchall()
        for _h in _hitl:
            _age_m = int((_now - float(_h["created_at"] or _now)) / 60)
            _preview = str(_h["proposal_text"] or "")[:80]
            _gates.append(("SEAL", f"POLARIS IS WAITING - PROPOSAL #{_h['id']} ({_age_m}m)",
                f"{_h['proposal_type'] or 'PROPOSAL'}: {_preview}",
                f"python approve_proposal.py {_h['id']}"))

        # Check: Forge stalled
        _forge_err = _gc.execute("""
            SELECT COUNT(*) n FROM polaris_proposals
            WHERE proposal_domain='FORGE' AND status='debate_error'
              AND COALESCE(cooldown_until, 0) > ?
        """, (_now,)).fetchone()
        _forge_open = _gc.execute("""
            SELECT COUNT(*) n FROM polaris_proposals
            WHERE proposal_domain='FORGE' AND status='open'
        """).fetchone()
        if (_forge_err and _forge_err["n"] >= 4) and (_forge_open and _forge_open["n"] == 0):
            _gates.append(("FORGE", "FORGE STALLED - POLARIS NEEDS EVIDENCE INPUT",
                "4+ proposals hit confidence floor. Reconnaissance engine may be offline.",
                "Get-Content logs\\reconnaissance_engine.log -Tail 10"))

        # Check: supervisor idle + no priced candidates
        _sup_hb = _gc.execute("""
            SELECT last_pulse, note FROM system_heartbeat
            WHERE service_name='neural_supervisor' ORDER BY id DESC LIMIT 1
        """).fetchone()
        if _sup_hb:
            _sup_age = _now - float(_sup_hb["last_pulse"] or 0)
            _sup_note = str(_sup_hb["note"] or "")
            if "candidates=0" in _sup_note and _sup_age < 120:
                _priced_qual = _gc.execute("""
                    SELECT COUNT(*) n FROM market_snapshots
                    WHERE candidate_state='qualified' AND price_status='priced'
                      AND COALESCE(price_updated_at,0) > ?
                """, (_now - 120,)).fetchone()
                if _priced_qual and _priced_qual["n"] == 0:
                    _gates.append(("STARVE", "PIPELINE STARVED - OPERATOR CHECK NEEDED",
                        "Qualified tokens exist but none priced. DexScreener may be failing.",
                        "Get-Content logs\\market_intelligence.log -Tail 20 | Select-String price"))

        _gc.close()
    except Exception:
        return

    if not _gates:
        return  # clear - collapse entirely

    # Severity → colour + pulse speed
    _sev_props = {
        "HALT":   {"col": "#FF073A", "pulse": "0.8s", "icon": "🚨"},
        "SEAL":   {"col": "#FFD700", "pulse": "1.2s", "icon": "⬡"},
        "FORGE":  {"col": "#9945FF", "pulse": "1.5s", "icon": "🔥"},
        "STARVE": {"col": "#FFB347", "pulse": "2.0s", "icon": "⚠"},
        "INFO":   {"col": "#8EF9FF", "pulse": "3.0s", "icon": "◈"},
    }
    top_sev  = _gates[0][0]
    top_props = _sev_props.get(top_sev, _sev_props["INFO"])
    top_col  = top_props["col"]
    top_pulse = top_props["pulse"]
    top_icon  = top_props["icon"]

    # Full-width pulsing banner - unmissable
    _body = ""
    for sev, title, detail, action in _gates:
        _p = _sev_props.get(sev, _sev_props["INFO"])
        _c = _p["col"]
        _body += f"""
<div style='margin-bottom:10px;padding:10px 14px;
    border-left:4px solid {_c};border-radius:0 8px 8px 0;
    background:{_c}0d;'>
  <div style='font-family:Orbitron,sans-serif;font-size:0.7rem;
      font-weight:700;color:{_c};letter-spacing:2px;margin-bottom:4px;'>
      {_p['icon']} {html.escape(title)}</div>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
      color:rgba(255,255,255,0.6);margin-bottom:4px;'>{html.escape(detail)}</div>
  <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
      color:rgba(255,255,255,0.25);'>→ {html.escape(action)}</div>
</div>"""

    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

    try:

        _runner_gold_pct = 75.0

        try:

            if isinstance(locals().get("row"), dict):

                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

        except Exception:

            _runner_gold_pct = 75.0

        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

            _state = "RUNNER"

            _state_col = "#FFD700"

    except Exception:

        pass


    st.markdown(f"""
<style>
@keyframes _hitl_pulse {{
    0%,100% {{ box-shadow: 0 0 0px {top_col}00; border-color: {top_col}; }}
    50%      {{ box-shadow: 0 0 24px {top_col}99; border-color: {top_col}ff; }}
}}
</style>
<div style='
    margin: 0 0 16px 0;
    padding: 14px 18px;
    border: 2px solid {top_col};
    border-radius: 12px;
    background: rgba(5,2,16,0.95);
    animation: _hitl_pulse {top_pulse} ease-in-out infinite;
    position: relative;
'>
  <div style='display:flex;align-items:center;gap:10px;margin-bottom:10px;'>
    <span style='font-size:1.4rem;animation:_hitl_pulse {top_pulse} ease-in-out infinite;'>
        {top_icon}</span>
    <div>
      <div style='font-family:Orbitron,sans-serif;font-size:0.75rem;font-weight:900;
          color:{top_col};letter-spacing:4px;'>POLARIS - OPERATOR REQUIRED</div>
      <div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;
          color:{top_col}99;letter-spacing:2px;'>
          {len(_gates)} item{"s" if len(_gates)>1 else ""} await your seal · organism is paused</div>
    </div>
  </div>
  {_body}
</div>
""", unsafe_allow_html=True)


@st.fragment(run_every=53)
def render_forge_ambient() -> None:
    """
    Phase 5: AMBIENT FORGE VISIBILITY - debate sparks, evolution pulses.
    Phase 7: NEURAL SPIKE EVENTS - latch/bomb/heal moments as rare high-signal motion.
    Runs every 60s. Collapses when nothing is happening. Never shows raw logs.
    """
    import sqlite3 as _sq3, time as _t, html as _html
    _now = _t.time()
    _sparks = []
    _spikes = []
    try:
        _fc = _sq3.connect(str(DB_PATH), timeout=2.0)
        _fc.row_factory = _sq3.Row

        # Forge state
        _forge_debating = _fc.execute(
            "SELECT COUNT(*) FROM polaris_proposals WHERE status='debating'"
        ).fetchone()[0]
        _forge_open = _fc.execute(
            "SELECT COUNT(*) FROM polaris_proposals WHERE status='open' AND proposal_domain='FORGE'"
        ).fetchone()[0]
        _forge_approved = _fc.execute(
            "SELECT COUNT(*) FROM polaris_proposals WHERE status IN ('approved','applied') AND updated_at > ?",
            (_now - 3600,)
        ).fetchone()[0]

        # Neural spikes: recent high-signal cognition events
        _recent_events = _fc.execute("""
            SELECT stage, message, token, timestamp
            FROM cognition_log
            WHERE timestamp > ?
              AND (
                message LIKE '%SELECTIVE_AGGRESSION%'
                OR message LIKE '%BOMB SIGNATURE%'
                OR message LIKE '%ENTRY OPENED%'
                OR message LIKE '%Auto-heal fired%'
                OR message LIKE '%ORACLE_STALLED%'
                OR message LIKE '%PHASE_A_PASS%'
              )
            ORDER BY timestamp DESC LIMIT 5
        """, (_now - 300,)).fetchall()
        _fc.close()

        for ev in _recent_events:
            _age = int(_now - float(ev["timestamp"] or _now))
            _msg = str(ev["message"] or "")[:60]
            _tok = str(ev["token"] or "")[:12]
            _stg = str(ev["stage"] or "")
            if "BOMB" in _msg or "SELECTIVE_AGGRESSION" in _msg:
                _col, _icon = "#FF073A", "’¥"
            elif "ENTRY OPENED" in _msg or "PHASE_A_PASS" in _msg:
                _col, _icon = "#14F195", "⚡"
            elif "heal" in _msg.lower():
                _col, _icon = "#9945FF", "↺"
            elif "STALLED" in _msg:
                _col, _icon = "#FFB347", "⚠"
            else:
                _col, _icon = "#8EF9FF", "·"
            _spikes.append((_col, _icon, _age, _tok or _stg, _msg))

    except Exception:
        return

    # Nothing to show
    if not _forge_debating and not _forge_open and not _forge_approved and not _spikes:
        return

    _parts = [
        '<div style="display:flex;gap:8px;flex-wrap:wrap;align-items:flex-start;'
        'padding:6px 10px;margin-bottom:4px;border-radius:6px;'
        'background:rgba(5,2,16,0.4);border:1px solid rgba(153,69,255,0.12);">'
    ]

    # Forge ambient indicators
    if _forge_debating:
        _parts.append(
            f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;'
            f'color:#9945FF55;letter-spacing:2px;animation:forgeBreath 3s ease-in-out infinite;">'
            f'◈ DEBATING {_forge_debating}</span>'
        )
    if _forge_open:
        _parts.append(
            f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;'
            f'color:#9945FF33;letter-spacing:2px;">◈ QUEUED {_forge_open}</span>'
        )
    if _forge_approved:
        _parts.append(
            f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;'
            f'color:#FFD70066;letter-spacing:2px;">◈ APPROVED 1h</span>'
        )

    # Neural spike events
    for col, icon, age, tok, msg in _spikes[:3]:
        _parts.append(
            f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;'
            f'color:{col}88;letter-spacing:1px;">{icon} {tok} {age}s</span>'
        )

    _parts.append(
        '<style>@keyframes forgeBreath{0%,100%{opacity:0.4}50%{opacity:0.7}}</style>'
        '</div>'
    )
    st.markdown("".join(_parts), unsafe_allow_html=True)


@st.fragment(run_every=31)
def render_same_eyes_monitor() -> None:
    """
    SAME-EYES EXECUTION MONITOR - reads dry-run output from execution_engine.
    Zero writes. Shows exactly what gates are blocking entry for each candidate.
    """
    try:
        import sys as _sys
        from services.execution_engine import dry_run_entry_scan
        _decisions = dry_run_entry_scan(limit=20)
    except Exception as _e:
        st.markdown(
            f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:#888;">'
            f'SAME-EYES: unavailable ({type(_e).__name__}: {_e})</div>',
            unsafe_allow_html=True,
        )
        return

    if not _decisions:
        return

    _rows_html = ""
    for _d in _decisions:
        _dec   = _d.get("decision", "?")
        _mint  = str(_d.get("mint_address") or "")[:12]
        _sig   = _d.get("signal_age_sec")
        _prc   = _d.get("price_age_sec")
        _conf  = _d.get("confidence")
        _token = display_for_row(_d)[:12]
        _col   = "#14F195" if _dec == "WOULD_ENTER" else (
                 "#FFD700" if "STALE" in _dec or "OLD" in _dec else "#FF073A")
        _sig_s  = f"{_sig:.0f}s"  if _sig  is not None else "?"
        _prc_s  = f"{_prc:.0f}s"  if _prc  is not None else "?"
        _conf_s = f"{_conf:.2f}"  if _conf is not None else "?"
        _rows_html += (
            f'<tr style="font-size:0.66rem;font-family:Share Tech Mono;">'
            f'<td style="color:#CCC;padding:2px 6px;">{_token}</td>'
            f'<td style="color:#888;padding:2px 6px;">{_prc_s}</td>'
            f'<td style="color:#888;padding:2px 6px;">{_sig_s}</td>'
            f'<td style="color:#888;padding:2px 6px;">{_conf_s}</td>'
            f'<td style="color:{_col};padding:2px 6px;">{_dec}</td>'
            f'</tr>'
        )

    st.markdown(
        f'<div style="margin-top:8px;padding:8px 10px;border-radius:6px;'
        f'background:rgba(5,2,16,0.5);border:1px solid rgba(255,255,255,0.07);">'
        f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:#8EF9FF;'
        f'letter-spacing:2px;margin-bottom:6px;">EXECUTION SAME-EYES</div>'
        f'<table style="width:100%;border-collapse:collapse;">'
        f'<tr style="font-size:0.66rem;color:#555;font-family:Share Tech Mono;">'
        f'<th style="text-align:left;padding:2px 6px;">MINT</th>'
        f'<th style="text-align:left;padding:2px 6px;">PRC_AGE</th>'
        f'<th style="text-align:left;padding:2px 6px;">SIG_AGE</th>'
        f'<th style="text-align:left;padding:2px 6px;">CONF</th>'
        f'<th style="text-align:left;padding:2px 6px;">DECISION</th>'
        f'</tr>'
        f'{_rows_html}'
        f'</table>'
        f'</div>',
        unsafe_allow_html=True,
    )


@st.cache_data(ttl=25, show_spinner=False)
def _fetch_pipeline_counts(now_bucket: int) -> dict:
    """Consolidates all pipeline stage counts into one DB open, cached 25s.
    now_bucket is int(time/25) so cache invalidates on 25s boundary.
    Replaces 9+ separate sqlite3.connect calls inside render_pipeline_flow_debug."""
    import time as _t, sqlite3 as _sq3
    _now = _t.time()
    out = dict(disc=0, qual_n=0, qual_ts=0, priced_n=0, priced_ts=0,
               sv_cand=0, latched_n=0, latched_claim=0, exec_r=0,
               opens=0, bomb_ct=0)
    try:
        _pc = _sq3.connect(str(DB_PATH), timeout=2.0)
        _pc.row_factory = _sq3.Row
        out["disc"]        = _pc.execute("SELECT COUNT(*) FROM raw_dna WHERE COALESCE(first_seen_at, timestamp, 0) > ?", (int(_now - 300),)).fetchone()[0]
        _q = _pc.execute("SELECT COUNT(*), MAX(created_at) FROM market_snapshots WHERE candidate_state='qualified' OR (candidate_state='pending' AND quality_status='qualified')").fetchone()
        out["qual_n"], out["qual_ts"] = _q[0], (_q[1] or 0)
        _p = _pc.execute("SELECT COUNT(*), MAX(price_updated_at) FROM market_snapshots WHERE price_status='priced' AND is_tradeable=1 AND (candidate_state='qualified' OR (candidate_state='pending' AND quality_status='qualified'))").fetchone()
        out["priced_n"], out["priced_ts"] = _p[0], (_p[1] or 0)
        out["sv_cand"]     = _pc.execute("SELECT COUNT(*) FROM market_snapshots WHERE latched=0 AND (candidate_state='qualified' OR (candidate_state='pending' AND quality_status='qualified')) AND price_status='priced' AND is_tradeable=1").fetchone()[0]
        # Count only truly executable latches. Stale rows can briefly retain latched=1
        # while cleaners demote them; the UI must not show those as live signals.
        _l = _pc.execute("""
            SELECT COUNT(*), MAX(latch_claimed_until)
            FROM market_snapshots
            WHERE COALESCE(latched,0)=1
              AND COALESCE(execution_ready,0) IN (1,2)
              AND candidate_state='latched'
        """).fetchone()
        out["latched_n"], out["latched_claim"] = _l[0], (_l[1] or 0)
        out["exec_r"]      = _pc.execute("SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(execution_ready,0) IN (1,2) AND candidate_state='latched'").fetchone()[0]
        out["opens"]       = _pc.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0]
        try:
            out["bomb_ct"] = _pc.execute("SELECT COUNT(*) FROM market_snapshots WHERE bomb_signature=1 AND candidate_state NOT IN ('vetoed','executed','expired_stale')").fetchone()[0]
        except Exception:
            out["bomb_ct"] = 0
        _pc.close()
    except Exception:
        pass
    return out


@st.fragment(run_every=37)
def render_pipeline_flow_debug() -> None:
    """
    THERMAL PIPELINE CORE - observability with organism-state heat rendering.
    Discovery→Qual→Priced→Supervisor→Latch→Exec→Open rendered as thermal lanes.
    Operator feels pressure/bottleneck before reading a number.
    DB queries consolidated into _fetch_pipeline_counts (cached 25s, one connection).
    """
    import time as _pt
    _now = _pt.time()
    _pdata = _fetch_pipeline_counts(int(_now / 25))
    try:
        _pc = None  # no longer opened here - data comes from cache
        # Unpack cached values into original variable names so all downstream code is unchanged
        _disc    = _pdata["disc"]
        _qual    = (_pdata["qual_n"], _pdata["qual_ts"])
        _priced  = (_pdata["priced_n"], _pdata["priced_ts"])
        _sv_cand = _pdata["sv_cand"]
        _latched = (_pdata["latched_n"], _pdata["latched_claim"])
        _exec_r  = _pdata["exec_r"]
        _opens   = _pdata["opens"]
        # Momentum: any bomb signatures or high-momentum rows
        # bomb_ct now comes from _fetch_pipeline_counts cache (no separate connection needed)
        _bomb_ct = _pdata["bomb_ct"]

        _qual_age  = (_now - float(_qual[1]))  if _qual[1]   else None
        _price_age = (_now - float(_priced[1])) if _priced[1] else None

        # Thermal colour doctrine:
        # DISCOVERY  = electric blue  (#00D4FF)
        # QUALIFIED  = white pressure (#F0F0FF)
        # PRICED     = warm amber     (#FFB347)
        # SUPERVISOR = gold ignition  (#FFD700)
        # LATCHED    = neon pulse     (#14F195)
        # EXEC       = emerald        (#00FF88)
        # OPEN       = sovereign gold (#FFD700) / bomb = red pulse
        LANE_COLOURS = ["#00D4FF","#F0F0FF","#FFB347","#FFD700","#14F195","#00FF88","#FFD700"]
        LANE_LABELS  = ["DISC","QUAL","PRICED","SV","LATCH","EXEC","OPEN"]
        LANE_COUNTS  = [_disc, _qual[0] or 0, _priced[0] or 0, _sv_cand, _latched[0] or 0, _exec_r, _opens]
        LANE_AGES    = [None, _qual_age, _price_age, None, None, None, None]
        LANE_THRESH  = [30, 120, 120, 180, 180, 180, 300]

        # Detect blockage and ambient heat level
        _blocked_idx = None
        for i, (cnt, age, thr) in enumerate(zip(LANE_COUNTS, LANE_AGES, LANE_THRESH)):
            if cnt == 0:
                _blocked_idx = i
                break
            if age is not None and age > thr:
                _blocked_idx = i
                break

        # Organism heat: 0.0 cold → 1.0 hot
        _heat = min(1.0, (_disc / 50.0) * 0.3 + (_opens / 3.0) * 0.4 + (0.3 if _latched[0] else 0))
        _heat_col = f"rgba({int(20 + _heat*235)},{int(241 - _heat*200)},{int(149 - _heat*149)},0.15)"

        lanes_html = ""
        for i, (label, col, count, age, thresh) in enumerate(zip(
            LANE_LABELS, LANE_COLOURS, LANE_COUNTS, LANE_AGES, LANE_THRESH
        )):
            # Thermal state per lane
            if count == 0:
                intensity = "0"
                border    = "#FF073A"
                glow      = "rgba(255,7,58,0.4)"
                anim      = "animation:thermalDead 1.4s ease-in-out infinite;"
                val_col   = "#FF073A"
            elif age is not None and age > thresh:
                intensity = "0.4"
                border    = "#FFD700"
                glow      = "rgba(255,215,0,0.3)"
                anim      = "animation:thermalWarm 2.5s ease-in-out infinite;"
                val_col   = "#FFD700"
            else:
                fill      = min(1.0, count / max(count, 10))
                intensity = f"{0.6 + fill * 0.4:.2f}"
                border    = col
                glow      = f"{col}66"
                anim      = "animation:thermalAlive 2s ease-in-out infinite;"
                val_col   = col

            age_str = f" {int(age)}s" if age is not None else ""
            # Bomb badge on SV lane
            bomb_badge = ""
            if i == 3 and _bomb_ct > 0:
                bomb_badge = f'<span style="margin-left:4px;font-size:0.66rem;color:#FF073A;letter-spacing:1px;animation:bombPulse 0.8s ease-in-out infinite;">’¥{_bomb_ct}</span>'

            lanes_html += (
                f'<div style="flex:1;min-width:52px;padding:5px 4px;text-align:center;'
                f'background:{col}0a;border:1px solid {border}44;border-radius:4px;'
                f'box-shadow:0 0 8px {glow};{anim}">'
                f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;'
                f'color:#555;letter-spacing:1px;margin-bottom:2px;">{label}</div>'
                f'<div style="font-family:Orbitron,monospace;font-size:0.75rem;'
                f'font-weight:700;color:{val_col};text-shadow:0 0 6px {val_col}88;">'
                f'{count}</div>'
                f'<div style="font-size:0.66rem;color:#444;">{age_str}</div>'
                f'{bomb_badge}'
                f'</div>'
            )

        _blocked_html = ""
        if _blocked_idx is not None:
            _bn = LANE_LABELS[_blocked_idx]
            _blocked_html = (
                f'<div style="margin-top:5px;font-family:Share Tech Mono,monospace;'
                f'font-size:0.66rem;color:#FF073A;letter-spacing:2px;'
                f'animation:thermalDead 1.4s ease-in-out infinite;">'
                f'⚠ PRESSURE BLOCKED @ {_bn}</div>'
            )

        forge_ambient = ""
        try:
            _fconn = sqlite3.connect(str(DB_PATH), timeout=1.0)
            _fconn.row_factory = sqlite3.Row
            _forge_active = _fconn.execute(
                "SELECT COUNT(*) FROM polaris_proposals WHERE proposal_domain='FORGE' AND status IN ('open','debating')"
            ).fetchone()[0]
            _fconn.close()
            if _forge_active > 0:
                forge_ambient = (
                    f'<div style="margin-top:4px;font-family:Share Tech Mono,monospace;'
                    f'font-size:0.66rem;color:#9945FF55;letter-spacing:2px;">'
                    f'◈ FORGE DEBATING {_forge_active}</div>'
                )
        except Exception: pass

        st.markdown(
            f'<style>'
            f'@keyframes thermalAlive{{0%,100%{{opacity:0.85}}50%{{opacity:1}}}}'
            f'@keyframes thermalWarm{{0%,100%{{opacity:0.6;border-color:#FFD70044}}50%{{opacity:0.9;border-color:#FFD700aa}}}}'
            f'@keyframes thermalDead{{0%,100%{{opacity:0.4}}50%{{opacity:0.85}}}}'
            f'@keyframes bombPulse{{0%,100%{{opacity:0.7}}50%{{opacity:1;text-shadow:0 0 8px #FF073A}}}}'
            f'</style>'
            f'<div style="padding:7px 10px;margin-bottom:6px;border-radius:7px;'
            f'background:{_heat_col};border:1px solid rgba(255,255,255,0.06);">'
            f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:#555;'
            f'margin-bottom:5px;letter-spacing:3px;">THERMAL PIPELINE</div>'
            f'<div style="display:flex;gap:4px;flex-wrap:nowrap;">{lanes_html}</div>'
            f'{_blocked_html}{forge_ambient}'
            f'</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass


@st.fragment(run_every=41)
def render_intelligence_scouts() -> None:
    """
    Single call point for all scout panels - isolated fragment, 30s refresh.
    Wraps telegram/wallet scout data. Low-priority data, no need for fast refresh.
    Fragment isolation means scout DB queries don't block the live trade panels.
    """
    import html as _html
    _C_CYAN  = "#8EF9FF"
    _C_GREEN = "#14F195"

    st.markdown(
        f"<div style='font-family:Share Tech Mono;font-size:0.75rem;"
        f"letter-spacing:3px;color:{_C_CYAN};margin-bottom:8px;'>"
        f"“¡ SCOUT FEEDS</div>",
        unsafe_allow_html=True,
    )

    # ── SMART MONEY SIGNAL LAMPBOARD ─────────────────────────────────────────
    try:
        _lamp_now = __import__("time").time()
        _wallet_fresh = query_db(
            "SELECT COUNT(*) as n FROM symbiotic_candidates "
            "WHERE wallet_score > 0.5 AND first_seen_at > ?",
            params=(_lamp_now - 300,)
        )
        _tg_fresh = query_db(
            "SELECT COUNT(*) as n FROM symbiotic_candidates "
            "WHERE telegram_score > 0.5 AND first_seen_at > ?",
            params=(_lamp_now - 300,)
        )
        _clustering = query_db(
            "SELECT COUNT(DISTINCT wallet_observation_id) as n FROM symbiotic_candidates "
            "WHERE wallet_score > 0.3 AND first_seen_at > ?",
            params=(_lamp_now - 600,)
        )
        _high_wr = query_db(
            "SELECT COUNT(*) as n FROM symbiotic_candidates WHERE wallet_score >= 0.8"
        )
        _wn  = int(_wallet_fresh["n"].iloc[0]) if not _wallet_fresh.empty else 0
        _tn  = int(_tg_fresh["n"].iloc[0])     if not _tg_fresh.empty     else 0
        _cln = int(_clustering["n"].iloc[0])   if not _clustering.empty   else 0
        _hwn = int(_high_wr["n"].iloc[0])      if not _high_wr.empty      else 0

        def _lamp(label, active, count=0):
            if active and count >= 3:
                col, glow, dot = "#FFD700", "rgba(255,215,0,0.2)", "●"
            elif active:
                col, glow, dot = "#14F195", "rgba(20,241,149,0.12)", "●"
            else:
                col, glow, dot = "#333", "transparent", "○"
            return (
                f'<div style="display:inline-flex;align-items:center;gap:4px;'
                f'padding:3px 8px;border-radius:4px;background:{glow};'
                f'border:1px solid {col}33;margin-right:4px;">'
                f'<span style="color:{col};font-size:0.66rem;">{dot}</span>'
                f'<span style="font-family:Share Tech Mono;font-size:0.66rem;color:{col};">'
                f'{label}{(" ×"+str(count)) if active and count > 1 else ""}</span></div>'
            )

        _lamps = "".join([
            _lamp("WALLET BUY",  _wn  > 0, _wn),
            _lamp("CLUSTERING",  _cln > 1, _cln),
            _lamp("HIGH-WR",     _hwn > 0, _hwn),
            _lamp("TG SCOUT",    _tn  > 0, _tn),
        ])
        st.markdown(
            f'<div style="margin-bottom:10px;padding:6px 8px;'
            f'border-radius:6px;background:rgba(5,2,16,0.4);'
            f'border:1px solid rgba(153,69,255,0.15);">'
            f'{_lamps}</div>',
            unsafe_allow_html=True,
        )
    except Exception:
        pass  # lampboard is advisory - never block scouts on DB error

    root_col1, root_col2 = st.columns(2, gap="small")

    with root_col1:
        st.markdown("**“¡ TELEGRAM WATCHER**")
        try:
            calls = query_db(
                "SELECT source_channel, token_address, telegram_score, first_seen_at "
                "FROM symbiotic_candidates ORDER BY first_seen_at DESC LIMIT 3"
            )
            if not calls.empty:
                for _, c in calls.iterrows():
                    _ch  = _html.escape(str(c.get("source_channel") or "?")[:20])
                    _sc  = float(c.get("telegram_score") or 0)
                    _tok = str(c.get("token_address") or "?")[:8]
                    st.markdown(
                        f'<div class="symbiotic-root">Ch: {_ch} • Conf: {_sc:.2f} • {_tok}...</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    '<div class="symbiotic-root">telegram_scout • no recent calls</div>',
                    unsafe_allow_html=True,
                )
        except Exception:
            st.markdown(
                '<div class="symbiotic-root">telegram_scout initializing</div>',
                unsafe_allow_html=True,
            )

    with root_col2:
        st.markdown(
            f"<div style='font-family:Share Tech Mono;font-size:0.72rem;"
            f"letter-spacing:2px;color:{_C_GREEN};margin-bottom:6px;'>"
            f"§¬ WALLET SIGNAL NODES</div>",
            unsafe_allow_html=True,
        )
        try:
            wallets = query_db(
                "SELECT wallet_observation_id, wallet_score, token_address, first_seen_at "
                "FROM symbiotic_candidates ORDER BY wallet_score DESC, first_seen_at DESC LIMIT 5"
            )
            if not wallets.empty:
                _wmax = float(wallets["wallet_score"].max() or 1)
                for _, w in wallets.iterrows():
                    _wid  = str(w.get("wallet_observation_id") or "?")[:10]
                    _wsc  = float(w.get("wallet_score") or 0)
                    _wtok = str(w.get("token_address") or "?")[:8]
                    _pct  = _wsc / _wmax if _wmax > 0 else 0
                    # Color by conviction tier
                    if _pct >= 0.85:
                        _nc, _glow = "#FFD700", "rgba(255,215,0,0.25)"
                    elif _pct >= 0.6:
                        _nc, _glow = "#14F195", "rgba(20,241,149,0.15)"
                    elif _pct >= 0.35:
                        _nc, _glow = "#8EF9FF", "rgba(142,249,255,0.1)"
                    else:
                        _nc, _glow = "#9945FF", "rgba(153,69,255,0.08)"
                    _bar_w = max(4, int(_pct * 60))
                    st.markdown(
                        f'<div style="display:flex;align-items:center;gap:8px;'
                        f'padding:5px 8px;margin-bottom:3px;border-radius:5px;'
                        f'background:{_glow};border-left:3px solid {_nc};">'
                        f'<div style="width:{_bar_w}px;height:6px;border-radius:3px;'
                        f'background:{_nc};opacity:0.85;flex-shrink:0;"></div>'
                        f'<span style="font-family:Share Tech Mono;font-size:0.66rem;'
                        f'color:{_nc};">{_wid}...</span>'
                        f'<span style="font-family:Share Tech Mono;font-size:0.66rem;'
                        f'color:#888;margin-left:auto;">{_wtok}... {_wsc:.1f}</span>'
                        f'</div>',
                        unsafe_allow_html=True,
                    )
            else:
                st.markdown(
                    f'<div style="font-family:Share Tech Mono;font-size:0.66rem;'
                    f'color:#444;padding:8px;">// NO WALLET SIGNALS //</div>',
                    unsafe_allow_html=True,
                )
        except Exception:
            st.markdown(
                f'<div style="font-family:Share Tech Mono;font-size:0.66rem;'
                f'color:#444;padding:8px;">wallet_scout initializing</div>',
                unsafe_allow_html=True,
            )


@st.fragment(run_every=43)
def render_intelligence_substrate_panel():
    """
    SMART MONEY OBSERVATORY{_holoq("hunting_grounds")} - Smart money substrate with real wallet data. (renamed from Hunting Grounds, SIGNOFF_OBSERVATORY_RENAME_20260718; help key unchanged for wiring)
    Reads from watched_wallets, telegram_channel_trust, and symbiotic_candidates.
    Shows archetype badges, tier classification, and enrichment health.
    """
    import time as _t, html as _h
    now = _t.time()

    # ── LAMPBOARD ─────────────────────────────────────────────────────────────
    _wn = _cln = _hwn = _tn = 0
    try:
        _r = query_db("SELECT COUNT(*) n FROM watched_wallets WHERE active=1")
        _wn = int(_r["n"].iloc[0]) if not _r.empty else 0
        _r2 = query_db("SELECT COUNT(*) n FROM watched_wallets WHERE active=1 AND win_rate >= 0.7")
        _hwn = int(_r2["n"].iloc[0]) if not _r2.empty else 0
        _r3 = query_db("SELECT COUNT(*) n FROM telegram_channel_trust WHERE accuracy_score > 0.5")
        _tn = int(_r3["n"].iloc[0]) if not _r3.empty else 0
    except Exception:
        pass

    def _lmp(label, active, count=0):
        if active and count >= 3: nc, glow, dot = "#FFD700", "rgba(255,215,0,0.2)", "●"
        elif active:              nc, glow, dot = "#14F195", "rgba(20,241,149,0.12)", "●"
        else:                     nc, glow, dot = "#333",    "transparent",           "○"
        return (f'<span style="display:inline-flex;align-items:center;gap:3px;padding:2px 7px;'
                f'border-radius:4px;background:{glow};border:1px solid {nc}33;margin-right:4px;">'
                f'<span style="color:{nc};font-size:0.66rem;">{dot}</span>'
                f'<span style="font-family:Share Tech Mono;font-size:0.66rem;color:{nc};">'
                f'{label}{(" ×"+str(count)) if active and count>1 else ""}</span></span>')

    _lamps = _lmp("APEX WALLETS", _wn>0, _wn) + _lmp("HIGH-WR", _hwn>0, _hwn) + _lmp("TG SIGNAL", _tn>0, _tn)

    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

    try:

        _runner_gold_pct = 75.0

        try:

            if isinstance(locals().get("row"), dict):

                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

        except Exception:

            _runner_gold_pct = 75.0

        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

            _state = "RUNNER"

            _state_col = "#FFD700"

    except Exception:

        pass


    st.markdown(
        f'''<div class="substrate-wrap">
        <div class="substrate-title">⬡ SMART MONEY OBSERVATORY - WALLET INTELLIGENCE SUBSTRATE</div>
        <div class="substrate-sub">Apex wallet register · Telegram signal array · Enrichment health · Shared infra with Council forge.</div>
        <div style="margin-top:6px;">{_lamps}</div>
        </div>''',
        unsafe_allow_html=True
    )

    _sc1, _sc2, _sc3 = st.columns(3, gap="large")

    # ── COL 1: APEX WALLET REGISTER ──────────────────────────────────────
    with _sc1:
        wallets = []
        try:
            _wd = query_db("""
                SELECT wallet_address, label, profit_score,
                       win_rate, trade_count, last_seen, added_at
                FROM watched_wallets WHERE active=1
                ORDER BY profit_score DESC LIMIT 6
            """)
            if not _wd.empty:
                wallets = _wd.to_dict("records")
        except Exception:
            pass

        # Also pull symbiotic candidates if watched_wallets empty
        if not wallets:
            try:
                _sd = query_db("""
                    SELECT wallet_observation_id as wallet_address,
                           wallet_score as profit_score,
                           token_address as label,
                           0 as win_rate, 0 as trade_count,
                           first_seen_at as last_seen
                    FROM symbiotic_candidates
                    WHERE wallet_score > 0.3
                    ORDER BY wallet_score DESC LIMIT 6
                """)
                if not _sd.empty:
                    wallets = _sd.to_dict("records")
            except Exception:
                pass

        st.markdown(
            f'<div class="substrate-card" style="border-color:#14F19588;">',
            unsafe_allow_html=True
        )
        st.markdown(
            f'<div class="substrate-head" style="color:#14F195;">👁️ APEX WALLET CONSTELLATION ({len(wallets)})</div>',
            unsafe_allow_html=True
        )

        if not wallets:
            st.markdown(
                '<div class="substrate-muted">// CONSTELLATION AWAITING FIRST APEX WALLET //<br>Tracking activates once wallets are profiled. Run: python initiate_intelligence_build.py</div>',
                unsafe_allow_html=True
            )
        else:
            for w in wallets:
                wr    = float(w.get("win_rate") or 0)
                score = float(w.get("profit_score") or 0)
                tc    = int(w.get("trade_count") or 0)
                lbl   = str(w.get("label") or w.get("wallet_address") or "")[:14]
                ls    = float(w.get("last_seen") or 0)
                ls_str = f"{round((now-ls)/3600,1)}h ago" if ls else "never"

                # Archetype
                if wr >= 0.7 and tc > 20:   arch, ac = "SNIPER",   "#FFD700"
                elif wr >= 0.55 and score>3: arch, ac = "SMART$",   "#14F195"
                elif tc > 50 and wr < 0.35:  arch, ac = "WASH⚠",   "#FF073A"
                elif score > 8:              arch, ac = "LIQ HNT",  "#8EF9FF"
                else:                         arch, ac = "SCOUT",    "#666"

                # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

                # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

                try:

                    _runner_gold_pct = 75.0

                    try:

                        if isinstance(locals().get("row"), dict):

                            _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

                    except Exception:

                        _runner_gold_pct = 75.0

                    if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

                        _state = "RUNNER"

                        _state_col = "#FFD700"

                except Exception:

                    pass


                st.markdown(
                    f'''<div style="padding:5px 6px;border-left:2px solid {ac}44;
                    margin-bottom:4px;font-family:Share Tech Mono;font-size:0.66rem;">
                    <span style="color:#CCC;">{_h.escape(lbl)}</span>
                    <span style="color:{ac};margin-left:6px;">{arch}</span>
                    <span style="color:#666;float:right;">{wr:.0%} WR · {ls_str}</span>
                    </div>''',
                    unsafe_allow_html=True
                )
        st.markdown('</div>', unsafe_allow_html=True)

    # ── COL 2: TELEGRAM SIGNAL ARRAY ──────────────────────────────────────────
    with _sc2:
        tg_rows = []
        try:
            _td = query_db("""
                SELECT channel_name, channel_id, accuracy_score,
                       calls_total, calls_hit, avg_x, last_updated
                FROM telegram_channel_trust
                ORDER BY accuracy_score DESC LIMIT 5
            """)
            if not _td.empty:
                tg_rows = _td.to_dict("records")
        except Exception:
            pass

        st.markdown(
            '<div class="substrate-card" style="border-color:#8EF9FF88;">',
            unsafe_allow_html=True
        )
        st.markdown(
            '<div class="substrate-head" style="color:#8EF9FF;">📡 SIGNAL ARRAY - TELEGRAM</div>',
            unsafe_allow_html=True
        )

        if not tg_rows:
            st.markdown(
                '<div class="substrate-muted">// SIGNAL ARRAY ARMED - AWAITING TELEGRAM SCOUT //<br>Channel scoring begins when telegram_scout is started.</div>',
                unsafe_allow_html=True
            )
        else:
            for ch in tg_rows:
                acc   = float(ch.get("accuracy_score") or 0)
                hits  = int(ch.get("calls_hit") or 0)
                total = int(ch.get("calls_total") or 0)
                name  = str(ch.get("channel_name") or "Unknown")[:20]
                col   = "#14F195" if acc > 0.6 else ("#FFD700" if acc > 0.4 else "#FF073A")
                # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
                # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
                try:
                    _runner_gold_pct = 75.0
                    try:
                        if isinstance(locals().get("row"), dict):
                            _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
                    except Exception:
                        _runner_gold_pct = 75.0
                    if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
                        _state = "RUNNER"
                        _state_col = "#FFD700"
                except Exception:
                    pass

                st.markdown(
                    f'''<div style="padding:5px 6px;border-left:2px solid {col}44;
                    margin-bottom:4px;font-family:Share Tech Mono;font-size:0.66rem;">
                    <span style="color:{col};">{_h.escape(name)}</span><br>
                    <span style="color:#666;">acc={acc:.0%} · {hits}/{total} hits</span>
                    </div>''',
                    unsafe_allow_html=True
                )
        st.markdown('</div>', unsafe_allow_html=True)

    # ── COL 3: ENRICHMENT HEALTH ──────────────────────────────────────────────
    with _sc3:
        pq = mtm = mcap_miss = sv = 0
        try:
            _e1 = query_db("SELECT COUNT(*) n FROM market_snapshots WHERE quality_status='pending' AND candidate_state='pending'")
            pq = int(_e1["n"].iloc[0]) if not _e1.empty else 0
            _e2 = query_db("SELECT COUNT(*) n FROM market_snapshots WHERE candidate_state='mtm' AND quality_status='pending'")
            mtm = int(_e2["n"].iloc[0]) if not _e2.empty else 0
            _e3 = query_db("SELECT COUNT(*) n FROM market_snapshots WHERE quality_status='pending' AND (market_cap_usd IS NULL OR market_cap_usd=0)")
            mcap_miss = int(_e3["n"].iloc[0]) if not _e3.empty else 0
            _e4 = query_db("SELECT COUNT(*) n FROM market_snapshots WHERE latched=0 AND quality_status='qualified' AND price_status='priced' AND is_tradeable=1")
            sv = int(_e4["n"].iloc[0]) if not _e4.empty else 0
        except Exception:
            pass

        bottleneck = pq > 50 or mcap_miss > 20
        bc = "#FF073A" if bottleneck else "#14F195"
        btext = "⚠ ENRICHMENT_BOTTLENECK" if bottleneck else "✓ PIPELINE_CLEAR"

        # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

        # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

        try:

            _runner_gold_pct = 75.0

            try:

                if isinstance(locals().get("row"), dict):

                    _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

            except Exception:

                _runner_gold_pct = 75.0

            if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

                _state = "RUNNER"

                _state_col = "#FFD700"

        except Exception:

            pass


        st.markdown(
            f'''<div class="substrate-card" style="border-color:{bc}88;">
            <div class="substrate-head" style="color:{bc};">⚒ ORACLE ENRICHMENT FORGE</div>
            <div style="font-family:Share Tech Mono;font-size:0.66rem;color:{bc};margin-bottom:8px;">{btext}</div>''',
            unsafe_allow_html=True
        )

        def _ebar(label, val, warn_at, col_ok="#14F195", col_warn="#FF073A"):
            c = col_warn if val > warn_at else col_ok
            pct = min(100, int(val / max(1, warn_at * 2) * 100))
            return (f'<div style="margin-bottom:5px;font-family:Share Tech Mono;font-size:0.66rem;">'                   f'<span style="color:#666;">{label}</span>'                   f'<span style="color:{c};float:right;">{val}</span>'                   f'<div style="height:2px;background:#111;margin-top:2px;">'                   f'<div style="width:{pct}%;height:100%;background:{c};"></div></div></div>')

        bars = (
            _ebar("PENDING QUAL", pq, 50)
            + _ebar("MTM BLIND",  mtm, 20)
            + _ebar("MCAP MISS",  mcap_miss, 15)
            + f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:#14F195;margin-top:6px;">SUPERVISOR VISIBLE: {sv}</div>'
        )
        st.markdown(bars + '</div>', unsafe_allow_html=True)



@st.cache_data(ttl=30, show_spinner=False)
def fetch_live_open_positions_breathe(db_path_str: str, refresh_bucket: int) -> list:
    """
    SIGN-OFF OPEN POSITION TRUTH SOURCE.

    Visible open-position PnL is sourced from paper_positions.live_exec_* only.
    market_snapshots is retained only as structural context / optional snap metadata.
    If execution has not written live_exec data, the UI shows NO_EXEC_DATA instead
    of synthesising fake micro-movement from snapshots.

    SAME-EYES ENHANCEMENT: Also reads mtm_ticks from sentinuity_intelligence.db
    directly - the same Tier-1 source the executor uses - so the UI sees the real
    live price without waiting for the executor write cycle. This closes the gap
    where positions close at 79% but the meter only ever showed 0% because the
    executor hadn't written live_exec_pct yet.
    """
    import time
    try:
        conn = sqlite3.connect(db_path_str, check_same_thread=False, timeout=2.0)
        conn.row_factory = sqlite3.Row

        # Cache oracle gate config in session_state so render_living_trade_meter
        # can read it without a separate DB query per render cycle.
        try:
            _gate_row = conn.execute(
                "SELECT value FROM system_config WHERE key='ORACLE_LIVENESS_GATE_SEC'"
            ).fetchone()
            if _gate_row:
                st.session_state["_cfg_oracle_gate"] = float(_gate_row[0])
        except Exception:
            pass

        rows = conn.execute("""
            SELECT p.*,
                   COALESCE(m.observed_price, p.entry_price) AS snap_price,
                   COALESCE(m.price_updated_at, p.last_marked_at) AS snap_marked_at
            FROM paper_positions p
            LEFT JOIN (
                SELECT mint_address, observed_price, price_updated_at
                FROM market_snapshots
                WHERE candidate_state = 'mtm' AND observed_price > 0
                GROUP BY mint_address
                HAVING price_updated_at = MAX(price_updated_at)
            ) m ON p.mint_address = m.mint_address
            WHERE p.status = 'OPEN'
            ORDER BY p.opened_at DESC
        """).fetchall()
        conn.close()

        # Build Intel DB price map - same Tier-1 source as executor
        # Keyed by mint_address → {price, age_sec}
        _intel_prices: dict = {}
        for _intel_path in INTEL_DB_PATHS:
            try:
                _ic = sqlite3.connect(str(_intel_path), timeout=1.0)
                _ic.row_factory = sqlite3.Row
                _iticks = _ic.execute(
                    "SELECT mint_address, price_usd, ts_ms "
                    "FROM mtm_ticks "
                    "ORDER BY ts_ms DESC LIMIT 200"
                ).fetchall()
                _ic.close()
                _inow = time.time()
                for _t in _iticks:
                    _m = str(_t["mint_address"] or "")
                    if _m and _m not in _intel_prices:
                        _ip = float(_t["price_usd"] or 0)
                        _ia = _inow - float(_t["ts_ms"] or 0) / 1000.0
                        if _ip > 0 and _ia >= 0:
                            _intel_prices[_m] = {"price": _ip, "age": _ia}
                break  # first working intel DB is enough
            except Exception:
                pass

        now = time.time()
        payload = []
        seen_ids = set()

        for r in rows:
            _pos_id = r["id"]
            if _pos_id in seen_ids:
                continue
            seen_ids.add(_pos_id)

            _keys = set(r.keys()) if hasattr(r, "keys") else set()

            def _get(key, default=None):
                try:
                    return r[key] if key in _keys else default
                except Exception:
                    return default

            entry = float(_get("entry_price", 0.0) or 0.0)
            size  = float(_get("position_size_usd", 0.0) or 0.0)

            # Execution engine owns live visible truth.
            _lep     = float(_get("live_exec_price", 0.0) or 0.0)
            _lect_raw = _get("live_exec_pct", None)
            _leu     = float(_get("live_exec_updated_at", 0.0) or 0.0)
            _can_exit = int(_get("live_exec_can_exit", 0) or 0)

            # SAME-EYES: if executor hasn't written live_exec yet (new position),
            # read directly from Intel DB mtm_ticks - same Tier-1 source as executor.
            # This closes the gap where meter shows 0% until executor catches up.
            _mint = str(_get("mint_address", "") or "")
            _opened_at = float(_get("opened_at", 0.0) or 0.0)
            _intel = _intel_prices.get(_mint)
            if (_intel and _intel["price"] > 0 and _intel["age"] < 120
                    and (_lect_raw is None or _can_exit != 1)):
                _ip  = _intel["price"]
                _ia  = _intel["age"]
                _ep2 = float(_get("entry_price", 0.0) or 0.0)
                if _ep2 > 0 and _ip > 0:
                    _intel_pct = (_ip - _ep2) / _ep2 * 100.0
                    _lep      = _ip
                    _lect_raw = _intel_pct
                    _leu      = now - _ia
                    _can_exit = 1 if _ia < 120 else 0

            if _lep > 0 and _lect_raw is not None and _can_exit == 1:
                pnl_pct = float(_lect_raw)
                pnl = size * (pnl_pct / 100.0) if size > 0 else 0.0
                mark_age = int(now - _leu) if _leu > 0 else 9999
                is_fresh = mark_age < 15
                age_str = f"{mark_age}s"
                src_badge = "EXEC_FRESH" if is_fresh else "STALE_EXEC"
            elif _lep > 0 and _lect_raw is not None and _can_exit != 1:
                # Price exists but router says not executable - show ?? not fake %
                pnl_pct = None
                pnl = 0.0
                mark_age = int(now - _leu) if _leu > 0 else 9999
                is_fresh = False
                age_str = f"{mark_age}s"
                src_badge = "GATE_BLOCKED"
            else:
                pnl_pct = None
                pnl = 0.0
                mark_age = 9999
                is_fresh = False
                age_str = "NO EXEC"
                src_badge = "NO_EXEC_DATA"

            _ss_key = f"_prev_pct_{_pos_id}"
            _prev = st.session_state.get(_ss_key)
            if pnl_pct is not None:
                st.session_state[_ss_key] = pnl_pct

            # Post-entry tick count from Intel DB for coverage display
            _tick_count = 0
            try:
                import sqlite3 as _sq3
                _idb_path = str(_Path(__file__).resolve().parent.parent / "sentinuity_intelligence.db")
                _ic = _sq3.connect(_idb_path, timeout=1.0)
                _tc_row = _ic.execute(
                    "SELECT COUNT(*) FROM mtm_ticks WHERE mint_address=? AND ts_ms>=?",
                    (str(_get("mint_address", "") or ""), float(_get("opened_at", 0) or 0) * 1000)
                ).fetchone()
                _ic.close()
                _tick_count = int(_tc_row[0]) if _tc_row else 0
            except Exception:
                pass

            payload.append({
                "id":                 _pos_id,
                "token":              display_name(token_name=_get("token_name", ""), mint=_get("mint_address", ""))[:18],
                "pnl":                pnl,
                "pnl_pct":            pnl_pct,
                "prev_pnl_pct":       _prev,
                "is_fresh":           is_fresh,
                "age_str":            age_str,
                "src_badge":          src_badge,
                "resolved_price":     _lep if _lep > 0 else None,
                "resolved_updated_at": _leu if _leu > 0 else None,
                "tick_count":         _tick_count,
                "raw_row":            dict(r),
            })

        return payload

    except Exception as e:
        _hub_log.warning(f"UI Breathe failed: {e}")
        return []

@st.cache_data(ttl=20, show_spinner=False)
def _fetch_all_dashboard_data():
    conn = None
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=2000")   # was 15000 - fail fast, return stale from cache
        conn.row_factory = sqlite3.Row

        _wallet_df      = pd.read_sql_query("SELECT COALESCE((SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_WALLET_EQUITY_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='SOLANA_PAPER_WALLET_EQUITY_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_EQUITY_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_TRADING_BALANCE_USD'),wallet_balance) AS wallet_balance, COALESCE((SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_INITIAL_CAPITAL_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='SOLANA_PAPER_INITIAL_CAPITAL_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_EQUITY_BASELINE_USD'),(SELECT CAST(value AS REAL) FROM system_config WHERE key='PAPER_STARTING_BALANCE_USD'),initial_capital) AS initial_capital FROM system_state WHERE id=1 LIMIT 1", conn)
        # INGEST_IDENTITY_TRUTH_20260718: raw_dna is a short-lived queue and is
        # deliberately pruned after resolution.  Counting only processed_state=1
        # therefore reports zero even while the ingest organism is healthy.  The
        # identity card now uses durable pipeline lineage, while retaining live
        # queue and recent-intake telemetry as separate fields.
        _raw_dna_df = pd.read_sql_query("""
            SELECT
              MAX(
                (SELECT COUNT(*) FROM raw_dna),
                (SELECT COUNT(*) FROM market_snapshots),
                (SELECT COUNT(*) FROM resolved_transactions)
              ) AS count,
              (SELECT COUNT(*) FROM raw_dna) AS raw_active,
              (SELECT COUNT(*) FROM raw_dna
                 WHERE COALESCE(first_seen_at, timestamp, 0) >= unixepoch() - 600
              ) AS raw_recent_10m,
              (SELECT COUNT(*) FROM market_snapshots) AS snapshots_total,
              (SELECT COUNT(*) FROM resolved_transactions) AS resolved_total
        """, conn)
        _snapshots_df   = pd.read_sql_query("""
            SELECT id, token_name, mint_address, mint_confidence, candidate_state, quality_status, price_status, latched
            FROM market_snapshots
            WHERE candidate_state = 'latched'
              OR id IN (SELECT id FROM market_snapshots ORDER BY id DESC LIMIT 50)
            ORDER BY id DESC LIMIT 100
        """, conn)
        _open_pos_df    = pd.read_sql_query("""
            -- TRADE_TYPE_DERIVED_PATCH
            SELECT id, token_name, mint_address, status,
                   entry_price, position_size_usd, unrealized_pnl_usd,
                   opened_at, last_marked_at, last_price, highest_price_seen,
                   take_profit_pct, stop_loss_pct,
                   live_exec_price, live_exec_pct, live_exec_band,
                   live_exec_updated_at, live_exec_source,
                   entry_price_source,
                   COALESCE(funding_mode, CASE
                     WHEN entry_price_source LIKE 'LIVE:%'
                       OR entry_price_source LIKE 'live_tx:%' THEN 'REAL'
                     ELSE 'SIM'
                   END) AS funding_mode,
                   COALESCE(execution_source, CASE
                     WHEN entry_price_source LIKE 'PAPER_ONLY%' THEN 'PAPER_ENGINE'
                     WHEN entry_price_source LIKE 'LIVE:%'
                       OR entry_price_source LIKE 'live_tx:%' THEN 'REAL_TX'
                     ELSE 'PAPER_ENGINE'
                   END) AS execution_source,
                   COALESCE(money_source, CASE
                     WHEN entry_price_source LIKE 'LIVE:%'
                       OR entry_price_source LIKE 'live_tx:%' THEN 'REAL_WALLET'
                     ELSE 'SIM_EQUITY'
                   END) AS money_source,
                   'OPEN - ' || COALESCE(funding_mode, CASE
                     WHEN entry_price_source LIKE 'LIVE:%'
                       OR entry_price_source LIKE 'live_tx:%' THEN 'REAL'
                     ELSE 'SIM'
                   END) AS trade_type,
                   entry_price AS dashboard_last_price,
                   last_marked_at AS dashboard_last_marked_at,
                   unrealized_pnl_usd AS dashboard_unrealized,
                   NULL AS mtm_age_seconds
            FROM paper_positions WHERE status = 'OPEN' ORDER BY opened_at DESC
        """, conn)
        _executions_df    = pd.read_sql_query("SELECT * FROM paper_executions ORDER BY id DESC LIMIT 20", conn)
        _reviews_df       = pd.read_sql_query("SELECT win_loss, realized_pnl_usd, exit_category, hold_seconds, entry_mint_confidence FROM polaris_trade_reviews ORDER BY reviewed_at DESC LIMIT 50", conn)
        _proposals_df     = pd.read_sql_query("SELECT id, proposal_type, proposal_text, suggested_action, confidence, status, created_at, unified_diff, rewritten_code FROM polaris_proposals ORDER BY created_at DESC LIMIT 20", conn)
        _proposals_df     = _apply_focus_lock(_proposals_df)
        _debate_df        = pd.read_sql_query(
            "SELECT speaker, "
            "COALESCE(json_extract(content_json,'$.verdict'),json_extract(content_json,'$.summary'),"
            "json_extract(content_json,'$.rebuttal_summary'),message,action,'') AS verdict_text, "
            "thinking_state, verdict_type, approved_by, logged_at, content_json, "
            "COALESCE(json_extract(content_json,'$.search_query'),'')  AS oracle_query, "
            "COALESCE(json_extract(content_json,'$.confirmed'),'')     AS oracle_confirmed, "
            "COALESCE(json_extract(content_json,'$.evidence_snippets'),'') AS oracle_snippets, "
            "COALESCE(json_extract(content_json,'$.winner'),'')        AS nugget_winner, "
            "COALESCE(json_extract(content_json,'$.reason'),'')        AS nugget_reason, "
            "COALESCE(json_extract(content_json,'$.confidence'),'')    AS nugget_confidence, "
            "COALESCE(json_extract(content_json,'$.recommended_next_step'),'') AS nugget_next, "
            "COALESCE(json_extract(content_json,'$.objections'),'')    AS ivaris_objections, "
            "COALESCE(json_extract(content_json,'$.narrative'),'') AS grok_narrative, "
            "COALESCE(proposal_id, NULL) AS proposal_id "
            "FROM debate_log ORDER BY logged_at DESC LIMIT 30",
            conn
        )
        if _debate_df.empty:
            _dh_raw = pd.read_sql_query("SELECT id, proposal_id, transcript_json, created_at FROM debate_history ORDER BY created_at DESC LIMIT 20", conn)
            if not _dh_raw.empty:
                import json as _json
                _mapped = []
                for _, _dh_row in _dh_raw.iterrows():
                    _ts = float(_dh_row.get("created_at") or 0)
                    _raw = _dh_row.get("transcript_json") or "[]"
                    try: _turns = _json.loads(_raw) if isinstance(_raw, str) else _raw
                    except Exception: _turns = []
                    if not isinstance(_turns, list): _turns = [_turns] if isinstance(_turns, dict) else []
                    for idx, _turn in enumerate(_turns):
                        _spk    = str(_turn.get("speaker") or "SYSTEM").upper()
                        _action = str(_turn.get("action") or "")
                        _result = _turn.get("result") or {}
                        _vtext = (_result.get("verdict") or _result.get("summary") or _result.get("rebuttal_summary") or _action)
                        if _action == "initial_critique": _tstate, _vtype = "critiquing", "initial_critique"
                        elif _action == "rebuttal": _tstate, _vtype = "rebutting", "rebuttal"
                        elif _action == "rebuttal_evaluation": _tstate, _vtype = "evaluating", "rebuttal_evaluation"
                        else: _tstate, _vtype = "critiquing", _action
                        if idx == len(_turns) - 1:
                            if _result.get("_critic_unavailable") or _result.get("_cognitive_failure"): _tstate, _vtype = "blocked", "blocked"
                            elif _result.get("consensus"): _tstate, _vtype = "consensus", "final_consensus"
                            else: _tstate, _vtype = "rejected", "final_rejection"
                        _mapped.append({"speaker": _spk, "verdict_text": str(_vtext or ""), "thinking_state": _tstate, "verdict_type": _vtype, "approved_by": str(_result.get("approved_by") or ""), "logged_at": _ts + idx * 0.001})
                if _mapped: _debate_df = pd.DataFrame(_mapped)

        _calibration_df   = pd.read_sql_query("SELECT key, value FROM system_config WHERE key IN ('SUPERVISOR_MIN_MINT_CONFIDENCE','DRAWDOWN_HALT_ACTIVE','DRAWDOWN_ACCUMULATED_PCT','POSITION_SIZE_PCT','STOP_LOSS_PCT','TAKE_PROFIT_PCT','MIN_LIQUIDITY_USD','TRAIL_ACTIVATE_PCT')", conn)
        _open_count_df    = pd.read_sql_query("SELECT COUNT(*) as n FROM paper_positions WHERE status='OPEN'", conn)
        _heal_log_df      = pd.read_sql_query("SELECT timestamp, stage, message FROM cognition_log WHERE stage IN ('GUARDIAN','HEALER','WATCHDOG','HEALTH','POLARIS','SYS','EXECUTOR','SUPERVISOR','DEBATE','SYSTEM','SCOUT','SYMBIOTIC') ORDER BY timestamp DESC LIMIT 8", conn)
        _heartbeat_df     = pd.read_sql_query("SELECT service_name, status, last_pulse, COALESCE(note, '') AS note FROM system_heartbeat ORDER BY service_name ASC", conn)
        _patch_history_df = pd.read_sql_query("SELECT applied_at, NULL AS proposal_id, proposal_type, action, outcome, NULL AS confidence, brave_confirmed, '' AS notes FROM patch_history ORDER BY applied_at DESC LIMIT 10", conn)
        _autopsy_df       = pd.read_sql_query("SELECT id, win_loss, realized_pnl_usd FROM trade_autopsies ORDER BY id ASC LIMIT 100", conn)
        _cognition_df     = pd.read_sql_query("SELECT id, timestamp AS sort_ts, timestamp, COALESCE(stage, 'SCOUT') AS stage, COALESCE(token, '') AS token, message, COALESCE(confidence, 0.0) AS confidence FROM cognition_log ORDER BY timestamp DESC LIMIT 25", conn)

        return (_wallet_df, _raw_dna_df, _snapshots_df, _open_pos_df, _executions_df, _reviews_df, _proposals_df, _debate_df, _calibration_df, _open_count_df, _heal_log_df, _heartbeat_df, _patch_history_df, _autopsy_df, _cognition_df)

    except Exception:
        empty = pd.DataFrame()
        return (empty,) * 15

    finally:
        if conn:
            conn.close()

def _fmt_clock(ts: float) -> str:
    try: return time.strftime("%H:%M:%S", time.localtime(float(ts)))
    except Exception: return ""

@st.cache_data(ttl=15, show_spinner=False)
def build_live_event_feed(cognition_df, executions_df, open_pos_df, snapshots_df, proposals_df):
    frames = []
    if cognition_df is not None and not cognition_df.empty:
        base = cognition_df.copy()
        for col, default in [("sort_ts", 0.0), ("timestamp", ""), ("stage", "SCOUT"), ("token", ""), ("message", ""), ("confidence", 0.0), ("id", 0)]:
            if col not in base.columns: base[col] = default
        frames.append(base[["id", "sort_ts", "timestamp", "stage", "token", "message", "confidence"]])

    if executions_df is not None and not executions_df.empty:
        exec_rows = []
        for _, row in executions_df.head(12).iterrows():
            ts = float(row.get("timestamp") or row.get("created_at") or 0.0)
            side = str(row.get("side", "")).upper()
            if not (row.get("token_name") or row.get("mint_address")): continue
            token = display_for_row(row)
            price = float(row.get("price") or 0.0)
            value = float(row.get("notional_usd") or row.get("value_usd") or 0.0)
            reason = str(row.get("reason", "") or "").strip()
            if side == "BUY": msg = f"Deployed {chr(36)}{value:.2f} into {token}. Entry armed at {chr(36)}{price:.8f}." + (f" Trigger: {reason}." if reason else "")
            elif side == "SELL": msg = f"Motor output closed {token} at {chr(36)}{price:.8f} for {chr(36)}{value:.2f}." + (f" Exit: {reason}." if reason else "")
            else: msg = f"Execution event on {token} at {chr(36)}{price:.8f}."
            exec_rows.append({"id": 1_000_000 + int(row.get("id", 0) or 0), "sort_ts": ts, "timestamp": _fmt_clock(ts), "stage": "EXECUTOR", "token": token[:14], "message": msg, "confidence": 0.0})
        if exec_rows: frames.append(pd.DataFrame(exec_rows))

    if snapshots_df is not None and not snapshots_df.empty:
        snap_rows = []
        for _, row in snapshots_df.head(16).iterrows():
            state = str(row.get("candidate_state", "") or "").lower()
            if not (row.get("token_name") or row.get("mint_address")): continue
            token = display_for_row(row)
            conf = float(row.get("mint_confidence") or 0.0)
            ts = float(row.get("price_updated_at") or row.get("updated_at") or row.get("created_at") or row.get("timestamp") or 0.0)
            if state in {"latched", "qualified"}: msg, stage = f"All gates passed for {token}. Signal latched at conf {conf:.3f}.", "SUPERVISOR"
            elif state in {"vetoed", "rejected"}: msg, stage = f"{token} vetoed at the gate memory layer.", "SUPERVISOR"
            else: continue
            snap_rows.append({"id": 2_000_000 + int(row.get("id", 0) or 0), "sort_ts": ts, "timestamp": _fmt_clock(ts), "stage": stage, "token": token[:14], "message": msg, "confidence": conf})
        if snap_rows: frames.append(pd.DataFrame(snap_rows))

    if not frames: return pd.DataFrame(columns=["id", "sort_ts", "timestamp", "stage", "token", "message", "confidence"])
    merged = pd.concat(frames, ignore_index=True, sort=False)
    merged["dedup_key"] = merged["stage"].astype(str).fillna("") + "|" + merged["token"].astype(str).fillna("") + "|" + merged["message"].astype(str).fillna("")
    merged = merged.sort_values(["sort_ts", "id"], ascending=[False, False]).drop_duplicates(subset=["dedup_key"], keep="first")
    return merged.head(40).copy()

def get_dominant_state(halted, latency, open_pos, recent_heal_count, win_r):
    if halted:              return "HALTED",        "The organism has severed execution nerves to protect capital.", C_RED
    if latency > 1000:      return "WOUNDED",       "Experiencing severe neural lag. Reflexes impaired.", C_IVY
    if recent_heal_count>0: return "SELF-HEALING", "Actively repairing pipeline blockages and restoring flow.", C_PURPLE
    if open_pos > 0:        return "EXECUTING",    "Exposed to the market. Managing active live capital.", C_GOLD
    if win_r > 60:          return "ASCENDING",    "Highly calibrated, profitable, and scanning for prime entry.", C_PURPLE
    return "HUNTING", "Stable, selective, and hunting with strict restraint.", C_PURPLE

def write_organism_state(dom_state, dom_col, cog_df, auto_df):
    def safe_int(v):
        try: return int(float(v)) if pd.notna(v) else 0
        except: return 0
    feed_data = [{"id": safe_int(r.get('id')), "stage": str(r.get('stage','')).upper(), "token": str(r.get('token',''))[:14], "message": str(r.get('message','')), "timestamp": str(r.get('timestamp',''))[-8:][:5], "color": dom_col} for _, r in cog_df.iterrows()]
    canopy_nodes = [{"id": safe_int(r.get('id')), "type": "WIN" if str(r.get('win_loss','')).upper()=="WIN" else "LOSS", "val": abs(float(r.get('realized_pnl_usd',0)))*2} for _, r in auto_df.iterrows()]
    return json.dumps({"dominant_state": dom_state, "color": dom_col, "cognition": feed_data, "canopy": canopy_nodes})

def build_cognitive_canopy(state_json_str: str) -> str:
    return f"""<!DOCTYPE html>
    <html><head>
        <style>
            @import url('https://fonts.googleapis.com/css2?family=VT323&display=swap');
            body {{ margin:0; padding:0; background:transparent; color:#8EF9FF; font-family:'VT323',monospace; overflow:hidden; display:flex; flex-direction:column; gap:15px; height:100vh;}}
            #state-bar {{ display:flex; align-items:center; justify-content:space-between; padding:10px 20px; background:rgba(5,10,10,0.6); border:1px solid #8EF9FF44; border-radius:12px; box-shadow:0 0 20px rgba(0,0,0,0.5);}}
            #voice-init {{ background:#14F19522; color:#14F195; border:1px solid #14F195; padding:8px 16px; font-family:'VT323',monospace; letter-spacing:2px; cursor:pointer; border-radius:6px; transition:0.3s; }}
            #main-arena {{ display:flex; gap:15px; height:calc(100vh - 60px); }}
            #eternal-cortex {{ flex:1; overflow:hidden; background:rgba(5,10,10,0.4); border:1px solid #8EF9FF44; border-radius:16px; position:relative; box-shadow:inset 0 0 40px rgba(0,0,0,0.8); }}
            #thought-stream {{ height:100%; overflow-y:auto; padding:20px; scrollbar-width:none; }}
            #thought-stream::-webkit-scrollbar {{ display:none; }}
            #canopy-container {{ flex:1.5; border-radius:16px; border:1px solid #FFD70033; overflow:hidden; position:relative; box-shadow:0 0 30px #FFD70011; background: radial-gradient(ellipse at 50% 50%, rgba(153,69,255,0.08) 0%, transparent 70%), radial-gradient(ellipse at 20% 80%, rgba(20,241,149,0.05) 0%, transparent 50%), #050210; }}
        </style>
    </head><body>
    <div id="organism-state-data" style="display:none;visibility:hidden;position:absolute;">{state_json_str}</div>
    <div id="state-bar">
        <div style="display:flex;align-items:center;gap:15px;"><button id="voice-init">AWAKEN AUTONOMIC RESONANCE</button><span id="voice-state" style="color:#8EF9FF;letter-spacing:2px;font-size:1.1rem;">CORTEX DORMANT</span></div>
        <div id="visual-state" style="font-size:1.4rem;font-weight:bold;letter-spacing:4px;text-shadow:0 0 10px #FFF;">SYNCING...</div>
    </div>
    <div id="main-arena"><div id="eternal-cortex"><div id="thought-stream"></div></div><div id="canopy-container"></div></div>
    <script>
        setTimeout(function() {{
            try {{
                var stateEl = document.getElementById('organism-state-data');
                var _raw = stateEl.textContent || stateEl.innerText || '';
                var liveState = JSON.parse(_raw);
                document.getElementById('visual-state').innerText = liveState.dominant_state;
                document.getElementById('visual-state').style.color = liveState.color;

                var stream = document.getElementById('thought-stream');
                var known = new Set();
                try {{ known = new Set(JSON.parse(sessionStorage.getItem('eternalBrain') || '[]')); }} catch(e){{}}
                
                var sorted = (liveState.cognition || []).slice().sort((a,b) => a.id - b.id);
                sorted.forEach(entry => {{
                    if(known.has(entry.id)) return;
                    known.add(entry.id);
                    var t = document.createElement('div');
                    t.style.cssText = 'margin-bottom:18px;padding:12px 16px;background:rgba(5,2,16,0.6);border-left:5px solid ' + entry.color + ';';
                    t.innerHTML = '<span style="color:' + entry.color + ';font-weight:900;">' + entry.stage + '</span><span style="float:right;color:#FFD700;font-size:0.85rem;">' + entry.timestamp + '</span><br><span style="color:#9945FF;">' + (entry.token || '-') + '</span> - <span style="color:#FFF;">' + entry.message + '</span>';
                    stream.appendChild(t);
                }});
                stream.scrollTop = stream.scrollHeight;
                try {{ sessionStorage.setItem('eternalBrain', JSON.stringify(Array.from(known))); }} catch(e){{}}

                var _cc = document.getElementById('canopy-container');
                if(!liveState.canopy || liveState.canopy.length === 0) {{
                    _cc.innerHTML = '<div style="position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);color:rgba(153,69,255,0.35);font-family:VT323,monospace;font-size:1.1rem;letter-spacing:3px;text-align:center;">TEMPORAL CANOPY<br><span style="font-size:.7rem;opacity:.6;">AWAITING TRADE HISTORY</span></div>';
                }} else {{
                    let wins = liveState.canopy.filter(n => n.type === 'WIN');
                    let losses = liveState.canopy.filter(n => n.type === 'LOSS');
                    let svgHtml = '<div style="position:absolute; top:15px; left:15px; z-index:10; font-family:VT323,monospace; font-size:1.2rem; color:#8EF9FF;">CANOPY CONSTELLATION<br><span style="color:#FFD700">WINS: ' + wins.length + '</span> | <span style="color:#FF073A">LOSSES: ' + losses.length + '</span> | TOTAL: ' + liveState.canopy.length + '</div>';
                    svgHtml += '<svg width="100%" height="100%" viewBox="-250 -250 500 500" preserveAspectRatio="xMidYMid meet" style="position:absolute;top:0;left:0;"><style>.core {{ fill: #9945FF; animation: pulseCore 2s infinite alternate; }} @keyframes pulseCore {{ 0% {{ r: 8; opacity: 0.6; }} 100% {{ r: 14; opacity: 1; filter: drop-shadow(0 0 10px #9945FF); }} }}</style><circle class="core" cx="0" cy="0" r="10" />';
                    liveState.canopy.forEach(function(n) {{
                        let angle = (n.id * 137.508) % 360; let rad = angle * Math.PI / 180;
                        let isWin = n.type === 'WIN'; let radius = isWin ? (120 + (n.id % 60)) : (50 + (n.id % 40));
                        let cx = Math.cos(rad) * radius; let cy = Math.sin(rad) * radius;
                        let r = isWin ? 3.5 : 2.5; let fill = isWin ? '#FFD700' : '#FF073A';
                        let dur = 3 + (n.id % 4); let rotDur = 40 + (n.id % 60); let dir = isWin ? 360 : -360;
                        svgHtml += '<g><animateTransform attributeName="transform" type="rotate" from="0 0 0" to="' + dir + ' 0 0" dur="' + rotDur + 's" repeatCount="indefinite" /><circle cx="' + cx + '" cy="' + cy + '" r="' + r + '" fill="' + fill + '" opacity="0.8"><animate attributeName="opacity" values="0.3;1;0.3" dur="' + dur + 's" repeatCount="indefinite" /></circle></g>';
                    }});
                    svgHtml += '</svg>';
                    _cc.innerHTML = svgHtml;
                }}
            }} catch(err) {{
                var vs = document.getElementById('visual-state');
                if(vs) {{ vs.innerText = 'CORTEX FAULT: ' + err.message; vs.style.color = '#FF073A'; }}
            }}
        }}, 50);
    </script></body></html>"""

@st.cache_data(ttl=120, show_spinner=False)
def build_dynamic_css(dom_col="#9945FF"):
    return f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=VT323&display=swap');
    @import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@400;500;700;800;900&family=Share+Tech+Mono&family=Rajdhani:wght@300;400;600;700&display=swap');

    :root {{
        --dom-color: {dom_col};
        --mycelial-purple: #9945FF;
        --machine-cyan: #8EF9FF;
        --north-star-cyan: #8EF9FF;
        --golden: #FFD700;
        --nugget-bronze: #C19A6B;
        --glass: rgba(5,10,10,0.32);
        --vein: rgba(153,69,255,0.28);
    }}

    /* NEURAL WEAVE - living gold-purple background veins */
    html::before {{
        content: ""; position: fixed; inset: 0; z-index: -3; pointer-events: none;
        background:
            radial-gradient(circle at 20% 30%, rgba(153,69,255,0.15) 0%, transparent 45%),
            radial-gradient(circle at 80% 70%, rgba(255,215,0,0.10) 0%, transparent 45%),
            radial-gradient(circle at 50% 90%, rgba(142,249,255,0.08) 0%, transparent 40%);
        animation: neuralWeave 28s linear infinite alternate;
    }}
    @keyframes neuralWeave {{
        0%   {{ opacity: 0.7; transform: scale(1.0); }}
        50%  {{ opacity: 1.0; transform: scale(1.04); }}
        100% {{ opacity: 0.8; transform: scale(0.98); }}
    }}

    html,body,[data-testid="stAppViewContainer"],.stApp {{
        background: radial-gradient(circle at 50% 30%, #1a0033 0%, #050210 70%) !important;
        color: var(--mycelial-purple);
        font-family: 'VT323', monospace;
        animation: mycelialPulse 18s infinite ease-in-out;
    }}
    @keyframes mycelialPulse {{
        0%, 100% {{ background-position: 0% 50%; }}
        50% {{ background-position: 100% 50%; }}
    }}

    [data-testid="stHeader"],[data-testid="stToolbar"],[data-testid="stDecoration"],
    .stSpinner,[data-testid="stStatusWidget"],footer,.stFooter {{
        display: none !important;
    }}
    .stApp,.stAppViewContainer {{ transition: none !important; }}

    /* ── NO-DIM: suppress Streamlit's stale-element dimming during rerun ── */
    /* Without this, every refresh dims the entire page while Streamlit reloads */
    [data-stale="true"], [data-stale] {{
        opacity: 1 !important;
        pointer-events: auto !important;
        filter: none !important;
        transition: none !important;
    }}
    /* Suppress the running/reloading indicator dot */
    [data-testid="stStatusWidget"], .stStatusWidget,
    div[class*="StatusWidget"], div[class*="statusWidget"] {{
        display: none !important;
    }}
    /* Keep old data visible and unblurred until new data overwrites */
    .stMarkdown, .stDataFrame, .stTable, .element-container {{
        transition: none !important;
    }}

    /* ── ZERO-LEAK SHIELD: raw errors never surface in organism layer ── */
    /* Stack traces, SQL dumps, Python errors stay in engineering panels only */
    .stException, [data-testid="stException"],
    div[class*="Exception"], .stAlert[data-baseweb="notification"] {{
        display: none !important;
    }}

    /* ── TEMPORAL HEATMAP STATE - ambient freshness atmosphere ── */
    /* CSS vars updated by JS below; create subconscious freshness awareness */
    :root {{
        --organism-heat: 0.0;
        --heat-glow: rgba(20, 241, 149, 0.03);
        --heat-border: rgba(20, 241, 149, 0.15);
    }}
    /* Subtle background pulse reflects organism heat level */
    [data-testid="stAppViewContainer"] {{
        background: radial-gradient(
            ellipse at 50% 0%,
            var(--heat-glow) 0%,
            transparent 60%
        ) !important;
    }}

    /* ── PANELS ── */
    .panel,.nerve-card,.lore-card,#dc-wrap,.hb-strip,.stDataFrame,.stTable,.stAlert {{
        background: var(--glass) !important;
        backdrop-filter: blur(8px) saturate(115%) !important;
        -webkit-backdrop-filter: blur(8px) saturate(115%) !important;
        border: 1px solid rgba(153,69,255,0.42) !important;
        border-radius: 16px !important;
        box-shadow: 0 0 28px rgba(153,69,255,0.24), inset 0 0 22px rgba(142,249,255,0.10) !important;
        margin-bottom: 20px;
        position: relative;
    }}
    .panel:hover,.lore-card:hover,#dc-wrap:hover {{
        box-shadow: 0 0 40px rgba(153,69,255,0.45), inset 0 0 25px rgba(142,249,255,0.15) !important;
    }}
    /* Mycelial veins connecting panels */
    .panel::after {{
        content: '';
        position: absolute;
        bottom: -20px; left: 50%;
        width: 3px; height: 40px;
        background: linear-gradient(to bottom, var(--vein), transparent);
        transform: translateX(-50%);
        z-index: -1;
    }}

    /* ── TYPOGRAPHY ── */
    h1 {{
        background: linear-gradient(90deg, #9945FF 0%, #FFD700 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        text-shadow: 0 0 35px rgba(153,69,255,0.85), 0 0 60px rgba(255,215,0,0.55) !important;
        font-size: clamp(2.6rem, 9vw, 4.4rem) !important;
        letter-spacing: clamp(8px, 3.5vw, 20px) !important;
        line-height: 1.05 !important;
        margin-bottom: 8px !important;
        padding: 0 10px;
    }}
    h2,h3 {{ text-shadow: 0 0 12px {C_PURPLE}, 0 0 24px {C_PURPLE}88; color: var(--mycelial-purple) !important; font-family: 'Orbitron', sans-serif !important; letter-spacing: 4px !important; }}
    p[style*="letter-spacing"] {{ color: var(--north-star-cyan) !important; text-shadow: 0 0 16px rgba(142,249,255,0.65) !important; font-size: 1.18rem !important; }}
    .stMarkdown p,label {{ color: #8EF9FF !important; text-shadow: 0 0 8px rgba(142,249,255,0.45); }}

    /* ── METRICS ── restore June-11 style: VT323 gold value, muted label, green delta ── */
    [data-testid="stMetric"] {{ padding: 8px 12px !important; }}
    [data-testid="stMetricLabel"] {{ font-family: 'Share Tech Mono', monospace !important; font-size: 0.84rem !important; letter-spacing: 1.5px !important; margin-bottom: 4px !important; opacity: 0.9; color: #C19A6B !important; }}
    [data-testid="stMetricLabel"] * {{ font-family: 'Share Tech Mono', monospace !important; color: #C19A6B !important; }}
    [data-testid="stMetricValue"] {{ font-family: 'VT323', monospace !important; font-size: 3.2rem !important; line-height: 1.02 !important; letter-spacing: 1px !important; color: #FFD700 !important; text-shadow: 0 0 18px rgba(255,215,0,0.75) !important; }}
    [data-testid="stMetricValue"] * {{ font-family: 'VT323', monospace !important; color: #FFD700 !important; }}
    [data-testid="stMetricDelta"] {{ font-family: 'Share Tech Mono', monospace !important; font-size: 0.72rem !important; letter-spacing: 1px !important; color: #14F195 !important; text-shadow: 0 0 6px rgba(20,241,149,0.30) !important; }}
    [data-testid="stMetricDelta"] * {{ font-family: 'VT323', monospace !important; color: #14F195 !important; fill: #14F195 !important; }}

    /* ── CLINICAL TEXT ── */
    .clinical-text {{ font-size: 0.66rem; color: #8EF9FF99; font-family: 'Share Tech Mono', monospace; }}

    /* ── TYPEWRITER ── */
    .typewriter {{
        display: inline-block;
        overflow: hidden;
        white-space: nowrap;
        border-right: 2px solid var(--dom-color);
        animation: typing 1.5s steps(50, end) forwards, blink-caret .75s step-end infinite;
    }}
    @keyframes typing {{ from {{ width: 0; opacity: 0; }} to {{ width: 100%; opacity: 1; }} }}
    @keyframes blink-caret {{ 50% {{ border-color: transparent; }} }}

    /* ── NEXT-UP FLASH ── */
    .next-up {{ animation: nextUpFlash 1.2s ease-in-out infinite alternate; }}
    @keyframes nextUpFlash {{ from {{ opacity: 0.3; text-shadow: none; }} to {{ opacity: 1; text-shadow: 0 0 15px currentColor; }} }}

    /* ── GOLDEN LATTICE ── */
    .golden-lattice {{
        background: rgba(5,10,10,0.35);
        border: 2px solid #FFD700;
        border-radius: 16px;
        padding: 20px;
        box-shadow: 0 0 30px #FFD70066, inset 0 0 20px #FFD70011;
        position: sticky; top: 10px; overflow: hidden;
    }}
    .golden-lattice::after {{
        content: ''; position: absolute; top: -50%; left: -50%; width: 40%; height: 300%;
        background: linear-gradient(120deg, transparent, rgba(255,215,0,0.4), transparent);
        transform: skewX(-25deg); animation: holographicSweep 3.5s linear infinite; pointer-events: none;
    }}
    @keyframes holographicSweep {{ 0% {{ transform: translateX(-150%) skewX(-25deg); }} 100% {{ transform: translateX(400%) skewX(-25deg); }} }}
    .golden-lattice-card {{
        background: rgba(255,215,0,0.06); border: 1px solid #FFD70044;
        border-radius: 12px; padding: 16px; margin-bottom: 14px;
        font-family: 'Share Tech Mono', monospace; font-size: 0.72rem;
        position: relative; z-index: 1;
    }}

    /* ── AGENT IDENTITY ── */
    .agent-nugget {{ color: var(--nugget-bronze) !important; border-color: var(--nugget-bronze) !important; }}

    /* POLARIS - slow cyan nebula breathe */
    @keyframes polarisGlow {{
        0%   {{ text-shadow: 0 0 6px rgba(142,249,255,0.45), 0 0 14px rgba(142,249,255,0.2); opacity: 0.85; }}
        50%  {{ text-shadow: 0 0 20px rgba(142,249,255,0.9), 0 0 40px rgba(142,249,255,0.45); opacity: 1; }}
        100% {{ text-shadow: 0 0 6px rgba(142,249,255,0.45), 0 0 14px rgba(142,249,255,0.2); opacity: 0.85; }}
    }}
    .arena-msg-polaris {{ animation: polarisGlow 4s ease-in-out infinite; color: #d0f8ff !important; font-family: 'Rajdhani',sans-serif !important; letter-spacing: 0.3px; }}

    /* IVARIS - hot amber ember flicker */
    @keyframes ivarisFlicker {{
        0%   {{ text-shadow: 0 0 5px rgba(255,179,71,0.5); opacity: 0.9; }}
        30%  {{ text-shadow: 0 0 18px rgba(255,179,71,1.0), 0 0 34px rgba(255,80,0,0.5); opacity: 1; }}
        70%  {{ text-shadow: 0 0 8px rgba(255,179,71,0.6); opacity: 0.92; }}
        100% {{ text-shadow: 0 0 5px rgba(255,179,71,0.5); opacity: 0.9; }}
    }}
    .arena-msg-ivaris {{ animation: ivarisFlicker 2.8s ease-in-out infinite; color: #ffe0b0 !important; font-family: 'Rajdhani',sans-serif !important; letter-spacing: 0.3px; }}

    /* ORACLE - cold machine-green scan pulse */
    @keyframes oracleScan {{
        0%   {{ text-shadow: 0 0 2px rgba(20,241,149,0.3); opacity: 0.8; }}
        35%  {{ text-shadow: 0 0 8px rgba(20,241,149,0.7), 0 0 14px rgba(20,241,149,0.2); opacity: 1; }}
        100% {{ text-shadow: 0 0 2px rgba(20,241,149,0.3); opacity: 0.8; }}
    }}
    .arena-msg-oracle {{ animation: oracleScan 1.8s ease-in-out infinite; color: #b0ffe0 !important; font-family: 'Share Tech Mono',monospace !important; font-size: 0.78rem !important; letter-spacing: 0.5px; }}

    /* NUGGET - bronze shimmer */
    @keyframes nuggetShimmer {{
        0%   {{ text-shadow: 0 0 6px rgba(193,154,107,0.55), 0 0 14px rgba(255,215,0,0.18); opacity: 0.88; }}
        40%  {{ text-shadow: 0 0 18px rgba(193,154,107,0.95), 0 0 32px rgba(255,215,0,0.42); opacity: 1; }}
        100% {{ text-shadow: 0 0 6px rgba(193,154,107,0.55), 0 0 14px rgba(255,215,0,0.18); opacity: 0.88; }}
    }}
    .arena-msg-nugget {{ animation: nuggetShimmer 3.2s ease-in-out infinite; color: #f0dab8 !important; font-family: 'Rajdhani',sans-serif !important; letter-spacing: 0.4px; }}

    /* Consensus golden burst */
    @keyframes consensusBurst {{
        0%   {{ background: rgba(255,215,0,0.0); box-shadow: none; }}
        20%  {{ background: rgba(255,215,0,0.12); box-shadow: 0 0 30px rgba(255,215,0,0.4); }}
        100% {{ background: rgba(255,215,0,0.02); box-shadow: 0 0 6px rgba(255,215,0,0.08); }}
    }}
    .arena-consensus {{ animation: consensusBurst 3s ease-out forwards !important; border-color: #FFD700 !important; }}

    /* Patch activity pulse */
    @keyframes patchPulse {{
        0%   {{ border-left-color: #14F195; box-shadow: -3px 0 10px rgba(20,241,149,0.5); }}
        50%  {{ border-left-color: #FFD700; box-shadow: -3px 0 18px rgba(255,215,0,0.7); }}
        100% {{ border-left-color: #14F195; box-shadow: -3px 0 10px rgba(20,241,149,0.5); }}
    }}
    .arena-patch {{ animation: patchPulse 1.5s ease-in-out infinite; }}

    /* Unfold entry */
    @keyframes unfoldText {{ 0% {{ opacity: 0; transform: translateY(5px); }} 100% {{ opacity: 1; transform: translateY(0); }} }}
    .unfold {{ animation: unfoldText 0.6s ease-out forwards; }}

    /* ── STALENESS SIREN ── */
    @keyframes sirenPulse {{
        0%,100% {{ box-shadow: 0 0 10px rgba(255,7,58,0.4); background: rgba(255,7,58,0.08); }}
        50%     {{ box-shadow: 0 0 30px rgba(255,7,58,0.9); background: rgba(255,7,58,0.18); }}
    }}
    .truth-siren {{ animation: sirenPulse 1s ease-in-out infinite; border-color: #FF073A !important; }}
    .truth-synced {{ border-color: #14F195 !important; box-shadow: 0 0 12px rgba(20,241,149,0.3) !important; }}
    
    /* ── SPORE ANIMATION ── */
    .spore-bloom {{
        animation: sporeExplode 3.2s cubic-bezier(0.23, 1, 0.32, 1) forwards;
        color: #FFD700;
        text-shadow: 0 0 30px #FFD700, 0 0 60px #9945FF;
    }}
    @keyframes sporeExplode {{
        0%   {{ transform: scale(0.1) rotate(0deg); opacity: 0; filter: blur(8px); }}
        40%  {{ transform: scale(1.4) rotate(120deg); opacity: 1; filter: blur(0); }}
        70%  {{ transform: scale(0.95) rotate(240deg); }}
        100% {{ transform: scale(1.1) rotate(360deg); opacity: 0.9; }}
    }}
    .symbiotic-root {{
        border-left: 4px solid #C19A6B;
        padding: 12px 16px;
        background: rgba(193, 154, 107, 0.08);
        border-radius: 0 12px 12px 0;
        margin-bottom: 10px;
    }}
    .model-tag {{
        display: inline-flex; align-items: center; padding: 2px 8px;
        font-family: "Share Tech Mono", monospace; font-size: 11px; font-weight: 600;
        letter-spacing: 0.8px; text-transform: uppercase; border-radius: 20px;
        background: rgba(15,15,25,0.65); backdrop-filter: blur(8px);
        border: 1px solid rgba(255,255,255,0.15); color: #fff;
        margin-left: 6px; vertical-align: middle;
    }}
    .tag-polaris {{ border-color: #8EF9FF; color: #8EF9FF; }}
    .tag-ivaris  {{ border-color: #FFB347; color: #FFB347; }}
    .tag-nugget  {{ border-color: #C19A6B; color: #C19A6B; }}
    .tag-oracle  {{ border-color: #14F195; color: #14F195; }}
    .model-tag.leading {{
        border-color: #FFD700 !important; color: #FFD700 !important;
        box-shadow: 0 0 10px #FFD700AA;
        animation: tagPulse 1.4s ease-in-out infinite alternate;
    }}
    @keyframes tagPulse {{
        from {{ box-shadow: 0 0 6px #FFD700; }}
        to   {{ box-shadow: 0 0 16px #FFD700; }}
    }}


    /* ── HARDENED LINK BADGES ── */
    .link-verified {{
        color:#8EF9FF !important; text-decoration:none !important;
        border:1px solid rgba(20,241,149,.45); padding:2px 7px; border-radius:999px;
        background:rgba(20,241,149,.08); font-family:'Share Tech Mono',monospace; font-size:0.66rem;
    }}
    .link-blocked {{
        color:#FF073A; border:1px solid rgba(255,7,58,.55); padding:2px 7px; border-radius:999px;
        background:rgba(255,7,58,.08); font-family:'Share Tech Mono',monospace; font-size:0.66rem;
    }}

    /* ── INTELLIGENCE SUBSTRATE STRIP ── */
    .substrate-wrap {{ margin-top: 22px; padding: 16px 18px 10px 18px; border:1px solid rgba(255,215,0,0.28); border-radius:16px; background:rgba(5,2,16,0.42); box-shadow:0 0 24px rgba(153,69,255,0.18); }}
    .substrate-title {{ color:#FFD700; font-family:'Share Tech Mono',monospace; font-size:0.82rem; letter-spacing:4px; }}
    .substrate-sub {{ color:#8EF9FF99; font-family:'Share Tech Mono',monospace; font-size:0.66rem; margin-top:5px; letter-spacing:1.2px; }}
    .substrate-card {{ min-height: 188px; border:1px solid; border-radius:14px; padding:14px 14px; background:rgba(255,255,255,0.025); margin-top:12px; }}
    .substrate-head {{ font-family:'Share Tech Mono',monospace; font-size:0.74rem; letter-spacing:3px; margin-bottom:8px; }}
    .substrate-muted {{ color:#888; font-size:0.68rem; font-style:italic; padding:12px 0; }}
    .substrate-source {{ color:#777; font-size:0.66rem; letter-spacing:1px; margin-bottom:8px; }}
    .substrate-row {{ border-left:3px solid rgba(255,215,0,0.35); background:rgba(255,255,255,0.025); border-radius:0 8px 8px 0; padding:8px 10px; margin-bottom:7px; font-family:'Share Tech Mono',monospace; font-size:0.66rem; }}
    .substrate-status {{ float:right; color:#FFD70099; font-size:0.66rem; }}
    .substrate-msg {{ color:#EEE; line-height:1.45; }}
    </style>"""

if img_command_center:
    st.markdown(f'<style>:root {{--bg-image: url("{img_command_center}");}}</style>', unsafe_allow_html=True)

def _latest_mtm_for_mint(mint: str) -> dict:
    """
    Returns the most recent price for a mint.
    Priority 1: candidate_state='mtm' rows (MTM oracle for open positions)
    Priority 2: any recent priced snapshot (fallback)
    """
    try:
        with sqlite3.connect(str(DB_PATH), timeout=2.0) as conn:
            conn.row_factory = sqlite3.Row
            # Priority 1: dedicated MTM row
            row = conn.execute(
                "SELECT observed_price, price_updated_at "
                "FROM market_snapshots "
                "WHERE mint_address=? AND candidate_state='mtm' AND observed_price > 0 "
                "ORDER BY price_updated_at DESC LIMIT 1",
                (mint,)
            ).fetchone()
            if row and row["observed_price"]:
                age = int(time.time() - float(row["price_updated_at"] or 0))
                return {"price": float(row["observed_price"]), "source": "mtm", "age_seconds": age, "is_fresh": age < 60}
            # Priority 2: any recent priced snapshot
            row = conn.execute(
                "SELECT observed_price, price_updated_at, candidate_state "
                "FROM market_snapshots "
                "WHERE mint_address=? AND observed_price > 0 "
                "ORDER BY price_updated_at DESC LIMIT 1",
                (mint,)
            ).fetchone()
            if row and row["observed_price"]:
                age = int(time.time() - float(row["price_updated_at"] or 0))
                return {"price": float(row["observed_price"]), "source": str(row["candidate_state"] or "snapshot"), "age_seconds": age, "is_fresh": age < 120}
    except Exception:
        pass
    return {"price": 0.0, "source": "none", "age_seconds": None, "is_fresh": False}



# SENTINUITY_RUNNER_SUBSTRATE_UI_20260621
def _snt_ro_conn(_db_path=None):
    """Short-lived read-only connection for dashboard panels."""
    import sqlite3 as _snt_sqlite3
    from pathlib import Path as _SntPath
    _p = _SntPath(str(_db_path or DB_PATH)).resolve()
    _c = _snt_sqlite3.connect(f"file:{_p.as_posix()}?mode=ro", uri=True, timeout=1.5)
    _c.row_factory = _snt_sqlite3.Row
    try:
        _c.execute("PRAGMA query_only=ON")
        _c.execute("PRAGMA busy_timeout=1000")
    except Exception:
        pass
    return _c


def _snt_float(v, default=0.0):
    try:
        if v is None or str(v).strip() == "":
            return float(default)
        return float(v)
    except Exception:
        return float(default)


def render_substrate_node_section(_db_path=None) -> None:
    """Visible Substrate Node section: bankroll, node build state, copytrade source state."""
    try:
        import html as _html
        cfg = {}
        node_count = ready_count = 0
        signal_count = new_signal_count = 0
        stage = "AWAITING"
        with _snt_ro_conn(_db_path) as _c:
            try:
                rows = _c.execute("""
                    SELECT key,value FROM system_config WHERE key IN (
                      'SUBSTRATE_PAPER_BALANCE_USD','SUBSTRATE_PAPER_CASH_USD','SUBSTRATE_PAPER_RESERVED_USD',
                      'SUBSTRATE_POSITION_SIZE_USD','SUBSTRATE_LIVE_ENABLED','SUBSTRATE_LIVE_ARMED',
                      'SUBSTRATE_LIVE_PROVIDER','SUBSTRATE_LIVE_POSITION_SIZE_USD',
                      'SUBSTRATE_COPYTRADE_PAPER_INFLUENCE','SUBSTRATE_COPYTRADE_DEMO_MODE'
                    )
                """).fetchall()
                cfg = {str(r["key"]): str(r["value"] or "") for r in rows}
            except Exception:
                cfg = {}
            try:
                tables = {r[0] for r in _c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
                if "substrate_nodes" in tables:
                    node_count = int(_c.execute("SELECT COUNT(*) FROM substrate_nodes").fetchone()[0] or 0)
                    try:
                        ready_count = int(_c.execute("SELECT COUNT(*) FROM substrate_nodes WHERE COALESCE(build_pct,0) >= 100").fetchone()[0] or 0)
                    except Exception:
                        ready_count = 0
                if "forge_projects" in tables:
                    r = _c.execute("SELECT current_stage FROM forge_projects WHERE project_key='substrate_node_buildout' LIMIT 1").fetchone()
                    if r and r[0]:
                        stage = str(r[0]).upper()[:32]
                if "substrate_copytrade_signals" in tables:
                    signal_count = int(_c.execute("SELECT COUNT(*) FROM substrate_copytrade_signals").fetchone()[0] or 0)
                    try:
                        new_signal_count = int(_c.execute("SELECT COUNT(*) FROM substrate_copytrade_signals WHERE state IN ('NEW','READY','OBSERVE')").fetchone()[0] or 0)
                    except Exception:
                        new_signal_count = 0
            except Exception:
                pass
        cash = _snt_float(cfg.get("SUBSTRATE_PAPER_CASH_USD"), _snt_float(cfg.get("SUBSTRATE_PAPER_BALANCE_USD"), 0))
        start = _snt_float(cfg.get("SUBSTRATE_PAPER_BALANCE_USD"), cash)
        reserved = _snt_float(cfg.get("SUBSTRATE_PAPER_RESERVED_USD"), 0)
        pos = _snt_float(cfg.get("SUBSTRATE_POSITION_SIZE_USD"), 0)
        live_on = str(cfg.get("SUBSTRATE_LIVE_ENABLED", "0")).lower() in ("1","true","yes","on")
        live_armed = str(cfg.get("SUBSTRATE_LIVE_ARMED", "0")).lower() in ("1","true","yes","on")
        live_provider = str(cfg.get("SUBSTRATE_LIVE_PROVIDER", "wallet") or "wallet")
        influence = str(cfg.get("SUBSTRATE_COPYTRADE_PAPER_INFLUENCE", "0")).lower() in ("1","true","yes","on")
        demo = str(cfg.get("SUBSTRATE_COPYTRADE_DEMO_MODE", "0")).lower() in ("1","true","yes","on")
        mode = "LIVE MANUAL-SIGN" if (live_on and live_armed) else ("LIVE CONFIGURED" if live_on else "PAPER ONLY")
        src = "DEMO ONLY" if demo else ("PAPER INFLUENCE" if influence else "AWAITING REAL WALLET SOURCE")
        roi = ((cash + reserved - start) / start * 100.0) if start else 0.0
        st.markdown(
            f"<div style='margin:10px 0 14px;padding:12px 14px;border:1px solid #9945FF55;border-radius:14px;background:linear-gradient(90deg,rgba(153,69,255,.16),rgba(5,2,16,.70));'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:10px;'>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:.74rem;letter-spacing:3px;color:#C7A6FF;'>SUBSTRATE NODE</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#8EF9FF;'>stage {_html.escape(stage)} · {_html.escape(src)}</span></div>"
            f"<div style='display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;'>"
            f"<div style='border:1px solid #9945FF44;border-radius:10px;padding:9px;background:#9945FF10;'><div style='font-size:0.66rem;letter-spacing:2px;color:#BFA2FF;'>BANKROLL</div><div style='font-family:Orbitron,sans-serif;font-size:1.25rem;color:#FFD700;font-weight:800;'>${cash:,.2f}</div><div style='font-size:0.66rem;color:#8EF9FF;'>start ${start:,.2f} · ROI {roi:+.1f}%</div></div>"
            f"<div style='border:1px solid #9945FF44;border-radius:10px;padding:9px;background:#9945FF10;'><div style='font-size:0.66rem;letter-spacing:2px;color:#BFA2FF;'>POSITION</div><div style='font-family:Orbitron,sans-serif;font-size:1.25rem;color:#C7A6FF;font-weight:800;'>${pos:,.2f}</div><div style='font-size:0.66rem;color:#8EF9FF;'>reserved ${reserved:,.2f}</div></div>"
            f"<div style='border:1px solid #9945FF44;border-radius:10px;padding:9px;background:#9945FF10;'><div style='font-size:0.66rem;letter-spacing:2px;color:#BFA2FF;'>NODES</div><div style='font-family:Orbitron,sans-serif;font-size:1.25rem;color:#C7A6FF;font-weight:800;'>{ready_count}/{node_count}</div><div style='font-size:0.66rem;color:#8EF9FF;'>ready / total</div></div>"
            f"<div style='border:1px solid #9945FF44;border-radius:10px;padding:9px;background:#9945FF10;'><div style='font-size:0.66rem;letter-spacing:2px;color:#BFA2FF;'>COPY SIGNALS</div><div style='font-family:Orbitron,sans-serif;font-size:1.25rem;color:#C7A6FF;font-weight:800;'>{new_signal_count}/{signal_count}</div><div style='font-size:0.66rem;color:#8EF9FF;'>{_html.escape(mode)} · {_html.escape(live_provider)}</div></div>"
            f"</div></div>",
            unsafe_allow_html=True,
        )
    except Exception as _sub_e:
        try:
            st.caption(f"Substrate Node section unavailable: {_sub_e}")
        except Exception:
            pass



def _sentinuity_live_wallet_truth_from_db(fallback=0.0):
    """Hotfix guard: Live Wallet card must never read paper equity or SOL oracle."""
    try:
        import sqlite3
        from pathlib import Path
        dbp = Path(globals().get("DB_PATH", "sentinuity_matrix.db"))
        if not dbp.exists():
            dbp = Path.cwd() / "sentinuity_matrix.db"
        con = sqlite3.connect(str(dbp))
        cur = con.cursor()

        # Canonical live-wallet keys first.
        for key in (
            "SOLANA_LIVE_WALLET_USD",
            "LIVE_WALLET_USD",
            "LIVE_WALLET_BALANCE_USD",
            "REAL_LIVE_WALLET_USD",
        ):
            row = cur.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
            if row and row[0] not in (None, ""):
                val = float(row[0])
                if val >= 0:
                    con.close()
                    return val

        # Real live fallback.
        row = cur.execute("SELECT wallet_balance FROM system_state WHERE id=1").fetchone()
        if row and row[0] is not None:
            val = float(row[0])
            if val >= 0:
                con.close()
                return val

        # Legacy fallback only after real/canonical paths.
        for key in ("LAST_REAL_WALLET_USD", "WALLET_BALANCE_USD"):
            row = cur.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
            if row and row[0] not in (None, ""):
                val = float(row[0])
                if val >= 0:
                    con.close()
                    return val

        con.close()
    except Exception:
        pass

    try:
        return float(fallback or 0.0)
    except Exception:
        return 0.0


def render_solana_capsule(bt, sol_price=0.0, sol_chg=0.0) -> None:
    """SOLANA capsule - signed-off crystalline glass style.

    Visual-only: keeps the same balance truth reads; only normalises typography,
    glass material, help affordance, and canonical gold/red/cyan hierarchy.
    """
    try:
        GOLD = C_GOLD
        CYAN_SUB = C_CYAN

        def _g(name, default=0.0):
            try:
                v = getattr(bt, name, None)
                return float(v) if v is not None else float(default)
            except Exception:
                return float(default)

        paper_eq    = _g("paper_equity")
        paper_start = _g("paper_start", paper_eq)
        paper_cash  = _g("paper_cash")
        paper_roi   = _g("paper_roi_pct")
        cash_roi    = _g("paper_cash_roi_pct")
        live_wallet = _sentinuity_live_wallet_truth_from_db(_g("live_wallet", _g("live_equity", 0.0)))
        live_avail = live_wallet

        try:
            _sp = float(sol_price or 0.0)
        except Exception:
            _sp = 0.0
        try:
            _sc = float(sol_chg or 0.0)
        except Exception:
            _sc = 0.0

        # SIGNOFF_GOLD_DOCTRINE_20260715: cumulative wallet figures (equity,
        # cash, cumulative realized PnL — e.g. the $1,298.11 wallet realized)
        # are ACCOUNT TRUTH, not one verified trade. Doctrine: gold is reserved
        # for earned/verified apex only, so these tiles render truth cyan.
        def tile(label, big, sub, big_color=CYAN_SUB):
            return (
                f"<div class='snty-metric-card'>"
                f"<div class='snty-label'>{label}</div>"
                f"<div class='snty-stat-value' style='color:{big_color};'>{big}</div>"
                f"<div class='snty-sub'>{sub}</div>"
                f"</div>"
            )

        grid = (
            tile("PAPER EQUITY", f"${paper_eq:,.2f}",
                 f"start ${paper_start:,.2f} · ROI {paper_roi:+.1f}%")
            + tile("PAPER CASH", f"${paper_cash:,.2f}", f"ROI {cash_roi:+.1f}%")
            + tile("LIVE WALLET", f"${live_wallet:,.2f}", f"available ${live_avail:,.2f}")
            + tile("SOL ORACLE", f"${_sp:,.2f}", f"{_sc:+.1f}% 24h")
        )

        st.markdown(
            f"""<div class='snty-crystal-panel snty-gold-panel' style='margin:10px 0 14px;padding:13px 14px;'>
              <div class='snty-title-row'>
                <div class='snty-title-left'>
                  <span class='snty-section-title gold'>SOLANA</span>
                  <span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1.5px;color:var(--snty-muted);margin-left:8px;'>CUMULATIVE WALLET TRUTH · NOT A SINGLE TRADE</span>
                  <details class='snty-helpbox'><summary>?</summary><div class='snty-help-pop'>Solana lane wallet truth: paper equity, paper cash, live wallet visibility, and current SOL oracle price. Visual only - no trading gates changed.</div></details>
                </div>
                <span class='snty-section-kicker' style='color:{CYAN_SUB};'>SOL ${_sp:,.2f} · {_sc:+.1f}% 24h</span>
              </div>
              <div class='snty-metric-grid'>{grid}</div>
            </div>""",
            unsafe_allow_html=True,
        )
    except Exception as _sol_e:
        try:
            st.caption(f"Solana section unavailable: {_sol_e}")
        except Exception:
            pass


def _fetch_runner_panel_rows(_db_path=None):
    rows = []
    try:
        import time as _time
        now = _time.time()
        with _snt_ro_conn(_db_path) as _c:
            tables = {r[0] for r in _c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            if "paper_positions" not in tables:
                return []
            cols = {r[1] for r in _c.execute("PRAGMA table_info(paper_positions)").fetchall()}
            wanted = ["id","token_name","mint_address","status","opened_at","closed_at","entry_price","exit_price","last_price","highest_price_seen","realized_pnl_usd","unrealized_pnl_usd","position_size_usd","pnl_pct","final_exec_pct","live_exec_pct","exit_pct","exit_reason","exit_category","price_integrity_status","peak_pct","peak_seen_at","runner_trace_json","close_vs_peak_giveback_pct"]
            select_cols = [c for c in wanted if c in cols]
            if not select_cols:
                return []
            sql = f"SELECT {', '.join(select_cols)} FROM paper_positions WHERE status IN ('OPEN','CLOSED') ORDER BY COALESCE(closed_at, opened_at, 0) DESC LIMIT 180"
            _afterlife = {}
            if "trade_afterlife_metrics" in tables:
                try:
                    for _a in _c.execute(
                        "SELECT source_trade_id,max_price_after_close,"
                        "max_pct_after_close,observation_window_sec,complete "
                        "FROM trade_afterlife_metrics"
                    ).fetchall():
                        _afterlife[int(_a["source_trade_id"])] = dict(_a)
                except Exception:
                    _afterlife = {}

            for r in _c.execute(sql).fetchall():
                d = dict(r)
                status = str(d.get("status") or "").upper()
                pnl = _snt_float(d.get("realized_pnl_usd") if status == "CLOSED" else d.get("unrealized_pnl_usd"), 0)
                pct = None
                for k in ("exit_pct", "pnl_pct", "final_exec_pct", "live_exec_pct"):
                    if d.get(k) is not None:
                        pct = _snt_float(d.get(k), 0); break
                if pct is None:
                    ep = _snt_float(d.get("entry_price"), 0)
                    px = _snt_float(d.get("exit_price") if status == "CLOSED" else d.get("last_price"), 0)
                    if ep > 0 and px > 0:
                        pct = ((px - ep) / ep) * 100.0
                # WINS_LOSSES_TAPE_20260624: collect ALL with computed pnl/pct, then pick
                # both extremes (top wins + worst losses) + open runners below.
                d["_panel_pnl"] = pnl
                d["_panel_pct"] = pct
                d["_panel_age"] = now - _snt_float(
                    d.get("closed_at") or d.get("opened_at"), now
                )
                d["_panel_status"] = status

                # EXIT FLIGHT PATH truth:
                # use the furthest measured pre-exit high and post-exit high.
                # Never stop visually at +100 merely because peak_pct was absent
                # or an older cached field was capped.
                _entry = _snt_float(d.get("entry_price"), 0.0)
                _flight_candidates = []
                if d.get("peak_pct") is not None:
                    _flight_candidates.append(_snt_float(d.get("peak_pct"), 0.0))
                _high = _snt_float(d.get("highest_price_seen"), 0.0)
                if _entry > 0 and _high > 0:
                    _flight_candidates.append((_high / _entry - 1.0) * 100.0)
                if pct is not None:
                    _flight_candidates.append(float(pct))

                _af = _afterlife.get(int(d.get("id") or 0), {})
                _post_px = _snt_float(_af.get("max_price_after_close"), 0.0)
                if _entry > 0 and _post_px > 0:
                    _flight_candidates.append((_post_px / _entry - 1.0) * 100.0)
                _post_pct = _af.get("max_pct_after_close")
                # max_pct_after_close is relative to close, so retain it as a
                # label but calculate full flight from price/entry where possible.
                d["_flight_peak_pct"] = max(_flight_candidates) if _flight_candidates else None
                if _af:
                    _window = _snt_float(_af.get("observation_window_sec"), 0.0)
                    _done = bool(_af.get("complete"))
                    _post_label = (
                        f"afterlife {float(_post_pct):+.0f}% vs exit"
                        if _post_pct is not None else
                        f"afterlife {_window/60:.0f}m"
                    )
                    d["_afterlife_label"] = _post_label + (" ✓" if _done else "")
                rows.append(d)
    except Exception:
        return []
    # WINS_LOSSES_TAPE_20260624: from everything gathered, show both extremes + open runners.
    try:
        opens = [r for r in rows if r.get('_panel_status') == 'OPEN']
        closed = [r for r in rows if r.get('_panel_status') == 'CLOSED']
        wins = sorted([r for r in closed if _snt_float(r.get('_panel_pnl'), 0) > 0],
                      key=lambda r: -_snt_float(r.get('_panel_pnl'), 0))[:3]
        losses = sorted([r for r in closed if _snt_float(r.get('_panel_pnl'), 0) <= 0],
                        key=lambda r: _snt_float(r.get('_panel_pnl'), 0))[:3]
        open_runners = [r for r in opens
                        if (r.get('_panel_pct') is not None and float(r.get('_panel_pct')) >= 75.0)
                        or _snt_float(r.get('_panel_pnl'), 0) >= 20.0][:2]
        # newest-first within the chosen set, wins then losses interleaved by recency
        chosen = open_runners + wins + losses
        seen = set(); out = []
        for r in sorted(chosen, key=lambda r: -_snt_float(r.get('closed_at') or r.get('opened_at'), 0)):
            rid = r.get('id') or (r.get('mint_address'), r.get('opened_at'))
            if rid in seen:
                continue
            seen.add(rid); out.append(r)
        return out[:6] if out else rows[:6]
    except Exception:
        return rows[:6]


def render_runner_observability_panel(_db_path=None) -> None:
    """Gold pinned runner strip: open runners + recent closed runners, under meter/feed."""
    try:
        import html as _html
        rows = _fetch_runner_panel_rows(_db_path)
        if not rows:
            return
        cards = []
        _C = SENTINUITY_COLORS
        # RUN_ROW_20260624: compact peak->final rows. Faint violet bar = how far
        # it ran (peak); bright tick = where it closed (green if +, red if -).
        # Gold diamond only for a true runner (>=2x = +100%). Replaces the old
        # gold-washed mini-cards. Shared scale = max peak in the visible set.
        import math as _math
        _peaks = [
            float(d.get("_flight_peak_pct")) for d in rows[:5]
            if d.get("_flight_peak_pct") is not None
        ]
        _scale = max(
            [_math.log1p(max(0.0, p)) for p in _peaks] + [_math.log1p(100.0)]
        )
        for d in rows[:5]:
            status = str(d.get("status") or "?").upper()
            token = str(d.get("token_name") or d.get("mint_address") or "?")[:16]
            mint = str(d.get("mint_address") or "")[:10]
            pnl = _snt_float(d.get("_panel_pnl"), 0)
            pct = d.get("_panel_pct")
            peak = d.get("_flight_peak_pct")
            reason = str(d.get("exit_reason") or d.get("exit_category") or "needs instrumentation")[:40]
            _final = None if pct is None else float(pct)
            _peakv = None if peak is None else float(peak)
            _run_w = (
                0.0 if _peakv is None else
                max(0.0, min(100.0, _math.log1p(max(0.0, _peakv)) / _scale * 100.0))
            )
            _is_runner = (_final is not None and _final >= 100.0)
            _final_col = _C["gold"] if _is_runner else (_C["green"] if (_final or 0) > 0 else _C["red"])
            _final_txt = "-" if _final is None else f"{_final:+.1f}%"
            _peak_txt = "" if _peakv is None else f"peak {_peakv:+.0f}%"
            _ghost = str(d.get("_afterlife_label") or "")  # populated by afterlife join
            _crown = (f"<i style='color:{_C['gold']};font-style:normal;'>◇ </i>" if _is_runner else "")
            _open_dot = (f"<span style='color:{_C['cyan']};'>● </span>" if status == "OPEN" else "")
            # final-result tick position along the same shared scale
            _tick_pos = (
                0.0 if (_final is None or _final <= 0) else
                min(100.0, _math.log1p(max(0.0, _final)) / _scale * 100.0)
            )
            cards.append(
                f"<div style='display:grid;grid-template-columns:118px 1fr 70px;"
                f"align-items:center;gap:9px;padding:7px 4px;"
                f"border-bottom:0.5px solid rgba(255,255,255,.06);'>"
                f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
                f"color:#cfe;'>{_open_dot}{_crown}{_html.escape(token)}</span>"
                f"<div style='position:relative;height:16px;' title='{_html.escape(reason)}'>"
                f"<div style='position:absolute;left:0;top:6px;height:4px;width:{_run_w:.1f}%;"
                f"background:{_C['purple']}73;border-radius:2px;'></div>"
                f"<div style='position:absolute;left:{_tick_pos:.1f}%;top:2px;width:3px;"
                f"height:12px;background:{_final_col};box-shadow:0 0 5px {_final_col};'></div>"
                f"<span style='position:absolute;right:0;top:1px;font-size:0.66rem;"
                f"color:#9DB5A8;'>{_html.escape(_peak_txt)}</span>"
                f"{('<span style=' + chr(39) + 'position:absolute;right:0;top:9px;font-size:0.66rem;color:' + _C['cyan'] + ';' + chr(39) + '>' + _html.escape(_ghost) + '</span>') if _ghost else ''}"
                f"</div>"
                f"<span style='font-family:Orbitron,sans-serif;font-size:.66rem;"
                f"font-weight:700;color:{_final_col};text-align:right;'>{_final_txt}</span>"
                f"</div>"
            )
        st.markdown(
            "<div style='margin:8px 0 10px;padding:10px 12px;border:0.5px solid rgba(153,69,255,.30);"
            "border-radius:12px;background:rgba(6,4,16,.55);'>"
            "<div style='display:flex;justify-content:space-between;font-family:Share Tech Mono,monospace;"
            "font-size:0.66rem;letter-spacing:3px;color:#8EF9FF;margin-bottom:6px;'>"
            "<span>BIGGEST WINS &amp; LOSSES - peak → final</span>"
            "<span style='color:#9945FF;'>violet = how far it ran</span></div>"
            + "".join(cards) + "</div>",
            unsafe_allow_html=True,
        )
    except Exception as _rp_e:
        try:
            st.caption(f"Runner panel unavailable: {_rp_e}")
        except Exception:
            pass
# END SENTINUITY_RUNNER_SUBSTRATE_UI_20260621

def render_living_trade_meter(row: dict) -> None:
    """
    EVOLVED PRESSURE GAUGE - centre equilibrium, SL bleeds left (red),
    TP expands right (green), price breathes at centre. Organism stress visible.
    """
    import time as _mt
    try:
        _pct     = float(row.get("pnl_pct") or 0.0)
        _pnl     = float(row.get("pnl") or 0.0)
        _badge   = str(row.get("src_badge","STALE"))
        _token   = str(row.get("token","?"))[:14]
        _age     = str(row.get("age_str","?"))
        _fresh   = row.get("is_fresh", False)
        _tp_pct  = float(row.get("take_profit_pct") or 25.0)  # ALIGN: TAKE_PROFIT_PCT seed=25
        _sl_pct  = float(row.get("stop_loss_pct") or 4.0)  # ALIGN: STOP_LOSS_PCT/HARD_STOP=4 (hard floor)

        # Normalise: centre = 0%, full left = -SL%, full right = +TP%
        _range   = _tp_pct + _sl_pct  # total range
        _centre  = _sl_pct / _range * 100  # centre line position %
        _price_pos = (_pct + _sl_pct) / _range * 100  # 0-100
        _price_pos = max(1, min(99, _price_pos))

        # Colour logic
        # RUNNER_GOLD_20260621: a true runner (>= target + 75% of target, i.e. well
        # past TP) turns GOLD, distinct from an ordinary "approaching target" green.
        # Previously a +1270% runner stayed green because it only checked
        # _pct > _tp_pct*0.7 - every winner looked the same. Gold marks the runners.
        _runner_threshold = _tp_pct * 1.75  # target + 75% beyond it
        _is_runner = _pct >= _runner_threshold
        if _is_runner:
            _state = "RUNNER"; _state_col = "#FFD700"
        elif _pct > _tp_pct * 0.7:
            _state = "APPROACHING TARGET"; _state_col = "#14F195"
        elif _pct < -_sl_pct * 0.7:
            _state = "CRITICAL LOSS"; _state_col = "#FF073A"
        elif abs(_pct) < 2:
            _state = "EQUILIBRIUM"; _state_col = "#FFD700"
        else:
            _state = "BUILDING"; _state_col = "#8EF9FF"
        # the tp-fill bar uses runner gold when running, else green
        _tp_fill_col = "#FFD700" if _is_runner else "#14F195"

        _badge_col = "#14F195" if _badge=="LIVE" else ("#FF073A" if "STALE" in _badge else "#FFD700")
        _pnl_col   = "#14F195" if _pnl >= 0 else "#FF073A"
        _pulse_anim= "animation:meterPulse 1.8s ease-in-out infinite;" if _fresh else ""

        # Build gauge
        _sl_fill  = max(0, min(_centre, _centre - _price_pos)) if _price_pos < _centre else 0
        _tp_fill  = max(0, _price_pos - _centre) if _price_pos > _centre else 0
        _tp_col = "#FFD700" if _state == "RUNNER" else "#14F195"

        # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

        # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

        try:

            _runner_gold_pct = 75.0

            try:

                if isinstance(locals().get("row"), dict):

                    _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

            except Exception:

                _runner_gold_pct = 75.0

            if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

                _state = "RUNNER"

                _state_col = "#FFD700"

        except Exception:

            pass


        _html = f"""
<style>
@keyframes meterPulse{{0%,100%{{opacity:.85}}50%{{opacity:1}}}}
@keyframes centrePulse{{0%,100%{{box-shadow:0 0 4px #FFD700}}50%{{box-shadow:0 0 12px #FFD700}}}}
</style>
<div style="padding:8px 12px 10px;border-left:2px solid {_state_col};
  background:rgba(5,2,16,0.6);margin-bottom:6px;border-radius:0 6px 6px 0;{_pulse_anim}">
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;
    font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;">
    <span style="color:#8EF9FF;">{_token}</span>
    <span style="color:{_state_col};letter-spacing:2px;">{_state}</span>
    <span style="color:{_badge_col};font-size:0.66rem;">[{_badge} {_age}]</span>
  </div>
  <!-- PRESSURE GAUGE BAR -->
  <div style="position:relative;height:16px;border-radius:8px;
    background:linear-gradient(90deg,#FF073A22 0%,#1a0a0a 40%,#0a1a0a 60%,#14F19522 100%);
    border:1px solid rgba(255,255,255,0.08);overflow:visible;">
    <!-- SL pressure bleed (left red fill) -->
    <div style="position:absolute;left:0;top:0;height:100%;border-radius:8px 0 0 8px;
      width:{min(_centre,100-_price_pos+_centre) if _price_pos<_centre else 0:.1f}%;
      background:linear-gradient(90deg,#FF073A,#FF073A44);opacity:.8;"></div>
    <!-- TP expansion (right green fill) -->
    <div style="position:absolute;left:{_centre:.1f}%;top:0;height:100%;
      width:{max(0,_price_pos-_centre):.1f}%;
      background:linear-gradient(90deg,{_tp_fill_col}44,{_tp_fill_col});opacity:.85;"></div>
    <!-- Centre equilibrium line -->
    <div style="position:absolute;top:-2px;left:{_centre:.1f}%;width:2px;height:20px;
      background:#FFD700;animation:centrePulse 2s ease-in-out infinite;
      transform:translateX(-50%);border-radius:1px;"></div>
    <!-- Price cursor -->
    <div style="position:absolute;top:-3px;left:{_price_pos:.1f}%;width:10px;height:22px;
      background:{_state_col};border-radius:3px;transform:translateX(-50%);
      box-shadow:0 0 8px {_state_col};transition:left .5s ease;"></div>
    <!-- SL label -->
    <div style="position:absolute;left:4px;top:50%;transform:translateY(-50%);
      font-family:Share Tech Mono;font-size:0.66rem;color:#FF073A99;">SL</div>
    <!-- TP label -->
    <div style="position:absolute;right:4px;top:50%;transform:translateY(-50%);
      font-family:Share Tech Mono;font-size:0.66rem;color:{_tp_col}99;">TP</div>
  </div>
  <!-- PnL readout beneath gauge -->
  <div style="display:flex;justify-content:space-between;margin-top:4px;
    font-family:Share Tech Mono,monospace;font-size:0.66rem;">
    <span style="color:#555;">-{_sl_pct:.0f}%</span>
    <span style="color:{_pnl_col};font-weight:700;">{_pct:+.2f}%&nbsp;&nbsp;${_pnl:+.3f}</span>
    <span style="color:#555;">+{_tp_pct:.0f}%</span>
  </div>
</div>"""
        st.markdown(_html, unsafe_allow_html=True)
    except Exception as _me:
        st.markdown(f"<div style='color:#FF073A;font-size:0.66rem;'>METER ERR: {html.escape(str(_me)[:40])}</div>", unsafe_allow_html=True)


def truth_lens_modal(row):
    import time as _time
    import sqlite3 as _sqlite3

    # FORCE LIVE READ - bypass 12s cache on dashboard data
    # Truth Lens must show current DB state not stale snapshot
    _pos_id = row.get("id")
    if _pos_id:
        try:
            _lconn = _sqlite3.connect(str(DB_PATH), timeout=2.0)
            _lconn.row_factory = _sqlite3.Row
            _fresh = _lconn.execute(
                "SELECT * FROM paper_positions WHERE id=? LIMIT 1",
                (_pos_id,)
            ).fetchone()
            _lconn.close()
            if _fresh:
                row = dict(_fresh)
        except Exception:
            pass  # fall through to cached row if DB read fails

    mint      = str(row.get("mint_address", ""))
    token     = str(row.get("token_name", ""))
    entry     = float(row.get("entry_price", 0) or 0)
    size      = float(row.get("position_size_usd", 0) or 0)
    status    = str(row.get("status", "CLOSED")).upper()

    # ── Prefer execution-aligned live_exec_* columns (written by execution engine) ──
    def _sf(v):
        try:
            if v is None: return None
            f = float(v)
            return None if f != f else f
        except Exception:
            return None
    live_exec_price   = _sf(row.get("live_exec_price"))
    live_exec_pct     = _sf(row.get("live_exec_pct"))
    live_exec_band    = row.get("live_exec_band") or "UNKNOWN"
    live_exec_src     = str(row.get("live_exec_source") or "execution-engine")
    live_exec_updated = _sf(row.get("live_exec_updated_at"))

    # Determine data source and freshness
    using_exec_data = (
        status == 'OPEN'
        and live_exec_price is not None
        and live_exec_pct is not None
        and float(live_exec_price) > 0
    )

    if using_exec_data:
        current   = float(live_exec_price)
        pct_moved = float(live_exec_pct)
        pnl       = size * (pct_moved / 100.0) if size > 0 else 0.0
        raw_delta = current - entry if entry > 0 else 0.0
        exec_age  = int(_time.time() - float(live_exec_updated)) if live_exec_updated else 9999
        src_label = {
            "mtm": "EXEC-MTM", "intel-mtm": "EXEC-INTEL",
            "unscoped": "EXEC-SNAP", "dex-stale": "EXEC-DEX",
            "engine-fallback": "EXEC-FALLBACK",
        }.get(live_exec_src, "EXEC")
        data_source = f"{src_label} ({exec_age}s)"
        if exec_age < 10:
            freshness = "LIVE"
            age_col   = C_GREEN
        elif exec_age < 30:
            freshness = "LIVE"
            age_col   = C_GREEN
        elif exec_age < 60:
            freshness = "STALE"
            age_col   = C_GOLD
        else:
            # Gemini fix: oracle dark >60s → treat as DEAD → show ?? not fake %
            freshness = "DEAD"
            age_col   = C_RED
        age_str = f"{exec_age}s"
    else:
        # Fallback to oracle MTM for display
        mtm       = _latest_mtm_for_mint(mint)
        current   = mtm["price"]
        pct_moved = ((current - entry) / entry * 100.0) if entry > 0 and current > 0 else 0.0
        pnl       = size * (pct_moved / 100.0) if size > 0 else 0.0
        raw_delta = (current - entry) if entry > 0 and current > 0 else 0.0
        age_str   = f"{mtm['age_seconds']}s" if mtm["age_seconds"] is not None else "NO DATA"
        age_col   = C_GREEN if mtm["is_fresh"] else (C_GOLD if mtm["age_seconds"] and mtm["age_seconds"] < 120 else C_RED)
        data_source = f"oracle-mtm"
        live_exec_band = "UNKNOWN"
        if status != "OPEN":
            freshness = "HISTORICAL"
            age_col   = "#888888"
            age_str   = f"CLOSED - {age_str}"
        elif mtm["is_fresh"]:
            freshness = "LIVE"
        elif mtm["age_seconds"] and mtm["age_seconds"] < 120:
            freshness = "STALE"
        else:
            freshness = "DEAD"
            age_col = C_RED

    pcol = C_GREEN if pnl >= 0 else C_RED

    # Band colour
    band_col = {
        "TAKE_PROFIT_READY":   C_GREEN,
        "TAKE_PROFIT_ARMING":  C_GOLD,
        "STOP_LOSS_READY":     C_RED,
        "STOP_LOSS_ARMING":    "#FF6B35",
        "FLAT":                "#888888",
        "LIVE":                C_PURPLE,
        "HISTORICAL":          "#888888",
    }.get(live_exec_band, C_PURPLE)
    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
    try:
        _runner_gold_pct = 75.0
        try:
            if isinstance(locals().get("row"), dict):
                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
        except Exception:
            _runner_gold_pct = 75.0
        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
            _state = "RUNNER"
            _state_col = "#FFD700"
    except Exception:
        pass

    st.markdown(f"""<div style="font-family:'VT323',monospace;padding:10px;">
        <h2 style="color:{C_PURPLE};">{token}</h2>
        <code style="color:{C_GOLD};background:rgba(0,0,0,0.5);padding:5px;">MINT: {mint}</code>
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:12px;margin-top:20px;">
            <div style="border:1px solid {C_GREEN}55;padding:12px;border-radius:8px;text-align:center;">
                ENTRY<br><span style="color:{C_GREEN};font-size:0.75rem;">${entry:.10f}</span>
            </div>
            <div style="border:1px solid {C_GOLD}55;padding:12px;border-radius:8px;text-align:center;">
                EXEC PRICE<br><span style="color:{C_GOLD};font-size:0.75rem;">${current:.10f}</span>
            </div>
            <div style="border:1px solid {age_col}55;padding:12px;border-radius:8px;text-align:center;">
                STATUS [{freshness}]<br><span style="color:{age_col};font-size:0.82rem;">{age_str}</span>
                <div style="font-size:0.66rem;color:#888;margin-top:2px;">{data_source}</div>
            </div>
        </div>
        <div style="margin-top:15px;padding:24px;background:{pcol}22;border:2px solid {pcol};border-radius:12px;text-align:center;">
            <div style="font-size:0.85rem;opacity:0.8;letter-spacing:2px;">EXECUTION MOVE</div>
            <div style="font-size:3rem;font-weight:bold;color:{pcol};line-height:1.1;">{pct_moved:+.2f}%</div>
            <div style="font-size:1.6rem;color:{pcol};margin-top:4px;">${pnl:+.2f} USD</div>
            <div style="margin-top:10px;padding:6px 12px;background:{band_col}33;border:1px solid {band_col};border-radius:6px;display:inline-block;">
                <span style="color:{band_col};font-size:0.8rem;letter-spacing:2px;">{live_exec_band}</span>
            </div>
            <div style="font-size:0.66rem;color:#888;margin-top:8px;opacity:0.7;">RAW DELTA: {raw_delta:+.10f}</div>
        </div>
    </div>""", unsafe_allow_html=True)

    # Living trade meter - visual interpolation layer, no DB writes
    render_living_trade_meter(row)

def compile_truth_stream(proposals_df: pd.DataFrame, debate_df: pd.DataFrame) -> list[dict]:
    if proposals_df.empty: return []
    bundles, debate_dict = [], {}
    if not debate_df.empty and "proposal_id" in debate_df.columns:
        for _, d in debate_df.iterrows():
            pid = d.get("proposal_id")
            if pid is None or pid == "" or (isinstance(pid, float) and pid != pid): pid = d.get("id")
            if pid is None or pid == "": continue
            pid = int(pid)
            if pid not in debate_dict: debate_dict[pid] = []
            debate_dict[pid].append({"speaker": str(d.get("speaker", "SYSTEM")).upper(), "text": str(d.get("verdict_text", d.get("message", ""))), "ts": d.get("logged_at", d.get("created_at", 0))})
    for _, prop in proposals_df.iterrows():
        pid = int(prop.get("id", 0))
        bundles.append({
            "type": "proposal", "id": pid, "status": str(prop.get("status", "")).lower(),
            "proposal_type": html.escape(str(prop.get("proposal_type", "UNKNOWN")).upper()),
            "genesis_text": html.escape(str(prop.get("suggested_action", prop.get("proposal_text", "")))),
            "created_at": float(prop.get("created_at", 0)),
            "unified_diff": html.escape(str(prop.get("unified_diff", prop.get("rewritten_code", "")))),
            "debates": debate_dict.get(pid, [])
        })
    bundles.sort(key=lambda x: x["created_at"], reverse=True)
    return bundles

def get_arena_lock_css(active: bool = False) -> str:
    if not active: return "<style>@keyframes goldPulse { from { box-shadow: 0 0 80px #FFD700, 0 0 140px #FFD70088; } to { box-shadow: 0 0 120px #FFD700, 0 0 200px #FFD700cc; } }</style>"
    return "<style>@keyframes goldPulse { from { box-shadow: 0 0 80px #FFD700, 0 0 140px #FFD70088; } to { box-shadow: 0 0 120px #FFD700, 0 0 200px #FFD700cc; } } .stApp .panel, .lore-card, [data-testid='stMetric'] { opacity: 0.25 !important; filter: brightness(0.6) !important; transition: opacity 1.2s ease; pointer-events: none; }</style>"


def render_motor_output_command_deck(*args, **kwargs):
    """
    INLINE BUY/SELL FEED - restored compact Sentinuity terminal style.
    Renderer is self-contained in this file. No external delegate needed.
    Read-only display. No execution logic touched.
    """
    _render_inline_buy_sell_feed(DB_PATH)


def assert_no_motor_bypass() -> str:
    """Integrity probe retained for API compatibility. Always returns OK -
    renderer is now inline and the external-delegate architecture is retired."""
    return "OK"


# ── CSS for the inline BUY/SELL feed (injected once per session) ─────────────
_FEED_CSS_INJECTED = False

_FEED_CSS = """
<style>
.sntFeedWrap{background:rgba(5,2,16,.96);border:1px solid rgba(153,69,255,.22);
border-radius:8px;padding:6px 8px;margin-bottom:14px;
box-shadow:0 0 12px rgba(153,69,255,.06);}
.sntFeedHdr{font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;
color:#9945FF;border-bottom:1px solid rgba(153,69,255,.12);
padding-bottom:5px;margin-bottom:6px;
display:flex;justify-content:space-between;align-items:center;}
.sntFeedLegal{font-size:0.66rem;color:#14F195;letter-spacing:2px;
border:1px solid rgba(20,241,149,.3);border-radius:999px;
padding:1px 7px;background:rgba(20,241,149,.05);}
/* === MANDATORY STRUCTURE: 3-child outer grid ============================ */
.sntRow{display:grid;grid-template-columns:5px 1fr auto;gap:8px;align-items:center;
padding:3px 4px;border-bottom:1px solid rgba(255,255,255,.025);}
.sntRow:last-child{border-bottom:none;}
/* === Thermal strip - left edge accent, true triage signal =============== */
.sntAccent{align-self:stretch;border-radius:2px;min-height:18px;}
/* === Main content block - dense inner layout =========================== */
.sntMain{min-width:0;}
.sntLine1{display:flex;align-items:center;gap:7px;font-family:Share Tech Mono,monospace;
font-size:0.66rem;line-height:1.15;white-space:nowrap;}
.sntPct{font-variant-numeric:tabular-nums;font-weight:700;min-width:52px;
text-align:left;flex-shrink:0;}
.sntPctPos{color:#14F195;}
.sntPctNeg{color:#FF073A;}
.sntPctNeu{color:rgba(255,255,255,.28);}
.sntPctGold{color:#FFD700;text-shadow:0 0 6px rgba(255,215,0,.4);}
.sntSide{font-size:0.66rem;letter-spacing:1px;padding:1px 5px;border-radius:3px;
font-weight:700;flex-shrink:0;font-variant-numeric:tabular-nums;}
.sntSideBuy{color:#14F195;border:1px solid rgba(20,241,149,.35);background:rgba(20,241,149,.05);}
.sntSideSell{color:#FF073A;border:1px solid rgba(255,7,58,.3);background:rgba(255,7,58,.05);}
.sntName{color:rgba(255,255,255,.92);overflow:hidden;text-overflow:ellipsis;
white-space:nowrap;flex:1;min-width:0;letter-spacing:.5px;}
.sntTicker{color:rgba(142,249,255,.65);margin-left:4px;font-size:0.66rem;}
.sntPnlUsd{font-variant-numeric:tabular-nums;flex-shrink:0;font-size:0.66rem;}
.sntMeta{font-family:Share Tech Mono,monospace;font-size:0.66rem;
color:rgba(255,255,255,.28);margin-top:1px;display:flex;flex-wrap:nowrap;gap:6px;
font-variant-numeric:tabular-nums;line-height:1.3;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
.sntMeta span{display:inline-block;}
.sntHourBadge{font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:.5px;
border-radius:3px;padding:0 4px;border:1px solid;white-space:nowrap;
margin-left:3px;flex-shrink:0;}
.sntHourGold{color:#FFD700;border-color:rgba(255,215,0,.45);background:rgba(255,215,0,.06);}
.sntHourGreen{color:#14F195;border-color:rgba(20,241,149,.3);background:rgba(20,241,149,.04);}
.sntHourAmber{color:#FF9500;border-color:rgba(255,149,0,.3);background:rgba(255,149,0,.04);}
.sntHourRed{color:rgba(255,7,58,.65);border-color:rgba(255,7,58,.22);background:rgba(255,7,58,.03);}
.sntHourNeutral{color:rgba(255,255,255,.22);border-color:rgba(255,255,255,.06);background:transparent;}
/* === Age column ======================================================== */
.sntAge{font-family:Share Tech Mono,monospace;font-size:0.66rem;
color:rgba(255,255,255,.22);text-align:right;white-space:nowrap;
font-variant-numeric:tabular-nums;flex-shrink:0;line-height:1.2;}
/* === Pagination controls =============================================== */
.sntPager{display:flex;justify-content:space-between;align-items:center;
padding-top:6px;margin-top:4px;border-top:1px solid rgba(153,69,255,.08);
font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;
color:rgba(153,69,255,.5);}
.sntEmpty{font-family:Share Tech Mono,monospace;font-size:0.66rem;
color:rgba(153,69,255,.32);letter-spacing:3px;text-align:center;padding:16px 0;}
</style>
"""


def _inject_feed_css() -> None:
    global _FEED_CSS_INJECTED
    if _FEED_CSS_INJECTED:
        return
    try:
        st.markdown(_FEED_CSS, unsafe_allow_html=True)
        _FEED_CSS_INJECTED = True
    except Exception:
        pass


def _feed_safe(val, maxlen: int = 80, fallback: str = "\u2014") -> str:
    import re as _re
    if val is None:
        return fallback
    s = str(val).strip()
    if not s or s.lower() in ("none", "nan", "null"):
        return fallback
    s = _re.sub(r"<[^>]+>", "", s)
    return html.escape(s[:maxlen])


def _feed_ts(epoch) -> str:
    try:
        import datetime as _dt
        return _dt.datetime.fromtimestamp(float(epoch)).strftime("%m/%d %H:%M")
    except Exception:
        return "\u2014"


def _feed_age(epoch, now: float) -> str:
    try:
        s = now - float(epoch)
        if s < 0: return "\u2014"
        if s < 60: return f"{int(s)}s"
        if s < 3600: return f"{int(s/60)}m"
        return f"{s/3600:.1f}h"
    except Exception:
        return "\u2014"


def _feed_meter_pct(pnl_pct: float) -> int:
    try:
        return max(2, min(100, int(abs(float(pnl_pct)) / 50 * 100)))
    except Exception:
        return 0


def _feed_meter_color(pnl_usd: float, pnl_pct: float) -> str:
    if pnl_pct >= 75: return "#FFD700"
    if pnl_usd > 0:   return "#14F195"
    if pnl_usd < 0:   return "#FF073A"
    return "rgba(255,255,255,0.2)"


def _feed_short_mint(mint: str, n: int = 16) -> str:
    if not mint or len(mint) <= n:
        return mint or "\u2014"
    return mint[:8] + "\u2026" + mint[-6:]


def _feed_pill(text: str, color: str) -> str:
    return (f"<span class='sntPill' style='color:{color};border-color:{color}44;"
            f"background:{color}0A;'>{html.escape(str(text))}</span>")


def _feed_status_badge(status: str) -> str:
    s = (status or "").upper()
    col = {"ACTIVE":"#8EF9FF","OPEN":"#8EF9FF","ENTRY":"#9945FF","EXIT":"#FFD700",
           "SOLD":"#FFD700","CLOSED":"rgba(255,255,255,.3)","VETOED":"#FF073A",
           "HOLD":"#FFD700","SCRATCH":"rgba(255,255,255,.25)"}.get(s,"rgba(255,255,255,.3)")
    return (f"<span class='sntStatusBadge' style='color:{col};border-color:{col}44;"
            f"background:{col}0A;'>{s or '?'}</span>")


def _feed_links(mint: str) -> str:
    if not mint or len(mint) < 8:
        return ""
    return (f"<div class='sntLinks'>"
            f"<a href='https://dexscreener.com/solana/{mint}' target='_blank'>DEX</a>"
            f"<a href='https://pump.fun/{mint}' target='_blank'>PUMP</a>"
            f"<a href='https://birdeye.so/token/{mint}?chain=solana' target='_blank'>BIRD</a>"
            f"<a href='https://jup.ag/swap/SOL-{mint}' target='_blank'>JUP</a>"
            f"</div>")


def _feed_hour_class(ts: float, golden_hours_aest: set, block_hours_utc: set,
                     reduce_hours_utc: set) -> tuple:
    import datetime as _dt
    try:
        utc_hour  = _dt.datetime.utcfromtimestamp(ts).hour
        aest_hour = (utc_hour + 10) % 24
        day_abbr  = _dt.datetime.utcfromtimestamp(ts).strftime("%a").upper()[:2]
        label = f"{day_abbr} {aest_hour:02d}h"
        if aest_hour in golden_hours_aest: return label, "sntHourGold"
        if utc_hour  in block_hours_utc:   return label, "sntHourRed"
        if utc_hour  in reduce_hours_utc:  return label, "sntHourAmber"
        return label, "sntHourGreen"
    except Exception:
        return "", "sntHourNeutral"


def _fetch_feed_rows_inline(db_path) -> list:
    """
    Fetch and merge open positions + closed position truth + recent execution events.

    Sign-off goals:
      - No fake rows.
      - No hardcoded PAPER mode.
      - LIVE/REAL wallet rows display as LIVE when funding/execution fields prove it.
      - SELL rows show true realized PnL and actual exit cashflow, not just stake.
      - Closed paper_positions are read directly so audited PnL windows can populate pages
        even if paper_executions is incomplete or stale.
    """
    import sqlite3 as _sq3
    rows = []
    now = time.time()

    def _f(v, default=0.0):
        try:
            if v is None or str(v).strip() == "":
                return default
            return float(v)
        except Exception:
            return default

    def _s(v, default=""):
        try:
            if v is None:
                return default
            return str(v)
        except Exception:
            return default

    def _cell(r, selected_cols, c, default=None):
        try:
            return r[c] if c in selected_cols else default
        except Exception:
            return default

    def _derive_mode_from_values(*vals) -> str:
        blob = " ".join(_s(v).upper() for v in vals if v is not None)
        if (
            "REAL_WALLET" in blob
            or "REAL_TX" in blob
            or "LIVE:" in blob
            or "LIVE_TX:" in blob
            or "LIVE_EXEC" in blob
            or "FUNDING_MODE=REAL" in blob
            or " REAL " in f" {blob} "
        ):
            return "LIVE"
        return "PAPER"

    def _derive_engine_from_values(mode, execution_source, engine_id, entry_price_source):
        if mode == "LIVE":
            return _s(execution_source or engine_id or entry_price_source or "REAL_TX")
        return _s(execution_source or engine_id or "PAPER_ENGINE")

    try:
        conn = _sq3.connect(str(db_path), timeout=4)
        conn.row_factory = _sq3.Row
        conn.execute("PRAGMA busy_timeout=1500")

        seen_open_mints = set()
        seen_closed_position_ids = set()

        # ── OPEN POSITIONS: active BUY rows, mode-aware ─────────────────────
        try:
            _cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
            _base = ["id", "mint_address", "token_name", "status", "opened_at",
                     "entry_price", "position_size_usd", "realized_pnl_usd"]
            _opt = [
                "token_symbol", "unrealized_pnl_usd", "current_price", "last_price",
                "final_exec_pct", "exit_category", "win_loss", "closed_at",
                "slippage_pct", "confidence", "reason_code", "trigger_code",
                "engine_id", "mark_source", "live_exec_pct", "live_exec_source",
                "quantity", "funding_mode", "execution_source", "money_source",
                "entry_price_source", "exit_reason", "peak_pnl_pct",
                "copytrade_influenced", "copytrade_source", "copytrade_wallet",
                "copytrade_reason",
            ]
            _sel = [c for c in _base if c in _cols] + [c for c in _opt if c in _cols]
            if not {"mint_address", "token_name", "status", "opened_at"}.issubset(set(_sel)):
                raise RuntimeError("paper_positions missing required feed columns")

            open_rows = conn.execute(
                f"SELECT {', '.join(_sel)} FROM paper_positions "
                "WHERE status='OPEN' ORDER BY opened_at DESC LIMIT 80"
            ).fetchall()
            selected = set(_sel)

            for r in open_rows:
                mint = _s(_cell(r, selected, "mint_address"))
                seen_open_mints.add(mint)

                size = _f(_cell(r, selected, "position_size_usd", 0.0))
                pnl_usd = _f(
                    _cell(r, selected, "unrealized_pnl_usd",
                          _cell(r, selected, "realized_pnl_usd", 0.0))
                )
                pnl_pct = (pnl_usd / size * 100.0) if size > 0 else 0.0

                funding_mode = _cell(r, selected, "funding_mode", "")
                execution_source = _cell(r, selected, "execution_source", "")
                money_source = _cell(r, selected, "money_source", "")
                entry_price_source = _cell(r, selected, "entry_price_source", "")
                live_exec_source = _cell(r, selected, "live_exec_source", "")
                exit_reason = _cell(r, selected, "exit_reason", "")

                mode = _derive_mode_from_values(
                    funding_mode, execution_source, money_source,
                    entry_price_source, live_exec_source, exit_reason
                )
                engine = _derive_engine_from_values(
                    mode, execution_source,
                    _cell(r, selected, "engine_id", ""),
                    entry_price_source
                )

                rows.append({
                    "side": "BUY",
                    "src": "paper_positions",
                    "mode": mode,
                    "name": _s(_cell(r, selected, "token_name", "")),
                    "symbol": _s(_cell(r, selected, "token_symbol", "")),
                    "mint": mint,
                    "status": _s(_cell(r, selected, "status", "OPEN")),
                    "stake_usd": size,
                    "size_usd": size,
                    "cash_flow_usd": -abs(size) if size else 0.0,
                    "exit_value_usd": 0.0,
                    "qty": _f(_cell(r, selected, "quantity", 0.0)),
                    "entry_price": _f(_cell(r, selected, "entry_price", 0.0)),
                    "current_price": _f(_cell(r, selected, "current_price",
                                           _cell(r, selected, "last_price", 0.0))),
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "ts": _f(_cell(r, selected, "opened_at", now), now),
                    "conf": _f(_cell(r, selected, "confidence", 0.0)),
                    "slip": _f(_cell(r, selected, "slippage_pct", 0.0)),
                    "reason": _s(_cell(r, selected, "reason_code",
                                  _cell(r, selected, "trigger_code", ""))),
                    "engine": engine,
                    "mark_src": _s(_cell(r, selected, "mark_source", "")),
                    "exit_cat": _s(_cell(r, selected, "exit_category", "")),
                    "win_loss": _s(_cell(r, selected, "win_loss", "")),
                    "runner_tier": "",
                    "runner_score_pct": 0.0,
                    "volume_5m_usd": 0.0,
                    "buy_velocity": 0.0,
                    "peak_pnl_pct": _f(_cell(r, selected, "peak_pnl_pct", 0.0)),
                    "liquidity_usd": 0.0,
                    "copytrade_influenced": int(_cell(r, selected, "copytrade_influenced", 0) or 0),
                    "copytrade_source": _s(_cell(r, selected, "copytrade_source", "")),
                    "copytrade_wallet": _s(_cell(r, selected, "copytrade_wallet", "")),
                    "copytrade_reason": _s(_cell(r, selected, "copytrade_reason", "")),
                })
        except Exception:
            seen_open_mints = set()

        # ── CLOSED POSITIONS: canonical SELL/PnL rows from position truth ───
        try:
            _cols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
            _base = ["id", "mint_address", "token_name", "status", "opened_at",
                     "entry_price", "position_size_usd", "realized_pnl_usd"]
            _opt = [
                "token_symbol", "closed_at", "exit_price", "final_exit_price",
                "current_price", "last_price", "exit_value_usd", "final_value_usd",
                "exit_category", "win_loss", "slippage_pct", "confidence",
                "reason_code", "trigger_code", "engine_id", "mark_source",
                "quantity", "funding_mode", "execution_source", "money_source",
                "entry_price_source", "exit_reason", "live_exec_source",
                "peak_pnl_pct",
                "copytrade_influenced", "copytrade_source", "copytrade_wallet",
                "copytrade_reason",
            ]
            _sel = [c for c in _base if c in _cols] + [c for c in _opt if c in _cols]
            if not {"id", "mint_address", "token_name", "status", "opened_at"}.issubset(set(_sel)):
                raise RuntimeError("paper_positions missing required closed feed columns")
            _order = "COALESCE(closed_at, opened_at, 0)" if "closed_at" in _cols else "COALESCE(opened_at, 0)"

            closed_rows = conn.execute(
                f"SELECT {', '.join(_sel)} FROM paper_positions "
                f"WHERE status='CLOSED' ORDER BY {_order} DESC LIMIT 320"
            ).fetchall()
            selected = set(_sel)

            for r in closed_rows:
                pid = int(_cell(r, selected, "id", 0) or 0)
                seen_closed_position_ids.add(pid)

                size = _f(_cell(r, selected, "position_size_usd", 0.0))
                pnl_usd = _f(_cell(r, selected, "realized_pnl_usd", 0.0))
                pnl_pct = (pnl_usd / size * 100.0) if size > 0 else 0.0

                explicit_exit_value = _f(_cell(r, selected, "exit_value_usd",
                                         _cell(r, selected, "final_value_usd", 0.0)))
                exit_value = explicit_exit_value if explicit_exit_value > 0 else max(0.0, size + pnl_usd)

                funding_mode = _cell(r, selected, "funding_mode", "")
                execution_source = _cell(r, selected, "execution_source", "")
                money_source = _cell(r, selected, "money_source", "")
                entry_price_source = _cell(r, selected, "entry_price_source", "")
                live_exec_source = _cell(r, selected, "live_exec_source", "")
                exit_reason = _cell(r, selected, "exit_reason", "")

                mode = _derive_mode_from_values(
                    funding_mode, execution_source, money_source,
                    entry_price_source, live_exec_source, exit_reason
                )
                engine = _derive_engine_from_values(
                    mode, execution_source,
                    _cell(r, selected, "engine_id", ""),
                    entry_price_source
                )

                exit_px = _f(_cell(r, selected, "exit_price",
                              _cell(r, selected, "final_exit_price",
                              _cell(r, selected, "current_price",
                              _cell(r, selected, "last_price", 0.0)))))

                rows.append({
                    "side": "SELL",
                    "src": "paper_positions",
                    "mode": mode,
                    "name": _s(_cell(r, selected, "token_name", "")),
                    "symbol": _s(_cell(r, selected, "token_symbol", "")),
                    "mint": _s(_cell(r, selected, "mint_address", "")),
                    "status": "CLOSED",
                    "stake_usd": size,
                    "size_usd": exit_value,
                    "cash_flow_usd": exit_value,
                    "exit_value_usd": exit_value,
                    "qty": _f(_cell(r, selected, "quantity", 0.0)),
                    "entry_price": _f(_cell(r, selected, "entry_price", 0.0)),
                    "current_price": exit_px,
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "ts": _f(_cell(r, selected, "closed_at", _cell(r, selected, "opened_at", now)), now),
                    "conf": _f(_cell(r, selected, "confidence", 0.0)),
                    "slip": _f(_cell(r, selected, "slippage_pct", 0.0)),
                    "reason": _s(_cell(r, selected, "exit_reason",
                                  _cell(r, selected, "reason_code",
                                  _cell(r, selected, "trigger_code", "")))),
                    "engine": engine,
                    "mark_src": _s(_cell(r, selected, "mark_source", "")),
                    "exit_cat": _s(_cell(r, selected, "exit_category", "")),
                    "win_loss": _s(_cell(r, selected, "win_loss", "")),
                    "runner_tier": "",
                    "runner_score_pct": 0.0,
                    "volume_5m_usd": 0.0,
                    "buy_velocity": 0.0,
                    "peak_pnl_pct": _f(_cell(r, selected, "peak_pnl_pct", pnl_pct)),
                    "liquidity_usd": 0.0,
                    "copytrade_influenced": int(_cell(r, selected, "copytrade_influenced", 0) or 0),
                    "copytrade_source": _s(_cell(r, selected, "copytrade_source", "")),
                    "copytrade_wallet": _s(_cell(r, selected, "copytrade_wallet", "")),
                    "copytrade_reason": _s(_cell(r, selected, "copytrade_reason", "")),
                })
        except Exception:
            pass

        # ── EXECUTION EVENTS: entry rows + fallback exit rows, mode-aware ───
        try:
            _ecols = {r[1] for r in conn.execute("PRAGMA table_info(paper_executions)").fetchall()}
            _ppcols = {r[1] for r in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}

            def _e(col, alias=None, fallback="NULL"):
                alias_sql = f" AS {alias or col}"
                return f"pe.{col}{alias_sql}" if col in _ecols else f"{fallback}{alias_sql}"

            def _p(col, alias=None, fallback="NULL"):
                alias_sql = f" AS {alias or ('pp_' + col)}"
                return f"pp.{col}{alias_sql}" if col in _ppcols else f"{fallback}{alias_sql}"

            _esel = [
                _e("id"), _e("position_id"), _e("token_name"), _e("mint_address"),
                _e("side", fallback="'SELL'"), _e("price", fallback="0.0"),
                _e("quantity", fallback="0.0"), _e("notional_usd", fallback="0.0"),
                _e("value_usd", fallback="0.0"), _e("reason", fallback="''"),
                _e("timestamp", fallback="0.0"),
                _p("realized_pnl_usd", "true_pnl_usd", "0.0"),
                _p("position_size_usd", "true_size_usd", "0.0"),
                _p("win_loss", "pp_win_loss", "''"),
                _p("exit_category", "pp_exit_cat", "''"),
                _p("status", "pp_status", "''"),
            ]
            for _ec in ["token_symbol", "slippage_pct", "route", "engine_id", "confidence"]:
                _esel.append(_e(_ec, fallback=("0.0" if _ec in {"slippage_pct", "confidence"} else "''")))
            for _pc in ["funding_mode", "execution_source", "money_source",
                        "entry_price_source", "exit_reason", "live_exec_source"]:
                _esel.append(_p(_pc, f"pp_{_pc}", "''"))

            exec_rows = conn.execute(
                f"SELECT {', '.join(_esel)} FROM paper_executions pe "
                "LEFT JOIN paper_positions pp ON pp.id = pe.position_id "
                "ORDER BY pe.timestamp DESC LIMIT 500"
            ).fetchall()

            for r in exec_rows:
                side = _s(r["side"] or "SELL").upper()
                pid = int(r["position_id"] or 0)

                # Closed positions above are canonical SELL truth; avoid duplicate SELL rows.
                if side == "SELL" and pid in seen_closed_position_ids:
                    continue

                mint = _s(r["mint_address"])
                if side == "BUY" and mint in seen_open_mints:
                    continue

                # FEED_ORPHAN_DEDUP_20260625: skip orphan ENTRY events whose position already
                # CLOSED - the closed-position row supersedes the raw entry,
                # so a finished trade no longer lingers as an n/a BUY row.
                _pp_status = _s(r["pp_status"]).upper() if "pp_status" in r.keys() else ""
                if side == "BUY" and _pp_status == "CLOSED":
                    continue

                funding_mode = r["pp_funding_mode"]
                execution_source = r["pp_execution_source"]
                money_source = r["pp_money_source"]
                entry_price_source = r["pp_entry_price_source"]
                exit_reason = r["pp_exit_reason"]
                live_exec_source = r["pp_live_exec_source"]

                mode = _derive_mode_from_values(
                    funding_mode, execution_source, money_source,
                    entry_price_source, exit_reason, live_exec_source
                )

                notional = _f(r["notional_usd"], _f(r["value_usd"]))
                value_usd = _f(r["value_usd"], notional)

                if side == "SELL":
                    pnl_usd = _f(r["true_pnl_usd"])
                    stake = _f(r["true_size_usd"], notional)
                    pnl_pct = (pnl_usd / stake * 100.0) if stake > 0 else 0.0
                    exit_value = value_usd if value_usd > 0 else max(0.0, stake + pnl_usd)
                    cash_flow = exit_value
                    win_loss = _s(r["pp_win_loss"])
                    exit_cat = _s(r["pp_exit_cat"])
                    status = "EXIT"
                    size_usd = exit_value
                else:
                    pnl_usd = 0.0
                    pnl_pct = 0.0
                    stake = notional
                    exit_value = 0.0
                    cash_flow = -abs(notional) if notional else 0.0
                    win_loss = ""
                    exit_cat = ""
                    status = "ENTRY"
                    size_usd = notional

                engine = _derive_engine_from_values(mode, execution_source, r["engine_id"], entry_price_source)

                rows.append({
                    "side": side,
                    "src": "paper_executions",
                    "mode": mode,
                    "name": _s(r["token_name"]),
                    "symbol": _s(r["token_symbol"]),
                    "mint": mint,
                    "status": status,
                    "stake_usd": stake,
                    "size_usd": size_usd,
                    "cash_flow_usd": cash_flow,
                    "exit_value_usd": exit_value,
                    "qty": _f(r["quantity"]),
                    "entry_price": 0.0,
                    "current_price": _f(r["price"]),
                    "pnl_usd": pnl_usd,
                    "pnl_pct": pnl_pct,
                    "ts": _f(r["timestamp"], now),
                    "conf": _f(r["confidence"]),
                    "slip": _f(r["slippage_pct"]),
                    "reason": _s(r["reason"]),
                    "engine": engine,
                    "mark_src": _s(r["route"]),
                    "exit_cat": exit_cat,
                    "win_loss": win_loss,
                    "runner_tier": "",
                    "runner_score_pct": 0.0,
                    "volume_5m_usd": 0.0,
                    "buy_velocity": 0.0,
                    "peak_pnl_pct": 0.0,
                    "liquidity_usd": 0.0,
                })
        except Exception:
            pass

        conn.close()
    except Exception:
        pass

    # SIGNIFICANT_TRADE_SURFACING_20260621: previously rows were sorted purely by
    # timestamp then cut at [:300]. A runner that closed a while ago could be pushed
    # past row 300 by a flood of tiny recent trades/execution events and dropped
    # entirely - which is why a banked +$300 runner never appeared in the feed.
    # Fix: keep ALL significant closes (|PnL| >= threshold, i.e. real wins/losses,
    # not micro-noise) regardless of the cap, then fill the rest by recency.
    _SIGNIFICANT_PNL_USD = 5.0  # a real win/loss, not slippage noise
    _significant = [r for r in rows
                    if abs(_f(r.get("pnl_usd", 0.0))) >= _SIGNIFICANT_PNL_USD]
    _ordinary = [r for r in rows
                 if abs(_f(r.get("pnl_usd", 0.0))) < _SIGNIFICANT_PNL_USD]
    _significant.sort(key=lambda x: x.get("ts", 0), reverse=True)
    _ordinary.sort(key=lambda x: x.get("ts", 0), reverse=True)
    # significant trades always survive; ordinary fill the remaining budget
    _cap = 300
    _budget_ordinary = max(0, _cap - len(_significant))
    merged = _significant + _ordinary[:_budget_ordinary]
    merged.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return merged[:max(_cap, len(_significant))]


def _feed_thermal_color(r: dict, now: float, hour_config: dict = None) -> str:
    """Return the left-edge thermal triage colour for a feed row.

    Priority (cold → hot Solana palette):
      1. monster runner (≥75% pnl/peak/runner)  → gold  #FFD700
      2. golden hour trade                        → gold  #FFD700
      3. clean positive pnl / win                → green #14F195
      4. negative pnl / loss / veto              → red   #FF073A
      5. warm hour (volume building)             → magenta #9945FF
      6. fresh ingestion (age < 60s)             → cyan  #8EF9FF
      7. recent cortex analysis (age < 600s)     → purple #9945FF
      8. cold/stale                              → grey  #555555
    """
    try:
        pnl_pct    = float(r.get("pnl_pct") or 0.0)
        pnl_usd    = float(r.get("pnl_usd") or 0.0)
        ts         = float(r.get("ts") or now)
        age        = max(0.0, now - ts)
        wl         = str(r.get("win_loss") or "").upper()
        side       = str(r.get("side") or "").upper()
        status     = str(r.get("status") or "").upper()
        runner_pct = float(r.get("runner_score_pct") or 0.0)
        peak_pct   = float(r.get("peak_pnl_pct") or 0.0)

        # 1. Monster runner
        if pnl_pct >= 75 or peak_pct >= 75 or runner_pct >= 75:
            return "#FFD700"

        # 2. Golden hour - check trade timestamp against golden hours config
        if hour_config:
            import datetime as _dt
            try:
                import pytz as _pytz
                _aest = _pytz.timezone("Australia/Sydney")
                _trade_hr = _dt.datetime.fromtimestamp(ts, tz=_aest).hour
            except Exception:
                # fallback: UTC+10 offset
                _trade_hr = _dt.datetime.utcfromtimestamp(ts + 36000).hour
            if _trade_hr in hour_config.get("golden", set()):
                return "#FFD700"

        # 3. Clean win
        if pnl_usd > 0 or "WIN" in wl:
            return "#14F195"

        # 4. Loss / veto
        if pnl_usd < 0 or "LOSS" in wl or status == "VETOED" or (side == "SELL" and pnl_usd < 0):
            return "#FF073A"

        # 5/6. Fresh / recent - cyan for very fresh, purple for recent with context
        if age < 60:
            return "#8EF9FF"
        if age < 600 and (r.get("reason") or r.get("engine")):
            return "#9945FF"

        return "#555555"
    except Exception:
        return "#555555"



def _render_feed_row(r: dict, now: float, hour_config: dict = None) -> str:
    """Render one feed row in the mandatory 3-child structure:
        <sntRow>
            <sntAccent/>      thermal strip
            <sntMain>         line1 + meta line2
            <sntAge>          age column
        </sntRow>
    """
    side    = str(r.get("side") or "BUY").upper()
    mode    = str(r.get("mode") or "PAPER").upper()
    pnl_usd = float(r.get("pnl_usd") or 0.0)
    pnl_pct = float(r.get("pnl_pct") or 0.0)
    cash_flow = float(r.get("cash_flow_usd") or 0.0)
    stake = float(r.get("stake_usd") or 0.0)
    exit_value = float(r.get("exit_value_usd") or 0.0)

    has_pnl = abs(pnl_usd) > 0.001 or abs(pnl_pct) > 0.01

    if not has_pnl:
        pct_display, pct_cls = "  n/a", "sntPctNeu"
    elif pnl_pct >= 75:
        pct_display, pct_cls = f"+{pnl_pct:.1f}%", "sntPctGold"
    elif pnl_pct > 0:
        pct_display, pct_cls = f"+{pnl_pct:.1f}%", "sntPctPos"
    elif pnl_pct < 0:
        pct_display, pct_cls = f"{pnl_pct:.1f}%", "sntPctNeg"
    else:
        pct_display, pct_cls = "0.0%", "sntPctNeu"

    accent_col = _feed_thermal_color(r, now, hour_config)
    side_cls = "sntSideBuy" if side == "BUY" else "sntSideSell"

    name = _feed_safe(r.get("name", ""), 18) or "UNKNOWN"
    sym  = _feed_safe(r.get("symbol", ""), 8)
    ticker_html = f"<span class='sntTicker'>{sym}</span>" if sym else ""

    hour_badge = ""
    if hour_config and r.get("ts"):
        hl, hc = _feed_hour_class(
            r["ts"],
            hour_config.get("golden", set()),
            hour_config.get("block",  set()),
            hour_config.get("reduce", set()),
        )
        if hl:
            hour_badge = f"<span class='sntHourBadge {hc}'>{html.escape(hl)}</span>"

    if has_pnl:
        usd_col = "#14F195" if pnl_usd > 0 else "#FF073A"
        usd_sign = "+" if pnl_usd > 0 else ""
        usd_html = (
            f"<span class='sntPnlUsd' style='color:{usd_col};'>"
            f"{usd_sign}{pnl_usd:.2f}</span>"
        )
    else:
        usd_html = ""

    line1 = (
        f"<div class='sntLine1'>"
        f"<span class='sntPct {pct_cls}'>{html.escape(pct_display)}</span>"
        f"<span class='sntSide {side_cls}'>{side}</span>"
        f"<span class='sntName'>{name}{ticker_html}{hour_badge}</span>"
        f"{usd_html}"
        f"</div>"
    )

    meta = []

    if r.get("entry_price"):
        meta.append(f"entry {float(r['entry_price']):.6g}")
    if r.get("current_price"):
        meta.append(f"now {float(r['current_price']):.6g}")

    if stake:
        meta.append(f"<span style='color:#8EF9FF;'>stake ${stake:.2f}</span>")

    if side == "BUY" and cash_flow:
        meta.append(f"<span style='color:#FFB347;'>cash {cash_flow:.2f}</span>")

    if side == "SELL":
        if exit_value:
            meta.append(f"<span style='color:#FFD700;'>exit ${exit_value:.2f}</span>")
        elif cash_flow:
            meta.append(f"<span style='color:#FFD700;'>cash +${cash_flow:.2f}</span>")

    if r.get("conf"):
        meta.append(f"c{float(r['conf']):.2f}")

    if r.get("reason"):
        meta.append(f"<span style='color:rgba(255,215,0,.55);'>{_feed_safe(r['reason'], 22)}</span>")

    if r.get("src"):
        meta.append(f"<span style='color:rgba(255,255,255,.18);'>{_feed_safe(r['src'], 14)}</span>")

    mode_col = "#FF073A" if mode == "LIVE" else "rgba(153,69,255,.55)"
    meta.append(f"<span style='color:{mode_col};font-weight:700;'>{mode}</span>")

    # SIGNOFF_COPYTRADE_ADVISORY_20260615 - copytrade influence badge.
    # Advisory only; reflects whether a tracked smart wallet touched this mint.
    # Mainline rows show MAINLINE ONLY so the distinction is always visible.
    if int(r.get("copytrade_influenced") or 0) == 1:
        _ct_src = str(r.get("copytrade_source") or "").lower()
        if "gmgn" in _ct_src:
            _ct_label = "GMGN"
        elif "telegram" in _ct_src or "tg" in _ct_src:
            _ct_label = "TELEGRAM"
        elif "shadow" in _ct_src:
            _ct_label = "PAPER ADVISORY"
        else:
            _ct_label = "SMART WALLET"
        _ct_w = str(r.get("copytrade_wallet") or "")[:6]
        _ct_txt = f"{_ct_label}" + (f" {_ct_w}" if _ct_w else "")
        meta.append(
            f"<span style='color:#AFA9EC;background:rgba(127,119,221,.16);"
            f"border:1px solid #7F77DD;border-radius:4px;padding:1px 6px;"
            f"font-weight:700;letter-spacing:.04em;'>{html.escape(_ct_txt)}</span>"
        )
    else:
        meta.append(
            f"<span style='color:rgba(255,255,255,.22);font-size:0.66rem;"
            f"letter-spacing:.04em;'>MAINLINE ONLY</span>"
        )

    if r.get("engine"):
        engine_col = "#FF073A" if mode == "LIVE" else "rgba(255,255,255,.24)"
        meta.append(f"<span style='color:{engine_col};'>{_feed_safe(r['engine'], 18)}</span>")

    stat_v = str(r.get("status") or "").upper()
    if stat_v:
        meta.append(f"<span style='color:rgba(255,255,255,.32);'>{stat_v}</span>")

    if r.get("win_loss"):
        wl = r["win_loss"].upper()
        wl_col = "#14F195" if "WIN" in wl else ("#FF073A" if "LOSS" in wl else "rgba(255,255,255,.28)")
        meta.append(f"<span style='color:{wl_col};'>{wl}</span>")

    line2 = (f"<div class='sntMeta'>" + " · ".join(meta) + "</div>") if meta else ""

    age_str = _feed_age(r.get("ts"), now)
    ts_str  = _feed_ts(r.get("ts"))
    age_html = (
        f"<div class='sntAge'>{age_str}<br>"
        f"<span style='color:rgba(255,255,255,.12);'>{ts_str}</span></div>"
    )

    return (
        f"<div class='sntRow'>"
        f"<div class='sntAccent' style='background:{accent_col};'></div>"
        f"<div class='sntMain'>{line1}{line2}</div>"
        f"{age_html}"
        f"</div>"
    )


def _render_inline_buy_sell_feed(db_path) -> None:
    """Inline AXON/Motor BUY/SELL feed. Compact, paginated, terminal-grade.
    Renders 12 rows per page. No sidecar renderer. No DB writes."""
    _inject_feed_css()
    now = time.time()

    st.markdown(
        "<div class='sntFeedHdr'>"
        "<span>⚡ AXON / MOTOR FEED - BUY / SELL</span>"
        "<span class='sntFeedLegal'>LEGAL MODE · MODE-AWARE</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    if not Path(str(db_path)).exists():
        st.markdown("<div class='sntEmpty'>// DB NOT FOUND - FEED OFFLINE //</div>",
                    unsafe_allow_html=True)
        return

    rows = _fetch_feed_rows_inline(db_path)
    if not rows:
        st.markdown("<div class='sntEmpty'>// NO TRADES YET - ORGANISM HUNTING //</div>",
                    unsafe_allow_html=True)
        return

    _hour_config: dict = {"golden": set(), "block": set(), "reduce": set()}
    try:
        import sqlite3 as _sq3
        _hc = _sq3.connect(str(db_path), timeout=2)

        def _hcfg(k, d=""):
            r = _hc.execute("SELECT value FROM system_config WHERE key=?", (k,)).fetchone()
            return str(r[0]) if r and r[0] else d

        def _parse_hrs(s):
            out = set()
            for tok in str(s).replace(",", " ").split():
                try:
                    out.add(int(tok))
                except ValueError:
                    pass
            return out

        _hour_config["golden"] = _parse_hrs(_hcfg("GOLDEN_HOURS_AEST", "10,11,15,22"))
        _hour_config["block"]  = _parse_hrs(_hcfg("HOUR_GATE_BLOCK_UTC", ""))
        _hour_config["reduce"] = _parse_hrs(_hcfg("HOUR_GATE_REDUCE_UTC", ""))
        _hc.close()
    except Exception:
        _hour_config["golden"] = {10, 11, 15, 22}

    _PAGE_SIZE = 12
    total = len(rows)
    total_pages = max(1, (total + _PAGE_SIZE - 1) // _PAGE_SIZE)

    try:
        page = int(st.session_state.get("snt_feed_page", 0))
    except Exception:
        page = 0

    if page < 0:
        page = 0
    if page >= total_pages:
        page = total_pages - 1
    st.session_state["snt_feed_page"] = page

    start = page * _PAGE_SIZE
    end   = min(start + _PAGE_SIZE, total)
    visible = rows[start:end]

    rows_html = "".join(_render_feed_row(r, now, _hour_config) for r in visible)
    st.markdown(f"<div class='sntFeedWrap'>{rows_html}</div>", unsafe_allow_html=True)

    open_ct = sum(1 for r in rows if str(r.get("status", "")).upper() in ("OPEN", "ACTIVE"))
    live_ct = sum(1 for r in rows if str(r.get("mode", "")).upper() == "LIVE")
    vis_pnl = sum(float(r.get("pnl_usd") or 0) for r in visible)
    pnl_col = "#14F195" if vis_pnl >= 0 else "#FF073A"
    pnl_sign = "+" if vis_pnl >= 0 else ""

    _c1, _c2, _c3 = st.columns([1, 3, 1])

    with _c1:
        if st.button("‹ PREV", key="snt_feed_prev", disabled=(page <= 0),
                     use_container_width=True):
            st.session_state["snt_feed_page"] = max(0, page - 1)
            try:
                st.rerun()
            except Exception:
                try:
                    st.experimental_rerun()
                except Exception:
                    pass

    with _c2:
        st.markdown(
            f"<div style='text-align:center;font-family:Share Tech Mono,monospace;"
            f"font-size:0.66rem;color:rgba(255,255,255,.32);letter-spacing:2px;"
            f"padding-top:6px;'>"
            f"PAGE {page + 1}/{total_pages} · ROWS {start + 1}-{end}/{total} · "
            f"{open_ct} OPEN · {live_ct} LIVE · VISIBLE PnL "
            f"<span style='color:{pnl_col};'>{pnl_sign}{vis_pnl:.2f} USD</span>"
            f"</div>",
            unsafe_allow_html=True,
        )

    with _c3:
        if st.button("NEXT ›", key="snt_feed_next",
                     disabled=(page >= total_pages - 1),
                     use_container_width=True):
            st.session_state["snt_feed_page"] = min(total_pages - 1, page + 1)
            try:
                st.rerun()
            except Exception:
                try:
                    st.experimental_rerun()
                except Exception:
                    pass



@st.fragment(run_every=19)
def render_unified_execution_lanes() -> None:
    """
    UNIFIED EXECUTION LANE - isolated fragment, auto-refreshes every 20s.
    Shows: token, pct, dollar PnL, price source badge, age.
    Reads from fetch_live_open_positions_breathe() which prefers live_exec_*
    columns (age 1-8s) over market_snapshots (up to 20s stale).
    Fragment isolation means this section re-renders independently -
    no full-page rerun needed, no other panels disturbed.
    """
    import time as _t
    _rb = int(_t.time() // 5)  # 5s bucket for cache alignment
    _trades = fetch_live_open_positions_breathe(str(DB_PATH), _rb)
    if not _trades:
        st.markdown(
            "<div style='padding:10px 16px;border:1px dashed rgba(153,69,255,0.2);"
            "border-radius:8px;background:rgba(5,2,16,0.3);margin:4px 0 8px;'>"
            "<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            "color:#333;letter-spacing:2px;margin-bottom:6px;'>⚡ LIVE EXECUTION LANES - DORMANT</div>"
            "<div style='height:12px;border-radius:6px;background:rgba(255,255,255,0.03);"
            "border:1px solid rgba(255,255,255,0.06);position:relative;'>"
            "<div style='position:absolute;top:-2px;left:50%;width:2px;height:16px;"
            "background:#ffffff11;transform:translateX(-50%);'></div></div>"
            "<div style='display:flex;justify-content:space-between;margin-top:4px;"
            "font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#2a2a2a;'>"
            "<span>◄ SL</span><span>HUNTING - AWAITING LATCH</span><span>TP ►</span>"
            "</div></div>",
            unsafe_allow_html=True,
        )
        render_runner_observability_panel(DB_PATH)
        return

    st.markdown(
        "<div style='margin-bottom:8px;padding:8px 14px;"
        "border:1px solid rgba(20,241,149,0.2);border-radius:8px;"
        "background:rgba(5,2,16,0.4);'>"
        f"<span style='font-family:Share Tech Mono;font-size:0.72rem;"
        f"letter-spacing:3px;color:#14F195;'>LIVE EXECUTION LANE</span>"
        f"<span style='font-family:Share Tech Mono;font-size:0.66rem;"
        f"color:#888;float:right;'>{len(_trades)} position(s)</span>"
        "</div>",
        unsafe_allow_html=True,
    )

    for _t_row in _trades:
        _pct_raw = _t_row.get("pnl_pct", None)
        _pct    = float(_pct_raw) if _pct_raw is not None else 0.0
        _pnl    = float(_t_row.get("pnl", 0.0) or 0.0)
        _badge  = _t_row.get("src_badge", "STALE")
        _age    = _t_row.get("age_str", "?")
        _token  = _t_row.get("token", "?")
        _fresh  = _t_row.get("is_fresh", False)
        _pcol   = "#14F195" if _pct >= 0 else "#FF073A"
        _bcol   = "#14F195" if _badge == "LIVE" else (
                  "#FF073A" if _badge in ("STALE","STALE_EXEC","NO_DATA","NO_EXEC_DATA") else "#FFD700")
        st.markdown(
            f"<div style='display:flex;justify-content:space-between;"
            f"align-items:center;padding:6px 12px;margin-bottom:2px;"
            f"border-radius:6px;border-left:3px solid {_pcol};"
            f"background:rgba(255,255,255,0.02);"
            f"font-family:Share Tech Mono,monospace;font-size:0.72rem;'>"
            f"<span style='color:#8EF9FF;'>{html.escape(_token)}</span>"
            f"<span style='color:{_pcol};font-weight:bold;'>"
            f"{(_pct_raw is not None and f'{_pct:+.2f}%&nbsp;&nbsp;{chr(36)}{_pnl:+.3f}' or ('?? GATE BLOCKED' if _badge == 'GATE_BLOCKED' else 'NO EXEC DATA'))}</span>"
            f"<span style='color:{_bcol};font-size:0.66rem;'>"
            f"[{_badge}&nbsp;{_age}]</span>"
            f"</div>",
            unsafe_allow_html=True,
        )
        # PRESSURE GAUGE METER — SL(red)→→TP(green) per trade
        render_living_trade_meter(_t_row)

    render_runner_observability_panel(DB_PATH)


def render_sovereign_flow_engine(snapshots_df=None, open_pos_df=None, reviews_df=None, raw_dna_df=None, executions_df=None) -> None:
    """SOVEREIGN FLOW ENGINE - unified pipeline visualiser. No @fragment.

    SIGNOFF_20260624 visual pass: one typography system, canonical danger red,
    crystalline glass cards, and a tap help affordance. Logic unchanged.
    """
    import html as _html, time as _t
    _G=C_GREEN; _GO=C_GOLD; _R=C_RED; _P=C_PURPLE; _C=C_CYAN; _DIM="#94A3B8"
    def _sdf(df): return df is not None and not df.empty
    def _ival(df,col,d=0):
        try: return int(df.iloc[0][col]) if _sdf(df) and col in df.columns else d
        except: return d
    _total_dna=_ival(raw_dna_df,"count",0)
    _recent_raw=len(snapshots_df) if _sdf(snapshots_df) else 0
    _raw_color=_G if _total_dna>0 else _DIM
    _latched_n=len(snapshots_df[snapshots_df["candidate_state"]=="latched"]) if _sdf(snapshots_df) and "candidate_state" in snapshots_df.columns else 0
    _vetoed_n=len(snapshots_df[snapshots_df["candidate_state"]=="vetoed"]) if _sdf(snapshots_df) and "candidate_state" in snapshots_df.columns else 0
    _total_snap=_recent_raw
    _qual_rate=(_latched_n/max(_total_snap,1))*100
    _rug_rate=(_vetoed_n/max(_total_snap,1))*100
    if _total_snap==0: _tide_state="🌑 DORMANT";_tide_color=_DIM;_tide_score=0
    elif _qual_rate>=15 and _rug_rate<40: _tide_state="🔥 HUNTING";_tide_color=_G;_tide_score=min(100,int(_qual_rate*4))
    elif _qual_rate>=5: _tide_state="🧪 LEARNING";_tide_color=_GO;_tide_score=min(80,int(_qual_rate*3))
    elif _rug_rate>60: _tide_state="⚡ CHAOTIC";_tide_color=_R;_tide_score=15
    else: _tide_state="🛡 DEFENSIVE";_tide_color=_C;_tide_score=30
    # TIDE_TRUTH_20260714: configured tide is authoritative; intake heuristic is diagnostic only.
    _observed_tide_state, _observed_tide_score = _tide_state, _tide_score
    try:
        _configured_tide = str(query_db("SELECT value FROM system_config WHERE key='MARKET_TIDE_STATE' LIMIT 1").iloc[0, 0] or '').strip().upper()
    except Exception:
        _configured_tide = ''
    _tide_palette = {'FLOOD': ('FLOOD', _G, 85), 'HUNTING': ('HUNTING', _G, 75), 'NORMAL': ('NORMAL', _C, 55), 'DROUGHT': ('DROUGHT', _GO, 25), 'LEARNING': ('LEARNING', _GO, 45), 'DEFENSIVE': ('DEFENSIVE', _C, 30), 'CHAOTIC': ('CHAOTIC', _R, 15), 'DORMANT': ('DORMANT', _DIM, 0), 'EXTREME': ('EXTREME', _R, 10)}
    if _configured_tide in _tide_palette:
        _tv, _tide_color, _tide_score = _tide_palette[_configured_tide]
        _tide_state = _tv
    _tide_source = 'CONFIG' if _configured_tide in _tide_palette else 'OBSERVED'
    _execute_n=_watch_n=_skip_n=0
    try:
        import sqlite3 as _sq3f
        _cf=_sq3f.connect(str(DB_PATH),timeout=2,check_same_thread=False)
        _tables_f={r[0] for r in _cf.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "candidate_scores" in _tables_f:
            for _sr in _cf.execute("SELECT total_score FROM candidate_scores ORDER BY scored_at DESC LIMIT 20").fetchall():
                s=int(_sr[0] or 0)
                if s>=80: _execute_n+=1
                elif s>=55: _watch_n+=1
                else: _skip_n+=1
        elif _sdf(snapshots_df) and "mint_confidence" in snapshots_df.columns:
            for _sc in snapshots_df["mint_confidence"].dropna().head(20):
                s=int(float(_sc)*100)
                if s>=80: _execute_n+=1
                elif s>=55: _watch_n+=1
                else: _skip_n+=1
        _cf.close()
    except Exception: pass
    _matrix_color=_G if _execute_n>0 else (_GO if _watch_n>0 else _DIM)
    _latch_color=_G if _latched_n>0 else _DIM
    _open_n=len(open_pos_df) if _sdf(open_pos_df) else 0
    _pnl_sum=0.0
    if _sdf(open_pos_df) and "unrealized_pnl_usd" in open_pos_df.columns:
        try: _pnl_sum=float(open_pos_df["unrealized_pnl_usd"].fillna(0).sum())
        except: pass
    _exec_pnl_col=_G if _pnl_sum>=0 else _R
    _exec_sub=f"{chr(36)}{_pnl_sum:+.2f} uPnL" if _open_n>0 else "no positions"
    # AUTHORITATIVE_WINRATE_20260713
    # Canonical CLOSED paper_positions control trading performance.
    # Polaris reviews remain research telemetry only.
    _mem_total = 0
    _mem_wins = 0
    _mem_losses = 0
    _mem_scratch = 0
    _wr = None

    try:
        _trade_truth = query_db("""
            SELECT
                COUNT(*) AS closed_count,
                SUM(
                    CASE WHEN CAST(realized_pnl_usd AS REAL) > 0
                    THEN 1 ELSE 0 END
                ) AS wins,
                SUM(
                    CASE WHEN CAST(realized_pnl_usd AS REAL) < 0
                    THEN 1 ELSE 0 END
                ) AS losses,
                SUM(
                    CASE WHEN CAST(realized_pnl_usd AS REAL) = 0
                    THEN 1 ELSE 0 END
                ) AS scratch
            FROM paper_positions
            WHERE UPPER(COALESCE(status, '')) = 'CLOSED'
              AND realized_pnl_usd IS NOT NULL
        """)

        if not _trade_truth.empty:
            _trade_row = _trade_truth.iloc[0]
            _mem_total = int(_trade_row["closed_count"] or 0)
            _mem_wins = int(_trade_row["wins"] or 0)
            _mem_losses = int(_trade_row["losses"] or 0)
            _mem_scratch = int(_trade_row["scratch"] or 0)

            if _mem_total > 0:
                _wr = round(
                    100.0 * _mem_wins / _mem_total,
                    1
                )
    except Exception:
        _wr = None

    _mem_color = (
        _G if _wr is not None and _wr >= 60.0
        else _GO if _wr is not None and _wr >= 40.0
        else _R if _wr is not None
        else _DIM
    )

    _mem_label = (
        f"{_wr:.1f}% WR"
        if _wr is not None
        else "history unavailable"
    )
    def _node(icon,title,value,sub,color,active=True):
        _op="1.0" if active else "0.42"
        return (f"<div class='snty-flow-node' style='--node-color:{color};opacity:{_op};'>"
                f"<div class='snty-flow-icon'>{icon}</div>"
                f"<div class='snty-flow-title'>{_html.escape(title)}</div>"
                f"<div class='snty-flow-value'>{_html.escape(str(value))}</div>"
                f"<div class='snty-flow-sub'>{_html.escape(str(sub))}</div></div>")
    def _arrow(color="#52606f"):
        return f"<div class='snty-flow-arrow' style='color:{color};'>▶</div>"
    _matrix_total=_execute_n+_watch_n+_skip_n
    _n1=_node("“¡","RAW STREAM",f"{_total_dna:,}",f"{_recent_raw} recent",_raw_color,_total_dna>0)
    _n2=_node("T","MARKET TIDE",_tide_state.split(" ",1)[-1],f"{_tide_source.lower()} · score {_tide_score}",_tide_color,True)
    _n3=_node("⬡","MATRIX",f"{_matrix_total} scored",f"{_execute_n}E {_watch_n}W {_skip_n}S",_matrix_color,_matrix_total>0)
    _n4=_node("”’","LATCH",str(_latched_n),"signals locked" if _latched_n>0 else "idle",_latch_color,_latched_n>0)
    _n5=_node("⚡","EXECUTION",str(_open_n),_exec_sub,_exec_pnl_col,_open_n>0)
    _n6=_node("§ ","MEMORY",_mem_label,f"{_mem_wins}W/{_mem_total-_mem_wins}L" if _mem_total>0 else "learning",_mem_color,_mem_total>0)

    st.markdown(f"""<div class='snty-crystal-panel snty-cyan-panel' style='margin:16px 0 12px;padding:16px 18px;width:100%;box-sizing:border-box;'>
      <div class='snty-title-row'>
        <div class='snty-title-left'>
          <span class='snty-section-title'>SOVEREIGN FLOW ENGINE</span>
          <details class='snty-helpbox'><summary>?</summary><div class='snty-help-pop'>Live pipeline map from raw signal intake through market tide, matrix scoring, latch, execution, and memory. It is display-only.</div></details>
        </div>
        <span class='snty-section-kicker'>RAW → TIDE → MATRIX → LATCH → EXEC → MEMORY</span>
      </div>
      <div class='snty-flow-row'>
        {_n1}{_arrow(_raw_color if _total_dna>0 else "#52606f")}{_n2}{_arrow(_tide_color if _total_snap>0 else "#52606f")}{_n3}{_arrow(_matrix_color if _matrix_total>0 else "#52606f")}{_n4}{_arrow(_latch_color if _latched_n>0 else "#52606f")}{_n5}{_arrow(_exec_pnl_col if _open_n>0 else "#52606f")}{_n6}
      </div>
      <div class='snty-summary-row'>
        <span style='color:{_tide_color};'>{_tide_state}</span>
        <span style='color:{_matrix_color};'>{_execute_n}E / {_watch_n}W / {_skip_n}S</span>
        <span style='color:{_exec_pnl_col if _open_n>0 else _DIM};'>{_open_n} open</span>
        <span style='color:{_mem_color};'>{_mem_label}</span>
      </div></div>""", unsafe_allow_html=True)


def render_matrix_filtration_panel() -> None:
    """
    EDGE CANDIDATE ARENA - Phase 3: Selective Aggression visual home.
    momentum_score ranked. Bomb Signatures ignite. Weak candidates recede.
    Observe-only. No @fragment - parent cortex cycle refreshes.
    """
    import sqlite3 as _sq3, html as _html, time as _t, json as _json
    _now = _t.time()
    _rows = []
    try:
        _c = _sq3.connect(str(DB_PATH), timeout=2.0, check_same_thread=False)
        _c.row_factory = _sq3.Row
        _ms_cols = {r[1] for r in _c.execute("PRAGMA table_info(market_snapshots)").fetchall()}
        _has_mom = "momentum_score" in _ms_cols
        if _has_mom:
            _rows = _c.execute("""
                SELECT COALESCE(token_name, mint_address) AS token_name,
                       COALESCE(momentum_score, 0.0) AS momentum_score,
                       COALESCE(bomb_signature, 0) AS bomb_signature,
                       COALESCE(ranking_snapshot, '') AS ranking_snapshot,
                       COALESCE(mint_confidence, 0.0) AS confidence,
                       COALESCE(freshness_score, 0.0) AS freshness_score,
                       COALESCE(tier, 'COLD') AS tier,
                       COALESCE(token_liquidity_usd, 0) AS liquidity_usd,
                       COALESCE(curve_progress_pct, 0) AS curve_pct,
                       COALESCE(token_age_seconds, 0) AS age_sec,
                       candidate_state, quality_status
                FROM market_snapshots
                WHERE candidate_state NOT IN ('vetoed','expired_stale','executed','dead','EXECUTOR_STALE_GATE')
                  AND COALESCE(price_updated_at, 0) > ?
                ORDER BY momentum_score DESC, freshness_score DESC, mint_confidence DESC LIMIT 8
            """, (_now - 300,)).fetchall()
        else:
            _rows = _c.execute("""
                SELECT COALESCE(token_name, mint_address) AS token_name,
                       0.0 AS momentum_score, 0 AS bomb_signature, '' AS ranking_snapshot,
                       COALESCE(mint_confidence, 0) AS confidence,
                       COALESCE(freshness_score, 0) AS freshness_score,
                       COALESCE(tier,'COLD') AS tier,
                       COALESCE(token_liquidity_usd,0) AS liquidity_usd,
                       COALESCE(curve_progress_pct,0) AS curve_pct,
                       COALESCE(token_age_seconds,0) AS age_sec,
                       candidate_state, quality_status
                FROM market_snapshots
                WHERE candidate_state NOT IN ('vetoed','expired_stale','executed','dead','EXECUTOR_STALE_GATE')
                ORDER BY mint_confidence DESC, price_updated_at DESC LIMIT 8
            """).fetchall()
        _c.close()
    except Exception:
        _rows = []

    st.markdown(
        "<div style='margin-top:12px;padding:14px 16px;"
        "border:1px solid rgba(153,69,255,0.35);border-radius:12px;"
        "background:rgba(5,2,16,0.65);overflow:hidden;width:100%;box-sizing:border-box;'>",
        unsafe_allow_html=True,
    )
    st.markdown(
        '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">'
        '<span style="font-family:Share Tech Mono;font-size:0.78rem;letter-spacing:4px;color:#9945FF;">⬡ EDGE CANDIDATE ARENA</span>'
        '<span style="font-family:Share Tech Mono;font-size:0.66rem;color:#333;letter-spacing:1px;">MOMENTUM-RANKED</span>'
        '</div>',
        unsafe_allow_html=True,
    )

    if not _rows:
        st.markdown(
            '<div style="font-family:Share Tech Mono;font-size:0.66rem;color:#333;'
            'letter-spacing:2px;padding:6px 0;">// ARENA AWAITING CANDIDATES //</div>',
            unsafe_allow_html=True,
        )
        st.markdown("</div>", unsafe_allow_html=True)
        return

    _anim_css = (
        "<style>"
        "@keyframes bombIgnite{0%,100%{box-shadow:0 0 8px #FF073A44}50%{box-shadow:0 0 22px #FF073Acc,inset 0 0 14px #FF073A22}}"
        "@keyframes hotPulse{0%,100%{opacity:0.88}50%{opacity:1}}"
        "@keyframes recede{0%,100%{opacity:0.32}50%{opacity:0.42}}"
        "</style>"
    )
    _col_header = (
        '<div style="display:grid;grid-template-columns:2fr 70px 48px 54px 48px 1fr;'
        'gap:3px;padding:2px 6px 5px;font-family:Share Tech Mono;font-size:0.66rem;'
        'letter-spacing:1px;color:#2a2a2a;border-bottom:1px solid rgba(255,255,255,0.04);margin-bottom:3px;">'
        '<span>TOKEN</span><span>MOMENTUM</span><span>CURVE</span>'
        '<span>LIQ</span><span>AGE</span><span>FACTOR</span></div>'
    )

    rows_html = ""
    for r in _rows:
        try:
            token = _html.escape(str(r["token_name"] or "")[:14])
            mom   = float(r["momentum_score"] or 0)
            bomb  = bool(r["bomb_signature"])
            tier  = str(r["tier"] or "COLD")
            liq   = float(r["liquidity_usd"] or 0)
            curve = float(r["curve_pct"] or 0)
            age   = float(r["age_sec"] or 0)
            state = str(r["candidate_state"] or "")
            # Dominant factor
            _dom = ""
            try:
                _bd = _json.loads(r["ranking_snapshot"] or "{}")
                _fac = {"pv": _bd.get("price_velocity",0), "la": _bd.get("liquidity_acceleration",0), "cv": _bd.get("curve_velocity",0)}
                _dk = max(_fac, key=_fac.get)
                if _fac[_dk] > 0.1:
                    _dom = f"{_dk}={_fac[_dk]:.2f}"
            except Exception: pass
            # Visual tier
            if bomb:
                bg,bc,anim,op,rc = "rgba(255,7,58,0.08)","#FF073A","animation:bombIgnite 0.9s ease-in-out infinite;","1.0","#FF073A"
                rlabel,nc = f"BOMB {mom:.3f}","#FF073A"
            elif tier == "HOT" and mom > 0.3:
                bg,bc,anim,op,rc = "rgba(20,241,149,0.06)","#14F195","animation:hotPulse 2s ease-in-out infinite;","1.0","#14F195"
                rlabel,nc = f"↑ {mom:.3f}","#FFF"
            elif tier in ("WARM","HOT"):
                bg,bc,anim,op,rc = "rgba(255,215,0,0.04)","#FFD700","","0.85","#FFD700"
                rlabel,nc = f"  {mom:.3f}","#DDD"
            else:
                bg,bc,anim,op,rc = "rgba(5,2,16,0.3)","#2a2a2a","animation:recede 4s ease-in-out infinite;","0.38","#333"
                rlabel,nc = f"  {mom:.3f}","#555"
            bw = max(2, int(mom * 58))
            ls = f"{chr(36)}{liq/1000:.0f}k" if liq >= 1000 else (f"{chr(36)}{liq:.0f}" if liq > 0 else "-")
            sb = "”’" if state == "latched" else "·"
            rows_html += (
                f'<div style="display:grid;grid-template-columns:2fr 70px 48px 54px 48px 1fr;'
                f'gap:3px;align-items:center;padding:4px 6px;margin-bottom:2px;'
                f'border-radius:4px;border-left:3px solid {bc};background:{bg};opacity:{op};{anim}">'
                f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{nc};'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{sb} {token}</span>'
                f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{rc};">'
                f'{rlabel}<span style="display:block;height:2px;width:{bw}px;background:{rc};'
                f'border-radius:1px;margin-top:1px;opacity:0.65;"></span></span>'
                f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#555;">{curve:.0f}%</span>'
                f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#444;">{ls}</span>'
                f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#444;">{age:.0f}s</span>'
                f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#333;'
                f'overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">{_html.escape(_dom)}</span>'
                f'</div>'
            )
        except Exception:
            continue

    st.markdown(_anim_css + _col_header + rows_html, unsafe_allow_html=True)
    st.markdown("</div>", unsafe_allow_html=True)


def render_api_status_bar() -> None:
    """Grand Vision header using canonical runtime model assignments."""
    import os

    # Never allow a missing/changed assignment table to crash the whole UI.
    # get_agent_status_snapshot() already handles absent tables and old/new schemas.
    try:
        _agents = get_agent_status_snapshot() or {}
    except Exception:
        _agents = {}

    def _assigned(name: str, fallback: str) -> str:
        try:
            value = str((_agents.get(name.upper()) or {}).get("current_model") or "").strip()
            return value if value and value != "-" else fallback
        except Exception:
            return fallback

    COUNCIL = [
        ("POLARIS",  "Polar",   "Architect",  _assigned("POLARIS", "gpt-5.4-mini"), "OPENAI_API_KEY",     "#8EF9FF"),
        ("IVARIS",   "Ivy",     "Critic",     _assigned("IVARIS", "UNASSIGNED"),    "NVIDIA_NIM_API_KEY", "#FFB347"),
        ("NUGGET",   "Nugget",  "Auditor",    _assigned("NUGGET", "UNASSIGNED"),    "NVIDIA_NIM_API_KEY", "#FFD700"),
        ("GROK",     "Rhiza",   "Integrator", _assigned("GROK", "grok-3"),          "XAI_API_KEY",        "#FF073A"),
        ("AXIOM",    "NIM",     "Library",    _assigned("AXIOM", "UNASSIGNED"),     "NVIDIA_NIM_API_KEY", "#76B900"),
        ("GOVERNOR", "Local",   "Gate",       "orchestrator",                       "",                   "#9945FF"),
    ]
    SENSES = [
        ("X SCOUT",  "TWITTER_BEARER_TOKEN", "#1DA1F2"),
        ("BRAVE",    "BRAVE_SEARCH_API_KEY", "#FF6B35"),
        ("TELEGRAM", "TELEGRAM_BOT_TOKEN",   "#2CA5E0"),
    ]

    agent_html = ""
    for code, origin, role, model, key, col in COUNCIL:
        ok = bool(os.getenv(key, "")) if key else True
        dot = col if ok else "#2a2a2a"
        glow = f"0 0 8px {col}88" if ok else "none"
        agent_html += (
            "<div style='display:inline-flex;flex-direction:column;align-items:center;"
            "justify-content:center;gap:1px;min-width:74px;flex:0 0 auto;'>"
            "<div style='display:flex;align-items:center;gap:4px;white-space:nowrap;'>"
            f"<span style='width:6px;height:6px;border-radius:50%;background:{dot};box-shadow:{glow};flex-shrink:0;'></span>"
            f"<span style='font-family:Orbitron,sans-serif;font-size:0.66rem;letter-spacing:1.5px;color:{col if ok else chr(35)+'333'};font-weight:700;'>{code}</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:{col}66;letter-spacing:.8px;'>· {origin}</span>"
            "</div>"
            f"<span style='font-size:0.66rem;color:#444;font-family:Share Tech Mono,monospace;letter-spacing:.8px;white-space:nowrap;'>{role} · {model[:13]}</span>"
            "</div>"
        )

    sense_html = ""
    for name, key, col in SENSES:
        ok = bool(os.getenv(key, ""))
        dot = col if ok else "#2a2a2a"
        # Special handling for BRAVE - show daily usage counter
        if name == "BRAVE" and ok:
            try:
                # BUGFIX_20260718: get_config_value was never imported in this
                # module, so this block always raised NameError and the daily
                # BRAVE quota counter silently never displayed.
                from services.schema import get_config_value as _gcv_brave
                _brave_used = int(_gcv_brave("BRAVE_SEARCHES_TODAY", 0) or 0)
                _brave_hit = _brave_used >= 900
                dot = "#FF073A" if _brave_hit else col
                _blink = "animation:pulse 1s infinite;" if _brave_hit else ""
                sense_html += (
                    f"<span style='display:inline-flex;align-items:center;gap:4px;"
                    f"font-size:0.66rem;font-family:Share Tech Mono,monospace;letter-spacing:1px;"
                    f"white-space:nowrap;flex:0 0 auto;color:{dot};'>"
                    f"<span style='width:5px;height:5px;border-radius:50%;background:{dot};{_blink}'></span>BRAVE</span>"
                )
                continue
            except Exception:
                pass
        sense_html += (
            "<span style='display:inline-flex;align-items:center;gap:4px;"
            "font-size:0.66rem;font-family:Share Tech Mono,monospace;letter-spacing:1px;"
            "white-space:nowrap;flex:0 0 auto;"
            f"color:{col if ok else chr(35)+'333'};'>"
            f"<span style='width:5px;height:5px;border-radius:50%;background:{dot};'></span>{name}</span>"
        )

    st.markdown(
        "<div style='padding:8px 14px;background:rgba(5,2,16,0.88);"
        "border-bottom:1px solid rgba(153,69,255,0.22);display:flex;align-items:center;"
        "gap:10px;flex-wrap:nowrap;white-space:nowrap;overflow-x:auto;overflow-y:hidden;'>"
        "<span style='font-size:0.66rem;color:#333;font-family:Share Tech Mono,monospace;"
        "letter-spacing:3px;margin-right:4px;padding-right:10px;border-right:1px solid rgba(255,255,255,0.08);flex:0 0 auto;'>COUNCIL</span>"
        f"{agent_html}"
        "<div style='margin-left:auto;display:inline-flex;align-items:center;gap:8px;flex:0 0 auto;"
        "padding-left:12px;border-left:1px solid rgba(255,255,255,0.08);'>"
        "<span style='font-size:0.66rem;color:#333;font-family:Share Tech Mono,monospace;letter-spacing:3px;'>SENSES</span>"
        f"{sense_html}"
        "</div>"
        "</div>",
        unsafe_allow_html=True,
    )


def render_organism_pressure_core() -> None:
    """
    DOMINANT organism nervous system meter - full width, above execution arena.
    6 animated bars: conviction, exec flow, market resonance, evolution energy,
    research heat, live risk. CSS glow + breathing only - no canvas.
    """
    import time as _t, sqlite3 as _sq
    _cfg = {}; _latch_ok = False; _open = 0; _wr = 0; _halt = False
    _debate_rounds = 0; _x_posts = 0; _proposals_open = 0
    _best_mult = 0.0; _best_roi = 0.0
    try:
        _db = _sq.connect(str(DB_PATH), timeout=2.0); _db.row_factory = _sq.Row
        _cfg = {str(r["key"]): str(r["value"]) for r in _db.execute(
            "SELECT key, value FROM system_config").fetchall()}
        _latch_ok = bool(_db.execute(
            "SELECT 1 FROM market_snapshots WHERE candidate_state='latched' LIMIT 1").fetchone())
        _open = int((_db.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone() or [0])[0])
        # Gold state: best open runner (existing meter, no new bar)
        _best_mult = 0.0; _best_roi = 0.0
        try:
            for _rr in _db.execute(
                "SELECT entry_price, COALESCE(last_price,current_price,entry_price) lp,"
                " COALESCE(highest_price_seen,0) pk"
                " FROM paper_positions WHERE status='OPEN' AND entry_price>0"
            ).fetchall():
                _e=float(_rr[0] or 0); _l=float(_rr[1] or 0); _pk=float(_rr[2] or 0)
                if _e>0:
                    _m=max(_l,_pk)/_e
                    if _m>_best_mult: _best_mult=_m
                    _r=(_l-_e)/_e*100
                    if _r>_best_roi: _best_roi=_r
        except Exception: pass
        _rev = _db.execute(
            "SELECT win_loss FROM polaris_trade_reviews ORDER BY id DESC LIMIT 30").fetchall()
        if len(_rev) >= 3:
            _wins = sum(1 for r in _rev if str(r[0] if not hasattr(r,"keys") else r["win_loss"]).upper()=="WIN")
            _wr = round(_wins/len(_rev)*100, 1)
        _halt = _cfg.get("DRAWDOWN_HALT_ACTIVE","0") in ("1","true","True")
        _debate_rounds = int((_db.execute(
            "SELECT COUNT(*) FROM debate_log").fetchone() or [0])[0])
        _proposals_open = int((_db.execute(
            "SELECT COUNT(*) FROM polaris_proposals WHERE status IN ('open','debating','pending_replay')").fetchone() or [0])[0])
        try:
            _x_posts = int((_db.execute(
                "SELECT COUNT(*) FROM intelligence_forge WHERE stage='X_SCOUT'").fetchone() or [0])[0])
        except Exception: _x_posts = 0
        _db.close()
    except Exception: pass

    _max_pos = int(_cfg.get("MAX_OPEN_POSITIONS","3"))
    _min_conf = float(_cfg.get("SUPERVISOR_MIN_MINT_CONFIDENCE","0.75"))

    # Calculate 6 pressure signals
    conviction      = min(100, int(sum([_latch_ok, _wr>=40, not _halt, _open>0])/4*100))
    exec_flow       = min(100, int(_open/_max_pos*100)) if _max_pos else 0
    mkt_resonance   = min(100, int(_min_conf*100 + (10 if _latch_ok else 0)))
    evolution_energy= min(100, min(100, _debate_rounds//5 + _proposals_open*15))
    research_heat   = min(100, min(100, _x_posts//3 + _debate_rounds//10))
    live_risk       = min(100, int(_open/_max_pos*100 + (20 if _halt else 0))) if _max_pos else 0

    # Gold state MUST be calculated before bars - fixes the reference-before-assignment bug
    _gold_state = (_best_mult >= 10.0) or (_best_roi >= 75.0)
    if _gold_state:
        conviction = 100
    # DOCTRINE_RECOLOUR_20260624: gold dominant ONLY when a real runner is earned.
    # Otherwise green (high conviction) / purple (evolving) / neutral.
    dominant_col = (
        SENTINUITY_COLORS["gold"] if _gold_state
        else (SENTINUITY_COLORS["green"] if conviction >= 60
              else (SENTINUITY_COLORS["purple"] if evolution_energy >= 40 else "#555"))
    )

    _C = SENTINUITY_COLORS
    # DOCTRINE_RECOLOUR_20260624: one hue per bar, doctrine-mapped. No orange,
    # no gold base. Gold appears only as an earned tip (pinned at max) below.
    bars = [
        ("CONVICTION" + (" ⚡ RADIANT" if _gold_state else ""), conviction, _C["green"]),
        ("EXECUTION FLOW",   exec_flow,        _C["cyan"]),
        ("MARKET RESONANCE", mkt_resonance,    _C["purple"]),
        ("EVOLUTION ENERGY", evolution_energy, _C["blue"]),
        ("RESEARCH HEAT",    research_heat,    _C["cyan"]),
        ("LIVE RISK",        live_risk,        _C["red"]),
    ]

    # HOLO_CRYSTAL_METERS_20260612: gold-tinted liquid-glass tracks, doctrine
    # gold at >=75 for conviction-type meters, prism sheen crest on every fill.
    # DOCTRINE_RECOLOUR_20260624: single-hue fills on a void track. The fill no
    # longer ends in gold; gold appears ONLY as a small earned tip when a bar
    # pins at max (or when the earned _gold_state crowns conviction). The old
    # gold-tinted track border + gold/purple/cyan rainbow sheen are removed.
    bar_rows = ""
    _gold_keys = ("CONVICTION", "CONFIDENCE")
    for label, pct, col in bars:
        _earned_crown = (_gold_state and any(k in label for k in _gold_keys))
        _pinned = pct >= SENTINUITY_GOLD_PIN_PCT
        _show_gold_tip = _earned_crown or _pinned
        _val_col = SENTINUITY_COLORS["gold"] if _show_gold_tip else col
        _gold_tip = (
            f"<div style='position:absolute;right:0;top:0;height:100%;width:12%;"
            f"background:{SENTINUITY_COLORS['gold']};"
            f"box-shadow:0 0 6px {SENTINUITY_COLORS['gold']}99;'></div>"
            if _show_gold_tip else ""
        )
        bar_rows += (
            f"<div style='margin-bottom:11px;'>"
            f"<div style='display:flex;justify-content:space-between;"
            f"font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"letter-spacing:2px;color:#8A9890;margin-bottom:4px;'>"
            f"<span>{label}</span>"
            f"<span style='color:{_val_col};font-weight:700;'>{pct}%"
            f"{' ◇' if _show_gold_tip else ''}</span></div>"
            f"<div style='position:relative;height:9px;border-radius:6px;overflow:hidden;"
            f"background:{SENTINUITY_COLORS['void']};"
            f"border:1px solid rgba(255,255,255,.08);"
            f"box-shadow:inset 0 0 8px rgba(0,0,0,.6);'>"
            f"<div style='height:100%;width:{pct}%;border-radius:6px;"
            f"background:{col};"
            f"box-shadow:0 0 8px {col}88;"
            f"animation:pressBreath 3s ease-in-out infinite;'></div>"
            f"{_gold_tip}"
            f"</div></div>"
        )

    html_out = (
        f"<style>"
        f"@keyframes pressBreath{{0%,100%{{opacity:.9}}50%{{opacity:1}}}}"
        f"@keyframes holoSheen{{0%{{transform:translateX(-140%) skewX(-18deg)}}100%{{transform:translateX(420%) skewX(-18deg)}}}}"
        f"@keyframes coreGlow{{0%,100%{{box-shadow:0 0 20px {dominant_col}22,inset 0 0 30px {dominant_col}08}}"
        f"50%{{box-shadow:0 0 40px {dominant_col}44,inset 0 0 40px {dominant_col}12}}}}"
        f"@keyframes sovGold{{0%{{filter:brightness(1.0)}}50%{{filter:brightness(1.55)}}100%{{filter:brightness(1.0)}}}}"
        f"</style>"
        f"<div style='padding:16px 20px;border-radius:16px;margin:12px 0 14px;"
        f"background:linear-gradient(115deg,rgba(153,69,255,.05),rgba(8,14,11,.85) 60%),rgba(6,4,16,.92);"
        f"backdrop-filter:blur(8px) saturate(1.15);-webkit-backdrop-filter:blur(8px) saturate(1.15);"
        f"border:1.4px solid {dominant_col}44;"
        f"box-shadow:inset 0 1px 0 rgba(255,255,255,.06),0 0 22px {dominant_col}33;"
        f"animation:coreGlow 4s ease-in-out infinite{', sovGold 1.8s ease-in-out infinite' if _gold_state else ''};'>"
        f"<div style='font-family:Orbitron,sans-serif;font-size:.72rem;letter-spacing:5px;"
        f"color:{dominant_col};margin-bottom:14px;"
        f"text-shadow:0 0 12px {dominant_col}88;'>"
        f"◈ ORGANISM PRESSURE CORE{_holoq('pressure_core')}</div>"
        f"<div style='display:grid;grid-template-columns:1fr 1fr;gap:0 24px;'>"
        f"{bar_rows}"
        f"</div>"
        f"<div style='margin-top:10px;display:flex;gap:16px;flex-wrap:wrap;'>"
        f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#444;'>"
        f"DEBATE ROUNDS {_debate_rounds}</span>"
        f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#444;'>"
        f"X POSTS {_x_posts}</span>"
        f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#444;'>"
        f"PROPOSALS ACTIVE {_proposals_open}</span>"
        f"</div>"
        f"</div>"
    )
    st.markdown(html_out, unsafe_allow_html=True)

def render_holographic_ascension_map() -> None:
    """
    THE ASCENSION PATH - horizontal glowing node timeline.
    Maps one proposal from SEED → RESEARCH → DEBATE → SIMULATE → PAPER → DEPLOY → LATTICE → SOVEREIGN
    Active node glows. HITL node pulses gold. Blocked node red.
    """
    import time as _t, sqlite3 as _sq, html as _html

    NODES = [
        ("SEED",     "🌱", "Genesis"),
        ("RESEARCH", "🔭", "X/Brave"),
        ("DEBATE",   "⚖️",  "Council"),
        ("SIMULATE", "🧪", "Replay"),
        ("PAPER",    "📋", "Validate"),
        ("DEPLOY",   "⚡", "Live"),
        ("LATTICE",  "⬡",  "Absorb"),
        ("SOVEREIGN","◈",  "Evolved"),
    ]

    # Read current proposal state
    active_stage = "SEED"; hitl_id = None; hitl_cmd = ""; blocker = ""
    total_props = 0; approved = 0; debating = 0; blocked_count = 0
    try:
        _db = _sq.connect(str(DB_PATH), timeout=2.0); _db.row_factory = _sq.Row
        _props = _db.execute(
            "SELECT id, status, proposal_type, proposal_text FROM polaris_proposals ORDER BY updated_at DESC LIMIT 30"
        ).fetchall()
        total_props = len(_props)
        for p in _props:
            s = str(p["status"] or "")
            if s == "approved": approved += 1
            elif s in ("open","debating","pending_replay"): debating += 1
            elif s in ("nugget_escalated","HITL_REQUIRED"):
                hitl_id = p["id"]; hitl_cmd = f"python approve_proposal.py {p['id']}"
                active_stage = "LATTICE"
            elif s in ("critic_unavailable","rejected_by_ivaris"): blocked_count += 1
        # Determine stage from state
        if hitl_id:                                   active_stage = "LATTICE"
        elif approved > 0 and debating == 0:          active_stage = "SOVEREIGN"
        elif debating > 0 and blocked_count == 0:     active_stage = "DEBATE"
        elif blocked_count > total_props * 0.5:       active_stage = "RESEARCH"
        elif total_props == 0:                        active_stage = "SEED"
        else:                                         active_stage = "PAPER"
        # Blocker message
        _bl = _db.execute(
            "SELECT message FROM cognition_log WHERE stage='BUILD_BLOCKER' ORDER BY ts DESC LIMIT 1"
        ).fetchone()
        if _bl: blocker = str(_bl["message"] or "")[:80]
        _db.close()
    except Exception: pass

    active_idx = next((i for i,n in enumerate(NODES) if n[0]==active_stage), 0)

    # Build CSS + HTML as one atomic string
    html_parts = ["""
<style>
@keyframes nodeGlow{0%,100%{box-shadow:0 0 12px currentColor,0 0 24px currentColor}50%{box-shadow:0 0 24px currentColor,0 0 48px currentColor}}
@keyframes hitlPulse{0%,100%{box-shadow:0 0 16px #FFD700,0 0 32px #FFD700;transform:scale(1)}50%{box-shadow:0 0 32px #FFD700,0 0 64px #FFD700;transform:scale(1.08)}}
@keyframes connectorFlow{0%{background-position:0% 50%}100%{background-position:200% 50%}}
.asc-wrap{padding:14px 16px;background:rgba(5,2,16,0.85);border:1px solid rgba(153,69,255,0.25);border-radius:12px;margin-bottom:12px;overflow:hidden}
.asc-title{font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:4px;color:#9945FF;margin-bottom:14px}
.asc-row{display:flex;align-items:center;justify-content:flex-start;flex-wrap:nowrap;overflow-x:auto;gap:0;padding-bottom:8px}
.asc-row::-webkit-scrollbar{height:3px}.asc-row::-webkit-scrollbar-thumb{background:#1a1a2e}
.asc-node{display:flex;flex-direction:column;align-items:center;gap:4px;flex-shrink:0;min-width:60px}
.asc-circle{width:36px;height:36px;border-radius:50%;display:flex;align-items:center;justify-content:center;font-size:1rem;border:2px solid currentColor;transition:.4s}
.asc-label{font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;text-align:center;line-height:1.3}
.asc-sub{font-family:Share Tech Mono,monospace;font-size:0.66rem;opacity:.5;text-align:center}
.asc-conn{flex:1;height:2px;min-width:12px;background:linear-gradient(90deg,currentColor 0%,transparent 100%);opacity:.35;align-self:center;margin:0 2px;margin-bottom:18px}
.asc-conn.active{opacity:1;background:linear-gradient(90deg,var(--prev-col),var(--next-col));background-size:200%;animation:connectorFlow 2s linear infinite}
</style>"""]

    html_parts.append('<div class="asc-wrap">')
    html_parts.append(f'<div class="asc-title">⬡ HOLOGRAPHIC ASCENSION PATH &nbsp;·&nbsp; '
                      f'<span style="color:#555;font-size:0.66rem;">'
                      f'{approved} absorbed · {debating} active · {blocked_count} blocked'
                      f'</span></div>')
    html_parts.append('<div class="asc-row">')

    for i, (stage, glyph, sub) in enumerate(NODES):
        is_active = (i == active_idx)
        is_done   = (i < active_idx)
        is_hitl   = (stage == "LATTICE" and hitl_id)
        is_blocked= (stage == "DEBATE" and blocked_count > debating and not is_done)

        if is_hitl:
            col = "#FFD700"; anim = "hitlPulse 1.2s ease-in-out infinite"
            bg  = "rgba(255,215,0,0.15)"
        elif is_done:
            col = "#14F195"; anim = "none"; bg = "rgba(20,241,149,0.08)"
        elif is_active:
            col = "#9945FF"; anim = "nodeGlow 2.5s ease-in-out infinite"; bg = "rgba(153,69,255,0.12)"
        elif is_blocked:
            col = "#FF073A"; anim = "nodeGlow 3s ease-in-out infinite"; bg = "rgba(255,7,58,0.08)"
        else:
            col = "#2a2a2e"; anim = "none"; bg = "transparent"

        label_col = col if (is_active or is_done or is_hitl) else "#444"
        done_tick = " ✓" if is_done else ""

        html_parts.append(
            f'<div class="asc-node">'
            f'<div class="asc-circle" style="color:{col};background:{bg};'
            f'animation:{anim};border-color:{col};">{glyph}</div>'
            f'<div class="asc-label" style="color:{label_col};">{stage}{done_tick}</div>'
            f'<div class="asc-sub" style="color:{label_col};">{sub}</div>'
            f'</div>'
        )
        if i < len(NODES) - 1:
            conn_active = is_done or is_active
            conn_col    = "#14F195" if is_done else ("#9945FF" if is_active else "#1a1a2e")
            conn_cls    = "asc-conn active" if conn_active else "asc-conn"
            html_parts.append(
                f'<div class="{conn_cls}" style="color:{conn_col};'
                f'--prev-col:{conn_col};--next-col:{col};"></div>'
            )

    html_parts.append('</div>')  # asc-row

    # HITL gate message
    if hitl_id:
        html_parts.append(
            f'<div style="margin-top:10px;padding:8px 12px;background:rgba(255,215,0,0.06);'
            f'border:1px solid rgba(255,215,0,0.4);border-radius:8px;'
            f'font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#FFD700;">'
            f'🟠 SOVEREIGN SEAL REQUIRED - proposal #{hitl_id}<br>'
            f'<span style="color:#888;">{hitl_cmd}</span></div>'
        )
    elif blocker:
        html_parts.append(
            f'<div style="margin-top:8px;padding:6px 10px;background:rgba(255,7,58,0.06);'
            f'border-left:3px solid #FF073A;border-radius:4px;'
            f'font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#FF073A;">'
            f'⚠ {_html.escape(blocker)}</div>'
        )

    html_parts.append('</div>')  # asc-wrap
    st.markdown("".join(html_parts), unsafe_allow_html=True)


def render_golden_lattice() -> None:
    """
    THE GOLDEN LATTICE - Evolution Chamber.
    Shows proposals awaiting sovereign absorption (approved / HITL_REQUIRED).
    Sacred black/gold panel. This is the destination of the ascension path.
    """
    import sqlite3 as _sq, html as _html
    try:
        _db = _sq.connect(str(DB_PATH), timeout=2.0); _db.row_factory = _sq.Row
        _ready = _db.execute("""
            SELECT id, proposal_type, proposal_text, status, applied_value, notes
            FROM polaris_proposals
            WHERE status IN ('approved','nugget_escalated','HITL_REQUIRED','pending_replay')
            ORDER BY updated_at DESC LIMIT 5
        """).fetchall()
        _db.close()
    except Exception:
        _ready = []

    if not _ready:
        return  # Nothing to show - map hasn't reached lattice yet

    lattice_html = ["""
<style>
@keyframes latticeBreath{0%,100%{box-shadow:0 0 20px rgba(255,215,0,0.15),inset 0 0 20px rgba(255,215,0,0.03)}
50%{box-shadow:0 0 40px rgba(255,215,0,0.3),inset 0 0 30px rgba(255,215,0,0.06)}}
@keyframes glyphSpin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
</style>
<div style="padding:14px 16px;background:rgba(5,2,16,0.95);
border:1px solid rgba(255,215,0,0.4);border-radius:12px;
animation:latticeBreath 4s ease-in-out infinite;margin-bottom:12px;">
<div style="font-family:Orbitron,sans-serif;font-size:0.66rem;letter-spacing:4px;
color:#FFD700;margin-bottom:10px;display:flex;align-items:center;gap:8px;">
<span style="animation:glyphSpin 8s linear infinite;display:inline-block;">◈</span>
GOLDEN LATTICE - EVOLUTION CHAMBER
</div>"""]

    for p in _ready:
        sid  = p["id"]
        ptype= str(p["proposal_type"] or "")
        txt  = str(p["proposal_text"] or "")[:100]
        stat = str(p["status"] or "")
        notes= str(p["notes"] or "")[:60]
        is_hitl = stat in ("nugget_escalated","HITL_REQUIRED")
        border_col = "#FFD700" if is_hitl else "#14F195"
        stat_label = "⚡ AWAITING SEAL" if is_hitl else "✅ APPROVED"

        lattice_html.append(
            f'<div class="shine-lattice" style="margin-bottom:8px;padding:8px 12px;'
            f'border-left:3px solid {border_col};background:rgba(255,215,0,0.04);border-radius:4px;">'
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;'
            f'color:{border_col};letter-spacing:2px;margin-bottom:4px;">'
            f'{stat_label} &nbsp;·&nbsp; #{sid} &nbsp;·&nbsp; {_html.escape(ptype[:20])}</div>'
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#aaa;">'
            f'{_html.escape(txt)}</div>'
        )
        if notes:
            lattice_html.append(
                f'<div style="font-size:0.66rem;color:#666;margin-top:3px;">{_html.escape(notes)}</div>'
            )
        if is_hitl:
            lattice_html.append(
                f'<div style="font-size:0.66rem;color:#FFD700;margin-top:4px;">'
                f'◈ SOVEREIGN SEAL REQUIRED - enter approval code below</div>'
            )
        lattice_html.append('</div>')

    lattice_html.append('</div>')
    st.markdown("".join(lattice_html), unsafe_allow_html=True)

    # Inline approval UI for HITL proposals
    _hitl_proposals = [p for p in _ready if str(p["status"] or "") in ("nugget_escalated","HITL_REQUIRED")]
    if _hitl_proposals:
        st.markdown(
            "<div style='border:1px solid rgba(255,215,0,0.3);border-radius:8px;"
            "padding:12px 14px;background:rgba(255,215,0,0.04);margin-top:8px;'>",
            unsafe_allow_html=True
        )
        for _hp in _hitl_proposals[:3]:
            _hp_id = _hp["id"]
            _hp_type = str(_hp["proposal_type"] or "")[:30]
            st.markdown(
                f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
                f"color:#FFD700;letter-spacing:2px;margin-bottom:6px;'>"
                f"◈ PROPOSAL #{_hp_id} - {_html.escape(_hp_type)}</div>",
                unsafe_allow_html=True
            )
            _col1, _col2 = st.columns([3,1])
            with _col1:
                _code = st.text_input(
                    "Sovereign Seal Code",
                    key=f"lattice_code_{_hp_id}",
                    placeholder="Enter approval code...",
                    label_visibility="collapsed"
                )
            with _col2:
                if st.button("◈ SEAL", key=f"lattice_seal_{_hp_id}", use_container_width=True):
                    if _code and len(_code) >= 4:
                        try:
                            import sqlite3 as _sq2
                            _db2 = _sq2.connect(str(DB_PATH), timeout=2.0)
                            _db2.execute(
                                "UPDATE polaris_proposals SET status='approved', "
                                "notes=? WHERE id=? AND status IN ('HITL_REQUIRED','nugget_escalated')",
                                (f"SEALED_BY_OPERATOR code={_code[:8]}", _hp_id)
                            )
                            _db2.commit(); _db2.close()
                            st.success(f"◈ Proposal #{_hp_id} sealed - Golden Lattice advancing")
                        except Exception as _se:
                            st.error(f"Seal failed: {_se}")
                    else:
                        st.warning("Enter a valid approval code")
        st.markdown("</div>", unsafe_allow_html=True)


# render_council_build_map() removed 2026-05-26 - dead wrapper, never invoked.
# It called both render_holographic_ascension_map() and render_golden_lattice(),
# both of which are now invoked once each at page level (lines ~6068 and ~6073).
# Keeping the dead wrapper around was a latent landmine: any future caller would
# have caused both panels to render twice. The audit that flagged "duplication"
# in screenshots was misdiagnosed at the tab-container level - the real risk was
# this wrapper. Removing it closes that loop.


def _measure_live_latency():
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=2.0)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE IF NOT EXISTS _dash_probe (ts INTEGER)")
        start = time.monotonic()
        conn.execute("INSERT INTO _dash_probe VALUES (?)", (int(time.time()),))
        conn.commit()
        conn.execute("DELETE FROM _dash_probe")
        conn.commit()
        ms = (time.monotonic() - start) * 1000
        conn.close()
        return ms
    except Exception: return 0.0

FOCUS_LOCK_TYPES    = {"SYSTEM_REPAIR"}
FOCUS_LOCK_STATUSES = {"open", "debating", "pending", "debate", "approved"}

def _is_focus_locked(proposals_df) -> bool:
    if proposals_df is None or proposals_df.empty: return False
    try:
        return not proposals_df[
            (proposals_df["proposal_type"].str.upper().isin(FOCUS_LOCK_TYPES)) &
            (proposals_df["status"].str.lower().isin(FOCUS_LOCK_STATUSES))
        ].empty
    except Exception: return False

def _apply_focus_lock(proposals_df):
    if proposals_df is None or proposals_df.empty: return proposals_df
    try:
        if not _is_focus_locked(proposals_df): return proposals_df
        repair_mask = proposals_df["proposal_type"].str.upper().isin(FOCUS_LOCK_TYPES)
        return pd.concat([proposals_df[repair_mask], proposals_df[~repair_mask].head(4)], ignore_index=True)
    except Exception: return proposals_df

def render_genesis_vault() -> None:
    """Lazy-loaded genesis vault - extracted to ui/genesis_vault.py to reduce hub parse time."""
    try:
        from ui.genesis_vault import render_genesis_vault as _gv
        _gv()
    except Exception as e:
        st.error(f"Genesis vault unavailable: {e}")

def _render_sovereign_world_html() -> str:
    # FENCED - WORLD_MODE_CANONICALIZATION_20260621
    # The legacy inline world (old battlefield/canopy) is permanently disabled.
    # World Mode now mounts ONLY the canonical ui/sovereign_world.html. This
    # guard guarantees no code path can resurrect an old world via this
    # function; the body below is retained as dead reference only.
    return ""
    import json as _swj, sqlite3 as _swsq
    _pos, _tel, _cruc = [], [], "IDLE"
    try:
        _db = _swsq.connect(str(DB_PATH), timeout=2)
        _db.row_factory = _swsq.Row
        for row in _db.execute("SELECT token_name, entry_price, exit_price, status FROM paper_positions ORDER BY COALESCE(closed_at,opened_at) DESC LIMIT 5").fetchall():
            ep=float(row["entry_price"] or 0); xp=float(row["exit_price"] or ep)
            pct=((xp-ep)/ep*100) if ep>0 else 0
            _pos.append({"token":str(row["token_name"] or "?")[:8],"pct":round(pct,1),"status":str(row["status"] or "")})
        for row in _db.execute("SELECT stage, message FROM cognition_log ORDER BY timestamp DESC LIMIT 8").fetchall():
            col={"EXECUTOR":"#14F195","SUPERVISOR":"#8EF9FF","POLARIS":"#8EF9FF","IVARIS":"#FFB347","GUARDIAN":"#FFD700"}.get(str(row["stage"] or "").upper(),"#9945FF")
            _tel.append({"text":f'[{row["stage"]}] {str(row["message"] or "")[:55]}',"col":col})
        if _db.execute("SELECT 1 FROM polaris_proposals WHERE status IN ('debating','open') LIMIT 1").fetchone():
            _cruc="DISTILLING"
        _db.close()
    except Exception:
        pass
    # Get tax reserve and trading mode for liberation
    _tax_res = 0.0
    _lib_ready = False
    try:
        _db2 = _swsq.connect(str(DB_PATH), timeout=2)
        _tr = _db2.execute("SELECT value FROM system_config WHERE key='TAX_RESERVE_USD'").fetchone()
        _tax_res = round(float(_tr[0]) if _tr else 0.0, 2)
        _tm = _db2.execute("SELECT value FROM system_config WHERE key='TRADING_MODE'").fetchone()
        _lib_ready = str(_tm[0] if _tm else 'paper').lower() in ('live',)
        _db2.close()
    except Exception:
        pass
    return (_SW_WORLD_HTML
        .replace("__POS_DATA__", _swj.dumps(_pos))
        .replace("__TEL_DATA__", _swj.dumps(_tel))
        .replace("__CRUC_STATE__", _cruc)
        .replace("__TAX_RESERVE__", str(_tax_res))
        .replace("__LIBERATION_READY__", "true" if _lib_ready else "false"))




# SIGNOFF_UI_20260714 — council transcript first, mobile-first de-clutter.
st.markdown("""<style>
.snty-sanctum-title{font-family:Orbitron,sans-serif;font-size:.72rem;letter-spacing:3px;color:#c9a7ff;margin:14px 0 7px;border-bottom:1px solid rgba(153,69,255,.18);padding-bottom:7px}
.snty-debate-stage{scrollbar-width:thin;scrollbar-color:#9945ff55 transparent}
.snty-debate-turn{border-radius:2px;box-shadow:none;transition:border-color .15s ease;background-clip:padding-box}
.snty-debate-turn:hover{border-color:#8ef9ff!important}
@media(max-width:900px){
  .snty-debate-turn{margin-left:0!important;margin-right:0!important;width:auto!important}
  .snty-debate-stage{max-height:520px!important;padding:8px 2px!important}
  .snty-flow-row{overflow-x:auto;justify-content:flex-start!important;padding-bottom:7px}
  .snty-flow-node{min-width:118px}
  .snty-stat-grid{grid-template-columns:1fr!important}
}
</style>""", unsafe_allow_html=True)

def render_living_cortex():
    # No outer cache - _fetch_all_dashboard_data(ttl=5) is the cache layer.
    # Previously wrapped in @st.cache_data(ttl=30) which caused meter bars and
    # main render sections to show data up to 30s stale even when the inner
    # bundle had refreshed. Removed to achieve unified organism-state rendering.
    _prices    = fetch_coingecko_prices()
    _sol_price = _prices.get("SOL", {}).get("price_usd", 0.0)
    _sol_chg   = _prices.get("SOL", {}).get("change_24h", 0.0)
    
    (wallet_df, raw_dna_df, snapshots_df, open_pos_df, executions_df, reviews_df, proposals_df, debate_df, calibration_df, open_count_df, heal_log_df, heartbeat_df, patch_history_df, autopsy_df, cognition_df) = _fetch_all_dashboard_data()
    _MODEL_TAGS = {
        "POLARIS": ("gpt-5.4-mini",     "tag-polaris"),
        "IVARIS":  ("deepseek-v4-flash",     "tag-ivaris"),
        "NUGGET":  ("kimi-k2-instruct", "tag-nugget"),
        "ORACLE":  ("brave",            "tag-oracle"),
    }
    live_feed_df = build_live_event_feed(cognition_df, executions_df, open_pos_df, snapshots_df, proposals_df)

    # SIGNOFF_COUNCIL_TELEMETRY_RESILIENCE_20260715
    # _fetch_all_dashboard_data() historically returned fifteen empty frames when
    # ANY optional panel query failed. That made both the Debate Chamber and the
    # developer feed look empty even while debate_log/cognition_log contained
    # valid rows. Recover these two critical surfaces independently.
    if debate_df is None or debate_df.empty:
        try:
            debate_df = query_db(
                "SELECT speaker, "
                "COALESCE(json_extract(content_json,'$.verdict'),"
                "json_extract(content_json,'$.summary'),"
                "json_extract(content_json,'$.rebuttal_summary'),"
                "message,action,'') AS verdict_text, "
                "thinking_state, verdict_type, approved_by, logged_at, "
                "content_json, COALESCE(proposal_id,NULL) AS proposal_id "
                "FROM debate_log ORDER BY logged_at DESC LIMIT 40"
            )
        except Exception:
            try:
                debate_df = query_db(
                    "SELECT speaker, COALESCE(message,action,'') AS verdict_text, "
                    "thinking_state, verdict_type, approved_by, logged_at, "
                    "content_json, proposal_id FROM debate_log "
                    "ORDER BY logged_at DESC LIMIT 40"
                )
            except Exception:
                debate_df = pd.DataFrame()

    if cognition_df is None or cognition_df.empty:
        try:
            cognition_df = query_db(
                "SELECT id, timestamp AS sort_ts, timestamp, "
                "COALESCE(stage,'SYSTEM') AS stage, COALESCE(token,'') AS token, "
                "COALESCE(message,'') AS message, COALESCE(confidence,0.0) AS confidence "
                "FROM cognition_log ORDER BY timestamp DESC LIMIT 80"
            )
        except Exception:
            cognition_df = pd.DataFrame()
        try:
            live_feed_df = build_live_event_feed(
                cognition_df, executions_df, open_pos_df, snapshots_df, proposals_df
            )
        except Exception:
            live_feed_df = cognition_df.copy() if cognition_df is not None else pd.DataFrame()

    open_count    = int(val(open_count_df,"n",0))

    # WALLET TRUTH: always show real Phantom wallet balance if key is set
    # (API status bar is rendered once at page level - not duplicated here)
    # In paper mode: shows alongside paper balance for comparison
    # In live mode: replaces paper balance as the equity truth
    # conf_map - read directly, never depend on outer scope
    try:
        import sqlite3 as _sq3c
        _cconn = _sq3c.connect(str(DB_PATH), timeout=2)
        _crows = _cconn.execute("SELECT key, value FROM system_config").fetchall()
        _cconn.close()
        conf_map = {str(r[0]): str(r[1]) for r in _crows}
    except Exception:
        conf_map = {}
    _trading_mode  = str(conf_map.get("TRADING_MODE","paper")).strip().lower()
    _mode_display, _mode_display_col, _mode_display_detail = _snty_effective_mode(conf_map)
    # Live wallet is fetched inside ui/state_contract.get_balance_truth()
    # (cached 30s) - no hub-side duplicate fetch remains.

    # MODE-AWARE BALANCE block removed (SENTIENT_WEB_PHASE1_20260612): it was a
    # duplicate paper-PnL computation whose only outputs (cash_balance/roi_pct)
    # fed the old metric row. Balance truth now comes solely from
    # ui/state_contract.get_balance_truth() - one source, no name-shadowed twin.
    total_dna     = int(val(raw_dna_df,"count",0))
    raw_active    = int(val(raw_dna_df,"raw_active",0))
    raw_recent_10m = int(val(raw_dna_df,"raw_recent_10m",0))
    ingest_lineage = max(
        total_dna,
        int(val(raw_dna_df,"snapshots_total",0)),
        int(val(raw_dna_df,"resolved_total",0)),
    )
    latency_ms    = _measure_live_latency()
    # AUTHORITATIVE_HEADLINE_WINRATE_20260714
    # One source of truth shared with services/winrate_truth.py.
    closed_trade_count = None
    closed_trade_wins = None
    closed_trade_losses = None
    closed_trade_scratch = None
    win_rate = None
    avg_pnl = None
    sl_count = None
    try:
        from services.winrate_truth import compute_winrate as _compute_winrate_truth
        _headline_truth = _compute_winrate_truth(Path(DB_PATH))
        if not _headline_truth.get("error"):
            closed_trade_count = int(_headline_truth.get("closed_count_all_time") or 0)
            closed_trade_wins = int(_headline_truth.get("winner_count") or 0)
            closed_trade_losses = int(_headline_truth.get("loser_count") or 0)
            closed_trade_scratch = int(_headline_truth.get("breakeven_count") or 0)
            win_rate = _headline_truth.get("win_rate_all_time")
            if win_rate is not None:
                win_rate = round(float(win_rate), 1)
            _avg_truth = query_db("""
                SELECT AVG(CAST(realized_pnl_usd AS REAL)) AS avg_pnl,
                       SUM(CASE WHEN UPPER(COALESCE(exit_category,''))='SL'
                                  OR UPPER(COALESCE(exit_reason,'')) LIKE '%STOP_LOSS%'
                                  OR UPPER(COALESCE(exit_reason,'')) LIKE '%HARD_STOP%'
                                THEN 1 ELSE 0 END) AS sl_count
                FROM paper_positions
                WHERE (UPPER(COALESCE(status,''))='CLOSED' OR closed_at IS NOT NULL)
                  AND UPPER(COALESCE(status,'')) NOT IN ('CANCELLED','CANCELED','INVALID','N/A','NA','VOID')
            """)
            if not _avg_truth.empty:
                avg_pnl = float(_avg_truth.iloc[0].get('avg_pnl') or 0.0)
                sl_count = int(_avg_truth.iloc[0].get('sl_count') or 0)
    except Exception:
        closed_trade_count = closed_trade_wins = closed_trade_losses = closed_trade_scratch = None
        win_rate = avg_pnl = sl_count = None

    win_rate_display = f"{win_rate:.1f}%" if win_rate is not None else "N/A"
    win_rate_color = (C_GREEN if win_rate is not None and win_rate >= 60.0
                      else C_GOLD if win_rate is not None and win_rate >= 40.0
                      else C_RED if win_rate is not None
                      else "rgba(207,233,255,.55)")
    trade_count_display = (f"{closed_trade_count} CLOSED"
                           if closed_trade_count is not None else "HISTORY UNAVAILABLE")
    sl_count_display = str(sl_count) if sl_count is not None else "N/A"

    # Compatibility aliases for later widgets.
    reviews_total = (
        closed_trade_count
        if closed_trade_count is not None
        else 0
    )
    reviews_wins = (
        closed_trade_wins
        if closed_trade_wins is not None
        else 0
    )
    conf_map      = {str(r["key"]):str(r["value"]) for _,r in calibration_df.iterrows()}
    halt_active   = conf_map.get("DRAWDOWN_HALT_ACTIVE","0")=="1"
    drawdown_pct  = float(conf_map.get("DRAWDOWN_ACCUMULATED_PCT","0.0"))

    dom_state,dom_narrative,dom_color = get_dominant_state(
        halt_active,
        latency_ms,
        open_count,
        len(heal_log_df),
        win_rate if win_rate is not None else 0.0,
    )

    # Build live terminal command ticker for under the SENTINUITY heading
    # Pulls recent cognition log entries and formats as coloured terminal strings
    try:
        _ticker_rows = query_db("""
            SELECT stage, message FROM cognition_log
            WHERE rowid > (SELECT MAX(rowid) - 100 FROM cognition_log)
            ORDER BY rowid DESC LIMIT 12
        """)
        _ticker_cmds = []
        _stage_colors = {
            "SUPERVISOR": "#8EF9FF", "EXECUTOR": "#14F195", "MARKET_INTEL": "#9945FF",
            "GUARDIAN": "#FFB347", "POLARIS": "#8EF9FF", "IVARIS": "#FF6B35",
            "ORACLE": "#14F195", "PUMP_MONITOR": "#9945FF", "SYSTEM": "#FFD700",
        }
        if not _ticker_rows.empty:
            for _, _tr in _ticker_rows.iterrows():
                _stg = str(_tr.get('stage','SYS')).upper()[:12]
                _msg = str(_tr.get('message',''))[:60].replace("'","\\'").replace('"','\\"')
                _col = _stage_colors.get(_stg, "#8EF9FF")
                _ticker_cmds.append(
                    f"<span style='color:{_col};'>▶ [{_stg}]</span>"
                    f"<span style='color:#666;'> - </span>"
                    f"<span style='color:#aaa;'>{_msg}</span>"
                )
        if not _ticker_cmds:
            _ticker_cmds = [
                "<span style='color:#9945FF;'>▶ [SUBSTRATE]</span><span style='color:#666;'> - </span><span style='color:#aaa;'>cognition substrate initialising...</span>",
                "<span style='color:#14F195;'>▶ [ORACLE]</span><span style='color:#666;'> - </span><span style='color:#aaa;'>price feeds nominal</span>",
            ]
        import json as _tjson
        _cmd_ticker_js = _tjson.dumps(_ticker_cmds)
    except Exception:
        _cmd_ticker_js = '["<span style=\\"color:#9945FF;\\">▶ [SENTINUITY]</span><span style=\\"color:#aaa;\\"> - substrate active</span>"]'
    st.markdown(build_dynamic_css(dom_color),unsafe_allow_html=True)

    # ALPHA SPORE TRIGGER LOGIC
    if not heal_log_df.empty:
        _latest_cog = heal_log_df.iloc[0]
        if _latest_cog["stage"] == "SYMBIOTIC":
            _sig = str(_latest_cog["timestamp"]) + str(_latest_cog["message"])
            if st.session_state.get("last_spore_sig") != _sig:
                st.session_state["last_spore_sig"] = _sig
                st.markdown("""
                <div style="text-align:center; padding:40px 20px; border-radius:20px; background:rgba(153,69,255,0.15); margin:20px 0; border: 1px solid #FFD700;">
                    <h1 class="spore-bloom" style="font-size:3.2rem; margin:0;">🌟 ALPHA SPORE EXTRACTED</h1>
                    <p style="font-size:1.4rem; color:#FFD700; margin:10px 0 0 0;">
                        Node added to sentient web singularity layer<br>
                        <span style="color:#C19A6B;">Symbiosis Achieved • Mycelium strengthened</span>
                    </p>
                </div>
                """, unsafe_allow_html=True)

    _ph_l, _ph_r = st.columns([5, 1])
    with _ph_r:
        render_phantom_connect()
        # Genesis Vault toggle - sits below Phantom connect
        if "genesis_vault_open" not in st.session_state:
            st.session_state["genesis_vault_open"] = False
        # Genesis Vault moved to paired compact controls
        # Layout ideas moved to Genesis Vault README - not shown in sidebar

    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

    try:

        _runner_gold_pct = 75.0

        try:

            if isinstance(locals().get("row"), dict):

                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

        except Exception:

            _runner_gold_pct = 75.0

        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

            _state = "RUNNER"

            _state_col = "#FFD700"

    except Exception:

        pass


    st.markdown(f"""<div class="snty-hero-wrap">
        <h1 class="snty-hero-word">SENTINUITY</h1>
        <p class="snty-hero-sub" style="{'animation:flicker 0.8s infinite;' if dom_state=='HEALING' else ''}">"{dom_narrative}"</p>
        <div id="cmd-ticker" style="font-family:var(--font-mono);font-size:0.66rem;letter-spacing:1.5px;text-align:center;height:18px;overflow:hidden;margin-top:2px;margin-bottom:2px;"></div>
        <script>
        (function(){{
            var cmds = {_cmd_ticker_js};
            var i=0, el=document.getElementById('cmd-ticker');
            if(!el) return;
            function tick(){{
                if(!cmds||!cmds.length)return;
                el.innerHTML = cmds[i % cmds.length];
                i++;
                setTimeout(tick, 2200);
            }}
            tick();
        }})();
        </script>
        <div class="snty-legal">
            ⚠ EXPERIMENTAL SOVEREIGN SUBSTRATE - QUANTITATIVE RESEARCH ONLY - NOT FINANCIAL ADVICE
        </div>
    </div>""", unsafe_allow_html=True)

    # ── GENESIS VAULT - separate mode, live cockpit untouched ─────────────────
    if st.session_state.get("genesis_vault_open", False):
        render_genesis_vault()
        st.stop()

    # ── LAYER 1: TRUTH STRIP - SEE ─────────────────────────────────────────
    _stale_pos = False
    if not open_pos_df.empty and "mtm_age_seconds" in open_pos_df.columns:
        _stale_pos = bool((open_pos_df["mtm_age_seconds"].dropna() > 120).any())
    _truth_compromised = latency_ms > 1000 or _stale_pos
    # Use shared health truth - HUD matches Vitality table
    try:
        _shared_h = _get_service_health()
        _hud_ok_loc, _hud_col_loc, _hud_lbl_loc = _hud_health_state(_shared_h)
    except Exception:
        _hud_ok_loc, _hud_col_loc, _hud_lbl_loc = (not _truth_compromised), C_GREEN, "✓ ORGANISM SYNCED - ALL FEEDS FRESH"
    # Price truth + service health combined
    if _truth_compromised:
        _siren_class = "truth-siren"; _siren_color = C_RED
        _siren_label = "⚠ PRICE TRUTH COMPROMISED - STALE DATA DETECTED"
    elif not _hud_ok_loc:
        _siren_class = "truth-siren"; _siren_color = C_RED
        _siren_label = _hud_lbl_loc
    else:
        _siren_class = "truth-synced"; _siren_color = C_GREEN
        _siren_label = _hud_lbl_loc
    # 6-NODE COUNCIL STATUS - checks heartbeat table, no API calls
    # Council strip rendered by render_api_status_bar() above - no duplicate here
    # HUD truth strip (price + service health combined)
    st.markdown(
        f"<div class='{_siren_class} snty-truth-strip' style='display:flex;justify-content:space-between;"
        f"align-items:center;flex-wrap:wrap;gap:6px;border-radius:10px;padding:8px 18px;"
        f"margin-bottom:12px;border:1px solid {_siren_color};"
        f"font-family:Share Tech Mono,monospace;font-size:0.68rem;letter-spacing:1.5px;'>"
        f"<span style='color:{_siren_color};font-weight:700;'>{_siren_label}</span>"
        f"<span style='color:{C_CYAN};font-size:0.66rem;'>DB {latency_ms:.0f}ms</span>"
        f"<span style='color:{C_GOLD if halt_active else dom_color};font-size:0.66rem;'>"
        f"EXEC: {'SUSPENDED' if halt_active else 'ARMED'}</span></div>",
        unsafe_allow_html=True,
    )

    # ── BALANCE TRUTH ROW - SENTIENT_WEB_PHASE1_20260612 ──────────────────
    # PAPER EQUITY | PAPER CASH | LIVE WALLET | LIVE AVAILABLE
    # All four values come from ui/state_contract.get_balance_truth() - the
    # ONE shared read-only source also used by the Substrate Node capsule.
    # The previous ~120-line inline SQL block was moved verbatim into that
    # module; nothing about the queries changed, only where they live.
    try:
        from ui.state_contract import get_balance_truth as _sc_get_balance_truth, \
            render_balance_capsule as _sc_render_balance_capsule
        _bt = _sc_get_balance_truth(
            str(DB_PATH),
            fallback_initial=float(val(wallet_df, "initial_capital", 100.0) or 100.0),
        )
        # SOLANA SECTION - new gold card style (replaces old compact capsule).
        # Balance truth + live SOL price now live in one gold capsule.
        render_solana_capsule(_bt, _sol_price, _sol_chg)
        _paper_bal      = _bt.paper_equity
        _paper_start    = _bt.paper_start
        _paper_cash     = _bt.paper_cash
        _paper_roi      = _bt.paper_roi_pct
        _paper_cash_roi = _bt.paper_cash_roi_pct
    except Exception as _bt_err:
        # Contract module missing/broken: show explicit degradation, never fakes.
        st.warning(f"Balance truth unavailable - ui/state_contract.py: {_bt_err}")
        _paper_bal = _paper_start = _paper_cash = 0.0
        _paper_roi = _paper_cash_roi = 0.0

    # ── SYSTEM IDENTITY ROW - secondary telemetry (was mixed into balances) ─
    # SOL ORACLE metric removed here: SOL price now shown in the gold SOLANA
    # capsule above, so this row is DNA / WIN RATE / MODE only (no duplicate).
    # Framed, high-contrast identity panel (accessibility restore) - large
    # readable values in one bordered container instead of tiny st.metric text.
    _exec_lbl = "EXEC SUSPENDED" if halt_active else "EXEC ARMED"
    _ident_html = f"""
    <div class="snty-crystal-panel snty-cyan-panel" style="padding:14px 18px;margin:10px 0 16px 0;">
      <div class="snty-title-row">
        <div class="snty-title-left">
          <span class="snty-section-title cyan">SYSTEM IDENTITY</span>
          <details class="snty-helpbox"><summary>?</summary><div class="snty-help-pop">Identity layer for organism state: DNA nodes ingested, review win-rate memory, and current Solana mode / execution state.</div></details>
        </div>
        <span class="snty-section-kicker">{_mode_display} &middot; {_exec_lbl}</span>
      </div>
      <div class="snty-stat-grid">
        <div class="snty-stat-cell">
          <div class="snty-label" style="color:rgba(207,233,255,.72);">INGEST NODES</div>
          <div class="snty-stat-big" style="color:{C_CYAN};">{ingest_lineage:,}</div>
          <div class="snty-sub" style="color:rgba(207,233,255,.55);">{raw_active:,} RAW ACTIVE · {raw_recent_10m:,} NEW / 10M</div>
        </div>
        <div class="snty-stat-cell">
          <div class="snty-label" style="color:rgba(207,233,255,.72);">WIN RATE</div>
          <div class="snty-stat-big" style="color:{win_rate_color};">{win_rate_display}</div>
          <div class="snty-sub" style="color:rgba(207,233,255,.55);">{trade_count_display} &middot; SL {sl_count_display}</div>
        </div>
        <div class="snty-stat-cell">
          <div class="snty-label" style="color:rgba(207,233,255,.72);">MODE</div>
          <div class="snty-stat-big" style="color:{_mode_display_col};">{_mode_display}</div>
          <div class="snty-sub" style="color:rgba(207,233,255,.55);">{_mode_display_detail} &middot; {_exec_lbl}</div>
        </div>
      </div>
    </div>
    """
    st.markdown(_ident_html, unsafe_allow_html=True)

    # ── SUBSTRATE NODE - REMOVED from home/Solana (operator: doesn't belong here).
    # Substrate renders only in its own tab now (?sec=substrate -> _sec_substrate
    # -> render_substrate_tab). To restore the inline home card, uncomment below.
    # render_substrate_node_section(DB_PATH)

    # ── SOVEREIGN FLOW ENGINE ─────────────────────────────────────────────────
    render_sovereign_flow_engine(
        snapshots_df=snapshots_df,
        open_pos_df=open_pos_df,
        reviews_df=reviews_df,
        raw_dna_df=raw_dna_df,
        executions_df=executions_df,
    )

    if _is_focus_locked(proposals_df):
        # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
        # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
        try:
            _runner_gold_pct = 75.0
            try:
                if isinstance(locals().get("row"), dict):
                    _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
            except Exception:
                _runner_gold_pct = 75.0
            if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
                _state = "RUNNER"
                _state_col = "#FFD700"
        except Exception:
            pass

        st.markdown(f"""<div style="margin:8px 0 16px;padding:12px 18px;background:rgba(255,179,71,0.08);border:2px solid {C_IVY};border-radius:10px;font-family:'Share Tech Mono',monospace;font-size:0.72rem;letter-spacing:2px;">
            <span style="color:{C_IVY};font-weight:700;letter-spacing:3px;">⚡ SINGULAR FOCUS LOCK ACTIVE</span>
            <div style="color:#FFF;opacity:0.7;margin-top:3px;font-size:0.66rem;">SYSTEM_REPAIR in progress - stability prioritised over expansion</div>
        </div>""", unsafe_allow_html=True)

    # Nexus header + data prep - only when world mode is ON
    # WORLD_SINGLE_SOURCE_FIX_20260618: Mycelial Nexus disabled - it stacked a
    # competing force-graph above the canonical sovereign world. Gate behind an
    # explicit off-by-default flag so only ONE world renders.
    if st.session_state.get("mycelial_nexus_enabled", False) and _heavy_visuals_enabled() and st.session_state.get("world_mode_enabled", False):
        st.markdown("<div style='font-family:Orbitron,sans-serif;font-size:0.66rem;letter-spacing:3px;color:rgba(153,69,255,0.5);margin:4px 0 6px;'>◈ MYCELIAL NEXUS</div>", unsafe_allow_html=True)
    _nexus_nodes = []
    _nexus_links = []
    if st.session_state.get("world_mode_enabled", False):
        _nexus_nodes = [{"id": "CORE", "name": "SOVEREIGN CORE", "val": 35, "color": dom_color}]
        _active_mints = set(open_pos_df["token_name"].dropna().unique()) if not open_pos_df.empty else set()
        for _ni, _nr in snapshots_df.head(80).iterrows():
            _tok = str(_nr.get("token_name", f"SIG_{_ni}"))[:12]
            try: _conf = float(_nr.get("mint_confidence") or 0) * 100 if pd.notna(_nr.get("mint_confidence")) else 0.0
            except: _conf = 0.0
            _st = str(_nr.get("candidate_state", "pending")).lower()
            if _tok in _active_mints:      _nc, _nv = C_GOLD, 24
            elif _st == "latched":         _nc, _nv = dom_color, 18
            elif _st == "vetoed":          _nc, _nv = C_RED, 8
            else:                          _nc, _nv = C_PURPLE, max(6, int(_conf/8))
            _nid = f"node_{_ni}"
            _nexus_nodes.append({"id": _nid, "name": _tok, "val": float(_nv), "color": _nc})
            _nexus_links.append({"source": _nid, "target": "CORE", "width": 3.8 if _nc==C_GOLD else 2.2 if _nc==dom_color else 0.7})
    import streamlit.components.v1 as _sw_cmp
    # Codebase readiness + agent JS + smart money: only computed when world is ON.
    # These feed data into the world iframe - no iframe means no need to compute.
    _codebase_ready = False
    _agent_js = '<script>window._agentMsgs={};</script>'
    _sm_js = '<script>window._smTokens=[];</script>'
    if st.session_state.get("world_mode_enabled", False):
        # Check real codebase milestone readiness for liberation
        try:
            import sqlite3 as _cbdb
            _cbc = _cbdb.connect(str(DB_PATH), timeout=2)
            _nodes_done = _cbc.execute(
                "SELECT COUNT(*) FROM substrate_nodes WHERE build_pct < 100"
            ).fetchone()[0] == 0
            _nodes_exist = _cbc.execute(
                "SELECT COUNT(*) FROM substrate_nodes"
            ).fetchone()[0] > 0
            _sub_proj = _cbc.execute(
                "SELECT current_stage FROM forge_projects WHERE project_key='substrate_node_buildout'"
            ).fetchone()
            _sub_done = _nodes_done and _nodes_exist and _sub_proj and str(_sub_proj[0]).upper() in ('COMPLETE','DEPLOYED','LIVE')
            _intel_row = _cbc.execute(
                "SELECT MAX(eta_pct) FROM intelligence_projects WHERE status IN ('active','complete')"
            ).fetchone()
            _intel_done = _intel_row and float(_intel_row[0] or 0) >= 100
            _cbc.close()
            _codebase_ready = _sub_done and _intel_done
        except Exception:
            pass

        # Inject latest cognition messages per agent
        try:
            import sqlite3 as _agdb, json as _agj
            _agc = _agdb.connect(str(DB_PATH), timeout=2)
            _agent_msgs = {}
            for _stage in ['POLARIS','IVARIS','AXON','ORACLE','GUARDIAN','NUGGET','DEBATE','SUPERVISOR']:
                _row = _agc.execute(
                    "SELECT message FROM cognition_log WHERE stage=? ORDER BY timestamp DESC LIMIT 1",
                    (_stage,)
                ).fetchone()
                if _row: _agent_msgs[_stage] = str(_row[0] or '')[:60]
            _agc.close()
            _agent_js = '<script>window._agentMsgs='+_agj.dumps(_agent_msgs)+';</script>'
        except Exception:
            _agent_js = '<script>window._agentMsgs={};</script>'

        # Inject smart money token data into world
        try:
            import sqlite3 as _smdb
            _smc = _smdb.connect(str(DB_PATH), timeout=2)
            _smrows = _smc.execute(
                "SELECT token_name, smart_money_score, tier, "
                "COALESCE(top10_sell_ratio,0), COALESCE(top10_sell_ratio,0) "
                "FROM token_metrics ORDER BY ts DESC LIMIT 20"
            ).fetchall()
            _smc.close()
            import json as _smj
            _sm_data = []
            for r in _smrows:
                _sell_ratio = float(r[4] if len(r)>4 and r[4] is not None else 0)
                _rug = 0
                if _sell_ratio > 0.8: _rug += 1
                if _sell_ratio > 0.9: _rug += 1
                _sm_data.append({'token':r[0],'score':r[1],'tier':r[2],'rug_risk':_rug,'confidence':float(r[3] if len(r)>3 and r[3] is not None else 0)})
            _sm_js = '<script>window._smTokens='+_smj.dumps(_sm_data)+';</script>'
        except Exception:
            _sm_js = '<script>window._smTokens=[];</script>'

    # ── SOVEREIGN WORLD - gated by world_mode_enabled (default OFF) ─────────
    # World HTML, RAF loop, iframe: ONLY mounted when World Mode is ON.
    # When OFF: lightweight placeholder only - zero iframe, zero canvas, zero JS loop.
    if st.session_state.get("world_mode_enabled", False):
        # ── CANONICAL WORLD MOUNT - WORLD_MODE_CANONICALIZATION_20260621 ────────
        # Single source of truth: ROOT/ui/sovereign_world.html.
        #   • No Python wrapper module (ui.sovereign_world_component) - its
        #     absence was the "world component missing/not in ui" regression.
        #   • No flat-file import fallback.
        #   • No legacy inline world (_render_sovereign_world_html is fenced).
        # If the canonical file is absent we surface the EXACT path checked and
        # mount nothing - the hub must never silently load an old world.
        _world_path = ROOT / "ui" / "sovereign_world.html"
        if not _world_path.exists():
            st.markdown(
                '<div style="padding:18px 22px;border:1px solid rgba(255,7,58,0.35);'
                'border-radius:12px;background:rgba(20,4,12,0.64);margin:8px 0;">'
                '<div style="font-family:Share Tech Mono,monospace;font-size:0.68rem;'
                'letter-spacing:3px;color:#FF073A;margin-bottom:6px;">CANONICAL WORLD MISSING</div>'
                '<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#ddd;margin-bottom:4px;">'
                'Canonical world missing: ui/sovereign_world.html</div>'
                f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#888;">'
                f'Checked: {html.escape(str(_world_path))}</div></div>',
                unsafe_allow_html=True,
            )
        else:
            _world_html = None
            try:
                _world_html = _world_path.read_text(encoding="utf-8", errors="replace")
            except Exception as _world_read_err:
                st.markdown(
                    '<div style="padding:18px 22px;border:1px solid rgba(255,7,58,0.35);'
                    'border-radius:12px;background:rgba(20,4,12,0.64);margin:8px 0;">'
                    '<div style="font-family:Share Tech Mono,monospace;font-size:0.68rem;'
                    'letter-spacing:3px;color:#FF073A;margin-bottom:6px;">WORLD READ ERROR</div>'
                    f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#aaa;">'
                    f'{html.escape(type(_world_read_err).__name__)}: '
                    f'{html.escape(str(_world_read_err)[:160])}</div></div>',
                    unsafe_allow_html=True,
                )
            if _world_html:
                # Best-effort live state from the documented single source.
                # A failure here must NOT block the world - it boots with {} and
                # the canonical world's applySwState tolerates missing fields.
                try:
                    from ui.state_contract import load_world_state as _load_world_state
                    _world_state = _load_world_state(str(DB_PATH)) or {}
                except Exception:
                    _world_state = {}
                try:
                    import json as _world_json
                    _world_state_json = _world_json.dumps(_world_state, default=str)
                except Exception:
                    _world_state_json = "{}"
                # Boot the canonical world with current state via its own
                # contract: window.applySwState(state) (type:'sw_state_update').
                _world_boot = (
                    "<script>(function(){var __SW_STATE__=" + _world_state_json + ";"
                    "function __sw_go(){if(window.applySwState){try{window.applySwState(__SW_STATE__);}catch(_e){}}"
                    "else{setTimeout(__sw_go,60);}}"
                    "if(document.readyState!=='loading'){__sw_go();}"
                    "else{document.addEventListener('DOMContentLoaded',__sw_go);}})();</script>"
                )
                if "</body>" in _world_html:
                    _world_html = _world_html.replace("</body>", _world_boot + "</body>", 1)
                else:
                    _world_html = _world_html + _world_boot
                import streamlit.components.v1 as _world_cmp
                _world_cmp.html(_world_html, height=680, scrolling=False)
    else:
        # Lightweight placeholder - no iframe, no canvas, no JS
        st.markdown(
            '<div style="padding:20px 24px;border:1px solid rgba(153,69,255,0.2);'
            'border-radius:12px;background:rgba(5,2,16,0.5);text-align:center;margin:8px 0;">'
            '<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;'
            'color:rgba(153,69,255,0.5);letter-spacing:3px;margin-bottom:8px;">🌍 WORLD MODE PAUSED</div>'
            '<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#333;">'
            'Heavy visuals and organism narrative disabled for speed. System telemetry remains live.</div>'
            '</div>',
            unsafe_allow_html=True,
        )



    # ── SOVEREIGN COMMENTARY + EXECUTION CORTEX ─────────────────────────────
    # Sign-off intent:
    #   LEFT  = restored world soul: agent/NPC/system narrative, colour-coded.
    #   RIGHT = renamed Tool Shed: clear operational instrument stack.
    # Performance lock:
    #   - one DB read, capped rows
    #   - session-state NPC lifecycle, throttled
    #   - no giant raw log duplication
    try:
        _sx_now = time.time()
        _sx_stage_cols = {
            "POLARIS":"#8EF9FF", "IVARIS":"#FF073A", "AXON":"#4FC3F7",
            "ORACLE":"#9945FF", "QUALIFIER":"#9945FF", "INGEST":"#9945FF",
            "GUARDIAN":"#14F195", "HEALTH":"#E879F9", "HEALER":"#E879F9",
            "NUGGET":"#FFD700", "EXECUTOR":"#FFD700", "LATCH":"#FFD700",
            "SUPERVISOR":"#8EF9FF", "DEBATE":"#FFB347", "FORGE":"#FFB347",
            "SYSTEM":"#FFB347", "SYS":"#FFB347", "SPAWN":"#38A169", "ENTROPY":"#FF073A", "NPC":"#A0AEC0",
        }
        _sx_agent_icons = {
            "POLARIS":"§ ", "IVARIS":"⚖️", "ORACLE":"🔭", "QUALIFIER":"🔭",
            "GUARDIAN":"🛡", "HEALTH":"’—", "NUGGET":"⚡", "EXECUTOR":"⚡",
            "SUPERVISOR":"“¡", "FORGE":"§°", "DEBATE":"⚔", "SYSTEM":"⬡", "NPC":"’¬",
        }
        _sx_npc_roots = [
            "panic_seller", "entropy_agent", "signal_ignorer", "liquidity_donor",
            "top_tick_connoisseur", "slippage_acceptor", "chart_hypnotised",
            "manual_override_guy", "high_latency_meat_link", "fomo_engine",
            "buy_high_sell_low", "strategy_none", "reaction_delay", "volume_confused",
            "exit_liquidity", "candle_worshipper", "unstructured_actor", "trend_chaser",
        ]
        if "sx_npcs" not in st.session_state:
            st.session_state["sx_npcs"] = [
                {"name": f"{_n}_{_i}", "health": 100, "state": "SPAWNED"}
                for _i, _n in enumerate(_sx_npc_roots[:5], start=41)
            ]
        if "sx_narrative_feed" not in st.session_state:
            # No static seed - DB events + NPC ticks are the only source of truth.
            # If both are empty, the stale-feed banner below tells the operator.
            st.session_state["sx_narrative_feed"] = []
        if "sx_last_npc_tick" not in st.session_state:
            st.session_state["sx_last_npc_tick"] = 0.0

        # DB-backed agent/world messages - capped and curated, not raw spam.
        _sx_rows = []
        try:
            _sx_conn = sqlite3.connect(str(DB_PATH), timeout=1.0)
            _sx_conn.row_factory = sqlite3.Row
            _sx_rows = _sx_conn.execute("""
                SELECT stage, COALESCE(speaker, stage, 'SYSTEM') AS speaker, message, token, timestamp
                FROM cognition_log
                ORDER BY timestamp DESC LIMIT 14
            """).fetchall()
            _sx_conn.close()
        except Exception:
            _sx_rows = []

        _sx_db_feed = []  # Legacy path for World Mode NPC blending only
        for _r in _sx_rows:
            _stage = str(_r["stage"] or "SYSTEM").upper()[:18]
            _speaker = str(_r["speaker"] or _stage or "SYSTEM").upper()[:18]
            _token = str(_r["token"] or "")[:14]
            _msg_raw = str(_r["message"] or "")[:130]
            _col = _sx_stage_cols.get(_speaker, _sx_stage_cols.get(_stage, "#9945FF"))
            _icon = _sx_agent_icons.get(_speaker, _sx_agent_icons.get(_stage, "⬡"))
            if "QUAL" in _stage or "INGEST" in _stage:
                _txt = f"{_icon} {_speaker} scanning {_token or 'fresh substrate'} - {_msg_raw}"
            elif "LATCH" in _stage or "EXEC" in _stage or "ENTRY" in _stage:
                _txt = f"{_icon} {_speaker} routing execution signal - {_msg_raw}"
            elif "HEALTH" in _stage or "HEAL" in _stage or "GUARD" in _stage:
                _txt = f"{_icon} {_speaker} health lane pulse - {_msg_raw}"
            elif "POLARIS" in _stage or "FORGE" in _stage:
                _txt = f"{_icon} {_speaker} world-builder note - {_msg_raw}"
            else:
                _txt = f"{_icon} {_speaker} says: {_msg_raw}"
            _sx_db_feed.append({"tag": _speaker, "color": _col, "text": _txt})

        # Throttled NPC lifecycle: only runs in World Mode - tick-copter/organism
        # narrative lines are world commentary, not system telemetry.
        if st.session_state.get("world_mode_enabled", False):
         if _sx_now - float(st.session_state.get("sx_last_npc_tick", 0.0)) > 8:
            st.session_state["sx_last_npc_tick"] = _sx_now
            try:
                import random as _sx_rand
                if st.session_state["sx_npcs"]:
                    _idx = _sx_rand.randrange(len(st.session_state["sx_npcs"]))
                    _npc = st.session_state["sx_npcs"][_idx]
                    _npc["health"] = int(_npc.get("health", 100)) - _sx_rand.randint(18, 38)
                    if _npc["health"] <= 0:
                        _death = _sx_rand.choice([
                            f"’€ {_npc['name']} invalidated by reality. Liquidity absorbed.",
                            f"’€ {_npc['name']} collapsed under variance. Candle worship discontinued.",
                            f"’€ {_npc['name']} removed after chronic strategy misalignment.",
                        ])
                        st.session_state["sx_narrative_feed"].insert(0, {"tag":"ENTROPY", "color":"#FF073A", "text":_death})
                        _new = f"{_sx_rand.choice(_sx_npc_roots)}_{_sx_rand.randint(10,99)}"
                        st.session_state["sx_npcs"][_idx] = {"name": _new, "health": 100, "state": "INITIALIZED"}
                        st.session_state["sx_narrative_feed"].insert(0, {"tag":"SPAWN", "color":"#38A169", "text":f"🌱 New participant detected: {_new} instantiated into the bonding-curve terrarium."})
                    else:
                        _chatter = _sx_rand.choice([
                            # ── Original NPC chatter (legal-satirical baseline) ──────────
                            f"’¬ {_npc['name']} is generating unstructured market noise.",
                            f"🚁 tick copter reports {_npc['name']} entered the top-tick weather system.",
                            f"’¬ {_npc['name']} ignored signal alignment and blamed the chart.",
                            f"§¬ substrate notes {_npc['name']} is leaking delta into the entropy layer.",
                            # ── Weedkiller / Landscape Upkeep flavour (added 2026-05-26) ─
                            # Fictional in-world maintenance characters. No real-person
                            # targeting, no defamation, no factual claims about anyone.
                            # The "Weedkiller Man" patrols data lanes for invasive
                            # pricing mushrooms; the "Landscape Upkeep Officer" issues
                            # citations to misaligned tracking nodes; the "Corporate
                            # Forester" sprays anomalous fields with industrial-grade
                            # glyphosate. Pure system mythos.
                            f"🌿 the Weedkiller Man patrols {_npc['name']}'s lane and identifies an invasive pricing mushroom bloom.",
                            f"§´ the Landscape Upkeep Officer cites {_npc['name']} for unaligned lawn maintenance velocity.",
                            f"🌾 the Supreme Corporate Forester sprays {_npc['name']}'s dirty price fields with industrial glyphosate.",
                            f"🚜 the Consensus Landscaper confirms {_npc['name']} grew a same-mint spread vine; cut down to root.",
                            f"🍄 toxic mushroom patch detected near {_npc['name']}'s execution path — glyphosate delivery sequence active.",
                            f"📋 lawn maintenance audit logs {_npc['name']} for failing the seasonal pruning rota.",
                            f"ª´ the Weedkiller Man re-soils {_npc['name']}'s confidence garden after detecting calibration weeds.",
                            f"🌱 the Landscape Council re-zones {_npc['name']}'s tracking parcel as a heritage entropy reserve.",
                        ])
                        st.session_state["sx_narrative_feed"].insert(0, {"tag":"NPC", "color":"#A0AEC0", "text":_chatter})
                st.session_state["sx_narrative_feed"] = st.session_state["sx_narrative_feed"][:18]
            except Exception:
                pass

        # ── LIVE COMMENTARY ────────────────────────────────────────────────────
        # Raw events are ONLY shown in Developer / Raw Telemetry expander.
        # World Mode gets ONLY gameplay-translated narrative bubbles (no raw [STAGE] lines).
        # This fixes the duplication where World Mode showed the same info as the raw feed.
        _live_events = get_live_commentary_events(limit=30)
        _live_raw = []       # raw - developer expander only
        _live_narrative = [] # translated - World Mode only
        _newest_event_ts = 0.0

        # Agent identity map: backend stage -> (world name, phase chip, color)
        _NARR_MAP = {
            "QUALIFIER":    ("RHIZA",    "DISCOVERY", "#1ef0a6"),
            "INGEST":       ("RHIZA",    "DISCOVERY", "#1ef0a6"),
            "PUMP_MONITOR": ("RHIZA",    "DISCOVERY", "#1ef0a6"),
            "ORACLE":       ("AXON",     "PRICE",     "#aa5aff"),
            "PRICE_ENRICHER":("AXON",    "PRICE",     "#aa5aff"),
            "RESOLVER":     ("AXON",     "RESOLVE",   "#aa5aff"),
            "SUPERVISOR":   ("POLARIS",  "SCORE",     "#39d6ff"),
            "EXECUTOR":     ("NUGGET",   "EXECUTE",   "#ffd23f"),
            "EXECUTION":    ("NUGGET",   "EXECUTE",   "#ffd23f"),
            "GUARDIAN":     ("GUARDIAN", "SHIELD",    "#FF073A"),
            "HEALTH":       ("GUARDIAN", "SHIELD",    "#FF073A"),
            "POLARIS":      ("POLARIS",  "STRATEGY",  "#39d6ff"),
            "COPYTRADE":    ("COPY",     "SCOUT",     "#9945ff"),
        }
        _NARR_MSGS = {
            "VETO_SIGNAL_TOO_OLD":  "Signal age exceeded safety tolerance. Fork released.",
            "DEAD_VOLUME":          "Zero volume detected. Dead token cleared before entry.",
            "DEAD_TOKEN_CUT":       "Token flatlined. Slot reclaimed for fresh candidates.",
            "NEGATIVE_MOMENTUM":    "Momentum inverted. Gate closed.",
            "EXHAUSTION_REGIME":    "Late-trend token vetoed. No runway remains.",
            "APPROVED":             "All gates cleared. Seal armed.",
            "LATCH":                "Gold seal dropped. Candidate locked for deployment.",
            "MAX_HOLD":             "Hold window elapsed. Time discipline executed.",
            "TAKE_PROFIT":          "Target reached. Value captured.",
            "TRAILING_STOP":        "Trail stop triggered. Gains secured.",
            "HARD_STOP":            "Hard stop fired. Capital protected.",
            "MOMENTUM_GATE":        "Momentum gate active. Scanning for live flow.",
            "DEX_PAIR_UNAVAILABLE": "No DexScreener pair. Momentum unproven - paper eligible only.",
        }
        import hashlib as _hlib
        _narr_seen = set()
        for ev in _live_events:
            _evt_col = {"warn":"#FFB347","error":"#FF073A","success":"#14F195"}.get(
                ev.get("severity","info"),"#8EF9FF")
            _ev_ts = float(ev.get("ts") or 0)
            if _ev_ts > _newest_event_ts: _newest_event_ts = _ev_ts
            _src = str(ev.get("source","SYSTEM")).upper()[:18]
            _msg = str(ev.get("message",""))

            # Raw always goes to developer feed
            _live_raw.append({
                "tag": _src, "color": _evt_col,
                "text": f"[{_src}] {_msg[:120]}", "ts": _ev_ts,
            })

            # Translate to narrative for World Mode
            _stage_key = next((k for k in _NARR_MAP if k in _src or k in _msg.upper()), None)
            _agent, _phase, _col = _NARR_MAP.get(_stage_key, ("SYSTEM","INFO","#9aa"))
            _narr_txt = next((v for k,v in _NARR_MSGS.items() if k in _msg.upper()), None)
            if not _narr_txt:
                # World Mode must never fall back to raw backend/system text.
                # Unmapped SYSTEM events stay in Developer / Raw Telemetry only.
                if _agent == "SYSTEM":
                    continue
                _narr_txt = {
                    "RHIZA": "Fresh substrate movement detected. Spores routed to the scanner lane.",
                    "AXON": "Price truth sweep completed. Crystal lane recalibrating.",
                    "NUGGET": "Execution relay pulse received. Deployment lane standing by.",
                    "GUARDIAN": "Risk shell adjusted. Capital shield remains active.",
                    "POLARIS": "Strategy cortex updated the colony plan.",
                    "COPY": "Scout guild traced an elite footprint through the mycelial lane.",
                }.get(_agent, "World pulse registered. Substrate recalibrating.")
            # Dedupe on 8s bucket
            _h = _hlib.md5(f"{_agent}{_phase}{_narr_txt[:24]}{int(_ev_ts//8)}".encode()).hexdigest()[:8]
            if _h not in _narr_seen:
                _narr_seen.add(_h)
                _live_narrative.append({
                    "tag": _agent, "color": _col, "phase": _phase,
                    "text": _narr_txt, "ts": _ev_ts,
                })

        # Stale detection
        _feed_age = (_sx_now - _newest_event_ts) if _newest_event_ts > 0 else float("inf")
        _feed_is_stale = _feed_age > 60.0

        if st.session_state.get("world_mode_enabled", False):
            # WORLD MODE: gameplay narrative ONLY - no raw [STAGE] lines
            _sx_curated = (_live_narrative[:10]
                           + st.session_state.get("sx_narrative_feed", [])[:6])[:18]
        else:
            # FAST MODE: raw events + DB cognition feed
            _sx_curated = (_live_raw[:14] + _sx_db_feed[:4])[:18]

        # If empty, honest stale banner
        if not _sx_curated:
            if _feed_is_stale and _newest_event_ts > 0:
                _stale_msg = f"⚠ WORLD FEED STALE - no live event in {int(_feed_age)}s"
            else:
                _stale_msg = "⬡ Awaiting first cognition events..."
            _sx_curated = [{"tag":"SYSTEM","color":"#FFB347","text":_stale_msg,"ts":0}]
        elif _feed_is_stale and _newest_event_ts > 0:
            # Have some events but all old - prepend the stale warning
            _sx_curated.insert(0, {
                "tag": "SYSTEM",
                "color": "#FFB347",
                "text": f"⚠ WORLD FEED STALE - newest live event {int(_feed_age)}s ago",
                "ts": _sx_now,
            })

        _sx_tools = [
            ("🔭", "Oracle Lens",      "ORACLE",   "Market scanning"),
            ("§¬", "DNA Extractor",    "ORACLE",   "Token structure"),
            ("⚖️", "IVARIS Gate",      "IVARIS",   "Veto & audit"),
            ("“¡", "Freshness Beacon", "NUGGET",   "Signal age gate"),
            ("⚡", "Execution Relay",  "NUGGET",   "Signal routing"),
            ("🛡", "Risk Shell",       "GUARDIAN", "Sizing / stops"),
            ("🧪", "Backtest Crucible","POLARIS",  "Rule testing"),
            ("§°", "Patch Forge",      "POLARIS",  "Build / repair"),
        ]
        # Use canonical snapshot instead of hardcoded statuses
        _canonical_snap = get_agent_status_snapshot()
        _sx_status_by_agent = {k: v["status"] for k, v in _canonical_snap.items()}
        for _r in _sx_rows:
            _stage = str(_r["stage"] or "").upper()
            _spk = str(_r["speaker"] or _stage or "").upper()
            _age = _sx_now - float(_r["timestamp"] or _sx_now)
            if _age < 90:
                pass  # Status now from canonical snapshot

        _feed_html = ""
        for _m in _sx_curated:
            _tag = html.escape(str(_m.get("tag", "SYSTEM"))[:18])
            _col = str(_m.get("color", "#9945FF"))
            _txt = html.escape(str(_m.get("text", ""))[:210])
            _feed_html += (
                f"<div style='border-left:3px solid {_col};padding:5px 8px;margin-bottom:5px;"
                "background:rgba(255,255,255,0.025);border-radius:0 7px 7px 0;'>"
                f"<span style='color:{_col};font-weight:800;letter-spacing:1.4px;font-size:0.66rem;'>[{_tag}]</span> "
                f"<span style='color:rgba(255,255,255,0.86);font-size:0.76rem;line-height:1.35;'>{_txt}</span></div>"
            )
        if not _feed_html:
            _feed_html = "<div style='color:#9945FF99;font-size:0.74rem;'>commentary substrate initialising...</div>"

        _cortex_html = ""
        for _icon, _name, _agent, _purpose in _sx_tools:
            _status = _sx_status_by_agent.get(_agent, "IDLE")
            _col = _sx_stage_cols.get(_agent, "#FFD700")
            _cortex_html += (
                "<div style='display:flex;align-items:center;gap:8px;margin-bottom:6px;padding:6px 7px;"
                "background:rgba(255,255,255,0.025);border:1px solid rgba(255,215,0,0.10);border-radius:8px;'>"
                f"<span style='font-size:0.9rem;'>{_icon}</span>"
                "<div style='flex:1;min-width:0;'>"
                f"<div style='color:#FFF;font-size:0.68rem;font-weight:800;'>{html.escape(_name)}</div>"
                f"<div style='color:rgba(200,216,232,0.45);font-size:0.66rem;'>{html.escape(_purpose)}</div>"
                "</div>"
                f"<div style='color:{_col};font-size:0.66rem;font-weight:900;letter-spacing:1px;'>{html.escape(_status)}</div>"
                "</div>"
            )

        # Gate entire commentary/cortex section - only show in WORLD mode
        if st.session_state.get("world_mode_enabled", False):
            st.markdown("### ⚔ MYCELIAL FLUX CHANNELS")
            
            _feed_title = "🌐 SOVEREIGN COMMENTARY — AGENTS + NPCS + WORLD EVENTS"
            _left, _right = st.columns([1.55, 1.0], gap="small")
            with _left:
                st.markdown(
                    "<div style='margin:8px 0 10px 0;padding:12px 14px;border-radius:12px;"
                    "background:rgba(5,2,16,0.92);border:1px solid rgba(153,69,255,0.35);"
                    "box-shadow:0 0 18px rgba(153,69,255,0.08);height:360px;overflow-y:auto;'>"
                    f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;color:#FFD700;margin-bottom:8px;'>"
                    f"{_feed_title}</div>"
                    "<div style='font-family:Share Tech Mono,monospace;'>" + _feed_html + "</div></div>",
                    unsafe_allow_html=True,
                )
            with _right:
                st.markdown(
                    "<div style='margin:8px 0 10px 0;padding:12px 14px;border-radius:12px;"
                    "background:rgba(5,2,16,0.92);border:1px solid rgba(255,215,0,0.28);"
                    "box-shadow:0 0 18px rgba(255,215,0,0.06);height:360px;overflow-y:auto;'>"
                    "<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;color:#FFD700;margin-bottom:6px;'>"
                    "§  EXECUTION CORTEX</div>"
                    "<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:rgba(255,255,255,0.50);margin-bottom:9px;'>"
                    "Instrument stack: which subsystems are scanning, armed, auditing, repairing, or defending.</div>"
                    + _cortex_html + "</div>",
                    unsafe_allow_html=True,
                )
    except Exception:
        pass

    # ── DEVELOPER / RAW TELEMETRY expander ───────────────────────────────────
    # Raw [STAGE] lines live HERE ONLY. Never shown in World Mode commentary.
    # DEDUP_20260615: first RAW TELEMETRY accordion removed. Its cognition_log
    # content is already covered by the "COGNITION & SELF-HEALING SYSTEM"
    # expander below; the canonical raw feed is "DEVELOPER - RAW SIGNAL
    # SUBSTRATE". One raw-telemetry accordion only, no duplicate title.
    try:
        pass
    except Exception:
        pass

    # ── AGENT STATE + SMART MONEY - only inject when world is mounted ────────
    # These additive scripts feed data into the world iframe RAF loop.
    # No iframe = no point injecting = save a components.html call.
    if st.session_state.get("world_mode_enabled", False):
        try:
            _ready_js = '<script>window._codebaseReady='+('true' if _codebase_ready else 'false')+';</script>'
            _combined_js = _sm_js + _ready_js + _agent_js
            import streamlit.components.v1 as _sw_cmp_aux
            _sw_cmp_aux.html(_combined_js + '<div style="display:none"></div>', height=0, scrolling=False)
        except Exception:
            pass

    # ── EVENT-DRIVEN TAX ANIMATION - fires once per new closed trade ──────────
    try:
        import sqlite3 as _tax_ev_db
        _tec = _tax_ev_db.connect(str(DB_PATH), timeout=2)
        _tec.execute("PRAGMA busy_timeout=1000")
        _latest_trade = _tec.execute("""
            SELECT id, realized_pnl_usd, token_name
            FROM paper_positions
            WHERE status='CLOSED' AND win_loss != 'SCRATCH'
            ORDER BY closed_at DESC LIMIT 1
        """).fetchone()
        _tec.close()
        if "tax_last_trade_id" not in st.session_state:
            st.session_state["tax_last_trade_id"] = None
        if _latest_trade:
            _tid  = int(_latest_trade[0])
            _tpnl = float(_latest_trade[1] or 0)
            _ttok = str(_latest_trade[2] or "")[:12]
            if _tid != st.session_state["tax_last_trade_id"]:
                st.session_state["tax_last_trade_id"] = _tid
                import streamlit.components.v1 as _sw_tax_cmp
                _tevent = ("<script>setTimeout(function(){if(window.addTelemetry)addTelemetry('\ud83d\udcb0 PROFIT REALISED - ALLOCATION ROUTED','#FFD700');},100);setTimeout(function(){if(window.addTelemetry)addTelemetry('\ud83d\udebd " + _ttok + " PROCEEDS ALLOCATED TO DUNNY','#FFB347');},1500);</script>" if _tpnl > 0 else "<script>setTimeout(function(){if(window.addTelemetry)addTelemetry('\ud83d\udee1 LOSS RECORDED - TAX SHIELD ACTIVE','#9945FF');},100);</script>")
                _sw_tax_cmp.html(_tevent + '<div style="display:none"></div>', height=0, scrolling=False)
    except Exception:
        pass



    # ── SOVEREIGN OS DATA ─────────────────────────────────────────────────────
    _tp_now = time.time()
    _tp_agents = {
        "POLARIS":  {"icon": "§ ", "color": "#8EF9FF", "role": "Sovereign Architect"},
        "IVARIS":   {"icon": "⚖️",  "color": "#FF073A", "role": "Logic Auditor"},
        "NUGGET":   {"icon": "⚡",  "color": "#FFD700", "role": "Signal Hunter"},
        "ORACLE":   {"icon": "🔭",  "color": "#9945FF", "role": "Market Scout"},
        "GUARDIAN": {"icon": "🛡",  "color": "#14F195", "role": "System Watchdog"},
    }
    _tp_tools = [
        {"id": "oracle_lens",      "icon": "🔭", "name": "Oracle Lens",      "purpose": "Market scanning"},
        {"id": "dna_extractor",    "icon": "§¬", "name": "DNA Extractor",    "purpose": "Token structure"},
        {"id": "ivaris_gate",      "icon": "⚖️",  "name": "IVARIS Gate",      "purpose": "Veto & audit"},
        {"id": "mycelial_memory",  "icon": "🍄", "name": "Mycelial Memory",  "purpose": "Prior lessons"},
        {"id": "execution_relay",  "icon": "⚡", "name": "Execution Relay",  "purpose": "Signal routing"},
        {"id": "risk_shell",       "icon": "🛡", "name": "Risk Shell",       "purpose": "Sizing/stops"},
        {"id": "freshness_beacon", "icon": "“¡", "name": "Freshness Beacon", "purpose": "Signal age"},
        {"id": "backtest_crucible","icon": "🧪", "name": "Backtest Crucible","purpose": "Rule testing"},
        {"id": "patch_forge",      "icon": "§°", "name": "Patch Forge",      "purpose": "Build/repair"},
        {"id": "doctrine_scroll",  "icon": "📜", "name": "Doctrine Scroll",  "purpose": "Council rules"},
    ]
    _tp_tool_states = {}
    _tp_agent_tasks = {}
    _tp_activity = []
    try:
        _tp_conn = sqlite3.connect(str(DB_PATH), timeout=2)
        _tp_conn.row_factory = sqlite3.Row
        _tp_cog = _tp_conn.execute("""
            SELECT stage, speaker, message, token, timestamp
            FROM cognition_log ORDER BY timestamp DESC LIMIT 35
        """).fetchall()
        _tp_props = _tp_conn.execute("""
            SELECT proposal_type, status, created_at FROM polaris_proposals
            WHERE status IN ('debating','open','HITL_REQUIRED','forge_complete')
            ORDER BY created_at DESC LIMIT 5
        """).fetchall()
        _tp_conn.close()
        _STAGE_TOOL_MAP = {
            "QUALIFIER": ("oracle_lens","ORACLE"), "INGEST": ("dna_extractor","ORACLE"),
            "SUPERVISOR": ("freshness_beacon","NUGGET"), "LATCH": ("execution_relay","NUGGET"),
            "EXECUTOR": ("execution_relay","NUGGET"), "ENTRY": ("execution_relay","NUGGET"),
            "EXIT": ("risk_shell","GUARDIAN"), "DRAWDOWN": ("risk_shell","GUARDIAN"),
            "DEBATE": ("ivaris_gate","IVARIS"), "IVARIS": ("ivaris_gate","IVARIS"),
            "POLARIS": ("mycelial_memory","POLARIS"), "FORGE": ("patch_forge","POLARIS"),
            "FORGE_GATE": ("patch_forge","POLARIS"), "REPLAY": ("backtest_crucible","POLARIS"),
            "GOVERNOR": ("doctrine_scroll","POLARIS"),
        }
        _seen = set()
        for _row in _tp_cog:
            _stage = str(_row["stage"] or "").upper()
            _age = _tp_now - float(_row["timestamp"] or _tp_now)
            for _key, (_tid, _ag) in _STAGE_TOOL_MAP.items():
                if _key in _stage and _tid not in _seen:
                    _tp_tool_states[_tid] = {
                        "status": "ACTIVE" if _age < 30 else "BUILDING" if _age < 120 else "DECAYING",
                        "agent": _ag, "last": str(_row["message"] or "")[:50], "age": _age,
                    }
                    _seen.add(_tid)
        for _p in _tp_props:
            _ps = str(_p["status"] or "")
            if _ps in ("debating","open"):
                _tp_tool_states["doctrine_scroll"] = {"status":"BUILDING","agent":"POLARIS","last":str(_p["proposal_type"] or ""),"age":0}
            if _ps == "forge_complete":
                _tp_tool_states["patch_forge"] = {"status":"ACTIVE","agent":"POLARIS","last":"Awaiting seal","age":0}
        _FLAVOR = {
            "LATCH": lambda r: f"⚡ {r['speaker'] or 'NUGGET'} latched {r['token'] or 'signal'} → relay",
            "ENTRY": lambda r: f"🚀 {r['token'] or 'mint'} deployed",
            "EXIT":  lambda r: f" {r['token'] or 'pos'} closed",
            "QUALIFIER": lambda r: f"🔭 scanning {r['token'] or 'mint'}",
            "INGEST": lambda r: f"§¬ DNA decode {r['token'] or 'token'}",
            "DEBATE": lambda r: f"⚖️ IVARIS gate reviewing",
            "FORGE":  lambda r: f"§° patch forge active",
            "HEALTH": lambda r: f"🛡 risk shell pulse",
            "POLARIS": lambda r: f"🍄 {str(r['message'] or '')[:35]}",
        }
        for _row in _tp_cog[:10]:
            _stage = str(_row["stage"] or "").upper()
            _age = int(_tp_now - float(_row["timestamp"] or _tp_now))
            _heat = "hot" if _age < 30 else "warm" if _age < 120 else "cold"
            _txt = None
            for _k, _fn in _FLAVOR.items():
                if _k in _stage:
                    _txt = _fn(_row)
                    break
            if not _txt:
                _txt = f"◈ {str(_row['speaker'] or '')} - {str(_row['message'] or '')[:40]}"
            _tp_activity.append({"text": _txt, "heat": _heat, "age": _age})
        # Agent tasks
        _AGENT_FLAVOR = {
            "POLARIS": [("FORGE","🍄 mycelial memory pass"),("DEBATE","📜 doctrine update"),("RESEARCH","🧪 stress-testing")],
            "IVARIS":  [("DEBATE","⚖️ auditing proposal"),("REJECT","⚖️ vetoing signal")],
            "NUGGET":  [("LATCH","⚡ latching signal"),("QUALIFY","“¡ freshness check")],
            "ORACLE":  [("QUALIFY","🔭 oracle scan"),("INGEST","§¬ DNA decode")],
            "GUARDIAN":[("HEALTH","🛡 risk shell check"),("HEAL","🛡 healing node")],
        }
        for _row in _tp_cog:
            _spk = str(_row["speaker"] or "").upper()
            _stage = str(_row["stage"] or "").upper()
            _age = _tp_now - float(_row["timestamp"] or _tp_now)
            if _spk in _AGENT_FLAVOR and _spk not in _tp_agent_tasks:
                for _trig, _flav in _AGENT_FLAVOR[_spk]:
                    if _trig in _stage:
                        _tp_agent_tasks[_spk] = {"task": _flav, "age": _age,
                            "tool": next((t["icon"] for t in _tp_tools if _tp_tool_states.get(t["id"],{}).get("agent")==_spk), "")}
                        break
    except Exception:
        pass
    # Baselines - always fire
    if "oracle_lens" not in _tp_tool_states:
        _tp_tool_states["oracle_lens"] = {"status":"ACTIVE","agent":"ORACLE","last":"scanning substrate","age":60}
    if "freshness_beacon" not in _tp_tool_states:
        _tp_tool_states["freshness_beacon"] = {"status":"ACTIVE","agent":"ORACLE","last":"signal age gate","age":60}
    if "risk_shell" not in _tp_tool_states:
        _tp_tool_states["risk_shell"] = {"status":"ACTIVE","agent":"GUARDIAN","last":"exposure check","age":60}
    if "execution_relay" not in _tp_tool_states:
        _tp_tool_states["execution_relay"] = {"status":"ACTIVE","agent":"NUGGET","last":"relay armed","age":60}

    # LEFT AGENT RAIL REMOVED - replaced by _render_agent_heartbeat_cards() above world section.
    # Old disconnected "awaiting signal" column removed per sign-off directive.
    # LAYOUT_20260614: golden lattice moved from the narrow right-hand column to
    # render UNDERNEATH the debate chamber, streamlined like the other sections.
    # Both are now full-width stacked containers; main_col is rendered first
    # (see reorder below), lattice_col second so it appears beneath.
    main_col = st.container()
    lattice_col = st.container()

    with lattice_col:
        _forge_rows = []
        try:
            with sqlite3.connect(str(DB_PATH), timeout=2.0) as _lc:
                _forge_rows = _lc.execute("SELECT id, proposal_type, suggested_action, unified_diff, forge_narrative, forge_checksum FROM polaris_proposals WHERE status IN ('forge_complete','HITL_REQUIRED','nugget_escalated') ORDER BY created_at DESC LIMIT 3").fetchall()
        except Exception: pass

        if _forge_rows:
            # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
            # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
            try:
                _runner_gold_pct = 75.0
                try:
                    if isinstance(locals().get("row"), dict):
                        _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
                except Exception:
                    _runner_gold_pct = 75.0
                if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
                    _state = "RUNNER"
                    _state_col = "#FFD700"
            except Exception:
                pass

            st.markdown(f"""<div class="golden-lattice">
                <div style="color:#FFD700;font-family:'Share Tech Mono',monospace;font-size:0.72rem;letter-spacing:3px;margin-bottom:14px;">✦ OPERATOR GATE - AWAITING SEAL</div>
            """, unsafe_allow_html=True)
            for _fr in _forge_rows:
                _fp_id, _fp_type, _fp_action, _fp_diff, _fp_narr, _fp_checksum = _fr[0], str(_fr[1] or ''), html.escape(str(_fr[2] or '')[:200]), html.escape(str(_fr[3] or '')[:600]), html.escape(str(_fr[4] or '')), str(_fr[5] or f"FORGE-{_fr[0]}")
                _narr_html = f'<div style="color:#FFD700;font-style:italic;font-size:0.75rem;margin-bottom:8px;">{_fp_narr}</div>' if _fp_narr else ''
                st.markdown(
                    '<div class="golden-lattice-card">' +
                    f'<div style="color:#FFD700;font-size:0.66rem;letter-spacing:2px;margin-bottom:6px;">{_fp_type}</div>' +
                    _narr_html +
                    f'<div style="color:#FFF;font-size:0.7rem;margin-bottom:8px;">{_fp_action}</div>' +
                    f'<div style="color:#FFD70066;font-size:0.66rem;margin-bottom:10px;font-family:Share Tech Mono,monospace;">{_fp_checksum}</div>' +
                    '</div>',
                    unsafe_allow_html=True
                )

                with st.form(key=f"seal_form_{_fp_id}", clear_on_submit=True):
                    _seal_input = st.text_input("SEAL CODE", type="password", label_visibility="collapsed", placeholder="ENTER SEAL CODE TO INTEGRATE")
                    _lc1, _lc2 = st.columns(2)
                    with _lc1: _seal_btn = st.form_submit_button("⚡ SEAL & INTEGRATE", type="primary", use_container_width=True)
                    with _lc2: _deny_btn = st.form_submit_button("✕ DISSOLVE", use_container_width=True)
                if _seal_btn:
                    try:
                        _raw = _seal_input.strip()
                        # Silently reject anything that isn't exactly 4 digits
                        if not (_raw.isdigit() and len(_raw) == 4):
                            st.error("SEAL REJECTED")
                        else:
                            with sqlite3.connect(str(DB_PATH), timeout=2.0) as _sc:
                                _scfg = _sc.execute("SELECT value FROM system_config WHERE key='OPERATOR_SEAL_CODE' LIMIT 1").fetchone()
                                _expected = str(_scfg[0]).strip() if _scfg else ""
                                if _raw == _expected and _expected != "":
                                    _sc.execute("UPDATE polaris_proposals SET status='approved' WHERE id=?", (_fp_id,))
                                    _sc.commit()
                                    st.session_state[f"evolution_{_fp_id}"] = True
                                    st.rerun()
                                else:
                                    st.error("SEAL REJECTED")
                    except Exception as _se: st.error(f"INTEGRATION FAILED: {_se}")
                if _deny_btn:
                    try:
                        with sqlite3.connect(str(DB_PATH), timeout=2.0) as _dc:
                            _dc.execute("UPDATE polaris_proposals SET status='rejected' WHERE id=?", (_fp_id,))
                            _dc.commit()
                        st.rerun()
                    except Exception as _de: st.error(f"DISSOLVE FAILED: {_de}")

                if st.session_state.get(f"evolution_{_fp_id}"):
                    st.markdown("""<div class="evolution-burst" id="mycelial-burst"></div><script>setTimeout(function() {var el = document.getElementById('mycelial-burst'); if (el) el.remove(); }, 3000);</script>""", unsafe_allow_html=True)
                    del st.session_state[f"evolution_{_fp_id}"]
            st.markdown("</div>", unsafe_allow_html=True)
        else:
            # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
            # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
            try:
                _runner_gold_pct = 75.0
                try:
                    if isinstance(locals().get("row"), dict):
                        _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
                except Exception:
                    _runner_gold_pct = 75.0
                if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
                    _state = "RUNNER"
                    _state_col = "#FFD700"
            except Exception:
                pass

            st.markdown(f"""<div style="border:1px solid #FFD70033;border-radius:16px;padding:20px;background:rgba(5,10,10,0.2);text-align:center;margin-top:10px;">
                <div style="color:#FFD70044;font-family:'Share Tech Mono',monospace;font-size:0.66rem;letter-spacing:3px;margin-bottom:8px;">GOLDEN LATTICE</div>
                <div style="color:#FFD70033;font-size:0.7rem;font-style:italic;">Awaiting Ascension...<br><span style="font-size:0.66rem;opacity:0.6;">Forge completes when System 1 reaches consensus</span></div>
            </div>""", unsafe_allow_html=True)
            # Oracle budget: Brave limit shown as flashing red dot in status bar only
            # No text here - keeps debate chamber at full width

        # ── EXECUTION CORTEX moved under the world beside Sovereign Commentary.

    with main_col:
        # ── Mycelial Activity Feed - slim strip above sanctum ──────────────
        if _tp_activity:
            st.markdown('<div style="margin-bottom:8px;">', unsafe_allow_html=True)
            for _act in _tp_activity[:5]:
                st.markdown(
                    f'<div class="tp-activity-item {_act["heat"]}" style="font-size:0.66rem;padding:3px 6px;margin-bottom:2px;">' +
                    _act["text"] +
                    f'<span style="color:rgba(200,216,232,0.2);font-size:0.66rem;float:right;">{_act["age"]}s</span></div>',
                    unsafe_allow_html=True
                )
            st.markdown('</div>', unsafe_allow_html=True)

        st.markdown("<div class='snty-sanctum-title'>THE SOVEREIGN SANCTUM · COUNCIL LIVE</div>", unsafe_allow_html=True)
        cortex_container = st.container()
        with cortex_container:

            if live_feed_df.empty:
                # SIGNOFF_WORLD_SINGLE_SOURCE_20260613:
                # The legacy cognitive canopy is an old world/canvas renderer.
                # Rendering it here created a second battlefield-style world
                # underneath the new World Engine whenever the feed was empty.
                # Keep the sanctum truthful and lightweight; the canonical world
                # iframe is mounted once above by render_sovereign_world().
                st.markdown(
                    "<div style='text-align:center;color:#8EF9FF66;padding:8px;"
                    "font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;'>"
                    "COUNCIL LINK READY · AWAITING NEXT TRANSCRIPT TURN</div>",
                    unsafe_allow_html=True,
                )
            else:
                st.markdown("<div style='text-align:center;color:#14F195;padding:20px;'>CORTEX LIVE - organism active</div>", unsafe_allow_html=True)

            # ── SOVEREIGN SANCTUM V1.5 ───────────────────────────────────────────────
            try:
                pass  # Sovereign world rendered above via _render_sovereign_world_html()
            except Exception:
                pass

                        # ── SECTION 2: LIVE DEBATE CHAMBER ──────────────────────────────────
            st.markdown(f"<div style='font-family:Orbitron,sans-serif;font-size:0.7rem;letter-spacing:4px;color:#9945FF;margin-bottom:8px;'>⚔ LIVE DEBATE CHAMBER</div>", unsafe_allow_html=True)
            # SIGNOFF_20260713: prepare the persistent workstream quietly.
            # The former full-width task-card wall competed with the Debate Chamber.
            # Keep every task and state, but render a slim rail beneath the transcript.
            _task_rows = pd.DataFrame()
            _task_error = ""
            try:
                with sqlite3.connect(str(DB_PATH), timeout=2.0) as _task_conn:
                    _task_tables = {
                        str(r[0]) for r in _task_conn.execute(
                            "SELECT name FROM sqlite_master WHERE type='table'"
                        ).fetchall()
                    }
                if "polaris_standing_tasks" in _task_tables:
                    _task_rows = query_db("""
                        SELECT id,title,domain,priority,status,stage,current_owner,
                               progress_pct,next_action,blocked_reason,updated_at
                        FROM polaris_standing_tasks
                        WHERE UPPER(COALESCE(status,'OPEN')) NOT IN ('ARCHIVED')
                        ORDER BY COALESCE(priority,9), COALESCE(updated_at,0) DESC
                        LIMIT 22
                    """)
                elif "standing_tasks" in _task_tables:
                    _task_rows = query_db("""
                        SELECT id,
                               COALESCE(title,task_name,task_key,'Standing task') AS title,
                               COALESCE(domain,task_type,'COUNCIL') AS domain,
                               COALESCE(priority,9) AS priority,
                               COALESCE(status,'ACTIVE') AS status,
                               COALESCE(last_outcome,'') AS stage,
                               COALESCE(claimed_by,owner,'') AS current_owner,
                               CASE WHEN UPPER(COALESCE(status,'')) IN ('DONE','COMPLETE') THEN 100
                                    WHEN claimed_by IS NOT NULL THEN 55 ELSE 15 END AS progress_pct,
                               COALESCE(description,acceptance_criteria,'') AS next_action,
                               CASE WHEN UPPER(COALESCE(last_outcome,''))='DEFERRED'
                                    THEN COALESCE(last_evidence_json,'') ELSE '' END AS blocked_reason,
                               COALESCE(updated_at,created_at,0) AS updated_at
                        FROM standing_tasks
                        WHERE UPPER(COALESCE(status,'ACTIVE')) NOT IN ('ARCHIVED')
                        ORDER BY COALESCE(priority,9), COALESCE(updated_at,created_at,0) DESC
                        LIMIT 22
                    """)
                else:
                    _task_error = "No standing-task table found"
            except Exception as _task_exc:
                _task_error = _sanitize_exception_text(str(_task_exc))

            _debate_html = "<div class='snty-debate-stage' style='max-height:560px;overflow-y:auto;padding:12px 10px;font-family:\"Share Tech Mono\",monospace;background:linear-gradient(180deg,rgba(7,3,20,.42),rgba(3,2,10,.18));border-top:1px solid rgba(153,69,255,.22);border-bottom:1px solid rgba(142,249,255,.12);margin-bottom:10px;'>"
            if not debate_df.empty:
                for idx, row in debate_df.iterrows():
                    spk = str(row.get('speaker', '')).upper()
                    _raw_verdict = _sanitize_exception_text(str(row.get('verdict_text', '') or ''))
                    _action_codes = ('RESEARCH_FIRST INITIATION', 'AUDIT_FIRST INITIATION',
                                     'DESIGN_FIRST INITIATION', 'current_state', 'oracle_evidence',
                                     'loop_detected', 'final_rejection', 'loop_detection')
                    if any(code in _raw_verdict for code in _action_codes) or len(_raw_verdict) < 30:
                        try:
                            import json as _jmod
                            _cj = row.get('content_json', '')
                            if _cj:
                                _cjd = _jmod.loads(str(_cj)) if isinstance(_cj, str) else _cj
                                _raw_verdict = (
                                    _cjd.get('polaris_summary') or
                                    _cjd.get('research_summary') or
                                    _cjd.get('proposal_summary') or
                                    _cjd.get('verdict') or
                                    _cjd.get('summary') or
                                    _cjd.get('rebuttal_summary') or
                                    _cjd.get('current_state_summary') or
                                    _raw_verdict
                                )
                        except Exception:
                            pass
                        _code_map = {
                            'RESEARCH_FIRST INITIATION': 'Beginning research phase - gathering evidence before debate',
                            'AUDIT_FIRST INITIATION': 'Beginning audit phase - reviewing existing data',
                            'DESIGN_FIRST INITIATION': 'Beginning design phase - architecting the solution',
                            'loop_detected': 'Debate loop detected - proposal has been seen too many times',
                            'final_rejection': 'Proposal rejected - could not reach consensus',
                            'per-proposal cap exceeded': 'This proposal has been debated too many times without resolution',
                        }
                        for code, readable in _code_map.items():
                            if code in _raw_verdict:
                                _raw_verdict = readable
                                break
                    msg = purify_links(_raw_verdict)
                    ts = _fmt_clock(row.get('logged_at', 0))
                    think = str(row.get('thinking_state', ''))
                    verd = str(row.get('verdict_type', ''))
                    icon = AGENT_EMOJIS.get(spk, "⬜")
                    col  = (C_CYAN if spk == "POLARIS" else C_IVY if spk == "IVARIS" else C_NUGGET if spk == "NUGGET" else "#14F195" if spk == "ORACLE" else C_IVY)
                    badge = ""
                    if think: badge += f"<span style='background:rgba(153,69,255,0.2);padding:2px 6px;border-radius:4px;font-size:0.66rem;margin-right:6px;'>{_thematic_label(think)}</span>"
                    if verd:
                        v_col = C_GREEN if "CONSENSUS" in verd.upper() or "APPROVED" in verd.upper() or "HARMONIC" in verd.upper() else C_RED
                        badge += f"<span style='background:{v_col}33;color:{v_col};border:1px solid {v_col};padding:2px 6px;border-radius:4px;font-size:0.66rem;'>{_thematic_label(verd)}</span>"
                    _prop_context = ''
                    try:
                        _pid = int(row.get('proposal_id', 0) or 0)
                        if _pid and not proposals_df.empty and 'id' in proposals_df.columns:
                            _prop_rows = proposals_df[proposals_df['id'] == _pid]
                            if not _prop_rows.empty:
                                _prop_text = str(_prop_rows.iloc[0].get('proposal_text', '') or '')
                                if _prop_text:
                                    _prop_context = _prop_text[:120].replace('\n', ' ').strip()
                                    if len(_prop_text) > 120: _prop_context += '...'
                    except Exception:
                        _prop_context = ''
                    _align = "left" if spk == "POLARIS" else "right"
                    _bg = "rgba(142,249,255,0.05)" if spk == "POLARIS" else "rgba(255,179,71,0.05)"
                    _border = f"border-left:3px solid {col};" if spk == "POLARIS" else f"border-right:3px solid {col};"
                    _grok_narr = html.escape(_sanitize_exception_text(str(row.get('grok_narrative', '') or '')))
                    name_class = "next-up" if idx == 0 else ""
                    text_class = "typewriter" if idx == 0 else ""
                    is_mastery = think == 'golden_mastery'
                    is_consensus = "consensus" in think.lower() or "harmonic" in think.lower() or "consensus" in verd.lower()
                    is_patch = any(kw in msg.lower() for kw in ("applied patch", "file edited", "file created", "integrated", "patch absorbed"))
                    _msg_class = f"arena-msg-{spk.lower()}" if spk in ("POLARIS","IVARIS","ORACLE","NUGGET") else ""
                    if _grok_narr:
                        _rich_body = f"<div style='color:{col};font-family:Rajdhani,sans-serif;font-size:0.95rem;font-style:italic;margin-bottom:6px;opacity:0.92;word-wrap:break-word;overflow-wrap:break-word;'>{_grok_narr}</div><div class='clinical-text {text_class} {_msg_class}' style='word-wrap:break-word;overflow-wrap:break-word;white-space:normal;'>{msg}</div>"
                    else:
                        _rich_body = f"<div class='{text_class} {_msg_class}' style='font-size:0.9rem;word-wrap:break-word;overflow-wrap:break-word;white-space:normal;'>{msg}</div>"
                    if spk == "ORACLE":
                        _oq  = html.escape(str(row.get('oracle_query', '') or ''))
                        _oc  = str(row.get('oracle_confirmed', '') or '')
                        _cc  = "#14F195" if str(_oc).lower() == "true" else ("#FF073A" if str(_oc).lower() == "false" else "#888")
                        _cl  = "CONFIRMED" if str(_oc).lower() == "true" else ("NOT CONFIRMED" if str(_oc).lower() == "false" else "INCONCLUSIVE")
                        _rich_body = f"<div style='color:#14F195;font-size:0.75rem;letter-spacing:1px;margin-bottom:4px;'>SEARCH QUERY</div><div style='color:#FFF;font-family:Share Tech Mono,monospace;font-size:0.8rem;background:rgba(20,241,149,0.07);padding:6px 10px;border-radius:6px;margin-bottom:8px;'>{_oq or '-'}</div><div style='display:inline-block;padding:2px 10px;border-radius:4px;font-size:0.7rem;font-weight:700;background:{_cc}22;color:{_cc};border:1px solid {_cc};margin-bottom:8px;'>{_cl}</div>"
                    elif spk == "NUGGET":
                        _nw = str(row.get('nugget_winner', '') or '-')
                        _nr = html.escape(str(row.get('nugget_reason', '') or '-'))
                        _nc = str(row.get('nugget_confidence', '') or '')
                        _nn = str(row.get('nugget_next', '') or '-')
                        _nw_col = "#14F195" if _nw == "POLARIS" else ("#FF073A" if _nw == "IVARIS" else "#888")
                        try: _nc_f = f"{float(_nc):.2f}"
                        except Exception: _nc_f = _nc or "-"
                        _rich_body = f"<div style='display:flex;gap:10px;flex-wrap:wrap;margin-bottom:8px;'><span style='background:{_nw_col}22;color:{_nw_col};border:1px solid {_nw_col};padding:2px 10px;border-radius:4px;font-size:0.7rem;font-weight:700;'>WINNER: {_nw}</span><span style='background:rgba(153,69,255,0.15);color:#9945FF;border:1px solid #9945FF55;padding:2px 10px;border-radius:4px;font-size:0.7rem;'>CONF: {_nc_f}</span><span style='background:rgba(255,255,255,0.05);color:#ccc;border:1px solid #ffffff22;padding:2px 10px;border-radius:4px;font-size:0.7rem;'>NEXT: {_nn}</span></div><div style='color:#FFF;font-family:Rajdhani,sans-serif;font-size:0.9rem;' class='{text_class}'>{_nr}</div>"
                    mastery_class = "golden-masterpiece" if is_mastery else ""
                    arena_class = "arena-consensus" if is_consensus else ("arena-patch" if is_patch else "")
                    _badge_div = f'<div style="margin-bottom:6px;">{badge}</div>' if badge else ''
                    _mtag_info = _MODEL_TAGS.get(spk, ('', ''))
                    _mtag_leading = ' leading' if (is_consensus or (think == 'critiquing' and idx == 0)) else ''
                    _mtag_html = f'<span class="model-tag {_mtag_info[1]}{_mtag_leading}" title="{_mtag_info[0]}">{_mtag_info[0]}</span>' if _mtag_info[0] else ''
                    _side_margin = 'margin-right:51%;' if spk in ('POLARIS','ORACLE') else 'margin-left:51%;'
                    _debate_html += f"<article class='snty-debate-turn unfold {mastery_class} {arena_class}' style='{_side_margin}margin-bottom:9px;padding:9px 11px;{_border}background:{_bg};text-align:left;word-wrap:break-word;overflow-wrap:break-word;overflow:hidden;'><header style='display:flex;justify-content:space-between;gap:8px;align-items:center;margin-bottom:4px;'><span class='{name_class} agent-{spk.lower()}' style='color:{col};font-weight:700;letter-spacing:1px;'>{icon} {spk}{_mtag_html}</span><span style='color:#777;font-size:0.66rem;'>{ts}</span></header>{_badge_div}{_rich_body}</article>"
            else:
                # SIGNOFF_CHAMBER_TRUTH_20260714: an empty chamber must explain
                # itself. Derive IDLE / STALE / BLOCKED from real producer
                # heartbeats and standing-task state — never a bare decorative
                # void and never a fabricated transcript.
                _chamber_state = "COUNCIL IDLE — NO ACTIVE TASK"
                _chamber_col = "#8b86a0"
                _chamber_note = ("No transcript turns recorded. The chamber populates when "
                                 "the Council claims a standing task and the producer chain pulses.")
                try:
                    _ch_hb = query_db(
                        "SELECT service_name, status, note, last_pulse FROM system_heartbeat "
                        "WHERE service_name IN ('council_chamber_bridge','debate_engine',"
                        "'sovereign_governor','polaris','council_execution_spine')"
                    )
                    _ch_now = time.time()
                    _ch_blocked, _ch_stale, _ch_alive = [], [], []
                    if _ch_hb is not None and not _ch_hb.empty:
                        for _, _hrow in _ch_hb.iterrows():
                            _svc = str(_hrow.get('service_name', '') or '')
                            _hst = str(_hrow.get('status', '') or '').upper()
                            try:
                                _age = _ch_now - float(_hrow.get('last_pulse') or 0)
                            except Exception:
                                _age = 1e12
                            _hnote = str(_hrow.get('note', '') or '')
                            if _hst in ('ERROR', 'BLOCKED', 'DEAD', 'FAILED'):
                                _ch_blocked.append((_svc, _hst, _hnote))
                            elif _hst == 'WARN':
                                # SIGNOFF_CHAMBER_WARN_20260715: a WARN pulse is a
                                # degraded-but-alive producer (the bridge writes WARN
                                # with the swallowed exception text on a transient
                                # cycle error). Rendering it as COUNCIL BLOCKED was
                                # the source of false red "COUNCIL BLOCKED — <svc>
                                # <err>" banners. It now renders amber DEGRADED with
                                # the real exception, and only ERROR/BLOCKED/DEAD/
                                # FAILED stay red.
                                _ch_stale.append((_svc, 0.0, _hnote))
                            else:
                                try:
                                    from ui.theme import service_heartbeat_thresholds as _hb_thresholds
                                    _fresh_s, _aging_s = _hb_thresholds(_svc)
                                except Exception:
                                    _aging_s = 180.0
                                if _age > _aging_s:
                                    _ch_stale.append((_svc, _age))
                                else:
                                    _ch_alive.append(_svc)
                    if _ch_blocked:
                        _svc, _hst, _hnote = _ch_blocked[0]
                        _chamber_state = f"COUNCIL BLOCKED — {html.escape(_svc)} {html.escape(_hst)}"
                        _chamber_col = "#FF073A"
                        _reason = _sanitize_exception_text(_hnote)[:220].strip()
                        _chamber_note = html.escape(_reason) if _reason else \
                            "Producer reported a failure with no note. Open the Glassbox for the full trace."
                    elif _ch_stale and not _ch_alive:
                        _st_entry = _ch_stale[0]
                        _svc, _age = _st_entry[0], _st_entry[1]
                        _warn_note = _st_entry[2] if len(_st_entry) > 2 else ""
                        if _warn_note:
                            _chamber_state = f"COUNCIL DEGRADED — {html.escape(_svc)} WARN"
                            _chamber_col = "#FFB347"
                            _chamber_note = html.escape(
                                _sanitize_exception_text(_warn_note)[:220]
                            ) or "Producer reported a transient warning."
                        else:
                            _age_lbl = "no pulse recorded" if _age > 1e9 else f"last pulse {int(_age)}s ago"
                            _chamber_state = f"COUNCIL STALE — {html.escape(_svc)}"
                            _chamber_col = "#FFB347"
                            _chamber_note = (f"{_age_lbl}. The transcript resumes when the producer "
                                             "heartbeat returns; no synthetic turns are shown in its place.")
                        # SIGNOFF_CHAMBER_CLAIM_TRUTH_20260715: show the real task +
                        # claim age alongside the state (directive: "Polaris must show
                        # real task, claim age, heartbeat, latest output, and exception").
                        try:
                            _claim_df = query_db(
                                "SELECT title, status, claimed_by, claim_until, updated_at "
                                "FROM polaris_standing_tasks "
                                "WHERE claimed_by IS NOT NULL AND claimed_by<>'' "
                                "ORDER BY updated_at DESC LIMIT 1")
                            if _claim_df is not None and not _claim_df.empty:
                                _cr = _claim_df.iloc[0]
                                _c_age = time.time() - float(_cr.get('updated_at') or time.time())
                                _chamber_note += (
                                    f" · active claim: {html.escape(str(_cr.get('title',''))[:60])} "
                                    f"by {html.escape(str(_cr.get('claimed_by','?')))} "
                                    f"({int(_c_age)}s ago)")
                        except Exception:
                            pass
                    elif _ch_hb is None or _ch_hb.empty:
                        _chamber_state = "COUNCIL STALE — NO PRODUCER HEARTBEAT"
                        _chamber_col = "#FFB347"
                        _chamber_note = ("system_heartbeat has no Council producer rows yet. "
                                         "The continuity bridge writes them on boot.")
                except Exception as _ch_exc:
                    _chamber_state = "COUNCIL STATE UNKNOWN"
                    _chamber_col = "#FFB347"
                    _chamber_note = html.escape(_sanitize_exception_text(str(_ch_exc))[:220])
                _debate_html += (
                    "<div style='text-align:center;padding:26px 14px;'>"
                    f"<div style='color:{_chamber_col};font-family:Orbitron,sans-serif;"
                    "font-size:.78rem;letter-spacing:3px;margin-bottom:8px;'>"
                    f"{_chamber_state}</div>"
                    "<div style='color:#8b86a0;font-family:Share Tech Mono,monospace;"
                    "font-size:0.66rem;letter-spacing:1px;line-height:1.6;max-width:520px;"
                    f"margin:0 auto;'>{_chamber_note}</div></div>"
                )
            _debate_html += "</div>"
            st.markdown(_debate_html, unsafe_allow_html=True)


            # Compact Council Workstream — all functionality retained, card wall removed.
            try:
                _state_col = {
                    'ACTIVE':'#FFD700','RESEARCHING':'#4DA3FF','DEBATING':'#9945FF',
                    'BUILDING':'#8EF9FF','VALIDATING':'#14F195','DONE':'#14F195',
                    'BLOCKED':'#FF073A','OPEN':'#D8D8E8','CLAIMED':'#FFB347'
                }
                _domain_col = {'SOLANA':'#9945FF','SUBSTRATE':'#8EF9FF','COUNCIL':'#FFD700','INFRA':'#8EF9FF','ARCHIVE':'#B8A7FF'}
                _counts = {'TOTAL': 0, 'ACTIVE': 0, 'DEBATING': 0, 'BLOCKED': 0, 'DONE': 0}
                if _task_rows is not None and not _task_rows.empty:
                    _counts['TOTAL'] = int(len(_task_rows))
                    _statuses = _task_rows['status'].fillna('OPEN').astype(str).str.upper()
                    _counts['ACTIVE'] = int(_statuses.isin(['ACTIVE','RESEARCHING','BUILDING','VALIDATING','CLAIMED']).sum())
                    _counts['DEBATING'] = int((_statuses == 'DEBATING').sum())
                    _counts['BLOCKED'] = int((_statuses == 'BLOCKED').sum())
                    _counts['DONE'] = int(_statuses.isin(['DONE','COMPLETED']).sum())

                _rail = (
                    "<div style='margin:0 0 12px;border:1px solid rgba(153,69,255,.18);"
                    "border-radius:7px;background:rgba(5,2,16,.42);overflow:hidden;'>"
                    "<div style='display:flex;align-items:center;justify-content:space-between;gap:10px;"
                    "padding:7px 10px;border-bottom:1px solid rgba(142,249,255,.08);'>"
                    "<span style='font-family:Orbitron,sans-serif;font-size:0.66rem;letter-spacing:2.4px;color:#8EF9FF;'>"
                    "COUNCIL WORKSTREAM</span>"
                    f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#8b86a0;'>"
                    f"{_counts['TOTAL']} STANDING&nbsp;&nbsp;·&nbsp;&nbsp;"
                    f"<b style='color:#FFD700'>{_counts['ACTIVE']} ACTIVE</b>&nbsp;&nbsp;·&nbsp;&nbsp;"
                    f"<b style='color:#9945FF'>{_counts['DEBATING']} DEBATING</b>&nbsp;&nbsp;·&nbsp;&nbsp;"
                    f"<b style='color:#FF073A'>{_counts['BLOCKED']} BLOCKED</b></span></div>"
                )
                if _task_error:
                    _rail += f"<div style='padding:8px 10px;color:#FFB347;font-family:Share Tech Mono,monospace;font-size:0.66rem;'>WORKSTREAM READ ERROR · {html.escape(_task_error)}</div>"
                elif _task_rows is None or _task_rows.empty:
                    _rail += "<div style='padding:9px 10px;color:#777;font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;'>// NO STANDING TASKS — CONTINUITY BRIDGE WILL SEED ON BOOT //</div>"
                else:
                    _rail += "<details style='padding:0 10px 8px;'><summary style='cursor:pointer;list-style:none;padding:7px 0 3px;color:#777;font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1.5px;'>VIEW TASK LEDGER</summary><div style='margin-top:4px;border-top:1px solid rgba(255,255,255,.05);'>"
                    for _, _tr in _task_rows.iterrows():
                        _st = str(_tr.get('status','OPEN') or 'OPEN').upper()
                        _dm = str(_tr.get('domain','COUNCIL') or 'COUNCIL').upper()
                        _sc = _state_col.get(_st,'#D8D8E8'); _dc = _domain_col.get(_dm,'#D8D8E8')
                        _title = html.escape(str(_tr.get('title','Standing task') or 'Standing task'))
                        _owner = html.escape(str(_tr.get('current_owner','COUNCIL') or 'COUNCIL').upper())
                        _stage = html.escape(str(_tr.get('stage','seeded') or 'seeded'))
                        _next = html.escape(str(_tr.get('next_action','Awaiting next action') or 'Awaiting next action'))
                        try:
                            _pct = max(0, min(100, float(_tr.get('progress_pct', 0) or 0)))
                        except Exception:
                            _pct = 0
                        # Seeded/default percentages made every task look frozen at 25%.
                        # Preserve genuine progress, otherwise derive a truthful stage fallback.
                        _stage_key = f"{_st} {_stage}".upper()
                        if _pct in (0, 15, 25):
                            _fallback_progress = (
                                100 if any(x in _stage_key for x in ("DONE", "COMPLETE")) else
                                85 if "VALIDAT" in _stage_key else
                                65 if "BUILD" in _stage_key else
                                45 if "DEBAT" in _stage_key else
                                25 if "RESEARCH" in _stage_key else
                                15
                            )
                            _pct = _fallback_progress
                        _rail += (
                            f"<details style='border-bottom:1px solid rgba(255,255,255,.045);padding:7px 0;'>"
                            f"<summary style='cursor:pointer;list-style:none;display:grid;grid-template-columns:minmax(0,1fr) auto;gap:10px;align-items:center;'>"
                            f"<span style='min-width:0;color:#d8d7e2;font-family:Share Tech Mono,monospace;font-size:.82rem;line-height:1.45;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;'>"
                            f"<b style='color:{_dc};font-weight:600'>{_owner}</b>&nbsp;·&nbsp;{_title}</span>"
                            f"<span style='color:{_sc};font-family:Share Tech Mono,monospace;font-size:.72rem;white-space:nowrap;'>● {_st}&nbsp;&nbsp;{_pct:.0f}%</span></summary>"
                            f"<div style='padding:6px 0 2px 13px;color:#8f8a9d;font-family:Share Tech Mono,monospace;font-size:.76rem;line-height:1.58;border-left:1px solid {_dc}55;'>"
                            f"{_dm} · {_stage}<br><span style='color:#bbb7c8'>NEXT</span> · {_next}</div></details>"
                        )
                    _rail += "</div></details>"
                _rail += "</div>"
                st.markdown(_rail, unsafe_allow_html=True)
            except Exception as _rail_exc:
                st.markdown(
                    f"<div style='margin:0 0 12px;padding:8px 10px;border:1px solid rgba(255,179,71,.2);"
                    f"color:#FFB347;font-family:Share Tech Mono,monospace;font-size:0.66rem;'>"
                    f"COUNCIL WORKSTREAM UNAVAILABLE · {html.escape(_sanitize_exception_text(str(_rail_exc)))}</div>",
                    unsafe_allow_html=True,
                )

            # ── GOLDEN LATTICE - proposals awaiting operator action ───────────────
            # Glassbox: dimmed code preview shows what the lattice is evaluating
            try:
                _lattice_proposals = query_db("""
                    SELECT id, proposal_type, proposal_domain, proposal_text,
                           status, created_at, project_key
                    FROM polaris_proposals
                    WHERE status IN ('approved','nugget_escalated','HITL_REQUIRED','pending_replay')
                    ORDER BY created_at DESC LIMIT 8
                """)
                # Golden Lattice moved to standalone render_golden_lattice() call
            except Exception:
                pass

            # ── SECTION 3: RAW STRINGS & TRADE SIGNALS (inside developer expander only) ──
        with st.expander("⚙️ DEVELOPER — RAW SIGNAL SUBSTRATE", expanded=False):
            st.markdown(
                "<div style='font-family:Share Tech Mono;font-size:0.66rem;color:rgba(142,249,255,0.45);margin-bottom:6px;'>"
                "// RAW SIGNAL SUBSTRATE - DEBUG ONLY //</div>",
                unsafe_allow_html=True,
            )
            
            _raw_html = "<div style='height:480px;overflow-y:auto;padding:16px;font-family:\"Share Tech Mono\",monospace;background:rgba(5,2,16,0.4);border-radius:12px;border:1px solid rgba(142,249,255,0.15);'>"
            if not live_feed_df.empty:
                for _, _frow in live_feed_df.iterrows():
                    _stg = str(_frow.get('stage', 'SYS')).upper()
                    _tok = html.escape(str(_frow.get('token', '')) or 'SYS')
                    _msg = purify_links(str(_frow.get('message', '')))
                    _ts  = str(_frow.get('timestamp', ''))[-8:][:5]
                    _display_stg = "⚡ AXON" if _stg == "EXECUTOR" else ("🕸️ RHIZA" if _stg in ("REPLAY","REPLAY_ENGINE") else _stg)
                    _stg_col = {"EXECUTOR": "#14F195", "AXON": "#14F195", "RHIZA": "#9945FF", "SUPERVISOR": "#8EF9FF", "POLARIS": "#9945FF", "SYSTEM": "#FFB347", "DEBATE": "#9945FF", "REPLAY": "#9945FF"}.get(_stg, "#8EF9FF")
                    if "latched" in _msg.lower(): _stg_col = "#FFD700"
                    elif "vetoed" in _msg.lower() or "rejected" in _msg.lower(): _stg_col = "#FF073A"
                    elif "approved" in _msg.lower() or "passed" in _msg.lower(): _stg_col = "#14F195"
                    _raw_html += f"<div style='margin-bottom:10px;padding:10px 12px;border-left:3px solid {_stg_col};background:rgba(255,255,255,0.02);border-radius:0 6px 6px 0;'><span style='color:{_stg_col};font-weight:bold;letter-spacing:1px;'>{_display_stg}</span><span style='float:right;color:#FFD700;font-size:.75rem;'>{_ts}</span><br><span style='color:#9945FF;'>{_tok}</span><span style='color:#888;'> - </span><span style='color:#FFF;'>{_msg}</span></div>"
            else:
                _raw_html += "<div style='color:#888;text-align:center;padding:28px 12px;'>// RAW STREAM SILENT //</div>"
            _raw_html += "</div>"
            st.markdown(_raw_html, unsafe_allow_html=True)

    # ── LINEAR CORTEX SCROLL - COGNITION LOG in collapsed expander (mobile perf) ─
    if True:  # preserves indentation level
        # ── COGNITION LOG - collapsed by default, compact on mobile ──────────
        # DEDUP_20260615b: COGNITION & SELF-HEALING accordion removed - it
        # duplicated the RAW SIGNAL SUBSTRATE feed above (both surfaced the same
        # SUPERVISOR veto / heal lines). Heal + health events remain visible in
        # the System Vitalities & Freshness glassbox. One developer feed only.
        if False:  # neutralized duplicate
            pass

        # Vitality/gates/substrate/conviction moved to page level (after motor output)

    
    if _query_errors:
        import datetime as _dtq
        st.markdown(f"<div style='margin:8px 0;padding:10px 14px;background:rgba(255,7,58,0.08);border:1px solid rgba(255,7,58,0.4);border-radius:8px;font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;'><span style='color:{C_RED};letter-spacing:2px;'>DB QUERY ERRORS - PANELS MAY BE BLANK DUE TO FAILURE NOT EMPTY DATA</span>", unsafe_allow_html=True)
        for _ets, _emsg, _esql in _query_errors[-5:]:
            try: _etime = _dtq.datetime.fromtimestamp(_ets).strftime("%H:%M:%S")
            except Exception: _etime = "?"
            st.markdown(f"<div style='color:rgba(255,7,58,0.8);margin-top:4px;'><span style='color:#888;'>{_etime}</span> <span style='color:{C_RED};'>{html.escape(_emsg)}</span></div>", unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)



# ── LAUNCH FRESHNESS GLASSBOX DEFINITIONS - must be defined before page render ──
# ══════════════════════════════════════════════════════════════════════════════
# LAUNCH FRESHNESS GLASSBOX - operational trust telemetry
# Shows real DB-backed freshness/maintenance state.
# Placed immediately after Motor Output per sign-off directive.
# Schema-tolerant: any missing table/column silently shows UNKNOWN.
# Performance: one cached DB read, cap 20 events, no continuous loops.
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_data(ttl=18, show_spinner=False)
def _fetch_glassbox_data(now_bucket: int) -> dict:
    """
    Single DB open, cached 18s.
    now_bucket = int(time/18) so cache invalidates on 18s boundary.
    Returns dict of freshness telemetry.
    """
    import time as _gt, sqlite3 as _gq
    _now = _gt.time()

    def _tbl_exists(conn, tbl):
        try:
            return conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (tbl,)
            ).fetchone() is not None
        except Exception:
            return False

    def _col_exists(conn, tbl, col):
        try:
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
            return col in cols
        except Exception:
            return False

    def _safe(conn, sql, params=(), default=None):
        try:
            return conn.execute(sql, params).fetchone()
        except Exception:
            return default

    result = {
        "rows": [],       # component freshness rows
        "events": [],     # recent real log events (max 20)
        "wal_mb": None,
        "now": _now,
    }

    try:
        conn = _gq.connect(str(DB_PATH), timeout=2.0)
        conn.execute("PRAGMA busy_timeout=1500")
        conn.row_factory = _gq.Row

        # ── 1. COMPONENT FRESHNESS ROWS ───────────────────────────────────────
        components = []

        # Freshness Enforcer - heartbeat
        if _tbl_exists(conn, "system_heartbeat"):
            r = _safe(conn,
                "SELECT last_pulse, status, note FROM system_heartbeat "
                "WHERE service_name IN ('freshness_enforcer','market_intelligence','neural_supervisor') "
                "ORDER BY last_pulse DESC LIMIT 1"
            )
            if r:
                age = int(_now - float(r[0] or 0))
                status = "ONLINE" if age <= 90 else ("DEGRADED" if age <= 300 else "STALE")
                components.append(("Freshness Enforcer", status, age,
                    str(r[2] or "")[:60] or "heartbeat active"))
            else:
                components.append(("Freshness Enforcer", "UNKNOWN", None, "no heartbeat row found"))

            # Prelaunch Guard
            r2 = _safe(conn,
                "SELECT last_pulse, note FROM system_heartbeat "
                "WHERE service_name IN ('prelaunch_guard','guardian','system_guardian') "
                "ORDER BY last_pulse DESC LIMIT 1"
            )
            if r2:
                age2 = int(_now - float(r2[0] or 0))
                status2 = "PASS" if age2 <= 120 else ("WARN" if age2 <= 300 else "BLOCKED")
                components.append(("Prelaunch Guard", status2, age2,
                    str(r2[1] or "")[:60] or "launch freshness checked"))
            else:
                components.append(("Prelaunch Guard", "UNKNOWN", None, "no heartbeat row found"))

            # Market Intelligence
            r3 = _safe(conn,
                "SELECT last_pulse, note FROM system_heartbeat "
                "WHERE service_name='market_intelligence' "
                "ORDER BY last_pulse DESC LIMIT 1"
            )
            if r3:
                age3 = int(_now - float(r3[0] or 0))
                status3 = "FRESH" if age3 <= 90 else ("DEGRADED" if age3 <= 300 else "STALE")
                components.append(("Market Intelligence", status3, age3,
                    str(r3[1] or "")[:60] or "processing candidates"))
            else:
                components.append(("Market Intelligence", "UNKNOWN", None, "no heartbeat row found"))

            # Oracle price writes
            r4 = _safe(conn,
                "SELECT last_pulse, note FROM system_heartbeat "
                "WHERE service_name IN ('ws_price_oracle','oracle_autoheal') "
                "ORDER BY last_pulse DESC LIMIT 1"
            )
            if r4:
                age4 = int(_now - float(r4[0] or 0))
                status4 = "FRESH" if age4 <= 60 else ("STALE" if age4 <= 180 else "DEAD")
                components.append(("Oracle Price Writes", status4, age4,
                    str(r4[1] or "")[:60] or "MTM ticks writing"))
            else:
                components.append(("Oracle Price Writes", "UNKNOWN", None, "no heartbeat row found"))

            # Sovereign Governor
            r5 = _safe(conn,
                "SELECT last_pulse, note FROM system_heartbeat "
                "WHERE service_name IN ('sovereign_governor','governor') "
                "ORDER BY last_pulse DESC LIMIT 1"
            )
            if r5:
                age5 = int(_now - float(r5[0] or 0))
                status5 = "ACTIVE" if age5 <= 120 else ("STALE" if age5 <= 420 else "OFFLINE")
                components.append(("Governor", status5, age5,
                    str(r5[1] or "")[:60] or "governance active"))
            else:
                components.append(("Governor", "UNKNOWN", None, "no heartbeat row found"))
        else:
            for name in ("Freshness Enforcer", "Prelaunch Guard", "Market Intelligence",
                         "Oracle Price Writes", "Governor"):
                components.append((name, "UNKNOWN", None, "system_heartbeat table absent"))

        # Signal Gate — prefer the dedicated sensor's reasoned verdict.
        # Never call a healthy, actively-refreshing feed "STARVED" merely
        # because no candidate currently passes qualification.
        _sg_loaded = False
        for _intel_path in INTEL_DB_PATHS:
            try:
                with sqlite3.connect(str(_intel_path), timeout=1.0) as _ic:
                    _ir = _ic.execute(
                        "SELECT state,reason,fresh_60s,fresh_300s,updated_at "
                        "FROM signal_gate_state WHERE id=1"
                    ).fetchone()
                if _ir and (_now - float(_ir[4] or 0)) <= 120:
                    _state = str(_ir[0] or "UNKNOWN")
                    _label = {
                        "PASSING": "PASSING",
                        "IDLE_NO_FLOW": "FILTERING",
                        "VETO_DOMINATED": "FILTERING",
                        "SENSOR_MISMATCH": "DEGRADED",
                        "STARVED_STALE_SOURCE": "STARVED",
                        "STARVED_SERVICE_DOWN": "STARVED",
                    }.get(_state, "UNKNOWN")
                    components.append((
                        "Signal Gate", _label, None,
                        str(_ir[1] or f"fresh {_ir[2]}/{_ir[3]} @60/300s")[:100]
                    ))
                    _sg_loaded = True
                    break
            except Exception:
                continue

        if not _sg_loaded and _tbl_exists(conn, "market_snapshots"):
            _has_quality = _col_exists(conn, "market_snapshots", "quality_status")
            _has_fresh = _col_exists(conn, "market_snapshots", "price_updated_at")
            if _has_quality and _has_fresh:
                fresh_all = _safe(conn,
                    "SELECT COUNT(*) FROM market_snapshots WHERE price_updated_at > ?",
                    (_now - 120,)
                )
                fresh_q = _safe(conn,
                    "SELECT COUNT(*) FROM market_snapshots "
                    "WHERE quality_status='qualified' AND price_updated_at > ?",
                    (_now - 120,)
                )
                fresh_all_n = int(fresh_all[0] if fresh_all else 0)
                fresh_n = int(fresh_q[0] if fresh_q else 0)
                if fresh_n > 0:
                    sg_status, sg_note = "PASSING", f"{fresh_n} fresh qualified candidates"
                elif fresh_all_n > 0:
                    sg_status, sg_note = "FILTERING", (
                        f"{fresh_all_n} fresh snapshots / 0 qualified — input healthy"
                    )
                else:
                    sg_status, sg_note = "STARVED", "0 fresh snapshots in 120s"
                components.append(("Signal Gate", sg_status, None, sg_note))
            else:
                components.append(("Signal Gate", "UNKNOWN", None, "schema columns missing"))
        elif not _sg_loaded:
            components.append(("Signal Gate", "UNKNOWN", None, "market_snapshots absent"))

        # Golden Lattice Queue
        if _tbl_exists(conn, "polaris_proposals"):
            lat_r = _safe(conn,
                "SELECT COUNT(*) FROM polaris_proposals "
                "WHERE status IN ('approved','HITL_REQUIRED','nugget_escalated')"
            )
            lat_open = _safe(conn,
                "SELECT COUNT(*) FROM polaris_proposals WHERE status IN ('open','debating')"
            )
            lat_n   = int(lat_r[0] if lat_r else 0)
            lat_op  = int(lat_open[0] if lat_open else 0)
            if lat_n > 0:
                components.append(("Golden Lattice Queue", "ABSORBING",
                    None, f"{lat_n} awaiting seal / {lat_op} debating"))
            elif lat_op > 0:
                components.append(("Golden Lattice Queue", "DEBATING",
                    None, f"{lat_op} proposals in debate"))
            else:
                components.append(("Golden Lattice Queue", "IDLE", None, "no pending proposals"))
        else:
            components.append(("Golden Lattice Queue", "UNKNOWN", None, "polaris_proposals absent"))

        # DB WAL / Checkpoint
        try:
            _wal_path = DB_PATH.parent / (DB_PATH.name + "-wal")
            if _wal_path.exists():
                _wal_mb = round(_wal_path.stat().st_size / 1_048_576, 1)
                _wal_status = "HEALTHY" if _wal_mb < 50 else ("WARN" if _wal_mb < 150 else "NEEDS CHECKPOINT")
                components.append(("DB WAL / Checkpoint", _wal_status, None,
                    f"WAL {_wal_mb}MB {'- checkpoint recommended' if _wal_mb >= 50 else '- healthy'}"))
                result["wal_mb"] = _wal_mb
            else:
                components.append(("DB WAL / Checkpoint", "HEALTHY", None, "WAL not present / checkpointed"))
        except Exception:
            components.append(("DB WAL / Checkpoint", "UNKNOWN", None, "WAL size check failed"))

        result["rows"] = components

        # ── 2. REAL FRESHNESS EVENTS from cognition_log ───────────────────────
        if _tbl_exists(conn, "cognition_log"):
            _has_ts = _col_exists(conn, "cognition_log", "timestamp")
            _has_stage = _col_exists(conn, "cognition_log", "stage")
            _has_msg = _col_exists(conn, "cognition_log", "message")
            if _has_ts and _has_stage and _has_msg:
                ev_rows = conn.execute("""
                    SELECT stage, message, timestamp FROM cognition_log
                    WHERE timestamp > ?
                      AND (
                        stage IN ('GUARDIAN','HEALTH','HEALER','ORACLE','SUPERVISOR',
                                  'FRESHNESS','PRELAUNCH','SYSTEM','LATCH','GUARDIAN_HEAL',
                                  'AUTO_HEAL','HEAL','EXECUTOR','DEBATE')
                        OR message LIKE '%fresh%'
                        OR message LIKE '%stale%'
                        OR message LIKE '%recycle%'
                        OR message LIKE '%repair%'
                        OR message LIKE '%golden%'
                        OR message LIKE '%checkpoint%'
                        OR message LIKE '%vetoed%'
                        OR message LIKE '%heal%'
                        OR message LIKE '%blocked%'
                        OR message LIKE '%restored%'
                      )
                    ORDER BY timestamp DESC LIMIT 20
                """, (_now - 600,)).fetchall()
                for ev in ev_rows:
                    age = int(_now - float(ev["timestamp"] or _now))
                    result["events"].append({
                        "stage": str(ev["stage"] or "SYSTEM")[:14],
                        "msg": str(ev["message"] or "")[:90],
                        "age": age,
                    })

        conn.close()
    except Exception:
        pass

    return result


def render_launch_freshness_glassbox() -> None:
    """
    LAUNCH FRESHNESS GLASSBOX - lightweight operational trust panel.
    Shows real DB-backed freshness/maintenance state.
    Schema-tolerant. Max 20 events. No continuous loops.
    """
    import time as _gbt, html as _gbh
    _now = _gbt.time()

    data = _fetch_glassbox_data(int(_now / 18))
    rows = data.get("rows", [])
    events = data.get("events", [])

    # ── Status colour map ─────────────────────────────────────────────────────
    _STATUS_COL = {
        "ONLINE": "#14F195", "PASS": "#14F195", "FRESH": "#14F195",
        "ACTIVE": "#14F195", "HEALTHY": "#14F195", "PASSING": "#14F195",
        "ABSORBING": "#FFD700", "DEBATING": "#FFD700", "IDLE": "#9945FF",
        "DEGRADED": "#FFB347", "WARN": "#FFB347", "STALE": "#FFB347",
        "BLOCKED": "#FF073A", "STARVED": "#FF073A", "DEAD": "#FF073A",
        "NEEDS CHECKPOINT": "#FF073A", "OFFLINE": "#FF073A",
        "UNKNOWN": "#555555",
    }

    def _status_col(s):
        return _STATUS_COL.get(s, "#8EF9FF")

    def _age_str(age):
        if age is None:
            return "-"
        if age < 60:
            return f"{age}s ago"
        if age < 3600:
            return f"{age // 60}m ago"
        return f"{age // 3600}h ago"

    # ── Header ────────────────────────────────────────────────────────────────
    st.markdown(
        "<div style='margin-top:12px;padding:12px 16px 4px;"
        "border:1px solid rgba(20,241,149,0.2);border-radius:12px 12px 0 0;"
        "background:rgba(5,2,16,0.88);'>"
        "<div style='font-family:Orbitron,sans-serif;font-size:0.66rem;"
        "letter-spacing:4px;color:#14F195;margin-bottom:10px;'>"
        "⬡ SYSTEM VITALITIES & FRESHNESS GLASSBOX</div>",
        unsafe_allow_html=True,
    )

    # ── Component rows ────────────────────────────────────────────────────────
    rows_html = ""
    for name, status, age, note in rows:
        col   = _status_col(status)
        age_s = _age_str(age)
        dot_glow = f"0 0 5px {col}" if status not in ("UNKNOWN", "IDLE") else "none"
        rows_html += (
            f"<div style='display:flex;align-items:center;gap:8px;padding:5px 0;"
            f"border-bottom:1px solid rgba(255,255,255,0.04);'>"
            f"<span style='width:8px;height:8px;border-radius:50%;"
            f"background:{col};box-shadow:{dot_glow};flex-shrink:0;'></span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:#aaa;min-width:140px;flex-shrink:0;'>{_gbh.escape(name)}</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:{col};font-weight:700;min-width:80px;letter-spacing:1px;'>{_gbh.escape(status)}</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:#444;min-width:48px;flex-shrink:0;'>{_gbh.escape(age_s)}</span>"
            f"<span style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:#333;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;'>"
            f"{_gbh.escape(note)}</span>"
            f"</div>"
        )

    st.markdown(
        f"<div style='font-family:Share Tech Mono,monospace;padding:0;'>{rows_html}</div>",
        unsafe_allow_html=True,
    )

    # ── Real freshness events ─────────────────────────────────────────────────
    if events:
        st.markdown(
            "<div style='margin-top:8px;padding:8px 10px;"
            "background:rgba(0,0,0,0.35);border-radius:0 0 4px 4px;"
            "border:1px solid rgba(20,241,149,0.08);max-height:180px;overflow-y:auto;'>",
            unsafe_allow_html=True,
        )
        _STAGE_COL = {
            "GUARDIAN": "#FFD700", "HEALTH": "#E879F9", "HEALER": "#E879F9",
            "ORACLE": "#14F195", "SUPERVISOR": "#8EF9FF", "FRESHNESS": "#14F195",
            "PRELAUNCH": "#8EF9FF", "SYSTEM": "#FFB347", "LATCH": "#FFD700",
            "GUARDIAN_HEAL": "#E879F9", "AUTO_HEAL": "#E879F9", "HEAL": "#E879F9",
            "EXECUTOR": "#14F195", "DEBATE": "#9945FF",
        }
        ev_html = ""
        for ev in events[:20]:
            sc = _STAGE_COL.get(ev["stage"].upper(), "#9945FF")
            ev_html += (
                f"<div style='display:flex;gap:6px;align-items:baseline;"
                f"padding:2px 0;border-bottom:1px solid rgba(255,255,255,0.03);'>"
                f"<span style='color:{sc};font-size:0.66rem;min-width:72px;"
                f"font-family:Share Tech Mono,monospace;letter-spacing:1px;'>"
                f"[{_gbh.escape(ev['stage'])}]</span>"
                f"<span style='color:rgba(255,255,255,0.6);font-size:0.66rem;"
                f"font-family:Share Tech Mono,monospace;flex:1;'>{_gbh.escape(ev['msg'])}</span>"
                f"<span style='color:#333;font-size:0.66rem;flex-shrink:0;margin-left:6px;'>"
                f"{ev['age']}s</span>"
                f"</div>"
            )
        st.markdown(ev_html, unsafe_allow_html=True)
        st.markdown("</div>", unsafe_allow_html=True)
    else:
        st.markdown(
            "<div style='padding:6px 8px;font-family:Share Tech Mono,monospace;"
            "font-size:0.66rem;color:#2a2a2a;'>no freshness events in last 10m - "
            "cognition_log silent or services not running</div>",
            unsafe_allow_html=True,
        )

    st.markdown("</div>", unsafe_allow_html=True)

# ══════════════════════════════════════════════════════════════════════════════
# PAGE LAYOUT - GLASSBOX → CORTEX   (Grand Vision Aligned)
#
# ZONE 1 GLASSBOX - what's happening with my money right now (full width, fast)
# ZONE 2 CORTEX   - how is the organism evolving (2-col, research-heavy)
# ══════════════════════════════════════════════════════════════════════════════

@fragment(run_every=67)
def _render_cortex_slow():
    render_living_cortex()

# ══════════════════════════════════════════════════════════════════════════════
# GRAND VISION NARRATIVE FLOW
# Identity/HUD → Vitals/Engine → Cortex/Mind → Ascension →
# Diagnostics/Gates → Substrate → Conviction → Pressure → Execution → Motor → Memory
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# RENDER-FLOW AUDIT RECEIPT - 2026-05-26
# ──────────────────────────────────────────────────────────────────────────────
# A full trace was performed on every render_*() invocation in this file.
# Each panel below is invoked EXACTLY ONCE per page render. The dead wrapper
# render_council_build_map() was removed (it was defined but never called and
# was the only latent duplication risk in the entire flow).
#
# If an external audit/screenshot ever claims "panels are rendering twice",
# the issue is NOT in this render flow - likely candidates:
#   • Two browser tabs open at once
#   • Stale Streamlit fragment retaining content during a rerun
#   • A child component (e.g. inside render_living_cortex) drawing its own
#     copy of something we also render at page level - check the comments
#     at lines ~6076/6078 below; those panels are explicitly NOT duplicated.
#   • Old browser cache of a prior version
#
# Do NOT replace the crown deck without re-reading services/sovereign_hub.py's
# render_crown_navigation_deck() - it carries goldenSweep / mycelialShimmer /
# glassPulse animations, per-facet active glows, legacy flag sync, first-tick
# NPC seed, and iframe slot cleanup. Replacements that strip those are
# regressions, not upgrades.
# ══════════════════════════════════════════════════════════════════════════════

# ── SOVEREIGN COMMAND BAR - SIGNOFF_COPYTRADE_PAPER_BONUS_20260613 ────────────
# One compact, always-visible strip answering "are we healthy, scanning,
# qualifying, trading, and learning?" in <5 seconds. Read-only, fail-silent,
# schema-tolerant. Holds: mode / oracle / ingest / qualifier / supervisor /
# executor / paper open / live armed / copytrade lane state / heartbeat age.
def render_sovereign_command_bar() -> None:
    import sqlite3 as _sq, time as _t, html as _h
    now = _t.time()
    hb, cfg = {}, {}
    paper_open = live_open = 0
    try:
        _db = _sq.connect(str(DB_PATH), timeout=2.0); _db.row_factory = _sq.Row
        try:
            for r in _db.execute("SELECT service_name,last_pulse,status,note FROM system_heartbeat"):
                k = str(r["service_name"] or "").lower()
                p = float(r["last_pulse"] or 0)
                if k not in hb or p > hb[k][0]:
                    hb[k] = (p, str(r["status"] or ""), str(r["note"] or ""))
        except Exception:
            pass
        try:
            for r in _db.execute(
                "SELECT key,value FROM system_config WHERE key IN "
                "('TRADING_MODE','PAPER_TRADING_ENABLED','LIVE_TRADING_ENABLED',"
                " 'LIVE_MODE_B_ENABLED','MODE_B_ENABLED','DUAL_MODE_ENABLED',"
                " 'LIVE_PAPER_SHADOW_ON_BLOCK','WS_ORACLE_STATE',"
                " 'COPYTRADE_PAPER_BONUS_ENABLED','MARKET_TIDE_STATE')"):
                cfg[str(r["key"])] = str(r["value"] or "")
        except Exception:
            pass
        try:
            paper_open = int(_db.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND "
                "(entry_price_source LIKE 'PAPER%' OR COALESCE(funding_mode,'SIM')='SIM')"
            ).fetchone()[0] or 0)
            live_open = int(_db.execute(
                "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND "
                "entry_price_source LIKE 'LIVE%'").fetchone()[0] or 0)
        except Exception:
            try:
                paper_open = int(_db.execute(
                    "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'"
                ).fetchone()[0] or 0)
            except Exception:
                pass
        _db.close()
    except Exception:
        pass

    def _age(svc):
        rec = hb.get(svc)
        return (now - rec[0]) if rec and rec[0] > 0 else None

    def _svc_chip(label, svc, stale=120.0):
        a = _age(svc)
        if a is None:
            return label, "#3a3a4a", "no hb"
        rec = hb.get(svc)
        blob = ((rec[1] or "") + " " + (rec[2] or "")).lower()
        if any(t in blob for t in ("error", "dead", "fatal", "broken", "traceback")):
            return label, "#FF073A", "ERROR"
        if a > stale:
            return label, "#FF9500", f"stale {int(a)}s"
        return label, "#14F195", f"{int(a)}s"

    mode_label, mode_col, _mode_detail = _snty_effective_mode(cfg)
    # BUGFIX_20260718: mode_raw was referenced below but never defined, so this
    # entire command bar died with a NameError that its fail-silent caller
    # swallowed — the strip simply never rendered. Derived here from config.
    mode_raw = str(cfg.get("TRADING_MODE", "paper") or "paper").strip().lower()
    if mode_label == "LIVE":
        mode_label, mode_col = "LIVE-GATED", "#FF073A"

    oracle_state = (cfg.get("WS_ORACLE_STATE") or "UNKNOWN").upper()
    o_age = _age("ws_price_oracle")
    oracle_col = ("#14F195" if oracle_state in ("HEALTHY", "OK", "ALIVE") and (o_age or 9e9) <= 120
                  else "#FF073A" if oracle_state in ("STALLED", "ERROR", "DEAD")
                  else "#FF9500")
    oracle_sub = f"{oracle_state.lower()}" + (f" {int(o_age)}s" if o_age is not None else "")

    ct_state, ct_col, ct_sub = "OFFLINE", "#3a3a4a", "module missing"
    try:
        try:
            from services.copytrade_influence import get_lane_state as _ct_lane
        except Exception:
            from copytrade_influence import get_lane_state as _ct_lane  # type: ignore
        _ls = _ct_lane()
        ct_state = str(_ls.get("state", "UNKNOWN"))
        ct_sub = str(_ls.get("detail", ""))[:40]
        ct_col = {"PAPER_BONUS_ELIGIBLE": "#FFD700", "PAPER_SHADOW_READY": "#14F195",
                  "OBSERVING": "#8EF9FF", "LIVE_OBSERVE_ONLY": "#9945FF",
                  "NO_DATA": "#FF9500", "NO_WALLETS": "#FF9500"}.get(ct_state, "#FF073A")
    except Exception:
        pass

    core_ages = [a for a in (_age(s) for s in
                 ("ws_price_oracle", "market_intelligence", "neural_supervisor",
                  "execution_engine")) if a is not None]
    hb_age = f"{int(min(core_ages))}s" if core_ages else "-"
    hb_col = "#14F195" if core_ages and min(core_ages) <= 120 else "#FF9500"

    chips = [("MODE", mode_col, mode_label),
             ("ORACLE", oracle_col, oracle_sub),
             _svc_chip("INGEST", "pump_monitor"),
             _svc_chip("QUALIFIER", "market_intelligence"),
             _svc_chip("SUPERVISOR", "neural_supervisor"),
             _svc_chip("EXECUTOR", "execution_engine"),
             ("PAPER OPEN", "#8EF9FF", str(paper_open)),
             ("LIVE", "#FFD700" if mode_raw == "live" else "#3a3a4a",
              (f"armed · {live_open} open" if mode_raw == "live" else "off")),
             ("COPYTRADE", ct_col, ct_state),
             ("HB AGE", hb_col, hb_age)]
    cells = "".join(
        f'<div style="flex:1 1 0;min-width:86px;background:linear-gradient(165deg,rgba(12,6,28,.96),rgba(5,2,16,.96));'
        f'border:1px solid {c}33;border-top:2px solid {c};border-radius:8px;padding:5px 7px;overflow:hidden;">'
        f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1.4px;color:#8a8aa6;">{_h.escape(lab)}</div>'
        f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:.6px;color:{c};font-weight:800;'
        f'white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{_h.escape(str(val))}</div></div>'
        for lab, c, val in chips)
    st.markdown(
        f'<div style="display:flex;gap:6px;flex-wrap:wrap;margin:2px 0 8px 0;">{cells}</div>',
        unsafe_allow_html=True)
# ── END SOVEREIGN COMMAND BAR ─────────────────────────────────────────────────


# ── COPYTRADE LANE CARD - SIGNOFF_COPYTRADE_PAPER_BONUS_20260613 ──────────────
# Dedicated glassbox card for the copytrade lane: explicit state vocabulary
# (NO_WALLETS / NO_DATA / OBSERVING / PAPER_SHADOW_READY / PAPER_BONUS_ELIGIBLE /
# LIVE_OBSERVE_ONLY), wallet evidence counters, bonus decision trail.
# Read-only; "Live influence: OFF" is structural (see copytrade_influence.py).
def render_copytrade_lane_card() -> None:
    import html as _h
    s = {}
    try:
        try:
            from services.copytrade_influence import summary_for_ui as _ct_sum
        except Exception:
            from copytrade_influence import summary_for_ui as _ct_sum  # type: ignore
        s = _ct_sum() or {}
    except Exception as exc:
        st.caption(f"Copytrade lane card unavailable: {type(exc).__name__}")
        return
    state = str(s.get("state", "UNKNOWN"))
    col = {"PAPER_BONUS_ELIGIBLE": "#FFD700", "PAPER_SHADOW_READY": "#14F195",
           "OBSERVING": "#8EF9FF", "LIVE_OBSERVE_ONLY": "#9945FF",
           "NO_DATA": "#FF9500", "NO_WALLETS": "#FF9500"}.get(state, "#FF073A")

    def _m(label, val):
        return (f'<div style="flex:1 1 0;min-width:104px;">'
                f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1.3px;color:#8a8aa6;">{_h.escape(label)}</div>'
                f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#cfe9ff;font-weight:700;">{_h.escape(str(val))}</div></div>')

    hb = s.get("scanner_heartbeat_age_s")
    last_dec = s.get("last_decision")
    metrics = "".join([
        _m("WALLETS TRACKED", s.get("wallets_tracked", 0)),
        _m("OBSERVED TRADES", s.get("observed_trades_total", 0)),
        _m("BUYS / 1H", s.get("recent_buys_1h", 0)),
        _m("SELLS / 1H", s.get("recent_sells_1h", 0)),
        _m("FRESH SIGNALS / 1H", s.get("fresh_signals_1h", 0)),
        _m("BONUSES / 24H", s.get("bonuses_24h", 0)),
        _m("DENIALS / 24H", s.get("denials_24h", 0)),
        _m("LAST SCAN AGE", f"{int(hb)}s" if hb is not None else "no hb"),
    ])
    overlap = " · ".join(f"{o.get('symbol','?')}×{o.get('hits',0)}"
                         for o in (s.get("top_overlap") or [])[:3]) or "-"
    last_line = (f"{last_dec}: {s.get('last_reason','')} "
                 f"({int(s.get('last_decision_age_s',0))}s ago)" if last_dec else "no decisions yet")
    st.markdown(
        f'<div style="background:linear-gradient(165deg,rgba(12,6,28,.96),rgba(5,2,16,.96));'
        f'border:1px solid {col}33;border-left:3px solid {col};border-radius:10px;padding:10px 12px;margin:6px 0;">'
        f'<div style="display:flex;align-items:center;gap:10px;margin-bottom:6px;">'
        f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:2px;color:#cfe9ff;font-weight:800;">COPYTRADE LANE</span>'
        f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;color:{col};font-weight:800;">{_h.escape(state)}</span>'
        f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#7c7c92;">{_h.escape(str(s.get("detail",""))[:80])}</span>'
        f'<span style="margin-left:auto;font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#9945FF;font-weight:800;">LIVE INFLUENCE: OFF</span>'
        f'</div>'
        f'<div style="display:flex;gap:8px;flex-wrap:wrap;margin-bottom:5px;">{metrics}</div>'
        f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#7c7c92;">'
        f'TOP OVERLAP: {_h.escape(overlap)} &nbsp;·&nbsp; LAST DECISION - {_h.escape(last_line)}</div>'
        f'</div>', unsafe_allow_html=True)
# ── END COPYTRADE LANE CARD ───────────────────────────────────────────────────


def render_golden_filtration_spine() -> None:
    """GOLDEN FILTRATION SPINE - one-glance organism pipeline health.

    DISCOVERY -> ORACLE -> INTELLIGENCE -> COPYTRADE -> SUPERVISOR -> EXECUTION -> PNL MEMORY

    Every node's state is derived from REAL system_heartbeat rows (+ real recent PnL for
    the memory node). Read-only, fail-silent, schema-tolerant. Touches no trading path.
    Honest by construction: missing heartbeat -> IDLE, error/scorer_import_failed -> BROKEN,
    'stall' in note -> STALLED, stale pulse -> IDLE(stale Ns), otherwise ALIVE with the real
    note surfaced. No fabricated 'alive'.
    """
    import sqlite3 as _sq, time as _t, html as _h
    now = _t.time()
    NODES = [
        ("DISCOVERY",    ["pump_monitor", "pump_activity_monitor"]),
        ("ORACLE",       ["ws_price_oracle"]),
        ("INTELLIGENCE", ["market_intelligence"]),
        ("COPYTRADE",    ["copytrade_shadow_scanner"]),
        ("SUPERVISOR",   ["neural_supervisor"]),
        ("EXECUTION",    ["execution_engine", "trade_executor"]),
        ("PNL MEMORY",   ["__pnl__"]),
    ]
    hb = {}
    pnl_recent = None
    try:
        _db = _sq.connect(str(DB_PATH), timeout=2); _db.row_factory = _sq.Row
        try:
            for r in _db.execute("SELECT service_name, last_pulse, status, note FROM system_heartbeat"):
                k = str(r["service_name"] or "").lower()
                p = float(r["last_pulse"] or 0)
                if k not in hb or p > hb[k][0]:
                    hb[k] = (p, str(r["status"] or ""), str(r["note"] or ""))
        except Exception:
            pass
        try:
            _row = _db.execute(
                "SELECT COALESCE(SUM(realized_pnl_usd),0) FROM paper_positions "
                "WHERE status='CLOSED' AND COALESCE(closed_at,0) > ?", (now - 6 * 3600,)
            ).fetchone()
            pnl_recent = float(_row[0]) if _row else 0.0
        except Exception:
            pnl_recent = None
        _db.close()
    except Exception:
        pass

    STALE = 120.0

    def state_of(aliases):
        if aliases == ["__pnl__"]:
            if pnl_recent is None:
                return ("IDLE", "#3a3a4a", "no data")
            if pnl_recent > 0:
                return ("PROFIT", "#FFD700", f"+${pnl_recent:.2f} / 6h")
            if pnl_recent < 0:
                return ("LOSS", "#FF073A", f"-${abs(pnl_recent):.2f} / 6h")
            return ("FLAT", "#8EF9FF", "$0.00 / 6h")
        rec = next((hb[a.lower()] for a in aliases if a.lower() in hb), None)
        if not rec:
            return ("IDLE", "#3a3a4a", "no heartbeat")
        pulse, status, note = rec
        age = now - pulse if pulse > 0 else 9e9
        blob = (status + " " + note).lower()
        if any(t in blob for t in ("scorer_import_failed", "error", "traceback", "dead", "fatal", "broken")):
            return ("BROKEN", "#FF073A", (note or status)[:48])
        if "stall" in blob:
            return ("STALLED", "#FF9500", (note or "stalled")[:48])
        if age > STALE:
            return ("IDLE", "#6a6a4a", f"stale {int(age)}s")
        return ("ALIVE", "#14F195", (note or status or "alive")[:48])

    chips = []
    for i, (label, aliases) in enumerate(NODES):
        st_name, col, sub = state_of(aliases)
        live = st_name in ("ALIVE", "PROFIT")
        dot_anim = "animation:spinePulse 1.6s ease-in-out infinite;" if live else ""
        dot_glow = f"box-shadow:0 0 8px {col},0 0 16px {col}66;" if st_name in ("ALIVE", "PROFIT", "BROKEN") else "opacity:.6;"
        arrow = "" if i == 0 else (
            '<div style="flex:0 0 18px;align-self:center;color:#3a3450;'
            'font-family:Share Tech Mono,monospace;font-size:.7rem;margin-top:-14px;">&#8594;</div>'
        )
        chips.append(arrow + (
            f'<div style="flex:1 1 0;min-width:96px;background:linear-gradient(165deg,rgba(12,6,28,.94),rgba(5,2,16,.94));'
            f'border:1px solid {col}33;border-left:3px solid {col};border-radius:9px;padding:7px 8px;overflow:hidden;">'
            f'<div style="display:flex;align-items:center;gap:6px;margin-bottom:3px;">'
            f'<span style="width:8px;height:8px;border-radius:50%;background:{col};{dot_glow}{dot_anim}display:inline-block;"></span>'
            f'<span style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1.5px;color:#cfe9ff;font-weight:800;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{label}</span>'
            f'</div>'
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:1px;color:{col};margin-bottom:2px;">{st_name}</div>'
            f'<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#7c7c92;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">{_h.escape(sub)}</div>'
            f'</div>'
        ))

    html_out = (
        "<style>@keyframes spinePulse{0%,100%{transform:scale(1);opacity:1}50%{transform:scale(1.5);opacity:.6}}</style>"
        '<div style="background:rgba(5,2,16,.55);border:1px solid rgba(255,215,0,.16);border-radius:12px;'
        'padding:9px 12px 11px;margin:6px 0 12px;">'
        '<div style="font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;color:#FFD700AA;'
        'margin-bottom:7px;display:flex;align-items:center;gap:8px;">'
        '<span>&#9670; GOLDEN FILTRATION SPINE</span>'
        '<span style="color:#8EF9FF66;font-size:0.66rem;letter-spacing:2px;">REAL HEARTBEAT TRUTH &middot; READ-ONLY</span>'
        '</div>'
        '<div style="display:flex;align-items:stretch;gap:2px;flex-wrap:nowrap;overflow-x:auto;">'
        + "".join(chips) +
        '</div></div>'
    )
    try:
        st.markdown(html_out, unsafe_allow_html=True)
    except Exception:
        pass


# 1. IDENTITY CROWN + 2. HUD / API STATUS BAR (shared health truth)
# ── POLARIS FORWARD PATH + HOLOGRAPHIC HUD ────────────────────────────────────
@st.fragment(run_every=23)
def render_polaris_hud() -> None:
    """
    Holographic HUD - readable on mobile, debate-chamber styled.
    Shows: Polaris Forward Path, agent tasks, system gates.
    Read-only. Fail-silent.
    """
    try:
        import sqlite3 as _hdb, time as _ht
        _now = _ht.time()
        _hc  = _hdb.connect(str(DB_PATH), timeout=2)
        _hc.execute("PRAGMA busy_timeout=1000")
        _hc.row_factory = _hdb.Row

        # Read key state
        def _cfg(key, default="?"):
            r = _hc.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
            return r[0] if r else default

        _mode     = _cfg("TRADING_MODE", "paper")
        _mode_cfg = {
            "TRADING_MODE": _mode,
            "PAPER_TRADING_ENABLED": _cfg("PAPER_TRADING_ENABLED", "1"),
            "LIVE_TRADING_ENABLED": _cfg("LIVE_TRADING_ENABLED", "0"),
            "LIVE_MODE_B_ENABLED": _cfg("LIVE_MODE_B_ENABLED", "0"),
            "MODE_B_ENABLED": _cfg("MODE_B_ENABLED", "0"),
            "DUAL_MODE_ENABLED": _cfg("DUAL_MODE_ENABLED", "0"),
            "LIVE_PAPER_SHADOW_ON_BLOCK": _cfg("LIVE_PAPER_SHADOW_ON_BLOCK", "1"),
        }
        _tide     = _cfg("MARKET_TIDE_STATE", "NORMAL")
        _halt     = _cfg("DRAWDOWN_HALT_ACTIVE", "0")
        _hour_en  = _cfg("HOUR_GATE_ENABLED", "0")
        _conf     = _cfg("SUPERVISOR_MIN_MINT_CONFIDENCE", "0.75")
        _tax      = float(_cfg("TAX_RESERVE_USD", "0") or 0)
        _open_pos = _hc.execute("SELECT COUNT(*) FROM paper_positions WHERE status='OPEN'").fetchone()[0]
        _fresh_px = _hc.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE price_status='priced' "
            "AND COALESCE(price_updated_at,0)>?", (_now-120,)
        ).fetchone()[0]
        _qual     = _hc.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE quality_status='qualified' "
            "AND candidate_state NOT IN ('vetoed','dead','executed','expired_stale')"
        ).fetchone()[0]
        _oracle_r = _hc.execute(
            "SELECT status FROM system_heartbeat WHERE service_name='ws_price_oracle'"
        ).fetchone()
        _oracle_s = _oracle_r[0] if _oracle_r else "UNKNOWN"

        _hc.close()

        # ── Forward path checks ────────────────────────────────────────────
        def _gate(label, ok, detail=""):
            col  = "#14F195" if ok else "#FF073A"
            icon = "✓" if ok else "✗"
            sub  = f"<span style='color:#555;font-size:0.66rem'>{detail}</span>" if detail else ""
            return (
                f"<div style='display:flex;align-items:center;gap:6px;"
                f"padding:3px 0;border-bottom:0.5px solid #1a1a1a'>"
                f"<span style='color:{col};font-size:0.66rem;min-width:14px'>{icon}</span>"
                f"<span style='color:{col};font-size:0.66rem;font-family:Share Tech Mono,monospace;"
                f"letter-spacing:1px;flex:1'>{label}</span>{sub}</div>"
            )

        import datetime as _dt
        _utc_hour = _dt.datetime.utcnow().hour

        _gates = [
            _gate("ORACLE",      _oracle_s not in ("STALLED","ERROR","DEAD"),  _oracle_s),
            _gate("FRESH PRICES",_fresh_px > 0,  f"{_fresh_px} <120s"),
            _gate("QUALIFIED",   _qual > 0,       f"{_qual} ready"),
            _gate("DRAWDOWN",    _halt != "1",    "halted" if _halt=="1" else "clear"),
            _gate("HOUR GATE",   not (_hour_en=="1"), f"UTC {_utc_hour}"),
            _gate("TIDE",        _tide != "EXTREME", _tide),
            _gate("PAPER SLOTS", _open_pos < 3,  f"{_open_pos}/3 open"),
            _gate("CONF FLOOR",  True,            f"{_conf}"),
        ]

        # Overall path status
        _blockers = []
        if _oracle_s in ("STALLED","ERROR","DEAD"): _blockers.append("ORACLE")
        if _fresh_px == 0: _blockers.append("NO PRICES")
        if _qual == 0:     _blockers.append("NO QUALIFIED")
        if _halt == "1":   _blockers.append("DRAWDOWN HALT")
        if _hour_en == "1": _blockers.append("HOUR GATE")
        if _open_pos >= 3: _blockers.append("POS FULL")

        _path_ok    = len(_blockers) == 0
        _path_col   = "#14F195" if _path_ok else "#FF073A"
        _path_label = "CLEAR PATH VERIFIED" if _path_ok else f"BLOCKER: {' · '.join(_blockers)}"

        # Mode label - use effective mode, because dual may be represented by
        # TRADING_MODE=paper + LIVE_MODE_B_ENABLED=1 for runtime safety.
        _mode_eff, _mode_eff_col, _mode_eff_detail = _snty_effective_mode(_mode_cfg)
        _mode_col = _mode_eff_col
        _mode_label = {"DUAL": "🟡 DUAL · PAPER + MODE-B", "LIVE": "🔴 LIVE ARMED"}.get(_mode_eff, "📄 PAPER LEARNING")

        # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.

        # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.

        try:

            _runner_gold_pct = 75.0

            try:

                if isinstance(locals().get("row"), dict):

                    _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)

            except Exception:

                _runner_gold_pct = 75.0

            if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):

                _state = "RUNNER"

                _state_col = "#FFD700"

        except Exception:

            pass


        st.markdown(
            f"""
            <div style="
                background:rgba(5,2,16,0.88);
                border:1px solid {_path_col}44;
                border-left:3px solid {_path_col};
                border-radius:8px;
                padding:10px 14px;
                margin:4px 0 8px;
                font-family:'Share Tech Mono',monospace;
            ">
              <div style="display:flex;align-items:center;justify-content:space-between;
                          margin-bottom:8px;flex-wrap:wrap;gap:6px">
                <span style="font-size:0.66rem;color:{_path_col};font-weight:600;
                             letter-spacing:2px;text-shadow:0 0 8px {_path_col}66">
                  ◈ POLARIS FORWARD PATH - {_path_label}
                </span>
                <span style="font-size:0.66rem;color:{_mode_col};letter-spacing:1px">
                  {_mode_label}
                </span>
              </div>
              <div style="display:grid;grid-template-columns:1fr 1fr;gap:0 16px">
                {"".join(_gates)}
              </div>
              <div style="margin-top:6px;display:flex;gap:12px;flex-wrap:wrap">
                <span style="font-size:0.66rem;color:#555">
                  TIDE: <span style="color:{'#FF9900' if _tide=='FLOOD' else '#14F195'}">{_tide}</span>
                </span>
                <span style="font-size:0.66rem;color:#555">
                  CONF: <span style="color:#9945FF">{_conf}</span>
                </span>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )
    except Exception:
        pass

# ── SIGNOFF_CANONICAL_HIERARCHY_20260715: relocatable truth/diagnostic mounts ─
# These wrap previously-inline page blocks into functions so the canonical
# hierarchy can place them correctly. Renderers, imports and data sources are
# unchanged - only the mount position moved.

def _render_final_gate_glassbox() -> None:
    """Final Gate · Sovereign Pulse · Twin paper/live rails · Price Truth ·
    Council · Copytrade Outpost. All panels read real DB/log telemetry through
    ui/data_sources.py (read-only, short TTL caches); every tile prints its
    backend source; missing sources render "not wired" - nothing is faked."""
    # DEDUP_20260718: render_glassbox() paints its own full "🜂 SOVEREIGN
    # GLASSBOX" hero header, so the duplicate title strip here is removed —
    # one truth, one heading. This divider now carries id="glassbox-anchor",
    # which the crystalline command rail's GLASSBOX pill links to but which
    # previously existed nowhere on the page (dead jump).
    st.markdown(
        "<div id='glassbox-anchor' style='margin:20px 0 10px;"
        "border-top:1px solid rgba(20,241,149,0.25);'></div>",
        unsafe_allow_html=True
    )
    try:
        from ui.sovereign_glassbox import render_glassbox as _render_glassbox
    except Exception as _gbx_imp_err:
        st.markdown(f"""
<div style='text-align:center;padding:24px;font-family:Share Tech Mono,monospace;
    font-size:0.66rem;color:#555;letter-spacing:2px;'>
    // SOVEREIGN GLASSBOX MODULE NOT FOUND //<br>
    Copy ui/sovereign_glassbox.py + ui/data_sources.py + ui/theme.py - {html.escape(str(_gbx_imp_err))}
</div>""", unsafe_allow_html=True)
        return
    try:
        _render_glassbox()
    except Exception as _gbx_err:
        st.warning(f"Glassbox render error (backend unaffected): {_gbx_err}")


def _render_runtime_fingerprint() -> None:
    # SIGNOFF_CANONICAL_FINGERPRINT_20260714: prove which hub build is live.
    # Application source, build id, source mtime, process start, active database.
    try:
        import hashlib as _fp_hl
        _fp_src = Path(__file__).resolve()
        _fp_bid = _fp_hl.sha256(_fp_src.read_bytes()).hexdigest()[:12]
        _fp_mt = time.strftime("%Y-%m-%d %H:%M:%S",
                               time.localtime(_fp_src.stat().st_mtime))
        _fp_ps_lbl = "SESSION START"
        try:
            import psutil as _fp_ps
            _fp_start = _fp_ps.Process(os.getpid()).create_time()
            _fp_ps_lbl = "PROC START"
        except Exception:
            _fp_start = st.session_state.setdefault("_snty_session_start", time.time())
        _fp_start_s = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(_fp_start))
        try:
            _fp_db = str(DB_PATH)
        except Exception:
            _fp_db = "unresolved"
        st.markdown(
            "<div style='margin:2px 0 10px;padding:5px 10px;"
            "border:1px solid rgba(142,249,255,.10);border-radius:5px;"
            "background:rgba(5,2,16,.35);font-family:Share Tech Mono,monospace;"
            "font-size:0.66rem;letter-spacing:1px;color:#6f6a84;line-height:1.7;"
            "word-break:break-all;'>"
            f"SOURCE&nbsp;services/{_fp_src.name}&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"BUILD&nbsp;<span style='color:#8EF9FF'>{_fp_bid}</span>&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"MODIFIED&nbsp;{_fp_mt}&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"{_fp_ps_lbl}&nbsp;{_fp_start_s}&nbsp;&nbsp;·&nbsp;&nbsp;"
            f"DB&nbsp;{html.escape(_fp_db)}</div>",
            unsafe_allow_html=True,
        )
    except Exception as _fp_exc:
        st.markdown(
            f"<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
            f"color:#FFB347;'>RUNTIME FINGERPRINT UNAVAILABLE · "
            f"{html.escape(_sanitize_exception_text(str(_fp_exc)))}</div>",
            unsafe_allow_html=True,
        )


def _render_diagnostics_bay() -> None:
    """SIGNOFF_DIAGNOSTICS_BAY_20260715: expandable runtime-authority diagnostics.
    Real values only - every field is queried live from DB/heartbeats/source; any
    unavailable field prints its concrete failure reason instead of a guess."""
    with st.expander("⟐ DIAGNOSTICS — RUNTIME AUTHORITY · HEARTBEATS · AI HANDOFF EXPORT", expanded=False):
        _render_runtime_fingerprint()
        _diag = {}
        try:
            _diag["canonical_source"] = str(Path(__file__).resolve())
        except Exception as _e:
            _diag["canonical_source"] = f"unresolved ({type(_e).__name__})"
        try:
            _diag["active_db"] = str(DB_PATH)
        except Exception:
            _diag["active_db"] = "unresolved"
        _hb_lines = []
        try:
            _dc = sqlite3.connect(str(DB_PATH), timeout=2.0)
            _dc.execute("PRAGMA busy_timeout=1000")
            _now_d = time.time()
            for _sn, _stt, _lp in _dc.execute(
                    "SELECT service_name, COALESCE(status,''), COALESCE(last_pulse,0) "
                    "FROM system_heartbeat ORDER BY service_name ASC").fetchall():
                try:
                    _agev = int(_now_d - float(_lp)) if float(_lp) > 0 else None
                except Exception:
                    _agev = None
                _hb_lines.append({"service": str(_sn), "status": str(_stt),
                                  "age_s": _agev if _agev is not None else "no pulse"})
            # live-gate blocker counts (last hour, real cognition_log rows)
            try:
                _blk = _dc.execute(
                    "SELECT COUNT(*) FROM cognition_log WHERE stage IN "
                    "('LIVE_GATE','LIVE_BLOCK','GATE','QUALIFIER') AND "
                    "LOWER(COALESCE(note,'')) LIKE '%block%' AND timestamp > ?",
                    (_now_d - 3600,)).fetchone()
                _diag["live_gate_blocks_1h"] = int(_blk[0]) if _blk else 0
            except Exception as _e:
                _diag["live_gate_blocks_1h"] = f"unavailable ({type(_e).__name__})"
            # recent schema/query exceptions surfaced in cognition_log
            try:
                _exc_rows = _dc.execute(
                    "SELECT timestamp, stage, note FROM cognition_log WHERE "
                    "(LOWER(COALESCE(note,'')) LIKE '%no such table%' OR "
                    " LOWER(COALESCE(note,'')) LIKE '%no such column%' OR "
                    " LOWER(COALESCE(note,'')) LIKE '%operationalerror%') "
                    "ORDER BY timestamp DESC LIMIT 5").fetchall()
                _diag["recent_schema_query_exceptions"] = [
                    {"age_s": int(_now_d - float(r[0] or 0)), "stage": str(r[1]),
                     "note": str(r[2] or "")[:160]} for r in _exc_rows]
            except Exception as _e:
                _diag["recent_schema_query_exceptions"] = f"unavailable ({type(_e).__name__})"
            # oracle freshness
            try:
                _orc = _dc.execute(
                    "SELECT COALESCE(last_pulse,0) FROM system_heartbeat WHERE "
                    "service_name IN ('ws_price_oracle','price_oracle','oracle') "
                    "ORDER BY last_pulse DESC LIMIT 1").fetchone()
                if _orc and float(_orc[0] or 0) > 0:
                    _diag["oracle_freshness_s"] = int(_now_d - float(_orc[0]))
                else:
                    _diag["oracle_freshness_s"] = "no oracle heartbeat row"
            except Exception as _e:
                _diag["oracle_freshness_s"] = f"unavailable ({type(_e).__name__})"
            # council producer state
            try:
                _cp = _dc.execute(
                    "SELECT service_name, COALESCE(status,''), COALESCE(last_pulse,0) "
                    "FROM system_heartbeat WHERE service_name IN "
                    "('council_chamber_bridge','debate_engine','council_execution_spine',"
                    "'council_build_orchestrator') ORDER BY last_pulse DESC").fetchall()
                _diag["council_producers"] = [
                    {"service": str(r[0]), "status": str(r[1]),
                     "age_s": (int(_now_d - float(r[2])) if float(r[2] or 0) > 0 else "no pulse")}
                    for r in _cp] or "no council producer heartbeat rows"
            except Exception as _e:
                _diag["council_producers"] = f"unavailable ({type(_e).__name__})"
            # copytrade ingestion state - absence stated as absence, never "noise"
            try:
                _ct_hb = _dc.execute(
                    "SELECT COALESCE(last_pulse,0) FROM system_heartbeat WHERE "
                    "service_name LIKE '%copytrade%' OR service_name LIKE '%smart_wallet%' "
                    "ORDER BY last_pulse DESC LIMIT 1").fetchone()
                if _ct_hb and float(_ct_hb[0] or 0) > 0:
                    _ct_age = int(_now_d - float(_ct_hb[0]))
                    _diag["copytrade_ingestion"] = (
                        f"heartbeat {_ct_age}s ago" if _ct_age <= 600
                        else f"STALE - last heartbeat {_ct_age}s ago")
                else:
                    _diag["copytrade_ingestion"] = "INGESTER OFFLINE - no heartbeat row"
            except Exception as _e:
                _diag["copytrade_ingestion"] = f"unavailable ({type(_e).__name__})"
            _dc.close()
        except Exception as _e:
            _diag["heartbeat_read_error"] = f"{type(_e).__name__}: {_sanitize_exception_text(str(_e))[:160]}"
        _diag["service_heartbeats"] = _hb_lines or "none readable"
        try:
            import psutil as _dg_ps
            _diag["process_start"] = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(_dg_ps.Process(os.getpid()).create_time()))
        except Exception:
            _diag["process_start"] = time.strftime(
                "%Y-%m-%d %H:%M:%S",
                time.localtime(st.session_state.get("_snty_session_start", time.time())))
        # Compact operator summary first. Full machine JSON remains available
        # only inside a nested expander so mobile is never dominated by a
        # multi-screen raw dump.
        try:
            _svc_count = len(_hb_lines)
            _fresh_count = sum(
                1 for _h in _hb_lines
                if isinstance(_h.get("age_s"), int) and _h["age_s"] <= 180
            )
            _schema_faults = _diag.get("recent_schema_query_exceptions", [])
            _schema_fault_count = len(_schema_faults) if isinstance(_schema_faults, list) else 0
            st.markdown(
                f"<div class='snty-diag-summary'>"
                f"<span><b>{_fresh_count}/{_svc_count}</b> fresh services</span>"
                f"<span><b>{html.escape(str(_diag.get('oracle_freshness_s','—')))}</b> oracle age</span>"
                f"<span><b>{_schema_fault_count}</b> recent schema faults</span>"
                f"<span><b>{html.escape(str(_diag.get('copytrade_ingestion','—')))}</b></span>"
                f"</div>",
                unsafe_allow_html=True,
            )
            with st.expander("AI HANDOFF JSON · COPY ONLY WHEN NEEDED", expanded=False):
                st.code(json.dumps(_diag, indent=2, default=str), language="json")
        except Exception as _e:
            st.caption(f"Handoff export unavailable: {type(_e).__name__}")
        # PIPELINE TRUTH - operator price-handoff blocker surface (moved here
        # from the page header region; same renderer, diagnostics placement).
        try:
            st.markdown(
                "<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;"
                "letter-spacing:2px;color:#9945FF;margin:10px 0 4px;'>§¬ PIPELINE TRUTH / "
                "PRICE HANDOFF</div>", unsafe_allow_html=True)
            render_pipeline_truth_panel()
        except Exception as _pt_panel_err:
            st.caption(f"Pipeline Truth unavailable: {_pt_panel_err}")
# ── /SIGNOFF_CANONICAL_HIERARCHY_20260715 mounts ─────────────────────────────

# GRAND_VISION_UI_INLINE_20260715
# Integrated into the canonical composition root to avoid an additional runtime
# module. Presentation-only; all reads are fail-silent and no backend state is written.
_GV_DOCTRINE_CSS = r"""
<style id="sentinuity-grand-vision-v1">
:root {
  --snty-void:#050210;
  --snty-surface:rgba(10,13,20,.88);
  --snty-surface-2:rgba(28,20,50,.34);
  --snty-cyan:#8EF9FF; /* SIGNOFF_DOCTRINE_CYAN_UNIFY_20260718: duplicate :root previously re-declared the deprecated cyan and, loading later, won the cascade */
  --snty-green:#14F195;
  --snty-purple:#9945FF;
  --snty-red:#FF073A;
  --snty-gold:#FFD700;
  --snty-amber:#FFB020;
  --snty-muted:#8190A8;
  --snty-line:rgba(255,255,255,.075);
}
html,body,[data-testid="stAppViewContainer"],.stApp {
  background:
    radial-gradient(circle at 15% -8%,rgba(56,225,255,.09),transparent 34rem),
    radial-gradient(circle at 86% 0%,rgba(153,69,255,.10),transparent 38rem),
    linear-gradient(180deg,#050210 0%,#070512 54%,#04020b 100%) !important;
}
[data-testid="stHeader"] {background:rgba(5,2,16,.72)!important;backdrop-filter:blur(16px)}
[data-testid="stMainBlockContainer"] {max-width:1500px;padding-top:.65rem;padding-bottom:4rem}

/* One coherent surface language instead of equal-weight telemetry boxes. */
[data-testid="stExpander"] {
  border:1px solid var(--snty-line)!important;border-radius:12px!important;
  background:linear-gradient(145deg,rgba(12,9,25,.74),rgba(5,2,16,.76))!important;
  box-shadow:0 12px 32px rgba(0,0,0,.16)!important;overflow:hidden
}
[data-testid="stExpander"] summary {min-height:44px!important}
[data-testid="stMetric"] {
  background:linear-gradient(145deg,rgba(14,11,29,.82),rgba(5,2,16,.86));
  border:1px solid rgba(56,225,255,.12);border-radius:10px;padding:.55rem .7rem;
}
[data-testid="stMetricLabel"] {font-size:.72rem!important;letter-spacing:.09em!important;text-transform:uppercase}
[data-testid="stMetricValue"] {font-family:'Share Tech Mono',monospace!important}
[data-testid="stDataFrame"], [data-testid="stTable"] {
  border:1px solid var(--snty-line);border-radius:10px;overflow:auto;
  background:rgba(5,2,16,.58)
}
button,[role="button"],a {transition:transform .16s ease,border-color .16s ease,background .16s ease}
button:hover,[role="button"]:hover {transform:translateY(-1px)}

.snty-gv-shell {position:relative;margin:.15rem 0 .85rem;border:1px solid rgba(56,225,255,.18);
 border-radius:16px;background:linear-gradient(135deg,rgba(8,8,20,.97),rgba(20,10,38,.91));
 box-shadow:0 20px 70px rgba(0,0,0,.34),inset 0 1px 0 rgba(255,255,255,.035);overflow:hidden}
.snty-gv-shell:before {content:"";position:absolute;inset:0;background:
 linear-gradient(90deg,transparent 0 49.8%,rgba(56,225,255,.025) 50%,transparent 50.2%),
 linear-gradient(0deg,transparent 0 49.8%,rgba(153,69,255,.025) 50%,transparent 50.2%);
 background-size:48px 48px;pointer-events:none}
.snty-gv-top {position:relative;display:flex;align-items:center;justify-content:space-between;gap:16px;padding:15px 18px 11px}
.snty-gv-brand {display:flex;align-items:center;gap:12px;min-width:0}
.snty-gv-sigil {width:38px;height:38px;border-radius:11px;display:grid;place-items:center;color:var(--snty-cyan);
 border:1px solid rgba(56,225,255,.35);background:radial-gradient(circle,rgba(56,225,255,.16),rgba(153,69,255,.06));
 box-shadow:0 0 24px rgba(56,225,255,.12);font-size:20px}
.snty-gv-title {font-family:Orbitron,sans-serif;font-size:clamp(.86rem,2vw,1.16rem);font-weight:800;letter-spacing:.19em;color:#eefaff;white-space:nowrap}
.snty-gv-sub {font:600 .68rem Rajdhani,sans-serif;letter-spacing:.13em;color:#7f94ab;text-transform:uppercase;margin-top:2px}
.snty-gv-mode {font:800 .66rem 'Share Tech Mono',monospace;letter-spacing:.12em;border-radius:999px;padding:7px 11px;border:1px solid currentColor;white-space:nowrap}
.snty-gv-metrics {position:relative;display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:7px;padding:0 12px 12px}
.snty-gv-metric {min-height:64px;padding:9px 10px;border-radius:10px;border:1px solid var(--snty-line);
 background:linear-gradient(155deg,rgba(255,255,255,.028),rgba(255,255,255,.009));min-width:0}
.snty-gv-label {font:700 0.66rem 'Share Tech Mono',monospace;letter-spacing:.13em;color:#718198;text-transform:uppercase;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-gv-value {font:800 clamp(.78rem,1.5vw,1.02rem) 'Share Tech Mono',monospace;color:#eaf8ff;margin-top:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-gv-detail {font:600 0.66rem Rajdhani,sans-serif;color:#73839a;margin-top:2px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.snty-gv-rail {display:flex;align-items:center;gap:5px;overflow-x:auto;padding:7px 10px;margin:0 0 9px;
 border:1px solid rgba(153,69,255,.14);border-radius:12px;background:rgba(5,2,16,.66);scrollbar-width:none}
.snty-gv-rail::-webkit-scrollbar{display:none}
.snty-gv-rail a {flex:0 0 auto;text-decoration:none!important;color:#9aa7b9!important;font:700 0.66rem 'Share Tech Mono',monospace;
 letter-spacing:.09em;text-transform:uppercase;padding:7px 10px;border-radius:8px;border:1px solid transparent}
.snty-gv-rail a:hover {color:#eefaff!important;border-color:rgba(56,225,255,.22);background:rgba(56,225,255,.055)}
.snty-section-head {display:flex;align-items:center;gap:11px;margin:19px 0 7px;padding:0 2px}
.snty-section-index {font:800 0.66rem 'Share Tech Mono',monospace;color:var(--snty-cyan);letter-spacing:.12em;border:1px solid rgba(56,225,255,.27);border-radius:6px;padding:4px 6px}
.snty-section-copy {min-width:0}
.snty-section-title {font:800 .75rem Orbitron,sans-serif;letter-spacing:.15em;color:#e6f8ff;text-transform:uppercase}
.snty-section-sub {font:600 .68rem Rajdhani,sans-serif;color:#77869d;letter-spacing:.04em;margin-top:1px}
.snty-section-line {height:1px;flex:1;background:linear-gradient(90deg,rgba(56,225,255,.28),rgba(153,69,255,.12),transparent)}
.snty-runtime-stamp {font:600 0.66rem 'Share Tech Mono',monospace;color:#68768b;letter-spacing:.06em;padding:0 14px 10px;position:relative}

@media (max-width:900px){
 [data-testid="stMainBlockContainer"]{padding-left:.72rem!important;padding-right:.72rem!important}
 .snty-gv-metrics{grid-template-columns:repeat(3,minmax(0,1fr))}
}
@media (max-width:600px){
 html,body,[data-testid="stAppViewContainer"]{overflow-x:hidden!important}
 [data-testid="stMainBlockContainer"]{padding:.45rem .48rem 3rem!important;max-width:100vw!important}
 .snty-gv-shell{border-radius:13px;margin-top:0}
 .snty-gv-top{padding:12px 11px 9px;align-items:flex-start}
 .snty-gv-title{font-size:.82rem;letter-spacing:.13em}
 .snty-gv-sub{font-size:0.66rem;letter-spacing:.09em}
 .snty-gv-sigil{width:34px;height:34px;flex:0 0 34px}
 .snty-gv-mode{font-size:0.66rem;padding:6px 8px}
 .snty-gv-metrics{grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;padding:0 8px 9px}
 .snty-gv-metric{min-height:60px;padding:8px}
 .snty-gv-label{font-size:0.66rem}.snty-gv-value{font-size:.81rem}.snty-gv-detail{font-size:0.66rem}
 .snty-gv-rail{position:sticky;top:2.9rem;z-index:90;border-radius:10px;backdrop-filter:blur(14px);padding:6px}
 .snty-gv-rail a{min-height:40px;display:flex;align-items:center;font-size:0.66rem;padding:5px 9px}
 .snty-section-head{margin-top:15px}.snty-section-title{font-size:.67rem}.snty-section-sub{font-size:0.66rem}
 [data-testid="stHorizontalBlock"]{flex-wrap:wrap!important;gap:.5rem!important}
 [data-testid="column"]{min-width:100%!important;width:100%!important;flex:1 1 100%!important}
 [data-testid="stDataFrame"],[data-testid="stTable"]{max-width:calc(100vw - 1rem)!important;overflow-x:auto!important}
 [data-testid="stMarkdownContainer"] p,[data-testid="stMarkdownContainer"] li{font-size:.88rem!important;line-height:1.48!important}
 [data-testid="stCaptionContainer"],.stCaption{font-size:.7rem!important}
 button,[role="button"],summary{min-height:44px!important}
}
</style>
"""


def _gv_inject_css() -> None:
    st.markdown(_GV_DOCTRINE_CSS, unsafe_allow_html=True)


def _gv_scalar(conn: sqlite3.Connection, sql: str, args: tuple[object, ...] = (), default: object = None) -> object:
    try:
        row = conn.execute(sql, args).fetchone()
        return row[0] if row else default
    except Exception:
        return default


def _gv_money(value: object) -> str:
    try:
        return f"${float(value):,.2f}"
    except Exception:
        return "—"


def _gv_age(value: object, now: float) -> str:
    try:
        sec = max(0, int(now - float(value)))
        return f"{sec}s" if sec < 120 else f"{sec//60}m"
    except Exception:
        return "no pulse"


def _gv_safe(s: object) -> str:
    return html.escape(str(s if s is not None else "—"))


def _gv_header(db_path: str | Path, canonical_file: str | Path) -> None:
    """Render read-only command truth. Never raises into the canonical hub."""
    now = time.time()
    mode = "PAPER"
    mode_color = "#14F195"
    paper_equity = paper_cash = realized = None
    live_wallet = None
    paper_open = live_open = 0
    oracle_pulse = executor_pulse = council_pulse = None
    oracle_status = executor_status = council_status = "UNKNOWN"
    live_keys: dict[str, str] = {}
    try:
        conn = sqlite3.connect(str(db_path), timeout=1.5)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT key,value FROM system_config WHERE key IN ("
                "'TRADING_MODE','PAPER_TRADING_ENABLED','DUAL_MODE_ENABLED','DUAL_MODE_ARMED',"
                "'LIVE_TRADING_ENABLED','LIVE_MODE_B_ENABLED','LIVE_ARMED')").fetchall()
            cfg = {str(r[0]): str(r[1] or '') for r in rows}
            live_keys = cfg
            armed = all(cfg.get(k, "0").strip().lower() in ("1","true","yes","on") for k in
                        ("DUAL_MODE_ENABLED","DUAL_MODE_ARMED","LIVE_TRADING_ENABLED","LIVE_MODE_B_ENABLED","LIVE_ARMED"))
            dual_requested = cfg.get("DUAL_MODE_ENABLED", "0") in ("1","true","TRUE") or cfg.get("LIVE_MODE_B_ENABLED", "0") in ("1","true","TRUE")
            raw = cfg.get("TRADING_MODE", "paper").upper()
            if armed:
                mode, mode_color = "DUAL · ARMED", "#9945FF"
            elif dual_requested:
                mode, mode_color = "DUAL · UNARMED", "#FFB020"
            elif raw == "LIVE":
                mode, mode_color = "LIVE · GATED", "#FF073A"
        except Exception:
            pass
        try:
            r = conn.execute("SELECT cash_balance,equity,realized_pnl FROM paper_wallet ORDER BY updated_at DESC LIMIT 1").fetchone()
            if r:
                paper_cash, paper_equity, realized = r[0], r[1], r[2]
        except Exception:
            pass
        for sql in (
            "SELECT balance_usd FROM live_wallet_state ORDER BY updated_at DESC LIMIT 1",
            "SELECT wallet_balance FROM live_wallet_state ORDER BY updated_at DESC LIMIT 1",
            "SELECT balance FROM live_wallet ORDER BY updated_at DESC LIMIT 1",
        ):
            live_wallet = _gv_scalar(conn, sql, default=None)
            if live_wallet is not None:
                break
        paper_open = int(_gv_scalar(conn, "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND COALESCE(funding_mode,'SIM')!='REAL'", default=0) or 0)
        live_open = int(_gv_scalar(conn, "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND (COALESCE(funding_mode,'SIM')='REAL' OR COALESCE(entry_price_source,'') LIKE 'LIVE%')", default=0) or 0)
        try:
            for svc, pulse, status in conn.execute("SELECT service_name,last_pulse,status FROM system_heartbeat"):
                key = str(svc or '').lower()
                if key in ("ws_price_oracle","price_oracle","oracle") and (oracle_pulse is None or float(pulse or 0)>oracle_pulse):
                    oracle_pulse, oracle_status = float(pulse or 0), str(status or 'UNKNOWN')
                if key in ("execution_engine","trade_executor") and (executor_pulse is None or float(pulse or 0)>executor_pulse):
                    executor_pulse, executor_status = float(pulse or 0), str(status or 'UNKNOWN')
                if key in ("debate_engine","council_chamber_bridge","council_execution_spine") and (council_pulse is None or float(pulse or 0)>council_pulse):
                    council_pulse, council_status = float(pulse or 0), str(status or 'UNKNOWN')
        except Exception:
            pass
        conn.close()
    except Exception:
        pass

    def status_detail(status: str, pulse: object) -> tuple[str, str]:
        age = (now - pulse) if pulse else 1e9
        blob = status.upper()
        if any(x in blob for x in ("ERROR","DEAD","FAILED","BLOCKED")):
            return "#FF073A", f"{blob} · {_gv_age(pulse,now)}"
        if age > 180 or blob in ("WARN","DEGRADED","STALE"):
            return "#FFB020", f"{blob} · {_gv_age(pulse,now)}"
        return "#14F195", f"{blob} · {_gv_age(pulse,now)}"

    oracle_color, oracle_text = status_detail(oracle_status, oracle_pulse)
    exec_color, exec_text = status_detail(executor_status, executor_pulse)
    council_color, council_text = status_detail(council_status, council_pulse)
    metrics = [
        ("PAPER EQUITY", _gv_money(paper_equity), "cumulative wallet truth", "#8EF9FF"),
        ("REALIZED PNL", _gv_money(realized), "cumulative · not one trade", "#8EF9FF"),
        ("LIVE WALLET", _gv_money(live_wallet), f"{live_open} open", "#9945FF"),
        ("POSITIONS", f"{paper_open} paper · {live_open} live", "current exposure", "#14F195"),
        ("PRICE TRUTH", oracle_text, "oracle heartbeat", oracle_color),
        ("COUNCIL", council_text, f"executor {exec_text}", council_color if council_color != '#14F195' else exec_color),
    ]
    cards = "".join(
        f'<div class="snty-gv-metric" style="border-top:2px solid {c}"><div class="snty-gv-label">{_gv_safe(l)}</div>'
        f'<div class="snty-gv-value" style="color:{c}">{_gv_safe(v)}</div><div class="snty-gv-detail">{_gv_safe(d)}</div></div>'
        for l,v,d,c in metrics
    )
    try:
        p = Path(canonical_file)
        digest = hashlib.sha256(p.read_bytes()).hexdigest()[:12]
        mtime = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(p.stat().st_mtime))
        stamp = f"CANONICAL {p.as_posix()} · SHA {digest} · MODIFIED {mtime} · PID {os.getpid()}"
    except Exception:
        stamp = f"CANONICAL RUNTIME · PID {os.getpid()}"

    st.markdown(
        f'<div class="snty-gv-shell"><div class="snty-gv-top"><div class="snty-gv-brand">'
        f'<div class="snty-gv-sigil">◇</div><div><div class="snty-gv-title">SENTINUITY SOVEREIGN OS</div>'
        f'<div class="snty-gv-sub">TRADING TRUTH · INTELLIGENCE · AUTONOMOUS LEARNING</div></div></div>'
        f'<div class="snty-gv-mode" style="color:{mode_color};background:{mode_color}0D">{_gv_safe(mode)}</div></div>'
        f'<div class="snty-gv-metrics">{cards}</div><div class="snty-runtime-stamp">{_gv_safe(stamp)}</div></div>',
        unsafe_allow_html=True,
    )


def _gv_rail() -> None:
    links = [
        ("truth","Trade Truth"),("gate","Final Gate"),("flow","Flow Engine"),
        ("learning","Post-Exit"),("council","Council"),("intelligence","Intelligence"),
        ("diagnostics","Diagnostics"),
    ]
    st.markdown('<div class="snty-gv-rail">' + ''.join(
        f'<a href="#{a}">{html.escape(t)}</a>' for a,t in links) + '</div>', unsafe_allow_html=True)


def _gv_section(anchor: str, index: str, title: str, subtitle: str) -> None:
    st.markdown(
        f'<div id="{html.escape(anchor)}" class="snty-section-head"><div class="snty-section-index">{html.escape(index)}</div>'
        f'<div class="snty-section-copy"><div class="snty-section-title">{html.escape(title)}</div>'
        f'<div class="snty-section-sub">{html.escape(subtitle)}</div></div><div class="snty-section-line"></div></div>',
        unsafe_allow_html=True,
    )


_GV_UI_AVAILABLE = True

if _GV_UI_AVAILABLE:
    try:
        _gv_inject_css()
        _gv_header(DB_PATH, __file__)
        # SIGNOFF_UI_UNIFICATION_20260715:
        # One top navigation surface only. The crystalline SNTY command rail
        # below owns route navigation; the duplicate in-page anchor rail is
        # intentionally not mounted.
    except Exception:
        pass

# SENTINUITY_V2_SOVEREIGN_GLASSBOX_SHELL_20260716
# Presentation-only. Reads runtime truth; never mutates execution or schema.
try:
    from ui.sovereign_v2_shell import inject_sovereign_v2 as _inject_sovereign_v2
    _inject_sovereign_v2(st, DB_PATH)
except Exception as _v2_shell_err:
    try:
        st.caption(f"V2 shell unavailable (backend unaffected): {type(_v2_shell_err).__name__}: {_v2_shell_err}")
    except Exception:
        pass

render_polaris_hud()

# Crown navigation moved to top (right after convergence_gate) per directive

# Agent cards moved after cortex/hero per mobile directive

# 3. VITALS + FLOW ENGINE + CORTEX ARENA (3D gated) + COGNITION + VITALITY
# All of the above live inside render_living_cortex() for data coherence
# (dom_state, dom_narrative, and all DFs computed once and shared)
render_top_command_nav()
# UNIFORM_TYPE_20260612: one type scale site-wide - Orbitron headers, Rajdhani
# body, Share Tech Mono telemetry - substrate node stops drifting from main.
st.markdown("""<style>
[data-testid="stMarkdownContainer"] p, [data-testid="stMarkdownContainer"] li
 {font-family:Rajdhani,sans-serif;font-size:.88rem;line-height:1.5;color:#C9D4CC}
.stTabs [data-baseweb="tab"] p, .stTabs button
 {font-family:'Orbitron',sans-serif !important;font-size:0.66rem !important;
  letter-spacing:.14em !important;text-transform:uppercase}
[data-testid="stDataFrame"] *, [data-testid="stTable"] *
 {font-family:'Share Tech Mono',monospace !important;font-size:.68rem !important}
[data-testid="stCaptionContainer"], .stCaption
 {font-family:'Share Tech Mono',monospace !important;font-size:0.66rem !important;color:#5A7060 !important}
h1,h2,h3 {font-family:'Orbitron',sans-serif !important;letter-spacing:.12em}
[data-testid="stExpanderDetails"] p, [data-testid="stExpanderDetails"] li,
[data-testid="stExpanderDetails"] span:not([style*="color"])
 {color:#C9D4CC !important;font-family:Rajdhani,sans-serif}
[data-testid="stExpanderDetails"] [data-testid="stMarkdownContainer"] p {font-size:.88rem !important}
</style>""", unsafe_allow_html=True)
# SUBSTRATE/SECTION ROUTING FIX 20260623: when a sovereign module section is
# selected, skip the entire home composite below so the section renders alone
# (it used to render buried under the full home page, which read as "not loading").
_sec_active = str(st.query_params.get("sec","")).strip().lower() in (
    "worldos","forest","substrate","intel","bio","readme","polaris","ivy","lab","vault")
if not _sec_active:
    # ── SIGNOFF_CANONICAL_HIERARCHY_20260715 ─────────────────────────────────
    # 1. COMPACT SYSTEM HEADER + MODE/STATUS STRIP + BLOCKERS FIRST.
    # Convergence gate (operator blockers) leads; then the one-strip command
    # bar (mode / oracle / lanes / heartbeat), API status and crown deck.
    # The hero/cortex renders AFTER the truth strip - blockers before theatre.

    # CONVERGENCE GATE - operator alert banner, appears ONLY when action needed
    # Drawdown halt, HITL proposals, forge stall, pipeline starve
    # Collapses to nothing when system is clear - never cries wolf
    render_convergence_gate()

    # ── SOVEREIGN COMMAND BAR - compact always-visible system status (top) ───────
    # SIGNOFF_COPYTRADE_PAPER_BONUS_20260613: mode / oracle / pipeline / positions /
    # copytrade lane / heartbeat age in one strip. Read-only, fail-silent.
    try:
        render_sovereign_command_bar()
    except Exception:
        pass

    render_api_status_bar()

    # SIGNOFF_UI_UNIFICATION_20260715:
    # The six-facet crown deck duplicated HOME/GLASSBOX/INTEL/VAULT controls
    # already available in the crystalline SNTY command rail. Its renderer is
    # retained for backwards compatibility but is no longer mounted here.

    # 2-6. HERO + EXECUTION TRUTH + POSITIONS + PnL + DEBATE CHAMBER composite.
    # (The cortex owns the truth strip, balance rows, flow engine, sanctum and
    # Debate Chamber with the truthful idle/stale/blocked states - unchanged.)
    _render_cortex_slow()

    # ── GOLDEN FILTRATION SPINE - one-glance organism pipeline health (real heartbeats) ──
    # Read-only; derives node states from system_heartbeat + recent PnL. Fail-silent.
    try:
        render_golden_filtration_spine()
    except Exception:
        pass

    # PIPELINE TRUTH expander moved into the diagnostics bay at the bottom of the
    # page (SIGNOFF_CANONICAL_HIERARCHY_20260715) - diagnostics are secondary.
    
    # CINEMATIC OVERLAY - expression layer, shows one high-signal event at a time
    # Non-blocking fragment, auto-cycles every 4s, invisible when nothing notable
    try:
        from ui.cinematic_overlay import render_cinematic_overlay, render_lifecycle_visual
        _cinematic_available = True
    except Exception:
        _cinematic_available = False
    
    if _cinematic_available and _heavy_visuals_enabled() and st.session_state.get("world_mode_enabled", False):
        render_cinematic_overlay()
    
    
    # ── SIGNOFF_CANONICAL_HIERARCHY_20260715 body order ──────────────────────
    # Trading truth first: execution lanes → buys/sells feed → PnL cadence →
    # Final Gate & Price Truth glassbox → copytrade truth chain. The decorative
    # panels (lattice, pressure core, heartbeat cards, ascension) follow, and
    # diagnostics (freshness glassbox, pipeline debug, vitality) render last as
    # a visually secondary bay. No renderer removed; every call preserved.

    # 2. EXECUTION TRUTH - per-trade equilibrium meters, full width
    if _GV_UI_AVAILABLE:
        try: _gv_section("truth", "01", "Trade Truth", "Open exposure, execution state and realized outcomes")
        except Exception: pass
    render_unified_execution_lanes()

    # 3. CURRENT POSITIONS + RECENT BUYS/SELLS with real source badges
    st.markdown(
        "<div style='height:1px;margin:2px 0;background:linear-gradient(90deg,"
        "transparent,rgba(153,69,255,0.5),rgba(20,241,149,0.3),transparent);'></div>",
        unsafe_allow_html=True,
    )
    # ── INLINE BUY/SELL FEED - no external delegate, no integrity gate needed ───
    render_motor_output_command_deck()

    # 4. PnL / PERFORMANCE TRUTH
    # GLASS_CADENCE_20260625: real closed-trade cadence chart.
    # Read-only UI; module lives in ui/ to match project layout doctrine.
    try:
        from ui.glass_cadence_chart import render_glass_cadence as _render_glass_cadence
        _render_glass_cadence(
            str(DB_PATH),
            table="paper_positions",
            key_prefix="sol",
            st=st,
            empty_label="No closed Solana paper trades in this cadence window yet",
        )
    except Exception as _cadence_err:
        st.caption(f"Cluster cadence unavailable: {type(_cadence_err).__name__}: {_cadence_err}")

    # LIVE GATE CONSTELLATION — canonical decision boundary between observed
    # outcome cadence and the Final Gate execution arena. Read-only UI.
    try:
        from ui.live_gate_constellation import render_live_gate_constellation
        render_live_gate_constellation(st, DB_PATH, ROOT / "sentinuity_intelligence.db")
    except Exception as _lgc_err:
        st.caption(f"Live Gate Constellation unavailable; backend unaffected: {type(_lgc_err).__name__}: {_lgc_err}")

    # 5. FINAL GATE · PRICE TRUTH · TWIN RAILS (Sovereign Glassbox)
    if _GV_UI_AVAILABLE:
        try: _gv_section("gate", "02", "Final Gate & Execution Arena", "Candidate evidence, price freshness and paper/live admission truth")
        except Exception: pass
    # Moved up from the page tail (SIGNOFF_CANONICAL_HIERARCHY_20260715) so the
    # live-lane admission truth sits with the execution/PnL group. Renderer and
    # data sources unchanged.
    _render_final_gate_glassbox()

    # ── secondary expression layer follows the truth group ───────────────────
    if _GV_UI_AVAILABLE:
        try: _gv_section("flow", "03", "Sovereign Flow Engine", "Discovery through qualification, execution and organism pressure")
        except Exception: pass
    # AGENT HEARTBEAT CARDS
    try:
        _render_agent_heartbeat_cards()
    except Exception:
        pass

    # HOLOGRAPHIC ASCENSION MAP - cinematic visual, World Mode only
    if st.session_state.get("world_mode_enabled", False):
        render_holographic_ascension_map()

    # GOLDEN LATTICE - evolution chamber + operator seal gate
    # Real HITL proposals: always visible (operator may need to act without enabling world).
    # Collapses to nothing when no action needed - never clutters the view.
    render_golden_lattice()

    # LOGIC GATES: Signal Latch + Matrix Filtration + Same-Eyes
    # (these are inside render_living_cortex already - no duplicate call needed)

    # INTELLIGENCE SUBSTRATE (inside render_living_cortex - no duplicate)

    # ORGANISM CONVICTION + PRESSURE CORE - full width
    render_organism_pressure_core()

    if _GV_UI_AVAILABLE:
        try: _gv_section("learning", "04", "Post-Exit Intelligence", "Unbounded continuation, trajectory and exit-cohort learning")
        except Exception: pass

    # LIFECYCLE VISUAL - position state layer, shows PLI states per open position
    # Gated behind world mode - it's part of the world expression layer
    if _cinematic_available and _heavy_visuals_enabled() and st.session_state.get("world_mode_enabled", False):
        render_lifecycle_visual()

    # LAUNCH FRESHNESS GLASSBOX moved to the diagnostics bay at page bottom
    # (SIGNOFF_CANONICAL_HIERARCHY_20260715) - same renderer, same data.
    
    # ── COPY-TRADE DATA ABSORPTION PIPELINE - oracle/scout task with real data ───
    def render_copy_trade_absorption_pipeline() -> None:
        """
        Visual digestion pipeline for smart wallet intelligence.
        ORACLE/SCOUT daily task - ingests wallet profiles, builds fingerprints, scores conviction.
        OBSERVE / TRAINING mode only - advisory influence, no live copy execution.
        """
        import sqlite3
        import time
        
        # Read real source status from DB
        sources_status = []
        fingerprints_count = 0
        signals_count = 0
        
        try:
            conn = sqlite3.connect(str(DB_PATH), timeout=2.0)
            conn.row_factory = sqlite3.Row
            
            # Get source status
            sources = conn.execute("""
                SELECT source_name, status, last_success_at, records_seen 
                FROM smart_wallet_sources 
                ORDER BY last_success_at DESC LIMIT 5
            """).fetchall()
            
            for s in sources:
                age = time.time() - float(s['last_success_at'] or 0)
                sources_status.append({
                    'name': s['source_name'],
                    'status': s['status'],
                    'age': age,
                    'count': int(s['records_seen'] or 0)
                })
            
            # Get fingerprint count
            fp_row = conn.execute("SELECT COUNT(*) c FROM wallet_entry_fingerprints").fetchone()
            fingerprints_count = int(fp_row['c']) if fp_row else 0
            
            # Get signal count
            sig_row = conn.execute("SELECT COUNT(*) c FROM wallet_entry_likelihood_signals").fetchone()
            signals_count = int(sig_row['c']) if sig_row else 0
            
            conn.close()
        except Exception:
            pass
        
        # Determine pipeline health
        fresh_sources = sum(1 for s in sources_status if s['age'] < 3600 and s['status'] == 'OK')
        pipeline_health = "ACTIVE" if fresh_sources > 0 else "NEEDS ORACLE SCOUT RUN"
        health_color = "#14F195" if fresh_sources > 0 else "#FF073A"
        
        # Build source list
        source_html = ""
        if sources_status:
            for s in sources_status:
                age_str = f"{int(s['age']//60)}m" if s['age'] < 3600 else f"{int(s['age']//3600)}h"
                status_col = "#14F195" if s['status'] == 'OK' else "#888"
                source_html += (
                    f'<div style="font-size:0.66rem;color:{status_col};margin:2px 0;">'
                    f'• {s["name"]}: {s["count"]} wallets ({age_str} ago)</div>'
                )
        else:
            source_html = '<div style="font-size:0.66rem;color:#FF073A;">● No sources - Oracle wallet scout needs to run</div>'
        
        stages = [
            ("1. SOURCE", f"{len(sources_status)} Active", "#8EF9FF"),
            ("2. EXTRACT", "Wallet Scout", "#8EF9FF"),
            ("3. FINGERPRINT", f"{fingerprints_count} Built", "#8EF9FF"),
            ("4. SCORE", f"{signals_count} Signals", "#FFD700"),
            ("5. ABSORB", "Training Set", "#14F195"),
            ("6. INFLUENCE", "Advisory", "#9945FF"),
        ]
    
        stages_html = ""
        for title, sub, color in stages:
            stages_html += (
                f'<div style="flex:1;min-width:108px;background:rgba(5,2,16,0.9);'
                f'padding:6px 8px;border-radius:5px;border:1px solid {color}33;">'
                f'<div style="color:{color};font-weight:700;font-size:0.66rem;">{title}</div>'
                f'<div style="color:#888;font-size:0.66rem;margin-top:2px;">{sub}</div>'
                f'</div>'
            )
    
        full_html = (
            f'<div style="margin:12px 0 6px 0;padding:10px 14px;'
            f'background:rgba(153,69,255,0.04);'
            f'border:1px dashed rgba(153,69,255,0.3);'
            f'border-radius:8px;">'
            f'<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">'
            f'<div style="font-family:Orbitron,sans-serif;font-size:0.66rem;'
            f'color:#9945FF;letter-spacing:2px;font-weight:700;">'
            f'§¬ COPY-TRADE DATA ABSORPTION PIPELINE'
            f'</div>'
            f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:{health_color};">'
            f'{pipeline_health}'
            f'</div>'
            f'</div>'
            f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:#14F195;margin-bottom:8px;">'
            f'ORACLE/SCOUT DAILY TASK - READ-ONLY TELEMETRY - ZERO LIVE EXECUTION INFLUENCE'
            f'</div>'
            f'<div style="display:flex;flex-wrap:wrap;gap:6px;margin-bottom:8px;">'
            + stages_html +
            f'</div>'
            f'<div style="margin-top:8px;padding-top:8px;border-top:1px solid rgba(153,69,255,0.2);">'
            f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:rgba(142,249,255,0.7);margin-bottom:4px;">'
            f'🔭 ORACLE WALLET SCOUT SOURCES:'
            f'</div>'
            + source_html +
            f'</div>'
            f'<div style="font-family:Share Tech Mono;font-size:0.66rem;color:#888;margin-top:6px;">'
            f'Task: Oracle agents scout GMGN/top wallets → extract trades → build Safe-X fingerprints → '
            f'score conviction → train paper-trade advisory system (no live copy execution).'
            f'</div>'
            f'</div>'
        )
    
        st.markdown(full_html, unsafe_allow_html=True)
    
    
    # ── SMART-MONEY MYCELIUM{_holoq("copytrade")} FILTRATION CHAMBER ──────────────────────────────────
    # UNIFIED COGNITIVE MODULE - merges intake telemetry (absorption pipeline) with
    # conviction filtering matrices (smart wallet hub) into ONE container. Removes
    # the previous look of having two stacked, duplicate-looking copy-trade panels.
    # Strict observe-only - paper advisory, zero live execution influence.
    st.markdown("<div class='mycelial-artery'>", unsafe_allow_html=True)
    st.markdown("""
    <div style="margin: 14px 0 4px; padding: 14px 14px 6px; background: rgba(5,2,16,0.92); border: 1px solid rgba(153,69,255,0.25); border-radius: 12px; box-shadow: 0 0 15px rgba(153,69,255,0.05);">
        <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px;">
            <span style="font-family: 'Orbitron', sans-serif; font-size: 0.7rem; color: #9945FF; letter-spacing: 2px; font-weight: 700;">
                §¬ SMART-MONEY MYCELIUM - COPY-TRADE FILTRATION
            </span>
            <span style="font-family: 'Share Tech Mono'; font-size: 0.66rem; color: #14F195; background: rgba(20,241,149,0.1); padding: 2px 6px; border-radius: 4px; border: 1px solid #14F195;">
                OBSERVE ONLY - PAPER ADVISORY
            </span>
        </div>
        <div style="font-family: 'Share Tech Mono'; font-size: 0.66rem; color: rgba(142,249,255,0.6); margin-bottom: 6px;">
            // INFRASTRUCTURE PATH: Ingesting raw scout data into read-only training substrates. Zero live execution influence.
        </div>
    </div>
    """, unsafe_allow_html=True)
    
    # STAGE 0 - COPYTRADE LANE CARD - explicit lane state + bonus decision glassbox
    # SIGNOFF_COPYTRADE_PAPER_BONUS_20260613. Read-only, fail-silent.
    try:
        render_copytrade_lane_card()
    except Exception:
        pass
    
    # STAGE 1 - Intake roots (absorption pipeline)
    if _GV_UI_AVAILABLE:
        try: _gv_section("council", "05", "Council & Intelligence", "Polaris workstream, agent truth and external conviction")
        except Exception: pass
    render_copy_trade_absorption_pipeline()
    
    # STAGE 2 - Digestion (conviction matrix nested inside the same chamber)
    if _SMART_WALLET_AVAILABLE:
        try:
            render_smart_wallet_conviction_matrix(DB_PATH)
        except Exception:
            st.markdown(
                "<div style='font-family: Share Tech Mono; font-size: 0.66rem; color: #777;'>"
                "⬡ Awaiting active wallet scout incoming telemetry streams...</div>",
                unsafe_allow_html=True,
            )
    else:
        st.caption("Copy-trade conviction matrix unavailable - services.smart_wallet_hub not importable.")
    
    st.markdown("</div>", unsafe_allow_html=True)  # close mycelial-artery

    # SIGNOFF_TRUTH_DF_REFRESH_20260715: the page-level panels below previously
    # read the module-level EMPTY placeholder DataFrames (they were only ever
    # populated inside render_living_cortex's local scope), so the latched/veto
    # rows, active-proposal cards, applied-patch list and conviction meter always
    # rendered from empty frames. Refresh from the cached bundle (ttl=5s - no
    # extra DB cost) so these panels show the same real data the cortex shows.
    try:
        (wallet_df, raw_dna_df, snapshots_df, open_pos_df, executions_df,
         reviews_df, proposals_df, debate_df, calibration_df, open_count_df,
         heal_log_df, heartbeat_df, patch_history_df, autopsy_df,
         cognition_df) = _fetch_all_dashboard_data()
    except Exception:
        pass  # placeholders remain empty; panels fail soft exactly as before

    # ── POST-EXECUTION DIAGNOSTICS - vitality, gates, substrate, doctrine ─────────
    # Grand Vision order: pressure → execution → motor → THEN diagnostics
    st.markdown(
        "<div style='margin:20px 0 8px;height:1px;background:linear-gradient(90deg,"
        "transparent,rgba(153,69,255,0.3),transparent);'></div>",
        unsafe_allow_html=True
    )
    
    # ── SELF-HEALING VITALITY SCANNER ─────────────────────────────────────
    st.markdown("<div style='margin-top:10px;border-radius:10px;border:1px solid rgba(153,69,255,0.25);overflow:hidden;'>",unsafe_allow_html=True)
    # Use cached heartbeat bundle - avoids raw DB hit on every main render
    @st.cache_data(ttl=20, show_spinner=False)
    def _fetch_vitality_data():
        try:
            _vc = sqlite3.connect(str(DB_PATH), timeout=2.0)
            _hb = pd.read_sql_query(
                "SELECT service_name, status, last_pulse, COALESCE(note,'') AS note FROM system_heartbeat ORDER BY service_name ASC",
                _vc
            )
            _now = time.time()
            _hc_rows = pd.read_sql_query(
                "SELECT note, timestamp FROM cognition_log "
                "WHERE stage IN ('GUARDIAN_HEAL','AUTO_HEAL','HEAL','GUARDIAN') "
                f"AND timestamp > {_now - 600} "
                "ORDER BY timestamp DESC LIMIT 6",
                _vc
            )
            _vc.close()
            return _hb, _hc_rows
        except Exception:
            return pd.DataFrame(), pd.DataFrame()
    
    _vt_heartbeat_df, _heal_rows_df = _fetch_vitality_data()
    _vt_conn = None  # no longer opened directly
    if not _vt_heartbeat_df.empty:
        _hb_now2=time.time()
        _svcd=[]
        for _,row in _vt_heartbeat_df.iterrows():
            svc2=str(row.get("service_name","")).upper(); st2=str(row.get("status",""))
            try: p2=float(row.get("last_pulse") or 0)
            except: p2=0.0
            a2=int(_hb_now2-p2) if p2>0 else None
            if p2<=0: sk,as2="zombie","dead"
            elif a2>420: sk,as2="stale",f"{a2}s"
            elif a2>120: sk,as2="warn",f"{a2}s"
            elif st2 in ("ERROR","DEAD"): sk,as2="error",f"{a2}s"
            else: sk,as2="alive",f"{a2}s"
            _svcd.append({"n":svc2,"s":sk,"a":as2})
    
        # ── Build heal command log for glass-box visibility ──────────────────
        # Query recent guardian auto-heal events so operator sees real commands
        _heal_cmds = []
        try:
            # Use pre-fetched heal rows from cached _fetch_vitality_data()
            for _, _hr in _heal_rows_df.iterrows():
                _age = int(_hb_now2 - float(_hr.get("timestamp") or 0))
                _heal_cmds.append({"note": str(_hr.get("note") or "")[:80], "age": _age})
        except Exception:
            pass
    
        # Also show guardian heartbeat note which contains heal counts
        _guardian_note = ""
        try:
            _gn = _vt_heartbeat_df[_vt_heartbeat_df["service_name"]=="system_guardian"]["note"].values
            if len(_gn): _guardian_note = str(_gn[0] or "")
        except Exception:
            pass
    
        import json as _j3
        _sj=_j3.dumps(_svcd)
        _hj=_j3.dumps(_heal_cmds)
    
        _heal_html = ""
        if _heal_cmds or _guardian_note:
            _cmd_rows = "".join(
                f'<div style="display:flex;gap:8px;align-items:baseline;padding:2px 0;border-bottom:1px solid rgba(20,241,149,0.06);">'
                f'<span style="color:rgba(20,241,149,0.35);font-size:0.66rem;min-width:32px;">{c["age"]}s</span>'
                f'<span style="color:rgba(20,241,149,0.75);font-size:0.66rem;">{html.escape(c["note"])}</span>'
                f'</div>'
                for c in _heal_cmds
            )
            _gn_html = (
                f'<div style="color:rgba(142,249,255,0.5);font-size:0.66rem;letter-spacing:1px;'
                f'padding:3px 0 4px;border-bottom:1px solid rgba(20,241,149,0.08);">'
                f'Guardian: {html.escape(_guardian_note[:120])}</div>'
            ) if _guardian_note else ""
            _heal_html = (
                f'<div style="margin-top:8px;padding:6px 8px;background:rgba(20,241,149,0.03);'
                f'border-radius:4px;border:1px solid rgba(20,241,149,0.1);">'
                f'<div style="font-family:Orbitron,sans-serif;font-size:0.66rem;letter-spacing:3px;'
                f'color:#8EF9FF;margin-bottom:4px;">&#x2699; AUTO-HEAL LOG</div>'
                + _gn_html
                + (_cmd_rows or '<div style="color:#333;font-size:0.66rem;">no heal events in last 10m</div>')
                + '</div>'
            )
    
        _sh=f"""<style>
    @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Orbitron:wght@400;700&display=swap');
    #vt{{font-family:'Share Tech Mono',monospace;padding:12px 14px;background:rgba(5,2,16,0.85);}}
    .vt-h{{display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;}}
    .vt-t{{font-size:0.7rem;letter-spacing:4px;color:#8EF9FF;font-family:'Orbitron',sans-serif;}}
    .vt-l{{font-size:0.66rem;letter-spacing:3px;}}
    .vt-pb{{height:1px;background:rgba(255,255,255,0.06);margin-bottom:8px;border-radius:1px;overflow:hidden;}}
    .vt-b{{height:1px;background:linear-gradient(90deg,#14F195,#8EF9FF);width:0%;transition:width 0.1s linear;}}
    .vt-w{{position:relative;}}
    .vt-bm{{position:absolute;left:0;right:0;height:1px;pointer-events:none;z-index:5;opacity:0;background:linear-gradient(90deg,transparent,#14F195,#8EF9FF,#14F195,transparent);box-shadow:0 0 8px #14F195;transition:opacity 0.1s;}}
    .vr{{display:flex;align-items:center;gap:7px;padding:4px 7px;margin-bottom:1px;border-radius:3px;border-left:2px solid transparent;position:relative;overflow:hidden;font-size:0.66rem;letter-spacing:1px;transition:all 0.2s;}}
    .vr.sc{{background:rgba(20,241,149,0.06);border-left-color:#8EF9FF!important;}}
    .vr-n{{flex:1;color:rgba(255,255,255,0.5);transition:color 0.2s;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}}
    .vr.sc .vr-n{{color:#fff;}}
    .vr-s{{font-weight:700;letter-spacing:2px;min-width:44px;text-align:right;transition:all 0.2s;}}
    .vr-a{{color:rgba(255,255,255,0.18);font-size:0.66rem;min-width:32px;text-align:right;}}
    .ca{{color:#14F195;}}.cs{{color:#FFB347;}}.cw{{color:#FFD700;}}.ce{{color:#FF073A;}}.cz{{color:#FF073A;}}
    .ba{{border-left-color:#14F195!important;}}.bs,.bw{{border-left-color:#FFB347!important;}}.be,.bz{{border-left-color:#FF073A!important;}}
    @keyframes sh2{{0%{{transform:translateX(-100%)}}100%{{transform:translateX(700%)}}}}
    .vr.bs .sh2,.vr.be .sh2,.vr.bz .sh2,.vr.bw .sh2{{position:absolute;top:0;left:0;width:12%;height:100%;background:linear-gradient(90deg,transparent,rgba(255,255,255,0.05),transparent);animation:sh2 3.5s linear infinite;}}
    @keyframes hg{{0%,100%{{background:rgba(0,0,0,0)}}50%{{background:rgba(255,215,0,0.1)}}}}
    .vr.hl{{animation:hg 0.8s ease-in-out infinite;}}
    @keyframes jh{{0%{{border-left-color:#FFD700;box-shadow:inset 0 0 10px rgba(255,215,0,0.12)}}100%{{border-left-color:#14F195;box-shadow:none;}}}}
    .vr.jh{{animation:jh 1.2s ease-out forwards;}}
    .vr.jh .vr-s{{color:#FFD700;}}
    .vt-st{{display:flex;justify-content:space-around;margin-top:8px;padding-top:7px;border-top:1px solid rgba(20,241,149,0.1);}}
    .vt-sn{{font-family:'Orbitron',sans-serif;font-size:11px;font-weight:700;text-align:center;}}
    .vt-sl{{font-size:11px;letter-spacing:2px;color:rgba(255,255,255,0.2);margin-top:1px;text-align:center;}}
    /* cmd log */
    #cmd-log{{font-family:'Share Tech Mono',monospace;font-size:0.66rem;padding:5px 8px;
      background:rgba(0,0,0,0.3);border-radius:4px;margin-top:8px;border:1px solid rgba(20,241,149,0.12);max-height:100px;overflow-y:auto;}}
    #cmd-log .cmd{{color:#14F195;opacity:0;animation:cmdIn 0.3s ease forwards;}}
    #cmd-log .cmd.dim{{color:rgba(20,241,149,0.3);}}
    @keyframes cmdIn{{from{{opacity:0;transform:translateX(-4px)}}to{{opacity:1;transform:none}}}}
    </style>
    <div id="vt">
    <div class="vt-h"><span class="vt-t">⬡ SYSTEM VITALITY</span><span class="vt-l" id="vt-l" style="color:#14F195;">NOMINAL</span></div>
    <div class="vt-pb"><div class="vt-b" id="vt-b"></div></div>
    <div class="vt-w" id="vt-w"><div class="vt-bm" id="vt-m"></div><div id="vt-list"></div></div>
    <div class="vt-st" id="vt-st"></div>
    <div id="cmd-log"><span style="color:rgba(20,241,149,0.2);font-size:0.66rem;letter-spacing:2px;">// HEAL COMMAND STREAM //</span></div>
    </div>
    <script>
    /* SIGNOFF_VITALITY_TRUTH_20260715: the scan strip animates over REAL
       heartbeat rows only. The former randomized HEAL_CMDS array and the
       visual STALE→ALIVE flip were fabricated telemetry and are removed.
       The command stream now prints only real cognition_log heal events
       (HJ below), or states plainly that none occurred. Service states are
       never rewritten client-side. */
    const D={_sj};
    const HJ={_hj};
    let sc=false,si=0;
    let clog=document.getElementById('cmd-log');
    function logCmd(txt,dim){{const s=document.createElement('div');s.className='cmd'+(dim?' dim':'');s.textContent='> '+txt;clog.appendChild(s);if(clog.children.length>12)clog.removeChild(clog.children[1]);clog.scrollTop=clog.scrollHeight;}}
    function rdr(){{const l=document.getElementById('vt-list');l.innerHTML='';D.forEach((s,i)=>{{const d=document.createElement('div');d.className=`vr b${{s.s[0]}}`;d.id=`vr${{i}}`;d.innerHTML=`<div class="sh2"></div><span class="vr-n">${{s.n}}</span><span class="vr-s c${{s.s[0]}}">${{s.s.toUpperCase()}}</span><span class="vr-a">${{s.a}}</span>`;l.appendChild(d);}});sts();}}
    function sts(){{const a=D.filter(s=>s.s==='alive').length,w=D.filter(s=>s.s==='stale'||s.s==='warn').length,e=D.filter(s=>s.s==='error'||s.s==='zombie').length;document.getElementById('vt-st').innerHTML=`<div><div class="vt-sn" style="color:#14F195">${{a}}</div><div class="vt-sl">ALIVE</div></div><div><div class="vt-sn" style="color:#FFB347">${{w}}</div><div class="vt-sl">WARN</div></div><div><div class="vt-sn" style="color:#FF073A">${{e}}</div><div class="vt-sl">ERROR</div></div><div><div class="vt-sn" style="color:#8EF9FF">${{D.length}}</div><div class="vt-sl">TOTAL</div></div>`;}}
    function run(){{if(sc)return;sc=true;si=0;document.getElementById('vt-l').textContent='SCANNING';document.getElementById('vt-b').style.width='0%';logCmd('heartbeat sweep - '+D.length+' services (system_heartbeat)',false);step();}}
    function step(){{if(si>=D.length){{finish();return;}}document.getElementById('vt-b').style.width=Math.round(si/D.length*100)+'%';const w=document.getElementById('vt-w'),rh=w.offsetHeight/D.length,bm=document.getElementById('vt-m');bm.style.top=(si*rh+rh/2)+'px';bm.style.opacity='1';document.querySelectorAll('.vr').forEach(r=>r.classList.remove('sc'));const c=document.getElementById(`vr${{si}}`);if(c)c.classList.add('sc');if(D[si]&&D[si].s!=='alive')logCmd(D[si].n+': '+D[si].s.toUpperCase()+' ('+D[si].a+')',false);setTimeout(()=>{{if(c)c.classList.remove('sc');si++;step();}},D[si]&&D[si].s==='alive'?40:130);}}
    function finish(){{document.getElementById('vt-m').style.opacity='0';document.getElementById('vt-b').style.width='100%';
      const bad=D.filter(s=>s.s!=='alive').length;
      document.getElementById('vt-l').textContent = bad===0 ? 'NOMINAL' : (bad+' NEED ATTENTION');
      if(HJ && HJ.length){{HJ.slice(0,6).forEach(c=>logCmd('guardian heal '+c.age+'s ago: '+c.note,false));}}
      else{{logCmd('no guardian heal events in last 10m (cognition_log)',true);}}
      setTimeout(()=>{{document.getElementById('vt-b').style.width='0%';sc=false;setTimeout(run,12000);}},600);
    }}
    rdr();setTimeout(run,700);
    </script>"""
        st.components.v1.html(_sh, height=max(380, len(_svcd)*24+140), scrolling=False)
        if _heal_html:
            st.markdown(_heal_html, unsafe_allow_html=True)
    else:
        st.markdown('<div style="color:#666;font-size:0.66rem;padding:10px;text-align:center;">// HEARTBEAT SILENT //</div>',unsafe_allow_html=True)
    st.markdown("</div>",unsafe_allow_html=True)
    
    
    # SIGNAL LATCH removed - duplicate of execution lanes above
    if not snapshots_df.empty:
        for _,row in snapshots_df[snapshots_df["candidate_state"]=="latched"].head(6).iterrows():
            conf=float(row.get("mint_confidence",0)); token=html.escape(str(row.get("token_name",""))[:18])
            st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 10px;margin-bottom:4px;border-radius:6px;border-left:3px solid {C_GREEN};background:rgba(20,241,149,0.04);font-family:Share Tech Mono,monospace;font-size:0.7rem;"><span style="color:#FFF;font-size:0.7rem;">- {token}</span><span style="color:rgba(20,241,149,0.7);font-size:0.66rem;">{conf:.3f}</span></div>',unsafe_allow_html=True)
        for _,row in snapshots_df[snapshots_df["candidate_state"]=="vetoed"].head(4).iterrows():
            token=html.escape(str(row.get("token_name",""))[:18])
            st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;padding:6px 10px;margin-bottom:4px;border-radius:6px;border-left:3px solid {C_RED};background:rgba(255,7,58,0.04);font-family:Share Tech Mono,monospace;font-size:0.7rem;"><span style="color:#888;font-size:0.7rem;">- {token}</span><span style="color:rgba(255,7,58,0.7);font-size:0.66rem;">VETO</span></div>',unsafe_allow_html=True)
    st.markdown("</div>",unsafe_allow_html=True)
    
    # ── MATRIX FILTRATION (below signal latch) ────────────────────────────
    render_matrix_filtration_panel()
    
    # ── PIPELINE FLOW + SAME-EYES (below live trades) ─────────────────────
    render_forge_ambient()
    render_pipeline_flow_debug()
    render_same_eyes_monitor()
    
    st.markdown("<div style='margin-top:15px;padding:14px 16px;border:1px solid rgba(153,69,255,0.25);border-radius:12px;background:rgba(5,2,16,0.5);overflow:hidden;width:100%;box-sizing:border-box;'>",unsafe_allow_html=True)
    
    # ── DOCTRINE CONFIG SAFETY - page-scope fallback, prevents bottom crash ──────
    try:
        _cfg_df = query_db("SELECT key, value FROM system_config")
        if not _cfg_df.empty and "key" in _cfg_df.columns and "value" in _cfg_df.columns:
            conf_map = {str(r["key"]): str(r["value"]) for _, r in _cfg_df.iterrows()}
        else:
            conf_map = {}
    except Exception:
        conf_map = {}
    
    try:
        halt_active = str(conf_map.get("DRAWDOWN_HALT_ACTIVE", "0")).strip().lower() in ("1", "true", "yes", "on")
    except Exception:
        halt_active = False
    
    try:
        drawdown_pct = float(conf_map.get("DRAWDOWN_ACCUMULATED_PCT", 0) or 0)
    except Exception:
        drawdown_pct = 0.0
    
    st.markdown(f'<div style="font-family:Share Tech Mono;font-size:0.8rem;letter-spacing:4px;color:{C_PURPLE};">DOCTRINE STATE{_holoq("doctrine")} - PARAMETERS</div>',unsafe_allow_html=True)
    for k,v,vc in [("CONFIDENCE FLOOR",conf_map.get("SUPERVISOR_MIN_MINT_CONFIDENCE","-"),C_CYAN),("TAKE PROFIT %",conf_map.get("TAKE_PROFIT_PCT","-"),C_GREEN),("STOP LOSS %",conf_map.get("STOP_LOSS_PCT","-"),C_RED),("MIN LIQUIDITY","$"+conf_map.get("MIN_LIQUIDITY_USD","-"),C_CYAN),("POSITION SIZE",conf_map.get("POSITION_SIZE_PCT","-")+"% wallet",C_GOLD),("TRAIL ACTIVATE",conf_map.get("TRAIL_ACTIVATE_PCT","-")+"%",C_GOLD),("DRAWDOWN","- ACTIVE" if halt_active else f"{drawdown_pct:.1f}% acc",C_RED if halt_active else C_CYAN)]:
        st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;margin-bottom:5px;border-radius:6px;background:rgba(255,255,255,0.02);font-family:Share Tech Mono,monospace;"><span style="color:#8EF9FF;font-size:0.68rem;letter-spacing:1px;">{k}</span><span style="color:{vc};font-size:0.72rem;font-weight:700;">{v}</span></div>',unsafe_allow_html=True)
    st.markdown("</div>",unsafe_allow_html=True)
    
    if not proposals_df.empty:
        st.markdown("<div style='margin-top:15px;padding:14px 16px;border:1px solid rgba(153,69,255,0.25);border-radius:12px;background:rgba(5,2,16,0.5);overflow:hidden;width:100%;box-sizing:border-box;'>",unsafe_allow_html=True)
        st.markdown(f'<div style="font-family:Share Tech Mono;font-size:0.8rem;letter-spacing:4px;color:{C_GREEN};">POLARIS - ACTIVE PROPOSALS</div>',unsafe_allow_html=True)
        for _,row in proposals_df.head(4).iterrows():
            st.markdown(f'<div style="background:rgba(20,241,149,0.05);border:1px solid rgba(20,241,149,0.3);border-radius:8px;padding:10px 14px;margin-bottom:8px;font-size:.7rem;"><div style="color:{C_GREEN};font-size:0.66rem;">{html.escape(str(row.get("proposal_type","")))}</div><div style="color:#FFF;line-height:1.5;">{html.escape(str(row.get("suggested_action",""))[:100])}</div></div>',unsafe_allow_html=True)
        st.markdown("</div>",unsafe_allow_html=True)
    
    if not patch_history_df.empty:
        import datetime as _dt
        st.markdown("<div style='margin-top:15px;padding:14px 16px;border:1px solid rgba(153,69,255,0.25);border-radius:12px;background:rgba(5,2,16,0.5);overflow:hidden;width:100%;box-sizing:border-box;'>",unsafe_allow_html=True)
        st.markdown(f'<div style="color:{C_GOLD};font-size:.8rem;letter-spacing:4px;">SOVEREIGN PATCHES - APPLIED</div>',unsafe_allow_html=True)
        for _,ph in patch_history_df.head(5).iterrows():
            outcome=str(ph.get("outcome","pending")).upper(); ptype=str(ph.get("proposal_type",""))[:22]; applied=float(ph.get("applied_at",0) or 0)
            try: ts_str=_dt.datetime.fromtimestamp(applied).strftime("%m/%d %H:%M")
            except: ts_str="-"
            out_col=C_GREEN if outcome=="IMPROVED" else (C_RED if outcome=="DEGRADED" else C_GOLD)
            st.markdown(f'<div style="display:flex;justify-content:space-between;align-items:center;padding:7px 10px;margin-bottom:5px;border-radius:6px;background:rgba(255,255,255,0.02);font-family:Share Tech Mono,monospace;"><span style="color:#FFF;font-size:0.68rem;">{html.escape(ptype)}</span><span style="color:{out_col};font-size:0.7rem;font-weight:700;">{ts_str}</span></div>',unsafe_allow_html=True)
        st.markdown("</div>",unsafe_allow_html=True)
    
    # Convergence Gate and intelligence substrate - single render at bottom
    if _GV_UI_AVAILABLE:
        try: _gv_section("intelligence", "06", "Secondary Systems", "Copytrade, Substrate and autonomous research surfaces")
        except Exception: pass
    render_intelligence_substrate_panel()
    st.markdown("<div style='margin-top:20px;padding:14px 16px;border:1px solid rgba(153,69,255,0.25);border-radius:12px;background:rgba(5,2,16,0.5);'>",unsafe_allow_html=True)
    
    # Live conviction score from 5 signals
    _latch_ok = (not snapshots_df.empty and
    len(snapshots_df[snapshots_df["candidate_state"] == "latched"]) > 0)
    _wr_num = (round((int((reviews_df["win_loss"]=="WIN").sum())/max(len(reviews_df),1))*100,1)
    if len(reviews_df) >= 3 else 0)
    _wr_ok = _wr_num >= 60
    _halt_ok = not halt_active
    # SIGNOFF_CONVICTION_TRUTH_20260715: score over the 3 real signals actually
    # evaluated (latch, win-rate, no-halt). The previous sum carried two
    # hard-coded False phantom signals and divided by 5 while the label said /3.
    _signals_fired = sum([_latch_ok, _wr_ok, _halt_ok])
    conviction_score = min(100, int((_signals_fired / 3) * 100))
    bar_color = C_GREEN if conviction_score >= 60 else (C_GOLD if conviction_score >= 40 else C_RED)
    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
    try:
        _runner_gold_pct = 75.0
        try:
            if isinstance(locals().get("row"), dict):
                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
        except Exception:
            _runner_gold_pct = 75.0
        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
            _state = "RUNNER"
            _state_col = "#FFD700"
    except Exception:
        pass
    
    st.markdown(f"""
    <div style="margin-bottom:16px;">
    <div style="display:flex;justify-content:space-between;font-family:Share Tech Mono;font-size:0.8rem;letter-spacing:4px;color:{C_NUGGET};margin-bottom:8px;">
        <span>ORGANISM CONVICTION</span><span style="color:{bar_color};">{conviction_score}/100</span>
    </div>
    <div style="height:8px; border-radius:9999px; background:#222; overflow:hidden;">
        <div style="height:100%; width:{conviction_score}%; background:{bar_color}; box-shadow:0 0 10px {bar_color};"></div>
    </div>
    <div style="font-family:Share Tech Mono;font-size:0.66rem;color:#888;margin-top:4px;">{_signals_fired}/3 MIN SIGNALS - {"THRESHOLD MET" if _signals_fired >= 3 else "BELOW THRESHOLD"}</div>
    </div>
    """, unsafe_allow_html=True)
    
    st.markdown("</div>",unsafe_allow_html=True)
    
    
    # ── DIAGNOSTICS CLUSTER (SIGNOFF_CANONICAL_HIERARCHY_20260715) ────────────
    if _GV_UI_AVAILABLE:
        try: _gv_section("diagnostics", "07", "Diagnostics & AI Handoff", "Collapsed runtime fingerprint, heartbeats and exact exceptions")
        except Exception: pass
    # Visually secondary, page-bottom. Launch freshness glassbox (real DB-backed
    # freshness/maintenance telemetry, unchanged renderer) + the expandable
    # diagnostics bay (runtime authority fingerprint, heartbeat ages, blocker
    # counts, oracle freshness, council producer state, copytrade ingestion
    # state, schema/query exceptions, pipeline truth, AI handoff export).
    try:
        render_launch_freshness_glassbox()
    except Exception as exc:
        st.caption(f"Launch Freshness Glassbox unavailable: {type(exc).__name__}: {exc}")

    try:
        _render_diagnostics_bay()
    except Exception as _dbay_err:
        st.caption(f"Diagnostics bay unavailable: {type(_dbay_err).__name__}: {_dbay_err}")

    # >>> SIGNOFF_SOVEREIGN_VOICE_GATE_MOUNT
    # Sovereign Voice Gate - Living Command Chamber
    # Safe voice/task bridge. View-only/paper-only by default. No live mutation.
    try:
        from services.sovereign_voice_gate import render_sovereign_voice_gate as _render_sovereign_voice_gate
        _render_sovereign_voice_gate(query_db if 'query_db' in globals() else None)
    except Exception as _svg_err:
        try:
            st.warning(f"Sovereign Voice Gate unavailable: {type(_svg_err).__name__}: {_svg_err}")
        except Exception:
            pass
    # <<< SIGNOFF_SOVEREIGN_VOICE_GATE_MOUNT

    # SOVEREIGN GLASSBOX MOUNT moved up to the execution/PnL truth group via
    # _render_final_gate_glassbox() (SIGNOFF_CANONICAL_HIERARCHY_20260715).
    
    st.markdown(
        "<div style='margin:20px 0 10px;padding:8px 0;border-top:1px solid rgba(153,69,255,0.15);"
        "font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:4px;color:#333;'>"
        "⬡ THE CORTEX - MACHINE MIND &amp; RESEARCH SUBSTRATE</div>",
        unsafe_allow_html=True
    )
    
    # ── TOP-LEVEL SECTION ROUTER - TOPNAV_AS_REAL_NAV_20260612 ───────────────────
    # CONSOLIDATION: the eight stacked bottom expanders are removed. The top
    # command rail's ?sec= parameter is now the ONE navigation: it routes to a
    # single full-width section rendered here. With no section selected, a compact
    # module grid (links only, no content) is shown. Section CONTENT is unchanged
    # and reads the same backend sources - only the navigation architecture moved.
st.markdown('<div id="lore-modules"></div>', unsafe_allow_html=True)

try:
    from ui.sovereign_health_tab import render_health_tab as _render_health_tab
    _health_tab_available = True
except Exception:
    _health_tab_available = False

try:
    from ui.substrate_node import render_substrate_tab as _render_substrate_tab
    _substrate_node_available = True
except Exception:
    try:
        from services.substrate_node import render_substrate_tab as _render_substrate_tab
        _substrate_node_available = True
    except Exception:
        try:
            # Root level fallback
            import importlib.util as _ilu, os as _os
            _sn_path = _os.path.join(_os.path.dirname(__file__), 'substrate_node.py')
            if not _os.path.exists(_sn_path):
                _sn_path = _os.path.join(_os.path.dirname(__file__), 'services', 'substrate_node.py')
            _spec = _ilu.spec_from_file_location("substrate_node", _sn_path)
            _sn_mod = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_sn_mod)
            render_substrate_tab = _sn_mod.render_substrate_tab
            _render_substrate_tab = render_substrate_tab
            _substrate_node_available = True
        except Exception:
            _substrate_node_available = False


def _lore_card(title: str, accent: str, rgba: str, subtitle: str, body: str,
               img_src: str = "", emoji: str = "") -> None:
    """Shared lore card builder - replaces five near-identical inline blocks."""
    if img_src:
        _media = (f'<img src="{img_src}" style="width:100%;max-width:320px;object-fit:cover;'
                  f'border-radius:12px;border:1px solid rgba({rgba},0.2);margin-bottom:12px;">')
    else:
        _media = (f'<div style="font-size:3rem;margin-bottom:10px;">{emoji}</div>')
    st.markdown(
        f'<div style="border:1px solid rgba({rgba},0.3);border-radius:16px;padding:28px 24px;'
        f'background:rgba(5,2,16,0.7);display:flex;flex-direction:column;align-items:center;text-align:center;">'
        f'{_media}'
        f'<div style="font-family:Orbitron,sans-serif;font-size:1.4rem;font-weight:900;color:{accent};'
        f'letter-spacing:6px;margin:14px 0 6px;text-shadow:0 0 20px rgba({rgba},0.8);">{title}</div>'
        f'<div style="font-family:Share Tech Mono,monospace;font-size:0.68rem;color:rgba({rgba},0.6);'
        f'letter-spacing:3px;margin-bottom:14px;">{subtitle}</div>'
        f'<div style="font-family:Rajdhani,sans-serif;font-size:0.95rem;color:#CCC;line-height:1.7;'
        f'max-width:520px;">{body}</div></div>',
        unsafe_allow_html=True,
    )


def _sec_polaris() -> None:
    _lore_card("POLARIS", "#8EF9FF", "142,249,255", "AUTONOMOUS ARCHITECT - SOVEREIGN MIND",
               "The fixed point. Everything in the organism navigates by her. She watches every "
               "trade, every veto, every latched signal - detecting patterns across hundreds of "
               "cycles and proposing precise changes to improve the organism.",
               img_src=img_polaris or "", emoji="❄️")


def _sec_ivy() -> None:
    _lore_card("IVARIS // GENESIS SQUAD", C_GOLD, "255,215,0", "ADVERSARIAL CRITIC - IMMUNE SYSTEM",
               "The organism that binds and never lets go until satisfied. She is the immune "
               "system - finding every reason a proposal could fail before it reaches the "
               "operator. The last line of defence before a patch touches live trading.",
               img_src=img_ivy or "", emoji="🔥")


def _sec_lab() -> None:
    _lore_card("SENTINUITY LAB CORE", C_PURPLE, "153,69,255", "IMAGE-TO-IMAGE MATRIX",
               "Multi-modal creative engine. The visual cortex of the organism - where signals "
               "become imagery and the synthetic mind renders its own perception of market reality.",
               img_src=img_lab or "", emoji="”¬")


def _sec_vault() -> None:
    _lore_card("HERITAGE VAULT", C_CYAN, "142,249,255", "IMMUTABLE CODE MEMORY",
               "Every code change fingerprinted. Every evolution recorded. The organism cannot "
               "forget its own history - each patch, each debate, each improvement sealed into "
               "the sovereign ledger forever.", emoji="›️")


def _sec_readme() -> None:
    _lore_card("POLARIZE", C_PURPLE, "153,69,255", "SOVEREIGN TERMINAL ARCHITECTURE",
               "The closed-loop autonomous organism - ingest, qualify, supervise, execute, "
               "review, propose, debate, approve, apply. The organism that trades itself into "
               "existence.", emoji="📋")



def _render_council_build_status_panel() -> None:
    """SIGNOFF_COUNCIL_VISIBILITY_20260621 - show autonomous build blockers,
    standing Solana edge task, and apply journal without requiring a separate tab."""
    if not _BUILD_MAP_AVAILABLE:
        st.caption("Council build map not available - services.council_build_map import failed.")
        return
    try:
        _bm = _get_build_map()
    except Exception as _bm_err:
        st.caption(f"Council build map unavailable: {_bm_err}")
        return

    _summary = _bm.get("summary", {}) if isinstance(_bm, dict) else {}
    st.markdown("### ° Council Build / Autonomous Edge Work")
    st.caption(
        f"queue_open={_summary.get('queue_open', 0)} · "
        f"standing_blocked={_summary.get('standing_blocked', 0)} · "
        f"approvals={_summary.get('approvals', 0)} · "
        f"blockers={_summary.get('blockers', 0)} · "
        f"patch_journal={_summary.get('patch_journal_rows', 0)}"
    )

    _blockers = (_bm.get("blockers") or [])[:8]
    if _blockers:
        st.warning("NEEDS-YOU / BLOCKERS")
        for _b in _blockers:
            st.caption(f"[{_b.get('stage','BLOCKED')}] {_b.get('ago','?')} - {_b.get('msg','')}")
    else:
        st.success("No council build blockers surfaced.")

    _rows = []
    for _item in (_bm.get("queues") or [])[:12]:
        _rows.append({
            "lane": "queue",
            "id": _item.get("id"),
            "status": _item.get("label") or _item.get("status"),
            "phase": _item.get("phase"),
            "priority": _item.get("priority"),
            "risk": _item.get("risk"),
            "agent": _item.get("agent"),
            "title": _item.get("title"),
            "blocker/next": _item.get("blocker") or _item.get("verifier") or "",
        })
    for _item in (_bm.get("standing") or [])[:12]:
        _rows.append({
            "lane": "standing",
            "id": _item.get("id"),
            "status": _item.get("label") or _item.get("status"),
            "phase": _item.get("stage"),
            "priority": _item.get("priority"),
            "risk": _item.get("risk"),
            "agent": _item.get("owner"),
            "title": _item.get("title"),
            "blocker/next": _item.get("blocker") or _item.get("next_action") or "",
        })
    if _rows:
        st.dataframe(pd.DataFrame(_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No council queue/standing rows found yet. Orchestrator will seed them on launch.")


def _sec_substrate() -> None:
    if _substrate_node_available:
        try:
            _render_substrate_tab(query_db)
        except Exception as _ste:
            st.error(f"Substrate tab error: {_ste}")
        _render_council_build_status_panel()
    else:
        st.markdown("""
<div style='text-align:center;padding:40px;font-family:Share Tech Mono,monospace;
    font-size:0.66rem;color:#555;letter-spacing:2px;'>
    // SUBSTRATE NODE MODULE NOT FOUND //<br>
    Copy ui/substrate_node.py to load Lane 2
</div>""", unsafe_allow_html=True)


def _sec_bio() -> None:
    if _health_tab_available:
        try:
            _render_health_tab()
        except Exception as _he:
            st.error(f"Health tab error: {_he}")
    else:
        st.markdown("""
<div style='text-align:center;padding:40px;font-family:Share Tech Mono,monospace;
    font-size:0.66rem;color:#555;letter-spacing:2px;'>
    // BIOLOGICAL INTELLIGENCE MODULE NOT FOUND //<br>
    Copy ui/sovereign_health_tab.py to load Lane 3
</div>""", unsafe_allow_html=True)


def _sec_intel() -> None:
    st.header("- INTELLIGENCE - Research & Self-Evolution")
    try:
        from ui.intelligence_tab import render_intelligence_tab as _rit_exp
        _rit_exp(query_db)
    except Exception as _intel_err:
        st.error(f"Intelligence tab error: {str(_intel_err)}")



def _sec_worldos() -> None:
    """Redirect legacy ``?sec=worldos`` links to the canonical HTML World.

    World Mode has one runtime implementation: ``ui/sovereign_world.html``.
    Older bookmarks used the removed ``ui.world_os`` Python component.  Keep
    those links compatible without restoring a second World implementation.
    """
    st.session_state["active_facet"] = "world"
    st.session_state["world_mode_enabled"] = True
    st.session_state["genesis_vault_open"] = False
    st.session_state["hub_anchor"] = "world"
    st.session_state["sx_last_npc_tick"] = 0.0

    try:
        if "sec" in st.query_params:
            del st.query_params["sec"]
    except Exception:
        pass

    st.info("Opening the canonical World interface…")
    st.rerun()

def _sec_forest() -> None:
    """LIVING EXCHANGE FOREST - operational world map (LIVING_FOREST_20260612).
    Every object binds to ui.state_contract.load_world_state(). No ad-hoc SQL
    here, no invented state: a missing source renders as NOT WIRED."""
    try:
        from ui.state_contract import load_world_state
        W = load_world_state(str(DB_PATH))
    except Exception as _wf_err:
        st.error(f"World state contract unavailable: {_wf_err}")
        return

    def _card(title, accent, rgba, status, status_col, rows):
        body = "".join(
            f"<div style='display:flex;justify-content:space-between;gap:10px;"
            f"font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#9DB5A8;"
            f"padding:2px 0;'><span>{k}</span>"
            f"<span style='color:{v_col};text-align:right'>{v}</span></div>"
            for k, v, v_col in rows)
        return (
            f"<div style='border-radius:13px;padding:1px;background:linear-gradient("
            f"150deg,rgba({rgba},.45),rgba({rgba},.10) 60%,transparent);'>"
            f"<div style='border-radius:12px;padding:12px 14px;height:100%;"
            f"background:linear-gradient(150deg,rgba({rgba},.06),rgba(5,7,6,.96)),rgba(5,7,6,.92);"
            f"backdrop-filter:blur(8px);'>"
            f"<div style='display:flex;justify-content:space-between;align-items:center;margin-bottom:7px;'>"
            f"<span style='font-family:Orbitron,sans-serif;font-size:0.66rem;font-weight:900;"
            f"letter-spacing:.16em;color:{accent};'>{title}</span>"
            f"<span style='font-family:Orbitron,sans-serif;font-size:0.66rem;font-weight:700;"
            f"letter-spacing:.12em;color:{status_col};border:1px solid {status_col}55;"
            f"border-radius:999px;padding:2px 8px;'>{status}</span></div>{body}</div></div>")

    NW = "#5A6B60"  # not-wired grey
    o, x, c, p, b, e = (W.get("oracle"), W.get("execution"), W.get("copytrade"),
                        W.get("pipeline"), W.get("build"), W.get("edge"))
    bal, sub = W.get("balance"), W.get("substrate")
    svc = {s.service_name.lower(): s for s in (W.get("services") or [])}

    cards = []
    # 1 - ORACLE TOWER (cyan = price truth)
    if o:
        oc = "#FF073A" if o.is_stale else ("#E8B84C" if o.price_age_sec > 60 else "#8EF9FF")
        cards.append(_card("⛩ ORACLE TOWER", "#8EF9FF", "142,249,255",
            "STALE" if o.is_stale else "FRESH", oc, [
            ("price age", f"{o.price_age_sec:.0f}s" if o.price_age_sec >= 0 else "NOT WIRED",
             oc if o.price_age_sec >= 0 else NW),
            ("signal age (latched avg)", f"{o.signal_age_sec:.0f}s" if o.signal_age_sec >= 0 else "NOT WIRED",
             "#C9D4CC" if o.signal_age_sec >= 0 else NW),
            ("anchor source", o.anchor_source, "#8EF9FF"),
            ("hot set / writes", f"{o.hot_set} mints · {o.write_per_minute}/min", "#C9D4CC"),
            ("heartbeat", o.oracle_status, oc)]))
    # 2 - MYCELIUM FOREST (green = discovery)
    if p:
        dens = p.pending_count + p.qualified_count
        fg = "#14F195" if p.fresh_qualified_priced_count > 0 else "#E8B84C"
        cards.append(_card("🌿 MYCELIUM FOREST", "#14F195", "20,241,149",
            f"{dens} ROOTS", fg, [
            ("pending / qualified", f"{p.pending_count} / {p.qualified_count}", "#C9D4CC"),
            ("fresh qualified+priced", str(p.fresh_qualified_priced_count), fg),
            ("dead roots (stale signal)", str(p.stale_signal_count),
             "#FF073A" if p.stale_signal_count else "#14F195"),
            ("unpriced/stale price", str(p.stale_price_count),
             "#E8B84C" if p.stale_price_count else "#14F195")]))
    # 3 - LATCH GATE (purple = execution threshold)
    if x:
        gc = {"OPEN": "#14F195", "CAUTION": "#E8B84C",
              "SLAMMED_SHUT": "#FF073A"}.get(x.gate_status, NW)
        cards.append(_card("⛓ LATCH GATE", "#9945FF", "153,69,255", x.gate_status, gc, [
            ("last pickup", f"{x.latch_to_open_sec:.0f}s" if x.latch_to_open_sec is not None
             else "no measured opens yet", "#C9D4CC" if x.latch_to_open_sec is not None else NW),
            ("budget (target 90s)", f"{x.max_budget_sec:.0f}s fail-closed", "#9945FF"),
            ("stale latches now", str(x.stale_latch_count),
             "#FF073A" if x.stale_latch_count else "#14F195"),
            ("exec-ready", str(x.exec_ready_count), "#C9D4CC"),
            ("last veto", (x.last_veto_reason or "-")[:46], "#E8B84C" if x.last_veto_reason else NW)]))
    # 4 - MOTOR FACTORY (execution engine)
    ex_hb = svc.get("execution_engine") or svc.get("executor")
    mf_age = ex_hb.pulse_age_sec if (ex_hb and ex_hb.pulse_age_sec is not None) else -1
    mf_col = "#14F195" if 0 <= mf_age < 120 else ("#E8B84C" if 0 <= mf_age < 600 else "#FF073A")
    if p:
        cards.append(_card("⚙ MOTOR FACTORY", "#E8B84C", "232,184,76",
            f"PULSE {mf_age:.0f}s" if mf_age >= 0 else "NO HEARTBEAT", mf_col, [
            ("latched / exec-ready", f"{p.latched_count} / {p.exec_ready_count}", "#C9D4CC"),
            ("dead-token share 24h", f"{p.dead_token_share:.1f}%",
             "#FF073A" if p.dead_token_share > 30 else "#14F195"),
            ("red smoke", "QUARANTINE GATE ARMED", "#14F195")]))
    # 5 - VAULT (gold = reserves; shared BalanceTruth only)
    if bal:
        lv = f"${bal.live_wallet_usd:,.2f}" if bal.live_wallet_synced else "wallet not synced"
        cards.append(_card("🛡 VAULT", "#FFD700", "255,215,0",
            bal.trading_mode.upper(), "#9945FF" if bal.trading_mode == "live" else "#14F195", [
            ("paper equity / cash", f"${bal.paper_equity:,.2f} / ${bal.paper_cash:,.2f}", "#14F195"),
            ("paper reserved", f"${bal.paper_open_reserved:,.2f}", "#C9D4CC"),
            ("live wallet", lv, "#9945FF" if bal.live_wallet_synced else NW),
            ("live available", f"${bal.live_available_usd:,.2f}" if bal.live_wallet_synced
             else "wallet not synced", "#9945FF" if bal.live_wallet_synced else NW)]))
    # 6 - SUBSTRATE NODE (durable memory)
    if sub:
        disk_col = "#FF073A" if 0 <= sub.disk_free_gb < 5 else "#14F195"
        cards.append(_card("⬡ SUBSTRATE NODE", "#8EF9FF", "142,249,255",
            f"{sub.db_latency_ms:.1f}ms" if sub.db_latency_ms >= 0 else "NOT WIRED",
            "#8EF9FF", [
            ("db size / wal", f"{sub.sqlite_memory_bytes/1e6:.0f}MB / {sub.wal_size_bytes/1e6:.1f}MB", "#C9D4CC"),
            ("disk free", f"{sub.disk_free_gb:.1f} GB" if sub.disk_free_gb >= 0 else "NOT WIRED", disk_col),
            ("read latency", f"{sub.db_latency_ms:.2f} ms", "#8EF9FF")]))
    # 7 - COUNCIL SANCTUM (build organism)
    if b:
        idle = (b.open_tasks == 0 and b.forge_created == 0 and b.patches_ready == 0)
        bc = "#FF073A" if idle else "#14F195"
        cards.append(_card("° COUNCIL SANCTUM", "#9945FF", "153,69,255",
            "IDLE - NOTHING SEEDED" if idle else "BUILDING", bc, [
            ("open tasks", str(b.open_tasks), bc),
            ("active task", (b.active_task_title or "none")[:42],
             "#C9D4CC" if b.active_task_title else NW),
            ("forge c/v/a", f"{b.forge_created}/{b.forge_validated}/{b.forge_applied}", "#C9D4CC"),
            ("proposals / patches", f"{b.proposals_open} open · {b.patches_ready} ready", "#C9D4CC"),
            ("applied 24h", str(b.patches_applied_today), "#C9D4CC"),
            ("blocker", (b.last_blocker or "-")[:44], "#E8B84C" if b.last_blocker else NW)]))
    # 8 - COPYTRADE BAZAAR
    if c:
        mc = {"OBSERVE": "#8EF9FF", "PAPER_ONLY": "#14F195",
              "LIVE_GATED": "#9945FF"}.get(c.influence_mode, "#E8B84C")
        cards.append(_card("🛕 COPYTRADE BAZAAR", "#E8B84C", "232,184,76",
            c.influence_mode, mc, [
            ("tracked / active 1h", f"{c.tracked_wallets} / {c.active_wallets}", "#C9D4CC"),
            ("smart trades 24h", str(c.recent_smart_trades), "#C9D4CC"),
            ("conviction / matched", f"{c.conviction_score:.2f} · {c.matched_wallets}",
             "#FFD700" if c.conviction_score >= 0.75 else "#C9D4CC"),
            ("scanner error", (c.last_error or "-")[:44], "#FF073A" if c.last_error else NW)]))

    # EDGE HEALTH banner above the grid
    if e:
        ec = {"HEALTHY": "#14F195", "RESTORING": "#8EF9FF",
              "DEGRADED": "#E8B84C", "QUARANTINED": "#FF073A"}.get(e.current_edge_status, NW)
        st.markdown(
            f"<div style='display:flex;flex-wrap:wrap;gap:16px;align-items:center;"
            f"border-radius:12px;padding:10px 16px;margin-bottom:10px;"
            f"border:1px solid {ec}44;background:rgba(5,7,6,.92);"
            f"font-family:Share Tech Mono,monospace;font-size:0.66rem;color:#9DB5A8;'>"
            f"<span style='font-family:Orbitron,sans-serif;font-weight:900;font-size:0.66rem;"
            f"letter-spacing:.16em;color:{ec};'>EDGE: {e.current_edge_status}</span>"
            f"<span>mode {e.entry_quality_mode}</span>"
            f"<span>momentum_5m ≥ {e.min_price_momentum_5m:g}</span>"
            f"<span>positive MTM {'REQUIRED' if e.require_positive_mtm else 'OFF ⚠'}</span>"
            f"<span>24h W/L {e.recent_wins}/{e.recent_losses}</span>"
            f"<span>dead cuts {e.recent_dead_token_cuts}</span></div>",
            unsafe_allow_html=True)

    st.markdown(
        "<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(255px,1fr));"
        "gap:10px;'>" + "".join(cards) + "</div>", unsafe_allow_html=True)
    if W.get("errors"):
        st.caption("contract degraded: " + ", ".join(W["errors"]))


_HUB_SECTIONS = {
    "worldos":   ("🜲 WORLD OS - COUNCIL CONTROL",             _sec_worldos),
    "forest":    ("🌲 LIVING EXCHANGE FOREST",                   _sec_forest),
    "substrate": ("🌐 SUBSTRATE NODE",                          _sec_substrate),
    "intel":     ("§  INTELLIGENCE - RESEARCH & SELF-EVOLUTION", _sec_intel),
    "bio":       ("⬡ BIOLOGICAL INTELLIGENCE - LANE 3",         _sec_bio),
    "readme":    ("📋 POLARIZE - README",                        _sec_readme),
    "polaris":   ("❄️ POLARIS — AUTONOMOUS ARCHITECT",          _sec_polaris),
    "ivy":       ("🔥 IVY GENESIS SQUAD",                        _sec_ivy),
    "lab":       ("”¬ SENTINUITY LAB CORE",                      _sec_lab),
    "vault":     ("›️ HERITAGE VAULT",                          _sec_vault),
}

try:
    _sec = str(st.query_params.get("sec", "")).lower()
except Exception:
    _sec = ""

if _sec in _HUB_SECTIONS:
    _sec_title, _sec_fn = _HUB_SECTIONS[_sec]
    st.markdown(
        f"<div style='display:flex;justify-content:space-between;align-items:center;"
        f"margin:6px 0 14px;padding:1px;border-radius:14px;"
        f"background:linear-gradient(100deg,rgba(255,215,0,.45),rgba(153,69,255,.40) 50%,rgba(20,241,149,.30));'>"
        f"<div style='flex:1;display:flex;justify-content:space-between;align-items:center;"
        f"border-radius:13px;padding:10px 16px;background:rgba(5,7,6,.95);"
        f"backdrop-filter:blur(8px);'>"
        f"<span style='font-family:Orbitron,sans-serif;font-size:0.8rem;font-weight:900;"
        f"letter-spacing:5px;color:{C_GOLD};text-shadow:0 0 14px rgba(255,215,0,.5);'>{_sec_title}</span>"
        f"<a href='?' target='_self' style='font-family:Orbitron,sans-serif;font-size:0.66rem;"
        f"font-weight:700;letter-spacing:.18em;color:#8FA89B;text-decoration:none;"
        f"border:1px solid rgba(153,69,255,.35);border-radius:999px;padding:5px 13px;"
        f"transition:all .2s;'>✕ CLOSE</a></div></div>",
        unsafe_allow_html=True,
    )
    _sec_fn()
else:
    # No section selected: compact module grid - LINKS only, zero stacked content.
    # RAIL_V2_20260612: facet tiles share the rail's crystal grammar - hairline
    # gradient frame, glass body, gold bloom on hover, one type voice.
    _grid_pills = "".join(
        f'<a href="?sec={_k}#lore-modules" target="_self" class="snty-facet">{_t}</a>'
        for _k, (_t, _f) in _HUB_SECTIONS.items()
    )
    # SENTINUITY_RUNNER_GOLD_20260621_V3: visual-only runner colour override.
    # If this render scope has _pct/_state/_state_col, runners at >=75% PnL turn gold.
    try:
        _runner_gold_pct = 75.0
        try:
            if isinstance(locals().get("row"), dict):
                _runner_gold_pct = float(locals().get("row", {}).get("runner_gold_pct") or 75.0)
        except Exception:
            _runner_gold_pct = 75.0
        if "_pct" in locals() and "_state_col" in locals() and float(_pct) >= float(_runner_gold_pct):
            _state = "RUNNER"
            _state_col = "#FFD700"
    except Exception:
        pass

    st.markdown(
        f"""<style>
.snty-facet{{position:relative;display:block;text-align:center;text-decoration:none;
 font-family:Orbitron,sans-serif;font-size:0.66rem;font-weight:700;letter-spacing:.16em;
 text-transform:uppercase;color:#9DB5A8;padding:13px 10px;border-radius:12px;
 background:linear-gradient(150deg,rgba(255,215,0,.05),rgba(153,69,255,.06) 60%,rgba(5,7,6,.95)),rgba(5,7,6,.9);
 border:1px solid rgba(153,69,255,.25);backdrop-filter:blur(8px);
 box-shadow:inset 0 1px 0 rgba(255,255,255,.05);transition:all .25s}}
.snty-facet:hover{{color:#FFD700;border-color:rgba(255,215,0,.55);transform:translateY(-1px);
 box-shadow:0 6px 18px rgba(0,0,0,.5),0 0 12px rgba(255,215,0,.25)}}
</style>
<div style='font-family:Share Tech Mono,monospace;font-size:0.66rem;letter-spacing:3px;
 color:{C_PURPLE};margin:14px 0 8px;'>// SOVEREIGN MODULES - OPEN VIA COMMAND RAIL //</div>
<div style='display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
 gap:9px;margin-bottom:14px;'>{_grid_pills}</div>""",
        unsafe_allow_html=True,
    )


# --- GEMINI_DENSITY_SIGNOFF_V8_2_OLD_CARD_WALL_BLOCK_REMOVED ---
# BUY/SELL feed renderer is now inline in this file via _render_inline_buy_sell_feed().
# sov_hub_trade_balance_signoff.py is no longer imported or required.