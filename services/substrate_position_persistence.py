#!/usr/bin/env python3
"""
services/substrate_position_persistence.py — MISSION G.
Reboot-safe journal for substrate longs/shorts. NEVER deletes a position.

Boot policy:
  offline <= intended_hold_seconds (and within stale_after)  -> RESUME
  offline >  stale_after_seconds                             -> STALE_REBOOT_REVIEW
  else                                                       -> REVIEW_REQUIRED
Positions are re-marked, never wiped; substrate PnL stays labelled separately
from Solana paper PnL.

  python services/substrate_position_persistence.py --db sentinuity_matrix.db --boot
  python services/substrate_position_persistence.py --db sentinuity_matrix.db --status
"""
import sqlite3, time, argparse, json

DDL = """CREATE TABLE IF NOT EXISTS substrate_position_journal(
 position_id TEXT PRIMARY KEY, instrument TEXT, side TEXT,
 entry_time REAL, entry_price REAL, size_usd REAL,
 intended_hold_seconds REAL, thesis TEXT, risk_state TEXT,
 last_mark_time REAL, last_mark_price REAL, unrealized_pnl_usd REAL,
 reboot_seen_at REAL, stale_after_seconds REAL,
 recovery_action TEXT, journal_updated_at REAL, lane TEXT DEFAULT 'SUBSTRATE')"""

DEFAULT_STALE = {"SCALP": 900, "LONG": 86400, "THESIS": 604800, "SHORT": 86400}

def connect(db):
    c = sqlite3.connect(db); c.row_factory = sqlite3.Row
    c.execute(DDL)
    c.execute("CREATE INDEX IF NOT EXISTS idx_spj_action ON substrate_position_journal(recovery_action)")
    return c

def journal_open(c, position_id, instrument, side, entry_price, size_usd,
                 intended_hold_seconds=None, thesis="", kind="LONG"):
    """Call at substrate open time. MISSION H: intended hold stored at open."""
    now = time.time()
    hold = intended_hold_seconds or DEFAULT_STALE.get(kind.upper(), 86400)
    c.execute("""INSERT OR REPLACE INTO substrate_position_journal
      (position_id,instrument,side,entry_time,entry_price,size_usd,intended_hold_seconds,
       thesis,risk_state,last_mark_time,last_mark_price,unrealized_pnl_usd,
       stale_after_seconds,recovery_action,journal_updated_at,lane)
      VALUES(?,?,?,?,?,?,?,?,'OK',?,?,0,?,'ACTIVE',?, 'SUBSTRATE')""",
      (str(position_id), instrument, side.upper(), now, entry_price, size_usd,
       hold, thesis, now, entry_price, hold*2, now))
    c.commit()

def journal_mark(c, position_id, price, unrealized):
    now = time.time()
    c.execute("""UPDATE substrate_position_journal SET last_mark_time=?, last_mark_price=?,
       unrealized_pnl_usd=?, journal_updated_at=? WHERE position_id=?""",
       (now, price, unrealized, now, str(position_id)))
    c.commit()

def boot_recover(c, verbose=True):
    """On boot: classify every still-open journalled position. Deletes nothing."""
    now = time.time(); acted = []
    rows = c.execute("SELECT * FROM substrate_position_journal WHERE recovery_action NOT IN ('CLOSED','ARCHIVE_STALE')").fetchall()
    for r in rows:
        d = dict(r)
        offline = now - float(d["last_mark_time"] or d["entry_time"] or now)
        hold  = float(d["intended_hold_seconds"] or 86400)
        stale = float(d["stale_after_seconds"] or hold*2)
        if offline <= hold:      action = "RESUME"
        elif offline > stale:    action = "STALE_REBOOT_REVIEW"
        else:                    action = "REVIEW_REQUIRED"
        c.execute("""UPDATE substrate_position_journal SET reboot_seen_at=?, recovery_action=?,
           journal_updated_at=? WHERE position_id=?""", (now, action, now, d["position_id"]))
        acted.append((d["position_id"], d["instrument"], d["side"], round(offline), action))
    c.commit()
    if verbose:
        print(f"  substrate positions journalled: {len(rows)}  (none deleted)")
        for pid, inst, side, off, act in acted:
            print(f"    {pid:14} {inst:10} {side:5} offline={off:>7}s -> {act}")
        if not acted: print("    (no open substrate positions)")
    return acted

def status(c):
    print("  substrate journal status:")
    for r in c.execute("""SELECT recovery_action, COUNT(*) n, COALESCE(SUM(unrealized_pnl_usd),0) upnl
                          FROM substrate_position_journal GROUP BY recovery_action"""):
        print(f"    {r[0]:22} n={r[1]:<4} unrealized=${r[2]:+.2f}  [SUBSTRATE lane, not Solana paper]")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="sentinuity_matrix.db")
    ap.add_argument("--boot", action="store_true")
    ap.add_argument("--status", action="store_true")
    a = ap.parse_args()
    c = connect(a.db)
    print("="*60); print("  SUBSTRATE POSITION PERSISTENCE"); print("="*60)
    if a.boot: boot_recover(c)
    status(c)
    c.close()
    print("\n  Positions are never deleted. Substrate PnL stays lane-labelled.")
