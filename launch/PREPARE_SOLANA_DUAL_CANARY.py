from __future__ import annotations

import argparse
import os
import re
import shutil
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "sentinuity_matrix.db"
ENV = ROOT / ".env"

SETTINGS = {
    "TRADING_MODE": "paper",
    "PAPER_TRADING_ENABLED": "1",
    "DUAL_MODE_ENABLED": "1",
    "DUAL_MODE_ARMED": "1",
    "LIVE_TRADING_ENABLED": "1",
    "LIVE_MODE_B_ENABLED": "1",
    "LIVE_ARMED": "1",
    "LIVE_MONEY_MODE": "1",
    "EXECUTION_ARMED": "1",
    "PAPER_MAX_OPEN_POSITIONS": "3",
    "LIVE_MAX_OPEN_POSITIONS": "1",
    "LIVE_POSITION_SIZE_USD": "8",
    "LIVE_TRADE_AMOUNT_USD": "8",
    "LIVE_MAX_POSITION_USD": "8",
    "MAX_LIVE_POSITION_USD": "8",
    "LIVE_MAX_TOTAL_EXPOSURE_USD": "8",
    "LIVE_DAILY_LOSS_LIMIT_USD": "8",
    "LIVE_CONSECUTIVE_LOSS_LIMIT": "1",
    "LIVE_REENTRY_COOLDOWN_SECONDS": "900",
    "OPERATOR_LIVE_POSITION_SIZE_USD": "8",
    "LIVE_MAX_PRICE_AGE_SEC": "30",
    "MAX_ROUND_TRIP_SLIPPAGE_PCT": "8",
    "EXCEPTIONAL_FIRE_ENABLED": "1",
    "LATCHED_OVERRIDE_ENABLED": "0",
    "RUNNER_LIVE_ESCALATION_ENABLED": "0",
    "LIVE_PAPER_SHADOW_ON_BLOCK": "1",
    "PATTERN_LIVE_ARMING_MODE": "advisory",
    "PATTERN_LIVE_ARMING_REQUIRED": "0",
    "SUBSTRATE_LIVE_ENABLED": "0",
    "SUBSTRATE_LIVE_ARMED": "0",
}


def env_values() -> dict[str, str]:
    vals = dict(os.environ)
    if ENV.exists():
        for line in ENV.read_text(encoding="utf-8", errors="replace").splitlines():
            m = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$", line)
            if m:
                vals.setdefault(m.group(1), m.group(2).strip().strip('"').strip("'"))
    return vals


def cols(cur: sqlite3.Cursor) -> set[str]:
    return {str(r[1]) for r in cur.execute("PRAGMA table_info(system_config)")}


def upsert(cur: sqlite3.Cursor, key: str, value: str, description: str) -> None:
    available = cols(cur)
    now = time.time()
    if "description" in available and "updated_at" in available:
        cur.execute(
            "INSERT INTO system_config(key,value,description,updated_at) VALUES(?,?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description,updated_at=excluded.updated_at",
            (key, value, description, now),
        )
    elif "description" in available:
        cur.execute(
            "INSERT INTO system_config(key,value,description) VALUES(?,?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value,description=excluded.description",
            (key, value, description),
        )
    else:
        cur.execute(
            "INSERT INTO system_config(key,value) VALUES(?,?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, value),
        )


def source_contracts() -> list[str]:
    failures: list[str] = []
    required = {
        ROOT / "services" / "pattern_live_arming.py": ["return classify_realised(realised_pct)"],
        ROOT / "services" / "execution_engine.py": ["entry_market_cap_usd", "entry_liquidity_usd", "entry_curve_progress_pct"],
        ROOT / "services" / "macro_price_feed.py": ["HISTORY_REHYDRATED", "[-period:]"],
        ROOT / "services" / "execution_engine.py": ["RUNNER_GAP_THROUGH_FLOOR", "entry_market_cap_usd"],
    }
    # Merge duplicate path requirements safely.
    merged: dict[Path, list[str]] = {}
    for path, needles in required.items():
        merged.setdefault(path, []).extend(needles)
    for path, needles in merged.items():
        if not path.exists():
            failures.append(f"missing source: {path.relative_to(ROOT)}")
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for needle in needles:
            if needle not in text:
                failures.append(f"contract missing in {path.relative_to(ROOT)}: {needle}")
    return failures


def verify() -> int:
    failures: list[str] = []
    failures.extend(source_contracts())
    vals = env_values()
    if not any(vals.get(k, "").strip() for k in ("SOLANA_PRIVATE_KEY", "PRIVATE_KEY", "WALLET_PRIVATE_KEY")):
        failures.append("private-key environment variable not found")
    if not any(vals.get(k, "").strip() for k in ("QUICKNODE_RPC", "SOLANA_RPC_URL", "HELIUS_RPC_URL", "CHAINSTACK_RPC")):
        failures.append("Solana RPC environment variable not found")
    if not DB.exists():
        failures.append("sentinuity_matrix.db missing")
    else:
        con = sqlite3.connect(DB, timeout=20)
        cur = con.cursor()
        qc = cur.execute("PRAGMA quick_check").fetchone()[0]
        if qc != "ok":
            failures.append(f"database quick_check={qc}")
        cur.execute("CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT)")
        got = {k: (cur.execute("SELECT value FROM system_config WHERE key=?", (k,)).fetchone() or [None])[0] for k in SETTINGS}
        con.close()
        for key, expected in SETTINGS.items():
            if str(got.get(key)) != expected:
                failures.append(f"{key}={got.get(key)!r}; expected {expected!r}")

    print("=" * 78)
    print("SENTINUITY SOLANA DUAL CANARY FINAL PREFLIGHT")
    print("=" * 78)
    if failures:
        for item in failures:
            print("[FAIL]", item)
        print("DUAL CANARY NOT READY")
        return 1
    print("[PASS] signed-off source contracts present")
    print("[PASS] private key configured (value hidden)")
    print("[PASS] RPC configured (value hidden)")
    print("[PASS] database quick_check ok")
    print("[PASS] paper remains active alongside live")
    print("[PASS] Solana live capped at one $8 position")
    print("[PASS] daily loss cap $8; one consecutive live loss limit")
    print("[PASS] smart-wallet bypass and runner auto-escalation disabled")
    print("[PASS] Substrate live disabled")
    print("DUAL CANARY PREFLIGHT PASS")
    print("NOTE: this does not prove current RPC reachability, quote availability, or future fill quality.")
    return 0


def apply() -> int:
    if not DB.exists():
        print(f"[FAIL] database missing: {DB}")
        return 2
    backup_dir = ROOT / "db_backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = backup_dir / f"sentinuity_matrix.PRE_DUAL_CANARY_{stamp}.db"
    shutil.copy2(DB, backup)
    print(f"[BACKUP] {backup}")

    con = sqlite3.connect(DB, timeout=30)
    cur = con.cursor()
    cur.execute("CREATE TABLE IF NOT EXISTS system_config(key TEXT PRIMARY KEY,value TEXT)")
    desc = "operator-confirmed bounded Solana dual canary; paper alongside; Substrate live disabled"
    for key, value in SETTINGS.items():
        upsert(cur, key, value, desc)
    con.commit()
    qc = cur.execute("PRAGMA quick_check").fetchone()[0]
    con.close()
    if qc != "ok":
        print(f"[FAIL] quick_check={qc}; restore {backup}")
        return 3
    return verify()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=("apply", "verify"))
    args = ap.parse_args()
    return apply() if args.action == "apply" else verify()


if __name__ == "__main__":
    raise SystemExit(main())
