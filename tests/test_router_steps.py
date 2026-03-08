"""Tests for WorkflowEngine router step support (goto/loop behavior)."""

from datetime import UTC, datetime
from typing import Any

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
from nexus.core.workflow import _MAX_LOOP_ITERATIONS, WorkflowEngine


# ---------------------------------------------------------------------------
# Minimal in-memory storage reused from test_conditional_steps pattern
# ---------------------------------------------------------------------------


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

    async def get_agent_metadata(self, workflow_id: str, agent_name: str) -> dict[str, Any] | None:
        return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return 0


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _agent() -> Agent:
    return Agent(
        name="test-agent", display_name="Test Agent", description="test", timeout=60, max_retries=0
    )


def _make_step(step_num: int, name: str, routes=None, condition=None) -> WorkflowStep:
    return WorkflowStep(
        step_num=step_num,
        name=name,
        agent=_agent(),
        prompt_template="do {issue_url}",
        routes=routes or [],
        condition=condition,
    )


async def _engine_with_workflow(workflow: Workflow, event_bus: EventBus | None = None) -> tuple:
    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, event_bus=event_bus)
    await storage.save_workflow(workflow)
    return engine, storage


def _make_workflow(steps: list[WorkflowStep]) -> Workflow:
    wf = Workflow(
        id="wf-test",
        name="Test Workflow",
        version="1.0",
        steps=steps,
        state=WorkflowState.RUNNING,
        current_step=1,
    )
    steps[0].status = StepStatus.RUNNING
    steps[0].started_at = datetime.now(UTC)
    return wf


# ---------------------------------------------------------------------------
# Test 1: Router routes to the correct branch when condition matches
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_routes_approved_to_deployer():
    """Router 'approved' condition should activate the deploy step."""
    develop = _make_step(1, "develop")
    review = _make_step(2, "review")
    router = _make_step(
        3,
        "route_review",
        routes=[
            {"when": "review['decision'] == 'approved'", "goto": "deploy"},
            {"default": True, "goto": "develop"},
        ],
    )
    deploy = _make_step(4, "deploy")

    wf = _make_workflow([develop, review, router, deploy])
    engine, storage = await _engine_with_workflow(wf)

    # develop → COMPLETED, review activated
    await engine.complete_step("wf-test", step_num=1, outputs={"pr": "1"})
    assert wf.steps[1].status == StepStatus.RUNNING

    # review → COMPLETED with approved
    await engine.complete_step("wf-test", step_num=2, outputs={"decision": "approved"})

    assert deploy.status == StepStatus.RUNNING, "deploy should be activated on approval"
    assert wf.current_step == deploy.step_num
    assert wf.state == WorkflowState.RUNNING


@pytest.mark.asyncio
async def test_router_default_routes_to_develop():
    """Router default route should activate develop when no when-clause matches."""
    develop = _make_step(1, "develop")
    review = _make_step(2, "review")
    router = _make_step(
        3,
        "route_review",
        routes=[
            {"when": "review['decision'] == 'approved'", "goto": "deploy"},
            {"default": True, "goto": "develop"},
        ],
    )
    deploy = _make_step(4, "deploy")

    wf = _make_workflow([develop, review, router, deploy])
    engine, _ = await _engine_with_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"pr": "1"})
    await engine.complete_step("wf-test", step_num=2, outputs={"decision": "changes_requested"})

    assert (
        develop.status == StepStatus.RUNNING
    ), "develop should be re-activated on changes_requested"
    assert wf.current_step == develop.step_num
    assert develop.iteration == 1


# ---------------------------------------------------------------------------
# Test 2: Full review/develop loop — approved on second pass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_review_develop_loop_two_passes():
    """develop → review (changes) → develop → review (approved) → deploy."""
    develop = _make_step(1, "develop")
    review = _make_step(2, "review")
    router = _make_step(
        3,
        "route_review",
        routes=[
            {"when": "review['decision'] == 'approved'", "goto": "deploy"},
            {"default": True, "goto": "develop"},
        ],
    )
    deploy = _make_step(4, "deploy")

    wf = _make_workflow([develop, review, router, deploy])
    engine, _ = await _engine_with_workflow(wf)

    # Pass 1: develop completes → review activated
    await engine.complete_step("wf-test", step_num=1, outputs={"pr": "1"})
    assert review.status == StepStatus.RUNNING

    # Pass 1: review → changes_requested → develop re-activated
    await engine.complete_step("wf-test", step_num=2, outputs={"decision": "changes_requested"})
    assert develop.status == StepStatus.RUNNING
    assert develop.iteration == 1

    # Pass 2: develop completes → review activated again
    await engine.complete_step("wf-test", step_num=1, outputs={"pr": "1"})
    assert review.status == StepStatus.RUNNING
    # review is re-activated sequentially (not via a router goto), so iteration stays 0

    # Pass 2: review → approved → deploy activated
    await engine.complete_step("wf-test", step_num=2, outputs={"decision": "approved"})
    assert deploy.status == StepStatus.RUNNING
    assert wf.state == WorkflowState.RUNNING


# ---------------------------------------------------------------------------
# Test 3: Max iteration guard fails the workflow
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_max_loop_iteration_fails_workflow():
    """After _MAX_LOOP_ITERATIONS re-activations, the workflow should FAIL."""
    develop = _make_step(1, "develop")
    review = _make_step(2, "review")
    router = _make_step(
        3,
        "route_review",
        routes=[
            {"when": "review['decision'] == 'approved'", "goto": "deploy"},
            {"default": True, "goto": "develop"},
        ],
    )
    deploy = _make_step(4, "deploy")

    wf = _make_workflow([develop, review, router, deploy])
    engine, _ = await _engine_with_workflow(wf)

    # Exhaust all allowed iterations via changes_requested loop
    for i in range(_MAX_LOOP_ITERATIONS):
        await engine.complete_step("wf-test", step_num=1, outputs={"pr": str(i)})
        await engine.complete_step("wf-test", step_num=2, outputs={"decision": "changes_requested"})
        assert develop.iteration == i + 1

    # At the limit, one more loop attempt should fail the workflow
    await engine.complete_step("wf-test", step_num=1, outputs={"pr": "x"})
    await engine.complete_step("wf-test", step_num=2, outputs={"decision": "changes_requested"})

    assert wf.state == WorkflowState.FAILED, "workflow should FAIL after exceeding max iterations"
    assert deploy.status != StepStatus.RUNNING


# ---------------------------------------------------------------------------
# Test 4: No matching route and no default → workflow completes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_no_match_and_no_default_ends_workflow():
    """If no route matches and there is no default, the workflow should complete."""
    review = _make_step(1, "review")
    router = _make_step(
        2,
        "route_review",
        routes=[
            {"when": "review['decision'] == 'approved'", "goto": "deploy"},
            # No default
        ],
    )
    deploy = _make_step(3, "deploy")

    wf = _make_workflow([review, router, deploy])
    engine, _ = await _engine_with_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"decision": "irrelevant"})

    assert wf.state == WorkflowState.COMPLETED


@pytest.mark.asyncio
async def test_final_step_stops_workflow_before_later_linear_steps():
    """A final_step must complete workflow without activating subsequent steps."""
    implement = _make_step(1, "implement")
    close_loop = _make_step(2, "close_loop")
    close_loop.final_step = True
    close_rejected = _make_step(3, "close_rejected")

    wf = _make_workflow([implement, close_loop, close_rejected])
    engine, _ = await _engine_with_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"ok": True})
    assert close_loop.status == StepStatus.RUNNING

    await engine.complete_step("wf-test", step_num=2, outputs={"done": True})

    assert wf.state == WorkflowState.COMPLETED
    assert close_rejected.status == StepStatus.PENDING


@pytest.mark.asyncio
async def test_on_success_jumps_to_named_step_not_sequential():
    """on_success target should be activated even when it is not the next linear step."""
    triage = _make_step(1, "triage")
    triage.on_success = "develop"
    design = _make_step(2, "design")
    develop = _make_step(3, "develop")

    wf = _make_workflow([triage, design, develop])
    engine, _ = await _engine_with_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"needs_design": False})

    assert design.status == StepStatus.PENDING
    assert develop.status == StepStatus.RUNNING
    assert wf.current_step == develop.step_num


# ---------------------------------------------------------------------------
# StepSkipped event emission for router bypass
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_router_bypass_emits_step_skipped_event():
    """Router evaluation bypass must emit a StepSkipped event on the EventBus."""
    develop = _make_step(1, "develop")
    review = _make_step(2, "review")
    router = _make_step(
        3,
        "route_review",
        routes=[
            {"when": "review['decision'] == 'approved'", "goto": "deploy"},
            {"default": True, "goto": "develop"},
        ],
    )
    deploy = _make_step(4, "deploy")

    wf = _make_workflow([develop, review, router, deploy])
    bus = EventBus()
    emitted: list[NexusEvent] = []
    bus.subscribe("step.skipped", lambda e: emitted.append(e))

    engine, _ = await _engine_with_workflow(wf, event_bus=bus)
    # develop → done, review activated
    await engine.complete_step("wf-test", step_num=1, outputs={"pr": "1"})
    # review → approved; router is bypassed (skipped), deploy activated
    await engine.complete_step("wf-test", step_num=2, outputs={"decision": "approved"})

    assert len(emitted) == 1
    skipped_event = emitted[0]
    assert isinstance(skipped_event, StepSkipped)
    assert skipped_event.step_name == "route_review"
    assert skipped_event.workflow_id == "wf-test"
    assert skipped_event.reason == "router evaluated"


# ---------------------------------------------------------------------------
# WorkflowCompleted step-count tests for the final_step path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_final_step_workflow_completed_carries_step_counts():
    """WorkflowCompleted emitted via the final_step path must include step counts."""
    implement = _make_step(1, "implement")
    close_loop = _make_step(2, "close_loop")
    close_loop.final_step = True
    close_rejected = _make_step(3, "close_rejected")

    wf = _make_workflow([implement, close_loop, close_rejected])
    bus = EventBus()
    completed_events: list[NexusEvent] = []
    bus.subscribe("workflow.completed", lambda e: completed_events.append(e))

    engine, _ = await _engine_with_workflow(wf, event_bus=bus)
    await engine.complete_step("wf-test", step_num=1, outputs={})
    await engine.complete_step("wf-test", step_num=2, outputs={})

    assert len(completed_events) == 1
    evt = completed_events[0]
    assert isinstance(evt, WorkflowCompleted)
    # implement + close_loop completed; close_rejected stays PENDING (not counted)
    assert evt.total_steps == 3
    assert evt.completed_steps == 2
    assert evt.skipped_steps == 0
    assert evt.failed_steps == 0
