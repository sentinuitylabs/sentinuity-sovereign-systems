"""
birdeye_quota_guard.py
Local Birdeye pacing / CU budget guard for Sentinuity.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

_INSTALLED = False
_LOCK = threading.RLock()
ROOT = Path.cwd().resolve()
DB = ROOT / "sentinuity_matrix.db"

_ENDPOINT_CU = [
    ("/defi/history_price", 45),
    ("/defi/historical_price_unix", 6),
    ("/defi/ohlcv", 35),
    ("/defi/token_overview", 25),
    ("/defi/v3/token/exit-liquidity", 15),
    ("/defi/v3/pair/overview", 15),
    ("/defi/v3/token/market-data", 12),
    ("/defi/v3/token/trade-data", 12),
    ("/defi/price_volume/single", 8),
    ("/defi/v3/token/meta-data/single", 5),
    ("/defi/price", 3),
]


def _cfg(key: str, default: str) -> str:
    env = os.getenv(key)
    if env not in (None, ""):
        return str(env)
    try:
        if DB.exists():
            con = sqlite3.connect(str(DB), timeout=2)
            try:
                row = con.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
                if row and row[0] not in (None, ""):
                    return str(row[0])
            finally:
                con.close()
    except Exception:
        pass
    return default


def _num(key: str, default: float) -> float:
    try:
        return float(_cfg(key, str(default)))
    except Exception:
        return float(default)


def _estimate_cu(url: str) -> float:
    path = urlparse(str(url)).path.lower()
    for frag, cu in _ENDPOINT_CU:
        if frag in path:
            return float(cu)
    return _num("BIRDEYE_ESTIMATE_DEFAULT_CU", 5.0)


def _ensure_ledger(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS api_usage_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            provider TEXT NOT NULL,
            endpoint TEXT,
            ts REAL NOT NULL,
            cu_estimate REAL DEFAULT 0,
            status TEXT,
            detail TEXT
        )
        """
    )
    con.execute("CREATE INDEX IF NOT EXISTS idx_api_usage_ledger_provider_ts ON api_usage_ledger(provider, ts)")


def _usage_since(seconds: float) -> float:
    if not DB.exists():
        return 0.0
    cutoff = time.time() - seconds
    try:
        con = sqlite3.connect(str(DB), timeout=2)
        try:
            _ensure_ledger(con)
            row = con.execute("SELECT COALESCE(SUM(cu_estimate),0) FROM api_usage_ledger WHERE provider='birdeye' AND ts>=?", (cutoff,)).fetchone()
            return float(row[0] or 0.0)
        finally:
            con.close()
    except Exception:
        return 0.0


def _last_ts() -> float:
    if not DB.exists():
        return 0.0
    try:
        con = sqlite3.connect(str(DB), timeout=2)
        try:
            _ensure_ledger(con)
            row = con.execute("SELECT MAX(ts) FROM api_usage_ledger WHERE provider='birdeye'").fetchone()
            return float(row[0] or 0.0)
        finally:
            con.close()
    except Exception:
        return 0.0


def _record(endpoint: str, cu: float, status: str, detail: str = "") -> None:
    if not DB.exists():
        return
    try:
        con = sqlite3.connect(str(DB), timeout=2)
        try:
            _ensure_ledger(con)
            con.execute("INSERT INTO api_usage_ledger(provider, endpoint, ts, cu_estimate, status, detail) VALUES ('birdeye',?,?,?,?,?)", (endpoint[:240], time.time(), float(cu), status[:40], detail[:500]))
            con.commit()
        finally:
            con.close()
    except Exception:
        pass


def _make_local_429(message: str):
    try:
        import requests
        resp = requests.Response()
        resp.status_code = 429
        resp._content = json.dumps({"error": "BIRDEYE_LOCAL_BUDGET_GUARD", "message": message}).encode("utf-8")
        resp.headers["content-type"] = "application/json"
        resp.url = "local://birdeye-budget-guard"
        return resp
    except Exception:
        raise RuntimeError(message)


def _preflight(url: str):
    if _cfg("BIRDEYE_LOCAL_GUARD_ENABLED", "1") != "1":
        return None
    daily_budget = _num("BIRDEYE_DAILY_CU_BUDGET", 900.0)
    monthly_budget = _num("BIRDEYE_MONTHLY_CU_BUDGET", 30000.0)
    reserve = _num("BIRDEYE_CU_RESERVE", 3000.0)
    min_interval = _num("BIRDEYE_MIN_INTERVAL_SEC", 20.0)
    cu = _estimate_cu(url)
    endpoint = urlparse(str(url)).path or str(url)[:120]
    with _LOCK:
        day = _usage_since(86400)
        month = _usage_since(30 * 86400)
        if day + cu > daily_budget:
            _record(endpoint, 0, "local_block_daily", f"day={day} cu={cu} daily_budget={daily_budget}")
            return _make_local_429(f"Birdeye daily CU budget would be exceeded: {day}+{cu}>{daily_budget}")
        if month + cu > max(0.0, monthly_budget - reserve):
            _record(endpoint, 0, "local_block_month", f"month={month} cu={cu} monthly_budget={monthly_budget} reserve={reserve}")
            return _make_local_429(f"Birdeye monthly CU reserve protected: {month}+{cu}>{monthly_budget-reserve}")
        last = _last_ts()
        sleep_for = max(0.0, min_interval - (time.time() - last))
        if sleep_for > 0:
            time.sleep(min(sleep_for, 60.0))
        _record(endpoint, cu, "attempt", "paced")
    return None


def install_birdeye_requests_guard() -> bool:
    global _INSTALLED
    if _INSTALLED:
        return False
    try:
        import requests
    except Exception:
        return False
    original = requests.sessions.Session.request
    if getattr(original, "_sentinuity_birdeye_guarded", False):
        _INSTALLED = True
        return False
    def guarded(self, method, url, *args, **kwargs):
        if "birdeye" in str(url).lower():
            local = _preflight(str(url))
            if local is not None:
                return local
        return original(self, method, url, *args, **kwargs)
    guarded._sentinuity_birdeye_guarded = True  # type: ignore[attr-defined]
    requests.sessions.Session.request = guarded
    _INSTALLED = True
    return True
