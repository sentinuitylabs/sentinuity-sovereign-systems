import sqlite3, time

db = sqlite3.connect('sentinuity_matrix.db')
db.row_factory = sqlite3.Row
now = time.time()

print("=== CURRENT FORGE PROJECTS ===")
for p in db.execute("SELECT project_key, current_stage FROM forge_projects").fetchall():
    print(f"  {p['project_key']}: {p['current_stage']}")

cols = [r[1] for r in db.execute("PRAGMA table_info(forge_projects)").fetchall()]
print(f"\nColumns: {cols}")

# Ensure all existing projects have status='active'
if 'status' in cols:
    r = db.execute("UPDATE forge_projects SET status='active' WHERE status IS NULL OR status=''")
    print(f"✓ Set {r.rowcount} existing projects to active")

# Add substrate_node_buildout if not exists
existing = db.execute("SELECT project_key FROM forge_projects WHERE project_key='substrate_node_buildout'").fetchone()
if not existing:
    insert_cols = ['project_key', 'current_stage', 'updated_at', 'created_at']
    insert_vals = ['substrate_node_buildout', 'RESEARCH', now, now]

    if 'status' in cols:
        insert_cols.append('status')
        insert_vals.append('active')
    if 'priority' in cols:
        insert_cols.append('priority')
        insert_vals.append(2)
    if 'description' in cols:
        insert_cols.append('description')
        insert_vals.append('Substrate node buildout — macro temporal arbitrage layer with BTC/ETH/SOL hexagonal nodes, compression/expansion detection feeding pump.fun confidence floor.')

    placeholders = ','.join(['?' for _ in insert_vals])
    db.execute(f"INSERT INTO forge_projects ({','.join(insert_cols)}) VALUES ({placeholders})", insert_vals)
    print("✓ substrate_node_buildout project added at RESEARCH stage")
else:
    # Make sure it's active
    if 'status' in cols:
        db.execute("UPDATE forge_projects SET status='active' WHERE project_key='substrate_node_buildout'")
    print("  substrate_node_buildout already exists — set to active")

# Set priorities: intelligence=1 (high), substrate=2
if 'priority' in cols:
    db.execute("UPDATE forge_projects SET priority=1 WHERE project_key IN ('agentic_trading_frameworks','quant_grid_dca_research','wallet_convergence')")
    db.execute("UPDATE forge_projects SET priority=2 WHERE project_key='substrate_node_buildout'")
    print("✓ Priority: intelligence build=1, substrate=2")

db.commit()

print("\n=== FORGE PROJECTS AFTER ===")
for p in db.execute("SELECT project_key, current_stage FROM forge_projects ORDER BY project_key").fetchall():
    print(f"  {p['project_key']}: {p['current_stage']}")

db.close()
print("\nDone. Both projects now active.")
print("Intelligence build runs at priority 1, substrate at priority 2.")
print("The orchestrator cycles both — intelligence gets more cycles.")
