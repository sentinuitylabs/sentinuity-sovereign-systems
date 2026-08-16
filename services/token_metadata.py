"""Canonical, cross-process token metadata resolution.

SENTINUITY_TOKEN_METADATA_20260805
==================================

Why this module exists
----------------------
``live_trading._get_token_decimals`` resolved SPL mint decimals through a single
un-retried RPC call behind a process-local dict. Three measured consequences in
the 2026-08-05 stop-realisability cohort (n=16):

  * 6 of 16 probes terminated at ``probe_status='decimals_failed'`` before any
    quote was measured, holding quote coverage at 62.5% against a 95% floor;
  * ``pre_quote_setup_sec`` ran 5.88-15.27s, consistent with a scalar
    ``timeout=8.0`` applied separately to connect and read;
  * the resolved value was ``6`` on every successful row -- an immutable
    per-mint constant was being fetched over the network, repeatedly, per
    process.

Contract
--------
SPL mint decimals are immutable for the life of the mint. A positive result is
therefore cached permanently and shared across processes via SQLite. A negative
result is cached only briefly, so a transient provider outage cannot become a
sticky refusal, and never becomes a guess.

FAIL CLOSED. This module never invents, defaults, or infers a decimals value.
Callers receive an integer or an exception carrying the exact failure type and
the provider host. A wrong decimals value silently mis-scales every raw token
amount, so an unresolved mint must stop the caller, not be papered over.

Security
--------
Configured RPC URLs embed API keys. Only the hostname is ever recorded or
logged; full URLs never enter the database, the logs, or an exception message.
"""

from __future__ import annotations

import os
import time
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, Optional, Tuple, List
from urllib.parse import urlsplit

log = logging.getLogger("token_metadata")

TABLE = "token_metadata_cache"

# Decimals are immutable, so a RESOLVED row never expires. An UNRESOLVED row
# expires quickly: a provider outage must not become a durable refusal.
NEGATIVE_TTL_SEC_DEFAULT = 20.0

# Split connect/read budget. The prior scalar timeout=8.0 was applied to both
# phases independently by requests, giving a ~16s worst case per attempt.
CONNECT_TIMEOUT_SEC_DEFAULT = 2.0
READ_TIMEOUT_SEC_DEFAULT = 4.0

MAX_ATTEMPTS_DEFAULT = 2

# SPL token decimals are a u8; anything outside this is a malformed response.
MIN_DECIMALS = 0
MAX_DECIMALS = 18

_MEMO: Dict[str, int] = {}
_MEMO_LOCK = threading.Lock()

_DDL = f"""
CREATE TABLE IF NOT EXISTS {TABLE} (
    mint             TEXT PRIMARY KEY,
    decimals         INTEGER,
    source           TEXT,
    provider         TEXT,
    resolved_at      REAL,
    validation_state TEXT,
    attempts         INTEGER DEFAULT 0,
    last_error_type  TEXT,
    last_error_at    REAL,
    updated_at       REAL
)
"""

STATE_RESOLVED = "RESOLVED"
STATE_UNRESOLVED = "UNRESOLVED"


class TokenMetadataUnresolved(RuntimeError):
    """Raised when decimals cannot be established. Carries typed detail.

    The message deliberately preserves the legacy leading token
    ``token_decimals_unresolved`` so existing log greps and any caller matching
    on that substring continue to work.
    """

    def __init__(self, mint: str, failure_type: str, provider: str = "",
                 detail: str = ""):
        self.mint = str(mint or "")
        self.failure_type = str(failure_type or "unknown")
        self.provider = str(provider or "none")
        self.detail = str(detail or "")[:200]
        super().__init__(
            f"token_decimals_unresolved mint={self.mint[:16]} "
            f"failure={self.failure_type} provider={self.provider}"
            + (f" detail={self.detail}" if self.detail else "")
        )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

def _cfg_float(key: str, default: float) -> float:
    try:
        from core.schema import get_config_value
        return float(get_config_value(key, default))
    except Exception:
        try:
            return float(os.getenv(key, default))
        except Exception:
            return float(default)


def _cfg_int(key: str, default: int) -> int:
    try:
        return int(_cfg_float(key, float(default)))
    except Exception:
        return int(default)


def provider_label(url: str) -> str:
    """Hostname only. Configured RPC URLs embed API keys and must not be stored."""
    try:
        host = urlsplit(str(url or "")).hostname or ""
        return host or "unknown"
    except Exception:
        return "unknown"


def _endpoints() -> List[str]:
    """Ordered, de-duplicated RPC endpoints: primaries first, then backups."""
    names = (
        "SOLANA_RPC_URL", "HELIUS_RPC_URL", "CHAINSTACK_RPC", "QUICKNODE_RPC",
        "HELIUS_RPC_URL_BACKUP", "CHAINSTACK_RPC_BACKUP", "QUICKNODE_RPC_BACKUP",
        "SOLANA_RPC_URL_BACKUP",
    )
    out: List[str] = []
    seen = set()
    for n in names:
        v = str(os.getenv(n, "") or "").strip()
        if v and v not in seen:
            seen.add(v)
            out.append(v)
    return out


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _db_path() -> Path:
    try:
        from core.schema import DB_PATH
        return Path(DB_PATH)
    except Exception:
        return Path(__file__).resolve().parent.parent / "sentinuity_matrix.db"


def _connect(conn=None):
    if conn is not None:
        return conn, False
    c = sqlite3.connect(str(_db_path()), timeout=15.0, isolation_level=None)
    return c, True


def ensure_schema(conn=None) -> bool:
    c, owned = _connect(conn)
    try:
        c.execute(_DDL)
        return True
    except Exception as exc:
        log.debug("[TOKEN_METADATA] ensure_schema failed: %s", type(exc).__name__)
        return False
    finally:
        if owned:
            try:
                c.close()
            except Exception:
                pass


def _read_row(mint: str, conn=None) -> Optional[dict]:
    c, owned = _connect(conn)
    try:
        ensure_schema(c)
        r = c.execute(
            f"SELECT mint,decimals,source,provider,resolved_at,validation_state,"
            f"attempts,last_error_type,last_error_at FROM {TABLE} WHERE mint=?",
            (str(mint),),
        ).fetchone()
        if not r:
            return None
        return {
            "mint": r[0], "decimals": r[1], "source": r[2], "provider": r[3],
            "resolved_at": r[4], "validation_state": r[5], "attempts": r[6],
            "last_error_type": r[7], "last_error_at": r[8],
        }
    except Exception:
        return None
    finally:
        if owned:
            try:
                c.close()
            except Exception:
                pass


def _write_row(mint: str, *, decimals=None, source="", provider="",
               validation_state=STATE_UNRESOLVED, error_type="", conn=None) -> None:
    c, owned = _connect(conn)
    now = time.time()
    try:
        ensure_schema(c)
        c.execute(
            f"INSERT INTO {TABLE} (mint,decimals,source,provider,resolved_at,"
            f"validation_state,attempts,last_error_type,last_error_at,updated_at) "
            f"VALUES (?,?,?,?,?,?,1,?,?,?) "
            f"ON CONFLICT(mint) DO UPDATE SET "
            f"  decimals=COALESCE(excluded.decimals,{TABLE}.decimals),"
            f"  source=CASE WHEN excluded.validation_state='{STATE_RESOLVED}' "
            f"              THEN excluded.source ELSE {TABLE}.source END,"
            f"  provider=excluded.provider,"
            f"  resolved_at=CASE WHEN excluded.validation_state='{STATE_RESOLVED}' "
            f"                   THEN excluded.resolved_at ELSE {TABLE}.resolved_at END,"
            f"  validation_state=excluded.validation_state,"
            f"  attempts={TABLE}.attempts+1,"
            f"  last_error_type=excluded.last_error_type,"
            f"  last_error_at=excluded.last_error_at,"
            f"  updated_at=excluded.updated_at",
            (str(mint), decimals, str(source), str(provider),
             (now if validation_state == STATE_RESOLVED else None),
             str(validation_state), str(error_type or ""),
             (now if error_type else None), now),
        )
    except Exception as exc:
        log.debug("[TOKEN_METADATA] write failed mint=%s: %s",
                  str(mint)[:16], type(exc).__name__)
    finally:
        if owned:
            try:
                c.close()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

def _validate_decimals(payload: Any) -> Tuple[Optional[int], str]:
    """Strict extraction. A missing field is a failure, never zero.

    The prior implementation used ``int((...).get("decimals") or 0)``, which
    silently yields 0 for a malformed or partial response. decimals=0 scales a
    raw token amount by 10**0, so a sell would be sized wrong by orders of
    magnitude. Absent means unresolved.
    """
    try:
        value = ((payload or {}).get("value") or {})
    except Exception:
        return None, "malformed_payload"
    if not isinstance(value, dict) or "decimals" not in value:
        return None, "decimals_field_absent"
    raw = value.get("decimals")
    if isinstance(raw, bool) or not isinstance(raw, int):
        try:
            raw = int(str(raw).strip())
        except Exception:
            return None, "decimals_not_integer"
    if raw < MIN_DECIMALS or raw > MAX_DECIMALS:
        return None, f"decimals_out_of_range:{raw}"
    return int(raw), ""


def _rpc_get_token_supply(url: str, mint: str,
                          connect_timeout: float,
                          read_timeout: float) -> Tuple[Optional[int], str]:
    import requests
    try:
        resp = requests.post(
            url,
            json={"jsonrpc": "2.0", "id": 1, "method": "getTokenSupply",
                  "params": [str(mint), {"commitment": "confirmed"}]},
            timeout=(float(connect_timeout), float(read_timeout)),
        )
    except Exception as exc:
        return None, f"transport:{type(exc).__name__}"
    try:
        if resp.status_code != 200:
            return None, f"http_{resp.status_code}"
        payload = resp.json()
    except Exception as exc:
        return None, f"decode:{type(exc).__name__}"
    if payload.get("error") is not None:
        return None, "rpc_error"
    return _validate_decimals(payload.get("result"))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def resolve_decimals(mint: str, *, conn=None) -> Dict[str, Any]:
    """Resolve decimals for ``mint``, returning full provenance.

    Returns a dict with: decimals, source, provider, cache_hit, elapsed_sec,
    validation_state, failure_type. ``decimals`` is None only when
    validation_state is UNRESOLVED.
    """
    started = time.time()
    mint = str(mint or "").strip()
    out: Dict[str, Any] = {
        "mint": mint, "decimals": None, "source": "", "provider": "",
        "cache_hit": False, "elapsed_sec": 0.0,
        "validation_state": STATE_UNRESOLVED, "failure_type": "",
    }
    if not mint:
        out.update(failure_type="empty_mint", elapsed_sec=time.time() - started)
        return out

    # Layer 1: in-process memo.
    with _MEMO_LOCK:
        memo = _MEMO.get(mint)
    if memo is not None:
        out.update(decimals=int(memo), source="memo", cache_hit=True,
                   validation_state=STATE_RESOLVED,
                   elapsed_sec=time.time() - started)
        return out

    # Layer 2: shared SQLite cache.
    row = _read_row(mint, conn=conn)
    if row and row.get("validation_state") == STATE_RESOLVED \
            and row.get("decimals") is not None:
        try:
            d = int(row["decimals"])
        except Exception:
            d = None
        if d is not None and MIN_DECIMALS <= d <= MAX_DECIMALS:
            with _MEMO_LOCK:
                _MEMO[mint] = d
            out.update(decimals=d, source="sqlite_cache",
                       provider=str(row.get("provider") or ""), cache_hit=True,
                       validation_state=STATE_RESOLVED,
                       elapsed_sec=time.time() - started)
            return out

    # Negative cache: suppress a retry storm, briefly.
    neg_ttl = _cfg_float("TOKEN_METADATA_NEGATIVE_TTL_SEC", NEGATIVE_TTL_SEC_DEFAULT)
    if row and row.get("validation_state") == STATE_UNRESOLVED:
        last = float(row.get("last_error_at") or 0.0)
        if last > 0 and (time.time() - last) < neg_ttl:
            out.update(failure_type=f"negative_cached:{row.get('last_error_type') or 'unknown'}",
                       provider=str(row.get("provider") or ""),
                       source="negative_cache", cache_hit=True,
                       elapsed_sec=time.time() - started)
            return out

    # Layer 3: bounded network resolution with provider failover.
    endpoints = _endpoints()
    if not endpoints:
        _write_row(mint, validation_state=STATE_UNRESOLVED,
                   error_type="no_endpoint_configured", conn=conn)
        out.update(failure_type="no_endpoint_configured",
                   elapsed_sec=time.time() - started)
        return out

    max_attempts = max(1, _cfg_int("TOKEN_METADATA_MAX_ATTEMPTS", MAX_ATTEMPTS_DEFAULT))
    ct = _cfg_float("TOKEN_METADATA_CONNECT_TIMEOUT_SEC", CONNECT_TIMEOUT_SEC_DEFAULT)
    rt = _cfg_float("TOKEN_METADATA_READ_TIMEOUT_SEC", READ_TIMEOUT_SEC_DEFAULT)

    last_failure = "unknown"
    last_provider = ""
    for attempt in range(max_attempts):
        url = endpoints[attempt % len(endpoints)]
        last_provider = provider_label(url)
        decimals, failure = _rpc_get_token_supply(url, mint, ct, rt)
        if decimals is not None:
            with _MEMO_LOCK:
                _MEMO[mint] = int(decimals)
            _write_row(mint, decimals=int(decimals), source="rpc_getTokenSupply",
                       provider=last_provider, validation_state=STATE_RESOLVED,
                       conn=conn)
            out.update(decimals=int(decimals), source="rpc_getTokenSupply",
                       provider=last_provider, validation_state=STATE_RESOLVED,
                       elapsed_sec=time.time() - started)
            return out
        last_failure = failure

    _write_row(mint, validation_state=STATE_UNRESOLVED, provider=last_provider,
               error_type=last_failure, conn=conn)
    log.warning("[TOKEN_METADATA] unresolved mint=%s failure=%s provider=%s",
                mint[:16], last_failure, last_provider)
    out.update(failure_type=last_failure, provider=last_provider,
               elapsed_sec=time.time() - started)
    return out


def get_decimals(mint: str, *, conn=None) -> int:
    """Return decimals or raise TokenMetadataUnresolved. Never guesses."""
    r = resolve_decimals(mint, conn=conn)
    if r.get("decimals") is None:
        raise TokenMetadataUnresolved(
            mint, r.get("failure_type") or "unresolved",
            provider=r.get("provider") or "", detail=r.get("source") or "")
    return int(r["decimals"])


def is_resolved(mint: str, *, conn=None) -> bool:
    """Non-raising precondition check, e.g. before FIRE_PATH_OPEN."""
    try:
        return resolve_decimals(mint, conn=conn).get("decimals") is not None
    except Exception:
        return False


def _reset_memo_for_tests() -> None:
    with _MEMO_LOCK:
        _MEMO.clear()
