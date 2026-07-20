"""
services/forge_notifier.py
===========================
Forge build notification service.
Sends alerts when forge milestones complete or patches are applied.
"""
import logging
log = logging.getLogger("forge_notifier")


def notify_milestone_complete(project_key: str, milestone_key: str, title: str) -> None:
    """Notify when a forge milestone completes."""
    log.info("[FORGE_NOTIFIER] Milestone complete: %s / %s — %s", project_key, milestone_key, title)


def notify_patch_applied(proposal_id: int, action: str, pnl_impact: float = 0.0) -> None:
    """Notify when a code patch is applied."""
    log.info("[FORGE_NOTIFIER] Patch applied: proposal=%d action=%s pnl_impact=%+.4f",
             proposal_id, action, pnl_impact)


def notify_build_stalled(reason: str) -> None:
    """Notify when the build pipeline stalls."""
    log.warning("[FORGE_NOTIFIER] Build stalled: %s", reason)
