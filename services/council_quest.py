# coding: utf-8
"""
services/council_quest.py — COUNCIL_QUEST_READ_MODEL_20260813

Read-only Quest model for the routed UI.

A pressure reading can propose a quest, but it is not itself the active quest.
The active quest is a persisted row in council_quests, and its journey is a set
of explicit evidence-backed council_quest_events written by the low-priority
council_quest_bridge.

Rendering never creates schema or writes state.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, List, Optional

try:
    from core.schema import get_connection
except Exception:  # pragma: no cover
    get_connection = None  # type: ignore

try:
    from services.organism_pressure import snapshot
except Exception:  # pragma: no cover
    snapshot = lambda *a, **k: {"faculties": [], "top_pressure": None}

_LIMIT = 120
_WINDOW_S = 86400.0

JOURNEY = [
    ("CLUE",        "Following the trail"),
    ("PROPOSITION", "Stating what we think is true"),
    ("CHALLENGE",   "Trying to break it"),
    ("TEST",        "Proving or killing it"),
    ("FORGE",       "Turning it into a change"),
    ("POLARIS",     "Deciding"),
    ("FILE_CHANGE", "The organism changes"),
    ("REFLECTION",  "Did it actually help?"),
]
STAGE_INDEX = {k: i for i, (k, _) in enumerate(JOURNEY)}

ROLE_MANDATE = {
    "NUGGET":    {"verb": "discovers",  "emoji": "🌲", "sides": 3},
    "AXON":      {"verb": "traces",     "emoji": "🧭", "sides": 4},
    "MECHANIST": {"verb": "inspects",   "emoji": "🔎", "sides": 4},
    "RHIZA":     {"verb": "remembers",  "emoji": "🧠", "sides": 5},
    "IVARIS":    {"verb": "challenges", "emoji": "🛡", "sides": 6},
    "SUBSTRATE": {"verb": "tests",      "emoji": "🧪", "sides": 0},
    "FORGE":     {"verb": "builds",     "emoji": "🔨", "sides": 4},
    "POLARIS":   {"verb": "decides",    "emoji": "⭐", "sides": 8},
}

HABITAT_LABEL = {
    "RUNTIME": "our runtime", "CODE": "our code", "HISTORY": "our history",
    "ONCHAIN": "on-chain", "GITHUB_EXPEDITION": "the outside forest",
    "GITHUB": "the outside forest", "RESEARCH": "research",
    "SOLANA": "the Solana pipeline", "PRICE_TRUTH": "price truth",
    "COPYTRADE": "copytrade", "SUBSTRATE": "the proving ground",
    "INTELLIGENCE": "intelligence", "COUNCIL": "Council Camp", "FORGE": "Forge",
}

STAGE_REACHED, STAGE_ACTIVE, STAGE_FOGGED = "REACHED", "ACTIVE", "FOGGED"
STAGE_CLOSED = "CLOSED"


def _conn() -> Optional[sqlite3.Connection]:
    if get_connection is None:
        return None
    try:
        c = get_connection()
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA busy_timeout=1500")
        return c
    except Exception:
        return None


def _has(c: sqlite3.Connection, table: str) -> bool:
    try:
        return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None
    except Exception:
        return False


def _loads(v: Any, default: Any) -> Any:
    try:
        x = json.loads(str(v or ""))
        return x
    except Exception:
        return default


def _events(c: sqlite3.Connection, quest_id: int) -> List[Dict[str, Any]]:
    if not _has(c, "council_quest_events"):
        return []
    try:
        rr = c.execute(
            """SELECT id,stage,actor_role,event_type,summary,next_action,
                      source_table,source_row_id,evidence_json,created_at
               FROM council_quest_events WHERE quest_id=?
               ORDER BY created_at ASC,id ASC LIMIT ?""",
            (int(quest_id), _LIMIT),
        ).fetchall()
    except Exception:
        return []

    out: List[Dict[str, Any]] = []
    for r in rr:
        role = str(r["actor_role"] or "MECHANIST").upper()
        mand = ROLE_MANDATE.get(role, ROLE_MANDATE["MECHANIST"])
        summary = str(r["summary"] or "")
        stance = "SUPPORTS"
        et = str(r["event_type"] or "").upper()
        if "CHALLENGE" in et or "REJECT" in et or role == "IVARIS":
            stance = "DOUBTS"
        out.append({
            "id": int(r["id"]), "role": role, "verb": mand["verb"],
            "emoji": mand["emoji"], "sides": mand["sides"],
            "stage": str(r["stage"] or "CLUE"), "stance": stance,
            "body": summary, "next_action": str(r["next_action"] or ""),
            "evidence": _loads(r["evidence_json"], {}),
            "at": float(r["created_at"] or 0),
            "provenance": f"{r['source_table'] or 'council_quest_events'}#{r['source_row_id'] or r['id']}",
            "event_type": et,
        })
    return out


def branches(contribs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for c in contribs:
        if c.get("stance") in ("REFUTES", "DOUBTS"):
            out.append({
                "role": c.get("role", ""),
                "kind": "CLOSED" if c.get("stance") == "REFUTES" else "OPEN",
                "body": c.get("body", ""),
                "provenance": c.get("provenance", ""),
            })
    return out


def _applied_files(events: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out = []
    for e in events:
        if e.get("stage") != "FILE_CHANGE":
            continue
        ev = e.get("evidence") if isinstance(e.get("evidence"), dict) else {}
        path = str(ev.get("path") or "")
        if path:
            out.append({"path": path, "territory": path.split("/")[0] if "/" in path else "",
                        "result": str(ev.get("result") or "")})
    return out


def active_quest(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    """Return the one persisted active Quest. No pressure-derived fake quest."""
    now = float(now or time.time())
    c = _conn()
    if c is None:
        return None
    try:
        if not _has(c, "council_quests"):
            return None
        q = c.execute("SELECT * FROM council_quests WHERE status='ACTIVE' ORDER BY id DESC LIMIT 1").fetchone()
        if not q:
            return None

        contribs = _events(c, int(q["id"]))
        reached = str(q["current_stage"] or "CLUE")
        ri = STAGE_INDEX.get(reached, 0)

        # A node is 'live' only while a real event is recent enough to represent
        # current work. Nothing wanders merely because the page is open.
        live_roles: List[Dict[str, Any]] = []
        seen = set()
        for e in reversed(contribs):
            if now - float(e.get("at") or 0) > 180.0:
                continue
            role = str(e.get("role") or "").upper()
            if not role or role in seen:
                continue
            seen.add(role)
            mand = ROLE_MANDATE.get(role, ROLE_MANDATE["MECHANIST"])
            live_roles.append({
                "role": role, "emoji": mand["emoji"], "sides": mand["sides"],
                "verb": mand["verb"], "subject": e.get("body", ""),
                "habitat": "", "provenance": e.get("provenance", ""),
            })

        trail = []
        for i, (skey, human) in enumerate(JOURNEY):
            if i < ri:
                state = STAGE_REACHED
            elif i == ri:
                state = STAGE_ACTIVE if live_roles else STAGE_REACHED
            else:
                state = STAGE_FOGGED
            trail.append({"key": skey, "human": human, "state": state, "index": i})

        pressure = _loads(q["pressure_json"], {})
        present = pressure.get("senses_present", []) or []
        absent = pressure.get("senses_absent", []) or []
        dests = _loads(q["destinations_json"], []) or []
        latest = contribs[-1] if contribs else None

        return {
            "id": int(q["id"]), "key": str(q["faculty"] or ""),
            "status": str(q["status"] or "ACTIVE"),
            "opened_at": float(q["opened_at"] or 0),
            "senses_have": len(present), "senses_total": len(present) + len(absent),
            "faculty_state": pressure.get("state", ""),
            "headline": str(q["question"] or ""),
            "why": str(pressure.get("note") or ""),
            "known": str(latest.get("body") if latest else pressure.get("note") or ""),
            "destinations": dests,
            "destination_human": " and ".join(HABITAT_LABEL.get(str(d), str(d).lower()) for d in dests),
            "trail": trail, "reached": reached, "reached_index": ri,
            "contributions": contribs, "latest": latest,
            "branches": branches(contribs), "live_roles": live_roles,
            "applied_files": _applied_files(contribs),
            "success": str(q["success_condition"] or ""),
            "kill": str(q["kill_condition"] or ""),
            "territory": " / ".join(str(x) for x in dests),
            "measured_from": str(pressure.get("evidence") or "organism_pressure"),
            "has_council_activity": bool(contribs or live_roles),
        }
    except Exception:
        return None
    finally:
        try: c.close()
        except Exception: pass


def quiet_camp(now: Optional[float] = None) -> Dict[str, Any]:
    """Last completed quest + current pressure when no active quest exists."""
    now = float(now or time.time())
    c = _conn()
    last = None
    if c is not None:
        try:
            if _has(c, "council_quests"):
                r = c.execute("SELECT * FROM council_quests WHERE status='CLOSED' ORDER BY closed_at DESC,id DESC LIMIT 1").fetchone()
                if r:
                    last = {"role": "COUNCIL", "body": f"{r['question']} — {r['close_reason'] or 'closed'}",
                            "at": float(r["closed_at"] or 0)}
        except Exception:
            pass
        finally:
            try: c.close()
            except Exception: pass
    try:
        snap = snapshot(now)
        nxt = snap.get("top_pressure") or {}
    except Exception:
        nxt = {}
    return {
        "last_expedition": last,
        "last_change": None,
        "next_pressure": {
            "label": nxt.get("label", ""),
            "why": f"{nxt.get('label','')} is the strongest unresolved measured pressure."
        } if nxt else None,
    }
