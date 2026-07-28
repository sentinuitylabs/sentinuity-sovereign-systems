#!/usr/bin/env python3
"""
core/live_lane_common.py — shared helpers for the live-lane measurement layer.

DOCTRINE (non-negotiable):
  * Matrix DB is opened READ-ONLY by everything in this layer.
  * All writes go to sentinuity_intelligence.db only.
  * No live arming. No signing. No swap paths. Shadow only.

All SQL is defensive: columns are introspected at runtime and missing
columns are tolerated (older/pruned DBs must never crash an audit).
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

ROOT = Path(__file__).resolve().parent.parent

# Thresholds from the signed-off overnight audit doctrine
MONSTER_WIN_USD = 80.0     # winners >= $80 are "monsters"
BAD_LOSS_USD    = -10.0    # losses <= -$10 are "bad losses"

AEST_OFFSET_HOURS = 10     # Australia/Melbourne standard offset (no DST math;
                           # hour buckets are doctrine buckets, not tax records)


# ---------------------------------------------------------------- db locate

def _first_existing(cands: List[Path]) -> Optional[Path]:
    for p in cands:
        try:
            if p and p.exists() and p.stat().st_size > 0:
                return p
        except Exception:
            pass
    return None


def find_matrix_db(explicit: Optional[str] = None) -> Optional[Path]:
    if explicit:
        p = Path(explicit)
        return p if p.exists() else None
    env = os.environ.get("SENTINUITY_MATRIX_DB")
    return _first_existing([
        Path(env) if env else None,
        ROOT / "sentinuity_matrix.db",
        ROOT / "services" / "sentinuity_matrix.db",
    ])


def find_intel_db(explicit: Optional[str] = None, create_near_matrix: bool = True) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("SENTINUITY_INTEL_DB")
    found = _first_existing([
        Path(env) if env else None,
        ROOT / "sentinuity_intelligence.db",
        ROOT / "services" / "sentinuity_intelligence.db",
    ])
    if found:
        return found
    # default location mirrors core.schema.get_intel_connection
    return ROOT / "sentinuity_intelligence.db"


def connect_ro(path: Path) -> sqlite3.Connection:
    """Read-only connection. query_only=ON as a second lock."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=10)
    con.row_factory = sqlite3.Row
    try:
        con.execute("PRAGMA query_only=ON")
    except Exception:
        pass
    return con


def connect_intel_rw(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(str(path), timeout=15, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    con.execute("PRAGMA busy_timeout=5000")
    return con


# ---------------------------------------------------------------- introspect

def table_exists(con: sqlite3.Connection, table: str) -> bool:
    try:
        r = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        return r is not None
    except Exception:
        return False


def cols(con: sqlite3.Connection, table: str) -> List[str]:
    try:
        return [r[1] for r in con.execute(f"PRAGMA table_info('{table}')")]
    except Exception:
        return []


def pick(row: sqlite3.Row, *names: str, default=None):
    """Return the first present, non-None field among names."""
    keys = row.keys() if hasattr(row, "keys") else []
    for n in names:
        if n in keys:
            v = row[n]
            if v is not None:
                return v
    return default


def fnum(v, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None or v == "":
            return default
        return float(v)
    except Exception:
        return default


# ---------------------------------------------------------------- doctrine

def aest_hour(epoch: Optional[float]) -> Optional[int]:
    if not epoch:
        return None
    try:
        return int(((float(epoch) / 3600.0) + AEST_OFFSET_HOURS) % 24)
    except Exception:
        return None


def integrity_bucket(status: Optional[str]) -> str:
    s = str(status or "").upper()
    if "CLEAN" in s:
        return "CLEAN"
    if "UNSTABLE" in s or "OUTLIER" in s:
        return "UNSTABLE"
    return "UNKNOWN"


def exit_category(exit_reason: Optional[str]) -> str:
    r = str(exit_reason or "").upper()
    for tag in ("TRAILING_STOP", "TRAIL", "RUNNER", "LILYPAD"):
        if tag in r:
            return "RUNNER_TRAIL"
    if "TAKE_PROFIT" in r:
        return "TAKE_PROFIT"
    if "HARD_STOP" in r:
        return "HARD_STOP"
    if "STOP_LOSS" in r:
        return "STOP_LOSS"
    if "MAX_HOLD" in r:
        return "MAX_HOLD"
    if "GUARDIAN" in r and "STALE" in r:
        return "GUARDIAN_STALE"
    if "STALE" in r:
        return "STALE"
    if "DEAD" in r:
        return "DEAD_TOKEN"
    if "UNSTABLE" in r or "OUTLIER" in r:
        return "UNSTABLE"
    if not r:
        return "UNKNOWN"
    return "OTHER"


def is_runner_shaped(exit_reason: Optional[str], pnl_usd: Optional[float]) -> bool:
    """Winner whose exit came from trailing/runner/lilypad machinery."""
    return (fnum(pnl_usd, 0.0) or 0.0) > 0 and exit_category(exit_reason) in (
        "RUNNER_TRAIL",
    )


def profit_factor(pnls: List[float]) -> Optional[float]:
    gains = sum(p for p in pnls if p > 0)
    losses = -sum(p for p in pnls if p < 0)
    if losses <= 0:
        return None if gains <= 0 else float("inf")
    return gains / losses


# ---------------------------------------------------------------- positions

# Column aliases seen across schema generations
PP_ALIASES = {
    "pnl":        ("realized_pnl_usd", "pnl_usd", "realized_pnl"),
    "pnl_pct":    ("pnl_pct", "final_exec_pct"),
    "opened_at":  ("opened_at",),
    "closed_at":  ("closed_at",),
    "exit_reason": ("exit_reason",),
    "integrity":  ("price_integrity_status",),
    "peak":       ("peak_pnl_pct", "max_pnl_pct", "peak_unrealized_pnl_pct"),
    "mae":        ("max_adverse_pct", "min_pnl_pct", "trough_pnl_pct"),
    "conf":       ("entry_confidence", "confidence"),
}


def load_closed_positions(con: sqlite3.Connection,
                          since_epoch: Optional[float] = None,
                          until_epoch: Optional[float] = None) -> List[Dict[str, Any]]:
    """Normalized closed-position rows from any schema generation."""
    if not table_exists(con, "paper_positions"):
        return []
    c = set(cols(con, "paper_positions"))
    where = ["UPPER(COALESCE(status,''))='CLOSED'"]
    args: List[Any] = []
    tcol = "closed_at" if "closed_at" in c else ("opened_at" if "opened_at" in c else None)
    if since_epoch and tcol:
        where.append(f"COALESCE({tcol},0) >= ?"); args.append(float(since_epoch))
    if until_epoch and tcol:
        where.append(f"COALESCE({tcol},0) <= ?"); args.append(float(until_epoch))
    rows = con.execute(
        f"SELECT * FROM paper_positions WHERE {' AND '.join(where)}", args
    ).fetchall()

    out: List[Dict[str, Any]] = []
    for r in rows:
        d: Dict[str, Any] = {
            "id":           pick(r, "id"),
            "mint_address": pick(r, "mint_address"),
            "token_name":   pick(r, "token_name"),
            "opened_at":    fnum(pick(r, *PP_ALIASES["opened_at"])),
            "closed_at":    fnum(pick(r, *PP_ALIASES["closed_at"])),
            "pnl_usd":      fnum(pick(r, *PP_ALIASES["pnl"]), 0.0),
            "pnl_pct":      fnum(pick(r, *PP_ALIASES["pnl_pct"])),
            "exit_reason":  pick(r, *PP_ALIASES["exit_reason"], default=""),
            "integrity":    integrity_bucket(pick(r, *PP_ALIASES["integrity"])),
            "peak_pnl_pct": fnum(pick(r, *PP_ALIASES["peak"])),
            "mae_pct":      fnum(pick(r, *PP_ALIASES["mae"])),
            "entry_confidence": fnum(pick(r, *PP_ALIASES["conf"])),
        }
        d["exit_category"] = exit_category(d["exit_reason"])
        oa, ca = d["opened_at"], d["closed_at"]
        d["hold_seconds"] = (ca - oa) if (oa and ca and ca >= oa) else None
        d["hour_aest"] = aest_hour(oa)
        d["is_monster"] = (d["pnl_usd"] or 0.0) >= MONSTER_WIN_USD
        d["is_bad_loss"] = (d["pnl_usd"] or 0.0) <= BAD_LOSS_USD
        d["is_win"] = (d["pnl_usd"] or 0.0) > 0
        d["runner_shaped"] = is_runner_shaped(d["exit_reason"], d["pnl_usd"])
        out.append(d)
    return out


def discover_archive_dbs(extra_dirs: Optional[List[Path]] = None) -> List[Path]:
    """Best-effort discovery of archive DBs that may hold pruned history."""
    hits: List[Path] = []
    dirs = [ROOT, ROOT / "archives", ROOT / "archive", ROOT / "db_archives",
            ROOT / "launch" / "db_prune_reports"] + (extra_dirs or [])
    seen = set()
    for d in dirs:
        try:
            if not d.exists():
                continue
            for p in d.rglob("*.db"):
                name = p.name.lower()
                if p in seen:
                    continue
                if "archive" in name or "history" in name or "backup" in name:
                    seen.add(p)
                    hits.append(p)
        except Exception:
            continue
    return hits
