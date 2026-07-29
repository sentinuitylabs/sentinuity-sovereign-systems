# coding: utf-8
"""
services/council_apply.py — COUNCIL_REPAIR_20260729

THE single autonomous file-mutation path. Every autonomous writer must call
apply_patch() here. It exists because council_autobuilder.py previously called
apply_policy.can_autoapply() and then wrote the target directly, bypassing
autonomous_apply_policy.can_autonomous_apply() — the deny-by-default money guard
whose own docstring claims every apply path calls it.

Decision order is fixed and non-negotiable (F6):

    1. central security/money guard   autonomous_apply_policy
    2. risk-tier policy               TIER_0..TIER_4
    3. operator-approval requirement
    4. containment allowlist          COUNCIL_BUILD_ALLOWED_ROOTS
    5. base-hash verification         F7
    6. backup
    7. staged write beside target
    8. compile / test on the STAGED file
    9. atomic replace                 os.replace
   10. post-replace hash verification
   -> only now may the caller emit APPLIED

Nothing here emits ledger phases; it returns a result and lets the engine
record it. A failed write can therefore never produce APPLIED.
"""
from __future__ import annotations

import hashlib
import os
import py_compile
import shutil
import tempfile
import time
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
BACKUP_DIR = ROOT / "backups" / "council_apply"

# ── risk tiers (F: "Risk tiers") ────────────────────────────────────────────
TIER_0, TIER_1, TIER_2, TIER_3, TIER_4 = (
    "TIER_0", "TIER_1", "TIER_2", "TIER_3", "TIER_4")

#: tiers an unattended IGN-off run may apply. Tier 2 requires explicit
#: paper-build authority; tiers 3/4 are never autonomous.
AUTO_APPLY_TIERS = {TIER_0, TIER_1}
PAPER_AUTHORITY_TIERS = {TIER_2}
NEVER_AUTONOMOUS_TIERS = {TIER_3, TIER_4}


@dataclass
class ApplyResult:
    ok: bool
    reason: str
    stage: str = ""                      # last gate reached
    target: str = ""
    tier: str = ""
    backup_path: Optional[str] = None
    staged_path: Optional[str] = None
    base_sha256: Optional[str] = None
    current_sha256: Optional[str] = None
    result_sha256: Optional[str] = None
    rollback_available: bool = False
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


def sha256_file(path: Path) -> Optional[str]:
    """Hash of an existing file, or None when it does not exist yet."""
    if not path.exists():
        return None
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def allowed_roots() -> tuple[Path, ...]:
    raw = os.getenv("COUNCIL_BUILD_ALLOWED_ROOTS", "ui")
    return tuple((ROOT / r.strip()).resolve()
                 for r in raw.split(",") if r.strip())


PROTECTED_NAMES = {
    "execution_engine.py", "live_trading.py", "live_decision_contract.py",
    "price_integrity_contract.py", "ws_price_oracle.py",
    "market_intelligence.py", "ingest_pipeline.py", "system_guardian.py",
    "pattern_live_arming.py", "schema.py", "live_lane_common.py",
    # build-plane integrity: a builder that can patch its own gate has no gate
    "council_apply.py", "autonomous_apply_policy.py", "apply_policy.py",
    "council_autobuilder.py", "council_task_ledger.py",
    "council_handler_registry.py", "safe_patch_apply.py",
}


def _central_guard(target: Path, patch_type: str, task_type: str):
    """Gate 1. Import failure is fail-CLOSED, never fail-open."""
    try:
        from services.autonomous_apply_policy import can_autonomous_apply
    except Exception:
        try:
            from autonomous_apply_policy import can_autonomous_apply  # type: ignore
        except Exception:
            return False, "CENTRAL_POLICY_UNAVAILABLE_FAIL_CLOSED", None
    try:
        rel = str(target.resolve().relative_to(ROOT)).replace("\\", "/")
    except Exception:
        rel = str(target)
    try:
        decision = can_autonomous_apply(rel, patch_type=patch_type,
                                        task_type=task_type)
    except Exception as exc:
        return False, f"CENTRAL_POLICY_RAISED_FAIL_CLOSED:{exc}"[:200], None
    return bool(getattr(decision, "allowed", False)), \
        str(getattr(decision, "reason", "")), decision


def apply_patch(*,
                target_file: Path | str,
                new_content: str,
                tier: str,
                base_sha256: Optional[str],
                patch_type: str = "",
                task_type: str = "",
                operator_approved: bool = False,
                paper_authority: bool = False,
                test: Optional[Callable[[Path], bool]] = None,
                compile_py: bool = True) -> ApplyResult:
    """Run the full gate chain. Returns ok=True ONLY when the target has been
    atomically replaced and its post-replace hash verified."""
    target = Path(target_file).resolve()
    res = ApplyResult(ok=False, reason="", target=str(target), tier=tier,
                      base_sha256=base_sha256)

    # 1. central security / money guard — before anything else
    allowed, reason, _decision = _central_guard(target, patch_type, task_type)
    res.stage = "central_policy"
    if not allowed:
        res.reason = f"CENTRAL_POLICY_DENIED: {reason}"
        return res

    # 2. risk tier
    res.stage = "risk_tier"
    if tier in NEVER_AUTONOMOUS_TIERS:
        res.reason = f"TIER_REFUSED: {tier} is never autonomous"
        return res
    if tier in PAPER_AUTHORITY_TIERS and not paper_authority:
        res.reason = f"TIER_REFUSED: {tier} requires paper-build authority"
        return res
    if tier not in AUTO_APPLY_TIERS and tier not in PAPER_AUTHORITY_TIERS:
        res.reason = f"TIER_REFUSED: unknown tier {tier!r}"
        return res

    # 3. operator approval requirement
    res.stage = "operator_approval"
    if tier in PAPER_AUTHORITY_TIERS and not operator_approved and not paper_authority:
        res.reason = "OPERATOR_APPROVAL_REQUIRED"
        return res

    # 4. containment allowlist
    res.stage = "containment"
    roots = allowed_roots()
    inside = any(target == r or r in target.parents for r in roots)
    if not inside or target.name in PROTECTED_NAMES:
        res.reason = (f"BUILD_CONTAINMENT_DENIED: {target} outside "
                      f"{[str(r) for r in roots]} or protected module")
        return res

    # 5. base-hash verification (F7)
    res.stage = "base_hash"
    current = sha256_file(target)
    res.current_sha256 = current
    if base_sha256 is not None and current is not None and base_sha256 != current:
        res.reason = "STALE_BASE"
        res.detail["rebase_required"] = True
        return res
    if base_sha256 is not None and current is None:
        res.reason = "STALE_BASE"
        res.detail["rebase_required"] = True
        res.detail["note"] = "target vanished since proposal"
        return res

    # 6. backup (only when there is something to back up)
    res.stage = "backup"
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d_%H%M%S")
    backup = BACKUP_DIR / f"{target.name}.{stamp}.bak"
    try:
        if target.exists():
            shutil.copy2(target, backup)
            res.backup_path = str(backup)
            res.rollback_available = True
        else:
            res.detail["new_file"] = True
    except Exception as exc:
        res.reason = f"BACKUP_FAILED: {exc}"[:200]
        return res

    # 7. staged write beside the target (same filesystem => atomic replace)
    res.stage = "staged_write"
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_path: Optional[Path] = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".staged")
        tmp_path = Path(tmp_name)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(new_content)
            fh.flush()
            os.fsync(fh.fileno())
        res.staged_path = str(tmp_path)
    except Exception as exc:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        res.reason = f"STAGED_WRITE_FAILED: {exc}"[:200]
        return res

    # 8. compile + test the STAGED file, never the live target
    res.stage = "staged_validation"
    try:
        if compile_py and target.suffix == ".py":
            py_compile.compile(str(tmp_path), doraise=True)
        if test is not None and not test(tmp_path):
            raise RuntimeError("staged smoke test returned False")
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        res.reason = f"STAGED_VALIDATION_FAILED: {exc}"[:200]
        res.detail["target_untouched"] = True
        return res

    # 9. atomic replace
    res.stage = "atomic_replace"
    try:
        os.replace(str(tmp_path), str(target))
    except Exception as exc:
        tmp_path.unlink(missing_ok=True)
        res.reason = f"ATOMIC_REPLACE_FAILED: {exc}"[:200]
        res.detail["target_untouched"] = True
        return res

    # 10. post-replace hash verification
    res.stage = "post_replace_hash"
    expect = sha256_text(new_content)
    got = sha256_file(target)
    res.result_sha256 = got
    if got != expect:
        if res.backup_path:
            try:
                rollback(target, Path(res.backup_path))
            except Exception:
                pass
        res.reason = f"POST_REPLACE_HASH_MISMATCH expected={expect[:12]} got={str(got)[:12]}"
        return res

    res.ok = True
    res.stage = "applied"
    res.reason = "APPLIED"
    return res


def rollback(target_file: Path | str, backup_path: Path | str) -> ApplyResult:
    """Atomic rollback. Same replace discipline as apply."""
    target, backup = Path(target_file).resolve(), Path(backup_path)
    res = ApplyResult(ok=False, reason="", target=str(target),
                      backup_path=str(backup))
    if not backup.exists():
        res.reason = "BACKUP_MISSING"
        return res
    tmp_path = None
    try:
        fd, tmp_name = tempfile.mkstemp(
            dir=str(target.parent), prefix=f".{target.name}.", suffix=".rb")
        os.close(fd)
        tmp_path = Path(tmp_name)
        shutil.copy2(backup, tmp_path)
        os.replace(str(tmp_path), str(target))
    except Exception as exc:
        if tmp_path and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        res.reason = f"ROLLBACK_FAILED: {exc}"[:200]
        return res
    res.ok = True
    res.reason = "ROLLED_BACK"
    res.result_sha256 = sha256_file(target)
    return res
