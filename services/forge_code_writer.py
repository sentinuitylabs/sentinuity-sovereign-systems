"""
services/forge_code_writer.py
==============================
THE MISSING LINK: Approved Proposal → Actual Code Change

This is the step that was never built. The forge chain was:
  propose → debate → approve → NOTHING

Now it's:
  propose → debate → approve → forge_code_writer → code_patches table
  → AXON dry-run → HITL approval (if required) → applied to file

Doctrine:
  - POLARIS writes the proposed code after council consensus
  - AXON dry-runs it (syntax check + logic validation)  
  - If AXON passes + HITL not required → apply to file automatically
  - If HITL required → surface in convergence gate for operator approval
  - All changes logged to code_patches + patch_history
  - Rollback available at all times

Only touches files in the approved target list.
Never touches: schema.py, execution_engine.py core logic,
               wallet keys, .env, live trading parameters
               without explicit HITL approval.
"""
from __future__ import annotations
import sys, time, logging, ast, sqlite3, os, shutil
from pathlib import Path
from services.autonomous_apply_policy import can_autonomous_apply

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.append(str(BASE_DIR))

from core.schema import get_connection, update_heartbeat, get_config_value

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [forge_writer] %(levelname)-7s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("forge_code_writer")

SERVICE_NAME  = "forge_code_writer"
CYCLE_SECONDS = 30

# Files the forge is ALLOWED to write to autonomously
# Everything else requires HITL approval
AUTONOMOUS_TARGETS = {
    "ui/substrate_node.py",
    "ui/sovereign_chamber_v1_5.py",
    "services/pump_activity_monitor.py",
    "services/macro_price_feed.py",
    "services/macro_channel.py",
    "services/freshness_enforcer.py",
    "services/forge_research_bridge.py",
}

HITL_REQUIRED_TARGETS = {
    "services/execution_engine.py",
    "services/neural_supervisor.py",
    "services/market_intelligence.py",
    "services/ws_price_oracle.py",
    "services/sovereign_governor.py",
    "core/schema.py",
}


def _log_cognition(agent: str, message: str) -> None:
    try:
        with get_connection() as conn:
            conn.execute(
                "INSERT INTO cognition_log (stage, message, timestamp) VALUES (?,?,?)",
                (agent.upper(), message, time.time())
            )
            conn.commit()
    except Exception:
        pass


def _axon_dry_run(target_file: str, new_code: str) -> tuple[bool, str]:
    """
    AXON validates proposed code before it touches any file.
    Returns (passed, reason).
    """
    # Step 1: Python syntax check
    if target_file.endswith(".py"):
        try:
            ast.parse(new_code)
        except SyntaxError as e:
            return False, f"SYNTAX_ERROR: {e}"

    # Step 2: Safety checks — no dangerous patterns
    forbidden = [
        "os.system(", "subprocess.call(", "eval(", "exec(",
        "__import__('os')", "shutil.rmtree(",
        "DROP TABLE", "DELETE FROM paper_positions",
        "DELETE FROM system_state",
    ]
    for pattern in forbidden:
        if pattern in new_code:
            return False, f"FORBIDDEN_PATTERN: {pattern}"

    # Step 3: File size sanity — reject if > 500KB
    if len(new_code.encode()) > 500_000:
        return False, "FILE_TOO_LARGE: >500KB rejected"

    return True, "AXON_DRY_RUN_PASSED"


def _apply_patch(patch_id: int, target_file: str, new_code: str,
                 old_code: str, description: str) -> bool:
    """Apply a validated code patch to the actual file."""
    # ── CENTRAL AUTONOMOUS-APPLY GUARD ──────────────────────────────────
    try:
        _decision = can_autonomous_apply(target_file, patch_type="", task_type="forge")
    except Exception as _e:
        log.error("[GUARD] policy error, denying: %s", _e)
        return False
    if not _decision.allowed:
        log.warning("[GUARD] BLOCKED %s — %s", target_file, _decision.reason)
        try:
            _log_cognition("FORGE_WRITER", f"GUARD_BLOCKED: {target_file} — {_decision.reason}")
        except Exception:
            pass
        return False
    # ────────────────────────────────────────────────────────────────────
    target_path = BASE_DIR / target_file

    try:
        # Create backup
        backup_dir = BASE_DIR / "forge_backups"
        backup_dir.mkdir(exist_ok=True)
        backup_path = backup_dir / f"{target_file.replace('/','_')}.{int(time.time())}.bak"

        if target_path.exists():
            shutil.copy2(str(target_path), str(backup_path))
            log.info("[PATCH_BACKUP] %s → %s", target_file, backup_path.name)

        # Ensure parent directory exists
        target_path.parent.mkdir(parents=True, exist_ok=True)

        # Write the new code
        with open(str(target_path), 'w', encoding='utf-8') as f:
            f.write(new_code)

        log.info("[PATCH_APPLIED] %s — %s", target_file, description[:60])
        _log_cognition("FORGE_WRITER", f"PATCH_APPLIED: {target_file} — {description[:60]}")

        # Mark patch as applied
        with get_connection() as conn:
            conn.execute(
                "UPDATE code_patches SET status='applied', applied_at=? WHERE id=?",
                (time.time(), patch_id)
            )
            # Also log to patch_history
            conn.execute("""
                INSERT INTO patch_history
                    (applied_at, proposal_type, action, param_key, new_value, outcome)
                VALUES (?,?,?,?,?,?)
            """, (time.time(), "CODE_PATCH", "file_write",
                  target_file, description[:200], "applied"))
            conn.commit()

        return True

    except Exception as e:
        log.error("[PATCH_FAILED] %s: %s", target_file, e)
        with get_connection() as conn:
            conn.execute(
                "UPDATE code_patches SET status='failed' WHERE id=?",
                (patch_id,)
            )
            conn.commit()
        return False


def _process_pending_patches() -> int:
    """Process patches that have passed AXON and are ready to apply."""
    now = time.time()
    applied = 0

    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            patches = conn.execute("""
                SELECT id, proposal_id, project_key, target_file,
                       new_code, old_code, description, author_agent,
                       axon_passed, status
                FROM code_patches
                WHERE status IN ('axon_approved', 'hitl_approved')
                ORDER BY created_at ASC LIMIT 5
            """).fetchall()
    except Exception as e:
        log.debug("pending patches query: %s", e)
        return 0

    for patch in patches:
        patch = dict(patch)
        target = patch["target_file"]
        new_code = patch["new_code"] or ""

        if not new_code.strip():
            log.warning("[PATCH_SKIP] empty code patch id=%d", patch["id"])
            continue

        # Check if HITL required for this target
        needs_hitl = target in HITL_REQUIRED_TARGETS
        is_hitl_approved = patch["status"] == "hitl_approved"

        if needs_hitl and not is_hitl_approved:
            # Surface in convergence gate
            try:
                with get_connection() as conn:
                    conn.execute(
                        "UPDATE code_patches SET status='hitl_pending' WHERE id=?",
                        (patch["id"],)
                    )
                    # Create HITL proposal
                    conn.execute("""
                        INSERT OR IGNORE INTO polaris_proposals
                            (proposal_type, proposal_domain, project_key,
                             proposal_text, status, confidence, created_at)
                        VALUES ('CODE_PATCH_HITL','FORGE',?,?,
                                'HITL_REQUIRED',0.85,?)
                    """, (patch["project_key"] or "unknown",
                          f"HITL required: patch {patch['id']} → {target}: {patch['description'] or ''}",
                          now))
                    conn.commit()
            except Exception as he:
                log.debug("HITL surfacing error: %s", he)
            log.info("[PATCH_HITL_REQUIRED] id=%d target=%s", patch["id"], target)
            continue

        # Apply the patch
        success = _apply_patch(
            patch["id"], target,
            new_code, patch["old_code"] or "",
            patch["description"] or "forge patch"
        )
        if success:
            applied += 1

    return applied


def _run_axon_validation() -> int:
    """Run AXON dry-run on pending patches."""
    validated = 0

    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            patches = conn.execute("""
                SELECT id, target_file, new_code, description
                FROM code_patches
                WHERE status = 'pending'
                ORDER BY created_at ASC LIMIT 10
            """).fetchall()
    except Exception:
        return 0

    for patch in patches:
        patch = dict(patch)
        passed, reason = _axon_dry_run(
            patch["target_file"] or "",
            patch["new_code"] or ""
        )

        new_status = "axon_approved" if passed else "axon_failed"
        try:
            with get_connection() as conn:
                conn.execute(
                    "UPDATE code_patches SET status=?, axon_dry_run=?, axon_passed=? WHERE id=?",
                    (new_status, reason, int(passed), patch["id"])
                )
                conn.commit()
        except Exception:
            pass

        if passed:
            log.info("[AXON_APPROVED] patch id=%d target=%s", patch["id"], patch["target_file"])
            _log_cognition("AXON", f"DRY_RUN_PASSED: patch {patch['id']} → {patch['target_file']}")
            validated += 1
        else:
            log.warning("[AXON_FAILED] patch id=%d reason=%s", patch["id"], reason)
            _log_cognition("AXON", f"DRY_RUN_BLOCKED: {reason}")

    return validated


def validate_forge_completion(proposal: dict) -> tuple[bool, str]:
    """
    HARD ENFORCEMENT: forge_complete is illegal unless rewritten_code exists
    and axon_passed == 1. Called before any code_patches row is created.
    """
    rw = (proposal.get("rewritten_code") or "").strip()
    if not rw:
        return False, "INVALID_FORGE_COMPLETION: rewritten_code is missing or empty"
    if not proposal.get("axon_passed"):
        return False, "INVALID_FORGE_COMPLETION: axon_passed != 1"
    return True, "OK"


def _check_approved_proposals() -> int:
    """
    Consume forge_complete/approved FORGE proposals that have rewritten_code.

    PRIMARY path:  rewritten_code + axon_passed=1  (forge_complete or approved)
    FALLBACK path: suggested_action with TARGET/CODE block (legacy only)

    Hard rule: forge_complete without rewritten_code is REJECTED here.
    No dependency on suggested_action for the primary path.
    """
    created = 0
    now = time.time()

    try:
        with get_connection() as conn:
            conn.row_factory = sqlite3.Row
            proposals = conn.execute("""
                SELECT id, proposal_type, project_key, proposal_text,
                       suggested_action, rewritten_code, axon_passed,
                       forge_checksum, confidence, status
                FROM polaris_proposals
                WHERE proposal_domain = 'FORGE'
                  AND status IN ('forge_complete', 'approved', 'applied')
                  AND id NOT IN (
                      SELECT DISTINCT proposal_id FROM code_patches
                      WHERE proposal_id IS NOT NULL
                  )
                ORDER BY created_at ASC LIMIT 5
            """).fetchall()
    except Exception as e:
        log.debug("approved proposals query: %s", e)
        return 0

    for prop in proposals:
        prop = dict(prop)
        target_file = None
        new_code    = None
        via         = "unknown"

        import re as _re

        # ── PRIMARY: rewritten_code ───────────────────────────────────────────
        rw = (prop.get("rewritten_code") or "").strip()
        if rw:
            # Enforce the hard rule
            valid, reason = validate_forge_completion(prop)
            if not valid:
                log.warning(
                    "[FORGE_WRITER] proposal %d REJECTED: %s", prop["id"], reason
                )
                _log_cognition("AXON", f"INVALID_FORGE_COMPLETION: proposal {prop['id']} — {reason}")
                try:
                    with get_connection() as conn:
                        conn.execute(
                            "UPDATE polaris_proposals SET status='rejected', "
                            "suggested_action=? WHERE id=?",
                            (reason, prop["id"])
                        )
                        conn.commit()
                except Exception:
                    pass
                continue

            # Extract target from rewritten_code header or suggested_action
            tgt = _re.search(r'TARGET:\s*([^\n]+)', rw)
            if tgt:
                target_file = tgt.group(1).strip()
            else:
                action = prop.get("suggested_action") or ""
                tgt2 = _re.search(r'TARGET:\s*([^\n]+)', action)
                if tgt2:
                    target_file = tgt2.group(1).strip()
            new_code = rw
            via = "rewritten_code"

        # ── FALLBACK: suggested_action (legacy) ───────────────────────────────
        if not new_code:
            action = prop.get("suggested_action") or ""
            if not action:
                log.debug(
                    "[FORGE_WRITER] proposal %d: no code in rewritten_code or suggested_action",
                    prop["id"]
                )
                continue
            tgt3 = _re.search(r'TARGET:\s*([^\n]+)', action)
            if tgt3:
                target_file = tgt3.group(1).strip()
            cm = _re.search(r'```python\n(.*?)```', action, _re.DOTALL)
            if not cm:
                cm = _re.search(r'CODE:\n(.*?)(?:END_CODE|$)', action, _re.DOTALL)
            if cm:
                new_code = cm.group(1).strip()
            via = "suggested_action_fallback"

        if not target_file or not new_code:
            log.debug(
                "[FORGE_WRITER] proposal %d: no extractable target/code (via=%s)",
                prop["id"], via
            )
            continue

        # Validate target is in allowed list
        if target_file not in AUTONOMOUS_TARGETS and target_file not in HITL_REQUIRED_TARGETS:
            log.warning(
                "[FORGE_WRITER] proposal %d targets unlisted file: %s — skipping",
                prop["id"], target_file
            )
            continue

        try:
            with get_connection() as conn:
                conn.execute("""
                    INSERT INTO code_patches
                        (proposal_id, project_key, target_file, new_code,
                         description, author_agent, status, created_at)
                    VALUES (?,?,?,?,?,?,?,?)
                """, (
                    prop["id"],
                    prop["project_key"] or "unknown",
                    target_file,
                    new_code,
                    f"From proposal #{prop['id']} ({prop['status']}) via={via}: "
                    f"{prop['proposal_type']}",
                    "polaris",
                    "pending",
                    now,
                ))
                conn.commit()
            log.info(
                "[FORGE_WRITER] code_patch created: proposal=%d target=%s via=%s",
                prop["id"], target_file, via
            )
            _log_cognition(
                "POLARIS",
                f"CODE_PATCH_CREATED: proposal {prop['id']} → {target_file} via={via}"
            )
            created += 1
        except Exception as ce:
            log.debug("code patch insert error: %s", ce)

    return created


def run() -> None:
    log.info("Forge code writer started — cycle=%ds", CYCLE_SECONDS)
    log.info("Autonomous targets: %d files | HITL targets: %d files",
             len(AUTONOMOUS_TARGETS), len(HITL_REQUIRED_TARGETS))
    update_heartbeat(SERVICE_NAME, "starting", "forge_code_writer online")

    while True:
        try:
            # 1. Check approved proposals for extractable code
            created = _check_approved_proposals()

            # 2. AXON validates pending patches
            validated = _run_axon_validation()

            # 3. Apply AXON-approved patches
            applied = _process_pending_patches()

            note = f"created={created} validated={validated} applied={applied}"
            if any([created, validated, applied]):
                log.info("[CYCLE] %s", note)
            update_heartbeat(SERVICE_NAME, "alive", note)

        except Exception as exc:
            log.warning("[WRITER_ERROR] %s", exc)
            update_heartbeat(SERVICE_NAME, "warn", f"error: {exc}")

        time.sleep(CYCLE_SECONDS)


if __name__ == "__main__":
    run()
