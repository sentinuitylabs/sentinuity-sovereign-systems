"""
polaris_notify.py — Stage notification system for Polaris
─────────────────────────────────────────────────────────
Polaris calls _send_stage_notification() at each milestone.
Messages arrive on Telegram cleanly formatted.
Operator replies with approval code or runs command in hub.

Deploy to: services/polaris_notify.py
Import in polaris.py: from services.polaris_notify import send_stage_notification
"""

import os, sqlite3, time, requests, hashlib
from pathlib import Path
from datetime import datetime

# ── CONFIG ────────────────────────────────────────────────────────────────────
BOT_TOKEN = (
    os.getenv("TELEGRAM_POLARIS_TOKEN") or
    os.getenv("TELEGRAM_BOT_TOKEN") or ""
).strip()

OWNER_ID = (
    os.getenv("TELEGRAM_OWNER_ID") or
    os.getenv("TELEGRAM_CHAT_ID") or ""
).strip()

DB_PATH = Path(__file__).resolve().parent.parent / "sentinuity_matrix.db"

# ── STAGE TEMPLATES ───────────────────────────────────────────────────────────
STAGE_TEMPLATES = {
    "debate_started": """
🔵 SENTINUITY COUNCIL
Stage: DEBATE STARTED
Proposal: {proposal_type}
"{proposal_text}"
Confidence: {confidence}
— POLARIS
""",
    "debate_complete": """
✅ SENTINUITY COUNCIL
Stage: DEBATE COMPLETE
Proposal: {proposal_type}
"{proposal_text}"
Verdict: {verdict}
Confidence: {confidence}
Action needed: {action}
— POLARIS
""",
    "build_research": """
🔬 SENTINUITY COUNCIL
Stage: RESEARCH COMPLETE
Task: {task_name}
Finding: {summary}
No action needed — logged to Intelligence Tab.
— POLARIS
""",
    "approval_needed": """
🔐 SENTINUITY COUNCIL
Stage: YOUR APPROVAL NEEDED
Task: {task_name}
"{summary}"
Risk: {risk_level}
To approve: enter code in Hub → Intelligence Tab → Golden Lattice
Code: {approval_code}
— POLARIS
""",
    "command_needed": """
⚡ SENTINUITY COUNCIL
Stage: COMMAND NEEDED
Polaris requests: {command_text}
Reason: {reason}
Risk: {risk_level}
To run: open Hub → enter code {approval_code}
DO NOT run unknown commands.
— POLARIS
""",
    "morning_brief": """
🌅 SENTINUITY MORNING BRIEF
{summary}
— POLARIS {timestamp}
""",
    "error": """
⚠️ SENTINUITY ALERT
Service: {service}
Issue: {message}
— POLARIS
""",
    "x_scout_finding": """
📡 X SCOUT FINDING
Token: {token}
Signal: {signal_text}
Source: {source}
⚠️ Post link saved to DB only — not sharing raw links.
— POLARIS
""",
}


def send_telegram(text: str) -> bool:
    """Send message via Telegram bot. Returns True if successful."""
    if not BOT_TOKEN or not OWNER_ID:
        return False
    try:
        r = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={
                "chat_id": OWNER_ID,
                "text": text.strip(),
                "parse_mode": "HTML",
            },
            timeout=5,
        )
        return r.status_code == 200
    except Exception:
        return False


def send_stage_notification(
    stage: str,
    data: dict,
    write_to_db: bool = True,
) -> bool:
    """
    Send a clean stage notification to Telegram.
    Also writes to operator_command_queue if approval needed.

    Stages:
        debate_started, debate_complete, build_research,
        approval_needed, command_needed, morning_brief,
        error, x_scout_finding
    """
    template = STAGE_TEMPLATES.get(stage)
    if not template:
        return False

    # Generate approval code for stages needing it
    if stage in ("approval_needed", "command_needed"):
        seed = f"{stage}{data.get('task_name','')}{time.time():.0f}"
        data["approval_code"] = hashlib.md5(seed.encode()).hexdigest()[:6].upper()

    data["timestamp"] = datetime.now().strftime("%H:%M %d/%m")

    try:
        text = template.format(**{k: str(v)[:200] for k,v in data.items()})
    except KeyError:
        text = f"POLARIS STAGE: {stage}\n{str(data)[:300]}"

    # Write to operator_command_queue if command needed
    if write_to_db and stage == "command_needed":
        try:
            db = sqlite3.connect(str(DB_PATH), timeout=3)
            db.execute("""
                INSERT INTO operator_command_queue
                (requested_by, command_text, reason, risk_level, status, created_at)
                VALUES ('POLARIS', ?, ?, ?, 'pending', ?)
            """, (
                data.get("command_text",""),
                data.get("reason",""),
                data.get("risk_level","LOW"),
                time.time(),
            ))
            db.commit()
            db.close()
        except Exception:
            pass

    # Write to task_runs as operator_visible
    if write_to_db:
        try:
            db = sqlite3.connect(str(DB_PATH), timeout=3)
            db.execute("""
                INSERT INTO task_runs
                (task_name, run_type, status, started_at, finished_at,
                 summary, operator_visible, telegram_sent)
                VALUES (?, ?, ?, ?, ?, ?, 1, ?)
            """, (
                data.get("task_name", stage),
                stage,
                "NOTIFIED",
                time.time(),
                time.time(),
                text[:800],
                1,
            ))
            db.commit()
            db.close()
        except Exception:
            pass

    return send_telegram(text)


def test_notify():
    """Test notification — run this to verify Telegram is working."""
    ok = send_stage_notification("morning_brief", {
        "summary": "TEST — Polaris notification system online.\nTelegram connection verified.",
        "task_name": "test",
    })
    print(f"Telegram send: {'OK' if ok else 'FAILED — check TELEGRAM_POLARIS_TOKEN and TELEGRAM_OWNER_ID in .env'}")
    return ok


if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv(Path('.env'), override=False)
    load_dotenv(Path('..') / '.env', override=False)
    # Re-read after loading env
    BOT_TOKEN = (os.getenv("TELEGRAM_POLARIS_TOKEN") or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    OWNER_ID  = (os.getenv("TELEGRAM_OWNER_ID") or os.getenv("TELEGRAM_CHAT_ID") or "").strip()
    print(f"BOT_TOKEN: {'SET' if BOT_TOKEN else 'MISSING'}")
    print(f"OWNER_ID:  {'SET' if OWNER_ID else 'MISSING'}")
    test_notify()
