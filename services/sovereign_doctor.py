#!/usr/bin/env python3
"""
Sentinuity Sovereign Doctor
===========================
Thin CLI wrapper around core.sovereign_gate_map.collect_gate_map().
The dashboard and doctor now share the same diagnostic source of truth.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.sovereign_gate_map import collect_gate_map, final_gate_verdict_text


def main() -> int:
    ap = argparse.ArgumentParser(description="Sentinuity read-only sovereign doctor")
    ap.add_argument("--once", action="store_true", help="run once and print verdict")
    ap.add_argument("--json", action="store_true", help="print JSON gate map")
    ap.add_argument("--db", default=None, help="optional sentinuity_matrix.db path")
    ap.add_argument("--intel-db", default=None, help="optional sentinuity_intelligence.db path")
    args = ap.parse_args()

    gate = collect_gate_map(args.db, args.intel_db)
    if args.json:
        print(json.dumps(gate, indent=2, sort_keys=True, default=str))
        return 0

    print("=" * 78)
    print("SENTINUITY SOVEREIGN DOCTOR — READ ONLY")
    print("=" * 78)
    print(final_gate_verdict_text(gate))

    cand = gate.get("candidates", {})
    print("\nCANDIDATE FLOW — LAST 10 MIN")
    for key in [
        "discovered_10m", "priced_10m", "qualified_10m", "latched_10m",
        "execution_ready_10m", "expired_10m", "vetoed_10m",
    ]:
        print(f"  {key:<24} {cand.get(key, 0)}")

    print("\nTOP VETO / TERMINAL REASONS")
    reasons = cand.get("top_veto_reasons") or []
    if not reasons:
        print("  none found in current window")
    for r in reasons[:8]:
        print(f"  {int(r.get('count', 0)):>4}  {r.get('reason')}")

    print("\nNOTE")
    print("  Terminal states such as vetoed/rejected/expired_stale are not counted as stuck rows.")
    print("  Paper and live are diagnosed separately; HOUR_GATE_LIVE never implies paper is locked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
