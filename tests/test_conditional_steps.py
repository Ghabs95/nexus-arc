"""Tests for conditional step execution in WorkflowEngine."""

from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from nexus.adapters.storage.base import StorageBackend
from nexus.core.events import EventBus, NexusEvent, StepSkipped, WorkflowCompleted
from nexus.core.models import (
    Agent,
    AuditEvent,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.workflow import WorkflowEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_agent(name: str = "test_agent") -> Agent:
    return Agent(name=name, display_name=name, description="test", timeout=60, max_retries=1)


def make_step(
    step_num: int,
    name: str,
    condition: str | None = None,
    routes: list[dict[str, Any]] | None = None,
) -> WorkflowStep:
    return WorkflowStep(
        step_num=step_num,
        name=name,
        agent=make_agent(),
        prompt_template="do something",
        condition=condition,
        routes=routes or [],
    )


def make_workflow(steps: list[WorkflowStep]) -> Workflow:
    wf = Workflow(
        id="wf-test",
        name="Test Workflow",
        version="1.0",
        steps=steps,
        state=WorkflowState.RUNNING,
        current_step=1,
    )
    # Mark the first step as running
    if steps:
        steps[0].status = StepStatus.RUNNING
        steps[0].started_at = datetime.now(UTC)
    return wf


class InMemoryStorage(StorageBackend):
    """Minimal in-memory storage for tests."""

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

    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return 0


async def engine_with_workflow(workflow: Workflow, event_bus: EventBus | None = None) -> tuple:
    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, event_bus=event_bus)
    await storage.save_workflow(workflow)
    return engine, storage


# ---------------------------------------------------------------------------
# Unit tests for _evaluate_condition
# ---------------------------------------------------------------------------


class TestEvaluateCondition:
    def setup_method(self):
        self.engine = WorkflowEngine(storage=AsyncMock())

    def test_no_condition_returns_true(self):
        assert self.engine._evaluate_condition(None, {}) is True

    def test_empty_string_condition_returns_true(self):
        assert self.engine._evaluate_condition("", {}) is True

    def test_true_expression(self):
        assert self.engine._evaluate_condition("x == 1", {"x": 1}) is True

    def test_false_expression(self):
        assert self.engine._evaluate_condition("x == 2", {"x": 1}) is False

    def test_dict_key_access(self):
        ctx = {"result": {"tier": "high"}}
        assert self.engine._evaluate_condition("result['tier'] == 'high'", ctx) is True
        assert self.engine._evaluate_condition("result['tier'] == 'low'", ctx) is False

    def test_invalid_expression_defaults_to_true(self):
        # A broken expression should not crash; it should default to True
        assert self.engine._evaluate_condition("undefined_var == 1", {}) is True

    def test_truthy_string(self):
        assert self.engine._evaluate_condition("'non-empty'", {}) is True

    def test_falsy_zero(self):
        assert self.engine._evaluate_condition("0", {}) is False

    def test_yaml_style_boolean_literals_supported(self):
        assert self.engine._evaluate_condition("flag == true", {"flag": True}) is True
        assert self.engine._evaluate_condition("flag == false", {"flag": False}) is True


# ---------------------------------------------------------------------------
# Integration tests for complete_step with conditions
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_condition_passes_step_runs():
    """When condition evaluates to True, the next step should run normally."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "detailed_design", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2])
    engine, storage = await engine_with_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={"tier": "high"})

    assert result.current_step == 2
    assert result.steps[1].status == StepStatus.RUNNING
    assert result.state == WorkflowState.RUNNING


@pytest.mark.asyncio
async def test_condition_fails_step_skipped():
    """When condition evaluates to False, the step is skipped and workflow completes."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "detailed_design", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2])
    engine, storage = await engine_with_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert result.steps[1].status == StepStatus.SKIPPED
    assert result.state == WorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_chained_skips():
    """Multiple consecutive conditions that all fail should all be skipped."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "design", condition="result['tier'] == 'high'")
    step3 = make_step(3, "review", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2, step3])
    engine, storage = await engine_with_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert result.steps[1].status == StepStatus.SKIPPED
    assert result.steps[2].status == StepStatus.SKIPPED
    assert result.state == WorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_null_condition_step_always_runs():
    """A step with no condition should always execute."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement", condition=None)
    wf = make_workflow([step1, step2])
    engine, storage = await engine_with_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={})

    assert result.steps[1].status == StepStatus.RUNNING
    assert result.state == WorkflowState.RUNNING


@pytest.mark.asyncio
async def test_skipped_step_logged_in_audit():
    """Skipped steps must produce a STEP_SKIPPED audit entry."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "detailed_design", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2])
    engine, storage = await engine_with_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    audit_log = await storage.get_audit_log("wf-test")
    skip_events = [e for e in audit_log if e.event_type == "STEP_SKIPPED"]
    assert len(skip_events) == 1
    assert skip_events[0].data["step_name"] == "detailed_design"
    assert "condition" in skip_events[0].data
    assert "reason" in skip_events[0].data


@pytest.mark.asyncio
async def test_condition_skips_middle_step_runs_last():
    """Skip a middle step; the last unconditional step should still run."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "design", condition="result['tier'] == 'high'")
    step3 = make_step(3, "implement", condition=None)
    wf = make_workflow([step1, step2, step3])
    engine, storage = await engine_with_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert result.steps[1].status == StepStatus.SKIPPED
    assert result.steps[2].status == StepStatus.RUNNING
    assert result.state == WorkflowState.RUNNING


@pytest.mark.asyncio
async def test_router_condition_error_does_not_match_first_branch():
    """Missing route vars should not be treated as True for router branches."""
    step1 = make_step(1, "review")
    route = make_step(
        2,
        "route_review",
        routes=[
            {"when": "review_status == 'approved'", "then": "compliance"},
            {"default": "develop"},
        ],
    )
    compliance = make_step(3, "compliance")
    develop = make_step(4, "develop")
    wf = make_workflow([step1, route, compliance, develop])
    engine, _ = await engine_with_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={"next_agent": "developer"})

    assert result.steps[1].status == StepStatus.SKIPPED
    assert result.steps[2].status == StepStatus.PENDING
    assert result.steps[3].status == StepStatus.RUNNING
    assert result.current_step == 4


# ---------------------------------------------------------------------------
# StepSkipped event emission tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_condition_false_emits_step_skipped_event():
    """A condition-false skip must emit a StepSkipped event on the EventBus."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "detailed_design", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2])

    bus = EventBus()
    emitted: list[NexusEvent] = []
    bus.subscribe("step.skipped", lambda e: emitted.append(e))

    engine, _ = await engine_with_workflow(wf, event_bus=bus)
    await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert len(emitted) == 1
    skipped_event = emitted[0]
    assert isinstance(skipped_event, StepSkipped)
    assert skipped_event.step_name == "detailed_design"
    assert skipped_event.workflow_id == "wf-test"
    assert "Condition evaluated to False" in skipped_event.reason


@pytest.mark.asyncio
async def test_chained_condition_false_emits_multiple_step_skipped_events():
    """Each condition-false skip in a chain must emit its own StepSkipped event."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "design", condition="result['tier'] == 'high'")
    step3 = make_step(3, "review", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2, step3])

    bus = EventBus()
    emitted: list[NexusEvent] = []
    bus.subscribe("step.skipped", lambda e: emitted.append(e))

    engine, _ = await engine_with_workflow(wf, event_bus=bus)
    await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert len(emitted) == 2
    names = [e.step_name for e in emitted]  # type: ignore[attr-defined]
    assert "design" in names
    assert "review" in names


# ---------------------------------------------------------------------------
# WorkflowCompleted step-count tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_workflow_completed_counts_include_skipped_steps():
    """WorkflowCompleted event must carry correct step counts when steps are skipped."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "detailed_design", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2])

    bus = EventBus()
    completed_events: list[NexusEvent] = []
    bus.subscribe("workflow.completed", lambda e: completed_events.append(e))

    engine, _ = await engine_with_workflow(wf, event_bus=bus)
    await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert len(completed_events) == 1
    evt = completed_events[0]
    assert isinstance(evt, WorkflowCompleted)
    assert evt.total_steps == 2
    assert evt.completed_steps == 1
    assert evt.skipped_steps == 1
    assert evt.failed_steps == 0


@pytest.mark.asyncio
async def test_workflow_completed_counts_all_completed_steps():
    """WorkflowCompleted event counts must reflect all steps completing normally."""
    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement")
    wf = make_workflow([step1, step2])

    bus = EventBus()
    completed_events: list[NexusEvent] = []
    bus.subscribe("workflow.completed", lambda e: completed_events.append(e))

    engine, _ = await engine_with_workflow(wf, event_bus=bus)
    await engine.complete_step("wf-test", step_num=1, outputs={})
    await engine.complete_step("wf-test", step_num=2, outputs={})

    assert len(completed_events) == 1
    evt = completed_events[0]
    assert isinstance(evt, WorkflowCompleted)
    assert evt.total_steps == 2
    assert evt.completed_steps == 2
    assert evt.skipped_steps == 0
    assert evt.failed_steps == 0
