"""
ui/sentinuity_home.py
=====================
SENTINUITY_HOME_HIERARCHY_20260817

The Home surface. ONE canonical payload in, seven layers out, each with exactly
one job:

    SOVEREIGN HUD      is the organism solvent and safe right now?
    LIVING WORLD       what is happening (visual)
    CURRENT EXPEDITION what single mission is underway (human-readable)
    DEBATE CHAMBER     what is the decisive disagreement
    FORGE / POLARIS    implementation, testing and safety state
    OUTCOME            absorbed or rejected
    TRADE TRUTH        did executable results actually improve
    EVIDENCE           provenance, forensics, raw events (collapsed)
    CHRONICLE          history (collapsed)

No layer re-narrates another layer's prose. Everything below TRADE TRUTH is
collapsed by default. Nothing is deleted â€” progressive disclosure only.
"""
from __future__ import annotations

import html as _html
from pathlib import Path
from typing import Any, Optional

_HERE = Path(__file__).resolve().parent
ROOT = _HERE.parent if _HERE.name.lower() == "ui" else _HERE
DB_PATH = ROOT / "sentinuity_matrix.db"

PASS, FAIL, NOT_RUN = "PASS", "FAIL", "NOT_RUN"

_TRI_STYLE = {
    PASS:    ("#42f5a7", "PASS"),
    FAIL:    ("#ff5577", "FAIL"),
    NOT_RUN: ("#8fa3c4", "NOT RUN"),
}
_STAGE_STYLE = {
    "DONE":    ("#42f5a7", "â—"),
    "ACTIVE":  ("#52f4ff", "â—‰"),
    "FAILED":  ("#ff5577", "âœ•"),
    "PENDING": ("#54617a", "â—‹"),
}

CSS = """
<style>
.sn-wrap{font-family:ui-monospace,"SF Mono",Menlo,Consolas,monospace;color:#e8f4ff}
.sn-hud{display:flex;flex-wrap:wrap;gap:8px;margin:2px 0 10px}
.sn-tile{flex:1 1 128px;min-width:118px;padding:9px 11px;border-radius:9px;
  border:1px solid rgba(96,132,196,.28);background:rgba(8,11,26,.62)}
.sn-tile .k{font-size:8px;letter-spacing:.16em;color:#8fa3c4}
.sn-tile .v{font-size:19px;font-weight:600;margin-top:3px;line-height:1.1}
.sn-tile .s{font-size:9px;color:#7f92ae;margin-top:2px}
.sn-good{color:#42f5a7}.sn-warn{color:#ffd166}.sn-bad{color:#ff5577}
.sn-dim{color:#8fa3c4}.sn-cy{color:#52f4ff}
.sn-h{font-size:9px;letter-spacing:.22em;color:#ad63ff;margin:16px 0 7px;
  border-bottom:1px solid rgba(140,110,220,.22);padding-bottom:5px}
.sn-card{border:1px solid rgba(96,132,196,.24);border-radius:10px;padding:11px 13px;
  background:rgba(7,10,24,.55);margin-bottom:8px}
.sn-rail{display:flex;gap:3px;flex-wrap:wrap;margin:9px 0 4px}
.sn-step{flex:1 1 78px;min-width:70px;padding:6px 5px;border-radius:6px;
  border:1px solid rgba(96,132,196,.2);background:rgba(5,8,20,.6);text-align:center}
.sn-step .n{font-size:7.5px;letter-spacing:.11em}
.sn-step .d{font-size:7px;color:#7f92ae;margin-top:3px;line-height:1.35;
  word-break:break-word}
.sn-pos{display:flex;justify-content:space-between;align-items:baseline;gap:8px;
  padding:6px 0;border-bottom:1px solid rgba(96,132,196,.14)}
.sn-pos:last-child{border-bottom:0}
.sn-agent{font-size:11px;font-weight:600;letter-spacing:.06em}
.sn-stance{font-size:8px;letter-spacing:.12em;padding:1px 6px;border-radius:3px;
  border:1px solid currentColor}
.sn-sup{color:#42f5a7}.sn-chal{color:#ff8a3d}.sn-obs{color:#8fa3c4}
.sn-sum{font-size:9.5px;color:#b8cbe4;flex:1;min-width:110px}
.sn-obscount{font-size:8px;color:#6f829d;white-space:nowrap}
.sn-tri{display:inline-block;font-size:8px;letter-spacing:.1em;padding:1px 6px;
  border-radius:3px;border:1px solid currentColor;margin-right:5px}
.sn-note{font-size:9px;color:#8fa3c4;margin-top:6px;line-height:1.5}
.sn-blocked{border-color:rgba(255,85,119,.5);background:rgba(40,8,18,.42)}
.sn-quiet{color:#7f92ae;font-size:10px;padding:10px 2px;text-align:center}
@media(max-width:640px){
  .sn-tile{flex:1 1 44%;min-width:0}
  .sn-tile .v{font-size:16px}
  .sn-step{flex:1 1 30%}
}
</style>
"""


def _e(v: Any) -> str:
    return _html.escape(str(v if v is not None else ""))


def _money(v) -> str:
    if v is None:
        return "â€”"
    v = float(v)
    return ("-$" if v < 0 else "$") + f"{abs(v):,.2f}"


def _tri_chip(value: str) -> str:
    col, label = _TRI_STYLE.get(value, _TRI_STYLE[NOT_RUN])
    return f'<span class="sn-tri" style="color:{col}">{label}</span>'


def _age(sec) -> str:
    if sec is None:
        return "â€”"
    sec = float(sec)
    if sec < 90:
        return f"{sec:.0f}s ago"
    if sec < 5400:
        return f"{sec/60:.0f}m ago"
    if sec < 172800:
        return f"{sec/3600:.1f}h ago"
    return f"{sec/86400:.1f}d ago"


# â”€â”€ layers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def render_hud(st, s: dict) -> None:
    h = s.get("hud", {})
    t = s.get("trade_truth", {})
    wr = h.get("win_rate")
    wr_cls = "sn-dim" if wr is None else ("sn-bad" if wr < 20 else
                                          "sn-warn" if wr < 45 else "sn-good")
    risk = h.get("risk", "UNKNOWN")
    risk_cls = "sn-good" if risk == "NOMINAL" else "sn-warn"
    alive, total = h.get("services_alive", 0), h.get("services_total", 0)
    svc_cls = "sn-good" if total and alive == total else (
        "sn-warn" if alive else "sn-bad")
    restarting = h.get("services_restarting", 0)

    tiles = [
        ("MODE", _e(h.get("mode", "â€”")), "sn-cy",
         f"{t.get('source') or 'no position source'}"),
        ("EQUITY", _money(h.get("equity")), "",
         f"realized {_money(h.get('realized'))}"),
        ("WIN RATE", "â€”" if wr is None else f"{wr}%", wr_cls,
         f"{h.get('closed_sample', 0)} closed Â· {_e(h.get('verdict', ''))}"),
        ("OPEN", str(h.get("open_positions", 0)), "",
         f"price {_e(h.get('oracle_state', 'â€”')).lower()}"),
        ("SERVICES", f"{alive}/{total}", svc_cls,
         f"{restarting} restarting" if restarting else "no restart claims"),
        ("TRUTH", _e(risk), risk_cls, _e(h.get("oracle_note", ""))[:38] or "â€”"),
    ]
    cells = "".join(
        f'<div class="sn-tile"><div class="k">{k}</div>'
        f'<div class="v {cls}">{v}</div><div class="s">{_e(sub)}</div></div>'
        for k, v, cls, sub in tiles)
    st.markdown(CSS + f'<div class="sn-wrap"><div class="sn-hud">{cells}</div></div>',
                unsafe_allow_html=True)


def render_expedition(st, s: dict) -> None:
    exp = s.get("expedition", {})
    st.markdown('<div class="sn-wrap"><div class="sn-h">CURRENT EXPEDITION</div></div>',
                unsafe_allow_html=True)

    if exp.get("stage") in (None, "UNKNOWN"):
        st.markdown('<div class="sn-wrap"><div class="sn-card sn-quiet">'
                    'Runtime state unavailable â€” the world is showing unknown, '
                    'not healthy.</div></div>', unsafe_allow_html=True)
        return

    disc = exp.get("discovery") or {}
    blocked = exp.get("blocked_reason")
    body = [
        f'<div class="sn-card{" sn-blocked" if blocked else ""}">',
        f'<div style="font-size:8px;letter-spacing:.18em;color:#8fa3c4">'
        f'{"EXPEDITION #" + str(exp["id"]) if exp.get("id") else "NO ACTIVE EXPEDITION"}'
        f' Â· STAGE {_e(exp.get("stage"))}</div>',
        f'<div style="font-size:15px;margin:5px 0 3px">{_e(exp.get("title"))}</div>',
    ]
    if exp.get("found"):
        body.append(f'<div class="sn-note"><b style="color:#b8cbe4">Found:</b> '
                    f'{_e(exp["found"])[:260]}</div>')
    if exp.get("why"):
        body.append(f'<div class="sn-note"><b style="color:#b8cbe4">Why Sentinuity '
                    f'cares:</b> {_e(exp["why"])[:260]}</div>')
    if disc.get("repository"):
        body.append(f'<div class="sn-note">Source: {_e(disc["repository"])}'
                    f'{" Â· " + _e(disc.get("licence")) if disc.get("licence") else ""}'
                    f'{" Â· " + _e(disc.get("language")) if disc.get("language") else ""}'
                    f'</div>')
    c = exp.get("council", {})
    body.append(f'<div class="sn-note">Council: '
                f'<span class="sn-good">{c.get("support", 0)} support</span> Â· '
                f'<span class="sn-warn">{c.get("challenge", 0)} challenge</span></div>')
    action_cls = "sn-bad" if blocked else "sn-cy"
    body.append(f'<div class="sn-note {action_cls}" style="margin-top:7px">'
                f'{_e(exp.get("current_action"))}</div>')
    body.append("</div>")

    # journey rail â€” the canonical state machine, PENDING looks unfinished
    steps = []
    for j in exp.get("journey", []):
        col, glyph = _STAGE_STYLE.get(j["status"], _STAGE_STYLE["PENDING"])
        steps.append(
            f'<div class="sn-step" style="border-color:{col}44">'
            f'<div class="n" style="color:{col}">{glyph} {_e(j["stage"])}</div>'
            f'<div class="d">{_e(j["detail"])[:56]}</div></div>')
    body.append(f'<div class="sn-rail">{"".join(steps)}</div>')
    st.markdown('<div class="sn-wrap">' + "".join(body) + "</div>",
                unsafe_allow_html=True)


def render_chamber(st, s: dict) -> None:
    d = s.get("debate", {})
    st.markdown('<div class="sn-wrap"><div class="sn-h">DEBATE CHAMBER</div></div>',
                unsafe_allow_html=True)
    if not d.get("available"):
        st.markdown(f'<div class="sn-wrap"><div class="sn-card sn-quiet">'
                    f'{_e(d.get("reason") or "no debate record")}</div></div>',
                    unsafe_allow_html=True)
        return

    q = s.get("quests", {}).get("active") or {}
    rows = ['<div class="sn-card">']
    if q.get("question"):
        rows.append('<div style="font-size:8px;letter-spacing:.18em;color:#8fa3c4">'
                    'DECISIVE QUESTION</div>')
        rows.append(f'<div style="font-size:12px;margin:4px 0 9px;color:#fff">'
                    f'{_e(q["question"])}</div>')
    for p in d.get("positions", []):
        cls = {"support": "sn-sup", "challenge": "sn-chal"}.get(p["stance"], "sn-obs")
        conf = f'{float(p["confidence"])*100:.0f}%' if p.get("confidence") is not None else "â€”"
        rows.append(
            f'<div class="sn-pos"><span class="sn-agent">{_e(p["agent"])}</span>'
            f'<span class="sn-stance {cls}">{_e(p["stance"]).upper()} Â· {conf}</span>'
            f'<span class="sn-sum">{_e(p["summary"])[:110]}</span>'
            f'<span class="sn-obscount">{p["observations"]} obs</span></div>')
    rows.append(f'<div class="sn-note">{len(d.get("canonical", []))} canonical events '
                f'collapsed from {d.get("total_rows", 0)} raw utterances. '
                f'Nothing deleted â€” expand Evidence for the raw record.</div></div>')
    st.markdown('<div class="sn-wrap">' + "".join(rows) + "</div>",
                unsafe_allow_html=True)


def render_forge_polaris(st, s: dict) -> None:
    f = s.get("forge", {})
    p = s.get("polaris", {})
    st.markdown('<div class="sn-wrap"><div class="sn-h">FORGE Â· POLARIS</div></div>',
                unsafe_allow_html=True)

    if not f.get("patches"):
        reason = f.get("reason") or "no implementation written"
        st.markdown(
            f'<div class="sn-wrap"><div class="sn-card">'
            f'<div style="font-size:12px;color:#ffd166">NOTHING IN THE FORGE</div>'
            f'<div class="sn-note">{_e(reason)}. '
            f'Council may have accepted candidates, but no patch exists, so no '
            f'capability can be absorbed.</div>'
            f'<div class="sn-note">Polaris gate: '
            f'<span class="sn-cy">{_e(p.get("gate", "UNKNOWN"))}</span> Â· '
            f'{p.get("open", 0)} open proposals</div></div></div>',
            unsafe_allow_html=True)
        return

    c = f.get("counts", {})
    rows = ['<div class="sn-card">',
            f'<div class="sn-note">{c.get("written",0)} written Â· '
            f'{c.get("applied",0)} applied Â· '
            f'<b class="sn-good">{c.get("absorbed",0)} absorbed</b> Â· '
            f'<b class="sn-warn">{c.get("unverified",0)} applied but unverified</b>'
            f'</div>']
    for patch in f["patches"][:6]:
        rows.append(
            f'<div class="sn-pos" style="flex-wrap:wrap">'
            f'<span class="sn-agent">{_e(patch["stage_label"])}</span>'
            f'<span class="sn-sum">{_e(patch.get("target_file") or "â€”")}</span></div>'
            f'<div style="padding:2px 0 8px">'
            f'compile {_tri_chip(patch["compile"])}'
            f'smoke {_tri_chip(patch["smoke"])}'
            f'verify {_tri_chip(patch["verify"])}</div>')
    rows.append(f'<div class="sn-note">A written patch is not an applied patch, and an '
                f'applied patch with an unrun check is not an absorbed capability.'
                f'</div></div>')
    st.markdown('<div class="sn-wrap">' + "".join(rows) + "</div>",
                unsafe_allow_html=True)


def render_trade_truth(st, s: dict) -> None:
    t = s.get("trade_truth", {})
    st.markdown('<div class="sn-wrap"><div class="sn-h">TRADE TRUTH â€” THE ONLY '
                'MEASURE THAT SETTLES IT</div></div>', unsafe_allow_html=True)
    if not t.get("available"):
        st.markdown(f'<div class="sn-wrap"><div class="sn-card sn-quiet">'
                    f'{_e(t.get("reason") or "no position source")}</div></div>',
                    unsafe_allow_html=True)
        return
    wr = t.get("win_rate")
    verdict_cls = {"NOT PROFITABLE": "sn-bad", "UNPROVEN": "sn-warn",
                   "IMPROVING": "sn-good"}.get(t.get("verdict"), "sn-dim")
    reasons = "".join(
        f'<div class="sn-pos"><span class="sn-sum">{_e(r["reason"])}</span>'
        f'<span class="sn-obscount">{r["count"]}</span></div>'
        for r in t.get("exit_reasons", [])[:6])
    why_closed = (
        '<div class="sn-note" style="margin-top:8px">Why positions closed:</div>' + reasons
        if reasons else ""
    )
    st.markdown(
        f'<div class="sn-wrap"><div class="sn-card">'
        f'<div style="font-size:15px" class="{verdict_cls}">{_e(t.get("verdict"))}</div>'
        f'<div class="sn-note">{t.get("wins",0)} winners Â· {t.get("losses",0)} losers Â· '
        f'{t.get("flat",0)} flat across the last {t.get("closed_sample",0)} closed '
        f'positions'
        f'{" Â· win rate " + str(wr) + "%" if wr is not None else ""}'
        f'{" Â· net " + _money(t.get("pnl_sum")) if t.get("pnl_sum") is not None else ""}'
        f'</div>'
        f'{why_closed}'
        f'<div class="sn-note" style="margin-top:8px">No absorbed capability is '
        f'credited with improving this number without evidence.</div>'
        f'</div></div>', unsafe_allow_html=True)


def render_evidence(st, s: dict) -> None:
    """Everything forensic, collapsed. Nothing here is deleted â€” only folded."""
    exp = s.get("expedition", {})
    disc = exp.get("discovery") or {}

    with st.expander("EVIDENCE â€” provenance, files, safety, raw council record",
                     expanded=False):
        if disc:
            st.markdown(
                CSS + f'<div class="sn-wrap"><div class="sn-card">'
                f'<div class="sn-note">Repository: <b>{_e(disc.get("repository"))}</b>'
                f'<br>URL: {_e(disc.get("repository_url"))}'
                f'<br>Commit: {_e(disc.get("commit_sha"))}'
                f'<br>Licence: {_e(disc.get("licence"))} Â· '
                f'Stars: {_e(disc.get("stars"))} Â· '
                f'Language: {_e(disc.get("language"))}'
                f'<br>Files examined: {_e(disc.get("files_examined"))}'
                f'<br>Safety: {_e(disc.get("safety_status"))} '
                f'{_e(disc.get("safety_findings"))}'
                f'<br>Score: {_e(disc.get("score"))} Â· '
                f'Disposition: {_e(disc.get("disposition"))}'
                f'</div><div class="sn-note" style="margin-top:8px">'
                f'<b>Extracted principle</b><br>{_e(disc.get("principle"))}</div>'
                f'</div></div>', unsafe_allow_html=True)
        refs = exp.get("evidence_refs") or []
        if refs:
            st.caption("Evidence rows: " + " Â· ".join(refs))

        d = s.get("debate", {})
        if d.get("canonical"):
            st.caption(f"Raw council record â€” {d.get('total_rows', 0)} rows, "
                       f"grouped by speaker and action")
            st.dataframe(
                [{"agent": g["agent"], "action": g["action"],
                  "observations": g["count"], "summary": g["summary"][:90]}
                 for g in d["canonical"]],
                use_container_width=True, hide_index=True)

        miss = s.get("missing_tables") or []
        notes = s.get("notes") or []
        if miss or notes:
            st.caption("Projection gaps (blocks rendered as unknown, never as healthy)")
            if miss:
                st.code("absent tables: " + ", ".join(miss))
            if notes:
                st.code("read notes: " + ", ".join(notes[:12]))

    with st.expander("FACULTY DETAIL â€” service-level health", expanded=False):
        for key, fac in (s.get("faculties") or {}).items():
            st.markdown(f"**{fac['label']}** â€” {fac['health']} Â· "
                        f"{fac['services_alive']}/{fac['services_known']} alive Â· "
                        f"{fac['capabilities']} absorbed capabilities")
            st.dataframe(fac.get("detail", []), use_container_width=True,
                         hide_index=True)

    with st.expander("CHRONICLE â€” canonical event history", expanded=False):
        ev = s.get("chronicle") or []
        if not ev:
            st.caption("No events recorded.")
        else:
            st.dataframe(
                [{"when": _age(e.get("age_sec")), "agent": e.get("agent"),
                  "event": e.get("event_type"), "summary": (e.get("summary") or "")[:88],
                  "state": e.get("state"), "source": e.get("source")}
                 for e in ev],
                use_container_width=True, hide_index=True)


# â”€â”€ entry point â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
def render_home(st, state: Optional[dict] = None, world_height: int = 620) -> dict:
    """
    Renders the whole Home surface in canonical order.
    Returns the payload so callers can reuse it without a second DB read.
    """
    if state is None:
        from ui.sentinuity_canon import load_canonical_state
        state = load_canonical_state(DB_PATH)

    render_hud(st, state)

    st.markdown('<div class="sn-wrap"><div class="sn-h">LIVING WORLD</div></div>',
                unsafe_allow_html=True)
    try:
        from ui.sentinuity_world_bridge import render_world
        render_world(state, height=world_height)
    except Exception as exc:
        st.warning(f"World layer unavailable: {type(exc).__name__}: {str(exc)[:160]}")

    render_expedition(st, state)
    render_chamber(st, state)
    render_forge_polaris(st, state)
    render_trade_truth(st, state)
    render_evidence(st, state)
    return state
