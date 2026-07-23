"""
ui/sovereign_world_component.py
================================
STATE BRIDGE — read-only. The world lives in the browser; Python sends only data.

Two-slot architecture (unchanged contract):
  world_slot  — st.empty(), world HTML injected ONCE, never replaced (iframe persists)
  update_slot — st.empty(), tiny state script only (zero height), written on reruns

Every DB query is individually guarded. A missing table/column/config key returns a
safe empty field and appends a note to state["debug"] — it never kills the payload.
NO write statements. Opens DB read-only.
"""
from __future__ import annotations
import time, json, hashlib, sqlite3, re
import streamlit as st
import streamlit.components.v1 as components
from pathlib import Path
try:
    from token_display import display_for_row, short_mint
except Exception:
    def short_mint(m): return (str(m)[:4]+"\u2026"+str(m)[-4:]) if m and len(str(m))>8 else (m or "?")
    def display_for_row(r):
        d=dict(r) if hasattr(r,"keys") else (r or {})
        for k in ("symbol","token_name","metadata_name"):
            v=d.get(k)
            if v and str(v).lower() not in ("n/a","none","unknown",""): return str(v)
        return short_mint(d.get("mint") or d.get("mint_address"))

# SIGNOFF_WORLD_SINGLE_SOURCE_20260613:
# Support both layouts used in handoff zips:
#   repo/ui/sovereign_world_component.py -> root is parent of ui/
#   repo/sovereign_world_component.py    -> root is current directory
_HERE            = Path(__file__).resolve().parent
ROOT             = _HERE.parent if _HERE.name.lower() == "ui" else _HERE
DB_PATH          = ROOT / "sentinuity_matrix.db"
_WORLD_HTML_PATH = _HERE / "sovereign_world.html"

STATE_VERSION      = 2

# ── NARRATIVE ADAPTER — real backend events -> cinematic agent bubbles ────────
# Read-only. No fabrication. Every bubble derives from a real cognition_log row.
# Raw tel stays separate (developer/glassbox); these are the World Engine bubbles.
_NARR_AGENT = {
    "QUALIFIER": ("POLARIS", "SCORE",   "#39d6ff"),
    "SUPERVISOR":("POLARIS", "LATCH",   "#39d6ff"),
    "CORTEX":    ("POLARIS", "SCORE",   "#39d6ff"),
    "EXECUTOR":  ("IVARIS",  "EXECUTE", "#ffb24a"),
    "ORACLE":    ("AXON",    "PRICE",   "#aa5aff"),
    "PRICE_ENRICHER":("AXON","PRICE",   "#aa5aff"),
    "SCOUT":     ("RHIZA",   "DISCOVERY","#1ef0a6"),
    "PUMP_MONITOR":("RHIZA", "DISCOVERY","#1ef0a6"),
    "GUARDIAN":  ("SYSTEM",  "HEAL",    "#ffd23f"),
    "HEALTH":    ("SYSTEM",  "HEAL",    "#ffd23f"),
    "POLARIS":   ("POLARIS", "SCORE",   "#39d6ff"),
    "RUNNER_DUD":("NUGGET",  "DEFEND",  "#ffd23f"),
}

def _mask_intent(stage, raw):
    """Convert a raw cognition line into safe cinematic narrative. No SQL, no keys, no traces."""
    r = (raw or "").upper()
    if "VETO_SIGNAL_TOO_OLD" in r or "SIGNAL_TOO_OLD" in r:
        return ("warning","VETO","Signal age exceeded safety tolerance. Fork released.")
    if "STALE_SIGNAL" in r or "STALE_BLOCK" in r or "EXECUTION_STALE" in r:
        return ("warning","STALE_SERVICE","Price truth went quiet. Execution stays sealed until the mark returns.")
    if "MARKET CAP BELOW" in r or "MCAP" in r:
        return ("info","VETO","Candidate too thin. Held below the market-cap floor.")
    if "APPROVED" in r and "EXECUTION" in r:
        return ("success","LATCH","All gates cleared. Seal armed for execution.")
    if "RUNNER HARVESTED" in r or "HARVESTED" in r:
        return ("success","EXECUTION","Runner banked. Value absorbed into the core.")
    if "CUT_RECOMMENDATION" in r or "RUNNER_DUD" in r:
        return ("info","DEFEND","Momentum stalled. Marking the fork for release.")
    if "MTM" in r or "REFRESHED" in r or "PRICE LAYER" in r:
        return ("info","PRICE_REFRESH","Oracle refreshed the mark. Value path is measurable.")
    if "ZERO_HEARTBEAT" in r or "RESTARTED" in r:
        return ("critical","RECOVERY","A limb went dark. Guardian restarted the service.")
    if "MAX_HOLD" in r:
        return ("info","EXECUTION","Hold window elapsed. Position closed on time discipline.")
    # generic fallback — still real, just trimmed and de-jargoned
    txt = (raw or "")[:70]
    return ("info","INFO", txt)

def build_world_narrative_events(tel_rows):
    """tel_rows: list of {'stage','message'} dicts already pulled read-only.
    Returns deduped cinematic bubbles. Pure transform — no DB, no fabrication."""
    out, seen = [], set()
    import time as _t
    bucket = int(_t.time() // 8)  # 8s dedupe window
    for row in tel_rows:
        stage = str(row.get("stage") or "").upper()
        msg   = str(row.get("message") or "")
        agent, phase, col = _NARR_AGENT.get(stage, ("SYSTEM","INFO","#9aa"))
        sev, etype, narr = _mask_intent(stage, msg)
        h = hash((agent, etype, narr[:24], bucket))
        if h in seen:
            continue
        seen.add(h)
        out.append({
            "agent": agent, "phase": phase, "severity": sev,
            "event_type": etype, "color": col, "message": narr,
            "source": "derived_from_backend_event",
        })
        if len(out) >= 12:
            break
    return out

_MIN_PUSH_INTERVAL = 0.5
HEARTBEAT_ALIVE_SEC = 45   # configurable: tower considered alive if pulse younger than this

CORE_SERVICES = ["pump_monitor","ingest_pipeline","market_intelligence",
                 "ws_price_oracle","neural_supervisor","execution_engine"]

# ── HTML cache ────────────────────────────────────────────────────────────────
_HTML_CACHE = ""; _HTML_MTIME = 0.0
def _load_world_html() -> str:
    global _HTML_CACHE, _HTML_MTIME
    try:
        m = _WORLD_HTML_PATH.stat().st_mtime
        if m != _HTML_MTIME or not _HTML_CACHE:
            _HTML_CACHE = _WORLD_HTML_PATH.read_text(encoding="utf-8")
            _HTML_MTIME = m
    except Exception:
        pass
    return _HTML_CACHE

# ── read-only connection + schema helpers ─────────────────────────────────────
def _ro_connect():
    # STRICT read-only. No read-write fallback — if mode=ro cannot open, we raise
    # and the caller records db_open_failed. We never open a connection that could
    # create or write to the database file.
    c = sqlite3.connect(f"file:{DB_PATH}?mode=ro", uri=True, timeout=2)
    c.execute("PRAGMA query_only=ON")
    c.execute("PRAGMA busy_timeout=1000")
    c.row_factory = sqlite3.Row
    return c

def _has_table(c, t):
    try: return c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (t,)).fetchone() is not None
    except Exception: return False
def _cols(c, t):
    try: return {r[1] for r in c.execute(f"PRAGMA table_info({t})").fetchall()}
    except Exception: return set()

# ── state fetch ───────────────────────────────────────────────────────────────
def _fetch_state() -> dict:
    debug = []
    services = {s: {"alive": False, "age": 999, "note": ""} for s in CORE_SERVICES}
    funnel = {k: 0 for k in ["raw_recent","inserted_recent","validated_recent","priced",
                             "qualified","latched","vetoed_recent","open_positions","closed_recent",
                             "raw_total","priced_total","vetoed_total","latched_total",
                             "polaris_pending","open_priced","open_unpriced"]}
    wallet = {k: 0.0 for k in ["paper_equity","paper_cash","realized_pnl","unrealized_pnl","live_wallet","live_start"]}
    positions = []
    tel = []
    _narrative = []
    copytrade = {"scanner_age": None, "scanned": 0, "matched": 0, "no_elite": 0, "boosted": 0, "mode": "shadow"}
    cruc = "IDLE"; conf_floor = "?"; oracle_state = "?"; liberation_ready = False

    def guard(label, fn):
        try: return fn()
        except Exception as e: debug.append(f"{label}:{type(e).__name__}"); return None

    c = guard("db", _ro_connect)
    if c is None:
        _off = {a: {"active": False, "state": "OFFLINE", "msg": "Database unavailable",
                    "task": "", "tool": ""} for a in
                ["SCOUT","RESOLVER","ORACLE","CORTEX","EXECUTOR","COPY","POLARIS"]}
        return {"version":STATE_VERSION,"services":services,"funnel":funnel,"wallet":wallet,
                "positions":positions,"cruc":cruc,"conf_floor":conf_floor,"oracle_state":oracle_state,
                "liberation_ready":liberation_ready,"tel":[],"narrative":[],"copytrade":{"scanner_age":None,"scanned":0,"matched":0,"no_elite":0,"boosted":0,"mode":"shadow"},
                "agents":_off,"commentary":["Database unavailable — bridge offline"],"ticker":[],
                "debug":["db_open_failed"],"ts":int(time.time())}
    now = time.time()

    def cfg(k, d=None):
        return guard("cfg:"+k, lambda: (lambda r: r[0] if r else d)(
            c.execute("SELECT value FROM system_config WHERE key=?", (k,)).fetchone())) or d

    # heartbeats
    if _has_table(c, "system_heartbeat"):
        hc = _cols(c, "system_heartbeat")
        tcol = "last_pulse" if "last_pulse" in hc else ("last_seen" if "last_seen" in hc else None)
        for svc in CORE_SERVICES:
            row = guard("hb:"+svc, lambda svc=svc: c.execute(
                "SELECT * FROM system_heartbeat WHERE service_name=?", (svc,)).fetchone())
            if row:
                age = (now - float(row[tcol])) if tcol and row[tcol] else 999
                services[svc] = {"alive": age < HEARTBEAT_ALIVE_SEC, "age": round(age,1),
                                 "note": (str(row["note"])[:60] if "note" in hc and row["note"] else "")}
    else:
        debug.append("no_system_heartbeat")

    # funnel
    def cnt(table, where="1=1", *p):
        if not _has_table(c, table): debug.append("no_"+table); return 0
        return guard("cnt:"+table, lambda: c.execute(f"SELECT COUNT(*) FROM {table} WHERE {where}", p).fetchone()[0]) or 0

    ms = _cols(c, "market_snapshots")
    # schema-adaptive qualified predicate: only reference columns that exist
    _qparts = []
    if "candidate_state" in ms: _qparts.append("candidate_state='qualified'")
    if "quality_status" in ms:  _qparts.append("quality_status='qualified'")
    if _qparts:
        qual = "(" + " OR ".join(_qparts) + ")"
    else:
        qual = None
        debug.append("no_qualified_columns(candidate_state/quality_status absent)")
    if _has_table(c, "raw_dna"):
        rt = "first_seen_at" if "first_seen_at" in _cols(c,"raw_dna") else "created_at"
        funnel["raw_recent"]      = cnt("raw_dna", f"{rt} > ?", now-120)
        if "processed_state" in _cols(c,"raw_dna"):
            funnel["inserted_recent"] = cnt("raw_dna", f"processed_state=1 AND {rt} > ?", now-120)
    if ms:
        mt = "created_at" if "created_at" in ms else "first_seen_at"
        funnel["validated_recent"] = cnt("market_snapshots", f"{mt} > ?", now-120)
        if qual:
            funnel["priced"]    = cnt("market_snapshots", qual+" AND price_status='priced'") if "price_status" in ms else 0
            funnel["qualified"] = cnt("market_snapshots", qual)
        funnel["latched"]   = cnt("market_snapshots", "latched=1") if "latched" in ms else 0
        if "candidate_state" in ms:
            funnel["vetoed_recent"] = cnt("market_snapshots", f"candidate_state='vetoed' AND {mt} > ?", now-120)
        # ── monotonic counters for event deltas (Point 4) ──────────────────────
        # Rolling windows can miss events when rows age out; use stable totals + max id.
        funnel["raw_total"]    = cnt("raw_dna") if _has_table(c,"raw_dna") else 0
        funnel["priced_total"] = cnt("market_snapshots", qual+" AND price_status='priced'") if (qual and "price_status" in ms) else 0
        funnel["vetoed_total"] = cnt("market_snapshots", "candidate_state='vetoed'") if "candidate_state" in ms else 0
        funnel["latched_total"]= cnt("market_snapshots", "latched=1") if "latched" in ms else 0

    # positions + wallet
    if _has_table(c, "paper_positions"):
        pc = _cols(c, "paper_positions")
        ot = "opened_at" if "opened_at" in pc else "created_at"
        funnel["open_positions"] = cnt("paper_positions", "UPPER(status)='OPEN'")
        ct = "closed_at" if "closed_at" in pc else None
        if ct: funnel["closed_recent"] = cnt("paper_positions", f"{ct} > ?", now-300)
        rows = guard("positions", lambda: c.execute(
            f"SELECT * FROM paper_positions ORDER BY COALESCE({ct or ot},{ot}) DESC LIMIT 8").fetchall()) or []
        for r in rows:
            ep = float(r["entry_price"] or 0) if "entry_price" in pc else 0
            xp = float(r["exit_price"] or ep) if "exit_price" in pc else ep
            pct = round(((xp-ep)/ep*100) if ep>0 else 0, 1)
            _disp = display_for_row({
                "symbol": (r["symbol"] if "symbol" in pc else ""),
                "token_name": (r["token_name"] if "token_name" in pc else ""),
                "mint_address": (r["mint_address"] if "mint_address" in pc else ""),
            })
            positions.append({
                "token": _disp[:12],
                "mint":  (str(r["mint_address"])[:8] if "mint_address" in pc and r["mint_address"] else ""),
                "pct": pct,
                "status": (str(r["status"]) if "status" in pc else ""),
                "opened_at": (float(r[ot]) if r[ot] else 0),
                "closed_at": (float(r[ct]) if ct and r[ct] else 0),
            })
        realized = guard("realized", lambda: c.execute(
            "SELECT COALESCE(SUM(realized_pnl_usd),0) FROM paper_positions WHERE closed_at IS NOT NULL").fetchone()[0]) or 0
        wallet["realized_pnl"] = round(float(realized),2)

    if _has_table(c, "paper_wallet"):
        wc = _cols(c, "paper_wallet")
        w = guard("wallet", lambda: c.execute("SELECT * FROM paper_wallet LIMIT 1").fetchone())
        if w:
            if "equity" in wc:       wallet["paper_equity"] = round(float(w["equity"] or 0),2)
            if "cash_balance" in wc: wallet["paper_cash"]   = round(float(w["cash_balance"] or 0),2)

    wallet["live_wallet"] = float(cfg("LIVE_WALLET_USD", 0) or 0)
    wallet["live_start"]  = float(cfg("LIVE_WALLET_START_USD", 0) or 0)

    # ── copytrade glassbox payload (read-only, additive) ──────────────────
    copytrade = {"scanner_age": None, "scanned": 0, "matched": 0,
                 "no_elite": 0, "boosted": 0, "mode": "shadow"}
    if "copytrade_scanned_at" in _cols(c, "market_snapshots"):
        copytrade["scanned"]  = guard("ct_scanned", lambda: c.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE copytrade_scanned_at IS NOT NULL").fetchone()[0]) or 0
        copytrade["matched"]  = guard("ct_matched", lambda: c.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE copytrade_signal_state='scored' AND copytrade_matched_wallets>0").fetchone()[0]) or 0
        copytrade["no_elite"] = guard("ct_noelite", lambda: c.execute(
            "SELECT COUNT(*) FROM market_snapshots WHERE copytrade_reason='NO_ELITE_WALLETS'").fetchone()[0]) or 0
    if _has_table(c, "system_heartbeat"):
        _hc=_cols(c,"system_heartbeat"); _tc="last_pulse" if "last_pulse" in _hc else None
        _r=guard("ct_hb", lambda: c.execute("SELECT * FROM system_heartbeat WHERE service_name='copytrade_shadow_scanner'").fetchone())
        if _r and _tc and _r[_tc]:
            copytrade["scanner_age"]=round(now-float(_r[_tc]),1)
            note = str(_r["note"] if "note" in _hc and _r["note"] else "")
            status = str(_r["status"] if "status" in _hc and _r["status"] else "")
            copytrade["status"] = status
            copytrade["note"] = note[:220]
            def _rx_int(name, pat):
                m = re.search(pat, note)
                if m:
                    try: copytrade[name] = int(float(m.group(1)))
                    except Exception: pass
            _rx_int("scanned_last_cycle", r"scanned_last_cycle=(\d+)")
            _rx_int("matched_last_cycle", r"matched_last_cycle=(\d+)")
            _rx_int("signals_written", r"signals_written=(\d+)")
            _rx_int("no_elite", r"no_elite_wallets_count=(\d+)")
            m = re.search(r"last_error='([^']*)'", note)
            if m: copytrade["last_error"] = m.group(1)[:160]
            if ("scorer_import_failed" in note.lower()) or ("no module named" in note.lower()):
                copytrade["broken"] = True
                copytrade["mode"] = "broken"

    # cognition telemetry → council glow (guarded, read-only)
    tel = []
    if _has_table(c, "cognition_log"):
        COL = {"EXECUTOR":"#1ef0a6","SUPERVISOR":"#39d6ff","POLARIS":"#39d6ff",
               "IVARIS":"#ffb24a","GUARDIAN":"#ffd23f","GROK":"#46b6ff","AXON":"#aa5aff"}
        rows = guard("cognition_log", lambda: c.execute(
            "SELECT stage, message FROM cognition_log ORDER BY timestamp DESC LIMIT 8").fetchall()) or []
        _narr_src = []
        for r in rows:
            stg = str(r["stage"] or "").upper()
            tel.append({"text": f"[{stg}] " + str(r["message"] or "")[:55], "col": COL.get(stg,"#A14BFF")})
            _narr_src.append({"stage": stg, "message": str(r["message"] or "")})
        _narrative = build_world_narrative_events(_narr_src)
    else:
        debug.append("no_cognition_log")

    # crucible / config flags
    if _has_table(c, "polaris_proposals"):
        if guard("polaris", lambda: c.execute(
            "SELECT 1 FROM polaris_proposals WHERE status IN ('debating','open') LIMIT 1").fetchone()):
            cruc = "DISTILLING"
    conf_floor   = cfg("SUPERVISOR_MIN_MINT_CONFIDENCE", "?")
    oracle_state = cfg("WS_ORACLE_STATE", "?")
    liberation_ready = str(cfg("TRADING_MODE","paper") or "paper").lower() in ("live","dual")

    # polaris proposals awaiting / in flight (read-only)
    if _has_table(c, "polaris_proposals"):
        funnel["polaris_pending"] = cnt("polaris_proposals", "status IN ('open','debating','approved')")

    # ── unrealized PnL on OPEN positions (read-only, schema-adaptive) ──────────
    # Never guesses a number it can't justify: if no usable mark + size columns
    # are present, unrealized_pnl stays 0.0 and a debug note is recorded.
    if _has_table(c, "paper_positions"):
        pc       = _cols(c, "paper_positions")
        mark_col = next((x for x in ("current_price","mark_price","last_price","mark","price") if x in pc), None)
        if   "position_size_usd" in pc: size_mode = "usd"
        elif "qty_tokens"        in pc: size_mode = "qty"
        elif "position_size"     in pc: size_mode = "usd"   # legacy alias
        else:                            size_mode = None
        urows = guard("urows", lambda: c.execute(
            "SELECT * FROM paper_positions WHERE UPPER(status)='OPEN'").fetchall()) or []
        upnl = 0.0; priced = 0
        for r in urows:
            ep = float(r["entry_price"] or 0) if "entry_price" in pc else 0.0
            mk = float(r[mark_col] or 0) if (mark_col and r[mark_col]) else 0.0
            if not mk and ms and "price" in ms and "mint_address" in pc and r["mint_address"]:
                mt2 = "created_at" if "created_at" in ms else "first_seen_at"
                mk = guard("mk", lambda mint=r["mint_address"]: (lambda row: float(row[0]) if row and row[0] else 0.0)(
                    c.execute(f"SELECT price FROM market_snapshots WHERE mint_address=? ORDER BY {mt2} DESC LIMIT 1",
                              (mint,)).fetchone())) or 0.0
            if ep > 0 and mk > 0 and size_mode:
                priced += 1
                if size_mode == "usd":
                    cost = float((r["position_size_usd"] if "position_size_usd" in pc else
                                  r["position_size"] if "position_size" in pc else 0) or 0)
                    upnl += cost * ((mk - ep) / ep)
                else:  # qty
                    upnl += (mk - ep) * float(r["qty_tokens"] or 0)
        wallet["unrealized_pnl"] = round(upnl, 2)
        funnel["open_priced"]    = priced
        funnel["open_unpriced"]  = max(0, int(funnel.get("open_positions", 0)) - int(priced))
        if urows and (priced == 0 or size_mode is None):
            debug.append("unrealized_unavailable(no usable mark/size cols)")

    # ── per-agent honest state (derived only from the real signals above) ──────
    live_blocked = not liberation_ready
    def _ag(active, state, msg, task, tool=""):
        return {"active": bool(active), "state": state, "msg": msg, "task": task, "tool": tool}
    s = services
    o_stale = (funnel["priced"] == 0) or (str(oracle_state).lower() in ("stale", "?", "none", ""))
    agents = {
        "SCOUT":    _ag(funnel["raw_recent"] > 0 or s["pump_monitor"]["alive"],
                        "SWEEPING" if funnel["raw_recent"] > 0 else ("IDLE" if s["pump_monitor"]["alive"] else "DOWN"),
                        f"{funnel['raw_recent']} new candidates (2m)" if funnel["raw_recent"] > 0 else "Awaiting fresh DNA",
                        "Sweeping pump.fun", "scanner"),
        "RESOLVER": _ag(funnel["validated_recent"] > 0 or s["ingest_pipeline"]["alive"],
                        "RESOLVING" if funnel["validated_recent"] > 0 else "IDLE",
                        f"{funnel['validated_recent']} validated (2m)" if funnel["validated_recent"] > 0 else "Awaiting validated rows",
                        (s["ingest_pipeline"]["note"] or "Resolving mints via RPC")[:42], "resolver"),
        "ORACLE":   _ag(s["ws_price_oracle"]["alive"] and not o_stale,
                        "STALE" if (o_stale and s["ws_price_oracle"]["alive"]) else ("PRICING" if funnel["priced"] > 0 else "IDLE"),
                        "Oracle stale" if o_stale else f"{funnel['priced']} priced candidates",
                        "Reconnecting price feed" if o_stale else "Streaming live prices", "oracle"),
        "CORTEX":   _ag(funnel["latched"] > 0 or s["neural_supervisor"]["alive"],
                        "LATCHED" if funnel["latched"] > 0 else "HOLDING",
                        f"{funnel['latched']} latched / {funnel['qualified']} qualified" if funnel["latched"] > 0 else "Supervisor holding gate",
                        f"min conf {conf_floor}", "supervisor"),
        "EXECUTOR": _ag(s["execution_engine"]["alive"],
                        "PAPER" if live_blocked else "LIVE",
                        f"{funnel['open_positions']} open · live blocked" if live_blocked else f"{funnel['open_positions']} open · LIVE armed",
                        "Paper execution only" if live_blocked else "Managing live positions", "executor"),
        "COPY":     _ag(copytrade.get("matched", 0) > 0 or copytrade.get("scanner_age") is not None or copytrade.get("broken"),
                        "BROKEN" if copytrade.get("broken") else ("SIGNAL" if copytrade.get("matched", 0) > 0 else "OBSERVING"),
                        ("Conviction scorer import failed" if copytrade.get("broken") else (f"{copytrade.get('matched',0)} wallet matches" if copytrade.get("matched", 0) > 0 else "Copy-trade observing only")),
                        (copytrade.get("last_error") or copytrade.get("note") or "Scoring smart money")[:42], "copy_scout"),
        "GUARDIAN": _ag(bool(funnel.get("open_unpriced",0) or any((not v.get("alive", True)) for v in s.values())),
                        "DEFEND" if funnel.get("open_unpriced",0) else ("REPAIR" if any((not v.get("alive", True)) for v in s.values()) else "WATCH"),
                        (f"{funnel.get('open_unpriced',0)} open without fresh mark" if funnel.get("open_unpriced",0) else "Risk shell nominal"),
                        "Containing stale positions" if funnel.get("open_unpriced",0) else "Watching service shell", "guardian"),
        "POLARIS":  _ag(True,
                        "PROPOSING" if funnel["polaris_pending"] > 0 else "STANDBY",
                        f"{funnel['polaris_pending']} proposals awaiting approval" if funnel["polaris_pending"] > 0 else "Standing by — no open proposals",
                        "Drafting improvements" if funnel["polaris_pending"] > 0 else "Monitoring system", "polaris"),
    }

    # ── commentary + ticker: real cognition events only, newest first, max 12 ──
    commentary = [t["text"] for t in tel][:12] or ["No live agent events — system idle"]
    ticker     = tel[:12]

    guard("close", c.close)
    return {"version":STATE_VERSION,"services":services,"funnel":funnel,"wallet":wallet,
            "positions":positions,"cruc":cruc,"conf_floor":conf_floor,"oracle_state":oracle_state,
            "liberation_ready":liberation_ready,"tel":tel,"narrative":_narrative,"copytrade":copytrade,
            "agents":agents,"commentary":commentary,"ticker":ticker,
            "debug":debug,"ts":int(time.time())}

# ── main render (two-slot, unchanged behaviour) ───────────────────────────────
def _world_layer_attach(state: dict) -> dict:
    """SOVEREIGN_WORLD_UPGRADE_20260723 — canonical world layer.
    Runs schema ensure + resume event + one narrative pass ONCE per Streamlit
    session, then attaches the persistent layer to every state push. All world
    writes stay inside world_* tables; trading tables are read-only."""
    try:
        from services import world_build_state as wbs
        from services import world_narrative_engine as wne
        if not st.session_state.get("_sw_world_persist_init"):
            wbs.ensure_schema()
            try:
                from launch.world_schema_migrate import migrate as _wsm
                _wsm()
            except Exception:
                pass
            wbs.append_resume_event()
            wne.run_world_pass()
            st.session_state["_sw_world_persist_init"] = True
        state["world"] = wbs.load_world_layer()
        state["world"]["ambient_active"] = wne.ambient_signals()
        state["world"]["agent_intent"] = wne.derive_agent_states()
        state["world"]["chronicle"] = wne.chronicle()
    except Exception as exc:
        state["world"] = {"error": str(exc)[:140]}
    return state


def _render_paper_ready_gate() -> None:
    """The OPEN INTELLIGENCE INSTITUTE button appears ONLY when the
    canonical DB row says paper_ready=1 — never from browser state."""
    try:
        from services.world_build_state import get_building
        b = get_building("intelligence_institute")
        if b and int(b.get("paper_ready") or 0) == 1:
            st.success("INTELLIGENCE INSTITUTE — PAPER READY FOR OPERATOR TESTING")
            if st.button("OPEN INTELLIGENCE INSTITUTE",
                         key="_sw_open_institute", type="primary"):
                st.session_state["open_intelligence_tab"] = True
                st.rerun()
    except Exception:
        pass


def render_sovereign_world(height: int = 680) -> None:
    if "_sw_world_slot" not in st.session_state:
        st.session_state["_sw_world_slot"]     = st.empty()
        st.session_state["_sw_update_slot"]    = st.empty()
        st.session_state["_sw_world_injected"] = False
        st.session_state["_sw_state_hash"]     = ""
        st.session_state["_sw_last_push"]      = 0.0

    world_slot  = st.session_state["_sw_world_slot"]
    update_slot = st.session_state["_sw_update_slot"]
    now = time.time()

    if not st.session_state["_sw_world_injected"]:
        world_html = _load_world_html()
        if not world_html:
            st.warning("sovereign_world.html not found — copy it into the ui/ folder")
            return
        state = _world_layer_attach(_fetch_state())
        init = (f"<script>window.__SW_STATE_VERSION__={STATE_VERSION};"
                f"window.__SW_STATE__={json.dumps(state)};"
                f"if(typeof applySwState==='function')applySwState(window.__SW_STATE__);</script>")
        full = world_html.replace("</body>", init+"</body>") if "</body>" in world_html else world_html+init
        with world_slot:
            components.html(full, height=height, scrolling=False)
        st.session_state["_sw_world_injected"] = True
        st.session_state["_sw_last_push"] = now
        st.session_state["_sw_state_hash"] = hashlib.md5(
            json.dumps({k:v for k,v in state.items() if k!="ts"}, sort_keys=True, default=str).encode()).hexdigest()[:16]
        _render_paper_ready_gate()
        return

    if now - st.session_state["_sw_last_push"] < _MIN_PUSH_INTERVAL:
        return
    state = _world_layer_attach(_fetch_state())
    h = hashlib.md5(json.dumps({k:v for k,v in state.items() if k!="ts"}, sort_keys=True, default=str).encode()).hexdigest()[:16]
    if h == st.session_state["_sw_state_hash"]:
        return
    upd = f"""<script>(function(){{
      var s={json.dumps(state)};
      if(s.version!=={STATE_VERSION})return;
      var payload={{type:'sw_state_update',source:'__sw_state_bridge',state:s}};
      try{{var target=null;
        Array.from(window.parent.frames||[]).some(function(f){{
          try{{if(f!==window&&typeof f.applySwState==='function'){{target=f;return true;}}}}catch(e){{}}return false;}});
        if(target)target.postMessage(payload,'*');
      }}catch(e){{}}
    }})();</script>"""
    with update_slot:
        components.html(upd, height=0, scrolling=False)
    st.session_state["_sw_state_hash"] = h
    st.session_state["_sw_last_push"]  = now
    _render_paper_ready_gate()
