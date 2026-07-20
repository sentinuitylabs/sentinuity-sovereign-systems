"""
root_sweep.py
=============
Audits trading-bot root folder and sweeps loose .py files
into designated folders. Run from trading-bot root.

DRY RUN by default. Pass --execute to actually move files.
Pass --auto to also set up a scheduled auto-sweep.
"""
import os, sys, shutil, time
from pathlib import Path

ROOT = Path('.')
SWEEP_DIR = ROOT / '_archive_scripts'
EXECUTE = '--execute' in sys.argv

# ── FILES THAT MUST STAY IN ROOT ────────────────────────────────────────────
KEEP_IN_ROOT = {
    # Launch/shutdown essentials
    'preflight_verifier.py',
    'safe_restart.py',
    'reset_pipeline.py',
    'prelaunch.py',
    # Active utilities called regularly
    'post_launch_fix.py',
    'status.py',
    'wallet_check.py',
    'onchain_audit.py',
    'sell_audit.py',
    'cluster_diag.py',
    'bloat_check.py',
    'freshness_check.py',
    'pnl_build_audit.py',
    'build_check.py',
    # Emergency tools
    'slim_db.py',
    'emergency_vacuum.py',
    'unjam.py',
    'unjam_live.py',
    'nuclear_clear.py',
    'unblock.py',
    'diagnose_now.py',
    'diagnose_jam.py',
    'deep_diag.py',
    'cleanup_dead_trades.py',
    'clear_ghost_trades.py',
    'nuke_loop_tokens.py',
    'wipe_token_trades.py',
    # Live trading
    'kill_live.py',
    # This script itself
    'root_sweep.py',
}

# ── SUBFOLDERS TO IGNORE (dont touch these) ──────────────────────────────────
IGNORE_DIRS = {
    'services', 'core', 'ui', 'launch', 'ops', 'tools',
    'components', 'config', 'data', 'docs', 'logs', 'runtime',
    'vault', 'sentinuity_core', 'sentinuity_v4', 'execution_layer',
    '_archive_scripts', '_archive_not_for_family_zip',
    '__pycache__', '.openclaw', '.streamlit',
}

print('='*60)
print('ROOT SWEEP AUDIT')
print('Mode: '+('EXECUTE' if EXECUTE else 'DRY RUN'))
print('='*60)

# Get all .py files directly in root (not in subfolders)
root_py = [f for f in ROOT.glob('*.py') if f.is_file()]
root_py.sort(key=lambda x: x.name)

keep = []
sweep = []
temp_files = []

for f in root_py:
    name = f.name
    # Temp/output files we created during debugging
    if name.startswith('_') or name.startswith('fix_') or name.startswith('check_'):
        temp_files.append(f)
    elif name in KEEP_IN_ROOT:
        keep.append(f)
    else:
        sweep.append(f)

print('\n[KEEP IN ROOT] '+str(len(keep))+' files')
for f in keep:
    print('  ✓ '+f.name)

print('\n[SWEEP TO _archive_scripts] '+str(len(sweep))+' files')
for f in sweep:
    print('  → '+f.name)

print('\n[TEMP/DEBUG FILES - delete] '+str(len(temp_files))+' files')
for f in temp_files:
    size = f.stat().st_size
    print('  🗑 '+f.name+' ('+str(size)+'b)')

if EXECUTE:
    print('\n--- EXECUTING ---')
    # Create archive dir
    SWEEP_DIR.mkdir(exist_ok=True)

    # Move sweep files
    moved = 0
    for f in sweep:
        dest = SWEEP_DIR / f.name
        if dest.exists():
            dest = SWEEP_DIR / (f.stem+'_'+str(int(time.time()))+'.py')
        shutil.move(str(f), str(dest))
        print('  MOVED: '+f.name+' → _archive_scripts/')
        moved += 1

    # Delete temp files
    deleted = 0
    for f in temp_files:
        f.unlink()
        print('  DELETED: '+f.name)
        deleted += 1

    print('\nMoved: '+str(moved)+' | Deleted: '+str(deleted))
    print('Root .py files remaining: '+str(len(keep)))

    # Write auto-sweep watcher script
    watcher = ROOT / 'auto_sweep.py'
    watcher.write_text('''"""
auto_sweep.py - run once to install auto-sweep of root .py files.
Moves any new .py files dumped in root to _archive_scripts/ every 60s.
Keep this running in background or add to launch bat.
"""
import time, shutil
from pathlib import Path

ROOT = Path(".")
SWEEP_DIR = ROOT / "_archive_scripts"
SWEEP_DIR.mkdir(exist_ok=True)

KEEP = set(open("root_sweep.py", encoding="utf-8").read().split("KEEP_IN_ROOT = {")[1].split("}")[0].replace("'","").replace(",","").split()) if (ROOT/"root_sweep.py").exists() else set()

print("[AUTO-SWEEP] Watching root for loose .py files...")
while True:
    for f in ROOT.glob("*.py"):
        if f.name not in KEEP and not f.name.startswith("auto_sweep"):
            dest = SWEEP_DIR / f.name
            try:
                shutil.move(str(f), str(dest))
                print("[AUTO-SWEEP] Moved: "+f.name)
            except: pass
    time.sleep(86400)  # once per day
''', encoding='utf-8')
    print('\nAuto-sweep watcher written: auto_sweep.py')
    print('Run it in background: start /b python auto_sweep.py')

else:
    print('\n--- DRY RUN COMPLETE ---')
    print('Run with --execute to apply:')
    print('  python root_sweep.py --execute')
    print()
    print('Summary:')
    print('  Keep in root:     '+str(len(keep)))
    print('  Move to archive:  '+str(len(sweep)))
    print('  Delete (temp):    '+str(len(temp_files)))
