#!/usr/bin/env python3
"""
SENTINUITY — EDGE MEASUREMENT SPINE PATCHER

Applies the minimum viable production change: one fail-safe ledger call at the
end of market_intelligence._write_qualifier_result, plus capture of the two
calibrator diagnostics that are currently computed and discarded.

Design intent: exactly ONE production file is modified, by anchored insertion
rather than replacement, because market_intelligence.py is ~96KB of working
pipeline and a full-file swap is the single most common way an audit pack
breaks a system it was meant to measure.

Usage:
    python launch/apply_edge_measurement_spine.py --verify
    python launch/apply_edge_measurement_spine.py --apply
    python launch/apply_edge_measurement_spine.py --rollback

Guarantees:
  * Idempotent -- re-running --apply is a no-op once applied.
  * Every anchor must match EXACTLY ONCE or nothing is written.
  * Timestamped backup taken before any write.
  * py_compile check after write; automatic restore if compilation fails.
  * Changes no gate, threshold, timestamp, or entry behaviour.
"""
from __future__ import annotations

import argparse
import hashlib
import py_compile
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "services" / "market_intelligence.py"
MARKER = "EDGE_MEASUREMENT_SPINE_20260801"

# ── Anchor 1: capture calibrator diagnostics that are currently discarded ────
A1_OLD = '''    trading_confidence = 0.0
    confidence_source = "calibrator_unavailable"
    if quality_status == "qualified":'''

A1_NEW = '''    trading_confidence = 0.0
    confidence_source = "calibrator_unavailable"
    # EDGE_MEASUREMENT_SPINE_20260801: diagnostics for the measurement ledger.
    _cal_evidence = None
    _cal_risk = None
    if quality_status == "qualified":'''

A2_OLD = '''            confidence_source = str(_cal.confidence_source or "multi_factor")'''

A2_NEW = '''            confidence_source = str(_cal.confidence_source or "multi_factor")
            _cal_evidence = getattr(_cal, "evidence_count", None)
            _cal_risk = getattr(_cal, "risk_penalty", None)'''

# ── Anchor 3: the ledger write, at the very end of _write_qualifier_result ───
A3_OLD = '''    except Exception:
        pass


def _mark_qualifier_error(row_id: int, reason: str) -> None:'''

A3_NEW = '''    except Exception:
        pass

    # EDGE_MEASUREMENT_SPINE_20260801:
    # One durable audit row per evaluated candidate -- admitted OR rejected.
    # Measurement only. Writes solely to edge_confidence_ledger. Changes no
    # gate, no threshold, no timestamp, and no entry behaviour. Fail-safe by
    # construction: a ledger fault must never become a qualification fault.
    try:
        from services.edge_ledger import record_candidate as _edge_record
        _edge_record(
            snapshot_id=row_id,
            metrics=metrics,
            quality_status=quality_status,
            quality_reason=quality_reason,
            calibrated_confidence=trading_confidence,
            confidence_source=confidence_source,
            evidence_count=_cal_evidence,
            risk_penalty=_cal_risk,
        )
    except Exception:
        pass


def _mark_qualifier_error(row_id: int, reason: str) -> None:'''

ANCHORS = (("calibrator diagnostics init", A1_OLD, A1_NEW),
           ("calibrator diagnostics capture", A2_OLD, A2_NEW),
           ("ledger write hook", A3_OLD, A3_NEW))


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()[:16]


def verify() -> int:
    if not TARGET.exists():
        print(f"FAIL  target not found: {TARGET}")
        return 2
    src = TARGET.read_text(encoding="utf-8", errors="replace")
    print(f"target : {TARGET}")
    print(f"sha256 : {sha(TARGET)}  ({TARGET.stat().st_size} bytes)")
    if MARKER in src:
        print("state  : ALREADY APPLIED")
        return 0
    print("state  : NOT APPLIED")
    ok = True
    for name, old, _new in ANCHORS:
        n = src.count(old)
        flag = "OK" if n == 1 else "FAIL"
        if n != 1:
            ok = False
        print(f"  [{flag}] anchor {n}x  {name}")
    if not ok:
        print("\nRefusing to patch: anchors did not match exactly once.")
        print("The target file differs from the audited tree. Do not force this.")
        return 1
    print("\nAll anchors matched exactly once. Safe to --apply.")
    return 0


def apply() -> int:
    rc = verify()
    if rc != 0:
        return rc
    src = TARGET.read_text(encoding="utf-8", errors="replace")
    if MARKER in src:
        print("Already applied. No change.")
        return 0

    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = TARGET.with_suffix(f".py.pre_edge_spine.{stamp}")
    shutil.copy2(TARGET, backup)
    print(f"backup : {backup.name}")

    out = src
    for name, old, new in ANCHORS:
        if out.count(old) != 1:
            print(f"FAIL  anchor drifted mid-apply: {name}")
            shutil.copy2(backup, TARGET)
            return 1
        out = out.replace(old, new, 1)

    TARGET.write_text(out, encoding="utf-8", newline="")
    try:
        py_compile.compile(str(TARGET), doraise=True)
    except py_compile.PyCompileError as e:
        print(f"FAIL  compilation error, restoring backup:\n{e}")
        shutil.copy2(backup, TARGET)
        return 1

    print(f"sha256 : {sha(TARGET)}  (patched)")
    print("APPLIED. Run the test suite before launching.")
    return 0


def rollback() -> int:
    backups = sorted(TARGET.parent.glob("market_intelligence.py.pre_edge_spine.*"))
    if not backups:
        print("No edge-spine backup found.")
        return 1
    latest = backups[-1]
    shutil.copy2(latest, TARGET)
    print(f"Restored from {latest.name}")
    print(f"sha256 : {sha(TARGET)}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--verify", action="store_true")
    g.add_argument("--apply", action="store_true")
    g.add_argument("--rollback", action="store_true")
    a = ap.parse_args()
    sys.exit(verify() if a.verify else apply() if a.apply else rollback())
