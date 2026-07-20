import os
import sys
from pathlib import Path

# Fix path FIRST before any local imports
BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# Fix Windows cp1252 encoding for emoji in logs
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

import time
import sqlite3
import logging
from services.cognition_logger import log_cognition
import requests
from typing import Dict, List, Tuple
from dotenv import load_dotenv

from core.schema import get_connection, update_heartbeat, get_config_value

# This forces the script to look for the hidden .env file in your root folder
load_dotenv(BASE_DIR / ".env", override=True)

SERVICE_NAME = "price_enricher"
JUPITER_URL = "https://api.jup.ag/price/v3"

# This line pulls your secure key from the .env file!
API_KEY = os.getenv("JUPITER_PRICE_API_KEY", "").strip()

HTTP_TIMEOUT = 8
BATCH_LIMIT = 50

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [ORACLE] %(levelname)-7s %(message)s",
    handlers=[logging.StreamHandler()],
)


def chunked(items: List[str], size: int) -> List[List[str]]:
    return [items[i:i + size] for i in range(0, len(items), size)]


def ensure_oracle_schema() -> None:
    try:
        with get_connection() as conn:
            cols = {row["name"] for row in conn.execute("PRAGMA table_info(paper_positions)").fetchall()}
            if "last_price" not in cols:
                conn.execute("ALTER TABLE paper_positions ADD COLUMN last_price REAL")
            if "last_marked_at" not in cols:
                conn.execute("ALTER TABLE paper_positions ADD COLUMN last_marked_at REAL")
            if "highest_price_seen" not in cols:
                conn.execute("ALTER TABLE paper_positions ADD COLUMN highest_price_seen REAL")
            conn.commit()
    except Exception as e:
        logging.debug("ensure_oracle_schema skipped: %s", e)



def get_pending_rows(limit: int) -> List[sqlite3.Row]:
    with get_connection() as conn:
        return conn.execute("""
            SELECT id, mint_address, COALESCE(price_attempts, 0) AS price_attempts
            FROM market_snapshots
            WHERE COALESCE(observed_price, 0) <= 0
              AND COALESCE(mint_address, '') != ''
              AND COALESCE(tx_hash, '') NOT LIKE 'mtm:%'
              AND COALESCE(candidate_state, 'pending') IN ('pending','qualified','latched')
              AND COALESCE(price_status, 'pending') IN ('pending', 'retry', '')
            ORDER BY COALESCE(created_at, timestamp, 0) DESC, id DESC LIMIT ?
        """, (limit,)).fetchall()


def get_open_position_mints() -> list[str]:
    with get_connection() as conn:
        rows = conn.execute("""
            SELECT DISTINCT mint_address
            FROM paper_positions
            WHERE status = 'OPEN'
              AND mint_address IS NOT NULL
              AND TRIM(mint_address) != ''
        """).fetchall()
        return [row["mint_address"] for row in rows]


def build_mtm_tx_hash(mint: str, now: float, seq: int = 0) -> str:
    return f"mtm:{mint}:{int(now * 1000)}:{seq}"


# ── PATCH (2026-06-26): mark-source outlier rejection ──────────────────────────
# Poisoned marks (a single Jupiter/DexScreener tick that disagrees wildly with the
# position's existing healthy mark) were writing -90%/-96% prices into last_price /
# highest_price_seen and mtm_ticks, triggering fake HARD_STOP closes and polluting
# peak stats. This guard rejects a new price for an OPEN position when it disagrees
# with the position's most recent valid mark by more than MARK_OUTLIER_REJECT_PCT.
# It does NOT block first marks (no prior reference) and does NOT touch the trusted
# upward path beyond a sanity cap, so real runners (e.g. confirmed +958%) pass.
try:
    _MARK_OUTLIER_REJECT_PCT = float(get_config_value("MARK_OUTLIER_REJECT_PCT", 50.0) or 50.0)
except Exception:
    _MARK_OUTLIER_REJECT_PCT = 50.0
try:
    _MARK_OUTLIER_GUARD_ENABLED = str(get_config_value("MARK_OUTLIER_GUARD_ENABLED", "1")).strip() not in ("0", "false", "False", "")
except Exception:
    _MARK_OUTLIER_GUARD_ENABLED = True


def _reference_marks(conn, mints: List[str]) -> Dict[str, float]:
    """Latest known-good mark per OPEN position (last_price, else entry_price)."""
    refs: Dict[str, float] = {}
    if not mints:
        return refs
    try:
        qs = ",".join("?" for _ in mints)
        for r in conn.execute(
            f"SELECT mint_address, COALESCE(last_price, entry_price) ref "
            f"FROM paper_positions WHERE status='OPEN' AND mint_address IN ({qs})",
            list(mints),
        ).fetchall():
            v = r["ref"]
            if v and float(v) > 0:
                refs[r["mint_address"]] = float(v)
    except Exception:
        pass
    return refs


def _filter_outlier_marks(rows: List[Tuple[str, float]], now: float):
    """Split rows into (clean, rejected). rejected logged as MARK_OUTLIER_REJECTED.

    A large adverse one-tick move is only QUARANTINED (not written to position
    last_price/highest_price_seen/ticks) when it is UNCONFIRMED - i.e. the position
    does not already have a recent prior mark at a similarly-low level. The first
    big-drop tick is held back; if the NEXT cycle's price confirms the drop (within
    tolerance of the quarantined price), it is allowed through. This blocks single
    poisoned ticks (5964/6007: helius green, one enricher -96% tick) while still
    letting a genuine sustained crash close the position one cycle later.
    """
    if not _MARK_OUTLIER_GUARD_ENABLED or not rows:
        return rows, []
    clean, rejected = [], []
    try:
        with get_connection() as conn:
            refs = _reference_marks(conn, [m for m, _ in rows])
            quarantine = _load_quarantine(conn, [m for m, _ in rows])
    except Exception:
        return rows, []
    thr = _MARK_OUTLIER_REJECT_PCT
    confirmed_now = {}
    for mint, price in rows:
        ref = refs.get(mint)
        if not ref or not price or price <= 0:
            clean.append((mint, price))
            continue
        move_pct = (float(price) - ref) / ref * 100.0
        if move_pct >= 5000.0:
            rejected.append((mint, price, ref, move_pct))
            logging.warning("[MARK_OUTLIER_REJECTED] mint=%s bad_price=%.12g ref=%.12g "
                            "move=%.1f%% - non-physical up-spike", str(mint)[:16], float(price), ref, move_pct)
            continue
        if move_pct <= -thr:
            # adverse big drop. Confirm against quarantine from a prior cycle.
            q = quarantine.get(mint)
            if q is not None and abs((float(price) - q) / q * 100.0) <= thr:
                # second corroborating low tick -> real crash, allow through
                clean.append((mint, price))
            else:
                # first unconfirmed big drop -> quarantine, don't write yet
                confirmed_now[mint] = float(price)
                rejected.append((mint, price, ref, move_pct))
                logging.warning(
                    "[MARK_OUTLIER_REJECTED] mint=%s bad_price=%.12g ref=%.12g move=%.1f%% "
                    "thr=%.0f%% - quarantined pending confirmation (poison suspected)",
                    str(mint)[:16], float(price), ref, move_pct, thr,
                )
        else:
            clean.append((mint, price))
    try:
        with get_connection() as conn:
            _save_quarantine(conn, confirmed_now, now)
    except Exception:
        pass
    return clean, rejected


def _load_quarantine(conn, mints: List[str]) -> Dict[str, float]:
    out: Dict[str, float] = {}
    if not mints:
        return out
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mark_quarantine "
            "(mint_address TEXT PRIMARY KEY, price REAL, ts REAL)"
        )
        qs = ",".join("?" for _ in mints)
        cutoff = time.time() - 120  # quarantine valid for 120s
        for r in conn.execute(
            f"SELECT mint_address, price FROM mark_quarantine "
            f"WHERE mint_address IN ({qs}) AND ts>=?",
            list(mints) + [cutoff],
        ).fetchall():
            if r["price"] and float(r["price"]) > 0:
                out[r["mint_address"]] = float(r["price"])
    except Exception:
        pass
    return out


def _save_quarantine(conn, prices: Dict[str, float], now: float) -> None:
    if not prices:
        return
    try:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS mark_quarantine "
            "(mint_address TEXT PRIMARY KEY, price REAL, ts REAL)"
        )
        for mint, price in prices.items():
            conn.execute(
                "INSERT INTO mark_quarantine(mint_address, price, ts) VALUES(?,?,?) "
                "ON CONFLICT(mint_address) DO UPDATE SET price=excluded.price, ts=excluded.ts",
                (mint, price, now),
            )
        conn.commit()
    except Exception:
        pass

def append_mtm_rows(rows: List[Tuple[str, float]], now: float) -> int:
    # PATCH (2026-06-26 ported): drop poisoned marks before they touch positions.
    rows, _rejected = _filter_outlier_marks(rows, now)
    inserted = 0
    with get_connection() as conn:
        for seq, (mint, price_value) in enumerate(rows):
            # Candidate-identity invariant:
            # - Open positions may receive a dedicated MTM snapshot row.
            # - Discovery/qualification candidates must be updated in place so
            #   mint_confidence, source_note and birth timestamps are preserved.
            _open_pos = conn.execute(
                "SELECT 1 FROM paper_positions "
                "WHERE mint_address=? AND status='OPEN' LIMIT 1",
                (mint,),
            ).fetchone()

            if _open_pos:
                conn.execute("""
                    INSERT INTO market_snapshots (
                        tx_hash,
                        token_name,
                        mint_address,
                        observed_price,
                        timestamp,
                        mint_confidence,
                        resolver_status,
                        resolution_method,
                        candidate_state,
                        price_attempts,
                        price_last_attempt_at,
                        price_status,
                        price_updated_at,
                        execution_ready,
                        latched
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    build_mtm_tx_hash(mint, now, seq),
                    "MTM",
                    mint,
                    price_value,
                    now,
                    0.0,
                    "resolved",
                    "mtm_oracle",
                    "mtm",
                    0,
                    now,
                    "priced",
                    now,
                    0,
                    0,
                ))
                inserted += 1
            else:
                conn.execute("""
                    UPDATE market_snapshots
                    SET observed_price=?,
                        price_attempts=0,
                        price_last_attempt_at=?,
                        price_status='priced',
                        price_updated_at=?
                    WHERE id=(
                        SELECT id FROM market_snapshots
                        WHERE mint_address=?
                          AND candidate_state IN ('pending','qualified')
                          AND COALESCE(execution_ready,0) != 2
                        ORDER BY id DESC
                        LIMIT 1
                    )
                """, (price_value, now, now, mint))

            conn.execute(
                """
                UPDATE paper_positions
                SET last_price = ?,
                    last_marked_at = ?,
                    unrealized_pnl_usd = CASE
                        WHEN entry_price > 0 THEN position_size_usd * ((? - entry_price) / entry_price)
                        ELSE COALESCE(unrealized_pnl_usd, 0.0)
                    END,
                    highest_price_seen = CASE
                        WHEN COALESCE(highest_price_seen, 0) > ? THEN highest_price_seen
                        ELSE ?
                    END
                WHERE mint_address = ?
                  AND status = 'OPEN'
                """,
                (price_value, now, price_value, price_value, price_value, mint),
            )
        conn.commit()
    return inserted


def build_headers() -> Dict[str, str]:
    headers = {"Accept": "application/json"}
    if API_KEY:
        headers["X-API-Key"] = API_KEY
    return headers


def fetch_prices(session: requests.Session, mints: List[str]) -> Dict[str, dict]:
    response = session.get(
        JUPITER_URL,
        params={"ids": ",".join(mints)},
        headers=build_headers(),
        timeout=HTTP_TIMEOUT,
    )
    if response.status_code == 429:
        raise requests.HTTPError("429 rate limited", response=response)
    response.raise_for_status()
    payload = response.json()
    return payload.get("data", payload) if isinstance(payload, dict) else {}


def get_latched_stale_mints() -> list[dict]:
    """
    Return latched tokens whose price_updated_at is older than 120s.
    These are qualified tokens waiting for execution that need fresh prices.
    Oracle tries Jupiter first; if Jupiter returns null it falls back to DexScreener.
    """
    try:
        cutoff = time.time() - 120
        with get_connection() as conn:
            rows = conn.execute("""
                SELECT id, mint_address, observed_price
                FROM market_snapshots
                WHERE latched = 1
                  AND COALESCE(execution_ready,0) IN (1,2)
                  AND candidate_state = 'latched'
                  AND (price_updated_at IS NULL OR price_updated_at < ?)
                  AND COALESCE(tx_hash, '') NOT LIKE 'mtm:%'
            """, (cutoff,)).fetchall()
        return [{"id": r["id"], "mint": r["mint_address"],
                 "last_price": r["observed_price"]} for r in rows]
    except Exception:
        return []


def refresh_price_dexscreener(session: requests.Session, mint: str) -> float | None:
    """
    Fetch current price from DexScreener for a single mint.
    Used as fallback when Jupiter does not have the token listed.
    Returns usd price float or None.
    """
    try:
        url = f"https://api.dexscreener.com/tokens/v1/solana/{mint}"
        resp = session.get(url, timeout=HTTP_TIMEOUT, headers={"Accept": "application/json"})
        if resp.status_code != 200:
            return None
        data = resp.json() or []
        # v1/solana endpoint returns list directly, fallback to .pairs for legacy
        if isinstance(data, dict):
            pairs = data.get("pairs") or []
        else:
            pairs = data if isinstance(data, list) else []
        sol_pairs = [p for p in pairs if isinstance(p, dict)
                     and str(p.get("chainId") or "").lower() == "solana"]
        if not sol_pairs:
            return None
        best = max(sol_pairs,
                   key=lambda p: float((p.get("liquidity") or {}).get("usd") or 0))
        price = best.get("priceUsd")
        return float(price) if price else None
    except Exception:
        return None


def update_rows(rows: List[sqlite3.Row], price_map: Dict[str, dict], max_attempts: int) -> Tuple[int, int, int]:
    """Write price results back one row at a time - never hold lock across batch."""
    priced, retried, dead, now = 0, 0, 0, time.time()
    for row in rows:
        row_id   = row["id"]
        mint     = row["mint_address"]
        attempts = int(row["price_attempts"] or 0) + 1
        usd      = (price_map.get(mint, {}) or {}).get("usdPrice")

        if usd is not None:
            try:
                price_value = float(usd)
                if price_value > 0:
                    for attempt in range(4):
                        try:
                            with get_connection() as conn:
                                conn.execute(
                                    "UPDATE market_snapshots SET observed_price=?, price_attempts=?, "
                                    "price_last_attempt_at=?, price_updated_at=?, price_status='priced' WHERE id=?",
                                    (price_value, attempts, now, now, row_id),
                                )
                                conn.commit()
                            priced += 1
                            try:
                                log_cognition("ORACLE",
                                    f"Priced {str(row['token_name'] if 'token_name' in row.keys() else '')[:14]} at "
                                    f"${float(price_value):.8f}. Establishing baseline.",
                                    token=str(row['token_name'] if 'token_name' in row.keys() else '')[:16])
                            except Exception:
                                pass
                            break
                        except Exception:
                            if attempt < 3:
                                time.sleep(0.05 * (2 ** attempt))
                    continue
            except (TypeError, ValueError):
                pass

        new_status = "dead" if attempts >= max_attempts else "retry"
        for attempt in range(4):
            try:
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE market_snapshots SET price_attempts=?, price_last_attempt_at=?, price_status=? WHERE id=?",
                        (attempts, now, new_status, row_id),
                    )
                    conn.commit()
                if new_status == "dead":
                    dead += 1
                else:
                    retried += 1
                break
            except Exception:
                if attempt < 3:
                    time.sleep(0.05 * (2 ** attempt))
    return priced, retried, dead


_oracle_idle_count = 0

def run() -> None:
    ensure_oracle_schema()
    update_heartbeat(SERVICE_NAME, "starting", "price_enricher online - Jupiter+DexScreener dual oracle")
    print("PRICE ENRICHER ONLINE")
    session = requests.Session()

    while True:
        cooldown = get_config_value("ORACLE_429_COOLDOWN_SECONDS", 30, int)
        max_attempts = get_config_value("ORACLE_MAX_ATTEMPTS", 6, int)

        try:
            rows = get_pending_rows(limit=200)

            # --- MTM refresh for open positions ---
            # This block runs UNCONDITIONALLY every cycle, regardless of whether
            # there are pending rows to price. This is the fix: previously this
            # block was gated behind `if not rows: continue` and would never run
            # when the pending queue was empty, starving open positions of fresh
            # prices and preventing executor exit checks from ever firing.
            open_mints = get_open_position_mints()
            mtm_total_inserted = 0
            if open_mints:
                for mint_chunk in chunked(open_mints, BATCH_LIMIT):
                    price_map = fetch_prices(session, mint_chunk)
                    mtm_rows = []
                    now = time.time()
                    for mint in mint_chunk:
                        usd = (price_map.get(mint, {}) or {}).get("usdPrice")
                        price_value = None
                        if usd is not None:
                            try:
                                price_value = float(usd) if float(usd) > 0 else None
                            except (TypeError, ValueError):
                                price_value = None
                        # FIX: DexScreener fallback for open position MTM pricing.
                        # Pre-graduation pump.fun tokens are not listed on Jupiter.
                        # Without this fallback, Jupiter returns null, usd is None,
                        # no MTM row is written, _evaluate_exit sees price_age > 300
                        # and returns without selling - every cycle until max hold timeout.
                        # This mirrors the same fallback already used for latched tokens.
                        if price_value is None:
                            price_value = refresh_price_dexscreener(session, mint)
                        if price_value and price_value > 0:
                            mtm_rows.append((mint, price_value))
                    if mtm_rows:
                        mtm_total_inserted += append_mtm_rows(mtm_rows, now)
                        # Cognition: organism sees its open positions being priced live
                        try:
                            # Log only first token per batch to avoid cognition spam
                            if mtm_rows:
                                mint, price_val = mtm_rows[0]
                                log_cognition(
                                    "PRICE_ENRICHER",
                                    f"MTM batch: {len(mtm_rows)} positions refreshed. "
                                    f"Lead token ${price_val:.8f}.",
                                    token=mint[:16],
                                )
                        except Exception:
                            pass

            # --- PERMANENT FIX: Refresh prices for latched tokens ---
            # Latched tokens are qualified and waiting for the executor to open a position.
            # The supervisor requires price_updated_at < max_age (600s) to keep them live.
            # For pre-graduation pump.fun tokens Jupiter returns null, so we fall back to
            # DexScreener. This keeps latched tokens fresh so supervisor never defers them.
            latched_stale = get_latched_stale_mints()
            latched_refreshed = 0
            for item in latched_stale:
                mint = item["mint"]
                row_id = item["id"]
                # Try Jupiter first
                try:
                    pm = fetch_prices(session, [mint])
                    usd = (pm.get(mint, {}) or {}).get("usdPrice")
                    price_value = float(usd) if usd else None
                except Exception:
                    price_value = None
                # Fallback to DexScreener if Jupiter has no listing
                if not price_value:
                    price_value = refresh_price_dexscreener(session, mint)
                if price_value and price_value > 0:
                    try:
                        with get_connection() as conn:
                            conn.execute(
                                "UPDATE market_snapshots SET observed_price=?, "
                                "price_updated_at=?, price_status='priced' WHERE id=?",
                                (price_value, time.time(), row_id)
                            )
                            conn.commit()
                        latched_refreshed += 1
                    except Exception:
                        pass

            if not rows:
                global _oracle_idle_count
                _oracle_idle_count += 1
                if _oracle_idle_count % 15 == 1:  # every ~30s (2s sleep * 15)
                    try:
                        log_cognition(
                            "ORACLE",
                            f"Scanning price layer - {len(open_mints)} open position(s) under MTM watch. "
                            f"No new snapshots pending Jupiter pricing.",
                        )
                    except Exception:
                        pass
                update_heartbeat(
                    SERVICE_NAME,
                    "ALIVE",
                    f"Idle mtm_rows={mtm_total_inserted} open_mints={len(open_mints)}",
                )
                time.sleep(2)
                continue

            # --- Price pending snapshots ---
            mint_to_rows: Dict[str, list] = {}
            for row in rows:
                if row["mint_address"]:
                    mint_to_rows.setdefault(row["mint_address"], []).append(row)

            all_unique_mints = list(mint_to_rows.keys())
            total_priced, total_retried, total_dead = 0, 0, 0

            for mint_chunk in chunked(all_unique_mints, BATCH_LIMIT):
                price_map = fetch_prices(session, mint_chunk)
                affected_rows = []
                for mint in mint_chunk:
                    affected_rows.extend(mint_to_rows.get(mint, []))
                priced, retried, dead = update_rows(affected_rows, price_map, max_attempts)
                total_priced += priced
                total_retried += retried
                total_dead += dead

            update_heartbeat(
                SERVICE_NAME,
                "ALIVE",
                (
                    f"priced={total_priced} retried={total_retried} dead={total_dead} "
                    f"mtm_rows={mtm_total_inserted} open_mints={len(open_mints)}"
                ),
                work_processed=total_priced + mtm_total_inserted,
                last_success_at=time.time() if (total_priced > 0 or mtm_total_inserted > 0) else None,
            )

        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if status == 429:
                update_heartbeat(SERVICE_NAME, "WARN", f"Rate limited; cooldown={cooldown}s")
                time.sleep(cooldown)
                continue
            update_heartbeat(SERVICE_NAME, "ERROR", f"HTTP {status}: {exc}")

        except Exception as exc:
            update_heartbeat(SERVICE_NAME, "ERROR", str(exc))

        import random
        time.sleep(2 + random.uniform(0, 0.5))


if __name__ == "__main__":
    run()