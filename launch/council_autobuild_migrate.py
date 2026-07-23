# coding: utf-8
"""
launch/council_autobuild_migrate.py — COUNCIL_AUTOBUILD_20260723_R2
PRODUCTION SCHEMA MIGRATION ONLY. Idempotent. Seeds NO demonstration tasks
(use tools/seed_council_proof_task.py for that, operator-invoked).

Quarantine contract (R2 — evidence-based, NEVER price-movement based):
  AUTO-QUARANTINE (pnl_eligible=0, is_legacy=1, SUSPECT_SYNTHETIC) only when a
  durable indicator proves synthetic/legacy origin:
    - explicit synthetic/test price_source
    - known legacy/test strategy tag
    - explicit is_test marker already set
    - entry before operator-set canonical epoch (SUBSTRATE_CANONICAL_EPOCH_TS,
      applied ONLY if that config key exists)
  UNCERTAIN (no originating signal reference AND untrusted source):
    entry_truth_status='REVIEW_REQUIRED' — pnl_eligible UNCHANGED, row visible.
  Post-entry price movement is NOT evidence. A +80% winner or -50% loser stays
  fully PnL-eligible.
ROLLBACK: UPDATE substrate_positions SET pnl_eligible=1,is_legacy=0,
          entry_truth_status='UNVERIFIED';
"""
from __future__ import annotations
import sqlite3, sys, time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DB = ROOT / "sentinuity_matrix.db"

SYNTHETIC_SOURCES = ("synthetic", "test", "fixture", "demo", "seed")
LEGACY_STRATEGIES = ("legacy", "test", "demo", "synthetic")


def _q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _first(cols: set[str], *names: str) -> str | None:
    lower = {c.lower(): c for c in cols}
    for name in names:
        if name.lower() in lower:
            return lower[name.lower()]
    return None


def migrate(db_path: Path | None = None) -> dict:
    db = Path(db_path or DB)
    from services.council_autobuilder import ensure_schema
    ensure_schema(db)
    out = {"quarantine_cols": [], "flagged_synthetic": 0,
           "review_required": 0, "seeded": 0, "warnings": []}
    c = sqlite3.connect(str(db), timeout=10); c.row_factory = sqlite3.Row
    try:
        c.execute("PRAGMA busy_timeout=8000")
        cols = {r[1] for r in c.execute("PRAGMA table_info(substrate_positions)")}
        if cols:
            for name, decl in (("entry_truth_status","TEXT DEFAULT 'UNVERIFIED'"),
                               ("is_test","INTEGER DEFAULT 0"),
                               ("is_legacy","INTEGER DEFAULT 0"),
                               ("pnl_eligible","INTEGER DEFAULT 1")):
                if name not in cols:
                    c.execute(f"ALTER TABLE substrate_positions ADD COLUMN {_q(name)} {decl}")
                    out["quarantine_cols"].append(name)
                    cols.add(name)

            # Evidence may live under different historical column names. Build
            # predicates only from columns that really exist in this database.
            source_cols = [x for x in (
                _first(cols, "price_source"),
                _first(cols, "source"),
                _first(cols, "mark_source"),
            ) if x]
            strategy_col = _first(cols, "strategy", "strategy_name")
            opened_col = _first(cols, "opened_at", "created_at", "entry_time")
            signal_col = _first(cols, "signal_ref", "strategy_signal_id",
                                "opportunity_id", "proposal_id")

            evidence_predicates = []
            for col in source_cols:
                for marker in SYNTHETIC_SOURCES:
                    evidence_predicates.append(
                        f"lower(COALESCE(CAST({_q(col)} AS TEXT),'')) LIKE '%{marker}%'")
            if strategy_col:
                for marker in LEGACY_STRATEGIES:
                    evidence_predicates.append(
                        f"lower(COALESCE(CAST({_q(strategy_col)} AS TEXT),'')) = '{marker}'")
            evidence_predicates.append("COALESCE(is_test,0)=1")

            epoch = None
            try:
                r = c.execute("SELECT value FROM system_config WHERE key=?",
                              ("SUBSTRATE_CANONICAL_EPOCH_TS",)).fetchone()
                epoch = float(r[0]) if r and r[0] else None
            except Exception:
                epoch = None
            if epoch and opened_col:
                evidence_predicates.append(
                    f"COALESCE(CAST({_q(opened_col)} AS REAL),0) < {float(epoch)}")

            if evidence_predicates:
                cur = c.execute(f"""
                    UPDATE substrate_positions
                    SET entry_truth_status='SUSPECT_SYNTHETIC', is_legacy=1,
                        pnl_eligible=0
                    WHERE COALESCE(entry_truth_status,'UNVERIFIED')
                            <> 'SUSPECT_SYNTHETIC'
                      AND ({' OR '.join(evidence_predicates)})
                """)
                out["flagged_synthetic"] = max(cur.rowcount, 0)

            # REVIEW_REQUIRED is non-destructive: it never changes pnl_eligible.
            # Only run it when both an origin-reference column and at least one
            # source column exist. Historical schemas lacking either are left
            # UNVERIFIED rather than guessed about.
            if signal_col and source_cols:
                trusted = ("coingecko", "dexscreener", "oracle", "matrix",
                           "birdeye", "jupiter", "helius", "chainstack",
                           "quicknode")
                trusted_pred = " OR ".join(
                    f"lower(COALESCE(CAST({_q(col)} AS TEXT),'')) IN "
                    f"({','.join(repr(x) for x in trusted)})"
                    for col in source_cols)
                cur = c.execute(f"""
                    UPDATE substrate_positions
                    SET entry_truth_status='REVIEW_REQUIRED'
                    WHERE COALESCE(entry_truth_status,'UNVERIFIED')='UNVERIFIED'
                      AND COALESCE(CAST({_q(signal_col)} AS TEXT),'') = ''
                      AND NOT ({trusted_pred})
                """)
                out["review_required"] = max(cur.rowcount, 0)
            elif not signal_col:
                out["warnings"].append(
                    "No signal-reference column found; uncertain rows left UNVERIFIED")
            elif not source_cols:
                out["warnings"].append(
                    "No source column found; uncertain rows left UNVERIFIED")
        c.commit()
    finally:
        c.close()
    return out


if __name__ == "__main__":
    import json
    print(json.dumps(migrate(), indent=2))
