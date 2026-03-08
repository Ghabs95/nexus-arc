import logging
from datetime import UTC, datetime
from typing import Awaitable, Callable

from nexus.core.events import NexusEvent, WorkflowCompleted
from nexus.core.models import StepStatus, Workflow, WorkflowState, WorkflowStep

logger = logging.getLogger(__name__)


async def finalize_terminal_success(
    *,
    workflow: Workflow,
    workflow_id: str,
    step_num: int,
    step_name: str,
    outputs: dict,
    save_workflow: Callable[[Workflow], Awaitable[None]],
    audit: Callable[[str, str, dict], Awaitable[None]],
    emit: Callable[[NexusEvent], Awaitable[None]],
    on_workflow_complete: Callable[[Workflow, dict], Awaitable[None]] | None,
) -> Workflow:
    """Finalize a workflow when a completed step is marked final_step."""
    workflow.state = WorkflowState.COMPLETED
    workflow.completed_at = datetime.now(UTC)
    workflow.updated_at = datetime.now(UTC)
    await save_workflow(workflow)
    await audit(
        workflow_id,
        "STEP_COMPLETED",
        {"step_num": step_num, "step_name": step_name, "error": None},
    )
    logger.info("Completed step %s in workflow %s", step_num, workflow_id)
    await emit(
        WorkflowCompleted(
            workflow_id=workflow_id,
            total_steps=len(workflow.steps),
            completed_steps=sum(1 for s in workflow.steps if s.status == StepStatus.COMPLETED),
            failed_steps=sum(1 for s in workflow.steps if s.status == StepStatus.FAILED),
            skipped_steps=sum(1 for s in workflow.steps if s.status == StepStatus.SKIPPED),
        )
    )
    if on_workflow_complete:
        try:
            await on_workflow_complete(workflow, outputs)
        except Exception as exc:
            logger.error(
                "on_workflow_complete callback failed for workflow %s: %s", workflow_id, exc
            )
    return workflow


async def finalize_step_completion_tail(
    *,
    workflow: Workflow,
    workflow_id: str,
    step_num: int,
    step_name: str,
    outputs: dict,
    error: str | None,
    activated_step: WorkflowStep | None,
    save_workflow: Callable[[Workflow], Awaitable[None]],
    audit: Callable[[str, str, dict], Awaitable[None]],
    on_step_transition: Callable[[Workflow, WorkflowStep, dict], Awaitable[None]] | None,
    on_workflow_complete: Callable[[Workflow, dict], Awaitable[None]] | None,
) -> None:
    """Run final save/audit/log/callback tail for complete_step."""
    workflow.updated_at = datetime.now(UTC)
    await save_workflow(workflow)

    event_type = "STEP_FAILED" if error else "STEP_COMPLETED"
    await audit(
        workflow_id,
        event_type,
        {"step_num": step_num, "step_name": step_name, "error": error},
    )

    logger.info("Completed step %s in workflow %s", step_num, workflow_id)

    if not error and activated_step and on_step_transition:
        try:
            await on_step_transition(workflow, activated_step, outputs)
        except Exception as exc:
            logger.error(
                "on_step_transition callback failed for workflow %s, step %s: %s",
                workflow_id,
                activated_step.step_num,
                exc,
            )
    elif workflow.state == WorkflowState.COMPLETED and on_workflow_complete:
        try:
            await on_workflow_complete(workflow, outputs)
        except Exception as exc:
            logger.error(
                "on_workflow_complete callback failed for workflow %s: %s", workflow_id, exc
            )
