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
    c.execute("PRAGMA busy_timeout=20000")
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


def _role_pool(rows: List[dict], role: str) -> List[str]:
    """Return current-catalogue candidates, static preferences first.

    NVIDIA rotates/renames hosted models. Static role lists are preferences, not
    truth. A model is eligible for autonomous failover only if it exists in the
    provider's current catalogue and is chat-capable; runtime probing still has
    to succeed before it can become the active IVARIS assignment.
    """
    ids=[str(x.get("id") or "") for x in rows if x.get("id") and _chat_capable(str(x.get("id")))]
    present=set(ids)
    preferred=[m for m in ROLE_CANDIDATES.get(str(role).upper(), []) if m in present]
    # Dynamic tail keeps the council recoverable when NVIDIA retires every
    # static preferred model. Prefer general instruction/chat families, but do
    # not assign any dynamic model until _probe() proves the endpoint works.
    markers=("qwen","deepseek","nemotron","mistral","llama","glm","kimi","gpt-oss")
    dynamic=sorted((m for m in ids if m not in preferred), key=lambda m:(0 if any(x in m.lower() for x in markers) else 1,m))
    return preferred+dynamic

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
    """Refresh catalogue and assignments without holding SQLite over network I/O.

    Phase 1 is a short catalogue write. Phase 2 probes providers with no DB
    connection open. Phase 3 writes probe/assignment results in a short
    transaction. This removes the lock-amplification path that previously held
    a write transaction across repeated 35-second HTTP probes.
    """
    rows=fetch_catalogue(); now=time.time()
    ids={str(x.get('id')) for x in rows if x.get('id')}
    version=hashlib.sha256("\n".join(sorted(ids)).encode()).hexdigest()[:16]

    # Phase 1: catalogue truth only; commit before any provider probe.
    con=_connect()
    try:
        try: con.execute("PRAGMA journal_mode=WAL")
        except Exception: pass
        con.execute("UPDATE llm_model_catalog SET available=0")
        for x in rows:
            mid=str(x.get("id") or ""); prov=str(x.get("owned_by") or mid.split('/')[0])
            if not mid: continue
            con.execute("INSERT INTO llm_model_catalog(model_id,provider,first_seen_at,last_seen_at,available,chat_capable,health_status) VALUES(?,?,?,?,1,?,?) ON CONFLICT(model_id) DO UPDATE SET provider=excluded.provider,last_seen_at=excluded.last_seen_at,available=1,chat_capable=excluded.chat_capable",(mid,prov,now,now,int(_chat_capable(mid)),"UNTESTED"))
        con.commit()
    finally:
        con.close()

    # Phase 2: network I/O with no SQLite connection held.
    probe_results={}
    role_candidates={}
    for role in ROLE_CANDIDATES:
        available=_role_pool(rows, role)
        role_candidates[role]=available
        for mid in available[:6]:
            if mid in probe_results: continue
            if probe:
                probe_results[mid]=_probe(mid)
            else:
                probe_results[mid]=(True,999999.0,"")

    # Phase 3: short result/assignment write transaction.
    changes=[]; write_now=time.time(); con=_connect()
    try:
        try: con.execute("PRAGMA journal_mode=WAL")
        except Exception: pass
        for mid,(ok,lat,err) in probe_results.items():
            con.execute("UPDATE llm_model_catalog SET health_status=?,median_latency_ms=?,last_error=?,last_probed_at=? WHERE model_id=?",("HEALTHY" if ok else "FAILED",lat,"" if ok else err,write_now,mid))
        for role,available in role_candidates.items():
            healthy=[(m,probe_results[m][1]) for m in available[:6] if probe_results.get(m,(False,0,''))[0]]
            if not healthy: continue
            eligible=[m for m,_ in healthy if not is_quarantined(m,con)]
            if not eligible: continue
            chosen=eligible[0]; fallback=eligible[1] if len(eligible)>1 else chosen
            old=con.execute("SELECT model_id FROM council_model_assignments WHERE agent_name=?",(role,)).fetchone(); oldm=old[0] if old else None
            reason="best validated role candidate available in current NVIDIA catalogue"
            con.execute("INSERT INTO council_model_assignments(agent_name,provider,model_id,fallback_model_id,assignment_reason,capability_score,assigned_at,catalogue_version,assignment_source,health_status) VALUES(?,?,?,?,?,?,?,?,?,?) ON CONFLICT(agent_name) DO UPDATE SET provider=excluded.provider,model_id=excluded.model_id,fallback_model_id=excluded.fallback_model_id,assignment_reason=excluded.assignment_reason,assigned_at=excluded.assigned_at,catalogue_version=excluded.catalogue_version,assignment_source=excluded.assignment_source,health_status=excluded.health_status",(role,"nim",chosen,fallback,reason,100.0,write_now,version,"AUTO_DISCOVERY","HEALTHY"))
            con.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(CONFIG_KEYS[role],chosen))
            if oldm!=chosen:
                con.execute("INSERT INTO council_model_assignment_history(agent_name,old_model_id,new_model_id,reason,changed_at,catalogue_version) VALUES(?,?,?,?,?,?)",(role,oldm,chosen,reason,write_now,version)); changes.append((role,oldm,chosen))
        con.commit()
        return {"count":len(ids),"catalogue_version":version,"changes":changes}
    finally:
        con.close()

def rotate_after_failure(role: str, failed_model: str, error: str = "", *, refresh: bool = True) -> str:
    """Rotate a failed Council model only to catalogue-confirmed healthy truth.

    A stale fallback string is not a fallback.  The prior implementation could
    rotate IVARIS from one retired model to another because it checked only the
    quarantine table.  This path also retries short SQLite lock contention; it
    never touches trading/capital tables.
    """
    role=str(role or "").upper().strip(); failed_model=str(failed_model or "").strip()
    now=time.time()

    def _eligible(con, mid: str) -> bool:
        if not mid or mid == failed_model or is_quarantined(mid, con): return False
        row=con.execute("SELECT available,chat_capable,health_status FROM llm_model_catalog WHERE model_id=?",(mid,)).fetchone()
        if not row: return False
        return int(row["available"] or 0)==1 and int(row["chat_capable"] or 0)==1 and str(row["health_status"] or "").upper() not in {"FAILED","RETIRED","UNAVAILABLE"}

    for db_attempt in range(3):
        con=None
        try:
            con=_connect()
            row=con.execute("SELECT model_id,fallback_model_id FROM council_model_assignments WHERE agent_name=?",(role,)).fetchone()
            fallback=str(row["fallback_model_id"] or "").strip() if row else ""
            if failed_model:
                con.execute("UPDATE llm_model_catalog SET health_status='FAILED',last_error=?,last_probed_at=? WHERE model_id=?",(str(error or "runtime failure")[:400],now,failed_model))
                _quarantine(con,failed_model,role,error or "runtime failure")
            candidates=[]
            if _eligible(con,fallback): candidates.append(fallback)
            # Use only models already proven HEALTHY by the current catalogue.
            for rr in con.execute("SELECT model_id FROM llm_model_catalog WHERE available=1 AND chat_capable=1 AND health_status='HEALTHY' ORDER BY last_probed_at DESC").fetchall():
                mid=str(rr[0] or "")
                if mid not in candidates and _eligible(con,mid): candidates.append(mid)
            if candidates:
                chosen=candidates[0]; next_fb=candidates[1] if len(candidates)>1 else ""
                con.execute("UPDATE council_model_assignments SET model_id=?,fallback_model_id=?,health_status='HEALTHY',assignment_reason=?,assigned_at=? WHERE agent_name=?",(chosen,next_fb,"runtime failover to catalogue-confirmed healthy model",now,role))
                key=CONFIG_KEYS.get(role)
                if key: con.execute("INSERT INTO system_config(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",(key,chosen))
                con.execute("INSERT INTO council_model_assignment_history(agent_name,old_model_id,new_model_id,reason,changed_at,catalogue_version) VALUES(?,?,?,?,?,?)",(role,failed_model,chosen,str(error or "runtime failure")[:300],now,"runtime-failover"))
                con.commit(); return chosen
            con.commit()
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or db_attempt>=2: raise
            time.sleep(0.35*(db_attempt+1))
        finally:
            if con is not None:
                try: con.close()
                except Exception: pass

    if refresh:
        # Provider catalogue refresh/probing occurs after the short DB write
        # transaction is closed, avoiding a network call while holding SQLite.
        try:
            scan_and_align(probe=True)
            candidate=get_assignment(role, "")
            if candidate and candidate != failed_model and not is_quarantined(candidate):
                return candidate
        except Exception:
            return ""
    return ""

def get_assignment(role:str, default:str="")->str:
    role=role.upper()
    try:
        con=_connect(); row=con.execute("SELECT model_id FROM council_model_assignments WHERE agent_name=? AND health_status='HEALTHY'",(role,)).fetchone(); con.close()
        if row and row[0]: return str(row[0])
    except Exception: pass
    return os.getenv(CONFIG_KEYS.get(role,""),default) or default

def get_assignments()->Dict[str,dict]:
    try:
        con=_connect(); rows=con.execute("SELECT * FROM council_model_assignments ORDER BY agent_name").fetchall(); con.close(); return {r['agent_name']:dict(r) for r in rows}
    except Exception: return {}

if __name__=="__main__": print(json.dumps(scan_and_align(probe=True),indent=2))
