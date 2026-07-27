from __future__ import annotations
import shutil, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
STAMP = time.strftime("%Y%m%d_%H%M%S")
QUAR = ROOT / "_layout_quarantine" / STAMP

# Root is allowed to hold only DBs plus normal folders for this clean pack.
# This script moves old duplicate launch/core/service files out of root so the
# May22 foldered layout is not shadowed or confused by flat donor leftovers.
folders = ["core", "services", "launch"]
folder_names = set()
for folder in folders:
    base = ROOT / folder
    if base.exists():
        for p in base.iterdir():
            if p.is_file():
                folder_names.add(p.name.lower())

explicit_root_dupes = {
    "schema.py", "prelaunch.py", "preflight_verifier.py", "set_config.py", "verify_state.py", "launch_config.py",
    "apply_may22_paper_signoff.py", "verify_may22_paper_prelaunch.py", "check_may22_boot_health.py",
    "forge_genesis_seed.py", "sentinuity_sovereign_doctrine.md",
    "launch_sentinuity.bat", "launch_may22_paper_only.bat", "restart_sentinuity.bat",
    "shutdown_sentinuity.bat", "stop_all.bat", "watchdog_sentinuity.bat",
}

moved=[]
for p in list(ROOT.iterdir()):
    if not p.is_file():
        continue
    name_l=p.name.lower()
    if name_l in {"sentinuity_matrix.db", "sentinuity_intelligence.db"}:
        continue
    should_move = name_l in explicit_root_dupes or (name_l.endswith('.py') and name_l in folder_names)
    if should_move:
        QUAR.mkdir(parents=True, exist_ok=True)
        dest=QUAR / p.name
        i=1
        while dest.exists():
            dest=QUAR / f"{p.stem}_{i}{p.suffix}"
            i += 1
        shutil.move(str(p), str(dest))
        moved.append(p.name)

print("MAY22 CLEAN LAYOUT CHECK")
print("root:", ROOT)
if moved:
    print("quarantined root duplicates:", ", ".join(moved))
    print("quarantine:", QUAR)
else:
    print("no root duplicates found")
