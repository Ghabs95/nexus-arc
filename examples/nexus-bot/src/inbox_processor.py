import asyncio
import glob
import logging
import os
import re
import time
from typing import Any

from nexus.core.config.bootstrap import initialize_runtime

initialize_runtime(configure_logging=False)

# Nexus Core framework imports — orchestration handled by ProcessOrchestrator
# Import centralized configuration
from nexus.core.config import (
    BASE_DIR,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
    NEXUS_FEATURE_REGISTRY_ENABLED,
    NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
    NEXUS_STORAGE_BACKEND,
    NEXUS_STORAGE_DSN,
    NEXUS_STATE_DIR,
    ORCHESTRATOR_CONFIG,
    PROJECT_CONFIG,
    SLEEP_INTERVAL,
    get_default_project,
    get_repo_branch,
    get_repo,
    get_repos,
    get_inbox_storage_backend,
    get_inbox_dir,
    get_nexus_dir_name,
    get_project_platform,
    get_tasks_active_dir,
    get_tasks_closed_dir,
)
from nexus.adapters.git.utils import build_issue_url
from nexus.core.auth.access_domain import (
    auth_enabled as _auth_enabled,
    build_execution_env as _build_execution_env,
    check_project_access as _check_project_access,
    check_repo_access as _check_repo_access,
    has_setup_ready_user_for_project as _has_setup_ready_user_for_project,
    resolve_project_polling_git_token as _resolve_project_polling_git_token,
)
from nexus.core.auth.credential_store import bind_issue_requester as _bind_issue_requester
from nexus.core.auth.credential_store import get_issue_requester as _get_issue_requester
from nexus.core.auth.credential_store import (
    list_bound_issue_numbers as _list_bound_issue_numbers,
)
from nexus.core.comment_monitor_service import (
    run_comment_monitor_cycle as _run_comment_monitor_cycle,
)
from nexus.core.completion_monitor_service import (
    post_completion_comments_from_logs as _svc_post_completion_comments_from_logs,
)
from nexus.core.completion_monitor_service import (
    run_completion_monitor_cycle as _run_completion_monitor_cycle,
)
from nexus.core.completion_store import CompletionStore
from nexus.core.feature_registry_service import FeatureRegistryService
from nexus.core.git_sync.workflow_start_sync_service import (
    sync_project_repos_on_workflow_start as _sync_project_repos_on_workflow_start,
)
from nexus.core.inbox.inbox_issue_context_service import (
    find_task_file_for_issue as _svc_find_task_file_for_issue,
)
from nexus.core.inbox.inbox_issue_context_service import (
    get_initial_agent_from_workflow as _svc_get_initial_agent_from_workflow,
)
from nexus.core.inbox.inbox_issue_context_service import (
    resolve_project_for_issue as _svc_resolve_project_for_issue,
)
from nexus.core.inbox.inbox_issue_context_service import (
    resolve_project_from_task_file as _svc_resolve_project_from_task_file,
)
from nexus.core.inbox.inbox_persistence_service import (
    get_completion_replay_window_seconds as _svc_get_completion_replay_window_seconds,
)
from nexus.core.inbox.inbox_persistence_service import (
    load_json_state_file as _svc_load_json_state_file,
)
from nexus.core.inbox.inbox_persistence_service import (
    save_json_state_file as _svc_save_json_state_file,
)
from nexus.core.inbox.inbox_processor_entry_service import (
    drain_postgres_inbox_queue_once as _svc_drain_postgres_inbox_queue_once,
)
from nexus.core.inbox.inbox_processor_entry_service import (
    run_inbox_processor_main as _svc_run_inbox_processor_main,
)
from nexus.core.inbox.inbox_repo_path_service import (
    extract_repo_from_issue_url as _svc_extract_repo_from_issue_url,
)
from nexus.core.inbox.inbox_repo_path_service import (
    reroute_webhook_task_to_project as _svc_reroute_webhook_task_to_project,
)
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_git_dir as _svc_resolve_git_dir,
)
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_git_dir_for_repo as _svc_resolve_git_dir_for_repo,
)
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_git_dirs as _svc_resolve_git_dirs,
)
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_project_for_repo as _svc_resolve_project_for_repo,
)
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_project_from_path as _svc_resolve_project_from_path,
)
from nexus.core.inbox.inbox_repo_path_service import (
    resolve_repo_strict as _svc_resolve_repo_strict,
)
from nexus.core.inbox.inbox_runtime_singletons_service import (
    get_completion_store as _svc_get_completion_store,
)
from nexus.core.inbox.inbox_runtime_singletons_service import (
    get_process_orchestrator as _svc_get_process_orchestrator,
)
from nexus.core.inbox.inbox_signal_probe_service import (
    read_latest_local_completion as _svc_read_latest_local_completion,
)
from nexus.core.inbox.inbox_signal_probe_service import (
    read_latest_structured_comment as _svc_read_latest_structured_comment,
)
from nexus.core.inbox.inbox_sop_naming_service import (
    generate_issue_name_with_ai as _svc_generate_issue_name_with_ai,
    get_sop_tier_for_task as _svc_get_sop_tier_for_task,
    refine_issue_content_with_ai as _svc_refine_issue_content_with_ai,
    render_checklist_from_workflow as _svc_render_checklist_from_workflow,
    render_fallback_checklist as _svc_render_fallback_checklist,
)
from nexus.core.inbox.inbox_task_processor_service import (
    process_task_context as _svc_process_task_context,
)
from nexus.core.inbox.inbox_task_processor_service import (
    process_task_payload as _svc_process_task_payload,
)
from nexus.core.integrations.inbox_queue import claim_pending_tasks, mark_task_done, mark_task_failed
from nexus.core.integrations.notifications import (
    emit_alert,
    notify_agent_needs_input,
    notify_workflow_completed,
)
from nexus.core.issue_finalize import (
    cleanup_worktree as _finalize_cleanup_worktree,
)
from nexus.core.issue_finalize import (
    close_issue as _finalize_close_issue,
)
from nexus.core.issue_finalize import (
    create_pr_from_changes as _finalize_create_pr_from_changes,
)
from nexus.core.issue_finalize import (
    finalize_workflow as _svc_finalize_workflow,
)
from nexus.core.issue_finalize import (
    find_existing_pr as _finalize_find_existing_pr,
)
from nexus.core.issue_finalize import (
    verify_workflow_terminal_before_finalize as _verify_workflow_terminal_before_finalize,
)
from nexus.core.issue_lifecycle import (
    create_issue as _create_issue,
)
from nexus.core.issue_lifecycle import (
    rename_task_file_and_sync_issue_body as _rename_task_file_and_sync_issue_body,
)
from nexus.core.merge_queue import (
    enqueue_merge_queue_prs as _enqueue_merge_queue_prs,
)
from nexus.core.merge_queue import (
    merge_queue_auto_merge_once_with_token_resolver as _merge_queue_auto_merge_once,
)
from nexus.core.orchestration.ai_orchestrator import get_orchestrator
from nexus.core.orchestration.nexus_core_helpers import (
    complete_step_for_issue,
    get_git_platform,
    get_workflow_definition_path,
    start_workflow,
)
from nexus.core.orchestration.plugin_runtime import (
    get_runtime_ops_plugin,
    get_workflow_monitor_policy_plugin,
    get_workflow_policy_plugin,
    get_workflow_state_plugin,
)
from nexus.core.process_orchestrator import ProcessOrchestrator
from nexus.core.processor_loops import (
    run_processor_loop as _run_processor_loop,
)
from nexus.core.processor_runtime_state import (
    ProcessorRuntimeState,
)
from nexus.core.project.repo_resolution import (
    resolve_repo_for_issue as _service_resolve_repo_for_issue,
)
from nexus.core.project.repo_utils import (
    iter_project_configs as _iter_project_configs,
)
from nexus.core.project.repo_utils import (
    project_repos_from_config as _project_repos_from_config,
)
from nexus.core.router import WorkflowRouter
from nexus.core.runtime.agent_launcher import (
    clear_launch_guard,
    invoke_ai_agent,
    is_recent_launch,
)
from nexus.core.runtime_mode import is_issue_process_running, is_postgres_backend
from nexus.core.startup_recovery import (
    build_startup_workflow_payload_loader as _build_startup_workflow_payload_loader,
)
from nexus.core.startup_recovery import (
    reconcile_completion_signals_on_startup as _startup_reconcile_completion_signals,
)
from nexus.core.state_manager import HostStateManager
from nexus.core.task_archive import (
    archive_closed_task_files as _svc_archive_closed_task_files,
)
from nexus.core.task_context import (
    load_task_context as _load_task_context,
)
from nexus.core.task_dispatch import (
    handle_new_task as _handle_new_task,
)
from nexus.core.task_dispatch import (
    handle_webhook_task as _handle_webhook_task,
)
from nexus.core.tier_resolution import (
    resolve_tier_for_issue as _resolve_tier_for_issue,
)
from nexus.core.workflow_runtime.workflow_pr_monitor_service import (
    build_bot_comments_getter as _build_bot_comments_getter,
)
from nexus.core.workflow_runtime.workflow_pr_monitor_service import (
    build_workflow_issue_number_lister as _build_workflow_issue_number_lister,
)
from nexus.core.workflow_runtime.workflow_pr_monitor_service import (
    check_and_notify_pr as _service_check_and_notify_pr,
)
from nexus.core.workflow_runtime.workflow_recovery_service import (
    recover_orphaned_running_agents as _svc_recover_orphaned_running_agents,
)
from nexus.core.workflow_runtime.workflow_recovery_service import (
    run_stuck_agents_cycle as _run_stuck_agents_cycle,
)
from nexus.core.workflow_runtime.workflow_signal_sync import (
    normalize_agent_reference as _normalize_agent_reference,
)
from nexus.core.workflow_runtime.workflow_unmapped_recovery_service import (
    recover_unmapped_issues_from_completions as _service_recover_unmapped_issues_from_completions,
)
from nexus.core.workspace import WorkspaceManager

_STEP_COMPLETE_COMMENT_RE = re.compile(
    r"^\s*##\s+.+?\bcomplete\b\s*[-–—:]\s*`?@?([0-9a-z_-]+)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_READY_FOR_COMMENT_RE = re.compile(
    r"\bready\s+for\s+(?:\*\*)?`?@?([0-9a-z_-]+)",
    re.IGNORECASE,
)
_WORKFLOW_MONITOR_LABELS = (
    "workflow:full",
    "workflow:shortened",
    "workflow:fast-track",
)


# Helper to get issue repo (currently defaults to nexus, should be extended for multi-project)
def get_issue_repo(project: str = "nexus") -> str:
    """Get the Git repo for issue operations.

    Args:
        project: Project name (currently unused, defaults to nexus)

    Returns:
        Git repo string

    Note: This should be extended to support per-project repos when multi-project
          issue tracking is implemented.
    """
    try:
        return get_repo(project)
    except Exception:
        return get_repo(get_default_project())


# Initialize orchestrator (CLI-only)
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)

# Track mutable runtime state explicitly (aliases kept for behavior-preserving migration)
PROCESSOR_RUNTIME_STATE = ProcessorRuntimeState()
alerted_agents = PROCESSOR_RUNTIME_STATE.alerted_agents
notified_comments = (
    PROCESSOR_RUNTIME_STATE.notified_comments
)  # Track comment IDs we've already notified about
auto_chained_agents = PROCESSOR_RUNTIME_STATE.auto_chained_agents  # issue -> log_file
INBOX_PROCESSOR_STARTED_AT = time.time()
POLLING_FAILURE_THRESHOLD = 3
polling_failure_counts = PROCESSOR_RUNTIME_STATE.polling_failure_counts
_ORPHAN_RECOVERY_COOLDOWN_SECONDS = max(
    60,
    int(os.getenv("NEXUS_ORPHAN_RECOVERY_COOLDOWN_SECONDS", "180")),
)
_orphan_recovery_last_attempt = PROCESSOR_RUNTIME_STATE.orphan_recovery_last_attempt
_STALE_WORKTREE_CLEANUP_INTERVAL_SECONDS = max(
    300,
    int(os.getenv("NEXUS_STALE_WORKTREE_CLEANUP_INTERVAL_SECONDS", "3600")),
)
_STALE_WORKTREE_MAX_AGE_HOURS = max(
    1,
    int(os.getenv("NEXUS_STALE_WORKTREE_MAX_AGE_HOURS", "168")),
)
_last_stale_worktree_cleanup_ts = 0.0
from nexus.core.integrations.workflow_state_factory import get_workflow_state as _get_wf_state
from nexus.core.integrations.workflow_state_factory import get_storage_backend as _get_storage_backend


_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: _get_wf_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: _get_wf_state().map_issue(n, w),
    "workflow_definition_path_resolver": get_workflow_definition_path,
}


# Load persisted state
launched_agents_tracker = HostStateManager.load_launched_agents()
# PROJECT_CONFIG is now imported from config.py

# Failed task file lookup tracking (stop checking after 3 failures)
FAILED_LOOKUPS_FILE = os.path.join(NEXUS_STATE_DIR, "failed_task_lookups.json")
COMPLETION_COMMENTS_FILE = os.path.join(NEXUS_STATE_DIR, "completion_comments.json")


def load_failed_lookups():
    return _svc_load_json_state_file(
        path=FAILED_LOOKUPS_FILE,
        logger=logger,
        warn_only=False,
        storage_backend=NEXUS_STORAGE_BACKEND,
        state_key="failed_task_lookups",
    )


def save_failed_lookups(lookups):
    _svc_save_json_state_file(
        path=FAILED_LOOKUPS_FILE,
        data=lookups if isinstance(lookups, dict) else {},
        logger=logger,
        warn_only=False,
        storage_backend=NEXUS_STORAGE_BACKEND,
        state_key="failed_task_lookups",
    )


def load_completion_comments():
    return _svc_load_json_state_file(
        path=COMPLETION_COMMENTS_FILE,
        logger=logger,
        warn_only=True,
        storage_backend=NEXUS_STORAGE_BACKEND,
        state_key="completion_comments",
    )


def save_completion_comments(comments):
    _svc_save_json_state_file(
        path=COMPLETION_COMMENTS_FILE,
        data=comments if isinstance(comments, dict) else {},
        logger=logger,
        warn_only=True,
        storage_backend=NEXUS_STORAGE_BACKEND,
        state_key="completion_comments",
    )


def get_completion_replay_window_seconds() -> int:
    return _svc_get_completion_replay_window_seconds(getenv=os.getenv, default_seconds=1800)


def _build_inbox_logging_handlers() -> list[logging.Handler]:
    return [logging.StreamHandler()]


# Logging — force=True overrides the root handler set by config.py at import time
logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    force=True,
    handlers=_build_inbox_logging_handlers(),
)
logger = logging.getLogger("InboxProcessor")

failed_task_lookups = load_failed_lookups()
completion_comments = load_completion_comments()
feature_registry_service: FeatureRegistryService | None = None


def _get_feature_registry_service() -> FeatureRegistryService:
    global feature_registry_service
    if feature_registry_service is None:
        feature_registry_service = FeatureRegistryService(
            enabled=NEXUS_FEATURE_REGISTRY_ENABLED,
            backend=NEXUS_STORAGE_BACKEND,
            state_dir=NEXUS_STATE_DIR,
            postgres_dsn=NEXUS_STORAGE_DSN,
            max_items_per_project=NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
            dedup_similarity=NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
        )
    return feature_registry_service


def _record_polling_failure(scope: str, error: Exception) -> None:
    """Increment polling failure count and alert once threshold is reached."""
    count = polling_failure_counts.get(scope, 0) + 1
    polling_failure_counts[scope] = count
    if count != POLLING_FAILURE_THRESHOLD:
        return

    try:
        emit_alert(
            "⚠️ **Polling Error Threshold Reached**\n\n"
            f"Scope: `{scope}`\n"
            f"Consecutive failures: {count}\n"
            f"Last error: `{error}`",
            severity="error",
            source="inbox_processor",
        )
    except Exception as notify_err:
        logger.error(f"Failed to send polling escalation alert for {scope}: {notify_err}")


def _clear_polling_failures(scope: str) -> None:
    """Reset polling failure count for a scope after a successful attempt."""
    if scope in polling_failure_counts:
        polling_failure_counts.pop(scope, None)


def slugify(text):
    """Converts text to a branch-friendly slug."""
    text = text.lower()
    text = re.sub(r"[^a-z0-9\s-]", "", text)
    text = re.sub(r"\s+", "-", text)
    return text[:50]


def _resolve_project_from_path(summary_path: str) -> str:
    return _svc_resolve_project_from_path(
        summary_path=summary_path,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        iter_project_configs=_iter_project_configs,
        get_repos=get_repos,
    )


def _extract_repo_from_issue_url(issue_url: str) -> str:
    return _svc_extract_repo_from_issue_url(issue_url)


def _resolve_project_for_repo(repo_name: str) -> str | None:
    return _svc_resolve_project_for_repo(
        repo_name=repo_name,
        project_config=PROJECT_CONFIG,
        iter_project_configs=_iter_project_configs,
        project_repos_from_config=_project_repos_from_config,
        get_repos=get_repos,
    )


def _reroute_webhook_task_to_project(filepath: str, target_project: str) -> str | None:
    return _svc_reroute_webhook_task_to_project(
        filepath=filepath,
        target_project=target_project,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        get_inbox_dir=get_inbox_dir,
    )


def _resolve_repo_for_issue(issue_num: str, default_project: str | None = None) -> str:
    return _service_resolve_repo_for_issue(
        issue_num=str(issue_num),
        default_project=default_project,
        project_config=PROJECT_CONFIG,
        get_default_project=get_default_project,
        get_repo=get_repo,
        iter_project_configs=_iter_project_configs,
        project_repos_from_config=_project_repos_from_config,
        get_repos=get_repos,
        get_git_platform=get_git_platform,
        resolve_issue_token=_resolve_issue_requester_token,
        extract_repo_from_issue_url=_extract_repo_from_issue_url,
        base_dir=BASE_DIR,
    )


def _resolve_repo_strict(project_name: str, issue_num: str) -> str:
    return _svc_resolve_repo_strict(
        project_name=project_name,
        issue_num=str(issue_num),
        project_config=PROJECT_CONFIG,
        project_repos_from_config=_project_repos_from_config,
        get_repos=get_repos,
        resolve_repo_for_issue=_resolve_repo_for_issue,
        get_default_project=get_default_project,
        get_repo=get_repo,
        emit_alert=emit_alert,
        logger=logger,
    )


def _read_latest_local_completion(issue_num: str) -> dict | None:
    return _svc_read_latest_local_completion(
        issue_num=str(issue_num),
        db_only_task_mode=lambda: is_postgres_backend(NEXUS_STORAGE_BACKEND),
        get_storage_backend=_get_storage_backend,
        normalize_agent_reference=_normalize_agent_reference,
        base_dir=BASE_DIR,
        get_nexus_dir_name=get_nexus_dir_name,
    )


def _read_latest_structured_comment(issue_num: str, repo: str, project_name: str) -> dict | None:
    return _svc_read_latest_structured_comment(
        issue_num=str(issue_num),
        repo=str(repo),
        project_name=str(project_name),
        get_git_platform=get_git_platform,
        resolve_issue_token=_resolve_issue_requester_token,
        require_issue_requester_token=_auth_enabled(),
        normalize_agent_reference=_normalize_agent_reference,
        step_complete_comment_re=_STEP_COMPLETE_COMMENT_RE,
        ready_for_comment_re=_READY_FOR_COMMENT_RE,
        logger=logger,
    )


def reconcile_completion_signals_on_startup() -> None:
    """Audit workflow/comment/local completion alignment and alert on drift.

    Safe startup check only: emits alerts when signals diverge, does not mutate
    workflow state or completion files.
    """
    _startup_reconcile_completion_signals(
        logger=logger,
        emit_alert=emit_alert,
        get_workflow_state_mappings=lambda: _get_wf_state().load_all_mappings(),
        nexus_core_storage_dir=NEXUS_CORE_STORAGE_DIR,
        load_workflow_payload=_build_startup_workflow_payload_loader(
            db_only_task_mode=lambda: is_postgres_backend(NEXUS_STORAGE_BACKEND),
            get_storage_backend=_get_storage_backend,
            logger=logger,
            nexus_core_storage_dir=NEXUS_CORE_STORAGE_DIR,
        ),
        normalize_agent_reference=_normalize_agent_reference,
        extract_repo_from_issue_url=_extract_repo_from_issue_url,
        read_latest_local_completion=_read_latest_local_completion,
        read_latest_structured_comment=_read_latest_structured_comment,
        is_terminal_agent_reference=_is_terminal_agent_reference,
        complete_step_for_issue=complete_step_for_issue,
        local_completions_enabled=lambda: not is_postgres_backend(NEXUS_STORAGE_BACKEND),
    )


def _is_terminal_agent_reference(agent_ref: str) -> bool:
    """Return True when a next-agent reference means workflow completion."""
    return _normalize_agent_reference(agent_ref).lower() in {
        "none",
        "n/a",
        "null",
        "no",
        "end",
        "done",
        "finish",
        "complete",
        "",
    }


def _resolve_git_dir(project_name: str) -> str | None:
    return _svc_resolve_git_dir(
        project_name=project_name,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
    )


def _resolve_git_dir_for_repo(project_name: str, repo_name: str) -> str | None:
    return _svc_resolve_git_dir_for_repo(
        project_name=project_name,
        repo_name=repo_name,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
    )


def _resolve_git_dirs(project_name: str) -> dict[str, str]:
    return _svc_resolve_git_dirs(
        project_name=project_name,
        get_repos=get_repos,
        resolve_git_dir_for_repo=_resolve_git_dir_for_repo,
    )


def _workflow_policy_notify(message: str) -> None:
    emit_alert(message, severity="info", source="workflow_policy")


def _archive_closed_task_files(
    issue_num: str,
    project_name: str,
    *,
    project_config: dict[str, Any] | None = None,
    base_dir: str | None = None,
    get_tasks_active_dir=None,
    get_tasks_closed_dir=None,
    logger_override=None,
) -> int:
    """Backward-compatible wrapper around task archival service.

    Supports legacy positional calls used in tests and older integrations.
    """
    return _svc_archive_closed_task_files(
        issue_num=str(issue_num),
        project_name=str(project_name),
        project_config=project_config if isinstance(project_config, dict) else PROJECT_CONFIG,
        base_dir=str(base_dir or BASE_DIR),
        get_tasks_active_dir=get_tasks_active_dir or globals()["get_tasks_active_dir"],
        get_tasks_closed_dir=get_tasks_closed_dir or globals()["get_tasks_closed_dir"],
        logger=logger_override or logger,
    )


def _verify_workflow_terminal_before_finalize_local(
    *,
    workflow_plugin,
    issue_num: str,
    project_name: str,
    alert_source: str = "inbox_processor",
) -> bool:
    """Emit guardrail alert via this module's notification bridge."""
    allowed = _verify_workflow_terminal_before_finalize(
        workflow_plugin=workflow_plugin,
        issue_num=issue_num,
        project_name=project_name,
        alert_source=alert_source,
    )
    if not allowed:
        try:
            status = None
            if workflow_plugin and hasattr(workflow_plugin, "get_workflow_status"):
                status = asyncio.run(workflow_plugin.get_workflow_status(str(issue_num)))
            state = str((status or {}).get("state", "unknown")).strip().lower() or "unknown"
            emit_alert(
                "⚠️ Finalization blocked for "
                f"issue #{issue_num}: workflow state is `{state}` (expected terminal).",
                severity="warning",
                source=alert_source,
                issue_number=str(issue_num),
                project_key=project_name,
            )
        except Exception:
            emit_alert(
                f"⚠️ Finalization blocked for issue #{issue_num}: workflow is non-terminal.",
                severity="warning",
                source=alert_source,
                issue_number=str(issue_num),
                project_key=project_name,
            )
    return allowed


def _finalize_workflow(issue_num: str, repo: str, last_agent: str, project_name: str) -> None:
    _svc_finalize_workflow(
        issue_num=str(issue_num),
        repo=repo,
        last_agent=last_agent,
        project_name=project_name,
        logger=logger,
        get_workflow_state_plugin=get_workflow_state_plugin,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        verify_workflow_terminal_before_finalize_fn=_verify_workflow_terminal_before_finalize_local,
        get_workflow_policy_plugin=get_workflow_policy_plugin,
        resolve_git_dir=_resolve_git_dir,
        resolve_git_dirs=_resolve_git_dirs,
        create_pr_from_changes_fn=lambda **kwargs: _finalize_create_pr_from_changes(
            project_name=project_name,
            repo=kwargs["repo"],
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            title=kwargs["title"],
            body=kwargs["body"],
            issue_repo=kwargs.get("issue_repo"),
            token_override=_resolve_issue_requester_token(
                project_name,
                kwargs["repo"],
                str(kwargs["issue_number"]),
            ),
            base_branch=kwargs.get("base_branch"),
        ),
        resolve_repo_branch_fn=lambda **kwargs: get_repo_branch(
            project_name,
            str(kwargs.get("repo", "")),
        ),
        find_existing_pr_fn=lambda **kwargs: _finalize_find_existing_pr(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
            token_override=_resolve_issue_requester_token(
                project_name,
                kwargs["repo"],
                str(kwargs["issue_number"]),
            ),
        ),
        cleanup_worktree_fn=lambda **kwargs: _finalize_cleanup_worktree(
            repo_dir=kwargs["repo_dir"],
            issue_number=str(kwargs["issue_number"]),
            is_issue_agent_running_fn=lambda value: is_issue_process_running(
                value, cache_key="runtime-ops:inbox"
            ),
        ),
        close_issue_fn=lambda **kwargs: _finalize_close_issue(
            project_name=project_name,
            repo=kwargs["repo"],
            issue_number=str(kwargs["issue_number"]),
            comment=kwargs.get("comment"),
            token_override=_resolve_issue_requester_token(
                project_name,
                kwargs["repo"],
                str(kwargs["issue_number"]),
            ),
        ),
        send_notification=_workflow_policy_notify,
        enqueue_merge_queue_prs=_enqueue_merge_queue_prs,
        archive_closed_task_files=_archive_closed_task_files,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        get_tasks_active_dir=get_tasks_active_dir,
        get_tasks_closed_dir=get_tasks_closed_dir,
    )


def _list_bound_workflow_issue_numbers(project_name: str, repo: str) -> list[int]:
    if not _auth_enabled():
        return []
    try:
        return _list_bound_issue_numbers(project_name, repo)
    except Exception as exc:
        logger.warning(
            "Could not list bound issues for project=%s repo=%s: %s",
            project_name,
            repo,
            exc,
        )
        return []


def _resolve_issue_requester_token(project_name: str, repo: str, issue_number: str) -> str | None:
    if not _auth_enabled():
        return None

    requester_nexus_id = _get_issue_requester(str(repo), str(issue_number))
    if not requester_nexus_id:
        return None

    user_env, env_error = _build_execution_env(str(requester_nexus_id))
    if env_error:
        logger.warning(
            "Requester token unavailable for project=%s repo=%s issue=%s requester=%s: %s",
            project_name,
            repo,
            issue_number,
            requester_nexus_id,
            env_error,
        )
        return None

    try:
        platform = str(get_project_platform(str(project_name)) or "").strip().lower()
    except Exception:
        platform = ""

    if platform == "gitlab":
        return str(user_env.get("GITLAB_TOKEN") or user_env.get("GITHUB_TOKEN") or "").strip() or None
    return str(user_env.get("GITHUB_TOKEN") or user_env.get("GITLAB_TOKEN") or "").strip() or None


def _resolve_project_requester_token(project_name: str, requester_nexus_id: str | None) -> str | None:
    requester = str(requester_nexus_id or "").strip()
    if not requester or not _auth_enabled():
        return None
    user_env, env_error = _build_execution_env(requester)
    if env_error:
        logger.warning(
            "Requester token unavailable for project=%s requester=%s: %s",
            project_name,
            requester,
            env_error,
        )
        return None
    try:
        platform = str(get_project_platform(str(project_name)) or "").strip().lower()
    except Exception:
        platform = ""
    if platform == "gitlab":
        return str(user_env.get("GITLAB_TOKEN") or user_env.get("GITHUB_TOKEN") or "").strip() or None
    return str(user_env.get("GITHUB_TOKEN") or user_env.get("GITLAB_TOKEN") or "").strip() or None


def _resolve_tier_for_issue_scoped(
    issue_num: str,
    project_name: str,
    repo: str,
    *,
    context: str = "auto-chain",
    alert_source: str = "inbox_processor",
    requester_nexus_id: str | None = None,
) -> str | None:
    token_override = _resolve_project_requester_token(project_name, requester_nexus_id)
    if not token_override:
        token_override = _resolve_issue_requester_token(project_name, repo, issue_num)
    return _resolve_tier_for_issue(
        issue_num,
        project_name,
        repo,
        context=context,
        alert_source=alert_source,
        token_override=token_override,
    )


def _get_process_orchestrator() -> ProcessOrchestrator:
    return _svc_get_process_orchestrator(
        finalize_fn=_finalize_workflow,
        resolve_project=_resolve_project_from_path,
        resolve_repo=lambda proj, issue: _resolve_repo_strict(proj, issue),
        complete_step_fn=complete_step_for_issue,
        nexus_dir=get_nexus_dir_name(),
    )


def _get_completion_store() -> CompletionStore:
    backend = get_inbox_storage_backend()
    storage = None
    if backend == "postgres":
        storage = _get_storage_backend()
    return _svc_get_completion_store(
        backend=backend,
        storage=storage,
        base_dir=BASE_DIR,
        nexus_dir=get_nexus_dir_name(),
    )


def _ingest_feature_registry_from_completions(
    detected_completions: list[Any], dedup: set[str]
) -> None:
    service = _get_feature_registry_service()
    if not service.is_enabled():
        return

    for completion in detected_completions:
        dedup_key = str(getattr(completion, "dedup_key", "") or "")
        if dedup_key and dedup_key in dedup:
            continue

        issue_number = str(getattr(completion, "issue_number", "") or "").strip()
        if not issue_number:
            continue

        try:
            project_key = _resolve_project_for_issue(issue_number) or get_default_project()
            summary = getattr(completion, "summary", None)
            payload = dict(getattr(summary, "raw", {}) or {})
            payload.setdefault("status", str(getattr(summary, "status", "complete") or "complete"))
            payload.setdefault("summary", str(getattr(summary, "summary", "") or ""))
            payload.setdefault("key_findings", list(getattr(summary, "key_findings", []) or []))

            saved = service.ingest_completion(
                project_key=project_key,
                issue_number=issue_number,
                payload=payload,
            )
            if saved:
                logger.info(
                    "Feature registry upserted from completion issue=%s project=%s feature=%s",
                    issue_number,
                    project_key,
                    saved.get("canonical_title"),
                )
        except Exception as exc:
            logger.warning(
                "Feature registry ingestion failed for issue #%s: %s",
                issue_number,
                exc,
            )


def _post_completion_comments_from_logs() -> None:
    _svc_post_completion_comments_from_logs(
        base_dir=BASE_DIR,
        inbox_processor_started_at=INBOX_PROCESSOR_STARTED_AT,
        completion_comments=completion_comments,
        save_completion_comments=save_completion_comments,
        get_completion_replay_window_seconds=get_completion_replay_window_seconds,
        get_process_orchestrator=_get_process_orchestrator,
        get_workflow_policy_plugin=get_workflow_policy_plugin,
        get_completion_store=_get_completion_store,
        resolve_project=_resolve_project_from_path,
        resolve_repo=lambda proj, issue: _resolve_repo_strict(proj, issue),
        ingest_detected_completions=_ingest_feature_registry_from_completions,
    )


def _get_initial_agent_from_workflow(project_name: str, workflow_type: str = "") -> str:
    from nexus.core.workflow import WorkflowDefinition

    return _svc_get_initial_agent_from_workflow(
        project_name=project_name,
        workflow_type=workflow_type,
        logger=logger,
        emit_alert=emit_alert,
        get_workflow_definition_path=get_workflow_definition_path,
        workflow_definition_loader=WorkflowDefinition.from_yaml,
    )


def check_stuck_agents():
    """Monitor agent processes and handle timeouts with auto-kill and retry.

    Delegates to :class:`ProcessOrchestrator` which implements both
    strategy-1 (stale-log timeout kill) and strategy-2 (dead-process detection).
    """
    _run_stuck_agents_cycle(
        logger=logger,
        base_dir=BASE_DIR,
        scope="stuck-agents:loop",
        orchestrator_check_stuck_agents=lambda base_dir: _get_process_orchestrator().check_stuck_agents(
            base_dir
        ),
        recover_orphaned_running_agents=_recover_orphaned_running_agents,
        recover_unmapped_issues_from_completions=_recover_unmapped_issues_from_completions,
        clear_polling_failures=_clear_polling_failures,
        record_polling_failure=_record_polling_failure,
    )


def _cleanup_stale_worktrees_once() -> None:
    global _last_stale_worktree_cleanup_ts

    now = time.time()
    if now - _last_stale_worktree_cleanup_ts < _STALE_WORKTREE_CLEANUP_INTERVAL_SECONDS:
        return
    _last_stale_worktree_cleanup_ts = now

    try:
        runtime_ops = get_runtime_ops_plugin(cache_key="runtime-ops:stale-worktree-cleanup")
    except Exception as exc:
        logger.warning("Could not initialize runtime ops for stale worktree cleanup: %s", exc)
        runtime_ops = None

    def _is_running(issue_number: str) -> bool:
        if runtime_ops is None or not hasattr(runtime_ops, "is_issue_process_running"):
            return False
        try:
            return bool(runtime_ops.is_issue_process_running(str(issue_number)))
        except Exception:
            return False

    aggregate = {
        "scanned": 0,
        "removed": 0,
        "skipped_recent": 0,
        "skipped_running": 0,
        "skipped_dirty": 0,
        "failed": 0,
    }
    processed_git_dirs: set[str] = set()

    for project_name, _cfg in _iter_project_configs(PROJECT_CONFIG, get_repos):
        resolved = _resolve_git_dirs(project_name)
        if not resolved:
            fallback_git_dir = _resolve_git_dir(project_name)
            resolved = {project_name: fallback_git_dir} if fallback_git_dir else {}

        for git_dir in set(resolved.values()):
            if not git_dir or git_dir in processed_git_dirs:
                continue
            processed_git_dirs.add(git_dir)
            stats = WorkspaceManager.cleanup_stale_worktrees(
                base_repo_path=git_dir,
                max_age_hours=_STALE_WORKTREE_MAX_AGE_HOURS,
                is_issue_agent_running=_is_running,
                require_clean=True,
            )
            for key in aggregate:
                aggregate[key] += int(stats.get(key, 0))

    if aggregate["scanned"] > 0:
        logger.info(
            "Stale worktree cleanup: scanned=%s removed=%s skipped_recent=%s "
            "skipped_running=%s skipped_dirty=%s failed=%s",
            aggregate["scanned"],
            aggregate["removed"],
            aggregate["skipped_recent"],
            aggregate["skipped_running"],
            aggregate["skipped_dirty"],
            aggregate["failed"],
        )


def _recover_orphaned_running_agents(max_relaunches: int = 3) -> int:
    orchestrator = _get_process_orchestrator()
    runtime = getattr(orchestrator, "_runtime", None)
    return _svc_recover_orphaned_running_agents(
        max_relaunches=max_relaunches,
        logger=logger,
        orchestrator=orchestrator,
        runtime=runtime,
        load_all_mappings=lambda: _get_wf_state().load_all_mappings(),
        load_launched_agents=HostStateManager.load_launched_agents,
        orphan_recovery_last_attempt=_orphan_recovery_last_attempt,
        orphan_recovery_cooldown_seconds=_ORPHAN_RECOVERY_COOLDOWN_SECONDS,
        resolve_project_for_issue=_resolve_project_for_issue,
        resolve_repo_for_issue=_resolve_repo_for_issue,
        reconcile_closed_or_missing_issue=_reconcile_closed_or_missing_issue,
    )


def _reconcile_closed_or_missing_issue(issue_num: str, repo_name: str, workflow_id: str) -> None:
    """Clear stale local runtime/workflow state when remote issue is closed or missing."""
    issue_key = str(issue_num)

    launched = HostStateManager.load_launched_agents(recent_only=False)
    launched_keys_removed = [
        key
        for key, value in launched.items()
        if key == issue_key
        or key.startswith(f"{issue_key}_")
        or (isinstance(value, dict) and str(value.get("issue", "")) == issue_key)
    ]
    for key in launched_keys_removed:
        launched.pop(key, None)
    HostStateManager.save_launched_agents(launched)

    tracked = HostStateManager.load_tracked_issues()
    tracked_removed = tracked.pop(issue_key, None) is not None
    HostStateManager.save_tracked_issues(tracked)

    _get_wf_state().remove_mapping(issue_key)
    _get_wf_state().clear_pending_approval(issue_key)
    cleared_guards = clear_launch_guard(issue_key)

    logger.info(
        "Reconciled closed/missing issue #%s (%s): workflow_id=%s mapping_cleared=true "
        "launched_keys_removed=%s tracked_removed=%s launch_guards_cleared=%s",
        issue_key,
        repo_name,
        workflow_id or "unknown",
        len(launched_keys_removed),
        tracked_removed,
        cleared_guards,
    )


def _resolve_project_for_issue(issue_num: str, workflow_id: str | None = None) -> str | None:
    return _svc_resolve_project_for_issue(
        issue_num=str(issue_num),
        workflow_id=workflow_id,
        find_task_file_for_issue=_find_task_file_for_issue,
        resolve_project_from_task_file=_resolve_project_from_task_file,
        iter_project_configs=_iter_project_configs,
        project_config=PROJECT_CONFIG,
        get_repos=get_repos,
    )


def _find_task_file_for_issue(issue_num: str) -> str | None:
    return _svc_find_task_file_for_issue(
        issue_num=str(issue_num),
        db_only_task_mode=is_postgres_backend(NEXUS_STORAGE_BACKEND),
        base_dir=BASE_DIR,
        nexus_dir_name=get_nexus_dir_name(),
    )


def _resolve_project_from_task_file(task_file: str) -> str | None:
    return _svc_resolve_project_from_task_file(
        task_file=task_file,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        iter_project_configs=_iter_project_configs,
        get_repos=get_repos,
    )


def _recover_unmapped_issues_from_completions(max_relaunches: int = 20) -> int:
    """Recover issues missing workflow mapping using latest completion signal.

    This path handles cases where workflow mapping is lost after restarts but
    local completion + task context still indicates the next agent to run.
    """
    if is_postgres_backend(NEXUS_STORAGE_BACKEND):
        return 0

    runtime = getattr(_get_process_orchestrator(), "_runtime", None)
    return _service_recover_unmapped_issues_from_completions(
        max_relaunches=max_relaunches,
        logger=logger,
        runtime=runtime,
        completion_store=_get_completion_store(),
        host_state_manager=HostStateManager,
        get_workflow_id=lambda issue_num: _get_wf_state().get_workflow_id(issue_num),
        normalize_agent_reference=_normalize_agent_reference,
        is_terminal_agent_reference=_is_terminal_agent_reference,
        find_task_file_for_issue=_find_task_file_for_issue,
        resolve_project_from_task_file=_resolve_project_from_task_file,
        get_default_project=get_default_project,
        project_config=PROJECT_CONFIG,
        resolve_repo_for_issue=_resolve_repo_for_issue,
        build_issue_url=build_issue_url,
        get_sop_tier=get_sop_tier,
        invoke_ai_agent=invoke_ai_agent,
        base_dir=BASE_DIR,
        orphan_recovery_last_attempt=_orphan_recovery_last_attempt,
        orphan_recovery_cooldown_seconds=_ORPHAN_RECOVERY_COOLDOWN_SECONDS,
    )


def check_agent_comments():
    """Monitor Git issues for agent comments requesting input across all projects."""
    allow_service_token_fallback = str(
        os.getenv("NEXUS_ALLOW_SERVICE_GIT_TOKEN_FALLBACK", "true")
    ).strip().lower() in {"1", "true", "yes", "on"}
    polling_token_by_project: dict[str, str] = {}
    polling_token_owner_by_project: dict[str, str] = {}

    def _get_cached_polling_token(project_key: str) -> str:
        cache_key = project_key.lower()
        if cache_key in polling_token_by_project:
            return polling_token_by_project.get(cache_key) or ""
        token, token_owner = _resolve_project_polling_git_token(project_key)
        polling_token_by_project[cache_key] = str(token or "")
        polling_token_owner_by_project[cache_key] = str(token_owner or "")
        if token and token_owner:
            logger.debug(
                "Using per-user polling token for project %s (nexus_id=%s).",
                project_key,
                token_owner,
            )
        return polling_token_by_project.get(cache_key) or ""

    def _should_poll_project(project_key: str) -> bool:
        if not _has_setup_ready_user_for_project(project_key):
            return False
        if allow_service_token_fallback:
            return True
        return bool(_get_cached_polling_token(project_key))

    def _polling_git_platform(
        repo: str,
        project_name: str | None = None,
        token_override: str | None = None,
    ):
        project_key = str(project_name or get_default_project()).strip()
        resolved_token_override = token_override
        if _auth_enabled():
            cached = _get_cached_polling_token(project_key)
            if not resolved_token_override:
                resolved_token_override = cached or None

        return get_git_platform(
            repo,
            project_name=project_name,
            token_override=resolved_token_override,
        )

    _run_comment_monitor_cycle(
        logger=logger,
        iter_projects=lambda: _iter_project_configs(PROJECT_CONFIG, get_repos),
        get_project_platform=get_project_platform,
        get_repo=get_repo,
        list_workflow_issue_numbers=_build_workflow_issue_number_lister(
            get_workflow_monitor_policy_plugin=get_workflow_monitor_policy_plugin,
            get_git_platform=_polling_git_platform,
            workflow_labels=_WORKFLOW_MONITOR_LABELS,
            list_bound_issue_numbers=(
                _list_bound_workflow_issue_numbers if _auth_enabled() else None
            ),
        ),
        get_bot_comments=_build_bot_comments_getter(
            get_workflow_monitor_policy_plugin=get_workflow_monitor_policy_plugin,
            get_git_platform=_polling_git_platform,
            bot_author="Ghabs",
            resolve_issue_token=_resolve_issue_requester_token,
            require_issue_requester_token=_auth_enabled(),
        ),
        notify_agent_needs_input=notify_agent_needs_input,
        notified_comments=PROCESSOR_RUNTIME_STATE.notified_comments,
        clear_polling_failures=_clear_polling_failures,
        record_polling_failure=_record_polling_failure,
        should_poll_project=_should_poll_project if _auth_enabled() else None,
    )


def check_and_notify_pr(issue_num, project):
    """
    Check if there's a PR linked to the issue and notify user for review.

    Delegates to nexus-arc's GitPlatform.search_linked_prs().

    Args:
        issue_num: Git issue number
        project: Project name
    """
    _service_check_and_notify_pr(
        issue_num=issue_num,
        project=project,
        logger=logger,
        get_repo=get_repo,
        get_workflow_monitor_policy_plugin=get_workflow_monitor_policy_plugin,
        get_git_platform=get_git_platform,
        notify_workflow_completed=notify_workflow_completed,
        resolve_issue_token=_resolve_issue_requester_token,
        require_issue_requester_token=_auth_enabled(),
    )


def check_completed_agents():
    """Monitor for completed agent steps and auto-chain to next agent.

    Delegates to _post_completion_comments_from_logs()
    which uses the nexus-arc framework for completion scanning and auto-chaining.
    """
    _run_completion_monitor_cycle(
        post_completion_comments_from_logs=_post_completion_comments_from_logs,
    )


def _render_checklist_from_workflow(project_name: str, tier_name: str) -> str:
    return _svc_render_checklist_from_workflow(
        project_name=project_name,
        tier_name=tier_name,
        get_workflow_definition_path=get_workflow_definition_path,
    )


def _render_fallback_checklist(tier_name: str) -> str:
    return _svc_render_fallback_checklist(tier_name=tier_name)


def get_sop_tier(task_type, title=None, body=None):
    """Returns (tier_name, sop_template, workflow_label) based on task type AND content.

    Now integrates WorkflowRouter for intelligent routing based on issue content.

    Workflow mapping:
    - hotfix, chore, feature-simple, improvement-simple → fast-track:
        Triage → Develop → Review → Deploy
    - bug → shortened:
        Triage → Debug → Develop → Review → Deploy → Close
    - feature, improvement, release → full:
        Triage → Design → Develop → Review → Compliance → Deploy → Close
    """
    return _svc_get_sop_tier_for_task(
        task_type=str(task_type or ""),
        title=title,
        body=body,
        suggest_tier_label=WorkflowRouter.suggest_tier_label,
        logger=logger,
    )


def generate_issue_name(
    content,
    project_name,
    requester_context: dict[str, Any] | None = None,
):
    """Generate a concise task name using orchestrator (CLI only).

    Returns a slugified name in format: "this-is-the-task-name"
    Falls back to slugified content if AI tools are unavailable.
    """
    return _svc_generate_issue_name_with_ai(
        content=content,
        project_name=project_name,
        run_analysis=orchestrator.run_text_to_speech_analysis,
        slugify=slugify,
        logger=logger,
        requester_context=requester_context,
    )


def _refine_issue_content(
    content: str,
    project_name: str,
    requester_context: dict[str, Any] | None = None,
) -> str:
    return _svc_refine_issue_content_with_ai(
        content=content,
        project_name=project_name,
        run_analysis=orchestrator.run_text_to_speech_analysis,
        logger=logger,
        requester_context=requester_context,
    )


def _extract_inline_task_name(content: str) -> str:
    if not isinstance(content, str) or not content:
        return ""
    for line in content.splitlines():
        stripped = str(line or "").strip()
        if not stripped.startswith("**Task Name:**"):
            continue
        candidate = stripped.split("**Task Name:**", 1)[1].strip()
        return candidate
    return ""


def process_file(filepath):
    """Processes a single task file."""
    logger.info(f"Processing: {filepath}")

    try:
        task_ctx = _load_task_context(
            filepath=filepath,
            project_config=PROJECT_CONFIG,
            base_dir=BASE_DIR,
            get_nexus_dir_name=get_nexus_dir_name,
            iter_project_configs=_iter_project_configs,
            get_repos=get_repos,
        )
        if not task_ctx:
            logger.warning("⚠️ No project config for file '%s', skipping.", filepath)
            return False
        return _process_task_context(task_ctx=task_ctx, filepath=filepath)

    except Exception as e:
        logger.error(f"Failed to process {filepath}: {e}")
        return False


def process_task_payload(*, project_key: str, workspace: str, filename: str, content: str) -> bool:
    return _svc_process_task_payload(
        project_key=project_key,
        workspace=workspace,
        filename=filename,
        content=content,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        logger=logger,
        process_task_context_fn=_process_task_context,
    )


def _ensure_project_and_repo_access(
    requester_nexus_id: str,
    project_key: str,
    repo_name: str,
) -> tuple[bool, str]:
    if not _auth_enabled():
        return True, ""
    requester = str(requester_nexus_id or "").strip()
    if not requester:
        return False, "Missing requester identity for authenticated execution."
    allowed_project, project_error = _check_project_access(requester, project_key)
    if not allowed_project:
        return False, project_error
    allowed_repo, repo_error = _check_repo_access(requester, repo_name, project_key)
    if not allowed_repo:
        return False, repo_error
    return True, ""


def _process_task_context(*, task_ctx: dict[str, object], filepath: str) -> bool:
    return _svc_process_task_context(
        task_ctx=task_ctx,
        filepath=filepath,
        deps={
            "base_dir": BASE_DIR,
            "logger": logger,
            "emit_alert": emit_alert,
            "extract_repo_from_issue_url": _extract_repo_from_issue_url,
            "resolve_project_for_repo": _resolve_project_for_repo,
            "reroute_webhook_task_to_project": _reroute_webhook_task_to_project,
            "get_tasks_active_dir": get_tasks_active_dir,
            "is_recent_launch": is_recent_launch,
            "get_initial_agent_from_workflow": _get_initial_agent_from_workflow,
            "get_repo_for_project": get_repo,
            "get_repos_for_project": get_repos,
            "get_repo_branch_for_project": get_repo_branch,
            "resolve_git_dir_for_project": _resolve_git_dir,
            "resolve_git_dir_for_repo_for_project": _resolve_git_dir_for_repo,
            "resolve_git_dirs_for_project": _resolve_git_dirs,
            "run_workflow_start_git_sync": _sync_project_repos_on_workflow_start,
            "resolve_tier_for_issue": _resolve_tier_for_issue_scoped,
            "invoke_ai_agent": invoke_ai_agent,
            "handle_webhook_task": _handle_webhook_task,
            "handle_new_task": _handle_new_task,
            "refine_issue_content": _refine_issue_content,
            "extract_inline_task_name": _extract_inline_task_name,
            "slugify": slugify,
            "generate_issue_name": generate_issue_name,
            "get_sop_tier": get_sop_tier,
            "render_checklist_from_workflow": _render_checklist_from_workflow,
            "render_fallback_checklist": _render_fallback_checklist,
            "create_issue": _create_issue,
            "rename_task_file_and_sync_issue_body": _rename_task_file_and_sync_issue_body,
            "get_workflow_state_plugin": get_workflow_state_plugin,
            "workflow_state_plugin_kwargs": _WORKFLOW_STATE_PLUGIN_KWARGS,
            "start_workflow": start_workflow,
            "bind_issue_requester": _bind_issue_requester,
            "ensure_project_and_repo_access": _ensure_project_and_repo_access,
        },
    )


def _process_filesystem_inbox_once(base_dir: str) -> None:
    """Scan and process filesystem inbox tasks once."""
    nexus_dir_name = get_nexus_dir_name()
    pattern = os.path.join(base_dir, "**", nexus_dir_name, "inbox", "*", "*.md")
    files = glob.glob(pattern, recursive=True)

    for filepath in files:
        process_file(filepath)


def main():
    from nexus.core.orchestration.nexus_core_helpers import setup_event_handlers

    _svc_run_inbox_processor_main(
        logger=logger,
        base_dir=BASE_DIR,
        sleep_interval=SLEEP_INTERVAL,
        get_inbox_storage_backend=get_inbox_storage_backend,
        reconcile_completion_signals_on_startup=reconcile_completion_signals_on_startup,
        check_stuck_agents=check_stuck_agents,
        check_agent_comments=check_agent_comments,
        check_completed_agents=check_completed_agents,
        merge_queue_auto_merge_once=lambda: _merge_queue_auto_merge_once(
            resolve_issue_token=_resolve_issue_requester_token,
            require_issue_requester_token=_auth_enabled(),
        ),
        cleanup_stale_worktrees_once=_cleanup_stale_worktrees_once,
        drain_postgres_inbox_queue=_drain_postgres_inbox_queue,
        process_filesystem_inbox_once=_process_filesystem_inbox_once,
        run_processor_loop=_run_processor_loop,
        runtime_state=PROCESSOR_RUNTIME_STATE,
        time_module=time,
        setup_event_handlers=setup_event_handlers,
    )


def _drain_postgres_inbox_queue(batch_size: int = 25) -> None:
    _svc_drain_postgres_inbox_queue_once(
        batch_size=batch_size,
        logger=logger,
        claim_pending_tasks=claim_pending_tasks,
        process_task_payload=process_task_payload,
        mark_task_done=mark_task_done,
        mark_task_failed=mark_task_failed,
    )


if __name__ == "__main__":
    main()
