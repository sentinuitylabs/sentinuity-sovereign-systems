import sqlite3, time

db = sqlite3.connect('sentinuity_matrix.db')
now = time.time()

# Activate forge focus lock
db.execute("INSERT OR REPLACE INTO system_config(key,value) VALUES('POLARIS_FORGE_ONLY_MODE','1')")
db.execute("INSERT OR REPLACE INTO system_config(key,value) VALUES('FOCUS_LOCK_ACTIVE','1')")
db.execute("INSERT OR REPLACE INTO system_config(key,value) VALUES('FOCUS_LOCK_PROJECT','intelligence_build')")
print("✓ POLARIS_FORGE_ONLY_MODE = 1")
print("✓ FOCUS_LOCK_ACTIVE = 1")

# Dissolve all open trading proposals
r1 = db.execute("UPDATE polaris_proposals SET status='dissolved' WHERE status='open' AND proposal_domain='TRADING'")
print(f"✓ Dissolved {r1.rowcount} trading proposals")

# Promote forge proposals to gate
r2 = db.execute("UPDATE polaris_proposals SET status='forge_complete' WHERE status='open' AND proposal_domain='FORGE'")
print(f"✓ Promoted {r2.rowcount} forge proposals to Operator Gate")

db.commit()

# Show forge projects
print("\nForge projects:")
for row in db.execute("SELECT project_key, current_stage FROM forge_projects ORDER BY updated_at DESC").fetchall():
    print(f"  {row[0]}: {row[1]}")

db.close()
print("\nPolaris is now forge-locked. Drop polaris.py into services/ to activate.")
print("Seal the forge_complete proposals in your Operator Gate to advance build stages.")
