import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from nexus.core.autofix_learning import build_autofix_payload, is_autofix_candidate
from nexus.core.events import NexusEvent, StepCompleted, StepFailed
from nexus.core.models import StepStatus, Workflow, WorkflowStep

logger = logging.getLogger(__name__)


def compute_retry_backoff_seconds(
    *,
    retry_count: int,
    strategy: str | None,
    initial_delay: float,
    default_base: float,
    max_seconds: float = 60.0,
) -> float:
    """Compute retry backoff for a workflow step."""
    effective_strategy = strategy or "exponential"
    base = initial_delay if initial_delay > 0 else default_base
    if effective_strategy == "linear":
        return min(base * retry_count, max_seconds)
    if effective_strategy == "constant":
        return base
    return min(base * (2 ** (retry_count - 1)), max_seconds)


def apply_retry_transition(
    workflow: Workflow,
    step: WorkflowStep,
    *,
    error: str,
    default_backoff_base: float,
) -> tuple[bool, float | None, int]:
    """Apply retry transition state to a failed step.

    Returns ``(will_retry, backoff_seconds, max_retries)``.
    """
    max_retries = step.retry if step.retry is not None else step.agent.max_retries
    if step.retry_count >= max_retries:
        step.status = StepStatus.FAILED
        return False, None, max_retries

    step.retry_count += 1
    step.status = StepStatus.PENDING
    step.completed_at = None
    step.error = None
    backoff = compute_retry_backoff_seconds(
        retry_count=step.retry_count,
        strategy=step.backoff_strategy,
        initial_delay=step.initial_delay,
        default_base=default_backoff_base,
    )
    return True, backoff, max_retries


@dataclass
class StepCompletionApplyResult:
    retry_handled: bool
    has_error: bool


async def apply_step_completion_result(
    *,
    workflow: Workflow,
    workflow_id: str,
    step: WorkflowStep,
    step_num: int,
    outputs: dict[str, Any],
    error: str | None,
    default_backoff_base: float,
    save_workflow: Callable[[Workflow], Awaitable[None]],
    audit: Callable[[str, str, dict[str, Any]], Awaitable[None]],
    emit: Callable[[NexusEvent], Awaitable[None]],
) -> StepCompletionApplyResult:
    """Apply step outputs/error, emit step event, and handle retry path when needed."""
    step.completed_at = datetime.now(UTC)
    step.outputs = outputs
    step.error = error
    candidate = is_autofix_candidate(
        step_name=step.name,
        agent_type=step.agent.name,
        outputs=outputs,
        error=error,
    )

    if error:
        will_retry, backoff, max_retries = apply_retry_transition(
            workflow,
            step,
            error=error,
            default_backoff_base=default_backoff_base,
        )
        if candidate:
            await audit(
                workflow_id,
                "AUTOFIX_ATTEMPTED",
                build_autofix_payload(
                    step_num=step_num,
                    step_name=step.name,
                    agent_type=step.agent.name,
                    error=error,
                    retry_count=step.retry_count,
                    retry_planned=will_retry,
                    outputs=outputs,
                ),
            )
        if will_retry:
            workflow.updated_at = datetime.now(UTC)
            await save_workflow(workflow)
            await audit(
                workflow_id,
                "STEP_RETRY",
                {
                    "step_num": step_num,
                    "step_name": step.name,
                    "retry_count": step.retry_count,
                    "backoff_seconds": backoff,
                    "error": error,
                },
            )
            if candidate:
                await audit(
                    workflow_id,
                    "AUTOFIX_FAILED",
                    build_autofix_payload(
                        step_num=step_num,
                        step_name=step.name,
                        agent_type=step.agent.name,
                        error=error,
                        retry_count=step.retry_count,
                        retry_planned=True,
                        outputs=outputs,
                    ),
                )
            logger.info(
                "Retrying step %s in workflow %s (attempt %s/%s, backoff %ss)",
                step_num,
                workflow_id,
                step.retry_count,
                max_retries,
                backoff,
            )
            return StepCompletionApplyResult(retry_handled=True, has_error=True)

        await emit(
            StepFailed(
                workflow_id=workflow_id,
                step_num=step_num,
                step_name=step.name,
                agent_type=step.agent.name,
                error=error,
            )
        )
        if candidate:
            await audit(
                workflow_id,
                "AUTOFIX_FAILED",
                build_autofix_payload(
                    step_num=step_num,
                    step_name=step.name,
                    agent_type=step.agent.name,
                    error=error,
                    retry_count=step.retry_count,
                    retry_planned=False,
                    outputs=outputs,
                ),
            )
        return StepCompletionApplyResult(retry_handled=False, has_error=True)

    step.status = StepStatus.COMPLETED
    if candidate:
        await audit(
            workflow_id,
            "AUTOFIX_VALIDATED",
            build_autofix_payload(
                step_num=step_num,
                step_name=step.name,
                agent_type=step.agent.name,
                error=None,
                retry_count=step.retry_count,
                retry_planned=False,
                outputs=outputs,
            ),
        )
    await emit(
        StepCompleted(
            workflow_id=workflow_id,
            step_num=step_num,
            step_name=step.name,
            agent_type=step.agent.name,
            outputs=outputs,
        )
    )
    return StepCompletionApplyResult(retry_handled=False, has_error=False)
