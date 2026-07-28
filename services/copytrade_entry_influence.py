"""
services/copytrade_entry_influence.py
ADVISORY copytrade tagging at paper entry — observational only.

Doctrine (SIGNOFF_COPYTRADE_PAPER_BONUS_20260613):
  - PAPER-ONLY. Never touches live execution.
  - ADVISORY. Records whether a tracked smart wallet touched this mint recently,
    onto the paper_positions row, so influence is *visible and measurable*.
  - DOES NOT change the entry decision. The position is already open by the time
    this runs; this only annotates it. No gate is bypassed, no admission altered.
  - The admission-changing confidence bonus is a SEPARATE step that lives in
    smart_wallet_conviction.py and is intentionally NOT done here.

Why self-contained: this reads only copytrade/smart-wallet tables that already
exist in sentinuity_matrix.db. It introspects their columns (same defensive
approach as sentinuity_diagnostics.py) so it either resolves a mint↔wallet link
and tags the row, or safely no-ops. It can never raise into the caller.

Columns written on paper_positions (added by execution_engine schema-ensure):
  copytrade_influenced       INTEGER  1 if a tracked wallet touched this mint
  copytrade_source           TEXT     e.g. 'smart_wallet_events' / 'gmgn' / 'telegram'
  copytrade_wallet           TEXT     the wallet address that touched it (truncated)
  copytrade_confidence_bonus REAL     advisory bonus (capped), NOT applied to admission
  copytrade_reason           TEXT     human-readable note (column pre-exists)
"""
from __future__ import annotations
import sqlite3
import time

# Doctrine cap — advisory only; recorded, never auto-applied to admission here.
_BONUS_CAP = 0.03
# How recently a wallet touch must have happened to count as influence.
_RECENCY_SEC = 6 * 3600

# Candidate tables that may carry "a smart wallet touched a mint" evidence,
# in priority order. We probe each for usable columns.
_CANDIDATE_TABLES = [
    "smart_wallet_events",
    "copytrade_shadow_events",
    "copytrade_signals",
    "wallet_entry_fingerprints",
    "wallet_pattern_observations",
]

_MINT_COLS   = ("mint_address", "mint", "token_mint", "address", "ca", "contract")
_WALLET_COLS = ("wallet_address", "wallet", "trader", "address_wallet",
                 "signer", "owner", "wallet_addr")
_TS_COLS     = ("observed_at", "created_at", "ts", "event_ts", "last_seen",
                "updated_at", "added_at")
_SRC_COLS    = ("source", "src", "origin", "provider", "channel")


def _cols(conn: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
    except Exception:
        return set()


def _pick(available: set[str], candidates) -> str | None:
    for c in candidates:
        if c in available:
            return c
    # loose contains-match fallback (e.g. 'buyer_wallet')
    for c in candidates:
        for a in available:
            if c in a:
                return a
    return None


def _lookup_influence(conn: sqlite3.Connection, mint: str) -> dict | None:
    """Return {source, wallet, reason} if a tracked wallet touched this mint
    recently, else None. Defensive: returns None on any uncertainty."""
    if not mint:
        return None
    now = time.time()
    for table in _CANDIDATE_TABLES:
        cset = _cols(conn, table)
        if not cset:
            continue
        mcol = _pick(cset, _MINT_COLS)
        if not mcol:
            continue  # can't link to a mint — skip this table
        wcol = _pick(cset, _WALLET_COLS)
        tcol = _pick(cset, _TS_COLS)
        scol = _pick(cset, _SRC_COLS)

        sel = [mcol]
        if wcol: sel.append(wcol)
        if tcol: sel.append(tcol)
        if scol: sel.append(scol)
        where = f"{mcol}=?"
        params: list = [mint]
        if tcol:
            where += f" AND {tcol} > ?"
            params.append(now - _RECENCY_SEC)
        order = f" ORDER BY {tcol} DESC" if tcol else ""
        try:
            row = conn.execute(
                f"SELECT {', '.join(sel)} FROM {table} WHERE {where}{order} LIMIT 1",
                params,
            ).fetchone()
        except Exception:
            continue
        if not row:
            continue
        wallet = ""
        if wcol:
            try:
                wallet = str(row[sel.index(wcol)] or "")[:44]
            except Exception:
                wallet = ""
        source = table
        if scol:
            try:
                source = str(row[sel.index(scol)] or table)[:24]
            except Exception:
                source = table
        reason = f"smart wallet {wallet[:8] or '?'} touched mint via {source}"
        return {"source": source, "wallet": wallet, "reason": reason}
    return None


def mark_copytrade_influence(conn: sqlite3.Connection, position_id: int,
                             mint: str) -> bool:
    """Annotate a just-opened paper_positions row with advisory copytrade
    influence. Returns True if the row was tagged as influenced.

    NEVER raises. NEVER changes the trade decision. Safe to call inside the
    same open transaction (uses the provided conn)."""
    try:
        info = _lookup_influence(conn, mint)
    except Exception:
        info = None

    if not info:
        # Explicitly mark as mainline so the feed can show MAINLINE ONLY.
        try:
            conn.execute(
                "UPDATE paper_positions SET copytrade_influenced=0 WHERE id=?",
                (position_id,),
            )
        except Exception:
            pass
        return False

    # Advisory bonus is recorded but capped; it is NOT applied to admission here.
    bonus = _BONUS_CAP
    try:
        conn.execute(
            """UPDATE paper_positions
               SET copytrade_influenced=1,
                   copytrade_source=?,
                   copytrade_wallet=?,
                   copytrade_confidence_bonus=?,
                   copytrade_reason=?
               WHERE id=?""",
            (info["source"], info["wallet"], bonus, info["reason"], position_id),
        )
        return True
    except Exception:
        return False
