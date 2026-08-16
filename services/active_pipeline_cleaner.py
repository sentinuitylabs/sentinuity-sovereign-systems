from pathlib import Path
import os
import sqlite3, time, shutil, re, json, argparse
from datetime import datetime, timezone


try:
    from services.scoring_lane_guard import is_fresh_unscored, DEFAULT_SCORING_GRACE_SECONDS
except Exception:
    DEFAULT_SCORING_GRACE_SECONDS = 900
    def is_fresh_unscored(row, grace_seconds=900):
        return False

ROOT = Path(__file__).resolve().parents[1]
LOG_DIR = ROOT / "logs"
RUNTIME_DIR = ROOT / "runtime"
LOG_DIR.mkdir(exist_ok=True)
RUNTIME_DIR.mkdir(exist_ok=True)

DEFAULT_CUTOFF_SECONDS = 600
MAX_SCAN_ROWS = 100000
MAX_DELETE_PER_TABLE = 500
HOT_PRICE_DB_NAMES = {"sentinuity_matrix.db", "sentinuity_price_truth.db"}
HOT_PRICE_TABLES = {"market_snapshots", "price_truth_snapshots", "pump_curve_shadow"}

ACTIVE_EXACT_TABLES = {
    "polaris_proposals",
    "market_snapshots",
    "qualified_snapshots",
    "qualified_proposals",
    "proposal_queue",
    "signal_queue",
    "pending_signals",
    "candidate_signals",
    "token_queue",
    "token_scan_queue",
    "execution_queue",
    "replay_queue",
    "pump_queue",
    "pump_candidates",
    "market_intelligence_queue",
}

ACTIVE_HINTS = [
    "proposal",
    "signal_queue",
    "pending_signal",
    "candidate_signal",
    "qualified_snapshot",
    "market_snapshot",
    "token_queue",
    "execution_queue",
    "replay_queue",
    "pump_candidate",
    "market_intelligence_queue",
]

PROTECT_HINTS = [
    "position", "positions",
    "trade", "trades",
    "execution_history", "executed",
    "order", "orders",
    "fill", "fills",
    "pnl", "profit", "loss",
    "wallet", "wallets",
    "balance", "equity",
    "dna", "review", "reviews",
    "performance", "backtest",
    "config", "setting", "settings",
    "parameter", "parameters",
    "heartbeat", "heartbeats",
    "service", "services",
    "guardian", "governor",
    "code_vault",
    "migration", "schema",
    "health", "system_health",
    "research", "council", "anomaly", "improvement",
    "archive", "history", "audit",
]

TIME_HINTS = [
    "created_at", "updated_at", "timestamp", "ts", "time",
    "seen_at", "first_seen", "first_seen_at",
    "last_seen", "last_seen_at",
    "discovered_at", "inserted_at", "qualified_at",
    "priced_at", "snapshot_at", "price_ts", "signal_ts",
    "received_at", "event_time",
]

REASON_HINTS = [
    "reason", "reject_reason", "rejection_reason", "status_reason",
    "verdict", "decision", "state", "status", "phase", "error",
]

def now_iso():
    return datetime.now(timezone.utc).isoformat()

def log(msg):
    line = f"[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {msg}"
    print(line, flush=True)
    with open(LOG_DIR / "active_pipeline_cleaner.log", "a", encoding="utf-8") as f:
        f.write(line + "\n")

def normalize_ts(v):
    if v is None:
        return None
    try:
        if isinstance(v, (int, float)):
            x = float(v)
        else:
            s = str(v).strip()
            if not s:
                return None
            if re.fullmatch(r"\d+(\.\d+)?", s):
                x = float(s)
            else:
                s2 = s.replace("Z", "+00:00")
                try:
                    dt = datetime.fromisoformat(s2)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt.timestamp()
                except Exception:
                    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M:%S.%f"):
                        try:
                            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()
                        except Exception:
                            pass
                    return None
        if x > 10_000_000_000:
            x = x / 1000.0
        if x < 1_000_000_000:
            return None
        return x
    except Exception:
        return None

def find_dbs():
    found = []
    preferred = [
        ROOT / "sentinuity_matrix.db",
        ROOT / "sentinuity_master.db",
        ROOT / "data" / "sentinuity_matrix.db",
        ROOT / "data" / "sentinuity_master.db",
        ROOT / "sentinuity_matrix.db",
        ROOT / "data" / "sentinuity_matrix.db",
        ROOT / "trading_bot.db",
        ROOT / "data" / "trading_bot.db",
    ]
    for p in preferred:
        if p.exists() and p not in found:
            found.append(p)
    for p in ROOT.rglob("*.db"):
        if _is_excluded_path(p):
            continue
        if p not in found:
            found.append(p)
    return found

# BACKUP_RECURSION_FIX_20260730 -----------------------------------------------
# Root cause of the 113 GB `backups` tree: discover_dbs() rglob'd every *.db under
# ROOT while only filtering the substring "backup". Audit trees (`audits`,
# `audit_outputs`, `_KEEP_IMPORTANT_ARCHIVES`) were therefore swept in, and
# backup_dbs() created a brand-new timestamped folder on every 10-minute loop
# with no retention cap at all. Each run re-copied the previous run's captures.
EXCLUDED_DIR_NAMES = {
    "backups", "audits", "audit_outputs", "db_backups",
    "_KEEP_IMPORTANT_ARCHIVES", "_ui_backups", "_prelaunch_patch_backups",
    "__pycache__", ".git", ".venv", "venv", "site-packages", "node_modules",
    "logs", "runtime",
}
EXCLUDED_SUFFIXES = ("-wal", "-shm", ".db-wal", ".db-shm")
BACKUP_RETENTION = max(1, int(os.environ.get("CLEANER_BACKUP_RETENTION", "5")))
BACKUP_PREFIX = "active_pipeline_cleaner_"


def _is_excluded_path(path) -> bool:
    """True when a path sits inside an excluded tree or is a sidecar journal."""
    try:
        parts = {seg.lower() for seg in Path(path).parts}
    except Exception:
        return True
    if parts & {name.lower() for name in EXCLUDED_DIR_NAMES}:
        return True
    lowered = str(path).lower()
    if "backup" in lowered or "audit" in lowered:
        return True
    return lowered.endswith(EXCLUDED_SUFFIXES)


def prune_backups(retention: int = BACKUP_RETENTION):
    """Keep the newest `retention` cleaner captures. Print kept and removed."""
    root = ROOT / "backups"
    if not root.exists():
        return [], []
    try:
        captures = sorted(
            (d for d in root.iterdir() if d.is_dir() and d.name.startswith(BACKUP_PREFIX)),
            key=lambda d: d.name, reverse=True,
        )
    except Exception as e:
        log(f"[WARN] backup prune scan failed: {e}")
        return [], []
    keep, remove = captures[:retention], captures[retention:]
    for d in keep:
        log(f"[KEEP]   {d.name}")
    for d in remove:
        try:
            shutil.rmtree(d, ignore_errors=True)
            log(f"[REMOVE] {d.name}")
        except Exception as e:
            log(f"[WARN] could not remove {d}: {e}")
    log(f"[OK] backup retention: kept {len(keep)}, removed {len(remove)} "
        f"(CLEANER_BACKUP_RETENTION={retention})")
    return keep, remove


def backup_dbs(dbs):
    backup_dir = ROOT / "backups" / (BACKUP_PREFIX + datetime.now().strftime("%Y%m%d_%H%M%S"))
    backup_dir.mkdir(parents=True, exist_ok=True)
    copied = skipped = 0
    for db in dbs:
        try:
            if _is_excluded_path(db):
                skipped += 1
                log(f"[SKIP] excluded from backup: {db}")
                continue
            # HYBRID_EDGE_SIGNOFF_20260731: the cleaner is a maintenance lane,
            # not the owner of live price databases. Copying a hot WAL database
            # with shutil can collide with price-truth writers and is not a
            # transactionally complete snapshot. Dedicated shutdown backups retain
            # these databases; the recurring cleaner skips them while online.
            if db.name.lower() in HOT_PRICE_DB_NAMES:
                skipped += 1
                log(f"[SKIP_HOT_DB_BACKUP] {db.name}")
                continue
            dest = backup_dir / db.relative_to(ROOT)
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(db, dest)
            copied += 1
        except Exception as e:
            log(f"[WARN] backup failed {db}: {e}")
    log(f"[OK] backed up {copied} db file(s) ({skipped} excluded) to {backup_dir}")
    prune_backups()

NON_TERMINAL_PROPOSAL_STATES = {
    "open", "researching", "evidence_ready", "debating",
    "awaiting_approval", "approved", "patch_generated", "staged",
    "applying", "applied", "verifying", "pending_measurement",
    "rollback_available",
}
TERMINAL_PROPOSAL_STATES = {
    "rejected_final", "superseded", "rolled_back_final",
    "measured_complete", "abandoned_by_operator",
}
PROPOSAL_TABLES = {"polaris_proposals", "qualified_proposals",
                   "proposal_queue", "council_build_proposals"}

def proposal_row_is_archivable(row):
    """F4: only TERMINAL proposals may be archived. A proposal in
    DEBATING or AWAITING_APPROVAL must survive indefinitely — deleting it
    also destroys the debate cooldown, which is keyed on proposal_id."""
    for key in ("lifecycle_state", "status", "state"):
        v = str(row.get(key) or "").strip().lower()
        if not v:
            continue
        if v in TERMINAL_PROPOSAL_STATES:
            return True
        if v in NON_TERMINAL_PROPOSAL_STATES:
            return False
    return False          # unknown state => fail closed, keep the row

def should_clean_table(table):
    t = table.lower()
    if t == "active_pipeline_stale_archive":
        return False
    if any(h in t for h in PROTECT_HINTS):
        return False
    if t in ACTIVE_EXACT_TABLES:
        return True
    return any(h in t for h in ACTIVE_HINTS)

def get_tables(con):
    return [r[0] for r in con.execute("select name from sqlite_master where type='table' order by name")]

def get_cols(con, table):
    return [r[1] for r in con.execute(f'pragma table_info("{table}")')]

def ensure_archive(con):
    con.execute("""
        create table if not exists active_pipeline_stale_archive (
            archive_id integer primary key autoincrement,
            cleaned_at text not null,
            source_table text not null,
            source_rowid text,
            reason text,
            age_seconds real,
            timestamp_col text,
            timestamp_value text,
            row_json text not null
        )
    """)

def ensure_heartbeat(con):
    con.execute("""
        create table if not exists active_pipeline_cleaner_heartbeat (
            service text primary key,
            heartbeat_at text not null,
            cutoff_seconds integer,
            deleted_last_pass integer,
            archived_last_pass integer,
            note text
        )
    """)

def classify_row(row_dict, time_cols, reason_cols, cutoff_seconds):
    now = time.time()

    for c in reason_cols[:6]:
        v = str(row_dict.get(c, "")).lower()
        if "signal_stale" in v or "expired" in v or "stale" in v:
            return True, "status_marked_stale", None, c, row_dict.get(c)

    timestamps = []
    for c in time_cols[:10]:
        ts = normalize_ts(row_dict.get(c))
        if ts:
            timestamps.append((ts, c, row_dict.get(c)))

    if not timestamps:
        return False, "no_parseable_timestamp", None, None, None

    newest_ts, newest_col, newest_val = max(timestamps, key=lambda x: x[0])
    age = now - newest_ts
    if age > cutoff_seconds:
        return True, f"older_than_{cutoff_seconds}s", age, newest_col, newest_val

    return False, "fresh", age, newest_col, newest_val

def load_open_position_mints():
    """OPEN_POSITION_COVERAGE_GUARD_20260722.

    Returns the set of mint addresses with an active paper/live position, or
    None when the canonical DB cannot be read. Callers must FAIL CLOSED on
    None for any table that carries a mint_address column: during a DB-lock
    storm or outage - exactly when marks stop arriving - deleting a position's
    last surviving price row destroys the last trusted mark and forces the
    Guardian into an invented flat close (positions 1869-1872, 2026-07-22).
    """
    try:
        db = ROOT / "sentinuity_matrix.db"
        if not db.exists():
            db = ROOT / "data" / "sentinuity_matrix.db"
        if not db.exists():
            return set()
        con = sqlite3.connect(db, timeout=10)
        try:
            con.execute("pragma busy_timeout=10000")
            cols = {r[1] for r in con.execute("pragma table_info(paper_positions)").fetchall()}
            live_states = ("'BUY_SUBMITTED','BUY_CONFIRMED_UNRESOLVED','OPEN_REAL',"
                           "'EXIT_INTENT','SELL_TRIGGERED','SELL_SUBMITTED',"
                           "'SELL_CONFIRMED_UNRESOLVED'")
            where = "status='OPEN'"
            if "live_state" in cols:
                where += f" OR UPPER(COALESCE(live_state,'')) IN ({live_states})"
            rows = con.execute(
                f"select distinct mint_address from paper_positions where {where}"
            ).fetchall()
            return {str(r[0]) for r in rows if r and r[0]}
        finally:
            con.close()
    except Exception as e:
        log(f"[OPEN_MINTS_LOAD_FAIL] {e} - mint-bearing tables will be SKIPPED this pass (fail closed)")
        return None

def clean_table(con, db_label, table, cutoff_seconds, dry_run=False, protected_mints=frozenset()):
    cols = get_cols(con, table)
    if not cols:
        return 0, 0

    time_cols = []
    reason_cols = []

    for c in cols:
        lc = c.lower()
        if lc in TIME_HINTS or any(h in lc for h in ["timestamp", "created", "updated", "seen", "priced", "snapshot", "signal_ts", "discovered", "inserted", "qualified", "received"]):
            time_cols.append(c)
        if lc in REASON_HINTS or any(h in lc for h in ["reason", "status", "state", "verdict", "decision", "error"]):
            reason_cols.append(c)

    if not time_cols and not reason_cols:
        return 0, 0

    stale = []
    protected_fresh_unscored = 0
    protected_open_position = 0
    try:
        con.row_factory = sqlite3.Row
        rows = con.execute(f'select rowid as __rowid__, * from "{table}" limit ?', (MAX_SCAN_ROWS,)).fetchall()
    except Exception as e:
        log(f"[SKIP] {db_label}::{table} rowid scan failed: {e}")
        return 0, 0

    for row in rows:
        d = dict(row)
        rid = d.pop("__rowid__", None)
        if table == "market_snapshots" and is_fresh_unscored(d, DEFAULT_SCORING_GRACE_SECONDS):
            protected_fresh_unscored += 1
            continue
        # OPEN_POSITION_COVERAGE_GUARD_20260722: never archive/delete any row
        # keyed to a mint with an active position. These rows are the position's
        # price-coverage evidence (last trusted mark, MTM history, resolver and
        # snapshot lineage). Rows become eligible again after the position
        # reaches a terminal state.
        if protected_mints and str(d.get("mint_address") or "") in protected_mints:
            protected_open_position += 1
            continue
        # HANDOFF_V1: never delete in-flight handoff rows. A latched /
        # execution_ready candidate is the executor's active queue; erasing it
        # mid-handoff was destroying evidence and racing the open path. Rows
        # are eligible again once they reach a terminal state (vetoed/exited/
        # expired) or are unlatched.
        if table == "market_snapshots":
            try:
                if (int(d.get("latched") or 0) == 1
                        or int(d.get("execution_ready") or 0) in (1, 2)
                        or str(d.get("candidate_state") or "") == "latched"):
                    continue
            except Exception:
                pass
        if table.lower() in PROPOSAL_TABLES and not proposal_row_is_archivable(d):
            protected_fresh_unscored += 1
            continue
        should_delete, reason, age, ts_col, ts_val = classify_row(d, time_cols, reason_cols, cutoff_seconds)
        if should_delete:
            stale.append((rid, d, reason, age, ts_col, ts_val))
            if len(stale) >= MAX_DELETE_PER_TABLE:
                break

    if protected_fresh_unscored:
        log(f"[PROTECTED_FRESH_UNSCORED] {db_label}::{table} rows={protected_fresh_unscored}")
    if protected_open_position:
        log(f"[PROTECTED_OPEN_POSITION] {db_label}::{table} rows={protected_open_position}")

    if not stale:
        return 0, 0

    if dry_run:
        return len(stale), len(stale)

    ensure_archive(con)

    for rid, d, reason, age, ts_col, ts_val in stale:
        con.execute(
            """
            insert into active_pipeline_stale_archive
            (cleaned_at, source_table, source_rowid, reason, age_seconds, timestamp_col, timestamp_value, row_json)
            values (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                now_iso(),
                table,
                str(rid),
                reason,
                float(age) if age is not None else None,
                ts_col,
                str(ts_val) if ts_val is not None else None,
                json.dumps(d, default=str, ensure_ascii=False),
            )
        )

    rowids = [x[0] for x in stale if x[0] is not None]
    deleted = 0
    for i in range(0, len(rowids), 900):
        part = rowids[i:i+900]
        ph = ",".join(["?"] * len(part))
        con.execute(f'delete from "{table}" where rowid in ({ph})', part)
        deleted += len(part)

    return deleted, len(stale)

def clean_once(cutoff_seconds=DEFAULT_CUTOFF_SECONDS, dry_run=False, backup=False):
    dbs = find_dbs()
    if not dbs:
        log("[WARN] no sqlite db files found")
        return 0

    if backup and not dry_run:
        backup_dbs(dbs)

    total_deleted = 0
    total_archived = 0
    scanned_tables = 0

    log("=" * 80)
    log(f"ACTIVE PIPELINE CLEANER cutoff={cutoff_seconds}s dry_run={dry_run} backup={backup}")
    _open_mints = load_open_position_mints()
    log(f"[OPEN_POSITION_GUARD] protected_mints={'FAIL_CLOSED' if _open_mints is None else len(_open_mints)}")

    for db in dbs:
        db_label = str(db.relative_to(ROOT))
        try:
            con = sqlite3.connect(db, timeout=10)
            con.execute("pragma busy_timeout=10000")
            con.row_factory = sqlite3.Row

            db_deleted = 0
            db_archived = 0

            for table in get_tables(con):
                if not should_clean_table(table):
                    continue
                # SIGNOFF_HOT_PRICE_GUARD_20260729: while any position is open,
                # bulk cleanup of hot marking tables is deferred. The existing
                # per-mint protection remains, but avoiding the scan/write/checkpoint
                # entirely prevents contention with the mark loop.
                if _open_mints and db.name.lower() in HOT_PRICE_DB_NAMES and table.lower() in HOT_PRICE_TABLES:
                    log(f"[DEFER_HOT_PRICE_TABLE] {db_label}::{table} open_positions={len(_open_mints)}")
                    continue
                scanned_tables += 1
                try:
                    _tbl_cols = {x[1] for x in con.execute(f'pragma table_info("{table}")').fetchall()}
                    if "mint_address" in _tbl_cols and _open_mints is None:
                        log(f"[SKIP_FAIL_CLOSED] {db_label}::{table} open-position mints unavailable")
                        continue
                    deleted, archived = clean_table(
                        con, db_label, table, cutoff_seconds, dry_run=dry_run,
                        protected_mints=(_open_mints or frozenset()),
                    )
                    if deleted or archived:
                        action = "WOULD_ARCHIVE_DELETE" if dry_run else "ARCHIVED_DELETED"
                        log(f"[{action}] {db_label}::{table} rows={archived}")
                    db_deleted += deleted
                    db_archived += archived
                except Exception as e:
                    log(f"[WARN] skipped {db_label}::{table}: {e}")

            if not dry_run:
                ensure_heartbeat(con)
                con.execute(
                    """
                    insert or replace into active_pipeline_cleaner_heartbeat
                    (service, heartbeat_at, cutoff_seconds, deleted_last_pass, archived_last_pass, note)
                    values (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "active_pipeline_cleaner",
                        now_iso(),
                        int(cutoff_seconds),
                        int(db_deleted),
                        int(db_archived),
                        "archive-first active pipeline cleaner",
                    )
                )
                con.commit()
                try:
                    if not (_open_mints and db.name.lower() in HOT_PRICE_DB_NAMES):
                        con.execute("pragma wal_checkpoint(passive)")
                    else:
                        log(f"[DEFER_HOT_DB_CHECKPOINT] {db_label} open_positions={len(_open_mints)}")
                except Exception:
                    pass

            con.close()
            total_deleted += db_deleted
            total_archived += db_archived

        except Exception as e:
            log(f"[WARN] db skipped {db_label}: {e}")

    heartbeat = {
        "service": "active_pipeline_cleaner",
        "heartbeat_at": now_iso(),
        "cutoff_seconds": cutoff_seconds,
        "deleted_last_pass": total_deleted,
        "archived_last_pass": total_archived,
        "dry_run": dry_run,
        "scanned_tables": scanned_tables,
    }
    (RUNTIME_DIR / "active_pipeline_cleaner.heartbeat.json").write_text(json.dumps(heartbeat, indent=2), encoding="utf-8")

    log(f"[DONE] scanned_tables={scanned_tables} archived={total_archived} deleted={total_deleted}")
    return total_deleted

def loop(cutoff_seconds=DEFAULT_CUTOFF_SECONDS, interval=600):
    log("=" * 80)
    log(f"ACTIVE PIPELINE CLEANER LOOP START interval={interval}s cutoff={cutoff_seconds}s")
    first = True
    while True:
        try:
            clean_once(cutoff_seconds=cutoff_seconds, dry_run=False, backup=first)
            first = False
        except Exception as e:
            log(f"[WARN] cleaner loop error: {e}")
        time.sleep(interval)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cutoff", type=int, default=DEFAULT_CUTOFF_SECONDS)
    ap.add_argument("--interval", type=int, default=600)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--backup", action="store_true")
    ap.add_argument("--loop", action="store_true")
    args = ap.parse_args()

    if args.loop:
        loop(cutoff_seconds=args.cutoff, interval=args.interval)
    else:
        clean_once(cutoff_seconds=args.cutoff, dry_run=args.dry_run, backup=True if not args.dry_run else False)

if __name__ == "__main__":
    main()
