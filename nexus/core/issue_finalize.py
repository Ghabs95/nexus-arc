"""Workflow finalization helpers extracted from inbox_processor."""

import asyncio
import logging
import os

logger = logging.getLogger(__name__)


def emit_alert(*args, **kwargs):
    """Proxy host alert emitter."""
    try:
        from nexus.core.integrations.notifications import emit_alert as _host_emit_alert
    except Exception:
        return None
    return _host_emit_alert(*args, **kwargs)


def get_git_platform(repo_key: str, *, project_name: str):
    """Resolve provider adapter from host orchestration helper."""
    try:
        from nexus.core.orchestration.nexus_core_helpers import get_git_platform as _host_get_git_platform
    except Exception as exc:
        raise RuntimeError("Host get_git_platform is not available") from exc
    return _host_get_git_platform(repo_key, project_name=project_name)


def verify_workflow_terminal_before_finalize(
    *,
    workflow_plugin,
    issue_num: str,
    project_name: str,
    alert_source: str = "inbox_processor",
) -> bool:
    """Return True when finalization may proceed; emit alert on non-terminal state."""
    try:
        if workflow_plugin and hasattr(workflow_plugin, "get_workflow_status"):
            status = asyncio.run(workflow_plugin.get_workflow_status(str(issue_num)))
            state = str((status or {}).get("state", "")).strip().lower()
            if state and state not in {"completed", "failed", "cancelled"}:
                logger.warning(
                    "Skipping finalize for issue #%s: workflow state is non-terminal (%s)",
                    issue_num,
                    state,
                )
                emit_alert(
                    "⚠️ Finalization blocked for "
                    f"issue #{issue_num}: workflow state is `{state}` (expected terminal).",
                    severity="warning",
                    source=alert_source,
                    issue_number=str(issue_num),
                    project_key=project_name,
                )
                return False
    except Exception as exc:
        logger.warning(
            "Could not verify workflow state before finalize for issue #%s: %s",
            issue_num,
            exc,
        )
    return True


def create_pr_from_changes(
    *,
    project_name: str,
    repo: str,
    repo_dir: str,
    issue_number: str,
    title: str,
    body: str,
    issue_repo: str | None = None,
    token_override: str | None = None,
) -> str | None:
    issue_worktree_dir = os.path.join(
        str(repo_dir),
        ".nexus",
        "worktrees",
        f"issue-{str(issue_number).strip()}",
    )
    target_repo_dir = issue_worktree_dir if os.path.isdir(issue_worktree_dir) else repo_dir
    if target_repo_dir != repo_dir:
        logger.info(
            "Using issue worktree for PR creation on issue #%s: %s",
            issue_number,
            target_repo_dir,
        )

    platform = get_git_platform(
        repo,
        project_name=project_name,
        token_override=token_override,
    )
    pr_result = asyncio.run(
        platform.create_pr_from_changes(
            repo_dir=target_repo_dir,
            issue_number=issue_number,
            title=title,
            body=body,
            issue_repo=issue_repo,
        )
    )
    return pr_result.url if pr_result else None


def close_issue(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
    comment: str | None = None,
    token_override: str | None = None,
) -> bool:
    platform = get_git_platform(
        repo,
        project_name=project_name,
        token_override=token_override,
    )
    return bool(asyncio.run(platform.close_issue(issue_number, comment=comment)))


def find_existing_pr(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
    token_override: str | None = None,
) -> str | None:
    platform = get_git_platform(
        repo,
        project_name=project_name,
        token_override=token_override,
    )
    linked = asyncio.run(platform.search_linked_prs(str(issue_number)))
    if not linked:
        return None
    open_pr = next((pr for pr in linked if str(pr.state).lower() == "open"), None)
    selected = open_pr or linked[0]
    return selected.url


def cleanup_worktree(
    *,
    repo_dir: str,
    issue_number: str,
    is_issue_agent_running_fn=None,
) -> bool:
    from nexus.core.workspace import WorkspaceManager

    return bool(
        WorkspaceManager.cleanup_worktree_safe(
            base_repo_path=repo_dir,
            issue_number=str(issue_number),
            is_issue_agent_running=is_issue_agent_running_fn,
            require_clean=True,
        )
    )


def finalize_workflow(
    *,
    issue_num: str,
    repo: str,
    last_agent: str,
    project_name: str,
    logger,
    get_workflow_state_plugin,
    workflow_state_plugin_kwargs: dict,
    verify_workflow_terminal_before_finalize_fn,
    get_workflow_policy_plugin,
    resolve_git_dir,
    resolve_git_dirs,
    create_pr_from_changes_fn,
    find_existing_pr_fn,
    cleanup_worktree_fn,
    close_issue_fn,
    send_notification,
    enqueue_merge_queue_prs,
    archive_closed_task_files,
    project_config: dict,
    base_dir: str,
    get_tasks_active_dir,
    get_tasks_closed_dir,
) -> None:
    try:
        workflow_plugin = get_workflow_state_plugin(
            **workflow_state_plugin_kwargs,
            cache_key="workflow:state-engine",
        )
        if not verify_workflow_terminal_before_finalize_fn(
            workflow_plugin=workflow_plugin,
            issue_num=str(issue_num),
            project_name=project_name,
            alert_source="inbox_processor",
        ):
            return
    except Exception as exc:
        logger.warning(
            "Could not verify workflow state before finalize for issue #%s: %s",
            issue_num,
            exc,
        )

    workflow_policy = get_workflow_policy_plugin(
        resolve_git_dir=resolve_git_dir,
        resolve_git_dirs=resolve_git_dirs,
        create_pr_from_changes=create_pr_from_changes_fn,
        find_existing_pr=find_existing_pr_fn,
        cleanup_worktree=cleanup_worktree_fn,
        close_issue=close_issue_fn,
        send_notification=send_notification,
        cache_key="workflow-policy:finalize",
    )

    result = workflow_policy.finalize_workflow(
        issue_number=str(issue_num),
        repo=repo,
        last_agent=last_agent,
        project_name=project_name,
    )

    pr_urls = result.get("pr_urls") if isinstance(result, dict) else None
    if isinstance(pr_urls, list) and pr_urls:
        for pr_link in pr_urls:
            logger.info("🔀 Created/linked PR for issue #%s: %s", issue_num, pr_link)
        enqueue_merge_queue_prs(
            issue_num=str(issue_num),
            issue_repo=repo,
            project_name=project_name,
            pr_urls=[str(url) for url in pr_urls if str(url).strip()],
        )
    if result.get("issue_closed"):
        logger.info("🔒 Closed issue #%s", issue_num)
        archived = archive_closed_task_files(
            issue_num=str(issue_num),
            project_name=project_name,
            project_config=project_config,
            base_dir=base_dir,
            get_tasks_active_dir=get_tasks_active_dir,
            get_tasks_closed_dir=get_tasks_closed_dir,
            logger=logger,
        )
        if archived:
            logger.info("📦 Archived %s task file(s) for closed issue #%s", archived, issue_num)
