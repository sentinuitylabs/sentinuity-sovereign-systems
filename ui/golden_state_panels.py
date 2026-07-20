#!/usr/bin/env python3
"""
ui/golden_state_panels.py — GOLDEN-STATE UI PANELS (Task 11)

Compact liquid-glass renderers for every table built in this directive.
All HTML builders are pure functions (headless-testable); render_* wrappers
need streamlit. Doctrine: Solana violet/cyan/mint/gold, glass pills and
thin rails — no bulky boxes, no generic traffic lights. Missing data
renders as an honest MISSING/n-a state, never fake numbers.

Panels:
  render_winrate_card()          Task 1  — real winrate, n/a on zero sample
  render_hour_heatmap()          Task 2/3— living 24h strip + pressure row
  render_sub100_row()            Task 4  — hot-potato mode per hour
  render_signal_gate_reason()    Task 5  — exact starvation reason pill
  render_copytrade_status()      Task 6  — dataset source + influence mode
  render_standing_tasklist()     Task 7  — owner/state pills
  render_debate_chamber()        Task 8  — real rows or IDLE, never blank
  render_db_lights()             Task 9  — size %, colour, maintenance
  render_live_test_ledger()      Task 10 — mini mechanism-test panel

Drop-in: from ui.golden_state_panels import render_all;  render_all()
"""
from __future__ import annotations

import html
import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

V, CYAN, MINT, GOLD, RED, DIM, GLASS = ("#9945FF", "#38E1FF", "#14F195",
                                        "#FFD700", "#FF073A", "#5C6F66",
                                        "rgba(5,7,6,.88)")
MONO = "font-family:Share Tech Mono,monospace;"

BAND_HEX = {
    "BRIGHT_GOLD_PRIME": "#FFD700", "SOFT_GOLD_STRONG": "#E6C200",
    "EMERALD_WARM": "#14F195", "CYAN_WATCH": "#38E1FF",
    "DEEP_VIOLET_COLD": "#6B4FA0", "RED_VIOLET_DANGER": "#C2185B",
    "VIOLET_GLASS_LOW_SAMPLE": "#9945FF",
}
MODE_HEX = {"PRIME_RUNNER_ALLOWED": GOLD, "NORMAL": MINT,
            "HOT_POTATO": "#FF6B35", "PAPER_ONLY": V, "BLOCK": RED}
GATE_HEX = {"PASSING": MINT, "IDLE_NO_FLOW": CYAN,
            "VETO_DOMINATED": GOLD, "SENSOR_MISMATCH": "#FF6B35",
            "STARVED_STALE_SOURCE": RED, "STARVED_SERVICE_DOWN": RED}


def _intel() -> Optional[Path]:
    for p in (Path(os.environ.get("SENTINUITY_INTEL_DB", "")) if
              os.environ.get("SENTINUITY_INTEL_DB") else None,
              ROOT / "sentinuity_intelligence.db",
              ROOT / "services" / "sentinuity_intelligence.db"):
        if p and p.exists():
            return p
    return None


def _rows(table: str, order: str = "") -> List[Dict[str, Any]]:
    db = _intel()
    if not db:
        return []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        out = [dict(r) for r in con.execute(
            f"SELECT * FROM {table} {order}")]
        con.close()
        return out
    except Exception:
        return []


def _pill(text: str, colour: str, title: str = "", pulse: bool = False) -> str:
    anim = "animation:gsglow 1.8s ease-in-out infinite;" if pulse else ""
    return (f"<span title='{html.escape(title)}' style='display:inline-flex;"
            f"align-items:center;gap:6px;border:1px solid {colour}55;"
            f"background:{GLASS};border-radius:999px;padding:2px 10px 2px 6px;"
            f"font-size:.62rem;{MONO}color:#9DB5A8;'>"
            f"<span style='width:7px;height:7px;border-radius:50%;"
            f"background:{colour};box-shadow:0 0 6px {colour};{anim}'></span>"
            f"<span style='color:{colour};letter-spacing:.07em;'>"
            f"{html.escape(text)}</span></span>")


def _rail(parts: List[str], label: str = "") -> str:
    head = (f"<div style='color:{DIM};font-size:.55rem;letter-spacing:.18em;"
            f"{MONO}margin-bottom:3px;'>{html.escape(label)}</div>"
            if label else "")
    return ("<style>@keyframes gsglow{0%,100%{opacity:1}50%{opacity:.4}}"
            "</style>" + head +
            "<div style='display:flex;flex-wrap:wrap;gap:7px;align-items:"
            "center;padding:2px 0 8px 0;'>" + "".join(parts) + "</div>")


def _missing(what: str, hint: str) -> str:
    return _rail([_pill(f"{what} MISSING — {hint}", "#3b2d5e")])


def _age(ts) -> str:
    if not ts:
        return "-"
    a = time.time() - float(ts)
    return f"{a:.0f}s" if a < 120 else (f"{a/60:.0f}m" if a < 7200
                                        else f"{a/3600:.1f}h")


# ── Task 1 ──────────────────────────────────────────────────────────────

def winrate_card_html() -> str:
    r = (_rows("winrate_truth") or [None])[0]
    if not r:
        return _missing("WINRATE", "run services/winrate_truth.py")
    n = r.get("closed_count_all_time") or 0
    wr = r.get("win_rate_all_time")
    txt = "n/a" if (wr is None or n == 0) else f"{wr:.1f}%"
    col = V if n < 10 else (MINT if (wr or 0) >= 50 else
                            ("#C2185B" if (wr or 0) < 40 else GOLD))
    parts = [
        f"<div style='border:1px solid {col}44;background:{GLASS};"
        f"border-radius:12px;padding:8px 14px;display:inline-block;'>"
        f"<div style='color:{DIM};font-size:.55rem;letter-spacing:.2em;"
        f"{MONO}'>WIN RATE · paper_positions</div>"
        f"<div style='color:{col};font-size:1.3rem;font-weight:900;"
        f"{MONO}'>{txt} <span style='font-size:.65rem;color:{DIM};'>"
        f"n={n}</span></div>"
        f"<div style='color:{DIM};font-size:.58rem;{MONO}'>"
        f"24h {('n/a' if r.get('win_rate_24h') is None else f_pct(r['win_rate_24h']))}"
        f" ({r.get('closed_count_24h') or 0}) · "
        f"72h {('n/a' if r.get('win_rate_72h') is None else f_pct(r['win_rate_72h']))}"
        f" ({r.get('closed_count_72h') or 0}) · "
        f"{r.get('winner_count') or 0}W/{r.get('loser_count') or 0}L/"
        f"{r.get('breakeven_count') or 0}BE · "
        f"upd {_age(r.get('latest_winrate_updated_at'))} ago</div></div>"]
    return _rail(parts)


def f_pct(v) -> str:
    return f"{float(v):.1f}%"


# ── Tasks 2 + 3 ─────────────────────────────────────────────────────────

def hour_heatmap_html() -> str:
    perf = {r["local_hour"]: r for r in _rows("hourly_performance_profile")}
    pres = {r["local_hour"]: r for r in _rows("hourly_market_pressure")}
    if not perf:
        return _missing("HOUR MAP", "run services/hour_intelligence.py --once")
    import sys as _sys
    if str(ROOT) not in _sys.path:
        _sys.path.insert(0, str(ROOT))
    from core.live_lane_common import aest_hour
    now_h = aest_hour(time.time())
    cells = []
    for h in range(24):
        p = perf.get(h, {})
        n = p.get("sample_count_7d") or 0
        band = p.get("colour_band") or "VIOLET_GLASS_LOW_SAMPLE"
        col = BAND_HEX.get(band, V)
        q = pres.get(h, {})
        tide = q.get("market_tide_state") or "?"
        pulse = ("animation:gsglow 1.8s ease-in-out infinite;"
                 if h == now_h else "")
        low = "opacity:.45;border-style:dashed;" if n < 3 else ""
        title = (f"{h:02d}:00 AEST | n7d={n} net={p.get('net_pnl') or 0:+.1f} "
                 f"wr={p.get('win_rate') if p.get('win_rate') is not None else '-'} "
                 f"{band} | tide={tide} "
                 f"pressure={q.get('pressure_score') if q else '-'}")
        cells.append(
            f"<div title='{html.escape(title)}' style='flex:1;min-width:22px;"
            f"text-align:center;'>"
            f"<div style='height:16px;border-radius:4px;background:{col};"
            f"box-shadow:0 0 6px {col}66;{pulse}{low}'></div>"
            f"<div style='color:{DIM};font-size:.5rem;{MONO}'>{h:02d}</div>"
            f"<div style='color:{CYAN};font-size:.48rem;{MONO}'>"
            f"{html.escape(str(tide)[:4])}</div></div>")
    return ("<style>@keyframes gsglow{0%,100%{opacity:1}50%{opacity:.4}}"
            "</style>"
            f"<div style='color:{DIM};font-size:.55rem;letter-spacing:.18em;"
            f"{MONO}margin-bottom:3px;'>LIVING HOUR MAP (AEST) · rolling 7d · "
            f"dashed = low sample</div>"
            "<div style='display:flex;gap:3px;'>" + "".join(cells) + "</div>")


# ── Task 4 ──────────────────────────────────────────────────────────────

def sub100_row_html() -> str:
    rows = {r["local_hour"]: r for r in _rows("sub100_hour_profile")}
    if not rows:
        return _missing("SUB-100", "run services/hour_intelligence.py --once")
    cells = []
    for h in range(24):
        r = rows.get(h, {})
        mode = r.get("recommended_mode") or "PAPER_ONLY"
        col = MODE_HEX.get(mode, V)
        n = r.get("sub100_entries") or 0
        title = (f"{h:02d}:00 sub-100k | n={n} "
                 f"net={r.get('sub100_net_pnl') if r.get('sub100_net_pnl') is not None else '-'} "
                 f"hotpotato={r.get('sub100_hot_potato_score') if r.get('sub100_hot_potato_score') is not None else '-'} "
                 f"→ {mode}")
        low = "opacity:.4;" if n < 3 else ""
        cells.append(
            f"<div title='{html.escape(title)}' style='flex:1;min-width:22px;"
            f"height:8px;border-radius:3px;background:{col};{low}'></div>")
    return (f"<div style='color:{DIM};font-size:.55rem;letter-spacing:.18em;"
            f"{MONO}margin:6px 0 3px;'>SUB-100K HOT-POTATO MODE · "
            f"<span style='color:{GOLD};'>prime</span> "
            f"<span style='color:{MINT};'>normal</span> "
            f"<span style='color:#FF6B35;'>hot-potato</span> "
            f"<span style='color:{V};'>paper-only</span> "
            f"<span style='color:{RED};'>block</span></div>"
            "<div style='display:flex;gap:3px;'>" + "".join(cells) + "</div>")


# ── Task 5 ──────────────────────────────────────────────────────────────

def signal_gate_reason_html() -> str:
    r = (_rows("signal_gate_state") or [None])[0]
    if not r:
        return _missing("SIGNAL GATE SENSOR",
                        "run services/signal_gate_sensor.py")
    st = str(r.get("state") or "UNKNOWN")
    col = GATE_HEX.get(st, DIM)
    detail = (f"fresh {r.get('fresh_60s')}/{r.get('fresh_300s')}/"
              f"{r.get('fresh_900s')} @60/300/900s · stale "
              f"{r.get('stale_count')} · veto {r.get('vetoed_count')} · "
              f"upd {_age(r.get('updated_at'))}")
    return _rail([_pill(f"SIGNAL GATE {st}", col,
                        title=str(r.get("reason") or ""),
                        pulse=st.startswith("STARVED")),
                  _pill(detail, DIM)],
                 label="SIGNAL GATE DIAGNOSIS")


# ── Task 6 ──────────────────────────────────────────────────────────────

def copytrade_status_html() -> str:
    rows = _rows("copytrade_hot_summary")
    real = [r for r in rows if r.get("wallet_address") != "__NONE__"]
    if not rows:
        return _missing("COPYTRADE",
                        "run AUDIT_COPYTRADE_REGRESSION.py --build-summary")
    now = time.time()
    n24 = sum(r.get("observed_trades_24h") or 0 for r in real)
    latest = max((r.get("last_seen_at") or 0 for r in real), default=0)
    src = real[0].get("source") if real else "EMPTY"
    col = {"HOT_DB": MINT, "ARCHIVE": GOLD, "STALE": "#FF6B35",
           "EMPTY": V}.get(str(src), DIM)
    return _rail([
        _pill(f"WALLETS {len(real)}", col, title=f"dataset source: {src}"),
        _pill(f"OBS 24H {n24}", CYAN),
        _pill(f"LAST OBS {_age(latest) if latest else 'never'}", DIM),
        _pill(f"SOURCE {src}", col),
        _pill("INFLUENCE OFF/OBSERVE — LIVE_BLOCKED", V,
              title="copytrade cannot influence live until proven fresh"),
    ], label="COPYTRADE LANE")


# ── Task 7 ──────────────────────────────────────────────────────────────

def standing_tasklist_html() -> str:
    rows = [r for r in _rows("standing_tasklist", "ORDER BY task_name")
            if not r.get("retired")]
    if not rows:
        return _missing("TASKLIST", "run core/standing_tasklist_contract.py")
    st_col = {"PASS": MINT, "OBSERVE": CYAN, "RESEARCH": CYAN,
              "BUILDING": GOLD, "TESTING": "#FF6B35",
              "NEEDS-YOU": RED, "BLOCKED": RED}
    parts = []
    for r in rows:
        st = str(r.get("state") or "OBSERVE")
        col = st_col.get(st, DIM)
        auto = "" if r.get("autonomous_allowed") else " ⛔"
        parts.append(_pill(
            f"{r['task_name'][:26]} · {r.get('owner','?')[:7]} · {st}{auto}",
            col,
            title=f"next: {r.get('next_action')} | "
                  f"auto={bool(r.get('autonomous_allowed'))} "
                  f"op_needed={bool(r.get('operator_needed'))} | "
                  f"upd {_age(r.get('last_update'))}",
            pulse=(st in ("NEEDS-YOU", "BLOCKED"))))
    return _rail(parts, label="STANDING TASKLIST · ⛔ = operator-gated")


# ── Task 8 ──────────────────────────────────────────────────────────────

def debate_chamber_html() -> str:
    """Real debate_log rows from the HOT db, or honest IDLE. Never blank,
    never decorative fake council text."""
    hot = None
    for p in (ROOT / "sentinuity_matrix.db",
              ROOT / "services" / "sentinuity_matrix.db"):
        if p.exists():
            hot = p
            break
    if hot is None:
        return _missing("DEBATE CHAMBER", "hot DB not found")
    try:
        con = sqlite3.connect(f"file:{hot}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        cols_ = {r[1] for r in con.execute("PRAGMA table_info(debate_log)")}
        rows = []
        if cols_:
            tsc = next((c for c in ("created_at", "timestamp", "ts")
                        if c in cols_), None)
            rows = con.execute(
                f"SELECT * FROM debate_log ORDER BY "
                f"{tsc or 'rowid'} DESC LIMIT 6").fetchall()
        con.close()
    except Exception:
        rows = []
    if not rows:
        return _rail([_pill("DEBATE CHAMBER IDLE — no live debate", V,
                            title="chamber wired, no debate rows; council "
                                  "writer not currently posting")],
                     label="COUNCIL DEBATE CHAMBER")
    parts = []
    for r in rows:
        keys = r.keys()
        agent = next((str(r[k]) for k in ("agent", "speaker", "role",
                                          "agent_name") if k in keys and r[k]),
                     "council")
        msg = next((str(r[k]) for k in ("message", "content", "argument",
                                        "text", "position") if k in keys
                    and r[k]), "")
        col = {"POLARIS": CYAN, "IVARIS": "#FF6B35", "NUGGET": GOLD,
               "FABLE": MINT, "GUARDIAN": RED}.get(agent.upper()[:8], V)
        parts.append(_pill(f"{agent[:9]}: {msg[:52]}", col, title=msg[:300]))
    return _rail(parts, label="COUNCIL DEBATE CHAMBER · latest real rows")


# ── Task 9 ──────────────────────────────────────────────────────────────

def db_lights_html() -> str:
    r = (_rows("db_lights_state") or [None])[0]
    if not r:
        return _missing("DB LIGHTS", "run services/db_lights.py --check")
    col = {"GREEN": MINT, "CYAN_GREEN": CYAN, "AMBER_GOLD": GOLD,
           "RED": RED, "VIOLET": V}.get(str(r.get("colour")), DIM)
    pct = r.get("pct_of_max") or 0
    bar = (f"<span style='display:inline-block;width:90px;height:7px;"
           f"border-radius:4px;background:#111;overflow:hidden;"
           f"vertical-align:middle;'><span style='display:block;height:100%;"
           f"width:{min(100, pct):.0f}%;background:{col};'></span></span>")
    return _rail([
        _pill(f"DB {r.get('db_mb')}MB +{r.get('wal_mb')}WAL", col,
              pulse=str(r.get("colour")) in ("RED", "VIOLET")),
        _pill(f"{pct:.0f}% of 125MB", col),
        _pill(f"{r.get('maintenance_state')}", col),
        _pill(f"ENTRIES {'FROZEN' if r.get('entries_frozen') else 'OPEN'}",
              RED if r.get("entries_frozen") else MINT),
        _pill(f"last prune {_age(r.get('last_prune_at')) if r.get('last_prune_at') else 'never'}",
              DIM, title=str(r.get("next_prune_reason") or "")),
        bar,
    ], label="DB LIGHTS")


# ── Task 10 ─────────────────────────────────────────────────────────────

def live_test_ledger_html() -> str:
    hot = None
    for p in (ROOT / "sentinuity_matrix.db",
              ROOT / "services" / "sentinuity_matrix.db"):
        if p.exists():
            hot = p
            break
    if hot is None:
        return _missing("LIVE TEST", "hot DB not found")
    rows: List[Dict[str, Any]] = []
    budget, per = 30.0, 5.0
    try:
        con = sqlite3.connect(f"file:{hot}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        try:
            rows = [dict(x) for x in con.execute(
                "SELECT * FROM live_mechanism_test_ledger "
                "ORDER BY opened_at DESC LIMIT 10")]
        except Exception:
            pass
        for k, tgt in (("LIVE_TEST_BUDGET_USD", "budget"),
                       ("LIVE_TEST_PER_TRADE_USD", "per")):
            try:
                v = con.execute("SELECT value FROM system_config WHERE key=?",
                                (k,)).fetchone()
                if v and tgt == "budget":
                    budget = float(v[0])
                elif v:
                    per = float(v[0])
            except Exception:
                pass
        con.close()
    except Exception:
        pass
    used = sum(abs(r.get("realized_pnl_usd") or 0) * 0 + per for r in rows)
    gas = [r.get("gas_usd") for r in rows if r.get("gas_usd") is not None]
    slip = [r.get("slippage_pct") for r in rows
            if r.get("slippage_pct") is not None]
    lat = [r.get("exec_ready_to_open_sec") for r in rows
           if r.get("exec_ready_to_open_sec") is not None]
    last = rows[0].get("mechanism_status") if rows else "NO TESTS YET"
    return _rail([
        _pill(f"TEST BUDGET ${used:.0f}/${budget:.0f} (${per:.0f}/trade)",
              GOLD if used < budget else RED),
        _pill(f"AVG GAS {'$'+format(sum(gas)/len(gas), '.3f') if gas else 'n/a'}",
              CYAN),
        _pill(f"AVG SLIP {f'{sum(slip)/len(slip):.2f}%' if slip else 'n/a'}",
              CYAN),
        _pill(f"EXEC LAT {f'{sum(lat)/len(lat):.1f}s' if lat else 'n/a'}",
              MINT),
        _pill(f"LAST {last}", V, pulse=not rows),
    ], label="TINY LIVE MECHANISM TEST · mechanism/gas/slippage only")


# ── streamlit wrappers ──────────────────────────────────────────────────

def _st_md(html_str: str) -> None:
    import streamlit as st
    st.markdown(html_str, unsafe_allow_html=True)


def render_winrate_card():        _st_md(winrate_card_html())
def render_hour_heatmap():        _st_md(hour_heatmap_html())
def render_sub100_row():          _st_md(sub100_row_html())
def render_signal_gate_reason():  _st_md(signal_gate_reason_html())
def render_copytrade_status():    _st_md(copytrade_status_html())
def render_standing_tasklist():   _st_md(standing_tasklist_html())
def render_debate_chamber():      _st_md(debate_chamber_html())
def render_db_lights():           _st_md(db_lights_html())
def render_live_test_ledger():    _st_md(live_test_ledger_html())


def render_all() -> None:
    for fn in (render_winrate_card, render_hour_heatmap, render_sub100_row,
               render_signal_gate_reason, render_db_lights,
               render_copytrade_status, render_debate_chamber,
               render_live_test_ledger, render_standing_tasklist):
        try:
            fn()
        except Exception as e:
            import streamlit as st
            st.caption(f"panel unavailable: {e}")


if __name__ == "__main__":
    parts = [winrate_card_html(), hour_heatmap_html(), sub100_row_html(),
             signal_gate_reason_html(), copytrade_status_html(),
             standing_tasklist_html(), debate_chamber_html(),
             db_lights_html(), live_test_ledger_html()]
    out = ROOT / "golden_state_panels_selftest.html"
    out.write_text("<body style='background:#080B09;'>" +
                   "".join(parts) + "</body>", encoding="utf-8")
    print(f"[selftest] wrote {out} ({sum(len(p) for p in parts)} bytes)")
