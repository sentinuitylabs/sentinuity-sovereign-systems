# council_build_map.py — SIGNOFF_COUNCIL_BUILD_MAP_V2_20260621
# Read-only map for the autonomous build/council process.
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

BASE_DIR = Path(__file__).resolve().parent.parent
DB = BASE_DIR / "sentinuity_matrix.db"


def gc() -> sqlite3.Connection:
    c = sqlite3.connect(str(DB), timeout=8)
    c.row_factory = sqlite3.Row
    return c


def s(v: Any, d: str = "") -> str:
    try:
        return str(v if v is not None else d)
    except Exception:
        return d


def ago(ts: Any) -> str:
    if not ts:
        return "?"
    try:
        secs = time.time() - float(ts)
        if secs < 0:
            secs = 0
        if secs < 60:
            return f"{secs:.0f}s"
        if secs < 3600:
            return f"{secs/60:.0f}m"
        return f"{secs/3600:.1f}h"
    except Exception:
        return "?"


def _table_exists(c: sqlite3.Connection, table: str) -> bool:
    return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=? LIMIT 1", (table,)).fetchone() is not None


def _fetch(c: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    try:
        return [dict(r) for r in c.execute(sql, params).fetchall()]
    except Exception:
        return []


def _status_label(status: str, blocker: str = "") -> tuple[str, str, bool]:
    st = s(status, "").upper()
    block = s(blocker, "")
    if "BLOCK" in st or block:
        return "🔴", "BLOCKED", True
    if "NEEDS" in st or "APPROVAL" in st or "PAUSED" in st:
        return "🟠", "NEEDS YOU", True
    if st in {"DONE", "VERIFIED", "APPLIED", "COMPLETE"}:
        return "✅", "DONE", False
    if st in {"APPLYING", "VERIFYING", "RESEARCHING", "DEBATING", "PROPOSING", "GOLDEN_GATE"}:
        return "🔵", st, False
    return "🟡", st or "OPEN", False


def get_build_map() -> dict[str, Any]:
    with gc() as c:
        now = time.time()
        queues: list[dict[str, Any]] = []
        standing: list[dict[str, Any]] = []
        approvals: list[dict[str, Any]] = []
        blockers: list[dict[str, Any]] = []

        if _table_exists(c, "council_work_queue"):
            rows = _fetch(c, """
                SELECT * FROM council_work_queue
                ORDER BY
                  CASE WHEN status='OPEN' THEN 0 ELSE 1 END,
                  priority ASC,
                  updated_at DESC
                LIMIT 50
            """)
            for r in rows:
                blocker = s(r.get("last_error") or r.get("blocker_reason") or "")
                emoji, label, needs = _status_label(s(r.get("phase") or r.get("status")), blocker)
                item = {
                    "id": r.get("id"),
                    "title": s(r.get("title"))[:140],
                    "status": s(r.get("status")),
                    "phase": s(r.get("phase")),
                    "priority": r.get("priority"),
                    "risk": s(r.get("risk_level")),
                    "agent": s(r.get("assigned_agent")),
                    "target_tab": s(r.get("target_tab")),
                    "blocker": blocker[:220],
                    "verifier": s(r.get("verifier_result"))[:220],
                    "patch_path": s(r.get("patch_path")),
                    "age": ago(r.get("updated_at") or r.get("created_at")),
                    "emoji": emoji,
                    "label": label,
                    "needs_you": needs,
                }
                queues.append(item)
                if needs:
                    blockers.append({"msg": f"{item['title']} — {item['blocker'] or item['label']}", "stage": item["label"], "ago": item["age"]})
                if "APPROVAL" in item["phase"].upper() or int(r.get("approval_required") or 0):
                    approvals.append(item)

        if _table_exists(c, "polaris_standing_tasks"):
            rows = _fetch(c, """
                SELECT * FROM polaris_standing_tasks
                ORDER BY
                  CASE WHEN status IN ('BLOCKED','PAUSED') THEN 0 ELSE 1 END,
                  priority ASC,
                  updated_at DESC
                LIMIT 50
            """)
            for r in rows:
                blocker = s(r.get("blocked_reason") or r.get("last_error") or "")
                emoji, label, needs = _status_label(s(r.get("status")), blocker)
                item = {
                    "id": r.get("id"),
                    "title": s(r.get("title"))[:140],
                    "domain": s(r.get("domain")),
                    "status": s(r.get("status")),
                    "stage": s(r.get("stage")),
                    "progress": r.get("progress_pct"),
                    "priority": r.get("priority"),
                    "risk": s(r.get("risk_level")),
                    "owner": s(r.get("current_owner")),
                    "model": s(r.get("assigned_model")),
                    "blocker": blocker[:220],
                    "next_action": s(r.get("next_action"))[:220],
                    "vote_state": s(r.get("vote_state")),
                    "golden_gate": s(r.get("golden_gate_state")),
                    "age": ago(r.get("updated_at") or r.get("created_at")),
                    "emoji": emoji,
                    "label": label,
                    "needs_you": needs,
                }
                standing.append(item)
                if needs:
                    blockers.append({"msg": f"{item['title']} — {item['blocker'] or item['next_action']}", "stage": item["label"], "ago": item["age"]})

        proposals = []
        if _table_exists(c, "polaris_proposals"):
            proposals = _fetch(c, """
                SELECT id, proposal_type, proposal_text, suggested_action, confidence, status,
                       created_at, last_seen_at, stage, proposal_domain, target_files_json
                FROM polaris_proposals
                ORDER BY COALESCE(last_seen_at, created_at, 0) DESC
                LIMIT 40
            """)

        journal = []
        if _table_exists(c, "patch_apply_journal"):
            journal = _fetch(c, """
                SELECT id, created_at, task_id, file_path, risk_level, apply_result,
                       postcheck_result, rollback_result, final_status, outcome, detail
                FROM patch_apply_journal
                ORDER BY COALESCE(created_at, ts, id) DESC
                LIMIT 25
            """)

        api_status = {}
        try:
            for api in ["openai", "anthropic", "brave", "xai", "nim", "x_scout"]:
                row = c.execute("SELECT value FROM system_config WHERE key=?", (f"API_STATUS_{api.upper()}",)).fetchone()
                api_status[api] = s(row["value"] if row else "unknown")
        except Exception:
            pass

        phases: dict[str, dict[str, Any]] = {}

        def _add_phase(key: str, label: str, item: dict[str, Any]) -> None:
            ph = phases.setdefault(key, {"label": label, "proposals": [], "open": 0, "done": 0, "blocked": 0})
            ph["proposals"].append(item)
            lab = item.get("label", "").upper()
            if "DONE" in lab or "APPLIED" in lab or "VERIFIED" in lab:
                ph["done"] += 1
            elif "BLOCK" in lab or item.get("needs_you"):
                ph["blocked"] += 1
            else:
                ph["open"] += 1

        for item in queues[:20]:
            _add_phase("WORK_QUEUE", "🧭 COUNCIL WORK QUEUE", item)
        for item in standing[:20]:
            _add_phase("STANDING_TASKS", "📌 STANDING TASKS", item)
        for p in proposals[:20]:
            emoji, label, needs = _status_label(s(p.get("status")), "")
            _add_phase("PROPOSALS", "🧠 PROPOSALS", {
                "id": p.get("id"),
                "text": s(p.get("proposal_text"))[:120],
                "status": s(p.get("status")),
                "status_emoji": emoji,
                "status_label": label,
                "needs_hitl": needs,
                "age": ago(p.get("last_seen_at") or p.get("created_at")),
                "notes": s(p.get("suggested_action"))[:120],
                "rounds": 0,
                "agent": "POLARIS",
                "label": label,
                "needs_you": needs,
            })

        return {
            "summary": {
                "queue_open": sum(1 for x in queues if s(x.get("status")).upper() == "OPEN"),
                "standing_blocked": sum(1 for x in standing if x.get("needs_you")),
                "approvals": len(approvals),
                "patch_journal_rows": len(journal),
                "blockers": len(blockers),
            },
            "queues": queues,
            "standing": standing,
            "approvals": approvals,
            "journal": journal,
            "phases": phases,
            "api_status": api_status,
            "blockers": blockers[:20],
            "insights": [],
            "x_scout": {"posts": 0, "last_scan": "?", "latest_tag": "—"},
            "brave": {"searches": 0, "last_search": "?"},
            "total_proposals": len(proposals),
            "ts": now,
        }


def write_blocker(message: str, stage: str = "BUILD_BLOCKER") -> None:
    try:
        with gc() as c:
            c.execute("INSERT INTO cognition_log (stage, message, ts) VALUES (?, ?, ?)",
                      (stage, message[:500], time.time()))
            c.commit()
    except Exception as e:
        print(f"write_blocker failed: {e}")


def write_progress(phase: str, message: str, proposal_id: int | None = None) -> None:
    try:
        with gc() as c:
            full_msg = f"[{phase}] {message}"
            c.execute("INSERT INTO cognition_log (stage, message, ts) VALUES ('BUILD_PROGRESS', ?, ?)",
                      (full_msg[:500], time.time()))
            if proposal_id:
                c.execute("UPDATE polaris_proposals SET notes=? WHERE id=?",
                          (message[:500], proposal_id))
            c.commit()
    except Exception as e:
        print(f"write_progress failed: {e}")


if __name__ == "__main__":
    data = get_build_map()
    print(f"\\n{'='*70}")
    print(f"  ⬡ SENTINUITY COUNCIL BUILD MAP V2 — {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"{'='*70}")
    print("SUMMARY:", json.dumps(data.get("summary", {}), indent=2))
    if data.get("blockers"):
        print("\\nNEEDS YOU / BLOCKERS")
        for b in data["blockers"][:10]:
            print(f" - [{b['stage']}] {b['ago']} {b['msg']}")
    print("\\nOPEN/RECENT TASKS")
    for item in (data.get("queues", []) + data.get("standing", []))[:20]:
        print(f" {item.get('emoji','?')} #{item.get('id')} {item.get('label')} p={item.get('priority')} {item.get('title')} :: {item.get('blocker') or item.get('next_action') or item.get('phase')}")
