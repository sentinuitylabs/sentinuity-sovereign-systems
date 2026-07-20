#!/usr/bin/env python3
"""
Sentinuity Sovereign Gate Map
=============================
Read-only diagnostic spine shared by:
  - core/sovereign_doctor.py --once
  - ui/auto_debugger_panel.py

This module does not trade, does not mutate trading gates, and does not invent data.
It only reads SQLite/runtime state and returns a structured gate map that makes
paper, live, oracle, candidate expiry, and copytrade states impossible to confuse.
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
import time
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None  # type: ignore

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = Path(os.getenv("SENTINUITY_DB_PATH", ROOT / "sentinuity_matrix.db"))
INTEL_DB_PATH = Path(os.getenv("SENTINUITY_INTEL_DB_PATH", ROOT / "sentinuity_intelligence.db"))
ORACLE_GATE_SEC_DEFAULT = 300.0
NOW = lambda: time.time()

TERMINAL_STATES = {
    "expired", "expired_stale", "vetoed", "rejected", "executed", "dead", "closed", "mtm"
}


def _connect(path: Path) -> Optional[sqlite3.Connection]:
    try:
        if not path.exists():
            return None
        conn = sqlite3.connect(str(path), timeout=2.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only=ON")
        conn.execute("PRAGMA busy_timeout=1500")
        return conn
    except Exception:
        return None


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    try:
        return conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)
        ).fetchone() is not None
    except Exception:
        return False


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    if not _table_exists(conn, table):
        return set()
    try:
        return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _scalar(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = (), default: Any = None) -> Any:
    try:
        row = conn.execute(sql, tuple(params)).fetchone()
        if row is None:
            return default
        return row[0]
    except Exception:
        return default


def _rows(conn: sqlite3.Connection, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    try:
        return list(conn.execute(sql, tuple(params)).fetchall())
    except Exception:
        return []


def _cfg(conn: Optional[sqlite3.Connection], key: str, default: Any = None) -> Any:
    if conn is None or not _table_exists(conn, "system_config"):
        return default
    val = _scalar(conn, "SELECT value FROM system_config WHERE key=? LIMIT 1", (key,), default)
    return default if val is None else val


def _float(v: Any, default: float = 0.0) -> float:
    try:
        f = float(v)
        if f != f or f in (float("inf"), float("-inf")):
            return default
        return f
    except Exception:
        return default


def _int(v: Any, default: int = 0) -> int:
    try:
        return int(float(v))
    except Exception:
        return default


def _truthy(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on", "live"}


def _parse_hour_list(v: Any) -> list[int]:
    if v is None:
        return []
    out: list[int] = []
    for part in re.split(r"[^0-9]+", str(v)):
        if part.strip().isdigit():
            h = int(part)
            if 0 <= h <= 23 and h not in out:
                out.append(h)
    return sorted(out)


def _age_from_ts(ts: Any, now: Optional[float] = None) -> Optional[float]:
    now = NOW() if now is None else now
    f = _float(ts, 0.0)
    if f <= 0:
        return None
    # Some tables store ms. Detect obvious millisecond values.
    if f > 10_000_000_000:
        f = f / 1000.0
    age = now - f
    if age < -86400:
        return None
    return max(0.0, age)


def _age_expr(cols: set[str]) -> str:
    # Newest operational timestamp first, so fresh qualification/latch does not get
    # misread as old token birth/discovery age.
    candidates = [
        "execution_ready_at", "latched_at", "qualified_at", "updated_at",
        "price_updated_at", "first_seen_at", "created_at", "timestamp"
    ]
    present = [c for c in candidates if c in cols]
    if not present:
        return "0"
    return "MAX(" + ",".join([f"COALESCE({c},0)" for c in present]) + ")"


def _safe_count(conn: Optional[sqlite3.Connection], table: str, where: str = "1=1", params: Iterable[Any] = ()) -> int:
    if conn is None or not _table_exists(conn, table):
        return 0
    return _int(_scalar(conn, f"SELECT COUNT(*) FROM {table} WHERE {where}", params, 0), 0)


def _heartbeat(conn: Optional[sqlite3.Connection], names: list[str]) -> dict[str, Any]:
    if conn is None or not _table_exists(conn, "system_heartbeat"):
        return {"state": "unknown", "age_sec": None, "note": "no system_heartbeat table"}
    cols = _cols(conn, "system_heartbeat")
    service_col = "service_name" if "service_name" in cols else ("name" if "name" in cols else None)
    ts_col = "last_seen" if "last_seen" in cols else ("updated_at" if "updated_at" in cols else ("ts" if "ts" in cols else None))
    if not service_col:
        return {"state": "unknown", "age_sec": None, "note": "heartbeat schema unknown"}
    note_col = "note" if "note" in cols else ("message" if "message" in cols else None)
    status_col = "status" if "status" in cols else None
    q_names = [n.lower() for n in names]
    ph = ",".join("?" for _ in q_names)
    sel = [service_col]
    if ts_col: sel.append(ts_col)
    if status_col: sel.append(status_col)
    if note_col: sel.append(note_col)
    row = None
    try:
        row = conn.execute(
            f"SELECT {','.join(sel)} FROM system_heartbeat WHERE lower({service_col}) IN ({ph}) ORDER BY COALESCE({ts_col or '0'},0) DESC LIMIT 1",
            q_names,
        ).fetchone()
    except Exception:
        row = None
    if not row:
        return {"state": "dead", "age_sec": None, "note": "no heartbeat"}
    age = _age_from_ts(row[ts_col]) if ts_col else None
    state = "fresh" if age is not None and age <= 45 else ("stale" if age is not None else "unknown")
    return {
        "state": state,
        "age_sec": age,
        "status": str(row[status_col]) if status_col else "",
        "note": str(row[note_col]) if note_col else "",
    }


def _oracle_state(conn: Optional[sqlite3.Connection], intel: Optional[sqlite3.Connection], now: float) -> dict[str, Any]:
    gate = _float(_cfg(conn, "ORACLE_LIVENESS_GATE_SEC", ORACLE_GATE_SEC_DEFAULT), ORACLE_GATE_SEC_DEFAULT)
    age = None
    source = "none"
    if intel is not None and _table_exists(intel, "mtm_ticks"):
        icols = _cols(intel, "mtm_ticks")
        # Prefer obvious timestamp columns; support ts_ms separately.
        if "ts_ms" in icols:
            ts = _scalar(intel, "SELECT MAX(ts_ms) FROM mtm_ticks", default=None)
            age = _age_from_ts(ts, now)
            source = "sentinuity_intelligence.mtm_ticks.ts_ms"
        else:
            ts_col = next((c for c in ["timestamp", "ts", "created_at", "updated_at"] if c in icols), None)
            if ts_col:
                ts = _scalar(intel, f"SELECT MAX({ts_col}) FROM mtm_ticks", default=None)
                age = _age_from_ts(ts, now)
                source = f"sentinuity_intelligence.mtm_ticks.{ts_col}"
    if age is None and conn is not None and _table_exists(conn, "market_snapshots"):
        mcols = _cols(conn, "market_snapshots")
        if "price_updated_at" in mcols:
            ts = _scalar(conn, "SELECT MAX(price_updated_at) FROM market_snapshots WHERE COALESCE(observed_price,0)>0", default=None)
            age = _age_from_ts(ts, now)
            source = "market_snapshots.price_updated_at"
    if age is None:
        return {"state": "unknown", "age_sec": None, "gate_sec": gate, "block_reason": "NO_ORACLE_TICK_SOURCE", "source": source}
    if age > gate:
        return {"state": "stale", "age_sec": age, "gate_sec": gate, "block_reason": f"BLOCKED_ORACLE_STALE age={age:.1f}s gate={gate:.0f}s", "source": source}
    return {"state": "fresh", "age_sec": age, "gate_sec": gate, "block_reason": None, "source": source}


def _candidate_counts(conn: Optional[sqlite3.Connection], now: float, window_sec: int = 600) -> dict[str, Any]:
    base = {
        "discovered_10m": 0, "priced_10m": 0, "qualified_10m": 0,
        "latched_10m": 0, "expired_10m": 0, "vetoed_10m": 0,
        "execution_ready_10m": 0, "top_veto_reasons": [],
        "median_signal_age_at_qualification_sec": None,
        "max_signal_age_at_qualification_sec": None,
        "terminal_not_counted_as_stuck": True,
    }
    if conn is None or not _table_exists(conn, "market_snapshots"):
        return base
    cols = _cols(conn, "market_snapshots")
    ts_expr = _age_expr(cols)
    cutoff = now - window_sec

    def count(extra: str) -> int:
        return _safe_count(conn, "market_snapshots", f"({ts_expr}) >= ? AND ({extra})", (cutoff,))

    base["discovered_10m"] = count("1=1")
    if "price_status" in cols:
        base["priced_10m"] = count("LOWER(COALESCE(price_status,''))='priced' OR COALESCE(observed_price,0)>0")
    elif "observed_price" in cols:
        base["priced_10m"] = count("COALESCE(observed_price,0)>0")
    if "quality_status" in cols:
        base["qualified_10m"] = count("LOWER(COALESCE(quality_status,''))='qualified'")
    if "latched" in cols:
        base["latched_10m"] = count("COALESCE(latched,0)=1")
    if "execution_ready" in cols:
        base["execution_ready_10m"] = count("COALESCE(execution_ready,0)=1")
    if "candidate_state" in cols:
        base["expired_10m"] = count("LOWER(COALESCE(candidate_state,'')) LIKE 'expired%'")
        base["vetoed_10m"] = count("LOWER(COALESCE(candidate_state,'')) IN ('vetoed','rejected')")
    elif "quality_status" in cols:
        base["vetoed_10m"] = count("LOWER(COALESCE(quality_status,''))='rejected'")

    # Top reasons: from quality_reason/source_note if present. Do not classify terminal rows as stuck.
    reason_col = "quality_reason" if "quality_reason" in cols else ("source_note" if "source_note" in cols else None)
    if reason_col:
        rows = _rows(conn, f"""
            SELECT COALESCE({reason_col},'UNKNOWN') AS reason, COUNT(*) AS n
            FROM market_snapshots
            WHERE ({ts_expr}) >= ?
              AND COALESCE({reason_col},'') NOT IN ('','OK')
            GROUP BY COALESCE({reason_col},'UNKNOWN')
            ORDER BY n DESC
            LIMIT 8
        """, (cutoff,))
        base["top_veto_reasons"] = [{"reason": str(r["reason"])[:90], "count": int(r["n"])} for r in rows]

    # Signal-age stats for qualified rows if fields exist.
    signal_age_cols = [c for c in ["signal_age_seconds", "token_age_seconds"] if c in cols]
    if signal_age_cols:
        sig_col = signal_age_cols[0]
        rows = _rows(conn, f"""
            SELECT {sig_col} AS age
            FROM market_snapshots
            WHERE ({ts_expr}) >= ?
              AND LOWER(COALESCE(quality_status,''))='qualified'
              AND {sig_col} IS NOT NULL
            ORDER BY {sig_col}
            LIMIT 500
        """, (cutoff,))
        vals = sorted([_float(r["age"], -1) for r in rows if _float(r["age"], -1) >= 0])
        if vals:
            mid = len(vals) // 2
            base["median_signal_age_at_qualification_sec"] = vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2
            base["max_signal_age_at_qualification_sec"] = max(vals)
    return base


def _position_state(conn: Optional[sqlite3.Connection], now: float, candidates: dict[str, Any], oracle: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    paper = {
        "state": "unknown", "open_positions": 0, "cap": None,
        "last_fill_age_sec": None, "last_block_reason": None,
    }
    live = {
        "state": "unknown", "hour_gate_enabled": False, "current_utc_hour": datetime.now(timezone.utc).hour,
        "current_melbourne_hour": None, "blocked_hours": [], "next_open_utc_hour": None,
        "next_open_melbourne": None, "wallet_balance": None, "flat_size": None,
        "open_positions": 0, "last_fill_age_sec": None, "last_block_reason": None,
    }
    if ZoneInfo:
        try:
            live["current_melbourne_hour"] = datetime.now(ZoneInfo("Australia/Melbourne")).hour
        except Exception:
            pass
    if conn is None:
        paper["state"] = live["state"] = "error"
        paper["last_block_reason"] = live["last_block_reason"] = "DB_UNAVAILABLE"
        return paper, live

    pp_cols = _cols(conn, "paper_positions") if _table_exists(conn, "paper_positions") else set()
    if pp_cols:
        status_col = "status" if "status" in pp_cols else None
        if status_col:
            paper["open_positions"] = _safe_count(conn, "paper_positions", "UPPER(COALESCE(status,''))='OPEN'")
        else:
            paper["open_positions"] = _safe_count(conn, "paper_positions")
        # Last fill from positions or executions.
        opened_col = "opened_at" if "opened_at" in pp_cols else ("created_at" if "created_at" in pp_cols else None)
        if opened_col:
            paper["last_fill_age_sec"] = _age_from_ts(_scalar(conn, f"SELECT MAX({opened_col}) FROM paper_positions", default=None), now)
    if _table_exists(conn, "paper_executions"):
        ex_cols = _cols(conn, "paper_executions")
        ts_col = next((c for c in ["executed_at", "created_at", "timestamp", "ts"] if c in ex_cols), None)
        if ts_col:
            age2 = _age_from_ts(_scalar(conn, f"SELECT MAX({ts_col}) FROM paper_executions", default=None), now)
            if age2 is not None and (paper["last_fill_age_sec"] is None or age2 < paper["last_fill_age_sec"]):
                paper["last_fill_age_sec"] = age2

    cap_keys = ["PAPER_MAX_OPEN_POSITIONS", "MAX_OPEN_POSITIONS"]
    for k in cap_keys:
        v = _cfg(conn, k, None)
        if v is not None:
            paper["cap"] = _int(v, 0) or None
            break
    if paper["cap"] is None:
        paper["cap"] = 3

    if paper["cap"] and paper["open_positions"] >= int(paper["cap"]):
        paper["state"] = "blocked"
        paper["last_block_reason"] = f"PAPER_MAX_OPEN_POSITIONS cap reached: {paper['open_positions']}/{paper['cap']}"
    elif oracle.get("state") == "stale" and paper["open_positions"] > 0:
        # This mirrors the historical executor pattern: stale oracle matters most when open positions need MTM.
        paper["state"] = "blocked"
        paper["last_block_reason"] = oracle.get("block_reason")
    elif candidates.get("qualified_10m", 0) > 0 and candidates.get("expired_10m", 0) > candidates.get("latched_10m", 0):
        paper["state"] = "starved"
        paper["last_block_reason"] = "candidates expiring before claim/fill"
    elif candidates.get("vetoed_10m", 0) > candidates.get("qualified_10m", 0):
        paper["state"] = "starved"
        paper["last_block_reason"] = "quality vetoes dominate candidate flow"
    else:
        paper["state"] = "open"
        paper["last_block_reason"] = None

    trading_mode = str(_cfg(conn, "TRADING_MODE", os.getenv("TRADING_MODE", "paper"))).strip().lower()
    live_enabled = trading_mode == "live" or str(_cfg(conn, "LIVE_TRADING_ENABLED", os.getenv("LIVE_TRADING_ENABLED", "0"))).strip() == "1"
    live["wallet_balance"] = _float(_cfg(conn, "LIVE_WALLET_USD", os.getenv("LIVE_WALLET_USD", "0")), 0.0)
    # Prefer flat live sizing keys seen in recent logs; fallback to env.
    live["flat_size"] = _float(_cfg(conn, "SIZING_LIVE_FLAT", os.getenv("SIZING_LIVE_FLAT", "0")), 0.0) or _float(_cfg(conn, "LIVE_POSITION_SIZE_USD", os.getenv("LIVE_POSITION_SIZE_USD", "0")), 0.0)
    live["hour_gate_enabled"] = _truthy(_cfg(conn, "HOUR_GATE_ENABLED", os.getenv("HOUR_GATE_ENABLED", "0")))
    live["blocked_hours"] = _parse_hour_list(_cfg(conn, "HOUR_GATE_BLOCK_UTC", os.getenv("HOUR_GATE_BLOCK_UTC", "")))
    current_h = int(live["current_utc_hour"])
    if live["hour_gate_enabled"]:
        for offset in range(1, 25):
            h = (current_h + offset) % 24
            if h not in live["blocked_hours"]:
                live["next_open_utc_hour"] = h
                try:
                    next_utc = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0) + timedelta(hours=offset)
                    if ZoneInfo:
                        live["next_open_melbourne"] = next_utc.astimezone(ZoneInfo("Australia/Melbourne")).strftime("%Y-%m-%d %H:%M")
                    else:
                        live["next_open_melbourne"] = None
                except Exception:
                    pass
                break
    if not live_enabled:
        live["state"] = "blocked"
        live["last_block_reason"] = f"TRADING_MODE={trading_mode}; live not armed"
    elif live["hour_gate_enabled"] and current_h in live["blocked_hours"]:
        live["state"] = "hour_gated"
        live["last_block_reason"] = f"HOUR_GATE_LIVE utc_hour={current_h}; normal live entries suppressed; Mode B still allowed"
    elif oracle.get("state") == "stale" and paper["open_positions"] > 0:
        live["state"] = "blocked"
        live["last_block_reason"] = oracle.get("block_reason")
    elif live["flat_size"] and live["wallet_balance"] is not None and float(live["wallet_balance"] or 0) < float(live["flat_size"]):
        live["state"] = "wallet_limited"
        live["last_block_reason"] = f"wallet ${live['wallet_balance']:.2f} < flat size ${live['flat_size']:.2f}"
    else:
        live["state"] = "open"
        live["last_block_reason"] = None
    return paper, live


def _copytrade_state(conn: Optional[sqlite3.Connection], now: float) -> dict[str, Any]:
    out = {
        "state": "unknown", "last_scan_age_sec": None, "wallets_watched": 0,
        "signals_found_10m": 0, "signals_promoted_10m": 0, "last_reason": None,
        "heartbeat_note": None, "source_status": "unknown",
    }
    if conn is None:
        out.update({"state": "error", "last_reason": "DB_UNAVAILABLE"})
        return out
    hb = _heartbeat(conn, ["copytrade_shadow_scanner", "copytrade", "wallet_scout"])
    out["last_scan_age_sec"] = hb.get("age_sec")
    out["heartbeat_note"] = hb.get("note")
    if _table_exists(conn, "watched_wallets"):
        out["wallets_watched"] = _safe_count(conn, "watched_wallets")
    elif _table_exists(conn, "smart_wallets"):
        out["wallets_watched"] = _safe_count(conn, "smart_wallets")
    elif _table_exists(conn, "wallet_intelligence"):
        out["wallets_watched"] = _safe_count(conn, "wallet_intelligence")
    # Copy signal event tables vary. Probe common options.
    cutoff = now - 600
    for table in ["copytrade_shadow_events", "copytrade_signals", "smart_wallet_signals"]:
        if not _table_exists(conn, table):
            continue
        cols = _cols(conn, table)
        ts_col = next((c for c in ["created_at", "timestamp", "ts", "detected_at"] if c in cols), None)
        promoted_col = next((c for c in ["promoted", "promoted_to_market", "is_promoted"] if c in cols), None)
        if ts_col:
            out["signals_found_10m"] += _safe_count(conn, table, f"COALESCE({ts_col},0)>=?", (cutoff,))
            if promoted_col:
                out["signals_promoted_10m"] += _safe_count(conn, table, f"COALESCE({ts_col},0)>=? AND COALESCE({promoted_col},0)=1", (cutoff,))
    if out["last_scan_age_sec"] is None:
        out["state"] = "dead"
        out["last_reason"] = "no scanner heartbeat"
    elif out["last_scan_age_sec"] > 180:
        out["state"] = "dead"
        out["last_reason"] = f"scanner heartbeat stale {out['last_scan_age_sec']:.0f}s"
    elif out["signals_found_10m"] > 0 and out["signals_promoted_10m"] == 0:
        out["state"] = "shadow_only"
        out["last_reason"] = "signals observed but not promoted live"
    elif out["signals_found_10m"] > 0:
        out["state"] = "active"
        out["last_reason"] = "signals observed"
    elif out["wallets_watched"] == 0:
        out["state"] = "idle"
        out["last_reason"] = "no watched wallets configured"
    else:
        out["state"] = "idle"
        out["last_reason"] = "no tracked wallet movement in last 10m"
    if out["heartbeat_note"]:
        out["last_reason"] = str(out["heartbeat_note"])[:180]
    return out


def _primary_blocker(paper: dict[str, Any], live: dict[str, Any], copy: dict[str, Any], oracle: dict[str, Any], cand: dict[str, Any]) -> tuple[str, str, str]:
    if oracle.get("state") == "stale" and (paper.get("open_positions", 0) or live.get("state") != "blocked"):
        return (
            f"Oracle stale ({oracle.get('age_sec'):.1f}s > {oracle.get('gate_sec'):.0f}s)",
            "Open positions require fresh MTM ticks.",
            "Restore ws_price_oracle/mtm_ticks before touching trade gates.",
        )
    if live.get("state") == "hour_gated":
        nxt = live.get("next_open_melbourne") or f"UTC hour {live.get('next_open_utc_hour')}"
        return (
            "Live normal entries suppressed by configured HOUR_GATE_LIVE.",
            f"Paper is separate: {paper.get('state')} — {paper.get('last_block_reason') or 'no paper blocker'}.",
            f"Wait for open window ({nxt}) or explicitly change HOUR_GATE_BLOCK_UTC.",
        )
    if paper.get("state") == "blocked":
        return (
            f"Paper blocked: {paper.get('last_block_reason')}",
            f"Live state: {live.get('state')} — {live.get('last_block_reason') or 'no live blocker'}.",
            "Resolve the paper blocker before chasing copytrade/UI symptoms.",
        )
    if cand.get("qualified_10m", 0) > 0 and cand.get("expired_10m", 0) > max(1, cand.get("latched_10m", 0)):
        return (
            "Qualified candidates are expiring before execution claim/fill.",
            "Likely claim timing, signal-age latency, or configured gate window pressure.",
            "Inspect executor candidate pickup and signal_age/qualified_at timestamps next.",
        )
    if cand.get("vetoed_10m", 0) > cand.get("qualified_10m", 0) and cand.get("vetoed_10m", 0) > 0:
        reason = cand.get("top_veto_reasons")[:1]
        reason_txt = reason[0]["reason"] if reason else "quality vetoes"
        return (
            f"Quality vetoes dominate: {reason_txt}",
            "Pipeline is alive but filters are rejecting current market flow.",
            "Do not loosen live gates; audit the top veto reason and signal latency.",
        )
    if copy.get("state") in {"dead", "error"}:
        return (
            f"Copytrade visibility lane {copy.get('state')}: {copy.get('last_reason')}",
            "Core trade flow may still be independent of copytrade.",
            "Start/fix copytrade_shadow_scanner if copytrade visibility is required.",
        )
    return (
        "No single hard blocker found in read-only gate map.",
        "If no fills continue, compare executor logs against current candidate IDs.",
        "Run tail on execution_engine.log and inspect last block reason.",
    )


def collect_gate_map(db_path: Optional[str | Path] = None, intel_db_path: Optional[str | Path] = None) -> dict[str, Any]:
    now = NOW()
    dbp = Path(db_path) if db_path else DB_PATH
    idbp = Path(intel_db_path) if intel_db_path else INTEL_DB_PATH
    conn = _connect(dbp)
    intel = _connect(idbp)
    try:
        oracle = _oracle_state(conn, intel, now)
        candidates = _candidate_counts(conn, now, 600)
        paper, live = _position_state(conn, now, candidates, oracle)
        copy = _copytrade_state(conn, now)
        primary, secondary, action = _primary_blocker(paper, live, copy, oracle, candidates)
        hbs = {
            "discovery": _heartbeat(conn, ["pump_monitor", "scout", "ingest_pipeline"]),
            "market_intelligence": _heartbeat(conn, ["market_intelligence", "signal_engine"]),
            "execution_engine": _heartbeat(conn, ["execution_engine", "paper_executor"]),
            "oracle": _heartbeat(conn, ["ws_price_oracle", "price_oracle", "oracle"]),
        }
        return {
            "schema_version": "SENTINUITY_GATE_MAP_V1",
            "generated_at": now,
            "db_path": str(dbp),
            "intel_db_path": str(idbp),
            "oracle": oracle,
            "paper": paper,
            "live": live,
            "candidates": candidates,
            "copytrade": copy,
            "heartbeats": hbs,
            "primary_blocker": primary,
            "secondary_pressure": secondary,
            "safe_next_action": action,
        }
    finally:
        try:
            if conn: conn.close()
        except Exception:
            pass
        try:
            if intel: intel.close()
        except Exception:
            pass


def _fmt_age(v: Any) -> str:
    if v is None:
        return "unknown"
    try:
        s = float(v)
    except Exception:
        return "unknown"
    if s < 60:
        return f"{s:.1f}s"
    if s < 3600:
        return f"{s/60:.1f}m"
    return f"{s/3600:.1f}h"


def final_gate_verdict_text(g: dict[str, Any]) -> str:
    p = g.get("paper", {})
    l = g.get("live", {})
    c = g.get("copytrade", {})
    o = g.get("oracle", {})
    return "\n".join([
        "FINAL GATE VERDICT",
        f"PAPER: {p.get('state','unknown').upper()} — {p.get('last_block_reason') or 'no confirmed paper blocker'}",
        f"LIVE: {l.get('state','unknown').upper()} — {l.get('last_block_reason') or 'no confirmed live blocker'}",
        f"COPYTRADE: {c.get('state','unknown').upper()} — {c.get('last_reason') or 'no confirmed copytrade reason'}",
        f"ORACLE: {o.get('state','unknown').upper()} — age={_fmt_age(o.get('age_sec'))}; {o.get('block_reason') or 'fresh/no block'}",
        f"PRIMARY CURRENT BLOCKER: {g.get('primary_blocker')}",
        f"SECONDARY PRESSURE: {g.get('secondary_pressure')}",
        f"SAFE NEXT ACTION: {g.get('safe_next_action')}",
    ])


def main() -> int:
    import argparse
    ap = argparse.ArgumentParser(description="Sentinuity read-only gate map")
    ap.add_argument("--json", action="store_true", help="print structured JSON")
    ap.add_argument("--db", default=None)
    ap.add_argument("--intel-db", default=None)
    args = ap.parse_args()
    g = collect_gate_map(args.db, args.intel_db)
    if args.json:
        print(json.dumps(g, indent=2, sort_keys=True, default=str))
    else:
        print(final_gate_verdict_text(g))
        cand = g.get("candidates", {})
        print("\nCANDIDATE FLOW 10M")
        for k in ["discovered_10m", "priced_10m", "qualified_10m", "latched_10m", "execution_ready_10m", "expired_10m", "vetoed_10m"]:
            print(f"  {k}: {cand.get(k, 0)}")
        reasons = cand.get("top_veto_reasons") or []
        if reasons:
            print("\nTOP REASONS")
            for r in reasons[:6]:
                print(f"  {r.get('count',0):>4}  {r.get('reason')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
