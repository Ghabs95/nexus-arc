"""Shared task-flow helpers extracted from inbox processing/runtime paths."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from nexus.core.config import BASE_DIR, NEXUS_CORE_STORAGE_DIR, PROJECT_CONFIG
from nexus.core.config import get_tasks_active_dir, get_tasks_closed_dir
from nexus.core.inbox.inbox_repo_path_service import resolve_git_dir, resolve_git_dirs
from nexus.core.integrations.notifications import send_notification
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.issue_finalize import (
    cleanup_worktree as _cleanup_worktree,
    close_issue as _close_issue,
    create_pr_from_changes as _create_pr_from_changes,
    finalize_workflow as _finalize_workflow_core,
    find_existing_pr as _find_existing_pr,
    verify_workflow_terminal_before_finalize as _verify_workflow_terminal_before_finalize,
)
from nexus.core.merge_queue import enqueue_merge_queue_prs
from nexus.core.orchestration.plugin_runtime import get_workflow_policy_plugin, get_workflow_state_plugin
from nexus.core.runtime_mode import is_issue_process_running
from nexus.core.task_archive import archive_closed_task_files
from nexus.core.workflow_runtime.workflow_signal_sync import normalize_agent_reference
from nexus.core.inbox.inbox_sop_naming_service import get_sop_tier_for_task

logger = logging.getLogger(__name__)


_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: get_workflow_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: get_workflow_state().map_issue(n, w),
}


def get_sop_tier(task_type: str, title: str | None = None, body: str | None = None):
    """Compatibility wrapper returning (tier_name, sop_template, workflow_label)."""
    return get_sop_tier_for_task(
        task_type=str(task_type or ""),
        title=title,
        body=body,
        suggest_tier_label=None,
        logger=logger,
    )


def finalize_workflow(issue_num: str, repo: str, last_agent: str, project_name: str) -> None:
    """Finalize workflow with PR/issue close/archive semantics."""

    def _notify(message: str) -> None:
        try:
            send_notification(str(message))
        except Exception:
            logger.debug("workflow finalize notification failed", exc_info=True)

    _finalize_workflow_core(
        issue_num=str(issue_num),
        repo=repo,
        last_agent=last_agent,
        project_name=project_name,
        logger=logger,
        get_workflow_state_plugin=get_workflow_state_plugin,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        verify_workflow_terminal_before_finalize_fn=_verify_workflow_terminal_before_finalize,
        get_workflow_policy_plugin=get_workflow_policy_plugin,
        resolve_git_dir=resolve_git_dir,
        resolve_git_dirs=resolve_git_dirs,
        create_pr_from_changes_fn=lambda **kwargs: _create_pr_from_changes(
            project_name=project_name,
            repo=kwargs["repo"],
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            title=kwargs["title"],
            body=kwargs["body"],
            issue_repo=kwargs.get("issue_repo"),
        ),
        find_existing_pr_fn=lambda **kwargs: _find_existing_pr(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
        ),
        cleanup_worktree_fn=lambda **kwargs: _cleanup_worktree(
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            is_issue_agent_running_fn=lambda value: is_issue_process_running(
                value, cache_key="runtime-ops:inbox"
            ),
        ),
        close_issue_fn=lambda **kwargs: _close_issue(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
            comment=kwargs.get("comment"),
        ),
        send_notification=_notify,
        enqueue_merge_queue_prs=enqueue_merge_queue_prs,
        archive_closed_task_files=archive_closed_task_files,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        get_tasks_active_dir=get_tasks_active_dir,
        get_tasks_closed_dir=get_tasks_closed_dir,
    )

