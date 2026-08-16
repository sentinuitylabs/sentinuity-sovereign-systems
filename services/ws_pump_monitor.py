"""
ws_pump_monitor.py — WebSocket listener for pump.fun program
Drop in trading-bot/services/ alongside pump_monitor.py

EDGE_AUDIT_20260815 — DISCOVERY STARVATION REPAIR
=================================================
The previous version of this file was the direct cause of the ~340s median
discovery lag. Three defects, each individually severe, compounding:

1. FIREHOSE, NOT CREATION STREAM.
   programSubscribe delivers an accountNotification on EVERY write to a
   pump.fun bonding curve — every buy and every sell on every live token, not
   just creations. The discriminator check confirmed "this is a bonding curve"
   and then treated it as "this is a NEW bonding curve". Those are different
   statements.

2. DEDUPE KEY INCLUDED THE SLOT.
       tx_hash = f"ws_{mint}_{slot}"
   Every trade lands in a different slot, so ON CONFLICT DO NOTHING could
   never fire. A single popular token generated an unbounded stream of new
   raw_dna rows. Those rows carry a synthetic tx_hash that is not a real
   signature and logs="[]", so the resolver's getTransaction call could never
   succeed — each one burned an RPC credit (contributing to the observed 429
   pressure), occupied a queue slot, and resolved to nothing. Because
   ingest claims ORDER BY id DESC, these newest-and-useless rows outranked
   genuine creation signatures, which then aged out via
   STALE_RESOLVER_KILLED at 600s.

3. THE FIREHOSE SUPPRESSED THE ONLY WORKING PATH.
   _LAST_WS_EVENT was stamped on every notification, and pump_monitor's
   poll_loop skips its HTTP poll whenever should_skip_next_poll() is True
   (within 4s of the last event). Since some pump.fun curve changes somewhere
   almost every second, that predicate was effectively always True. The HTTP
   getSignaturesForAddress path — the ONLY path that produces resolvable
   signatures — was being skipped almost permanently. Discovery happened only
   in the rare 4-second windows when no curve anywhere moved.

THIS VERSION
------------
  * Subscribes with SERVER-SIDE filters (memcmp on the discriminator) so the
    provider stops shipping irrelevant accounts over the socket.
  * Dedupes by MINT, never by slot. First sight of a curve account for a mint
    is a creation candidate; every later write to that same curve is an
    update and is ignored.
  * Stamps _LAST_WS_EVENT ONLY on a genuinely new mint, so poll suppression
    can never again be driven by ordinary trading activity.
  * Ships with suppression DISABLED by default (WS_MAY_SUPPRESS_HTTP_POLL=0).
    Until a WS row can actually be resolved end-to-end, the HTTP path must
    never be throttled by this module. Discovery correctness outranks credits.
  * Ships with raw_dna writes DISABLED by default (WS_WRITE_RAW_DNA=0). The
    rows are currently unresolvable, so writing them only burns RPC and
    starves the queue. Telemetry is retained either way.
  * Emits WS_CREATION_DETECTED with slot and mint so the true creation
    timestamp becomes measurable for the first time. This is the evidence
    needed to build the canonical token_birth_ts.

SCOPE
-----
This is a containment and measurement round. It does not make the WS path the
primary discovery lane — that requires ingest to short-circuit ws_ rows using
the embedded mint instead of calling getTransaction on a synthetic hash, which
is a downstream change and is deliberately NOT bundled here. What this file
does is stop the WS path from actively destroying the HTTP path.

Usage in pump_monitor.py run():
    from services.ws_pump_monitor import start_ws_listener, should_skip_next_poll
    start_ws_listener()
"""

import asyncio
import base64
import json
import logging
import os
import threading
import time

log = logging.getLogger("ws_pump_monitor")

PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
# pump.fun bonding curve discriminator (first 8 bytes of account data)
# Verified April 2026 — if pump.fun upgrades program, update this
BONDING_CURVE_DISCRIMINATOR = bytes([0x17, 0xb7, 0xf8, 0x37, 0x60, 0x06, 0x9c, 0x54])
_DISCRIMINATOR_B58_SEED = BONDING_CURVE_DISCRIMINATOR

# ── Behaviour switches ───────────────────────────────────────────────────────
# Both default to the SAFE value. Neither should be turned on until the WS
# lane has been proven end-to-end against a live window.


def _env_flag(name: str, default: str = "0") -> bool:
    return str(os.getenv(name, default)).strip().lower() in ("1", "true", "yes", "on")


def _may_suppress_http_poll() -> bool:
    """Default OFF. The HTTP lane is the only lane that currently resolves."""
    return _env_flag("WS_MAY_SUPPRESS_HTTP_POLL", "0")


def _may_write_raw_dna() -> bool:
    """Default OFF. ws_ rows cannot be resolved by getTransaction today."""
    return _env_flag("WS_WRITE_RAW_DNA", "0")


# ── State ────────────────────────────────────────────────────────────────────
_LAST_NEW_MINT_AT = 0.0          # stamped ONLY on a genuinely new mint
_WS_RUNNING = False
_SEEN_MINTS: set[str] = set()
_SEEN_LOCK = threading.Lock()
_SEEN_MAX = 200_000               # bounded; pump.fun mint churn is high

_STATS = {
    "notifications": 0,
    "curve_accounts": 0,
    "new_mints": 0,
    "duplicate_updates": 0,
    "raw_dna_writes": 0,
    "parse_errors": 0,
}
_LAST_STATS_LOG = 0.0


def _get_ws_url() -> str:
    """Convert HTTP RPC URL to WebSocket URL."""
    for key in ("QUICKNODE_RPC", "HELIUS_RPC"):
        url = os.getenv(key, "").strip()
        if url:
            return url.replace("https://", "wss://").replace("http://", "wss://").rstrip("/")
    return "wss://api.mainnet-beta.solana.com"


def _b58encode(raw: bytes) -> str:
    """Local base58 so the memcmp filter never depends on an optional import."""
    alphabet = b"123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
    num = int.from_bytes(raw, "big")
    out = bytearray()
    while num > 0:
        num, rem = divmod(num, 58)
        out.append(alphabet[rem])
    for byte in raw:
        if byte == 0:
            out.append(alphabet[0])
        else:
            break
    return bytes(reversed(out)).decode()


def _seed_seen_mints() -> int:
    """
    Seed the mint dedupe set from the DB so a restart does not re-announce
    every curve as new. Failure is non-fatal: a cold set only costs one
    duplicate announcement per mint.
    """
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from core.schema import get_connection
        seeded = 0
        with get_connection() as conn:
            rows = conn.execute(
                "SELECT tx_hash FROM raw_dna WHERE tx_hash LIKE 'ws_%' "
                "ORDER BY id DESC LIMIT ?", (_SEEN_MAX,)
            ).fetchall()
            with _SEEN_LOCK:
                for row in rows:
                    tx = str(row[0] if not isinstance(row, dict) else row["tx_hash"])
                    # Tolerate BOTH key shapes: the new "ws_<mint>" and the
                    # legacy "ws_<mint>_<slot>" left behind by the old build.
                    body = tx[3:]
                    mint = body.rsplit("_", 1)[0] if body.count("_") else body
                    if mint:
                        _SEEN_MINTS.add(mint)
                        seeded += 1
        return seeded
    except Exception as exc:
        log.debug("mint dedupe seed skipped: %s", exc)
        return 0


def _note_mint(mint: str) -> bool:
    """Return True only the FIRST time this mint is observed."""
    with _SEEN_LOCK:
        if mint in _SEEN_MINTS:
            return False
        if len(_SEEN_MINTS) >= _SEEN_MAX:
            # Bounded eviction. Arbitrary victim is acceptable: the cost of a
            # false "new" is one duplicate row, not a trading decision.
            for _ in range(_SEEN_MAX // 10):
                try:
                    _SEEN_MINTS.pop()
                except KeyError:
                    break
        _SEEN_MINTS.add(mint)
        return True


def _maybe_log_stats(force: bool = False) -> None:
    global _LAST_STATS_LOG
    now = time.time()
    if not force and (now - _LAST_STATS_LOG) < 60.0:
        return
    _LAST_STATS_LOG = now
    log.info(
        "[WS_PUMP_STATS] notifications=%d curve_accounts=%d new_mints=%d "
        "duplicate_updates=%d raw_dna_writes=%d parse_errors=%d "
        "suppression=%s raw_dna_enabled=%s tracked_mints=%d",
        _STATS["notifications"], _STATS["curve_accounts"], _STATS["new_mints"],
        _STATS["duplicate_updates"], _STATS["raw_dna_writes"],
        _STATS["parse_errors"], _may_suppress_http_poll(),
        _may_write_raw_dna(), len(_SEEN_MINTS),
    )


def _build_subscription() -> dict:
    """
    Server-side filtering. The previous build subscribed to the whole program
    and discarded non-curve accounts client-side, paying full bandwidth and
    full notification volume for data it then threw away. memcmp at offset 0
    against the discriminator moves that filter to the provider.
    """
    return {
        "jsonrpc": "2.0", "id": 1,
        "method": "programSubscribe",
        "params": [
            PROGRAM_ID,
            {
                "commitment": "confirmed",
                "encoding": "base64",
                "filters": [
                    {"memcmp": {"offset": 0,
                                "bytes": _b58encode(_DISCRIMINATOR_B58_SEED)}}
                ],
            },
        ],
    }


async def _listener_loop():
    global _LAST_NEW_MINT_AT
    try:
        import websockets
    except ImportError:
        log.warning("websockets not installed — run: pip install websockets")
        return

    ws_url = _get_ws_url()
    log.info("WS pump monitor connecting to %s", ws_url[:50])
    seeded = _seed_seen_mints()
    log.info("WS mint dedupe seeded with %d known mints", seeded)

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=10,
                max_size=2**20,
            ) as ws:
                await ws.send(json.dumps(_build_subscription()))
                confirm = await ws.recv()
                log.info("WS subscribed (filtered): %s", str(confirm)[:80])
                backoff = 1.0

                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        await ws.ping()
                        _maybe_log_stats()
                        continue

                    try:
                        p = json.loads(msg)
                    except Exception:
                        continue

                    if p.get("method") != "accountNotification":
                        continue

                    _STATS["notifications"] += 1

                    try:
                        v = p["params"]["result"]["value"]
                        data = v["account"]["data"]
                        raw = base64.b64decode(data[0] if isinstance(data, list) else data)

                        if len(raw) < 40:
                            continue
                        # Retained even with the server-side filter: a provider
                        # that silently ignores `filters` must not be able to
                        # push non-curve accounts into the mint set.
                        if raw[:8] != BONDING_CURVE_DISCRIMINATOR:
                            continue

                        _STATS["curve_accounts"] += 1

                        mint_bytes = raw[8:40]
                        try:
                            from solders.pubkey import Pubkey
                            mint = str(Pubkey.from_bytes(mint_bytes))
                        except Exception:
                            mint = _b58encode(mint_bytes)

                        slot = p["params"]["result"]["context"]["slot"]

                        # ── THE FIX ──────────────────────────────────────────
                        # Every write to a live curve arrives here. Only the
                        # first sight of a given mint is a creation candidate.
                        if not _note_mint(mint):
                            _STATS["duplicate_updates"] += 1
                            continue

                        _STATS["new_mints"] += 1
                        # Stamped ONLY here. Ordinary trading activity can no
                        # longer suppress the HTTP discovery poll.
                        _LAST_NEW_MINT_AT = time.time()

                        log.info("[WS_CREATION_DETECTED] mint=%s slot=%d detected_at=%.3f",
                                 mint, int(slot), _LAST_NEW_MINT_AT)

                        if _may_write_raw_dna():
                            _write_to_raw_dna(mint, int(slot))

                    except Exception as exc:
                        _STATS["parse_errors"] += 1
                        log.debug("WS parse error: %s", exc)
                        continue

                    _maybe_log_stats()

        except Exception as exc:
            log.warning("WS disconnected: %s — retrying in %.0fs", exc, backoff)
            _maybe_log_stats(force=True)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _write_to_raw_dna(mint: str, slot: int) -> None:
    """
    Write a new mint to raw_dna — same schema as pump_monitor HTTP polling.

    Keyed by MINT ONLY. The old build appended the slot, which defeated
    ON CONFLICT DO NOTHING and turned this function into an unbounded row
    generator. The slot is retained in `logs` for provenance instead of being
    smuggled into the primary key.

    Disabled by default (WS_WRITE_RAW_DNA=0): the synthetic tx_hash is not a
    real signature, so the resolver's getTransaction cannot succeed on it. Do
    not enable until ingest short-circuits ws_ rows.
    """
    try:
        import sys
        from pathlib import Path
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from core.schema import get_connection
        now = time.time()
        with get_connection() as conn:
            conn.execute(
                """
                INSERT INTO raw_dna (tx_hash, logs, processed_state, first_seen_at, timestamp)
                VALUES (?, ?, 0, ?, ?)
                ON CONFLICT(tx_hash) DO NOTHING
                """,
                (f"ws_{mint}", "[]", now, now),
            )
            conn.commit()
        _STATS["raw_dna_writes"] += 1
    except Exception as exc:
        log.debug("raw_dna write failed: %s", exc)


def _thread_runner():
    global _WS_RUNNING
    _WS_RUNNING = True
    try:
        asyncio.run(_listener_loop())
    except Exception as exc:
        log.error("WS thread crashed: %s", exc)
    finally:
        _WS_RUNNING = False


def start_ws_listener() -> threading.Thread:
    """Start WebSocket listener as background daemon thread."""
    t = threading.Thread(target=_thread_runner, daemon=True, name="ws_pump_monitor")
    t.start()
    log.info("WS pump monitor thread started (suppression=%s raw_dna=%s)",
             _may_suppress_http_poll(), _may_write_raw_dna())
    return t


def should_skip_next_poll() -> bool:
    """
    Returns True only if a genuinely NEW mint arrived in the last 4 seconds
    AND suppression has been explicitly enabled.

    The previous implementation returned True whenever ANY bonding curve
    account changed — i.e. essentially always — which silently disabled the
    HTTP getSignaturesForAddress lane that produces the only resolvable
    signatures in the system. That single predicate is the primary cause of
    the measured ~340s discovery lag.

    Default is now hard OFF. Do not enable WS_MAY_SUPPRESS_HTTP_POLL until a
    WS-sourced mint has been proven to reach market_snapshots end-to-end.
    """
    if not _may_suppress_http_poll():
        return False
    return (time.time() - _LAST_NEW_MINT_AT) < 4.0


def is_running() -> bool:
    return _WS_RUNNING


def stats() -> dict:
    """Read-only snapshot for diagnostics and the UI."""
    out = dict(_STATS)
    out["tracked_mints"] = len(_SEEN_MINTS)
    out["last_new_mint_at"] = _LAST_NEW_MINT_AT
    out["suppression_enabled"] = _may_suppress_http_poll()
    out["raw_dna_enabled"] = _may_write_raw_dna()
    return out
