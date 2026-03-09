"""Tests for WebSocket state streaming (ADR-083).

Covers:
- SocketIO event bridge subscribes to EventBus step/workflow events
- step_status_changed payload matches ADR-083 schema
- workflow_completed payload matches ADR-083 schema
- mermaid_diagram is emitted after step events
- WorkflowStateEnginePlugin._get_engine() picks up event_bus from config
"""

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.adapters.storage.base import StorageBackend
from nexus.core.events import (
    EventBus,
    StepStarted,
    WorkflowCompleted,
    WorkflowFailed,
)
from nexus.core.models import Agent, AuditEvent, Workflow, WorkflowStep


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_agent(name: str = "test_agent") -> Agent:
    return Agent(name=name, display_name=name, description="test", timeout=60, max_retries=1)


def make_step(step_num: int, name: str) -> WorkflowStep:
    return WorkflowStep(
        step_num=step_num,
        name=name,
        agent=make_agent(name),
        prompt_template="do something",
    )


class InMemoryStorage(StorageBackend):
    def __init__(self) -> None:
        self._workflows: dict[str, Workflow] = {}
        self._audit: list[AuditEvent] = []

    async def save_workflow(self, workflow: Workflow) -> None:
        self._workflows[workflow.id] = workflow

    async def load_workflow(self, workflow_id: str) -> Workflow | None:
        return self._workflows.get(workflow_id)

    async def list_workflows(self, state=None, limit: int = 100):
        return list(self._workflows.values())

    async def delete_workflow(self, workflow_id: str) -> bool:
        return bool(self._workflows.pop(workflow_id, None))

    async def append_audit_event(self, event: AuditEvent) -> None:
        self._audit.append(event)

    async def get_audit_log(self, workflow_id: str, since=None) -> list[AuditEvent]:
        return [e for e in self._audit if e.workflow_id == workflow_id]

    async def save_agent_metadata(
        self, workflow_id: str, agent_name: str, metadata: dict[str, Any]
    ) -> None:
        pass

    async def load_agent_metadata(
        self, workflow_id: str, agent_name: str
    ) -> dict[str, Any] | None:
        return None


# ---------------------------------------------------------------------------
# Tests: SocketIO bridge payload format (ADR-083 compliance)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_step_status_changed_payload_on_step_started():
    """step_status_changed carries 'running' status when step.started fires."""
    emitted: list[tuple[str, dict]] = []

    with patch(
        "nexus.core.state_manager._socketio_emitter",
        side_effect=lambda evt, data: emitted.append((evt, data)),
    ):
        from nexus.core.state_manager import HostStateManager

        HostStateManager.emit_step_status_changed(
            issue="42",
            workflow_id="nexus-42-full",
            step_id="develop",
            agent_type="developer",
            status="running",
        )

    assert len(emitted) == 1
    event_name, payload = emitted[0]
    assert event_name == "step_status_changed"
    assert payload["issue"] == "42"
    assert payload["workflow_id"] == "nexus-42-full"
    assert payload["step_id"] == "develop"
    assert payload["agent_type"] == "developer"
    assert payload["status"] == "running"
    assert "timestamp" in payload


@pytest.mark.asyncio
async def test_step_status_changed_payload_on_step_completed():
    """step_status_changed carries 'done' status when step.completed fires."""
    emitted: list[tuple[str, dict]] = []

    with patch(
        "nexus.core.state_manager._socketio_emitter",
        side_effect=lambda evt, data: emitted.append((evt, data)),
    ):
        from nexus.core.state_manager import HostStateManager

        HostStateManager.emit_step_status_changed(
            issue="42",
            workflow_id="nexus-42-full",
            step_id="develop",
            agent_type="developer",
            status="done",
        )

    assert emitted[0][1]["status"] == "done"


@pytest.mark.asyncio
async def test_workflow_completed_payload_format():
    """workflow_completed payload matches ADR-083 schema."""
    emitted: list[tuple[str, dict]] = []

    with patch(
        "nexus.core.state_manager._socketio_emitter",
        side_effect=lambda evt, data: emitted.append((evt, data)),
    ):
        from nexus.core.state_manager import HostStateManager

        HostStateManager.emit_transition(
            "workflow_completed",
            {
                "issue": "42",
                "workflow_id": "nexus-42-full",
                "status": "success",
                "summary": "Workflow success: 3/3 steps completed",
                "total_steps": 3,
                "completed_steps": 3,
                "failed_steps": 0,
                "skipped_steps": 0,
                "timestamp": 1234567890.0,
            },
        )

    assert len(emitted) == 1
    event_name, payload = emitted[0]
    assert event_name == "workflow_completed"
    assert payload["status"] == "success"
    assert payload["total_steps"] == 3
    assert "summary" in payload


@pytest.mark.asyncio
async def test_emit_is_noop_when_no_socketio_emitter():
    """emit_transition is safe (no-op) when SocketIO emitter is not registered."""
    with patch("nexus.core.state_manager._socketio_emitter", None):
        from nexus.core.state_manager import HostStateManager

        # Must not raise
        HostStateManager.emit_transition("step_status_changed", {"issue": "1"})
        HostStateManager.emit_step_status_changed(
            issue="1",
            workflow_id="wf-1",
            step_id="step",
            agent_type="agent",
            status="running",
        )


# ---------------------------------------------------------------------------
# Tests: EventBus bridge handler
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_socketio_bridge_emits_step_status_changed_for_step_started():
    """Bridge handler translates step.started → step_status_changed(running)."""
    bus = EventBus()
    emitted_events: list[tuple[str, dict]] = []

    # Wire up bridge with mocked SocketIO emitter
    with (
        patch(
            "nexus.core.state_manager._socketio_emitter",
            side_effect=lambda evt, data: emitted_events.append((evt, data)),
        ),
        patch(
            "nexus.core.integrations.workflow_state_factory.get_workflow_state"
        ) as mock_wf_state,
        patch(
            "nexus.core.orchestration.nexus_core_helpers.get_workflow_engine"
        ) as mock_engine_factory,
    ):
        mock_wf_state.return_value.load_all_mappings.return_value = {"42": "nexus-42-full"}

        # Prevent mermaid diagram emission for this test
        mock_engine = MagicMock()
        mock_engine.get_workflow = AsyncMock(return_value=None)
        mock_engine_factory.return_value = mock_engine

        from nexus.core.orchestration.nexus_core_helpers import _setup_socketio_event_bridge

        await _setup_socketio_event_bridge(bus)

        await bus.emit(
            StepStarted(
                workflow_id="nexus-42-full",
                step_num=1,
                step_name="develop",
                agent_type="developer",
            )
        )

    step_events = [(n, d) for n, d in emitted_events if n == "step_status_changed"]
    assert len(step_events) == 1
    _, payload = step_events[0]
    assert payload["issue"] == "42"
    assert payload["workflow_id"] == "nexus-42-full"
    assert payload["step_id"] == "develop"
    assert payload["agent_type"] == "developer"
    assert payload["status"] == "running"


@pytest.mark.asyncio
async def test_socketio_bridge_emits_workflow_completed():
    """Bridge handler translates workflow.completed → workflow_completed(success)."""
    bus = EventBus()
    emitted_events: list[tuple[str, dict]] = []

    with (
        patch(
            "nexus.core.state_manager._socketio_emitter",
            side_effect=lambda evt, data: emitted_events.append((evt, data)),
        ),
        patch(
            "nexus.core.integrations.workflow_state_factory.get_workflow_state"
        ) as mock_wf_state,
    ):
        mock_wf_state.return_value.load_all_mappings.return_value = {"42": "nexus-42-full"}

        from nexus.core.orchestration.nexus_core_helpers import _setup_socketio_event_bridge

        await _setup_socketio_event_bridge(bus)

        await bus.emit(
            WorkflowCompleted(
                workflow_id="nexus-42-full",
                total_steps=3,
                completed_steps=3,
                failed_steps=0,
                skipped_steps=0,
            )
        )

    wc_events = [(n, d) for n, d in emitted_events if n == "workflow_completed"]
    assert len(wc_events) == 1
    _, payload = wc_events[0]
    assert payload["issue"] == "42"
    assert payload["status"] == "success"
    assert payload["total_steps"] == 3
    assert "summary" in payload


@pytest.mark.asyncio
async def test_socketio_bridge_emits_workflow_failed():
    """Bridge handler translates workflow.failed → workflow_completed(failed)."""
    bus = EventBus()
    emitted_events: list[tuple[str, dict]] = []

    with (
        patch(
            "nexus.core.state_manager._socketio_emitter",
            side_effect=lambda evt, data: emitted_events.append((evt, data)),
        ),
        patch(
            "nexus.core.integrations.workflow_state_factory.get_workflow_state"
        ) as mock_wf_state,
    ):
        mock_wf_state.return_value.load_all_mappings.return_value = {"42": "nexus-42-full"}

        from nexus.core.orchestration.nexus_core_helpers import _setup_socketio_event_bridge

        await _setup_socketio_event_bridge(bus)

        await bus.emit(
            WorkflowFailed(
                workflow_id="nexus-42-full",
                error="Step timed out",
            )
        )

    wc_events = [(n, d) for n, d in emitted_events if n == "workflow_completed"]
    assert len(wc_events) == 1
    _, payload = wc_events[0]
    assert payload["status"] == "failed"


@pytest.mark.asyncio
async def test_socketio_bridge_skips_unknown_workflow_id():
    """Bridge is silent when workflow_id has no mapped issue."""
    bus = EventBus()
    emitted_events: list[tuple[str, dict]] = []

    with (
        patch(
            "nexus.core.state_manager._socketio_emitter",
            side_effect=lambda evt, data: emitted_events.append((evt, data)),
        ),
        patch(
            "nexus.core.integrations.workflow_state_factory.get_workflow_state"
        ) as mock_wf_state,
    ):
        mock_wf_state.return_value.load_all_mappings.return_value = {}  # empty mapping

        from nexus.core.orchestration.nexus_core_helpers import _setup_socketio_event_bridge

        await _setup_socketio_event_bridge(bus)

        await bus.emit(
            StepStarted(
                workflow_id="nexus-99-full",
                step_num=1,
                step_name="develop",
                agent_type="developer",
            )
        )

    assert len(emitted_events) == 0


# ---------------------------------------------------------------------------
# Tests: WorkflowStateEnginePlugin event_bus wiring
# ---------------------------------------------------------------------------


def test_workflow_state_engine_plugin_uses_event_bus_from_config():
    """_get_engine() passes EventBus from config to WorkflowEngine."""
    from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin

    mock_bus = MagicMock()
    bus_factory = MagicMock(return_value=mock_bus)

    plugin = WorkflowStateEnginePlugin(
        config={
            "storage_dir": "/tmp/test-nexus-storage",
            "event_bus": bus_factory,
        }
    )

    with patch(
        "nexus.plugins.builtin.workflow_state_engine_plugin.WorkflowEngine"
    ) as mock_wf_engine_cls:
        mock_wf_engine_cls.return_value = MagicMock()
        plugin._get_engine()

    mock_wf_engine_cls.assert_called_once()
    _, kwargs = mock_wf_engine_cls.call_args
    assert kwargs.get("event_bus") is mock_bus
    bus_factory.assert_called_once()


def test_workflow_state_engine_plugin_event_bus_none_when_not_configured():
    """_get_engine() passes event_bus=None when config omits it."""
    from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin

    plugin = WorkflowStateEnginePlugin(
        config={
            "storage_dir": "/tmp/test-nexus-storage",
        }
    )

    with patch(
        "nexus.plugins.builtin.workflow_state_engine_plugin.WorkflowEngine"
    ) as mock_wf_engine_cls:
        mock_wf_engine_cls.return_value = MagicMock()
        plugin._get_engine()

    _, kwargs = mock_wf_engine_cls.call_args
    assert kwargs.get("event_bus") is None


def test_workflow_state_engine_plugin_engine_factory_takes_priority():
    """engine_factory in config takes priority over event_bus wiring."""
    from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin

    custom_engine = MagicMock()
    factory = MagicMock(return_value=custom_engine)
    bus_factory = MagicMock()

    plugin = WorkflowStateEnginePlugin(
        config={
            "storage_dir": "/tmp/test-nexus-storage",
            "engine_factory": factory,
            "event_bus": bus_factory,
        }
    )

    result = plugin._get_engine()
    assert result is custom_engine
    factory.assert_called_once()
    bus_factory.assert_not_called()


# ---------------------------------------------------------------------------
# Tests: build_mermaid_diagram output
# ---------------------------------------------------------------------------


def test_build_mermaid_diagram_basic():
    """build_mermaid_diagram produces a valid flowchart string."""
    from nexus.core.mermaid_render_service import build_mermaid_diagram

    steps = [
        {"name": "triage", "status": "completed", "agent": {"name": "triage"}},
        {"name": "develop", "status": "running", "agent": {"name": "developer"}},
        {"name": "review", "status": "pending", "agent": {"name": "reviewer"}},
    ]
    diagram = build_mermaid_diagram(steps, "117")

    assert "flowchart TD" in diagram
    assert 'Issue #117' in diagram
    assert "completed" in diagram
    assert "running" in diagram
    assert "pending" in diagram


def test_build_mermaid_diagram_empty_steps():
    """build_mermaid_diagram handles empty steps without error."""
    from nexus.core.mermaid_render_service import build_mermaid_diagram

    diagram = build_mermaid_diagram([], "1")
    assert "flowchart TD" in diagram
