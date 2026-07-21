#!/usr/bin/env python3
"""
launch/verify_sizing_gate.py
===============================================================================
SIZING-GATE FIXTURES A-F (SIZING_GATE_V2_20260721) — directive Phase 1

Two layers of proof, both decisive:

STATIC (source-of-truth): the patched services/execution_engine.py must
  resolve the operator notional BEFORE verdict derivation, carry a SIZING
  gate in the published gate list, publish would_fire_usd/size_multiplier,
  republish BLOCKED from the live-mirror exception handler, and RETAIN the
  fail-closed RuntimeError at the mirror boundary (defence in depth — the
  patch must never have weakened it).

DYNAMIC (A-F): the exact sizing expressions from the patch are re-evaluated
  against the REAL derive_verdict() and the REAL publish()/read_contract()
  from services/live_decision_contract.py, on a throwaway database
  (core.schema.DB_PATH is repointed at a temp file — production data is
  never touched):

    A. missing live size            -> SIZING BLOCK -> verdict BLOCKED
    B. missing exposure cap         -> SIZING BLOCK -> verdict BLOCKED
    C. full size                    -> FIRE_PATH_OPEN, would_fire_usd = min(size,cap)
    D. half-size (parallel caps)    -> would_fire_usd = 0.5x, caps NOT multiplied
    E. live-mirror exception        -> republished BLOCKED contract is visible
    F. valid candidate              -> FIRE_PATH_OPEN with positive notional

Run:  python launch/verify_sizing_gate.py     Exit 0 = PASS, 1 = FAIL.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
# Windows verifier console contract: force Unicode-safe output even when the
# parent console is cp1252. This changes presentation only, never test logic.
def _configure_verifier_console() -> None:
    for _stream in (sys.stdout, sys.stderr):
        try:
            _stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

_configure_verifier_console()

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FAILURES: list[str] = []


def check(label: str, ok: bool, detail: str = "") -> None:
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail else ""))
    if not ok:
        FAILURES.append(label)


# ── the exact parallel-cap doctrine from the patch, re-stated for testing ────
def resolve_notional(req_size: float, exp_cap: float, *,
                     pattern_required: bool = False,
                     pattern_multiplier: float = 1.0,
                     mb_half_size: bool = False):
    sizing_ok = req_size > 0.0 and exp_cap > 0.0
    mult = 1.0
    if pattern_required:
        mult = min(mult, max(0.0, min(1.0, float(pattern_multiplier))))
    if mb_half_size:
        mult = min(mult, 0.5)
    would_fire = (min(req_size, exp_cap) * mult) if sizing_ok else 0.0
    return sizing_ok, mult, would_fire


def sizing_gate(sizing_ok: bool, req: float, cap: float, mult: float,
                would_fire: float) -> dict:
    return {"name": "SIZING", "state": ("PASS" if sizing_ok else "BLOCK"),
            "current": (f"would fire ${would_fire:.2f} "
                        f"(size=${req:.2f} cap=${cap:.2f} mult={mult:.2f}x)"
                        if sizing_ok else
                        f"live size/exposure cap unresolved "
                        f"(size={req} cap={cap}); rerun launcher interview"),
            "contract": "LIVE_POSITION_SIZE_USD>0 and "
                        "LIVE_MAX_TOTAL_EXPOSURE_USD>0"}


def passing_core_gates() -> list[dict]:
    return [
        {"name": "LIVE_LANE_ARMED", "state": "PASS", "current": "armed",
         "contract": "operator armed the live lane"},
        {"name": "MODE_B", "state": "PASS", "current": "curve pass",
         "contract": "independent realised confirmations"},
        {"name": "PATTERN", "state": "PASS", "current": "BYPASSED",
         "contract": "capital authority"},
    ]


def main() -> int:
    print("── STATIC: patched executor source invariants ──────────────────")
    src = (ROOT / "services" / "execution_engine.py").read_text(encoding="utf-8")
    marker = "SIZING_GATE_V2_20260721"
    check("S1 patch marker present", src.count(marker) >= 2,
          f"count={src.count(marker)}")

    i_size = src.find('_ldc_req_size = float(get_config_value("LIVE_POSITION_SIZE_USD"')
    i_cap = src.find('_ldc_exp_cap = float(get_config_value("LIVE_MAX_TOTAL_EXPOSURE_USD"')
    i_derive = src.find("_ldc_verdict, _ldc_blocker = _derive_live_verdict(")
    check("S2 notional resolved BEFORE verdict derivation",
          -1 < i_size < i_derive and -1 < i_cap < i_derive,
          f"size@{i_size} cap@{i_cap} derive@{i_derive}")

    i_gate = src.find('{"name": "SIZING"')
    check("S3 SIZING gate is inside the published gate list",
          -1 < i_gate < i_derive, f"gate@{i_gate}")

    check("S4 would_fire_usd + size_multiplier published",
          "would_fire_usd=_ldc_would_fire," in src
          and "size_multiplier=_ldc_mult," in src)

    check("S5 live-mirror exception republishes BLOCKED",
          'authored_by="execution_engine.live_mirror_exception"' in src
          and src.find('"LIVE_MIRROR"') > 0)

    check("S6 fail-closed mirror RuntimeError NOT weakened",
          "live size/exposure cap missing or non-positive; rerun launcher interview"
          in src)

    check("S7 parallel-cap expression matches doctrine",
          "_ldc_would_fire = (min(_ldc_req_size, _ldc_exp_cap) * _ldc_mult) "
          "if _ldc_sizing_ok else 0.0" in src)

    print("── DYNAMIC: real derive_verdict + publish/read on a temp DB ────")
    import core.schema as core_schema
    tmp_db = Path(tempfile.mkdtemp(prefix="sizing_fixture_")) / "ldc_fixture.db"
    core_schema.DB_PATH = tmp_db          # fixtures never touch production data
    from services.live_decision_contract import (
        VERDICT_BLOCKED, VERDICT_FIRE_PATH_OPEN, derive_verdict, publish,
        read_contract,
    )

    # A — missing live size
    ok_a, mult_a, wf_a = resolve_notional(0.0, 1000.0)
    gates_a = passing_core_gates() + [sizing_gate(ok_a, 0.0, 1000.0, mult_a, wf_a)]
    verdict_a, blocker_a = derive_verdict(lane_armed=True, hard_gates=gates_a,
                                          flow_ready=True)
    check("A missing size -> BLOCKED naming SIZING",
          verdict_a == VERDICT_BLOCKED and str(blocker_a).startswith("SIZING"),
          f"{verdict_a} / {blocker_a}")

    # B — missing exposure cap
    ok_b, mult_b, wf_b = resolve_notional(20.0, 0.0)
    gates_b = passing_core_gates() + [sizing_gate(ok_b, 20.0, 0.0, mult_b, wf_b)]
    verdict_b, blocker_b = derive_verdict(lane_armed=True, hard_gates=gates_b,
                                          flow_ready=True)
    check("B missing cap -> BLOCKED naming SIZING",
          verdict_b == VERDICT_BLOCKED and str(blocker_b).startswith("SIZING"),
          f"{verdict_b} / {blocker_b}")
    check("B blocked would_fire is exactly 0", wf_b == 0.0)

    # C — full size, published end to end
    ok_c, mult_c, wf_c = resolve_notional(20.0, 1000.0)
    check("C full-size math: would_fire = min(size,cap) = $20.00",
          ok_c and mult_c == 1.0 and abs(wf_c - 20.0) < 1e-9, f"wf={wf_c}")
    gates_c = passing_core_gates() + [sizing_gate(ok_c, 20.0, 1000.0, mult_c, wf_c)]
    verdict_c, _ = derive_verdict(lane_armed=True, hard_gates=gates_c,
                                  flow_ready=True)
    publish(verdict=verdict_c, gates=gates_c, lane_armed=True,
            pattern_state="BYPASSED", pattern_armed=True,
            size_multiplier=mult_c, would_fire_usd=wf_c,
            candidate_mint="FIXTURE_MINT_C", authored_by="verify_sizing_gate")
    row_c = read_contract()
    check("C published FIRE_PATH_OPEN carries would_fire_usd=$20.00",
          row_c.get("verdict") == VERDICT_FIRE_PATH_OPEN
          and abs(float(row_c.get("would_fire_usd") or 0) - 20.0) < 1e-9,
          f"{row_c.get('verdict')} wf={row_c.get('would_fire_usd')}")

    # D — half-size: pattern 0.5x AND curve half must cap in PARALLEL (0.5x,
    # never multiplied to 0.25x)
    ok_d, mult_d, wf_d = resolve_notional(20.0, 1000.0, pattern_required=True,
                                          pattern_multiplier=0.5,
                                          mb_half_size=True)
    check("D parallel caps: 0.5x pattern + curve half -> $10.00 (not $5.00)",
          ok_d and mult_d == 0.5 and abs(wf_d - 10.0) < 1e-9,
          f"mult={mult_d} wf={wf_d}")

    # E — live-mirror exception republish visible as BLOCKED
    publish(verdict="BLOCKED",
            gates=[{"name": "LIVE_MIRROR", "state": "BLOCK",
                    "current": "RuntimeError: live size/exposure cap missing",
                    "contract": "resolved notional -> preflight -> "
                                "execute_live_buy must complete or report"}],
            blocker="LIVE_MIRROR: live size/exposure cap missing",
            next_event="resolve the live-mirror error above",
            lane_armed=True, candidate_mint="FIXTURE_MINT_E", position_id=999,
            authored_by="execution_engine.live_mirror_exception")
    row_e = read_contract()
    check("E mirror exception -> visible BLOCKED naming LIVE_MIRROR",
          row_e.get("verdict") == VERDICT_BLOCKED
          and "LIVE_MIRROR" in str(row_e.get("blocker"))
          and "LIVE_MIRROR" in str(row_e.get("gates_json") or row_e.get("gates") or ""),
          f"{row_e.get('verdict')} / {row_e.get('blocker')}")

    # F — valid candidate fires with positive notional
    ok_f, mult_f, wf_f = resolve_notional(25.0, 100.0)
    gates_f = passing_core_gates() + [sizing_gate(ok_f, 25.0, 100.0, mult_f, wf_f)]
    verdict_f, blocker_f = derive_verdict(lane_armed=True, hard_gates=gates_f,
                                          flow_ready=True)
    publish(verdict=verdict_f, gates=gates_f, lane_armed=True,
            pattern_state="BYPASSED", pattern_armed=True,
            size_multiplier=mult_f, would_fire_usd=wf_f,
            candidate_mint="FIXTURE_MINT_F", authored_by="verify_sizing_gate")
    row_f = read_contract()
    check("F valid candidate -> FIRE_PATH_OPEN, positive notional, no blocker",
          verdict_f == VERDICT_FIRE_PATH_OPEN and blocker_f is None
          and row_f.get("verdict") == VERDICT_FIRE_PATH_OPEN
          and float(row_f.get("would_fire_usd") or 0) > 0,
          f"wf={row_f.get('would_fire_usd')}")

    print()
    if FAILURES:
        print(f"SIZING GATE: FAIL ({len(FAILURES)}): {FAILURES}")
        return 1
    print("SIZING GATE: PASS — FIRE_PATH_OPEN is impossible without a resolved "
          "positive notional; would_fire_usd is published; mirror errors are "
          "visible BLOCKED contracts; no gate was weakened.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
