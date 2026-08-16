import sqlite3, os, datetime, time
DB="sentinuity_matrix.db"
REQ_FILES=[
    "launch/Launch_MAY22_PAPER_ONLY.bat","launch/Restart_Sentinuity.bat","launch/Shutdown_Sentinuity.bat","launch/Stop_All.bat","launch/Watchdog_Sentinuity.bat",
    "launch/prelaunch.py","launch/preflight_verifier.py","launch/set_config.py","launch/verify_state.py","launch/CLEAN_MAY22_LAYOUT.py","launch/CHECK_MAY22_BOOT_HEALTH.py",
    "launch/FIX_MAY22_RUNTIME_SCHEMA.py",
    "launch/ROTATE_MAY22_LOGS.py",
    "launch/Launch_Sentinuity.bat","launch/forge_genesis_seed.py",
    "core/schema.py","core/sovereign_identity.py","docs/SENTINUITY_SOVEREIGN_DOCTRINE.md",
    "services/execution_engine.py","services/market_intelligence.py","services/neural_supervisor.py","services/ws_price_oracle.py","services/freshness_enforcer.py","services/rolling_eviction.py","services/pump_monitor.py"
]
REQ_KEYS=[
    "TRADING_MODE","PAPER_TRADING_ENABLED","LIVE_TRADING_ENABLED","LIVE_MONEY_MODE","LIVE_ARMED","EXECUTION_ARMED",
    "PAPER_MAX_OPEN_POSITIONS","LIVE_MAX_OPEN_POSITIONS","EXECUTOR_MAX_OPEN_POSITIONS",
    "PAPER_CONFIDENCE_FLOOR","PAPER_SUPERVISOR_CONF_FLOOR","SUPERVISOR_MIN_MINT_CONFIDENCE",
    "HYBRID_CLUSTER_STRICT_ADMISSION","MOMENTUM_GATE_SHADOW_ONLY","MOMENTUM_GATE_ENABLED","SUPERVISOR_REQUIRE_POSITIVE_MTM",
    "EXECUTOR_MAX_PRICE_AGE_SEC","EXECUTOR_PHASE_A_MAX_PRICE_AGE","INTEL_PRICE_MAX_AGE_SEC",
    "EXECUTOR_MAX_SIGNAL_AGE_SEC","EXECUTOR_PHASE_A_MAX_SIGNAL_AGE","SUPERVISOR_MAX_SIGNAL_AGE_SEC"
]
print("="*90)
print("MAY22 PAPER PRELAUNCH VERIFY")
print("now:", datetime.datetime.now())
print("cwd:", os.getcwd())
print("="*90)
print("\nFILES:")
missing=[]
for f in REQ_FILES:
    ok=os.path.exists(f)
    print(f"{f:35} {'OK' if ok else 'MISSING'}")
    if not ok: missing.append(f)
if not os.path.exists(DB):
    print("\nDB MISSING:", DB)
    raise SystemExit(2)
conn=sqlite3.connect(DB)
conn.row_factory=sqlite3.Row
c=conn.cursor()
print("\nCONFIG:")
for k in REQ_KEYS:
    r=c.execute("SELECT value FROM system_config WHERE key=?",(k,)).fetchone()
    print(f"{k:42} = {r['value'] if r else '(missing)'}")
print("\nPAPER POSITIONS LAST 60M:")
try:
    cutoff=time.time()-3600
    r=c.execute("""
    SELECT COUNT(*) n,
           SUM(CASE WHEN COALESCE(peak_pnl_pct,0)>=1 THEN 1 ELSE 0 END) peak_ge_1,
           SUM(CASE WHEN COALESCE(peak_pnl_pct,0)>=5 THEN 1 ELSE 0 END) peak_ge_5,
           SUM(CASE WHEN COALESCE(peak_pnl_pct,0)>=10 THEN 1 ELSE 0 END) peak_ge_10,
           ROUND(SUM(COALESCE(realized_pnl_usd,0)),4) net_pnl
    FROM paper_positions WHERE opened_at>=?
    """,(cutoff,)).fetchone()
    print(dict(r))
except Exception as e:
    print("paper_positions check skipped:", e)
print("\nHEARTBEATS:")
for t in ["service_heartbeats","system_heartbeat"]:
    try:
        cols=[x[1] for x in c.execute(f"PRAGMA table_info({t})").fetchall()]
        if not cols: continue
        svc="service_name" if "service_name" in cols else "service"
        ts="last_seen" if "last_seen" in cols else ("last_pulse" if "last_pulse" in cols else None)
        if not ts: continue
        for r in c.execute(f"SELECT {svc} svc,{ts} ts FROM {t} ORDER BY {svc}").fetchall():
            print(dict(r))
        break
    except Exception as e:
        pass
conn.close()

print("\nROOT LAYOUT CHECK:")
allowed_root={"sentinuity_matrix.db","sentinuity_intelligence.db"}
root_dupes=[]
for name in ["schema.py","prelaunch.py","preflight_verifier.py","set_config.py","verify_state.py","launch_config.py","forge_genesis_seed.py","SENTINUITY_SOVEREIGN_DOCTRINE.md","Launch_Sentinuity.bat","Launch_MAY22_PAPER_ONLY.bat","Restart_Sentinuity.bat","Shutdown_Sentinuity.bat","Stop_All.bat","Watchdog_Sentinuity.bat"]:
    if os.path.exists(name): root_dupes.append(name)
print("root duplicate launch/core files:", root_dupes if root_dupes else "none")

print("\nVERIFY COMPLETE. Missing files must be fixed before launch:", missing if missing else "none")
