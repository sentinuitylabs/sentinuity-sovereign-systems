"""
services/tax_allocator.py
==========================
Sentinuity Tax Reserve Engine — High Water Mark Accounting

DOCTRINE (AU context):
  - Active trading income taxed as ordinary income, not CGT
  - Tax-free threshold ~$18,200 AUD (adjust TAX_RATE to your bracket)
  - Safe default: 20% (conservative — adjust after first tax return)
  - Division 296: additional 15% on super earnings above $3M from July 2026

HIGH WATER MARK METHOD (HWM):
  - Tax only fires when cumulative net PnL exceeds prior peak
  - Losses create a tax shield — no tax until previous peak is recovered
  - Only NEW net profit above the prior peak is taxable
  - NEVER reduce the reserve on losses
  - Mathematically correct capital separation

Example:
  Trade 1: +$10  → net=$10, hwm=0→$10, taxable=$10, tax=$2
  Trade 2: -$6   → net=$4,  hwm=$10,   taxable=0,   tax=0  (shield)
  Trade 3: +$8   → net=$12, hwm=$10→$12, taxable=$2, tax=$0.40

This is LEGALLY DEFENSIBLE — you only reserve tax on net new profit peaks.

Hook: called from close_position_canonical() in execution_engine.py
Deploy: services/tax_allocator.py
"""
from __future__ import annotations
import sqlite3
import time
import logging
from pathlib import Path

log = logging.getLogger("tax_allocator")

_here = Path(__file__).resolve().parent
ROOT  = _here
for _c in [_here, _here.parent, _here.parent.parent]:
    if (_c / "core").exists() and (_c / "services").exists():
        ROOT = _c
        break
DB_PATH = ROOT / "sentinuity_matrix.db"

DEFAULT_TAX_RATE = 0.20   # 20% — conservative AU buffer

# ── Schema ────────────────────────────────────────────────────────────────────

def ensure_tax_schema() -> None:
    """Create tax tables. Safe to call multiple times."""
    try:
        c = sqlite3.connect(str(DB_PATH), timeout=5)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=2000")

        # High Water Mark ledger — one row per trade close
        c.execute("""
            CREATE TABLE IF NOT EXISTS tax_ledger (
                id                  INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id            INTEGER UNIQUE,
                realized_pnl_usd    REAL,
                cumulative_net_pnl  REAL,
                high_water_mark     REAL,
                allocated_tax_usd   REAL,
                tax_rate_used       REAL,
                financial_year      TEXT,
                timestamp           REAL DEFAULT (strftime('%s','now'))
            )
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_tax_ledger_trade
            ON tax_ledger(trade_id)
        """)
        c.execute("""
            CREATE INDEX IF NOT EXISTS idx_tax_ledger_ts
            ON tax_ledger(timestamp)
        """)

        # Legacy tables — keep for backward compat with existing data
        c.execute("""
            CREATE TABLE IF NOT EXISTS tax_reserve (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                position_id     INTEGER,
                token_name      TEXT DEFAULT '',
                pnl_usd         REAL,
                tax_allocated   REAL,
                tax_rate_used   REAL,
                financial_year  TEXT,
                created_at      REAL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS tax_state (
                id                  INTEGER PRIMARY KEY,
                total_reserved      REAL DEFAULT 0,
                total_gross_profit  REAL DEFAULT 0,
                trade_count         INTEGER DEFAULT 0,
                last_updated        REAL DEFAULT 0
            )
        """)
        c.execute("""
            INSERT OR IGNORE INTO tax_state
                (id, total_reserved, total_gross_profit, trade_count, last_updated)
            VALUES (1, 0, 0, 0, 0)
        """)

        # Ensure TAX_RESERVE_USD exists in system_config
        c.execute("""
            INSERT OR IGNORE INTO system_config (key, value, description)
            VALUES ('TAX_RESERVE_USD', '0.0', 'Running tax reserve — High Water Mark method')
        """)

        c.commit()
        c.close()
    except Exception as e:
        log.debug("tax schema init: %s", e)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _get_rate() -> float:
    try:
        c = sqlite3.connect(str(DB_PATH), timeout=2)
        c.execute("PRAGMA busy_timeout=1000")
        row = c.execute(
            "SELECT value FROM system_config WHERE key='TAX_RESERVE_RATE'"
        ).fetchone()
        c.close()
        if row:
            return float(row[0])
    except Exception:
        pass
    return DEFAULT_TAX_RATE


def _financial_year(ts: float) -> str:
    import datetime
    dt = datetime.datetime.fromtimestamp(ts)
    if dt.month >= 7:
        return f"FY{dt.year}-{str(dt.year+1)[2:]}"
    return f"FY{dt.year-1}-{str(dt.year)[2:]}"


def _get_last_ledger_row(conn: sqlite3.Connection) -> tuple[float, float]:
    """Return (prev_cumulative_net_pnl, prev_high_water_mark) from last ledger row."""
    row = conn.execute("""
        SELECT cumulative_net_pnl, high_water_mark
        FROM tax_ledger
        ORDER BY id DESC LIMIT 1
    """).fetchone()
    if row:
        return float(row[0] or 0), float(row[1] or 0)
    return 0.0, 0.0


# ── Core allocation ───────────────────────────────────────────────────────────

def allocate_tax(
    position_id: int,
    pnl_usd: float,
    token_name: str = "",
) -> float:
    """
    Apply High Water Mark tax allocation on trade close.

    Returns allocated tax amount (0.0 if no new peak reached).
    Never raises — fire-and-forget from close_position_canonical.

    HWM rules:
      - If cumulative net PnL > prior high water mark → tax the difference
      - If cumulative net PnL <= prior HWM → no tax, loss creates shield
      - Tax reserve (TAX_RESERVE_USD) only ever increases, never decreases
    """
    try:
        rate = _get_rate()
        now  = time.time()
        fy   = _financial_year(now)

        c = sqlite3.connect(str(DB_PATH), timeout=5)
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA busy_timeout=2000")
        c.row_factory = sqlite3.Row

        prev_net, prev_hwm = _get_last_ledger_row(c)

        current_net = prev_net + pnl_usd

        # High Water Mark calculation
        if current_net > prev_hwm:
            taxable = current_net - prev_hwm
            tax     = round(taxable * rate, 6)
            new_hwm = current_net
        else:
            # Loss or recovery below prior peak — no tax
            taxable = 0.0
            tax     = 0.0
            new_hwm = prev_hwm

        # Insert ledger row — UNIQUE on trade_id prevents double-booking
        try:
            c.execute("""
                INSERT INTO tax_ledger
                    (trade_id, realized_pnl_usd, cumulative_net_pnl,
                     high_water_mark, allocated_tax_usd, tax_rate_used,
                     financial_year, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                position_id, pnl_usd, current_net,
                new_hwm, tax, rate, fy, now,
            ))
        except sqlite3.IntegrityError:
            # Already recorded for this trade_id — idempotent, skip
            c.close()
            return 0.0

        # Update system_config TAX_RESERVE_USD — the single truth value
        if tax > 0:
            c.execute("""
                UPDATE system_config
                SET value = CAST(ROUND(CAST(value AS REAL) + ?, 6) AS TEXT)
                WHERE key = 'TAX_RESERVE_USD'
            """, (tax,))

        # Update legacy tax_state for backward compat
        c.execute("""
            UPDATE tax_state
            SET total_reserved     = total_reserved + ?,
                total_gross_profit = total_gross_profit + ?,
                trade_count        = trade_count + 1,
                last_updated       = ?
            WHERE id = 1
        """, (tax, max(0.0, pnl_usd), now))

        # Legacy tax_reserve row for existing reports
        if pnl_usd > 0:
            c.execute("""
                INSERT INTO tax_reserve
                    (position_id, token_name, pnl_usd, tax_allocated,
                     tax_rate_used, financial_year, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (position_id, token_name, pnl_usd, tax, rate, fy, now))

        c.commit()
        c.close()

        if tax > 0:
            log.info(
                "[TAX_HWM] trade=%d pnl=%+.4f net=%.4f hwm=%.4f taxable=%.4f tax=%.4f",
                position_id, pnl_usd, current_net, new_hwm, taxable, tax,
            )
        else:
            log.debug(
                "[TAX_SHIELD] trade=%d pnl=%+.4f net=%.4f hwm=%.4f (no new peak)",
                position_id, pnl_usd, current_net, new_hwm,
            )

        return tax

    except Exception as e:
        log.debug("tax_allocator error: %s", e)
        return 0.0


# ── Read helpers ──────────────────────────────────────────────────────────────

def get_tax_reserve_usd() -> float:
    """Read TAX_RESERVE_USD from system_config — the single truth value."""
    try:
        c = sqlite3.connect(str(DB_PATH), timeout=2)
        c.execute("PRAGMA busy_timeout=1000")
        row = c.execute(
            "SELECT value FROM system_config WHERE key='TAX_RESERVE_USD'"
        ).fetchone()
        c.close()
        return round(float(row[0] or 0), 2) if row else 0.0
    except Exception:
        return 0.0


def get_tax_state() -> dict:
    """Return current tax state including HWM data."""
    try:
        c = sqlite3.connect(str(DB_PATH), timeout=2)
        c.execute("PRAGMA busy_timeout=1000")
        c.row_factory = sqlite3.Row

        state = c.execute(
            "SELECT * FROM tax_state WHERE id=1"
        ).fetchone()

        last = c.execute("""
            SELECT cumulative_net_pnl, high_water_mark, allocated_tax_usd
            FROM tax_ledger ORDER BY id DESC LIMIT 1
        """).fetchone()

        reserve = c.execute(
            "SELECT value FROM system_config WHERE key='TAX_RESERVE_USD'"
        ).fetchone()

        c.close()

        return {
            "total_reserved":     round(float(reserve[0] or 0), 2) if reserve else 0.0,
            "total_gross_profit": round(float(state["total_gross_profit"] or 0), 2) if state else 0.0,
            "trade_count":        int(state["trade_count"] or 0) if state else 0,
            "last_updated":       float(state["last_updated"] or 0) if state else 0.0,
            "rate":               _get_rate(),
            "cumulative_net_pnl": round(float(last["cumulative_net_pnl"] or 0), 4) if last else 0.0,
            "high_water_mark":    round(float(last["high_water_mark"] or 0), 4) if last else 0.0,
        }
    except Exception:
        return {
            "total_reserved": 0.0, "total_gross_profit": 0.0,
            "trade_count": 0, "last_updated": 0.0,
            "rate": DEFAULT_TAX_RATE,
            "cumulative_net_pnl": 0.0, "high_water_mark": 0.0,
        }


def get_effective_capital(wallet_balance: float) -> float:
    """Return tradeable capital after removing tax reserve."""
    reserve = get_tax_reserve_usd()
    return max(0.0, wallet_balance - reserve)


def generate_tax_report(output_path: str = "tax_report.csv") -> str:
    """Generate accountant-ready CSV. Run manually."""
    try:
        import csv, datetime
        c = sqlite3.connect(str(DB_PATH), timeout=5)
        c.row_factory = sqlite3.Row
        records = c.execute("""
            SELECT
                tl.id, tl.trade_id, tl.realized_pnl_usd,
                tl.cumulative_net_pnl, tl.high_water_mark,
                tl.allocated_tax_usd, tl.tax_rate_used,
                tl.financial_year, tl.timestamp,
                pp.token_name, pp.opened_at, pp.closed_at,
                pp.entry_price, pp.exit_price, pp.exit_reason
            FROM tax_ledger tl
            LEFT JOIN paper_positions pp ON pp.id = tl.trade_id
            ORDER BY tl.timestamp ASC
        """).fetchall()
        c.close()

        with open(output_path, 'w', newline='', encoding='utf-8') as f:
            w = csv.writer(f)
            w.writerow([
                "ledger_id", "trade_id", "token", "financial_year",
                "trade_date", "realized_pnl_usd", "cumulative_net_pnl",
                "high_water_mark", "allocated_tax_usd", "tax_rate",
                "opened_at", "closed_at", "entry_price", "exit_price", "exit_reason"
            ])
            for r in records:
                dt = datetime.datetime.fromtimestamp(
                    float(r["timestamp"] or 0)
                ).strftime("%Y-%m-%d %H:%M:%S")
                w.writerow([
                    r["id"], r["trade_id"], r["token_name"] or "",
                    r["financial_year"], dt,
                    f"{r['realized_pnl_usd']:.4f}",
                    f"{r['cumulative_net_pnl']:.4f}",
                    f"{r['high_water_mark']:.4f}",
                    f"{r['allocated_tax_usd']:.4f}",
                    f"{r['tax_rate_used']:.2f}",
                    r["opened_at"] or "", r["closed_at"] or "",
                    r["entry_price"] or "", r["exit_price"] or "",
                    r["exit_reason"] or "",
                ])

        total_tax = sum(float(r["allocated_tax_usd"] or 0) for r in records)
        total_pnl = sum(float(r["realized_pnl_usd"] or 0) for r in records)
        return (f"✓ {len(records)} trades | "
                f"Net PnL: ${total_pnl:.2f} | "
                f"Tax reserved: ${total_tax:.2f} → {output_path}")

    except Exception as e:
        return f"✗ Report failed: {e}"


# ── Schema init on import ─────────────────────────────────────────────────────
ensure_tax_schema()


if __name__ == "__main__":
    import sys
    if "--report" in sys.argv:
        print(generate_tax_report())
    elif "--state" in sys.argv:
        s = get_tax_state()
        print(f"Tax reserved (HWM):  ${s['total_reserved']:.2f}")
        print(f"Cumulative net PnL:  ${s['cumulative_net_pnl']:.4f}")
        print(f"High water mark:     ${s['high_water_mark']:.4f}")
        print(f"Gross profit:        ${s['total_gross_profit']:.2f}")
        print(f"Trades counted:      {s['trade_count']}")
        print(f"Current rate:        {s['rate']*100:.0f}%")
    else:
        print("Usage: python tax_allocator.py --state | --report")
