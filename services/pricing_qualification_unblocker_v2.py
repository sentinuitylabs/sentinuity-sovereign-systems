#!/usr/bin/env python3

# SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
from __future__ import annotations
try:
    from birdeye_quota_guard import install_birdeye_requests_guard as _install_birdeye_guard
    _install_birdeye_guard()
except Exception:
    pass
# /SENTINUITY_BIRDEYE_QUOTA_GUARD_V2
"""
Sentinuity Pricing + Qualification Lane Unblocker V2 SAFE-BOUNDED
=================================================================
Backstop for market_snapshots stuck pending quality/price. V2 is bounded:
local DB price sources first, no internal price_router unless explicitly enabled,
short network request timeouts, and whole-scan wall-clock budget.
No direct buys/sells; only updates market_snapshots compatibility fields.
"""
import argparse, importlib, json, os, re, sqlite3, sys, time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple
try:
    import requests
except Exception:
    requests = None  # type: ignore
try:
    from dotenv import load_dotenv
except Exception:
    load_dotenv = None  # type: ignore

SERVICE_NAME = "pricing_qualification_unblocker_v2"
DEFAULT_MIN_CONF = 0.75
DEFAULT_MIN_LIQUIDITY_USD = 1500.0
DEFAULT_MAX_AGE_SEC = 900
DEFAULT_INTERVAL_SEC = 12
DEFAULT_LIMIT = 15
DEFAULT_MAX_TOTAL_SEC = 45.0
DEFAULT_REQUEST_TIMEOUT = 2.0
USER_AGENT = "SentinuityPricingQualificationUnblockerV2/1.0"

@dataclass
class PriceResult:
    source: str
    price_usd: Optional[float]
    liquidity_usd: Optional[float] = None
    market_cap_usd: Optional[float] = None
    volume_24h_usd: Optional[float] = None
    raw: Optional[Dict[str, Any]] = None
    @property
    def priced(self) -> bool:
        return self.price_usd is not None and self.price_usd > 0

def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'

def root_dir() -> Path:
    return Path.cwd().resolve()

def load_env(root: Path) -> None:
    if load_dotenv:
        env = root / ".env"
        if env.exists():
            load_dotenv(env)

def find_db(root: Path) -> Path:
    for name in ("SENTINUITY_DB_PATH", "DATABASE_PATH", "DB_PATH", "SENTINUITY_MATRIX_DB"):
        val = os.getenv(name)
        if val:
            p = Path(val).expanduser()
            if not p.is_absolute():
                p = root / p
            if p.exists():
                return p
    preferred = root / "sentinuity_matrix.db"
    if preferred.exists():
        return preferred
    candidates: List[Tuple[int, Path]] = []
    for pat in ("*.db", "*.sqlite", "*.sqlite3", "**/*.db", "**/*.sqlite", "**/*.sqlite3"):
        for p in root.glob(pat):
            s = str(p).lower()
            if any(x in s for x in (".venv", "__pycache__", ".git", "backup", "patch_backups")):
                continue
            try:
                size = p.stat().st_size
            except OSError:
                continue
            if size > 1024 * 1024:
                candidates.append((size, p))
    if not candidates:
        raise SystemExit("[FAIL] Could not find Sentinuity DB")
    candidates.sort(reverse=True)
    return candidates[0][1]

def connect(db: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(db), timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA busy_timeout=15000")
    return con

def table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None

def columns(con: sqlite3.Connection, table: str) -> List[str]:
    return [r[1] for r in con.execute(f"PRAGMA table_info({qident(table)})").fetchall()]

def ensure_columns(con: sqlite3.Connection) -> None:
    if not table_exists(con, "market_snapshots"):
        raise SystemExit("[FAIL] market_snapshots table not found")
    existing = set(columns(con, "market_snapshots"))
    desired = {
        "price_status": "TEXT DEFAULT 'pending'",
        "price_attempts": "INTEGER DEFAULT 0",
        "latest_price": "REAL",
        "current_price": "REAL",
        "qual_price": "REAL",
        "liquidity_usd": "REAL",
        "market_cap_usd": "REAL",
        "volume_24h_usd": "REAL",
        "pricing_source": "TEXT",
        "pricing_updated_at": "REAL",
        "quality_status": "TEXT DEFAULT 'pending'",
        "quality_reason": "TEXT",
        "candidate_state": "TEXT DEFAULT 'pending'",
        "execution_ready": "INTEGER DEFAULT 0",
        "qualified_at": "REAL",
        "scoring_lifecycle": "TEXT",
    }
    for col, ddl in desired.items():
        if col not in existing:
            con.execute(f"ALTER TABLE market_snapshots ADD COLUMN {qident(col)} {ddl}")
    con.commit()

def parse_time(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        v = float(value)
        if v > 10_000_000_000:
            v /= 1000.0
        return v
    text = str(value).strip()
    if not text:
        return None
    try:
        return parse_time(float(text))
    except Exception:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            dt = datetime.strptime(text.replace("Z", "").split("+")[0], fmt)
            return dt.replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            continue
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except Exception:
        return None

def is_fresh(value: Any, max_age_sec: int) -> bool:
    ts = parse_time(value)
    if ts is None:
        return True
    age = time.time() - ts
    return -300 <= age <= max_age_sec

def float_or_none(v: Any) -> Optional[float]:
    try:
        if v is None or v == "":
            return None
        f = float(v)
        if f > 0:
            return f
    except Exception:
        pass
    return None

def row_keys(row: sqlite3.Row) -> List[str]:
    try:
        return list(row.keys())
    except Exception:
        return []

def extract_mint(row: sqlite3.Row, cols: Sequence[str]) -> Optional[str]:
    candidates: List[str] = []
    for c in ("mint_address", "token_mint", "address", "token", "token_name"):
        if c in cols:
            v = row[c]
            if v:
                candidates.append(str(v).strip())
    for v in candidates:
        if len(v) >= 32 and (re.fullmatch(r"[1-9A-HJ-NP-Za-km-z]+", v) or v.endswith("pump")):
            return v
    return candidates[0] if candidates else None

def get_token_name(row: sqlite3.Row, cols: Sequence[str]) -> Optional[str]:
    for c in ("token_name", "token", "symbol"):
        if c in cols and row[c]:
            return str(row[c]).strip()
    return None

def get_confidence(row: sqlite3.Row, cols: Sequence[str]) -> float:
    for c in ("calibrated_confidence", "confidence_score", "mint_confidence", "confidence", "score"):
        if c in cols:
            v = float_or_none(row[c])
            if v is not None:
                return v / 100.0 if v > 1.5 else v
    return 0.0

def parse_price_result(source: str, result: Any) -> Optional[PriceResult]:
    if result is None:
        return None
    if isinstance(result, (int, float, str)):
        p = float_or_none(result)
        return PriceResult(source=source, price_usd=p, raw={"raw": result}) if p else None
    if isinstance(result, dict):
        keys_price = ("price", "price_usd", "priceUsd", "usdPrice", "value", "current_price", "latest_price", "qual_price")
        keys_liq = ("liquidity", "liquidity_usd", "liquidityUsd", "liq_usd", "liquidityUSD")
        keys_mc = ("market_cap", "market_cap_usd", "marketCap", "mcap", "fdv")
        keys_vol = ("volume24h", "volume_24h", "v24hUSD", "volume24hUsd")
        price = next((float_or_none(result.get(k)) for k in keys_price if k in result and float_or_none(result.get(k)) is not None), None)
        liq = next((float_or_none(result.get(k)) for k in keys_liq if k in result and float_or_none(result.get(k)) is not None), None)
        mc = next((float_or_none(result.get(k)) for k in keys_mc if k in result and float_or_none(result.get(k)) is not None), None)
        vol = next((float_or_none(result.get(k)) for k in keys_vol if k in result and float_or_none(result.get(k)) is not None), None)
        for subkey in ("data", "result", "token", "pair"):
            if price is None and isinstance(result.get(subkey), dict):
                nested = parse_price_result(source, result[subkey])
                if nested and nested.priced:
                    nested.raw = result
                    return nested
        if price:
            return PriceResult(source=source, price_usd=price, liquidity_usd=liq, market_cap_usd=mc, volume_24h_usd=vol, raw=result)
    return None

def price_from_local_db(con: sqlite3.Connection, mint: str, token_name: Optional[str]) -> Optional[PriceResult]:
    keys = [x for x in {mint, token_name or ""} if x]
    if table_exists(con, "token_metrics"):
        tc = columns(con, "token_metrics")
        id_cols = [c for c in ("mint_address", "token_mint", "token_name", "symbol") if c in tc]
        price_cols = [c for c in ("price", "current_price", "latest_price", "qual_price") if c in tc]
        liq_cols = [c for c in ("liquidity", "liquidity_usd", "entry_liq_usd", "liq_usd") if c in tc]
        mc_cols = [c for c in ("market_cap", "market_cap_usd", "mcap", "entry_mcap_usd") if c in tc]
        vol_cols = [c for c in ("volume", "volume_24h", "volume_5m", "volume_1m") if c in tc]
        ts_col = "ts" if "ts" in tc else ("timestamp" if "timestamp" in tc else None)
        if id_cols and price_cols:
            where = " OR ".join([f"{qident(c)}=?" for c in id_cols for _ in keys])
            params = [k for _c in id_cols for k in keys]
            order = f" ORDER BY {qident(ts_col)} DESC" if ts_col else " ORDER BY rowid DESC"
            rows = con.execute(f"SELECT * FROM token_metrics WHERE {where}{order} LIMIT 10", params).fetchall()
            for r in rows:
                rk = row_keys(r)
                price = next((float_or_none(r[c]) for c in price_cols if float_or_none(r[c]) is not None), None)
                if not price:
                    continue
                liq = next((float_or_none(r[c]) for c in liq_cols if c in rk and float_or_none(r[c]) is not None), None)
                mc = next((float_or_none(r[c]) for c in mc_cols if c in rk and float_or_none(r[c]) is not None), None)
                vol = next((float_or_none(r[c]) for c in vol_cols if c in rk and float_or_none(r[c]) is not None), None)
                return PriceResult("local_db.token_metrics", price, liq, mc, vol)
    if table_exists(con, "price_marks"):
        pc = columns(con, "price_marks")
        if "price" in pc:
            id_cols = [c for c in ("mint_address", "token_mint", "token_name", "token") if c in pc]
            if id_cols:
                where = " OR ".join([f"{qident(c)}=?" for c in id_cols for _ in keys])
                params = [k for _c in id_cols for k in keys]
                order = " ORDER BY updated_at DESC" if "updated_at" in pc else " ORDER BY rowid DESC"
                rows = con.execute(f"SELECT * FROM price_marks WHERE {where}{order} LIMIT 5", params).fetchall()
                for r in rows:
                    price = float_or_none(r["price"])
                    if price:
                        return PriceResult("local_db.price_marks", price)
    if table_exists(con, "market_snapshots"):
        mc = columns(con, "market_snapshots")
        price_cols = [c for c in ("latest_price", "current_price", "qual_price", "price") if c in mc]
        id_cols = [c for c in ("mint_address", "token_mint", "token_name", "token") if c in mc]
        if price_cols and id_cols:
            where_ids = " OR ".join([f"{qident(c)}=?" for c in id_cols for _ in keys])
            where_price = " OR ".join([f"COALESCE({qident(c)},0)>0" for c in price_cols])
            params = [k for _c in id_cols for k in keys]
            order = " ORDER BY id DESC" if "id" in mc else " ORDER BY rowid DESC"
            rows = con.execute(f"SELECT * FROM market_snapshots WHERE ({where_ids}) AND ({where_price}){order} LIMIT 5", params).fetchall()
            for r in rows:
                price = next((float_or_none(r[c]) for c in price_cols if float_or_none(r[c]) is not None), None)
                if price:
                    liq = float_or_none(r["liquidity_usd"]) if "liquidity_usd" in mc else None
                    mcap = float_or_none(r["market_cap_usd"]) if "market_cap_usd" in mc else None
                    vol = float_or_none(r["volume_24h_usd"]) if "volume_24h_usd" in mc else None
                    return PriceResult("local_db.market_snapshots", price, liq, mcap, vol)
    return None

def price_from_dexscreener(mint: str, timeout: float) -> Optional[PriceResult]:
    if requests is None:
        return None
    try:
        r = requests.get(f"https://api.dexscreener.com/latest/dex/tokens/{mint}", timeout=(timeout, timeout), headers={"User-Agent": USER_AGENT})
        if r.status_code != 200:
            return None
        pairs = (r.json() or {}).get("pairs") or []
        sol_pairs = []
        for p in pairs:
            if (p.get("chainId") or "").lower() != "solana":
                continue
            base_addr = ((p.get("baseToken") or {}).get("address") or "").strip()
            quote_addr = ((p.get("quoteToken") or {}).get("address") or "").strip()
            if mint not in (base_addr, quote_addr):
                continue
            liq = float_or_none((p.get("liquidity") or {}).get("usd")) or 0.0
            sol_pairs.append((liq, p))
        if not sol_pairs:
            return None
        sol_pairs.sort(key=lambda x: x[0], reverse=True)
        pair = sol_pairs[0][1]
        price = float_or_none(pair.get("priceUsd"))
        if price:
            return PriceResult("dexscreener", price, float_or_none((pair.get("liquidity") or {}).get("usd")), float_or_none(pair.get("fdv")) or float_or_none(pair.get("marketCap")), float_or_none((pair.get("volume") or {}).get("h24")), raw=pair)
    except Exception:
        return None
    return None

def birdeye_key() -> Optional[str]:
    for k in ("BIRDEYE_API_KEY", "BIRDEYE_KEY", "BIRDEYE_API", "BIRDEYE_TOKEN"):
        v = os.getenv(k)
        if v:
            return v.strip()
    return None

def price_from_birdeye(mint: str, timeout: float) -> Optional[PriceResult]:
    if requests is None:
        return None
    key = birdeye_key()
    if not key:
        return None
    headers = {"X-API-KEY": key, "x-chain": "solana", "accept": "application/json", "User-Agent": USER_AGENT}
    for url in (f"https://public-api.birdeye.so/defi/price?address={mint}", f"https://public-api.birdeye.so/defi/token_overview?address={mint}"):
        try:
            r = requests.get(url, headers=headers, timeout=(timeout, timeout))
            if r.status_code != 200:
                continue
            parsed = parse_price_result("birdeye", r.json())
            if parsed and parsed.priced:
                return parsed
        except Exception:
            continue
    return None

def price_from_existing_router(mint: str, verbose: bool = False) -> Optional[PriceResult]:
    if os.getenv("SENTINUITY_UNBLOCK_USE_PRICE_ROUTER", "0").strip().lower() not in {"1", "true", "yes", "on"}:
        return None
    try:
        router = importlib.import_module("services.price_router")
    except Exception:
        return None
    for name in ("get_price", "fetch_price", "resolve_price", "route_price", "get_token_price", "get_price_for_mint"):
        fn = getattr(router, name, None)
        if not callable(fn):
            continue
        try:
            parsed = parse_price_result(f"price_router.{name}", fn(mint))
            if parsed and parsed.priced:
                return parsed
        except Exception as e:
            if verbose:
                print(f"[router] {name} failed: {e}")
    return None

def fetch_price(con: sqlite3.Connection, mint: str, token_name: Optional[str], request_timeout: float, verbose: bool=False) -> Optional[PriceResult]:
    for fn in (lambda: price_from_local_db(con, mint, token_name), lambda: price_from_existing_router(mint, verbose=verbose), lambda: price_from_dexscreener(mint, request_timeout), lambda: price_from_birdeye(mint, request_timeout)):
        res = fn()
        if res and res.priced:
            return res
    return None

def insert_cognition(con: sqlite3.Connection, stage: str, token: Optional[str], message: str, confidence: Optional[float] = None, meta: Optional[Dict[str, Any]] = None) -> None:
    if not table_exists(con, "cognition_log"):
        return
    cols = columns(con, "cognition_log")
    data: Dict[str, Any] = {}
    now_str = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
    for c in cols:
        if c == "timestamp": data[c] = now_str
        elif c == "stage": data[c] = stage
        elif c == "token": data[c] = token
        elif c == "message": data[c] = message[:2000]
        elif c == "confidence": data[c] = confidence
        elif c == "meta": data[c] = json.dumps(meta or {}, default=str)
    if data:
        keys = list(data.keys())
        con.execute(f"INSERT INTO cognition_log ({', '.join(qident(k) for k in keys)}) VALUES ({', '.join('?' for _ in keys)})", [data[k] for k in keys])

def update_heartbeat(root: Path, status: str, details: str) -> None:
    try:
        sys.path.insert(0, str(root))
        schema = importlib.import_module("core.schema")
        fn = getattr(schema, "update_heartbeat", None)
        if callable(fn):
            try:
                fn(SERVICE_NAME, status, details)
            except TypeError:
                try: fn(SERVICE_NAME, details)
                except Exception: pass
    except Exception:
        pass

def is_blacklisted(con: sqlite3.Connection, mint: str) -> Tuple[bool, str]:
    if not table_exists(con, "mint_blacklist"):
        return False, ""
    cols = columns(con, "mint_blacklist")
    if "mint_address" not in cols:
        return False, ""
    row = con.execute("SELECT * FROM mint_blacklist WHERE mint_address=? LIMIT 1", (mint,)).fetchone()
    if not row:
        return False, ""
    return True, str(row["reason"]) if "reason" in cols and row["reason"] else ""

def candidate_query(con: sqlite3.Connection, limit: int, max_age_sec: int) -> List[sqlite3.Row]:
    cols = columns(con, "market_snapshots")
    where: List[str] = []
    if "latched" in cols: where.append("COALESCE(latched,0)=0")
    if "executed" in cols: where.append("COALESCE(executed,0)=0")
    if "execution_ready" in cols: where.append("COALESCE(execution_ready,0)=0")
    if "candidate_state" in cols: where.append("(candidate_state IS NULL OR candidate_state IN ('pending','priced','qualified',''))")
    if "price_status" in cols: where.append("(price_status IS NULL OR price_status IN ('pending','error',''))")
    sql = "SELECT * FROM market_snapshots"
    if where:
        sql += " WHERE " + " AND ".join(where)
    order_col = "id" if "id" in cols else "rowid"
    sql += f" ORDER BY {qident(order_col)} DESC LIMIT ?"
    rows = con.execute(sql, (max(limit * 6, limit),)).fetchall()
    fresh: List[sqlite3.Row] = []
    time_col = "timestamp" if "timestamp" in cols else None
    for r in rows:
        if time_col and not is_fresh(r[time_col], max_age_sec):
            continue
        fresh.append(r)
        if len(fresh) >= limit:
            break
    return fresh

def update_snapshot_attempt(con: sqlite3.Connection, snap_id: Any, id_col: str, reason: str) -> None:
    cols = columns(con, "market_snapshots")
    sets: List[str] = []
    vals: List[Any] = []
    if "price_attempts" in cols: sets.append("price_attempts = COALESCE(price_attempts,0) + 1")
    if "quality_reason" in cols:
        sets.append("quality_reason = ?"); vals.append(reason[:500])
    if "pricing_updated_at" in cols:
        sets.append("pricing_updated_at = ?"); vals.append(time.time())
    if not sets: return
    vals.append(snap_id)
    con.execute(f"UPDATE market_snapshots SET {', '.join(sets)} WHERE {qident(id_col)}=?", vals)

def update_snapshot_priced(con: sqlite3.Connection, snap_id: Any, id_col: str, price: PriceResult, qualified: bool, reason: str) -> None:
    cols = columns(con, "market_snapshots")
    sets: List[str] = []
    vals: List[Any] = []
    def set_if(col: str, val: Any) -> None:
        if col in cols:
            sets.append(f"{qident(col)}=?"); vals.append(val)
    if "price_attempts" in cols: sets.append("price_attempts = COALESCE(price_attempts,0) + 1")
    for col in ("latest_price", "current_price", "qual_price", "price"): set_if(col, price.price_usd)
    set_if("liquidity_usd", price.liquidity_usd)
    set_if("market_cap_usd", price.market_cap_usd)
    set_if("volume_24h_usd", price.volume_24h_usd)
    set_if("pricing_source", price.source)
    set_if("pricing_updated_at", time.time())
    set_if("price_status", "priced")
    set_if("quality_status", "qualified" if qualified else "pending")
    set_if("quality_reason", reason[:500])
    # === PATCH 1 — UNBLOCKER AUTHORITY GUARD ===
    # Default fail-closed: unblocker may NOT force execution_ready / latched /
    # candidate_state='execution_ready'. Those belong to neural_supervisor.
    # The unblocker may still mark observation fields (priced, quality_status).
    import os as _os_p1
    _authority = _os_p1.environ.get("SENTINUITY_ALLOW_UNBLOCKER_AUTHORITY") == "1"
    if qualified:
        if _authority:
            set_if("candidate_state", "execution_ready")
            set_if("scoring_lifecycle", "execution_ready")
            set_if("execution_ready", 1)
            set_if("qualified_at", time.time())
        else:
            # Mark as observed/priced only — supervisor decides execution_ready
            set_if("candidate_state", "priced")
            set_if("scoring_lifecycle", "priced_awaiting_supervisor")
            set_if("execution_ready", 0)
    else:
        set_if("candidate_state", "priced")
        set_if("execution_ready", 0)
    # === END PATCH 1 ===
    if not sets: return
    vals.append(snap_id)
    con.execute(f"UPDATE market_snapshots SET {', '.join(sets)} WHERE {qident(id_col)}=?", vals)

def scan_once(root: Path, db: Path, limit: int, max_age_sec: int, min_conf: float, min_liq: float, max_total_sec: float, request_timeout: float, verbose: bool = False) -> Dict[str, Any]:
    started = time.monotonic()
    con = connect(db)
    ensure_columns(con)
    cols = columns(con, "market_snapshots")
    id_col = "id" if "id" in cols else "rowid"
    rows = candidate_query(con, limit=limit, max_age_sec=max_age_sec)
    stats: Dict[str, Any] = {"seen": len(rows), "attempted": 0, "priced": 0, "qualified": 0, "blacklisted": 0, "no_mint": 0, "no_price": 0, "low_conf": 0, "low_liquidity": 0, "errors": 0, "timed_out": 0, "elapsed_sec": 0.0}
    for row in rows:
        if time.monotonic() - started >= max_total_sec:
            stats["timed_out"] = 1
            break
        try:
            snap_id = row[id_col]
            mint = extract_mint(row, cols)
            token_name = get_token_name(row, cols)
            if not mint:
                stats["no_mint"] += 1; update_snapshot_attempt(con, snap_id, id_col, "pricing_unblocker_v2:no_mint"); con.commit(); continue
            blacklisted, bl_reason = is_blacklisted(con, mint)
            if blacklisted:
                stats["blacklisted"] += 1; update_snapshot_attempt(con, snap_id, id_col, f"pricing_unblocker_v2:blacklisted:{bl_reason}"); con.commit(); continue
            conf = get_confidence(row, cols)
            if conf < min_conf:
                stats["low_conf"] += 1; update_snapshot_attempt(con, snap_id, id_col, f"pricing_unblocker_v2:confidence_below_floor:{conf:.3f}"); con.commit(); continue
            stats["attempted"] += 1
            price = fetch_price(con, mint, token_name, request_timeout=request_timeout, verbose=verbose)
            if not price or not price.priced:
                stats["no_price"] += 1; update_snapshot_attempt(con, snap_id, id_col, "pricing_unblocker_v2:no_price_source_returned")
                if verbose: print(f"[NO_PRICE] snapshot={snap_id} mint={mint[:16]} token={token_name}")
                con.commit(); continue
            stats["priced"] += 1
            qualified = True
            reason_parts = [f"priced_by={price.source}", f"conf={conf:.3f}", f"price={price.price_usd}"]
            if price.liquidity_usd is not None:
                reason_parts.append(f"liq={price.liquidity_usd:.2f}")
                if price.liquidity_usd < min_liq:
                    qualified = False; stats["low_liquidity"] += 1; reason_parts.append(f"liquidity_below_floor={min_liq:.2f}")
            else:
                allow_unknown = os.getenv("SENTINUITY_UNBLOCK_ALLOW_UNKNOWN_LIQUIDITY", "0").strip().lower() in {"1", "true", "yes", "on"}
                reason_parts.append("liq=unknown")
                if not allow_unknown:
                    qualified = False; reason_parts.append("unknown_liquidity_not_qualified")
            update_snapshot_priced(con, snap_id, id_col, price, qualified, "pricing_unblocker_v2:" + ";".join(reason_parts))
            if qualified:
                stats["qualified"] += 1
                insert_cognition(con, "QUALIFIER", mint, f"Pricing unblocker V2 routed priced snapshot to execution_ready | snapshot={snap_id} source={price.source} conf={conf:.3f}", confidence=conf, meta={"snapshot_id": snap_id, "source": price.source, "liquidity_usd": price.liquidity_usd})
            if verbose: print(f"[PRICE] snapshot={snap_id} mint={mint[:16]} source={price.source} qualified={qualified}")
            con.commit()
        except Exception as e:
            stats["errors"] += 1
            if verbose: print(f"[ERR] row failed: {e}")
            try: con.rollback()
            except Exception: pass
    stats["elapsed_sec"] = round(time.monotonic() - started, 3)
    insert_cognition(con, "QUALIFIER", SERVICE_NAME, f"scan_once stats={json.dumps(stats, sort_keys=True)}", meta=stats)
    con.commit()
    update_heartbeat(root, "ALIVE" if not stats["errors"] else "WARN", f"priced={stats['priced']} qualified={stats['qualified']} attempted={stats['attempted']} timed_out={stats['timed_out']}")
    con.close()
    return stats

def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="Sentinuity pricing/qualification lane unblocker V2")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--limit", type=int, default=int(os.getenv("SENTINUITY_UNBLOCK_LIMIT", DEFAULT_LIMIT)))
    parser.add_argument("--interval", type=int, default=int(os.getenv("SENTINUITY_UNBLOCK_INTERVAL_SEC", DEFAULT_INTERVAL_SEC)))
    parser.add_argument("--max-age-sec", type=int, default=int(os.getenv("SENTINUITY_UNBLOCK_MAX_AGE_SEC", DEFAULT_MAX_AGE_SEC)))
    parser.add_argument("--min-conf", type=float, default=float(os.getenv("SENTINUITY_UNBLOCK_MIN_CONF", DEFAULT_MIN_CONF)))
    parser.add_argument("--min-liquidity-usd", type=float, default=float(os.getenv("SENTINUITY_UNBLOCK_MIN_LIQUIDITY_USD", DEFAULT_MIN_LIQUIDITY_USD)))
    parser.add_argument("--max-total-sec", type=float, default=float(os.getenv("SENTINUITY_UNBLOCK_MAX_TOTAL_SEC", DEFAULT_MAX_TOTAL_SEC)))
    parser.add_argument("--request-timeout", type=float, default=float(os.getenv("SENTINUITY_UNBLOCK_REQUEST_TIMEOUT", DEFAULT_REQUEST_TIMEOUT)))
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)
    root = root_dir(); load_env(root); db = find_db(root)
    print("=" * 90); print("SENTINUITY PRICING + QUALIFICATION LANE UNBLOCKER V2 SAFE-BOUNDED"); print("=" * 90)
    print(f"[ROOT] {root}"); print(f"[DB]   {db}")
    print(f"[SAFE] no direct buys/sells; min_conf={args.min_conf}; min_liq=${args.min_liquidity_usd:,.0f}; max_age={args.max_age_sec}s; limit={args.limit}; budget={args.max_total_sec}s; request_timeout={args.request_timeout}s")
    while True:
        try:
            stats = scan_once(root, db, args.limit, args.max_age_sec, args.min_conf, args.min_liquidity_usd, args.max_total_sec, args.request_timeout, verbose=args.verbose)
            print("[SCAN]", json.dumps(stats, sort_keys=True))
        except KeyboardInterrupt:
            print("[STOP] operator interrupt"); return 0
        except Exception as e:
            print(f"[ERR] scan failed: {e}"); update_heartbeat(root, "WARN", f"scan_error={e}")
        if args.once: return 0
        time.sleep(max(3, args.interval))
if __name__ == "__main__":
    raise SystemExit(main())
