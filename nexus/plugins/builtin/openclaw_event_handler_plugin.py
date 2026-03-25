"""OpenClaw Event Handler Plugin.

Subscribes to the EventBus and delivers Nexus workflow notifications to an
OpenClaw agent session (e.g. a Telegram chat) via the OpenClaw hooks bridge.

Activated automatically when ``NEXUS_RUNTIME_MODE=openclaw`` and
``NEXUS_OPENCLAW_BRIDGE_TOKEN`` are set in the environment.

Uses the existing :class:`OpenClawNotificationChannel` adapter.
"""

import logging
from typing import Any

from nexus.adapters.notifications.base import Message
from nexus.core.events import (
    AgentTimeout,
    EventBus,
    NexusEvent,
    StepCompleted,
    StepFailed,
    SystemAlert,
    WorkflowCancelled,
    WorkflowCompleted,
    WorkflowFailed,
    WorkflowStarted,
)
from nexus.core.models import Severity
from nexus.plugins.base import PluginHealthStatus

logger = logging.getLogger(__name__)

_EVENT_FORMAT: dict[str, tuple[str, str]] = {
    "workflow.started": ("🚀", "Workflow Started"),
    "workflow.completed": ("✅", "Workflow Completed"),
    "workflow.failed": ("❌", "Workflow Failed"),
    "workflow.cancelled": ("🛑", "Workflow Cancelled"),
    "step.completed": ("✔️", "Step Completed"),
    "step.failed": ("⚠️", "Step Failed"),
    "agent.timeout": ("⏰", "Agent Timeout"),
    "system.alert": ("🔔", "Alert"),
}

_SEVERITY_MAP: dict[str, Severity] = {
    "info": Severity.INFO,
    "warning": Severity.WARNING,
    "error": Severity.ERROR,
    "critical": Severity.ERROR,
}


class OpenClawEventHandler:
    """Sends workflow event notifications to OpenClaw via the hooks bridge.

    Wraps :class:`OpenClawNotificationChannel` and subscribes to the EventBus
    for reactive dispatch.  Implements ``PluginLifecycle`` for health checks.
    """

    def __init__(self, config: dict[str, Any]):
        from nexus.adapters.notifications.openclaw import OpenClawNotificationChannel

        self._channel = OpenClawNotificationChannel(
            bridge_url=config.get("bridge_url"),
            auth_token=config.get("auth_token"),
            sender_id=config.get("sender_id"),
            channel=config.get("channel"),
        )
        self._subscriptions: list[str] = []
        self._last_send_ok: bool = True

    # -- EventBus wiring ---------------------------------------------------

    def attach(self, bus: EventBus) -> None:
        """Subscribe to relevant events on *bus*."""
        self._subscriptions.append(bus.subscribe("workflow.started", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.completed", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.failed", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.cancelled", self._handle))
        self._subscriptions.append(bus.subscribe("step.completed", self._handle))
        self._subscriptions.append(bus.subscribe("step.failed", self._handle))
        self._subscriptions.append(bus.subscribe("agent.timeout", self._handle))
        self._subscriptions.append(bus.subscribe("system.alert", self._handle))
        logger.info(
            "OpenClawEventHandler attached to EventBus (%d subscriptions)",
            len(self._subscriptions),
        )

    def detach(self, bus: EventBus) -> None:
        """Unsubscribe all subscriptions from *bus*."""
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    # -- Handler -----------------------------------------------------------

    async def _handle(self, event: NexusEvent) -> None:
        emoji, label = _EVENT_FORMAT.get(event.event_type, ("📌", event.event_type))
        lines: list[str] = []
        severity = Severity.INFO

        if isinstance(event, SystemAlert):
            severity = _SEVERITY_MAP.get(str(event.severity or "info").lower(), Severity.INFO)
            lines.append(event.message)
            if event.source:
                lines.append(f"Source: {event.source}")
            if event.workflow_id:
                lines.append(f"Workflow: `{event.workflow_id}`")
            if event.project_key:
                lines.append(f"Project: `{event.project_key}`")
            if event.issue_number:
                lines.append(f"Issue: `#{event.issue_number}`")
        else:
            if event.workflow_id:
                lines.append(f"Workflow: `{event.workflow_id}`")

            if isinstance(event, WorkflowStarted):
                severity = Severity.INFO
            elif isinstance(event, WorkflowCompleted):
                severity = Severity.INFO
            elif isinstance(event, WorkflowFailed):
                lines.append(f"Error: {event.error}")
                severity = Severity.ERROR
            elif isinstance(event, WorkflowCancelled):
                severity = Severity.WARNING
            elif isinstance(event, StepCompleted):
                lines.append(f"Step: {event.step_name} (#{event.step_num})")
                severity = Severity.INFO
            elif isinstance(event, StepFailed):
                lines.append(f"Step: {event.step_name} (#{event.step_num})")
                lines.append(f"Error: {event.error}")
                severity = Severity.WARNING
            elif isinstance(event, AgentTimeout):
                lines.append(f"Agent: {event.agent_name}")
                if event.pid:
                    lines.append(f"PID: {event.pid}")
                severity = Severity.WARNING

            if event.data:
                for k, v in event.data.items():
                    lines.append(f"{k}: `{v}`")

        description = "\n".join(lines) if lines else ""
        text = f"{emoji} **{label}**"
        if description:
            text = f"{text}\n{description}"

        try:
            await self._channel.send_alert(text, severity)
            self._last_send_ok = True
        except Exception as exc:
            self._last_send_ok = False
            logger.error("OpenClawEventHandler send failed: %s", exc)

    # -- PluginLifecycle ---------------------------------------------------

    async def on_load(self, registry: Any) -> None:
        logger.info("OpenClawEventHandler loaded")

    async def on_unload(self) -> None:
        await self._channel.aclose()
        logger.info("OpenClawEventHandler unloaded")

    async def health_check(self) -> PluginHealthStatus:
        return PluginHealthStatus(
            healthy=self._last_send_ok,
            name="openclaw-event-handler",
            details="Last send OK" if self._last_send_ok else "Last send failed",
        )


# -- Plugin registration ---------------------------------------------------


def register_plugins(registry: Any) -> None:
    """Register OpenClaw event handler plugin."""
    from nexus.plugins.base import PluginKind

    registry.register_factory(
        kind=PluginKind.EVENT_HANDLER,
        name="openclaw-event-handler",
        version="1.0.0",
        factory=lambda config: OpenClawEventHandler(config),
        description="Delivers Nexus workflow notifications to OpenClaw via the hooks bridge",
    )
