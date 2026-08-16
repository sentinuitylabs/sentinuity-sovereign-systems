# coding: utf-8
"""
ui/organism_field.py — QUEST_FIELD_20260813 (DROP-IN ROUTED REPLACEMENT)

The expedition you can follow. Mobile-first.

WHAT THIS REPLACES
==================
    ui/organism_field.py     — the map-first pass that failed comprehension.
                               Its terrain survives here, demoted below the
                               fold as secondary context.
    ui/lumen_field.py        — nodes now travel with the quest.
    the linear Debate Chamber feed — becomes FIELD CHRONICLE, disclosure 2.
    standalone expedition/specimen cards — become findings inside the quest.

`services/organism_pressure.py` is kept unchanged: it derives pressure
correctly. The failure was representation, not measurement.

THE FIX FOR THE FAILED SCREENSHOT
=================================
The previous pass led with a map of equal-sized circles labelled "0/3 senses",
and put the explanation underneath in small prose. A viewer had to learn an
ontology before learning anything. Corrections, in order of the composition:

    1  Sovereign strip     — capital truth stays on the first screen
    2  THE QUESTION        — one plain-English sentence, largest text on page
    3  WHAT'S HAPPENING    — who is working, on what, right now
    4  LATEST FINDING      — the most recent real thing learned
    5  THE TRAIL           — where we are in the journey, compactly
    ...fold...
    6  Terrain             — the map, small, only the relevant territory lit
    7  Field chronicle / show their work — disclosure

Nothing above the fold names a table, an enum, or a "sense". Human language
first; provenance one level deeper.

MOTION MEANS WORK
=================
There is no ambient animation. A node moves only where an assignment is live.
A stage glimmers only when it is the active stage. Everything static is calm.
A quest with no live agents renders completely still — which is truthful.

NO FABRICATED JOURNEY
=====================
Stages come from `council_quest.active_quest()`, which marks a stage REACHED
only when persisted evidence exists. Fogged stages are drawn as fogged. When
nothing is under way, the camp is quiet and says what it is waiting for.
"""
from __future__ import annotations

import html
import math
from typing import Any, Dict, List, Optional

try:
    import streamlit as st
except Exception:                                     # pragma: no cover
    st = None                                         # type: ignore

try:
    from ui.sentinuity_tokens import C as TOKENS
except Exception:                                     # pragma: no cover
    TOKENS = {}

try:
    from services.council_quest import (
        active_quest, quiet_camp, JOURNEY, HABITAT_LABEL,
        STAGE_REACHED, STAGE_ACTIVE, STAGE_FOGGED,
    )
except Exception:                                     # pragma: no cover
    active_quest = lambda *a, **k: None
    quiet_camp = lambda *a, **k: {}
    JOURNEY, HABITAT_LABEL = [], {}
    STAGE_REACHED, STAGE_ACTIVE, STAGE_FOGGED = "REACHED", "ACTIVE", "FOGGED"

try:
    from services.organism_pressure import snapshot as _org_snapshot
except Exception:                                     # pragma: no cover
    _org_snapshot = lambda *a, **k: {"faculties": []}


def _tk(k: str, d: str) -> str:
    return str(TOKENS.get(k, d))


VOID   = _tk("void", "#050210")
ORG    = _tk("vio_hi", "#9945FF")
PERC   = _tk("cy_hi", "#8EF9FF")
TRUE   = _tk("em_hi", "#14F195")
EARNED = _tk("gold_hi", "#FFD700")
DANGER = _tk("coral_hi", "#FF073A")
ASH    = "#514A6B"
INK1   = "#EDE8FF"          # raised from token ink_1: the screenshot was too dim
INK2   = "#B3AAD4"
INK3   = "#7B7398"


def _e(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


# ── HUMAN LANGUAGE LAYER ────────────────────────────────────────────────────
# The pressure model speaks in perception percentages and faculty keys. That is
# correct for the model and unreadable on a phone. Nothing from that vocabulary
# is allowed above the fold, so every faculty gets a plain sentence about what
# has actually stopped. Directive: lead with "Price evidence has stopped
# arriving", never with "0/3 senses".
FACULTY_PLAIN = {
    "EDGE":          ("the trading pipeline", "candidates to trade"),
    "PRICE_TRUTH":   ("price tracking", "usable prices"),
    "SMART_MONEY":   ("the smart-money read", "wallet and volume evidence"),
    "EXECUTION":     ("execution", "orders reaching the market"),
    "COPYTRADE":     ("wallet watching", "wallet activity"),
    "SUBSTRATE":     ("the proving ground", "experiments to learn from"),
    "INTELLIGENCE":  ("what we've understood", "conclusions to draw on"),
    "COUNCIL":       ("the council's own work", "completed investigations"),
    "OBSERVABILITY": ("our instruments", "readings we can trust"),
}


def _human_headline(quest: Dict[str, Any]) -> str:
    """One plain sentence. No enum, no percentage, no table name."""
    subject, _ = FACULTY_PLAIN.get(quest.get("key", ""), ("this part of the system", "evidence"))
    total = int(quest.get("senses_total", 0) or 0)
    have = int(quest.get("senses_have", 0) or 0)
    # A persisted Quest may have a more precise question supplied by the
    # lifecycle bridge. Prefer that when it is already human-readable.
    persisted = str(quest.get("headline") or "").strip()
    if persisted and "unable to observe" not in persisted.lower():
        return persisted
    if have == 0:
        return f"Why has {subject} stopped reporting altogether?"
    return f"Why is {subject} only partly working?"


def _human_why(quest: Dict[str, Any]) -> str:
    """What has actually stopped, in words a stranger can read."""
    subject, thing = FACULTY_PLAIN.get(
        quest.get("key", ""), ("this part of the system", "evidence"))
    have, total = quest.get("senses_have", 0), quest.get("senses_total", 0)
    if total == 0:
        return (f"We can't get a reading from {subject} at all right now — "
                f"which is worse than a bad reading, because a bad reading "
                f"at least tells you what is wrong.")
    if have == 0:
        return (f"None of the {total} kinds of evidence {subject} depends on "
                f"are arriving, so it can't produce {thing}.")
    missing = total - have
    return (f"{subject.capitalize()} needs {total} kinds of evidence and is "
            f"only receiving {have}. The other {missing} stopped arriving.")


CSS = f"""
<style id="quest-field">
@import url('https://fonts.googleapis.com/css2?family=Orbitron:wght@600&family=Rajdhani:wght@400;500;600;700&family=Share+Tech+Mono&display=swap');
.qf{{background:{VOID};border:1px solid rgba(153,69,255,.26);border-radius:12px;
 font-family:'Rajdhani',system-ui,sans-serif;color:{INK1};overflow:hidden}}
.qf *{{box-sizing:border-box}}

/* 1 — SOVEREIGN. Flat, still, tabular. Never animated. */
.qf-sov{{display:grid;grid-template-columns:repeat(3,1fr);background:#0A0518;
 border-bottom:1px solid rgba(153,69,255,.26)}}
.qf-sc{{padding:7px 10px;border-right:1px solid rgba(153,69,255,.12)}}
.qf-sk{{font-family:'Share Tech Mono',monospace;font-size:9.5px;letter-spacing:.11em;
 color:{INK3};text-transform:uppercase}}
.qf-sv{{font-family:'Share Tech Mono',monospace;font-size:16px;line-height:1.2;
 font-variant-numeric:tabular-nums;color:#F6F3FF}}
.qf-sv.neg{{color:{DANGER}}}.qf-sv.pos{{color:{TRUE}}}

/* 2 — THE QUESTION. Largest thing on the page. */
.qf-head{{padding:15px 16px 3px}}
.qf-eyebrow{{font-family:'Share Tech Mono',monospace;font-size:9.5px;
 letter-spacing:.17em;color:{EARNED};text-transform:uppercase;margin-bottom:5px}}
.qf-q{{font-size:23px;line-height:1.24;font-weight:700;color:#FFFFFF;
 letter-spacing:.005em;margin:0 0 7px}}
.qf-why{{font-size:15px;line-height:1.46;color:{INK2};margin:0 0 3px}}
.qf-dest{{font-family:'Share Tech Mono',monospace;font-size:10.5px;
 letter-spacing:.07em;color:{PERC};margin-top:7px}}

/* 3 — WHAT'S HAPPENING NOW */
.qf-now{{padding:11px 16px 3px}}
.qf-now-l{{font-family:'Share Tech Mono',monospace;font-size:9.5px;
 letter-spacing:.15em;color:{INK3};text-transform:uppercase;margin-bottom:7px}}
.qf-agent{{display:flex;gap:9px;align-items:flex-start;margin-bottom:8px}}
.qf-agent-g{{flex:0 0 auto;margin-top:1px}}
.qf-agent-t{{font-size:14.5px;line-height:1.4;color:{INK1}}}
.qf-agent-r{{font-weight:600;color:{PERC}}}
.qf-still{{font-size:14.5px;color:{INK2};line-height:1.45}}

/* 4 — LATEST FINDING */
.qf-find{{margin:9px 16px 0;padding:11px 13px;border-radius:8px;
 background:rgba(153,69,255,.07);border-left:2px solid {PERC}}}
.qf-find.refutes{{border-left-color:{DANGER};background:rgba(255,7,58,.06)}}
.qf-find-w{{font-family:'Share Tech Mono',monospace;font-size:10px;
 letter-spacing:.13em;text-transform:uppercase;color:{PERC};margin-bottom:4px}}
.qf-find.refutes .qf-find-w{{color:{DANGER}}}
.qf-find-b{{font-size:15px;line-height:1.5;color:{INK1}}}

/* 5 — THE TRAIL. Horizontal, compact, scrollable on narrow screens. */
.qf-trail{{display:flex;align-items:flex-start;gap:0;overflow-x:auto;
 padding:14px 16px 12px;-webkit-overflow-scrolling:touch;scrollbar-width:none}}
.qf-trail::-webkit-scrollbar{{display:none}}
.qf-st{{flex:0 0 auto;text-align:center;min-width:62px;position:relative}}
.qf-dot{{width:9px;height:9px;border-radius:50%;margin:0 auto 5px;
 background:{ASH};opacity:.5}}
.qf-st.reached .qf-dot{{background:{PERC};opacity:1}}
.qf-st.active .qf-dot{{background:{EARNED};opacity:1;
 box-shadow:0 0 0 3px rgba(255,215,0,.18);animation:qf-live 2.6s ease-in-out infinite}}
.qf-st-n{{font-family:'Share Tech Mono',monospace;font-size:8.5px;
 letter-spacing:.07em;color:{ASH};text-transform:uppercase;line-height:1.3}}
.qf-st.reached .qf-st-n{{color:{INK2}}}
.qf-st.active .qf-st-n{{color:{EARNED};font-weight:700}}
.qf-link{{position:absolute;top:4px;left:50%;width:100%;height:1px;
 background:{ASH};opacity:.35;z-index:-1}}
.qf-st.reached .qf-link{{background:{PERC};opacity:.5}}
@keyframes qf-live{{0%,100%{{opacity:1}}50%{{opacity:.55}}}}

/* 6 — TERRAIN. Below the fold. Small. Only the live territory lit. */
.qf-terr{{padding:2px 10px 8px}}
.qf-terr-l{{font-family:'Share Tech Mono',monospace;font-size:9.5px;
 letter-spacing:.15em;color:{INK3};text-transform:uppercase;padding:0 6px 6px}}
.qf-svg{{width:100%;height:auto;display:block}}

/* 7 — DISCLOSURE */
details.qf-more{{margin:0 16px 12px}}
details.qf-more>summary{{font-family:'Share Tech Mono',monospace;font-size:10px;
 letter-spacing:.15em;color:{INK3};cursor:pointer;text-transform:uppercase;
 list-style:none;padding:9px 0;border-top:1px solid rgba(153,69,255,.16)}}
details.qf-more>summary::-webkit-details-marker{{display:none}}
details.qf-more>summary:hover,details.qf-more>summary:focus{{color:{PERC}}}
.qf-row{{font-size:14px;line-height:1.5;color:{INK2};margin:0 0 9px}}
.qf-who{{font-family:'Share Tech Mono',monospace;font-size:10px;
 letter-spacing:.12em;text-transform:uppercase;color:{PERC}}}
.qf-mono{{font-family:'Share Tech Mono',monospace;font-size:11px;
 color:{INK3};letter-spacing:.03em;line-height:1.6}}
.qf-file{{font-family:'Share Tech Mono',monospace;font-size:12px;color:{EARNED}}}
.qf-quiet{{padding:22px 18px;font-size:15.5px;line-height:1.55;color:{INK2}}}
.qf-quiet b{{color:{INK1};font-weight:600}}

@media (min-width:820px){{
  .qf-sov{{grid-template-columns:repeat(6,1fr)}}
  .qf-q{{font-size:29px}}
  .qf-why,.qf-agent-t,.qf-find-b{{font-size:16px}}
  .qf-body{{display:grid;grid-template-columns:1.15fr .85fr;gap:0}}
  .qf-terr{{padding:14px 16px}}
}}
@media (prefers-reduced-motion:reduce){{.qf-st.active .qf-dot{{animation:none}}}}
</style>
"""


def _glyph(sides: int, hue: str, size: int = 15) -> str:
    """Role identity by shape, so the field survives greyscale."""
    r, c = size / 2.4, size / 2
    if sides and sides >= 3:
        pts = " ".join(
            f"{c + r*math.cos(math.radians(-90 + k*360/sides)):.1f},"
            f"{c + r*math.sin(math.radians(-90 + k*360/sides)):.1f}"
            for k in range(sides))
        body = f'<polygon points="{pts}"'
    else:
        body = f'<circle cx="{c:.1f}" cy="{c:.1f}" r="{r:.1f}"'
    return (f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
            f'aria-hidden="true">{body} fill="{hue}" fill-opacity=".22" '
            f'stroke="{hue}" stroke-width="1.2"/></svg>')


def _sovereign(fin: Optional[Dict[str, Any]]) -> str:
    fin = fin or {}
    cells = [("CAPITAL", fin.get("capital_usd"), "usd"),
             ("MODE", fin.get("mode"), "text"),
             ("OPEN", fin.get("open_positions"), "int"),
             ("PnL", fin.get("pnl_pct"), "pct"),
             ("MARK AGE", fin.get("price_age_s"), "sec"),
             ("BLOCKED", fin.get("blockers"), "int")]
    out = ['<div class="qf-sov">']
    for k, v, kind in cells:
        cls, txt = "", "—"
        if v is not None and str(v) != "":
            if kind == "usd":
                txt = f"${float(v):,.0f}"
            elif kind == "pct":
                txt = f"{float(v):+.1f}%"
                cls = "pos" if float(v) > 0 else ("neg" if float(v) < 0 else "")
            elif kind == "sec":
                txt = f"{float(v):.0f}s"
                cls = "neg" if float(v) > 30 else ""
            elif kind == "int":
                txt = str(int(v))
                cls = "neg" if (k == "BLOCKED" and int(v) > 0) else ""
            else:
                txt = str(v).upper()
        out.append(f'<div class="qf-sc"><div class="qf-sk">{_e(k)}</div>'
                   f'<div class="qf-sv {cls}">{_e(txt)}</div></div>')
    return "".join(out) + "</div>"


def _terrain(quest: Optional[Dict[str, Any]]) -> str:
    """Secondary context. Only the quest's territory is lit; the rest is dark."""
    try:
        snap = _org_snapshot()
    except Exception:
        return ""
    facs = snap.get("faculties", [])
    if not facs:
        return ""
    live = (quest or {}).get("key", "")
    W, H = 620, 200
    n = max(1, len(facs))
    parts = [f'<svg viewBox="0 0 {W} {H}" class="qf-svg" role="img" '
             f'aria-label="Territory map. Active region: {_e(live or "none")}">']
    pos = {}
    for i, f in enumerate(facs):
        ang = -math.pi / 2 + i * 2 * math.pi / n
        x, y = W / 2 + (W * 0.34) * math.cos(ang), H / 2 + (H * 0.33) * math.sin(ang)
        pos[f["key"]] = (x, y)
    for f in facs:
        x, y = pos[f["key"]]
        on = f["key"] == live
        hue = EARNED if on else ORG
        op = 1.0 if on else 0.17
        r = 15 if on else 8
        parts.append(f'<circle cx="{x:.0f}" cy="{y:.0f}" r="{r}" fill="{hue}" '
                     f'fill-opacity="{0.20*op:.2f}" stroke="{hue}" '
                     f'stroke-opacity="{op:.2f}" stroke-width="1.2"/>')
        parts.append(
            f'<text x="{x:.0f}" y="{y + r + 13:.0f}" text-anchor="middle" '
            f'font-family="Share Tech Mono,monospace" font-size="8.5" '
            f'letter-spacing="1" fill="{INK2 if on else ASH}" '
            f'fill-opacity="{1.0 if on else .55}">{_e(f["label"])}</text>')
    parts.append("</svg>")
    return ('<div class="qf-terr"><div class="qf-terr-l">Where this is happening'
            '</div>' + "".join(parts) + "</div>")


def _trail(quest: Dict[str, Any]) -> str:
    out = ['<div class="qf-trail">']
    for s in quest.get("trail", []):
        cls = {"REACHED": "reached", "ACTIVE": "active"}.get(s["state"], "")
        out.append(f'<div class="qf-st {cls}"><div class="qf-link"></div>'
                   f'<div class="qf-dot"></div>'
                   f'<div class="qf-st-n">{_e(s["key"].replace("_", " "))}</div></div>')
    return "".join(out) + "</div>"


def _quiet(camp: Dict[str, Any]) -> str:
    last = camp.get("last_expedition")
    chg = camp.get("last_change")
    nxt = camp.get("next_pressure")
    p = ['<div class="qf-quiet">',
         '<b>The camp is quiet.</b> No expedition is under way — '
         'nothing is moving, and nothing is pretending to.']
    if last:
        p.append(f'<br><br>Last thing anyone reported: '
                 f'<span class="qf-who">{_e(last["role"])}</span> — '
                 f'{_e(last["body"])[:220]}')
    if chg:
        p.append(f'<br><br>Last change to the organism: '
                 f'<span class="qf-file">{_e(chg["path"])}</span>')
    if nxt and nxt.get("label"):
        p.append(f'<br><br><b>Next worth investigating:</b> {_e(nxt["why"])}')
    return "".join(p) + "</div>"


def render(financial: Optional[Dict[str, Any]] = None) -> None:
    """Render the quest. Safe when nothing is persisted."""
    if st is None:
        return
    try:
        quest = active_quest()
    except Exception as exc:
        st.markdown(
            f'{CSS}<div class="qf"><div class="qf-quiet">The expedition view is '
            f'unavailable ({_e(type(exc).__name__)}). Trading surfaces are '
            f'unaffected — this is read-only and fails closed.</div></div>',
            unsafe_allow_html=True)
        return

    p = [CSS, '<div class="qf">', _sovereign(financial)]

    if not quest:
        p.append(_quiet(quiet_camp()))
        p.append(_terrain(None) + "</div>")
        st.markdown("".join(p), unsafe_allow_html=True)
        return

    # ── 2. THE QUESTION ─────────────────────────────────────────────────────
    p.append('<div class="qf-head">')
    p.append('<div class="qf-eyebrow">The council is investigating</div>')
    p.append(f'<div class="qf-q">{_e(_human_headline(quest))}</div>')
    p.append(f'<div class="qf-why">{_e(_human_why(quest))}</div>')
    if quest.get("destination_human"):
        p.append(f'<div class="qf-dest">Looking in '
                 f'{_e(quest["destination_human"])}</div>')
    p.append("</div>")

    # ── 3. WHAT'S HAPPENING NOW ─────────────────────────────────────────────
    p.append('<div class="qf-now"><div class="qf-now-l">Right now</div>')
    live = quest.get("live_roles", [])
    if live:
        for a in live[:4]:
            where = HABITAT_LABEL.get(a["habitat"], a["habitat"].lower().replace("_", " "))
            subj = f' — {_e(a["subject"])}' if a.get("subject") else ""
            p.append(
                f'<div class="qf-agent"><div class="qf-agent-g">'
                f'{_glyph(a["sides"], PERC)}</div>'
                f'<div class="qf-agent-t"><span class="qf-agent-r">'
                f'{_e(a["emoji"])} {_e(a["role"].title())}</span> '
                f'{_e(a["verb"])} in {_e(where)}{subj}.</div></div>')
    else:
        p.append('<div class="qf-still">No agent is in the field. The question '
                 'is open and waiting for someone to pick it up.</div>')
    p.append("</div>")

    # ── 4. LATEST FINDING ───────────────────────────────────────────────────
    latest = quest.get("latest")
    if latest:
        refutes = latest["stance"] == "REFUTES"
        p.append(f'<div class="qf-find{" refutes" if refutes else ""}">')
        p.append(f'<div class="qf-find-w">{_e(latest["emoji"])} '
                 f'{_e(latest["role"])} '
                 f'{"found the flaw" if refutes else "reports"}</div>')
        p.append(f'<div class="qf-find-b">{_e(latest["body"])}</div>')
        p.append("</div>")
    elif quest.get("known"):
        p.append('<div class="qf-find"><div class="qf-find-w">What we know</div>'
                 f'<div class="qf-find-b">{_e(quest["known"])}</div></div>')

    # ── 5. THE TRAIL ────────────────────────────────────────────────────────
    p.append(_trail(quest))

    # ── fold ── 6. TERRAIN ──────────────────────────────────────────────────
    p.append(_terrain(quest))

    # ── 7. DISCLOSURE ───────────────────────────────────────────────────────
    contribs = quest.get("contributions", [])
    if contribs or quest.get("branches"):
        p.append('<details class="qf-more"><summary>Field chronicle</summary>')
        for c in contribs:
            p.append(f'<div class="qf-row"><span class="qf-who">'
                     f'{_e(c["emoji"])} {_e(c["role"])} {_e(c["verb"])}</span><br>'
                     f'{_e(c["body"])}</div>')
        for b in quest.get("branches", []):
            verdict = ("this line of enquiry closed here"
                       if b["kind"] == "CLOSED" else "still contested")
            p.append(f'<div class="qf-row" style="color:{DANGER}">'
                     f'<span class="qf-who" style="color:{DANGER}">'
                     f'{_e(b["role"])} — {_e(verdict)}</span><br>'
                     f'{_e(b["body"])}</div>')
        p.append("</details>")

    p.append('<details class="qf-more"><summary>Show their work</summary>')
    p.append(f'<div class="qf-mono">measured from {_e(quest["measured_from"])}<br>'
             f'stage reached &middot; {_e(quest["reached"])}<br>'
             f'success &middot; {_e(quest["success"])}<br>'
             f'stop if &middot; {_e(quest["kill"])}<br>'
             f'territory &middot; {_e(quest["territory"])}</div>')
    files = quest.get("applied_files", [])
    if files:
        p.append('<div class="qf-mono" style="margin-top:9px">'
                 'the organism changed here:</div>')
        for f in files:
            p.append(f'<div class="qf-file">{_e(f["path"])} '
                     f'<span class="qf-mono">{_e(f["result"])}</span></div>')
    for c in contribs:
        if c.get("evidence"):
            p.append(f'<div class="qf-mono">{_e(c["provenance"])} &middot; '
                     f'{_e(", ".join(str(x) for x in c["evidence"][:4]))}</div>')
    p.append("</details></div>")

    st.markdown("".join(p), unsafe_allow_html=True)
