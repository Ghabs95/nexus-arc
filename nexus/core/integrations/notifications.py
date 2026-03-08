"""Enhanced notifications with inline keyboards for Nexus.

Provides rich Telegram notifications with interactive buttons for quick actions.
"""

import asyncio
import logging
import re
import threading
from typing import Any, Sequence

from nexus.adapters.git.utils import build_issue_url
from nexus.core.config import TELEGRAM_CHAT_ID, TELEGRAM_TOKEN, get_repo, PROJECT_CONFIG
from nexus.core.orchestration.plugin_runtime import get_profiled_plugin

logger = logging.getLogger(__name__)

_EVENTBUS_LOOP_LOCK = threading.Lock()
_EVENTBUS_LOOP: asyncio.AbstractEventLoop | None = None
_EVENTBUS_LOOP_THREAD: threading.Thread | None = None


def _run_eventbus_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Run a dedicated loop for sync-to-async EventBus emits."""
    asyncio.set_event_loop(loop)
    loop.run_forever()


def _get_eventbus_loop() -> asyncio.AbstractEventLoop:
    """Get or start a dedicated background loop for EventBus emits."""
    global _EVENTBUS_LOOP, _EVENTBUS_LOOP_THREAD
    with _EVENTBUS_LOOP_LOCK:
        if (
            _EVENTBUS_LOOP is not None
            and _EVENTBUS_LOOP.is_running()
            and not _EVENTBUS_LOOP.is_closed()
        ):
            return _EVENTBUS_LOOP

        loop = asyncio.new_event_loop()
        thread = threading.Thread(
            target=_run_eventbus_loop,
            args=(loop,),
            daemon=True,
            name="nexus-eventbus-loop",
        )
        thread.start()
        _EVENTBUS_LOOP = loop
        _EVENTBUS_LOOP_THREAD = thread
        return loop


def _emit_eventbus_sync(*, bus: Any, event: Any, timeout: float = 10.0) -> None:
    """Emit an EventBus event from sync code without spinning a new loop per call."""
    loop = _get_eventbus_loop()
    future = asyncio.run_coroutine_threadsafe(bus.emit(event), loop)
    future.result(timeout=timeout)


def _extract_issue_number(text: str) -> str:
    """Best-effort issue number extraction from free-form alert text."""
    if not text:
        return ""
    match = re.search(r"(?:issue\s*#|#)(\d+)\b", text, flags=re.IGNORECASE)
    if match:
        return match.group(1)
    return ""


def _normalize_alert_actions(actions: Sequence[dict[str, Any]] | None) -> list[dict[str, str]]:
    """Normalize alert actions to ``{label, callback_data, url}`` dictionaries."""
    normalized: list[dict[str, str]] = []
    for raw in actions or []:
        if not isinstance(raw, dict):
            continue
        label = str(raw.get("label", "")).strip()
        callback_data = str(raw.get("callback_data", "")).strip()
        url = str(raw.get("url", "")).strip()
        if not label:
            continue
        if callback_data and "|" not in callback_data:
            logger.warning(
                "Dropping callback action without project context: %s",
                callback_data,
            )
            continue
        if not callback_data and not url:
            continue
        normalized.append(
            {
                "label": label,
                "callback_data": callback_data,
                "url": url,
            }
        )
    return normalized


def _default_alert_actions(
    severity: str,
    issue_number: str,
    project: str | None = None,
) -> list[dict[str, str]]:
    """Return default issue-scoped actions for high-signal alerts."""
    if not issue_number or not project:
        return []

    sev = str(severity or "info").strip().lower()
    if sev == "critical":
        return [
            {
                "label": "🔧 Start Fix",
                "callback_data": _issue_callback("reprocess", issue_number, project),
                "url": "",
            },
            {
                "label": "🛑 Stop",
                "callback_data": _issue_callback("stop", issue_number, project),
                "url": "",
            },
            {
                "label": "📝 Logs",
                "callback_data": _issue_callback("logs", issue_number, project),
                "url": "",
            },
        ]
    if sev == "error":
        return [
            {
                "label": "🔧 Start Fix",
                "callback_data": _issue_callback("reprocess", issue_number, project),
                "url": "",
            },
            {
                "label": "📝 Logs",
                "callback_data": _issue_callback("logs", issue_number, project),
                "url": "",
            },
        ]
    if sev == "warning":
        return [
            {
                "label": "📝 Logs",
                "callback_data": _issue_callback("logs", issue_number, project),
                "url": "",
            }
        ]
    return []


def _build_reply_markup(
    actions: Sequence[dict[str, str]] | None,
) -> dict[str, list[list[dict[str, str]]]] | None:
    """Build Telegram inline keyboard reply markup from action dicts."""
    rows: list[list[dict[str, str]]] = []
    current_row: list[dict[str, str]] = []
    for action in actions or []:
        label = str(action.get("label", "")).strip()
        callback_data = str(action.get("callback_data", "")).strip()
        url = str(action.get("url", "")).strip()
        if not label:
            continue
        if not callback_data and not url:
            continue
        btn: dict[str, str] = {"text": label}
        if url:
            btn["url"] = url
        else:
            btn["callback_data"] = callback_data
        current_row.append(btn)
        if len(current_row) == 2:
            rows.append(current_row)
            current_row = []
    if current_row:
        rows.append(current_row)
    if not rows:
        return None
    return {"inline_keyboard": rows}


def _issue_callback(action: str, issue_number: str, project: str | None = None) -> str:
    """Build callback payload with strict project hint requirement."""
    issue = str(issue_number or "").strip().lstrip("#")
    project_key = str(project or "").strip()
    if not issue or not project_key:
        raise ValueError(f"Callback '{action}' requires issue and project")
    return f"{action}_{issue}|{project_key}"


def _normalize_telegram_markdown(message: str, parse_mode: str) -> str:
    """Normalize markdown for Telegram legacy Markdown mode.

    Telegram's legacy Markdown expects *bold* rather than **bold**.
    """
    if parse_mode != "Markdown" or not message:
        return message
    return re.sub(r"\*\*(.+?)\*\*", r"*\1*", message)


def _get_notification_plugin():
    """Return shared Telegram notification plugin instance."""
    return get_profiled_plugin(
        "notification_telegram",
        overrides={
            "bot_token": TELEGRAM_TOKEN,
            "chat_id": TELEGRAM_CHAT_ID,
        },
        cache_key="notification:telegram-default",
    )


class InlineKeyboard:
    """Builder for Telegram inline keyboards."""

    def __init__(self):
        """Initialize keyboard builder."""
        self.rows: list[list[dict]] = []

    def add_button(self, text: str, callback_data: str | None = None, url: str | None = None):
        """
        Add a button to the current row.

        Args:
            text: Button label text
            callback_data: Callback data for button press (ignored if url provided)
            url: Optional URL to open (makes button a URL button)

        Returns:
            Self for chaining
        """
        if not self.rows:
            self.rows.append([])

        button = {"text": text}
        if url:
            button["url"] = url
        elif callback_data:
            button["callback_data"] = callback_data
        else:
            raise ValueError("Either callback_data or url must be provided")

        self.rows[-1].append(button)
        return self

    def new_row(self):
        """Start a new row of buttons."""
        self.rows.append([])
        return self

    def build(self) -> dict:
        """
        Build the keyboard structure.

        Returns:
            Inline keyboard markup dict
        """
        return {"inline_keyboard": self.rows}


def send_notification(
    message: str, parse_mode: str = "Markdown", keyboard: InlineKeyboard | None = None
) -> bool:
    """
    Send a notification to Telegram with optional inline keyboard.

    Args:
        message: Message text
        parse_mode: Parse mode (Markdown or HTML)
        keyboard: Optional inline keyboard

    Returns:
        True if sent successfully
    """
    plugin = _get_notification_plugin()
    message = _normalize_telegram_markdown(message, parse_mode)
    reply_markup = keyboard.build() if keyboard else None

    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping notification")
        return False
    if not plugin:
        logger.error("Telegram notification plugin unavailable")
        return False

    try:
        if not hasattr(plugin, "send_message_sync"):
            logger.error("Telegram notification plugin missing send_message_sync")
            return False

        return bool(
            plugin.send_message_sync(
                message=message,
                parse_mode=parse_mode,
                reply_markup=reply_markup,
            )
        )
    except Exception as e:
        logger.error(f"Failed to send notification: {e}")
        return False


def notify_agent_needs_input(
    issue_number: str, agent: str, preview: str, project: str = "nexus"
) -> bool:
    """
    Send notification that an agent needs input.

    Args:
        issue_number: Git issue number
        agent: Agent name
        preview: Preview of the agent's question
        project: Project name (default: nexus)

    Returns:
        True if sent successfully
    """
    message = (
        f"📋 **Agent Needs Input**\n\n"
        f"Issue: #{issue_number}\n"
        f"Agent: @{agent}\n\n"
        f"Preview:\n{preview}"
    )

    keyboard = (
        InlineKeyboard()
        .add_button("📝 View Full", callback_data=_issue_callback("logs", issue_number, project))
        .add_button(
            "🔗 Issue",
            url=build_issue_url(
                get_repo(project),
                issue_number,
                (
                    PROJECT_CONFIG.get(project)
                    if isinstance(PROJECT_CONFIG.get(project), dict)
                    else None
                ),
            ),
        )
        .new_row()
        .add_button("✍️ Respond", callback_data=_issue_callback("respond", issue_number, project))
    )

    return send_notification(message, keyboard=keyboard)


def notify_workflow_started(issue_number: str, project: str, tier: str, task_type: str) -> bool:
    """
    Send notification that a workflow has started.

    Args:
        issue_number: Git issue number
        project: Project name
        tier: Workflow tier (full, shortened, fast-track)
        task_type: Task type (feature, bug, hotfix, etc.)

    Returns:
        True if sent successfully
    """
    tier_emoji = {"full": "🟡", "shortened": "🟠", "fast-track": "🟢"}

    message = (
        f"🚀 **Workflow Started**\n\n"
        f"Issue: #{issue_number}\n"
        f"Project: {project}\n"
        f"Type: {task_type}\n"
        f"Tier: {tier_emoji.get(tier, '⚪')} {tier}"
    )

    keyboard = (
        InlineKeyboard()
        .add_button("👀 Logs", callback_data=_issue_callback("logs", issue_number, project))
        .add_button("📊 Status", callback_data=_issue_callback("status", issue_number, project))
        .new_row()
        .add_button(
            "🔗 Issue",
            url=build_issue_url(
                get_repo(project),
                issue_number,
                (
                    PROJECT_CONFIG.get(project)
                    if isinstance(PROJECT_CONFIG.get(project), dict)
                    else None
                ),
            ),
        )
        .add_button("⏸️ Pause", callback_data=_issue_callback("pause", issue_number, project))
    )

    return send_notification(message, keyboard=keyboard)


def notify_agent_completed(
    issue_number: str,
    completed_agent: str | None = None,
    next_agent: str = "",
    project: str = "nexus",
    agent_name: str | None = None,
) -> bool:
    """
    Send notification that an agent completed and next one started.

    Args:
        issue_number: Git issue number
        completed_agent: Agent that just completed
        next_agent: Agent that's starting next
        project: Project name (default: nexus)
        agent_name: Legacy alias for completed_agent

    Returns:
        True if sent successfully
    """
    resolved_completed_agent = completed_agent or agent_name or "agent"
    resolved_next_agent = next_agent or "unknown"

    message = (
        f"✅ **Agent Completed → Auto-Chain**\n\n"
        f"Issue: #{issue_number}\n"
        f"Completed: @{resolved_completed_agent}\n"
        f"Next: @{resolved_next_agent}"
    )

    keyboard = (
        InlineKeyboard()
        .add_button("📝 View Logs", callback_data=_issue_callback("logs", issue_number, project))
        .add_button(
            "🔗 Issue",
            url=build_issue_url(
                get_repo(project),
                issue_number,
                (
                    PROJECT_CONFIG.get(project)
                    if isinstance(PROJECT_CONFIG.get(project), dict)
                    else None
                ),
            ),
        )
        .new_row()
        .add_button("⏸️ Pause Chain", callback_data=_issue_callback("pause", issue_number, project))
        .add_button("🛑 Stop", callback_data=_issue_callback("stop", issue_number, project))
    )

    return send_notification(message, keyboard=keyboard)


def notify_agent_timeout(
    issue_number: str, agent: str, will_retry: bool, project: str = "nexus"
) -> bool:
    """
    Send notification about agent timeout.

    Args:
        issue_number: Git issue number
        agent: Agent name
        will_retry: Whether the agent will be retried
        project: Project name (default: nexus)

    Returns:
        True if sent successfully
    """
    agent_label = str(agent or "").strip().lstrip("@").strip()
    normalized = agent_label.lower()
    unresolved = not agent_label or normalized in {"unknown", "none", "n/a"}

    if unresolved:
        message = (
            f"⚠️ **Agent Timeout → Unresolved Agent**\n\n"
            f"Issue: #{issue_number}\n"
            f"Agent: n/a\n"
            f"Status: Missing agent identity in runtime tracker; review logs and reprocess manually"
        )

        keyboard = (
            InlineKeyboard()
            .add_button(
                "📝 View Logs", callback_data=_issue_callback("logs", issue_number, project)
            )
            .add_button(
                "🔗 Issue",
                url=build_issue_url(
                    get_repo(project),
                    issue_number,
                    (
                        PROJECT_CONFIG.get(project)
                        if isinstance(PROJECT_CONFIG.get(project), dict)
                        else None
                    ),
                ),
            )
            .new_row()
            .add_button(
                "🔄 Reprocess", callback_data=_issue_callback("reprocess", issue_number, project)
            )
            .add_button(
                "🛑 Stop Workflow", callback_data=_issue_callback("stop", issue_number, project)
            )
        )
        return send_notification(message, keyboard=keyboard)

    if will_retry:
        message = (
            f"⚠️ **Agent Timeout → Retrying**\n\n"
            f"Issue: #{issue_number}\n"
            f"Agent: @{agent_label}\n"
            f"Status: Process killed, retry scheduled"
        )

        keyboard = (
            InlineKeyboard()
            .add_button(
                "📝 View Logs", callback_data=_issue_callback("logs", issue_number, project)
            )
            .add_button(
                "🔗 Issue",
                url=build_issue_url(
                    get_repo(project),
                    issue_number,
                    (
                        PROJECT_CONFIG.get(project)
                        if isinstance(PROJECT_CONFIG.get(project), dict)
                        else None
                    ),
                ),
            )
            .new_row()
            .add_button(
                "🔄 Reprocess Now",
                callback_data=_issue_callback("reprocess", issue_number, project),
            )
            .add_button("🛑 Stop", callback_data=_issue_callback("stop", issue_number, project))
        )
    else:
        message = (
            f"❌ **Agent Failed → Max Retries**\n\n"
            f"Issue: #{issue_number}\n"
            f"Agent: @{agent_label}\n"
            f"Status: Manual intervention required"
        )

        keyboard = (
            InlineKeyboard()
            .add_button(
                "📝 View Logs", callback_data=_issue_callback("logs", issue_number, project)
            )
            .add_button(
                "🔗 Issue",
                url=build_issue_url(
                    get_repo(project),
                    issue_number,
                    (
                        PROJECT_CONFIG.get(project)
                        if isinstance(PROJECT_CONFIG.get(project), dict)
                        else None
                    ),
                ),
            )
            .new_row()
            .add_button(
                "🔄 Reprocess", callback_data=_issue_callback("reprocess", issue_number, project)
            )
            .add_button(
                "🛑 Stop Workflow", callback_data=_issue_callback("stop", issue_number, project)
            )
        )

    return send_notification(message, keyboard=keyboard)


def notify_workflow_completed(
    issue_number: str,
    project: str,
    pr_urls: Sequence[str] | None = None,
) -> bool:
    """
    Send notification that a workflow completed successfully.

    Args:
        issue_number: Git issue number
        project: Project name
        pr_urls: Optional PR URLs if found

    Returns:
        True if sent successfully
    """
    normalized_pr_urls = [str(url) for url in (pr_urls or []) if str(url).strip()]
    if normalized_pr_urls:
        first_pr_url = normalized_pr_urls[0]
        pr_lines = "\n".join(f"🔗 PR: {url}" for url in normalized_pr_urls)
        message = (
            f"🎉 **Workflow Completed**\n\n"
            f"Issue: #{issue_number}\n"
            f"Project: {project}\n"
            f"PRs: {len(normalized_pr_urls)}\n\n"
            f"All workflow steps completed. **Ready for review!**\n\n"
            f"🔗 Issue: {build_issue_url(get_repo(project), issue_number, PROJECT_CONFIG.get(project) if isinstance(PROJECT_CONFIG.get(project), dict) else None)}\n"
            f"{pr_lines}"
        )

        keyboard = (
            InlineKeyboard()
            .add_button("🔗 View PR", url=first_pr_url)
            .add_button(
                "🔗 View Issue",
                url=build_issue_url(
                    get_repo(project),
                    issue_number,
                    (
                        PROJECT_CONFIG.get(project)
                        if isinstance(PROJECT_CONFIG.get(project), dict)
                        else None
                    ),
                ),
            )
            .new_row()
            .add_button(
                "✅ Approve", callback_data=_issue_callback("approve", issue_number, project)
            )
            .add_button(
                "📝 Request Changes", callback_data=_issue_callback("reject", issue_number, project)
            )
            .new_row()
            .add_button(
                "📝 Full Logs", callback_data=_issue_callback("logsfull", issue_number, project)
            )
            .add_button("📊 Audit", callback_data=_issue_callback("audit", issue_number, project))
        )
    else:
        message = (
            f"🎉 **Workflow Completed**\n\n"
            f"Issue: #{issue_number}\n"
            f"Project: {project}\n"
            f"Status: All agents finished\n\n"
            f"⚠️ No PR found - implementation may be in progress."
        )

        keyboard = (
            InlineKeyboard()
            .add_button(
                "📝 View Full Logs",
                callback_data=_issue_callback("logsfull", issue_number, project),
            )
            .add_button(
                "🚀 Implement",
                callback_data=_issue_callback("implement", issue_number, project),
            )
            .add_button(
                "📝 Draft Plan",
                callback_data=_issue_callback("plan", issue_number, project),
            )
            .add_button(
                "🔗 Issue",
                url=build_issue_url(
                    get_repo(project),
                    issue_number,
                    (
                        PROJECT_CONFIG.get(project)
                        if isinstance(PROJECT_CONFIG.get(project), dict)
                        else None
                    ),
                ),
            )
            .new_row()
            .add_button(
                "📊 View Audit Trail", callback_data=_issue_callback("audit", issue_number, project)
            )
        )

    return send_notification(message, keyboard=keyboard)


def notify_implementation_requested(
    issue_number: str, requester: str, project: str = "nexus"
) -> bool:
    """
    Send notification that implementation was requested.

    Args:
        issue_number: Git issue number
        requester: Who requested the implementation
        project: Project name (default: nexus)

    Returns:
        True if sent successfully
    """
    message = (
        f"🛠️ **Implementation Requested**\n\n"
        f"Issue: #{issue_number}\n"
        f"Requester: {requester}\n"
        f"Status: Awaiting approval"
    )

    keyboard = (
        InlineKeyboard()
        .add_button("✅ Approve", callback_data=_issue_callback("approve", issue_number, project))
        .add_button("❌ Reject", callback_data=_issue_callback("reject", issue_number, project))
        .new_row()
        .add_button("📝 View Details", callback_data=_issue_callback("logs", issue_number, project))
        .add_button(
            "🔗 Issue",
            url=build_issue_url(
                get_repo(project),
                issue_number,
                (
                    PROJECT_CONFIG.get(project)
                    if isinstance(PROJECT_CONFIG.get(project), dict)
                    else None
                ),
            ),
        )
    )

    return send_notification(message, keyboard=keyboard)


def notify_approval_required(
    issue_number: str,
    step_num: int,
    step_name: str,
    agent: str,
    approvers: list[str],
    project: str = "nexus",
) -> bool:
    """
    Send notification that a workflow step is awaiting human approval.

    Args:
        issue_number: Git issue number
        step_num: Step number requiring approval
        step_name: Step name requiring approval
        agent: Agent that would execute the step
        approvers: List of required approvers (shown informatively)
        project: Project name (default: nexus)

    Returns:
        True if sent successfully
    """
    approvers_text = ", ".join(f"@{a}" for a in approvers) if approvers else "any admin"
    message = (
        f"⏳ **Approval Required**\n\n"
        f"Issue: #{issue_number}\n"
        f"Step {step_num}: {step_name}\n"
        f"Agent: @{agent}\n"
        f"Approvers: {approvers_text}\n\n"
        f"Approve to let the workflow continue, or deny to stop it."
    )

    keyboard = (
        InlineKeyboard()
        .add_button(
            "✅ Approve",
            callback_data=_issue_callback("wfapprove", f"{issue_number}_{step_num}", project),
        )
        .add_button(
            "❌ Deny",
            callback_data=_issue_callback("wfdeny", f"{issue_number}_{step_num}", project),
        )
        .new_row()
        .add_button(
            "🔗 View Issue",
            url=build_issue_url(
                get_repo(project),
                issue_number,
                (
                    PROJECT_CONFIG.get(project)
                    if isinstance(PROJECT_CONFIG.get(project), dict)
                    else None
                ),
            ),
        )
    )

    return send_notification(message, keyboard=keyboard)


# ---------------------------------------------------------------------------
# EventBus-based alerting
# ---------------------------------------------------------------------------


def emit_alert(
    message: str,
    severity: str = "info",
    source: str = "",
    workflow_id: str | None = None,
    issue_number: str | None = None,
    project_key: str | None = None,
    actions: Sequence[dict[str, Any]] | None = None,
) -> bool:
    """Emit a :class:`SystemAlert` on the shared EventBus.

    All attached event-handler plugins (Telegram, Discord, …) will receive
    the alert.  If the EventBus has no ``system.alert`` subscribers, the
    function falls back to the direct Telegram plugin so that alerts are
    never silently swallowed.

    Args:
        message:     Plain-text alert body.
        severity:    ``info``, ``warning``, ``error``, or ``critical``.
        source:      Originating module name (informational).
        workflow_id: Optional workflow context.
        issue_number: Optional issue identifier used for action routing.
        project_key: Optional project key used for command routing.
        actions: Optional action descriptors (``label``, ``callback_data``, ``url``).

    Returns:
        ``True`` if the alert was delivered by at least one channel.
    """
    from nexus.core.events import AlertAction, SystemAlert

    try:
        from nexus.core.orchestration.nexus_core_helpers import get_event_bus

        bus = get_event_bus()
    except Exception:
        bus = None

    resolved_issue = str(issue_number or "").strip() or _extract_issue_number(message)
    resolved_project = str(project_key or "").strip()
    resolved_actions = _normalize_alert_actions(actions)
    if not resolved_actions:
        resolved_actions = _default_alert_actions(
            severity,
            resolved_issue,
            resolved_project or None,
        )

    # Try EventBus path first
    if bus and bus.subscriber_count("system.alert") > 0:
        event = SystemAlert(
            message=message,
            severity=severity,
            source=source,
            workflow_id=workflow_id,
            project_key=resolved_project,
            issue_number=resolved_issue,
            actions=[
                AlertAction(
                    label=action["label"],
                    callback_data=action["callback_data"],
                    url=action["url"],
                )
                for action in resolved_actions
            ],
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        try:
            if loop and loop.is_running():
                # Already in an async context — schedule as a task
                loop.create_task(bus.emit(event))
                logger.info(
                    "Alert EventBus emit scheduled via active loop "
                    "(path=async-task severity=%s source=%s workflow_id=%s issue=%s project=%s)",
                    severity,
                    source,
                    workflow_id or "",
                    resolved_issue,
                    resolved_project,
                )
            else:
                _emit_eventbus_sync(bus=bus, event=event)
                logger.info(
                    "Alert EventBus emit delivered via background bridge "
                    "(path=sync-bridge severity=%s source=%s workflow_id=%s issue=%s project=%s)",
                    severity,
                    source,
                    workflow_id or "",
                    resolved_issue,
                    resolved_project,
                )
            return True
        except Exception as exc:
            logger.warning("EventBus emit failed, falling back to direct send: %s", exc)

    # Fallback: direct Telegram plugin (guarantees alert delivery)
    plugin = _get_notification_plugin()
    normalized = _normalize_telegram_markdown(message, "Markdown")
    fallback_markup = _build_reply_markup(resolved_actions)
    if plugin:
        try:
            if hasattr(plugin, "send_message_sync"):
                icon = {
                    "info": "ℹ️",
                    "warning": "⚠️",
                    "error": "❌",
                    "critical": "🚨",
                }.get(str(severity or "info").lower(), "ℹ️")
                return bool(
                    plugin.send_message_sync(
                        f"{icon} {normalized}",
                        parse_mode="Markdown",
                        reply_markup=fallback_markup,
                    )
                )
            if hasattr(plugin, "send_alert_sync"):
                return bool(plugin.send_alert_sync(normalized, severity=severity))
        except Exception as exc:
            logger.warning("Direct Telegram alert failed: %s", exc)

    # Pyre-ignore because Pyre gets confused about slicing Local variable `message`
    msg_sample = str(message)[:120] if message else ""  # pyre-ignore[16, 6]
    logger.warning("No alert channel available: %s", msg_sample)
    return False
