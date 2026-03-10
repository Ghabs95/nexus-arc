import asyncio
import logging
import os
from typing import Any

from nexus.adapters.git.utils import build_issue_url, resolve_repo
from nexus.core.analytics.reporting import get_stats_report
from nexus.core.audit_store import AuditStore
from nexus.core.completion import scan_for_completions
from nexus.core.config import (
    AI_PERSONA,
    BASE_DIR,
    LOGS_DIR,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
    NEXUS_FEATURE_REGISTRY_ENABLED,
    NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
    NEXUS_STORAGE_BACKEND,
    NEXUS_STORAGE_DSN,
    NEXUS_WORKFLOW_BACKEND,
    PROJECT_CONFIG,
    TELEGRAM_BOT_LOG_FILE,
    get_default_repo,
    get_default_project,
    get_inbox_dir,
    get_project_display_names,
    get_inbox_storage_backend,
    get_nexus_dir_name,
    get_repo,
    get_repos,
    get_tasks_active_dir,
    get_tasks_closed_dir,
    get_tasks_logs_dir,
    get_track_short_projects,
)
from nexus.core.error_handling import format_error_for_user
from nexus.core.feature_registry_service import FeatureRegistryService
from nexus.core.git.direct_issue_plugin_service import (
    get_direct_issue_plugin as _svc_get_direct_issue_plugin,
)
from nexus.core.handlers.feature_registry_command_handlers import FeatureRegistryCommandDeps
from nexus.core.handlers.inbox_routing_handler import TYPES
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.memory import append_message, create_chat, get_chat_history
from nexus.core.orchestration.ai_orchestrator import get_orchestrator
from nexus.core.orchestration.nexus_core_helpers import get_workflow_definition_path
from nexus.core.orchestration.plugin_runtime import (
    get_profiled_plugin,
    get_runtime_ops_plugin,
    get_workflow_state_plugin,
)
from nexus.core.project.catalog import (
    get_project_label as _svc_get_project_label,
)
from nexus.core.project.catalog import (
    iter_project_keys as _svc_iter_project_keys,
)
from nexus.core.project.issue_command_deps import (
    default_issue_url as _svc_default_issue_url,
)
from nexus.core.project.issue_command_deps import (
    get_issue_details as _svc_get_issue_details,
)
from nexus.core.project.issue_command_deps import (
    project_issue_url as _svc_project_issue_url,
)
from nexus.core.project.issue_command_deps import (
    project_repo as _svc_project_repo,
)
from nexus.core.project.key_utils import normalize_project_key_optional as _normalize_project_key
from nexus.core.runtime.bridge import find_task_file_by_issue
from nexus.core.runtime.bridge import get_retry_fuse_status
from nexus.core.runtime.bridge import get_sop_tier_from_issue, invoke_ai_agent
from nexus.core.runtime.bridge import workflow_pause_handler
from nexus.core.runtime.bridge import workflow_resume_handler
from nexus.core.runtime.bridge import workflow_stop_handler
from nexus.core.state_manager import HostStateManager
from nexus.core.task_flow.helpers import normalize_agent_reference as _normalize_agent_reference, get_sop_tier
from nexus.core.telegram.telegram_handler_deps_service import (
    build_issue_handler_deps as _svc_build_issue_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_monitoring_handler_deps as _svc_build_monitoring_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_ops_handler_deps as _svc_build_ops_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_visualize_handler_deps as _svc_build_visualize_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_watch_handler_deps as _svc_build_watch_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_workflow_handler_deps as _svc_build_workflow_handler_deps,
)
from nexus.core.telegram.telegram_issue_selection_service import (
    list_project_issues as _svc_list_project_issues,
)
from nexus.core.telegram.telegram_project_logs_service import (
    extract_issue_number_from_file as _svc_extract_issue_number_from_file,
)
from nexus.core.telegram.telegram_project_logs_service import (
    extract_project_from_nexus_path as _svc_extract_project_from_nexus_path,
)
from nexus.core.telegram.telegram_project_logs_service import (
    find_issue_log_files as _svc_find_issue_log_files,
)
from nexus.core.telegram.telegram_project_logs_service import (
    find_task_logs as _svc_find_task_logs,
)
from nexus.core.telegram.telegram_project_logs_service import (
    get_project_logs_dir as _svc_get_project_logs_dir,
)
from nexus.core.telegram.telegram_project_logs_service import (
    get_project_root as _svc_get_project_root,
)
from nexus.core.telegram.telegram_project_logs_service import (
    read_latest_log_full as _svc_read_latest_log_full,
)
from nexus.core.telegram.telegram_project_logs_service import (
    read_latest_log_tail as _svc_read_latest_log_tail,
)
from nexus.core.telegram.telegram_project_logs_service import (
    read_log_matches as _svc_read_log_matches,
)
from nexus.core.telegram.telegram_project_logs_service import (
    resolve_project_config_from_task as _svc_resolve_project_config_from_task,
)
from nexus.core.telegram.telegram_project_logs_service import (
    resolve_project_root_from_task_path as _svc_resolve_project_root_from_task_path,
)
from nexus.core.telegram.telegram_project_logs_service import (
    search_logs_for_issue as _svc_search_logs_for_issue,
)
from nexus.core.telegram.telegram_workflow_probe_service import (
    get_expected_running_agent_from_workflow as _svc_get_expected_running_agent_from_workflow,
)
from nexus.core.user_manager import get_user_manager
from nexus.core.workflow_runtime.workflow_control_service import kill_issue_agent, prepare_continue_context
from nexus.core.workflow_runtime.workflow_ops_service import (
    build_workflow_snapshot,
    fetch_workflow_state_snapshot,
    reconcile_issue_from_signals,
)
from nexus.core.workflow_runtime.workflow_signal_sync import (
    extract_structured_completion_signals,
    read_latest_local_completion,
    write_local_completion_from_signal,
)
from nexus.core.workflow_runtime.workflow_watch_service import get_workflow_watch_service

logger = logging.getLogger(__name__)
DEFAULT_REPO = get_default_repo()
PROJECTS_MAP = get_project_display_names()
active_tail_sessions: dict[tuple[int, int], str] = {}
active_tail_tasks: dict[tuple[int, int], asyncio.Task] = {}
_orchestrator = None
_user_manager = None
_tracked_issues: dict[str, Any] | None = None
_feature_registry_service: FeatureRegistryService | None = None

_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "storage_type": "postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file",
    "storage_config": (
        {"connection_string": NEXUS_STORAGE_DSN}
        if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
        else {}
    ),
    "issue_to_workflow_id": lambda n: get_workflow_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: get_workflow_state().map_issue(n, w),
    "workflow_definition_path_resolver": get_workflow_definition_path,
    "clear_pending_approval": lambda n: get_workflow_state().clear_pending_approval(n),
    "audit_log": AuditStore.audit_log,
}


def _iter_project_keys() -> list[str]:
    return _svc_iter_project_keys(project_config=PROJECT_CONFIG)


def _get_project_label(project_key: str) -> str:
    return _svc_get_project_label(project_key=project_key, projects_map=PROJECTS_MAP)


def _resolve_project_root_from_task_path(task_file: str) -> str:
    return _svc_resolve_project_root_from_task_path(task_file)


def _extract_project_from_nexus_path(path: str) -> str | None:
    return _svc_extract_project_from_nexus_path(
        path=path,
        normalize_project_key=_normalize_project_key,
        iter_project_keys_fn=_iter_project_keys,
    )


def _project_repo(project_key: str) -> str:
    return _svc_project_repo(
        project_key=project_key,
        project_config=PROJECT_CONFIG,
        default_repo=DEFAULT_REPO,
        resolve_repo=resolve_repo,
    )


def _project_issue_url(project_key: str, issue_num: str) -> str:
    return _svc_project_issue_url(
        project_key=project_key,
        issue_num=issue_num,
        project_config=PROJECT_CONFIG,
        default_repo=DEFAULT_REPO,
        resolve_repo=resolve_repo,
        build_issue_url=build_issue_url,
    )


def _default_issue_url(issue_num: str) -> str:
    return _svc_default_issue_url(
        issue_num=issue_num,
        default_repo=DEFAULT_REPO,
        get_default_project=get_default_project,
        project_issue_url_fn=_project_issue_url,
    )


def _get_direct_issue_plugin(repo: str, requester_nexus_id: str | None = None):
    return _svc_get_direct_issue_plugin(
        repo=repo,
        get_profiled_plugin=get_profiled_plugin,
        requester_nexus_id=requester_nexus_id,
    )


def _get_orchestrator():
    global _orchestrator
    if _orchestrator is None:
        _orchestrator = get_orchestrator()
    return _orchestrator


def _get_user_manager():
    global _user_manager
    if _user_manager is None:
        _user_manager = get_user_manager()
    return _user_manager


def _get_tracked_issues_ref() -> dict[str, Any]:
    global _tracked_issues
    if _tracked_issues is None:
        _tracked_issues = HostStateManager.load_tracked_issues()
    return _tracked_issues


def _get_feature_registry_service() -> FeatureRegistryService:
    global _feature_registry_service
    if _feature_registry_service is None:
        _feature_registry_service = FeatureRegistryService(
            enabled=NEXUS_FEATURE_REGISTRY_ENABLED,
            backend=NEXUS_STORAGE_BACKEND,
            state_dir=os.path.join(BASE_DIR, get_nexus_dir_name(), "state"),
            postgres_dsn=NEXUS_STORAGE_DSN,
            max_items_per_project=NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
            dedup_similarity=NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
        )
    return _feature_registry_service


def get_issue_details(
    issue_num: str,
    repo: str | None = None,
    requester_nexus_id: str | None = None,
):
    return _svc_get_issue_details(
        issue_num=str(issue_num),
        repo=repo,
        default_repo=DEFAULT_REPO,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logger,
        requester_nexus_id=requester_nexus_id,
    )


def _resolve_project_config_from_task(task_file: str):
    return _svc_resolve_project_config_from_task(
        task_file=task_file,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
    )


def _find_task_logs(task_file: str) -> list[str]:
    return _svc_find_task_logs(
        task_file=task_file,
        logger=logger,
        resolve_project_root_from_task_path_fn=_resolve_project_root_from_task_path,
        extract_project_from_nexus_path=_extract_project_from_nexus_path,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )


def _read_log_matches(
    log_path: str,
    issue_num: str,
    issue_url: str | None = None,
    max_lines: int = 20,
) -> list[str]:
    return _svc_read_log_matches(
        log_path=str(log_path),
        issue_num=str(issue_num),
        issue_url=issue_url,
        max_lines=max_lines,
        logger=logger,
    )


def _search_logs_for_issue(issue_num: str) -> list[str]:
    return _svc_search_logs_for_issue(
        issue_num=str(issue_num),
        telegram_bot_log_file=TELEGRAM_BOT_LOG_FILE,
        logs_dir=LOGS_DIR,
        logger=logger,
        read_log_matches_fn=_read_log_matches,
    )


def _read_latest_log_tail(task_file: str, max_lines: int = 20) -> list[str]:
    return _svc_read_latest_log_tail(
        task_file=task_file,
        max_lines=max_lines,
        logger=logger,
        find_task_logs_fn=_find_task_logs,
    )


def _find_issue_log_files(issue_num: str, task_file: str | None = None) -> list[str]:
    return _svc_find_issue_log_files(
        issue_num=str(issue_num),
        task_file=task_file,
        base_dir=BASE_DIR,
        nexus_dir_name=get_nexus_dir_name(),
        extract_project_from_nexus_path=_extract_project_from_nexus_path,
        resolve_project_root_from_task_path_fn=_resolve_project_root_from_task_path,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )


def _read_latest_log_full(task_file: str | None) -> list[str]:
    return _svc_read_latest_log_full(
        task_file=task_file,
        logger=logger,
        find_task_logs_fn=_find_task_logs,
    )


def _extract_issue_number_from_file(file_path: str) -> str | None:
    return _svc_extract_issue_number_from_file(file_path=file_path, logger=logger)


def _read_latest_local_completion(issue_num: str) -> dict[str, Any] | None:
    return read_latest_local_completion(BASE_DIR, get_nexus_dir_name(), issue_num)


def _write_local_completion_from_signal(
    project_key: str,
    issue_num: str,
    signal: dict[str, str],
) -> str:
    return write_local_completion_from_signal(
        BASE_DIR,
        get_nexus_dir_name(),
        project_key,
        issue_num,
        signal,
        key_findings=[
            "Workflow/comment/local completion drift reconciled via /reconcile",
            f"Source comment id: {signal.get('comment_id', 'n/a')}",
        ],
    )


def _get_expected_running_agent_from_workflow(issue_num: str) -> str | None:
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_KWARGS,
        cache_key="workflow:state-engine:expected-agent:discord",
    )
    return _svc_get_expected_running_agent_from_workflow(
        issue_num=str(issue_num),
        get_workflow_id=lambda n: get_workflow_state().get_workflow_id(n),
        workflow_plugin=workflow_plugin,
    )


def _get_inbox_queue_overview(limit: int) -> dict[str, Any]:
    from nexus.core.integrations.inbox_queue import get_queue_overview

    return get_queue_overview(limit=limit)


def _save_tracked_issues(data: dict[str, Any]) -> None:
    HostStateManager.save_tracked_issues(data)


def monitoring_bridge_deps(*, allowed_user_ids, ensure_project, ensure_project_issue):
    return _svc_build_monitoring_handler_deps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
        types_map=TYPES,
        ensure_project=ensure_project,
        ensure_project_issue=ensure_project_issue,
        normalize_project_key=_normalize_project_key,
        iter_project_keys=_iter_project_keys,
        get_project_label=_get_project_label,
        get_project_root=lambda key: _svc_get_project_root(
            project_key=key,
            project_config=PROJECT_CONFIG,
            base_dir=BASE_DIR,
        ),
        get_project_logs_dir=lambda key: _svc_get_project_logs_dir(
            project_key=key,
            project_config=PROJECT_CONFIG,
            base_dir=BASE_DIR,
            get_tasks_logs_dir=get_tasks_logs_dir,
        ),
        get_inbox_storage_backend=get_inbox_storage_backend,
        get_inbox_queue_overview=_get_inbox_queue_overview,
        project_repo=_project_repo,
        get_issue_details=get_issue_details,
        get_inbox_dir=get_inbox_dir,
        get_tasks_active_dir=get_tasks_active_dir,
        get_tasks_closed_dir=get_tasks_closed_dir,
        extract_issue_number_from_file=_extract_issue_number_from_file,
        build_issue_url=build_issue_url,
        find_task_file_by_issue=find_task_file_by_issue,
        find_issue_log_files=_find_issue_log_files,
        read_latest_log_tail=_read_latest_log_tail,
        search_logs_for_issue=_search_logs_for_issue,
        read_latest_log_full=_read_latest_log_full,
        read_log_matches=_read_log_matches,
        active_tail_sessions=active_tail_sessions,
        active_tail_tasks=active_tail_tasks,
        get_retry_fuse_status=get_retry_fuse_status,
        normalize_agent_reference=_normalize_agent_reference,
        get_expected_running_agent_from_workflow=_get_expected_running_agent_from_workflow,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        extract_structured_completion_signals=extract_structured_completion_signals,
        read_latest_local_completion=_read_latest_local_completion,
        build_workflow_snapshot=build_workflow_snapshot,
    )


def ops_bridge_deps(*, allowed_user_ids, prompt_project_selection, ensure_project_issue):
    return _svc_build_ops_handler_deps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        base_dir=BASE_DIR,
        nexus_dir_name=get_nexus_dir_name(),
        project_config=PROJECT_CONFIG,
        prompt_project_selection=prompt_project_selection,
        ensure_project_issue=ensure_project_issue,
        get_project_label=_get_project_label,
        get_stats_report=get_stats_report,
        get_inbox_storage_backend=get_inbox_storage_backend,
        get_inbox_queue_overview=_get_inbox_queue_overview,
        format_error_for_user=format_error_for_user,
        get_audit_history=lambda issue_num, limit: AuditStore.get_audit_history(
            int(issue_num), limit
        ),
        get_repo=get_repo,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        orchestrator=_get_orchestrator(),
        ai_persona=AI_PERSONA,
        get_chat_history=get_chat_history,
        append_message=append_message,
        create_chat=create_chat,
        requester_context_builder=lambda user_id: {
            "platform": "discord",
            "platform_user_id": str(user_id),
            "nexus_id": str(_get_user_manager().resolve_nexus_id("discord", str(user_id)) or ""),
        },
    )


def issue_bridge_deps(*, allowed_user_ids, prompt_project_selection, ensure_project_issue):
    return _svc_build_issue_handler_deps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        project_config=PROJECT_CONFIG,
        prompt_project_selection=prompt_project_selection,
        ensure_project_issue=ensure_project_issue,
        project_repo=_project_repo,
        project_issue_url=_project_issue_url,
        get_issue_details=get_issue_details,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        resolve_project_config_from_task=_resolve_project_config_from_task,
        invoke_ai_agent=invoke_ai_agent,
        get_sop_tier=get_sop_tier,
        find_task_file_by_issue=find_task_file_by_issue,
        resolve_repo=resolve_repo,
        build_issue_url=build_issue_url,
        user_manager=_get_user_manager(),
        save_tracked_issues=_save_tracked_issues,
        tracked_issues_ref=_get_tracked_issues_ref(),
        default_issue_url=_default_issue_url,
        get_project_label=_get_project_label,
        track_short_projects=get_track_short_projects(),
    )


def visualize_bridge_deps(*, allowed_user_ids, prompt_project_selection, ensure_project_issue):
    return _svc_build_visualize_handler_deps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        prompt_project_selection=prompt_project_selection,
        ensure_project_issue=ensure_project_issue,
    )


def watch_bridge_deps(*, allowed_user_ids, prompt_project_selection, ensure_project_issue):
    return _svc_build_watch_handler_deps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        prompt_project_selection=prompt_project_selection,
        ensure_project_issue=ensure_project_issue,
        get_watch_service=get_workflow_watch_service,
    )


def workflow_bridge_deps(*, allowed_user_ids, prompt_project_selection, ensure_project_issue):
    return _svc_build_workflow_handler_deps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        project_config=PROJECT_CONFIG,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        prompt_project_selection=prompt_project_selection,
        ensure_project_issue=ensure_project_issue,
        find_task_file_by_issue=find_task_file_by_issue,
        project_repo=_project_repo,
        get_issue_details=get_issue_details,
        resolve_project_config_from_task=_resolve_project_config_from_task,
        invoke_ai_agent=invoke_ai_agent,
        get_sop_tier_from_issue=get_sop_tier_from_issue,
        get_sop_tier=get_sop_tier,
        get_last_tier_for_issue=HostStateManager.get_last_tier_for_issue,
        prepare_continue_context=prepare_continue_context,
        kill_issue_agent=kill_issue_agent,
        get_runtime_ops_plugin=get_runtime_ops_plugin,
        get_workflow_state_plugin=get_workflow_state_plugin,
        fetch_workflow_state_snapshot=fetch_workflow_state_snapshot,
        scan_for_completions=scan_for_completions,
        normalize_agent_reference=_normalize_agent_reference,
        get_expected_running_agent_from_workflow=_get_expected_running_agent_from_workflow,
        reconcile_issue_from_signals=reconcile_issue_from_signals,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        extract_structured_completion_signals=extract_structured_completion_signals,
        write_local_completion_from_signal=_write_local_completion_from_signal,
        build_workflow_snapshot=build_workflow_snapshot,
        read_latest_local_completion=_read_latest_local_completion,
        workflow_pause_handler=workflow_pause_handler,
        workflow_resume_handler=workflow_resume_handler,
        workflow_stop_handler=workflow_stop_handler,
        requester_context_builder=lambda user_id: {
            "platform": "discord",
            "platform_user_id": str(user_id),
            "nexus_id": str(_get_user_manager().resolve_nexus_id("discord", str(user_id)) or ""),
        },
    )


def feature_registry_bridge_deps(*, allowed_user_ids):
    return FeatureRegistryCommandDeps(
        logger=logger,
        allowed_user_ids=allowed_user_ids,
        iter_project_keys=_iter_project_keys,
        normalize_project_key=_normalize_project_key,
        get_project_label=_get_project_label,
        feature_registry=_get_feature_registry_service(),
    )


def list_project_issues_bridge(
    *,
    project_key: str,
    state: str,
    limit: int = 25,
    requester_nexus_id: str | None = None,
) -> list[dict[str, Any]]:
    return _svc_list_project_issues(
        project_key=project_key,
        project_config=PROJECT_CONFIG,
        get_repos=get_repos,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logger,
        state=state,
        limit=limit,
        requester_nexus_id=requester_nexus_id,
    )
