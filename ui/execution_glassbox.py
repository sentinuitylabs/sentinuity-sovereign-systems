# -*- coding: utf-8 -*-
"""
SENTINUITY — Execution Glassbox
PRICE TRUTH · PEAK AUTHORITY · EXIT ROUTE

Read-only Streamlit representation for open positions. No execution imports,
no network I/O, no database writes. The panel reconstructs the live decision
chain from persisted position/price-truth/lifecycle evidence plus bounded log
TAIL inspection so UI rendering can never become an execution dependency.
"""
from __future__ import annotations

import datetime as _dt
import html as _html
import json
import math
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable

C_GREEN = "#14F195"
C_CYAN = "#5FD3E0"
C_PURPLE = "#9945FF"
C_GOLD = "#E8B84C"
C_RED = "#FF5C6E"
C_DIM = "rgba(255,255,255,0.28)"
C_TEXT = "rgba(255,255,255,0.82)"

_CSS = r"""
<style>
.sntGlass{background:linear-gradient(180deg,rgba(8,5,22,.90),rgba(4,3,14,.96));border:1px solid rgba(95,211,224,.16);border-radius:16px;padding:13px 14px 11px;margin:4px 0 14px;box-shadow:0 0 32px rgba(95,211,224,.045),inset 0 1px 0 rgba(255,255,255,.025);}
.sntGlassHdr{display:flex;justify-content:space-between;gap:12px;align-items:center;padding-bottom:9px;border-bottom:1px solid rgba(255,255,255,.055);font-family:Share Tech Mono,monospace;}
.sntGlassTitle{font-size:.64rem;letter-spacing:3.3px;color:rgba(95,211,224,.88);}.sntGlassSub{font-size:.48rem;letter-spacing:1.7px;color:rgba(255,255,255,.25);margin-top:2px;}.sntGlassBadge{font-size:.48rem;letter-spacing:1.5px;padding:3px 7px;border-radius:999px;border:1px solid rgba(20,241,149,.25);color:#14F195;background:rgba(20,241,149,.035);white-space:nowrap;}
.sntGBPos{margin-top:10px;border:1px solid rgba(153,69,255,.16);border-radius:12px;background:rgba(153,69,255,.022);overflow:hidden;}.sntGBPosTop{display:grid;grid-template-columns:minmax(140px,1.2fr) repeat(4,minmax(80px,.62fr));gap:8px;align-items:center;padding:9px 10px;background:rgba(255,255,255,.018);border-bottom:1px solid rgba(255,255,255,.045);}
.sntGBName{font-family:Share Tech Mono,monospace;font-size:.67rem;color:rgba(255,255,255,.9);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sntGBMint{font-size:.44rem;color:rgba(255,255,255,.20);margin-top:2px;letter-spacing:.8px}.sntGBMetric{text-align:right;font-family:Share Tech Mono,monospace}.sntGBMetricK{font-size:.43rem;letter-spacing:1.2px;color:rgba(255,255,255,.24)}.sntGBMetricV{font-size:.67rem;margin-top:2px;color:rgba(255,255,255,.74)}
.sntGBBody{display:grid;grid-template-columns:1.1fr .9fr;gap:10px;padding:10px}.sntGBPane{border:1px solid rgba(255,255,255,.045);border-radius:9px;padding:8px 9px;background:rgba(0,0,0,.13)}.sntGBPaneTitle{font-family:Share Tech Mono,monospace;font-size:.48rem;letter-spacing:2.1px;color:rgba(153,69,255,.72);margin-bottom:6px}
.sntGBSource{display:grid;grid-template-columns:88px 1fr 63px 68px;gap:6px;align-items:center;padding:3px 0;border-bottom:1px solid rgba(255,255,255,.025);font-family:Share Tech Mono,monospace;font-size:.49rem}.sntGBSource:last-child{border-bottom:0}.sntGBSrcName{color:rgba(255,255,255,.49)}.sntGBSrcVal{color:rgba(255,255,255,.78);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.sntGBAge{color:rgba(95,211,224,.55);text-align:right}.sntGBState{text-align:right;font-size:.44rem;letter-spacing:.7px}.sntGBok{color:#14F195}.sntGBflow{color:#5FD3E0}.sntGBintel{color:#9945FF}.sntGBgold{color:#E8B84C;text-shadow:0 0 7px rgba(232,184,76,.30)}.sntGBbad{color:#FF5C6E}.sntGBdim{color:rgba(255,255,255,.20)}
.sntGBTimeline{font-family:Share Tech Mono,monospace;font-size:.47rem;line-height:1.55;max-height:176px;overflow-y:auto;padding-right:3px}.sntGBEvt{display:grid;grid-template-columns:52px 82px 1fr;gap:6px;padding:2px 0}.sntGBT{color:rgba(255,255,255,.18)}.sntGBChan{color:rgba(95,211,224,.48);white-space:nowrap}.sntGBMsg{color:rgba(255,255,255,.56);word-break:break-word}.sntGBLinks{display:flex;flex-wrap:wrap;gap:7px;padding:0 10px 9px}.sntGBLinks a{font-family:Share Tech Mono,monospace;font-size:.44rem;letter-spacing:.9px;color:rgba(153,69,255,.62);text-decoration:none;border-bottom:1px solid rgba(153,69,255,.20)}.sntGBLinks a:hover{color:#5FD3E0;border-color:#5FD3E0}
.sntGBEmpty{font-family:Share Tech Mono,monospace;font-size:.52rem;letter-spacing:1.5px;color:rgba(255,255,255,.22);padding:11px 2px}.sntGBCadence{font-family:Share Tech Mono,monospace;font-size:.46rem;letter-spacing:.35px;color:rgba(95,211,224,.70);padding:7px 10px;margin:8px 0 9px;border:1px solid rgba(95,211,224,.12);border-radius:8px;background:rgba(95,211,224,.025);white-space:normal;word-break:break-word}.sntGBCadence b{color:#5FD3E0;font-weight:500}.sntGBLegend{font-family:Share Tech Mono,monospace;font-size:.42rem;color:rgba(255,255,255,.16);letter-spacing:.7px;margin-top:7px}.sntGBLegend b{font-weight:500}.sntGBGoldDot{color:#E8B84C}.sntGBCyanDot{color:#5FD3E0}.sntGBPplDot{color:#9945FF}.sntGBRedDot{color:#FF5C6E}.sntGBGreenDot{color:#14F195}
@media(max-width:900px){.sntGBBody{grid-template-columns:1fr}.sntGBPosTop{grid-template-columns:1fr 1fr}.sntGBNameBlock{grid-column:1/-1}.sntGBSource{grid-template-columns:78px 1fr 52px 55px}}
</style>
"""


def _safe(v: Any, n: int = 120) -> str:
    return _html.escape(str(v if v is not None else ""))[:n]


def _f(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return None


def _pct(price: Any, entry: Any) -> float | None:
    # SIGNOFF_MARK_TRUTH_20260812 (surviving -100% formula, site 2 of 3):
    # This guarded the DENOMINATOR (e <= 0) but not the NUMERATOR. A sentinel
    # or missing zero mark price therefore returned (0/e - 1.0)*100 = exactly
    # -100.0%, and _fmt_pct rendered that as a confident "-100.0%" instead of
    # the "n/a" it renders for None. An absent mark was displayed as total loss.
    #
    # p <= 0 is not a price. There is no executable quote at or below zero, so
    # it can only mean the mark is missing, sentinel, or corrupt — all of which
    # are "we do not know", not "the position is worthless".
    #
    # A genuine fresh positive near-zero quote (p > 0) still flows through
    # untouched and still reports its true catastrophic percentage. Nothing is
    # clamped: this separates UNKNOWN from CATASTROPHIC, it does not hide loss.
    p, e = _f(price), _f(entry)
    if p is None or e is None or e <= 0 or p <= 0:
        return None
    return (p / e - 1.0) * 100.0


def _fmt_pct(v: Any) -> str:
    x = _f(v)
    return "n/a" if x is None else f"{x:+.1f}%"


def _fmt_age(ts: Any, now: float) -> str:
    x = _f(ts)
    if not x:
        return "n/a"
    age = max(0.0, now - x)
    if age < 10:
        return f"{age:.1f}s"
    if age < 120:
        return f"{age:.0f}s"
    return f"{age/60:.1f}m"


def _clock(ts: Any) -> str:
    x = _f(ts)
    if not x:
        return "--:--:--"
    try:
        return _dt.datetime.fromtimestamp(x).strftime("%H:%M:%S")
    except Exception:
        return "--:--:--"


def _connect_ro(path: Path) -> sqlite3.Connection | None:
    try:
        c = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=0.25)
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def _table(conn: sqlite3.Connection | None, name: str) -> bool:
    if conn is None:
        return False
    try:
        return conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None
    except Exception:
        return False


def _cols(conn: sqlite3.Connection | None, name: str) -> set[str]:
    if conn is None:
        return set()
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({name})").fetchall()}
    except Exception:
        return set()


def _latest(conn: sqlite3.Connection | None, table: str, position_id: int, time_col: str) -> dict:
    if not _table(conn, table):
        return {}
    try:
        r = conn.execute(f"SELECT * FROM {table} WHERE position_id=? ORDER BY {time_col} DESC LIMIT 1", (int(position_id),)).fetchone()
        return dict(r) if r else {}
    except Exception:
        return {}


def _open_positions(matrix: sqlite3.Connection | None) -> list[dict]:
    if not _table(matrix, "paper_positions"):
        return []
    c = _cols(matrix, "paper_positions")
    wanted = [x for x in (
        "id","mint_address","token_name","token_symbol","entry_price","current_price",
        "position_size_usd","unrealized_pnl_usd","opened_at","runner_protected",
        "runner_peak_pct","runner_lock_floor_pct","runner_lock_price","runner_peak_trust_source",
        "highest_price_seen","trusted_peak_price","last_mark_source","mark_source","is_live",
        "mode","live_position","status"
    ) if x in c]
    if not wanted:
        return []
    try:
        rows = matrix.execute(f"SELECT {','.join(wanted)} FROM paper_positions WHERE status='OPEN' ORDER BY opened_at DESC LIMIT 8").fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _lifecycle(matrix: sqlite3.Connection | None, pos_id: int, mint: str, limit: int = 14) -> list[dict]:
    if not _table(matrix, "trade_lifecycle_events"):
        return []
    c = _cols(matrix, "trade_lifecycle_events")
    tc = next((x for x in ("event_ts","ts","timestamp","created_at") if x in c), None)
    if not tc:
        return []
    clauses, args = [], []
    if "position_id" in c:
        clauses.append("position_id=?"); args.append(int(pos_id))
    if "mint_address" in c:
        clauses.append("mint_address=?"); args.append(mint)
    if not clauses:
        return []
    try:
        q = f"SELECT * FROM trade_lifecycle_events WHERE ({' OR '.join(clauses)}) ORDER BY {tc} DESC LIMIT ?"
        rows = matrix.execute(q, (*args, int(limit))).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def _log_tail(root: Path, mint: str, token: str, max_events: int = 16) -> list[dict]:
    """Bounded tail scan only. Never opens more than a handful of known logs."""
    log_dir = root / "logs"
    if not log_dir.exists() or not mint:
        return []
    keys = ("dexscreener","gmgn","birdeye","gecko","jupiter","route","quote","runner_floor",
            "runner floor","peak","sell","external","corroborat","no_route","timeout")
    files = ("execution_engine.log","ws_price_oracle.log","market_intelligence.log",
             "system_guardian.log","price_truth_mesh.log")
    out: list[dict] = []
    mshort = mint[:12].lower()
    tshort = (token or "").lower()[:14]
    ts_re = re.compile(r"(?P<d>20\d\d[-/]\d\d[-/]\d\d)[ T](?P<t>\d\d:\d\d:\d\d)")
    for fn in files:
        p = log_dir / fn
        if not p.exists():
            continue
        try:
            # bounded byte tail, rather than reading multi-MB historical logs
            with p.open("rb") as f:
                f.seek(0, 2); size = f.tell(); f.seek(max(0, size - 180_000))
                text = f.read().decode("utf-8", "ignore")
            for line in text.splitlines():
                low = line.lower()
                if mshort not in low and (not tshort or tshort not in low):
                    continue
                if not any(k in low for k in keys):
                    continue
                mt = ts_re.search(line)
                out.append({"ts_text": mt.group("t") if mt else "--:--:--", "channel": fn.replace(".log",""), "msg": line[-520:]})
        except Exception:
            continue
    return out[-max_events:]


def _source_row(name: str, value: str, age: str, state: str, cls: str) -> str:
    return (f"<div class='sntGBSource'><span class='sntGBSrcName'>{_safe(name,18)}</span>"
            f"<span class='sntGBSrcVal'>{_safe(value,42)}</span><span class='sntGBAge'>{_safe(age,12)}</span>"
            f"<span class='sntGBState {cls}'>{_safe(state,18)}</span></div>")


def _event(ts: str, channel: str, msg: str, cls: str = "") -> str:
    return (f"<div class='sntGBEvt'><span class='sntGBT'>{_safe(ts,10)}</span>"
            f"<span class='sntGBChan'>{_safe(channel,18)}</span><span class='sntGBMsg {cls}'>{_safe(msg,420)}</span></div>")


def _latest_cadence_line(root: Path) -> str:
    """Read the newest measured [CADENCE] heartbeat emitted by execution_engine.

    The probe itself is process-local, so importing it in Streamlit would create
    an empty UI-process snapshot.  The engine log is the correct cross-process
    bridge and remains read-only from the Glassbox.
    """
    p = root / "logs" / "execution_engine.log"
    if not p.exists():
        return "CADENCE: awaiting measured engine heartbeat"
    try:
        with p.open("rb") as f:
            f.seek(0, 2)
            size = f.tell()
            f.seek(max(0, size - 120_000))
            lines = f.read().decode("utf-8", "ignore").splitlines()
        for line in reversed(lines):
            if "[CADENCE]" in line:
                return line.split("[CADENCE]", 1)[1].strip()[:420]
    except Exception:
        pass
    return "CADENCE: awaiting measured engine heartbeat"


def _external_activity(log_events: Iterable[dict], provider: str) -> tuple[str,str]:
    hits = [e for e in log_events if provider.lower() in str(e.get("msg","")).lower()]
    if not hits:
        return "NO PERSISTED SAMPLE", "sntGBdim"
    low = str(hits[-1].get("msg","")).lower()
    if any(k in low for k in ("timeout","fail","error","429","403","no_route")):
        return "ATTEMPT / FAIL", "sntGBbad"
    if any(k in low for k in ("success","confirmed","price","quote","cache")):
        return "OBSERVED", "sntGBintel"
    return "ATTEMPT", "sntGBintel"


def _links(mint: str) -> str:
    if len(mint) < 8:
        return ""
    urls = [
        ("DEXSCREENER", f"https://dexscreener.com/solana/{mint}"),
        ("GMGN", f"https://gmgn.ai/sol/token/{mint}"),
        ("BIRDEYE", f"https://birdeye.so/token/{mint}?chain=solana"),
        ("GECKO", f"https://www.geckoterminal.com/solana/tokens/{mint}"),
        ("JUPITER", f"https://jup.ag/swap/SOL-{mint}"),
        ("PUMP", f"https://pump.fun/{mint}"),
    ]
    return "<div class='sntGBLinks'>" + "".join(f"<a href='{u}' target='_blank'>{n}</a>" for n,u in urls) + "</div>"


def render_execution_glassbox(db_path: Any = None, root: Any = None) -> None:
    try:
        import streamlit as st
    except Exception:
        return

    base = Path(str(root)) if root is not None else Path(__file__).resolve().parent.parent
    matrix_path = Path(str(db_path)) if db_path is not None else base / "sentinuity_matrix.db"
    truth_path = base / "sentinuity_price_truth.db"
    matrix = _connect_ro(matrix_path)
    truth = _connect_ro(truth_path)
    now = time.time()

    try:
        positions = _open_positions(matrix)
        st.markdown(_CSS, unsafe_allow_html=True)
        head = ("<div class='sntGlass'><div class='sntGlassHdr'><div><div class='sntGlassTitle'>⬡ EXECUTION GLASSBOX</div>"
                "<div class='sntGlassSub'>PRICE TRUTH · PEAK AUTHORITY · EXIT ROUTE</div></div>"
                "<span class='sntGlassBadge'>READ-ONLY · HOT PATH ISOLATED</span></div>")
        body = []
        _cad = _latest_cadence_line(base)
        body.append("<div class='sntGBCadence'><b>MEASURED ENGINE CADENCE</b> · " + _safe(_cad, 420) + "</div>")
        if not positions:
            body.append("<div class='sntGBEmpty'>// NO OPEN POSITION — PRICE TRUTH FABRIC STANDING BY //</div>")
        for p in positions:
            pid = int(p.get("id") or 0); mint = str(p.get("mint_address") or "")
            name = str(p.get("token_name") or p.get("token_symbol") or mint[:12] or "UNKNOWN")
            entry = _f(p.get("entry_price")) or 0.0; cur = _f(p.get("current_price"))
            current_pct = _pct(cur, entry)
            floor_pct = _f(p.get("runner_lock_floor_pct")); floor_on = bool(int(p.get("runner_protected") or 0)) and floor_pct is not None

            onchain = _latest(truth, "peak_onchain_state", pid, "observed_at")
            tape = _latest(truth, "peak_trade_tape", pid, "observed_at")
            quote = _latest(truth, "peak_executable_quotes", pid, "quote_ts")
            cand = _latest(truth, "peak_truth_candidates", pid, "candidate_ts")
            logev = _log_tail(base, mint, name)
            life = _lifecycle(matrix, pid, mint)

            on_px = _f(onchain.get("executable_curve_price_usd")) or _f(onchain.get("derived_price_usd"))
            tape_px = _f(tape.get("effective_price_usd"))
            ex_px = _f(quote.get("effective_price_usd"))
            trust_px = _f(cand.get("trusted_price_usd"))
            ex_pct = _pct(ex_px, entry); trust_pct = _pct(trust_px, entry)
            raw_peak_pct = _pct(p.get("highest_price_seen"), entry)
            auth_state = str(cand.get("state") or "WAITING")
            auth_reason = str(cand.get("reason") or "no current peak candidate")
            sellable = bool(int(quote.get("sellable") or 0)) if quote else False
            route = str(quote.get("route") or "")
            impact = _f(quote.get("price_impact_pct"))
            quote_age = _fmt_age(quote.get("quote_ts"), now)

            cur_cls = "sntGBgold" if (current_pct is not None and current_pct >= 75) else ("sntGBok" if (current_pct or 0) > 0 else "sntGBbad" if (current_pct or 0) < 0 else "sntGBdim")
            floor_cls = "sntGBgold" if floor_on else "sntGBdim"
            route_state = "SELLABLE" if sellable else ("NO ROUTE" if quote else "WAITING")
            route_cls = "sntGBflow" if sellable else ("sntGBbad" if quote else "sntGBdim")

            src = []
            src.append(_source_row("NATIVE", _fmt_pct(_pct(on_px,entry)), _fmt_age(onchain.get("observed_at"),now), str(onchain.get("integrity_status") or "NO WITNESS"), "sntGBok" if onchain else "sntGBdim"))
            src.append(_source_row("TRADE TAPE", _fmt_pct(_pct(tape_px,entry)), _fmt_age(tape.get("observed_at"),now), str(tape.get("reconciliation_status") or "NO WITNESS"), "sntGBok" if tape else "sntGBdim"))
            src.append(_source_row("JUPITER", _fmt_pct(ex_pct), quote_age, "EXECUTABLE" if sellable else ("UNSELLABLE" if quote else "NO QUOTE"), "sntGBflow" if sellable else ("sntGBbad" if quote else "sntGBdim")))
            for provider in ("DEXSCREENER","GMGN","BIRDEYE","GECKO"):
                state, cls = _external_activity(logev, provider)
                src.append(_source_row(provider, "external corroborator", "cache/log", state, cls))

            timeline: list[str] = []
            # Persisted truth first; newest display later after sorting.
            evs: list[tuple[float,str,str,str]] = []
            if onchain:
                evs.append((_f(onchain.get("observed_at")) or 0,"PRICE::NATIVE",f"witness {onchain.get('integrity_status','')} price={_fmt_pct(_pct(on_px,entry))}","sntGBok"))
            if tape:
                evs.append((_f(tape.get("observed_at")) or 0,"PRICE::TAPE",f"trade witness {tape.get('reconciliation_status','')} price={_fmt_pct(_pct(tape_px,entry))}","sntGBok"))
            if quote:
                evs.append((_f(quote.get("quote_ts")) or 0,"PRICE::JUPITER",f"sell quote={_fmt_pct(ex_pct)} impact={(f'{impact:.1f}%' if impact is not None else 'n/a')} route={route[:80] or 'n/a'} sellable={int(sellable)}","sntGBflow" if sellable else "sntGBbad"))
            if cand:
                evs.append((_f(cand.get("candidate_ts")) or 0,"AUTH::PEAK",f"{auth_state} trusted={_fmt_pct(trust_pct)} · {auth_reason[:180]}","sntGBgold" if "TRUST" in auth_state.upper() else "sntGBintel"))
            for e in life[:10]:
                tc = next((_f(e.get(k)) for k in ("event_ts","ts","timestamp","created_at") if _f(e.get(k))),0.0)
                typ = str(e.get("event_type") or e.get("event") or e.get("stage") or "LIFECYCLE")
                msg = str(e.get("reason") or e.get("message") or e.get("detail") or e.get("state") or typ)
                evs.append((tc,"LIFE::"+typ[:12],msg[:220],"sntGBgold" if "RUNNER" in (typ+msg).upper() else ""))
            # Log evidence has text time only; render after persisted events as supplemental attempts.
            evs.sort(key=lambda x:x[0], reverse=True)
            for ts,ch,msg,cls in evs[:12]: timeline.append(_event(_clock(ts),ch,msg,cls))
            for e in logev[-8:]:
                msg = str(e.get("msg") or "")
                cls = "sntGBbad" if any(x in msg.lower() for x in ("fail","timeout","no_route","blocked")) else "sntGBintel" if any(x in msg.lower() for x in ("dex","gmgn","birdeye","gecko","external")) else "sntGBflow"
                timeline.append(_event(str(e.get("ts_text") or "--:--:--"),str(e.get("channel") or "LOG"),msg,cls))
            if not timeline:
                timeline.append(_event("--:--:--","GLASSBOX","no persisted route/source attempt yet","sntGBdim"))

            body.append(
                "<div class='sntGBPos'>"
                "<div class='sntGBPosTop'>"
                f"<div class='sntGBNameBlock'><div class='sntGBName'>{_safe(name,30)}</div><div class='sntGBMint'>{_safe(mint[:8]+'…'+mint[-6:] if len(mint)>16 else mint,28)}</div></div>"
                f"<div class='sntGBMetric'><div class='sntGBMetricK'>CURRENT</div><div class='sntGBMetricV {cur_cls}'>{_fmt_pct(current_pct)}</div></div>"
                f"<div class='sntGBMetric'><div class='sntGBMetricK'>TRUSTED PEAK</div><div class='sntGBMetricV {'sntGBgold' if trust_pct is not None else 'sntGBdim'}'>{_fmt_pct(trust_pct)}</div></div>"
                f"<div class='sntGBMetric'><div class='sntGBMetricK'>EXECUTABLE</div><div class='sntGBMetricV {'sntGBflow' if ex_pct is not None else 'sntGBdim'}'>{_fmt_pct(ex_pct)}</div></div>"
                f"<div class='sntGBMetric'><div class='sntGBMetricK'>FLOOR</div><div class='sntGBMetricV {floor_cls}'>{_fmt_pct(floor_pct) if floor_on else 'NOT ARMED'}</div></div>"
                "</div>"
                "<div class='sntGBBody'>"
                "<div class='sntGBPane'><div class='sntGBPaneTitle'>PRICE TRUTH · SOURCE MESH</div>" + "".join(src) +
                f"<div class='sntGBLegend'>RAW OBSERVED PEAK {_fmt_pct(raw_peak_pct)} · AUTHORITY <b class='{'sntGBgold' if 'TRUST' in auth_state.upper() else 'sntGBPplDot'}'>{_safe(auth_state,32)}</b> · ROUTE <b class='{route_cls}'>{route_state}</b></div></div>"
                "<div class='sntGBPane'><div class='sntGBPaneTitle'>DECISION TRACE · EXIT ROUTE BUILD</div><div class='sntGBTimeline'>" + "".join(timeline) + "</div></div>"
                "</div>" + _links(mint) + "</div>"
            )
        tail = ("<div class='sntGBLegend'><span class='sntGBGreenDot'>●</span> CONFIRMED TRUTH &nbsp; "
                "<span class='sntGBCyanDot'>●</span> EXECUTION / ROUTE &nbsp; <span class='sntGBPplDot'>●</span> RESEARCH / EXTERNAL &nbsp; "
                "<span class='sntGBGoldDot'>●</span> EARNED RUNNER / FLOOR &nbsp; <span class='sntGBRedDot'>●</span> REFUSAL / FAILURE</div></div>")
        st.markdown(head + "".join(body) + tail, unsafe_allow_html=True)
    finally:
        for c in (matrix, truth):
            try:
                if c: c.close()
            except Exception:
                pass


# Stable alias for hub integration.
render_price_truth_exit_route_glassbox = render_execution_glassbox
