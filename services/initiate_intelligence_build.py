"""
INTELLIGENCE TAB BUILD INITIATOR
─────────────────────────────────────────────────────────────────────
This script kicks off the AI council's self-directed build of the
Intelligence Tab. It:

1. Creates the required DB tables if missing
2. Injects INTELLIGENCE_BUILD proposals into polaris_proposals
3. Enables debates for one controlled session
4. Gives the agents their doctrine + context
5. Monitors progress

The agents will:
- Research copy trade wallets (GMGN/Cielo)
- Curate Telegram call channels by accuracy
- Build their own Intelligence Tab sections
- Self-test parameter changes via replay engine

Run (from the repo root): python -m services.initiate_intelligence_build
"""
import sqlite3, time, json
from pathlib import Path
from datetime import datetime

db = sqlite3.connect('sentinuity_matrix.db')
db.row_factory = sqlite3.Row
now = time.time()

print()
print("="*65)
print("  INTELLIGENCE TAB BUILD INITIATOR")
print(f"  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("="*65)

# ── STEP 1: Ensure required tables exist ─────────────────────────────────────
print("\n[1] Ensuring tables exist...")

db.execute("""
    CREATE TABLE IF NOT EXISTS intelligence_forge (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        title       TEXT,
        author      TEXT DEFAULT 'COUNCIL',
        doc_type    TEXT DEFAULT 'research',
        status      TEXT DEFAULT 'draft',
        content_md  TEXT,
        tags        TEXT,
        created_at  REAL DEFAULT (unixepoch()),
        updated_at  REAL DEFAULT (unixepoch())
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS research_queue (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        petition    TEXT,
        status      TEXT DEFAULT 'pending',
        priority    TEXT DEFAULT 'normal',
        created_at  REAL DEFAULT (unixepoch()),
        result      TEXT
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS watched_wallets (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_address TEXT UNIQUE,
        label          TEXT,
        profit_score   REAL DEFAULT 0,
        trade_count    INTEGER DEFAULT 0,
        win_rate       REAL DEFAULT 0,
        added_at       REAL DEFAULT (unixepoch()),
        last_seen      REAL,
        active         INTEGER DEFAULT 1
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS wallet_pattern_observations (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        wallet_address TEXT,
        mint_address   TEXT,
        action         TEXT,
        amount_sol     REAL,
        observed_at    REAL DEFAULT (unixepoch()),
        outcome_pct    REAL
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS telegram_channel_trust (
        id             INTEGER PRIMARY KEY AUTOINCREMENT,
        channel_id     TEXT UNIQUE,
        channel_name   TEXT,
        accuracy_score REAL DEFAULT 0,
        calls_total    INTEGER DEFAULT 0,
        calls_hit      INTEGER DEFAULT 0,
        avg_x          REAL DEFAULT 0,
        last_updated   REAL DEFAULT (unixepoch())
    )
""")

db.execute("""
    CREATE TABLE IF NOT EXISTS nim_call_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          REAL DEFAULT (unixepoch()),
        task_hash   TEXT,
        model       TEXT,
        mode        TEXT,
        reason      TEXT,
        latency_ms  REAL,
        success     INTEGER DEFAULT 1,
        output_hash TEXT
    )
""")

db.commit()
print("  Tables ready")

# ── STEP 2: Read NIM doctrine from DB ────────────────────────────────────────
print("\n[2] Loading NIM doctrine...")
doctrine_row = db.execute(
    "SELECT value FROM system_config WHERE key='NIM_DOCTRINE'"
).fetchone()
has_doctrine = bool(doctrine_row)
print(f"  NIM doctrine in DB: {'YES' if has_doctrine else 'NO — run nim_doctrine.py first'}")


# ── STEP 3: Inject INTELLIGENCE_BUILD proposals ───────────────────────────────
print("\n[3] Injecting intelligence build proposals...")

proposals = [
    {
        "type": "INTELLIGENCE_BUILD",
        "task_type": "build",
        "text": "INTELLIGENCE DELIVERY TRUTH: Build the Intelligence tab into a DB-backed council delivery surface showing task ownership, active work, blockers, persisted evidence, accepted artifacts, and causal regime telemetry. Never infer completion from model narration alone.",
        "action": "Complete and evidence the Intelligence council delivery workbench",
        "confidence": 0.95,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "task_type": "research",
        "text": "CAUSAL REGIME REVIEW: Monitor the signed-off two-success/15-minute Solana regime gate, compare armed and baseline cohorts, heavy-loss rate, sample size and execution quality. Recommendations remain shadow/research until operator approval.",
        "action": "Audit causal regime telemetry and publish an evidence-backed council review",
        "confidence": 0.95,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "text": "WALLET RESEARCH TASK: Use web search to find 10 profitable Solana pump.fun trader wallet addresses from GMGN.ai and Cielo Finance. Criteria: win rate >55%, >20 trades in 30 days, avg hold <5 minutes. Return as INSERT statements for watched_wallets table.",
        "action": "Research and populate watched_wallets with verified profitable trader addresses",
        "confidence": 0.85,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "text": "TELEGRAM CHANNEL AUDIT: Evaluate the current Telegram channel (-1002597061903 Leo_Bot1) for signal quality. It has 61 calls logged with 0 overlap with our trades. Determine if it should be replaced. Research and identify 3 better pump.fun alpha channels that post mint addresses within 60s of token launch.",
        "action": "Audit existing channel, find replacements, update telegram_channel_trust table",
        "confidence": 0.80,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "text": "PIPELINE LATENCY FIX: Current avg entry latency is 265 seconds. Pump.fun tokens peak in 60-90s. Propose specific parameter changes to reduce discovery-to-entry time to under 90s. Analyze the qualification pipeline stages and identify which gates are causing the most delay.",
        "action": "Propose parameter changes to reduce entry latency from 265s to <90s",
        "confidence": 0.90,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "text": "MAX HOLD REDUCTION: Current MAX_HOLD_SECONDS=900 (15 minutes) is too long for pump.fun tokens that die within 2-3 minutes of missing their pump. Propose and test optimal MAX_HOLD value. Analyze last 100 TIME_CUT closes to find the sweet spot.",
        "action": "Reduce MAX_HOLD_SECONDS from 900 to optimal value based on trade data analysis",
        "confidence": 0.88,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "text": "INTELLIGENCE TAB SELF-BUILD: Design and implement the copy trade watcher section of the Intelligence Tab. It should display: active watched wallets with their stats, recent observations, and a confidence score for each wallet. Read-only, uses watched_wallets and wallet_pattern_observations tables.",
        "action": "Build copy trade watcher panel in intelligence_tab.py",
        "confidence": 0.82,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "task_type": "design",
        "text": "X/OPENCLAW BUILD RESEARCH: Scrape X/Twitter for #openclaw builds, autonomous agent dashboards, and trading bot UIs. Study 20+ examples. Extract: recurring layout patterns, data panel hierarchy, operator interaction models, visual motifs used by top builders. Produce a DESIGN SPEC for the Sentinuity Intelligence Tab that reflects best-in-class autonomous agent UI patterns. Focus on: Genesis Macro-Map placement, council state visualization, live signal feeds, execution lane telemetry.",
        "action": "Produce design spec for intelligence tab based on X/openclaw build research",
        "confidence": 0.88,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "task_type": "research",
        "text": "ORACLE PRICE FRESHNESS RESEARCH: Research optimal price oracle architecture for pump.fun trading. Current setup: Birdeye at 7s polling only. Missing: Helius WebSocket, QuickNode WSS, Jupiter fallback. Find: which oracles pump.fun traders use, what latency is achievable, how to combine DexScreener free tier (unlimited) with WebSocket feeds for open positions. Produce configuration recommendations for PRICE_SOURCE_PRIORITY, poll intervals, and WebSocket subscription strategy.",
        "action": "Produce oracle architecture recommendations for system_config",
        "confidence": 0.85,
    },
    {
        "type": "INTELLIGENCE_BUILD",
        "task_type": "research",
        "text": "ENTRY MOMENTUM FILTER RESEARCH: Win rate is 12% with 131 TIME_CUT losses. Wins average +39% in 218s, losses average -8.3% in 375s. Research what entry signals distinguish winning from losing trades. Analyze: price momentum at latch time vs entry, bonding curve % at entry for wins vs losses, market cap trajectory in first 30s. Propose a momentum score gate that rejects tokens already declining at latch time.",
        "action": "Propose momentum_score gate config changes to reduce TIME_CUT losses",
        "confidence": 0.90,
    },
]

# Check existing INTELLIGENCE_BUILD proposals to avoid duplicates
existing = db.execute("""
    SELECT proposal_text FROM polaris_proposals
    WHERE proposal_type='INTELLIGENCE_BUILD' AND status='open'
""").fetchall()
existing_texts = {r['proposal_text'][:50] for r in existing}

injected = 0
for p in proposals:
    # Skip if similar proposal already exists
    if p['text'][:50] in existing_texts:
        print(f"  SKIP (exists): {p['text'][:50]}...")
        continue

    db.execute("""
        INSERT INTO polaris_proposals
        (proposal_type, proposal_text, suggested_action, confidence,
         status, created_at, seen_count)
        VALUES (?,?,?,?,'open',?,0)
    """, (p['type'], p['text'], p['action'], p['confidence'], now))
    injected += 1
    print(f"  Injected: {p['text'][:55]}...")

db.commit()
print(f"  {injected} new proposals injected")

# ── STEP 4: Enable debates for one session ────────────────────────────────────
print("\n[4] Enabling debates...")
db.execute("UPDATE system_config SET value='1' WHERE key='DEBATES_ENABLED'")
db.execute("UPDATE system_config SET value='0' WHERE key='HITL_REQUIRED'")
db.commit()
print("  DEBATES_ENABLED=1  HITL_REQUIRED=0")
print("  Debates will run automatically — monitor sovereign_governor log")
print("  To stop: python -c \"import sqlite3; db=sqlite3.connect('sentinuity_matrix.db'); db.execute(\\\"UPDATE system_config SET value='0' WHERE key='DEBATES_ENABLED'\\\"); db.commit()\"")

# ── STEP 5: Show status ───────────────────────────────────────────────────────
print("\n[5] Current state:")
open_props = db.execute(
    "SELECT COUNT(*) FROM polaris_proposals WHERE status='open'"
).fetchone()[0]
ib_props = db.execute(
    "SELECT COUNT(*) FROM polaris_proposals WHERE status='open' AND proposal_type='INTELLIGENCE_BUILD'"
).fetchone()[0]
print(f"  Open proposals total:           {open_props}")
print(f"  INTELLIGENCE_BUILD proposals:   {ib_props}")
print(f"  watched_wallets configured:     {db.execute('SELECT COUNT(*) FROM watched_wallets').fetchone()[0]}")

print()
print("="*65)
print("  BUILD INITIATED")
print()
print("  The AI council will now:")
print("  1. Sovereign governor picks up INTELLIGENCE_BUILD proposals")
print("  2. Polaris researches → IVARIS critiques → NIM arbitrates")
print("  3. Approved proposals apply to DB tables directly")
print("  4. Intelligence tab reads results and renders")
print()
print("  MONITOR PROGRESS:")
print("  type logs\\sovereign_governor.log   (debate activity)")
print("  type logs\\polaris.log              (research output)")
print("  python scout_audit2.py             (wallet/channel status)")
print()
print("  WATCH FOR IN HUB:")
print("  - watched_wallets count increasing")
print("  - telegram_channel_trust entries appearing")
print("  - Intelligence tab showing research documents")
print()
print("  STOP DEBATES WHEN DONE:")
print("  python -c \"import sqlite3; db=sqlite3.connect('sentinuity_matrix.db'); db.execute(\\\"UPDATE system_config SET value='0' WHERE key='DEBATES_ENABLED'\\\"); db.commit(); print('Debates OFF')\"")
print("="*65)


# ── STEP 6: Create operator command queue table ───────────────────────────────
print("\n[6] Creating operator command queue...")
db.execute("""
    CREATE TABLE IF NOT EXISTS operator_command_queue (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        requested_by TEXT DEFAULT 'POLARIS',
        command_text TEXT NOT NULL,
        reason       TEXT,
        risk_level   TEXT DEFAULT 'LOW',
        requires_2fa INTEGER DEFAULT 0,
        status       TEXT DEFAULT 'pending',
        result       TEXT,
        created_at   REAL DEFAULT (unixepoch()),
        executed_at  REAL
    )
""")
db.commit()
print("  operator_command_queue ready")
print("  Polaris can now request commands → you confirm in Hub → result feeds back")
