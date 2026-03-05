"""Tests for workflow approval gate feature."""

import asyncio
from unittest.mock import AsyncMock

import nexus.core.config as config
from nexus.core.models import (
    Agent,
    ApprovalGate,
    ApprovalGateType,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.workflow import WorkflowDefinition, WorkflowEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(name: str = "TestAgent") -> Agent:
    return Agent(name=name, display_name=name, description="test agent", timeout=60)


def _make_gate(
    gate_type: ApprovalGateType = ApprovalGateType.CUSTOM, required: bool = True
) -> ApprovalGate:
    return ApprovalGate(gate_type=gate_type, required=required)


def _make_step(step_num: int, approval_gates: list = None) -> WorkflowStep:
    return WorkflowStep(
        step_num=step_num,
        name=f"step_{step_num}",
        agent=_make_agent(),
        prompt_template="Do the thing",
        approval_gates=approval_gates or [],
    )


def _make_workflow(steps=None) -> Workflow:
    if steps is None:
        steps = [_make_step(1), _make_step(2)]
    return Workflow(
        id="test-wf-1",
        name="Test Workflow",
        version="1.0",
        steps=steps,
        state=WorkflowState.RUNNING,
        current_step=1,
    )


def _make_storage(workflow: Workflow):
    """Return a mock StorageBackend pre-loaded with `workflow`."""
    storage = AsyncMock()
    storage.load_workflow.return_value = workflow
    storage.save_workflow.return_value = None
    storage.append_audit_event.return_value = None
    return storage


# ---------------------------------------------------------------------------
# Model tests
# ---------------------------------------------------------------------------


class TestWorkflowStepApprovalFields:
    def test_default_approval_gates_empty(self):
        step = _make_step(1)
        assert step.approval_gates == []

    def test_approval_gate_added(self):
        gate = _make_gate(ApprovalGateType.DEPLOYMENT)
        step = _make_step(1, approval_gates=[gate])
        assert len(step.approval_gates) == 1
        assert step.approval_gates[0].gate_type == ApprovalGateType.DEPLOYMENT

    def test_step_with_no_gates(self):
        step = WorkflowStep(
            step_num=1,
            name="s",
            agent=_make_agent(),
            prompt_template="x",
        )
        assert step.approval_gates == []

    def test_multiple_approval_gates(self):
        gates = [
            _make_gate(ApprovalGateType.PR_MERGE),
            _make_gate(ApprovalGateType.DEPLOYMENT),
        ]
        step = _make_step(1, approval_gates=gates)
        assert len(step.approval_gates) == 2


class TestWorkflowStateEnum:
    def test_standard_states_exist(self):
        assert WorkflowState.PENDING.value == "pending"
        assert WorkflowState.RUNNING.value == "running"
        assert WorkflowState.PAUSED.value == "paused"
        assert WorkflowState.COMPLETED.value == "completed"
        assert WorkflowState.FAILED.value == "failed"

    def test_is_complete_not_true_for_running(self):
        wf = _make_workflow()
        wf.state = WorkflowState.RUNNING
        assert not wf.is_complete()


# ---------------------------------------------------------------------------
# WorkflowDefinition YAML / dict parsing tests
# ---------------------------------------------------------------------------


class TestWorkflowDefinitionApprovalParsing:
    def test_from_dict_parses_approval_gates(self):
        data = {
            "name": "My Workflow",
            "steps": [
                {
                    "name": "deploy",
                    "agent_type": "ops",
                    "approval_gates": [{"gate_type": "deployment"}],
                },
                {"name": "code", "agent_type": "dev"},
            ],
        }
        wf = WorkflowDefinition.from_dict(data)
        # All steps get default PR_MERGE gate; first also gets deployment
        assert any(g.gate_type == ApprovalGateType.PR_MERGE for g in wf.steps[0].approval_gates)

    def test_from_dict_defaults_include_pr_merge(self):
        data = {
            "name": "My Workflow",
            "steps": [{"name": "plain", "agent_type": "dev"}],
        }
        wf = WorkflowDefinition.from_dict(data)
        # Default approval gates include PR_MERGE
        assert any(g.gate_type == ApprovalGateType.PR_MERGE for g in wf.steps[0].approval_gates)

    def test_from_yaml(self, tmp_path):
        yaml_content = """\
name: Deploy Flow
steps:
  - name: design
    agent_type: architect
  - name: deploy
    agent_type: ops
    approval_gates:
      - gate_type: deployment
"""
        yaml_file = tmp_path / "workflow.yaml"
        yaml_file.write_text(yaml_content)

        wf = WorkflowDefinition.from_yaml(str(yaml_file))
        assert len(wf.steps) == 2
        # Both steps should have at least the default PR_MERGE gate
        assert len(wf.steps[0].approval_gates) >= 1
        assert len(wf.steps[1].approval_gates) >= 1


# ---------------------------------------------------------------------------
# WorkflowEngine approval gate tests
# ---------------------------------------------------------------------------


class TestWorkflowEngineWithApprovalGates:
    def test_complete_step_advances_current_step(self):
        """complete_step advances to the next step."""
        step1 = _make_step(1)
        step1.status = StepStatus.RUNNING
        step2 = _make_step(2, approval_gates=[_make_gate(ApprovalGateType.DEPLOYMENT)])

        wf = Workflow(
            id="wf-approval",
            name="Test",
            version="1.0",
            steps=[step1, step2],
            state=WorkflowState.RUNNING,
            current_step=1,
        )

        storage = _make_storage(wf)
        engine = WorkflowEngine(storage=storage)

        result = asyncio.run(engine.complete_step("wf-approval", step_num=1, outputs={}))

        assert result.current_step == 2

    def test_complete_step_no_gates_continues(self):
        """When step has no extra gates, execution continues normally."""
        step1 = _make_step(1)
        step1.status = StepStatus.RUNNING
        step2 = _make_step(2)

        wf = Workflow(
            id="wf-no-gates",
            name="Test",
            version="1.0",
            steps=[step1, step2],
            state=WorkflowState.RUNNING,
            current_step=1,
        )

        storage = _make_storage(wf)
        engine = WorkflowEngine(storage=storage)

        result = asyncio.run(engine.complete_step("wf-no-gates", step_num=1, outputs={}))

        assert result.state == WorkflowState.RUNNING

    def test_audit_events_logged_on_step_completion(self):
        """Audit events are emitted when a step completes."""
        step1 = _make_step(1)
        step1.status = StepStatus.RUNNING

        wf = Workflow(
            id="wf-audit",
            name="Test",
            version="1.0",
            steps=[step1],
            state=WorkflowState.RUNNING,
            current_step=1,
        )

        storage = _make_storage(wf)
        engine = WorkflowEngine(storage=storage)

        asyncio.run(engine.complete_step("wf-audit", step_num=1, outputs={}))

        # Check that audit events were recorded
        assert storage.append_audit_event.called


# ---------------------------------------------------------------------------
# HostStateManager approval state persistence tests
# ---------------------------------------------------------------------------


class TestWorkflowStateApproval:
    def test_set_and_get_pending_approval(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

        # Reset singleton so factory re-creates with tmp_path
        import nexus.core.integrations.workflow_state_factory as wsf

        monkeypatch.setattr(wsf, "_instance", None)

        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        store = get_workflow_state()
        store.set_pending_approval(
            issue_num="42",
            step_num=3,
            step_name="deploy",
            approvers=["tech-lead"],
            approval_timeout=3600,
        )

        pending = store.get_pending_approval("42")
        assert pending is not None
        assert pending["step_num"] == 3
        assert pending["step_name"] == "deploy"
        assert pending["approvers"] == ["tech-lead"]
        assert pending["approval_timeout"] == 3600

    def test_get_pending_approval_returns_none_when_absent(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

        import nexus.core.integrations.workflow_state_factory as wsf

        monkeypatch.setattr(wsf, "_instance", None)

        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        assert get_workflow_state().get_pending_approval("99") is None

    def test_clear_pending_approval(self, tmp_path, monkeypatch):
        monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))

        import nexus.core.integrations.workflow_state_factory as wsf

        monkeypatch.setattr(wsf, "_instance", None)

        from nexus.core.integrations.workflow_state_factory import get_workflow_state

        store = get_workflow_state()
        store.set_pending_approval(
            issue_num="55",
            step_num=1,
            step_name="review",
            approvers=[],
            approval_timeout=86400,
        )
        store.clear_pending_approval("55")
        assert store.get_pending_approval("55") is None
