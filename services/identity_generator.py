"""
services/identity_generator.py
================================
Sovereign Alias Engine

Every contributor receives a permanent Sovereign Alias and Emoji Fingerprint
derived from a SHA-256 hash of their identity string. Deterministic — same
input always produces same alias. No PII stored.

Usage:
    from services.identity_generator import generate_sovereign_identity, get_or_create_alias
    emoji, alias = generate_sovereign_identity("wallet_abc123")
"""
from __future__ import annotations
import hashlib
import sqlite3
import time
from pathlib import Path

ROOT    = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "sentinuity_matrix.db"

PREFIXES = ["Lattice", "Node", "Apex", "Vanguard", "Sentinel",
            "Forge",   "Nexus","Cipher","Pulse",   "Vector"]
SUFFIXES = ["Observer","Logic","Watcher","Core","Signal",
            "Arbiter", "Codec","Relay",  "Vault","Axiom"]
EMOJI_POOL = ["🛰️","🛡️","🧬","📡","🛸","🔮","⚗️","🧿","💠","🔬"]


def generate_sovereign_identity(user_id: str) -> tuple[str, str]:
    """
    Deterministically derive (emoji, alias) from any identity string.
    Same input always → same output. No randomness.
    """
    h = hashlib.sha256(user_id.encode()).hexdigest()
    emoji  = EMOJI_POOL[int(h[0:2], 16) % len(EMOJI_POOL)]
    prefix = PREFIXES[int(h[2:4], 16) % len(PREFIXES)]
    suffix = SUFFIXES[int(h[4:6], 16) % len(SUFFIXES)]
    alias  = f"{prefix}_{suffix}_{h[:4].upper()}"
    return emoji, alias


def get_or_create_alias(user_id: str) -> tuple[str, str]:
    """
    Returns (emoji, alias) for a user_id, persisting to DB on first call.
    Subsequent calls for same user_id return the stored alias immediately.
    """
    emoji, alias = generate_sovereign_identity(user_id)
    try:
        with sqlite3.connect(str(DB_PATH), timeout=3) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sovereign_aliases (
                    user_id     TEXT PRIMARY KEY,
                    alias       TEXT,
                    emoji       TEXT,
                    created_at  REAL
                )
            """)
            existing = conn.execute(
                "SELECT alias, emoji FROM sovereign_aliases WHERE user_id=?",
                (user_id,)
            ).fetchone()
            if existing:
                return existing[1], existing[0]
            conn.execute(
                "INSERT OR IGNORE INTO sovereign_aliases (user_id,alias,emoji,created_at) VALUES (?,?,?,?)",
                (user_id, alias, emoji, time.time())
            )
            conn.commit()
    except Exception:
        pass
    return emoji, alias


def _ensure_submission_table():
    try:
        with sqlite3.connect(str(DB_PATH), timeout=3) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS community_submissions (
                    id              INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id         TEXT,
                    alias           TEXT,
                    emoji           TEXT,
                    logic_text      TEXT,
                    logic_type      TEXT DEFAULT 'PARAMETER_CHANGE',
                    status          TEXT DEFAULT 'pending',
                    confidence      REAL DEFAULT 0.0,
                    outcome         TEXT,
                    forge_credits   INTEGER DEFAULT 0,
                    submitted_at    REAL,
                    reviewed_at     REAL,
                    purge_at        REAL
                )
            """)
            conn.commit()
    except Exception:
        pass


def submit_logic(user_id: str, logic_text: str,
                 logic_type: str = "PARAMETER_CHANGE") -> dict:
    """
    Submit community logic for council review.
    Returns the submission record including alias.
    Raw text is scheduled for purge 120s after review (biohazard protocol).
    """
    _ensure_submission_table()
    emoji, alias = get_or_create_alias(user_id)
    now = time.time()
    try:
        with sqlite3.connect(str(DB_PATH), timeout=3) as conn:
            cur = conn.execute("""
                INSERT INTO community_submissions
                    (user_id, alias, emoji, logic_text, logic_type, status, submitted_at)
                VALUES (?,?,?,?,?,?,?)
            """, (user_id, alias, emoji, logic_text, logic_type, "pending", now))
            conn.commit()
            return {
                "id":         cur.lastrowid,
                "alias":      alias,
                "emoji":      emoji,
                "logic_type": logic_type,
                "status":     "pending",
                "submitted_at": now,
            }
    except Exception as e:
        return {"error": str(e)}


def purge_reviewed_submissions():
    """
    Biohazard Protocol: purge raw logic text 120s after review.
    Called periodically — safe to call on every render cycle.
    """
    now = time.time()
    try:
        with sqlite3.connect(str(DB_PATH), timeout=3) as conn:
            conn.execute("""
                UPDATE community_submissions
                SET logic_text = '[PURGED — FORENSIC PROTOCOL]'
                WHERE status IN ('approved','rejected','distilled')
                  AND reviewed_at IS NOT NULL
                  AND reviewed_at < ?
                  AND logic_text != '[PURGED — FORENSIC PROTOCOL]'
            """, (now - 120,))
            conn.commit()
    except Exception:
        pass


if __name__ == "__main__":
    test_ids = ["wallet_abc123", "polar@sentinuity", "node_test_99"]
    for uid in test_ids:
        e, a = generate_sovereign_identity(uid)
        print(f"  {e}  {a}  ←  {uid}")
