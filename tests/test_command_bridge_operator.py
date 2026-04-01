from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from nexus.core.command_bridge.operator import BridgeOperatorService
from nexus.core.models import Agent, ApprovalGate, ApprovalGateType, StepStatus, Workflow, WorkflowState, WorkflowStep


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


class _ApprovalStore:
    def __init__(self, pending=None):
        self.pending = pending or {}

    def get_pending_approval(self, issue_num):
        return self.pending.get(str(issue_num))


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

    async def reset_to_agent_for_issue(self, issue_number, agent_ref):
        workflow_id = self._resolve_workflow_id(issue_number)
        workflow = await self.engine.get_workflow(workflow_id)
        if workflow is None:
            return False
        target_step = next(
            (
                step
                for step in workflow.steps
                if str(getattr(step.agent, "name", "")) == str(agent_ref)
                or str(getattr(step, "name", "")) == str(agent_ref)
            ),
            None,
        )
        if target_step is None:
            return False
        workflow.state = WorkflowState.RUNNING
        workflow.current_step = target_step.step_num
        target_step.status = StepStatus.RUNNING
        target_step.error = None
        return True


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

    review = _workflow(
        "demo-105-full",
        WorkflowState.RUNNING,
        [
            _step(1, "triage", "triage", StepStatus.COMPLETED),
            WorkflowStep(
                step_num=2,
                name="review",
                agent=_agent("reviewer"),
                prompt_template="...",
                status=StepStatus.PENDING,
                require_human_approval=True,
                approval_gates=[ApprovalGate(gate_type=ApprovalGateType.CUSTOM, tool_restrictions=["merge_pr"], approval_message="Need human approval")],
            ),
            _step(3, "compliance", "compliance", StepStatus.PENDING),
        ],
        current_step=2,
        issue_number="105",
        project="nexus",
    )
    review.metadata.update({
        "requester_login": "gab",
        "issue_author": "Gab",
        "comment_author": "nexus-operator[bot]",
        "source_platform": "openclaw",
    })

    workflows = {wf.id: wf for wf in [failed, paused, running, handoff, review]}
    storage = _Storage(
        {
            WorkflowState.FAILED: [failed],
            WorkflowState.PAUSED: [paused],
            WorkflowState.RUNNING: [running, handoff, review],
            WorkflowState.CANCELLED: [],
        }
    )
    plugin = _Plugin(
        _Engine(workflows, storage),
        issue_map={"101": failed.id, "102": paused.id, "103": running.id, "104": handoff.id, "105": review.id},
        plugin_status_map={"103": {"current_agent_type": "developer"}},
    )
    monkeypatch.setattr("nexus.core.command_bridge.operator.get_workflow_state_plugin", lambda **kwargs: plugin)
    monkeypatch.setattr(
        "nexus.core.integrations.workflow_state_factory.get_workflow_state",
        lambda: _ApprovalStore({"105": {"step_num": 2, "step_name": "review", "approvers": ["human"], "approval_timeout": 3600}}),
    )

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

    # Build service and inject a fake auth manager for LinkedIn connector tests
    service = BridgeOperatorService()

    class FakeAuthManager:
        def get_linkedin_auth_status(self, *, nexus_id: str) -> dict[str, Any]:
            if nexus_id == "nexus-user-1":
                return {
                    "nexus_id": "nexus-user-1",
                    "connected": True,
                    "has_access_token": True,
                    "has_author_urn": True,
                    "author_urn": "urn:li:person:abc123",
                    "expires_at": datetime(2026, 3, 30, 1, 0, tzinfo=UTC).isoformat(),
                    "is_expired": False,
                }
            return {"nexus_id": nexus_id, "connected": False}

        def get_linkedin_profile_me(self, *, nexus_id: str) -> dict[str, Any]:
            if nexus_id == "nexus-user-1":
                return {
                    "sub": "abc123",
                    "name": "Ada Lovelace",
                    "author_urn": "urn:li:person:abc123",
                }
            raise ValueError("Profile not found")

    service._auth_manager_instance = FakeAuthManager()
    return service


@pytest.mark.asyncio
async def test_linkedin_auth_status_endpoint(operator_service: BridgeOperatorService):
    payload = await operator_service.linkedin_auth_status(headers={"X-Nexus-ID": "nexus-user-1"})

    assert payload["ok"] is True
    assert payload["status"]["connected"] is True
    assert payload["status"]["author_urn"] == "urn:li:person:abc123"


@pytest.mark.asyncio
async def test_linkedin_profile_me_endpoint(operator_service: BridgeOperatorService):
    payload = await operator_service.linkedin_profile_me(headers={"X-Nexus-ID": "nexus-user-1"})

    assert payload["ok"] is True
    assert payload["profile"]["name"] == "Ada Lovelace"
    assert payload["profile"]["author_urn"] == "urn:li:person:abc123"

    with pytest.raises(ValueError, match="Profile not found"):
        await operator_service.linkedin_profile_me(headers={"X-Nexus-ID": "missing-user"})


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
    approval = await operator_service.workflow_diagnosis(issue_number="105")

    assert failed["diagnosis"] == "step_failed"
    assert paused["diagnosis"] == "workflow_paused"
    assert running["diagnosis"] == "agent_running"
    assert handoff["diagnosis"] == "handoff_pending"
    assert approval["diagnosis"] == "approval_required"
    assert "waiting for approval" in str(approval["likely_cause"]).lower()
    assert "inspect blockers" in approval["suggested_actions"]


@pytest.mark.asyncio
async def test_workflow_logs_context_returns_summary_plus_recent_log_tail(operator_service: BridgeOperatorService):
    payload = await operator_service.workflow_logs_context(issue_number="103")

    assert payload["ok"] is True
    assert "demo-103-full" in payload["summary"]
    assert payload["log_count"] == 1
    assert payload["log_context"][0]["file"] == "20260326_103_developer.log"
    assert any("issue #103" in line for line in payload["log_context"][0]["lines"])


@pytest.mark.asyncio
async def test_runtime_health_warns_when_openclaw_wake_mode_is_enabled(
    operator_service: BridgeOperatorService,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setenv("NEXUS_RUNTIME_MODE", "openclaw")
    monkeypatch.setenv("NEXUS_OPENCLAW_WAKE_MODE", "now")
    monkeypatch.setenv("NEXUS_OPENCLAW_BRIDGE_URL", "https://jarvis.ghabs.me")
    monkeypatch.setenv("NEXUS_OPENCLAW_BRIDGE_TOKEN", "secret")
    monkeypatch.setenv("NEXUS_OPENCLAW_SENDER_ID", "47168736")

    payload = await operator_service.runtime_health()

    assert payload["ok"] is True
    assert payload["runtime_mode"] == "openclaw"
    assert payload["bridge"]["openclaw_wake_mode"] == "now"
    assert payload["bridge"]["openclaw_sender_id"] is True
    assert any("Compacting context" in warning for warning in payload["warnings"])


@pytest.mark.asyncio
async def test_workflow_authorship_audit_classifies_bot_vs_human_signals(operator_service: BridgeOperatorService):
    payload = await operator_service.workflow_authorship_audit(issue_number="105")

    assert payload["ok"] is True
    assert payload["authorship"]["classification"] == "human_requested"
    assert any(item["source"] == "comment_author" and item["classification"] == "bot" for item in payload["authorship"]["provenance"])
    assert any(item["source"] == "requester_login" and item["classification"] == "human" for item in payload["authorship"]["provenance"])


@pytest.mark.asyncio
async def test_routing_validate_explains_nexus_repo_split(operator_service: BridgeOperatorService, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(
        "nexus.core.command_bridge.operator._config",
        lambda: ({
            "nexus": {
                "workflow_definition_path": "nexus/workflows/main.yaml",
                "git_platform": "github",
                "git_repo": "Ghabs95/nexus",
                "git_repos": ["Ghabs95/nexus-os", "Ghabs95/nexus-arc", "Ghabs95/nexus"],
                "git_branches": {"default": "develop"},
                "ai_tool_preferences": {"developer": {"profile": "reasoning", "provider": "codex"}},
                "model_profiles": {"reasoning": {"codex": "gpt-5.4"}},
                "profile_provider_priority": {"reasoning": ["codex", "gemini"]},
            }
        }, "Ghabs95/nexus"),
    )

    payload = await operator_service.routing_validate(project_key="nexus", task_type="operator")

    assert payload["ok"] is True
    assert payload["validation"]["recommended_repo"] == "Ghabs95/nexus-os"
    checks = {item["check"]: item for item in payload["validation"]["checks"]}
    assert checks["nexus_repo_split"]["status"] == "ok"
    roles = {item["slug"]: item for item in payload["validation"]["repo_roles"]}
    assert roles["nexus-os"]["task_type_match"] is True
    assert roles["nexus-arc"]["role"] == "framework"


@pytest.mark.asyncio
async def test_workflow_blockers_surfaces_approvals_and_pauses(operator_service: BridgeOperatorService):
    paused_payload = await operator_service.workflow_blockers(issue_number="102")
    approval_payload = await operator_service.workflow_blockers(issue_number="105")

    assert paused_payload["blocking"] is True
    assert any(item["type"] == "workflow_paused" for item in paused_payload["blockers"])
    assert approval_payload["blocking"] is True
    assert approval_payload["approval"]["pending_approval"]["step_name"] == "review"
    assert any(item["type"] == "approval_required" for item in approval_payload["blockers"])


@pytest.mark.asyncio
async def test_doctor_reports_runtime_and_applies_safe_fix(operator_service: BridgeOperatorService):
    runtime_payload = await operator_service.doctor()
    assert runtime_payload["ok"] is True
    assert runtime_payload["scope"] == "runtime"
    assert runtime_payload["fix"]["applied"] is False

    workflow_payload = await operator_service.doctor(issue_number="101", apply_fix=True)
    assert workflow_payload["ok"] is True
    assert workflow_payload["scope"] == "workflow"
    assert workflow_payload["fix"]["requested"] is True
    assert workflow_payload["fix"]["action"] == "continue_workflow"


@pytest.mark.asyncio
async def test_doctor_runtime_fix_attempts_openclaw_recovery(
    operator_service: BridgeOperatorService,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(
        operator_service,
        "_openclaw_health_snapshot",
        lambda: {
            "installed": True,
            "binary": "/usr/bin/openclaw",
            "healthy": False,
            "checks": [{"name": "openclaw_gateway", "status": "error"}],
        },
    )

    def _fake_run(cmd, timeout=20):
        cmd_text = " ".join(cmd)
        if "gateway restart" in cmd_text:
            return {"ok": True, "command": cmd_text, "returncode": 0, "stdout": "ok", "stderr": ""}
        if "gateway start" in cmd_text:
            return {"ok": True, "command": cmd_text, "returncode": 0, "stdout": "ok", "stderr": ""}
        if "system event" in cmd_text:
            return {"ok": True, "command": cmd_text, "returncode": 0, "stdout": "ok", "stderr": ""}
        return {"ok": True, "command": cmd_text, "returncode": 0, "stdout": "ok", "stderr": ""}

    monkeypatch.setattr(operator_service, "_run_cli_command", _fake_run)

    payload = await operator_service.doctor(apply_fix=True, target="session")

    assert payload["ok"] is True
    assert payload["scope"] == "runtime"
    assert payload["target"] == "session"
    assert payload["fix"]["requested"] is True
    assert payload["fix"]["action"] == "openclaw_recovery"
    assert payload["fix"]["applied"] is True
    assert any(item.get("name") == "gateway_restart" for item in payload["fix"]["actions"])


@pytest.mark.asyncio
async def test_workflow_summary_includes_blocker_context_for_pending_approval(operator_service: BridgeOperatorService):
    payload = await operator_service.workflow_summary(issue_number="105")

    assert payload["ok"] is True
    assert payload["blocking"] is True
    assert payload["approval"]["pending_approval"]["step_name"] == "review"
    assert any(item["type"] == "approval_required" for item in payload["blockers"])
    assert "approval_pending=2:review" in payload["summary"]
    assert "waiting for approval" in str(payload["reason"]).lower()
    assert "inspect blockers" in payload["suggested_actions"]


@pytest.mark.asyncio
async def test_workflow_summary_includes_blocker_context_for_paused_workflow(operator_service: BridgeOperatorService):
    payload = await operator_service.workflow_summary(issue_number="102")

    assert payload["ok"] is True
    assert payload["blocking"] is True
    assert any(item["type"] == "workflow_paused" for item in payload["blockers"])
    assert "paused" in str(payload["reason"]).lower()
    assert "inspect blockers" in payload["suggested_actions"]
