"""
hybrid_split_harvest.py
-------------------------------------------------------------------------------
SENTINUITY HYBRID SPLIT HARVEST RUNTIME — 2026-06-27
-------------------------------------------------------------------------------
Paper/shadow-only split-harvest ledger for restoring mid-runner visible wins
without capping monster/GOLD upside.

Safety contract:
- Does not close positions.
- Does not resize live or paper positions.
- Does not mutate live wallet or real balances.
- Writes only observational metadata + paper_split_harvest_events.
- Requires fresh trusted mark by default.
- Fail-soft: any error returns a non-fatal status dict.

Intended call site:
- services/execution_engine.py::_paper_gold_runner_evaluate(), after peak/current
  PnL are known and before GOLD force/trail close rules.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, Optional, Tuple

MARKER = "HYBRID_SPLIT_HARVEST_RUNTIME_20260627"

SUSPECT_SOURCE_FRAGMENTS = (
    "intel-mtm",
    "router:intel-mtm",
    "mtm-snapshot",
    "enricher_open_position",
    "enricher-open-position",
    "fallback",
    "unknown",
    "stale",
)

TRUSTED_SOURCE_HINTS = (
    "ws",
    "oracle",
    "curve",
    "pump",
    "raydium",
    "jupiter",
    "helius",
    "birdeye",
    "dex",
    "market_snapshots",
    "mtm_ticks",
)

PAPER_POSITION_COLUMNS = [
    ("split_harvest_state", "TEXT"),
    ("split_harvested_pct", "REAL DEFAULT 0"),
    ("split_harvested_usd", "REAL DEFAULT 0"),
    ("split_harvested_at", "REAL"),
    ("split_harvest_trigger_pct", "REAL"),
    ("runner_reserve_pct", "REAL DEFAULT 0"),
    ("runner_confirmed_at", "REAL"),
    ("runner_profit_lock_pct", "REAL DEFAULT 0"),
    ("runner_last_protect_reason", "TEXT"),
    ("trusted_peak_source", "TEXT"),
    ("trusted_peak_price", "REAL"),
    ("trusted_peak_pnl_pct", "REAL"),
    ("split_harvest_policy", "TEXT"),
    ("split_harvest_notes", "TEXT"),
]


def _f(x: Any, default: float = 0.0) -> float:
    try:
        if x is None:
            return default
        return float(x)
    except Exception:
        return default


def _s(x: Any, default: str = "") -> str:
    try:
        if x is None:
            return default
        return str(x)
    except Exception:
        return default


def _cfg(get_config_value_func: Optional[Callable[..., Any]], key: str, default: Any) -> Any:
    if get_config_value_func is None:
        return default
    try:
        return get_config_value_func(key, default)
    except Exception:
        return default


def _bool_cfg(get_config_value_func: Optional[Callable[..., Any]], key: str, default: str = "0") -> bool:
    v = _s(_cfg(get_config_value_func, key, default), default).strip().lower()
    return v in ("1", "true", "yes", "y", "on")


def _float_cfg(get_config_value_func: Optional[Callable[..., Any]], key: str, default: float) -> float:
    return _f(_cfg(get_config_value_func, key, default), default)


def _source_is_suspect(source: Any) -> bool:
    src = _s(source, "unknown").strip().lower()
    if not src:
        return True
    return any(fragment in src for fragment in SUSPECT_SOURCE_FRAGMENTS)


def _source_is_trusted(source: Any) -> bool:
    src = _s(source, "").strip().lower()
    if not src or _source_is_suspect(src):
        return False
    return any(hint in src for hint in TRUSTED_SOURCE_HINTS) or src not in ("unknown", "fallback")


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1] if not hasattr(r, "keys") else r["name"]) for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
    except Exception:
        return set()


def _pick(cols: Iterable[str], *names: str) -> Optional[str]:
    cset = set(cols)
    for n in names:
        if n in cset:
            return n
    return None


def ensure_split_harvest_schema(conn: sqlite3.Connection) -> None:
    """Idempotent schema additions for shadow/paper split-harvest."""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_split_harvest_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_key TEXT UNIQUE,
            position_id INTEGER,
            mint_address TEXT,
            token_name TEXT,
            event_type TEXT,
            threshold_pct REAL,
            policy_name TEXT,
            harvested_pct REAL DEFAULT 0,
            reserve_pct REAL DEFAULT 0,
            peak_pct REAL,
            cur_pnl_pct REAL,
            trigger_price REAL,
            harvested_usd REAL DEFAULT 0,
            reserve_unrealized_usd REAL DEFAULT 0,
            trusted_source TEXT,
            trusted_price REAL,
            source_age_sec REAL,
            state TEXT,
            created_at REAL,
            notes TEXT
        )
    """)

    pp_cols = _cols(conn, "paper_positions")
    for col, typedef in PAPER_POSITION_COLUMNS:
        if col not in pp_cols:
            try:
                conn.execute(f"ALTER TABLE paper_positions ADD COLUMN {col} {typedef}")
            except Exception:
                pass


def _query_latest_market_snapshot(
    conn: sqlite3.Connection,
    mint: str,
    opened_at: float,
    max_age_sec: float,
    now: float,
) -> Optional[Dict[str, Any]]:
    cols = _cols(conn, "market_snapshots")
    if not cols:
        return None
    mint_col = _pick(cols, "mint_address", "mint", "token_mint")
    price_col = _pick(cols, "observed_price", "price", "last_price", "mark_price")
    ts_col = _pick(cols, "price_updated_at", "timestamp", "ts", "created_at", "observed_at", "updated_at")
    src_col = _pick(cols, "source", "mark_source", "price_source", "provider", "route_source")
    if not (mint_col and price_col and ts_col):
        return None
    try:
        sql = (
            f"SELECT {price_col} AS price, {ts_col} AS ts"
            + (f", {src_col} AS source" if src_col else ", 'market_snapshots' AS source")
            + f" FROM market_snapshots WHERE {mint_col}=? AND {price_col}>0 "
              f"AND COALESCE({ts_col},0) >= ? ORDER BY {ts_col} DESC LIMIT 1"
        )
        row = conn.execute(sql, (mint, max(opened_at, now - max_age_sec))).fetchone()
        if not row:
            return None
        price = _f(row["price"] if hasattr(row, "keys") else row[0])
        ts = _f(row["ts"] if hasattr(row, "keys") else row[1])
        source = row["source"] if hasattr(row, "keys") else row[2]
        age = max(0.0, now - ts) if ts > 0 else 999999.0
        return {"price": price, "ts": ts, "source": source or "market_snapshots", "age_sec": age, "table": "market_snapshots"}
    except Exception:
        return None


def _query_latest_intel_mtm(
    get_intel_connection_func: Optional[Callable[[], sqlite3.Connection]],
    mint: str,
    opened_at: float,
    max_age_sec: float,
    now: float,
) -> Optional[Dict[str, Any]]:
    if get_intel_connection_func is None:
        return None
    conn = None
    try:
        conn = get_intel_connection_func()
        try:
            conn.row_factory = sqlite3.Row
        except Exception:
            pass
        cols = _cols(conn, "mtm_ticks")
        if not cols:
            return None
        mint_col = _pick(cols, "mint_address", "mint", "token_mint")
        price_col = _pick(cols, "price", "observed_price", "mark_price", "last_price", "current_price")
        ts_col = _pick(cols, "timestamp", "ts", "created_at", "observed_at", "price_updated_at", "updated_at")
        src_col = _pick(cols, "source", "mark_source", "price_source", "provider", "route_source")
        if not (mint_col and price_col and ts_col):
            return None
        sql = (
            f"SELECT {price_col} AS price, {ts_col} AS ts"
            + (f", {src_col} AS source" if src_col else ", 'mtm_ticks' AS source")
            + f" FROM mtm_ticks WHERE {mint_col}=? AND {price_col}>0 "
              f"AND COALESCE({ts_col},0) >= ? ORDER BY {ts_col} DESC LIMIT 1"
        )
        row = conn.execute(sql, (mint, max(opened_at, now - max_age_sec))).fetchone()
        if not row:
            return None
        price = _f(row["price"] if hasattr(row, "keys") else row[0])
        ts = _f(row["ts"] if hasattr(row, "keys") else row[1])
        source = row["source"] if hasattr(row, "keys") else row[2]
        age = max(0.0, now - ts) if ts > 0 else 999999.0
        return {"price": price, "ts": ts, "source": source or "mtm_ticks", "age_sec": age, "table": "mtm_ticks"}
    except Exception:
        return None
    finally:
        try:
            if conn is not None:
                conn.close()
        except Exception:
            pass


def latest_trusted_mark(
    conn: sqlite3.Connection,
    mint: str,
    opened_at: float,
    current_price: float,
    max_age_sec: float,
    now: float,
    get_intel_connection_func: Optional[Callable[[], sqlite3.Connection]] = None,
) -> Dict[str, Any]:
    """Return fresh trusted mark evidence, fail-soft."""
    candidates = []
    ms = _query_latest_market_snapshot(conn, mint, opened_at, max_age_sec, now)
    if ms:
        candidates.append(ms)
    im = _query_latest_intel_mtm(get_intel_connection_func, mint, opened_at, max_age_sec, now)
    if im:
        candidates.append(im)

    candidates.sort(key=lambda d: _f(d.get("ts")), reverse=True)
    for cand in candidates:
        if _f(cand.get("price")) <= 0:
            continue
        if _f(cand.get("age_sec"), 999999.0) > max_age_sec:
            continue
        source = cand.get("source") or cand.get("table") or "unknown"
        if _source_is_trusted(source):
            cand["trusted"] = True
            return cand

    return {
        "trusted": False,
        "price": current_price,
        "ts": 0.0,
        "source": "NO_FRESH_TRUSTED_MARK",
        "age_sec": 999999.0,
        "table": None,
    }


def _event_key(position_id: int, event_type: str, threshold_pct: float, policy_name: str) -> str:
    return f"{MARKER}|pos={int(position_id)}|type={event_type}|thr={float(threshold_pct):.4f}|policy={policy_name}"


def _insert_event(
    conn: sqlite3.Connection,
    *,
    position_id: int,
    mint: str,
    token_name: str,
    event_type: str,
    threshold_pct: float,
    policy_name: str,
    harvested_pct: float,
    reserve_pct: float,
    peak_pct: float,
    cur_pnl_pct: float,
    trigger_price: float,
    harvested_usd: float,
    reserve_unrealized_usd: float,
    trusted_source: str,
    trusted_price: float,
    source_age_sec: float,
    state: str,
    now: float,
    notes: str,
) -> bool:
    key = _event_key(position_id, event_type, threshold_pct, policy_name)
    cur = conn.execute(
        """
        INSERT OR IGNORE INTO paper_split_harvest_events (
            event_key, position_id, mint_address, token_name, event_type,
            threshold_pct, policy_name, harvested_pct, reserve_pct, peak_pct,
            cur_pnl_pct, trigger_price, harvested_usd, reserve_unrealized_usd,
            trusted_source, trusted_price, source_age_sec, state, created_at, notes
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            key, position_id, mint, token_name, event_type, threshold_pct,
            policy_name, harvested_pct, reserve_pct, peak_pct, cur_pnl_pct,
            trigger_price, harvested_usd, reserve_unrealized_usd,
            trusted_source, trusted_price, source_age_sec, state, now, notes,
        ),
    )
    return cur.rowcount > 0


def _protect_state(peak_pct: float, trail_pct: float) -> Tuple[str, float]:
    lock = max(0.0, peak_pct - max(0.0, trail_pct))
    if peak_pct >= 600.0:
        return "MONSTER_RESERVE", max(lock, 200.0)
    if peak_pct >= 200.0:
        return "TIGHT_RESERVE", max(lock, 100.0)
    if peak_pct >= 100.0:
        return "PROTECTED_RESERVE", max(lock, 50.0)
    if peak_pct >= 75.0:
        return "SPLIT_HARVEST_TRIGGER", lock
    if peak_pct >= 50.0:
        return "MID_RUNNER_SEEN", 0.0
    return "NONE", 0.0


def evaluate_paper_split_harvest(
    *,
    position_id: int,
    mint: str,
    token_name: str,
    entry_price: float,
    opened_at: float,
    pos_size_usd: float,
    peak_pct: float,
    cur_pnl_pct: float,
    current_price: float,
    peak_price: Optional[float] = None,
    get_connection_func: Optional[Callable[[], sqlite3.Connection]] = None,
    get_intel_connection_func: Optional[Callable[[], sqlite3.Connection]] = None,
    get_config_value_func: Optional[Callable[..., Any]] = None,
    log_func: Optional[Callable[[str], None]] = None,
    now: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Paper/shadow evaluation. Writes events/metadata only. No closes.

    Returns dict with status:
      DISABLED, NOT_CROSSED, NO_TRUSTED_TICK, HELD, HARVEST_EVENT_WRITTEN, ERROR
    """
    t = float(now or time.time())
    try:
        shadow_enabled = _bool_cfg(get_config_value_func, "PAPER_SPLIT_SHADOW_ENABLED", "1")
        paper_enabled = _bool_cfg(get_config_value_func, "PAPER_SPLIT_HARVEST_ENABLED", "0")
        if not shadow_enabled and not paper_enabled:
            return {"status": "DISABLED", "marker": MARKER}

        if not mint or int(position_id) <= 0 or _f(entry_price) <= 0:
            return {"status": "BAD_INPUT", "marker": MARKER}

        trigger_pct = _float_cfg(get_config_value_func, "PAPER_SPLIT_HARVEST_TRIGGER_PCT", 75.0)
        harvested_pct = _float_cfg(get_config_value_func, "PAPER_SPLIT_HARVEST_PCT", 50.0)
        reserve_pct = _float_cfg(get_config_value_func, "PAPER_RUNNER_RESERVE_PCT", max(0.0, 100.0 - harvested_pct))
        require_trusted = _bool_cfg(get_config_value_func, "PAPER_SPLIT_REQUIRE_TRUSTED_SOURCE", "1")
        max_age_sec = _float_cfg(get_config_value_func, "PAPER_SPLIT_MIN_MTM_FRESH_SEC", 120.0)
        trail_pct = _float_cfg(get_config_value_func, "GOLD_RUNNER_TRAIL_PCT", 20.0)
        policy_name = _s(_cfg(get_config_value_func, "PAPER_SPLIT_POLICY_NAME", "SPLIT_50_AT_75_RESERVE_50"), "SPLIT_50_AT_75_RESERVE_50")

        if peak_pct < 50.0:
            return {"status": "NOT_CROSSED", "peak_pct": peak_pct, "marker": MARKER}

        if peak_price is None or _f(peak_price) <= 0:
            peak_price = _f(entry_price) * (1.0 + _f(peak_pct) / 100.0)

        if get_connection_func is None:
            return {"status": "NO_CONNECTION_FUNC", "marker": MARKER}

        with get_connection_func() as conn:
            try:
                conn.row_factory = sqlite3.Row
            except Exception:
                pass
            ensure_split_harvest_schema(conn)

            trusted = latest_trusted_mark(
                conn, mint, _f(opened_at), _f(current_price), max_age_sec, t,
                get_intel_connection_func=get_intel_connection_func,
            )
            if require_trusted and not bool(trusted.get("trusted")):
                return {
                    "status": "NO_TRUSTED_TICK",
                    "peak_pct": peak_pct,
                    "source": trusted.get("source"),
                    "age_sec": trusted.get("age_sec"),
                    "marker": MARKER,
                }

            trusted_source = _s(trusted.get("source"), "trusted-disabled" if not require_trusted else "unknown")
            trusted_price = _f(trusted.get("price"), _f(current_price))
            source_age_sec = _f(trusted.get("age_sec"), 999999.0)

            crossed = [x for x in (50.0, 75.0, 100.0, 200.0, 600.0, 1000.0) if _f(peak_pct) >= x]
            inserted = 0
            state, lock_pct = _protect_state(_f(peak_pct), trail_pct)
            notes = "shadow/paper split ledger only; no close/no resize/no live mutation"

            # Write threshold events for continuous slow-runner rescue/milestones.
            for thr in crossed:
                if _insert_event(
                    conn,
                    position_id=position_id,
                    mint=mint,
                    token_name=token_name,
                    event_type="THRESHOLD_CROSSED",
                    threshold_pct=thr,
                    policy_name=policy_name,
                    harvested_pct=0.0,
                    reserve_pct=0.0,
                    peak_pct=_f(peak_pct),
                    cur_pnl_pct=_f(cur_pnl_pct),
                    trigger_price=_f(entry_price) * (1.0 + thr / 100.0),
                    harvested_usd=0.0,
                    reserve_unrealized_usd=0.0,
                    trusted_source=trusted_source,
                    trusted_price=trusted_price,
                    source_age_sec=source_age_sec,
                    state=state,
                    now=t,
                    notes=notes,
                ):
                    inserted += 1

            # Write one virtual harvest event when trigger crossed. It books a virtual
            # PnL leg only; it does not alter the position size or close anything.
            wrote_harvest = False
            if _f(peak_pct) >= trigger_pct:
                trigger_price = _f(entry_price) * (1.0 + trigger_pct / 100.0)
                harvested_usd = _f(pos_size_usd) * max(0.0, min(100.0, harvested_pct)) / 100.0 * trigger_pct / 100.0
                reserve_unrealized_usd = _f(pos_size_usd) * max(0.0, min(100.0, reserve_pct)) / 100.0 * _f(cur_pnl_pct) / 100.0
                wrote_harvest = _insert_event(
                    conn,
                    position_id=position_id,
                    mint=mint,
                    token_name=token_name,
                    event_type="VIRTUAL_HARVEST",
                    threshold_pct=trigger_pct,
                    policy_name=policy_name,
                    harvested_pct=harvested_pct,
                    reserve_pct=reserve_pct,
                    peak_pct=_f(peak_pct),
                    cur_pnl_pct=_f(cur_pnl_pct),
                    trigger_price=trigger_price,
                    harvested_usd=harvested_usd,
                    reserve_unrealized_usd=reserve_unrealized_usd,
                    trusted_source=trusted_source,
                    trusted_price=trusted_price,
                    source_age_sec=source_age_sec,
                    state=state,
                    now=t,
                    notes=notes,
                )
                if wrote_harvest:
                    inserted += 1

                # Metadata update. Keep existing confirmed time if already present.
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET split_harvest_state=?,
                        split_harvested_pct=CASE WHEN COALESCE(split_harvested_pct,0)>0 THEN split_harvested_pct ELSE ? END,
                        split_harvested_usd=CASE WHEN COALESCE(split_harvested_usd,0)>0 THEN split_harvested_usd ELSE ? END,
                        split_harvested_at=CASE WHEN COALESCE(split_harvested_at,0)>0 THEN split_harvested_at ELSE ? END,
                        split_harvest_trigger_pct=?,
                        runner_reserve_pct=?,
                        runner_confirmed_at=CASE WHEN COALESCE(runner_confirmed_at,0)>0 THEN runner_confirmed_at ELSE ? END,
                        runner_profit_lock_pct=MAX(COALESCE(runner_profit_lock_pct,0), ?),
                        runner_last_protect_reason=?,
                        trusted_peak_source=?,
                        trusted_peak_price=MAX(COALESCE(trusted_peak_price,0), ?),
                        trusted_peak_pnl_pct=MAX(COALESCE(trusted_peak_pnl_pct,0), ?),
                        split_harvest_policy=?,
                        split_harvest_notes=?
                    WHERE id=?
                    """,
                    (
                        state, harvested_pct, harvested_usd, t, trigger_pct, reserve_pct,
                        t, lock_pct, state, trusted_source, _f(peak_price), _f(peak_pct),
                        policy_name, notes, position_id,
                    ),
                )
            else:
                conn.execute(
                    """
                    UPDATE paper_positions
                    SET runner_confirmed_at=CASE WHEN COALESCE(runner_confirmed_at,0)>0 THEN runner_confirmed_at ELSE ? END,
                        runner_profit_lock_pct=MAX(COALESCE(runner_profit_lock_pct,0), ?),
                        runner_last_protect_reason=?,
                        trusted_peak_source=?,
                        trusted_peak_price=MAX(COALESCE(trusted_peak_price,0), ?),
                        trusted_peak_pnl_pct=MAX(COALESCE(trusted_peak_pnl_pct,0), ?),
                        split_harvest_state=CASE WHEN COALESCE(split_harvest_state,'')='' THEN ? ELSE split_harvest_state END,
                        split_harvest_policy=CASE WHEN COALESCE(split_harvest_policy,'')='' THEN ? ELSE split_harvest_policy END
                    WHERE id=?
                    """,
                    (
                        t, lock_pct, state, trusted_source, _f(peak_price), _f(peak_pct),
                        state, policy_name, position_id,
                    ),
                )

            try:
                conn.commit()
            except Exception:
                pass

        if inserted and log_func is not None:
            try:
                log_func(
                    f"HYBRID_SPLIT_HARVEST shadow pos={position_id} {token_name} "
                    f"peak={peak_pct:.1f}% cur={cur_pnl_pct:.1f}% state={state} "
                    f"inserted={inserted} trusted={trusted_source} age={source_age_sec:.1f}s"
                )
            except Exception:
                pass

        return {
            "status": "HARVEST_EVENT_WRITTEN" if wrote_harvest else "HELD",
            "inserted": inserted,
            "state": state,
            "peak_pct": peak_pct,
            "cur_pnl_pct": cur_pnl_pct,
            "trusted_source": trusted_source,
            "source_age_sec": source_age_sec,
            "marker": MARKER,
        }
    except Exception as e:
        return {"status": "ERROR", "error": repr(e), "marker": MARKER}
