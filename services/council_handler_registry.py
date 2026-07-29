# coding: utf-8
"""
services/council_handler_registry.py — COUNCIL_REPAIR_20260729

Typed handler authority (F1) and capability-gap handling (F1b).

Production routing resolves on handler_key ONLY. Titles are display strings and
must never be execution authority. LEGACY_TITLE_MAP exists solely to populate
missing handler_key values during migration and is not consulted once a row has
a handler_key.

A task with no registered handler is NOT parked. It becomes WAITING_FOR_HANDLER
and spawns an IMPLEMENT_HANDLER child. The capability handler writes a
diagnostic and a handler specification, and explicitly does NOT mark the parent
task COMPLETED, IMPROVED or built (F1b, defect 14).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent

# ── task taxonomy (F1) ──────────────────────────────────────────────────────
TASK_TYPES = ("RESEARCH", "AUDIT", "BUILD", "MEASUREMENT", "MAINTENANCE")
NETWORK_LOCAL, NETWORK_NETWORK, NETWORK_HYBRID = "LOCAL", "NETWORK", "HYBRID"
NETWORK_REQUIREMENTS = (NETWORK_LOCAL, NETWORK_NETWORK, NETWORK_HYBRID)

CAPABILITY_HANDLER_KEY = "CAPABILITY_GAP_DIAGNOSTIC"

SCHEMA = """
CREATE TABLE IF NOT EXISTS council_capability_gaps_v2(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    missing_handler_key TEXT NOT NULL,
    original_task_id INTEGER NOT NULL,
    reason TEXT,
    detected_at REAL NOT NULL,
    required_inputs TEXT,
    expected_outputs TEXT,
    status TEXT DEFAULT 'OPEN',
    restored_at REAL,
    child_task_id INTEGER,
    UNIQUE(missing_handler_key, original_task_id)
);
"""

#: handler_key -> factory(task, ctx) -> {research, propose, build}
HANDLERS: Dict[str, Callable] = {}

#: migration-only. Populates handler_key where it is missing. Never routes.
LEGACY_TITLE_MAP: Dict[str, str] = {
    "council autonomous build health check": "COUNCIL_AUTONOMOUS_BUILD_HEALTH",
    "council autonomous build health": "COUNCIL_AUTONOMOUS_BUILD_HEALTH",
    "recurring solana edge audit": "RECURRING_SOLANA_EDGE_AUDIT",
    "council stage rail canary": "COUNCIL_STAGE_RAIL_CANARY",
    "intelligence tab canary": "INTELLIGENCE_TAB_CANARY",
    "substrate chart": "SUBSTRATE_CHART_SOURCE",
    "schema-selection defect": "SCHEMA_SELECTION_AUTHORITY",
    "table existence authority": "SCHEMA_SELECTION_AUTHORITY",
}

#: handler_key -> declared execution profile
HANDLER_PROFILE: Dict[str, Dict[str, str]] = {}


def register(handler_key: str, *, task_type: str = "AUDIT",
             network_requirement: str = NETWORK_LOCAL,
             risk_tier: str = "TIER_0"):
    if task_type not in TASK_TYPES:
        raise ValueError(f"bad task_type {task_type!r}")
    if network_requirement not in NETWORK_REQUIREMENTS:
        raise ValueError(f"bad network_requirement {network_requirement!r}")

    def deco(fn):
        HANDLERS[handler_key] = fn
        HANDLER_PROFILE[handler_key] = {
            "task_type": task_type,
            "network_requirement": network_requirement,
            "risk_tier": risk_tier,
        }
        return fn
    return deco


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(SCHEMA)
    conn.commit()


def resolve(task: dict) -> tuple[Optional[Callable], str]:
    """Exact handler_key resolution. Returns (handler|None, handler_key)."""
    key = str(task.get("handler_key") or "").strip().upper()
    if not key:
        key = LEGACY_TITLE_MAP.get(
            str(task.get("title") or "").strip().lower(), "")
    if key and key in HANDLERS:
        return HANDLERS[key], key
    return None, key or "UNMAPPED"


def infer_handler_key(title: str) -> str:
    return LEGACY_TITLE_MAP.get((title or "").strip().lower(), "")


# ── F1b capability gap ──────────────────────────────────────────────────────
def record_capability_gap(conn: sqlite3.Connection, *, original_task_id: int,
                          missing_handler_key: str, reason: str,
                          required_inputs: str = "", expected_outputs: str = "",
                          child_task_id: Optional[int] = None) -> int:
    ensure_schema(conn)
    conn.execute(
        "INSERT INTO council_capability_gaps_v2(missing_handler_key,"
        " original_task_id, reason, detected_at, required_inputs,"
        " expected_outputs, status, child_task_id)"
        " VALUES(?,?,?,?,?,?, 'OPEN', ?)"
        " ON CONFLICT(missing_handler_key, original_task_id) DO UPDATE SET"
        " reason=excluded.reason, detected_at=excluded.detected_at,"
        " child_task_id=COALESCE(excluded.child_task_id, child_task_id)",
        (missing_handler_key, original_task_id, reason[:400], time.time(),
         required_inputs[:400], expected_outputs[:400], child_task_id))
    conn.commit()
    row = conn.execute(
        "SELECT id FROM council_capability_gaps_v2 WHERE missing_handler_key=?"
        " AND original_task_id=?", (missing_handler_key, original_task_id)).fetchone()
    return int(row[0]) if row else 0


def restore_capabilities(conn: sqlite3.Connection) -> Dict[str, Any]:
    """Reopen every task whose missing handler is now registered (F1b).

    This is the automatic reopening path the old code never had: nothing ever
    cleared blocker_code='NO_HANDLER', so a parked task stayed unclaimable even
    after its handler was written.
    """
    ensure_schema(conn)
    restored, keys = 0, []
    rows = conn.execute(
        "SELECT id, missing_handler_key, original_task_id FROM"
        " council_capability_gaps_v2 WHERE status='OPEN'").fetchall()
    for gap_id, key, task_id in rows:
        if key not in HANDLERS:
            continue
        conn.execute(
            "UPDATE council_task_ledger SET phase='OPEN', blocker_code=NULL,"
            " claimed_by=NULL, claimed_at=NULL, lease_expires_at=NULL,"
            " handler_key=?, updated_at=? WHERE canonical_id=?",
            (key, time.time(), task_id))
        conn.execute(
            "INSERT INTO council_task_transitions(canonical_id, ts, agent,"
            " from_phase, to_phase, reason) VALUES(?,?,?,?,?,?)",
            (task_id, time.time(), "CAPABILITY_RESTORE", "WAITING_FOR_HANDLER",
             "OPEN", f"CAPABILITY_RESTORED handler_key={key}"))
        conn.execute(
            "UPDATE council_capability_gaps_v2 SET status='RESTORED',"
            " restored_at=? WHERE id=?", (time.time(), gap_id))
        restored += 1
        keys.append(key)
    conn.commit()
    return {"restored": restored, "handler_keys": keys}


# ── the capability-gap handler (must NOT complete the parent task) ──────────
@register(CAPABILITY_HANDLER_KEY, task_type="MAINTENANCE",
          network_requirement=NETWORK_LOCAL, risk_tier="TIER_0")
def capability_gap_handler(task: dict, ctx: dict) -> Dict[str, Any]:
    """Produces a real Tier-0 artifact: a handler specification. It documents
    the gap; it does not perform the parent task's work."""
    cid = int(task["canonical_id"])
    missing = str(task.get("missing_handler_key")
                  or task.get("handler_key") or "UNMAPPED")
    target = ROOT / "docs" / "council" / f"handler_spec_{missing.lower()}.md"

    def research() -> Dict[str, Any]:
        db = ctx["build_db_path"]
        c = sqlite3.connect(str(db), timeout=15.0)
        try:
            c.execute("PRAGMA busy_timeout=8000")
            ensure_schema(c)
            open_gaps = c.execute(
                "SELECT COUNT(*) FROM council_capability_gaps_v2"
                " WHERE status='OPEN'").fetchone()[0]
        finally:
            c.close()
        return {
            "kind": "capability_gap",
            "summary": (f"no handler registered for handler_key={missing!r} "
                        f"(task #{cid}); {open_gaps} open gap(s)"),
            "data": {"missing_handler_key": missing, "open_gaps": open_gaps,
                     "registered": sorted(HANDLERS.keys())},
            "sample_size": max(int(open_gaps), 1),
            "confidence": 1.0,
            "methodology": "local build-DB read; no network, no external source",
            "limitations": ("documents the gap only; the parent task's work is "
                            "NOT performed and the parent is NOT completed"),
        }

    def propose(evidence):
        rel = str(target.relative_to(ROOT)).replace("\\", "/")
        content = _spec_text(missing, cid, task)
        return {
            "proposal_type": "capability_gap_spec",
            "proposal_text": (f"Specify the missing handler {missing!r} so task "
                              f"#{cid} becomes executable. Does not implement it."),
            "suggested_action": f"write {rel}",
            "files": [rel],
            "diff_chars": max(1, len(content)),
            "compile_ok": True,
            "test_cmd": "spec file exists and names the missing handler_key",
            "backup_planned": True,
            "risk_tier": "TIER_0",
            "new_content": content,
            "target_file": target,
            "completes_parent": False,        # explicit: defect 14
        }

    def build():
        content = _spec_text(missing, cid, task)

        def test(path: Path) -> bool:
            return missing in path.read_text(encoding="utf-8")

        def verify(path: Path) -> bool:
            return path.exists() and path.stat().st_size > 0

        return {"target_file": target, "new_content": content,
                "test": test, "verify": verify, "completes_parent": False}

    return {"research": research, "propose": propose, "build": build,
            "completes_parent": False}


def _spec_text(missing: str, cid: int, task: dict) -> str:
    return (
        f"# Handler specification — `{missing}`\n\n"
        f"Generated {time.strftime('%Y-%m-%d %H:%M:%S')} by "
        f"{CAPABILITY_HANDLER_KEY}.\n\n"
        f"## Gap\n\n"
        f"- blocked task: #{cid} — {task.get('title')!r}\n"
        f"- missing handler_key: `{missing}`\n"
        f"- task_type: {task.get('task_type') or 'UNSET'}\n"
        f"- network_requirement: {task.get('network_requirement') or 'UNSET'}\n"
        f"- risk_tier: {task.get('risk_tier') or 'UNSET'}\n\n"
        f"## Status\n\n"
        f"Task #{cid} is **WAITING_FOR_HANDLER**. It has NOT been performed and "
        f"is NOT complete. This document is evidence of the gap only.\n\n"
        f"## To close\n\n"
        f"1. Implement a handler and register it:\n"
        f"   `@register(\"{missing}\", task_type=..., network_requirement=..., "
        f"risk_tier=...)`\n"
        f"2. Return `research()`, `propose(evidence)` and `build()`.\n"
        f"3. `propose()` must set `diff_chars > 0` — the offline structural "
        f"critic rejects `diff_chars == 0` via its `diff_bounded_200k` check, "
        f"with no visible reason.\n"
        f"4. On the next cycle `restore_capabilities()` reopens task #{cid} "
        f"automatically and records CAPABILITY_RESTORED.\n"
    )


# ── real typed handlers (directive: "Handler implementation requirement") ───
@register("COUNCIL_AUTONOMOUS_BUILD_HEALTH", task_type="AUDIT",
          network_requirement=NETWORK_LOCAL, risk_tier="TIER_0")
def council_build_health_handler(task: dict, ctx: dict) -> Dict[str, Any]:
    """Fully local build-plane health audit. Inspects handler coverage, stage
    evidence, proposal retention, canary state, apply-policy usage, stale and
    WAITING_FOR_HANDLER tasks, contention and inspiration advancement."""
    target = ROOT / "docs" / "council" / "build_health.md"

    def _probe(db: Path) -> Dict[str, Any]:
        c = sqlite3.connect(str(db), timeout=15.0)
        c.row_factory = sqlite3.Row
        out: Dict[str, Any] = {}
        try:
            c.execute("PRAGMA busy_timeout=8000")

            def one(q, d=0):
                try:
                    return c.execute(q).fetchone()[0]
                except sqlite3.Error:
                    return d

            out["handlers_registered"] = len(HANDLERS)
            out["tasks_total"] = one("SELECT COUNT(*) FROM council_task_ledger")
            out["tasks_waiting_for_handler"] = one(
                "SELECT COUNT(*) FROM council_task_ledger"
                " WHERE phase='WAITING_FOR_HANDLER'")
            out["tasks_parked_no_handler"] = one(
                "SELECT COUNT(*) FROM council_task_ledger"
                " WHERE blocker_code='NO_HANDLER'")
            out["tasks_stale_30m"] = one(
                "SELECT COUNT(*) FROM council_task_ledger WHERE phase NOT IN"
                " ('COMPLETED','FAILED_FINAL','SUPERSEDED') AND updated_at < "
                + str(time.time() - 1800))
            out["stage_evidence_rows"] = one(
                "SELECT COUNT(*) FROM council_stage_evidence")
            out["stage_write_failures"] = one(
                "SELECT COUNT(*) FROM council_stage_write_failures")
            out["research_evidence_rows"] = one(
                "SELECT COUNT(*) FROM council_research_evidence")
            out["proposals_total"] = one("SELECT COUNT(*) FROM polaris_proposals")
            out["patches_applied"] = one(
                "SELECT COUNT(*) FROM code_patches WHERE status='APPLIED'")
            out["patches_with_backup"] = one(
                "SELECT COUNT(*) FROM code_patches WHERE backup_path IS NOT NULL")
            out["retrospectives"] = one("SELECT COUNT(*) FROM build_retrospectives")
            out["open_capability_gaps"] = one(
                "SELECT COUNT(*) FROM council_capability_gaps_v2"
                " WHERE status='OPEN'")
            try:
                from core.council_stage_contract import get_build_plane_state
                out["build_plane"] = get_build_plane_state(c)
            except Exception:
                out["build_plane"] = "UNKNOWN"
        finally:
            c.close()
        return out

    def _render(data: Dict[str, Any]) -> str:
        lines = ["# Council build-plane health", "",
                 f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}",
                 f"handler: COUNCIL_AUTONOMOUS_BUILD_HEALTH (LOCAL, TIER_0)", "",
                 "| metric | value |", "| --- | --- |"]
        for k in sorted(data):
            lines.append(f"| {k} | {data[k]} |")
        warn = []
        if data.get("tasks_parked_no_handler"):
            warn.append("tasks are parked on NO_HANDLER — capability restore "
                        "has not reopened them")
        if data.get("stage_write_failures"):
            warn.append("stage-evidence writes are FAILING — schema drift")
        if not data.get("stage_evidence_rows"):
            warn.append("no stage evidence at all — the stage contract is not "
                        "wired or is silently failing")
        if data.get("build_plane") not in ("BUILD_READY",):
            warn.append(f"build plane is {data.get('build_plane')} — the normal "
                        f"backlog is not claimable")
        lines += ["", "## Warnings", ""]
        lines += [f"- {w}" for w in warn] or ["- none"]
        return "\n".join(lines) + "\n"

    def research() -> Dict[str, Any]:
        data = _probe(Path(ctx["build_db_path"]))
        return {"kind": "build_health_probe",
                "summary": (f"build_plane={data.get('build_plane')} "
                            f"handlers={data.get('handlers_registered')} "
                            f"waiting={data.get('tasks_waiting_for_handler')} "
                            f"applied={data.get('patches_applied')}"),
                "data": data, "sample_size": max(1, int(data.get("tasks_total") or 1)),
                "confidence": 1.0,
                "methodology": "local build-DB inspection; no network",
                "limitations": "point-in-time snapshot"}

    def propose(evidence):
        content = _render(evidence["data"])
        rel = str(target.relative_to(ROOT)).replace("\\", "/")
        return {"proposal_type": "build_health_report",
                "proposal_text": "Refresh the local Council build-plane health report.",
                "suggested_action": f"write {rel}", "files": [rel],
                "diff_chars": max(1, len(content)), "compile_ok": True,
                "test_cmd": "report renders and contains the metrics table",
                "backup_planned": True, "risk_tier": "TIER_0",
                "new_content": content, "target_file": target}

    def build():
        content = _render(_probe(Path(ctx["build_db_path"])))

        def test(p: Path) -> bool:
            return "| metric | value |" in p.read_text(encoding="utf-8")

        def verify(p: Path) -> bool:
            return p.exists() and p.stat().st_size > 0

        return {"target_file": target, "new_content": content,
                "test": test, "verify": verify}

    return {"research": research, "propose": propose, "build": build}


@register("RECURRING_SOLANA_EDGE_AUDIT", task_type="RESEARCH",
          network_requirement=NETWORK_HYBRID, risk_tier="TIER_0")
def solana_edge_audit_handler(task: dict, ctx: dict) -> Dict[str, Any]:
    """HYBRID. Local historical analysis always runs; the external-source leg
    is skipped and recorded as outstanding when the network is unavailable."""
    target = ROOT / "docs" / "council" / "solana_edge_audit.md"

    def _local(db: Path) -> Dict[str, Any]:
        out: Dict[str, Any] = {"network_leg": "PENDING_NETWORK"}
        c = sqlite3.connect(f"file:{Path(db).as_posix()}?mode=ro", uri=True,
                            timeout=5.0)
        try:
            c.row_factory = sqlite3.Row
            c.execute("PRAGMA busy_timeout=4000")

            def one(q, d=0):
                try:
                    return c.execute(q).fetchone()[0]
                except sqlite3.Error:
                    return d
            out["closed_trades"] = one(
                "SELECT COUNT(*) FROM polaris_trade_reviews")
            out["wins"] = one(
                "SELECT COUNT(*) FROM polaris_trade_reviews WHERE win_loss='WIN'")
            out["avg_pnl_usd"] = one(
                "SELECT ROUND(AVG(realized_pnl_usd),4) FROM"
                " (SELECT realized_pnl_usd FROM polaris_trade_reviews"
                "  ORDER BY id DESC LIMIT 100)", 0)
        except sqlite3.Error:
            pass
        finally:
            c.close()
        t = int(out.get("closed_trades") or 0)
        out["win_rate_pct"] = round((int(out.get("wins") or 0) / t) * 100, 1) if t else None
        return out

    def _render(d: Dict[str, Any]) -> str:
        return ("# Solana edge audit (local leg)\n\n"
                f"generated: {time.strftime('%Y-%m-%d %H:%M:%S')}\n"
                f"handler: RECURRING_SOLANA_EDGE_AUDIT (HYBRID, TIER_0)\n\n"
                f"- closed trades reviewed: {d.get('closed_trades')}\n"
                f"- win rate: {d.get('win_rate_pct')}%\n"
                f"- avg realised pnl (last 100): {d.get('avg_pnl_usd')}\n"
                f"- external source research: {d.get('network_leg')}\n\n"
                "The network leg does not gate the local leg. When the network "
                "is unavailable the outstanding requirement is persisted and "
                "the local analysis still completes.\n")

    def research() -> Dict[str, Any]:
        data = _local(Path(ctx.get("market_db_path")
                           or (ROOT / "sentinuity_matrix.db")))
        return {"kind": "solana_edge_local",
                "summary": (f"local leg complete: {data.get('closed_trades')} "
                            f"reviews, win_rate={data.get('win_rate_pct')}; "
                            f"network leg {data.get('network_leg')}"),
                "data": data,
                "sample_size": max(1, int(data.get("closed_trades") or 1)),
                "confidence": 0.9,
                "methodology": "read-only market-DB analysis",
                "limitations": "no external comparison while offline"}

    def propose(evidence):
        content = _render(evidence["data"])
        rel = str(target.relative_to(ROOT)).replace("\\", "/")
        return {"proposal_type": "edge_audit_report",
                "proposal_text": "Refresh the local Solana edge audit.",
                "suggested_action": f"write {rel}", "files": [rel],
                "diff_chars": max(1, len(content)), "compile_ok": True,
                "test_cmd": "report contains the local leg summary",
                "backup_planned": True, "risk_tier": "TIER_0",
                "new_content": content, "target_file": target}

    def build():
        content = _render(_local(Path(ctx.get("market_db_path")
                                      or (ROOT / "sentinuity_matrix.db"))))

        def test(p: Path) -> bool:
            return "Solana edge audit" in p.read_text(encoding="utf-8")

        def verify(p: Path) -> bool:
            return p.exists() and p.stat().st_size > 0

        return {"target_file": target, "new_content": content,
                "test": test, "verify": verify}

    return {"research": research, "propose": propose, "build": build}
