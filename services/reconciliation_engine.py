"""
SENTINUITY — RECONCILIATION ENGINE
====================================
Continuous on-chain wallet vs DB sync. Runs as background service.

Built from:
  - Mixtral 8x22b output (Track 2, NIM audit v3.0)
  - Real paper_positions schema (mint_address, quantity, position_size_usd, status)
  - Existing RPC pattern from live_trading.py (QUICKNODE_RPC env var)

WHAT IT DOES (every 30s):
  1. Fetches all token balances from Solana via getTokenAccountsByOwner
  2. Compares to every OPEN position in DB
  3. OPEN but zero on-chain balance  → marks DESYNC_CLOSED + logs CRITICAL
  4. OPEN but >20% quantity mismatch → logs WARNING
  5. CLOSED in last 60s but tokens still on-chain → logs CRITICAL (stranded tokens)

DEPLOY:
  Drop into services/reconciliation_engine.py
  Add to Launch_LiveMoney.bat:
    start "" "Reconciler" python services\reconciliation_engine.py
    timeout /t 2

NO CHANGES needed to execution_engine.py or live_trading.py.
"""

import os
import time
import base64
import struct
import logging
import requests
import sqlite3
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────────
SERVICE_NAME    = "reconciler"
RECON_INTERVAL  = 30           # seconds between reconciliation passes
STALE_CLOSE_SEC = 60           # look back window for recently closed positions

_RPC_URL        = os.getenv("QUICKNODE_RPC") or os.getenv("SOLANA_RPC_URL",
                  "https://api.mainnet-beta.solana.com")
_WALLET_ADDRESS = os.getenv("SOLANA_WALLET_ADDRESS", "")

TOKEN_PROGRAM_ID = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [RECONCILER] %(levelname)-8s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("reconciler")

# ── DB connection ─────────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = BASE_DIR / "sentinuity_matrix.db"

def get_conn():
    conn = sqlite3.connect(str(DB_PATH), timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn

# ── Heartbeat ─────────────────────────────────────────────────────────────────
def _heartbeat(status: str, note: str):
    try:
        conn = get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO system_heartbeat
            (service_name, status, note, last_seen_at)
            VALUES (?, ?, ?, ?)
        """, (SERVICE_NAME, status, note[:200], time.time()))
        conn.commit()
        conn.close()
    except Exception:
        pass

# ── Core RPC functions ────────────────────────────────────────────────────────

def get_all_token_balances(wallet_pubkey: str) -> dict:
    """
    Fetch all SPL token balances for wallet via getTokenAccountsByOwner.
    Returns {mint_address: balance_as_float} using uiAmount (decimal-adjusted).

    Uses encoding=jsonParsed so we get human-readable uiAmount directly.
    """
    if not wallet_pubkey:
        log.warning("[RECON] No wallet address configured — skipping on-chain check")
        return {}

    try:
        r = requests.post(_RPC_URL, json={
            "jsonrpc": "2.0",
            "id":      1,
            "method":  "getTokenAccountsByOwner",
            "params":  [
                wallet_pubkey,
                {"programId": TOKEN_PROGRAM_ID},
                {"encoding": "jsonParsed"},
            ],
        }, timeout=10)

        result = r.json().get("result", {})
        accounts = result.get("value", [])

        balances = {}
        for account in accounts:
            try:
                info    = account["account"]["data"]["parsed"]["info"]
                mint    = info["mint"]
                ui_amt  = info["tokenAmount"].get("uiAmount") or 0.0
                balances[mint] = balances.get(mint, 0.0) + float(ui_amt)
            except (KeyError, TypeError):
                continue

        log.info("[RECON] On-chain balances fetched: %d token accounts", len(balances))
        return balances

    except requests.exceptions.Timeout:
        log.error("[RECON] getTokenAccountsByOwner timeout")
        return {}
    except Exception as e:
        log.error("[RECON] get_all_token_balances error: %s", e)
        return {}


def get_token_decimals(mint: str) -> int:
    """
    Fetch token decimals from on-chain mint account data.
    Decimals are stored at byte offset 44 in the MintLayout (1 byte, uint8).

    Falls back to 6 (pump.fun default) on any error.
    """
    try:
        r = requests.post(_RPC_URL, json={
            "jsonrpc": "2.0",
            "id":      1,
            "method":  "getAccountInfo",
            "params":  [mint, {"encoding": "base64"}],
        }, timeout=5)

        data_b64 = r.json()["result"]["value"]["data"][0]
        data     = base64.b64decode(data_b64)

        # MintLayout: decimals is a single uint8 at offset 44
        if len(data) >= 45:
            decimals = struct.unpack("<B", data[44:45])[0]
            return int(decimals)

    except Exception as e:
        log.debug("[RECON] get_token_decimals(%s) error: %s — defaulting to 6", mint[:12], e)

    return 6  # pump.fun default


# ── Reconciliation pass ───────────────────────────────────────────────────────

def reconcile_once() -> dict:
    """
    Single reconciliation pass. Compare on-chain balances to DB state.
    Returns summary dict with counts for heartbeat logging.
    """
    summary = {
        "ghost_closed":    0,
        "qty_mismatch":    0,
        "stranded_tokens": 0,
        "open_checked":    0,
        "closed_checked":  0,
        "errors":          0,
    }

    wallet = _WALLET_ADDRESS
    if not wallet:
        # Try reading from DB config
        try:
            conn = get_conn()
            row  = conn.execute(
                "SELECT value FROM system_config WHERE key='PHANTOM_WALLET_ADDRESS'"
            ).fetchone()
            conn.close()
            if row and row["value"]:
                wallet = str(row["value"]).strip()
        except Exception:
            pass

    if not wallet:
        log.warning("[RECON] No wallet address — cannot reconcile. "
                    "Set SOLANA_WALLET_ADDRESS in .env or connect wallet in hub.")
        _heartbeat("DEGRADED", "No wallet address configured")
        return summary

    # ── 1. Fetch on-chain balances ────────────────────────────────────────────
    onchain = get_all_token_balances(wallet)
    if not onchain and onchain is not None:
        # Empty dict = wallet has no token accounts (possible after all sells)
        log.info("[RECON] Wallet has no SPL token accounts")
    elif onchain is None:
        summary["errors"] += 1
        _heartbeat("ERROR", "RPC call failed")
        return summary

    # ── 2. Check OPEN positions ───────────────────────────────────────────────
    try:
        conn = get_conn()
        # TRUE_DUAL_RECON_GUARD_20260713 (Claude audit):
        # On-chain reconciliation is only meaningful for REAL-funded rows.
        # SIM paper positions were never bought on-chain, so comparing them to
        # wallet balances would flag EVERY paper position as a "ghost" and
        # DESYNC_CLOSE the entire paper lane within one 30s pass in dual mode.
        open_rows = conn.execute(
            "SELECT id, mint_address, token_name, quantity, position_size_usd, "
            "COALESCE(opened_at,0) AS opened_at "
            "FROM paper_positions WHERE status='OPEN' "
            "AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'"
        ).fetchall()
        conn.close()
    except Exception as e:
        log.error("[RECON] DB read error: %s", e)
        summary["errors"] += 1
        return summary

    summary["open_checked"] = len(open_rows)

    for pos in open_rows:
        pos_id    = pos["id"]
        mint      = pos["mint_address"]
        token     = pos["token_name"] or mint[:12]
        db_qty    = float(pos["quantity"] or 0)
        onchain_bal = onchain.get(mint, 0.0)

        # CASE 1: DB says OPEN but no tokens on-chain → ghost position
        if onchain_bal == 0.0:
            # TRUE_DUAL_RECON_GUARD_20260713: a REAL buy confirmed seconds ago may
            # not be visible at this RPC's commitment level yet. Never ghost-close
            # a REAL row younger than 120s.
            if time.time() - float(pos["opened_at"] or 0) < 120:
                log.warning(
                    "[RECON] pos=%d %s zero on-chain balance but opened <120s ago — "
                    "deferring (RPC propagation window)", pos_id, token
                )
                continue
            log.critical(
                "[RECON] GHOST POSITION — pos=%d %s OPEN in DB but ZERO on-chain balance. "
                "Marking DESYNC_CLOSED.", pos_id, token
            )
            try:
                conn = get_conn()
                conn.execute("""
                    UPDATE paper_positions
                    SET status='DESYNC_CLOSED',
                        exit_reason='RECON_ZERO_BALANCE',
                        closed_at=?
                    WHERE id=? AND status='OPEN'
                      AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'
                """, (time.time(), pos_id))
                conn.commit()
                conn.close()
                summary["ghost_closed"] += 1
            except Exception as e:
                log.error("[RECON] Failed to mark DESYNC_CLOSED pos=%d: %s", pos_id, e)
                summary["errors"] += 1

        # CASE 2: Balance exists but differs >20% from DB quantity → partial fill or desync
        elif db_qty > 0 and abs(onchain_bal - db_qty) / db_qty > 0.20:
            log.warning(
                "[RECON] QUANTITY MISMATCH — pos=%d %s | DB qty=%.4f | on-chain=%.4f | "
                "diff=%.1f%%",
                pos_id, token, db_qty, onchain_bal,
                abs(onchain_bal - db_qty) / db_qty * 100
            )
            summary["qty_mismatch"] += 1

    # ── 3. Check recently CLOSED positions for stranded tokens ────────────────
    try:
        conn = get_conn()
        closed_rows = conn.execute(
            "SELECT id, mint_address, token_name FROM paper_positions "
            "WHERE status='CLOSED' AND closed_at > ?",
            (time.time() - STALE_CLOSE_SEC,)
        ).fetchall()
        conn.close()
    except Exception as e:
        log.error("[RECON] DB closed positions read error: %s", e)
        closed_rows = []

    summary["closed_checked"] = len(closed_rows)

    for pos in closed_rows:
        mint  = pos["mint_address"]
        token = pos["token_name"] or mint[:12]
        bal   = onchain.get(mint, 0.0)
        if bal > 0.0:
            log.critical(
                "[RECON] STRANDED TOKENS — pos=%d %s is CLOSED in DB but "
                "%.6f tokens remain on-chain. Live sell may have failed silently.",
                pos["id"], token, bal
            )
            summary["stranded_tokens"] += 1

    # ── 4. Heartbeat ──────────────────────────────────────────────────────────
    note = (
        f"open={summary['open_checked']} "
        f"ghost_closed={summary['ghost_closed']} "
        f"stranded={summary['stranded_tokens']} "
        f"mismatch={summary['qty_mismatch']}"
    )
    status = "ALIVE" if summary["errors"] == 0 else "DEGRADED"
    _heartbeat(status, note)
    log.info("[RECON] Pass complete — %s", note)

    return summary


# ── Background loop ───────────────────────────────────────────────────────────

def run():
    log.info("[RECON] Reconciliation engine starting — interval=%ds wallet=%s",
             RECON_INTERVAL, _WALLET_ADDRESS[:8] + "..." if _WALLET_ADDRESS else "NOT_SET")
    _heartbeat("ALIVE", "Starting up")

    while True:
        try:
            reconcile_once()
        except Exception as e:
            log.exception("[RECON] Unhandled error in reconcile_once: %s", e)
            _heartbeat("ERROR", str(e)[:150])

        time.sleep(RECON_INTERVAL)


if __name__ == "__main__":
    run()
