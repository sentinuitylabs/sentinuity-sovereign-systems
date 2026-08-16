# coding: utf-8
"""
services/lumen_field_state.py — LUMEN_FIELD_STATE_20260813

Truth/state layer for Sentinuity's Living Field.

Design rules:
- Rendering is read-only. Merely opening the UI never creates schema or writes rows.
- Explicit write APIs create their own small cognition tables lazily.
- Existing Council/GitHub ledgers are projected into the field so the UI is useful
  immediately; no demo rows and no synthetic "agent activity" are invented.
- A Specimen is a proposition, not a repository.
- Evidence mass counts independent support groups, not raw source rows.
- This module is Tier-3 cognition and must never raise into trading surfaces.
"""
from __future__ import annotations

import json
import sqlite3
import time
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

try:
    from core.schema import get_connection
except Exception:  # defensive import for UI-only degradation
    get_connection = None  # type: ignore

ROLES: Dict[str, Dict[str, Any]] = {
    "NUGGET":    {"label": "Nugget",    "emoji": "🌲", "sides": 3},
    "MECHANIST": {"label": "Mechanist", "emoji": "🔎", "sides": 4},
    "RHIZA":     {"label": "Rhiza",     "emoji": "🧠", "sides": 5},
    "IVARIS":    {"label": "Ivaris",    "emoji": "🛡️", "sides": 6},
    "SUBSTRATE": {"label": "Substrate", "emoji": "🧪", "sides": 0},
    "FORGE":     {"label": "Forge",     "emoji": "🔨", "sides": 4},
    "POLARIS":   {"label": "Polaris",   "emoji": "⭐", "sides": 8},
}

HABITATS: Tuple[str, ...] = (
    "COUNCIL", "GITHUB_EXPEDITION", "SOLANA", "COPYTRADE", "PRICE_TRUTH",
    "SUBSTRATE", "INTELLIGENCE", "FORGE", "MEMORY",
)

JOURNEY: Tuple[str, ...] = (
    "TRAILHEAD", "FOREST", "SPECIMEN", "CAMP", "COMPARISON", "CHALLENGE",
    "PROVING_GROUND", "FORGE", "POLARIS", "REALITY",
)

VALID_ACTIVITIES = {
    "TRAVELLING", "INSPECTING", "COMPARING", "CHALLENGING", "TESTING",
    "BUILDING", "JUDGING", "AT_CAMP",
}
VALID_HANDLING = {"CLEAR_TRAIL", "HANDLE_WITH_CARE", "TOXIC", "UNASSESSED"}
VALID_VALUE = {"RARE", "NOTABLE", "ORDINARY", "UNASSESSED"}
VALID_NOTE_STATUS = {"OPEN", "CLAIMED", "RESOLVED", "DISMISSED"}

_SCHEMA_READY = False


def _conn() -> sqlite3.Connection:
    if get_connection is None:
        raise RuntimeError("canonical Sentinuity DB connection unavailable")
    c = get_connection()
    try:
        c.row_factory = sqlite3.Row
    except Exception:
        pass
    return c


def _tables(c: sqlite3.Connection) -> set[str]:
    try:
        return {str(r[0]) for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
    except Exception:
        return set()


def _cols(c: sqlite3.Connection, table: str) -> set[str]:
    try:
        return {str(r[1]) for r in c.execute(f'PRAGMA table_info("{table}")').fetchall()}
    except Exception:
        return set()


def _rows(c: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> List[Dict[str, Any]]:
    try:
        cur = c.execute(sql, tuple(params))
        names = [str(x[0]) for x in cur.description or []]
        return [dict(zip(names, r)) for r in cur.fetchall()]
    except Exception:
        return []


def _one(c: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Optional[Dict[str, Any]]:
    rr = _rows(c, sql, params)
    return rr[0] if rr else None


def _safe_text(v: Any, n: int = 900) -> str:
    s = str(v or "").replace("\x00", " ").strip()
    return s[:n]


def _epoch(v: Any) -> float:
    try:
        x = float(v or 0)
        if x > 1e14:
            x /= 1e6
        elif x > 1e11:
            x /= 1e3
        return x
    except Exception:
        return 0.0


def _json(v: Any) -> Dict[str, Any]:
    if isinstance(v, dict):
        return v
    if not v:
        return {}
    try:
        x = json.loads(str(v))
        return x if isinstance(x, dict) else {}
    except Exception:
        return {}


def _role(raw: Any) -> str:
    s = str(raw or "").upper().strip()
    aliases = {
        "POLAR": "POLARIS", "POLARIS": "POLARIS",
        "IVY": "IVARIS", "IVARIS": "IVARIS",
        "NUGGET": "NUGGET", "SCOUT": "NUGGET", "GITHUB_SCOUT": "NUGGET",
        "RHIZA": "RHIZA", "MEMORY": "RHIZA",
        "MECHANIST": "MECHANIST", "ARCHITECT": "MECHANIST", "AXON": "MECHANIST",
        "SUBSTRATE": "SUBSTRATE", "EXPERIMENTER": "SUBSTRATE",
        "FORGE": "FORGE", "ALPHA_FORGE": "FORGE",
    }
    return aliases.get(s, s if s in ROLES else "")


def _habitat_from_text(*values: Any) -> str:
    blob = " ".join(str(v or "") for v in values).lower()
    if "copy" in blob or "wallet" in blob or "smart money" in blob:
        return "COPYTRADE"
    if "price" in blob or "oracle" in blob or "mark" in blob:
        return "PRICE_TRUTH"
    if "substrate" in blob or "experiment" in blob or "replay" in blob:
        return "SUBSTRATE"
    if "forge" in blob or "patch" in blob or "build" in blob:
        return "FORGE"
    if "intelligence" in blob or "intel" in blob or "memory" in blob:
        return "INTELLIGENCE"
    if "github" in blob or "repository" in blob or "expedition" in blob:
        return "GITHUB_EXPEDITION"
    if "solana" in blob or "pump" in blob or "entry" in blob or "runner" in blob:
        return "SOLANA"
    return "COUNCIL"


def _activity_from_event(event: str, disposition: str = "") -> str:
    e = (event or "").upper()
    d = (disposition or "").upper()
    if "SEARCH" in e or "TRAIL" in e:
        return "TRAVELLING"
    if "SOURCE_INSPECTED" in e or "INSPECT" in e or "SCREEN" in e:
        return "INSPECTING"
    if "COMPARE" in e or "ABSTRACTION" in e:
        return "COMPARING"
    if "CHALLENGE" in e or "CRITIC" in e or "REJECT" in d:
        return "CHALLENGING"
    if "TEST" in e or "REPLAY" in e or "EXPERIMENT" in e:
        return "TESTING"
    if "FORGE" in e or "BUILD" in e or "PATCH" in e:
        return "BUILDING"
    if "POLARIS" in e or "JUDGE" in e or "APPROV" in e:
        return "JUDGING"
    return "INSPECTING"


def ensure_schema(c: Optional[sqlite3.Connection] = None) -> bool:
    """Create the Tier-3 Living Field tables. Called only by explicit write APIs."""
    global _SCHEMA_READY
    if _SCHEMA_READY:
        return True
    own = c is None
    try:
        c = c or _conn()
        c.executescript("""
        CREATE TABLE IF NOT EXISTS council_lumen_assignments(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            role TEXT NOT NULL,
            model_id TEXT,
            habitat TEXT NOT NULL,
            activity TEXT NOT NULL,
            subject TEXT,
            related_type TEXT,
            related_id TEXT,
            opened_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            closed_at REAL,
            status TEXT NOT NULL DEFAULT 'OPEN'
        );
        CREATE INDEX IF NOT EXISTS idx_lumen_assignment_open
            ON council_lumen_assignments(status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS council_field_notes(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            author_role TEXT NOT NULL,
            model_id TEXT,
            habitat TEXT NOT NULL,
            body TEXT NOT NULL,
            next_action TEXT,
            related_type TEXT,
            related_id TEXT,
            evidence_json TEXT,
            status TEXT NOT NULL DEFAULT 'OPEN',
            claimed_by_role TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            resolved_at REAL
        );
        CREATE INDEX IF NOT EXISTS idx_lumen_notes_open
            ON council_field_notes(status, updated_at DESC);

        CREATE TABLE IF NOT EXISTS council_specimens(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            proposition TEXT NOT NULL,
            they_do TEXT,
            we_do TEXT,
            the_difference TEXT,
            why_it_may_matter TEXT,
            how_we_kill_it TEXT,
            belongs_to TEXT,
            journey_stage TEXT NOT NULL DEFAULT 'SPECIMEN',
            handling TEXT NOT NULL DEFAULT 'UNASSESSED',
            value_axis TEXT NOT NULL DEFAULT 'UNASSESSED',
            contradicted INTEGER NOT NULL DEFAULT 0,
            contradiction_reason TEXT,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_lumen_specimens_stage
            ON council_specimens(journey_stage, updated_at DESC);

        CREATE TABLE IF NOT EXISTS council_specimen_evidence(
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            specimen_id INTEGER NOT NULL,
            source_type TEXT NOT NULL,
            source_ref TEXT NOT NULL,
            commit_sha TEXT,
            independence_group TEXT,
            evidence_note TEXT,
            created_at REAL NOT NULL,
            UNIQUE(specimen_id, source_type, source_ref, commit_sha),
            FOREIGN KEY(specimen_id) REFERENCES council_specimens(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_lumen_evidence_specimen
            ON council_specimen_evidence(specimen_id, independence_group);
        """)
        _SCHEMA_READY = True
        return True
    except Exception:
        return False
    finally:
        if own and c is not None:
            try:
                c.close()
            except Exception:
                pass


def open_assignment(role: str, habitat: str, activity: str, *, model_id: str = "",
                    subject: str = "", related_type: str = "", related_id: Any = "") -> int:
    r, h, a = _role(role), str(habitat or "").upper(), str(activity or "").upper()
    if not r or h not in HABITATS or a not in VALID_ACTIVITIES:
        return 0
    c = None
    try:
        c = _conn()
        if not ensure_schema(c):
            return 0
        now = time.time()
        cur = c.execute(
            """INSERT INTO council_lumen_assignments
               (role,model_id,habitat,activity,subject,related_type,related_id,opened_at,updated_at,status)
               VALUES(?,?,?,?,?,?,?,?,?,'OPEN')""",
            (r, _safe_text(model_id, 160), h, a, _safe_text(subject, 500),
             _safe_text(related_type, 100), _safe_text(related_id, 160), now, now),
        )
        return int(cur.lastrowid or 0)
    except Exception:
        return 0
    finally:
        if c is not None:
            try: c.close()
            except Exception: pass


def update_assignment(assignment_id: int, *, habitat: Optional[str] = None,
                      activity: Optional[str] = None, subject: Optional[str] = None,
                      close: bool = False) -> bool:
    c = None
    try:
        c = _conn()
        if not ensure_schema(c):
            return False
        sets, vals = ["updated_at=?"], [time.time()]
        if habitat is not None:
            h = str(habitat).upper()
            if h not in HABITATS: return False
            sets.append("habitat=?"); vals.append(h)
        if activity is not None:
            a = str(activity).upper()
            if a not in VALID_ACTIVITIES: return False
            sets.append("activity=?"); vals.append(a)
        if subject is not None:
            sets.append("subject=?"); vals.append(_safe_text(subject, 500))
        if close:
            sets += ["status='CLOSED'", "closed_at=?"]
            vals.append(time.time())
        vals.append(int(assignment_id))
        c.execute(f"UPDATE council_lumen_assignments SET {','.join(sets)} WHERE id=?", tuple(vals))
        return True
    except Exception:
        return False
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def leave_field_note(author_role: str, habitat: str, body: str, *, model_id: str = "",
                     next_action: str = "", related_type: str = "", related_id: Any = "",
                     evidence: Optional[Iterable[Any]] = None) -> int:
    r, h = _role(author_role), str(habitat or "").upper()
    if not r or h not in HABITATS or not str(body or "").strip():
        return 0
    c = None
    try:
        c = _conn()
        if not ensure_schema(c): return 0
        now = time.time()
        ev = list(evidence or [])[:30]
        cur = c.execute(
            """INSERT INTO council_field_notes
               (author_role,model_id,habitat,body,next_action,related_type,related_id,evidence_json,status,created_at,updated_at)
               VALUES(?,?,?,?,?,?,?,?, 'OPEN',?,?)""",
            (r,_safe_text(model_id,160),h,_safe_text(body,1400),_safe_text(next_action,700),
             _safe_text(related_type,100),_safe_text(related_id,160),json.dumps(ev,default=str),now,now),
        )
        return int(cur.lastrowid or 0)
    except Exception:
        return 0
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def claim_field_note(note_id: int, role: str) -> bool:
    r = _role(role)
    if not r: return False
    c = None
    try:
        c = _conn();
        if not ensure_schema(c): return False
        cur = c.execute(
            "UPDATE council_field_notes SET status='CLAIMED',claimed_by_role=?,updated_at=? WHERE id=? AND status='OPEN'",
            (r,time.time(),int(note_id)),
        )
        return bool(cur.rowcount)
    except Exception:
        return False
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def create_specimen(proposition: str, *, they_do: str = "", we_do: str = "",
                    the_difference: str = "", why_it_may_matter: str = "",
                    how_we_kill_it: str = "", belongs_to: str = "",
                    journey_stage: str = "SPECIMEN", handling: str = "UNASSESSED",
                    value_axis: str = "UNASSESSED") -> int:
    if not str(proposition or "").strip(): return 0
    stage = str(journey_stage or "SPECIMEN").upper()
    if stage not in set(JOURNEY) | {"RETURNED_TO_MEMORY", "SECOND_EXPEDITION"}: return 0
    h = str(handling or "UNASSESSED").upper(); v = str(value_axis or "UNASSESSED").upper()
    if h not in VALID_HANDLING or v not in VALID_VALUE: return 0
    belongs = str(belongs_to or "").upper()
    if belongs and belongs not in HABITATS: return 0
    c = None
    try:
        c = _conn();
        if not ensure_schema(c): return 0
        now=time.time()
        cur=c.execute("""INSERT INTO council_specimens
          (proposition,they_do,we_do,the_difference,why_it_may_matter,how_we_kill_it,belongs_to,
           journey_stage,handling,value_axis,created_at,updated_at)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
          tuple(_safe_text(x,1800) for x in (proposition,they_do,we_do,the_difference,why_it_may_matter,how_we_kill_it)) +
          (belongs,stage,h,v,now,now))
        return int(cur.lastrowid or 0)
    except Exception:
        return 0
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def add_specimen_evidence(specimen_id: int, source_type: str, source_ref: str, *,
                         commit_sha: str = "", independence_group: str = "",
                         evidence_note: str = "") -> bool:
    if not source_type or not source_ref: return False
    c=None
    try:
        c=_conn();
        if not ensure_schema(c): return False
        # No group means this source is its own independent voice. A caller that
        # knows two repos/forks share ancestry MUST give them the same group.
        group=_safe_text(independence_group or f"{source_type}:{source_ref}",400)
        c.execute("""INSERT OR IGNORE INTO council_specimen_evidence
          (specimen_id,source_type,source_ref,commit_sha,independence_group,evidence_note,created_at)
          VALUES(?,?,?,?,?,?,?)""",
          (int(specimen_id),_safe_text(source_type,80),_safe_text(source_ref,900),_safe_text(commit_sha,100),
           group,_safe_text(evidence_note,1000),time.time()))
        return True
    except Exception:
        return False
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def _explicit_nodes(c: sqlite3.Connection, now: float) -> List[Dict[str, Any]]:
    if "council_lumen_assignments" not in _tables(c): return []
    rr=_rows(c,"""SELECT * FROM council_lumen_assignments
                   WHERE status='OPEN' ORDER BY updated_at DESC LIMIT 40""")
    out=[]
    for r in rr:
        role=_role(r.get("role")); hab=str(r.get("habitat") or "").upper(); act=str(r.get("activity") or "").upper()
        if not role or hab not in HABITATS or act not in VALID_ACTIVITIES: continue
        age=max(0.0,now-_epoch(r.get("updated_at")))
        out.append({**r,"role":role,"habitat":hab,"activity":act,"stale":age>900,
                    "inferred":False,"provenance":f"council_lumen_assignments#{r.get('id')}"})
    return out


def _project_runtime_nodes(c: sqlite3.Connection, now: float) -> List[Dict[str, Any]]:
    """Project only actors that are explicitly present in existing runtime rows."""
    tt=_tables(c); out=[]
    # Scout expedition state is a real actor/service state, so NUGGET may be shown.
    if "github_expedition_state" in tt:
        r=_one(c,"SELECT * FROM github_expedition_state WHERE singleton=1") or {}
        updated=_epoch(r.get("updated_at") or r.get("cycle_finished_at") or r.get("cycle_started_at"))
        if updated and now-updated <= 1800:
            status=str(r.get("status") or "").upper()
            if "WAIT" in status or "TRAILHEAD" in status: act="AT_CAMP"
            elif "CLOSER" in status or "INSPECT" in status: act="INSPECTING"
            else: act="TRAVELLING"
            out.append({"id":"github_scout","role":"NUGGET","model_id":"","habitat":"GITHUB_EXPEDITION",
                        "activity":act,"subject":_safe_text(r.get("current_project") or r.get("active_query"),400),
                        "updated_at":updated,"stale":now-updated>600,"inferred":True,
                        "provenance":"github_expedition_state#1"})
    # Recent research rows carry their own actor. Show only if actor maps to a role.
    if "research_activity_ledger" in tt:
        cc=_cols(c,"research_activity_ledger")
        tc="created_at" if "created_at" in cc else ("updated_at" if "updated_at" in cc else "")
        if tc:
            rr=_rows(c,f'SELECT * FROM research_activity_ledger ORDER BY "{tc}" DESC LIMIT 20')
            seen=set()
            for r in rr:
                t=_epoch(r.get(tc)); role=_role(r.get("actor"))
                if not role or not t or now-t>420: continue
                event=str(r.get("event_type") or ""); disp=str(r.get("disposition") or "")
                hab=_habitat_from_text(r.get("task_id"),r.get("query"),r.get("summary"),r.get("source_type"))
                key=(role,hab)
                if key in seen: continue
                seen.add(key)
                out.append({"id":f"research:{r.get('id')}","role":role,"model_id":"","habitat":hab,
                            "activity":_activity_from_event(event,disp),
                            "subject":_safe_text(r.get("query") or r.get("summary"),400),"updated_at":t,
                            "stale":False,"inferred":True,"provenance":f"research_activity_ledger#{r.get('id')}"})
    # Claimed standing work: use current_owner only if it is itself a recognised role.
    if "polaris_standing_tasks" in tt:
        rr=_rows(c,"""SELECT * FROM polaris_standing_tasks
                     WHERE current_owner IS NOT NULL AND TRIM(current_owner)<>''
                       AND UPPER(COALESCE(status,'')) NOT IN ('DONE','COMPLETED','ARCHIVED','BLOCKED')
                     ORDER BY COALESCE(updated_at,0) DESC LIMIT 12""")
        for r in rr:
            role=_role(r.get("current_owner")); t=_epoch(r.get("updated_at"))
            if not role or (t and now-t>900): continue
            hab=_habitat_from_text(r.get("domain"),r.get("title"),r.get("stage"))
            out.append({"id":f"task:{r.get('id')}","role":role,"model_id":"","habitat":hab,
                        "activity":_activity_from_event(str(r.get("stage") or r.get("status") or "")),
                        "subject":_safe_text(r.get("title"),400),"updated_at":t or now,
                        "stale":bool(t and now-t>600),"inferred":True,
                        "provenance":f"polaris_standing_tasks#{r.get('id')}"})
    return out


def active_nodes() -> List[Dict[str, Any]]:
    c=None
    try:
        c=_conn(); now=time.time()
        explicit=_explicit_nodes(c,now)
        occupied={(n["role"],n["habitat"]) for n in explicit}
        projected=[n for n in _project_runtime_nodes(c,now) if (n["role"],n["habitat"]) not in occupied]
        return (explicit+projected)[:48]
    except Exception:
        return []
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def camp_roster() -> List[Dict[str, Any]]:
    nodes=active_nodes(); by_role={}
    for n in nodes:
        by_role.setdefault(n["role"],n)
    out=[]
    for role,meta in ROLES.items():
        n=by_role.get(role)
        out.append({"role":role,"label":meta["label"],"emoji":meta["emoji"],
                    "in_field":bool(n and n.get("activity")!="AT_CAMP"),
                    "habitat":n.get("habitat") if n else "COUNCIL",
                    "activity":n.get("activity") if n else "AT_CAMP",
                    "subject":n.get("subject") if n else "",
                    "provenance":n.get("provenance") if n else ""})
    return out


def _explicit_notes(c: sqlite3.Connection) -> List[Dict[str, Any]]:
    if "council_field_notes" not in _tables(c): return []
    rr=_rows(c,"""SELECT * FROM council_field_notes
                   WHERE status IN ('OPEN','CLAIMED') ORDER BY updated_at DESC LIMIT 30""")
    out=[]
    for r in rr:
        ev=[]
        try: ev=json.loads(str(r.get("evidence_json") or "[]"))
        except Exception: pass
        out.append({**r,"author_role":_role(r.get("author_role")) or str(r.get("author_role") or ""),
                    "evidence":ev if isinstance(ev,list) else [],"projected":False,
                    "provenance":f"council_field_notes#{r.get('id')}"})
    return out


def _project_activity_notes(c: sqlite3.Connection) -> List[Dict[str, Any]]:
    if "research_activity_ledger" not in _tables(c): return []
    rr=_rows(c,"SELECT * FROM research_activity_ledger ORDER BY created_at DESC LIMIT 16")
    out=[]; now=time.time()
    for r in rr:
        t=_epoch(r.get("created_at")); role=_role(r.get("actor"))
        if not role or not t or now-t>7200: continue
        event=str(r.get("event_type") or "").upper()
        if event not in {"SOURCE_INSPECTED","SEARCH_BROADENED","SEARCH_EXECUTED","SEARCH_PLANNED"}: continue
        summary=_safe_text(r.get("summary"),1000)
        query=_safe_text(r.get("query"),500)
        if event=="SEARCH_PLANNED":
            body=f"I’m following a new trail: {query}." if query else "I’m preparing a new expedition search."
            next_action="Inspect whatever survives the first relevance and safety screen."
        elif event=="SEARCH_BROADENED":
            body=f"The precise trail came back empty, so I broadened the search to: {query}."
            next_action="Compare the broader results without lowering the evidence bar."
        elif event=="SEARCH_EXECUTED":
            body=f"The search returned evidence to inspect. {summary}" if summary else "The search returned repositories to inspect."
            next_action="Open the promising sources at pinned commits; discard noise."
        else:
            body=summary or "I inspected an external source and recorded what survived screening."
            next_action="Bring any transferable proposition back for comparison with Sentinuity."
        out.append({"id":f"activity:{r.get('id')}","author_role":role,"model_id":"",
                    "habitat":_habitat_from_text(r.get("task_id"),query,summary),"body":body,
                    "next_action":next_action,"related_type":"research_activity","related_id":r.get("id"),
                    "evidence":[r.get("source_ref")] if r.get("source_ref") else [],"status":"OBSERVED",
                    "claimed_by_role":"","created_at":t,"updated_at":t,"projected":True,
                    "provenance":f"research_activity_ledger#{r.get('id')}"})
    return out[:8]


def field_notes() -> List[Dict[str, Any]]:
    c=None
    try:
        c=_conn(); explicit=_explicit_notes(c)
        # Explicit collaborative notes outrank projected activity narration.
        return (explicit + _project_activity_notes(c))[:20]
    except Exception:
        return []
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def _evidence_stats(c: sqlite3.Connection, sid: int) -> Tuple[int,int]:
    if "council_specimen_evidence" not in _tables(c): return (0,0)
    rr=_rows(c,"SELECT source_type,source_ref,commit_sha,independence_group FROM council_specimen_evidence WHERE specimen_id=?",(sid,))
    groups=set()
    for r in rr:
        g=_safe_text(r.get("independence_group"),400) or f"{r.get('source_type')}:{r.get('source_ref')}:{r.get('commit_sha')}"
        groups.add(g)
    return len(rr),len(groups)


def specimens(limit: int = 8) -> List[Dict[str, Any]]:
    c=None
    try:
        c=_conn()
        if "council_specimens" not in _tables(c): return []
        rr=_rows(c,"SELECT * FROM council_specimens ORDER BY updated_at DESC LIMIT ?",(max(1,min(30,int(limit))),))
        out=[]
        for r in rr:
            count,indep=_evidence_stats(c,int(r.get("id") or 0))
            out.append({**r,"journey_stage":str(r.get("journey_stage") or "SPECIMEN").upper(),
                        "handling":str(r.get("handling") or "UNASSESSED").upper(),
                        "value_axis":str(r.get("value_axis") or "UNASSESSED").upper(),
                        "contradicted":bool(r.get("contradicted")),"evidence_count":count,
                        "independent_support":indep,"provenance":f"council_specimens#{r.get('id')}"})
        return out
    except Exception:
        return []
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def camp_story(limit: int = 10) -> List[Dict[str, Any]]:
    """Human-readable, evidence-backed activity narration. No hidden reasoning."""
    c=None
    try:
        c=_conn(); tt=_tables(c); out=[]
        if "research_activity_ledger" in tt:
            rr=_rows(c,"SELECT * FROM research_activity_ledger ORDER BY created_at DESC LIMIT 30")
            for r in rr:
                role=_role(r.get("actor"))
                if not role: continue
                event=str(r.get("event_type") or "").upper(); query=_safe_text(r.get("query"),260)
                summary=_safe_text(r.get("summary"),700); src=_safe_text(r.get("source_ref"),180)
                if event=="SEARCH_PLANNED":
                    title="LEAVING THE TRAILHEAD"; text=f"I’m going looking for {query}." if query else "I’m opening a new research trail."
                elif event=="SEARCH_BROADENED":
                    title="THE TRAIL WENT QUIET"; text=f"The precise search found nothing useful, so I broadened it to {query}."
                elif event=="SEARCH_EXECUTED":
                    title="THE FOREST OPENED UP"; text=summary or "The search returned sources worth screening."
                elif event=="SOURCE_INSPECTED":
                    title="BACK WITH A FIND"; text=summary or f"I inspected {src or 'an external source'} and recorded what survived screening."
                else:
                    continue
                out.append({"role":role,"title":title,"text":text,"at":_epoch(r.get("created_at")),
                            "provenance":f"research_activity_ledger#{r.get('id')}"})
        # Polaris proposals are conclusions, not inner monologue.
        if "polaris_proposals" in tt:
            rr=_rows(c,"SELECT * FROM polaris_proposals ORDER BY created_at DESC LIMIT 8")
            for r in rr:
                text=_safe_text(r.get("proposal_text") or r.get("suggested_action"),700)
                if not text: continue
                out.append({"role":"POLARIS","title":"NORTH STAR", "text":text,
                            "at":_epoch(r.get("created_at")),"provenance":f"polaris_proposals#{r.get('id')}"})
        out.sort(key=lambda x:x.get("at",0),reverse=True)
        return out[:max(1,min(20,int(limit)))]
    except Exception:
        return []
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass



def fieldcraft_scores(limit: int = 7) -> List[Dict[str, Any]]:
    c=None
    try:
        c=_conn(); tt=_tables(c)
        if "github_agent_field_score" not in tt: return []
        rr=_rows(c,"""SELECT * FROM github_agent_field_score
                       ORDER BY COALESCE(cumulative_score,0) DESC, COALESCE(updated_at,0) DESC LIMIT ?""",
                 (max(1,min(20,int(limit))),))
        out=[]
        for r in rr:
            role=_role(r.get("agent_name")) or _safe_text(r.get("agent_name"),60).upper()
            out.append({**r,"role":role,"provenance":f"github_agent_field_score#{r.get('agent_name')}:{r.get('model_id')}"})
        return out
    except Exception:
        return []
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass


def expedition_state() -> Dict[str, Any]:
    c=None
    try:
        c=_conn(); tt=_tables(c)
        if "github_expedition_state" not in tt: return {}
        r=_one(c,"SELECT * FROM github_expedition_state WHERE singleton=1") or {}
        if r: r["provenance"]="github_expedition_state#1"
        return r
    except Exception:
        return {}
    finally:
        if c is not None:
            try:c.close()
            except Exception:pass

def field_summary() -> Dict[str, Any]:
    nodes=active_nodes(); notes=field_notes(); specs=specimens(limit=50)
    const=CounterLike()
    for n in nodes: const.add(str(n.get("habitat") or ""))
    return {
        "nodes_active":len(nodes),
        "nodes_stale":sum(1 for n in nodes if n.get("stale")),
        "open_notes":sum(1 for n in notes if str(n.get("status") or "").upper() in {"OPEN","CLAIMED","OBSERVED"}),
        "specimens":len(specs),
        "specimens_contradicted":sum(1 for s in specs if s.get("contradicted")),
        "constellations":{k:v for k,v in const.items() if v>=2},
        "has_any_state":bool(nodes or notes or specs),
    }


class CounterLike(dict):
    def add(self,key:str) -> None:
        if key: self[key]=int(self.get(key,0))+1
