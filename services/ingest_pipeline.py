from __future__ import annotations



import json
import logging
import sqlite3
import sys
import time
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

load_dotenv(BASE_DIR / ".env", override=True)

from core.schema import get_connection, update_heartbeat, init_db, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [INGEST_PIPELINE] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("ingest_pipeline")

SERVICE_NAME = "ingest_pipeline"

# Internal lane heartbeat names retained for visibility / backward observability.
HB_INGEST   = "ingest"
HB_RESOLVER = "resolver"
HB_WEAVER   = "signal_engine"

import os
RPC_URL = os.getenv("HELIUS_RPC", "").strip().strip('"').strip("'")
if not RPC_URL:
    RPC_URL = os.getenv("QUICKNODE_RPC", "").strip().strip('"').strip("'")
if not RPC_URL:
    RPC_URL = os.getenv("SOLANA_RPC_URL", "").strip().strip('"').strip("'")

HTTP_TIMEOUT = 8
VALIDATION_BATCH_SIZE = 200
RESOLVER_BATCH_SIZE = 6  # reduced from 100 — prevents RPC overload
VALIDATION_SLEEP = 1.0
RESOLVER_SLEEP = 0.6
PIPELINE_SLEEP = 1.0
CLAIM_SECONDS = 45  # was 30 — reduces latency floor
MAX_NOTE_LEN = 255


# ------------------------------------------------------------------------------
# SCHEMA SAFETY
# ------------------------------------------------------------------------------

def _ensure_pipeline_schema() -> None:
    """
    Non-breaking schema adds only.

    LOCK SAFETY: Each ALTER TABLE runs in its own connection so a lock on
    one operation never blocks the others. CREATE TABLE IF NOT EXISTS and
    CREATE INDEX IF NOT EXISTS are no-ops when the object already exists,
    so they are safe to run on every startup - they hold the write lock for
    microseconds only. ALTER TABLE is the only operation that can block;
    wrapping each in its own try/except means a transient lock on one column
    add does not abort the rest of the migration.

    All columns are added by fix_raw_dna.py and fix_market_snapshots.py before
    first launch, so these ALTERs are no-ops in normal operation.
    """
    # raw_dna columns - one connection per ALTER to minimise lock window
    _raw_dna_cols = [
        ("claim_until",       "REAL"),
        ("resolved_at",       "REAL"),
        ("resolution_status", "TEXT"),
        ("resolution_note",   "TEXT"),
        ("mint_address",      "TEXT"),
        ("mint_confidence",   "REAL"),
        ("resolution_method", "TEXT"),
        ("forensic_bundle",   "TEXT"),   # PATCH 1: signature/slot/block_time/logs
    ]
    try:
        with get_connection() as conn:
            raw_cols = {r["name"] for r in conn.execute("PRAGMA table_info(raw_dna)").fetchall()}
    except Exception:
        raw_cols = set()

    for col, col_type in _raw_dna_cols:
        if col not in raw_cols:
            try:
                with get_connection() as conn:
                    conn.execute(f"ALTER TABLE raw_dna ADD COLUMN {col} {col_type}")
                    conn.commit()
            except Exception:
                pass  # already exists or lock - prelaunch script handles this

    # Tables and indexes - CREATE IF NOT EXISTS is always safe
    try:
        with get_connection() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS resolved_transactions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    tx_hash TEXT UNIQUE,
                    mint_address TEXT,
                    mint_confidence REAL DEFAULT 0,
                    resolution_method TEXT DEFAULT '',
                    token_name TEXT DEFAULT '',
                    owner_address TEXT DEFAULT '',
                    block_time REAL,
                    raw_dna_id INTEGER,
                    created_at REAL NOT NULL,
                    note TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS anomaly_queue (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_service TEXT NOT NULL,
                    anomaly_type TEXT NOT NULL,
                    payload_json TEXT DEFAULT '{}',
                    created_at REAL NOT NULL
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_dna_state_id ON raw_dna(processed_state, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_raw_dna_claim_until ON raw_dna(claim_until)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_resolved_tx_hash ON resolved_transactions(tx_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_market_snapshots_tx_hash ON market_snapshots(tx_hash)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ms_mint_created ON market_snapshots(mint_address, created_at)")
            conn.commit()
    except Exception as e:
        log.warning("_ensure_pipeline_schema tables/indexes failed (non-fatal): %s", e)


# ------------------------------------------------------------------------------
# UTILITIES
# ------------------------------------------------------------------------------

def _safe_note(text: Any) -> str:
    return str(text or "")[:MAX_NOTE_LEN]


def _safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, "", "null"):
            return default
        return float(value)
    except Exception:
        return default


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, "", "null"):
            return default
        return int(float(value))
    except Exception:
        return default


def _json_loads(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return None


def _table_columns(conn: sqlite3.Connection, table_name: str) -> set[str]:
    try:
        return {r["name"] for r in conn.execute(f"PRAGMA table_info({table_name})").fetchall()}
    except Exception:
        return set()


def _db_write_retry(fn, attempts: int = 6, base_sleep: float = 0.15):
    last_exc = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except sqlite3.OperationalError as exc:
            last_exc = exc
            if "locked" not in str(exc).lower() or attempt == attempts:
                raise
            time.sleep(base_sleep * attempt)
    if last_exc:
        raise last_exc


def _emit_anomaly(anomaly_type: str, payload: dict) -> None:
    try:
        with get_connection() as conn:
            conn.execute("""
                INSERT INTO anomaly_queue (source_service, anomaly_type, payload_json, created_at)
                VALUES (?, ?, ?, ?)
            """, (SERVICE_NAME, anomaly_type, json.dumps(payload, ensure_ascii=False), time.time()))
            conn.commit()
    except Exception:
        pass


# ------------------------------------------------------------------------------
# INGEST LANE - former ingest_engine.py
# ------------------------------------------------------------------------------

def _is_reasonable_tx_hash(value: Any) -> bool:
    return isinstance(value, str) and 32 <= len(value.strip()) <= 120


def _parse_logs(raw_logs: Any) -> list[str] | None:
    logs = _json_loads(raw_logs)
    if not isinstance(logs, list) or not logs:
        return None
    cleaned = [item.strip() for item in logs if isinstance(item, str) and item.strip()]
    return cleaned if cleaned else None


def _has_minimum_signal(logs: list[str]) -> bool:
    """
    Modern pipeline note:
    Transaction logs are too inconsistent across providers and transaction
    shapes to serve as a hard reject gate at ingest time.

    Ingest validation should reject only structurally broken rows.
    Resolver / qualifier stages should decide quality and tradeability.
    """
    if logs is None:
        return True
    if not isinstance(logs, list):
        return False
    return True


def _purge_stale_validated_signals(conn: sqlite3.Connection, max_age_seconds: float = 7200) -> int:
    """
    Purge state=1 rows older than max_age_seconds that have never been claimed.
    These are signals that sat in the resolver queue too long to be tradeable.
    Uses COALESCE(created_at, first_seen_at) to handle rows from pump_monitor
    which writes first_seen_at but not created_at.
    Called from _validate_raw_rows() inside its own short transaction.
    Returns count of rows purged.
    """
    cutoff = time.time() - max_age_seconds
    cur = conn.execute("""
        UPDATE raw_dna
        SET processed_state = -1,
            resolution_status = 'PURGED_STALE',
            resolution_note = 'stale_validated_signal'
        WHERE processed_state = 1
          AND claim_until IS NULL
          AND COALESCE(created_at, first_seen_at) IS NOT NULL
          AND COALESCE(created_at, first_seen_at) < ?
    """, (cutoff,))
    return cur.rowcount if cur else 0


def _validate_raw_rows() -> tuple[int, int, int]:
    """
    Validate raw_dna rows from pump_monitor and advance them to the resolver.

    ARCHITECTURE NOTE (HTTP polling mode):
    pump_monitor inserts rows with logs="[]" (empty placeholder).
    The resolver (_resolve_one) fetches real TX data via RPC independently -
    it does not use the logs field at all.

    The old log-content check (_has_minimum_signal) was designed for the
    WebSocket architecture where logs were populated at insert time.
    With HTTP polling, logs are always empty at insert time, so applying
    that check here would reject 100% of rows before the resolver runs.

    Correct validation for HTTP polling mode:
      - Reject only rows with an invalid tx_hash (structural garbage).
      - Pass all rows with a valid tx_hash to processed_state=1 regardless
        of whether logs is empty. The resolver will fetch and validate the
        actual transaction data and set the final outcome.
    """
    validated = 0
    rejected = 0
    purged = 0

    # Fetch rows first (read-only, no lock held)
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT id, tx_hash, logs
                FROM raw_dna
                WHERE processed_state = 0
                ORDER BY id DESC
                LIMIT ?
            """, (VALIDATION_BATCH_SIZE,)).fetchall()
            rows = [dict(r) for r in rows]
    except Exception as e:
        log.warning("validate fetch failed: %s", e)
        rows = []

    # Process each row in its own short transaction - never hold lock across batch
    for row in rows:
        row_id  = row["id"]
        tx_hash = row["tx_hash"]

        try:
            # Fast-kill empty placeholder rows from pump_monitor HTTP polling.
            # These have logs="[]" and no mint - the resolver cannot do anything
            # with them and burning resolver RPC credits on them is pure waste.
            # pump_monitor uses HTTP polling: logs="[]" and mint_address=None
            # are normal at insert time. Do NOT reject on empty payload.
            # Resolver fetches real TX data via RPC independently.

            with get_connection() as conn:
                if not _is_reasonable_tx_hash(tx_hash):
                    conn.execute("""
                        UPDATE raw_dna
                        SET processed_state = -1,
                            resolution_status = 'REJECTED_INGEST',
                            resolution_note = ?
                        WHERE id = ?
                    """, ("INVALID_TX_HASH", row_id))
                    rejected += 1
                else:
                    conn.execute("""
                        UPDATE raw_dna
                        SET processed_state = 1,
                            resolution_status = 'VALIDATED',
                            resolution_note = ''
                        WHERE id = ?
                    """, (row_id,))
                    validated += 1
                conn.commit()
        except Exception as e:
            log.debug("validate row=%d failed: %s", row_id, e)

    # Purge stale in its own short transaction
    try:
        with get_connection() as conn:
            purged = _purge_stale_validated_signals(conn)
            conn.commit()
    except Exception:
        purged = 0

    note = f"validated={validated} rejected={rejected} purged={purged}"
    update_heartbeat(
        HB_INGEST,
        "ALIVE",
        note if (validated or rejected or purged) else "Idle - Awaiting fresh DNA",
        work_processed=validated,
        last_success_at=time.time() if validated > 0 else None,
    )
    return validated, rejected, purged


# ------------------------------------------------------------------------------
# RESOLVER LANE - former tx_resolver.py
# ------------------------------------------------------------------------------

def _heal_stuck_claims() -> int:
    now = time.time()
    with get_connection() as conn:
        cur = conn.execute("""
            UPDATE raw_dna
            SET processed_state = 1,
                claim_until = NULL,
                resolution_status = COALESCE(resolution_status, 'CLAIM_EXPIRED'),
                resolution_note = ?
            WHERE processed_state = 99
              AND claim_until IS NOT NULL
              AND claim_until < ?
        """, ("resolver_claim_expired_released", now))
        conn.commit()
        released = cur.rowcount if cur else 0

    if released:
        _emit_anomaly("RESOLVER_STUCK_CLAIMS", {"released": released, "ts": now})
    return released


def _claim_resolver_rows(limit: int = RESOLVER_BATCH_SIZE) -> list[dict]:
    now = time.time()
    lease_until = now + CLAIM_SECONDS

    claimed_rows: list[dict] = []
    with get_connection() as conn:
        raw_cols = _table_columns(conn, "raw_dna")
        select_cols = ["id", "tx_hash", "logs"]
        if "created_at" in raw_cols:
            select_cols.append("created_at")
        else:
            select_cols.append("NULL AS created_at")

        rows = conn.execute(f"""
            SELECT {", ".join(select_cols)}
            FROM raw_dna
            WHERE processed_state = 1
              AND (claim_until IS NULL OR claim_until < ?)
              AND COALESCE(first_seen_at, created_at, 0) > ?
            ORDER BY id DESC
            LIMIT ?
        """, (now, now - 600, limit)).fetchall()

        # Kill stale state=1 rows older than 2 minutes - Helius returns null
        # for old transactions so these will never resolve. Kill them now
        # instead of recycling them through failed=40 every cycle.
        conn.execute("""
            UPDATE raw_dna SET processed_state=-1,
                resolution_note='STALE_RESOLVER_KILLED'
            WHERE processed_state=1
              AND COALESCE(first_seen_at, created_at, 0) > 0
              AND COALESCE(first_seen_at, created_at, 0) < ?
        """, (now - 600,))
        conn.commit()

        for row in rows:
            cur = conn.execute("""
                UPDATE raw_dna
                SET processed_state = 99,
                    claim_until = ?
                WHERE id = ?
                  AND processed_state = 1
                  AND (claim_until IS NULL OR claim_until < ?)
            """, (lease_until, row["id"], now))
            if cur.rowcount == 1:
                claimed_rows.append(dict(row))

        conn.commit()

    return claimed_rows


def _rpc_post(session: requests.Session, method: str, params: list[Any]) -> Any:
    if not RPC_URL:
        raise RuntimeError("No RPC URL configured")
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": params,
    }
    resp = session.post(RPC_URL, json=payload, timeout=HTTP_TIMEOUT)
    resp.raise_for_status()
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"RPC {method} error: {data['error']}")
    return data.get("result")


def _extract_mint_from_meta(tx_result: dict) -> tuple[Optional[str], float, str, str]:
    """
    Conservative resolver:
    - prefers non-native, non-stable token balances
    - confidence strongest for a single clear mint candidate
    """
    meta = tx_result.get("meta") or {}
    if not isinstance(meta, dict):
        return None, 0.0, "meta_missing", "TX_META_ERR"

    post = meta.get("postTokenBalances") or []
    pre = meta.get("preTokenBalances") or []

    candidates: dict[str, dict[str, Any]] = {}

    def _walk(rows: list[dict], phase: str) -> None:
        for item in rows:
            if not isinstance(item, dict):
                continue
            mint = str(item.get("mint") or "").strip()
            owner = str(item.get("owner") or "").strip()
            if not mint:
                continue
            entry = candidates.setdefault(mint, {"owner": owner, "post": 0.0, "pre": 0.0})
            if owner and not entry["owner"]:
                entry["owner"] = owner

            amount = 0.0
            ui_token = item.get("uiTokenAmount") or {}
            if isinstance(ui_token, dict):
                raw_amt = ui_token.get("amount")
                decimals = _safe_int(ui_token.get("decimals"), 0)
                try:
                    amount = int(raw_amt) / (10 ** decimals) if raw_amt is not None else 0.0
                except Exception:
                    amount = _safe_float(ui_token.get("uiAmount"), 0.0)

            entry[phase] = max(entry[phase], amount)

    _walk(post, "post")
    _walk(pre, "pre")

    # Filter out obvious non-targets.
    reject = {
        "So11111111111111111111111111111111111111112",
        "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",
        "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",
    }

    scored: list[tuple[str, float, str]] = []
    for mint, d in candidates.items():
        if mint in reject:
            continue
        delta = _safe_float(d.get("post"), 0.0) - _safe_float(d.get("pre"), 0.0)
        if delta > 0:
            conf = 0.82
            if mint.endswith("pump"):
                conf = 0.89
            scored.append((mint, conf, d.get("owner") or ""))

    if not scored:
        return None, 0.0, "no_positive_token_delta", "NO_MINT"

    # Highest confidence first, then longest mint string for stability.
    scored.sort(key=lambda x: (x[1], len(x[0])), reverse=True)
    mint, confidence, owner = scored[0]
    method = "parsed_consensus" if confidence >= 0.85 else "token_balances_new"
    return mint, confidence, method, owner


def _resolve_one(session: requests.Session, tx_hash: str) -> tuple[str, str, Optional[dict]]:
    params = [
        tx_hash,
        {
            "encoding": "jsonParsed",
            "maxSupportedTransactionVersion": 0,
            "commitment": "confirmed",
        },
    ]
    tx_result = _rpc_post(session, "getTransaction", params)
    if not tx_result:
        return "failed", "NO_TRANSACTION", None

    mint, confidence, method, owner = _extract_mint_from_meta(tx_result)
    if not mint:
        return "failed", "NO_MINT", None

    block_time = _safe_float(tx_result.get("blockTime"), 0.0)
    token_name = str(mint).strip()  # preserve old signal_engine contract

    # PATCH 1: Persist full forensic bundle — signature, slot, block_time, logMessages.
    # Previously logMessages was discarded. Now persisted so POLARIS/IVARIS can
    # access raw log evidence during debates in sovereign_governor.py.
    import json as _json
    _meta         = tx_result.get("meta") or {}
    _log_messages = _meta.get("logMessages") or []
    if not isinstance(_log_messages, list):
        _log_messages = []
    _slot = tx_result.get("slot")
    forensic_bundle = _json.dumps({
        "signature":  tx_hash,
        "slot":       _slot,
        "block_time": block_time,
        "logs":       _log_messages[:50],   # cap 50 entries for WAL safety
    }, default=str)

    payload = {
        "mint_address":      mint,
        "mint_confidence":   confidence,
        "resolution_method": method,
        "owner_address":     owner,
        "block_time":        block_time,
        "token_name":        token_name,
        "forensic_bundle":   forensic_bundle,
    }
    return "resolved", "OK", payload


def _release_claim(row_id: int, new_state: int, status: str, note: str, payload: Optional[dict] = None) -> None:
    payload = payload or {}

    def _write() -> None:
        with get_connection() as conn:
            conn.execute("""
                UPDATE raw_dna
                SET processed_state = ?,
                    claim_until = NULL,
                    resolved_at = ?,
                    resolution_status = ?,
                    resolution_note = ?,
                    mint_address = COALESCE(?, mint_address),
                    mint_confidence = COALESCE(?, mint_confidence),
                    resolution_method = COALESCE(?, resolution_method),
                    forensic_bundle = COALESCE(?, forensic_bundle)
                WHERE id = ?
            """, (
                new_state,
                time.time(),
                status,
                _safe_note(note),
                payload.get("mint_address"),
                payload.get("mint_confidence"),
                payload.get("resolution_method"),
                payload.get("forensic_bundle"),
                row_id,
            ))
            conn.commit()

    _db_write_retry(_write)


def _record_resolution(tx_hash: str, row_id: int, payload: dict, note: str = "") -> None:
    def _write() -> None:
        with get_connection() as conn:
            cols = _table_columns(conn, "resolved_transactions")

            if not cols:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS resolved_transactions (
                        id INTEGER PRIMARY KEY AUTOINCREMENT,
                        tx_hash TEXT UNIQUE,
                        mint_address TEXT,
                        mint_confidence REAL DEFAULT 0,
                        resolution_method TEXT DEFAULT '',
                        token_name TEXT DEFAULT '',
                        owner_address TEXT DEFAULT '',
                        block_time REAL,
                        raw_dna_id INTEGER,
                        created_at REAL,
                        note TEXT DEFAULT ''
                    )
                """)
                conn.commit()
                cols = _table_columns(conn, "resolved_transactions")

            insert_fields: list[str] = []
            insert_values: list[Any] = []

            def add(field: str, value: Any) -> None:
                if field in cols:
                    insert_fields.append(field)
                    insert_values.append(value)

            add("tx_hash", tx_hash)
            add("mint_address", payload.get("mint_address"))
            add("mint_confidence", payload.get("mint_confidence"))
            add("resolution_method", payload.get("resolution_method"))
            add("token_name", payload.get("token_name"))
            add("owner_address", payload.get("owner_address"))
            add("block_time", payload.get("block_time"))
            add("raw_dna_id", row_id)
            add("created_at", time.time())
            add("note", _safe_note(note))

            if "tx_hash" not in insert_fields:
                raise RuntimeError("resolved_transactions schema missing tx_hash")

            update_fields = []
            for field in ("mint_address", "mint_confidence", "resolution_method", "token_name", "owner_address", "block_time", "raw_dna_id", "note"):
                if field in cols:
                    update_fields.append(f"{field}=excluded.{field}")

            sql = f"""
                INSERT INTO resolved_transactions ({", ".join(insert_fields)})
                VALUES ({", ".join("?" for _ in insert_fields)})
                ON CONFLICT(tx_hash) DO UPDATE SET
                    {", ".join(update_fields) if update_fields else "tx_hash=excluded.tx_hash"}
            """
            conn.execute(sql, tuple(insert_values))
            conn.commit()

    _db_write_retry(_write)


def _resolve_one_safe(row: dict) -> tuple[int, str, int]:
    """
    Thread-safe wrapper for _resolve_one.
    Each thread gets its own requests.Session — NOT shared.
    Returns (row_id, outcome, resolved_count)
    """
    row_id  = int(row["id"])
    tx_hash = str(row["tx_hash"] or "").strip()
    # Each thread owns its session — thread-safe, no sharing
    with requests.Session() as session:
        try:
            status, note, payload = _resolve_one(session, tx_hash)
            if status == "resolved" and payload:
                _record_resolution(tx_hash, row_id, payload, note)
                _release_claim(row_id, 2, "RESOLVED", note, payload)
                return row_id, "resolved", 1
            else:
                _release_claim(row_id, 1, "RETRY", note, payload)
                return row_id, "failed", 0
        except requests.RequestException as exc:
            _release_claim(row_id, 1, "RETRY", f"HTTP_ERR:{exc}")
            return row_id, "failed", 0
        except sqlite3.OperationalError as exc:
            _release_claim(row_id, 1, "RETRY", f"SQLITE_ERR:{exc}")
            return row_id, "failed", 0
        except Exception as exc:
            _release_claim(row_id, -1, "RESOLUTION_EXCEPTION", f"{type(exc).__name__}:{exc}")
            _emit_anomaly("RESOLUTION_EXCEPTION", {
                "tx_hash": tx_hash, "row_id": row_id, "error": str(exc),
            })
            return row_id, "exception", 0


def _resolve_transactions() -> tuple[int, int, int]:
    """
    Bounded parallel RPC resolver used by the 14/15/16 July profitable states.

    A cycle owns exactly one executor and cannot create a successor pool until
    every worker in the current pool has returned. This prevents unresolved DNS
    workers accumulating across cycles after a network interruption.
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FuturesTimeoutError

    resolved = 0
    failed = 0
    timed_out = 0
    released = _heal_stuck_claims()
    claimed = _claim_resolver_rows()

    if not claimed:
        update_heartbeat(HB_RESOLVER, "ALIVE", f"idle released={released}")
        return resolved, failed, released

    max_workers = max(1, int(os.getenv("RESOLVER_MAX_WORKERS", "3")))
    try:
        deadline = float(get_config_value("RESOLVER_BATCH_DEADLINE_SEC", 90.0)) \
            if "get_config_value" in globals() else 90.0
    except Exception:
        deadline = 90.0
    deadline = max(15.0, deadline)

    # Deliberately use the context-managed lifecycle shared by every supplied
    # profitable archive. shutdown(wait=True) is the no-leak ownership boundary:
    # the next cycle cannot stack another executor over unfinished DNS workers.
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="sentinuity-resolver") as pool:
        futures = {pool.submit(_resolve_one_safe, row): row for row in claimed}
        try:
            for future in as_completed(futures, timeout=deadline):
                try:
                    _, outcome, count = future.result()
                    if outcome == "resolved":
                        resolved += count
                    else:
                        failed += 1
                except Exception as exc:
                    failed += 1
                    log.warning("Resolver future failed: %s", exc)
        except FuturesTimeoutError:
            timed_out = sum(1 for future in futures if not future.done())
            failed += timed_out
            log.warning(
                "Resolver deadline %.0fs reached: %d/%d unfinished. "
                "The bounded executor will drain before another cycle starts.",
                deadline, timed_out, len(futures),
            )

    note = (
        f"resolved={resolved} failed={failed} released={released} "
        f"workers={max_workers} bounded=1"
        + (f" deadline_timeouts={timed_out}" if timed_out else "")
    )
    update_heartbeat(
        HB_RESOLVER,
        "ALIVE",
        note,
        work_processed=resolved,
        last_success_at=time.time() if resolved > 0 else None,
    )
    return resolved, failed, released


# ------------------------------------------------------------------------------
# WEAVER LANE - former signal_engine.py
# ------------------------------------------------------------------------------

def _market_snapshots_columns(conn: sqlite3.Connection) -> set[str]:
    return {r["name"] for r in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}


def _insert_market_snapshot_from_resolution(conn: sqlite3.Connection, row: dict) -> bool:
    cols = _market_snapshots_columns(conn)

    tx_hash = str(row.get("tx_hash") or "").strip()
    mint = str(row.get("mint_address") or "").strip()
    mint_conf = _safe_float(row.get("mint_confidence"), 0.0)
    resolution_method = str(row.get("resolution_method") or "").strip()
    token_name = str(row.get("token_name") or mint).strip()  # preserve old contract
    block_time = _safe_float(row.get("block_time"), 0.0)
    created_at = time.time()

    if not tx_hash or not mint:
        return False

    # Skip if already woven by tx_hash.
    existing = conn.execute(
        "SELECT 1 FROM market_snapshots WHERE tx_hash=? LIMIT 1",
        (tx_hash,)
    ).fetchone()
    if existing:
        return False

    # Skip if this mint has ever already been traded by this machine.
    # Historical rows remain for analytics and replay, but execution is one-and-done.
    try:
        already_traded = conn.execute(
            "SELECT 1 FROM paper_positions WHERE mint_address=? LIMIT 1",
            (mint,),
        ).fetchone()
        if already_traded:
            return False
    except Exception:
        pass  # Never block ingest on dedup failure

    # Skip if this mint already has an active recent snapshot in flight.
    # Same mint with multiple tx_hashes causes duplicate snapshots that
    # flood the pipeline and waste supervisor/executor cycles.
    # Uses a 20-minute time-window (stable regardless of table size).
    try:
        dedup_cutoff = time.time() - 300  # 5-minute dedup window (was 1200s — too long after pipeline wipe)
        mint_existing = conn.execute("""
            SELECT 1 FROM market_snapshots
            WHERE mint_address=?
              AND candidate_state NOT IN ('vetoed','exited','mtm')
              AND COALESCE(first_seen_at, created_at, 0) > ?
            LIMIT 1
        """, (mint, dedup_cutoff)).fetchone()
        if mint_existing:
            return False
    except Exception:
        pass  # Never block ingest on dedup failure

    fields: list[str] = []
    values: list[Any] = []

    def add(field: str, value: Any) -> None:
        if field in cols:
            fields.append(field)
            values.append(value)

    add("tx_hash", tx_hash)
    add("token_name", token_name)
    add("mint_address", mint)
    add("mint_confidence", mint_conf)
    add("resolution_method", resolution_method)
    add("candidate_state", "pending")
    add("quality_status", "pending")
    add("quality_reason", "")
    add("price_status", "pending")
    add("price_attempts", 0)
    add("observed_price", None)
    add("is_tradeable", 0)
    add("execution_ready", 0)
    add("latched", 0)
    add("created_at", created_at)
    add("updated_at", created_at)
    add("first_seen_at", created_at)  # FIX: wall clock time we ingested it — NOT block_time
    # block_time = Solana chain timestamp, same for every tx in a batch, up to 45min stale.
    # first_seen_at must reflect when OUR pipeline saw the signal, not when the chain processed it.
    # block_time is already stored separately for analytics — do not use it as freshness anchor.
    add("token_age_seconds", 0)
    add("source_note", "woven_from_ingest_pipeline")
    # Fix 2: write correct freshness at insert time — new rows are HOT by definition.
    # Schema defaults (tier='COLD', freshness_score=0.0) cause 0-60s guardian lag
    # before the supervisor can see correct values. Writing correct values here
    # ensures immediate supervisor visibility without waiting for the next guardian sweep.
    add("tier", "HOT")
    add("freshness_score", 1.0)
    add("active_cognition", 1)

    if not fields:
        raise RuntimeError("market_snapshots has no compatible columns for weave insert")

    placeholders = ", ".join("?" for _ in fields)
    sql = f"""
        INSERT INTO market_snapshots ({", ".join(fields)})
        VALUES ({placeholders})
    """
    conn.execute(sql, tuple(values))
    return True


def _weave_signals() -> int:
    woven = 0
    with get_connection() as conn:
        rt_cols = _table_columns(conn, "resolved_transactions")
        order_col = "id" if "id" in rt_cols else "rowid"
        select_parts = [
            "rt.tx_hash",
            "rt.mint_address",
            "rt.mint_confidence",
            "rt.resolution_method",
            "rt.token_name",
            "rt.block_time",
            "rt.raw_dna_id" if "raw_dna_id" in rt_cols else "NULL AS raw_dna_id",
        ]

        rows = conn.execute(f"""
            SELECT {", ".join(select_parts)}
            FROM resolved_transactions rt
            LEFT JOIN market_snapshots ms
              ON ms.tx_hash = rt.tx_hash
            WHERE ms.tx_hash IS NULL
            ORDER BY rt.{order_col} DESC
            LIMIT 100
        """).fetchall()

        for row in rows:
            if _insert_market_snapshot_from_resolution(conn, dict(row)):
                raw_dna_id = row["raw_dna_id"]
                if raw_dna_id:
                    conn.execute("""
                        UPDATE raw_dna
                        SET processed_state = 3,
                            resolution_status = 'WOVEN',
                            resolution_note = 'market_snapshot_created'
                        WHERE id = ?
                    """, (raw_dna_id,))
                woven += 1

        conn.commit()

    update_heartbeat(
        HB_WEAVER,
        "ALIVE",
        f"woven={woven}" if woven else "idle",
        work_processed=woven,
        last_success_at=time.time() if woven > 0 else None,
    )
    return woven


# ------------------------------------------------------------------------------
# MAIN LOOP
# ------------------------------------------------------------------------------

def run_pipeline() -> None:
    init_db()
    _ensure_pipeline_schema()  # idempotent - no-op if schema is current

    log.info("INGEST PIPELINE ONLINE - validate - resolve - weave")
    update_heartbeat(SERVICE_NAME, "ALIVE", "booted")

    last_anomaly_flush = 0.0

    while True:
        cycle_started = time.time()

        validated = rejected = purged = 0
        resolved = failed = released = 0
        woven = 0

        try:
            validated, rejected, purged = _validate_raw_rows()
        except Exception as exc:
            log.exception("validate lane failed")
            update_heartbeat(HB_INGEST, "ERROR", _safe_note(exc))

        try:
            resolved, failed, released = _resolve_transactions()
        except Exception as exc:
            log.exception("resolver lane failed")
            update_heartbeat(HB_RESOLVER, "ERROR", _safe_note(exc))

        try:
            woven = _weave_signals()
        except Exception as exc:
            log.exception("weaver lane failed")
            update_heartbeat(HB_WEAVER, "ERROR", _safe_note(exc))

        total_work = validated + resolved + woven
        elapsed_ms = int((time.time() - cycle_started) * 1000)

        summary = (
            f"validated={validated} rejected={rejected} purged={purged} | "
            f"resolved={resolved} failed={failed} released={released} | "
            f"woven={woven} | cycle_ms={elapsed_ms}"
        )

        update_heartbeat(
            SERVICE_NAME,
            "ALIVE",
            summary,
            work_processed=total_work,
            last_success_at=time.time() if total_work > 0 else None,
        )

        # Lightweight anomaly signal on suspicious repeated resolver failure.
        if failed >= 10 and (time.time() - last_anomaly_flush) > 60:
            _emit_anomaly("RESOLVER_FAILURE_BURST", {
                "failed": failed,
                "released": released,
                "cycle_ms": elapsed_ms,
                "ts": time.time(),
            })
            last_anomaly_flush = time.time()

        time.sleep(PIPELINE_SLEEP)


if __name__ == "__main__":
    run_pipeline()