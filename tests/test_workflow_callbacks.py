"""Tests for WorkflowEngine transition callbacks and WorkflowDefinition.to_prompt_context()."""

import os
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

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
from nexus.core.workflow import WorkflowDefinition, WorkflowEngine

# ---------------------------------------------------------------------------
# Helpers (reused from test_conditional_steps)
# ---------------------------------------------------------------------------


def make_agent(name: str = "test_agent") -> Agent:
    return Agent(name=name, display_name=name, description="test", timeout=60, max_retries=1)


def make_step(step_num: int, name: str, condition: str | None = None) -> WorkflowStep:
    return WorkflowStep(
        step_num=step_num,
        name=name,
        agent=make_agent(name),
        prompt_template="do something",
        condition=condition,
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
    if steps:
        steps[0].status = StepStatus.RUNNING
        steps[0].started_at = datetime.now(UTC)
    return wf


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

    async def save_agent_metadata(self, wid: str, name: str, meta: dict[str, Any]) -> None:
        pass

    async def get_agent_metadata(self, wid: str, name: str) -> dict[str, Any] | None:
        return None

    async def cleanup_old_workflows(self, older_than_days: int = 30) -> int:
        return 0


# ---------------------------------------------------------------------------
# Transition callback tests
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_on_step_transition_called():
    """on_step_transition fires when the next step is activated."""
    callback = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_step_transition=callback)

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"result": "ok"})

    callback.assert_awaited_once()
    args = callback.call_args[0]
    assert args[0].id == "wf-test"  # workflow
    assert args[1].step_num == 2  # next_step
    assert args[2] == {"result": "ok"}  # outputs


@pytest.mark.asyncio
async def test_on_workflow_complete_called():
    """on_workflow_complete fires when the last step finishes."""
    callback = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_workflow_complete=callback)

    step1 = make_step(1, "only_step")
    wf = make_workflow([step1])
    await storage.save_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={"done": True})

    callback.assert_awaited_once()
    args = callback.call_args[0]
    assert args[0].state == WorkflowState.COMPLETED
    assert args[1] == {"done": True}


@pytest.mark.asyncio
async def test_on_step_transition_not_called_on_error():
    """Callbacks should NOT fire when a step fails."""
    transition_cb = AsyncMock()
    complete_cb = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(
        storage=storage,
        on_step_transition=transition_cb,
        on_workflow_complete=complete_cb,
    )

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    await engine.complete_step("wf-test", step_num=1, outputs={}, error="something broke")

    transition_cb.assert_not_awaited()
    complete_cb.assert_not_awaited()


@pytest.mark.asyncio
async def test_on_workflow_complete_called_after_skip():
    """on_workflow_complete fires when the only remaining step is skipped."""
    callback = AsyncMock()

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_workflow_complete=callback)

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "optional", condition="result['tier'] == 'high'")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    result = await engine.complete_step("wf-test", step_num=1, outputs={"tier": "low"})

    assert result.state == WorkflowState.COMPLETED
    callback.assert_awaited_once()


@pytest.mark.asyncio
async def test_callback_exception_does_not_crash_engine():
    """If a callback raises, the engine should log but not propagate the error."""
    callback = AsyncMock(side_effect=RuntimeError("callback boom"))

    storage = InMemoryStorage()
    engine = WorkflowEngine(storage=storage, on_step_transition=callback)

    step1 = make_step(1, "analyze")
    step2 = make_step(2, "implement")
    wf = make_workflow([step1, step2])
    await storage.save_workflow(wf)

    # Should not raise
    result = await engine.complete_step("wf-test", step_num=1, outputs={})
    assert result.steps[1].status == StepStatus.RUNNING


# ---------------------------------------------------------------------------
# to_prompt_context tests
# ---------------------------------------------------------------------------


class TestToPromptContext:
    def test_renders_steps(self, tmp_path):
        workflow_yaml = tmp_path / "workflow.yaml"
        workflow_yaml.write_text(
            "name: Test Workflow\n"
            "steps:\n"
            "  - id: triage\n"
            "    name: Triage Issue\n"
            "    agent_type: triage\n"
            "    description: Classify severity\n"
            "  - id: implement\n"
            "    name: Implement Fix\n"
            "    agent_type: debug\n"
            "    description: Fix the bug\n"
        )
        result = WorkflowDefinition.to_prompt_context(str(workflow_yaml))
        assert "Triage Issue" in result
        assert "`triage`" in result
        assert "`debug`" in result
        assert "CRITICAL" in result

    def test_skips_router_steps(self, tmp_path):
        workflow_yaml = tmp_path / "workflow.yaml"
        workflow_yaml.write_text(
            "name: Test\n"
            "steps:\n"
            "  - id: route\n"
            "    name: Route\n"
            "    agent_type: router\n"
            "    description: Pick path\n"
            "  - id: impl\n"
            "    name: Implement\n"
            "    agent_type: debug\n"
            "    description: Do work\n"
        )
        result = WorkflowDefinition.to_prompt_context(str(workflow_yaml))
        assert "router" not in result.lower().split("agent_type")[0]  # router step omitted
        assert "`debug`" in result

    def test_returns_empty_on_missing_file(self):
        result = WorkflowDefinition.to_prompt_context("/nonexistent/workflow.yaml")
        assert result == ""

    def test_returns_empty_on_no_steps(self, tmp_path):
        workflow_yaml = tmp_path / "workflow.yaml"
        workflow_yaml.write_text("name: Empty\nsteps: []\n")
        result = WorkflowDefinition.to_prompt_context(str(workflow_yaml))
        assert result == ""


class TestResolveNextAgents:
    """Tests for WorkflowDefinition.resolve_next_agents()."""

    def _write_workflow(self, tmp_path):
        """Write a sample workflow with router for testing."""
        wf = tmp_path / "workflow.yaml"
        wf.write_text(
            "name: Test\n"
            "steps:\n"
            "  - id: triage\n"
            "    agent_type: triage\n"
            "    on_success: router\n"
            "  - id: router\n"
            "    agent_type: router\n"
            "    routes:\n"
            "      - when: \"type == 'bug'\"\n"
            "        then: debug\n"
            "      - when: \"type == 'feature'\"\n"
            "        then: design\n"
            "  - id: debug\n"
            "    agent_type: debug\n"
            "    on_success: develop\n"
            "  - id: design\n"
            "    agent_type: design\n"
            "    on_success: develop\n"
            "  - id: develop\n"
            "    agent_type: developer\n"
            "    on_success: close\n"
            "  - id: close\n"
            "    agent_type: summarizer\n"
            "    final_step: true\n"
        )
        return str(wf)

    def test_triage_routes_through_router(self, tmp_path):
        path = self._write_workflow(tmp_path)
        result = WorkflowDefinition.resolve_next_agents(path, "triage")
        assert "debug" in result
        assert "design" in result
        assert "developer" not in result

    def test_linear_step_single_next(self, tmp_path):
        path = self._write_workflow(tmp_path)
        assert WorkflowDefinition.resolve_next_agents(path, "debug") == ["developer"]
        assert WorkflowDefinition.resolve_next_agents(path, "design") == ["developer"]
        assert WorkflowDefinition.resolve_next_agents(path, "developer") == ["summarizer"]

    def test_final_step_returns_none(self, tmp_path):
        path = self._write_workflow(tmp_path)
        assert WorkflowDefinition.resolve_next_agents(path, "summarizer") == ["none"]

    def test_unknown_agent_returns_empty(self, tmp_path):
        path = self._write_workflow(tmp_path)
        assert WorkflowDefinition.resolve_next_agents(path, "nonexistent") == []

    def test_missing_file_returns_empty(self):
        assert WorkflowDefinition.resolve_next_agents("/no/such/file.yaml", "triage") == []

    def test_prompt_context_includes_constraint(self, tmp_path):
        path = self._write_workflow(tmp_path)
        text = WorkflowDefinition.to_prompt_context(path, current_agent_type="debug")
        assert "MUST be:" in text
        assert "`developer`" in text

    def test_prompt_context_multi_choice_constraint(self, tmp_path):
        path = self._write_workflow(tmp_path)
        text = WorkflowDefinition.to_prompt_context(path, current_agent_type="triage")
        assert "MUST be one of:" in text
        assert "`debug`" in text
        assert "`design`" in text

    def test_canonicalize_next_agent_exact_match(self, tmp_path):
        path = self._write_workflow(tmp_path)
        value = WorkflowDefinition.canonicalize_next_agent(path, "debug", "developer")
        assert value == "developer"

    def test_canonicalize_next_agent_strips_mention(self, tmp_path):
        path = self._write_workflow(tmp_path)
        value = WorkflowDefinition.canonicalize_next_agent(path, "debug", "@developer")
        assert value == "developer"

    def test_canonicalize_next_agent_maps_step_id(self, tmp_path):
        path = self._write_workflow(tmp_path)
        value = WorkflowDefinition.canonicalize_next_agent(path, "debug", "develop")
        assert value == "developer"

    def test_canonicalize_next_agent_uses_single_successor_fallback(self, tmp_path):
        path = self._write_workflow(tmp_path)
        value = WorkflowDefinition.canonicalize_next_agent(path, "debug", "fix")
        assert value == "developer"

    def test_canonicalize_next_agent_returns_empty_when_ambiguous(self, tmp_path):
        path = self._write_workflow(tmp_path)
        value = WorkflowDefinition.canonicalize_next_agent(path, "triage", "fix")
        assert value == ""

    def test_canonicalize_next_agent_terminal_value(self, tmp_path):
        path = self._write_workflow(tmp_path)
        value = WorkflowDefinition.canonicalize_next_agent(path, "developer", "none")
        assert value == "none"


# ---------------------------------------------------------------------------
# Multi-tier workflow tests
# ---------------------------------------------------------------------------

TIERED_WORKFLOW = (
    "name: Tiered Test\n"
    "workflow_types:\n"
    "  full: full\n"
    "  shortened: shortened\n"
    "  fast-track: fast-track\n"
    "full_workflow:\n"
    "  steps:\n"
    "    - id: vision\n"
    "      agent_type: ceo\n"
    "      on_success: feasibility\n"
    "    - id: feasibility\n"
    "      agent_type: cto\n"
    "      on_success: implement\n"
    "    - id: implement\n"
    "      agent_type: developer\n"
    "      on_success: qa\n"
    "    - id: qa\n"
    "      agent_type: qa\n"
    "      final_step: true\n"
    "shortened_workflow:\n"
    "  steps:\n"
    "    - id: triage\n"
    "      agent_type: lead\n"
    "      on_success: fix\n"
    "    - id: fix\n"
    "      agent_type: developer\n"
    "      on_success: verify\n"
    "    - id: verify\n"
    "      agent_type: qa\n"
    "      final_step: true\n"
    "fast_track_workflow:\n"
    "  steps:\n"
    "    - id: hotfix_triage\n"
    "      agent_type: lead\n"
    "      on_success: hotfix_impl\n"
    "    - id: hotfix_impl\n"
    "      agent_type: developer\n"
    "      final_step: true\n"
)


class TestMultiTierWorkflow:
    """Tests for multi-tier workflow support (_resolve_steps, from_yaml, etc.)."""

    def _write_tiered(self, tmp_path):
        wf = tmp_path / "tiered.yaml"
        wf.write_text(TIERED_WORKFLOW)
        return str(wf)

    # -- _resolve_steps --

    def test_resolve_full_tier(self, tmp_path):
        import yaml

        data = yaml.safe_load(TIERED_WORKFLOW)
        steps = WorkflowDefinition._resolve_steps(data, "full")
        assert len(steps) == 4
        assert steps[0]["agent_type"] == "ceo"

    def test_resolve_shortened_tier(self, tmp_path):
        import yaml

        data = yaml.safe_load(TIERED_WORKFLOW)
        steps = WorkflowDefinition._resolve_steps(data, "shortened")
        assert len(steps) == 3
        assert steps[0]["agent_type"] == "lead"

    def test_resolve_fast_track_tier(self, tmp_path):
        import yaml

        data = yaml.safe_load(TIERED_WORKFLOW)
        steps = WorkflowDefinition._resolve_steps(data, "fast-track")
        assert len(steps) == 2
        assert steps[0]["agent_type"] == "lead"

    def test_resolve_no_type_falls_back_to_first_tier(self, tmp_path):
        """Without workflow_type and no flat steps, falls back to first tier."""
        import yaml

        data = yaml.safe_load(TIERED_WORKFLOW)
        steps = WorkflowDefinition._resolve_steps(data, "")
        assert len(steps) == 4  # full_workflow is first

    def test_resolve_invalid_type_returns_empty(self, tmp_path):
        import yaml

        data = yaml.safe_load(TIERED_WORKFLOW)
        steps = WorkflowDefinition._resolve_steps(data, "nonexistent")
        assert steps == []

    def test_resolve_noncanonical_type_returns_empty(self, tmp_path):
        """Non-canonical workflow_type values are not resolved."""
        import yaml

        data = yaml.safe_load(TIERED_WORKFLOW)
        steps = WorkflowDefinition._resolve_steps(data, "bug_fix")
        assert steps == []

    def test_flat_steps_preferred_when_present(self, tmp_path):
        import yaml

        data = yaml.safe_load(TIERED_WORKFLOW)
        data["steps"] = [{"id": "flat", "agent_type": "flat_agent"}]
        steps = WorkflowDefinition._resolve_steps(data, "")
        assert len(steps) == 1
        assert steps[0]["agent_type"] == "flat_agent"

    # -- from_yaml with workflow_type --

    def test_from_yaml_full(self, tmp_path):
        path = self._write_tiered(tmp_path)
        wf = WorkflowDefinition.from_yaml(path, workflow_type="full")
        assert len(wf.steps) == 4
        assert wf.steps[0].agent.name == "ceo"

    def test_from_yaml_shortened(self, tmp_path):
        path = self._write_tiered(tmp_path)
        wf = WorkflowDefinition.from_yaml(path, workflow_type="shortened")
        assert len(wf.steps) == 3
        assert wf.steps[0].agent.name == "lead"

    def test_from_yaml_fast_track(self, tmp_path):
        path = self._write_tiered(tmp_path)
        wf = WorkflowDefinition.from_yaml(path, workflow_type="fast-track")
        assert len(wf.steps) == 2

    def test_from_yaml_invalid_tier_raises(self, tmp_path):
        path = self._write_tiered(tmp_path)
        with pytest.raises(ValueError, match="non-empty steps"):
            WorkflowDefinition.from_yaml(path, workflow_type="nonexistent")

    # -- resolve_next_agents with workflow_type --

    def test_resolve_next_in_full_tier(self, tmp_path):
        path = self._write_tiered(tmp_path)
        assert WorkflowDefinition.resolve_next_agents(path, "ceo", "full") == ["cto"]
        assert WorkflowDefinition.resolve_next_agents(path, "cto", "full") == ["developer"]
        assert WorkflowDefinition.resolve_next_agents(path, "qa", "full") == ["none"]

    def test_resolve_next_in_shortened_tier(self, tmp_path):
        path = self._write_tiered(tmp_path)
        assert WorkflowDefinition.resolve_next_agents(path, "lead", "shortened") == ["developer"]
        assert WorkflowDefinition.resolve_next_agents(path, "developer", "shortened") == ["qa"]

    def test_resolve_next_cross_tier_isolation(self, tmp_path):
        """Agent types from one tier shouldn't leak into another."""
        path = self._write_tiered(tmp_path)
        # 'ceo' only exists in full tier
        assert WorkflowDefinition.resolve_next_agents(path, "ceo", "shortened") == []

    # -- to_prompt_context with workflow_type --

    def test_prompt_context_includes_tier_label(self, tmp_path):
        path = self._write_tiered(tmp_path)
        text = WorkflowDefinition.to_prompt_context(path, workflow_type="shortened")
        assert "[shortened]" in text
        assert "`lead`" in text

    def test_prompt_context_constraint_with_tier(self, tmp_path):
        path = self._write_tiered(tmp_path)
        text = WorkflowDefinition.to_prompt_context(
            path, current_agent_type="lead", workflow_type="shortened"
        )
        assert "MUST be:" in text
        assert "`developer`" in text

    # -- enterprise workflow integration (example file) --

    def test_enterprise_workflow_loads_full(self):
        """Smoke test: load the enterprise workflow full tier."""
        path = os.path.join(
            os.path.dirname(__file__), "..", "examples", "workflows", "enterprise_full_workflow.yaml"
        )
        if not os.path.exists(path):
            pytest.skip("enterprise workflow not found")
        wf = WorkflowDefinition.from_yaml(path)
        assert len(wf.steps) > 5
        assert wf.steps[0].agent.name == "triage"

    def test_enterprise_workflow_loads_shortened(self):
        """Smoke test: load the enterprise workflow shortened tier."""
        path = os.path.join(
            os.path.dirname(__file__), "..", "examples", "workflows", "enterprise_shortened_workflow.yaml"
        )
        if not os.path.exists(path):
            pytest.skip("enterprise workflow not found")
        wf = WorkflowDefinition.from_yaml(path)
        assert len(wf.steps) >= 3
        assert wf.steps[0].agent.name == "triage"

    def test_enterprise_workflow_loads_fast_track(self):
        """Smoke test: load the enterprise workflow fast-track tier."""
        path = os.path.join(
            os.path.dirname(__file__), "..", "examples", "workflows", "enterprise_fast_track_workflow.yaml"
        )
        if not os.path.exists(path):
            pytest.skip("enterprise workflow not found")
        wf = WorkflowDefinition.from_yaml(path)
        assert len(wf.steps) >= 3
        # Last non-router step should be deployer
        non_router = [s for s in wf.steps if s.agent.name != "router"]
        assert non_router[-1].agent.name == "deployer"
