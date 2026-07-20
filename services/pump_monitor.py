"""
services/pump_monitor.py — SCOUT v5: HTTP POLLING ENGINE
=========================================================
Replaces logsSubscribe WebSocket with getSignaturesForAddress HTTP polling.

WHY:
QuickNode logsSubscribe bills ~20 credits per delivered notification.
Pump.fun generates ~130,000 notifications/day = ~2.6M credits/day on WSS.
HTTP polling getSignaturesForAddress costs ~20 credits per call regardless
of how many signatures are returned. At 1 poll/3s = 28,800 calls/day =
576,000 credits/day for discovery. Leaves 2M+ credits/day for resolver.

ARCHITECTURE:
  poll getSignaturesForAddress(PUMP_PROGRAM_ID, limit=100) every 3s
  → deduplicate signatures in memory + DB seed on restart
  → insert new signatures into raw_dna with processed_state=0
  → ingest validates state=0 rows as before
  → resolver picks up validated rows downstream
  → rest of pipeline completely unchanged

STABILITY PATCH:
  This version intentionally uses a SINGLE DB writer.
  SQLite WAL still only wants one writer lane at a time.
  The prior multi-worker ingest design could amplify lock contention and
  starve oracle / polaris under load.

File location: trading-bot/services/pump_monitor.py
"""

import asyncio
import logging
import os
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any, Optional

import requests
from dotenv import load_dotenv

# =============================================================================
# 0. BOOT / CONFIG
# =============================================================================

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat  # noqa: E402
from services.cognition_logger import log_cognition       # noqa: E402

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass

load_dotenv(BASE_DIR / ".env", override=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [SCOUT] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)

SERVICE_NAME = "pump_monitor"

# Build ordered fallback list — tries each in sequence on failure
_RPC_CANDIDATES = [
    # Chainstack REMOVED from pump_monitor — free plan blocks getSignaturesForAddress
    # (archive method, requires paid plan). Chainstack still used by ws_price_oracle WSS.
    os.getenv("HELIUS_RPC",      "").strip().strip('"').strip("'"),  # primary
    os.getenv("QUICKNODE_RPC",   "").strip().strip('"').strip("'"),
    os.getenv("SOLANA_RPC_URL",  "").strip().strip("'").strip('"'),
    "https://api.mainnet-beta.solana.com",  # public fallback last resort
]
_RPC_URLS = [u for u in _RPC_CANDIDATES if u.startswith("http")]
RPC_URL = _RPC_URLS[0] if _RPC_URLS else ""
_rpc_index = 0  # tracks which RPC we're currently using


def _rotate_rpc() -> str:
    """Rotate to next available RPC endpoint on failure."""
    global RPC_URL, _rpc_index
    if len(_RPC_URLS) <= 1:
        return RPC_URL
    _rpc_index = (_rpc_index + 1) % len(_RPC_URLS)
    RPC_URL = _RPC_URLS[_rpc_index]
    logging.warning("SCOUT: RPC rotated to endpoint %d/%d: %s...",
                    _rpc_index + 1, len(_RPC_URLS), RPC_URL[:40])
    return RPC_URL


# ── SIGNOFF SAFETY: pump_monitor runaway guard ────────────────────────────────
import time as _pump_time
_LAST_PUMP_CYCLE_TS = 0.0

def _pump_poll_guard(interval: float = 5.0) -> None:
    """Hard minimum delay between pump monitor cycles.
    3s minimum — fast enough to catch 0-120s launch windows without rate limits.
    """
    global _LAST_PUMP_CYCLE_TS
    interval = max(3.0, float(interval))
    now = _pump_time.monotonic()
    elapsed = now - _LAST_PUMP_CYCLE_TS
    if _LAST_PUMP_CYCLE_TS and elapsed < interval:
        _pump_time.sleep(interval - elapsed)
    _LAST_PUMP_CYCLE_TS = _pump_time.monotonic()
# ─────────────────────────────────────────────────────────────────────────────

PUMP_PROGRAM_ID = os.getenv("PUMP_PROGRAM_ID", "").strip()

POLL_INTERVAL_SECONDS = float(os.getenv("POLL_INTERVAL_SECONDS", "5.0"))
# 5s = 12/min = 720/hr = ~518k credits/month on Chainstack (17% of 3M free limit)
# Balanced: fast enough for 0-120s launch windows, well under rate limit.
POLL_LIMIT = int(os.getenv("SCOUT_POLL_LIMIT", "100"))
DEDUP_WINDOW = int(os.getenv("SCOUT_DEDUP_WINDOW", "10000"))

# NECESSARY STABILITY CHANGES:
# - one writer only
# - smaller batches
# - lighter queue pressure
BATCH_SIZE = int(os.getenv("SCOUT_BATCH_SIZE", "8"))
WORKER_COUNT = 1
QUEUE_MAXSIZE = int(os.getenv("SCOUT_QUEUE_MAXSIZE", "1000"))

# Less frequent write noise
HEARTBEAT_INTERVAL = int(os.getenv("SCOUT_HEARTBEAT_INTERVAL", "30"))
HTTP_TIMEOUT = int(os.getenv("SCOUT_HTTP_TIMEOUT", "10"))
COGNITION_PULSE_EVERY = int(os.getenv("SCOUT_COGNITION_PULSE_EVERY", "12"))

# Global state
_ingest_queue: Optional[asyncio.Queue] = None
_active_tasks: set[asyncio.Task] = set()
_seen_signatures: deque[str] = deque(maxlen=DEDUP_WINDOW)
_seen_set: set[str] = set()
_total_discovered: int = 0
_total_inserted: int = 0
_dropped_count: int = 0
_http_session: Optional[requests.Session] = None

# SINGLE writer lane only
DB_SEMAPHORE = asyncio.Semaphore(1)


# =============================================================================
# 1. VALIDATION
# =============================================================================

def validate_env() -> None:
    if not RPC_URL:
        raise RuntimeError("Missing QUICKNODE_RPC or SOLANA_RPC_URL in environment.")
    if not PUMP_PROGRAM_ID:
        raise RuntimeError("Missing PUMP_PROGRAM_ID in environment.")
    if not (RPC_URL.startswith("https://") or RPC_URL.startswith("http://")):
        raise RuntimeError("RPC_URL must be an HTTP endpoint (https://...)")

    calls_per_day = int(86400 / max(POLL_INTERVAL_SECONDS, 0.1))
    est_credits = calls_per_day * 20

    logging.info(f"SCOUT: RPC endpoint: {RPC_URL[:60]}...")
    logging.info(f"SCOUT: Pump program: {PUMP_PROGRAM_ID}")
    logging.info(
        f"SCOUT: {calls_per_day:,} polls/day × 20 credits = "
        f"{est_credits:,} discovery credits/day (budget: 2,666,667/day)"
    )
    logging.info(
        "SCOUT: stability mode active — single DB writer, batch_size=%d, queue_max=%d",
        BATCH_SIZE, QUEUE_MAXSIZE
    )


# =============================================================================
# 2. HTTP RPC
# =============================================================================

def get_session() -> requests.Session:
    global _http_session
    if _http_session is None:
        session = requests.Session()
        session.headers.update({"Content-Type": "application/json"})
        _http_session = session
    return _http_session


def rpc_call(method: str, params: list) -> Any:
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
    last_exc = None
    for attempt in range(len(_RPC_URLS)):
        try:
            response = get_session().post(RPC_URL, json=payload, timeout=HTTP_TIMEOUT)
            if response.status_code == 429:
                logging.warning("SCOUT: RPC 429 on %s... — rotating", RPC_URL[:30])
                _rotate_rpc()
                last_exc = requests.exceptions.HTTPError(response=response)
                continue
            response.raise_for_status()
            data = response.json()
            if "error" in data:
                raise RuntimeError(f"RPC error: {data['error']}")
            return data.get("result")
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout) as e:
            logging.warning("SCOUT: RPC connection error %s — rotating: %s", RPC_URL[:30], e)
            _rotate_rpc()
            last_exc = e
            continue
    raise last_exc or RuntimeError("All RPC endpoints failed")


def poll_signatures() -> list[str]:
    """
    Poll getSignaturesForAddress for Pump.fun program.
    Returns list of new (unseen, successful) signatures only.
    Cost: 20 credits per call regardless of result count.
    """
    result = rpc_call(
        "getSignaturesForAddress",
        [PUMP_PROGRAM_ID, {"limit": POLL_LIMIT, "commitment": "confirmed"}],
    )
    if not result or not isinstance(result, list):
        return []

    new_sigs: list[str] = []
    now = time.time()
    for item in result:
        sig = item.get("signature")
        if not sig:
            continue
        if item.get("err") is not None:
            continue
        if sig in _seen_set:
            continue
        # Only accept transactions that Helius has fully indexed (blockTime present).
        # When blockTime is None the transaction is not yet in Helius index;
        # getTransaction returns null so the resolver wastes a credit and produces
        # zero resolutions — the exact live starvation pattern (raw_dna high,
        # resolved_transactions = 0). These sigs reappear on the next poll (3s)
        # once indexed, with a valid blockTime. Dropping here costs nothing.
        block_time = item.get("blockTime")
        if block_time is None:
            continue
        # Discard transactions confirmed more than 120 seconds ago.
        # Prevents stale backlog replay after restarts or RPC disruptions.
        if (now - block_time) > 180:
            logging.debug(f"SCOUT DROP stale tx: age={now - block_time:.1f}s mint={item.get('mint','')[:12]}")
            continue
        new_sigs.append(sig)
    return new_sigs


def mark_seen(signatures: list[str]) -> None:
    """Add signatures to rolling dedup window."""
    for sig in signatures:
        if sig in _seen_set:
            continue

        if len(_seen_signatures) >= DEDUP_WINDOW:
            try:
                oldest = _seen_signatures.popleft()
                _seen_set.discard(oldest)
            except IndexError:
                pass

        _seen_signatures.append(sig)
        _seen_set.add(sig)


# =============================================================================
# 3. DATABASE
# =============================================================================

def ingest_batch_sync(signatures: list[str]) -> int:
    """
    Insert new signatures into raw_dna with processed_state=0.

    POLLING MODE:
    We still do discovery here, but we must write state=0 so ingest_engine
    can validate and advance the pipeline exactly as before.

    STABILITY PATCH:
    - single connection
    - small batch only
    - short transaction
    """
    if not signatures:
        return 0

    now = time.time()
    inserted = 0

    with get_connection() as conn:
        for sig in signatures:
            cur = conn.execute(
                """
                INSERT INTO raw_dna (tx_hash, logs, processed_state, first_seen_at, timestamp)
                VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(tx_hash) DO NOTHING
                """,
                (sig, "[]", now, now),
            )
            if cur.rowcount > 0:
                inserted += 1
        conn.commit()

    return inserted


def seed_seen_from_db() -> int:
    """Seed dedup window from existing DB signatures to prevent re-ingestion on restart."""
    with get_connection() as conn:
        rows = conn.execute(
            "SELECT tx_hash FROM raw_dna ORDER BY id DESC LIMIT ?",
            (DEDUP_WINDOW,),
        ).fetchall()

    sigs = [r["tx_hash"] for r in rows if r["tx_hash"]]
    mark_seen(sigs)
    return len(sigs)


def update_heartbeat_sync(status: str, note: str) -> None:
    update_heartbeat(SERVICE_NAME, status, note, 1, time.time())


# =============================================================================
# 4. TASK TRACKING
# =============================================================================

class suppress_exceptions:
    def __init__(self, *exceptions):
        self.exceptions = exceptions or (Exception,)

    def __enter__(self):
        return None

    def __exit__(self, exc_type, exc, tb):
        return exc is not None and isinstance(exc, self.exceptions)


def track_task(task: asyncio.Task) -> asyncio.Task:
    _active_tasks.add(task)

    def _cleanup(done: asyncio.Task) -> None:
        _active_tasks.discard(done)
        with suppress_exceptions(Exception):
            done.result()

    task.add_done_callback(_cleanup)
    return task


# =============================================================================
# 5. WORKERS
# =============================================================================

async def ingest_worker(worker_id: int) -> None:
    global _total_inserted
    assert _ingest_queue is not None

    while True:

        _pump_poll_guard(POLL_INTERVAL_SECONDS)
        batch: list[str] = []

        try:
            first = await _ingest_queue.get()
            batch.append(first)
        except Exception as exc:
            logging.error(f"Worker-{worker_id} queue.get() error: {exc}")
            await asyncio.sleep(0.1)
            continue

        while len(batch) < BATCH_SIZE:
            try:
                batch.append(_ingest_queue.get_nowait())
            except asyncio.QueueEmpty:
                break

        try:
            async with DB_SEMAPHORE:
                inserted = await asyncio.to_thread(ingest_batch_sync, batch)
            _total_inserted += inserted

            if inserted:
                logging.info(
                    f"Worker-{worker_id} batch={len(batch)} "
                    f"inserted={inserted} total={_total_inserted}"
                )

        except Exception as exc:
            logging.error(f"Worker-{worker_id} batch failed: {exc}")

        finally:
            for _ in batch:
                _ingest_queue.task_done()


async def heartbeat_loop() -> None:
    pulse_counter = 0

    while True:
        queue_depth = _ingest_queue.qsize() if _ingest_queue else 0
        note = (
            f"HTTP polling | discovered={_total_discovered} "
            f"inserted={_total_inserted} dropped={_dropped_count} "
            f"queue={queue_depth}"
        )

        try:
            await asyncio.to_thread(update_heartbeat_sync, "ALIVE", note)
        except Exception:
            pass

        pulse_counter += 1
        if pulse_counter % max(1, COGNITION_PULSE_EVERY) == 0:
            try:
                await asyncio.to_thread(
                    log_cognition,
                    "SIGNAL",
                    f"Scout scanning via HTTP polling — "
                    f"{_total_discovered} signatures sensed, "
                    f"{_total_inserted} woven into raw_dna, "
                    f"queue depth {queue_depth}.",
                )
            except Exception:
                pass

        await asyncio.sleep(HEARTBEAT_INTERVAL)


# =============================================================================
# 6. MAIN POLL LOOP
# =============================================================================

async def poll_loop() -> None:
    global _total_discovered, _dropped_count
    assert _ingest_queue is not None

    consecutive_errors = 0
    backoff_seconds = 2.0

    logging.info(
        f"SCOUT: HTTP polling started — interval={POLL_INTERVAL_SECONDS}s "
        f"limit={POLL_LIMIT} program={PUMP_PROGRAM_ID}"
    )

    try:
        seeded = await asyncio.to_thread(seed_seen_from_db)
        logging.info(f"SCOUT: Seeded dedup from DB with {seeded:,} signatures")
    except Exception as exc:
        logging.warning(f"SCOUT: Could not seed dedup from DB: {exc}")

    while True:
        loop_start = time.monotonic()

        # Skip HTTP poll if WebSocket already fired in last 4s (saves credits)
        try:
            from services.ws_pump_monitor import should_skip_next_poll
            if should_skip_next_poll():
                consecutive_errors = 0
                elapsed = time.monotonic() - loop_start
                await asyncio.sleep(max(0.1, POLL_INTERVAL_SECONDS - elapsed))
                continue
        except Exception:
            pass

        try:
            new_sigs = await asyncio.to_thread(poll_signatures)

            if new_sigs:
                mark_seen(new_sigs)
                _total_discovered += len(new_sigs)

                queued = 0
                for sig in new_sigs:
                    if _ingest_queue.full():
                        # Drop OLDEST signal not newest — preserve alpha
                        try:
                            _ingest_queue.get_nowait()
                        except Exception:
                            pass
                        _dropped_count += 1
                        continue
                    await _ingest_queue.put(sig)
                    queued += 1

                logging.info(
                    f"SCOUT: {len(new_sigs)} new sigs | queued={queued} "
                    f"total_discovered={_total_discovered}"
                )

            consecutive_errors = 0
            backoff_seconds = 2.0

        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if status == 429:
                logging.warning(f"SCOUT: Rate limited (429) — backoff {backoff_seconds}s")
                try:
                    await asyncio.to_thread(
                        update_heartbeat_sync,
                        "DEGRADED",
                        f"Rate limited — backoff {backoff_seconds}s"
                    )
                except Exception:
                    pass

                await asyncio.sleep(backoff_seconds)
                backoff_seconds = min(backoff_seconds * 2, 60.0)
                continue

            consecutive_errors += 1
            logging.error(f"SCOUT: HTTP {status}: {exc}")

        except Exception as exc:
            consecutive_errors += 1
            logging.warning(f"SCOUT: Poll error #{consecutive_errors}: {exc}")

            if consecutive_errors >= 5:
                try:
                    await asyncio.to_thread(
                        update_heartbeat_sync,
                        "DEGRADED",
                        f"Poll errors: {exc}"
                    )
                except Exception:
                    pass

            await asyncio.sleep(min(backoff_seconds * consecutive_errors, 30.0))
            continue

        elapsed = time.monotonic() - loop_start
        await asyncio.sleep(max(0.1, POLL_INTERVAL_SECONDS - elapsed))


# =============================================================================
# 7. SHUTDOWN / MAIN
# =============================================================================

async def shutdown() -> None:
    for task in list(_active_tasks):
        task.cancel()

    if _active_tasks:
        await asyncio.gather(*_active_tasks, return_exceptions=True)

    with suppress_exceptions(Exception):
        await asyncio.to_thread(update_heartbeat_sync, "OFFLINE", "Scout stopped")


async def main() -> None:
    global _ingest_queue

    validate_env()
    _ingest_queue = asyncio.Queue(maxsize=QUEUE_MAXSIZE)

    for i in range(WORKER_COUNT):
        track_task(asyncio.create_task(ingest_worker(i + 1)))

    track_task(asyncio.create_task(heartbeat_loop()))

    try:
        await asyncio.to_thread(
            log_cognition,
            "SIGNAL",
            "Scout online — HTTP polling mode. "
            "WebSocket logsSubscribe removed. "
            "Credit burn reduced from ~2.6M/day to ~576K/day for discovery.",
        )
    except Exception:
        pass

    # ── WebSocket fast-path (zero extra QuickNode calls) ─────────────────────
    # Converts existing HTTP RPC URL to WSS — no new quota consumed.
    # When WS fires, should_skip_next_poll() returns True for 4s,
    # skipping the next HTTP poll cycle entirely (saves credits).
    try:
        from services.ws_pump_monitor import start_ws_listener
        start_ws_listener()
        logging.info("SCOUT: WebSocket fast-path started (passive listener, no extra QuickNode)")
    except Exception as _wse:
        logging.info("SCOUT: WebSocket fast-path unavailable (%s) — HTTP polling only", _wse)

    await asyncio.to_thread(
        update_heartbeat_sync,
        "BOOTING",
        "Scout starting — HTTP polling mode"
    )

    try:
        await poll_loop()
    finally:
        await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logging.info("SCOUT: stopped by user")
    except Exception as exc:
        logging.exception(f"SCOUT fatal error: {exc}")
        raise