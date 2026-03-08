from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Awaitable, Callable

from nexus.core.events import NexusEvent, StepSkipped, StepStarted
from nexus.core.models import StepStatus, Workflow, WorkflowState, WorkflowStep


def resolve_route_target(
    *,
    workflow: Workflow,
    router_step: WorkflowStep,
    context: dict[str, Any],
    evaluate_condition: Callable[[str | None, dict[str, Any], bool], bool],
    find_step_by_name: Callable[[Workflow, str], WorkflowStep | None],
) -> WorkflowStep | None:
    """Evaluate a router step and return the matched target step."""
    default_target: str | None = None
    for route in router_step.routes:
        when: str | None = route.get("when")
        target_name: str | None = route.get("goto") or route.get("then")
        is_default: bool = bool(route.get("default")) and not when
        if is_default:
            default_val = route.get("default")
            default_target = target_name or (default_val if isinstance(default_val, str) else None)
            continue
        if when and target_name and evaluate_condition(when, context, False):
            return find_step_by_name(workflow, target_name)
    if default_target:
        return find_step_by_name(workflow, default_target)
    return None


def reset_step_for_goto(step: WorkflowStep, *, max_loop_iterations: int) -> None:
    """Reset a step for goto/loop re-execution with loop-iteration guard."""
    if step.iteration >= max_loop_iterations:
        raise RuntimeError(
            f"Step '{step.name}' has been re-activated {step.iteration} times "
            f"(limit {max_loop_iterations}). Aborting to prevent infinite loop."
        )
    step.iteration += 1
    step.status = StepStatus.PENDING
    step.started_at = None
    step.completed_at = None
    step.error = None
    step.outputs = {}
    step.retry_count = 0


@dataclass
class SuccessTransitionOutcome:
    activated_step: WorkflowStep | None = None
    goto_reset_error: str | None = None


async def advance_after_success(
    *,
    workflow: Workflow,
    workflow_id: str,
    completed_step: WorkflowStep,
    build_step_context: Callable[[Workflow], dict[str, Any]],
    find_step_by_name: Callable[[Workflow, str], WorkflowStep | None],
    reset_step_for_goto: Callable[[WorkflowStep], None],
    resolve_route: Callable[[Workflow, WorkflowStep, dict[str, Any]], WorkflowStep | None],
    evaluate_condition: Callable[[str | None, dict[str, Any]], bool],
    emit: Callable[[NexusEvent], Awaitable[None]],
    audit: Callable[[str, str, dict], Awaitable[None]],
) -> SuccessTransitionOutcome:
    """Advance workflow state after a successful step completion."""
    outcome = SuccessTransitionOutcome()

    context = build_step_context(workflow)
    next_step: WorkflowStep | None = None
    if completed_step.on_success:
        next_step = find_step_by_name(workflow, completed_step.on_success)
        if next_step is not None and next_step.status != StepStatus.PENDING:
            try:
                reset_step_for_goto(next_step)
            except RuntimeError as exc:
                outcome.goto_reset_error = str(exc)
                return outcome

    if next_step is None:
        next_step = workflow.get_next_step()

    while next_step:
        if next_step.routes:
            next_step.status = StepStatus.SKIPPED
            next_step.completed_at = datetime.now(UTC)
            await audit(
                workflow_id,
                "STEP_SKIPPED",
                {
                    "step_num": next_step.step_num,
                    "step_name": next_step.name,
                    "reason": "router evaluated",
                },
            )
            await emit(
                StepSkipped(
                    workflow_id=workflow_id,
                    step_num=next_step.step_num,
                    step_name=next_step.name,
                    agent_type=next_step.agent.name,
                    reason="router evaluated",
                )
            )
            workflow.current_step = next_step.step_num
            target = resolve_route(workflow, next_step, context)
            if target is None:
                workflow.state = WorkflowState.COMPLETED
                workflow.completed_at = datetime.now(UTC)
                return outcome
            try:
                reset_step_for_goto(target)
            except RuntimeError as exc:
                outcome.goto_reset_error = str(exc)
                return outcome
            next_step = target
            continue

        if evaluate_condition(next_step.condition, context):
            workflow.current_step = next_step.step_num
            next_step.status = StepStatus.RUNNING
            next_step.started_at = datetime.now(UTC)
            outcome.activated_step = next_step
            await emit(
                StepStarted(
                    workflow_id=workflow_id,
                    step_num=next_step.step_num,
                    step_name=next_step.name,
                    agent_type=next_step.agent.name,
                )
            )
            return outcome

        next_step.status = StepStatus.SKIPPED
        next_step.completed_at = datetime.now(UTC)
        await audit(
            workflow_id,
            "STEP_SKIPPED",
            {
                "step_num": next_step.step_num,
                "step_name": next_step.name,
                "condition": next_step.condition,
                "reason": f"Condition evaluated to False: {next_step.condition}",
            },
        )
        await emit(
            StepSkipped(
                workflow_id=workflow_id,
                step_num=next_step.step_num,
                step_name=next_step.name,
                agent_type=next_step.agent.name,
                reason=f"Condition evaluated to False: {next_step.condition}",
            )
        )
        workflow.current_step = next_step.step_num
        next_step = workflow.get_next_step()

    workflow.state = WorkflowState.COMPLETED
    workflow.completed_at = datetime.now(UTC)
    return outcome
