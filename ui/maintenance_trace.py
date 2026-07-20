"""
ui/maintenance_trace.py — LIVE MAINTENANCE TRACE (CONSOLIDATION_PASS_20260611)

Makes the hero claim — "Actively repairing pipeline blockages and restoring
flow" — prove itself. A slim, timestamped, monospaced strip of REAL
maintenance actions, merged from:

  1. system_heartbeat notes      (service :: loop state, written by services)
  2. system_guardian log         (RESTARTED / healed events)
  3. execution_engine log        (RECONCILER, MOMENTUM_GATE stats, cleanup)

Read-only. Nothing synthesized: if no events exist in the window, the strip
says so instead of animating fakes.
"""
from __future__ import annotations
import html as _h
import re
import time

from ui.data_sources import (_ro_conn, _tail_lines, _line_ts, _cached,
                             EXEC_LOG, GUARDIAN_LOG)

_GREEN, _GOLD, _EMBER, _RED, _CYAN, _MIST = (
    "#14F195", "#FFD700", "#FF6B35", "#FF073A", "#8EF9FF", "#9DB5A8")

_MAINT_PATTERNS = [
    # (regex on exec/guardian log line, actor, status-color fn)
    (re.compile(r"RECONCILER: (closed \d+ from [\d.]+s gap|\d+ positions checked[^\"]*)"),
     "execution_engine.reconciler", lambda m: _GOLD if "closed" in m.group(1) else _GREEN),
    (re.compile(r"MOMENTUM_GATE_SHADOW_STATS (total=\d+ vetoed=\d+)"),
     "execution_engine.momentum_stats", lambda m: _GREEN),
    (re.compile(r"RESTARTED:\s+(\S+).*?reason=(\S+)"),
     "system_guardian.restart_stale_service", lambda m: _EMBER),
    (re.compile(r"(MOMENTUM_GATE_HARD_DEMOTED snap=\d+)"),
     "execution_engine.momentum_gate", lambda m: _CYAN),
    (re.compile(r"(PAPER_OPENED pos=\d+ snap=\d+)"),
     "execution_engine.open_paper", lambda m: _GREEN),
    (re.compile(r"(LIVE_OPENED pos=\d+ snap=\d+)"),
     "execution_engine.open_live", lambda m: _GOLD),
    (re.compile(r"(ENTRY_TERMINAL_VETO[^\"]{0,40})"),
     "execution_engine.terminal_veto", lambda m: _EMBER),
]


@_cached(ttl=8)
def recent_maintenance_events(window_s: int = 900, limit: int = 8) -> dict:
    """Merge real maintenance events from heartbeat notes + logs."""
    cutoff = time.time() - window_s
    events: list[dict] = []

    # 1. heartbeat notes — each service's own statement of its last action
    con = _ro_conn()
    if con:
        try:
            for r in con.execute(
                "SELECT service_name, note, last_pulse FROM system_heartbeat "
                "WHERE last_pulse >= ? AND COALESCE(note,'') != '' "
                "ORDER BY last_pulse DESC LIMIT 12", (cutoff,)):
                events.append({
                    "ts": float(r["last_pulse"]),
                    "actor": f"{r['service_name']}.heartbeat",
                    "action": str(r["note"])[:72],
                    "color": _GREEN,
                })
        except Exception:
            pass
        finally:
            con.close()

    # 2+3. log-derived maintenance actions
    for path in (EXEC_LOG, GUARDIAN_LOG):
        for line in _tail_lines(path, 4000):
            ts = _line_ts(line)
            if ts is None or ts < cutoff:
                continue
            for rx, actor, color_fn in _MAINT_PATTERNS:
                m = rx.search(line)
                if m:
                    events.append({"ts": ts, "actor": actor,
                                   "action": m.group(1)[:72],
                                   "color": color_fn(m)})
                    break

    events.sort(key=lambda e: e["ts"], reverse=True)
    return {
        "wired": bool(events) or EXEC_LOG.exists(),
        "events": events[:limit],
        "src": ("system_heartbeat notes + system_guardian.log RESTARTED + "
                f"execution_engine.log maintenance markers — {window_s}s window, "
                "read-only, nothing synthesized"),
    }


def render_maintenance_trace(st) -> None:
    data = recent_maintenance_events()
    rows = data.get("events", [])
    if not rows:
        body = ('<div style="color:#5A7060;">// no maintenance events in window '
                '— organism at rest, nothing faked //</div>')
    else:
        lines = []
        for e in rows:
            t = time.strftime("%H:%M:%S", time.localtime(e["ts"]))
            lines.append(
                f'<div style="white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">'
                f'<span style="color:#4A5C50;">[{t}]</span> '
                f'<span style="color:{e["color"]};">{_h.escape(e["actor"])}()</span>'
                f'<span style="color:#666;"> → </span>'
                f'<span style="color:#AAB4C8;">{_h.escape(e["action"])}</span></div>')
        body = "".join(lines)
    st.markdown(
        f'<div style="border:1px solid rgba(255,215,0,0.14);border-radius:10px;'
        f'padding:8px 14px;margin:2px 0 10px;'
        f'background:linear-gradient(120deg,rgba(255,215,0,0.03),rgba(0,0,0,0.45));'
        f'backdrop-filter:blur(6px);font-family:\'Share Tech Mono\',monospace;'
        f'font-size:0.64rem;line-height:1.65;">'
        f'<div style="color:#FFD700;letter-spacing:.18em;font-size:.58rem;'
        f'margin-bottom:4px;">▶ LIVE MAINTENANCE TRACE — the claim, proven</div>'
        f'{body}'
        f'<div style="color:#3A4A40;font-size:.55rem;margin-top:4px;">'
        f'⬡ {_h.escape(data["src"])}</div></div>',
        unsafe_allow_html=True)
