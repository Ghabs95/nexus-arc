"""Basic workflow engine - simplified version for MVP."""

import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import yaml

from nexus.adapters.storage.base import StorageBackend
from nexus.core.events import (
    EventBus,
    StepStarted,
    WorkflowCancelled,
    WorkflowCompleted,
    WorkflowFailed,
    WorkflowPaused,
    WorkflowStarted,
    NexusEvent,
)
from nexus.core.models import (
    AuditEvent,
    DryRunReport,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.workflow_engine.audit_event_service import (
    finalize_step_completion_tail,
    finalize_terminal_success,
)
from nexus.core.workflow_engine.completion_service import apply_step_completion_result
from nexus.core.workflow_engine.condition_eval import evaluate_condition
from nexus.core.workflow_engine.transition_service import advance_after_success
from nexus.core.workflow_engine.transition_service import (
    reset_step_for_goto as reset_step_for_goto_impl,
    resolve_route_target,
)
from nexus.core.workflow_engine.workflow_definition_loader import (
    canonicalize_next_agent_from_steps,
    build_dry_run_report_fields,
    build_prompt_context_text,
    build_workflow_steps,
    parse_require_human_merge_approval,
    parse_require_approval_for,
    resolve_workflow_steps_list,
    resolve_next_agent_types_from_steps,
)

logger = logging.getLogger(__name__)

# Type aliases for transition callbacks.
OnStepTransition = Callable[[Workflow, WorkflowStep, dict], Awaitable[None]]
OnWorkflowComplete = Callable[[Workflow, dict], Awaitable[None]]

_MAX_LOOP_ITERATIONS = max(
    1, int(os.getenv("NEXUS_MAX_LOOP_ITERATIONS", "20"))
)  # Maximum times a step can be re-activated by a goto before aborting
_DEFAULT_BACKOFF_BASE = 1.0  # Default base delay (seconds) used when step.initial_delay is unset


class WorkflowEngine:
    """
    Core workflow orchestration engine.

    Handles workflow execution, state management, and step progression.
    """

    def __init__(
        self,
        storage: StorageBackend,
        on_step_transition: OnStepTransition | None = None,
        on_workflow_complete: OnWorkflowComplete | None = None,
        event_bus: EventBus | None = None,
    ):
        """
        Initialize workflow engine.

        Args:
            storage: Storage backend for persistence
            on_step_transition: Async callback invoked when the next step is ready.
                Signature: ``async (workflow, next_step, completed_step_outputs) -> None``
            on_workflow_complete: Async callback invoked when the workflow finishes.
                Signature: ``async (workflow, last_step_outputs) -> None``
            event_bus: Optional EventBus for reactive event emission.
        """
        self.storage = storage
        self._on_step_transition = on_step_transition
        self._on_workflow_complete = on_workflow_complete
        self._event_bus = event_bus

    async def create_workflow(self, workflow: Workflow) -> Workflow:
        """Create and persist a new workflow."""
        workflow.state = WorkflowState.PENDING
        workflow.created_at = datetime.now(UTC)
        workflow.updated_at = datetime.now(UTC)

        # Apply workflow-level approval gates to steps
        workflow.apply_approval_gates()

        await self.storage.save_workflow(workflow)
        await self._audit(workflow.id, "WORKFLOW_CREATED", {"name": workflow.name})

        logger.info(f"Created workflow {workflow.id}: {workflow.name}")
        return workflow

    async def get_workflow(self, workflow_id: str) -> Workflow | None:
        """Retrieve a workflow by ID."""
        workflow = await self.storage.load_workflow(workflow_id)
        if workflow:
            # Ensure approval gates are applied (in case workflow was saved before gates existed)
            workflow.apply_approval_gates()
        return workflow

    async def start_workflow(self, workflow_id: str) -> Workflow:
        """Start workflow execution."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        if workflow.state != WorkflowState.PENDING:
            raise ValueError(f"Cannot start workflow in state {workflow.state.value}")

        workflow.state = WorkflowState.RUNNING
        workflow.updated_at = datetime.now(UTC)
        workflow.current_step = 0

        first_step = workflow.get_step(1)
        if first_step and first_step.status == StepStatus.PENDING:
            first_step.status = StepStatus.RUNNING
            first_step.started_at = datetime.now(UTC)
            workflow.current_step = first_step.step_num
            await self._emit(
                StepStarted(
                    workflow_id=workflow_id,
                    step_num=first_step.step_num,
                    step_name=first_step.name,
                    agent_type=first_step.agent.name,
                )
            )

        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_STARTED", {})
        await self._emit(WorkflowStarted(workflow_id=workflow_id))

        logger.info(f"Started workflow {workflow_id}")
        return workflow

    async def pause_workflow(self, workflow_id: str) -> Workflow:
        """Pause workflow execution."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        if workflow.state != WorkflowState.RUNNING:
            raise ValueError(f"Cannot pause workflow in state {workflow.state.value}")

        workflow.state = WorkflowState.PAUSED
        workflow.updated_at = datetime.now(UTC)

        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_PAUSED", {})
        await self._emit(WorkflowPaused(workflow_id=workflow_id))

        logger.info(f"Paused workflow {workflow_id}")
        return workflow

    async def resume_workflow(self, workflow_id: str) -> Workflow:
        """Resume paused workflow."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        if workflow.state != WorkflowState.PAUSED:
            raise ValueError(f"Cannot resume workflow in state {workflow.state.value}")

        workflow.state = WorkflowState.RUNNING
        workflow.updated_at = datetime.now(UTC)

        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_RESUMED", {})

        logger.info(f"Resumed workflow {workflow_id}")
        return workflow

    async def cancel_workflow(self, workflow_id: str) -> Workflow:
        """Cancel workflow execution."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        if workflow.state not in (WorkflowState.RUNNING, WorkflowState.PAUSED):
            raise ValueError(f"Cannot cancel workflow in state {workflow.state.value}")

        workflow.state = WorkflowState.CANCELLED
        workflow.updated_at = datetime.now(UTC)
        workflow.completed_at = datetime.now(UTC)

        await self.storage.save_workflow(workflow)
        await self._audit(workflow_id, "WORKFLOW_CANCELLED", {})
        await self._emit(WorkflowCancelled(workflow_id=workflow_id))

        logger.info(f"Cancelled workflow {workflow_id}")
        return workflow

    async def complete_step(
        self, workflow_id: str, step_num: int, outputs: dict, error: str | None = None
    ) -> Workflow:
        """Mark a step as completed and advance workflow."""
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow:
            raise ValueError(f"Workflow {workflow_id} not found")

        step = workflow.get_step(step_num)
        if not step:
            raise ValueError(f"Step {step_num} not found in workflow")
        completion_result = await apply_step_completion_result(
            workflow=workflow,
            workflow_id=workflow_id,
            step=step,
            step_num=step_num,
            outputs=outputs,
            error=error,
            default_backoff_base=_DEFAULT_BACKOFF_BASE,
            save_workflow=self.storage.save_workflow,
            audit=self._audit,
            emit=self._emit,
        )
        if completion_result.retry_handled:
            return workflow

        activated_step: WorkflowStep | None = None

        if not completion_result.has_error:
            if step.final_step:
                return await finalize_terminal_success(
                    workflow=workflow,
                    workflow_id=workflow_id,
                    step_num=step_num,
                    step_name=step.name,
                    outputs=outputs,
                    save_workflow=self.storage.save_workflow,
                    audit=self._audit,
                    emit=self._emit,
                    on_workflow_complete=self._on_workflow_complete,
                )

            if step.on_success and self._find_step_by_name(workflow, step.on_success) is None:
                logger.warning(
                    "Step %s in workflow %s has unresolved on_success target '%s'; "
                    "falling back to sequential progression",
                    step.name,
                    workflow_id,
                    step.on_success,
                )

            transition_outcome = await advance_after_success(
                workflow=workflow,
                workflow_id=workflow_id,
                completed_step=step,
                build_step_context=self._build_step_context,
                find_step_by_name=self._find_step_by_name,
                reset_step_for_goto=self._reset_step_for_goto,
                resolve_route=self._resolve_route,
                evaluate_condition=self._evaluate_condition,
                emit=self._emit,
                audit=self._audit,
            )
            activated_step = transition_outcome.activated_step

            if transition_outcome.goto_reset_error:
                logger.error(transition_outcome.goto_reset_error)
                workflow.state = WorkflowState.FAILED
                workflow.completed_at = datetime.now(UTC)
                await self.storage.save_workflow(workflow)
                return workflow

            if workflow.state == WorkflowState.COMPLETED and activated_step is None:
                _total = len(workflow.steps)
                _completed = sum(1 for s in workflow.steps if s.status == StepStatus.COMPLETED)
                _failed = sum(1 for s in workflow.steps if s.status == StepStatus.FAILED)
                _skipped = sum(1 for s in workflow.steps if s.status == StepStatus.SKIPPED)
                await self._emit(
                    WorkflowCompleted(
                        workflow_id=workflow_id,
                        total_steps=_total,
                        completed_steps=_completed,
                        failed_steps=_failed,
                        skipped_steps=_skipped,
                    )
                )
        else:
            # Workflow failed
            workflow.state = WorkflowState.FAILED
            workflow.completed_at = datetime.now(UTC)
            await self._emit(
                WorkflowFailed(
                    workflow_id=workflow_id,
                    error=error or "Unknown error",
                )
            )

        await finalize_step_completion_tail(
            workflow=workflow,
            workflow_id=workflow_id,
            step_num=step_num,
            step_name=step.name,
            outputs=outputs,
            error=error,
            activated_step=activated_step,
            save_workflow=self.storage.save_workflow,
            audit=self._audit,
            on_step_transition=self._on_step_transition,
            on_workflow_complete=self._on_workflow_complete,
        )

        return workflow

    @staticmethod
    def render_prompt(template: str, context: dict[str, Any]) -> str:
        """Substitute ``{variable}`` placeholders in *template* with values from *context*.

        Uses :py:meth:`str.format_map` with a safe mapping so that unknown
        placeholders are left intact rather than raising :py:exc:`KeyError`.

        Args:
            template: Prompt template string, e.g. ``"Fix issue: {description}"``.
            context: Mapping of variable names to replacement values.

        Returns:
            The rendered string with known placeholders replaced.
        """

        class _SafeDict(dict):  # type: ignore[type-arg]
            def __missing__(self, key: str) -> str:
                return "{" + key + "}"

        return template.format_map(_SafeDict(context))

    def _resolve_route(
        self, workflow: Workflow, router_step: WorkflowStep, context: dict[str, Any]
    ) -> WorkflowStep | None:
        """Evaluate a router step's routes and return the matching target WorkflowStep.

        Route dict supports either ``goto:`` (YAML convention) or ``then:`` (alias).
        A ``default: true`` entry is used when no ``when:`` clause matches.
        """
        return resolve_route_target(
            workflow=workflow,
            router_step=router_step,
            context=context,
            evaluate_condition=lambda cond, ctx, default: self._evaluate_condition(
                cond,
                ctx,
                default_on_error=default,
            ),
            find_step_by_name=self._find_step_by_name,
        )

    def _find_step_by_name(self, workflow: Workflow, name: str) -> WorkflowStep | None:
        """Find a step by its slugified id or agent_type name."""
        slug = WorkflowDefinition._slugify(name)
        for step in workflow.steps:
            if step.name == name or step.name == slug or step.agent.name == name:
                return step
        return None

    def _reset_step_for_goto(self, step: WorkflowStep) -> None:
        """Reset a step so it can be re-executed (loop / goto support).

        Raises RuntimeError if the step has already been re-activated
        _MAX_LOOP_ITERATIONS times to guard against infinite loops.
        """
        reset_step_for_goto_impl(step, max_loop_iterations=_MAX_LOOP_ITERATIONS)

    def _build_step_context(self, workflow: Workflow) -> dict[str, Any]:
        """Build evaluation context from all completed/skipped step outputs.

        The context contains:
        - ``<step_id>``: the outputs dict for that step (keyed by step id)
        - ``result``: alias for the most-recently-completed step's outputs
        - top-level keys from the most-recently-completed step's outputs,
          so simple YAML conditions like ``approval_status == 'approved'``
          work without needing to qualify the key with the step id.
        """
        context: dict[str, Any] = {}
        for step in workflow.steps:
            if step.status in (StepStatus.COMPLETED, StepStatus.SKIPPED):
                context[step.name] = step.outputs
                # Expose the most-recently-completed step as `result`
                if step.status == StepStatus.COMPLETED:
                    context["result"] = step.outputs
                    # Also flatten keys at the top level for ergonomic conditions
                    # e.g. `approval_status == 'approved'` instead of
                    # `result['approval_status'] == 'approved'`
                    context.update(step.outputs)
        return context

    def _evaluate_condition(
        self,
        condition: str | None,
        context: dict[str, Any],
        default_on_error: bool = True,
    ) -> bool:
        """
        Evaluate a Python expression against the step context.

        Returns True when:
        - condition is None or empty (no condition → always run)
        - the expression evaluates to a truthy value

        Returns False when the expression evaluates to a falsy value.
        Logs a warning and returns *default_on_error* if the expression raises.
        """
        return evaluate_condition(condition, context, default_on_error=default_on_error)

    async def get_audit_log(self, workflow_id: str) -> list:
        """Get audit log for a workflow."""
        return await self.storage.get_audit_log(workflow_id)

    async def get_runnable_steps(self, workflow_id: str) -> list[WorkflowStep]:
        """Return all steps that are currently ready to run in parallel.

        A step is *runnable* when it is in ``PENDING`` state and its step number
        equals ``workflow.current_step`` **or** its ``parallel_with`` list contains
        the name of the step at position ``workflow.current_step`` (indicating it
        is part of a parallel group).

        This method does not mutate workflow state; callers must explicitly start
        each step via the normal engine flow.

        Args:
            workflow_id: ID of the target workflow.

        Returns:
            List of :class:`~nexus.core.models.WorkflowStep` objects that can be
            started concurrently.  Returns an empty list when the workflow is not
            found or is not running.
        """
        workflow = await self.storage.load_workflow(workflow_id)
        if not workflow or workflow.state != WorkflowState.RUNNING:
            return []

        current = workflow.get_step(workflow.current_step)
        if current is None:
            return []

        runnable: list[WorkflowStep] = []

        # Include the current step if it is still pending (not yet started)
        if current.status == StepStatus.PENDING:
            runnable.append(current)

        # Include any pending steps that declare themselves parallel to the current step
        current_name = current.name
        for step in workflow.steps:
            if step.step_num == workflow.current_step:
                continue  # Already handled above
            if step.status == StepStatus.PENDING and current_name in step.parallel_with:
                runnable.append(step)

        return runnable

    async def _audit(self, workflow_id: str, event_type: str, data: dict) -> None:
        """Add audit event."""
        event = AuditEvent(
            workflow_id=workflow_id, timestamp=datetime.now(UTC), event_type=event_type, data=data
        )
        await self.storage.append_audit_event(event)

    async def _emit(
        self,
        event: NexusEvent,
    ) -> None:
        """Emit event to EventBus if configured."""
        if self._event_bus:
            try:
                await self._event_bus.emit(event)
            except Exception as exc:
                event_type = getattr(event, "event_type", type(event).__name__)
                logger.warning("EventBus emit failed for %s: %s", event_type, exc)


class WorkflowDefinition:
    """
    Workflow definition loader (from YAML/JSON).

    Provides a minimal YAML-based loader that maps a workflow definition
    into Nexus Core's Workflow/WorkflowStep models.
    """

    _TERMINAL_NEXT_AGENT_VALUES = {
        "none",
        "n/a",
        "null",
        "no",
        "end",
        "done",
        "finish",
        "complete",
        "",
    }

    @staticmethod
    def normalize_workflow_type(tier_name: str, default: str = "shortened") -> str:
        """Normalize a workflow type string.

        Strips whitespace and lowercases.  Returns *default* when the
        input is empty.  No validation against a fixed list — the
        workflow definition YAML is the source of truth for valid types.
        """
        cleaned = tier_name.strip().lower()
        return cleaned if cleaned else default

    @staticmethod
    def _resolve_steps(data: dict[str, Any], workflow_type: str = "") -> list[dict]:
        """Resolve the steps list from a workflow definition.

        Supports two layouts:
        - **Flat**: a top-level ``steps`` list.
        - **Tiered**: keyed under ``<type>_workflow.steps`` sections
          (e.g. ``full_workflow``, ``shortened_workflow``, etc.).

        When *workflow_type* is provided, the engine first consults the
        optional ``workflow_types`` mapping in the YAML to resolve the
        external label to a section key prefix, then looks for the
        corresponding ``<prefix>_workflow`` section.  Hyphens in the
        resolved prefix are normalised to underscores for key lookup.

        When empty, the flat ``steps`` list is used; if that is absent,
        the first available ``*_workflow`` section is returned as a
        fallback.

        Args:
            data: Parsed workflow YAML dict.
            workflow_type: Optional tier selector.

        Returns:
            The resolved list of step dicts (may be empty).
        """
        return resolve_workflow_steps_list(data, workflow_type)

    @staticmethod
    def from_yaml(
        yaml_path: str,
        workflow_id: str | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        metadata: dict[str, Any] | None = None,
        workflow_type: str = "",
    ) -> Workflow:
        """Load workflow from a YAML file and return a Workflow object.

        Args:
            yaml_path: Path to the workflow YAML file.
            workflow_id: Optional explicit workflow ID.
            name_override: Override the workflow name from the file.
            description_override: Override the workflow description.
            metadata: Extra metadata to attach.
            workflow_type: Tier selector (``"full"``, ``"shortened"``,
                ``"fast-track"``).  When empty, uses flat ``steps:`` or
                falls back to the first available tier.
        """
        with open(yaml_path, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        if isinstance(data, dict):
            data["__yaml_path"] = yaml_path
        return WorkflowDefinition.from_dict(
            data,
            workflow_id=workflow_id,
            name_override=name_override,
            description_override=description_override,
            metadata=metadata,
            workflow_type=workflow_type,
        )

    @staticmethod
    def from_dict(
        data: dict[str, Any],
        workflow_id: str | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        metadata: dict[str, Any] | None = None,
        workflow_type: str = "",
    ) -> Workflow:
        """Load workflow from a dict and return a Workflow object.

        Args:
            data: Parsed workflow dict.
            workflow_id: Optional explicit workflow ID.
            name_override: Override the workflow name.
            description_override: Override the description.
            metadata: Extra metadata to attach.
            workflow_type: Tier selector (see ``_resolve_steps``).
        """
        if not isinstance(data, dict):
            raise ValueError("Workflow definition must be a dict")

        metadata_block = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
        name = (
            name_override
            or metadata_block.get("name")
            or "Unnamed Workflow"
        )
        description = (
            description_override
            or data.get("description")
            or metadata_block.get("description")
            or ""
        )
        version = data.get("version") or metadata_block.get("version") or "1.0"

        resolved_id = (
            workflow_id
            or metadata_block.get("id")
            or WorkflowDefinition._slugify(name)
        )
        if not resolved_id:
            raise ValueError("Workflow ID could not be resolved")

        steps_data = WorkflowDefinition._resolve_steps(data, workflow_type)
        if not isinstance(steps_data, list) or not steps_data:
            raise ValueError("Workflow definition must include a non-empty steps list")

        require_human_merge_approval = parse_require_human_merge_approval(data)
        require_approval_for = parse_require_approval_for(data)
        steps = build_workflow_steps(
            data=data,
            steps_data=steps_data,
            slugify=WorkflowDefinition._slugify,
        )

        workflow_metadata = {"definition": data}
        if metadata:
            workflow_metadata.update(metadata)

        workflow = Workflow(
            id=resolved_id,
            name=name,
            version=version,
            description=description,
            steps=steps,
            metadata=workflow_metadata,
            require_human_merge_approval=require_human_merge_approval,
            require_approval_for=require_approval_for,
        )

        # Apply workflow-level approval gates to all steps
        workflow.apply_approval_gates()

        return workflow

    @staticmethod
    def resolve_next_agents(
        yaml_path: str,
        current_agent_type: str,
        workflow_type: str = "",
    ) -> list[str]:
        """Resolve valid next agent_types for a given agent_type from the workflow.

        Follows ``on_success`` links and router ``routes`` to determine which
        agent_types can legitimately follow *current_agent_type*.

        Args:
            yaml_path: Path to workflow YAML file.
            current_agent_type: The agent_type whose successors we want.
            workflow_type: Tier selector (see ``_resolve_steps``).

        Returns:
            List of valid next agent_type strings (may include ``"none"`` for
            terminal steps).  Empty list if the workflow can't be parsed or
            the agent_type is not found.
        """
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
        except Exception:
            return []
        if isinstance(data, dict):
            data["__yaml_path"] = yaml_path

        steps = WorkflowDefinition._resolve_steps(data, workflow_type)
        return resolve_next_agent_types_from_steps(
            steps=steps,
            current_agent_type=current_agent_type,
        )

    @staticmethod
    def canonicalize_next_agent(
        yaml_path: str,
        current_agent_type: str,
        proposed_next_agent: str,
        workflow_type: str = "",
    ) -> str:
        """Canonicalize a proposed next_agent into a valid successor agent_type.

        Handles common model output drift:
        - ``@agent`` mention formatting
        - step IDs / names instead of agent_type
        - invalid value when there is only one valid successor

        Returns empty string when ambiguous or invalid.
        """

        def _normalize(value: str) -> str:
            text = (value or "").strip()
            text = text.lstrip("@").strip()
            return text.strip("`").strip()

        candidate = _normalize(proposed_next_agent)
        if candidate.lower() in WorkflowDefinition._TERMINAL_NEXT_AGENT_VALUES:
            return "none"

        valid_next = WorkflowDefinition.resolve_next_agents(
            yaml_path,
            current_agent_type,
            workflow_type=workflow_type,
        )
        if not valid_next:
            return ""

        if candidate in valid_next:
            return candidate

        try:
            with open(yaml_path) as handle:
                data = yaml.safe_load(handle)
        except Exception:
            return valid_next[0] if len(valid_next) == 1 else ""

        steps = WorkflowDefinition._resolve_steps(data, workflow_type)
        return canonicalize_next_agent_from_steps(
            steps=steps,
            candidate=candidate,
            valid_next_agents=valid_next,
        )

    @staticmethod
    def to_prompt_context(
        yaml_path: str,
        current_agent_type: str = "",
        workflow_type: str = "",
    ) -> str:
        """Render workflow steps as a prompt-friendly checklist.

        Reads the YAML file and returns a Markdown formatted list of steps
        with their ``agent_type`` names, suitable for embedding in agent
        prompts so agents know the full workflow and use correct step names.

        When *current_agent_type* is provided, the returned text includes an
        explicit directive stating which agent_type(s) are valid for the
        ``next_agent`` field in the completion summary.

        Args:
            yaml_path: Path to the workflow YAML file.
            current_agent_type: If set, resolves and embeds valid next agents.
            workflow_type: Tier selector (see ``_resolve_steps``).

        Returns:
            Formatted Markdown text, or empty string on error.
        """
        try:
            with open(yaml_path) as f:
                data = yaml.safe_load(f)
            if isinstance(data, dict):
                data["__yaml_path"] = yaml_path

            steps = WorkflowDefinition._resolve_steps(data, workflow_type)
            if not steps:
                return ""

            valid_next = (
                WorkflowDefinition.resolve_next_agents(
                    yaml_path, current_agent_type, workflow_type=workflow_type
                )
                if current_agent_type
                else []
            )
            return build_prompt_context_text(
                steps=steps,
                yaml_basename=os.path.basename(yaml_path),
                workflow_type=workflow_type,
                current_agent_type=current_agent_type,
                valid_next_agents=valid_next,
            )
        except Exception as exc:
            logger.warning(f"Could not render workflow prompt context from {yaml_path}: {exc}")
            return ""

    @staticmethod
    def dry_run(
        data: dict[str, Any],
        workflow_type: str = "",
    ) -> "DryRunReport":
        """Validate a workflow definition dict and simulate step execution.

        No agents or tools are invoked.  Returns a :class:`DryRunReport`
        containing detected configuration errors and the predicted step
        execution order.

        Validation checks:
        - Top-level ``name`` or ``id`` field is present.
        - The resolved steps list is non-empty.
        - Each step has a non-empty ``agent_type``.
        - ``on_success`` references point to steps that exist in the definition.
        - ``condition`` expressions are syntactically valid Python.

        Simulation:
        - Walks the steps in declaration order (skipping ``router`` steps).
        - Evaluates each condition with an empty context; a condition that
          raises a :class:`NameError` (because referenced outputs don't exist
          yet) is treated as **RUN** (unknown → conservative).
        - Any other evaluation error marks the step as SKIP.

        Args:
            data: Parsed workflow definition dict.
            workflow_type: Tier selector (see ``_resolve_steps``).

        Returns:
            A :class:`DryRunReport` with ``errors`` and ``predicted_flow``.
        """
        errors, predicted_flow = build_dry_run_report_fields(
            data=data,
            workflow_type=workflow_type,
            resolve_steps=WorkflowDefinition._resolve_steps,
        )
        return DryRunReport(errors=errors, predicted_flow=predicted_flow)

    @staticmethod
    def _slugify(text: str) -> str:
        """Convert text into a safe workflow ID."""
        value = re.sub(r"[^a-zA-Z0-9_-]+", "-", text.strip().lower())
        return value.strip("-")
