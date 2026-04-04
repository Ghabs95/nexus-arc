"""OpenClaw Event Handler Plugin.

Subscribes to the EventBus and delivers Nexus workflow notifications to an
OpenClaw agent session (e.g. a Telegram chat) via the OpenClaw hooks bridge.

Activated automatically when ``NEXUS_RUNTIME_MODE=openclaw`` and
``NEXUS_OPENCLAW_BRIDGE_TOKEN`` are set in the environment.

Uses the existing :class:`OpenClawNotificationChannel` adapter.
"""

import logging
from typing import Any

from nexus.core.events import (
    AgentTimeout,
    ApprovalRequired,
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
from nexus.plugins.base import PluginHealthStatus

logger = logging.getLogger(__name__)


def _safe_str(value: Any) -> str:
    return str(value or "").strip()


def _event_severity(event: NexusEvent) -> str:
    if isinstance(event, SystemAlert):
        return _safe_str(getattr(event, "severity", "info")).lower() or "info"
    if isinstance(event, (WorkflowFailed, StepFailed)):
        return "error"
    if isinstance(event, (WorkflowCancelled, AgentTimeout, ApprovalRequired)):
        return "warning"
    return "info"


def _event_summary(event: NexusEvent) -> str:
    if isinstance(event, WorkflowStarted):
        return "Workflow execution started in Nexus."
    if isinstance(event, WorkflowCompleted):
        return "Workflow completed successfully in Nexus."
    if isinstance(event, WorkflowFailed):
        return _safe_str(event.error) or "Workflow failed in Nexus."
    if isinstance(event, WorkflowCancelled):
        return "Workflow was cancelled."
    if isinstance(event, StepCompleted):
        return f"{event.step_name or 'step'} completed; workflow advanced."
    if isinstance(event, StepFailed):
        return _safe_str(event.error) or f"{event.step_name or 'step'} failed."
    if isinstance(event, AgentTimeout):
        agent = _safe_str(event.agent_name) or "agent"
        return f"{agent} timed out; manual attention may be required."
    if isinstance(event, ApprovalRequired):
        step_name = _safe_str(event.step_name) or "workflow step"
        return f"Human approval is required for {step_name}."
    if isinstance(event, SystemAlert):
        return _safe_str(event.message)
    return event.event_type.replace(".", " ")


def _event_findings(event: NexusEvent) -> list[str]:
    findings: list[str] = []
    if isinstance(event, StepFailed) and _safe_str(event.error):
        findings.append(_safe_str(event.error))
    if isinstance(event, WorkflowFailed) and _safe_str(event.error):
        findings.append(_safe_str(event.error))
    if isinstance(event, SystemAlert) and _safe_str(event.source):
        findings.append(f"source={_safe_str(event.source)}")
    if isinstance(event, ApprovalRequired) and event.approvers:
        findings.append("approvers=" + ", ".join(str(a) for a in event.approvers if str(a).strip()))
    return findings[:3]


def _suggested_actions(event: NexusEvent) -> list[str]:
    if isinstance(event, WorkflowCompleted):
        return ["show_status", "open_issue"]
    if isinstance(event, ApprovalRequired):
        return ["approve", "reject", "show_logs"]
    if isinstance(event, (WorkflowFailed, StepFailed, AgentTimeout, WorkflowCancelled)):
        return ["show_status", "show_logs", "continue"]
    if isinstance(event, SystemAlert):
        sev = _safe_str(getattr(event, "severity", "info")).lower()
        if sev in {"warning", "error", "critical"}:
            return ["show_status", "show_logs"]
    return ["show_status"]


class OpenClawEventHandler:
    """Sends workflow event notifications to OpenClaw via the hooks bridge."""

    def __init__(self, config: dict[str, Any]):
        from nexus.adapters.notifications.openclaw import OpenClawNotificationChannel

        self._channel = OpenClawNotificationChannel(
            bridge_url=config.get("bridge_url"),
            auth_token=config.get("auth_token"),
            sender_id=config.get("sender_id"),
            channel=config.get("channel"),
            session_key=config.get("session_key"),
        )
        self._subscriptions: list[str] = []
        self._last_send_ok: bool = True

    def attach(self, bus: EventBus) -> None:
        self._subscriptions.append(bus.subscribe("workflow.started", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.completed", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.failed", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.cancelled", self._handle))
        self._subscriptions.append(bus.subscribe("workflow.approval_required", self._handle))
        self._subscriptions.append(bus.subscribe("step.completed", self._handle))
        self._subscriptions.append(bus.subscribe("step.failed", self._handle))
        self._subscriptions.append(bus.subscribe("agent.timeout", self._handle))
        self._subscriptions.append(bus.subscribe("system.alert", self._handle))
        logger.info(
            "OpenClawEventHandler attached to EventBus (%d subscriptions)",
            len(self._subscriptions),
        )

    def detach(self, bus: EventBus) -> None:
        for sub_id in self._subscriptions:
            bus.unsubscribe(sub_id)
        self._subscriptions.clear()

    async def _handle(self, event: NexusEvent) -> None:
        data = dict(getattr(event, "data", {}) or {})
        workflow_id = _safe_str(getattr(event, "workflow_id", ""))
        project_key = _safe_str(
            data.get("project_key")
            or data.get("project")
            or (
                getattr(event, "project_key", "")
                if isinstance(event, SystemAlert)
                else ""
            )
        )
        issue_number = _safe_str(
            data.get("issue_number")
            or (
                getattr(event, "issue_number", "")
                if isinstance(event, SystemAlert)
                else ""
            )
        )
        repo = _safe_str(data.get("repo"))
        pr_number = _safe_str(data.get("pr_number"))
        pr_url = _safe_str(data.get("pr_url"))
        workflow_phase = _safe_str(data.get("workflow_phase") or data.get("state"))
        current_step = _safe_str(data.get("current_step"))
        blocked_reason = _safe_str(data.get("blocked_reason"))
        correlation_token = _safe_str(data.get("correlation_token"))

        step_num = 0
        step_name = ""
        step_id = _safe_str(data.get("step_id"))
        agent_type = _safe_str(data.get("agent_type"))

        if isinstance(event, (StepCompleted, StepFailed, ApprovalRequired)):
            step_num = int(getattr(event, "step_num", 0) or 0)
            step_name = _safe_str(getattr(event, "step_name", ""))
            current_step = current_step or step_name
        if isinstance(event, ApprovalRequired):
            agent_type = agent_type or _safe_str(getattr(event, "agent", ""))
        elif isinstance(event, (StepCompleted, StepFailed)):
            agent_type = agent_type or _safe_str(getattr(event, "agent_type", ""))
        elif isinstance(event, AgentTimeout):
            agent_type = agent_type or _safe_str(getattr(event, "agent_name", ""))

        try:
            if isinstance(event, SystemAlert) and not workflow_id:
                from nexus.adapters.notifications.base import Message
                from nexus.core.models import Severity

                severity_map = {
                    "info": Severity.INFO,
                    "warning": Severity.WARNING,
                    "error": Severity.ERROR,
                    "critical": Severity.CRITICAL,
                }
                ok = bool(await self._channel.send_message(
                    "",
                    Message(
                        text=_event_summary(event),
                        severity=severity_map.get(_event_severity(event), Severity.INFO),
                    ),
                ))
            else:
                ok = await self._channel.send_workflow_notification(
                    event_type=event.event_type,
                    workflow_id=workflow_id,
                    project_key=project_key,
                    repo=repo,
                    issue_number=issue_number,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    current_step=current_step,
                    step_id=step_id,
                    step_num=step_num,
                    step_name=step_name,
                    workflow_phase=workflow_phase,
                    agent_type=agent_type,
                    severity=_event_severity(event),
                    summary=_event_summary(event),
                    blocked_reason=blocked_reason,
                    key_findings=_event_findings(event),
                    suggested_actions=_suggested_actions(event),
                    correlation_token=correlation_token or None,
                )
            self._last_send_ok = bool(ok)
        except Exception as exc:
            self._last_send_ok = False
            logger.error("OpenClawEventHandler send failed: %s", exc)

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


def register_plugins(registry: Any) -> None:
    from nexus.plugins.base import PluginKind

    registry.register_factory(
        kind=PluginKind.EVENT_HANDLER,
        name="openclaw-event-handler",
        version="1.0.0",
        factory=lambda config: OpenClawEventHandler(config),
        description="Delivers Nexus workflow notifications to OpenClaw via the hooks bridge",
    )
