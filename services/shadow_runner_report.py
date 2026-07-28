"""
shadow_runner_report.py
Quick report on what services.shadow_runner_tracker has found.
Run anytime: python shadow_runner_report.py
"""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime
from pathlib import Path

DB = Path("sentinuity_matrix.db")
db = sqlite3.connect(str(DB), timeout=30)
db.row_factory = sqlite3.Row
now = time.time()

exists = db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='shadow_runners'").fetchone()
if not exists:
    print("shadow_runners table does not exist yet — service hasn't run a cycle yet.")
    print("Start services.shadow_runner_tracker, wait 90 seconds, then re-run this report.")
    db.close()
    raise SystemExit(0)

cols = {r[1] for r in db.execute("PRAGMA table_info(shadow_runners)").fetchall()}

def has(col: str) -> bool:
    return col in cols

print("=" * 78)
print(f"SHADOW RUNNER REPORT — generated {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
print("=" * 78)

total = db.execute("SELECT COUNT(*) FROM shadow_runners").fetchone()[0]
last_24h = db.execute("SELECT COUNT(*) FROM shadow_runners WHERE detected_at > ?", (now - 86400,)).fetchone()[0]
last_4h = db.execute("SELECT COUNT(*) FROM shadow_runners WHERE detected_at > ?", (now - 14400,)).fetchone()[0]
print(f"\nTotal shadow runners ever tracked: {total}")
print(f"Last 24h:  {last_24h}")
print(f"Last 4h:   {last_4h}")

print("\n── CLASSIFICATION (last 24h) ──")
for r in db.execute("""
    SELECT classification, COUNT(*) AS cnt,
           AVG(peak_mult) AS avg_mult, MAX(peak_mult) AS max_mult,
           SUM(COALESCE(smart_wallet_signal,0)) AS sm_hits
    FROM shadow_runners
    WHERE detected_at > ?
    GROUP BY classification ORDER BY cnt DESC
""", (now - 86400,)).fetchall():
    print(f"  {(r['classification'] or 'NULL'):<34} count={r['cnt']:3} avg={float(r['avg_mult'] or 0):.2f}x max={float(r['max_mult'] or 0):.2f}x smart_signals={r['sm_hits'] or 0}")

print("\n── TOP 10 MONSTER RUNNERS (last 24h, sorted by peak_mult) ──")
extra_cols = ""
if has("smart_wallet_count"):
    extra_cols += ", smart_wallet_count"
if has("elite_wallet_count"):
    extra_cols += ", elite_wallet_count"
if has("top_wallet_lead_time_sec"):
    extra_cols += ", top_wallet_lead_time_sec"
if has("tide_at_peak"):
    extra_cols += ", tide_at_peak"

top = db.execute(f"""
    SELECT mint_address, token_name, peak_mult, time_to_peak_sec,
           we_qualified, we_latched, we_opened, smart_wallet_signal,
           classification, rejection_reason {extra_cols}
    FROM shadow_runners
    WHERE detected_at > ?
    ORDER BY peak_mult DESC LIMIT 10
""", (now - 86400,)).fetchall()
for r in top:
    name = (r["token_name"] or r["mint_address"] or "?")[:16]
    ttp = f"{float(r['time_to_peak_sec'] or 0)/60:.1f}m" if r["time_to_peak_sec"] else "?"
    flags = []
    if r["we_qualified"]: flags.append("Q")
    if r["we_latched"]: flags.append("L")
    if r["we_opened"]: flags.append("O")
    if r["smart_wallet_signal"]: flags.append("SM")
    flag_str = "+".join(flags) if flags else "—"
    tail = ""
    if has("smart_wallet_count"):
        tail += f" wallets={r['smart_wallet_count'] or 0}"
    if has("elite_wallet_count"):
        tail += f" elite={r['elite_wallet_count'] or 0}"
    if has("tide_at_peak"):
        tail += f" tide={r['tide_at_peak'] or '?'}"
    print(f"  {name:<16} {float(r['peak_mult'] or 0):.2f}x ttp={ttp:<8} [{flag_str}] {r['classification']}{tail}")
    if r["rejection_reason"]:
        print(f"     reject_reason: {r['rejection_reason']}")

caught = db.execute("SELECT COUNT(*) FROM shadow_runners WHERE we_opened=1 AND detected_at > ?", (now - 86400,)).fetchone()[0]
missed = db.execute("SELECT COUNT(*) FROM shadow_runners WHERE we_opened=0 AND detected_at > ?", (now - 86400,)).fetchone()[0]
print("\n── HIT RATE (last 24h) ──")
if caught + missed:
    print(f"  Caught: {caught} / {caught+missed} ({caught*100/(caught+missed):.1f}%)")
    print(f"  Missed: {missed} / {caught+missed} ({missed*100/(caught+missed):.1f}%)")
else:
    print("  No runners detected yet")

print("\n── REJECTION REASONS THAT BLOCKED MONSTER RUNNERS (≥5x missed) ──")
rejects = db.execute("""
    SELECT rejection_reason, COUNT(*) AS cnt, AVG(peak_mult) AS avg_mult, MAX(peak_mult) AS max_mult
    FROM shadow_runners
    WHERE rejection_reason IS NOT NULL AND peak_mult >= 5.0 AND detected_at > ?
    GROUP BY rejection_reason ORDER BY cnt DESC LIMIT 10
""", (now - 86400,)).fetchall()
if rejects:
    for r in rejects:
        print(f"  {str(r['rejection_reason'])[:60]:<60} count={r['cnt']:3} max={float(r['max_mult'] or 0):.2f}x")
else:
    print("  None yet — either we caught all monsters or none rejected so far.")

print("\n── SMART WALLET SIGNAL ACCURACY ──")
sm_total = db.execute("SELECT COUNT(*) FROM shadow_runners WHERE smart_wallet_signal=1 AND detected_at > ?", (now - 86400,)).fetchone()[0]
sm_monster = db.execute("SELECT COUNT(*) FROM shadow_runners WHERE smart_wallet_signal=1 AND peak_mult >= 5.0 AND detected_at > ?", (now - 86400,)).fetchone()[0]
print(f"  Smart wallet flagged runners (24h): {sm_total}")
print(f"  Of those, ≥5x outcomes:             {sm_monster}")
if sm_total:
    print(f"  Smart wallet → monster rate:        {sm_monster*100/sm_total:.1f}%")
else:
    print("  No smart wallet signals yet — copytrade infra still observe/paper-only.")

db.close()
print("\n" + "=" * 78)
print("END SHADOW REPORT")
print("=" * 78)
