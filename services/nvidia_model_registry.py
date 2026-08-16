from __future__ import annotations

import json, os, sqlite3, time, urllib.request, urllib.error, hashlib
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "sentinuity_matrix.db"
API = "https://integrate.api.nvidia.com/v1"

ROLE_CANDIDATES = {
    "IVARIS": ["qwen/qwen3.5-397b-a17b","deepseek-ai/deepseek-v4-pro","nvidia/nemotron-3-ultra-550b-a55b","mistralai/mistral-large-3-675b-instruct-2512"],
    "NUGGET": ["nvidia/nemotron-3-super-120b-a12b","qwen/qwen3.5-122b-a10b","mistralai/mistral-medium-3.5-128b","openai/gpt-oss-120b"],
    "AXIOM": ["moonshotai/kimi-k2.6","mistralai/mistral-large-3-675b-instruct-2512","z-ai/glm-5.2","qwen/qwen3.5-397b-a17b"],
    "FORGE": ["deepseek-ai/deepseek-v4-pro","poolside/laguna-xs-2.1","qwen/qwen3.5-397b-a17b","openai/gpt-oss-120b"],
    "FAST": ["deepseek-ai/deepseek-v4-flash","stepfun-ai/step-3.7-flash","nvidia/nemotron-3-nano-30b-a3b","openai/gpt-oss-20b"],
    "VISION": ["meta/llama-4-maverick-17b-128e-instruct","google/gemma-4-31b-it","nvidia/nemotron-nano-12b-v2-vl"],
}
CONFIG_KEYS = {"IVARIS":"IVARIS_NIM_MODEL","NUGGET":"NUGGET_NIM_MODEL","AXIOM":"AXIOM_NIM_MODEL","FORGE":"FORGE_NIM_MODEL","FAST":"FAST_NIM_MODEL","VISION":"VISION_NIM_MODEL"}
NON_CHAT_MARKERS = ("embed","retriever","parse","reward","safety","guard","detector","translate","nvclip","deplot","gliner")

# SIGNOFF_MODEL_RESILIENCE_20260725
# Quarantine cooldown for models that returned empty/error responses. A model
# is not re-assignable until the cooldown expires; this is what stops the
# two-model oscillation (see rotate_after_failure below).
QUARANTINE_SEC = float(os.getenv("NIM_MODEL_QUARANTINE_SEC", "1800"))
_DDL_DONE = False


def _connect():
    """SIGNOFF_MODEL_RESILIENCE_20260725: DDL is executed ONCE per process,
    not on every connect. The previous version ran four CREATE TABLE
    statements against the shared trading matrix DB on every single call
    (including inside Ivaris retry loops), which violates the standing
    'no shared-market-DB migrations during runtime' doctrine and adds
    avoidable write-lock pressure to the pricing/execution hot path."""
    global _DDL_DONE
    c=sqlite3.connect(DB, timeout=15); c.row_factory=sqlite3.Row
    c.execute("PRAGMA busy_timeout=5000")
    if not _DDL_DONE:
        c.executescript('''
    CREATE TABLE IF NOT EXISTS llm_model_catalog(model_id TEXT PRIMARY KEY,provider TEXT,first_seen_at REAL,last_seen_at REAL,available INTEGER DEFAULT 1,chat_capable INTEGER,health_status TEXT,median_latency_ms REAL,last_error TEXT,last_probed_at REAL,capability_json TEXT);
    CREATE TABLE IF NOT EXISTS council_model_assignments(agent_name TEXT PRIMARY KEY,provider TEXT NOT NULL,model_id TEXT NOT NULL,fallback_model_id TEXT,assignment_reason TEXT,capability_score REAL,assigned_at REAL NOT NULL,catalogue_version TEXT,assignment_source TEXT,health_status TEXT);
    CREATE TABLE IF NOT EXISTS council_model_assignment_history(id INTEGER PRIMARY KEY AUTOINCREMENT,agent_name TEXT,old_model_id TEXT,new_model_id TEXT,reason TEXT,changed_at REAL,catalogue_version TEXT);
    CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT);
    CREATE TABLE IF NOT EXISTS llm_model_quarantine(model_id TEXT PRIMARY KEY,role TEXT,quarantined_at REAL,until_ts REAL,reason TEXT,failures INTEGER DEFAULT 1);
    ''')

        # SIGNOFF_20260811_NIM_SCHEMA_COMPAT:
        # CREATE TABLE IF NOT EXISTS does not upgrade an older table. Runtime
        # evidence showed rotate_after_failure() crashing on `no such column:
        # model_id`, leaving IVARIS permanently critic-unavailable after a 410.
        # Add the current columns non-destructively and copy legacy aliases when
        # they exist. This is Council/model-control state only; no trading table
        # or capital authority is touched.
        def _ensure_cols(table, spec):
            present = {r[1] for r in c.execute(f"PRAGMA table_info({table})").fetchall()}
            for name, decl, aliases in spec:
                if name not in present:
                    c.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")
                    present.add(name)
                for alias in aliases:
                    if alias in present:
                        c.execute(
                            f"UPDATE {table} SET {name}=COALESCE(NULLIF({name},''), {alias}) "
                            f"WHERE ({name} IS NULL OR {name}='') AND {alias} IS NOT NULL"
                        )
                        break

        _ensure_cols("llm_model_catalog", [
            ("model_id", "TEXT", ("id", "model", "model_name")),
            ("provider", "TEXT", ("owned_by",)),
            ("first_seen_at", "REAL", ()), ("last_seen_at", "REAL", ()),
            ("available", "INTEGER DEFAULT 1", ()), ("chat_capable", "INTEGER", ()),
            ("health_status", "TEXT", ("status",)), ("median_latency_ms", "REAL", ()),
            ("last_error", "TEXT", ("error",)), ("last_probed_at", "REAL", ()),
            ("capability_json", "TEXT", ()),
        ])
        _ensure_cols("council_model_assignments", [
            ("agent_name", "TEXT", ("agent", "role")),
            ("provider", "TEXT", ()),
            ("model_id", "TEXT", ("model", "model_name", "current_model")),
            ("fallback_model_id", "TEXT", ("fallback_model", "fallback")),
            ("assignment_reason", "TEXT", ("reason",)),
            ("capability_score", "REAL", ()), ("assigned_at", "REAL", ("updated_at",)),
            ("catalogue_version", "TEXT", ()), ("assignment_source", "TEXT", ()),
            ("health_status", "TEXT", ("status",)),
        ])
        _ensure_cols("llm_model_quarantine", [
            ("model_id", "TEXT", ("model", "model_name")),
            ("role", "TEXT", ("agent_name",)), ("quarantined_at", "REAL", ()),
            ("until_ts", "REAL", ("until",)), ("reason", "TEXT", ()),
            ("failures", "INTEGER DEFAULT 1", ()),
        ])
        # Legacy catalog/quarantine tables may have used `id`/`model` as the
        # primary key. Current UPSERTs target model_id, so give that canonical
        # alias a uniqueness contract after copying legacy values.
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_model_catalog_model_id ON llm_model_catalog(model_id)")
        c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_llm_model_quarantine_model_id ON llm_model_quarantine(model_id)")
        c.commit()
        _DDL_DONE = True
    return c


def _quarantine(c, model_id: str, role: str, reason: str) -> None:
    """Escalating quarantine: each repeat failure doubles the window
    (capped at 8x). Never raises."""
    try:
        now = time.time()
        row = c.execute("SELECT failures FROM llm_model_quarantine WHERE model_id=?",
                        (model_id,)).fetchone()
        n = int(row["failures"]) + 1 if row else 1
        window = QUARANTINE_SEC * min(8, 2 ** (n - 1))
        c.execute("INSERT INTO llm_model_quarantine(model_id,role,quarantined_at,until_ts,reason,failures)"
                  " VALUES(?,?,?,?,?,?) ON CONFLICT(model_id) DO UPDATE SET"
                  " role=excluded.role,quarantined_at=excluded.quarantined_at,"
                  " until_ts=excluded.until_ts,reason=excluded.reason,failures=?",
                  (model_id, role, now, now + window, str(reason)[:400], n, n))
    except Exception:
        pass


def is_quarantined(model_id: str, c=None) -> bool:
    """True while the model is inside its cooldown window."""
    if not model_id:
        return False
    own = c is None
    try:
        if own:
            c = _connect()
        row = c.execute("SELECT until_ts FROM llm_model_quarantine WHERE model_id=?",
                        (model_id,)).fetchone()
        return bool(row and float(row["until_ts"] or 0) > time.time())
    except Exception:
        return False
    finally:
        if own and c is not None:
            try:
                c.close()
            except Exception:
                pass

def _key():
    k=os.getenv("NVIDIA_NIM_API_KEY","").strip()
    if k: return k
    p=ROOT/".env"
    if p.exists():
        for line in p.read_text(errors="ignore").splitlines():
            if line.strip().startswith("NVIDIA_NIM_API_KEY="):
                return line.split("=",1)[1].strip().strip('"\'')
    return ""

def fetch_catalogue(timeout=30)->List[dict]:
    k=_key()
    if not k: raise RuntimeError("NVIDIA_NIM_API_KEY missing")
    req=urllib.request.Request(API+"/models",headers={"Authorization":f"Bearer {k}","Accept":"application/json"})
    with urllib.request.urlopen(req,timeout=timeout) as r: return json.loads(r.read()).get("data",[])

def _chat_capable(mid:str)->bool: return not any(x in mid.lower() for x in NON_CHAT_MARKERS)

def _probe(mid:str, timeout=35)->Tuple[bool,float,str]:
    k=_key(); started=time.perf_counter()
    payload=json.dumps({"model":mid,"max_tokens":24,"temperature":0,"messages":[{"role":"user","content":"Return exactly: {\"ok\":true}"}]}).encode()
    req=urllib.request.Request(API+"/chat/completions",data=payload,method="POST",headers={"Authorization":f"Bearer {k}","Content-Type":"application/json"})
    try:
        with urllib.request.urlopen(req,timeout=timeout) as r:
            body=r.read().decode("utf-8","replace"); return r.status==200,(time.perf_counter()-started)*1000,body[:300]
    except urllib.error.HTTPError as e: return False,(time.perf_counter()-started)*1000,f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:220]}"
    except Exception as e: return False,(time.perf_counter()-started)*1000,f"{type(e).__name__}: {e}"

def scan_and_align(probe=True)->Dict[str,object]:
    rows=fetch_catalogue(); now=time.time(); ids={str(x.get('id')) for x in rows if x.get('id')}; version=hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]
    con=_connect()
    try:
        con.execute("UPDATE llm_model_catalog SET available=0")
        for x in rows:
            mid=str(x.get("id") or ""); prov=str(x.get("owned_by") or mid.split('/')[0])
            if not mid: continue
            con.execute("INSERT INTO llm_model_catalog(model_id,provider,first_seen_at,last_seen_at,available,chat_capable,health_status) VALUES(?,?,?,?,1,?,?) ON CONFLICT(model_id) DO UPDATE SET provider=excluded.provider,last_seen_at=excluded.last_seen_at,available=1,chat_capable=excluded.chat_capable",(mid,prov,now,now,int(_chat_capable(mid)),"UNTESTED"))
        changes=[]
        for role,cands in ROLE_CANDIDATES.items():
            available=[m for m in cands if m in ids and _chat_capable(m)]
            if not available: continue
            healthy=[]
            for m in available[:3]:
                if probe:
                    ok,lat,err=_probe(m)
                    con.execute("UPDATE llm_model_catalog SET health_status=?,median_latency_ms=?,last_error=?,last_probed_at=? WHERE model_id=?",("HEALTHY" if ok else "FAILED",lat,"" if ok else err,now,m))
                    if ok: healthy.append((m,lat))
                else: healthy.append((m,999999))
            if not healthy: continue
            chosen=healthy[0][0]; fallback=healthy[1][0] if len(healthy)>1 else (available[1] if len(available)>1 else chosen)
            old=con.execute("SELECT model_id FROM council_model_assignments WHERE agent_name=?",(role,)).fetchone(); oldm=old[0] if old else None
            # SIGNOFF_MODEL_RESILIENCE_20260725: never assign a model that is
            # still inside its quarantine cooldown.
            if is_quarantined(chosen, con):
                _alt = next((m for m in ROLE_CANDIDATES.get(role, [])
                             if m in ids and not is_quarantined(m, con)), "")
                if _alt:
                    chosen = _alt
            reason="best validated role candidate available in current NVIDIA catalogue"
            con.execute("INSERT INTO council_model_assignments(agent_name,provider,model_id,fallback_model_id,assignment_reason,capability_score,assigned_at,catalogue_version,assignment_source,health_status) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(agent_name) DO UPDATE SET provider=excluded.provider,model_id=excluded.model_id,fallback_model_id=excluded.fallback_model_id,assignment_reason=excluded.assignment_reason,assigned_at=excluded.assigned_at,catalogue_version=excluded.catalogue_version,assignment_source=excluded.assignment_source,health_status=excluded.health_status",(role,"nim",chosen,fallback,reason,100.0,now,version,"AUTO_DISCOVERY","HEALTHY"))
            con.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(CONFIG_KEYS[role],chosen))
            if oldm!=chosen:
                con.execute("INSERT INTO council_model_assignment_history(agent_name,old_model_id,new_model_id,reason,changed_at,catalogue_version) VALUES(?,?,?,?,?,?)",(role,oldm,chosen,reason,now,version)); changes.append((role,oldm,chosen))
        con.commit(); return {"count":len(ids),"catalogue_version":version,"changes":changes}
    finally: con.close()


_LAST_RUNTIME_SCAN_AT = 0.0
_RUNTIME_SCAN_COOLDOWN_SEC = 300.0

def _catalogue_candidate(con, role: str, exclude=()) -> str:
    """Best known unquarantined chat model from persisted catalogue.

    Role candidates are preferred, but the catalogue is authoritative for
    what NVIDIA actually exposes.  This prevents a stale hard-coded model list
    from leaving IVARIS permanently UNASSIGNED after provider catalogue churn.
    """
    role = str(role or "").upper().strip()
    excluded = {str(x or "").strip() for x in exclude if str(x or "").strip()}
    try:
        rows = con.execute(
            "SELECT model_id,available,chat_capable,health_status,last_probed_at "
            "FROM llm_model_catalog WHERE COALESCE(available,1)=1"
        ).fetchall()
    except Exception:
        return ""

    preferred = {m: idx for idx, m in enumerate(ROLE_CANDIDATES.get(role, []))}
    candidates = []
    for row in rows:
        mid = str(row["model_id"] or "").strip()
        if not mid or mid in excluded or not _chat_capable(mid):
            continue
        try:
            if is_quarantined(mid, con):
                continue
        except Exception:
            continue
        health = str(row["health_status"] or "").upper()
        # FAILED is not a candidate until a later catalogue/probe changes it.
        if health == "FAILED":
            continue
        chat = row["chat_capable"]
        if chat not in (None, 1, True, "1"):
            continue
        pref = preferred.get(mid, 10_000)
        healthy_rank = 0 if health == "HEALTHY" else 1
        probed = float(row["last_probed_at"] or 0.0)
        candidates.append((pref, healthy_rank, -probed, mid))
    candidates.sort()
    return candidates[0][3] if candidates else ""

def _persist_runtime_assignment(con, role: str, model: str, old_model: str = "",
                                reason: str = "runtime catalogue failover") -> str:
    if not model:
        return ""
    now = time.time()
    fallback = _catalogue_candidate(con, role, exclude=(model,))
    con.execute(
        "INSERT INTO council_model_assignments("
        "agent_name,provider,model_id,fallback_model_id,assignment_reason,"
        "capability_score,assigned_at,catalogue_version,assignment_source,health_status"
        ") VALUES(?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(agent_name) DO UPDATE SET "
        "provider=excluded.provider,model_id=excluded.model_id,"
        "fallback_model_id=excluded.fallback_model_id,"
        "assignment_reason=excluded.assignment_reason,"
        "assigned_at=excluded.assigned_at,"
        "assignment_source=excluded.assignment_source,"
        "health_status=excluded.health_status",
        (role, "nim", model, fallback, reason, 100.0, now,
         "runtime-catalogue", "RUNTIME_RECOVERY", "HEALTHY"),
    )
    key = CONFIG_KEYS.get(role)
    if key:
        con.execute(
            "INSERT INTO system_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, model),
        )
    if old_model != model:
        con.execute(
            "INSERT INTO council_model_assignment_history("
            "agent_name,old_model_id,new_model_id,reason,changed_at,catalogue_version"
            ") VALUES(?,?,?,?,?,?)",
            (role, old_model or None, model, reason, now, "runtime-catalogue"),
        )
    con.commit()
    return model


def rotate_after_failure(role: str, failed_model: str, error: str = "", *,
                         refresh: bool = True) -> str:
    """Quarantine one failed model and immediately choose another known model.

    Runtime failover first consumes the stored fallback, then the persisted
    NVIDIA catalogue.  A network catalogue refresh is bounded and does not
    probe every model: the actual IVARIS call is the health probe.
    """
    global _LAST_RUNTIME_SCAN_AT
    role = str(role or "").upper().strip()
    failed_model = str(failed_model or "").strip()
    now = time.time()
    con = _connect()
    try:
        row = con.execute(
            "SELECT model_id,fallback_model_id FROM council_model_assignments "
            "WHERE agent_name=?", (role,)
        ).fetchone()
        current = str(row["model_id"] or "").strip() if row else ""
        fallback = str(row["fallback_model_id"] or "").strip() if row else ""

        if failed_model:
            con.execute(
                "UPDATE llm_model_catalog SET health_status='FAILED',last_error=?,"
                "last_probed_at=? WHERE model_id=?",
                (str(error or "runtime failure")[:400], now, failed_model),
            )
            _quarantine(con, failed_model, role, error or "runtime failure")

        if (
            fallback and fallback != failed_model
            and not is_quarantined(fallback, con)
        ):
            return _persist_runtime_assignment(
                con, role, fallback, failed_model or current,
                "runtime stored-fallback failover",
            )

        candidate = _catalogue_candidate(
            con, role, exclude=(failed_model, current)
        )
        if candidate:
            return _persist_runtime_assignment(
                con, role, candidate, failed_model or current,
                "runtime persisted-catalogue failover",
            )
        con.commit()
    finally:
        con.close()

    # Refresh at most once per five minutes and do not probe every advertised
    # model here.  A stale catalogue should not trap IVARIS indefinitely, but a
    # provider outage should not trigger a scan storm either.
    if refresh and (time.time() - _LAST_RUNTIME_SCAN_AT) >= _RUNTIME_SCAN_COOLDOWN_SEC:
        _LAST_RUNTIME_SCAN_AT = time.time()
        try:
            scan_and_align(probe=False)
        except Exception:
            return ""

        con = _connect()
        try:
            candidate = _catalogue_candidate(con, role, exclude=(failed_model,))
            if candidate:
                return _persist_runtime_assignment(
                    con, role, candidate, failed_model,
                    "runtime refreshed-catalogue failover",
                )
        finally:
            con.close()
    return ""


def get_assignment(role: str, default: str = "") -> str:
    """Return a healthy, non-quarantined runtime assignment.

    If the explicit assignment/config model is unusable, fall through to the
    persisted NVIDIA catalogue rather than repeatedly returning a dead default.
    """
    global _LAST_RUNTIME_SCAN_AT
    role = str(role or "").upper().strip()
    con = _connect()
    try:
        row = con.execute(
            "SELECT model_id FROM council_model_assignments "
            "WHERE agent_name=? AND health_status='HEALTHY'", (role,)
        ).fetchone()
        assigned = str(row["model_id"] or "").strip() if row else ""
        if assigned and not is_quarantined(assigned, con):
            return assigned

        key = CONFIG_KEYS.get(role)
        configured = ""
        if key:
            try:
                cr = con.execute(
                    "SELECT value FROM system_config WHERE key=?", (key,)
                ).fetchone()
                configured = str(cr["value"] or "").strip() if cr else ""
            except Exception:
                configured = ""
            configured = os.getenv(key, configured or "").strip()

        if configured and not is_quarantined(configured, con):
            return _persist_runtime_assignment(
                con, role, configured, assigned,
                "runtime configured-model recovery",
            )

        candidate = _catalogue_candidate(con, role, exclude=(assigned, configured))
        if candidate:
            return _persist_runtime_assignment(
                con, role, candidate, assigned or configured,
                "runtime persisted-catalogue recovery",
            )

        if default and not is_quarantined(default, con):
            return default
    finally:
        con.close()

    # One bounded catalogue refresh when there is literally no usable model.
    if _key() and (time.time() - _LAST_RUNTIME_SCAN_AT) >= _RUNTIME_SCAN_COOLDOWN_SEC:
        _LAST_RUNTIME_SCAN_AT = time.time()
        try:
            scan_and_align(probe=False)
        except Exception:
            return ""
        con = _connect()
        try:
            candidate = _catalogue_candidate(con, role)
            if candidate:
                return _persist_runtime_assignment(
                    con, role, candidate, "",
                    "runtime refreshed-catalogue recovery",
                )
        finally:
            con.close()
    return ""


def get_assignments()->Dict[str,dict]:
    try:
        con=_connect(); rows=con.execute("SELECT * FROM council_model_assignments ORDER BY agent_name").fetchall(); con.close(); return {r['agent_name']:dict(r) for r in rows}
    except Exception: return {}

if __name__=="__main__": print(json.dumps(scan_and_align(probe=True),indent=2))
