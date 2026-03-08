"""Event Bus for Nexus Core.

Provides a lightweight, async-first publish/subscribe system for decoupling
framework components. Any module can emit or subscribe to typed events
without direct dependencies on other modules.

Usage::

    bus = EventBus()

    # Subscribe
    sub_id = bus.subscribe("workflow.completed", my_handler)

    # Emit
    await bus.emit(WorkflowCompleted(workflow_id="abc-123"))

    # Unsubscribe
    bus.unsubscribe(sub_id)
"""

import asyncio
import fnmatch
import logging
import threading
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Event Types
# ---------------------------------------------------------------------------


@dataclass
class NexusEvent:
    """Base event for all Nexus framework events."""

    event_type: str
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    workflow_id: str | None = None
    data: dict[str, Any] = field(default_factory=dict)


@dataclass
class WorkflowStarted(NexusEvent):
    """Emitted when a workflow begins execution."""

    event_type: str = "workflow.started"


@dataclass
class WorkflowCompleted(NexusEvent):
    """Emitted when a workflow finishes all steps successfully."""

    event_type: str = "workflow.completed"
    total_steps: int = 0
    completed_steps: int = 0
    failed_steps: int = 0
    skipped_steps: int = 0


@dataclass
class WorkflowFailed(NexusEvent):
    """Emitted when a workflow terminates due to an unrecoverable error."""

    event_type: str = "workflow.failed"
    error: str = ""


@dataclass
class WorkflowPaused(NexusEvent):
    """Emitted when a workflow is paused."""

    event_type: str = "workflow.paused"


@dataclass
class WorkflowCancelled(NexusEvent):
    """Emitted when a workflow is cancelled."""

    event_type: str = "workflow.cancelled"


@dataclass
class StepStarted(NexusEvent):
    """Emitted when a workflow step begins execution."""

    event_type: str = "step.started"
    step_num: int = 0
    step_name: str = ""
    agent_type: str = ""


@dataclass
class StepCompleted(NexusEvent):
    """Emitted when a workflow step finishes."""

    event_type: str = "step.completed"
    step_num: int = 0
    step_name: str = ""
    agent_type: str = ""
    outputs: dict[str, Any] = field(default_factory=dict)


@dataclass
class StepFailed(NexusEvent):
    """Emitted when a step fails (may be retried)."""

    event_type: str = "step.failed"
    step_num: int = 0
    step_name: str = ""
    agent_type: str = ""
    error: str = ""


@dataclass
class StepSkipped(NexusEvent):
    """Emitted when a workflow step is skipped (condition evaluated False or router bypass)."""

    event_type: str = "step.skipped"
    step_num: int = 0
    step_name: str = ""
    agent_type: str = ""
    reason: str = ""


@dataclass
class AgentLaunched(NexusEvent):
    """Emitted when an agent process is started."""

    event_type: str = "agent.launched"
    agent_name: str = ""


@dataclass
class AgentTimeout(NexusEvent):
    """Emitted when an agent exceeds its execution timeout."""

    event_type: str = "agent.timeout"
    agent_name: str = ""
    pid: int | None = None


@dataclass
class AgentRetry(NexusEvent):
    """Emitted when an agent is scheduled for retry."""

    event_type: str = "agent.retry"
    agent_name: str = ""
    attempt: int = 0


@dataclass
class AuditLogged(NexusEvent):
    """Emitted when an audit event is recorded."""

    event_type: str = "audit.logged"
    audit_event_type: str = ""


@dataclass
class AlertAction:
    """Interactive action attached to a :class:`SystemAlert`."""

    label: str = ""
    callback_data: str = ""
    url: str = ""


@dataclass
class SystemAlert(NexusEvent):
    """Emitted for general-purpose operational alerts.

    Replaces direct ``send_telegram_alert()`` calls — any attached
    notification handler (Telegram, Discord, Loki …) will pick it up.
    """

    event_type: str = "system.alert"
    message: str = ""
    severity: str = "info"  # info, warning, error, critical
    source: str = ""  # originating module name
    project_key: str = ""
    issue_number: str = ""
    actions: list[AlertAction] = field(default_factory=list)


@dataclass
class ApprovalRequired(NexusEvent):
    """Emitted when a workflow step is blocked waiting for approval."""

    event_type: str = "workflow.approval_required"
    step_num: int = 0
    step_name: str = ""
    agent: str = ""
    approvers: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Handler Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for event handler callables."""

    async def __call__(self, event: NexusEvent) -> None: ...


# ---------------------------------------------------------------------------
# Subscription
# ---------------------------------------------------------------------------


@dataclass
class _Subscription:
    """Internal subscription record."""

    id: str
    event_type: str
    handler: Any  # Callable[[NexusEvent], Awaitable[None]]
    is_pattern: bool = False


# ---------------------------------------------------------------------------
# Event Bus
# ---------------------------------------------------------------------------


class EventBus:
    """Async-first publish/subscribe event dispatcher.

    Thread-safe subscription management. Events are dispatched to all
    matching handlers concurrently using ``asyncio.gather``.
    """

    def __init__(self) -> None:
        self._subscriptions: dict[str, _Subscription] = {}
        self._lock = threading.Lock()

    def subscribe(self, event_type: str, handler: Any) -> str:
        """Subscribe a handler to an exact event type.

        Args:
            event_type: The event type string to listen for (e.g., ``"workflow.completed"``).
            handler: An async callable that accepts a ``NexusEvent``.

        Returns:
            A unique subscription ID that can be used with :meth:`unsubscribe`.
        """
        sub_id = str(uuid.uuid4())
        sub = _Subscription(id=sub_id, event_type=event_type, handler=handler)
        with self._lock:
            self._subscriptions[sub_id] = sub
        return sub_id

    def subscribe_pattern(self, pattern: str, handler: Any) -> str:
        """Subscribe a handler using a glob pattern.

        Example patterns:
            - ``"workflow.*"`` matches ``workflow.started``, ``workflow.completed``, etc.
            - ``"agent.*"`` matches ``agent.launched``, ``agent.timeout``, etc.
            - ``"*"`` matches everything.

        Args:
            pattern: A glob pattern to match against event types.
            handler: An async callable that accepts a ``NexusEvent``.

        Returns:
            A unique subscription ID.
        """
        sub_id = str(uuid.uuid4())
        sub = _Subscription(id=sub_id, event_type=pattern, handler=handler, is_pattern=True)
        with self._lock:
            self._subscriptions[sub_id] = sub
        return sub_id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription.

        Args:
            subscription_id: The ID returned by :meth:`subscribe` or :meth:`subscribe_pattern`.

        Returns:
            ``True`` if the subscription was found and removed, ``False`` otherwise.
        """
        with self._lock:
            return self._subscriptions.pop(subscription_id, None) is not None

    async def emit(self, event: NexusEvent) -> None:
        """Emit an event to all matching subscribers.

        Handlers are executed concurrently via ``asyncio.gather``.
        Individual handler failures are logged but do not prevent delivery
        to other subscribers.

        Args:
            event: The event to dispatch.
        """
        with self._lock:
            subs = list(self._subscriptions.values())

        matching_handlers = []
        for sub in subs:
            if sub.is_pattern:
                if fnmatch.fnmatch(event.event_type, sub.event_type):
                    matching_handlers.append(sub.handler)
            else:
                if sub.event_type == event.event_type:
                    matching_handlers.append(sub.handler)

        if not matching_handlers:
            return

        results = await asyncio.gather(
            *(self._safe_call(handler, event) for handler in matching_handlers),
            return_exceptions=True,
        )

        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "Event handler error for %s: %s",
                    event.event_type,
                    result,
                    exc_info=result,
                )

    @staticmethod
    async def _safe_call(handler: Any, event: NexusEvent) -> None:
        """Call handler, wrapping sync callables if necessary."""
        try:
            result = handler(event)
            if asyncio.iscoroutine(result):
                await result
        except Exception:
            raise

    def subscriber_count(self, event_type: str | None = None) -> int:
        """Return the number of active subscriptions.

        Args:
            event_type: If provided, count only subscriptions for this exact type.
        """
        with self._lock:
            if event_type is None:
                return len(self._subscriptions)
            return sum(1 for sub in self._subscriptions.values() if sub.event_type == event_type)

    def clear(self) -> None:
        """Remove all subscriptions."""
        with self._lock:
            self._subscriptions.clear()
