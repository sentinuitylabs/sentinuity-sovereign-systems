# services/pricing_qualification_unblocker_v3.py
# SENTINUITY PRICING + QUALIFICATION LANE UNBLOCKER V3
# Bounded, non-hanging, no dataclass, workspace-safe.
#
# Purpose:
#   Restore movement from market_snapshots pending -> priced -> execution_ready
#   when the main pricing/qualification lane is starved.
#
# Safety:
#   - Does NOT execute buys/sells.
#   - Does NOT touch wallet balances.
#   - Does NOT bypass execution_engine/neural_supervisor.
#   - Only updates market_snapshots metadata/price/quality fields when a real price is found.
#   - Uses short HTTP timeouts and local DB evidence first.

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_WORKSPACE = ROOT

PRICE_COL_CANDIDATES = [
    "price", "current_price", "latest_price", "last_price", "qual_price",
    "entry_price", "token_price", "price_usd", "usd_price"
]
CONF_COL_CANDIDATES = [
    "calibrated_confidence", "mint_confidence", "confidence_score",
    "confidence", "runner_conviction", "edge_score", "score"
]


def log(msg: str) -> None:
    print(msg, flush=True)


def locate_workspace(start: Optional[Path] = None) -> Path:
    """Find the trading-bot workspace even when launched from an extracted zip folder."""
    start = (start or Path.cwd()).resolve()
    candidates: List[Path] = []

    for p in [start] + list(start.parents):
        candidates.append(p)

    candidates.append(DEFAULT_WORKSPACE)

    for p in candidates:
        try:
            if (p / "sentinuity_matrix.db").exists() and (p / "services").exists():
                return p
        except Exception:
            pass

    # Fallback: current directory if it at least looks like repo.
    if (start / "services").exists():
        return start

    return start


WORKSPACE = locate_workspace(ROOT)


def find_db(root: Optional[Path] = None) -> Path:
    root = root or WORKSPACE
    direct = root / "sentinuity_matrix.db"
    if direct.exists():
        return direct

    candidates: List[Tuple[int, Path]] = []
    for pat in ("*.db", "*.sqlite", "*.sqlite3", "**/*.db", "**/*.sqlite", "**/*.sqlite3"):
        for p in root.glob(pat):
            s = str(p).lower()
            if any(x in s for x in (".venv", "__pycache__", ".git", "backup", "patch_backups")):
                continue
            try:
                size = p.stat().st_size
                if size > 1024 * 1024:
                    candidates.append((size, p))
            except Exception:
                pass
    candidates.sort(reverse=True)
    if not candidates:
        raise FileNotFoundError(f"No Sentinuity DB found under {root}")
    return candidates[0][1]


def connect(db: Optional[Path] = None) -> sqlite3.Connection:
    db = db or find_db()
    con = sqlite3.connect(str(db), timeout=15)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA journal_mode=WAL")
    except Exception:
        pass
    try:
        con.execute("PRAGMA busy_timeout=15000")
    except Exception:
        pass
    return con


def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone() is not None


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info({table})").fetchall()]
    except Exception:
        return []


def ensure_col(con: sqlite3.Connection, table: str, name: str, ddl_type: str) -> None:
    existing = set(cols(con, table))
    if name not in existing:
        con.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl_type}")


def ensure_market_snapshot_columns(con: sqlite3.Connection) -> None:
    if not table_exists(con, "market_snapshots"):
        raise RuntimeError("market_snapshots table missing")

    # Add only compatibility metadata columns if absent.
    additions = [
        ("price_status", "TEXT DEFAULT 'pending'"),
        ("price_attempts", "INTEGER DEFAULT 0"),
        ("quality_status", "TEXT DEFAULT 'pending'"),
        ("quality_reason", "TEXT DEFAULT ''"),
        ("candidate_state", "TEXT DEFAULT 'pending'"),
        ("execution_ready", "INTEGER DEFAULT 0"),
        ("latest_price", "REAL"),
        ("qual_price", "REAL"),
        ("priced_at", "REAL"),
        ("pricing_source", "TEXT"),
        ("liquidity_usd", "REAL"),
        ("route_status", "TEXT"),
        ("unblocker_version", "TEXT"),
    ]
    for name, ddl in additions:
        try:
            ensure_col(con, "market_snapshots", name, ddl)
        except sqlite3.OperationalError as e:
            # Ignore duplicate races.
            if "duplicate column" not in str(e).lower():
                raise
    con.commit()


def safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        if isinstance(v, bytes):
            v = v.decode("utf-8", "ignore")
        if isinstance(v, str):
            v = v.strip().replace("$", "").replace(",", "")
            if not v:
                return None
        f = float(v)
        if f <= 0:
            return None
        if f != f or f == float("inf") or f == float("-inf"):
            return None
        return f
    except Exception:
        return None


def row_to_dict(row: sqlite3.Row) -> Dict[str, Any]:
    return {k: row[k] for k in row.keys()}


def get_confidence(row: Dict[str, Any]) -> float:
    for c in CONF_COL_CANDIDATES:
        if c in row:
            f = safe_float(row.get(c))
            if f is not None:
                # Some legacy values are 0-100
                return f / 100.0 if f > 1.0 else f
    return 0.0


def get_mint(row: Dict[str, Any]) -> str:
    for k in ("mint_address", "token_mint", "mint", "token_address", "token"):
        v = row.get(k)
        if v:
            return str(v).strip()
    return ""


def get_token_name(row: Dict[str, Any]) -> str:
    for k in ("token_name", "symbol", "token_symbol", "name"):
        v = row.get(k)
        if v:
            return str(v).strip()
    return ""


def local_row_price(row: Dict[str, Any]) -> Optional[Tuple[float, str, Optional[float]]]:
    for c in PRICE_COL_CANDIDATES:
        if c in row:
            f = safe_float(row.get(c))
            if f is not None:
                return f, f"market_snapshots.{c}", None
    return None


def lookup_price_marks(con: sqlite3.Connection, mint: str, token: str) -> Optional[Tuple[float, str, Optional[float]]]:
    if not table_exists(con, "price_marks"):
        return None
    c = cols(con, "price_marks")
    if "price" not in c:
        return None

    where_parts = []
    params: List[Any] = []

    for key in ("mint_address", "token_mint", "mint"):
        if key in c and mint:
            where_parts.append(f"{key}=?")
            params.append(mint)

    if "token_name" in c and token:
        where_parts.append("token_name=?")
        params.append(token)

    if not where_parts:
        return None

    order = "updated_at DESC" if "updated_at" in c else "rowid DESC"
    row = con.execute(
        f"SELECT * FROM price_marks WHERE {' OR '.join(where_parts)} ORDER BY {order} LIMIT 1",
        params,
    ).fetchone()
    if not row:
        return None
    d = row_to_dict(row)
    f = safe_float(d.get("price"))
    if f is None:
        return None
    return f, "price_marks", None


def lookup_token_metrics(con: sqlite3.Connection, mint: str, token: str) -> Optional[Tuple[float, str, Optional[float]]]:
    if not table_exists(con, "token_metrics"):
        return None
    c = cols(con, "token_metrics")
    if "price" not in c:
        return None

    where_parts = []
    params: List[Any] = []

    for key in ("mint_address", "token_mint", "mint", "token_address"):
        if key in c and mint:
            where_parts.append(f"{key}=?")
            params.append(mint)

    if "token_name" in c:
        if token:
            where_parts.append("token_name=?")
            params.append(token)
        if mint:
            where_parts.append("token_name=?")
            params.append(mint)

    if not where_parts:
        return None

    order = "ts DESC" if "ts" in c else ("updated_at DESC" if "updated_at" in c else "rowid DESC")
    row = con.execute(
        f"SELECT * FROM token_metrics WHERE {' OR '.join(where_parts)} ORDER BY {order} LIMIT 1",
        params,
    ).fetchone()
    if not row:
        return None
    d = row_to_dict(row)
    f = safe_float(d.get("price"))
    if f is None:
        return None
    liq = None
    for lk in ("liquidity", "liquidity_usd", "entry_liq_usd"):
        if lk in d:
            liq = safe_float(d.get(lk))
            if liq is not None:
                break
    return f, "token_metrics", liq


def lookup_dexscreener(mint: str, timeout: float = 3.5) -> Optional[Tuple[float, str, Optional[float]]]:
    """Short-timeout public lookup. Never allowed to hang the scan."""
    if not mint:
        return None
    if os.environ.get("SENTINUITY_UNBLOCKER_ALLOW_HTTP", "1").strip().lower() in ("0", "false", "no"):
        return None
    try:
        import requests  # type: ignore
        url = f"https://api.dexscreener.com/token-pairs/v1/solana/{mint}"
        r = requests.get(url, timeout=timeout, headers={"User-Agent": "SentinuityUnblockerV3/1.0"})
        if r.status_code != 200:
            return None
        data = r.json()
        if not isinstance(data, list) or not data:
            return None

        best = None
        best_liq = -1.0
        for pair in data:
            if not isinstance(pair, dict):
                continue
            price = safe_float(pair.get("priceUsd"))
            if price is None:
                continue
            liq = None
            if isinstance(pair.get("liquidity"), dict):
                liq = safe_float(pair["liquidity"].get("usd"))
            rank = liq if liq is not None else 0.0
            if rank > best_liq:
                best = (price, "dexscreener", liq)
                best_liq = rank
        return best
    except Exception:
        return None


def resolve_price(con: sqlite3.Connection, row: Dict[str, Any]) -> Optional[Tuple[float, str, Optional[float]]]:
    mint = get_mint(row)
    token = get_token_name(row)

    for fn in (
        lambda: local_row_price(row),
        lambda: lookup_price_marks(con, mint, token),
        lambda: lookup_token_metrics(con, mint, token),
        lambda: lookup_dexscreener(mint),
    ):
        try:
            result = fn()
            if result and result[0] > 0:
                return result
        except Exception:
            continue
    return None


def is_blacklisted(con: sqlite3.Connection, mint: str) -> Tuple[bool, str]:
    if not mint or not table_exists(con, "mint_blacklist"):
        return False, ""
    c = cols(con, "mint_blacklist")
    key = "mint_address" if "mint_address" in c else ("mint" if "mint" in c else None)
    if not key:
        return False, ""
    row = con.execute(f"SELECT * FROM mint_blacklist WHERE {key}=? LIMIT 1", (mint,)).fetchone()
    if not row:
        return False, ""
    d = row_to_dict(row)
    return True, str(d.get("reason") or "blacklisted")


def select_candidates(con: sqlite3.Connection, limit: int) -> List[sqlite3.Row]:
    ensure_market_snapshot_columns(con)
    c = cols(con, "market_snapshots")

    where = []
    params: List[Any] = []

    if "latched" in c:
        where.append("(latched IS NULL OR CAST(latched AS INTEGER)=0)")
    if "executed" in c:
        where.append("(executed IS NULL OR CAST(executed AS INTEGER)=0)")
    if "execution_ready" in c:
        where.append("(execution_ready IS NULL OR CAST(execution_ready AS INTEGER)=0)")

    # Prefer current pending snapshots. Do not keep hammering stale/expired ones.
    if "candidate_state" in c:
        where.append("(candidate_state IS NULL OR candidate_state IN ('pending','new','candidate','scored','qualified','execution_ready'))")
    if "price_status" in c:
        where.append("(price_status IS NULL OR price_status IN ('pending','error','retry',''))")
    if "price_attempts" in c:
        where.append("(price_attempts IS NULL OR CAST(price_attempts AS INTEGER) < ?)") 
        params.append(int(os.environ.get("SENTINUITY_UNBLOCKER_MAX_ATTEMPTS", "4")))

    # Freshest first.
    if "timestamp" in c:
        order = "timestamp DESC"
    elif "created_at" in c:
        order = "created_at DESC"
    else:
        order = "id DESC" if "id" in c else "rowid DESC"

    sql = f"SELECT * FROM market_snapshots"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += f" ORDER BY {order} LIMIT ?"
    params.append(limit)

    return con.execute(sql, params).fetchall()


def update_attempt(con: sqlite3.Connection, sid: Any, note: str = "") -> None:
    c = cols(con, "market_snapshots")
    sets = []
    params: List[Any] = []
    if "price_attempts" in c:
        sets.append("price_attempts=COALESCE(price_attempts,0)+1")
    if "pricing_source" in c:
        sets.append("pricing_source=?")
        params.append(note[:80])
    if "unblocker_version" in c:
        sets.append("unblocker_version='v3'")
    if not sets:
        return
    params.append(sid)
    con.execute(f"UPDATE market_snapshots SET {', '.join(sets)} WHERE id=?", params)


def update_priced(
    con: sqlite3.Connection,
    sid: Any,
    price: float,
    source: str,
    liquidity: Optional[float],
    confidence: float,
    route_status: str,
    execution_ready: bool,
    reason: str,
) -> None:
    c = cols(con, "market_snapshots")
    sets = []
    params: List[Any] = []

    def add(col: str, value: Any) -> None:
        if col in c:
            sets.append(f"{col}=?")
            params.append(value)

    add("latest_price", price)
    add("qual_price", price)
    add("price", price)
    add("price_status", "priced")
    add("priced_at", time.time())
    add("pricing_source", source)
    add("liquidity_usd", liquidity)
    add("route_status", route_status)
    # === PATCH 1 — UNBLOCKER AUTHORITY GUARD ===
    # Default fail-closed. Unblocker v3 may not promote rows to execution_ready
    # unless SENTINUITY_ALLOW_UNBLOCKER_AUTHORITY=1.
    import os as _os_p1
    _authority = _os_p1.environ.get("SENTINUITY_ALLOW_UNBLOCKER_AUTHORITY") == "1"
    if execution_ready and _authority:
        add("quality_status", "qualified")
        add("quality_reason", reason[:300])
        add("candidate_state", "execution_ready")
        add("execution_ready", 1)
    elif execution_ready and not _authority:
        # Would-have-been-execution-ready, but supervisor owns that flag now
        add("quality_status", "priced_waiting_supervisor")
        add("quality_reason", ("[guarded-by-patch-1] " + reason)[:300])
        add("candidate_state", "priced")
        add("execution_ready", 0)
    else:
        add("quality_status", "priced_waiting_quality")
        add("quality_reason", reason[:300])
        add("candidate_state", "priced")
        add("execution_ready", 0)
    add("unblocker_version", "v3")
    # === END PATCH 1 ===

    if "price_attempts" in c:
        sets.append("price_attempts=COALESCE(price_attempts,0)+1")

    if not sets:
        return

    params.append(sid)
    con.execute(f"UPDATE market_snapshots SET {', '.join(sets)} WHERE id=?", params)


def insert_cognition(con: sqlite3.Connection, token: str, message: str, confidence: float = 0.0) -> None:
    if not table_exists(con, "cognition_log"):
        return
    c = cols(con, "cognition_log")
    data: Dict[str, Any] = {}
    if "timestamp" in c:
        data["timestamp"] = time.strftime("%Y-%m-%d %H:%M:%S")
    if "stage" in c:
        data["stage"] = "QUALIFIER"
    if "token" in c:
        data["token"] = token
    if "message" in c:
        data["message"] = message
    if "confidence" in c:
        data["confidence"] = confidence
    if "meta" in c:
        data["meta"] = json.dumps({"source": "pricing_qualification_unblocker_v3"})
    if not data:
        return
    keys = list(data)
    con.execute(
        f"INSERT INTO cognition_log ({', '.join(keys)}) VALUES ({', '.join(['?'] * len(keys))})",
        [data[k] for k in keys],
    )


def run_once(limit: int = 10, verbose: bool = False) -> Dict[str, Any]:
    db = find_db()
    con = connect(db)
    ensure_market_snapshot_columns(con)

    conf_floor = float(os.environ.get("SENTINUITY_UNBLOCKER_CONF_FLOOR", "0.75"))
    min_liq = float(os.environ.get("SENTINUITY_UNBLOCKER_MIN_LIQ_USD", "0"))

    candidates = select_candidates(con, limit)

    result = {
        "db": str(db),
        "scanned": 0,
        "priced": 0,
        "execution_ready": 0,
        "no_price": 0,
        "blacklisted": 0,
        "errors": 0,
        "details": [],
    }

    deadline = time.time() + float(os.environ.get("SENTINUITY_UNBLOCKER_SCAN_TIMEOUT_SEC", "90"))

    for row in candidates:
        if time.time() > deadline:
            result["details"].append({"stop": "deadline"})
            break

        result["scanned"] += 1
        d = row_to_dict(row)
        sid = d.get("id")
        mint = get_mint(d)
        token = get_token_name(d) or mint
        confidence = get_confidence(d)

        try:
            blacklisted, black_reason = is_blacklisted(con, mint)
            if blacklisted:
                result["blacklisted"] += 1
                update_attempt(con, sid, f"blacklisted:{black_reason}")
                if verbose:
                    log(f"[SKIP] id={sid} {token} blacklisted {black_reason}")
                continue

            price_result = resolve_price(con, d)
            if not price_result:
                result["no_price"] += 1
                update_attempt(con, sid, "no_price_source_v3")
                if verbose:
                    log(f"[NO_PRICE] id={sid} {token} conf={confidence:.3f}")
                continue

            price, source, liquidity = price_result
            route_status = "route_unknown"
            if source in ("dexscreener", "token_metrics", "price_marks"):
                route_status = "priced_source_available"

            enough_liq = (liquidity is None and min_liq <= 0) or (liquidity is not None and liquidity >= min_liq)
            ready = confidence >= conf_floor and enough_liq

            reason = (
                f"priced_by_unblocker_v3 source={source} price={price} "
                f"conf={confidence:.3f} floor={conf_floor:.3f} liq={liquidity}"
            )

            update_priced(
                con=con,
                sid=sid,
                price=price,
                source=source,
                liquidity=liquidity,
                confidence=confidence,
                route_status=route_status,
                execution_ready=ready,
                reason=reason,
            )
            result["priced"] += 1
            if ready:
                result["execution_ready"] += 1
                insert_cognition(con, token, f"Execution-ready via pricing unblocker v3. {reason}", confidence)

            if verbose:
                status = "READY" if ready else "PRICED"
                log(f"[{status}] id={sid} token={token} price={price} source={source} conf={confidence:.3f} liq={liquidity}")

            result["details"].append({
                "id": sid,
                "token": token,
                "price": price,
                "source": source,
                "confidence": confidence,
                "liquidity": liquidity,
                "ready": ready,
            })

            con.commit()

        except Exception as e:
            result["errors"] += 1
            try:
                update_attempt(con, sid, f"error:{type(e).__name__}")
                con.commit()
            except Exception:
                pass
            if verbose:
                log(f"[ERR] id={sid} {token}: {type(e).__name__}: {e}")

    con.commit()
    con.close()
    return result


def loop(interval: float, limit: int, verbose: bool = False) -> None:
    log("=" * 90)
    log("SENTINUITY PRICING + QUALIFICATION UNBLOCKER V3 ONLINE")
    log(f"[WORKSPACE] {WORKSPACE}")
    log(f"[DB] {find_db()}")
    log(f"[INTERVAL] {interval}s limit={limit}")
    log("=" * 90)
    while True:
        try:
            res = run_once(limit=limit, verbose=verbose)
            log(json.dumps({k: v for k, v in res.items() if k != "details"}, default=str))
        except KeyboardInterrupt:
            raise
        except Exception as e:
            log(f"[LOOP_ERR] {type(e).__name__}: {e}")
            log(traceback.format_exc()[-1500:])
        time.sleep(interval)


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--interval", type=float, default=8.0)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args(argv)

    if args.once:
        res = run_once(limit=args.limit, verbose=args.verbose)
        log(json.dumps(res, indent=2, default=str))
        return 0

    loop(interval=args.interval, limit=args.limit, verbose=args.verbose)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
