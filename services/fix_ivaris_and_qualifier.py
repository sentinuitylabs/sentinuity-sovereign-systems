"""
fix_ivaris_and_qualifier.py — fix IVARIS config + diagnose why qualifier rejects everything
Run: python fix_ivaris_and_qualifier.py
"""
import sqlite3, time, os, sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
DB   = BASE / "sentinuity_matrix.db"
db   = sqlite3.connect(str(DB), timeout=10)
db.row_factory = sqlite3.Row
now  = time.time()

R="\033[0m";B="\033[1m";G="\033[92m";Y="\033[93m";RE="\033[91m";C="\033[96m";D="\033[2m";W="\033[97m"
if sys.platform=="win32": os.system("color")

print(f"\n{C}{B}  ⬡ IVARIS + QUALIFIER FIX{R}\n")

# ── 1. Check and fix IVARIS_PROVIDER ─────────────────────────────────────────
print(f"  {B}IVARIS config:{R}")
for k in ["IVARIS_PROVIDER","IVARIS_MODEL","ANTHROPIC_API_KEY"]:
    row = db.execute("SELECT value FROM system_config WHERE key=?", (k,)).fetchone()
    env = os.getenv(k,"") if "KEY" in k else None
    val = row["value"] if row else "NOT SET"
    if k == "ANTHROPIC_API_KEY":
        env_val = os.getenv(k,"").strip()
        val = f"SET (len={len(env_val)})" if env_val else "NOT IN ENV"
    print(f"  {k:<30} = {W}{val}{R}")

# Ensure IVARIS_PROVIDER=anthropic
db.execute("INSERT OR REPLACE INTO system_config(key,value) VALUES('IVARIS_PROVIDER','anthropic')")
db.execute("INSERT OR REPLACE INTO system_config(key,value) VALUES('IVARIS_MODEL','claude-haiku-4-5-20251001')")
db.commit()
print(f"  {G}✓ IVARIS_PROVIDER set to anthropic{R}")
print(f"  {G}✓ IVARIS_MODEL set to claude-haiku-4-5-20251001{R}")

# ── 2. Test Anthropic key directly ───────────────────────────────────────────
print(f"\n  {B}Testing Anthropic key:{R}")
ant_key = os.getenv("ANTHROPIC_API_KEY","").strip()
if not ant_key:
    # Try loading from .env
    env_path = BASE / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.strip().startswith("ANTHROPIC_API_KEY="):
                ant_key = line.split("=",1)[1].strip().strip('"').strip("'")
                os.environ["ANTHROPIC_API_KEY"] = ant_key
                break

if ant_key:
    import urllib.request, json
    try:
        payload = json.dumps({"model":"claude-haiku-4-5-20251001","max_tokens":5,
                              "messages":[{"role":"user","content":"hi"}]}).encode()
        req = urllib.request.Request("https://api.anthropic.com/v1/messages",
            data=payload, method="POST",
            headers={"x-api-key":ant_key,"anthropic-version":"2023-06-01",
                     "Content-Type":"application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            d = json.loads(r.read())
        print(f"  {G}✓ Anthropic key VALID — IVARIS will work directly{R}")
    except Exception as e:
        print(f"  {RE}✗ Anthropic key FAILED: {e}{R}")
        print(f"  {Y}→ IVARIS will use NIM fallback (Mistral Large 3){R}")
else:
    print(f"  {RE}✗ ANTHROPIC_API_KEY not found in env or .env{R}")
    print(f"  {Y}→ IVARIS will use NIM fallback (Mistral Large 3){R}")

# ── 3. Diagnose qualifier rejections ─────────────────────────────────────────
print(f"\n  {B}Qualifier rejection analysis (last 50 rows):{R}")
reasons = db.execute("""
    SELECT quality_reason, COUNT(*) n
    FROM market_snapshots
    WHERE quality_status IN ('rejected','vetoed','error','expired_stale')
       OR (candidate_state NOT IN ('pending','qualified','latched','mtm') 
           AND quality_reason IS NOT NULL AND quality_reason != '')
    GROUP BY quality_reason ORDER BY n DESC LIMIT 15
""").fetchall()

if reasons:
    for r in reasons:
        print(f"  {r[1]:>5}x  {r[0] or 'NO_REASON'}")
else:
    print(f"  {Y}No rejection reasons in DB — checking pending rows{R}")
    pending = db.execute("""
        SELECT quality_reason, quality_status, candidate_state, COUNT(*) n
        FROM market_snapshots
        WHERE candidate_state='pending'
        GROUP BY quality_reason, quality_status, candidate_state
        ORDER BY n DESC LIMIT 10
    """).fetchall()
    for r in pending:
        print(f"  {r['n']:>5}x  state={r['candidate_state']} status={r['quality_status'] or '?'} reason={r['quality_reason'] or 'none'}")

# ── 4. Check system_config qualification gates ────────────────────────────────
print(f"\n  {B}Qualification gate values:{R}")
gates = ["SIGNAL_TIER1_MAX_AGE_SEC","MIN_MARKET_CAP_USD","MIN_TOKEN_AGE_SEC",
         "MIN_CURVE_SOL","SUPERVISOR_MIN_MINT_CONFIDENCE","QUALIFIER_POLL_INTERVAL",
         "QUALIFIER_BATCH_SIZE","REQUIRE_DEXSCREENER"]
for k in gates:
    row = db.execute("SELECT value FROM system_config WHERE key=?", (k,)).fetchone()
    val = row["value"] if row else "NOT SET"
    flag = ""
    if k=="MIN_MARKET_CAP_USD" and row and float(row["value"] or 0) > 10000:
        flag = f" {RE}← too high for pump.fun{R}"
    if k=="REQUIRE_DEXSCREENER" and row and row["value"]=="1":
        flag = f" {RE}← DexScreener is 403, this blocks everything{R}"
    print(f"  {k:<40} = {W}{val}{R}{flag}")

# ── 5. Apply safe qualification fixes ────────────────────────────────────────
print(f"\n  {B}Applying fixes:{R}")
fixes = [
    ("SIGNAL_TIER1_MAX_AGE_SEC", "600"),
    ("MIN_MARKET_CAP_USD",       "1500"),
    ("MIN_TOKEN_AGE_SEC",        "10"),
    ("MIN_CURVE_SOL",            "2"),
    ("REQUIRE_DEXSCREENER",      "0"),
    ("QUALIFIER_BATCH_SIZE",     "40"),
    ("QUALIFIER_POLL_INTERVAL",  "1.0"),
]
for k,v in fixes:
    db.execute("INSERT OR REPLACE INTO system_config(key,value) VALUES(?,?)",(k,v))
db.commit()
for k,v in fixes:
    print(f"  {G}✓{R}  {k} = {v}")

# ── 6. Release any stuck qualifier claims ─────────────────────────────────────
n = db.execute("UPDATE market_snapshots SET qualify_claimed_until=NULL WHERE qualify_claimed_until IS NOT NULL").rowcount
db.commit()
print(f"\n  {G}✓ Released {n} stuck qualifier claim locks{R}")

print(f"\n  {D}Restart market_intelligence and sovereign_governor to pick up changes.{R}\n")
db.close()
