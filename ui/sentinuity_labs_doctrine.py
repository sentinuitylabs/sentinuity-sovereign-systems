"""Sentinuity Labs presentation doctrine for the trading hub (2026-10-01).

Presentation-only. Reads config through the runtime's own canonical mode
contract (core.mode_contract.resolve) and never writes anything.

Owns:
  * the Labs palette and the separate evidence/status family,
  * the one mode truth every UI surface should display,
  * section-head markup,
  * the final authoritative CSS layer (injected after every legacy layer).
"""
from __future__ import annotations

import html
import sqlite3
from pathlib import Path
from typing import Any

# ── Brand palette (Sentinuity Labs) ─────────────────────────────────────────
ABYSS = "#071417"
PANEL = "#0C1D21"
RAISED = "#12272C"
HAIRLINE = "#1C3A40"
WHITE = "#F4F8F8"
MUTED = "#A9BCBE"
SOFT = "#7E9699"
FAINT = "#4F6669"
AQUA = "#5BE0EC"
AQUA_LIT = "#7FEAF2"
AQUA_DEEP = "#0E6F7E"
ELECTRUM = "#D6B45A"
SOL_VIOLET = "#9945FF"

# ── Evidence / status family — never brand hues ─────────────────────────────
EVIDENCE = {
    "healthy": "#4CC38A",
    "warning": "#F2994A",
    "degraded": "#F2994A",
    "stale": "#F2994A",
    "failed": "#E5484D",
    "blocked": "#E5484D",
    "unknown": SOFT,
}

_TRUE = {"1", "true", "yes", "on", "enabled"}


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else "—"))


def _read_config(db_path: str | Path) -> dict[str, str]:
    cfg: dict[str, str] = {}
    try:
        uri = f"file:{Path(db_path).as_posix()}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=1.5)
        try:
            for k, v in conn.execute("SELECT key, value FROM system_config"):
                cfg[str(k)] = "" if v is None else str(v)
        finally:
            conn.close()
    except Exception:
        pass
    return cfg


def mode_truth(db_path: str | Path) -> dict[str, Any]:
    """The single mode statement for every UI surface.

    Derived from core.mode_contract.resolve — the same resolver the launcher
    and runtime use — so the hub can never disagree with execution authority.
    Returns label/detail/colour plus the raw contract facts.
    """
    cfg = _read_config(db_path)
    if not cfg:
        # Never assert "Paper" when the runtime config could not be read.
        return {"selection": "UNKNOWN", "label": "Mode unknown", "detail": "Runtime config unreadable",
                "color": EVIDENCE["warning"], "tone": "warning", "live_armed": False,
                "contradictions": ["system_config unreadable"], "source": "unavailable"}
    out: dict[str, Any] = {
        "selection": "PAPER", "label": "Paper", "detail": "Live execution off",
        "color": AQUA, "tone": "paper", "live_armed": False, "contradictions": [],
        "source": "unavailable",
    }
    try:
        from core.mode_contract import resolve  # runtime authority, pure read
        c = resolve(lambda k, d=None: cfg.get(k, d))
        out["selection"] = c.operator_selection
        out["live_armed"] = bool(c.live_execution_armed)
        out["contradictions"] = list(c.contradictions)
        out["source"] = c.selection_source
        if c.live_execution_armed:
            out.update(label=f"{c.operator_selection.title()} · live armed",
                       detail="Real-money execution is armed", color=ELECTRUM, tone="live")
        elif c.live_enabled:
            out.update(label=f"{c.operator_selection.title()} · not armed",
                       detail="Live selected, latch incomplete", color=EVIDENCE["warning"],
                       tone="warning")
        elif c.contradictions:
            out.update(label="Paper · check config",
                       detail=c.contradictions[0][:90], color=EVIDENCE["failed"], tone="failed")
    except Exception:
        raw = cfg.get("TRADING_MODE", "paper").strip().lower()
        if raw == "live":
            out.update(selection="LIVE", label="Live · unverified",
                       detail="Mode contract unavailable", color=EVIDENCE["warning"], tone="warning")
    return out


def effective_mode_triplet(db_path: str | Path) -> tuple[str, str, str]:
    """Backwards-compatible (label, colour, detail) for legacy call sites."""
    t = mode_truth(db_path)
    sel = str(t["selection"]).upper()
    return sel, t["color"], str(t["detail"]).upper()


def mode_pill_html(t: dict[str, Any]) -> str:
    title = _esc("; ".join(t.get("contradictions") or []) or t.get("detail"))
    return (f'<span class="sl-mode sl-mode--{_esc(t["tone"])}" title="{title}">'
            f'<i></i>{_esc(t["label"])}</span>')


def section_head(anchor: str, index: str, title: str, subtitle: str = "", first: bool = False) -> str:
    sub = f'<p class="sl-sec__sub">{_esc(subtitle)}</p>' if subtitle else ""
    cls = "sl-sec sl-sec--first" if first else "sl-sec"
    return (f'<header id="{_esc(anchor)}" class="{cls}">'
            f'<span class="sl-sec__idx">{_esc(index)}</span>'
            f'<div class="sl-sec__copy"><h2 class="sl-sec__title">{_esc(title)}</h2>{sub}</div>'
            f'</header>')


def status_cell(label: str, value: str, detail: str, tone: str) -> str:
    return (f'<div class="sl-cell sl-cell--{_esc(tone)}" title="{_esc(label)}: {_esc(value)} — {_esc(detail)}">'
            f'<div class="sl-cell__label">{_esc(label)}</div>'
            f'<div class="sl-cell__value">{_esc(value)}</div>'
            f'<div class="sl-cell__detail">{_esc(detail)}</div></div>')


def tone_for_status(text: str, age_s: float | None = None, stale_after: float = 180.0) -> str:
    blob = str(text or "").upper()
    if any(x in blob for x in ("ERROR", "DEAD", "FAILED", "BLOCKED", "STALLED", "FATAL")):
        return "failed"
    if age_s is None:
        return "unknown"
    if age_s > stale_after or any(x in blob for x in ("WARN", "DEGRADED", "STALE")):
        return "warning"
    return "healthy"


# ── Final authoritative CSS layer ────────────────────────────────────────────
# Injected after every legacy layer. `html body` prefixes win specificity ties
# against older !important rules without editing them out of history.
DOCTRINE_CSS = r"""
<style id="sentinuity-labs-doctrine-20261001">
@import url('https://fonts.googleapis.com/css2?family=IBM+Plex+Sans:wght@400;500;600;700&family=IBM+Plex+Mono:wght@400;500;600&family=Orbitron:wght@600;700&display=swap');
:root{
  --sl-abyss:#071417;--sl-panel:#0C1D21;--sl-raised:#12272C;--sl-hair:#1C3A40;
  --sl-white:#F4F8F8;--sl-muted:#A9BCBE;--sl-soft:#7E9699;--sl-faint:#4F6669;
  --sl-aqua:#5BE0EC;--sl-aqua-lit:#7FEAF2;--sl-aqua-deep:#0E6F7E;--sl-violet:#9945FF;--sl-violet-deep:#51228A;--sl-electrum:#D6B45A;
  --sl-ok:#4CC38A;--sl-warn:#F2994A;--sl-fail:#E5484D;
  --sl-sans:'IBM Plex Sans',system-ui,sans-serif;--sl-mono:'IBM Plex Mono',ui-monospace,monospace;
  --sl-mark:'Orbitron','IBM Plex Sans',sans-serif;
  --sl-r-panel:12px;--sl-r-cell:8px;--sl-gap:12px;
}
/* ground: abyss under the lattice canvas */
html body,html body .stApp,html body [data-testid="stAppViewContainer"]{
  background-color:var(--sl-abyss)!important;color:var(--sl-muted);font-family:var(--sl-sans)}
html body [data-testid="stAppViewContainer"]{background-image:
  radial-gradient(ellipse 70% 42% at 62% -6%,rgba(14,111,126,.20),transparent 70%),
  linear-gradient(180deg,#071417 0%,#06110F00 100%)!important}
html body [data-testid="stHeader"]{background:rgba(7,20,23,.72)!important;backdrop-filter:blur(14px);
  border-bottom:1px solid rgba(28,58,64,.6)}
html body .block-container,html body [data-testid="stMainBlockContainer"]{
  max-width:1360px!important;padding-top:1.1rem!important;padding-left:clamp(14px,3vw,40px)!important;
  padding-right:clamp(14px,3vw,40px)!important}
/* zero-height style/script mounts no longer leave 1rem gaps above the brand */
html body [data-testid="stElementContainer"]:has(> iframe[height="0"]),
html body [data-testid="stElementContainer"]:has(iframe[height="0"]){
  position:absolute!important;width:0!important;height:0!important;overflow:hidden!important;margin:0!important}
html body [data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"] > style:only-child){
  display:none!important}

/* legacy copy variables re-pointed to the Labs text ramp (body copy is not aqua) */
:root{--snty-copy:#A9BCBE!important;--snty-copy-dim:#7E9699!important;--snty-heading:#F4F8F8!important;
  --snty-muted:#7E9699!important;--cyan-soft:rgba(169,188,190,.8)!important}
html body [data-testid="stAppViewContainer"],html body [data-testid="stMarkdownContainer"]{color:var(--sl-muted)}
/* split-tag openers (`<div class=..>` in one st.markdown, `</div>` in another)
   render as empty styled boxes; hide those mounts entirely. Anchors (id) stay. */
html body [data-testid="stElementContainer"]:has([data-testid="stMarkdownContainer"] > div:empty:only-child:not([id])){display:none!important}
html body .gbx-world:empty{display:none!important}
/* restraint: no text glow, no sweeping shines, no hue-cycling */
html body [data-testid="stAppViewContainer"] *{text-shadow:none!important}
html body .shine-gold::before,html body .shine-cyan::before,html body .shine-lattice::before{display:none!important;animation:none!important}
html body .snty-crystal-panel::before{animation:none!important;opacity:.16!important}
html body .snty-crystal-panel::after{animation:none!important;box-shadow:none!important;
  background:linear-gradient(90deg,transparent,rgba(91,224,236,.38),transparent 70%)!important}
html body .crail,html body .crail-synapse,html body .crail-synapse::after{display:none!important}

/* typography groups */
html body [data-testid="stAppViewContainer"] p,html body [data-testid="stAppViewContainer"] li{
  font-family:var(--sl-sans);color:var(--sl-muted);line-height:1.55}
html body [data-testid="stAppViewContainer"] h1,html body [data-testid="stAppViewContainer"] h2,
html body [data-testid="stAppViewContainer"] h3,html body [data-testid="stAppViewContainer"] h4{
  font-family:var(--sl-sans)!important;color:var(--sl-white)!important;letter-spacing:.01em!important;
  font-weight:600!important}
html body [data-testid="stAppViewContainer"] h3{font-size:1.02rem!important}
html body code,html body pre,html body .stCode{font-family:var(--sl-mono)!important}
html body [data-testid="stCaptionContainer"],html body .stCaption{
  font-family:var(--sl-sans)!important;color:var(--sl-soft)!important;font-size:.76rem!important}

/* one surface language */
html body .snty-crystal-panel,html body .snty-cyan-panel,html body .snty-gold-panel,html body .snty-card,
html body .substrate-card,html body .intel-hero,html body .sntFeedWrap,html body .cncl-card,html body .snty-glassbox{
  border:1px solid var(--sl-hair)!important;border-radius:var(--sl-r-panel)!important;
  background:linear-gradient(180deg,rgba(18,39,44,.78),rgba(12,29,33,.86))!important;
  box-shadow:0 1px 0 rgba(244,248,248,.03) inset,0 18px 40px rgba(0,0,0,.28)!important;
  backdrop-filter:blur(6px)}
html body .snty-gold-panel:before,html body .snty-crystal-panel:before,html body .snty-cyan-panel:before,
html body .substrate-card:before{background:none!important}
html body [data-testid="stExpander"]{border:1px solid var(--sl-hair)!important;border-radius:var(--sl-r-panel)!important;
  background:rgba(12,29,33,.80)!important;box-shadow:none!important}
html body [data-testid="stExpander"] summary,html body [data-testid="stExpander"] summary p{
  font-family:var(--sl-sans)!important;font-weight:500!important;letter-spacing:.01em!important;
  color:var(--sl-muted)!important;font-size:.86rem!important}
html body [data-testid="stExpander"] summary:hover p{color:var(--sl-white)!important}
html body [data-testid="stMetric"]{background:rgba(18,39,44,.6)!important;border:1px solid var(--sl-hair)!important;
  border-radius:var(--sl-r-cell)!important;padding:.6rem .8rem!important}
html body [data-testid="stMetricLabel"] p{font-family:var(--sl-sans)!important;color:var(--sl-soft)!important;
  letter-spacing:.02em!important;text-transform:none!important}
html body [data-testid="stMetricValue"]{font-family:var(--sl-mono)!important;color:var(--sl-white)!important;
  font-variant-numeric:tabular-nums}
html body [data-testid="stDataFrame"],html body [data-testid="stTable"]{border:1px solid var(--sl-hair)!important;
  border-radius:var(--sl-r-cell)!important;background:rgba(7,20,23,.72)!important;box-shadow:none!important}
html body .stTabs [data-baseweb="tab-list"]{border-bottom:1px solid var(--sl-hair)!important;gap:2px}
html body .stTabs [data-baseweb="tab"] p,html body .stTabs button{font-family:var(--sl-sans)!important;
  text-transform:none!important;letter-spacing:.01em!important;font-size:.84rem!important;color:var(--sl-soft)!important}
html body .stTabs [aria-selected="true"] p{color:var(--sl-white)!important}
html body .stTabs [aria-selected="true"]{border-bottom:2px solid var(--sl-aqua)!important}

/* gold retired as a routine fill: labels/values step down to the white ramp */
html body .snty-metric-card{border:1px solid var(--sl-hair)!important;border-radius:var(--sl-r-cell)!important;
  background:rgba(18,39,44,.7)!important;box-shadow:none!important}
html body .snty-metric-card::after{display:none!important}
html body .snty-label{color:var(--sl-soft)!important;font-family:var(--sl-sans)!important;letter-spacing:.06em!important}
html body .snty-stat-value{color:var(--sl-white)!important;font-family:var(--sl-mono)!important;font-weight:600!important}
html body .snty-stat-big{font-family:var(--sl-mono)!important;font-weight:600!important}
html body .snty-sub{color:var(--sl-soft)!important;font-family:var(--sl-sans)!important}
html body .snty-section-title{font-family:var(--sl-sans)!important;font-weight:600!important;
  letter-spacing:.06em!important;color:var(--sl-white)!important;font-size:.84rem!important}
html body .snty-section-title.gold,html body .snty-section-title.cyan{color:var(--sl-white)!important}
html body .snty-section-kicker{font-family:var(--sl-mono)!important;color:var(--sl-soft)!important;letter-spacing:.04em!important}
html body .snty-title-row{border-bottom:1px solid var(--sl-hair)!important}
html body .snty-helpbox summary{border-color:rgba(91,224,236,.4)!important;background:transparent!important;
  box-shadow:none!important;color:var(--sl-aqua)!important}
html body .snty-helpbox .snty-help-pop{border-color:var(--sl-hair)!important;background:var(--sl-panel)!important;
  font-family:var(--sl-sans)!important;color:var(--sl-muted)!important}
html body .snty-flow-node{box-shadow:none!important;background:rgba(18,39,44,.7)!important}
/* electrum stays only where real money is armed / funded */
html body .snty-live-funded{border-color:rgba(214,180,90,.6)!important}

/* controls: one hierarchy */
html body .stButton>button,html body .stDownloadButton>button{font-family:var(--sl-sans)!important;
  text-transform:none!important;letter-spacing:.01em!important;font-weight:500!important;font-size:.84rem!important;
  border-radius:8px!important;border:1px solid var(--sl-hair)!important;background:rgba(18,39,44,.7)!important;
  color:var(--sl-muted)!important;box-shadow:none!important}
html body .stButton>button:hover{border-color:rgba(91,224,236,.45)!important;color:var(--sl-white)!important;transform:none!important}
html body .stButton>button[kind="primary"]{background:var(--sl-aqua)!important;color:var(--sl-abyss)!important;border-color:var(--sl-aqua)!important}
html body .stButton>button::after{display:none!important}
html body .stFormSubmitButton>button{font-family:var(--sl-sans)!important;text-transform:none!important;letter-spacing:.01em!important;
  border:1px solid rgba(214,180,90,.55)!important;color:var(--sl-electrum)!important;background:rgba(214,180,90,.06)!important;box-shadow:none!important}
html body .snty-danger-btn .stButton>button{border-color:rgba(229,72,77,.6)!important;color:#F3B5B7!important;background:rgba(229,72,77,.08)!important}

/* ── navigation rail ───────────────────────────────────────────────── */
.sl-nav{position:sticky;top:0;z-index:999;display:flex;align-items:center;gap:18px;
  margin:-2px 0 0;padding:10px 4px 10px 2px;border-bottom:1px solid var(--sl-hair);
  background:linear-gradient(180deg,rgba(7,20,23,.94),rgba(7,20,23,.82));backdrop-filter:blur(14px)}
html body .sl-nav__brand{display:flex;align-items:center;gap:9px;font:600 .78rem var(--sl-mark);letter-spacing:.22em;
  color:var(--sl-white)!important;text-decoration:none!important;flex:0 0 auto}
.sl-nav__links{display:flex;gap:2px;overflow-x:auto;scrollbar-width:none;flex:1 1 auto;min-width:0}
.sl-nav__links::-webkit-scrollbar{display:none}
.sl-nav__links a{font:500 .82rem var(--sl-sans);color:var(--sl-soft)!important;text-decoration:none!important;
  padding:6px 10px;border-radius:6px;white-space:nowrap;transition:color .15s,background .15s}
.sl-nav__links a:hover{color:var(--sl-white)!important;background:rgba(91,224,236,.06)}
.sl-nav__links a.on{color:var(--sl-white)!important;box-shadow:inset 0 -2px 0 var(--sl-violet)}
.sl-nav__sep{width:1px;height:16px;background:var(--sl-hair);flex:0 0 auto;margin:0 6px}
.sl-mode{display:inline-flex;align-items:center;gap:8px;flex:0 0 auto;font:500 .78rem var(--sl-sans);
  color:var(--sl-white);padding:6px 12px 6px 10px;border-radius:999px;border:1px solid var(--sl-hair);background:rgba(12,29,33,.9)}
.sl-mode i{width:7px;height:7px;border-radius:50%;background:var(--sl-aqua)}
.sl-mode--live{border-color:rgba(214,180,90,.6)}.sl-mode--live i{background:var(--sl-electrum)}
.sl-mode--warning{border-color:rgba(242,153,74,.55)}.sl-mode--warning i{background:var(--sl-warn)}
.sl-mode--failed{border-color:rgba(229,72,77,.6)}.sl-mode--failed i{background:var(--sl-fail)}

/* ── hero ─────────────────────────────────────────────────────────── */
html body .snty-hero-wrap{all:unset;display:block;text-align:center;padding:clamp(56px,9vh,104px) 12px clamp(18px,3vh,32px)!important;
  margin:0!important;border:0!important;background:none!important;box-shadow:none!important}
.sl-hero__mark{display:flex;justify-content:center;margin-bottom:22px}
html body .snty-hero-word{all:unset;display:block;font-family:var(--sl-mark)!important;font-weight:600!important;
  font-size:clamp(2.5rem,7.2vw,5.4rem)!important;letter-spacing:clamp(.18em,1.6vw,.32em)!important;
  padding-left:clamp(.18em,1.6vw,.32em);color:var(--sl-white)!important;line-height:1.02;white-space:nowrap;
  background:linear-gradient(180deg,#F4F8F8 0%,#F4F8F8 52%,#A9DDE2 100%);-webkit-background-clip:text;
  -webkit-text-fill-color:transparent;animation:none!important;filter:none!important}
.sl-hero__rule{width:min(320px,48vw);height:1px;margin:26px auto 20px;
  background:linear-gradient(90deg,transparent,rgba(153,69,255,.72),rgba(91,224,236,.74),transparent)}
html body .sl-hero__kicker{font:400 clamp(.98rem,1.4vw,1.12rem)/1.5 var(--sl-sans)!important;color:var(--sl-muted)!important;
  margin:0 auto!important;max-width:620px}
html body .snty-hero-sub{all:unset;display:block;font:400 .86rem/1.5 var(--sl-mono)!important;color:var(--sl-soft)!important;
  margin:14px auto 0!important;max-width:720px;font-style:normal!important;animation:none!important}
html body #cmd-ticker{color:var(--sl-faint);font-family:var(--sl-mono)!important;margin-top:6px!important}
html body .snty-legal{all:unset;display:block;font:400 .72rem var(--sl-sans)!important;color:var(--sl-faint)!important;
  letter-spacing:.02em!important;margin-top:22px!important;opacity:1!important}


/* approved Labs lockup: exact frame from the 27-Sep master reveal, not a redraw */
.sl-hero__master{display:flex;justify-content:center;align-items:center;margin:0 auto 8px;max-width:min(620px,82vw)}
.sl-hero__master img{display:block;width:min(500px,76vw);height:auto;mix-blend-mode:screen;filter:saturate(1.04) contrast(1.04);}
.sl-hero__sr-only{position:absolute!important;width:1px!important;height:1px!important;padding:0!important;margin:-1px!important;overflow:hidden!important;clip:rect(0,0,0,0)!important;white-space:nowrap!important;border:0!important}
/* Solana keeps its own violet identity; aqua remains flow/truth and Substrate stays aqua-led. */
html body .snty-solana-panel{border:1px solid rgba(153,69,255,.22)!important;box-shadow:inset 0 1px rgba(153,69,255,.10)!important}
html body .snty-solana-panel::before{background:linear-gradient(90deg,var(--sl-violet),var(--sl-aqua),transparent)!important;opacity:.62!important}
html body .snty-solana-title{color:var(--sl-violet)!important}

/* ── section heads (numbered because the page is a pipeline sequence) ── */
.sl-sec{display:flex;align-items:flex-start;gap:16px;margin:64px 0 18px;padding-top:22px;border-top:1px solid var(--sl-hair);
  scroll-margin-top:72px}
.sl-sec__idx{font:500 .78rem var(--sl-mono);color:var(--sl-aqua);padding-top:4px;min-width:22px}
.sl-sec__copy{min-width:0}
html body .sl-sec__title{margin:0!important;padding:0!important;font:600 1.32rem/1.25 var(--sl-sans)!important;
  color:var(--sl-white)!important;letter-spacing:-.005em!important}
html body .sl-sec__sub{margin:4px 0 0!important;font:400 .9rem/1.45 var(--sl-sans)!important;color:var(--sl-soft)!important;max-width:680px}
html body .sl-sec--first{margin-top:12px}


/* ── system state ─────────────────────────────────────────────────── */
.sl-state{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:1px;border:1px solid var(--sl-hair);
  border-radius:var(--sl-r-panel);overflow:hidden;background:var(--sl-hair)}
.sl-cell{background:rgba(12,29,33,.94);padding:14px 16px 13px;min-width:0;position:relative}
.sl-cell::before{content:"";position:absolute;left:16px;top:0;width:22px;height:2px;background:var(--sl-faint)}
.sl-cell__label{font:500 .76rem var(--sl-sans);color:var(--sl-soft);letter-spacing:.02em}
.sl-cell__value{font:600 1.02rem/1.3 var(--sl-mono);color:var(--sl-white);margin-top:6px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis;font-variant-numeric:tabular-nums}
.sl-cell__detail{font:400 .76rem/1.35 var(--sl-sans);color:var(--sl-soft);margin-top:3px;
  white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sl-cell--healthy::before{background:var(--sl-ok)}.sl-cell--warning::before{background:var(--sl-warn)}
.sl-cell--failed::before{background:var(--sl-fail)}.sl-cell--paper::before{background:var(--sl-aqua)}
.sl-cell--live::before{background:var(--sl-electrum)}
.sl-cell--failed .sl-cell__value{color:#F3B5B7}.sl-cell--warning .sl-cell__value{color:#F7C99C}
.sl-truth{display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin:10px 2px 14px;font:400 .8rem var(--sl-sans);color:var(--sl-soft)}
.sl-truth b{font-weight:500;color:var(--sl-muted)}
.sl-truth .sl-dot{width:7px;height:7px;border-radius:50%;display:inline-block;margin-right:7px;vertical-align:1px}
.sl-truth--alert{padding:10px 14px;border:1px solid rgba(229,72,77,.55);border-radius:var(--sl-r-cell);
  background:rgba(229,72,77,.07);color:#F3B5B7}
.sl-truth--alert b{color:#F3B5B7}

/* ── responsive ───────────────────────────────────────────────────── */
@media (max-width:1100px){.sl-state{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media (max-width:640px){
  .sl-state{grid-template-columns:repeat(2,minmax(0,1fr))}
  .sl-cell__value,.sl-cell__detail{white-space:normal;overflow:visible;text-overflow:clip}
  .sl-cell__value{font-size:.94rem}
  html body .snty-title-row{flex-wrap:wrap!important;align-items:flex-start!important}
  html body .snty-title-left{flex-wrap:wrap!important;row-gap:4px}
  html body .snty-title-left > span[style]{margin-left:0!important;flex:1 1 100%;letter-spacing:.04em!important}
  html body .snty-section-kicker{white-space:normal!important;flex:1 1 100%}
  .sl-nav{gap:10px;flex-wrap:wrap;padding:8px 2px}
  .sl-nav__links{order:3;flex:1 1 100%}
  .sl-nav__brand{font-size:.72rem}
  .sl-mode{margin-left:auto;font-size:.74rem}
  .sl-sec{margin-top:44px;gap:12px}
  html body .sl-sec__title{font-size:1.12rem!important}
  html body .snty-hero-word{font-size:clamp(1.9rem,10.4vw,3rem)!important;letter-spacing:.16em!important;padding-left:.16em}
  html body .snty-hero-wrap{padding-top:40px!important;padding-bottom:30px!important}
}
@media (prefers-reduced-motion:reduce){*,*::before,*::after{animation:none!important;transition:none!important}}
</style>
"""


def doctrine_css() -> str:
    return DOCTRINE_CSS
