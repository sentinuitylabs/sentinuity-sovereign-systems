# coding: utf-8
"""Operator approval authority for council Tier-B patches.

The daily DDMM seal is a convenience acknowledgement, not a security secret.
It is accepted only for Tier-B, non-funded council changes. Tier-C/funded paths
remain permanently outside autonomous approval and require manual code review.
"""
from __future__ import annotations
import hashlib, hmac, os, sqlite3, time
from datetime import datetime
from pathlib import Path
try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    ZoneInfo = None

ROOT = Path(__file__).resolve().parent.parent
BUILD_DB = ROOT / "sentinuity_build.db"
MELBOURNE_TZ = "Australia/Melbourne"


def expected_daily_code(now: datetime | None = None) -> str:
    if now is None:
        now = datetime.now(ZoneInfo(MELBOURNE_TZ)) if ZoneInfo else datetime.now()
    return now.strftime("%d%m")


def _valid_code(code: str) -> bool:
    supplied = str(code or "").strip()
    configured = os.getenv("COUNCIL_OPERATOR_APPROVAL_SECRET", "").strip()
    expected = configured or expected_daily_code()
    return bool(supplied) and hmac.compare_digest(supplied, expected)


def ensure_schema(db_path: Path = BUILD_DB) -> None:
    con = sqlite3.connect(str(db_path), timeout=3)
    try:
        cols = {r[1] for r in con.execute("PRAGMA table_info(council_needs_operator)")}
        for name, decl in {
            "approved_at":"REAL", "approved_by":"TEXT", "approval_digest":"TEXT",
            "decision":"TEXT", "resolved_at":"REAL"
        }.items():
            if name not in cols:
                con.execute(f'ALTER TABLE council_needs_operator ADD COLUMN "{name}" {decl}')
        con.commit()
    finally:
        con.close()


def approve_tier_b(canonical_id: int, code: str, db_path: Path = BUILD_DB) -> tuple[bool,str]:
    ensure_schema(db_path)
    if not _valid_code(code):
        return False, "INVALID_OPERATOR_SEAL"
    con = sqlite3.connect(str(db_path), timeout=3)
    con.row_factory = sqlite3.Row
    now=time.time()
    try:
        row=con.execute("""
            SELECT l.canonical_id,l.risk_tier,l.phase,n.id AS need_id
            FROM council_task_ledger l
            JOIN council_needs_operator n ON n.canonical_id=l.canonical_id
            WHERE l.canonical_id=?
        """,(int(canonical_id),)).fetchone()
        if not row: return False,"NO_PENDING_APPROVAL"
        if str(row['risk_tier']).upper()!='B': return False,"ONLY_TIER_B_DAILY_SEAL_ALLOWED"
        if str(row['phase']).upper()!='NEEDS_OPERATOR': return False,"TASK_NOT_WAITING_FOR_OPERATOR"
        digest=hashlib.sha256((str(code)+str(canonical_id)+str(int(now//86400))).encode()).hexdigest()
        con.execute("""UPDATE council_needs_operator SET approved_at=?,approved_by='LOCAL_OPERATOR',
                    approval_digest=?,decision='APPROVED',resolved_at=? WHERE canonical_id=?""",
                    (now,digest,now,int(canonical_id)))
        con.execute("""UPDATE council_task_ledger SET phase='OPEN',claimed_by=NULL,claimed_at=NULL,
                    lease_expires_at=NULL,blocker_code='OPERATOR_APPROVED',next_action='resume approved Tier-B task',updated_at=?
                    WHERE canonical_id=?""",(now,int(canonical_id)))
        con.execute("""INSERT INTO council_task_transitions(canonical_id,ts,agent,from_phase,to_phase,reason,next_action)
                    VALUES(?,?,'LOCAL_OPERATOR','NEEDS_OPERATOR','OPEN','valid Tier-B operator seal','resume approved task')""",
                    (int(canonical_id),now))
        con.commit(); return True,"APPROVED_AND_REQUEUED"
    finally:
        con.close()


def approval_is_valid(canonical_id: int, db_path: Path = BUILD_DB) -> bool:
    ensure_schema(db_path)
    con=sqlite3.connect(str(db_path),timeout=3)
    try:
        row=con.execute("SELECT approved_at,decision FROM council_needs_operator WHERE canonical_id=?",(int(canonical_id),)).fetchone()
        return bool(row and row[0] and str(row[1]).upper()=='APPROVED')
    finally:
        con.close()
