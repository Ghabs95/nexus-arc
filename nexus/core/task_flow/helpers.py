"""Shared task-flow helpers extracted from inbox processing/runtime paths."""

from __future__ import annotations

import logging

from nexus.adapters.git.utils import build_issue_url
from nexus.core.auth.execution_env_resolver import resolve_requester_git_token_for_issue
from nexus.core.config import BASE_DIR, NEXUS_CORE_STORAGE_DIR, PROJECT_CONFIG, get_repo_branch
from nexus.core.config import get_tasks_active_dir, get_tasks_closed_dir
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_git_dir,
    resolve_git_dir_for_repo,
    resolve_git_dirs,
)
from nexus.core.inbox.inbox_sop_naming_service import get_sop_tier_for_task
from nexus.core.integrations.notifications import send_notification
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.issue_finalize import (
    cleanup_worktree as _cleanup_worktree,
    close_issue as _close_issue,
    create_pr_from_changes as _create_pr_from_changes,
    finalize_workflow as _finalize_workflow_core,
    find_existing_pr as _find_existing_pr,
    find_existing_pr_details as _find_existing_pr_details,
    sync_existing_pr_changes as _sync_existing_pr_changes,
    verify_workflow_terminal_before_finalize as _verify_workflow_terminal_before_finalize,
)
from nexus.core.merge_queue import enqueue_merge_queue_prs
from nexus.core.orchestration.plugin_runtime import get_workflow_policy_plugin, get_workflow_state_plugin
from nexus.core.runtime_mode import is_issue_process_running
from nexus.core.task_archive import archive_closed_task_files
from nexus.core.workflow_runtime.workflow_signal_sync import (
    normalize_agent_reference as _normalize_agent_reference_from_signal_sync,
)

logger = logging.getLogger(__name__)


_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: get_workflow_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: get_workflow_state().map_issue(n, w),
}


def _resolve_issue_requester_token(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
) -> str | None:
    return resolve_requester_git_token_for_issue(
        repo_name=str(repo),
        issue_number=str(issue_number),
        project_name=project_name,
        issue_url=build_issue_url(
            str(repo),
            str(issue_number),
            {"git_platform": "github"},
        ),
        purpose="git",
    )


def normalize_agent_reference(agent_ref: str | None) -> str | None:
    """Normalize agent aliases/mentions to canonical agent identifier."""
    if agent_ref is None:
        return None
    normalized = _normalize_agent_reference_from_signal_sync(str(agent_ref))
    return normalized or None


def get_sop_tier(task_type: str, title: str | None = None, body: str | None = None):
    """Compatibility wrapper returning (tier_name, sop_template, workflow_label)."""
    return get_sop_tier_for_task(
        task_type=str(task_type or ""),
        title=title,
        body=body,
        suggest_tier_label=lambda _title, _body: None,
        logger=logger,
    )


def finalize_workflow(
    issue_num: str,
    repo: str,
    last_agent: str,
    project_name: str,
    *,
    emit_notifications: bool = True,
) -> dict:
    """Finalize workflow with PR/issue close/archive semantics."""

    def _notify(message: str) -> None:
        try:
            send_notification(str(message))
        except Exception:
            logger.debug("workflow finalize notification failed", exc_info=True)

    def _resolve_git_dir_for_project(project_name_arg: str) -> str | None:
        return resolve_git_dir(
            project_name=str(project_name_arg),
            project_config=PROJECT_CONFIG,
            base_dir=BASE_DIR,
        )

    def _resolve_git_dirs_for_project(project_name_arg: str) -> dict[str, str]:
        return resolve_git_dirs(
            project_name=str(project_name_arg),
            get_repos=lambda name: list(((PROJECT_CONFIG.get(str(name), {}) or {}).get("git_repos") or [])),
            resolve_git_dir_for_repo=lambda name, repo_name: resolve_git_dir_for_repo(
                project_name=str(name),
                repo_name=str(repo_name),
                project_config=PROJECT_CONFIG,
                base_dir=BASE_DIR,
            ),
        )

    return _finalize_workflow_core(
        issue_num=str(issue_num),
        repo=repo,
        last_agent=last_agent,
        project_name=project_name,
        emit_notifications=emit_notifications,
        logger=logger,
        get_workflow_state_plugin=get_workflow_state_plugin,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        verify_workflow_terminal_before_finalize_fn=_verify_workflow_terminal_before_finalize,
        get_workflow_policy_plugin=get_workflow_policy_plugin,
        resolve_git_dir=_resolve_git_dir_for_project,
        resolve_git_dirs=_resolve_git_dirs_for_project,
        create_pr_from_changes_fn=lambda **kwargs: _create_pr_from_changes(
            project_name=project_name,
            repo=kwargs["repo"],
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            title=kwargs["title"],
            body=kwargs["body"],
            issue_repo=kwargs.get("issue_repo"),
            base_branch=kwargs.get("base_branch"),
            token_override=_resolve_issue_requester_token(
                project_name=project_name,
                repo=str(kwargs["repo"]),
                issue_number=str(kwargs["issue_number"]),
            ),
        ),
        resolve_repo_branch_fn=lambda **kwargs: get_repo_branch(
            project_name,
            str(kwargs.get("repo", "")),
        ),
        find_existing_pr_fn=lambda **kwargs: _find_existing_pr(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
            token_override=_resolve_issue_requester_token(
                project_name=project_name,
                repo=str(kwargs["repo"]),
                issue_number=str(kwargs["issue_number"]),
            ),
        ),
        find_existing_pr_details_fn=lambda **kwargs: _find_existing_pr_details(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
            token_override=_resolve_issue_requester_token(
                project_name=project_name,
                repo=str(kwargs["repo"]),
                issue_number=str(kwargs["issue_number"]),
            ),
        ),
        cleanup_worktree_fn=lambda **kwargs: _cleanup_worktree(
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            is_issue_agent_running_fn=lambda value: is_issue_process_running(
                value, cache_key="runtime-ops:inbox"
            ),
        ),
        sync_existing_pr_changes_fn=lambda **kwargs: _sync_existing_pr_changes(
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            issue_repo=kwargs.get("issue_repo"),
            repo=kwargs.get("repo"),
            base_branch=kwargs.get("base_branch"),
        ),
        close_issue_fn=lambda **kwargs: _close_issue(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
            comment=kwargs.get("comment"),
            token_override=_resolve_issue_requester_token(
                project_name=project_name,
                repo=str(kwargs["repo"]),
                issue_number=str(kwargs["issue_number"]),
            ),
        ),
        send_notification=_notify,
        enqueue_merge_queue_prs=enqueue_merge_queue_prs,
        archive_closed_task_files=archive_closed_task_files,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        get_tasks_active_dir=get_tasks_active_dir,
        get_tasks_closed_dir=get_tasks_closed_dir,
        resolve_token_override_fn=lambda project_name_arg, repo_arg, issue_number_arg: _resolve_issue_requester_token(
            project_name=project_name_arg,
            repo=str(repo_arg),
            issue_number=str(issue_number_arg),
        ),
    )
