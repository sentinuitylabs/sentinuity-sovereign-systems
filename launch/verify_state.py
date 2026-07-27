"""
ops/verify_state.py — Pre-Relaunch State Verification
=======================================================
Checks wallet, open positions, oracle freshness, and service heartbeats
to confirm the system is in a clean state before relaunch.

This is the go/no-go gate between the reset sequence and the relaunch.
Do not relaunch if any FAIL items are reported.

USAGE
-----
    python ops/verify_state.py
    python ops/verify_state.py --verbose

EXIT CODES
----------
  0  All checks pass — safe to relaunch
  1  One or more FAIL checks — resolve before relaunching
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = BASE_DIR / "sentinuity_matrix.db"

# ANSI colour codes — fall back to plain text if terminal doesn't support them
try:
    import os
    _COLOUR = os.isatty(sys.stdout.fileno())
except Exception:
    _COLOUR = False

def _c(code: str, text: str) -> str:
    if not _COLOUR:
        return text
    codes = {"green": "92", "red": "91", "yellow": "93", "cyan": "96", "bold": "1"}
    return f"\033[{codes.get(code,'0')}m{text}\033[0m"

PASS  = _c("green",  "[PASS]")
FAIL  = _c("red",    "[FAIL]")
WARN  = _c("yellow", "[WARN]")
INFO  = _c("cyan",   "[INFO]")


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH), timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone()
    return row is not None


def verify(verbose: bool = False) -> bool:
    if not DB_PATH.exists():
        print(f"{FAIL} Database not found at {DB_PATH}")
        return False

    all_pass = True

    try:
        conn = _connect()
    except Exception as e:
        print(f"{FAIL} Cannot open database: {e}")
        return False

    print(_c("bold", f"\n{'='*60}"))
    print(_c("bold", "  SENTINUITY — PRE-RELAUNCH STATE VERIFICATION"))
    print(_c("bold", f"  {time.strftime('%Y-%m-%d %H:%M:%S')}"))
    print(_c("bold", f"{'='*60}\n"))

    # ── CHECK 1: Wallet balance ────────────────────────────────────────────────
    print(_c("bold", "1. WALLET BALANCE"))
    try:
        row = conn.execute(
            "SELECT wallet_balance, initial_capital FROM system_state WHERE id=1"
        ).fetchone()
        if not row:
            print(f"  {FAIL} system_state row not found — DB not initialised")
            all_pass = False
        else:
            bal = float(row["wallet_balance"] or 0)
            cap = float(row["initial_capital"] or 0)
            if bal <= 0:
                print(f"  {FAIL} wallet_balance={bal:.2f} — zero or negative")
                all_pass = False
            elif bal > 100_000:
                print(f"  {FAIL} wallet_balance={bal:.2f} — suspiciously large (> $100,000)")
                all_pass = False
            else:
                print(f"  {PASS} wallet_balance={bal:.2f}  initial_capital={cap:.2f}")
            if verbose:
                roi = ((bal - cap) / max(cap, 1)) * 100 if cap > 0 else 0
                print(f"  {INFO} ROI from initial capital: {roi:+.2f}%")
    except Exception as e:
        print(f"  {FAIL} Could not read system_state: {e}")
        all_pass = False
    print()

    # ── CHECK 2: Open positions ────────────────────────────────────────────────
    print(_c("bold", "2. OPEN POSITIONS"))
    try:
        if not _table_exists(conn, "paper_positions"):
            print(f"  {INFO} paper_positions table does not exist yet — clean slate")
        else:
            open_pos = conn.execute(
                """
                SELECT id, token_name, entry_price, position_size_usd,
                       unrealized_pnl_usd, last_price, last_marked_at
                FROM paper_positions WHERE status='OPEN'
                """
            ).fetchall()

            if not open_pos:
                print(f"  {PASS} No open positions")
            else:
                print(f"  {WARN} {len(open_pos)} open position(s) found — confirm all intentional")
                for p in open_pos:
                    pos_id   = p["id"]
                    token    = str(p["token_name"] or "?")
                    entry_px = float(p["entry_price"] or 0)
                    size_usd = float(p["position_size_usd"] or 0)
                    unreal   = float(p["unrealized_pnl_usd"] or 0)
                    last_px  = float(p["last_price"] or 0)
                    marked   = float(p["last_marked_at"] or 0)
                    age_s    = time.time() - marked if marked > 0 else 9999

                    corrupt = False
                    reasons = []
                    if entry_px > 0 and last_px > 0 and last_px > entry_px * 100:
                        reasons.append(f"last_price is {last_px/entry_px:.0f}x entry")
                        corrupt = True
                    if abs(unreal) > size_usd * 10:
                        reasons.append(f"unrealized_pnl ({unreal:.2f}) > 10x position_size ({size_usd:.2f})")
                        corrupt = True
                    if age_s > 300:
                        reasons.append(f"last_marked_at is {age_s:.0f}s stale")

                    status = FAIL if corrupt else WARN
                    reason_str = " | ".join(reasons) if reasons else "OK"

                    print(
                        f"  {status} pos={pos_id} {token:<20} "
                        f"entry=${entry_px:.10f} size=${size_usd:.2f} "
                        f"unreal=${unreal:.4f} "
                        f"[{reason_str}]"
                    )
                    if corrupt:
                        all_pass = False
    except Exception as e:
        print(f"  {FAIL} Could not read paper_positions: {e}")
        all_pass = False
    print()

    # ── CHECK 3: MTM snapshot freshness ───────────────────────────────────────
    print(_c("bold", "3. RECENT MTM MARKET_SNAPSHOT ROWS"))
    try:
        if not _table_exists(conn, "market_snapshots"):
            print(f"  {INFO} market_snapshots does not exist — clean")
        else:
            now = time.time()
            recent_mtm = conn.execute(
                """
                SELECT COUNT(*) AS n,
                       MAX(price_updated_at) AS newest
                FROM market_snapshots
                WHERE candidate_state='mtm'
                  AND timestamp > ?
                """,
                (now - 300,),
            ).fetchone()
            n    = int(recent_mtm["n"] or 0)
            newest_ts = float(recent_mtm["newest"] or 0)
            newest_age = now - newest_ts if newest_ts > 0 else 9999

            if n == 0:
                print(f"  {PASS} No recent MTM rows (< 5min old) — oracle will start fresh")
            else:
                print(
                    f"  {WARN} {n} recent MTM rows found "
                    f"(newest is {newest_age:.0f}s old). "
                    f"Oracle will immediately re-use these on startup — "
                    f"they must not contain corrupt prices."
                )
                # Scan for implausible observed_price values in recent MTM rows
                corrupt_mtm = conn.execute(
                    """
                    SELECT COUNT(*) AS n FROM market_snapshots
                    WHERE candidate_state='mtm'
                      AND timestamp > ?
                      AND observed_price > 1.0
                    """,
                    (now - 300,),
                ).fetchone()["n"]
                if int(corrupt_mtm or 0) > 0:
                    print(
                        f"  {FAIL} {corrupt_mtm} MTM row(s) have observed_price > $1.00 "
                        f"— likely corrupt. Run clear_recent_data.py before relaunching."
                    )
                    all_pass = False
    except Exception as e:
        print(f"  {FAIL} Could not read market_snapshots: {e}")
        all_pass = False
    print()

    # ── CHECK 4: Service heartbeats ───────────────────────────────────────────
    print(_c("bold", "4. SERVICE HEARTBEATS (should all be stale — machine should be stopped)"))
    try:
        if not _table_exists(conn, "system_heartbeat"):
            print(f"  {INFO} system_heartbeat table not found — first run or table dropped")
        else:
            now = time.time()
            all_hb = conn.execute(
                "SELECT service_name, status, last_pulse FROM system_heartbeat ORDER BY service_name"
            ).fetchall()

            if not all_hb:
                print(f"  {INFO} No heartbeat records found")
            else:
                live_found = False
                for hb in all_hb:
                    svc   = hb["service_name"]
                    pulse = float(hb["last_pulse"] or 0)
                    age   = now - pulse if pulse > 0 else 9999
                    age_str = f"{age:.0f}s ago" if age < 9999 else "never"

                    if age < 15:
                        print(f"  {FAIL} {svc:<30} pulse={age_str} — SERVICE IS STILL RUNNING")
                        all_pass = False
                        live_found = True
                    elif age < 60:
                        print(f"  {WARN} {svc:<30} pulse={age_str} — stopped recently, allow 60s")
                    else:
                        if verbose:
                            print(f"  {PASS} {svc:<30} pulse={age_str} — stopped")
                        else:
                            pass  # summarise only

                if not live_found:
                    alive_count = sum(1 for hb in all_hb if (now - float(hb["last_pulse"] or 0)) < 60)
                    stale_count = len(all_hb) - alive_count
                    print(f"  {PASS} {stale_count}/{len(all_hb)} service(s) show stale heartbeat — machine appears stopped")
    except Exception as e:
        print(f"  {FAIL} Could not read system_heartbeat: {e}")
        all_pass = False
    print()

    # ── CHECK 5: drawdown halt flag ───────────────────────────────────────────
    print(_c("bold", "5. DRAWDOWN HALT FLAG"))
    try:
        if not _table_exists(conn, "system_config"):
            print(f"  {INFO} system_config not found — halt flags are inactive")
        else:
            halt = conn.execute(
                "SELECT value FROM system_config WHERE key='DRAWDOWN_HALT_ACTIVE'"
            ).fetchone()
            if halt and str(halt["value"]).strip() == "1":
                print(f"  {WARN} DRAWDOWN_HALT_ACTIVE=1 — executor entry scan will be blocked on relaunch")
                print(f"  {INFO} To clear: UPDATE system_config SET value='0' WHERE key='DRAWDOWN_HALT_ACTIVE'")
            else:
                print(f"  {PASS} DRAWDOWN_HALT_ACTIVE is 0 or not set — executor will run normally")
    except Exception as e:
        print(f"  {WARN} Could not check drawdown halt: {e}")
    print()

    conn.close()

    # ── VERDICT ───────────────────────────────────────────────────────────────
    print(_c("bold", "="*60))
    if all_pass:
        print(_c("green", _c("bold", "  VERDICT: GO — all checks passed. Safe to relaunch.")))
        print()
        print(f"  Relaunch with:  Launch_Sentinuity.bat")
        print(f"  Then confirm:   wallet_balance in UI matches the reset value")
        print(f"                  no corrupt unrealized_pnl on any open position")
        print(f"                  oracle heartbeat appears within 30s of launch")
    else:
        print(_c("red", _c("bold", "  VERDICT: NO-GO — resolve all FAIL items before relaunching.")))
        print()
        print(f"  Common fixes:")
        print(f"    FAIL: wallet_balance absurd   → python ops/reset_wallet.py --balance 1000.0")
        print(f"    FAIL: corrupt open position   → python ops/clear_positions.py")
        print(f"    FAIL: corrupt MTM rows        → python ops/clear_recent_data.py --hours 4")
        print(f"    FAIL: service still running   → taskkill /F /IM python.exe /T  (Windows)")
        print(f"                                    pkill -f sentinuity             (Linux)")
    print(_c("bold", "="*60))
    print()

    return all_pass


# ── CLI ───────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Verify system state is clean before Sentinuity relaunch."
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Show all heartbeat entries (not just failures)"
    )
    args = parser.parse_args()

    ok = verify(verbose=args.verbose)
    sys.exit(0 if ok else 1)
