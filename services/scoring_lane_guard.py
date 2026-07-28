"""
Production scoring-lane guard helpers for Sentinuity.

This module is deliberately conservative:
- opens no trades
- resets no wallet state
- lowers no thresholds
- disables no cleaners
- protects only fresh unscored rows during a scoring grace window
"""
from __future__ import annotations

import time
from typing import Any, Dict, Optional

DEFAULT_SCORING_GRACE_SECONDS = 900
FRESH_STATES = {"pending", "mtm", "priced", "scoring", "retry", ""}
TERMINAL_QUALITY = {"qualified", "rejected", "error"}

SCHEMA_COLUMNS = [
    ("scoring_attempt_count", "INTEGER DEFAULT 0"),
    ("scoring_last_attempt_at", "REAL"),
    ("scoring_status", "TEXT"),
    ("scoring_error", "TEXT"),
    ("enrichment_source", "TEXT"),
    ("raw_confidence", "REAL"),
    ("calibrated_confidence", "REAL"),
]

def _safe_float(v: Any, default: Optional[float] = None) -> Optional[float]:
    try:
        if v is None or v == "":
            return default
        x = float(v)
        if x > 10_000_000_000:
            x /= 1000.0
        return x
    except Exception:
        return default

def _intish(v: Any) -> int:
    try:
        return int(float(v or 0))
    except Exception:
        return 0

def row_timestamp(row: Dict[str, Any]) -> float:
    vals = []
    for key in ("updated_at", "created_at", "first_seen_at", "price_updated_at", "timestamp"):
        x = _safe_float(row.get(key))
        if x and x > 1_000_000_000:
            vals.append(x)
    return max(vals) if vals else 0.0

def row_age_seconds(row: Dict[str, Any], now: Optional[float] = None) -> Optional[float]:
    ts = row_timestamp(row)
    if not ts:
        return None
    return (now or time.time()) - ts

def is_fresh_unscored(row: Dict[str, Any], grace_seconds: int = DEFAULT_SCORING_GRACE_SECONDS, now: Optional[float] = None) -> bool:
    """True only for fresh, unscored rows that deserve protection before cleanup."""
    now = now or time.time()
    age = row_age_seconds(row, now)
    if age is None or age < 0 or age > grace_seconds:
        return False
    q_status = str(row.get("quality_status") or "").strip().lower()
    q_reason = str(row.get("quality_reason") or "").strip().upper()
    c_state = str(row.get("candidate_state") or "").strip().lower()
    p_status = str(row.get("price_status") or "").strip().lower()
    if q_status in TERMINAL_QUALITY:
        return False
    if _intish(row.get("qualified")) == 1 or _intish(row.get("execution_ready")) == 1:
        return False
    if row.get("confidence") is not None or row.get("confidence_score") is not None:
        return False
    if c_state not in FRESH_STATES and p_status not in FRESH_STATES:
        return False
    terminal_tokens = ("BELOW_", "NOT_PUMP", "CURVE_COMPLETE", "SIGNAL_STALE", "BLACKLIST", "DANGER")
    if q_reason and any(x in q_reason for x in terminal_tokens):
        return False
    return True

def ensure_scoring_schema(conn) -> None:
    existing = {r[1] for r in conn.execute("PRAGMA table_info(market_snapshots)").fetchall()}
    for col, ddl in SCHEMA_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE market_snapshots ADD COLUMN {col} {ddl}")
    conn.commit()

def scoring_grace_seconds(get_config_value=None) -> int:
    if get_config_value:
        try:
            return max(DEFAULT_SCORING_GRACE_SECONDS, int(get_config_value("SCORING_GRACE_SECONDS", DEFAULT_SCORING_GRACE_SECONDS) or DEFAULT_SCORING_GRACE_SECONDS))
        except Exception:
            pass
    return DEFAULT_SCORING_GRACE_SECONDS

def audit_counts(conn):
    now = time.time()
    row = conn.execute(
        """
        SELECT
          (SELECT COUNT(*) FROM raw_dna WHERE timestamp >= ? - 300) AS raw_5m,
          (SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(created_at, updated_at, first_seen_at, price_updated_at, 0) >= ? - 600) AS snapshots_10m,
          (SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(created_at, updated_at, first_seen_at, price_updated_at, 0) >= ? - 600 AND COALESCE(confidence, confidence_score) IS NOT NULL) AS scored_10m,
          (SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(created_at, updated_at, first_seen_at, price_updated_at, 0) >= ? - 600 AND COALESCE(qualified,0)=1) AS qualified_10m,
          (SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(created_at, updated_at, first_seen_at, price_updated_at, 0) >= ? - 600 AND COALESCE(execution_ready,0) IN (1,2)) AS exec_ready_10m,
          (SELECT COUNT(*) FROM market_snapshots WHERE COALESCE(created_at, updated_at, first_seen_at, price_updated_at, 0) >= ? - 900 AND COALESCE(confidence, confidence_score) IS NULL AND COALESCE(qualified,0)=0) AS pending_unscored_fresh
        """,
        (now, now, now, now, now, now),
    ).fetchone()
    keys = ["raw_5m", "snapshots_10m", "scored_10m", "qualified_10m", "exec_ready_10m", "pending_unscored_fresh"]
    return {k: int(row[i] or 0) for i, k in enumerate(keys)}

# --- SENTINUITY COMPATIBILITY: normalize_scoring_lifecycle DB cleanup ---
def normalize_scoring_lifecycle(conn_or_value=None, min_confidence=0.75, grace_seconds=900, *args, **kwargs):
    """
    Dual-purpose function:
    1. If first arg is sqlite3.Connection: DB cleanup (releases expired claims, fills observability)
    2. If first arg is string/dict/None: string normalizer (legacy compatibility)
    
    DB cleanup mode (when conn_or_value is a Connection):
        - Releases expired qualify_claimed_until locks
        - Fills missing confidence scores for observability
        - Returns dict stats: {"released_claims": n, "filled_confidence": n}
    
    String normalizer mode (legacy):
        - Accepts dict/object/string/None
        - Returns canonical lifecycle string
    """
    import sqlite3
    import time
    
    # BRANCH 1: DB cleanup mode (first arg is Connection)
    if isinstance(conn_or_value, sqlite3.Connection):
        conn = conn_or_value
        now = time.time()
        stats = {"released_claims": 0, "filled_confidence": 0}
        
        try:
            # Release expired qualify_claimed_until locks
            released = conn.execute("""
                UPDATE market_snapshots
                SET qualify_claimed_until = NULL
                WHERE qualify_claimed_until IS NOT NULL
                  AND qualify_claimed_until < ?
            """, (now,)).rowcount
            stats["released_claims"] = released
            
            # Fill missing confidence scores for observability (if column exists)
            try:
                filled = conn.execute("""
                    UPDATE market_snapshots
                    SET resolver_confidence = ?
                    WHERE resolver_confidence IS NULL
                      AND quality_status = 'qualified'
                      AND COALESCE(created_at, first_seen_at, updated_at, 0) >= ?
                """, (min_confidence, now - grace_seconds)).rowcount
                stats["filled_confidence"] = filled
            except sqlite3.OperationalError:
                pass  # resolver_confidence column may not exist
            
            conn.commit()
            return stats
            
        except Exception as e:
            return {"error": str(e)}
    
    # BRANCH 2: String normalizer mode (legacy compatibility)
    value = conn_or_value
    try:
        if isinstance(value, dict):
            for key in ("scoring_lifecycle", "lifecycle", "status", "stage", "state"):
                if value.get(key) is not None:
                    value = value.get(key)
                    break
            else:
                value = kwargs.get("default", "candidate")
        elif value is None:
            value = kwargs.get("default", "candidate")

        text = str(value).strip().lower().replace("-", "_").replace(" ", "_")

        aliases = {
            "new": "candidate",
            "raw": "candidate",
            "queued": "candidate",
            "pending": "candidate",
            "candidate": "candidate",
            "scored": "scored",
            "score": "scored",
            "qualified": "qualified",
            "execution_ready": "execution_ready",
            "ready": "execution_ready",
            "latched": "latched",
            "executed": "executed",
            "filled": "executed",
            "rejected": "rejected",
            "vetoed": "vetoed",
            "expired": "expired",
            "stale": "expired",
        }

        return aliases.get(text, text or "candidate")
    except Exception:
        return kwargs.get("default", "candidate")
# --- END SENTINUITY COMPATIBILITY SHIM ---

