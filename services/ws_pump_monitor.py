"""
ws_pump_monitor.py — WebSocket listener for pump.fun program
Drop in trading-bot/services/ alongside pump_monitor.py

Runs as a background thread. When a new bonding curve account is detected
via WebSocket, it writes to raw_dna (same schema as HTTP polling) and sets
a flag so pump_monitor skips its next HTTP poll cycle.

Usage in pump_monitor.py run():
    from services.ws_pump_monitor import start_ws_listener, should_skip_next_poll
    start_ws_listener()
    # In main loop:
    if should_skip_next_poll():
        continue
"""

import asyncio
import json
import os
import time
import threading
import base64
import logging

log = logging.getLogger("ws_pump_monitor")

PROGRAM_ID = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
# pump.fun bonding curve discriminator (first 8 bytes of account data)
# Verified April 2026 — if pump.fun upgrades program, update this
BONDING_CURVE_DISCRIMINATOR = bytes([0x17, 0xb7, 0xf8, 0x37, 0x60, 0x06, 0x9c, 0x54])

_LAST_WS_EVENT = 0.0
_WS_RUNNING    = False


def _get_ws_url() -> str:
    """Convert HTTP RPC URL to WebSocket URL."""
    for key in ("QUICKNODE_RPC", "HELIUS_RPC"):
        url = os.getenv(key, "").strip()
        if url:
            return url.replace("https://", "wss://").replace("http://", "wss://").rstrip("/")
    return "wss://api.mainnet-beta.solana.com"


async def _listener_loop():
    global _LAST_WS_EVENT
    try:
        import websockets
    except ImportError:
        log.warning("websockets not installed — run: pip install websockets")
        return

    ws_url = _get_ws_url()
    log.info("WS pump monitor connecting to %s", ws_url[:50])

    backoff = 1.0
    while True:
        try:
            async with websockets.connect(
                ws_url,
                ping_interval=20,
                ping_timeout=10,
                max_size=2**20,
            ) as ws:
                sub = {
                    "jsonrpc": "2.0", "id": 1,
                    "method": "programSubscribe",
                    "params": [
                        PROGRAM_ID,
                        {"commitment": "confirmed", "encoding": "base64"},
                    ],
                }
                await ws.send(json.dumps(sub))
                confirm = await ws.recv()
                log.info("WS subscribed: %s", confirm[:80])
                backoff = 1.0

                while True:
                    try:
                        msg = await asyncio.wait_for(ws.recv(), timeout=30)
                    except asyncio.TimeoutError:
                        await ws.ping()
                        continue

                    try:
                        p = json.loads(msg)
                    except Exception:
                        continue

                    if p.get("method") != "accountNotification":
                        continue

                    try:
                        v    = p["params"]["result"]["value"]
                        data = v["account"]["data"]
                        raw  = base64.b64decode(data[0] if isinstance(data, list) else data)

                        # Filter: must be a bonding curve account
                        if len(raw) < 40:
                            continue
                        if raw[:8] != BONDING_CURVE_DISCRIMINATOR:
                            continue

                        # Extract mint address (bytes 8-40 = 32 bytes pubkey)
                        mint_bytes = raw[8:40]
                        try:
                            from solders.pubkey import Pubkey
                            mint = str(Pubkey.from_bytes(mint_bytes))
                        except Exception:
                            import base58
                            mint = base58.b58encode(mint_bytes).decode()

                        slot = p["params"]["result"]["context"]["slot"]
                        _write_to_raw_dna(mint, slot)
                        _LAST_WS_EVENT = time.time()
                        log.debug("WS new mint: %s slot=%d", mint[:16], slot)

                    except Exception as e:
                        log.debug("WS parse error: %s", e)
                        continue

        except Exception as e:
            log.warning("WS disconnected: %s — retrying in %.0fs", e, backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _write_to_raw_dna(mint: str, slot: int) -> None:
    """Write new mint to raw_dna — same schema as pump_monitor HTTP polling."""
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
                (f"ws_{mint}_{slot}", "[]", now, now),
            )
            conn.commit()
    except Exception as e:
        log.debug("raw_dna write failed: %s", e)


def _thread_runner():
    global _WS_RUNNING
    _WS_RUNNING = True
    try:
        asyncio.run(_listener_loop())
    except Exception as e:
        log.error("WS thread crashed: %s", e)
    finally:
        _WS_RUNNING = False


def start_ws_listener() -> threading.Thread:
    """Start WebSocket listener as background daemon thread."""
    t = threading.Thread(target=_thread_runner, daemon=True, name="ws_pump_monitor")
    t.start()
    log.info("WS pump monitor thread started")
    return t


def should_skip_next_poll() -> bool:
    """
    Returns True if a WS event arrived in the last 4 seconds.
    Call this at the top of pump_monitor's HTTP poll cycle.
    If True, skip the HTTP poll — WS already got this slot.
    """
    return (time.time() - _LAST_WS_EVENT) < 4.0


def is_running() -> bool:
    return _WS_RUNNING
