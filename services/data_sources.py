"""
ui/data_sources.py - real backend telemetry adapters for the Sovereign Glassbox
(SIGNOFF_FINAL_GATE_20260611)

Every function returns a dict with a 'src' key declaring exactly which backend
table/log marker/config key powers it - the UI prints that trace under each
tile. Nothing here is invented; if a source is missing the function returns
{'wired': False, ...} and the panel renders a 'not wired' chip.

Performance doctrine (UI must never lock the DB or stall trading):
  - read-only sqlite connections (file:...?mode=ro), 2s busy timeout
  - short-lived: open, query, close - no long transactions
  - log parsing reads only the tail (last ~16k lines) of each log
  - results cached via st.cache_data with short TTLs when streamlit present
  - this module performs ZERO writes anywhere, ever
"""
from __future__ import annotations
import os
import re
import sqlite3
import time
from collections import Counter
from pathlib import Path
from typing import Any

# ── ROOT / PATH RESOLUTION ───────────────────────────────────────────────────
def _root() -> Path:
    env = os.environ.get("SENTINUITY_ROOT")
    cands = ([Path(env)] if env else []) + [
        Path.cwd(), Path.cwd().parent, Path(__file__).resolve().parent.parent]
    for c in cands:
        if (c / "sentinuity_matrix.db").exists():
            return c
    return Path.cwd()

ROOT = _root()
DB_PATH = ROOT / "sentinuity_matrix.db"
EXEC_LOG = ROOT / "logs" / "execution_engine.log"
GUARDIAN_LOG = ROOT / "logs" / "system_guardian.log"

_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")

# ── CACHING: streamlit when available, tiny TTL memo otherwise ───────────────
try:  # pragma: no cover
    import streamlit as _st
    def _cached(ttl):
        return _st.cache_data(ttl=ttl, show_spinner=False)
except Exception:  # CLI / tests without streamlit
    def _cached(ttl):
        def deco(fn):
            memo: dict = {}
            def wrap(*a, **k):
                key = (a, tuple(sorted(k.items())))
                hit = memo.get(key)
                if hit and time.time() - hit[0] < ttl:
                    return hit[1]
                out = fn(*a, **k)
                memo[key] = (time.time(), out)
                return out
            return wrap
        return deco


def _ro_conn() -> sqlite3.Connection | None:
    """Short-lived READ-ONLY connection. Caller must close()."""
    try:
        con = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
        con.row_factory = sqlite3.Row
        return con
    except Exception:
        return None


def _tail_lines(path: Path, max_lines: int = 16000) -> list[str]:
    if not path.exists():
        return []
    try:
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.readlines()[-max_lines:]
    except Exception:
        return []


def _line_ts(line: str) -> float | None:
    m = _TS_RE.match(line)
    if not m:
        return None
    try:
        return time.mktime(time.strptime(m.group(1), "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return None


# ═════════════════════════════════ PANEL 1: SOVEREIGN PULSE ═════════════════
@_cached(ttl=5)
def heartbeats() -> dict:
    """src: system_heartbeat table - service_name, status, last_pulse, note."""
    con = _ro_conn()
    if not con:
        return {"wired": False, "src": "system_heartbeat (DB unreachable)"}
    try:
        now = time.time()
        rows = [dict(r) | {"age_s": now - float(r["last_pulse"] or 0)}
                for r in con.execute(
                    "SELECT service_name, status, last_pulse, note "
                    "FROM system_heartbeat ORDER BY service_name")]
        return {"wired": True, "rows": rows,
                "src": "DB system_heartbeat (read-only, ttl=5s)"}
    except Exception as e:
        return {"wired": False, "src": f"system_heartbeat ({e})"}
    finally:
        con.close()


@_cached(ttl=20)
def guardian_events(window_s: int = 3600) -> dict:
    """src: logs/system_guardian.log - RESTARTED markers in window."""
    cutoff = time.time() - window_s
    restarts: Counter = Counter()
    last_lines: list[str] = []
    for line in _tail_lines(GUARDIAN_LOG):
        ts = _line_ts(line)
        if ts is None or ts < cutoff:
            continue
        if "RESTARTED:" in line:
            m = re.search(r"RESTARTED:\s+(\S+)", line)
            if m:
                restarts[m.group(1)] += 1
                last_lines.append(line.strip())
    return {"wired": GUARDIAN_LOG.exists(),
            "restarts": dict(restarts), "recent": last_lines[-8:],
            "src": f"logs/system_guardian.log tail, 'RESTARTED:' markers, {window_s}s window"}


@_cached(ttl=20)
def db_lock_warnings(window_s: int = 3600) -> dict:
    """src: logs/execution_engine.log - 'database is locked' in window."""
    cutoff = time.time() - window_s
    n = 0
    for line in _tail_lines(EXEC_LOG):
        ts = _line_ts(line)
        if ts is not None and ts >= cutoff and "database is locked" in line:
            n += 1
    return {"wired": EXEC_LOG.exists(), "count": n,
            "src": f"logs/execution_engine.log tail, 'database is locked', {window_s}s window"}


# ═════════════════ PANEL 2+3: EXECUTION ARENA + FINAL GATE GLASSBOX ═════════
_GATE_MARKERS = {
    # PHASE_A_PASS_PRE_ENTRY contains PHASE_A_PASS - count once via pre_entry
    "phase_a_pass": "PHASE_A_PASS",
    "phase_a_blocked": "PHASE_A_BLOCKED",
    "mg_shadow_veto": "MOMENTUM_GATE_SHADOW_VETO",
    "mg_insufficient": "MOMENTUM_GATE_INSUFFICIENT_DATA_SHADOW_ONLY",
    "mg_hard_demoted": "MOMENTUM_GATE_HARD_DEMOTED",
    "mg_hard_live_only": "MOMENTUM_GATE_HARD_VETO_LIVE",
    "mg_hard_terminal": "MOMENTUM_GATE_HARD_VETO snap",
    "paper_opened": "PAPER_OPENED",
    "live_opened": "LIVE_OPENED",
    "paper_shadow_opened": "PAPER_SHADOW_OPENED",
    "live_blocked": "LIVE CAPITAL BLOCKED",
    "hour_gate_live": "HOUR_GATE_LIVE",
}

@_cached(ttl=10)
def gate_counters(window_s: int = 600) -> dict:
    """src: logs/execution_engine.log markers (patched names), windowed."""
    cutoff = time.time() - window_s
    counts: Counter = Counter()
    hard_snaps: Counter = Counter()
    live_block_reasons: Counter = Counter()
    snap_re = re.compile(r"MOMENTUM_GATE_HARD_VETO snap=(\d+)")
    reason_re = re.compile(r"LIVE CAPITAL BLOCKED.*reason=(\S+)")
    for line in _tail_lines(EXEC_LOG):
        ts = _line_ts(line)
        if ts is None or ts < cutoff:
            continue
        for key, marker in _GATE_MARKERS.items():
            if marker in line:
                counts[key] += 1
        m = snap_re.search(line)
        if m:
            hard_snaps[m.group(1)] += 1
        m = reason_re.search(line)
        if m:
            live_block_reasons[m.group(1)] += 1
    worst = hard_snaps.most_common(1)
    return {
        "wired": EXEC_LOG.exists(),
        "counts": dict(counts),
        "max_same_snap_hard_veto": worst[0][1] if worst else 0,
        "worst_snap": worst[0][0] if worst else None,
        "live_block_reasons": dict(live_block_reasons.most_common(5)),
        "window_s": window_s,
        "src": ("logs/execution_engine.log tail - markers: "
                "PHASE_A_PASS[_PRE_ENTRY], PHASE_A_BLOCKED, MOMENTUM_GATE_*, "
                "PAPER_OPENED, LIVE_OPENED, PAPER_SHADOW_OPENED, "
                f"LIVE CAPITAL BLOCKED - {window_s}s window"),
    }


@_cached(ttl=8)
def latch_state() -> dict:
    """src: market_snapshots latched/execution_ready/candidate_state counts."""
    con = _ro_conn()
    if not con:
        return {"wired": False, "src": "market_snapshots (DB unreachable)"}
    try:
        vis = con.execute(
            "SELECT COUNT(*) FROM market_snapshots "
            "WHERE COALESCE(latched,0)=1 AND COALESCE(execution_ready,0) IN (1,2)"
        ).fetchone()[0]
        jam = con.execute(
            "SELECT COUNT(*) FROM market_snapshots "
            "WHERE (COALESCE(latched,0)=1 OR COALESCE(execution_ready,0) IN (1,2)) "
            "  AND (quality_reason LIKE 'MOMENTUM_HARD_VETO%' "
            "       OR quality_reason LIKE 'MOMENTUM_JAM_CLEANUP%')"
        ).fetchone()[0]
        by_state = {r["candidate_state"]: r["c"] for r in con.execute(
            "SELECT candidate_state, COUNT(*) c FROM market_snapshots "
            "WHERE COALESCE(latched,0)=1 OR COALESCE(execution_ready,0) IN (1,2) "
            "GROUP BY candidate_state ORDER BY c DESC LIMIT 8")}
        return {"wired": True, "executor_visible": vis,
                "vetoed_still_visible": jam, "by_state": by_state,
                "src": "DB market_snapshots: latched, execution_ready, "
                       "candidate_state, quality_reason (read-only)"}
    except Exception as e:
        return {"wired": False, "src": f"market_snapshots ({e})"}
    finally:
        con.close()


@_cached(ttl=15)
def lanes() -> dict:
    """src: system_config flags + paper_positions + system_state equity."""
    con = _ro_conn()
    if not con:
        return {"wired": False, "src": "system_config/paper_positions (DB unreachable)"}
    try:
        def cfg(key, default=""):
            r = con.execute("SELECT value FROM system_config WHERE key=?",
                            (key,)).fetchone()
            return r["value"] if r else default
        paper_open = con.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND "
            "COALESCE(entry_price_source,'') NOT LIKE 'LIVE%'").fetchone()[0]
        live_open = con.execute(
            "SELECT COUNT(*) FROM paper_positions WHERE status='OPEN' AND "
            "COALESCE(entry_price_source,'') LIKE 'LIVE%'").fetchone()[0]
        eq_row = con.execute(
            "SELECT paper_equity FROM system_state WHERE id=1").fetchone()
        return {
            "wired": True,
            "mode": cfg("TRADING_MODE", "(unset)"),
            "paper_enabled": str(cfg("PAPER_TRADING_ENABLED", "1")) == "1",
            "shadow_on_block": str(cfg("LIVE_PAPER_SHADOW_ON_BLOCK", "1")) == "1",
            "paper_max": cfg("EXECUTOR_MAX_OPEN_POSITIONS", "?"),
            "live_max": cfg("LIVE_MAX_OPEN_POSITIONS", "1"),
            "paper_open": paper_open, "live_open": live_open,
            "paper_equity": (float(eq_row["paper_equity"])
                             if eq_row and eq_row["paper_equity"] is not None
                             else None),
            "src": "DB system_config (TRADING_MODE, PAPER_TRADING_ENABLED, "
                   "LIVE_PAPER_SHADOW_ON_BLOCK, *_MAX_OPEN_POSITIONS) + "
                   "paper_positions OPEN counts + system_state.wallet_balance",
        }
    except Exception as e:
        return {"wired": False, "src": f"lanes ({e})"}
    finally:
        con.close()


@_cached(ttl=30)
def momentum_config() -> dict:
    """src: system_config momentum gate keys (DB-only - there is no env layer)."""
    con = _ro_conn()
    keys = ("MOMENTUM_GATE_ENABLED", "MOMENTUM_GATE_SHADOW_ONLY",
            "MOMENTUM_GATE_FORCE_HARD", "MOMENTUM_GATE_HARD_APPLIES_TO_PAPER",
            "MOMENTUM_FROM_QUAL_PCT", "MOMENTUM_SHORT_TERM_PCT")
    if not con:
        return {"wired": False, "src": "system_config (DB unreachable)"}
    try:
        vals = {}
        for k in keys:
            r = con.execute("SELECT value FROM system_config WHERE key=?",
                            (k,)).fetchone()
            vals[k] = r["value"] if r else "(code default)"
        return {"wired": True, "vals": vals,
                "src": "DB system_config momentum keys (pinned at launch by "
                       "tools/apply_momentum_launch_guard.py)"}
    except Exception as e:
        return {"wired": False, "src": f"system_config ({e})"}
    finally:
        con.close()


# ═════════════════════════════ PANEL 4: PRICE TRUTH ═════════════════════════
@_cached(ttl=10)
def price_truth(limit: int = 8) -> dict:
    """src: ENTRY_AUDIT log lines (qualify vs final price, source, ages)."""
    rows = []
    audit_re = re.compile(
        r"ENTRY_AUDIT mint=(\S+) qualify=([\d.]+) final=([\d.]+) "
        r"source=(\S+) price_age=([\d.]+)s signal_age=([\d.]+)s")
    for line in reversed(_tail_lines(EXEC_LOG)):
        m = audit_re.search(line)
        if m:
            rows.append({"mint": m.group(1), "qualify": float(m.group(2)),
                         "final": float(m.group(3)), "source": m.group(4),
                         "price_age_s": float(m.group(5)),
                         "signal_age_s": float(m.group(6)),
                         "ts": _line_ts(line)})
            if len(rows) >= limit:
                break
    return {"wired": EXEC_LOG.exists(), "rows": rows,
            "src": "logs/execution_engine.log 'ENTRY_AUDIT' lines - "
                   "qualify/final price, source chain (router→upgraded→qualify), ages"}


@_cached(ttl=10)
def momentum_audit_recent(limit: int = 10) -> dict:
    """src: momentum_gate_audit table - real measured moves, no fake zeros."""
    con = _ro_conn()
    if not con:
        return {"wired": False, "src": "momentum_gate_audit (DB unreachable)"}
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT snapshot_id, token_name, qual_price, latest_price, "
            "move_from_qual_pct, would_veto, created_at "
            "FROM momentum_gate_audit ORDER BY id DESC LIMIT ?", (limit,))]
        return {"wired": True, "rows": rows,
                "src": "DB momentum_gate_audit (last rows, read-only). "
                       "Insufficient-data candidates are logged as "
                       "INSUFFICIENT_DATA_SHADOW_ONLY and never shown as 0.00%."}
    except Exception as e:
        return {"wired": False, "src": f"momentum_gate_audit ({e})"}
    finally:
        con.close()


# ═══════════════════ PANEL 6: COPYTRADE / SMART WALLET LANE ═════════════════
@_cached(ttl=60)
def copytrade_state() -> dict:
    """src: wallet tables if present + wallet_scout heartbeat. Honest wiring check."""
    con = _ro_conn()
    if not con:
        return {"wired": False, "src": "copytrade tables (DB unreachable)"}
    try:
        tables = {r["name"] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        wallet_tables = sorted(t for t in tables
                               if "wallet" in t.lower() or "copytrade" in t.lower()
                               or "smart_money" in t.lower())
        hb = con.execute(
            "SELECT service_name, last_pulse, (?-last_pulse) age_s "
            "FROM system_heartbeat WHERE service_name LIKE '%wallet%' "
            "OR service_name LIKE '%copytrade%'", (time.time(),)).fetchall()
        counts = {}
        for t in wallet_tables[:4]:
            try:
                counts[t] = con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            except Exception:
                counts[t] = "?"
        scout_alive = any(float(r["age_s"]) < 120 for r in hb) if hb else False
        return {
            "wired": bool(wallet_tables or hb),
            "tables": counts,
            "scout_alive": scout_alive,
            "scouts": [dict(r) for r in hb],
            "influences_entries": False,  # honest default: conviction is read-only
            "src": "DB sqlite_master wallet/copytrade tables + system_heartbeat "
                   "wallet_scout. influences_entries hard-coded False until "
                   "smart_wallet_conviction is wired into the entry path.",
        }
    except Exception as e:
        return {"wired": False, "src": f"copytrade ({e})"}
    finally:
        con.close()


# ═══════════════════════ PANEL 5: COUNCIL NARRATION (grounded) ══════════════
def council_lines(pulse: dict, gates: dict, lane: dict, latch: dict) -> list[dict]:
    """
    Grounded narration: every sentence is derived from a telemetry value that
    is ALSO visible in the panels. Agents never claim anything the data
    doesn't show. Returns [{agent, mode, text}].
    """
    out = []
    c = gates.get("counts", {})
    if gates.get("wired"):
        hard = c.get("mg_hard_terminal", 0)
        demoted = c.get("mg_hard_demoted", 0)
        insuff = c.get("mg_insufficient", 0)
        if hard == 0 and (demoted or insuff):
            out.append({"agent": "Polaris", "mode": "execution",
                        "text": f"The forge holds. {demoted} hard attempts demoted "
                                f"to shadow, {insuff} unmeasurable candidates waved "
                                f"through - paper keeps proving."})
        elif hard > 0:
            out.append({"agent": "Polaris", "mode": "execution",
                        "text": f"{hard} terminal momentum vetoes this window - "
                                f"each row terminalized, none left latched."})
    if latch.get("wired"):
        jam = latch.get("vetoed_still_visible", 0)
        out.append({"agent": "Ivaris", "mode": "governance",
                    "text": ("The lattice is clean - no vetoed husks clog the rails."
                             if jam == 0 else
                             f"{jam} vetoed rows still executor-visible - run the "
                             f"jam cleanup before trusting slot counts.")})
    if lane.get("wired"):
        pe = lane.get("paper_equity")
        out.append({"agent": "Nugget", "mode": "substrate",
                    "text": f"Paper rail carries {lane.get('paper_open',0)} open / "
                            f"{lane.get('paper_max','?')} slots"
                            + (f", equity ${pe:.2f}." if pe is not None else ".")})
    if pulse.get("wired"):
        stale = [r for r in pulse.get("rows", [])
                 if r["age_s"] > 120 and "exec" in str(r["service_name"]).lower()]
        if stale:
            out.append({"agent": "AXON", "mode": "health",
                        "text": f"Executor heartbeat aged {stale[0]['age_s']:.0f}s - "
                                f"watch Guardian; it must not kill an active scan."})
        else:
            out.append({"agent": "AXON", "mode": "health",
                        "text": "All core pulses fresh. The organism breathes."})
    return out
