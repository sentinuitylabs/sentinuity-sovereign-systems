from __future__ import annotations

"""
launch/apply_debate_quorum_routing.py
===============================================================================
COUNCIL DEGRADED-PROGRESS ROUTING (COUNCIL_DEGRADED_ROUTING_20260802)

Directive B3 asks for a critic-degraded progress mode. It already exists.

services/debate_quorum.py implements the entire doctrine — deterministic
structural-critic substitution, degraded quorum, transparent confidence decay
(x0.8 per substitution), 600s cooldown and unique attempt identity. Its own
docstring says "NEVER emits rounds=0 death loops". council_autobuilder uses it.

services/debate_engine.py does not import it. It runs the older path:

  * run_debate() returns rounds=0 and halts the moment IVARIS is unreachable
    (debate_engine.py:682-689);
  * get_open_proposals() then re-selects status='critic_unavailable' rows at
    HIGHEST priority every 60s with no backoff, no failure count and no retry
    timestamp (lines 137-139, comment: "critic_unavailable proposals get
    priority retry").

That pairing is the retry storm. This patch is therefore a ROUTING change, not
a new feature. It makes three surgical edits:

  1. Narrow the critic-unavailable detector. The current test is
     any("unavailable" in objection) — so a WORKING critic that objects
     "liquidity data is unavailable" is misread as a dead critic and the
     proposal is killed. Detection now keys on the explicit _ivaris_failed flag
     that IvarisClient already sets.
  2. On genuine critic outage, obtain a DEGRADED verdict from debate_quorum
     instead of returning rounds=0, and record the durable state
     RESEARCH_COMPLETE_CRITIC_PENDING with the failure count and next retry.
     Degraded verdicts can NEVER declare consensus or auto-apply: HITL and the
     Tier B/C policy in apply_policy still gate everything downstream.
  3. Honour debate_quorum's cooldown when selecting proposals, so a blocked
     proposal is retried on a backoff rather than every cycle.

SCOPE: this does not enable live capital, touch Mode B, enforce would_veto,
change live sizing, or weaken the canary governor. It cannot apply a patch —
it only changes which verdict a blocked proposal receives and how often it is
reconsidered.

IMPORTANT: apply this AFTER the substrate pack is verified, and watch the first
Council cycle. I could not exercise the LLM paths offline, so the first live
cycle is the real test. Rollback is one command.

Usage:
    python launch/apply_debate_quorum_routing.py --dry-run
    python launch/apply_debate_quorum_routing.py --apply
    python launch/apply_debate_quorum_routing.py --rollback
"""

import argparse
import py_compile
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
TARGET = ROOT / "services" / "debate_engine.py"
BACKUP_DIR = ROOT / "backups" / "council_degraded_routing"
TAG = "COUNCIL_DEGRADED_ROUTING_20260802"


# ── edit 1: narrow the over-broad critic-outage detector ─────────────────────
OLD_DETECTOR = '''    verdict_text = str(ivaris_verdict.get("verdict", "")).lower()
    objections   = ivaris_verdict.get("objections", [])
    api_blocked  = (
        "api unavailable" in verdict_text
        or any("api unavailable" in str(o).lower() for o in objections)
        or any("unavailable" in str(o).lower() for o in objections)
    )'''

NEW_DETECTOR = '''    verdict_text = str(ivaris_verdict.get("verdict", "")).lower()
    objections   = ivaris_verdict.get("objections", [])
    # COUNCIL_DEGRADED_ROUTING_20260802: the previous test was
    #     any("unavailable" in objection)
    # which misread a WORKING critic. "liquidity data is unavailable" is a
    # substantive objection, not a dead model route, and it silently killed the
    # proposal. IvarisClient.critique already sets _ivaris_failed=True on a real
    # provider outage, so that flag is the authority. The phrase tests remain
    # only as a fallback for the exact strings the client emits itself.
    api_blocked  = bool(ivaris_verdict.get("_ivaris_failed")) or (
        "api unavailable" in verdict_text
        or any("ivaris unavailable" in str(o).lower() for o in objections)
        or any("api unavailable" in str(o).lower() for o in objections)
    )'''


# ── edit 2: degraded progress instead of a rounds=0 halt ─────────────────────
OLD_HALT = '''    if api_blocked:
        log.warning("DEBATE: IVARIS API unavailable — marking CRITIC_UNAVAILABLE, blocking proposal")
        return {
            "consensus":          False,
            "rounds":             0,
            "final_confidence":   0.0,
            "final_objections":   ["IVARIS model route unavailable — proposal cannot be critiqued"],
            "transcript":         transcript,
            "critic_unavailable": True,
        }'''

NEW_HALT = '''    if api_blocked:
        # COUNCIL_DEGRADED_ROUTING_20260802: one unavailable model must not
        # freeze the Council. services/debate_quorum.py already implements the
        # degraded-quorum doctrine used by council_autobuilder — deterministic
        # structural critic, transparent confidence decay, cooldown and attempt
        # identity. Route through it instead of returning rounds=0 forever.
        #
        # A degraded verdict is NOT consensus. It records that research and
        # structural validation completed while the critic was down, so the
        # critic reviews an accumulated dossier on return rather than starting
        # from zero. HITL and apply_policy still gate everything downstream.
        log.warning("DEBATE: IVARIS route unavailable — entering "
                    "RESEARCH_COMPLETE_CRITIC_PENDING via degraded quorum")
        degraded = {}
        try:
            from services import debate_quorum
            degraded = debate_quorum.run_debate(
                {
                    "proposal_id": proposal.get("id"),
                    "proposal_type": proposal.get("proposal_type"),
                    "proposal_text": proposal.get("proposal_text"),
                    "suggested_action": proposal.get("suggested_action"),
                    "files": proposal.get("files", []) or [],
                    "diff_chars": proposal.get("diff_chars") or 0,
                    "compile_ok": proposal.get("compile_ok"),
                    "test_cmd": proposal.get("test_cmd"),
                },
                risk_tier=str(proposal.get("risk_tier") or "A"),
            )
            transcript.append({
                "round": 0, "speaker": "DEGRADED_QUORUM",
                "action": "structural_substitution", "result": degraded,
            })
        except Exception as exc:  # noqa: BLE001
            log.warning("degraded quorum unavailable: %s", exc)
            degraded = {"verdict": "DEGRADED_QUORUM_UNAVAILABLE",
                        "substitutions": [], "confidence": 0.0}

        return {
            # Never consensus while the critic is down, regardless of the
            # structural verdict. Degraded work stages; it does not approve.
            "consensus":          False,
            "rounds":             0,
            "final_confidence":   float(degraded.get("confidence") or 0.0),
            "final_objections":   ["IVARIS model route unavailable — structural "
                                   "review completed, critic review pending"],
            "transcript":         transcript,
            "critic_unavailable": True,
            "degraded_verdict":   degraded.get("verdict"),
            "degraded_quorum":    degraded.get("quorum"),
            "substitutions":      degraded.get("substitutions", []),
            "degraded_stage":     "RESEARCH_COMPLETE_CRITIC_PENDING",
        }'''


# ── edit 3: honour the cooldown when selecting work ──────────────────────────
OLD_SELECT = '''        result = []
        for r in rows:'''

NEW_SELECT = '''        # COUNCIL_DEGRADED_ROUTING_20260802: the query above deliberately
        # prioritises critic_unavailable rows, which with no backoff meant the
        # same dead proposal was re-debated every 60s indefinitely. Skip any
        # proposal still inside its debate_quorum cooldown window.
        try:
            from services.debate_quorum import cooldown_active
            rows = [r for r in rows if not cooldown_active(r["id"])]
        except Exception:
            pass

        result = []
        for r in rows:'''


EDITS = [
    ("narrow critic-outage detector", OLD_DETECTOR, NEW_DETECTOR),
    ("degraded progress instead of rounds=0 halt", OLD_HALT, NEW_HALT),
    ("honour debate_quorum cooldown on selection", OLD_SELECT, NEW_SELECT),
]


def _log(message: str) -> None:
    print(f"[{TAG}] {message}", flush=True)


def plan(source: str) -> tuple[bool, list]:
    findings, ok = [], True
    for name, old, _ in EDITS:
        count = source.count(old)
        findings.append(f"{name}: {count} match(es)")
        if count != 1:
            ok = False
    if "debate_quorum" in source and "COUNCIL_DEGRADED_ROUTING" in source:
        findings.append("ALREADY APPLIED")
        ok = False
    return ok, findings


def apply_patch(dry_run: bool) -> int:
    if not TARGET.exists():
        _log(f"target missing: {TARGET}")
        return 2
    source = TARGET.read_text(encoding="utf-8")
    ok, findings = plan(source)
    for finding in findings:
        _log(finding)
    if not ok:
        _log("ABORT: preconditions not met. The file has drifted from the audited "
             "revision, or the patch is already applied. Nothing written.")
        return 3
    if dry_run:
        _log("DRY RUN — all three anchors matched exactly once. Nothing written.")
        return 0

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"debate_engine.py.{stamp}"
    shutil.copy2(TARGET, backup)
    _log(f"backup: {backup}")

    patched = source
    for name, old, new in EDITS:
        patched = patched.replace(old, new, 1)
        _log(f"applied: {name}")
    TARGET.write_text(patched, encoding="utf-8")

    try:
        py_compile.compile(str(TARGET), doraise=True)
        _log("compile OK")
    except Exception as exc:  # noqa: BLE001
        _log(f"COMPILE FAILED: {exc} — restoring")
        shutil.copy2(backup, TARGET)
        return 4

    _log("APPLIED — COUNCIL_STAGING_WHILE_OFFLINE")
    _log("Watch the first Council cycle: expect status transitions to "
         "RESEARCH_COMPLETE_CRITIC_PENDING with DEGRADED_QUORUM verdicts, and "
         "debate_attempts rows appearing with degraded=1.")
    _log(f"rollback: python {Path(__file__).name} --rollback")
    return 0


def rollback() -> int:
    if not BACKUP_DIR.exists():
        _log("no backups found")
        return 1
    backups = sorted(BACKUP_DIR.glob("debate_engine.py.*"))
    if not backups:
        _log("no backups found")
        return 1
    latest = backups[-1]
    shutil.copy2(latest, TARGET)
    _log(f"restored from {latest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    args = parser.parse_args()
    if args.rollback:
        return rollback()
    if args.apply:
        return apply_patch(dry_run=False)
    if args.dry_run:
        return apply_patch(dry_run=True)
    parser.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
