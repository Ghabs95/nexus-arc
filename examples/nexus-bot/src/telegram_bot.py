import asyncio
import contextlib
import logging
import os
import time
from typing import Any

from src.alerting import init_alerting_system
from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    ReplyKeyboardMarkup,
    Update,
)
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)

from nexus.core.config.bootstrap import initialize_runtime

initialize_runtime(configure_logging=False)

# Import configuration from centralized config module
from nexus.core.config import (
    AI_PERSONA,
    BASE_DIR,
    LOGS_DIR,
    NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
    NEXUS_FEATURE_REGISTRY_ENABLED,
    NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
    NEXUS_AUTH_ENABLED,
    NEXUS_GITHUB_CLIENT_ID,
    NEXUS_GITLAB_CLIENT_ID,
    NEXUS_PUBLIC_BASE_URL,
    NEXUS_CORE_STORAGE_DIR,
    NEXUS_STORAGE_DSN,
    NEXUS_STORAGE_BACKEND,
    NEXUS_WORKFLOW_BACKEND,
    ORCHESTRATOR_CONFIG,
    PROJECT_CONFIG,
    TELEGRAM_ALLOWED_USER_IDS,
    TELEGRAM_BOT_LOG_FILE,
    TELEGRAM_CHAT_ID,
    TELEGRAM_TOKEN,
    get_default_repo,
    get_repo,
    get_repos,
    get_inbox_storage_backend,
    get_inbox_dir,
    get_nexus_dir_name,
    get_tasks_active_dir,
    get_tasks_closed_dir,
    get_tasks_logs_dir,
    get_track_short_projects,
)
from nexus.adapters.git.utils import build_issue_url, resolve_repo
from nexus.core.analytics.reporting import get_stats_report
from nexus.core.audit_store import AuditStore
from nexus.core.auth import (
    check_project_access as _svc_check_project_access,
)
from nexus.core.auth import create_login_session_for_user
from nexus.core.auth import (
    format_login_session_ref as _svc_format_login_session_ref,
)
from nexus.core.auth import (
    get_latest_login_session_status as _svc_get_latest_login_session_status,
)
from nexus.core.auth import (
    get_setup_status as _svc_get_setup_status,
)
from nexus.core.auth import register_onboarding_message as _svc_register_onboarding_message
from nexus.core.command_contract import (
    validate_command_parity,
    validate_required_command_interface,
)
from nexus.core.completion import scan_for_completions
from nexus.core.error_handling import format_error_for_user
from nexus.core.feature_registry_service import FeatureRegistryService
from nexus.core.git.direct_issue_plugin_service import (
    get_direct_issue_plugin as _svc_get_direct_issue_plugin,
)
from nexus.core.handlers.audio_transcription_handler import (
    AudioTranscriptionDeps,
    transcribe_telegram_voice,
)
from nexus.core.handlers.bug_report_handler import handle_report_bug
from nexus.core.handlers.callback_command_handlers import (
    CallbackHandlerDeps,
)
from nexus.core.handlers.callback_command_handlers import (
    close_flow_handler as callback_close_flow_handler,
)
from nexus.core.handlers.callback_command_handlers import (
    flow_close_handler as callback_flow_close_handler,
)
from nexus.core.handlers.callback_command_handlers import (
    inline_keyboard_handler as callback_inline_keyboard_handler,
)
from nexus.core.handlers.callback_command_handlers import (
    issue_picker_handler as callback_issue_picker_handler,
)
from nexus.core.handlers.callback_command_handlers import (
    menu_callback_handler as callback_menu_callback_handler,
)
from nexus.core.handlers.callback_command_handlers import (
    monitor_project_picker_handler as callback_monitor_project_picker_handler,
)
from nexus.core.handlers.callback_command_handlers import (
    project_picker_handler as callback_project_picker_handler,
)
from nexus.core.handlers.chat_command_handlers import (
    chat_agents_handler as core_chat_agents_handler,
    chat_callback_handler as core_chat_callback_handler,
    chat_menu_handler as core_chat_menu_handler,
)
from nexus.core.handlers.common_routing import extract_json_dict, route_task_with_context
from nexus.core.handlers.feature_ideation_handlers import (
    FeatureIdeationHandlerDeps,
    handle_feature_ideation_request,
    is_feature_ideation_request,
)
from nexus.core.handlers.feature_ideation_handlers import (
    feature_callback_handler as core_feature_callback_handler,
)
from nexus.core.handlers.feature_registry_command_handlers import (
    FeatureRegistryCommandDeps,
    feature_done_handler as core_feature_done_handler,
    feature_forget_handler as core_feature_forget_handler,
    feature_list_handler as core_feature_list_handler,
)
from nexus.core.handlers.hands_free_routing_handler import (
    HandsFreeRoutingDeps,
    resolve_pending_project_selection,
    route_hands_free_text,
)
from nexus.core.handlers.inbox_routing_handler import (
    PROJECTS,
    TYPES,
    process_inbox_task,
    save_resolved_task,
)
from nexus.core.handlers.issue_command_handlers import (
    IssueHandlerDeps,
)
from nexus.core.handlers.issue_command_handlers import (
    assign_handler as issue_assign_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    comments_handler as issue_comments_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    implement_handler as issue_implement_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    myissues_handler as issue_myissues_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    plan_handler as issue_plan_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    prepare_handler as issue_prepare_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    respond_handler as issue_respond_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    track_handler as issue_track_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    tracked_handler as issue_tracked_handler,
)
from nexus.core.handlers.issue_command_handlers import (
    untrack_handler as issue_untrack_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    MonitoringHandlersDeps,
)
from nexus.core.handlers.monitoring_command_handlers import (
    active_handler as monitoring_active_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    fuse_handler as monitoring_fuse_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    logs_handler as monitoring_logs_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    logsfull_handler as monitoring_logsfull_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    status_handler as monitoring_status_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    tail_handler as monitoring_tail_handler,
)
from nexus.core.handlers.monitoring_command_handlers import (
    tailstop_handler as monitoring_tailstop_handler,
)
from nexus.core.handlers.ops_command_handlers import (
    OpsHandlerDeps,
)
from nexus.core.handlers.ops_command_handlers import (
    agents_handler as ops_agents_handler,
)
from nexus.core.handlers.ops_command_handlers import (
    audit_handler as ops_audit_handler,
)
from nexus.core.handlers.ops_command_handlers import (
    direct_handler as ops_direct_handler,
)
from nexus.core.handlers.ops_command_handlers import (
    inboxq_handler as ops_inboxq_handler,
)
from nexus.core.handlers.ops_command_handlers import (
    stats_handler as ops_stats_handler,
)
from nexus.core.handlers.visualize_command_handlers import (
    VisualizeHandlerDeps,
)
from nexus.core.handlers.visualize_command_handlers import (
    visualize_handler as workflow_visualize_handler,
)
from nexus.core.handlers.watch_command_handlers import (
    WatchHandlerDeps,
)
from nexus.core.handlers.watch_command_handlers import (
    watch_handler as workflow_watch_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    WorkflowHandlerDeps,
)
from nexus.core.handlers.workflow_command_handlers import (
    continue_handler as workflow_continue_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    forget_handler as workflow_forget_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    kill_handler as workflow_kill_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    pause_handler as workflow_pause_picker_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    reconcile_handler as workflow_reconcile_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    reprocess_handler as workflow_reprocess_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    resume_handler as workflow_resume_picker_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    stop_handler as workflow_stop_picker_handler,
)
from nexus.core.handlers.workflow_command_handlers import (
    wfstate_handler as workflow_wfstate_handler,
)
from nexus.core.memory import (
    append_message,
    create_chat,
    get_active_chat,
    get_chat,
    get_chat_history,
    rename_chat,
)
from nexus.core.orchestration.ai_orchestrator import get_orchestrator
from nexus.core.orchestration.nexus_core_helpers import get_workflow_definition_path
from nexus.core.orchestration.plugin_runtime import (
    get_profiled_plugin,
    get_runtime_ops_plugin,
    get_workflow_state_plugin,
)
from nexus.core.orchestration.telegram.telegram_callback_router import (
    call_core_callback_handler as _router_call_core_callback_handler,
)
from nexus.core.orchestration.telegram.telegram_callback_router import (
    call_core_chat_handler as _router_call_core_chat_handler,
)
from nexus.core.orchestration.telegram.telegram_command_router import (
    dispatch_command as _router_dispatch_command,
)
from nexus.core.orchestration.telegram.telegram_update_bridge import (
    build_telegram_interactive_ctx as _bridge_build_telegram_interactive_ctx,
)
from nexus.core.orchestration.telegram.telegram_update_bridge import (
    buttons_to_reply_markup as _bridge_buttons_to_reply_markup,
)
from nexus.core.project.catalog import (
    get_single_project_key as _svc_get_single_project_key,
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
from nexus.core.report_scheduler import ReportScheduler
from nexus.core.runtime.bridge import find_task_file_by_issue
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
from nexus.core.telegram.telegram_bootstrap_ui_service import (
    build_menu_keyboard as _svc_build_menu_keyboard,
)
from nexus.core.telegram.telegram_bootstrap_ui_service import (
    check_tool_health as _svc_check_tool_health,
)
from nexus.core.telegram.telegram_bootstrap_ui_service import (
    handle_help as _svc_handle_help,
)
from nexus.core.telegram.telegram_bootstrap_ui_service import (
    handle_menu as _svc_handle_menu,
)
from nexus.core.telegram.telegram_bootstrap_ui_service import (
    handle_start as _svc_handle_start,
)
from nexus.core.telegram.telegram_bootstrap_ui_service import (
    on_startup as _svc_on_startup,
)
from nexus.core.telegram.telegram_chat_misc_service import (
    call_core_chat_wrapper as _svc_call_core_chat_wrapper,
)
from nexus.core.telegram.telegram_chat_misc_service import (
    handle_rename_chat as _svc_handle_rename_chat,
)
from nexus.core.telegram.telegram_command_runtime_service import (
    handle_progress_command as _svc_handle_progress_command,
)
from nexus.core.telegram.telegram_command_runtime_service import (
    rate_limited as _svc_rate_limited,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_audio_transcription_handler_deps as _svc_build_audio_transcription_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_callback_action_handlers as _svc_build_callback_action_handlers,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_callback_handler_deps as _svc_build_callback_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_feature_ideation_handler_deps as _svc_build_feature_ideation_handler_deps,
)
from nexus.core.telegram.telegram_handler_deps_service import (
    build_hands_free_routing_handler_deps as _svc_build_hands_free_routing_handler_deps,
)
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
from nexus.core.telegram.telegram_hands_free_service import handle_hands_free_message
from nexus.core.telegram.telegram_interactive_ctx_service import (
    ctx_call_telegram_handler as _svc_ctx_call_telegram_handler,
)
from nexus.core.telegram.telegram_interactive_ctx_service import (
    ctx_dispatch_command as _svc_ctx_dispatch_command,
)
from nexus.core.telegram.telegram_interactive_ctx_service import (
    ctx_ensure_project as _svc_ctx_ensure_project,
)
from nexus.core.telegram.telegram_interactive_ctx_service import (
    ctx_ensure_project_issue as _svc_ctx_ensure_project_issue,
)
from nexus.core.telegram.telegram_interactive_ctx_service import (
    ctx_prompt_issue_selection as _svc_ctx_prompt_issue_selection,
)
from nexus.core.telegram.telegram_interactive_ctx_service import (
    ctx_prompt_project_selection as _svc_ctx_prompt_project_selection,
)
from nexus.core.telegram.telegram_interactive_ctx_service import (
    ctx_telegram_runtime as _svc_ctx_telegram_runtime,
)
from nexus.core.telegram.telegram_issue_selection_service import (
    ensure_project_issue as _svc_ensure_project_issue,
)
from nexus.core.telegram.telegram_issue_selection_service import (
    handle_pending_issue_input as _svc_handle_pending_issue_input,
)
from nexus.core.telegram.telegram_issue_selection_service import (
    list_project_issues as _svc_list_project_issues,
)
from nexus.core.telegram.telegram_issue_selection_service import (
    parse_project_issue_args as _svc_parse_project_issue_args,
)
from nexus.core.telegram.telegram_main_bootstrap_service import (
    alerting_enabled as _svc_alerting_enabled,
)
from nexus.core.telegram.telegram_main_bootstrap_service import (
    allowed_updates_all_types as _svc_allowed_updates_all_types,
)
from nexus.core.telegram.telegram_main_bootstrap_service import (
    build_command_handler_map as _svc_build_command_handler_map,
)
from nexus.core.telegram.telegram_main_bootstrap_service import (
    build_post_init_with_scheduler as _svc_build_post_init_with_scheduler,
)
from nexus.core.telegram.telegram_main_bootstrap_service import (
    register_application_handlers as _svc_register_application_handlers,
)
from nexus.core.telegram.telegram_main_bootstrap_service import (
    reports_enabled as _svc_reports_enabled,
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
from nexus.core.telegram.telegram_selection_flow_service import (
    cancel_selection_flow as _svc_cancel_selection_flow,
)
from nexus.core.telegram.telegram_selection_flow_service import (
    project_selected_flow as _svc_project_selected_flow,
)
from nexus.core.telegram.telegram_selection_flow_service import (
    start_selection_flow as _svc_start_selection_flow,
)
from nexus.core.telegram.telegram_selection_flow_service import (
    type_selected_flow as _svc_type_selected_flow,
)
from nexus.core.telegram.telegram_task_capture_service import (
    handle_save_task_selection,
    handle_task_confirmation_callback,
)
from nexus.core.telegram.telegram_ui_prompts_service import (
    prompt_issue_selection as _svc_prompt_issue_selection,
)
from nexus.core.telegram.telegram_ui_prompts_service import (
    prompt_project_selection as _svc_prompt_project_selection,
)
from nexus.core.telegram.telegram_workflow_probe_service import (
    get_expected_running_agent_from_workflow as _svc_get_expected_running_agent_from_workflow,
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
from nexus.core.workflow_runtime.workflow_signal_sync import (
    extract_structured_completion_signals,
    read_latest_local_completion,
    write_local_completion_from_signal,
)
from nexus.core.workflow_runtime.workflow_watch_service import get_workflow_watch_service
from nexus.plugins.builtin.ai_runtime_plugin import AIProvider
from nexus.core.rate_limiter import RateLimit, get_rate_limiter

# --- LOGGING ---
logger = logging.getLogger(__name__)
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
    force=True,
    handlers=[logging.StreamHandler()],
)


install_secret_redaction([TELEGRAM_TOKEN or ""], logging.getLogger())

# Long-polling calls Telegram getUpdates repeatedly by design.
# Keep these transport logs at WARNING to avoid noisy INFO output.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

# Initialize AI Orchestrator (configured AI providers)
orchestrator = get_orchestrator(ORCHESTRATOR_CONFIG)

# Initialize rate limiter
rate_limiter = get_rate_limiter()

# Initialize user manager
user_manager = get_user_manager()


def _get_or_create_telegram_user(telegram_user: Any):
    return user_manager.get_or_create_user_by_identity(
        platform="telegram",
        platform_user_id=str(getattr(telegram_user, "id", "")),
        username=getattr(telegram_user, "username", None),
        first_name=getattr(telegram_user, "first_name", None),
    )


def check_permission_for_action(user_id: int, *, action: str = "execute") -> bool:
    action_value = str(action or "execute").strip().lower()
    if TELEGRAM_ALLOWED_USER_IDS and user_id not in TELEGRAM_ALLOWED_USER_IDS:
        return False
    if not NEXUS_AUTH_ENABLED:
        return True
    if action_value in {"readonly", "onboarding", "help"}:
        return True

    nexus_id = user_manager.resolve_nexus_id("telegram", str(user_id))
    if not nexus_id:
        return False
    try:
        setup = _svc_get_setup_status(str(nexus_id))
    except Exception:
        return False
    return bool(setup.get("ready"))


def _permission_denied_message(user_id: int, *, action: str = "execute") -> str:
    if TELEGRAM_ALLOWED_USER_IDS and user_id not in TELEGRAM_ALLOWED_USER_IDS:
        return "🔒 Unauthorized."
    if not NEXUS_AUTH_ENABLED:
        return "🔒 Unauthorized."
    action_value = str(action or "execute").strip().lower()
    if action_value in {"readonly", "onboarding", "help"}:
        return "🔒 Unauthorized."

    nexus_id = user_manager.resolve_nexus_id("telegram", str(user_id))
    if not nexus_id:
        return "🔐 Complete setup with `/login` before using task/workflow commands."
    try:
        setup = _svc_get_setup_status(str(nexus_id))
    except Exception:
        return "🔐 Auth storage is unavailable. Ask an admin to check auth configuration."

    missing: list[str] = []
    if not setup.get("git_provider_linked"):
        missing.append("Git provider login (GitHub or GitLab)")
    if not setup.get("ai_provider_ready"):
        missing.append(
            "AI provider credentials (Codex/OpenAI, Gemini, Claude, or GitHub for Copilot)"
        )
    if not setup.get("org_verified"):
        missing.append("allowed org/group membership")
    if int(setup.get("project_access_count") or 0) <= 0:
        missing.append("project team/group access")
    if missing:
        return "🔐 Setup incomplete: " + ", ".join(missing) + ". Run `/login` then `/setup_status`."
    return "🔒 Unauthorized."


def _requester_context_for_telegram_user(telegram_user: Any) -> dict[str, str]:
    user = _get_or_create_telegram_user(telegram_user)
    return {
        "nexus_id": str(user.nexus_id),
        "platform": "telegram",
        "platform_user_id": str(getattr(telegram_user, "id", "")),
    }


def _requester_context_for_telegram_user_id(user_id: int) -> dict[str, str]:
    resolved_nexus_id = user_manager.resolve_nexus_id("telegram", str(user_id))
    if not resolved_nexus_id:
        user = user_manager.get_or_create_user_by_identity(
            platform="telegram",
            platform_user_id=str(user_id),
            username=None,
            first_name=None,
        )
        resolved_nexus_id = str(user.nexus_id)
    return {
        "nexus_id": str(resolved_nexus_id),
        "platform": "telegram",
        "platform_user_id": str(user_id),
    }


def _authorize_project_for_requester(
    project_key: str,
    requester_context: dict[str, Any] | None,
) -> tuple[bool, str]:
    if not NEXUS_AUTH_ENABLED:
        return True, ""
    context = requester_context if isinstance(requester_context, dict) else {}
    nexus_id = str(context.get("nexus_id") or "").strip()
    if not nexus_id:
        return False, "🔐 Missing requester identity. Run `/login` and retry."
    return _svc_check_project_access(nexus_id, project_key)


def _authorize_update(*, update: Update, command: str | None = None, action: str = "execute"):
    effective_user = getattr(update, "effective_user", None)
    user_id = int(getattr(effective_user, "id", 0) or 0)
    if user_id <= 0:
        return False, "🔒 Unauthorized."
    allowed = check_permission_for_action(user_id, action=action)
    if allowed:
        return True, ""
    return False, _permission_denied_message(user_id, action=action)

feature_registry_service = FeatureRegistryService(
    enabled=NEXUS_FEATURE_REGISTRY_ENABLED,
    backend=NEXUS_STORAGE_BACKEND,
    state_dir=os.path.join(BASE_DIR, get_nexus_dir_name(), "state"),
    postgres_dsn=NEXUS_STORAGE_DSN,
    max_items_per_project=NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT,
    dedup_similarity=NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
)

DEFAULT_REPO = get_default_repo()
from nexus.core.integrations.workflow_state_factory import get_workflow_state as _get_wf_state

_WORKFLOW_STATE_PLUGIN_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "storage_type": "postgres" if NEXUS_WORKFLOW_BACKEND == "postgres" else "file",
    "storage_config": (
        {"connection_string": NEXUS_STORAGE_DSN}
        if NEXUS_WORKFLOW_BACKEND == "postgres" and NEXUS_STORAGE_DSN
        else {}
    ),
    "issue_to_workflow_id": lambda n: _get_wf_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: _get_wf_state().map_issue(n, w),
    "workflow_definition_path_resolver": get_workflow_definition_path,
    "clear_pending_approval": lambda n: _get_wf_state().clear_pending_approval(n),
    "audit_log": AuditStore.audit_log,
}


def _workflow_handler_deps() -> WorkflowHandlerDeps:
    return _svc_build_workflow_handler_deps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        project_config=PROJECT_CONFIG,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
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


def _visualize_handler_deps() -> VisualizeHandlerDeps:
    return _svc_build_visualize_handler_deps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
    )


def _fetch_workflow_snapshot(issue_num: str, project_key: str) -> dict[str, Any]:
    """Helper for /watch to fetch current state without passing all deps manually."""
    repo = _project_repo(project_key)
    expected_running = _normalize_agent_reference(
        _get_expected_running_agent_from_workflow(issue_num) or ""
    )
    return build_workflow_snapshot(
        issue_num=issue_num,
        repo=repo,
        get_issue_plugin=_get_direct_issue_plugin,
        expected_running_agent=expected_running,
        find_task_file_by_issue=find_task_file_by_issue,
        read_latest_local_completion=_read_latest_local_completion,
        extract_structured_completion_signals=_extract_structured_completion_signals,
    )


def _watch_handler_deps() -> WatchHandlerDeps:
    return _svc_build_watch_handler_deps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
        get_watch_service=get_workflow_watch_service,
    )


def _monitoring_handler_deps() -> MonitoringHandlersDeps:
    from nexus.core.runtime.nexus_agent_runtime import get_retry_fuse_status

    return _svc_build_monitoring_handler_deps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
        types_map=TYPES,
        ensure_project=_ctx_ensure_project,
        ensure_project_issue=_ctx_ensure_project_issue,
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
    return _svc_build_issue_handler_deps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        default_repo=DEFAULT_REPO,
        project_config=PROJECT_CONFIG,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
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


def _get_inbox_queue_overview(limit: int) -> dict[str, Any]:
    from nexus.core.integrations.inbox_queue import get_queue_overview

    return get_queue_overview(limit=limit)


def _enqueue_inbox_task(
    *, project_key: str, workspace: str, filename: str, markdown_content: str
) -> int:
    from nexus.core.integrations.inbox_queue import enqueue_task

    return enqueue_task(
        project_key=project_key,
        workspace=workspace,
        filename=filename,
        markdown_content=markdown_content,
    )


def _ops_handler_deps() -> OpsHandlerDeps:
    return _svc_build_ops_handler_deps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        base_dir=BASE_DIR,
        nexus_dir_name=get_nexus_dir_name(),
        project_config=PROJECT_CONFIG,
        prompt_project_selection=_ctx_prompt_project_selection,
        ensure_project_issue=_ctx_ensure_project_issue,
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
        requester_context_builder=_requester_context_for_telegram_user_id,
    )


def _callback_handler_deps() -> CallbackHandlerDeps:
    return _svc_build_callback_handler_deps(
        logger=logger,
        prompt_issue_selection=_ctx_prompt_issue_selection,
        dispatch_command=_ctx_dispatch_command,
        get_project_label=_get_project_label,
        get_repo=get_repo,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        get_workflow_state_plugin=get_workflow_state_plugin,
        workflow_state_plugin_kwargs=_WORKFLOW_STATE_PLUGIN_KWARGS,
        action_handlers=_svc_build_callback_action_handlers(
            ctx_call_telegram_handler=_ctx_call_telegram_handler,
            logs_handler=logs_handler,
            logsfull_handler=logsfull_handler,
            status_handler=status_handler,
            pause_handler=pause_handler,
            resume_handler=resume_handler,
            stop_handler=stop_handler,
            audit_handler=audit_handler,
            active_handler=active_handler,
            reprocess_handler=reprocess_handler,
            plan_handler=plan_handler,
        ),
        report_bug_action=_report_bug_action_wrapper,
    )


def _feature_ideation_handler_deps() -> FeatureIdeationHandlerDeps:
    return _svc_build_feature_ideation_handler_deps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        projects=PROJECTS,
        get_project_label=_get_project_label,
        orchestrator=orchestrator,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
        process_inbox_task=process_inbox_task,
        requester_context_builder=_requester_context_for_telegram_user_id,
        authorize_project=_authorize_project_for_requester,
        feature_registry_service=feature_registry_service,
        dedup_similarity=NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY,
    )


def _feature_registry_command_deps() -> FeatureRegistryCommandDeps:
    return FeatureRegistryCommandDeps(
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        iter_project_keys=_iter_project_keys,
        normalize_project_key=_normalize_project_key,
        get_project_label=_get_project_label,
        feature_registry=feature_registry_service,
    )


def _audio_transcription_handler_deps() -> AudioTranscriptionDeps:
    return _svc_build_audio_transcription_handler_deps(
        logger=logger,
        transcribe_audio=orchestrator.transcribe_audio,
    )


def _hands_free_routing_handler_deps() -> HandsFreeRoutingDeps:
    return _svc_build_hands_free_routing_handler_deps(
        logger=logger,
        orchestrator=orchestrator,
        ai_persona=AI_PERSONA,
        projects=PROJECTS,
        extract_json_dict=extract_json_dict,
        get_chat_history=get_chat_history,
        append_message=append_message,
        get_chat=get_chat,
        process_inbox_task=process_inbox_task,
        feature_ideation_deps=_feature_ideation_handler_deps(),
        normalize_project_key=_normalize_project_key,
        save_resolved_task=save_resolved_task,
        task_confirmation_mode=TASK_CONFIRMATION_MODE,
        requester_context_builder=_requester_context_for_telegram_user_id,
        authorize_project=_authorize_project_for_requester,
        base_dir=BASE_DIR,
        project_config=PROJECT_CONFIG,
    )


def _get_direct_issue_plugin(repo: str):
    """Return issue plugin for direct Telegram operations."""
    return _svc_get_direct_issue_plugin(repo=repo, get_profiled_plugin=get_profiled_plugin)


# --- RATE LIMITING DECORATOR ---
def rate_limited(action: str, limit: RateLimit = None):
    return _svc_rate_limited(rate_limiter=rate_limiter, logger=logger, action=action, limit=limit)


def load_tracked_issues():
    """Load tracked issues from file."""
    return HostStateManager.load_tracked_issues()


def save_tracked_issues(data):
    """Save tracked issues to file."""
    HostStateManager.save_tracked_issues(data)


# Moved `_refine_task_description` to inbox_routing_handler.py


def get_issue_details(issue_num, repo: str = None):
    """Query Git API for issue details."""
    return _svc_get_issue_details(
        issue_num=str(issue_num),
        repo=repo,
        default_repo=DEFAULT_REPO,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logger,
    )


def _get_expected_running_agent_from_workflow(issue_num: str) -> str | None:
    """Return the current RUNNING workflow agent for an issue, if available."""
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_KWARGS,
        cache_key="workflow:state-engine:expected-agent:telegram",
    )
    return _svc_get_expected_running_agent_from_workflow(
        issue_num=str(issue_num),
        get_workflow_id=lambda n: _get_wf_state().get_workflow_id(n),
        workflow_plugin=workflow_plugin,
    )


def _extract_structured_completion_signals(comments: list[dict]) -> list[dict[str, str]]:
    return extract_structured_completion_signals(comments)


def _write_local_completion_from_signal(
    project_key: str, issue_num: str, signal: dict[str, str]
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


def _read_latest_local_completion(issue_num: str) -> dict[str, Any] | None:
    return read_latest_local_completion(BASE_DIR, get_nexus_dir_name(), issue_num)


def _resolve_project_root_from_task_path(task_file: str) -> str:
    return _svc_resolve_project_root_from_task_path(task_file)


def find_task_logs(task_file):
    return _svc_find_task_logs(
        task_file=task_file,
        logger=logger,
        resolve_project_root_from_task_path_fn=_resolve_project_root_from_task_path,
        extract_project_from_nexus_path=_extract_project_from_nexus_path,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )


def read_log_matches(log_path, issue_num, issue_url=None, max_lines=20):
    return _svc_read_log_matches(
        log_path=str(log_path) if log_path is not None else "",
        issue_num=str(issue_num),
        issue_url=issue_url,
        max_lines=max_lines,
        logger=logger,
    )


def search_logs_for_issue(issue_num):
    return _svc_search_logs_for_issue(
        issue_num=str(issue_num),
        telegram_bot_log_file=TELEGRAM_BOT_LOG_FILE,
        logs_dir=LOGS_DIR,
        logger=logger,
        read_log_matches_fn=read_log_matches,
    )


def read_latest_log_tail(task_file, max_lines=20):
    return _svc_read_latest_log_tail(
        task_file=task_file,
        max_lines=max_lines,
        logger=logger,
        find_task_logs_fn=find_task_logs,
    )


def find_issue_log_files(issue_num, task_file=None):
    return _svc_find_issue_log_files(
        issue_num=str(issue_num),
        task_file=task_file,
        base_dir=BASE_DIR,
        nexus_dir_name=get_nexus_dir_name(),
        extract_project_from_nexus_path=_extract_project_from_nexus_path,
        resolve_project_root_from_task_path_fn=_resolve_project_root_from_task_path,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )


def read_latest_log_full(task_file):
    return _svc_read_latest_log_full(
        task_file=task_file,
        logger=logger,
        find_task_logs_fn=find_task_logs,
    )


def resolve_project_config_from_task(task_file):
    return _svc_resolve_project_config_from_task(
        task_file=task_file,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
    )


def _iter_project_keys() -> list[str]:
    return _svc_iter_project_keys(project_config=PROJECT_CONFIG)


def _get_single_project_key() -> str | None:
    return _svc_get_single_project_key(project_config=PROJECT_CONFIG)


def _get_project_label(project_key: str) -> str:
    return PROJECTS.get(project_key, project_key)


def _get_project_root(project_key: str) -> str | None:
    return _svc_get_project_root(
        project_key=project_key,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
    )


def _get_project_logs_dir(project_key: str) -> str | None:
    return _svc_get_project_logs_dir(
        project_key=project_key,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        get_tasks_logs_dir=get_tasks_logs_dir,
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


def _extract_project_from_nexus_path(path: str) -> str | None:
    return _svc_extract_project_from_nexus_path(
        path=path,
        normalize_project_key=_normalize_project_key,
        iter_project_keys_fn=_iter_project_keys,
    )


async def _prompt_monitor_project_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
) -> None:
    keyboard = [[InlineKeyboardButton("All Projects", callback_data=f"pickmonitor:{command}:all")]]
    keyboard.extend(
        [
            InlineKeyboardButton(
                _get_project_label(key), callback_data=f"pickmonitor:{command}:{key}"
            )
        ]
        for key in _iter_project_keys()
    )
    keyboard.append([InlineKeyboardButton("❌ Close", callback_data="flow:close")])

    text = f"Select a project for /{command}:"
    if update.callback_query:
        await update.callback_query.edit_message_text(
            text, reply_markup=InlineKeyboardMarkup(keyboard)
        )
    else:
        await update.effective_message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))


def _list_project_issues(project_key: str, state: str = "open", limit: int = 10) -> list[dict]:
    return _svc_list_project_issues(
        project_key=project_key,
        project_config=PROJECT_CONFIG,
        get_repos=get_repos,
        get_direct_issue_plugin=_get_direct_issue_plugin,
        logger=logger,
        state=state,
        limit=limit,
    )


async def _prompt_issue_selection(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    project_key: str,
    *,
    edit_message: bool = False,
    issue_state: str = "open",
) -> None:
    await _svc_prompt_issue_selection(
        update=update,
        command=command,
        project_key=project_key,
        list_project_issues=_list_project_issues,
        get_project_label=_get_project_label,
        inline_keyboard_button_cls=InlineKeyboardButton,
        inline_keyboard_markup_cls=InlineKeyboardMarkup,
        edit_message=edit_message,
        issue_state=issue_state,
    )


async def _prompt_project_selection(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> None:
    await _svc_prompt_project_selection(
        update=update,
        context=context,
        command=command,
        get_single_project_key=_get_single_project_key,
        dispatch_command=lambda u, c, cmd, proj, issue: _dispatch_command(u, c, cmd, proj, issue),
        prompt_issue_selection=lambda u, c, cmd, proj: _prompt_issue_selection(u, c, cmd, proj),
        iter_project_keys=_iter_project_keys,
        get_project_label=_get_project_label,
        inline_keyboard_button_cls=InlineKeyboardButton,
        inline_keyboard_markup_cls=InlineKeyboardMarkup,
    )


def _parse_project_issue_args(args: list[str]) -> tuple[str | None, str | None, list[str]]:
    return _svc_parse_project_issue_args(args=args, normalize_project_key=_normalize_project_key)


async def _ensure_project_issue(
    update: Update, context: ContextTypes.DEFAULT_TYPE, command: str
) -> tuple[str | None, str | None, list[str]]:
    project_key, issue_num, rest = await _svc_ensure_project_issue(
        update=update,
        context=context,
        command=command,
        iter_project_keys=_iter_project_keys,
        normalize_project_key=_normalize_project_key,
        parse_project_issue_args_fn=_parse_project_issue_args,
        prompt_project_selection=_prompt_project_selection,
        prompt_issue_selection=_prompt_issue_selection,
    )
    if project_key and NEXUS_AUTH_ENABLED:
        requester_context = _requester_context_for_telegram_user(update.effective_user)
        allowed, error_message = _authorize_project_for_requester(project_key, requester_context)
        if not allowed:
            await update.effective_message.reply_text(error_message or "🔒 Unauthorized project access.")
            return None, None, []
    return project_key, issue_num, rest


async def _handle_pending_issue_input(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    return await _svc_handle_pending_issue_input(
        update=update,
        context=context,
        is_feature_ideation_request=is_feature_ideation_request,
        dispatch_command=_dispatch_command,
    )


def _command_handler_map():
    return _svc_build_command_handler_map(
        status_handler=status_handler,
        active_handler=active_handler,
        inboxq_handler=inboxq_handler,
        stats_handler=stats_handler,
        logs_handler=logs_handler,
        logsfull_handler=logsfull_handler,
        tail_handler=tail_handler,
        fuse_handler=fuse_handler,
        audit_handler=audit_handler,
        comments_handler=comments_handler,
        wfstate_handler=wfstate_handler,
        visualize_handler=visualize_handler,
        watch_handler=watch_handler,
        reprocess_handler=reprocess_handler,
        reconcile_handler=reconcile_handler,
        continue_handler=continue_handler,
        forget_handler=forget_handler,
        respond_handler=respond_handler,
        kill_handler=kill_handler,
        assign_handler=assign_handler,
        implement_handler=implement_handler,
        prepare_handler=prepare_handler,
        plan_handler=plan_handler,
        pause_handler=pause_handler,
        resume_handler=resume_handler,
        stop_handler=stop_handler,
        track_handler=track_handler,
        tracked_handler=tracked_handler,
        untrack_handler=untrack_handler,
        agents_handler=agents_handler,
        feature_done_handler=feature_done_handler,
        feature_list_handler=feature_list_handler,
        feature_forget_handler=feature_forget_handler,
    )


def _buttons_to_reply_markup(buttons):
    return _bridge_buttons_to_reply_markup(buttons, InlineKeyboardButton, InlineKeyboardMarkup)


def _build_telegram_interactive_ctx(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return _bridge_build_telegram_interactive_ctx(
        update,
        context,
        buttons_to_reply_markup_fn=_buttons_to_reply_markup,
    )


def _ctx_telegram_runtime(ctx) -> tuple[Update, ContextTypes.DEFAULT_TYPE]:
    return _svc_ctx_telegram_runtime(ctx)


async def _ctx_call_telegram_handler(ctx, handler) -> None:
    await _svc_ctx_call_telegram_handler(
        ctx=ctx,
        handler=handler,
        ctx_telegram_runtime=_ctx_telegram_runtime,
    )


async def _ctx_prompt_issue_selection(
    ctx,
    command: str,
    project_key: str,
    *,
    edit_message: bool = False,
    issue_state: str = "open",
) -> None:
    await _svc_ctx_prompt_issue_selection(
        ctx=ctx,
        command=command,
        project_key=project_key,
        prompt_issue_selection=_prompt_issue_selection,
        ctx_telegram_runtime=_ctx_telegram_runtime,
        edit_message=edit_message,
        issue_state=issue_state,
    )


async def _ctx_prompt_project_selection(ctx, command: str) -> None:
    await _svc_ctx_prompt_project_selection(
        ctx=ctx,
        command=command,
        prompt_project_selection=_prompt_project_selection,
        ctx_telegram_runtime=_ctx_telegram_runtime,
    )


async def _ctx_ensure_project_issue(
    ctx,
    command: str,
) -> tuple[str | None, str | None, list[str]]:
    return await _svc_ctx_ensure_project_issue(
        ctx=ctx,
        command=command,
        ensure_project_issue=_ensure_project_issue,
        ctx_telegram_runtime=_ctx_telegram_runtime,
    )


async def _ctx_ensure_project(ctx, command: str) -> str | None:
    project_key = await _svc_ctx_ensure_project(
        ctx=ctx,
        command=command,
        get_single_project_key=_get_single_project_key,
        normalize_project_key=_normalize_project_key,
        iter_project_keys=_iter_project_keys,
        ctx_prompt_project_selection=_ctx_prompt_project_selection,
    )
    if project_key and NEXUS_AUTH_ENABLED:
        requester_context = _requester_context_for_telegram_user_id(int(str(ctx.user_id)))
        allowed, error_message = _authorize_project_for_requester(project_key, requester_context)
        if not allowed:
            await ctx.reply_text(error_message or "🔒 Unauthorized project access.")
            return None
    return project_key


async def _ctx_dispatch_command(
    ctx,
    command: str,
    project_key: str,
    issue_num: str,
    rest: list[str] | None = None,
) -> None:
    await _svc_ctx_dispatch_command(
        ctx=ctx,
        command=command,
        project_key=project_key,
        issue_num=issue_num,
        dispatch_command=_dispatch_command,
        ctx_telegram_runtime=_ctx_telegram_runtime,
        rest=rest,
    )


async def _call_core_callback_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE, handler
) -> None:
    await _router_call_core_callback_handler(
        update,
        context,
        handler,
        build_ctx=_build_telegram_interactive_ctx,
        deps_factory=_callback_handler_deps,
    )


async def _call_core_chat_handler(
    update: Update, context: ContextTypes.DEFAULT_TYPE, handler
) -> None:
    await _router_call_core_chat_handler(
        update,
        context,
        handler,
        build_ctx=_build_telegram_interactive_ctx,
    )


async def _dispatch_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    command: str,
    project_key: str,
    issue_num: str,
    rest: list[str] | None = None,
) -> None:
    async def _reply_unsupported(_update):
        await _update.effective_message.reply_text("Unsupported command.")

    await _router_dispatch_command(
        update=update,
        context=context,
        command=command,
        project_key=project_key,
        issue_num=issue_num,
        rest=rest,
        command_handler_map=_command_handler_map,
        reply_unsupported=_reply_unsupported,
    )


async def project_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_project_picker_handler)


async def issue_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_issue_picker_handler)


async def monitor_project_picker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_monitor_project_picker_handler)


async def close_flow_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_close_flow_handler)


# --- STATES ---
SELECT_PROJECT, SELECT_TYPE, INPUT_TASK = range(3)

tracked_issues = load_tracked_issues()  # Load on startup
active_tail_sessions: dict[tuple[int, int], str] = {}
active_tail_tasks: dict[tuple[int, int], asyncio.Task] = {}
TASK_CONFIRMATION_MODE = os.getenv("TASK_CONFIRMATION_MODE", "smart").strip().lower()
_watch_sender_bot: Bot | None = None


# --- 0. HELP & INFO ---
async def rename_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _svc_handle_rename_chat(
        update=update,
        context=context,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        get_active_chat=get_active_chat,
        rename_chat=rename_chat,
    )


async def chat_menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _svc_call_core_chat_wrapper(
        update=update,
        context=context,
        call_core_chat_handler=_call_core_chat_handler,
        handler=core_chat_menu_handler,
    )


async def chat_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _svc_call_core_chat_wrapper(
        update=update,
        context=context,
        call_core_chat_handler=_call_core_chat_handler,
        handler=core_chat_callback_handler,
    )


async def chat_agents_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _svc_call_core_chat_wrapper(
        update=update,
        context=context,
        call_core_chat_handler=_call_core_chat_handler,
        handler=core_chat_agents_handler,
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _svc_handle_help(
        update=update,
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
    )


async def login_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not NEXUS_AUTH_ENABLED:
        await update.effective_message.reply_text("ℹ️ Auth onboarding is disabled in this environment.")
        return
    if not NEXUS_PUBLIC_BASE_URL:
        await update.effective_message.reply_text(
            "⚠️ NEXUS_PUBLIC_BASE_URL is not configured. Ask an admin to configure auth."
        )
        return

    requested_provider = str((context.args[0] if context.args else "") or "").strip().lower()
    if requested_provider == "github" and not NEXUS_GITHUB_CLIENT_ID:
        await update.effective_message.reply_text(
            "⚠️ GitHub OAuth is not configured. Use `/login gitlab`.",
            parse_mode="Markdown",
        )
        return
    if requested_provider == "gitlab" and not NEXUS_GITLAB_CLIENT_ID:
        await update.effective_message.reply_text(
            "⚠️ GitLab OAuth is not configured. Use `/login github`.",
            parse_mode="Markdown",
        )
        return

    user = _get_or_create_telegram_user(update.effective_user)
    session_id = create_login_session_for_user(
        nexus_id=str(user.nexus_id),
        discord_user_id=str(update.effective_user.id),
        discord_username=getattr(update.effective_user, "username", None),
    )
    session_ref = _svc_format_login_session_ref(session_id) or session_id
    available_providers: list[str] = []
    if NEXUS_GITHUB_CLIENT_ID:
        available_providers.append("github")
    if NEXUS_GITLAB_CLIENT_ID:
        available_providers.append("gitlab")
    if not available_providers:
        await update.effective_message.reply_text(
            "⚠️ No OAuth providers are configured. Ask an admin to configure GitHub/GitLab OAuth.",
        )
        return

    if not requested_provider:
        keyboard = [
            [
                InlineKeyboardButton(
                    f"Continue with {provider.title()}",
                    url=f"{NEXUS_PUBLIC_BASE_URL}/auth/start?session={session_ref}&provider={provider}",
                )
            ]
            for provider in available_providers
        ]
        sent = await update.effective_message.reply_text(
            (
                "🔐 Setup required before task execution.\n\n"
                f"Session reference: {session_ref}\n"
                "Choose your Git provider to continue OAuth onboarding."
            ),
            reply_markup=InlineKeyboardMarkup(keyboard),
            disable_web_page_preview=True,
        )
        try:
            _svc_register_onboarding_message(
                session_id=session_id,
                chat_platform="telegram",
                chat_id=str(getattr(sent, "chat_id", "") or getattr(update.effective_chat, "id", "")),
                message_id=str(getattr(sent, "message_id", "")),
            )
        except Exception as exc:
            logger.warning("Failed to register Telegram onboarding message for session %s: %s", session_id, exc)
        return

    if requested_provider not in {"github", "gitlab"}:
        await update.effective_message.reply_text(
            "⚠️ Invalid provider. Use `/login github` or `/login gitlab`, or run `/login` to pick from menu.",
            parse_mode="Markdown",
        )
        return

    if requested_provider not in available_providers:
        await update.effective_message.reply_text(
            f"⚠️ {requested_provider.title()} OAuth is not configured in this environment.",
        )
        return

    login_url = (
        f"{NEXUS_PUBLIC_BASE_URL}/auth/start?session={session_ref}&provider={requested_provider}"
    )
    sent = await update.effective_message.reply_text(
        (
            "🔐 Setup required before task execution.\n\n"
            f"Session reference: {session_ref}\n"
            f"1. Open: {login_url}\n"
            f"2. Sign in with {requested_provider.title()}\n"
            "3. Add Codex/OpenAI, Gemini, and/or Claude key, or use Copilot with linked GitHub OAuth\n"
            "4. Run `/setup_status`"
        ),
        disable_web_page_preview=True,
    )
    try:
        _svc_register_onboarding_message(
            session_id=session_id,
            chat_platform="telegram",
            chat_id=str(getattr(sent, "chat_id", "") or getattr(update.effective_chat, "id", "")),
            message_id=str(getattr(sent, "message_id", "")),
        )
    except Exception as exc:
        logger.warning("Failed to register Telegram onboarding message for session %s: %s", session_id, exc)


async def setup_status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_or_create_telegram_user(update.effective_user)
    status = _svc_get_setup_status(str(user.nexus_id))
    latest_login = _svc_get_latest_login_session_status(str(user.nexus_id))
    if not status.get("auth_enabled"):
        await update.effective_message.reply_text("ℹ️ Auth onboarding is disabled in this environment.")
        return

    projects = status.get("projects") or []
    projects_line = ", ".join(projects) if projects else "(none)"
    lines = [
        "🧾 Setup Status",
        f"- Nexus ID: `{user.nexus_id}`",
        f"- GitHub linked: {'✅' if status.get('github_linked') else '❌'}",
        f"- GitLab linked: {'✅' if status.get('gitlab_linked') else '❌'}",
        f"- GitHub login: `{status.get('github_login') or 'n/a'}`",
        f"- GitLab username: `{status.get('gitlab_username') or 'n/a'}`",
        f"- Codex key set: {'✅' if status.get('codex_key_set') else '❌'}",
        f"- Gemini key set: {'✅' if status.get('gemini_key_set') else '❌'}",
        f"- Claude key set: {'✅' if status.get('claude_key_set') else '❌'}",
        f"- Copilot ready (GitHub OAuth or Copilot Token): {'✅' if status.get('copilot_ready') else '❌'}",
        f"- Org/group verified: {'✅' if status.get('org_verified') else '❌'}",
        f"- Project access: `{int(status.get('project_access_count') or 0)}`",
        f"- Projects: {projects_line}",
        f"- Ready: {'✅' if status.get('ready') else '❌'}",
    ]
    if latest_login.get("exists"):
        latest_ref = str(latest_login.get("session_ref") or latest_login.get("session_id") or "").strip()
        latest_state = str(latest_login.get("status") or "unknown").strip()
        if latest_ref:
            lines.append(f"- Last login session: `{latest_ref}` ({latest_state})")
    if not status.get("ready"):
        lines.append("")
        lines.append("Run `/login` to complete any missing steps.")
    await update.effective_message.reply_text("\n".join(lines))


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = _get_or_create_telegram_user(update.effective_user)
    setup = _svc_get_setup_status(str(user.nexus_id)) if NEXUS_AUTH_ENABLED else {}
    identities = ", ".join(
        f"{platform}:{value}" for platform, value in sorted((user.identities or {}).items())
    )
    lines = [
        "👤 Identity",
        f"- Nexus ID: `{user.nexus_id}`",
        f"- Telegram ID: `{update.effective_user.id}`",
        f"- Username: `{getattr(update.effective_user, 'username', None) or 'n/a'}`",
        f"- Linked identities: {identities or '(none)'}",
    ]
    if NEXUS_AUTH_ENABLED:
        lines.append(f"- GitHub login: `{setup.get('github_login') or 'n/a'}`")
        lines.append(f"- GitLab username: `{setup.get('gitlab_username') or 'n/a'}`")
        lines.append(f"- Ready: {'✅' if setup.get('ready') else '❌'}")
    await update.effective_message.reply_text("\n".join(lines))


def build_menu_keyboard(button_rows, include_back=True):
    return _svc_build_menu_keyboard(
        button_rows=button_rows,
        include_back=include_back,
        inline_keyboard_button_cls=InlineKeyboardButton,
        inline_keyboard_markup_cls=InlineKeyboardMarkup,
    )


async def menu_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _svc_handle_menu(
        update=update,
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        inline_keyboard_button_cls=InlineKeyboardButton,
        inline_keyboard_markup_cls=InlineKeyboardMarkup,
    )


async def menu_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _call_core_callback_handler(update, context, callback_menu_callback_handler)


async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await _svc_handle_start(
        update=update,
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        reply_keyboard_markup_cls=ReplyKeyboardMarkup,
    )


async def on_startup(application):
    global _watch_sender_bot
    await _svc_on_startup(
        application=application,
        logger=logger,
        validate_required_command_interface=validate_required_command_interface,
        validate_command_parity=validate_command_parity,
        bot_command_cls=BotCommand,
        check_tool_health_fn=_check_tool_health,
    )
    _watch_sender_bot = application.bot
    watch_service = get_workflow_watch_service()
    watch_service.bind_runtime(loop=asyncio.get_running_loop(), sender=_send_watch_message)
    watch_service.bind_snapshot_fetcher(fetcher=_fetch_workflow_snapshot)
    watch_service.ensure_started()


async def _check_tool_health(application):
    await _svc_check_tool_health(
        application=application,
        orchestrator=orchestrator,
        ai_providers=list(AIProvider),
        logger=logger,
        telegram_chat_id=TELEGRAM_CHAT_ID,
    )


async def _send_watch_message(chat_id: int, text: str) -> None:
    if _watch_sender_bot is None:
        return
    await _watch_sender_bot.send_message(chat_id=chat_id, text=text)


async def feature_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await core_feature_callback_handler(
        _build_telegram_interactive_ctx(update, context),
        _feature_ideation_handler_deps(),
    )


async def task_confirmation_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_task_confirmation_callback(
        update=update,
        context=context,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        logger=logger,
        route_task_with_context=route_task_with_context,
        orchestrator=orchestrator,
        get_chat=get_chat,
        process_inbox_task=process_inbox_task,
        requester_context_builder=_requester_context_for_telegram_user,
        authorize_project=_authorize_project_for_requester,
    )


async def _transcribe_voice_message(
    voice_file_id: str, context: ContextTypes.DEFAULT_TYPE
) -> str | None:
    return await transcribe_telegram_voice(
        voice_file_id, context, _audio_transcription_handler_deps()
    )


# --- 1. HANDS-FREE MODE (Auto-Router) ---
async def hands_free_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await handle_hands_free_message(
        update=update,
        context=context,
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        get_active_chat=get_active_chat,
        rename_chat=rename_chat,
        chat_menu_handler=chat_menu_handler,
        handle_pending_issue_input=_handle_pending_issue_input,
        transcribe_voice_message=_transcribe_voice_message,
        inline_keyboard_button_cls=InlineKeyboardButton,
        inline_keyboard_markup_cls=InlineKeyboardMarkup,
        resolve_pending_project_selection=resolve_pending_project_selection,
        build_ctx=_build_telegram_interactive_ctx,
        hands_free_routing_deps_factory=_hands_free_routing_handler_deps,
        get_chat=get_chat,
        handle_feature_ideation_request=handle_feature_ideation_request,
        feature_ideation_deps_factory=_feature_ideation_handler_deps,
        route_hands_free_text=route_hands_free_text,
    )


# --- 2. SELECTION MODE (Menu) ---
# (Steps 1 & 2 are purely Telegram UI, no AI needed)


async def start_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(getattr(update.effective_user, "id", 0) or 0)
    if not check_permission_for_action(user_id, action="execute"):
        await update.effective_message.reply_text(
            _permission_denied_message(user_id, action="execute")
        )
        return ConversationHandler.END
    return await _svc_start_selection_flow(
        update=update,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        projects=PROJECTS,
        inline_keyboard_button_cls=InlineKeyboardButton,
        inline_keyboard_markup_cls=InlineKeyboardMarkup,
        select_project_state=SELECT_PROJECT,
    )


async def project_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(getattr(update.effective_user, "id", 0) or 0)
    if not check_permission_for_action(user_id, action="execute"):
        await update.effective_message.reply_text(
            _permission_denied_message(user_id, action="execute")
        )
        return ConversationHandler.END
    return await _svc_project_selected_flow(
        update=update,
        context=context,
        projects=PROJECTS,
        types_map=TYPES,
        inline_keyboard_button_cls=InlineKeyboardButton,
        inline_keyboard_markup_cls=InlineKeyboardMarkup,
        select_type_state=SELECT_TYPE,
    )


async def type_selected(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(getattr(update.effective_user, "id", 0) or 0)
    if not check_permission_for_action(user_id, action="execute"):
        await update.effective_message.reply_text(
            _permission_denied_message(user_id, action="execute")
        )
        return ConversationHandler.END
    return await _svc_type_selected_flow(
        update=update,
        context=context,
        input_task_state=INPUT_TASK,
    )


# --- 3. SAVING THE TASK (Uses Gemini only if Voice) ---
async def save_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = int(getattr(update.effective_user, "id", 0) or 0)
    if not check_permission_for_action(user_id, action="execute"):
        await update.effective_message.reply_text(
            _permission_denied_message(user_id, action="execute")
        )
        return ConversationHandler.END
    return await handle_save_task_selection(
        update=update,
        context=context,
        logger=logger,
        orchestrator=orchestrator,
        projects=PROJECTS,
        types_map=TYPES,
        project_config=PROJECT_CONFIG,
        base_dir=BASE_DIR,
        get_inbox_storage_backend=get_inbox_storage_backend,
        enqueue_task=_enqueue_inbox_task,
        get_inbox_dir=get_inbox_dir,
        transcribe_voice_message=_transcribe_voice_message,
        conversation_end=ConversationHandler.END,
        requester_context_builder=_requester_context_for_telegram_user,
        authorize_project=_authorize_project_for_requester,
    )


async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    return await _svc_cancel_selection_flow(
        update=update,
        conversation_end=ConversationHandler.END,
    )


async def flow_close_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Close button for the /new flow."""
    return await _call_core_callback_handler(update, context, callback_flow_close_handler)


# --- MONITORING COMMANDS ---
def extract_issue_number_from_file(file_path):
    return _svc_extract_issue_number_from_file(file_path=file_path, logger=logger)


async def status_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows pending tasks in inbox folders."""
    await monitoring_status_handler(
        _build_telegram_interactive_ctx(update, context),
        _monitoring_handler_deps(),
    )


async def progress_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active issues with current workflow step, agent type, tool, and duration."""
    await _svc_handle_progress_command(
        update=update,
        logger=logger,
        allowed_user_ids=TELEGRAM_ALLOWED_USER_IDS,
        load_launched_agents=HostStateManager.load_launched_agents,
        time_module=time,
    )


async def active_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Shows active tasks being worked on."""
    await monitoring_active_handler(
        _build_telegram_interactive_ctx(update, context),
        _monitoring_handler_deps(),
    )


async def assign_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Assigns a Git issue to the user."""
    await issue_assign_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


@rate_limited("implement")
async def implement_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Requests AI agent implementation for an issue (approval workflow).

    Adds an `agent:requested` label and leaves a comment on the Git platform
    so it can be approved (add `agent:approved`) or triggered via agent mode.
    """
    await issue_implement_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def prepare_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Augments an issue with AI-friendly instructions and acceptance criteria."""
    await issue_prepare_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


@rate_limited("plan")
async def plan_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Requests AI agent to formulate a plan for an issue.

    Adds an `agent:plan-requested` label.
    """
    await issue_plan_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def track_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Subscribe to issue updates and track status changes.

    Usage:
      /track <project> <issue#>    - Track issue in specific project

    Examples:
      /track nxs 456
    """
    await issue_track_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def tracked_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show active globally tracked issues."""
    await issue_tracked_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def untrack_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop tracking an issue.

    Usage:
      /untrack <issue#>              - Stop global tracking
      /untrack <project> <issue#>    - Stop per-project tracking
    """
    await issue_untrack_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def myissues_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show all issues tracked by the user across projects."""
    await issue_myissues_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


@rate_limited("logs")
async def logs_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show combined timeline of Git activity and bot/processor logs for an issue."""
    await monitoring_logs_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


@rate_limited("logs")
async def logsfull_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show combined timeline of Git activity and full log lines for an issue."""
    await monitoring_logsfull_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


@rate_limited("logs")
async def tail_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show a short tail of the latest task log for an issue."""
    await monitoring_tail_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


@rate_limited("logs")
async def tailstop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop an active live tail session for the current user/chat."""
    await monitoring_tailstop_handler(
        _build_telegram_interactive_ctx(update, context),
        _monitoring_handler_deps(),
    )


@rate_limited("logs")
async def fuse_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show retry-fuse status for an issue."""
    await monitoring_fuse_handler(
        _build_telegram_interactive_ctx(update, context), _monitoring_handler_deps()
    )


async def audit_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display workflow audit trail for an issue (timeline of state changes, agent launches, etc)."""
    await ops_audit_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("stats")
async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Display system analytics and performance statistics."""
    await ops_stats_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("stats")
async def inboxq_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Inspect inbox queue summary for postgres backend."""
    await ops_inboxq_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("reprocess")
async def reprocess_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Re-run agent processing for an open issue."""
    await workflow_reprocess_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def continue_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Continue/resume agent processing for an issue with a continuation prompt."""
    await workflow_continue_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def forget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Permanently forget local workflow/tracker state for an issue."""
    await workflow_forget_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def kill_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Kill a running AI agent process."""
    await workflow_kill_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def reconcile_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Reconcile workflow and local completion from structured Git comments."""
    await workflow_reconcile_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def wfstate_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show workflow state + signal drift snapshot for an issue."""
    await workflow_wfstate_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def visualize_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Render a Mermaid workflow diagram for an issue."""
    await workflow_visualize_handler(
        _build_telegram_interactive_ctx(update, context), _visualize_handler_deps()
    )


async def watch_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stream live workflow updates for an issue."""
    await workflow_watch_handler(
        _build_telegram_interactive_ctx(update, context), _watch_handler_deps()
    )


async def pause_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Pause auto-chaining with project picker support."""
    await workflow_pause_picker_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def resume_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resume auto-chaining with project picker support."""
    await workflow_resume_picker_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


async def stop_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Stop workflow with project picker support."""
    await workflow_stop_picker_handler(
        _build_telegram_interactive_ctx(update, context), _workflow_handler_deps()
    )


# pause_handler, resume_handler, and stop_handler now wrap commands.workflow handlers


async def agents_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all agents for a specific project."""
    await ops_agents_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


@rate_limited("direct")
async def direct_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send a direct request to a specific agent for a project."""
    await ops_direct_handler(_build_telegram_interactive_ctx(update, context), _ops_handler_deps())


async def feature_done_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Mark a feature as implemented for a project."""
    await core_feature_done_handler(
        _build_telegram_interactive_ctx(update, context), _feature_registry_command_deps()
    )


async def feature_list_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List implemented features for a project."""
    await core_feature_list_handler(
        _build_telegram_interactive_ctx(update, context), _feature_registry_command_deps()
    )


async def feature_forget_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forget a previously implemented feature by id or title."""
    await core_feature_forget_handler(
        _build_telegram_interactive_ctx(update, context), _feature_registry_command_deps()
    )


async def comments_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """View recent comments on an issue."""
    await issue_comments_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def respond_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Post a response to an issue and automatically continue the agent."""
    await issue_respond_handler(
        _build_telegram_interactive_ctx(update, context), _issue_handler_deps()
    )


async def inline_keyboard_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle inline keyboard button presses from notifications."""
    await _call_core_callback_handler(update, context, callback_inline_keyboard_handler)


async def _report_bug_action_wrapper(ctx, issue_num: str, project_key: str):
    repo_key = get_repo(project_key)
    await handle_report_bug(
        ctx,
        issue_num,
        repo_key=repo_key,
        get_direct_issue_plugin=_get_direct_issue_plugin,
    )


async def telegram_error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global Telegram error handler for uncaught update exceptions."""
    logger.exception("Unhandled exception while processing update", exc_info=context.error)

    # Truncate and format
    user_msg = format_error_for_user(context.error, "Internal error")

    # Bug Reporting Button
    buttons = []
    # If we have an issue context in user_data, add a report button
    pending_issue = context.user_data.get("pending_issue")
    pending_project = context.user_data.get("pending_project")
    if pending_issue and pending_project:
        buttons = [
            [Button("🐞 Report Bug", callback_data=f"report_bug_{pending_issue}|{pending_project}")]
        ]

    if isinstance(update, Update) and update.effective_message:
        with contextlib.suppress(Exception):
            await update.effective_message.reply_text(
                f"❌ {user_msg}",
                reply_markup=_buttons_to_reply_markup(buttons) if buttons else None,
            )


# --- MAIN ---
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    report_scheduler = None
    if _svc_reports_enabled():
        report_scheduler = ReportScheduler()
        logger.info("📊 Scheduled reports will be enabled after startup")

    alerting_system = None
    if _svc_alerting_enabled():
        alerting_system = init_alerting_system()
        logger.info("🚨 Alerting system will be enabled after startup")

    app.post_init = _svc_build_post_init_with_scheduler(
        original_post_init=on_startup,
        report_scheduler=report_scheduler,
        alerting_system=alerting_system,
        logger=logger,
    )

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("new", start_selection)],
        states={
            SELECT_PROJECT: [
                CallbackQueryHandler(project_selected, pattern=r"^[a-z_]+$"),
                CallbackQueryHandler(flow_close_handler, pattern=r"^flow:close$"),
            ],
            SELECT_TYPE: [
                CallbackQueryHandler(type_selected, pattern=r"^[a-z-]+$"),
                CallbackQueryHandler(flow_close_handler, pattern=r"^flow:close$"),
            ],
            INPUT_TASK: [
                MessageHandler((filters.TEXT & ~filters.COMMAND) | filters.VOICE, save_task)
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
        per_message=False,
    )

    _svc_register_application_handlers(
        app=app,
        conv_handler=conv_handler,
        filters_module=filters,
        authorize_update=_authorize_update,
        handlers={
            "start_handler": start_handler,
            "help_handler": help_handler,
            "menu_handler": menu_handler,
            "login_handler": login_handler,
            "setup_status_handler": setup_status_handler,
            "whoami_handler": whoami_handler,
            "rename_handler": rename_handler,
            "cancel": cancel,
            "status_handler": status_handler,
            "inboxq_handler": inboxq_handler,
            "active_handler": active_handler,
            "progress_handler": progress_handler,
            "track_handler": track_handler,
            "tracked_handler": tracked_handler,
            "untrack_handler": untrack_handler,
            "myissues_handler": myissues_handler,
            "logs_handler": logs_handler,
            "logsfull_handler": logsfull_handler,
            "tail_handler": tail_handler,
            "tailstop_handler": tailstop_handler,
            "fuse_handler": fuse_handler,
            "audit_handler": audit_handler,
            "wfstate_handler": wfstate_handler,
            "visualize_handler": visualize_handler,
            "watch_handler": watch_handler,
            "stats_handler": stats_handler,
            "comments_handler": comments_handler,
            "reprocess_handler": reprocess_handler,
            "reconcile_handler": reconcile_handler,
            "continue_handler": continue_handler,
            "forget_handler": forget_handler,
            "kill_handler": kill_handler,
            "pause_handler": pause_handler,
            "resume_handler": resume_handler,
            "stop_handler": stop_handler,
            "agents_handler": agents_handler,
            "direct_handler": direct_handler,
            "respond_handler": respond_handler,
            "assign_handler": assign_handler,
            "implement_handler": implement_handler,
            "prepare_handler": prepare_handler,
            "plan_handler": plan_handler,
            "feature_done_handler": feature_done_handler,
            "feature_list_handler": feature_list_handler,
            "feature_forget_handler": feature_forget_handler,
            "chat_menu_handler": chat_menu_handler,
            "chat_agents_handler": chat_agents_handler,
            "chat_callback_handler": chat_callback_handler,
            "menu_callback_handler": menu_callback_handler,
            "project_picker_handler": project_picker_handler,
            "issue_picker_handler": issue_picker_handler,
            "monitor_project_picker_handler": monitor_project_picker_handler,
            "close_flow_handler": close_flow_handler,
            "feature_callback_handler": feature_callback_handler,
            "task_confirmation_callback_handler": task_confirmation_callback_handler,
            "inline_keyboard_handler": inline_keyboard_handler,
            "hands_free_handler": hands_free_handler,
            "telegram_error_handler": telegram_error_handler,
        },
    )

    print("Nexus Online...")
    app.run_polling(drop_pending_updates=True, allowed_updates=_svc_allowed_updates_all_types())


if __name__ == "__main__":
    main()
