from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from nexus.core.command_bridge.operator import BridgeOperatorService
from nexus.core.models import Agent, StepStatus, Workflow, WorkflowState, WorkflowStep


class _Storage:
    def __init__(self, by_state):
        self.by_state = by_state

    async def list_workflows(self, state, limit):
        return list(self.by_state.get(state, []))[:limit]


class _Engine:
    def __init__(self, workflows, storage):
        self.workflows = workflows
        self.storage = storage

    async def get_workflow(self, workflow_id):
        return self.workflows.get(workflow_id)


class _Plugin:
    def __init__(self, engine, issue_map, plugin_status_map=None):
        self.engine = engine
        self.issue_map = issue_map
        self.plugin_status_map = plugin_status_map or {}

    def _get_engine(self):
        return self.engine

    def _find_issue_for_workflow(self, workflow_id):
        for issue_num, mapped in self.issue_map.items():
            if mapped == workflow_id:
                return issue_num
        return None

    def _resolve_workflow_id(self, issue_number):
        return self.issue_map.get(str(issue_number))

    async def get_workflow_status(self, issue_number):
        return self.plugin_status_map.get(str(issue_number))


def _agent(name: str) -> Agent:
    return Agent(name=name, display_name=name.title(), description="test", timeout=60, max_retries=2)


def _step(step_num: int, name: str, agent_name: str, status: StepStatus, *, retry_count: int = 0, error: str | None = None):
    now = datetime(2026, 3, 26, 21, 0, tzinfo=UTC)
    started_at = now if status in {StepStatus.RUNNING, StepStatus.COMPLETED, StepStatus.FAILED} else None
    completed_at = now if status in {StepStatus.COMPLETED, StepStatus.FAILED} else None
    return WorkflowStep(
        step_num=step_num,
        name=name,
        agent=_agent(agent_name),
        prompt_template="...",
        status=status,
        started_at=started_at,
        completed_at=completed_at,
        retry_count=retry_count,
        error=error,
    )


def _workflow(workflow_id: str, state: WorkflowState, steps: list[WorkflowStep], *, current_step: int, issue_number: str, project: str = "demo") -> Workflow:
    now = datetime(2026, 3, 26, 21, 30, tzinfo=UTC)
    return Workflow(
        id=workflow_id,
        name=f"Workflow {workflow_id}",
        version="1",
        steps=steps,
        state=state,
        current_step=current_step,
        created_at=now,
        updated_at=now,
        metadata={"issue_number": issue_number, "project": project, "task_type": "feature", "tier": "standard"},
    )


@pytest.fixture
def operator_service(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> BridgeOperatorService:
    failed = _workflow(
        "demo-101-full",
        WorkflowState.FAILED,
        [
            _step(1, "triage", "triage", StepStatus.COMPLETED),
            _step(2, "implement", "developer", StepStatus.FAILED, retry_count=1, error="Developer step failed because tests still break after patch application."),
        ],
        current_step=2,
        issue_number="101",
    )
    paused = _workflow(
        "demo-102-full",
        WorkflowState.PAUSED,
        [
            _step(1, "triage", "triage", StepStatus.COMPLETED),
            _step(2, "review", "reviewer", StepStatus.PENDING),
        ],
        current_step=2,
        issue_number="102",
    )
    running = _workflow(
        "demo-103-full",
        WorkflowState.RUNNING,
        [
            _step(1, "triage", "triage", StepStatus.COMPLETED),
            _step(2, "develop", "developer", StepStatus.RUNNING),
            _step(3, "review", "reviewer", StepStatus.PENDING),
        ],
        current_step=2,
        issue_number="103",
    )
    handoff = _workflow(
        "demo-104-full",
        WorkflowState.RUNNING,
        [
            _step(1, "triage", "triage", StepStatus.COMPLETED),
            _step(2, "develop", "developer", StepStatus.COMPLETED, retry_count=2),
            _step(3, "review", "reviewer", StepStatus.PENDING),
        ],
        current_step=2,
        issue_number="104",
    )

    workflows = {wf.id: wf for wf in [failed, paused, running, handoff]}
    storage = _Storage(
        {
            WorkflowState.FAILED: [failed],
            WorkflowState.PAUSED: [paused],
            WorkflowState.RUNNING: [running, handoff],
            WorkflowState.CANCELLED: [],
        }
    )
    plugin = _Plugin(
        _Engine(workflows, storage),
        issue_map={"101": failed.id, "102": paused.id, "103": running.id, "104": handoff.id},
        plugin_status_map={"103": {"current_agent_type": "developer"}},
    )
    monkeypatch.setattr("nexus.core.command_bridge.operator.get_workflow_state_plugin", lambda **kwargs: plugin)

    logs_dir = tmp_path / ".nexus" / "tasks" / "demo" / "logs"
    logs_dir.mkdir(parents=True)
    (logs_dir / "20260326_103_developer.log").write_text("line 1\nissue #103 still running\nline 3\n", encoding="utf-8")

    monkeypatch.setattr(
        "nexus.core.command_bridge.operator._config",
        lambda: ({"demo": {"workspace": str(tmp_path)}}, None),
    )

    def _fake_resolve_logs_dir(self, project_key):
        return str(logs_dir) if project_key == "demo" else None

    monkeypatch.setattr(BridgeOperatorService, "_resolve_logs_dir", _fake_resolve_logs_dir)
    return BridgeOperatorService()


@pytest.mark.asyncio
async def test_workflow_timeline_exposes_step_history(operator_service: BridgeOperatorService):
    payload = await operator_service.workflow_timeline(issue_number="101")

    assert payload["ok"] is True
    assert payload["count"] == 2
    assert payload["timeline"][1]["step_num"] == 2
    assert payload["timeline"][1]["agent"] == "developer"
    assert payload["timeline"][1]["retry_count"] == 1
    assert "tests still break" in payload["timeline"][1]["error_summary"]


@pytest.mark.asyncio
async def test_recent_incidents_summarizes_problem_workflows(operator_service: BridgeOperatorService):
    payload = await operator_service.recent_incidents(limit=10)

    assert payload["ok"] is True
    assert payload["count"] >= 3
    by_id = {item["workflow_id"]: item for item in payload["items"]}
    assert by_id["demo-101-full"]["severity"] == "critical"
    assert by_id["demo-102-full"]["diagnosis"] == "workflow_paused"
    assert by_id["demo-104-full"]["retrying_steps"][0]["retry_count"] == 2


@pytest.mark.asyncio
async def test_workflow_diagnosis_distinguishes_common_operator_states(operator_service: BridgeOperatorService):
    failed = await operator_service.workflow_diagnosis(issue_number="101")
    paused = await operator_service.workflow_diagnosis(issue_number="102")
    running = await operator_service.workflow_diagnosis(issue_number="103")
    handoff = await operator_service.workflow_diagnosis(issue_number="104")

    assert failed["diagnosis"] == "step_failed"
    assert paused["diagnosis"] == "workflow_paused"
    assert running["diagnosis"] == "agent_running"
    assert handoff["diagnosis"] == "handoff_pending"


@pytest.mark.asyncio
async def test_workflow_logs_context_returns_summary_plus_recent_log_tail(operator_service: BridgeOperatorService):
    payload = await operator_service.workflow_logs_context(issue_number="103")

    assert payload["ok"] is True
    assert "demo-103-full" in payload["summary"]
    assert payload["log_count"] == 1
    assert payload["log_context"][0]["file"] == "20260326_103_developer.log"
    assert any("issue #103" in line for line in payload["log_context"][0]["lines"])
