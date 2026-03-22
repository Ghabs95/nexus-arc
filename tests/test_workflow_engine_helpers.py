from nexus.core.models import Agent, StepStatus, Workflow, WorkflowState, WorkflowStep
from nexus.core.autofix_learning import (
    build_error_fingerprint,
    find_similar_autofix_attempts,
    is_autofix_candidate,
)
from nexus.core.workflow_engine.completion_service import (
    apply_step_completion_result,
    apply_retry_transition,
    compute_retry_backoff_seconds,
)
from nexus.core.workflow_engine.condition_eval import evaluate_condition
from nexus.core.workflow_engine.transition_service import reset_step_for_goto, resolve_route_target
from nexus.core.workflow_engine.workflow_definition_loader import (
    build_dry_run_report_fields,
    build_prompt_context_text,
    canonicalize_next_agent_from_steps,
    resolve_next_agent_types_from_steps,
    resolve_workflow_steps_list,
)


def _make_step() -> WorkflowStep:
    return WorkflowStep(
        step_num=1,
        name="develop",
        agent=Agent(name="developer", display_name="Developer", description="d"),
        prompt_template="do it",
    )


def _make_workflow(step: WorkflowStep) -> Workflow:
    return Workflow(
        id="wf-1",
        name="WF",
        description="d",
        version="1",
        state=WorkflowState.RUNNING,
        steps=[step],
    )


def test_evaluate_condition_supports_yaml_literals():
    assert evaluate_condition("true and x == 1", {"x": 1}) is True
    assert evaluate_condition("false", {}) is False
    assert evaluate_condition("null is None", {}) is True


def test_evaluate_condition_defaults_on_error():
    assert evaluate_condition("missing + 1", {}, default_on_error=True) is True
    assert evaluate_condition("missing + 1", {}, default_on_error=False) is False


def test_compute_retry_backoff_seconds_strategies():
    assert (
        compute_retry_backoff_seconds(
            retry_count=1, strategy="exponential", initial_delay=0.0, default_base=1.0
        )
        == 1.0
    )
    assert (
        compute_retry_backoff_seconds(
            retry_count=3, strategy="linear", initial_delay=2.0, default_base=1.0
        )
        == 6.0
    )
    assert (
        compute_retry_backoff_seconds(
            retry_count=2, strategy="constant", initial_delay=3.0, default_base=1.0
        )
        == 3.0
    )


def test_apply_retry_transition_requeues_step_until_limit():
    step = _make_step()
    workflow = _make_workflow(step)
    step.retry = 2
    step.retry_count = 0
    step.status = StepStatus.RUNNING
    step.completed_at = object()  # type: ignore[assignment]
    step.error = "boom"

    will_retry, backoff, max_retries = apply_retry_transition(
        workflow,
        step,
        error="boom",
        default_backoff_base=1.0,
    )

    assert will_retry is True
    assert backoff == 1.0
    assert max_retries == 2
    assert step.status == StepStatus.PENDING
    assert step.retry_count == 1
    assert step.completed_at is None
    assert step.error is None


def test_apply_retry_transition_marks_failed_at_limit():
    step = _make_step()
    workflow = _make_workflow(step)
    step.retry = 1
    step.retry_count = 1
    step.status = StepStatus.RUNNING

    will_retry, backoff, max_retries = apply_retry_transition(
        workflow,
        step,
        error="boom",
        default_backoff_base=1.0,
    )

    assert will_retry is False
    assert backoff is None
    assert max_retries == 1
    assert step.status == StepStatus.FAILED


async def test_apply_step_completion_result_success_emits_step_completed():
    step = _make_step()
    workflow = _make_workflow(step)
    emitted: list[object] = []
    audits: list[tuple[str, str, dict]] = []
    saved: list[str] = []

    result = await apply_step_completion_result(
        workflow=workflow,
        workflow_id="wf-1",
        step=step,
        step_num=1,
        outputs={"ok": True},
        error=None,
        default_backoff_base=1.0,
        save_workflow=lambda wf: (saved.append(wf.id), __import__("asyncio").sleep(0))[1],
        audit=lambda wid, et, payload: (
            audits.append((wid, et, payload)),
            __import__("asyncio").sleep(0),
        )[1],
        emit=lambda event: (emitted.append(event), __import__("asyncio").sleep(0))[1],
    )

    assert result.retry_handled is False
    assert result.has_error is False
    assert step.status == StepStatus.COMPLETED
    assert step.outputs == {"ok": True}
    assert len(emitted) == 1
    assert emitted[0].__class__.__name__ == "StepCompleted"
    assert saved == []
    assert audits == []


async def test_apply_step_completion_result_retry_persists_and_audits():
    step = _make_step()
    workflow = _make_workflow(step)
    step.retry = 2
    step.retry_count = 0
    step.status = StepStatus.RUNNING
    emitted: list[object] = []
    audits: list[tuple[str, str, dict]] = []
    saved: list[str] = []

    result = await apply_step_completion_result(
        workflow=workflow,
        workflow_id="wf-1",
        step=step,
        step_num=1,
        outputs={},
        error="boom",
        default_backoff_base=1.0,
        save_workflow=lambda wf: (saved.append(wf.id), __import__("asyncio").sleep(0))[1],
        audit=lambda wid, et, payload: (
            audits.append((wid, et, payload)),
            __import__("asyncio").sleep(0),
        )[1],
        emit=lambda event: (emitted.append(event), __import__("asyncio").sleep(0))[1],
    )

    assert result.retry_handled is True
    assert result.has_error is True
    assert saved == ["wf-1"]
    assert audits and audits[0][1] == "STEP_RETRY"
    assert emitted == []


async def test_apply_step_completion_result_autofix_retry_emits_learning_events():
    step = _make_step()
    step.name = "autofix_patch"
    workflow = _make_workflow(step)
    step.retry = 2
    step.retry_count = 0
    step.status = StepStatus.RUNNING
    audits: list[tuple[str, str, dict]] = []

    result = await apply_step_completion_result(
        workflow=workflow,
        workflow_id="wf-1",
        step=step,
        step_num=1,
        outputs={"fix_strategy": "update import path"},
        error="ImportError: cannot import name Foo from /tmp/project/app.py",
        default_backoff_base=1.0,
        save_workflow=lambda wf: (__import__("asyncio").sleep(0)),
        audit=lambda wid, et, payload: (
            audits.append((wid, et, payload)),
            __import__("asyncio").sleep(0),
        )[1],
        emit=lambda event: (__import__("asyncio").sleep(0)),
    )

    assert result.retry_handled is True
    event_types = [entry[1] for entry in audits]
    assert event_types == ["AUTOFIX_ATTEMPTED", "STEP_RETRY", "AUTOFIX_FAILED"]
    autofix_payload = audits[0][2]
    assert autofix_payload["agent_type"] == "developer"
    assert autofix_payload["error_fingerprint"]


async def test_apply_step_completion_result_autofix_success_emits_validated_event():
    step = _make_step()
    workflow = _make_workflow(step)
    audits: list[tuple[str, str, dict]] = []

    result = await apply_step_completion_result(
        workflow=workflow,
        workflow_id="wf-1",
        step=step,
        step_num=1,
        outputs={"fix_summary": "Added guard and updated tests"},
        error=None,
        default_backoff_base=1.0,
        save_workflow=lambda wf: (__import__("asyncio").sleep(0)),
        audit=lambda wid, et, payload: (
            audits.append((wid, et, payload)),
            __import__("asyncio").sleep(0),
        )[1],
        emit=lambda event: (__import__("asyncio").sleep(0)),
    )

    assert result.has_error is False
    assert [entry[1] for entry in audits] == ["AUTOFIX_VALIDATED"]


def test_build_error_fingerprint_normalizes_volatile_tokens():
    fingerprint = build_error_fingerprint(
        "ImportError: cannot import name Foo from /home/ubuntu/app/module.py at line 987"
    )
    assert "foo" in fingerprint
    assert "<path>" in fingerprint
    assert "<num>" in fingerprint


def test_is_autofix_candidate_detection_paths():
    assert (
        is_autofix_candidate(
            step_name="autofix_patch",
            agent_type="developer",
            outputs={},
            error=None,
        )
        is True
    )
    assert (
        is_autofix_candidate(
            step_name="develop",
            agent_type="developer",
            outputs={"fix_summary": "updated"},
            error=None,
        )
        is True
    )
    assert (
        is_autofix_candidate(
            step_name="design",
            agent_type="designer",
            outputs={},
            error=None,
        )
        is False
    )


def test_find_similar_autofix_attempts_filters_and_limits():
    events = [
        {
            "event_type": "AUTOFIX_ATTEMPTED",
            "data": {"agent_type": "developer", "error_fingerprint": "a"},
        },
        {
            "event_type": "AUTOFIX_FAILED",
            "data": {"agent_type": "developer", "error_fingerprint": "a"},
        },
        {
            "event_type": "AUTOFIX_VALIDATED",
            "data": {"agent_type": "developer", "error_fingerprint": "b"},
        },
    ]
    matches = find_similar_autofix_attempts(
        events,
        agent_type="developer",
        error_fingerprint="a",
        limit=1,
    )
    assert len(matches) == 1
    assert matches[0]["event_type"] in {"AUTOFIX_ATTEMPTED", "AUTOFIX_FAILED"}


def test_reset_step_for_goto_resets_state():
    step = _make_step()
    step.iteration = 0
    step.status = StepStatus.RUNNING
    step.started_at = object()  # type: ignore[assignment]
    step.completed_at = object()  # type: ignore[assignment]
    step.error = "err"
    step.outputs = {"a": 1}
    step.retry_count = 2

    reset_step_for_goto(step, max_loop_iterations=5)

    assert step.iteration == 1
    assert step.status == StepStatus.PENDING
    assert step.started_at is None
    assert step.completed_at is None
    assert step.error is None
    assert step.outputs == {}
    assert step.retry_count == 0


def test_reset_step_for_goto_raises_at_limit():
    step = _make_step()
    step.iteration = 5

    try:
        reset_step_for_goto(step, max_loop_iterations=5)
    except RuntimeError as exc:
        assert "re-activated 5 times" in str(exc)
    else:
        raise AssertionError("expected RuntimeError")


def test_resolve_route_target_matches_when_then_and_default():
    router = _make_step()
    router.name = "router"
    router.routes = [
        {"when": "approved", "then": "deploy"},
        {"default": True, "goto": "develop"},
    ]
    deploy = WorkflowStep(step_num=2, name="deploy", agent=router.agent, prompt_template="x")
    develop = WorkflowStep(step_num=3, name="develop", agent=router.agent, prompt_template="x")
    wf = Workflow(
        id="wf",
        name="wf",
        version="1",
        state=WorkflowState.RUNNING,
        steps=[router, deploy, develop],
    )

    def _find(workflow: Workflow, name: str) -> WorkflowStep | None:
        return next((s for s in workflow.steps if s.name == name), None)

    def _eval(cond: str | None, ctx: dict, default: bool) -> bool:
        return evaluate_condition(cond, ctx, default_on_error=default)

    target = resolve_route_target(
        workflow=wf,
        router_step=router,
        context={"approved": True},
        evaluate_condition=_eval,
        find_step_by_name=_find,
    )
    assert target is deploy

    fallback = resolve_route_target(
        workflow=wf,
        router_step=router,
        context={"approved": False},
        evaluate_condition=_eval,
        find_step_by_name=_find,
    )
    assert fallback is develop


def test_build_dry_run_report_fields_validates_and_simulates():
    data = {
        "name": "wf",
        "steps": [
            {"id": "s1", "name": "Design", "agent_type": "designer"},
            {"id": "s2", "name": "Router", "agent_type": "router"},
            {"id": "s3", "name": "Build", "agent_type": "developer", "condition": "true"},
            {"id": "s4", "name": "Bad", "condition": "x = 1"},
        ],
    }

    errors, predicted = build_dry_run_report_fields(
        data=data,
        workflow_type="",
        resolve_steps=lambda d, wt: d["steps"],
    )

    assert any("missing 'agent_type'" in e for e in errors)
    assert any("malformed condition expression" in e for e in errors)
    assert "RUN  Design (designer)" in predicted[0]
    assert any(line.startswith("RUN  Build (developer)") for line in predicted)


def test_build_dry_run_report_fields_handles_non_dict_input():
    errors, predicted = build_dry_run_report_fields(
        data=[],  # type: ignore[arg-type]
        workflow_type="",
        resolve_steps=lambda d, wt: [],
    )
    assert errors == ["Workflow definition must be a dict"]
    assert predicted == []


def test_build_prompt_context_text_skips_router_and_adds_display_names():
    text = build_prompt_context_text(
        steps=[
            {"id": "design", "name": "Design", "agent_type": "designer", "description": "Draft"},
            {"id": "route", "name": "Route", "agent_type": "router", "description": "Internal"},
            {"id": "build", "name": "Build", "agent_type": "developer", "description": "Code"},
            {"id": "build2", "name": "Build2", "agent_type": "developer", "description": "More"},
        ],
        yaml_basename="workflow.yaml",
        workflow_type="workflow:full",
        current_agent_type="designer",
        valid_next_agents=[],
    )

    assert "**Workflow Steps [workflow:full] (from workflow.yaml):**" in text
    assert "`router`" not in text
    assert "- 1. **Design** — `designer` : Draft" in text
    assert "- 3. **Build** — `developer` : Code" in text
    assert "`designer` → **Designer**" in text
    assert text.count("`developer` → **Developer**") == 1


def test_build_prompt_context_text_renders_single_next_agent_constraint():
    text = build_prompt_context_text(
        steps=[{"id": "design", "name": "Design", "agent_type": "designer", "description": ""}],
        yaml_basename="wf.yaml",
        workflow_type="",
        current_agent_type="designer",
        valid_next_agents=["developer"],
    )

    assert "**YOUR next_agent MUST be:** `developer`" in text
    assert "MUST be one of" not in text


def test_build_prompt_context_text_renders_multiple_next_agent_constraint():
    text = build_prompt_context_text(
        steps=[{"id": "design", "name": "Design", "agent_type": "designer", "description": ""}],
        yaml_basename="wf.yaml",
        workflow_type="",
        current_agent_type="designer",
        valid_next_agents=["qa", "developer"],
    )

    assert "**YOUR next_agent MUST be one of:** `qa`, `developer`" in text
    assert "Choose based on your classification." in text


def test_resolve_next_agent_types_from_steps_expands_router_and_dedupes():
    steps = [
        {"id": "triage", "agent_type": "triage", "on_success": "route"},
        {
            "id": "route",
            "agent_type": "router",
            "routes": [
                {"when": "x", "then": "dev"},
                {"default": "qa"},
                {"then": "developer"},
            ],
            "default": "qa",
        },
        {"id": "dev", "agent_type": "developer"},
        {"id": "qa", "agent_type": "qa"},
    ]

    assert resolve_next_agent_types_from_steps(steps=steps, current_agent_type="triage") == [
        "developer",
        "qa",
    ]


def test_canonicalize_next_agent_from_steps_maps_step_id_name_or_single_fallback():
    steps = [
        {"id": "develop", "name": "Build", "agent_type": "developer"},
        {"id": "qa", "name": "Review", "agent_type": "qa"},
    ]

    assert (
        canonicalize_next_agent_from_steps(
            steps=steps,
            candidate="develop",
            valid_next_agents=["developer", "qa"],
        )
        == "developer"
    )
    assert (
        canonicalize_next_agent_from_steps(
            steps=steps,
            candidate="review",
            valid_next_agents=["qa"],
        )
        == "qa"
    )
    assert (
        canonicalize_next_agent_from_steps(
            steps=steps,
            candidate="unknown",
            valid_next_agents=["qa"],
        )
        == "qa"
    )


def test_resolve_workflow_steps_list_prefers_tier_mapping_and_normalizes_hyphens():
    data = {
        "workflow_types": {"workflow:fast-track": "fast-track"},
        "fast_track_workflow": {"steps": [{"id": "a"}]},
        "steps": [{"id": "flat"}],
    }

    assert resolve_workflow_steps_list(data, "workflow:fast-track") == [{"id": "a"}]


def test_resolve_workflow_steps_list_falls_back_to_flat_then_first_tier():
    assert resolve_workflow_steps_list({"steps": [{"id": "flat"}]}, "") == [{"id": "flat"}]
    assert resolve_workflow_steps_list(
        {"x_workflow": {"steps": [{"id": "tier"}]}},
        "",
    ) == [{"id": "tier"}]


def test_resolve_workflow_steps_list_workflow_type_falls_back_to_flat():
    data = {"steps": [{"id": "flat"}]}
    assert resolve_workflow_steps_list(data, "full") == [{"id": "flat"}]


def test_resolve_workflow_steps_list_follows_router_target_file(tmp_path):
    router = tmp_path / "enterprise_workflow.yaml"
    full = tmp_path / "enterprise_full_workflow.yaml"

    router.write_text(
        "steps:\n"
        "  - id: triage\n"
        "    agent_type: triage\n"
        "    routes:\n"
        "      - when: \"selected == 'full'\"\n"
        "        then: enterprise_full_workflow\n",
        encoding="utf-8",
    )
    full.write_text(
        "steps:\n"
        "  - id: design\n"
        "    agent_type: designer\n",
        encoding="utf-8",
    )

    data = {
        "__yaml_path": str(router),
        "steps": [{"id": "triage", "routes": [{"then": "enterprise_full_workflow"}]}],
    }
    assert resolve_workflow_steps_list(data, "full") == [{"id": "design", "agent_type": "designer"}]


def test_resolve_workflow_steps_list_expands_orchestrator_external_routes(tmp_path):
    master = tmp_path / "master.yaml"
    feature = tmp_path / "new_feature_workflow.yaml"
    bug = tmp_path / "bug_fix_workflow.yaml"

    master.write_text(
        "steps:\n"
        "  - id: triage\n"
        "    agent_type: triage\n"
        "    on_success: dispatch\n"
        "  - id: dispatch\n"
        "    agent_type: router\n"
        "    routes:\n"
        "      - when: \"selected_workflow == 'new_feature'\"\n"
        "        then: new_feature_workflow\n"
        "      - default: bug_fix_workflow\n",
        encoding="utf-8",
    )
    feature.write_text(
        "steps:\n"
        "  - id: design\n"
        "    agent_type: designer\n"
        "    on_success: implement\n"
        "  - id: implement\n"
        "    agent_type: developer\n"
        "    final_step: true\n",
        encoding="utf-8",
    )
    bug.write_text(
        "steps:\n"
        "  - id: diagnose\n"
        "    agent_type: reviewer\n"
        "    final_step: true\n",
        encoding="utf-8",
    )

    resolved = resolve_workflow_steps_list(
        {
            "__yaml_path": str(master),
            "steps": [
                {"id": "triage", "agent_type": "triage", "on_success": "dispatch"},
                {
                    "id": "dispatch",
                    "agent_type": "router",
                    "routes": [
                        {
                            "when": "selected_workflow == 'new_feature'",
                            "then": "new_feature_workflow",
                        },
                        {"default": "bug_fix_workflow"},
                    ],
                },
            ],
        },
        "full",
    )

    ids = [str(step.get("id")) for step in resolved if isinstance(step, dict)]
    assert "triage" in ids
    assert "dispatch" in ids
    assert "new_feature_workflow__design" in ids
    assert "new_feature_workflow__implement" in ids
    assert "bug_fix_workflow__diagnose" in ids

    dispatch = next(step for step in resolved if isinstance(step, dict) and step.get("id") == "dispatch")
    assert dispatch["routes"][0]["then"] == "new_feature_workflow__design"
    assert dispatch["routes"][1]["default"] == "bug_fix_workflow__diagnose"
