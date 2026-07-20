"""
provider_firewall.py
====================
EXTERNAL PROVIDER FIREWALL — Sentinuity Sovereign Forge

Central control for all external API calls.
No service may call external APIs directly.
All calls route through this module.

Doctrine:
  Governor debates.
  Scouts gather.
  Caches store.
  Forge builds.

Usage:
  from services.provider_firewall import check_provider, log_api_call, get_cached_evidence

"""
from __future__ import annotations
import sqlite3, time, logging, os
from pathlib import Path
from typing import Optional

log = logging.getLogger("provider_firewall")

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH  = BASE_DIR / "sentinuity_matrix.db"

# ── BUDGET LIMITS ─────────────────────────────────────────────────────────────
LIMITS = {
    "x":    {"24h": 12,   "30d": 350,  "cooldown_402h": 24},
    "brave":{"24h": 72,   "30d": 1000, "cooldown_402h": 1},
    "gmgn": {"24h": 100,  "30d": 2000, "cooldown_402h": 1},
}


def _get_db():
    c = sqlite3.connect(str(DB_PATH), timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _get_config(key: str, default: str = "1") -> str:
    try:
        c = _get_db()
        r = c.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
        c.close()
        return r["value"] if r else default
    except Exception:
        return default


def check_provider(provider: str, caller: str = "unknown") -> tuple[bool, str]:
    """
    Check if a provider is available before making any API call.
    Returns (allowed: bool, reason: str).

    Call this BEFORE every external request. If False, skip the request.
    """
    provider = provider.lower()

    # Check global enabled flag
    enabled_key = f"{provider.upper()}_SCOUT_ENABLED" if provider == "x" else f"{provider.upper()}_ENABLED"
    if _get_config(enabled_key, "1") == "0":
        return False, f"{provider.upper()}_DISABLED"

    now = time.time()
    try:
        c = _get_db()
        row = c.execute(
            "SELECT * FROM provider_health WHERE provider=?", (provider,)
        ).fetchone()

        if row:
            # Check cooldown
            cooldown = float(row["cooldown_until"] or 0)
            if cooldown > now:
                remaining = int((cooldown - now) / 3600)
                return False, f"COOLDOWN_{remaining}h_remaining"

            # Check 24h limit
            limits = LIMITS.get(provider, {"24h": 50, "30d": 500})
            if int(row["requests_24h"] or 0) >= limits["24h"]:
                return False, f"24H_LIMIT_REACHED_{limits['24h']}"

            # Check 30d limit
            if int(row["requests_30d"] or 0) >= limits["30d"]:
                return False, f"30D_LIMIT_REACHED_{limits['30d']}"

            # Check status
            status = str(row["status"] or "")
            if status in ("CREDITS_DEPLETED", "BANNED", "INVALID_TOKEN"):
                return False, status

        c.close()
        return True, "OK"
    except Exception as e:
        log.warning("provider_firewall check error: %s", e)
        return True, "CHECK_ERROR_ALLOWING"  # fail open so debates don't stall


def log_api_call(
    provider: str,
    caller_service: str,
    endpoint: str,
    status_code: int,
    proposal_id: int = None,
    error_type: str = None,
) -> None:
    """
    Log every external API call and update provider health.
    Call this AFTER every request, success or failure.
    """
    provider = provider.lower()
    now = time.time()

    try:
        c = _get_db()

        # Log the call
        c.execute("""
            INSERT INTO api_usage_log
                (provider, caller_service, endpoint, status_code,
                 request_ts, proposal_id, error_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (provider, caller_service, endpoint, status_code,
               now, proposal_id, error_type))

        # Count recent calls
        calls_24h = c.execute("""
            SELECT COUNT(*) FROM api_usage_log
            WHERE provider=? AND request_ts > ?
        """, (provider, now - 86400)).fetchone()[0]

        calls_30d = c.execute("""
            SELECT COUNT(*) FROM api_usage_log
            WHERE provider=? AND request_ts > ?
        """, (provider, now - 2592000)).fetchone()[0]

        # Determine new status and cooldown
        status = "OK"
        cooldown_until = 0
        limits = LIMITS.get(provider, {"24h": 50, "30d": 500, "cooldown_402h": 24})

        if status_code == 402:
            status = "CREDITS_DEPLETED"
            cooldown_until = now + (limits["cooldown_402h"] * 3600)
            log.warning("FIREWALL: %s returned 402 CreditsDepleted — cooling down %dh",
                        provider, limits["cooldown_402h"])
        elif status_code == 401:
            status = "INVALID_TOKEN"
            cooldown_until = now + 3600
        elif status_code == 429:
            status = "RATE_LIMITED"
            cooldown_until = now + 900  # 15 min
        elif status_code >= 500:
            status = "SERVER_ERROR"
            cooldown_until = now + 300
        elif status_code == 200:
            status = "OK"

        # Upsert provider health
        c.execute("""
            INSERT INTO provider_health
                (provider, status, cooldown_until, requests_24h, requests_30d,
                 last_status_code, last_error, updated_at, enabled)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
            ON CONFLICT(provider) DO UPDATE SET
                status=excluded.status,
                cooldown_until=excluded.cooldown_until,
                requests_24h=excluded.requests_24h,
                requests_30d=excluded.requests_30d,
                last_status_code=excluded.last_status_code,
                last_error=excluded.last_error,
                updated_at=excluded.updated_at
        """, (provider, status, cooldown_until, calls_24h, calls_30d,
               status_code, error_type, now))

        c.commit()
        c.close()

    except Exception as e:
        log.warning("provider_firewall log error: %s", e)


def get_cached_evidence(project_key: str = None, topic: str = None,
                        max_age_hours: int = 48) -> list[dict]:
    """
    Read evidence from forge_research_cache and cognition_log.
    This is what sovereign_governor must call instead of external APIs.
    Returns list of {topic, summary, confidence, source, age_hours}.
    """
    results = []
    now = time.time()
    cutoff = now - (max_age_hours * 3600)

    try:
        c = _get_db()

        # From forge_research_cache
        query = "SELECT * FROM forge_research_cache WHERE created_at > ?"
        params = [cutoff]
        if project_key:
            query += " AND project_key=?"
            params.append(project_key)
        if topic:
            query += " AND (topic LIKE ? OR summary LIKE ?)"
            params.extend([f"%{topic}%", f"%{topic}%"])
        query += " ORDER BY confidence DESC, created_at DESC LIMIT 20"

        for row in c.execute(query, params).fetchall():
            age_h = (now - float(row["created_at"])) / 3600
            results.append({
                "topic":      row["topic"],
                "summary":    row["summary"],
                "confidence": float(row["confidence"] or 0.5),
                "source":     row["source"] or "research_cache",
                "age_hours":  round(age_h, 1),
            })

        # From cognition_log (scout-produced summaries)
        cog_rows = c.execute("""
            SELECT stage, token, message, confidence, timestamp
            FROM cognition_log
            WHERE timestamp > ?
              AND stage NOT IN ('X_SCOUT')
              AND (message LIKE '%research%' OR message LIKE '%evidence%'
                   OR message LIKE '%wallet%')
            ORDER BY timestamp DESC LIMIT 10
        """, (cutoff,)).fetchall()

        for row in cog_rows:
            age_h = (now - float(row["timestamp"])) / 3600
            results.append({
                "topic":      str(row["token"] or row["stage"]),
                "summary":    str(row["message"] or "")[:300],
                "confidence": float(row["confidence"] or 0.3),
                "source":     "cognition_log",
                "age_hours":  round(age_h, 1),
            })

        c.close()
    except Exception as e:
        log.warning("get_cached_evidence error: %s", e)

    return results


def get_provider_status_all() -> dict:
    """Return status of all providers — used by UI and build_audit."""
    result = {}
    try:
        c = _get_db()
        rows = c.execute("SELECT * FROM provider_health").fetchall()
        now = time.time()
        for row in rows:
            cooldown_remaining = max(0, float(row["cooldown_until"] or 0) - now)
            result[row["provider"]] = {
                "enabled":    bool(row["enabled"]),
                "status":     row["status"],
                "cooldown_h": round(cooldown_remaining / 3600, 1),
                "req_24h":    row["requests_24h"],
                "req_30d":    row["requests_30d"],
                "last_code":  row["last_status_code"],
                "last_error": row["last_error"],
            }
        c.close()
    except Exception as e:
        log.warning("get_provider_status_all error: %s", e)
    return result
