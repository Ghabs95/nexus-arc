import logging

from nexus.adapters.git.utils import build_issue_url, resolve_repo
from nexus.core.analytics.reporting import get_stats_report
from nexus.core.audit_store import AuditStore
from nexus.core.completion import scan_for_completions
# Import configuration from centralized config module
from nexus.core.config import (
    AI_PERSONA,
    BASE_DIR,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_STORAGE_DSN,
    NEXUS_WORKFLOW_BACKEND,
    ORCHESTRATOR_CONFIG,
    PROJECT_CONFIG,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_LOG_FILE,
    TELEGRAM_TOKEN,
    get_default_repo,
    get_repo,
    get_inbox_dir,
    get_nexus_dir_name,
    get_tasks_active_dir,
    get_tasks_closed_dir,
    get_track_short_projects,
)
from nexus.core.error_handling import format_error_for_user
from nexus.core.handlers.inbox_routing_handler import (
    TYPES,
)
from nexus.core.handlers.issue_command_handlers import (
    IssueHandlerDeps,
)
from nexus.core.handlers.monitoring_command_handlers import (
    MonitoringHandlersDeps,
)
from nexus.core.handlers.ops_command_handlers import (
    OpsHandlerDeps,
)
from nexus.core.handlers.workflow_command_handlers import (
    WorkflowHandlerDeps,
)
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.memory import (
    append_message,
    create_chat,
    get_chat_history,
)
from nexus.core.orchestration.ai_orchestrator import get_orchestrator
from nexus.core.orchestration.nexus_core_helpers import get_workflow_definition_path
from nexus.core.orchestration.plugin_runtime import (
    get_runtime_ops_plugin,
    get_workflow_state_plugin,
)
from nexus.core.project.key_utils import normalize_project_key_optional as _normalize_project_key
from nexus.core.rate_limiter import get_rate_limiter
from nexus.core.runtime.bridge import get_sop_tier_from_issue, invoke_ai_agent
from nexus.core.runtime.workflow_commands import (
    pause_handler as workflow_pause_handler,
)
from nexus.core.runtime.workflow_commands import (
    resume_handler as workflow_resume_handler,
)
from nexus.core.runtime.workflow_commands import (
    stop_handler as workflow_stop_handler,
)
from nexus.core.state_manager import HostStateManager
from nexus.core.task_flow.helpers import (
    get_sop_tier,
    normalize_agent_reference as _normalize_agent_reference,
)
from nexus.core.user_manager import get_user_manager
from nexus.core.utils.logging_filters import install_secret_redaction
from nexus.core.workflow_runtime.workflow_control_service import (
    kill_issue_agent,
    prepare_continue_context,
)
from nexus.core.workflow_runtime.workflow_ops_service import (
    build_workflow_snapshot,
    fetch_workflow_state_snapshot,
    reconcile_issue_from_signals,
)

# --- LOGGING ---
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    force=True,
    handlers=[logging.StreamHandler(), logging.FileHandler(TELEGRAM_BOT_LOG_FILE)],
)


install_secret_redaction([TELEGRAM_TOKEN or ""], logging.getLogger())

# Long-polling calls Telegram getUpdates repeatedly by design.
# Keep these transport logs at WARNING to avoid noisy INFO output.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Initialize AI Orchestrator (CLI-only: gemini-cli + copilot-cli)
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)

# Initialize rate limiter
rate_limiter = get_rate_limiter()

# Initialize user manager
user_manager = get_user_manager()

DEFAULT_REPO = get_default_repo()
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


def _workflow_handler_deps() -> WorkflowHandlerDeps:
    return WorkflowHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        project_config=PROJECT_CONFIG,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        prompt_project_selection=_prompt_project_selection,
        ensure_project_issue=_ensure_project_issue,
        find_task_file_by_issue=find_task_file_by_issue,
        project_repo=_project_repo,
        get_issue_details=get_issue_details,
        resolve_project_config_from_task=resolve_project_config_from_task,
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
        extract_structured_completion_signals=_extract_structured_completion_signals,
        write_local_completion_from_signal=_write_local_completion_from_signal,
        build_workflow_snapshot=build_workflow_snapshot,
        read_latest_local_completion=_read_latest_local_completion,
        workflow_pause_handler=workflow_pause_handler,
        workflow_resume_handler=workflow_resume_handler,
        workflow_stop_handler=workflow_stop_handler,
    )


def _monitoring_handler_deps() -> MonitoringHandlersDeps:
    from nexus.core.runtime.nexus_agent_runtime import get_retry_fuse_status

    async def _ensure_project(ctx, command: str) -> str | None:
        project_key, _issue_num, _rest = await _ensure_project_issue(ctx, command)
        return project_key

    return MonitoringHandlersDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
        types_map=TYPES,
        ensure_project=_ensure_project,
        ensure_project_issue=_ensure_project_issue,
        normalize_project_key=_normalize_project_key,
        iter_project_keys=_iter_project_keys,
        get_project_label=_get_project_label,
        get_project_root=_get_project_root,
        get_project_logs_dir=_get_project_logs_dir,
        get_inbox_storage_backend=get_inbox_storage_backend,
        get_inbox_queue_overview=_get_inbox_queue_overview,
        project_repo=_project_repo,
        get_issue_details=get_issue_details,
        get_inbox_dir=get_inbox_dir,
        get_tasks_active_dir=get_tasks_active_dir,
        get_tasks_closed_dir=get_tasks_closed_dir,
        extract_issue_number_from_file=extract_issue_number_from_file,
        build_issue_url=build_issue_url,
        find_task_file_by_issue=find_task_file_by_issue,
        find_issue_log_files=find_issue_log_files,
        read_latest_log_tail=read_latest_log_tail,
        search_logs_for_issue=search_logs_for_issue,
        read_latest_log_full=read_latest_log_full,
        read_log_matches=read_log_matches,
        active_tail_sessions=active_tail_sessions,
        active_tail_tasks=active_tail_tasks,
        get_retry_fuse_status=get_retry_fuse_status,
        normalize_agent_reference=_normalize_agent_reference,
        get_expected_running_agent_from_workflow=_get_expected_running_agent_from_workflow,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        extract_structured_completion_signals=_extract_structured_completion_signals,
        read_latest_local_completion=_read_latest_local_completion,
        build_workflow_snapshot=build_workflow_snapshot,
    )


def _issue_handler_deps() -> IssueHandlerDeps:
    return IssueHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        project_config=PROJECT_CONFIG,
        prompt_project_selection=_prompt_project_selection,
        ensure_project_issue=_ensure_project_issue,
        project_repo=_project_repo,
        project_issue_url=_project_issue_url,
        get_issue_details=get_issue_details,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        resolve_project_config_from_task=resolve_project_config_from_task,
        invoke_ai_agent=invoke_ai_agent,
        get_sop_tier=get_sop_tier,
        find_task_file_by_issue=find_task_file_by_issue,
        resolve_repo=resolve_repo,
        build_issue_url=build_issue_url,
        user_manager=user_manager,
        save_tracked_issues=save_tracked_issues,
        tracked_issues_ref=tracked_issues,
        default_issue_url=_default_issue_url,
        get_project_label=_get_project_label,
        track_short_projects=get_track_short_projects(),
    )


def _ops_handler_deps() -> OpsHandlerDeps:
    def _get_inbox_queue_overview(limit: int) -> dict[str, object]:
        from nexus.core.integrations.inbox_queue import get_queue_overview

        return get_queue_overview(limit=limit)

    return OpsHandlerDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        nexus_dir_name=get_nexus_dir_name(),
        project_config=PROJECT_CONFIG,
        prompt_project_selection=_prompt_project_selection,
        ensure_project_issue=_ensure_project_issue,
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
        orchestrator=orchestrator,
        ai_persona=AI_PERSONA,
        get_chat_history=get_chat_history,
        append_message=append_message,
        create_chat=create_chat,
    )
