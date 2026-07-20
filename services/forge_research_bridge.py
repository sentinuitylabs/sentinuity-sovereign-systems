"""
services/forge_research_bridge.py
===================================
Temporal Research Cache Writer — Phase: Buildout Cognition

Runs every 5 minutes. Reads cognition_log entries relevant to each
active forge project (by project_key keyword matching), summarises
them into the forge_research_cache table.

Without this service, IVARIS debates with zero evidence on every
FORGE proposal, always returns low confidence, and FORGE proposals
can never reach consensus.

Schema written:
    forge_research_cache(id, project_key, topic, summary, source,
                         confidence, created_at, expires_at)
"""
from __future__ import annotations

import sys
import time
import logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [forge_bridge] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("forge_research_bridge")

SERVICE_NAME  = "forge_research_bridge"
CYCLE_SECONDS = 300   # 5 minutes
ENTRY_TTL_SEC = 86400 # 24h — research stays fresh for a day
MAX_ENTRIES_PER_PROJECT = 20
MAX_SUMMARY_LEN = 500


def _safe_ts(val) -> float:
    if not val: return 0.0
    try:
        f = float(val)
        return f if f > 1_000_000_000 else 0.0
    except (TypeError, ValueError): pass
    try:
        import datetime as dt
        s = str(val).strip()
        for fmt in ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S",
                    "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S"):
            try: return dt.datetime.strptime(s, fmt).timestamp()
            except ValueError: continue
    except Exception: pass
    return 0.0


def _get_active_projects(conn) -> list[dict]:
    """Return active forge projects with their keywords."""
    rows = conn.execute("""
        SELECT project_key, current_stage, status
        FROM forge_projects
        WHERE status = 'active'
        ORDER BY created_at ASC
    """).fetchall()
    projects = []
    for r in rows:
        pk = str(r["project_key"] or "")
        # Build keyword list from project_key (snake_case → words)
        keywords = [w for w in pk.replace("_", " ").split() if len(w) > 3]
        keywords.append(pk)
        projects.append({
            "project_key": pk,
            "stage": str(r["current_stage"] or ""),
            "keywords": keywords,
        })
    return projects


def _pull_cognition_entries(conn, project: dict, since: float) -> list[dict]:
    """Pull cognition_log entries relevant to this project."""
    keywords = project["keywords"]
    if not keywords:
        return []

    # Build keyword WHERE clause
    kw_clauses = " OR ".join(["message LIKE ?" for _ in keywords])
    params = [f"%{kw}%" for kw in keywords]
    params.append(since)

    rows = conn.execute(f"""
        SELECT stage, message, confidence, timestamp
        FROM cognition_log
        WHERE ({kw_clauses})
          AND COALESCE(CAST(timestamp AS REAL), 0) > ?
        ORDER BY rowid DESC
        LIMIT 50
    """, params).fetchall()

    entries = []
    for r in rows:
        ts = _safe_ts(r["timestamp"])
        if ts == 0:
            # Try string parse
            ts = _safe_ts(str(r["timestamp"] or ""))
        entries.append({
            "stage": str(r["stage"] or ""),
            "message": str(r["message"] or ""),
            "confidence": float(r["confidence"] or 0.5),
            "ts": ts,
        })
    return entries


def _pull_proposals(conn, project_key: str, since: float) -> list[dict]:
    """Pull recent proposals for this project as evidence."""
    try:
        rows = conn.execute("""
            SELECT proposal_type, proposal_text, status, created_at
            FROM polaris_proposals
            WHERE project_key = ?
              AND COALESCE(CAST(created_at AS REAL), 0) > ?
            ORDER BY id DESC
            LIMIT 10
        """, (project_key, since)).fetchall()
        entries = []
        for r in rows:
            entries.append({
                "type": str(r["proposal_type"] or ""),
                "text": str(r["proposal_text"] or "")[:300],
                "status": str(r["status"] or ""),
            })
        return entries
    except Exception:
        return []


def _write_cache_entry(conn, project_key: str, topic: str,
                       summary: str, source: str, confidence: float) -> None:
    """Write one cache entry, avoiding exact duplicates."""
    now = time.time()
    # Check for near-duplicate (same project+topic in last 1h)
    existing = conn.execute("""
        SELECT id FROM forge_research_cache
        WHERE project_key = ? AND topic = ?
          AND created_at > ?
        LIMIT 1
    """, (project_key, topic, now - 3600)).fetchone()
    if existing:
        # Update instead of insert
        conn.execute("""
            UPDATE forge_research_cache
            SET summary = ?, confidence = ?, created_at = ?, expires_at = ?
            WHERE id = ?
        """, (summary[:MAX_SUMMARY_LEN], confidence, now,
              now + ENTRY_TTL_SEC, existing["id"]))
    else:
        conn.execute("""
            INSERT INTO forge_research_cache
                (project_key, topic, summary, source, confidence, created_at, expires_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (project_key, topic, summary[:MAX_SUMMARY_LEN],
              source, confidence, now, now + ENTRY_TTL_SEC))


def _run_bridge_cycle() -> dict:
    """Run one bridge cycle. Returns stats."""
    now = time.time()
    since = now - 3600  # look back 1 hour for cognition entries

    with get_connection() as conn:
        import sqlite3 as _sq
        conn.row_factory = _sq.Row

        # Expire old cache entries
        conn.execute("""
            DELETE FROM forge_research_cache
            WHERE expires_at IS NOT NULL AND expires_at < ?
        """, (now,))

        projects = _get_active_projects(conn)
        if not projects:
            total_cache = conn.execute(
                "SELECT COUNT(*) FROM forge_research_cache"
            ).fetchone()[0]
            return {"projects": 0, "entries_written": 0, "total_cache": total_cache}

        total_written = 0

        for project in projects:
            pk = project["project_key"]
            written = 0

            # 1. Cognition log entries
            cog_entries = _pull_cognition_entries(conn, project, since)
            if cog_entries:
                # Group by stage and summarise
                by_stage: dict[str, list] = {}
                for e in cog_entries:
                    by_stage.setdefault(e["stage"], []).append(e)

                for stage, entries in by_stage.items():
                    msgs = [e["message"][:150] for e in entries[:5]]
                    avg_conf = sum(e["confidence"] for e in entries) / len(entries)
                    summary = f"[{stage}] {len(entries)} events: " + " | ".join(msgs)
                    _write_cache_entry(
                        conn, pk,
                        topic=f"cognition_{stage.lower()}",
                        summary=summary,
                        source="cognition_log",
                        confidence=min(0.9, avg_conf + 0.1),
                    )
                    written += 1

            # 2. Proposal activity evidence
            proposals = _pull_proposals(conn, pk, since)
            if proposals:
                statuses = [p["status"] for p in proposals]
                approved = sum(1 for s in statuses if s in ("approved", "applied", "completed"))
                total_p  = len(proposals)
                summary = (f"{total_p} proposals: {approved} approved. "
                           f"Recent: {proposals[0]['text'][:200] if proposals else ''}")
                confidence = 0.7 + (0.2 * approved / max(total_p, 1))
                _write_cache_entry(
                    conn, pk,
                    topic="proposal_activity",
                    summary=summary,
                    source="polaris_proposals",
                    confidence=min(0.9, confidence),
                )
                written += 1

            # 3. Stage progression evidence
            stage_summary = (f"Project {pk} currently at stage {project['stage']}. "
                             f"Active and receiving proposals.")
            _write_cache_entry(
                conn, pk,
                topic="project_stage",
                summary=stage_summary,
                source="forge_projects",
                confidence=0.8,
            )
            written += 1

            total_written += written
            if written > 0:
                log.info("[BRIDGE] %s: wrote %d cache entries", pk, written)

        conn.commit()

        total_cache = conn.execute(
            "SELECT COUNT(*) FROM forge_research_cache"
        ).fetchone()[0]

    return {
        "projects":      len(projects),
        "entries_written": total_written,
        "total_cache":   total_cache,
    }


def run() -> None:
    log.info("Forge research bridge started — cycle=%ds", CYCLE_SECONDS)
    update_heartbeat(SERVICE_NAME, "starting", "forge_research_bridge online")

    while True:
        try:
            stats = _run_bridge_cycle()
            note = (f"projects={stats['projects']} "
                    f"written={stats['entries_written']} "
                    f"total_cache={stats['total_cache']}")
            log.info("[BRIDGE_CYCLE] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note,
                             work_processed=stats["entries_written"])
        except Exception as exc:
            log.warning("[BRIDGE_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
