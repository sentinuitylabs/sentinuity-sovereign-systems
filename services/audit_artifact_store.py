"""Canonical audit/report persistence for Sentinuity.

Doctrine
--------
* ``audits/`` is the sole durable home for human-readable audit and task reports.
* Task state remains in SQLite; the DB stores searchable metadata and the exact file path.
* ``diagnostics/ai_handoff/`` contains only generated manifests/bundles, never a second
  canonical copy of a report.
* Secrets and long identifiers are sanitised before anything is written.

This module is presentation/evidence infrastructure only. It cannot change trading
configuration, approve patches, or write source files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Optional

ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = ROOT / "audits"
HANDOFF_ROOT = ROOT / "diagnostics" / "ai_handoff"

_SECRET_LINE = re.compile(
    r"(?i)(api[_-]?key|private[_-]?key|secret|password|passwd|bearer|seed[_-]?phrase|mnemonic|auth[_-]?token)\s*[:=]"
)
_URL = re.compile(r"https?://\S+", re.I)
_LONG_HEX = re.compile(r"\b(?:0x)?[0-9a-fA-F]{32,}\b")
_BASE58 = re.compile(r"\b[1-9A-HJ-NP-Za-km-z]{32,60}\b")
_SAFE_NAME = re.compile(r"[^a-zA-Z0-9._-]+")


def _safe_name(value: str, fallback: str = "report") -> str:
    clean = _SAFE_NAME.sub("-", (value or "").strip()).strip("-._").lower()
    return (clean[:80] or fallback)


def sanitize_text(value: Any, limit: int = 120_000) -> str:
    text = str(value if value is not None else "")
    lines: list[str] = []
    for raw in text.splitlines():
        if _SECRET_LINE.search(raw):
            lines.append("[sensitive configuration line removed]")
            continue
        line = _URL.sub("[url removed]", raw)
        line = _LONG_HEX.sub("[long-id masked]", line)
        line = _BASE58.sub("[wallet-or-mint masked]", line)
        lines.append(line)
    return "\n".join(lines)[:limit]


def sanitize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {sanitize_text(k, 120): sanitize_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [sanitize_value(v) for v in value]
    if isinstance(value, str):
        return sanitize_text(value)
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return sanitize_text(value)


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS audit_artifacts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at REAL NOT NULL,
            source TEXT NOT NULL,
            report_type TEXT NOT NULL,
            title TEXT NOT NULL,
            task_id INTEGER,
            task_name TEXT,
            status TEXT,
            canonical_path TEXT NOT NULL,
            json_path TEXT,
            sha256 TEXT NOT NULL,
            summary TEXT,
            tags_json TEXT DEFAULT '[]',
            metadata_json TEXT DEFAULT '{}'
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_artifacts_created ON audit_artifacts(created_at DESC)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_artifacts_task ON audit_artifacts(task_id, created_at DESC)")


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8", newline="\n")
    os.replace(tmp, path)


def _markdown(title: str, source: str, report_type: str, status: str,
              summary: str, evidence: Any, metadata: dict[str, Any]) -> str:
    now = datetime.now(timezone.utc).isoformat()
    body = [
        f"# {sanitize_text(title, 300)}",
        "",
        f"- Created (UTC): `{now}`",
        f"- Source: `{sanitize_text(source, 120)}`",
        f"- Type: `{sanitize_text(report_type, 120)}`",
        f"- Status: `{sanitize_text(status, 120)}`",
        "",
        "## Summary",
        "",
        sanitize_text(summary, 20_000) or "No summary supplied.",
        "",
        "## Evidence",
        "",
        "```json",
        json.dumps(sanitize_value(evidence), indent=2, ensure_ascii=False, default=str),
        "```",
    ]
    if metadata:
        body += ["", "## Metadata", "", "```json",
                 json.dumps(sanitize_value(metadata), indent=2, ensure_ascii=False, default=str), "```"]
    body += ["", "---", "Canonical report: `audits/`. AI handoff manifests reference this file; they do not duplicate it.", ""]
    return "\n".join(body)


def persist_report(
    conn: sqlite3.Connection,
    *,
    source: str,
    report_type: str,
    title: str,
    summary: str,
    evidence: Any = None,
    status: str = "INFO",
    task_id: Optional[int] = None,
    task_name: Optional[str] = None,
    tags: Optional[Iterable[str]] = None,
    metadata: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Persist one canonical report and register it for Council/Polaris retrieval."""
    ensure_schema(conn)
    created = time.time()
    stamp = datetime.fromtimestamp(created, timezone.utc)
    day_dir = AUDIT_ROOT / stamp.strftime("%Y") / stamp.strftime("%m") / stamp.strftime("%d") / _safe_name(source, "system")
    base = f"{stamp.strftime('%Y%m%dT%H%M%SZ')}_{_safe_name(task_name or title)}"
    md_path = day_dir / f"{base}.md"
    json_path = day_dir / f"{base}.json"

    clean_meta = sanitize_value(metadata or {})
    clean_evidence = sanitize_value(evidence or {})
    md = _markdown(title, source, report_type, status, summary, clean_evidence, clean_meta)
    payload = {
        "created_at": created,
        "created_at_utc": stamp.isoformat(),
        "source": sanitize_text(source, 120),
        "report_type": sanitize_text(report_type, 120),
        "title": sanitize_text(title, 300),
        "task_id": task_id,
        "task_name": sanitize_text(task_name or "", 300),
        "status": sanitize_text(status, 120),
        "summary": sanitize_text(summary, 20_000),
        "evidence": clean_evidence,
        "metadata": clean_meta,
        "tags": [sanitize_text(t, 80) for t in (tags or [])],
        "canonical_path": str(md_path.relative_to(ROOT)).replace("\\", "/"),
    }
    js = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    _write_atomic(md_path, md)
    _write_atomic(json_path, js)
    digest = hashlib.sha256(md.encode("utf-8")).hexdigest()

    cur = conn.execute(
        """
        INSERT INTO audit_artifacts(
            created_at,source,report_type,title,task_id,task_name,status,
            canonical_path,json_path,sha256,summary,tags_json,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (created, payload["source"], payload["report_type"], payload["title"], task_id,
         payload["task_name"], payload["status"], payload["canonical_path"],
         str(json_path.relative_to(ROOT)).replace("\\", "/"), digest,
         payload["summary"][:2000], json.dumps(payload["tags"]), json.dumps(clean_meta, default=str)),
    )
    artifact_id = int(cur.lastrowid)

    # The handoff directory stores only a compact index/pointer to canonical reports.
    recent = recent_reports(conn, limit=25)
    manifest = {
        "generated_at": time.time(),
        "doctrine": "Canonical reports live under audits/. This manifest contains references only.",
        "reports": recent,
    }
    _write_atomic(HANDOFF_ROOT / "latest_manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
    return {"id": artifact_id, "canonical_path": payload["canonical_path"], "json_path": str(json_path.relative_to(ROOT)).replace("\\", "/"), "sha256": digest}


def recent_reports(conn: sqlite3.Connection, limit: int = 20) -> list[dict[str, Any]]:
    ensure_schema(conn)
    rows = conn.execute(
        """SELECT id,created_at,source,report_type,title,task_id,task_name,status,
                  canonical_path,sha256,summary
           FROM audit_artifacts ORDER BY created_at DESC LIMIT ?""",
        (max(1, min(int(limit), 100)),),
    ).fetchall()
    names = [d[0] for d in conn.execute("PRAGMA table_info(audit_artifacts)").fetchall()]
    out = []
    for row in rows:
        if isinstance(row, sqlite3.Row):
            out.append(dict(row))
        else:
            out.append(dict(zip(names, row)))
    return out


def build_handoff_payload(conn: sqlite3.Connection, limit: int = 15) -> dict[str, Any]:
    return {
        "canonical_audit_root": "audits/",
        "handoff_manifest": "diagnostics/ai_handoff/latest_manifest.json",
        "storage_doctrine": "SQLite holds task state and searchable metadata; audits/ holds durable reports; diagnostics/ai_handoff holds generated references/bundles only.",
        "recent_reports": recent_reports(conn, limit=limit),
    }
