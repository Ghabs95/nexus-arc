"""Workflow tier resolution and workflow-label backfill helpers."""

import asyncio
import logging

logger = logging.getLogger(__name__)


def _emit_alert(*args, **kwargs):
    try:
        from nexus.core.integrations.notifications import emit_alert
    except Exception:
        return None
    return emit_alert(*args, **kwargs)


def _get_git_platform(repo: str, *, project_name: str | None):
    from nexus.core.orchestration.nexus_core_helpers import get_git_platform

    return get_git_platform(repo, project_name=project_name)


def _get_sop_tier_from_issue(issue_num: str, project_name: str, *, repo_override: str) -> str | None:
    from nexus.core.runtime.agent_launcher import get_sop_tier_from_issue

    return get_sop_tier_from_issue(issue_num, project_name, repo_override=repo_override)


def _state_manager():
    from nexus.core.state_manager import HostStateManager

    return HostStateManager


def ensure_workflow_label(
    issue_num: str,
    tier_name: str,
    repo: str,
    *,
    project_name: str | None = None,
    token_override: str | None = None,
) -> None:
    """Add `workflow:<tier>` label to an issue if missing."""
    label = f"workflow:{tier_name}"
    try:
        platform = _get_git_platform(
            repo,
            project_name=project_name,
            token_override=token_override,
        )
        issue = asyncio.run(platform.get_issue(str(issue_num)))
        if issue is None:
            raise RuntimeError("issue_not_found")
        labels = list(issue.labels or [])
        if label not in labels:
            labels.append(label)
            asyncio.run(platform.update_issue(str(issue_num), labels=labels))
            logger.info("Added missing label '%s' to issue #%s", label, issue_num)
    except Exception as exc:
        logger.warning("Failed to add label '%s' to issue #%s: %s", label, issue_num, exc)


def resolve_tier_for_issue(
    issue_num: str,
    project_name: str,
    repo: str,
    *,
    context: str = "auto-chain",
    alert_source: str = "inbox_processor",
    token_override: str | None = None,
) -> str | None:
    """Resolve workflow tier for an issue, sending alert when unavailable."""
    state_manager = _state_manager()
    tracker_tier = state_manager.get_last_tier_for_issue(issue_num)
    label_tier = _get_sop_tier_from_issue(issue_num, project_name, repo_override=repo)

    if tracker_tier and label_tier:
        if tracker_tier != label_tier:
            logger.warning(
                "Tier mismatch for issue #%s: tracker=%s, label=%s. Using label.",
                issue_num,
                tracker_tier,
                label_tier,
            )
        return label_tier

    if label_tier:
        return label_tier

    if tracker_tier:
        ensure_workflow_label(
            issue_num,
            tracker_tier,
            repo,
            project_name=project_name,
            token_override=token_override,
        )
        return tracker_tier

    logger.error(
        "Cannot determine workflow tier for issue #%s (%s): no tracker entry and no workflow label.",
        issue_num,
        context,
    )
    _emit_alert(
        f"⚠️ {context.title()} halted for issue #{issue_num}: "
        f"missing `workflow:` label and no prior launch data.\n"
        f"Add a label (e.g. `workflow:full`) to the issue and retry.",
        severity="warning",
        source=alert_source,
        issue_number=str(issue_num),
        project_key=project_name,
    )
    return None
