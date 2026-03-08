"""
Nexus-Arc Framework Integration Helpers.

This module provides integration between the original Nexus bot
and the nexus-arc workflow framework.
"""

import logging
import os
from typing import Any

from nexus.adapters.git.factory import resolve_git_platform_class
from nexus.adapters.storage.file import FileStorage
from nexus.core.audit_store import AuditStore
from nexus.core.config import (
    BASE_DIR,
    NEXUS_CORE_STORAGE_DIR,
    _get_project_config,
    get_default_project,
    get_repo,
    get_gitlab_base_url,
    get_project_platform,
)
from nexus.core.events import EventBus, NexusEvent
from nexus.core.mermaid_render_service import build_mermaid_diagram
from nexus.core.orchestration.plugin_runtime import get_workflow_state_plugin
from nexus.core.workflow import WorkflowEngine

logger = logging.getLogger(__name__)

# Singleton EventBus shared across the host application
_event_bus: EventBus | None = None


def get_git_repo(project_name: str) -> str:
    """Compatibility wrapper for repository lookup."""
    return get_repo(project_name)


def get_event_bus() -> EventBus:
    """Get or create the global EventBus instance."""
    global _event_bus
    if _event_bus is None:
        _event_bus = EventBus()
    return _event_bus


def get_workflow_engine() -> WorkflowEngine:
    """Get initialized workflow engine instance."""
    storage = FileStorage(base_path=NEXUS_CORE_STORAGE_DIR)
    return WorkflowEngine(storage=storage, event_bus=get_event_bus())


async def _setup_socketio_event_bridge(bus: EventBus) -> None:
    """Subscribe to workflow events and bridge them to SocketIO broadcasts."""
    from nexus.core.state_manager import HostStateManager
    from nexus.core.integrations.workflow_state_factory import get_workflow_state

    async def handle_event(event: NexusEvent) -> None:
        workflow_id = event.workflow_id
        if not workflow_id:
            return

        # 1. Resolve issue number from workflow_id
        mappings = get_workflow_state().load_all_mappings()
        issue = next((k for k, v in mappings.items() if v == workflow_id), None)
        if not issue:
            return

        # 2. Handle step status changes
        if event.event_type.startswith("step."):
            status_map = {
                "step.started": "running",
                "step.completed": "done",
                "step.failed": "failed",
                "step.skipped": "skipped",
            }
            status = status_map.get(event.event_type)
            if status:
                HostStateManager.emit_step_status_changed(
                    issue=issue,
                    workflow_id=workflow_id,
                    step_id=getattr(event, "step_name", ""),
                    agent_type=getattr(event, "agent_type", ""),
                    status=status,
                )

                # 3. Emit updated mermaid diagram (skip for skipped steps to reduce I/O)
                if event.event_type != "step.skipped":
                    engine = get_workflow_engine()
                    workflow = await engine.get_workflow(workflow_id)
                    if workflow:
                        steps_data = []
                        for s in workflow.steps:
                            steps_data.append(
                                {
                                    "name": s.name,
                                    "status": s.status.value,
                                    "agent": {"name": s.agent.name},
                                }
                            )
                        diagram = build_mermaid_diagram(steps_data, issue)
                        HostStateManager.emit_transition(
                            "mermaid_diagram",
                            {
                                "issue": issue,
                                "workflow_id": workflow_id,
                                "diagram": diagram,
                                "timestamp": event.timestamp.timestamp(),
                            },
                        )

        # 4. Handle workflow completion
        elif event.event_type in ("workflow.completed", "workflow.failed"):
            status = "success" if event.event_type == "workflow.completed" else "failed"
            total_steps = getattr(event, "total_steps", 0)
            completed_steps = getattr(event, "completed_steps", 0)
            failed_steps = getattr(event, "failed_steps", 0)
            skipped_steps = getattr(event, "skipped_steps", 0)
            if total_steps:
                summary = (
                    f"Workflow {status}: {completed_steps}/{total_steps} steps completed"
                    + (f", {skipped_steps} skipped" if skipped_steps else "")
                    + (f", {failed_steps} failed" if failed_steps else "")
                )
            else:
                summary = f"Workflow {status}"
            HostStateManager.emit_transition(
                "workflow_completed",
                {
                    "issue": issue,
                    "workflow_id": workflow_id,
                    "status": status,
                    "summary": summary,
                    "total_steps": total_steps,
                    "completed_steps": completed_steps,
                    "failed_steps": failed_steps,
                    "skipped_steps": skipped_steps,
                    "timestamp": event.timestamp.timestamp(),
                },
            )

    # Register handlers for step and workflow transitions
    bus.subscribe_pattern("step.*", handle_event)
    bus.subscribe_pattern("workflow.*", handle_event)
    logger.info("✅ SocketIO event bridge attached to EventBus")


def setup_event_handlers() -> None:
    """Attach event handler plugins to the shared EventBus.

    Reads config from environment variables:
        - TELEGRAM_TOKEN + TELEGRAM_CHAT_ID → Telegram handler
        - DISCORD_WEBHOOK_URL / DISCORD_TOKEN → Discord handler
        - SLACK_WEBHOOK_URL / SLACK_TOKEN → Slack handler

    Safe to call multiple times; handlers are only attached once.
    """
    bus = get_event_bus()

    # Already initialized?
    if getattr(setup_event_handlers, "_done", False):
        return
    setup_event_handlers._done = True  # type: ignore[attr-defined]

    # Initialize SocketIO bridge
    import asyncio

    try:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            loop.create_task(_setup_socketio_event_bridge(bus))
        else:
            asyncio.run(_setup_socketio_event_bridge(bus))
    except Exception as exc:
        logger.warning("Failed to setup SocketIO event bridge: %s", exc)

    import os

    # Telegram
    tg_token = os.getenv("TELEGRAM_TOKEN", "")
    tg_chat = os.getenv("TELEGRAM_CHAT_ID", "")
    if not tg_chat:
        tg_chat = os.getenv("ALLOWED_USER", "")
    if not tg_chat:
        tg_allowed = os.getenv("TELEGRAM_ALLOWED_USER_IDS", "")
        if tg_allowed:
            tg_chat = tg_allowed.split(",", 1)[0].strip()
    if tg_token and tg_chat:
        try:
            from nexus.plugins.builtin.telegram_event_handler_plugin import TelegramEventHandler

            handler = TelegramEventHandler({"bot_token": tg_token, "chat_id": tg_chat})
            handler.attach(bus)
            logger.info("Telegram event handler attached to EventBus")
        except Exception as exc:
            logger.warning("Failed to setup Telegram event handler: %s", exc)

    # Discord
    dc_webhook = os.getenv("DISCORD_WEBHOOK_URL", "")
    dc_bot_token = os.getenv("DISCORD_TOKEN", "")
    dc_channel = os.getenv("DISCORD_ALERT_CHANNEL_ID", "")
    dc_guild = os.getenv("DISCORD_GUILD_ID", "")
    if dc_webhook or dc_channel or dc_bot_token:
        try:
            from nexus.plugins.builtin.discord_event_handler_plugin import DiscordEventHandler

            handler = DiscordEventHandler(
                {
                    "webhook_url": dc_webhook or None,
                    "bot_token": dc_bot_token or None,
                    "alert_channel_id": dc_channel or None,
                    "guild_id": dc_guild or None,
                }
            )
            handler.attach(bus)
            logger.info("Discord event handler attached to EventBus")
        except Exception as exc:
            logger.warning("Failed to setup Discord event handler: %s", exc)

    # Slack
    slack_webhook = os.getenv("SLACK_WEBHOOK_URL", "")
    slack_bot_token = os.getenv("SLACK_TOKEN", "")
    slack_channel = os.getenv("SLACK_DEFAULT_CHANNEL", "#ops")
    if slack_webhook or slack_bot_token:
        try:
            from nexus.plugins.builtin.slack_event_handler_plugin import SlackEventHandler

            handler = SlackEventHandler(
                {
                    "webhook_url": slack_webhook or None,
                    "bot_token": slack_bot_token or "",
                    "default_channel": slack_channel,
                }
            )
            handler.attach(bus)
            logger.info("Slack event handler attached to EventBus")
        except Exception as exc:
            logger.warning("Failed to setup Slack event handler: %s", exc)


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return bool(default)
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def _allow_env_token_fallback_default() -> bool:
    """Allow global env-token fallback only for local/test runtime modes."""
    if not _env_bool("NEXUS_AUTH_ENABLED", False):
        return True
    storage_raw = str(os.getenv("NEXUS_STORAGE_BACKEND", "")).strip().lower()
    # Kept as explicit opt-in alias for local/test mode as requested.
    return storage_raw == "database"


def get_git_platform(
    repo: str = None,
    project_name: str = None,
    token_override: str | None = None,
    allow_env_token_fallback: bool | None = None,
):
    """Get initialized Git platform adapter for the project.

    Returns either :class:`GitPlatform` or :class:`GitLabPlatform`.
    """
    project_key = project_name or get_default_project()
    repo_name = repo or get_git_repo(project_key)
    platform_type = get_project_platform(project_key)

    project_config = _get_project_config().get(project_key, {})
    default_token_var = "GITLAB_TOKEN" if platform_type == "gitlab" else "GITHUB_TOKEN"
    token_var = project_config.get("git_token_var_name", default_token_var)
    allow_service_token_fallback = str(
        os.getenv("NEXUS_ALLOW_SERVICE_GIT_TOKEN_FALLBACK", "true")
    ).strip().lower() in {"1", "true", "yes", "on"}
    override_token = str(token_override or "").strip()
    token = override_token
    if not token:
        should_fallback = (
            _allow_env_token_fallback_default()
            if allow_env_token_fallback is None
            else bool(allow_env_token_fallback)
        )
        if should_fallback:
            token = str((os.getenv(token_var, "")).strip() if allow_service_token_fallback else "")

    if platform_type == "gitlab":
        platform_cls = resolve_git_platform_class("gitlab")
        if not token:
            raise ValueError(
                f"GitLab token required for project '{project_key}'. "
                "Provide user token context or enable NEXUS_ALLOW_SERVICE_GIT_TOKEN_FALLBACK. "
                "Provide a requester-scoped token_override "
                f"(env fallback disabled for token var '{token_var}')."
            )
        return platform_cls(
            token=token,
            repo=repo_name,
            base_url=get_gitlab_base_url(project_key),
        )

    if not token:
        logger.warning(
            "Git token missing for project '%s'. "
            "Provide requester-scoped token_override (env fallback may be disabled).",
            project_key,
        )
    platform_cls = resolve_git_platform_class("github")
    return platform_cls(repo=repo_name, token=token)


def get_workflow_definition_path(project_name: str) -> str | None:
    """Get workflow definition path for a project with fallback logic.

    Priority:
    1. Project-specific override in PROJECT_CONFIG
    2. Global workflow_definition_path in PROJECT_CONFIG
    3. None (caller must abort)

    Args:
        project_name: Project name (e.g., 'nexus')

    Returns:
        Absolute path to workflow YAML file, or None if not configured
    """
    config = _get_project_config()

    # Check project-specific override
    if project_name in config:
        project_config = config[project_name]
        if isinstance(project_config, dict) and "workflow_definition_path" in project_config:
            path = project_config["workflow_definition_path"]
            # Resolve relative paths to absolute
            if path and not os.path.isabs(path):
                path = os.path.join(BASE_DIR, path)
            return path

    # Check global workflow_definition_path
    if "workflow_definition_path" in config:
        path = config["workflow_definition_path"]
        # Resolve relative paths to absolute
        if path and not os.path.isabs(path):
            path = os.path.join(BASE_DIR, path)
        return path

    # No workflow definition found
    return None


from nexus.core.integrations.workflow_state_factory import get_workflow_state as _get_wf_state

_WORKFLOW_STATE_PLUGIN_BASE_KWARGS = {
    "storage_dir": NEXUS_CORE_STORAGE_DIR,
    "issue_to_workflow_id": lambda n: _get_wf_state().get_workflow_id(n),
    "issue_to_workflow_map_setter": lambda n, w: _get_wf_state().map_issue(n, w),
    "workflow_definition_path_resolver": get_workflow_definition_path,
    "set_pending_approval": lambda *a: _get_wf_state().set_pending_approval(*a),
    "clear_pending_approval": lambda n: _get_wf_state().clear_pending_approval(n),
    "audit_log": AuditStore.audit_log,
}
_WORKFLOW_STATE_PLUGIN_CACHE_KEY = "workflow:state-engine"


async def create_workflow_for_issue(
    issue_number: str,
    issue_title: str,
    project_name: str,
    tier_name: str,
    task_type: str,
    description: str = "",
) -> str | None:
    """
    Create a nexus-arc workflow for a Git issue.

    Args:
        issue_number: Git issue number
        issue_title: Issue title (slug)
        project_name: Project name (e.g., 'nxs')
        tier_name: Workflow tier (tier-1-simple, tier-2-standard, etc.)
        task_type: Task type (feature, bug, hotfix, etc.)
        description: Task description

    Returns:
        workflow_id if successful, None otherwise
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        repo_key=get_repo(project_name),
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )

    workflow_id = await workflow_plugin.create_workflow_for_issue(
        issue_number=issue_number,
        issue_title=issue_title,
        project_name=project_name,
        tier_name=tier_name,
        task_type=task_type,
        description=description,
    )

    if workflow_id:
        return workflow_id

    workflow_definition_path = get_workflow_definition_path(project_name)
    if not workflow_definition_path:
        msg = (
            f"No workflow_definition_path configured for project '{project_name}'. "
            "Cannot create workflow without a YAML definition."
        )
        logger.error(msg)
        from nexus.core.integrations.notifications import emit_alert

        emit_alert(f"❌ {msg}", severity="error", source="nexus_core_helpers")
    elif not os.path.exists(workflow_definition_path):
        msg = (
            f"Workflow definition not found at: {workflow_definition_path} "
            f"(project: {project_name})"
        )
        logger.error(msg)
        from nexus.core.integrations.notifications import emit_alert

        emit_alert(f"❌ {msg}", severity="error", source="nexus_core_helpers")
    return None


async def start_workflow(workflow_id: str, issue_number: str = None) -> bool:
    """
    Start a workflow.

    Args:
        workflow_id: Workflow ID
        issue_number: Optional issue number for Git comment

    Returns:
        True if successful
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )

    success = await workflow_plugin.start_workflow(workflow_id)
    if success and issue_number:
        logger.info(f"Started workflow {workflow_id} for issue #{issue_number}")
    return success


async def pause_workflow(issue_number: str, reason: str = "User requested") -> bool:
    """
    Pause a workflow by issue number.

    Args:
        issue_number: Git issue number
        reason: Reason for pausing

    Returns:
        True if successful
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.pause_workflow(issue_number, reason=reason)


async def resume_workflow(issue_number: str) -> bool:
    """
    Resume a paused workflow by issue number.

    Args:
        issue_number: Git issue number

    Returns:
        True if successful
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.resume_workflow(issue_number)


async def get_workflow_status(issue_number: str) -> dict | None:
    """
    Get workflow status for an issue.

    Args:
        issue_number: Git issue number

    Returns:
        Dict with workflow status or None
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.get_workflow_status(issue_number)


async def handle_approval_gate(
    workflow_id: str,
    issue_number: str,
    step_num: int,
    step_name: str,
    agent_name: str,
    approvers: list[str],
    approval_timeout: int,
    project: str = "nexus",
) -> None:
    """
    Called after complete_step when the next step has approval_required=True.
    Persists the pending approval and sends a Telegram notification.

    Args:
        workflow_id: The workflow ID (for reference)
        issue_number: Git issue number
        step_num: Step number awaiting approval
        step_name: Step name awaiting approval
        agent_name: Agent that will run the step when approved
        approvers: List of required approvers
        approval_timeout: Timeout in seconds
        project: Project name
    """

    def _notify_approval_required(**kwargs):
        from nexus.core.integrations.notifications import notify_approval_required

        notify_approval_required(**kwargs)

    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        notify_approval_required=_notify_approval_required,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )

    await workflow_plugin.request_approval_gate(
        workflow_id=workflow_id,
        issue_number=issue_number,
        step_num=step_num,
        step_name=step_name,
        agent_name=agent_name,
        approvers=approvers,
        approval_timeout=approval_timeout,
        project=project,
    )

    logger.info(
        f"Approval gate triggered for issue #{issue_number} " f"step {step_num} ({step_name})."
    )


async def complete_step_for_issue(
    issue_number: str,
    completed_agent_type: str,
    outputs: dict[str, Any],
    event_id: str = "",
):
    """Mark the current running step for *issue_number* as complete.

    Delegates to ``WorkflowStateEnginePlugin.complete_step_for_issue()``.
    The engine evaluates router steps automatically, handling conditional
    branches and review/develop loops.

    Args:
        issue_number: Git issue number.
        completed_agent_type: The ``agent_type`` that just finished.
        outputs: Structured outputs from the completion summary (use
            ``CompletionSummary.to_dict()`` or pass a raw dict).
        event_id: Optional deduplication token (comment id / completion hash).

    Returns:
        Updated :class:`~nexus.core.models.Workflow` (inspect ``.state`` and
        ``.active_agent_type`` to determine what to do next), or ``None``
        when no workflow is mapped to the issue.
    """
    workflow_plugin = get_workflow_state_plugin(
        **_WORKFLOW_STATE_PLUGIN_BASE_KWARGS,
        cache_key=_WORKFLOW_STATE_PLUGIN_CACHE_KEY,
    )
    return await workflow_plugin.complete_step_for_issue(
        issue_number=str(issue_number),
        completed_agent_type=completed_agent_type,
        outputs=outputs,
        event_id=event_id,
    )
