"""
services/telegram_scout.py

SENTINUITY SOVEREIGN HUB — Telegram Call Channel Alpha Scout
=============================================================

Monitors trusted Telegram call channels in real time.
Extracts token addresses and X multiplier claims from messages.
Logs everything to telegram_calls table for Polaris to learn from.

Over time Polaris builds a performance profile per channel:
  - hit rate (did the token actually move?)
  - average X achieved vs claimed
  - average entry metrics at time of call
  - channel trust score

This feeds into the supervisor confidence gate once enough data
accumulates — trusted channels get a signal boost, noise channels
get flagged.

Requirements:
    pip install telethon python-dotenv

.env additions:
    TELEGRAM_API_ID=your_api_id          (from my.telegram.org)
    TELEGRAM_API_HASH=your_api_hash      (from my.telegram.org)
    TELEGRAM_PHONE=+your_phone_number
    TELEGRAM_CALL_CHANNELS=channel1,channel2,-1001234567890

First run requires interactive SMS verification:
    python services/telegram_scout.py
It will prompt for the code Telegram sends to your phone.
After that it saves a session file and runs silently.

Session file location: trading-bot/sentinuity_scout.session
Keep this file secure — it is a login token.
Add to .gitignore: *.session
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env", override=True)

if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [TG_SCOUT] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)
log = logging.getLogger("tg_scout")

SERVICE_NAME = "telegram_scout"

# ── ENV ───────────────────────────────────────────────────────────────────────
API_ID       = int(os.getenv("TELEGRAM_API_ID", "0"))
API_HASH     = os.getenv("TELEGRAM_API_HASH", "").strip()
PHONE        = os.getenv("TELEGRAM_PHONE", "").strip()
RAW_CHANNELS = os.getenv("TELEGRAM_CALL_CHANNELS", "").strip()

# Parse channel list — supports usernames and numeric IDs
def _parse_channels(raw: str) -> list:
    channels = []
    for c in raw.split(","):
        c = c.strip()
        if not c:
            continue
        # Numeric ID (private groups are negative integers)
        try:
            channels.append(int(c))
        except ValueError:
            # Username — strip @ if present
            channels.append(c.lstrip("@"))
    return channels

WATCH_CHANNELS = _parse_channels(RAW_CHANNELS)

# ── EXTRACTION PATTERNS ───────────────────────────────────────────────────────
# Solana mint address: base58, 32-44 chars
SOLANA_RE = re.compile(r"\b([1-9A-HJ-NP-Za-km-z]{32,44})\b")

# X multiplier claims: "5x", "10X", "50x achieved", " 100x"
X_MULT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*[xX]", re.IGNORECASE)

# Common non-token base58 strings to filter out
_KNOWN_REJECTS = {
    "So11111111111111111111111111111111111111112",  # Wrapped SOL
    "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v",  # USDC
    "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB",  # USDT
    "mSoLzYCxHdYgdzU16g5QSh3i5K3z3KZK7ytfqcJm7So",  # mSOL
}

# Words that indicate a message is NOT a token call
_NOISE_PHRASES = {
    "telegram", "discord", "twitter", "website", "join", "follow",
    "subscribe", "invite", "referral", "airdrop", "free", "giveaway",
}


def extract_token_address(text: str) -> Optional[str]:
    """
    Extract the most likely Solana token mint from a message.
    Prefers longer addresses and filters known non-token mints.
    """
    candidates = SOLANA_RE.findall(text)
    # Filter known rejects
    candidates = [c for c in candidates if c not in _KNOWN_REJECTS]
    if not candidates:
        return None
    # Prefer 44-char addresses (full pubkeys), then 43, etc.
    candidates.sort(key=len, reverse=True)
    return candidates[0]


def extract_x_multiplier(text: str) -> Optional[float]:
    """Extract the largest claimed X multiplier from a message."""
    matches = X_MULT_RE.findall(text)
    if not matches:
        return None
    try:
        return max(float(m) for m in matches)
    except Exception:
        return None


def is_likely_call(text: str) -> bool:
    """
    Quick heuristic — is this message likely a token call?
    Reduces noise from chat messages and admin posts.
    """
    text_lower = text.lower()
    # Must contain a Solana address
    if not SOLANA_RE.search(text):
        return False
    # Skip obvious noise
    noise_count = sum(1 for phrase in _NOISE_PHRASES if phrase in text_lower)
    if noise_count >= 3:
        return False
    return True


# ── SCHEMA ────────────────────────────────────────────────────────────────────
def ensure_tables() -> None:
    """Create telegram_calls table if it doesn't exist."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_calls (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel         TEXT NOT NULL,
                channel_id      TEXT,
                message_id      INTEGER,
                token_address   TEXT,
                raw_text        TEXT,
                x_multiplier    REAL,
                called_at       REAL NOT NULL,
                -- Enriched after the fact by Polaris
                snapshot_id     INTEGER,
                entry_price     REAL,
                entry_liq_usd   REAL,
                entry_mcap_usd  REAL,
                entry_age_sec   REAL,
                peak_price      REAL,
                peak_x          REAL,
                outcome         TEXT DEFAULT 'pending',
                -- Channel trust tracking
                created_at      REAL DEFAULT (unixepoch())
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tg_calls_addr
            ON telegram_calls(token_address)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tg_calls_channel
            ON telegram_calls(channel, called_at)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tg_calls_outcome
            ON telegram_calls(outcome)
        """)

        # Channel trust scores table — Polaris writes here
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_channel_trust (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                channel         TEXT UNIQUE NOT NULL,
                total_calls     INTEGER DEFAULT 0,
                hit_calls       INTEGER DEFAULT 0,
                rug_calls       INTEGER DEFAULT 0,
                avg_x_claimed   REAL DEFAULT 0.0,
                avg_x_actual    REAL DEFAULT 0.0,
                trust_score     REAL DEFAULT 0.5,
                last_updated    REAL DEFAULT (unixepoch()),
                notes           TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_tg_trust_channel
            ON telegram_channel_trust(channel)
        """)
        conn.commit()

    log.info("Telegram tables ready")


def log_call(
    channel: str,
    channel_id: str,
    message_id: int,
    token_address: Optional[str],
    raw_text: str,
    x_multiplier: Optional[float],
    called_at: float,
) -> int:
    """Write a call to telegram_calls. Returns the new row id."""
    try:
        with get_connection() as conn:
            cur = conn.execute("""
                INSERT INTO telegram_calls
                    (channel, channel_id, message_id, token_address,
                     raw_text, x_multiplier, called_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                channel, channel_id, message_id,
                token_address, raw_text[:2000],
                x_multiplier, called_at,
            ))
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        log.warning("log_call failed: %s", e)
        return 0


def enrich_call_with_snapshot(call_id: int, token_address: str, called_at: float) -> None:
    """
    Try to find a market_snapshots row for this token around the call time.
    Enriches the telegram_calls row with entry metrics so Polaris can
    learn what the on-chain state looked like when the call was made.
    """
    if not call_id or not token_address:
        return

    try:
        with get_connection() as conn:
            # Find snapshot closest to call time
            snap = conn.execute("""
                SELECT id, observed_price, token_liquidity_usd,
                       market_cap_usd, token_age_seconds
                FROM market_snapshots
                WHERE mint_address = ?
                  AND ABS(timestamp - ?) < 300
                ORDER BY ABS(timestamp - ?) ASC
                LIMIT 1
            """, (token_address, called_at, called_at)).fetchone()

            if snap:
                conn.execute("""
                    UPDATE telegram_calls
                    SET snapshot_id    = ?,
                        entry_price    = ?,
                        entry_liq_usd  = ?,
                        entry_mcap_usd = ?,
                        entry_age_sec  = ?
                    WHERE id = ?
                """, (
                    snap["id"],
                    snap["observed_price"],
                    snap["token_liquidity_usd"],
                    snap["market_cap_usd"],
                    snap["token_age_seconds"],
                    call_id,
                ))
                conn.commit()
                log.debug(
                    "Enriched call %d with snapshot %d price=%.8f",
                    call_id, snap["id"], snap["observed_price"] or 0,
                )
    except Exception as e:
        log.debug("enrich_call_with_snapshot failed: %s", e)


def get_channel_stats() -> dict:
    """Return per-channel performance stats for dashboard display."""
    stats = {}
    try:
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT channel,
                       COUNT(*) as total,
                       SUM(CASE WHEN outcome='hit'  THEN 1 ELSE 0 END) as hits,
                       SUM(CASE WHEN outcome='rug'  THEN 1 ELSE 0 END) as rugs,
                       AVG(CASE WHEN x_multiplier IS NOT NULL
                           THEN x_multiplier ELSE NULL END) as avg_claimed_x,
                       AVG(CASE WHEN peak_x IS NOT NULL
                           THEN peak_x ELSE NULL END) as avg_actual_x
                FROM telegram_calls
                GROUP BY channel
                ORDER BY total DESC
            """).fetchall()

            for row in rows:
                total    = row["total"] or 1
                hit_rate = (row["hits"] or 0) / total
                stats[row["channel"]] = {
                    "calls":         row["total"],
                    "hits":          row["hits"] or 0,
                    "rugs":          row["rugs"] or 0,
                    "hit_rate":      round(hit_rate, 3),
                    "avg_claimed_x": round(row["avg_claimed_x"] or 0, 2),
                    "avg_actual_x":  round(row["avg_actual_x"] or 0, 2),
                }
    except Exception as e:
        log.warning("get_channel_stats failed: %s", e)
    return stats


# ── PEAK PRICE TRACKER ────────────────────────────────────────────────────────
async def peak_price_tracker() -> None:
    """
    Background task — periodically checks if called tokens hit new peaks.
    Updates peak_price and peak_x in telegram_calls.
    Marks outcome as 'hit' if peak_x >= 2x, 'rug' if token has no price
    data 30 minutes after the call.
    """
    while True:
        try:
            with get_connection() as conn:
                # Get pending calls from last 2 hours
                rows = conn.execute("""
                    SELECT id, token_address, entry_price,
                           x_multiplier, called_at, peak_x
                    FROM telegram_calls
                    WHERE outcome = 'pending'
                      AND token_address IS NOT NULL
                      AND called_at > ?
                """, (time.time() - 7200,)).fetchall()

            for row in rows:
                token   = row["token_address"]
                entry_p = row["entry_price"] or 0
                call_id = row["id"]
                age_s   = time.time() - row["called_at"]

                try:
                    with get_connection() as conn:
                        # Get highest price seen since call
                        price_row = conn.execute("""
                            SELECT MAX(observed_price) as peak
                            FROM market_snapshots
                            WHERE mint_address = ?
                              AND timestamp >= ?
                              AND observed_price IS NOT NULL
                        """, (token, row["called_at"])).fetchone()

                    peak_price = float(price_row["peak"] or 0) if price_row else 0

                    if peak_price > 0 and entry_p > 0:
                        peak_x   = peak_price / entry_p
                        outcome  = "hit" if peak_x >= 2.0 else "pending"
                    elif age_s > 1800 and peak_price == 0:
                        # 30 min old, never saw a price — likely rug or never listed
                        peak_x  = 0.0
                        outcome = "rug"
                    else:
                        continue

                    with get_connection() as conn:
                        conn.execute("""
                            UPDATE telegram_calls
                            SET peak_price = ?,
                                peak_x     = ?,
                                outcome    = ?
                            WHERE id = ?
                        """, (peak_price, peak_x, outcome, call_id))
                        conn.commit()

                    if outcome in ("hit", "rug"):
                        log.info(
                            "CALL OUTCOME: id=%d token=%s outcome=%s peak_x=%.2f",
                            call_id, token[:12], outcome, peak_x or 0,
                        )

                except Exception as e:
                    log.debug("peak tracker row error id=%d: %s", call_id, e)

        except Exception as e:
            log.warning("peak_price_tracker error: %s", e)

        await asyncio.sleep(60)  # Check every minute


# ── MAIN SCOUT LOOP ───────────────────────────────────────────────────────────
async def run_scout() -> None:
    """Main async loop — connects to Telegram and monitors call channels."""
    if not API_ID or not API_HASH:
        log.error(
            "TELEGRAM_API_ID / TELEGRAM_API_HASH not set in .env\n"
            "Get them from https://my.telegram.org"
        )
        update_heartbeat(SERVICE_NAME, "ERROR", "TELEGRAM_API_ID/API_HASH not set in .env")
        return

    if not WATCH_CHANNELS:
        log.warning(
            "No TELEGRAM_CALL_CHANNELS configured in .env\n"
            "Example: TELEGRAM_CALL_CHANNELS=channel1,channel2,-1001234567890"
        )
        update_heartbeat(SERVICE_NAME, "DEGRADED", "TELEGRAM_CALL_CHANNELS not set - scout idle")
        return

    if not PHONE:
        log.error("TELEGRAM_PHONE not set in .env — needed for first login")
        update_heartbeat(SERVICE_NAME, "ERROR", "TELEGRAM_PHONE not set in .env")
        return

    # Import here so the file can be imported without telethon installed
    try:
        from telethon import TelegramClient, events
    except ImportError:
        log.error(
            "telethon not installed.\n"
            "Run: pip install telethon"
        )
        update_heartbeat(SERVICE_NAME, "ERROR", "telethon not installed - pip install telethon")
        return

    ensure_tables()

    # Session file saves next to the DB — survives restarts without re-auth
    session_path = str(BASE_DIR / "sentinuity_scout")

    client = TelegramClient(session_path, API_ID, API_HASH)

    @client.on(events.NewMessage(chats=WATCH_CHANNELS))
    async def handle_message(event):
        text = event.message.message or ""
        if not text.strip():
            return

        # Quick noise filter
        if not is_likely_call(text):
            return

        called_at  = time.time()
        chat       = event.chat
        channel    = getattr(chat, "username", None) or str(event.chat_id)
        channel_id = str(event.chat_id)
        message_id = event.message.id

        token_address = extract_token_address(text)
        x_multiplier  = extract_x_multiplier(text)

        # Log even if no token found but has X claim — still useful signal
        if token_address or x_multiplier:
            call_id = log_call(
                channel     = channel,
                channel_id  = channel_id,
                message_id  = message_id,
                token_address = token_address,
                raw_text    = text,
                x_multiplier = x_multiplier,
                called_at   = called_at,
            )

            log.info(
                "CALL: channel=%-20s token=%-14s x=%s",
                channel[:20],
                (token_address or "none")[:14],
                f"{x_multiplier:.0f}x" if x_multiplier else "none",
            )

            # Try to enrich with on-chain metrics immediately
            if token_address and call_id:
                await asyncio.to_thread(
                    enrich_call_with_snapshot,
                    call_id, token_address, called_at,
                )

            update_heartbeat(
                SERVICE_NAME, "ALIVE",
                f"call logged channel={channel} token={token_address}",
                work_processed=1,
                last_success_at=called_at,
            )

    # Also catch edited messages — call channels sometimes edit to add X results
    @client.on(events.MessageEdited(chats=WATCH_CHANNELS))
    async def handle_edit(event):
        text = event.message.message or ""
        if not text.strip():
            return

        x_multiplier = extract_x_multiplier(text)
        if not x_multiplier:
            return

        message_id = event.message.id
        channel_id = str(event.chat_id)

        # Update the X multiplier on the original call if it exists
        try:
            with get_connection() as conn:
                conn.execute("""
                    UPDATE telegram_calls
                    SET x_multiplier = MAX(COALESCE(x_multiplier, 0), ?)
                    WHERE message_id = ? AND channel_id = ?
                """, (x_multiplier, message_id, channel_id))
                conn.commit()
            log.info(
                "EDIT UPDATE: channel_id=%s msg=%d x=%.0fx",
                channel_id[:12], message_id, x_multiplier,
            )
        except Exception as e:
            log.debug("handle_edit update failed: %s", e)

    await client.start(phone=PHONE)

    log.info(
        "TG_SCOUT ONLINE — watching %d channel(s): %s",
        len(WATCH_CHANNELS),
        ", ".join(str(c) for c in WATCH_CHANNELS),
    )

    # Print channel stats on startup
    stats = get_channel_stats()
    if stats:
        log.info("Channel performance history:")
        for ch, s in stats.items():
            log.info(
                "  %-30s calls=%d hit_rate=%.1f%% avg_x=%.1f",
                ch, s["calls"], s["hit_rate"] * 100, s["avg_actual_x"],
            )

    update_heartbeat(
        SERVICE_NAME, "ALIVE",
        f"watching {len(WATCH_CHANNELS)} channels",
    )

    # Start peak price tracker as background task
    asyncio.create_task(peak_price_tracker())

    # Run until disconnected — try/finally ensures session file
    # is always released cleanly on crash or restart.
    # Without this, a crash leaves .session locked and scout
    # won't reconnect without manual deletion of the session file.
    try:
        await client.run_until_disconnected()
    finally:
        try:
            await client.disconnect()
            log.info("Telegram client disconnected cleanly")
        except Exception:
            pass


if __name__ == "__main__":
    log.info("SENTINUITY TELEGRAM SCOUT STARTING")
    log.info("Session will be saved to: %s.session", BASE_DIR / "sentinuity_scout")
    log.info("Watching channels: %s", WATCH_CHANNELS or "NONE — check .env")

    try:
        asyncio.run(run_scout())
    except KeyboardInterrupt:
        log.info("Scout stopped by user")
    except Exception as e:
        log.exception("Fatal scout error: %s", e)
