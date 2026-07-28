"""
arm_dual_mode.py
=================
Set Sentinuity to DUAL-MODE ARMED state:
  - Paper runs alongside always (3 slots)
  - Live is ARMED (1 slot)
  - Exceptional fire ON
  - All safety gates intact — NOT relaxed

Run once to ensure the operator-intended config is set:
    python arm_dual_mode.py

What this script does NOT do:
  - Does NOT relax MODE_B_CONF_FLOOR
  - Does NOT relax freshness/loss/smart-money gates
  - Does NOT force live entries
  - Does NOT enable LATCHED_OVERRIDE (smart-wallet bypass stays OFF)
  - Does NOT enable RUNNER_LIVE_ESCALATION (MONSTER auto-fire stays OFF)

Live will only fire when ALL existing gates align:
  conf >= MODE_B_CONF_FLOOR + smart_money_tier OK + oracle fresh +
  no drawdown halt + live_losses_2h < 3 + signal age OK
"""
import sqlite3
from pathlib import Path

DB = Path("sentinuity_matrix.db")
if not DB.exists():
    print(f"FATAL: {DB} not found")
    raise SystemExit(1)

db = sqlite3.connect(str(DB), timeout=15)

# ── Dual-mode arming settings ──────────────────────────────────────────
# Operator-set values that should be preserved or set to safe defaults
ARMING_CONFIG = [
    # Mode itself — set to 'live' so dual-mode (paper alongside live) is active
    ("TRADING_MODE",                "live",
     "live | paper — live enables dual-mode paper+live"),
    ("LIVE_TRADING_ENABLED",        "1",
     "1=live armed, 0=paper only"),
    
    # Slot caps — operator's chosen safety bounds
    ("LIVE_MAX_OPEN_POSITIONS",     "1",
     "Hard cap: 1 live trade at a time"),
    ("PAPER_MAX_OPEN_POSITIONS",    "3",
     "Paper can hold 3 concurrent shadow positions for learning"),
    
    # Position size — operator's $20 per live trade
    ("POSITION_SIZE_USD",           "20",
     "$20 per live position"),
    
    # Exceptional fire — required for live to fire
    ("EXCEPTIONAL_FIRE_ENABLED",    "1",
     "Live requires exceptional-fire path to align"),
    
    # Safety gates — do NOT relax
    ("MODE_B_CONF_FLOOR",           "0.85",
     "Confidence floor for live fire (tighter than paper)"),
    
    # SAFETY: these stay OFF until operator explicitly enables
    ("LATCHED_OVERRIDE_ENABLED",    "0",
     "OFF — bypass of Mode B smart-wallet gate stays OFF"),
    ("RUNNER_LIVE_ESCALATION_ENABLED", "0",
     "OFF — MONSTER detection observes only, no auto live-fire"),
]

print("=" * 70)
print("ARMING DUAL-MODE TRADING")
print("=" * 70)
print()

# Read first — show operator what current state is
print("Current → Target:")
print()
changed = 0
preserved = 0
for key, target, description in ARMING_CONFIG:
    r = db.execute("SELECT value FROM system_config WHERE key=?", (key,)).fetchone()
    current = r[0] if r else "(unset)"
    
    # Decide whether to update
    if str(current) == str(target):
        marker = "✓"
        action = "preserved"
        preserved += 1
    else:
        # Special case: don't override an existing tighter MODE_B_CONF_FLOOR
        if key == "MODE_B_CONF_FLOOR":
            try:
                if float(current) >= float(target):
                    marker = "✓"
                    action = f"preserved (operator value {current} ≥ {target})"
                    preserved += 1
                    print(f"  {marker}  {key:<35} {current:<10} → {action}")
                    continue
            except (ValueError, TypeError):
                pass
        
        db.execute(
            "INSERT OR REPLACE INTO system_config(key,value,description) VALUES(?,?,?)",
            (key, target, description),
        )
        marker = "→"
        action = f"set to {target}"
        changed += 1
    
    print(f"  {marker}  {key:<35} {current:<10} → {action}")

db.commit()
print()
print(f"Changed: {changed}   Preserved: {preserved}")
print()
print("DUAL-MODE STATE")
print("─" * 70)
print("  LIVE ARMED:                  YES")
print("  PAPER ALONGSIDE:             YES")
print("  EXCEPTIONAL FIRE:            ON")
print("  LIVE CONDITIONAL FIRE:       YES (all gates intact)")
print("  LIVE FORCE/BYPASS:           NO")
print("  MODE B SAFETY:               KEPT")
print()
print("Live will only fire when the existing exceptional/Mode B conditions align.")
print("If live does not fire, run: python launch_truth.py")
print("That will show the exact blocker.")

db.close()
