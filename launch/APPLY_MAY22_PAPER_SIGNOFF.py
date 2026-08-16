import sqlite3, os, time

DB = "sentinuity_matrix.db"
if not os.path.exists(DB):
    raise SystemExit(f"DB not found: {DB}. Run from trading-bot root.")

conn = sqlite3.connect(DB)
cur = conn.cursor()
cur.execute("""
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT,
    description TEXT,
    updated_at REAL
)
""")

settings = {
    "TRADING_MODE": ("paper", "May22 paper-only proof"),
    "PAPER_TRADING_ENABLED": ("1", "Paper trading enabled"),
    "LIVE_TRADING_ENABLED": ("0", "Live trading disabled"),
    "LIVE_MONEY_MODE": ("0", "Live money disabled"),
    "LIVE_ARMED": ("0", "Live armed disabled"),
    "EXECUTION_ARMED": ("1", "Allow paper executor"),

    "PAPER_MAX_OPEN_POSITIONS": ("3", "May22 paper max opens"),
    "LIVE_MAX_OPEN_POSITIONS": ("0", "No live opens"),
    "EXECUTOR_MAX_OPEN_POSITIONS": ("3", "Legacy fallback aligns with paper max"),
    "PAPER_CONFIDENCE_FLOOR": ("0.50", "Paper confidence floor"),
    "PAPER_SUPERVISOR_CONF_FLOOR": ("0.50", "Paper supervisor floor"),
    "SUPERVISOR_MIN_MINT_CONFIDENCE": ("0.65", "May22 supervisor floor"),

    "MOMENTUM_GATE_SHADOW_ONLY": ("1", "Momentum observes but does not block"),
    "MOMENTUM_GATE_ENABLED": ("0", "Keep May-style paper breadth"),
    "SUPERVISOR_REQUIRE_POSITIVE_MTM": ("0", "Do not require positive MTM pre-open"),
    "HYBRID_CLUSTER_STRICT_ADMISSION": ("0", "Allow May22 paper breadth"),
    "PREGRAD_CURVE_ONLY_MAX_TOKEN_AGE_SEC": ("600", "Pre-grad launch lane max token age"),

    # Exact current audit fix: candidates are qualified/priced/tradeable, but supervisor had no latches.
    # Keep this diagnostic narrow: price window + paper direct-latch supervisor only.
    "SUPERVISOR_MAX_PRICE_AGE_SEC": ("180", "Supervisor phase-A price freshness window"),
    "SUPERVISOR_PRICE_MAX_AGE_SECONDS": ("180", "Legacy supervisor price freshness key kept aligned"),
    "EXECUTOR_MAX_PRICE_AGE_SEC": ("180", "Executor price freshness for May22 proof"),
    "EXECUTOR_PHASE_A_MAX_PRICE_AGE": ("180", "Executor Phase-A price age"),
    "INTEL_PRICE_MAX_AGE_SEC": ("180", "Market-intel price age alignment"),
    "ORACLE_LIVENESS_GATE_SEC": ("300", "Oracle liveness gate"),

    # Give the resurrected May22 pipeline enough breathing room; do not make this wider than already tested.
    "SUPERVISOR_MAX_SIGNAL_AGE_SEC": ("1800", "Supervisor signal age for May22 proof"),
    "SUPERVISOR_PHASE_A_SIGNAL_AGE_SEC": ("1800", "Supervisor Phase-A signal age for May22 proof"),
    "SUPERVISOR_MAX_DISCOVERY_AGE_SEC": ("1800", "Discovery-age guard"),
    "EXECUTOR_MAX_SIGNAL_AGE_SEC": ("1800", "Executor signal age"),
    "EXECUTOR_PHASE_A_MAX_SIGNAL_AGE": ("1800", "Executor Phase-A signal age"),
    "SIGNAL_TIER1_MAX_AGE_SEC": ("900", "May22 launch lane compatibility"),
    "SUPERVISOR_FRESHNESS_FLOOR": ("0.60", "Avoid vetoing 60-150s May22 pipeline rows"),

    "MAY22_PAPER_DIRECT_LATCH_VISIBLE": ("1", "Paper-only direct latch supervisor hotfix active"),
    "SMART_MONEY_MANDATORY": ("0", "Do not require smart money confirmation in May22 proof"),
    "PAPER_SLIPPAGE_ENTRY_PCT": ("0.0", "No added entry slippage during donor proof"),
    "PAPER_SLIPPAGE_EXIT_PCT": ("0.0", "No added exit slippage during donor proof"),
}

now = time.time()
for k, (v, desc) in settings.items():
    cur.execute("""
    INSERT INTO system_config(key,value,description,updated_at)
    VALUES(?,?,?,?)
    ON CONFLICT(key) DO UPDATE SET
        value=excluded.value,
        description=excluded.description,
        updated_at=excluded.updated_at
    """, (k, str(v), desc, now))

conn.commit()
conn.close()

print("MAY22 PAPER SIGNOFF APPLIED — SUPERVISOR LATCH HOTFIX")
print("DB:", DB)
for k, (v, _) in settings.items():
    print(f"{k:42} = {v}")
