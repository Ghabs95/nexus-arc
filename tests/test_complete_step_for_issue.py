"""Tests for WorkflowStateEnginePlugin.complete_step_for_issue and Workflow.active_agent_type."""

from datetime import UTC, datetime
from typing import Any

import pytest

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import (
    Agent,
    AuditEvent,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.workflow import WorkflowEngine
from nexus.plugins.builtin.workflow_state_engine_plugin import WorkflowStateEnginePlugin


# ---------------------------------------------------------------------------
# Helpers (reuse pattern from test_conditional_steps.py)
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


def _agent(agent_type: str) -> Agent:
    return Agent(
        name=agent_type,
        display_name=agent_type.title(),
        description="test",
        timeout=60,
        max_retries=0,
    )


def _step(num: int, step_id: str, agent_type: str, routes=None) -> WorkflowStep:
    return WorkflowStep(
        step_num=num,
        name=step_id,
        agent=_agent(agent_type),
        prompt_template="do work",
        routes=routes or [],
    )


def _make_workflow(workflow_id: str, steps: list[WorkflowStep]) -> Workflow:
    wf = Workflow(
        id=workflow_id,
        name="test",
        version="1.0",
        steps=steps,
        state=WorkflowState.RUNNING,
        current_step=1,
    )
    steps[0].status = StepStatus.RUNNING
    steps[0].started_at = datetime.now(UTC)
    return wf


async def _plugin_with_workflow(workflow: Workflow, issue_number: str) -> tuple:
    storage = InMemoryStorage()
    await storage.save_workflow(workflow)
    engine = WorkflowEngine(storage=storage)
    issue_map = {issue_number: workflow.id}
    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "issue_to_workflow_id": lambda n: issue_map.get(str(n)),
        }
    )
    return plugin, storage


async def _complete(
    plugin: WorkflowStateEnginePlugin,
    issue_number: str,
    completed_agent_type: str,
    outputs: dict[str, Any] | None = None,
    *,
    event_id: str = "",
):
    workflow_id = plugin._resolve_workflow_id(issue_number)
    workflow = await plugin._get_engine().get_workflow(str(workflow_id))
    if workflow is None:
        return await plugin.complete_step_for_issue(
            issue_number=issue_number,
            completed_agent_type=completed_agent_type,
            outputs=outputs or {},
            event_id=event_id,
        )
    running_step = next((step for step in workflow.steps if step.status == StepStatus.RUNNING), None)
    if running_step is None and workflow.steps:
        running_step = workflow.steps[0]
    payload = dict(outputs or {})
    if running_step is not None:
        payload.setdefault("step_id", running_step.name)
        payload.setdefault("step_num", running_step.step_num)
    return await plugin.complete_step_for_issue(
        issue_number=issue_number,
        completed_agent_type=completed_agent_type,
        outputs=payload,
        event_id=event_id,
    )


# ---------------------------------------------------------------------------
# Workflow.active_agent_type
# ---------------------------------------------------------------------------


class TestActiveAgentType:
    def test_returns_agent_type_when_step_running(self):
        step = _step(1, "develop", "developer")
        step.status = StepStatus.RUNNING
        wf = Workflow(
            id="w", name="t", version="1", steps=[step], state=WorkflowState.RUNNING, current_step=1
        )
        assert wf.active_agent_type == "developer"

    def test_returns_none_when_step_completed(self):
        step = _step(1, "develop", "developer")
        step.status = StepStatus.COMPLETED
        wf = Workflow(
            id="w", name="t", version="1", steps=[step], state=WorkflowState.RUNNING, current_step=1
        )
        assert wf.active_agent_type is None

    def test_returns_none_when_no_current_step(self):
        wf = Workflow(
            id="w", name="t", version="1", steps=[], state=WorkflowState.RUNNING, current_step=0
        )
        assert wf.active_agent_type is None

    def test_returns_none_workflow_completed(self):
        step = _step(1, "develop", "developer")
        step.status = StepStatus.COMPLETED
        wf = Workflow(
            id="w",
            name="t",
            version="1",
            steps=[step],
            state=WorkflowState.COMPLETED,
            current_step=1,
        )
        assert wf.active_agent_type is None


# ---------------------------------------------------------------------------
# complete_step_for_issue: basic routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_step_for_issue_advances_to_next():
    """Completing the first step should activate the second step."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    wf = _make_workflow("wf-42", [develop, review])
    plugin, _ = await _plugin_with_workflow(wf, "42")

    updated = await _complete(
        plugin,
        issue_number="42",
        completed_agent_type="developer",
        outputs={"pr": "https://github.com/org/repo/pull/1"},
    )

    assert updated is not None
    assert updated.active_agent_type == "reviewer"
    assert updated.state == WorkflowState.RUNNING


@pytest.mark.asyncio
async def test_complete_step_for_issue_completes_workflow_on_last_step():
    """Completing the last step with no successor should mark workflow COMPLETED."""
    summarizer = _step(1, "close_loop", "summarizer")
    wf = _make_workflow("wf-99", [summarizer])
    plugin, _ = await _plugin_with_workflow(wf, "99")

    updated = await _complete(
        plugin,
        issue_number="99",
        completed_agent_type="summarizer",
        outputs={"summary": "done"},
    )

    assert updated is not None
    assert updated.state == WorkflowState.COMPLETED
    assert updated.active_agent_type is None


@pytest.mark.asyncio
async def test_complete_step_for_issue_returns_none_when_no_mapping():
    """Returns None when no workflow is mapped to the issue."""
    plugin = WorkflowStateEnginePlugin(
        {
            "issue_to_workflow_id": lambda _: None,
        }
    )
    result = await plugin.complete_step_for_issue("999", "developer", {})
    assert result is None


@pytest.mark.asyncio
async def test_complete_step_for_issue_rejects_missing_step_identity():
    """Completion payload must include step_id + step_num."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    wf = _make_workflow("wf-strict-step", [develop, review])
    plugin, _ = await _plugin_with_workflow(wf, "strict-step")

    with pytest.raises(ValueError, match="Completion invalid schema"):
        await plugin.complete_step_for_issue(
            issue_number="strict-step",
            completed_agent_type="developer",
            outputs={"done": True},
        )


@pytest.mark.asyncio
async def test_complete_step_for_issue_noop_when_agent_type_mismatch():
    """When agent_type doesn't match RUNNING step, plugin raises mismatch error."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    wf = _make_workflow("wf-fallback", [develop, review])
    plugin, _ = await _plugin_with_workflow(wf, "fallback")

    # Pass wrong agent_type that doesn't match RUNNING "developer"
    with pytest.raises(ValueError, match="Completion stale/mismatched"):
        await _complete(
            plugin,
            issue_number="fallback",
            completed_agent_type="unknown-agent",
            outputs={"done": True},
        )


@pytest.mark.asyncio
async def test_complete_step_for_issue_autostarts_pending_workflow():
    """Pending workflows auto-start so first completion can advance chain."""
    triage = _step(1, "triage", "triage")
    debug = _step(2, "debug", "debug")
    wf = Workflow(
        id="wf-pending",
        name="test",
        version="1.0",
        steps=[triage, debug],
        state=WorkflowState.PENDING,
        current_step=0,
    )
    plugin, _ = await _plugin_with_workflow(wf, "pending")

    updated = await _complete(
        plugin,
        issue_number="pending",
        completed_agent_type="triage",
        outputs={"priority": "p2"},
    )

    assert updated is not None
    assert updated.state == WorkflowState.RUNNING
    assert updated.active_agent_type == "debug"


# ---------------------------------------------------------------------------
# complete_step_for_issue: router step (review/develop loop)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_step_for_issue_routes_through_router_to_close():
    """Router evaluates route condition and routes approved review to close_loop."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    router = _step(
        3,
        "route_review",
        "router",
        routes=[
            {"when": "approval_status == 'approved'", "then": "close_loop"},
            {"default": "develop"},
        ],
    )
    close_loop = _step(4, "close_loop", "summarizer")

    wf = _make_workflow("wf-router", [develop, review, router, close_loop])
    plugin, _ = await _plugin_with_workflow(wf, "router")

    # develop completes
    await _complete(plugin, "router", "developer", {"pr": "1"})
    assert wf.active_agent_type == "reviewer"

    # review completes — approved
    updated = await _complete(
        plugin,
        "router",
        "reviewer",
        {"approval_status": "approved", "review_comments": []},
    )

    assert updated is not None
    assert (
        updated.active_agent_type == "summarizer"
    ), f"expected summarizer, got {updated.active_agent_type} (state={updated.state})"


@pytest.mark.asyncio
async def test_complete_step_for_issue_routes_loop_back_to_develop():
    """Router evaluates default and loops reviewer → developer on changes_requested."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    router = _step(
        3,
        "route_review",
        "router",
        routes=[
            {"when": "approval_status == 'approved'", "then": "close_loop"},
            {"default": "develop"},
        ],
    )
    close_loop = _step(4, "close_loop", "summarizer")

    wf = _make_workflow("wf-loop", [develop, review, router, close_loop])
    plugin, _ = await _plugin_with_workflow(wf, "loop")

    # develop completes
    await _complete(plugin, "loop", "developer", {"pr": "1"})

    # review completes — changes requested → loops back
    updated = await _complete(plugin, "loop", "reviewer", {"approval_status": "changes_requested"})

    assert updated is not None
    assert updated.active_agent_type == "developer"
    assert develop.iteration == 1


@pytest.mark.asyncio
async def test_complete_step_for_issue_derives_review_status_for_changes_requested():
    """When reviewer provides next_agent=developer, routing derives changes_requested."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    route_review = _step(
        3,
        "route_review",
        "router",
        routes=[
            {"when": "review_status == 'approved'", "then": "compliance"},
            {"when": "review_status == 'changes_requested'", "then": "develop"},
            {"default": "close_rejected"},
        ],
    )
    compliance = _step(4, "compliance", "compliance")
    close_rejected = _step(5, "close_rejected", "summarizer")

    wf = _make_workflow(
        "wf-review-derived-dev", [develop, review, route_review, compliance, close_rejected]
    )
    plugin, _ = await _plugin_with_workflow(wf, "review-derived-dev")

    await _complete(plugin, "review-derived-dev", "developer", {"pr": "1"})
    updated = await _complete(
        plugin,
        "review-derived-dev",
        "reviewer",
        {"next_agent": "developer", "summary": "tests failed"},
    )

    assert updated is not None
    assert updated.active_agent_type == "developer"


@pytest.mark.asyncio
async def test_complete_step_for_issue_derives_review_status_for_approved():
    """When reviewer provides next_agent=compliance, routing derives approved."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    route_review = _step(
        3,
        "route_review",
        "router",
        routes=[
            {"when": "review_status == 'approved'", "then": "compliance"},
            {"when": "review_status == 'changes_requested'", "then": "develop"},
            {"default": "close_rejected"},
        ],
    )
    compliance = _step(4, "compliance", "compliance")
    close_rejected = _step(5, "close_rejected", "summarizer")

    wf = _make_workflow(
        "wf-review-derived-compliance", [develop, review, route_review, compliance, close_rejected]
    )
    plugin, _ = await _plugin_with_workflow(wf, "review-derived-compliance")

    await _complete(plugin, "review-derived-compliance", "developer", {"pr": "1"})
    updated = await _complete(
        plugin,
        "review-derived-compliance",
        "reviewer",
        {"next_agent": "compliance", "summary": "approved"},
    )

    assert updated is not None
    assert updated.active_agent_type == "compliance"


@pytest.mark.asyncio
async def test_complete_step_for_issue_derives_router_field_for_custom_agent_types():
    """Infer router condition vars from next_agent without hardcoded agent_type names."""
    implement = _step(1, "implement", "builder")
    validate = _step(2, "validate", "qa_guard")
    route_validate = _step(
        3,
        "route_validate",
        "router",
        routes=[
            {"when": "quality_gate == 'pass'", "then": "security_scan"},
            {"when": "quality_gate == 'fail'", "then": "rework"},
            {"default": "rework"},
        ],
    )
    security_scan = _step(4, "security_scan", "sec_ops")
    rework = _step(5, "rework", "builder")

    wf = _make_workflow(
        "wf-generic-routing",
        [implement, validate, route_validate, security_scan, rework],
    )
    plugin, _ = await _plugin_with_workflow(wf, "generic-routing")

    await _complete(plugin, "generic-routing", "builder", {"artifact": "build-1"})
    updated = await _complete(
        plugin,
        "generic-routing",
        "qa_guard",
        {"next_agent": "builder", "summary": "tests failed"},
    )

    assert updated is not None
    assert updated.active_agent_type == "builder"


@pytest.mark.asyncio
async def test_complete_step_for_issue_derives_numeric_router_field_from_next_agent():
    """Infer numeric route condition assignments (e.g. retry_count == 1)."""
    execute = _step(1, "execute", "runner")
    verify = _step(2, "verify", "validator")
    route_verify = _step(
        3,
        "route_verify",
        "router",
        routes=[
            {"when": "retry_count == 1", "then": "retry_once"},
            {"when": "retry_count == 0", "then": "ship"},
            {"default": "ship"},
        ],
    )
    retry_once = _step(4, "retry_once", "runner")
    ship = _step(5, "ship", "deployer")

    wf = _make_workflow(
        "wf-numeric-routing",
        [execute, verify, route_verify, retry_once, ship],
    )
    plugin, _ = await _plugin_with_workflow(wf, "numeric-routing")

    await _complete(plugin, "numeric-routing", "runner", {"artifact": "run-1"})
    updated = await _complete(
        plugin,
        "numeric-routing",
        "validator",
        {"next_agent": "retry_once", "summary": "retry requested"},
    )

    assert updated is not None
    assert updated.active_agent_type == "runner"


@pytest.mark.asyncio
async def test_complete_step_for_issue_derives_boolean_router_field_from_next_agent():
    """Infer boolean route condition assignments (e.g. is_blocked == True)."""
    implement = _step(1, "implement", "builder")
    guard = _step(2, "guard", "policy_guard")
    route_guard = _step(
        3,
        "route_guard",
        "router",
        routes=[
            {"when": "is_blocked == True", "then": "rework"},
            {"when": "is_blocked == False", "then": "release"},
            {"default": "release"},
        ],
    )
    rework = _step(4, "rework", "builder")
    release = _step(5, "release", "deployer")

    wf = _make_workflow(
        "wf-boolean-routing",
        [implement, guard, route_guard, rework, release],
    )
    plugin, _ = await _plugin_with_workflow(wf, "boolean-routing")

    await _complete(plugin, "boolean-routing", "builder", {"artifact": "build-1"})
    updated = await _complete(
        plugin,
        "boolean-routing",
        "policy_guard",
        {"next_agent": "rework", "summary": "blocked"},
    )

    assert updated is not None
    assert updated.active_agent_type == "builder"


@pytest.mark.asyncio
async def test_reset_to_agent_for_issue_rewinds_completed_workflow():
    """Forced reset should move workflow from COMPLETED back to RUNNING at target agent."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    close_loop = _step(3, "close_loop", "writer")
    wf = _make_workflow("wf-reset", [develop, review, close_loop])
    plugin, _ = await _plugin_with_workflow(wf, "reset")

    await _complete(plugin, "reset", "developer", {"next_agent": "reviewer"})
    await _complete(plugin, "reset", "reviewer", {"next_agent": "writer"})
    completed = await _complete(plugin, "reset", "writer", {"next_agent": "none"})
    assert completed is not None
    assert completed.state == WorkflowState.COMPLETED

    rewound = await plugin.reset_to_agent_for_issue("reset", "developer")
    assert rewound is True

    status = await plugin.get_workflow_status("reset")
    assert status is not None
    assert status["state"] == WorkflowState.RUNNING.value
    assert str(status["current_step_name"]).lower() == "develop"
    assert str(status["current_agent"]).lower() == "developer"


@pytest.mark.asyncio
async def test_reset_to_agent_for_issue_clears_iteration_counters_for_recovery():
    """Manual rewind should clear loop counters from the target step onward."""
    develop = _step(1, "develop", "developer")
    review = _step(2, "review", "reviewer")
    close_loop = _step(3, "close_loop", "writer")
    wf = _make_workflow("wf-reset-iterations", [develop, review, close_loop])
    plugin, _ = await _plugin_with_workflow(wf, "reset-iterations")

    loaded = await plugin._get_engine().get_workflow("wf-reset-iterations")
    assert loaded is not None
    loaded.steps[0].iteration = 5
    loaded.steps[1].iteration = 5
    loaded.steps[2].iteration = 3
    await plugin._get_engine().storage.save_workflow(loaded)

    rewound = await plugin.reset_to_agent_for_issue("reset-iterations", "developer")
    assert rewound is True

    status = await plugin.get_workflow_status("reset-iterations")
    assert status is not None
    assert status["state"] == WorkflowState.RUNNING.value

    updated = await plugin._get_engine().get_workflow("wf-reset-iterations")
    assert updated is not None
    assert updated.steps[0].iteration == 0
    assert updated.steps[1].iteration == 0
    assert updated.steps[2].iteration == 0


@pytest.mark.asyncio
async def test_reset_to_agent_for_issue_recovers_missing_workflow_from_mapping(tmp_path):
    """When mapping exists but workflow row is missing, reset should recover then rewind."""
    workflow_yaml = tmp_path / "workflow.yaml"
    workflow_yaml.write_text(
        "\n".join(
            [
                "name: Demo Flow",
                "full_workflow:",
                "  steps:",
                "    - name: triage",
                "      agent_type: triage",
                "    - name: develop",
                "      agent_type: developer",
                "    - name: review",
                "      agent_type: reviewer",
            ]
        ),
        encoding="utf-8",
    )

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage)
    issue_map = {"88": "nexus-88-full"}

    plugin = WorkflowStateEnginePlugin(
        {
            "engine_factory": lambda: engine,
            "issue_to_workflow_id": lambda n: issue_map.get(str(n)),
            "issue_to_workflow_map_setter": lambda n, w: issue_map.__setitem__(str(n), str(w)),
            "workflow_definition_path_resolver": (
                lambda project: str(workflow_yaml) if str(project) == "nexus" else None
            ),
            "default_task_type": "feature",
        }
    )

    assert await engine.get_workflow("nexus-88-full") is None

    rewound = await plugin.reset_to_agent_for_issue("88", "developer")
    assert rewound is True

    status = await plugin.get_workflow_status("88")
    assert status is not None
    assert status["state"] == WorkflowState.RUNNING.value
    assert str(status["current_step_name"]).lower() == "develop"
    assert str(status["current_agent"]).lower() in {"develop", "developer"}


# ---------------------------------------------------------------------------
# Idempotency ledger tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_complete_step_for_issue_idempotency_duplicate_suppressed(tmp_path):
    """Calling complete_step_for_issue with a duplicate event_id must be a no-op.

    Simulates a re-delivered signal: after the first call advances the workflow,
    we reset step 1 back to RUNNING and replay the same event_id.  The ledger
    must suppress the second advancement.
    """
    step1 = _step(1, "triage", "triage")
    step2 = _step(2, "dev", "developer")
    wf = _make_workflow("wf-idem", [step1, step2])
    plugin, _ = await _plugin_with_workflow(wf, "idem")
    ledger_path = str(tmp_path / "ledger.json")
    plugin.config["idempotency_ledger_path"] = ledger_path

    # First call advances the workflow (step1 → COMPLETED, step2 → RUNNING).
    updated = await _complete(plugin, "idem", "triage", {}, event_id="ev-001")
    assert updated is not None
    assert updated.active_agent_type == "developer"

    # Simulate re-delivery: reset step1 back to RUNNING.
    step1.status = StepStatus.RUNNING
    step2.status = StepStatus.PENDING

    # Second call with the same composite key must be suppressed by the ledger.
    updated2 = await _complete(plugin, "idem", "triage", {}, event_id="ev-001")
    # step1 must still be RUNNING (the ledger blocked the advancement).
    assert step1.status == StepStatus.RUNNING
    assert updated2 is not None


@pytest.mark.asyncio
async def test_complete_step_for_issue_different_event_ids_advance_independently(tmp_path):
    """Two distinct event_ids for different steps should both advance normally."""
    step1 = _step(1, "triage", "triage")
    step2 = _step(2, "dev", "developer")
    wf = _make_workflow("wf-idem2", [step1, step2])
    plugin, _ = await _plugin_with_workflow(wf, "idem2")
    ledger_path = str(tmp_path / "ledger2.json")
    plugin.config["idempotency_ledger_path"] = ledger_path

    await _complete(plugin, "idem2", "triage", {}, event_id="ev-aaa")
    updated = await _complete(plugin, "idem2", "developer", {}, event_id="ev-bbb")
    assert updated is not None
    from nexus.core.models import WorkflowState as WS

    assert updated.state == WS.COMPLETED


@pytest.mark.asyncio
async def test_complete_step_for_issue_no_event_id_always_advances(tmp_path):
    """Without an event_id the ledger is skipped and calls advance as normal."""
    step1 = _step(1, "triage", "triage")
    step2 = _step(2, "dev", "developer")
    wf = _make_workflow("wf-idem3", [step1, step2])
    plugin, _ = await _plugin_with_workflow(wf, "idem3")
    ledger_path = str(tmp_path / "ledger3.json")
    plugin.config["idempotency_ledger_path"] = ledger_path

    await _complete(plugin, "idem3", "triage", {})
    updated = await _complete(plugin, "idem3", "developer", {})
    assert updated is not None
    from nexus.core.models import WorkflowState as WS

    assert updated.state == WS.COMPLETED
