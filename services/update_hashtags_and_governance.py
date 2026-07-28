"""
update_hashtags_and_governance.py
==================================
Applies the council-consensus hashtag list and governance tier config
to system_config. Run from trading-bot root.

Hashtag list is the synthesised consensus from all 5 AIs:
Grok, ChatGPT, Polaris, Gemini, Claude — taking only where 3+ agreed.
"""
import sqlite3, time
from pathlib import Path

DB   = Path("sentinuity_matrix.db")
conn = sqlite3.connect(str(DB), timeout=10)
conn.row_factory = sqlite3.Row

configs = [

    # ── TIER 1: HIGH SIGNAL, LOW NOISE (all 5 AIs agreed) ──────────────────
    # Scrape every cycle. These are the organism's core intel feeds.
    (
        "X_SCOUT_HASHTAGS",
        ",".join([
            # Pump.fun ecosystem — ground zero
            "#pumpfun",
            "#memecoin",
            "#Solana",
            "#solanabot",
            "#SolanaAlpha",
            # Bot / builder layer — where the edge leaks
            "#OpenClaw",
            "#tradingbot",
            "#AItrading",
            "#AIagent",
            "#AlgoTrading",
            # Copy trading / alpha sharing
            "#copytrading",
            "#cryptoalpha",
            # On-chain / data layer
            "#onchain",
            "#degen",
        ])
    ),

    # ── TIER 2: BONUS TAGS (3+ AIs agreed, lower volume) ───────────────────
    # Added to a separate slower-cycle scan
    (
        "X_SCOUT_HASHTAGS_TIER2",
        ",".join([
            "#SmartMoney",
            "#DeFiBot",
            "#MemeCoinAlpha",
            "#OnChainAnalysis",
            "#SolanaDev",
            "#CryptoSentiment",
            "#Helius",
            "#Sentinuity",
        ])
    ),

    # ── GOVERNANCE: proposal confidence thresholds ──────────────────────────
    # Based on Gemini MVGS — 4 proposal types, 3 tiers
    ("GOVERNANCE_RESEARCH_CONFIDENCE",    "40"),   # any agent can kick off
    ("GOVERNANCE_BUILD_CONFIDENCE",       "65"),   # 3/4 majority
    ("GOVERNANCE_VALIDATION_CONFIDENCE",  "75"),   # 3/4 + stats
    ("GOVERNANCE_LIVE_DEPLOY_CONFIDENCE", "90"),   # unanimous + human seal

    # Intelligence tab proposals use Research threshold — not trading gates
    ("INTELLIGENCE_BUILD_CONFIDENCE",     "40"),   # low gate for UI/research
    ("INTELLIGENCE_BUILD_REQUIRES_HUMAN", "0"),    # no human seal for UI work

    # X scout scan interval
    ("X_SCOUT_INTERVAL_MINUTES",          "60"),   # tier 1 every hour
    ("X_SCOUT_TIER2_INTERVAL_MINUTES",    "360"),  # tier 2 every 6 hours
]

print("=== APPLYING COUNCIL CONSENSUS CONFIG ===\n")
for key, value in configs:
    conn.execute(
        "INSERT OR REPLACE INTO system_config (key, value) VALUES (?, ?)",
        (key, value)
    )
    if "HASHTAG" in key:
        tags = value.split(",")
        print(f"{key}: {len(tags)} tags")
        for t in tags:
            print(f"    {t}")
    else:
        print(f"{key} = {value}")
    print()

conn.commit()
conn.close()

print("=== DONE ===")
print("Tier 1 hashtags: every 60 minutes")
print("Tier 2 hashtags: every 6 hours")
print("Intelligence tab proposals: confidence >= 40 (low gate, no human seal)")
print("Live deployment: confidence >= 90 + human seal")
