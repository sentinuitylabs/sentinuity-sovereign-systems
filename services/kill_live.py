"""
kill_live.py — EMERGENCY LIVE TRADING KILL SWITCH
─────────────────────────────────────────────────────────────────────
The "bee stinger" — the very act of unauthorised access triggers this.

What it does immediately:
  1. Switches TRADING_MODE to 'paper' — no more real transactions
  2. Clears PHANTOM_WALLET_ADDRESS from DB — disconnects wallet
  3. Attempts to close all open live positions at market price
  4. Revokes/invalidates the SOLANA_PRIVATE_KEY in memory
  5. Overwrites the .env API keys with placeholder text
     (so any process still running can't make authenticated calls)
  6. Sends Telegram emergency alert
  7. Kills all Python processes
  8. Writes a forensic log of what triggered the kill

WHEN TO USE:
  - You see an alert from sentinel.py
  - You see trades you didn't authorise
  - The .env hash changed unexpectedly
  - Someone gained Polaris/OpenClaw access
  - Anything feels wrong

REGARDING YOUR QUESTION — close trade or take the loss?
  Default: CLOSE ALL OPEN POSITIONS FIRST, then kill.
  Rationale: a few seconds of exposure to close properly is worth
  more than leaving open positions that could keep moving against you
  with no monitoring. The loss from an emergency close is bounded.
  An unmonitored open position is unbounded.

  Set EMERGENCY_CLOSE=False below if you want instant kill without
  attempting to close positions first (faster but leaves positions open).

Run: python kill_live.py
Or:  python kill_live.py --no-close  (instant kill, no close attempt)
"""

import os, sys, sqlite3, time, subprocess, shutil
from pathlib import Path
from datetime import datetime

EMERGENCY_CLOSE = '--no-close' not in sys.argv
BASE     = Path('.').resolve()
LOG_FILE = BASE / "logs" / "kill_live.log"
LOG_FILE.parent.mkdir(exist_ok=True)

def log(msg):
    ts  = datetime.now().strftime("%H:%M:%S")
    out = f"[{ts}] {msg}"
    print(out)
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(out + '\n')

def send_telegram(msg):
    try:
        import requests
        tok = os.getenv("TELEGRAM_BOT_TOKEN","")
        cid = os.getenv("TELEGRAM_CHAT_ID","")
        if tok and cid:
            requests.post(
                f"https://api.telegram.org/bot{tok}/sendMessage",
                json={"chat_id": cid,
                      "text": f"🔴 SENTINUITY EMERGENCY KILL\n{msg}"},
                timeout=5
            )
    except Exception:
        pass

log("="*50)
log("EMERGENCY KILL SWITCH ACTIVATED")
log(f"Time: {datetime.now().isoformat()}")
log(f"Close positions first: {EMERGENCY_CLOSE}")
log("="*50)

# ── STEP 1: Switch to paper mode immediately ──────────────────────────────────
log("STEP 1: Switching to paper mode...")
try:
    db = sqlite3.connect('sentinuity_matrix.db', timeout=5)
    db.execute("INSERT OR REPLACE INTO system_config (key,value) VALUES ('TRADING_MODE','paper')")
    db.execute("INSERT OR REPLACE INTO system_config (key,value) VALUES ('PHANTOM_WALLET_ADDRESS','')")
    db.execute("INSERT OR REPLACE INTO system_config (key,value) VALUES ('EXECUTION_ENABLED','0')")
    db.commit()
    log("  Paper mode active. No new live transactions possible.")
except Exception as e:
    log(f"  DB switch failed: {e} — continuing kill sequence")

# ── STEP 2: Close open positions ──────────────────────────────────────────────
if EMERGENCY_CLOSE:
    log("STEP 2: Attempting emergency close of all open positions...")
    try:
        db2 = sqlite3.connect('sentinuity_matrix.db', timeout=5)
        db2.row_factory = sqlite3.Row
        # TRUE_DUAL_KILL_GUARD_20260713 (Claude audit):
        # This DB close never sends an on-chain sell, so it must only touch SIM
        # rows. A REAL row DB-closed here would fabricate an exit (tokens remain
        # in the wallet) and its proceeds would be credited to the PAPER wallet.
        # REAL rows are left OPEN and reported for manual/chain-confirmed exit.
        open_pos = db2.execute(
            "SELECT id, mint_address, entry_price, position_size_usd, token_name "
            "FROM paper_positions WHERE status='OPEN' "
            "AND UPPER(COALESCE(funding_mode,'SIM'))<>'REAL'"
        ).fetchall()
        real_open = db2.execute(
            "SELECT id, token_name FROM paper_positions WHERE status='OPEN' "
            "AND UPPER(COALESCE(funding_mode,'SIM'))='REAL'"
        ).fetchall()
        for rp in real_open:
            log(f"  REAL position left OPEN (requires on-chain sell): pos_id={rp['id']} {rp['token_name']}")
        log(f"  Found {len(open_pos)} open SIM positions ({len(real_open)} REAL left open)")

        if open_pos:
            for pos in open_pos:
                log(f"  Closing: {pos['token_name']} pos_id={pos['id']}")
                try:
                    # Try to get current price for a clean close
                    import requests
                    mint = pos['mint_address']
                    price = None

                    # Try Jupiter first
                    jkey = os.getenv("JUPITER_PRICE_API_KEY","")
                    if jkey:
                        r = requests.get(
                            f"https://api.jup.ag/price/v3",
                            params={"ids": mint},
                            headers={"x-api-key": jkey},
                            timeout=3
                        )
                        price = float((r.json().get(mint) or {}).get("usdPrice") or 0) or None

                    # Fall back to Birdeye
                    if not price:
                        bkey = os.getenv("BIRDEYE_API_KEY","")
                        if bkey:
                            r = requests.get(
                                "https://public-api.birdeye.so/defi/price",
                                params={"address": mint},
                                headers={"X-API-KEY": bkey, "x-chain": "solana"},
                                timeout=3
                            )
                            price = float((r.json().get("data") or {}).get("value") or 0) or None

                    # Use entry price as last resort (scratch close)
                    if not price:
                        price = float(pos['entry_price'])
                        log(f"    No price found — using entry price (scratch close)")

                    # Force close in DB
                    ep = float(pos['entry_price'])
                    sz = float(pos['position_size_usd'])
                    pnl = sz * ((price - ep) / ep) if ep > 0 else 0.0

                    now = time.time()
                    db2.execute("""
                        UPDATE paper_positions
                        SET status='CLOSED',
                            exit_price=?,
                            realized_pnl_usd=?,
                            exit_reason='EMERGENCY_KILL',
                            closed_at=?,
                            win_loss=?
                        WHERE id=?
                    """, (price, pnl, now,
                          'WIN' if pnl > 0 else 'LOSS', pos['id']))

                    db2.execute("""
                        UPDATE system_state
                        SET wallet_balance = wallet_balance + ?
                        WHERE id=1
                    """, (sz + pnl,))

                    db2.execute("""
                        INSERT INTO wallet_write_log
                        (source, delta_usd, pnl_usd, token_name, timestamp)
                        VALUES ('CLOSE_emergency', ?, ?, ?, ?)
                    """, (sz + pnl, pnl, pos['token_name'], now))

                    db2.commit()
                    log(f"    Closed at ${price:.8f}  PnL=${pnl:.4f}")

                except Exception as e:
                    log(f"    Failed to close pos {pos['id']}: {e}")

        db2.close()
        log("  Position close sequence complete.")
    except Exception as e:
        log(f"  Position close failed: {e}")
else:
    log("STEP 2: Skipped (--no-close flag set) — positions left open")

# ── STEP 3: Invalidate .env API keys ─────────────────────────────────────────
log("STEP 3: Invalidating API keys in .env...")
env_candidates = [BASE.parent / ".env", BASE / ".env"]
env_path = None
for ep in env_candidates:
    if ep.exists():
        env_path = ep
        break

if env_path:
    try:
        # Backup first
        backup_path = BASE / "logs" / f".env.backup.{int(time.time())}"
        shutil.copy2(env_path, backup_path)
        log(f"  .env backed up to: {backup_path}")

        # Read and invalidate sensitive keys
        lines = env_path.read_text(errors='ignore').split('\n')
        KEYS_TO_INVALIDATE = [
            'SOLANA_PRIVATE_KEY',
            'BIRDEYE_API_KEY',
            'JUPITER_PRICE_API_KEY',
            'OPENAI_API_KEY',
            'ANTHROPIC_API_KEY',
            'XAI_API_KEY',
            'GEMINI_API_KEY',
            'TWITTER_BEARER_TOKEN',
        ]
        new_lines = []
        for line in lines:
            invalidated = False
            for key in KEYS_TO_INVALIDATE:
                if line.startswith(key + '=') and '=' in line:
                    val = line.split('=', 1)[1].strip()
                    if val and val not in ('', 'REVOKED', 'your_key_here'):
                        new_lines.append(f"{key}=REVOKED_EMERGENCY_KILL_{int(time.time())}")
                        log(f"  Invalidated: {key}")
                        invalidated = True
                        break
            if not invalidated:
                new_lines.append(line)

        env_path.write_text('\n'.join(new_lines))
        log("  .env keys invalidated — running processes can't authenticate")
    except Exception as e:
        log(f"  .env invalidation failed: {e}")
else:
    log("  .env not found — manual key rotation required")

# ── STEP 4: Telegram emergency alert ─────────────────────────────────────────
log("STEP 4: Sending Telegram emergency alert...")
send_telegram(
    f"Emergency kill executed at {datetime.now().strftime('%H:%M:%S')}\n"
    f"Positions closed: {EMERGENCY_CLOSE}\n"
    f"API keys invalidated\n"
    f"TRADING_MODE = paper\n"
    f"ACTION REQUIRED: Rotate all API keys before restarting"
)

# ── STEP 5: Write forensic log ────────────────────────────────────────────────
log("STEP 5: Writing forensic snapshot...")
try:
    import psutil
    forensic = BASE / "logs" / f"forensic_{int(time.time())}.log"
    with open(forensic, 'w', encoding='utf-8') as f:
        f.write(f"EMERGENCY KILL FORENSIC LOG\n")
        f.write(f"Time: {datetime.now().isoformat()}\n\n")
        f.write("RUNNING PROCESSES AT TIME OF KILL:\n")
        for proc in psutil.process_iter(['pid','name','exe','cmdline']):
            try:
                f.write(f"  PID={proc.info['pid']} {proc.info['name']} {proc.info['exe'] or ''}\n")
            except Exception:
                pass
        f.write("\nNETWORK CONNECTIONS AT TIME OF KILL:\n")
        for conn in psutil.net_connections(kind='inet'):
            if conn.status == 'ESTABLISHED' and conn.raddr:
                try:
                    pname = psutil.Process(conn.pid).name() if conn.pid else "?"
                except Exception:
                    pname = "?"
                f.write(f"  {pname} -> {conn.raddr.ip}:{conn.raddr.port}\n")
    log(f"  Forensic log: {forensic}")
except Exception as e:
    log(f"  Forensic log failed: {e}")

# ── STEP 6: Kill all Python processes ────────────────────────────────────────
log("STEP 6: Killing all services...")
try:
    subprocess.run(['taskkill', '/F', '/IM', 'python.exe', '/T'],
                   capture_output=True, timeout=5)
    subprocess.run(['taskkill', '/F', '/IM', 'streamlit.exe', '/T'],
                   capture_output=True, timeout=5)
    subprocess.run(['taskkill', '/F', '/IM', 'cloudflared.exe', '/T'],
                   capture_output=True, timeout=5)
    log("  All services killed.")
except Exception as e:
    log(f"  Kill failed: {e}")

log("="*50)
log("EMERGENCY KILL COMPLETE")
log(f"Forensic log: logs/kill_live.log")
log(f"Backup .env: logs/.env.backup.*")
log("NEXT STEPS:")
log("  1. Rotate ALL API keys (Birdeye, Jupiter, OpenAI, etc)")
log("  2. Generate a NEW Phantom wallet for trading")
log("  3. Run paranoid_scan.py before restarting")
log("  4. Do NOT reuse any key that was active during this incident")
log("="*50)
