# SENTINUITY_WORLD_STATE_20260712
# Read-only world-state contract for the Sentinuity Living World (World tab).
#
# DOCTRINE (sign-off directive 12_07_26):
#   - OBSERVATIONAL ONLY. This module NEVER writes to the trading database,
#     never holds a write lock, never mutates entry/exit/runner/risk logic.
#   - Schema-tolerant: every table/column is probed before use. A missing
#     table degrades to an empty section, never an exception.
#   - Single cheap snapshot per call. No per-frame queries: the World HTML
#     animates client-side from this snapshot; Streamlit reruns refresh it.
#
# Consumed by: ui/sovereign_hub.py -> ui.state_contract.load_world_state
# Rendered by: ui/sovereign_world.html via window.applySwState(state)
#
# Contract keys (all optional for the renderer):
#   generated_at, db_path, db_ok, mode {paper,live,label}
#   system {health_pct, oracle_online, fresh_prices, qualified_signals,
#           open_positions, latest_signal_age_s, stale, blocked, services}
#   positions [ {id, token, mint, pnl_pct, pnl_usd, size_usd, opened_at,
#                age_s, status} ]           (OPEN only)
#   recent_closes [ {id, token, pnl_usd, pnl_pct, closed_at} ]
#   realized {net_24h, count_24h}
#   events [ {ts, stage, message, token} ]  (newest first, capped)
#   agents [ {agent_id, role, mode, mode_light, realm, destination_realm,
#             task_type, task_id, carried_item, position_id, label} ]
#   manifest [ {realm, plot_id, structure, level, state, source_metric} ]
#   thresholds { tiers: [...], hold_seconds, downgrade_grace_seconds }

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

# ── configurable position→world tier mapping (directive §3) ────────────────
WORLD_TIERS = [
    # (min_pct, max_pct, realm, tier_key, light)
    (None, 0.0,   "city",       "pending",   "grey"),
    (0.0,  25.0,  "amusement",  "observe",   "blue"),
    (25.0, 50.0,  "amusement",  "small_ride","green"),
    (50.0, 75.0,  "farmlands",  "stable",    "lime"),
    (75.0, 100.0, "city",       "premium",   "gold"),
    (100.0, 300.0,"ocean",      "runner",    "cyan"),
    (300.0, 600.0,"ocean",      "runner_hi", "cyan_bright"),
    (600.0, 1000.0,"ocean",     "storm",     "cyan_white"),
    (1000.0, None,"ocean",      "ultimate",  "beacon"),
]
TIER_HOLD_SECONDS = 90            # threshold must hold before promotion
TIER_DOWNGRADE_GRACE_SECONDS = 180  # no instant downgrade on one retracement


def _ro(db_path) -> Optional[sqlite3.Connection]:
    try:
        p = Path(db_path).resolve()
        if not p.exists():
            return None
        c = sqlite3.connect(f"file:{p.as_posix()}?mode=ro", uri=True, timeout=4)
        c.row_factory = sqlite3.Row
        return c
    except Exception:
        return None


def _table(c: sqlite3.Connection, name: str) -> bool:
    try:
        return c.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)
        ).fetchone() is not None
    except Exception:
        return False


def _cols(c: sqlite3.Connection, table: str) -> set:
    try:
        return {r[1] for r in c.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _f(v, d=0.0) -> float:
    try:
        if v is None or str(v).strip() == "":
            return d
        x = float(v)
        return x if x == x else d
    except Exception:
        return d


def _cfg(c: sqlite3.Connection) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not _table(c, "system_config"):
        return out
    try:
        for k, v in c.execute("SELECT key, value FROM system_config"):
            out[str(k)] = "" if v is None else str(v)
    except Exception:
        pass
    return out


# ── open positions ──────────────────────────────────────────────────────────
def _positions(c: sqlite3.Connection, now: float) -> List[Dict[str, Any]]:
    if not _table(c, "paper_positions"):
        return []
    cols = _cols(c, "paper_positions")
    if not cols:
        return []
    idc = "id" if "id" in cols else ("position_id" if "position_id" in cols else None)
    sel = [f"{idc} AS pid" if idc else "rowid AS pid"]
    sel.append("token_name" if "token_name" in cols else "'' AS token_name")
    sel.append("mint_address" if "mint_address" in cols else "'' AS mint_address")
    sel.append("COALESCE(opened_at,0) AS opened_at" if "opened_at" in cols else "0 AS opened_at")
    sel.append("COALESCE(position_size_usd,0) AS size_usd" if "position_size_usd" in cols else "0 AS size_usd")
    # live pnl% — prefer explicit column, else derive from prices if available
    if "pnl_pct" in cols:
        sel.append("COALESCE(pnl_pct,0) AS pnl_pct")
    elif {"entry_price", "current_price"}.issubset(cols):
        sel.append("CASE WHEN COALESCE(entry_price,0)>0 THEN "
                   "((COALESCE(current_price,entry_price)-entry_price)/entry_price)*100.0 "
                   "ELSE 0 END AS pnl_pct")
    else:
        sel.append("0 AS pnl_pct")
    if "pnl_usd" in cols:
        sel.append("COALESCE(pnl_usd,0) AS pnl_usd")
    elif "unrealized_pnl_usd" in cols:
        sel.append("COALESCE(unrealized_pnl_usd,0) AS pnl_usd")
    else:
        sel.append("0 AS pnl_usd")
    where = "UPPER(COALESCE(status,''))='OPEN'" if "status" in cols else "COALESCE(closed_at,0)=0"
    try:
        rows = c.execute(
            f"SELECT {', '.join(sel)} FROM paper_positions WHERE {where} "
            f"ORDER BY COALESCE(opened_at,0) DESC LIMIT 24"
        ).fetchall()
    except Exception:
        return []
    out = []
    for r in rows:
        opened = _f(r["opened_at"])
        out.append({
            "id": r["pid"],
            "token": (r["token_name"] or "")[:18] or (r["mint_address"] or "?")[:8],
            "mint": r["mint_address"] or "",
            "pnl_pct": round(_f(r["pnl_pct"]), 2),
            "pnl_usd": round(_f(r["pnl_usd"]), 2),
            "size_usd": round(_f(r["size_usd"]), 2),
            "opened_at": opened,
            "age_s": max(0, int(now - opened)) if opened > 0 else 0,
            "status": "OPEN",
        })
    return out


# ── recent closes / realized 24h ────────────────────────────────────────────
def _recent_closes(c: sqlite3.Connection, now: float) -> Dict[str, Any]:
    out = {"recent_closes": [], "realized": {"net_24h": 0.0, "count_24h": 0}}
    if not _table(c, "paper_positions"):
        return out
    cols = _cols(c, "paper_positions")
    if "closed_at" not in cols:
        return out
    idc = "id" if "id" in cols else "rowid"
    pnl = "realized_pnl_usd" if "realized_pnl_usd" in cols else None
    pct = "pnl_pct" if "pnl_pct" in cols else None
    try:
        rows = c.execute(
            f"SELECT {idc} AS pid, COALESCE(token_name,'') AS token, "
            f"COALESCE(CAST(closed_at AS REAL),0) AS t, "
            f"{'COALESCE('+pnl+',0)' if pnl else '0'} AS p, "
            f"{'COALESCE('+pct+',0)' if pct else '0'} AS pc "
            f"FROM paper_positions "
            f"WHERE COALESCE(CAST(closed_at AS REAL),0) > 0 "
            f"ORDER BY CAST(closed_at AS REAL) DESC LIMIT 12"
        ).fetchall()
    except Exception:
        return out
    cutoff = now - 86400.0
    for r in rows:
        out["recent_closes"].append({
            "id": r["pid"], "token": (r["token"] or "?")[:18],
            "pnl_usd": round(_f(r["p"]), 2), "pnl_pct": round(_f(r["pc"]), 2),
            "closed_at": _f(r["t"]),
        })
    try:
        row = c.execute(
            "SELECT COALESCE(SUM(COALESCE(realized_pnl_usd,0)),0), COUNT(*) "
            "FROM paper_positions WHERE COALESCE(CAST(closed_at AS REAL),0) >= ?",
            (cutoff,),
        ).fetchone()
        out["realized"] = {"net_24h": round(_f(row[0]), 2), "count_24h": int(row[1] or 0)}
    except Exception:
        pass
    return out


# ── events (cognition_log) ──────────────────────────────────────────────────
def _events(c: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not _table(c, "cognition_log"):
        return []
    cols = _cols(c, "cognition_log")
    tcol = "timestamp" if "timestamp" in cols else ("ts" if "ts" in cols else None)
    if not tcol or "message" not in cols:
        return []
    stage = "COALESCE(stage,'SYS')" if "stage" in cols else "'SYS'"
    token = "COALESCE(token,'')" if "token" in cols else "''"
    try:
        rows = c.execute(
            f"SELECT {tcol} AS ts, {stage} AS stage, message, {token} AS token "
            f"FROM cognition_log ORDER BY {tcol} DESC LIMIT 40"
        ).fetchall()
    except Exception:
        return []
    return [
        {"ts": _f(r["ts"]), "stage": str(r["stage"])[:16],
         "message": str(r["message"])[:180], "token": str(r["token"])[:18]}
        for r in rows
    ]


# ── system / health block ───────────────────────────────────────────────────
def _system(c: sqlite3.Connection, cfg: Dict[str, str], now: float,
            open_count: int) -> Dict[str, Any]:
    sysd: Dict[str, Any] = {
        "health_pct": None, "oracle_online": None, "fresh_prices": None,
        "qualified_signals": None, "open_positions": open_count,
        "latest_signal_age_s": None, "stale": False, "blocked": False,
        "services": [],
    }
    # qualified / tradeable snapshots
    if _table(c, "market_snapshots"):
        mcols = _cols(c, "market_snapshots")
        try:
            if "execution_ready" in mcols:
                sysd["qualified_signals"] = int(c.execute(
                    "SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(execution_ready,0)=1"
                ).fetchone()[0])
            if "updated_at" in mcols:
                r = c.execute("SELECT MAX(COALESCE(updated_at,0)) FROM market_snapshots").fetchone()
                latest = _f(r[0])
                if latest > 0:
                    sysd["latest_signal_age_s"] = max(0, int(now - latest))
        except Exception:
            pass
    # fresh price count — token_metrics rows updated in last 5m
    if _table(c, "token_metrics"):
        tcols = _cols(c, "token_metrics")
        tsc = "ts" if "ts" in tcols else ("updated_at" if "updated_at" in tcols else None)
        if tsc:
            try:
                sysd["fresh_prices"] = int(c.execute(
                    f"SELECT COUNT(*) FROM token_metrics WHERE COALESCE(CAST({tsc} AS REAL),0) >= ?",
                    (now - 300,),
                ).fetchone()[0])
            except Exception:
                pass
    # oracle / stale / blocked hints from config keys (schema-tolerant)
    def truthy(k):
        return str(cfg.get(k, "")).strip().lower() in ("1", "true", "yes", "on")
    for k in ("ORACLE_ONLINE", "ORACLE_STATUS_ONLINE"):
        if k in cfg:
            sysd["oracle_online"] = truthy(k)
            break
    if sysd["oracle_online"] is None and sysd["fresh_prices"] is not None:
        sysd["oracle_online"] = sysd["fresh_prices"] > 0
    for k in ("EXECUTION_BLOCKED", "TRADING_BLOCKED", "PRELAUNCH_BLOCK"):
        if truthy(k):
            sysd["blocked"] = True
    age = sysd.get("latest_signal_age_s")
    if age is not None and age > 900:
        sysd["stale"] = True
    # naive composite health
    score = 100.0
    if sysd["oracle_online"] is False:
        score -= 35
    if sysd["stale"]:
        score -= 20
    if sysd["blocked"]:
        score -= 25
    sysd["health_pct"] = round(max(0.0, score), 1)
    return sysd


# ── agents derived from real backend state (directive §4) ───────────────────
_ROLE_ITEM = {
    "scout": "scanner", "researcher": "book", "builder": "wrench",
    "councillor": "scroll", "oracle": "signal_lens", "executor": "tablet",
    "runner": "helmet",
}
_STAGE_ROLE = {
    "SCOUT": ("scout", "observing", "blue", "rainforest"),
    "POLARIS": ("scout", "observing", "blue", "rainforest"),
    "ORACLE": ("oracle", "verifying", "cyan", "rainforest"),
    "QUALIFIER": ("oracle", "verifying", "cyan", "rainforest"),
    "INGEST": ("oracle", "verifying", "cyan", "rainforest"),
    "DEBATE": ("councillor", "debating", "white", "city"),
    "FORGE": ("builder", "building", "amber", "city"),
    "BUILD": ("builder", "building", "amber", "city"),
    "GUARDIAN": ("researcher", "researching", "purple", "rainforest"),
    "HEALER": ("researcher", "researching", "purple", "rainforest"),
    "HEALTH": ("researcher", "researching", "purple", "rainforest"),
    "EXECUTOR": ("executor", "executing", "green", "city"),
    "NUGGET": ("executor", "executing", "green", "city"),
    "LATCH": ("executor", "executing", "gold", "city"),
    "SUPERVISOR": ("councillor", "governing", "white", "city"),
    "IVARIS": ("oracle", "fault", "red", "stronghold"),
    "ENTROPY": ("oracle", "fault", "red", "stronghold"),
}


def tier_for_pct(pct: Optional[float]) -> Dict[str, Any]:
    p = pct
    for lo, hi, realm, key, light in WORLD_TIERS:
        if p is None:
            break
        if (lo is None or p >= lo) and (hi is None or p < hi):
            return {"realm": realm, "tier": key, "light": light}
    return {"realm": "city", "tier": "pending", "light": "grey"}


def _agents(events: List[Dict[str, Any]], positions: List[Dict[str, Any]],
            sysd: Dict[str, Any]) -> List[Dict[str, Any]]:
    agents: List[Dict[str, Any]] = []
    # 1) one agent per OPEN position — executor/runner on its circuit
    for p in positions:
        t = tier_for_pct(p["pnl_pct"])
        role = "runner" if t["realm"] == "ocean" else "executor"
        agents.append({
            "agent_id": f"pos_{p['id']}",
            "role": role,
            "mode": t["tier"],
            "mode_light": t["light"],
            "realm": t["realm"],
            "destination_realm": t["realm"],
            "task_type": "position_circuit",
            "task_id": str(p["id"]),
            "carried_item": _ROLE_ITEM[role],
            "position_id": p["id"],
            "label": p["token"],
            "pnl_pct": p["pnl_pct"],
        })
    # 2) system agents from most recent distinct cognition stages
    seen = set()
    for ev in events:
        stage = ev["stage"].upper()
        base = None
        for key, tup in _STAGE_ROLE.items():
            if key in stage:
                base = (key, tup)
                break
        if not base or base[0] in seen:
            continue
        seen.add(base[0])
        key, (role, mode, light, realm) = base
        agents.append({
            "agent_id": f"sys_{key.lower()}",
            "role": role, "mode": mode, "mode_light": light,
            "realm": realm, "destination_realm": realm,
            "task_type": "system", "task_id": key,
            "carried_item": _ROLE_ITEM.get(role, "tablet"),
            "position_id": None,
            "label": key.title(),
        })
        if len(seen) >= 8:
            break
    # 3) fault agent if blocked/stale — stronghold investigation
    if sysd.get("blocked") or sysd.get("stale"):
        agents.append({
            "agent_id": "sys_fault_probe",
            "role": "oracle", "mode": "fault",
            "mode_light": "red_pulse" if sysd.get("blocked") else "red",
            "realm": "stronghold", "destination_realm": "stronghold",
            "task_type": "incident", "task_id": "stale" if sysd.get("stale") else "blocked",
            "carried_item": "signal_lens", "position_id": None,
            "label": "Fault Probe",
        })
    return agents


# ── council world manifest (directive §9) — read-only render source ─────────
def _manifest(c: sqlite3.Connection) -> List[Dict[str, Any]]:
    if not _table(c, "world_manifest"):
        return []
    cols = _cols(c, "world_manifest")
    need = {"realm", "plot_id", "structure"}
    if not need.issubset(cols):
        return []
    try:
        rows = c.execute(
            "SELECT realm, plot_id, structure, "
            "COALESCE(level,1) AS level, COALESCE(state,'active') AS state, "
            "COALESCE(source_metric,'') AS source_metric "
            "FROM world_manifest WHERE COALESCE(state,'active')='active' LIMIT 60"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


# ── public entry ─────────────────────────────────────────────────────────────
def load_world_state(db_path) -> Dict[str, Any]:
    now = time.time()
    state: Dict[str, Any] = {
        "generated_at": now,
        "db_path": str(db_path),
        "db_ok": False,
        "mode": {"paper": True, "live": False, "label": "PAPER"},
        "system": {}, "positions": [], "recent_closes": [],
        "realized": {"net_24h": 0.0, "count_24h": 0},
        "events": [], "agents": [], "manifest": [],
        "thresholds": {
            "tiers": [
                {"min": lo, "max": hi, "realm": realm, "tier": key, "light": light}
                for lo, hi, realm, key, light in WORLD_TIERS
            ],
            "hold_seconds": TIER_HOLD_SECONDS,
            "downgrade_grace_seconds": TIER_DOWNGRADE_GRACE_SECONDS,
        },
    }
    c = _ro(db_path)
    if c is None:
        return state
    try:
        state["db_ok"] = True
        cfg = _cfg(c)
        mode = str(cfg.get("TRADING_MODE", "paper")).lower()
        live = mode == "live" or str(cfg.get("LIVE_TRADING_ENABLED", "0")) == "1"
        state["mode"] = {"paper": not live, "live": live,
                         "label": "LIVE" if live else "PAPER"}
        state["positions"] = _positions(c, now)
        rc = _recent_closes(c, now)
        state["recent_closes"] = rc["recent_closes"]
        state["realized"] = rc["realized"]
        state["events"] = _events(c)
        state["system"] = _system(c, cfg, now, len(state["positions"]))
        state["agents"] = _agents(state["events"], state["positions"], state["system"])
        state["manifest"] = _manifest(c)
    except Exception:
        pass
    finally:
        try:
            c.close()
        except Exception:
            pass
    return state


if __name__ == "__main__":
    import json, sys
    db = sys.argv[1] if len(sys.argv) > 1 else "sentinuity_matrix.db"
    print(json.dumps(load_world_state(db), indent=2)[:4000])


# SOVEREIGN_WORLD_UPGRADE_20260723 — canonical world layer merge.
# Additive: wraps load_world_state so every consumer receives the persistent
# Sanctuary Tech Town layer (buildings, agents, chronicle, chapter) alongside
# the original telemetry payload. Read-only against world_* tables.
_orig_load_world_state = load_world_state

def load_world_state(db_path) -> Dict[str, Any]:  # noqa: F811
    state = _orig_load_world_state(db_path)
    try:
        from services.world_build_state import load_world_layer
        state["world"] = load_world_layer(Path(db_path) if db_path else None)
    except Exception as _wl_exc:
        state["world"] = {"error": str(_wl_exc)[:120]}
    return state
