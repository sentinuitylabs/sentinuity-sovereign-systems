# coding: utf-8
"""
ui/lumen_field.py — LUMEN_FIELD_20260813

The Living Field: Council roles rendered as Lumen Nodes inhabiting the organism.

WHAT THIS RENDERS AND WHAT IT REFUSES TO
========================================
Every glyph on screen is produced from a row returned by
services.lumen_field_state. There is no demo data, no ambient animation, and no
"idle drift" motion. When Council is idle the field is still and says so.

PROVENANCE MODE makes that auditable rather than merely asserted: toggle it and
every node, note and specimen prints the table and row id it came from. A shimmer
that cannot name its row is a bug you can now see.

TWO VISUAL CHANNELS FOR TWO VARIABLES
=====================================
The brief proposed a single colour doctrine carrying both lifecycle stage and
evidence strength. Those are orthogonal — a specimen can reach Forge on thin
evidence, or sit unexamined on strong evidence — so one channel cannot encode
both without lying. Split here:

    HUE        = lifecycle stage      (where a thing is in its journey)
    LUMINANCE  = evidence strength    (how much we should trust it)
    FACETS     = independent support  (how many separate voices agree)

So a pale gold crystal reads instantly as "approved, but on thin evidence" —
a state the single-channel version could not express at all, and precisely the
state most worth catching.

MASS IS INDEPENDENT SUPPORT, NOT SOURCE COUNT
=============================================
Facets are drawn from `independent_support`, never `evidence_count`. Two forks
of one upstream are one voice. The interface therefore cannot flatter weak
corroboration, which is the same discipline the organism needs for wallet
independence — one model, used twice.

ACCESSIBILITY
=============
Role identity is carried by SHAPE (sides count), not colour alone: triangle
Nugget, square Mechanist, pentagon Rhiza, hexagon Ivaris, circle Substrate,
diamond Forge, star Polaris. Every state also has a text label. Motion is
disabled under prefers-reduced-motion. The field is legible in greyscale.
"""
from __future__ import annotations

import html
import time
from typing import Any, Dict, List, Optional

try:
    import streamlit as st
except Exception:                                    # pragma: no cover
    st = None  # type: ignore

try:
    from services.lumen_field_state import (
        ROLES, HABITATS, JOURNEY, active_nodes, camp_roster, field_notes,
        field_summary, specimens, camp_story, fieldcraft_scores, expedition_state,
    )
except Exception:                                    # pragma: no cover
    ROLES, HABITATS, JOURNEY = {}, (), ()
    active_nodes = camp_roster = field_notes = specimens = lambda *a, **k: []
    field_summary = lambda: {"has_any_state": False}
    camp_story = lambda *a, **k: []
    fieldcraft_scores = lambda *a, **k: []
    expedition_state = lambda *a, **k: {}

# ── palette: existing Sentinuity doctrine, unchanged ────────────────────────
C_VOID   = "#050210"
C_PURPLE = "#9945FF"
C_GREEN  = "#14F195"
C_CYAN   = "#8EF9FF"
C_AMBER  = "#FFB347"
C_GOLD   = "#FFD700"
C_RED    = "#FF073A"
C_ASH    = "#6B6B7B"
C_PANEL  = "rgba(12,4,30,0.82)"
C_BORDER = "rgba(153,69,255,0.28)"
C_DIM    = "rgba(180,160,255,0.48)"

# HUE = stage. Ash is genuinely "not yet looked at", distinct from amber
# "looked at and contested" — the brief let those blur together.
STAGE_HUE = {
    "TRAILHEAD": C_ASH, "FOREST": C_ASH, "SPECIMEN": C_CYAN,
    "CAMP": C_CYAN, "COMPARISON": C_PURPLE, "CHALLENGE": C_AMBER,
    "PROVING_GROUND": C_AMBER, "FORGE": C_PURPLE, "POLARIS": C_GOLD,
    "REALITY": C_GREEN, "RETURNED_TO_MEMORY": C_ASH,
    "SECOND_EXPEDITION": C_CYAN,
}
ACTIVITY_HUE = {
    "TRAVELLING": C_ASH, "INSPECTING": C_CYAN, "COMPARING": C_PURPLE,
    "CHALLENGING": C_AMBER, "TESTING": C_AMBER, "BUILDING": C_PURPLE,
    "JUDGING": C_GOLD, "AT_CAMP": C_DIM,
}
HANDLING_GLYPH = {"CLEAR_TRAIL": "🌿", "HANDLE_WITH_CARE": "🍄",
                  "TOXIC": "☠️", "UNASSESSED": "◌"}
VALUE_GLYPH = {"RARE": "✨", "NOTABLE": "◈", "ORDINARY": "·", "UNASSESSED": "◌"}

HABITAT_LABEL = {
    "COUNCIL": "COUNCIL · EVOLVES", "GITHUB_EXPEDITION": "EXPEDITION · EXPLORES",
    "SOLANA": "SOLANA · ACTS", "COPYTRADE": "COPYTRADE · OBSERVES ACTORS",
    "PRICE_TRUTH": "PRICE TRUTH · PERCEIVES", "SUBSTRATE": "SUBSTRATE · EXPERIMENTS",
    "INTELLIGENCE": "INTELLIGENCE · UNDERSTANDS", "FORGE": "FORGE · GIVES FORM",
    "MEMORY": "MEMORY · REMEMBERS",
}


def _esc(v: Any) -> str:
    return html.escape(str(v if v is not None else ""))


def _polygon_points(sides: int, r: float, cx: float, cy: float,
                    rot: float = -90.0) -> str:
    import math
    if sides < 3:
        return ""
    pts = []
    for i in range(sides):
        a = math.radians(rot + i * 360.0 / sides)
        pts.append(f"{cx + r*math.cos(a):.2f},{cy + r*math.sin(a):.2f}")
    return " ".join(pts)


def _star_points(points: int, r_out: float, r_in: float,
                 cx: float, cy: float) -> str:
    import math
    pts = []
    for i in range(points * 2):
        r = r_out if i % 2 == 0 else r_in
        a = math.radians(-90 + i * 180.0 / points)
        pts.append(f"{cx + r*math.cos(a):.2f},{cy + r*math.sin(a):.2f}")
    return " ".join(pts)


# ── the Lumen Node glyph ────────────────────────────────────────────────────
def lumen_glyph_svg(role: str, activity: str, *, size: int = 46,
                    stale: bool = False, rings: int = 2,
                    uid_suffix: str = "") -> str:
    """A multi-layer mandala: concentric geometry, counter-rotating lattice,
    role sigil. Shape encodes role so the field survives greyscale."""
    meta = ROLES.get(role, {"sides": 6})
    sides = int(meta.get("sides", 6))
    hue = ACTIVITY_HUE.get(activity, C_DIM)
    if stale:
        hue = C_ASH
    cx = cy = size / 2.0
    uid = f"{role}{activity}{size}{uid_suffix}".replace(" ", "").replace(":", "")

    inner = (f'<circle cx="{cx}" cy="{cy}" r="{size*0.17:.1f}" fill="none" '
             f'stroke="{hue}" stroke-width="1.4" opacity=".95"/>'
             if sides == 0 else
             f'<polygon points="{_polygon_points(sides, size*0.19, cx, cy)}" '
             f'fill="none" stroke="{hue}" stroke-width="1.4" opacity=".95"/>')
    if role == "POLARIS":
        inner = (f'<polygon points="{_star_points(8, size*0.21, size*0.085, cx, cy)}" '
                 f'fill="none" stroke="{hue}" stroke-width="1.2" opacity=".95"/>')

    ring_svg = ""
    for i in range(max(1, rings)):
        r = size * (0.28 + 0.07 * i)
        dash = 3 + i * 2
        dur = 14 + i * 7
        dirn = "normal" if i % 2 == 0 else "reverse"
        ring_svg += (
            f'<circle class="lf-ring" cx="{cx}" cy="{cy}" r="{r:.1f}" fill="none" '
            f'stroke="{hue}" stroke-width="{1.0 - i*0.15:.2f}" '
            f'stroke-dasharray="{dash} {dash+3}" opacity="{0.5 - i*0.11:.2f}" '
            f'style="animation-duration:{dur}s;animation-direction:{dirn};'
            f'transform-origin:{cx}px {cy}px;"/>')

    return (
        f'<svg class="lf-glyph{" lf-stale" if stale else ""}" width="{size}" '
        f'height="{size}" viewBox="0 0 {size} {size}" role="img" '
        f'aria-label="{_esc(role)} {_esc(activity)}">'
        f'<defs><radialGradient id="g{uid}">'
        f'<stop offset="0%" stop-color="{hue}" stop-opacity=".34"/>'
        f'<stop offset="70%" stop-color="{hue}" stop-opacity=".05"/>'
        f'<stop offset="100%" stop-color="{hue}" stop-opacity="0"/>'
        f'</radialGradient></defs>'
        f'<circle cx="{cx}" cy="{cy}" r="{size*0.48:.1f}" fill="url(#g{uid})"/>'
        f'{ring_svg}{inner}</svg>')


# ── the Specimen crystal: mist → mote → shard → crystal → artifact ─────────
def specimen_crystal_svg(spec: Dict[str, Any], size: int = 58) -> str:
    """Facets = independent support. Luminance = evidence strength.
    Hue = journey stage. Three channels, three variables."""
    stage = spec.get("journey_stage", "SPECIMEN")
    hue = STAGE_HUE.get(stage, C_CYAN)
    indep = int(spec.get("independent_support", 0))
    contradicted = bool(spec.get("contradicted"))

    # facets grow only with INDEPENDENT voices, never raw source count
    facets = max(3, min(9, 2 + indep))
    # luminance carries evidence strength, independently of stage
    strength = min(1.0, indep / 4.0)
    fill_op = 0.06 + 0.20 * strength
    stroke_op = 0.30 + 0.62 * strength

    cx = cy = size / 2.0
    if contradicted:
        hue, fill_op, stroke_op = C_ASH, 0.03, 0.26

    outer = _polygon_points(facets, size * 0.34, cx, cy)
    innerp = _polygon_points(facets, size * 0.19, cx, cy, rot=-90 + 180.0 / facets)

    sovereign = ""
    if stage == "REALITY" and not contradicted and indep >= 3:
        # Sovereign Gold: earned only with post-implementation runtime evidence
        # AND independent corroboration. Deliberately hard to reach.
        sovereign = (f'<polygon points="{outer}" fill="none" stroke="#FFFFFF" '
                     f'stroke-width=".7" opacity=".85"/>')

    crack = ""
    if contradicted:
        crack = (f'<line x1="{cx-size*0.2:.1f}" y1="{cy-size*0.1:.1f}" '
                 f'x2="{cx+size*0.16:.1f}" y2="{cy+size*0.22:.1f}" '
                 f'stroke="{C_RED}" stroke-width=".9" opacity=".55"/>')

    return (
        f'<svg width="{size}" height="{size}" viewBox="0 0 {size} {size}" '
        f'role="img" aria-label="specimen, {_esc(stage)}, '
        f'{indep} independent sources">'
        f'<polygon points="{outer}" fill="{hue}" fill-opacity="{fill_op:.3f}" '
        f'stroke="{hue}" stroke-opacity="{stroke_op:.2f}" stroke-width="1.1"/>'
        f'<polygon points="{innerp}" fill="none" stroke="{hue}" '
        f'stroke-opacity="{stroke_op*0.65:.2f}" stroke-width=".7"/>'
        f'{sovereign}{crack}</svg>')


CSS = f"""
<style>
.lf-wrap {{ background:{C_VOID}; border:1px solid {C_BORDER}; border-radius:10px;
  padding:16px 18px; font-family:Rajdhani,sans-serif; color:#E8E2FF; }}
.lf-h {{ font-family:Orbitron,sans-serif; font-size:.78rem; letter-spacing:.16em;
  color:{C_CYAN}; text-transform:uppercase; margin:0 0 2px; }}
.lf-sub {{ font-family:Share Tech Mono,monospace; font-size:.62rem;
  color:{C_DIM}; letter-spacing:.05em; margin-bottom:14px; }}
.lf-grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(184px,1fr));
  gap:10px; }}
.lf-hab {{ background:{C_PANEL}; border:1px solid {C_BORDER}; border-radius:8px;
  padding:10px 11px; min-height:104px; position:relative; }}
.lf-hab-name {{ font-family:Share Tech Mono,monospace; font-size:.58rem;
  color:{C_DIM}; letter-spacing:.09em; margin-bottom:8px; }}
.lf-nodes {{ display:flex; flex-wrap:wrap; gap:2px; align-items:center; }}
.lf-node {{ display:flex; flex-direction:column; align-items:center; width:56px; }}
.lf-node-lbl {{ font-family:Share Tech Mono,monospace; font-size:.5rem;
  color:{C_DIM}; margin-top:-4px; text-align:center; line-height:1.15; }}
.lf-empty {{ font-family:Share Tech Mono,monospace; font-size:.56rem;
  color:rgba(180,160,255,.30); font-style:italic; }}
.lf-const {{ position:absolute; top:8px; right:10px;
  font-family:Share Tech Mono,monospace; font-size:.52rem; color:{C_AMBER};
  border:1px solid rgba(255,179,71,.35); border-radius:3px; padding:1px 5px; }}
.lf-ring {{ animation-name:lf-spin; animation-timing-function:linear;
  animation-iteration-count:infinite; }}
@keyframes lf-spin {{ to {{ transform:rotate(360deg); }} }}
.lf-stale {{ opacity:.42; }}
.lf-ribbon {{ display:flex; align-items:center; gap:0; margin:6px 0 2px;
  flex-wrap:wrap; }}
.lf-stage {{ font-family:Share Tech Mono,monospace; font-size:.52rem;
  letter-spacing:.04em; padding:2px 7px; border-radius:2px; white-space:nowrap; }}
.lf-stage-done {{ color:{C_CYAN}; background:rgba(142,249,255,.09); }}
.lf-stage-now {{ color:{C_VOID}; background:{C_GOLD}; font-weight:700; }}
.lf-stage-todo {{ color:rgba(180,160,255,.26); }}
.lf-sep {{ color:rgba(180,160,255,.22); font-size:.5rem; padding:0 1px; }}
.lf-note {{ background:{C_PANEL}; border-left:2px solid {C_PURPLE};
  border-radius:0 6px 6px 0; padding:9px 12px; margin-bottom:7px; }}
.lf-note-hd {{ font-family:Share Tech Mono,monospace; font-size:.56rem;
  color:{C_CYAN}; letter-spacing:.07em; margin-bottom:3px; }}
.lf-note-body {{ font-size:.82rem; color:#E8E2FF; line-height:1.42; }}
.lf-note-next {{ font-family:Share Tech Mono,monospace; font-size:.55rem;
  color:{C_AMBER}; margin-top:5px; }}
.lf-spec {{ display:flex; gap:12px; background:{C_PANEL}; border:1px solid {C_BORDER};
  border-radius:8px; padding:11px 13px; margin-bottom:8px; }}
.lf-prop {{ font-size:.88rem; color:#F0EBFF; line-height:1.4; margin-bottom:6px; }}
.lf-diff {{ display:grid; grid-template-columns:1fr 1fr; gap:8px; margin-top:8px; }}
.lf-diff-cell {{ border:1px solid rgba(153,69,255,.18); border-radius:5px;
  padding:6px 8px; }}
.lf-diff-lbl {{ font-family:Share Tech Mono,monospace; font-size:.5rem;
  color:{C_DIM}; letter-spacing:.1em; margin-bottom:2px; }}
.lf-diff-txt {{ font-size:.76rem; color:#DCD4FF; line-height:1.35; }}
.lf-the-diff {{ border:1px solid rgba(255,215,0,.32); border-radius:5px;
  padding:7px 9px; margin-top:8px; background:rgba(255,215,0,.04); }}
.lf-axes {{ font-family:Share Tech Mono,monospace; font-size:.54rem;
  color:{C_DIM}; margin-top:6px; }}
.lf-prov {{ font-family:Share Tech Mono,monospace; font-size:.5rem;
  color:rgba(142,249,255,.5); border-top:1px dotted rgba(142,249,255,.2);
  margin-top:6px; padding-top:3px; }}
.lf-story {{ display:grid; gap:7px; }}
.lf-story-turn {{ position:relative; padding:9px 12px 9px 14px; border-radius:7px;
  background:linear-gradient(90deg,rgba(153,69,255,.08),rgba(5,2,16,.15));
  border:1px solid rgba(153,69,255,.18); overflow:hidden; }}
.lf-story-turn:before {{ content:""; position:absolute; inset:0 auto 0 0; width:2px;
  background:var(--lf-role,#8EF9FF); box-shadow:0 0 14px var(--lf-role,#8EF9FF); }}
.lf-story-hd {{ font-family:Share Tech Mono,monospace; font-size:.56rem;
  color:var(--lf-role,#8EF9FF); letter-spacing:.08em; margin-bottom:3px; }}
.lf-story-body {{ font-family:Rajdhani,sans-serif; font-size:.84rem; color:#EEE9FF;
  line-height:1.42; }}
.lf-whisper {{ color:rgba(180,160,255,.42); font-family:Share Tech Mono,monospace;
  font-size:.5rem; margin-top:4px; }}
.lf-meta-row {{ display:flex; flex-wrap:wrap; gap:5px; margin:0 0 12px; }}
.lf-meta-chip {{ font-family:Share Tech Mono,monospace; font-size:.51rem; letter-spacing:.05em;
  padding:3px 7px; border-radius:999px; border:1px solid rgba(142,249,255,.2);
  background:rgba(142,249,255,.035); color:{C_DIM}; }}
.lf-meta-chip strong {{ color:{C_CYAN}; font-weight:600; }}
.lf-craft {{ display:flex; flex-wrap:wrap; gap:5px; margin-top:8px; }}
.lf-craft-chip {{ font-family:Share Tech Mono,monospace; font-size:.5rem; padding:3px 7px;
  border:1px solid rgba(255,215,0,.2); border-radius:4px; color:{C_GOLD};
  background:rgba(255,215,0,.035); }}
@media (prefers-reduced-motion: reduce) {{ .lf-ring {{ animation:none !important; }} }}
</style>
"""


def _journey_ribbon(current: str) -> str:
    stages = list(JOURNEY) or []
    try:
        idx = stages.index(current)
    except ValueError:
        idx = -1
    out = ['<div class="lf-ribbon">']
    for i, s in enumerate(stages):
        cls = ("lf-stage-done" if i < idx else
               "lf-stage-now" if i == idx else "lf-stage-todo")
        out.append(f'<span class="lf-stage {cls}">{_esc(s.replace("_"," "))}</span>')
        if i < len(stages) - 1:
            out.append('<span class="lf-sep">›</span>')
    out.append("</div>")
    return "".join(out)


def render_living_field(*, show_provenance: bool = False,
                        max_specimens: int = 6) -> None:
    """Render the Living Field. Safe to call when every table is empty."""
    if st is None:
        return
    summary = field_summary()
    nodes = active_nodes()
    roster = camp_roster()
    notes = field_notes()
    specs = specimens(limit=max_specimens)
    story = camp_story(limit=9)
    craft = fieldcraft_scores(limit=7)
    expedition = expedition_state()

    st.markdown(CSS, unsafe_allow_html=True)
    parts = ['<div class="lf-wrap">']
    parts.append('<div class="lf-h">The Living Field</div>')
    if expedition:
        _em = _esc(str(expedition.get("current_mode") or "").replace("_"," "))
        _es = _esc(str(expedition.get("status") or "").replace("_"," "))
        _ep = _esc(str(expedition.get("current_project") or "").replace("_"," "))
        _ec = _esc(expedition.get("total_cycles") or 0)
        parts.append('<div class="lf-meta-row">'
                     f'<span class="lf-meta-chip"><strong>{_em or "EXPEDITION"}</strong></span>'
                     + (f'<span class="lf-meta-chip">{_es}</span>' if _es else '')
                     + (f'<span class="lf-meta-chip">QUEST · {_ep}</span>' if _ep else '')
                     + f'<span class="lf-meta-chip">CYCLE · {_ec}</span></div>')

    if not summary.get("has_any_state"):
        # Emptiness is direction, not mood: say what would fill it.
        parts.append(
            '<div class="lf-sub">Council is at camp. No assignments are open, '
            'so nothing is moving.</div>'
            '<div class="lf-empty">Nodes appear here when a Council role opens '
            'an assignment. Specimens appear when an expedition brings a '
            'proposition home. Nothing is drawn from placeholder data.</div>'
            '</div>')
        st.markdown("".join(parts), unsafe_allow_html=True)
        return

    parts.append(
        f'<div class="lf-sub">{summary["nodes_active"]} node(s) in the field · '
        f'{summary["open_notes"]} open field note(s) · '
        f'{summary["specimens"]} specimen(s)'
        + (f' · {summary["nodes_stale"]} stale' if summary["nodes_stale"] else "")
        + '</div>')
    if craft:
        parts.append('<div class="lf-craft">')
        for _fc in craft:
            _fr = _esc(_fc.get("role") or _fc.get("agent_name") or "MODEL")
            try: _fs = f"{float(_fc.get('cumulative_score') or 0):.0f}"
            except Exception: _fs = "0"
            parts.append(f'<span class="lf-craft-chip">FIELDCRAFT · {_fr} {_fs}</span>')
        parts.append('</div>')

    # ── habitats ────────────────────────────────────────────────────────────
    by_hab: Dict[str, List[Dict[str, Any]]] = {}
    for n in nodes:
        by_hab.setdefault(n["habitat"], []).append(n)

    parts.append('<div class="lf-grid">')
    for hab in HABITATS:
        here = by_hab.get(hab, [])
        parts.append('<div class="lf-hab">')
        parts.append(f'<div class="lf-hab-name">{_esc(HABITAT_LABEL.get(hab, hab))}</div>')
        if len(here) >= 2:
            # Constellation is emergent — it is what 2+ nodes on one habitat
            # LOOKS like, not a state anyone assigns.
            parts.append(f'<div class="lf-const">{len(here)} minds</div>')
        if not here:
            parts.append('<div class="lf-empty">quiet</div>')
        else:
            parts.append('<div class="lf-nodes">')
            for n in here:
                meta = ROLES.get(n["role"], {})
                rings = 3 if n["activity"] in ("INSPECTING", "COMPARING",
                                               "CHALLENGING") else 2
                parts.append('<div class="lf-node">')
                parts.append(lumen_glyph_svg(n["role"], n["activity"],
                                             stale=n["stale"], rings=rings, uid_suffix=str(n.get("id",""))))
                act = "STALE" if n["stale"] else n["activity"].replace("_", " ")
                parts.append(
                    f'<div class="lf-node-lbl">{_esc(meta.get("emoji",""))} '
                    f'{_esc(meta.get("label", n["role"]))}<br>{_esc(act)}</div>')
                parts.append("</div>")
            parts.append("</div>")
            if show_provenance:
                parts.append('<div class="lf-prov">'
                             + " · ".join(_esc(n["provenance"]) for n in here)
                             + "</div>")
        parts.append("</div>")
    parts.append("</div>")
    parts.append("</div>")
    st.markdown("".join(parts), unsafe_allow_html=True)

    # ── campfire chronicle: human-readable summaries of observable work ─────
    if story:
        _role_col = {"NUGGET": C_CYAN, "MECHANIST": C_PURPLE, "RHIZA": C_GREEN,
                     "IVARIS": C_AMBER, "SUBSTRATE": C_AMBER, "FORGE": C_PURPLE,
                     "POLARIS": C_GOLD}
        _story_parts = [
            '<div class="lf-wrap" style="margin-top:10px;">',
            '<div class="lf-h">Council Camp · Field Chronicle</div>',
            '<div class="lf-sub">observable actions translated into ordinary language — not hidden reasoning</div>',
            '<div class="lf-story">'
        ]
        for turn in reversed(story):
            _rr = str(turn.get("role") or "").upper()
            _rm = ROLES.get(_rr, {})
            _rc = _role_col.get(_rr, C_CYAN)
            _story_parts.append(
                f'<div class="lf-story-turn" style="--lf-role:{_rc};">'
                f'<div class="lf-story-hd">{_esc(_rm.get("emoji","◌"))} {_esc(_rm.get("label",_rr).upper())} · {_esc(turn.get("title","FIELD NOTE"))}</div>'
                f'<div class="lf-story-body">{_esc(turn.get("text",""))}</div>'
                + (f'<div class="lf-whisper">SHOW THEIR WORK · {_esc(turn.get("provenance",""))}</div>' if show_provenance else '')
                + '</div>'
            )
        _story_parts.append('</div></div>')
        st.markdown("".join(_story_parts), unsafe_allow_html=True)

    # ── camp roster ─────────────────────────────────────────────────────────
    st.markdown(
        '<div class="lf-wrap" style="margin-top:10px;">'
        '<div class="lf-h">Council Camp</div>'
        '<div class="lf-sub">where discoveries return home</div>'
        + "".join(
            f'<div class="lf-note-hd">{_esc(r["emoji"])} {_esc(r["label"].upper())} — '
            f'<span style="color:{C_AMBER if r["in_field"] else C_DIM}">'
            f'{"IN THE FIELD · " + _esc(HABITAT_LABEL.get(r["habitat"], r["habitat"]).split(" ·")[0]) if r["in_field"] else "AT CAMP"}'
            f'</span>'
            + (f' <span style="color:{C_DIM}">— {_esc(r["subject"])}</span>' if r["subject"] else "")
            + (f' <span class="lf-prov" style="display:inline;border:0;">{_esc(r["provenance"])}</span>' if show_provenance else "")
            + "</div>"
            for r in roster)
        + "</div>", unsafe_allow_html=True)

    # ── field notes ─────────────────────────────────────────────────────────
    if notes:
        st.markdown('<div class="lf-wrap" style="margin-top:10px;">'
                    '<div class="lf-h">Field Notes</div>'
                    '<div class="lf-sub">left by a role, claimable by another</div>'
                    + "".join(
                        '<div class="lf-note">'
                        f'<div class="lf-note-hd">{_esc(ROLES.get(n["author_role"],{}).get("emoji",""))} '
                        f'{_esc(n["author_role"])} NOTE · {_esc(n["habitat"].replace("_"," "))}'
                        + (f' · claimed by {_esc(n["claimed_by_role"])}' if n["claimed_by_role"] else "")
                        + "</div>"
                        f'<div class="lf-note-body">{_esc(n["body"])}</div>'
                        + (f'<div class="lf-note-next">NEXT · {_esc(n["next_action"])}</div>'
                           if n["next_action"] else "")
                        + (f'<div class="lf-note-next" style="color:{C_DIM}">EVIDENCE · '
                           f'{len(n["evidence"])} reference(s)</div>' if n["evidence"] else "")
                        + (f'<div class="lf-prov">{_esc(n["provenance"])}</div>'
                           if show_provenance else "")
                        + "</div>"
                        for n in notes[:8])
                    + "</div>", unsafe_allow_html=True)

    # ── specimens ───────────────────────────────────────────────────────────
    if specs:
        block = ['<div class="lf-wrap" style="margin-top:10px;">',
                 '<div class="lf-h">Specimens</div>',
                 '<div class="lf-sub">a specimen is a transferable proposition, '
                 'not a repository — facets show independent support only</div>']
        for s in specs:
            block.append('<div class="lf-spec">')
            block.append(f'<div>{specimen_crystal_svg(s)}</div>')
            block.append('<div style="flex:1;">')
            block.append(f'<div class="lf-prop">{_esc(s["proposition"])}</div>')
            block.append(_journey_ribbon(s["journey_stage"]))
            if s["they_do"] or s["we_do"]:
                block.append('<div class="lf-diff">')
                block.append('<div class="lf-diff-cell"><div class="lf-diff-lbl">THEY DO</div>'
                             f'<div class="lf-diff-txt">{_esc(s["they_do"]) or "—"}</div></div>')
                block.append('<div class="lf-diff-cell"><div class="lf-diff-lbl">WE DO</div>'
                             f'<div class="lf-diff-txt">{_esc(s["we_do"]) or "—"}</div></div>')
                block.append("</div>")
            if s["the_difference"]:
                block.append('<div class="lf-the-diff">'
                             '<div class="lf-diff-lbl" style="color:#FFD700">THE DIFFERENCE</div>'
                             f'<div class="lf-diff-txt">{_esc(s["the_difference"])}</div></div>')
            if s["how_we_kill_it"]:
                block.append('<div class="lf-note-next">HOW WE KILL IT · '
                             f'{_esc(s["how_we_kill_it"])}</div>')
            # Two axes, never collapsed into one badge.
            block.append(
                '<div class="lf-axes">'
                f'{HANDLING_GLYPH.get(s["handling"],"◌")} {_esc(s["handling"].replace("_"," "))}'
                f' &nbsp;·&nbsp; {VALUE_GLYPH.get(s["value_axis"],"◌")} {_esc(s["value_axis"])}'
                f' &nbsp;·&nbsp; {s["independent_support"]} independent of '
                f'{s["evidence_count"]} source(s)'
                + (f' &nbsp;·&nbsp; BELONGS TO {_esc(s["belongs_to"])}' if s["belongs_to"] else "")
                + "</div>")
            if s["evidence_count"] > s["independent_support"]:
                block.append(
                    f'<div class="lf-note-next" style="color:{C_AMBER}">'
                    f'{s["evidence_count"] - s["independent_support"]} source(s) share '
                    'funding/fork ancestry and are counted once</div>')
            if s["contradicted"]:
                block.append(f'<div class="lf-note-next" style="color:{C_RED}">'
                             'FORMER DOCTRINE — retired after contradiction'
                             + (f': {_esc(s["contradiction_reason"])}' if s["contradiction_reason"] else "")
                             + "</div>")
            if show_provenance:
                block.append(f'<div class="lf-prov">{_esc(s["provenance"])}</div>')
            block.append("</div></div>")
        block.append("</div>")
        st.markdown("".join(block), unsafe_allow_html=True)


def render(**kw) -> None:
    """Entry point for the hub."""
    try:
        render_living_field(**kw)
    except Exception as exc:                          # never break the hub
        if st is not None:
            st.markdown(
                f'<div class="lf-wrap"><div class="lf-h">The Living Field</div>'
                f'<div class="lf-empty">Field unavailable: {_esc(type(exc).__name__)}. '
                f'Trading surfaces are unaffected — this panel is Tier-3 cognition '
                f'and fails closed.</div></div>', unsafe_allow_html=True)