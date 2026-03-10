"""Built-in plugin: workflow state engine adapter for issue-centric operations."""

import ast
import logging
import os
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from nexus.adapters.registry import AdapterRegistry
from nexus.adapters.storage.base import StorageBackend
from nexus.core.idempotency import IdempotencyKey, IdempotencyLedger
from nexus.core.models import StepStatus, WorkflowState
from nexus.core.workflow import WorkflowDefinition, WorkflowEngine
from nexus.core.workflow_runtime.completion_errors import (
    CompletionSchemaError,
    CompletionStaleError,
)

logger = logging.getLogger(__name__)

_STORAGE_TYPE_ENV = "NEXUS_STORAGE_BACKEND"
_STORAGE_DSN_ENV = "NEXUS_STORAGE_DSN"
_STORAGE_DIR_ENV = "NEXUS_STORAGE_DIR"


class WorkflowStateEnginePlugin:
    """Adapter around ``WorkflowEngine`` with issue-number centric operations.

    Storage backend selection
    -------------------------
    The plugin resolves the storage backend in the following priority order:

    1. ``engine_factory`` config key — callable that returns a
       :class:`~nexus.core.workflow.WorkflowEngine` directly (highest priority).
    2. ``storage`` config key — a pre-built :class:`~nexus.adapters.storage.base.StorageBackend`
       instance.
    3. ``storage_type`` config key (or ``NEXUS_STORAGE_BACKEND`` env var):

       * ``"file"`` (default) — JSON file storage.  Requires either
         ``storage_dir`` config key or ``NEXUS_STORAGE_DIR`` env var.
       * ``"postgres"`` / ``"postgresql"`` — PostgreSQL via SQLAlchemy.
         Requires either a ``storage_config`` dict with
         ``connection_string`` or the ``NEXUS_STORAGE_DSN`` env var.

    Example — file storage (default)::

        plugin = WorkflowStateEnginePlugin({"storage_dir": "./data"})

    Example — Postgres via config::

        plugin = WorkflowStateEnginePlugin({
            "storage_type": "postgres",
            "storage_config": {"connection_string": "postgresql+psycopg2://user:pass@host/db"},
        })

    Example — Postgres via environment variables::

        NEXUS_STORAGE_BACKEND=postgres
        NEXUS_STORAGE_DSN=postgresql+psycopg2://user:pass@host/db
    """

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}
        self.storage_dir: str = self.config.get("storage_dir", os.environ.get(_STORAGE_DIR_ENV, ""))
        self.engine_factory: Callable[[], WorkflowEngine] | None = self.config.get("engine_factory")
        self.issue_to_workflow_id = self.config.get("issue_to_workflow_id")
        self._completion_locks: dict[str, threading.RLock] = {}
        self._completion_locks_guard = threading.Lock()

    def _get_completion_lock(self, workflow_id: str) -> threading.RLock:
        with self._completion_locks_guard:
            lock = self._completion_locks.get(str(workflow_id))
            if lock is None:
                lock = threading.RLock()
                self._completion_locks[str(workflow_id)] = lock
            return lock

    @staticmethod
    def _mark_completion_outcome(workflow: Any, *, applied: bool, reason: str = "") -> None:
        metadata = dict(getattr(workflow, "metadata", {}) or {})
        metadata["_nexus_completion_applied"] = bool(applied)
        if reason:
            metadata["_nexus_completion_reason"] = str(reason)
        else:
            metadata.pop("_nexus_completion_reason", None)
        workflow.metadata = metadata

    def _build_storage(self) -> StorageBackend:
        """Construct the appropriate StorageBackend from config / env vars."""
        # A pre-built backend takes priority over type-based construction.
        prebuilt = self.config.get("storage")
        if isinstance(prebuilt, StorageBackend):
            return prebuilt

        storage_type = (
            self.config.get("storage_type") or os.environ.get(_STORAGE_TYPE_ENV, "file")
        ).lower()

        registry = AdapterRegistry()
        storage_cfg: dict[str, Any] = dict(self.config.get("storage_config") or {})

        if storage_type == "file":
            if not self.storage_dir:
                raise ValueError(
                    "storage_dir config key (or NEXUS_STORAGE_DIR env var) is required "
                    "when storage_type is 'file'"
                )
            storage_cfg.setdefault("base_path", self.storage_dir)
            return registry.create_storage("file", **storage_cfg)

        if storage_type in ("postgres", "postgresql"):
            if "connection_string" not in storage_cfg:
                dsn = os.environ.get(_STORAGE_DSN_ENV, "")
                if not dsn:
                    raise ValueError(
                        "A connection_string must be provided in storage_config or via "
                        f"the {_STORAGE_DSN_ENV} environment variable when "
                        f"storage_type is '{storage_type}'"
                    )
                storage_cfg["connection_string"] = dsn
            return registry.create_storage("postgres", **storage_cfg)

        # Unknown types — delegate to the registry (supports custom registrations).
        return registry.create_storage(storage_type, **storage_cfg)

    def _get_engine(self) -> WorkflowEngine:
        if callable(self.engine_factory):
            return self.engine_factory()
        event_bus = self.config.get("event_bus")
        if callable(event_bus):
            event_bus = event_bus()
        return WorkflowEngine(storage=self._build_storage(), event_bus=event_bus)

    def _resolve_workflow_id(self, issue_number: str) -> str | None:
        if callable(self.issue_to_workflow_id):
            return self.issue_to_workflow_id(str(issue_number))
        mapping = self.config.get("issue_workflow_map", {})
        if isinstance(mapping, dict):
            return mapping.get(str(issue_number))
        return None

    def _run_callback(self, callback_name: str, *args, **kwargs) -> None:
        callback = self.config.get(callback_name)
        if not callable(callback):
            return
        try:
            callback(*args, **kwargs)
        except Exception as exc:
            logger.warning("Callback %s failed: %s", callback_name, exc)

    @staticmethod
    def _issue_number_as_int(issue_number: str) -> int | None:
        try:
            return int(str(issue_number))
        except Exception:
            return None

    @staticmethod
    def _normalize_ref(value: str) -> str:
        return str(value or "").strip().lstrip("@").lower()

    @staticmethod
    def _match_step_by_ref(workflow, step_ref: str):
        normalized_ref = WorkflowStateEnginePlugin._normalize_ref(step_ref)
        if not normalized_ref:
            return None
        slug_ref = WorkflowDefinition._slugify(normalized_ref)
        for step in workflow.steps:
            step_name = WorkflowStateEnginePlugin._normalize_ref(step.name)
            agent_name = WorkflowStateEnginePlugin._normalize_ref(step.agent.name)
            if normalized_ref in {step_name, agent_name}:
                return step
            if slug_ref and slug_ref in {step_name, agent_name}:
                return step
        return None

    @staticmethod
    def _route_target_ref(route: dict[str, Any]) -> str:
        target = route.get("goto") or route.get("then")
        if target:
            return str(target)
        default_val = route.get("default")
        return str(default_val) if isinstance(default_val, str) else ""

    @staticmethod
    def _refs_match(left: str, right: str) -> bool:
        left_norm = WorkflowStateEnginePlugin._normalize_ref(left)
        right_norm = WorkflowStateEnginePlugin._normalize_ref(right)
        if not left_norm or not right_norm:
            return False
        if left_norm == right_norm:
            return True
        return WorkflowDefinition._slugify(left_norm) == WorkflowDefinition._slugify(right_norm)

    @staticmethod
    def _infer_simple_condition_assignment(condition: str):
        """Infer variable assignment from simple conditions like `x == 'value'`."""
        try:
            node = ast.parse(condition, mode="eval").body
        except Exception:
            return None

        if not isinstance(node, ast.Compare):
            return None
        if len(node.ops) != 1 or len(node.comparators) != 1:
            return None
        if not isinstance(node.ops[0], ast.Eq):
            return None
        if not isinstance(node.left, ast.Name):
            return None
        comparator = node.comparators[0]
        if not isinstance(comparator, ast.Constant):
            return None

        return node.left.id, comparator.value

    @staticmethod
    def _normalize_completion_outputs(
        workflow,
        completed_step,
        outputs: dict[str, Any],
    ) -> dict[str, Any]:
        normalized = dict(outputs or {})
        next_agent = WorkflowStateEnginePlugin._normalize_ref(normalized.get("next_agent", ""))
        if not next_agent:
            return normalized

        router_step = workflow.get_step(completed_step.step_num + 1)
        if not router_step or not router_step.routes:
            return normalized

        for route in router_step.routes:
            target_ref = WorkflowStateEnginePlugin._route_target_ref(route)
            if not target_ref:
                continue
            target_step = WorkflowStateEnginePlugin._match_step_by_ref(workflow, target_ref)

            route_points_to_next = WorkflowStateEnginePlugin._refs_match(target_ref, next_agent)
            if not route_points_to_next and target_step:
                route_points_to_next = WorkflowStateEnginePlugin._refs_match(
                    target_step.name, next_agent
                )
                if not route_points_to_next:
                    route_points_to_next = WorkflowStateEnginePlugin._refs_match(
                        target_step.agent.name,
                        next_agent,
                    )

            if not route_points_to_next:
                continue

            when = route.get("when")
            if not isinstance(when, str) or not when.strip():
                return normalized
            inferred = WorkflowStateEnginePlugin._infer_simple_condition_assignment(when)
            if not inferred:
                return normalized
            key, value = inferred
            normalized.setdefault(key, value)
            return normalized

        return normalized

    def _resolve_workflow_definition_path(self, project_name: str) -> str | None:
        resolver = self.config.get("workflow_definition_path_resolver")
        if callable(resolver):
            return resolver(project_name)

        project_paths = self.config.get("project_workflow_paths", {})
        if isinstance(project_paths, dict) and project_name in project_paths:
            return project_paths[project_name]

        return self.config.get("workflow_definition_path")

    @staticmethod
    def _infer_project_and_tier_from_workflow_id(
        workflow_id: str, issue_number: str
    ) -> tuple[str | None, str | None]:
        """Infer ``(project_name, tier_name)`` from workflow id format ``project-<issue>-<tier>``."""
        issue_key = str(issue_number or "").strip()
        parts = [part for part in str(workflow_id or "").strip().split("-") if part]
        if not issue_key or len(parts) < 3:
            return None, None

        try:
            issue_idx = parts.index(issue_key)
        except ValueError:
            return None, None

        project_name = "-".join(parts[:issue_idx]).strip()
        tier_name = "-".join(parts[issue_idx + 1 :]).strip()
        if not project_name or not tier_name:
            return None, None
        return project_name, tier_name

    async def _recover_missing_workflow(
        self, issue_number: str, workflow_id: str, engine: WorkflowEngine
    ) -> Any | None:
        """Best-effort recovery when mapping exists but workflow row is missing."""
        project_name, tier_name = self._infer_project_and_tier_from_workflow_id(
            workflow_id, issue_number
        )
        if not project_name or not tier_name:
            logger.warning(
                "Could not infer project/tier from missing workflow id '%s' (issue #%s)",
                workflow_id,
                issue_number,
            )
            return None

        logger.warning(
            "Missing workflow row for issue #%s (%s). Attempting recovery from workflow definition.",
            issue_number,
            workflow_id,
        )

        created_id = await self.create_workflow_for_issue(
            issue_number=str(issue_number),
            issue_title=f"issue-{issue_number}",
            project_name=project_name,
            tier_name=tier_name,
            task_type=str(self.config.get("default_task_type", "feature")),
            description=f"Recovered workflow for issue #{issue_number}",
        )
        if not created_id:
            return None

        try:
            await engine.start_workflow(created_id)
        except Exception as exc:
            logger.debug(
                "Recovery start_workflow skipped for issue #%s (%s): %s",
                issue_number,
                created_id,
                exc,
            )

        return await engine.get_workflow(workflow_id)

    def _resolve_issue_url(self, issue_number: str) -> str | None:
        resolver = self.config.get("issue_url_resolver")
        if callable(resolver):
            return resolver(issue_number)

        github_repo = self.config.get("github_repo")
        if github_repo:
            return f"https://github.com/{github_repo}/issues/{issue_number}"
        return None

    async def create_workflow_for_issue(
        self,
        issue_number: str,
        issue_title: str,
        project_name: str,
        tier_name: str,
        task_type: str,
        description: str = "",
    ) -> str | None:
        """Create workflow from configured workflow definition path for an issue."""
        workflow_definition_path = self._resolve_workflow_definition_path(project_name)
        if not workflow_definition_path:
            logger.error("No workflow definition path for project '%s'", project_name)
            return None
        if not os.path.exists(workflow_definition_path):
            logger.error("Workflow definition not found at: %s", workflow_definition_path)
            return None

        workflow_type = WorkflowDefinition.normalize_workflow_type(tier_name)
        workflow_id = f"{project_name}-{issue_number}-{tier_name}"
        workflow_name = f"{project_name}/{issue_title}"
        workflow_description = description or f"Workflow for issue #{issue_number}"

        metadata = {
            "issue_number": issue_number,
            "project": project_name,
            "tier": tier_name,
            "task_type": task_type,
            "workflow_type": workflow_type,
            "workflow_definition_path": workflow_definition_path,
        }
        issue_url = self._resolve_issue_url(issue_number)
        if issue_url:
            metadata["github_issue_url"] = issue_url

        workflow = WorkflowDefinition.from_yaml(
            workflow_definition_path,
            workflow_id=workflow_id,
            name_override=workflow_name,
            description_override=workflow_description,
            metadata=metadata,
            workflow_type=workflow_type,
        )

        try:
            engine = self._get_engine()
            await engine.create_workflow(workflow)
            mapper = self.config.get("issue_to_workflow_map_setter")
            if callable(mapper):
                mapper(issue_number, workflow_id)
            logger.info("Created workflow %s for issue #%s", workflow_id, issue_number)
            return workflow_id
        except Exception as exc:
            logger.error("Failed to create workflow for issue #%s: %s", issue_number, exc)
            return None

    async def start_workflow(self, workflow_id: str) -> bool:
        """Start workflow by workflow id."""
        try:
            engine = self._get_engine()
            await engine.start_workflow(workflow_id)
            logger.info("Started workflow %s", workflow_id)
            return True
        except Exception as exc:
            logger.error("Failed to start workflow %s: %s", workflow_id, exc)
            return False

    async def pause_workflow(self, issue_number: str, reason: str = "User requested") -> bool:
        """Pause workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.pause_workflow(workflow_id)
            logger.info("Paused workflow %s for issue #%s: %s", workflow_id, issue_number, reason)
            return True
        except Exception as exc:
            logger.error("Failed to pause workflow for issue #%s: %s", issue_number, exc)
            return False

    async def resume_workflow(self, issue_number: str) -> bool:
        """Resume workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.resume_workflow(workflow_id)
            logger.info("Resumed workflow %s for issue #%s", workflow_id, issue_number)
            return True
        except Exception as exc:
            logger.error("Failed to resume workflow for issue #%s: %s", issue_number, exc)
            return False

    async def get_workflow_status(self, issue_number: str) -> dict[str, Any] | None:
        """Return status payload for workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            return None
        try:
            engine = self._get_engine()
            workflow = await engine.get_workflow(workflow_id)
            if not workflow:
                return None

            steps = list(workflow.steps or [])
            total_steps = len(steps)
            current_step_obj = None
            current_step_display = 0

            if total_steps > 0:
                raw_current = int(workflow.current_step or 0)

                # Prefer explicit step_num match (works regardless of index base).
                for idx, step in enumerate(steps):
                    if getattr(step, "step_num", None) == raw_current:
                        current_step_obj = step
                        current_step_display = idx + 1
                        break

                # Fallback to index interpretations.
                if current_step_obj is None and 0 <= raw_current < total_steps:
                    current_step_obj = steps[raw_current]
                    current_step_display = raw_current + 1
                elif current_step_obj is None and 1 <= raw_current <= total_steps:
                    idx = raw_current - 1
                    current_step_obj = steps[idx]
                    current_step_display = idx + 1

                # Final fallback: first RUNNING step, otherwise first step.
                if current_step_obj is None:
                    for idx, step in enumerate(steps):
                        if str(getattr(step, "status", "")).strip().upper() == "RUNNING":
                            current_step_obj = step
                            current_step_display = idx + 1
                            break
                if current_step_obj is None:
                    current_step_obj = steps[0]
                    current_step_display = 1

            return {
                "workflow_id": workflow.id,
                "name": workflow.name,
                "state": workflow.state.value,
                "current_step": current_step_display,
                "current_step_num": int(getattr(current_step_obj, "step_num", 0) or 0),
                "current_step_id": str(getattr(current_step_obj, "name", "") or ""),
                "total_steps": total_steps,
                "current_step_name": getattr(current_step_obj, "name", "unknown"),
                "current_agent_type": (
                    getattr(getattr(current_step_obj, "agent", None), "name", "")
                    or "unknown"
                ),
                "current_agent": (
                    getattr(getattr(current_step_obj, "agent", None), "display_name", "")
                    or getattr(getattr(current_step_obj, "agent", None), "name", "unknown")
                ),
                "created_at": workflow.created_at.isoformat() if workflow.created_at else None,
                "updated_at": workflow.updated_at.isoformat() if workflow.updated_at else None,
                "metadata": workflow.metadata,
                "steps": [
                    {
                        "name": str(getattr(step, "name", "") or ""),
                        "status": str(getattr(getattr(step, "status", None), "value", "") or ""),
                        "agent": {
                            "name": str(
                                getattr(getattr(step, "agent", None), "name", "") or ""
                            ),
                            "display_name": str(
                                getattr(getattr(step, "agent", None), "display_name", "") or ""
                            ),
                        },
                    }
                    for step in steps
                ],
            }
        except Exception as exc:
            logger.error("Failed to read workflow status for issue #%s: %s", issue_number, exc)
            return None

    async def approve_step(self, issue_number: str, approved_by: str) -> bool:
        """Approve pending step for workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.approve_step(workflow_id, approved_by=approved_by)
            self._run_callback("clear_pending_approval", issue_number)
            issue_num_int = self._issue_number_as_int(issue_number)
            if issue_num_int is not None:
                self._run_callback(
                    "audit_log",
                    issue_num_int,
                    "APPROVAL_GRANTED",
                    f"by {approved_by}",
                )
            logger.info("Approved workflow step for %s by %s", workflow_id, approved_by)
            return True
        except Exception as exc:
            logger.error("Failed to approve workflow for issue #%s: %s", issue_number, exc)
            return False

    async def deny_step(self, issue_number: str, denied_by: str, reason: str) -> bool:
        """Deny pending step for workflow mapped to issue number."""
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False
        try:
            engine = self._get_engine()
            await engine.deny_step(workflow_id, denied_by=denied_by, reason=reason)
            self._run_callback("clear_pending_approval", issue_number)
            issue_num_int = self._issue_number_as_int(issue_number)
            if issue_num_int is not None:
                self._run_callback(
                    "audit_log",
                    issue_num_int,
                    "APPROVAL_DENIED",
                    f"by {denied_by}",
                )
            logger.info("Denied workflow step for %s by %s", workflow_id, denied_by)
            return True
        except Exception as exc:
            logger.error("Failed to deny workflow for issue #%s: %s", issue_number, exc)
            return False

    def _get_ledger(self) -> IdempotencyLedger:
        """Return the shared IdempotencyLedger, lazily initialised."""
        if not hasattr(self, "_idempotency_ledger"):
            ledger_path = self.config.get(
                "idempotency_ledger_path",
                os.path.join(self.storage_dir or ".", ".nexus_idempotency_ledger.json"),
            )
            self._idempotency_ledger = IdempotencyLedger(ledger_path)
        return self._idempotency_ledger  # type: ignore[return-value]

    async def complete_step_for_issue(
        self,
        issue_number: str,
        completed_agent_type: str,
        outputs: dict[str, Any],
        event_id: str = "",
    ) -> Any | None:
        """Mark the current running step for *issue_number* as complete.

        Locates the RUNNING step, validates strict step identity
        (``step_id`` + ``step_num`` + ``completed_agent_type``), then calls
        ``WorkflowEngine.complete_step()`` to advance the workflow.
        Router steps are evaluated automatically, handling loops and
        conditional branches.

        An idempotency check is performed before advancing the state machine.
        If the composite key ``(issue_number, step_num, completed_agent_type,
        event_id)`` was already processed, the call is a no-op and the current
        workflow is returned unchanged.

        Args:
            issue_number: GitHub issue number (or any issue id).
            completed_agent_type: The ``agent_type`` that reports completion.
            outputs: Structured outputs from the completion summary to record
                against the step and expose to subsequent route conditions.
                Must include ``step_id`` and ``step_num`` matching the active
                RUNNING workflow step.
            event_id: Caller-supplied deduplication token — typically a GitHub
                comment ID or a hash of the completion file path/content.
                An empty string disables the ledger check.

        Returns:
            The updated :class:`~nexus.core.models.Workflow` (inspect
            ``.state`` and ``.active_agent_type`` for next steps), or
            ``None`` when no workflow is mapped to *issue_number* or the
            workflow cannot be loaded from storage.
        """
        from nexus.core.models import StepStatus  # local import to avoid circular

        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.debug("complete_step_for_issue: no workflow mapping for issue #%s", issue_number)
            return None
        lock = self._get_completion_lock(str(workflow_id))
        with lock:
            engine = self._get_engine()
            workflow = await engine.get_workflow(workflow_id)
            if not workflow:
                logger.debug(
                    "complete_step_for_issue: workflow %s not found (issue #%s)",
                    workflow_id,
                    issue_number,
                )
                return None

            if workflow.state == WorkflowState.PENDING:
                try:
                    await engine.start_workflow(workflow_id)
                    workflow = await engine.get_workflow(workflow_id)
                except Exception as exc:
                    logger.warning(
                        "complete_step_for_issue: failed to auto-start pending workflow %s "
                        "(issue #%s): %s",
                        workflow_id,
                        issue_number,
                        exc,
                    )
                    return workflow

            payload = dict(outputs or {})
            reported_step_id = self._normalize_ref(payload.get("step_id", ""))
            try:
                reported_step_num = int(payload.get("step_num", 0) or 0)
            except (TypeError, ValueError):
                reported_step_num = 0
            completed_agent_norm = self._normalize_ref(completed_agent_type)

            if not reported_step_id or reported_step_num <= 0:
                logger.warning(
                    "complete_step_for_issue: invalid completion schema for issue #%s "
                    "(completed_agent=%s, step_id=%s, step_num=%s, event_id=%s)",
                    issue_number,
                    completed_agent_type,
                    payload.get("step_id", ""),
                    payload.get("step_num", ""),
                    event_id or "<none>",
                )
                raise CompletionSchemaError(
                    f"Completion invalid schema for issue #{issue_number}: "
                    f"step_id={payload.get('step_id', '')!r}, step_num={payload.get('step_num', '')!r}"
                )

            # Strict step-key idempotency: if this exact step already completed, no-op success.
            reported_step = None
            for step in workflow.steps:
                step_id_norm = self._normalize_ref(getattr(step, "name", ""))
                step_num_val = int(getattr(step, "step_num", 0) or 0)
                if self._refs_match(reported_step_id, step_id_norm) and step_num_val == reported_step_num:
                    reported_step = step
                    break

            if reported_step is not None and reported_step.status == StepStatus.COMPLETED:
                reported_agent = self._normalize_ref(
                    getattr(getattr(reported_step, "agent", None), "name", "")
                )
                if reported_agent and self._refs_match(completed_agent_norm, reported_agent):
                    logger.info(
                        "complete_step_for_issue: duplicate step completion suppressed for issue #%s "
                        "(workflow=%s step_id=%s step_num=%s agent=%s event_id=%s)",
                        issue_number,
                        workflow_id,
                        getattr(reported_step, "name", ""),
                        getattr(reported_step, "step_num", 0),
                        completed_agent_type,
                        event_id or "<none>",
                    )
                    self._mark_completion_outcome(
                        workflow,
                        applied=False,
                        reason="duplicate-step-noop",
                    )
                    return workflow

            # Find the RUNNING step (single active step expected).
            running_step = None
            for step in workflow.steps:
                if step.status == StepStatus.RUNNING:
                    running_step = step
                    break

            if not running_step:
                active_agent = workflow.active_agent_type
                logger.warning(
                    "complete_step_for_issue: stale completion for issue #%s: "
                    "completed_agent=%s, active_agent=%s, event_id=%s",
                    issue_number,
                    completed_agent_type,
                    active_agent,
                    event_id or "<none>",
                )
                raise CompletionStaleError(
                    f"Completion stale/mismatched for issue #{issue_number}: "
                    f"completed_agent={completed_agent_type}, active_agent={active_agent}"
                )

            expected_step_id = self._normalize_ref(getattr(running_step, "name", ""))
            expected_step_num = int(getattr(running_step, "step_num", 0) or 0)
            expected_agent = self._normalize_ref(
                getattr(getattr(running_step, "agent", None), "name", "")
            )

            if not expected_step_id or expected_step_num <= 0:
                logger.warning(
                    "complete_step_for_issue: workflow step metadata invalid for issue #%s "
                    "(active_step_id=%s, active_step_num=%s, event_id=%s)",
                    issue_number,
                    expected_step_id,
                    expected_step_num,
                    event_id or "<none>",
                )
                raise CompletionSchemaError(
                    f"Completion invalid schema for issue #{issue_number}: "
                    f"workflow active step metadata invalid (step_id={expected_step_id!r}, "
                    f"step_num={expected_step_num!r})"
                )

            if not expected_agent:
                logger.warning(
                    "complete_step_for_issue: workflow step agent metadata invalid for issue #%s "
                    "(active_step_id=%s, active_step_num=%s, event_id=%s)",
                    issue_number,
                    expected_step_id,
                    expected_step_num,
                    event_id or "<none>",
                )
                raise CompletionSchemaError(
                    f"Completion invalid schema for issue #{issue_number}: "
                    "workflow active step agent is missing"
                )

            step_id_matches = self._refs_match(reported_step_id, expected_step_id)
            step_num_matches = reported_step_num == expected_step_num
            agent_matches = self._refs_match(completed_agent_norm, expected_agent)
            if not (step_id_matches and step_num_matches and agent_matches):
                logger.warning(
                    "complete_step_for_issue: stale completion for issue #%s: "
                    "completed_agent=%s, active_agent=%s, reported_step_id=%s, reported_step_num=%s, "
                    "active_step_id=%s, active_step_num=%s, event_id=%s",
                    issue_number,
                    completed_agent_type,
                    workflow.active_agent_type,
                    payload.get("step_id", ""),
                    payload.get("step_num", ""),
                    running_step.name,
                    running_step.step_num,
                    event_id or "<none>",
                )
                raise CompletionStaleError(
                    f"Completion stale/mismatched for issue #{issue_number}: "
                    f"completed_agent={completed_agent_type}, active_agent={workflow.active_agent_type}, "
                    f"reported_step=({payload.get('step_id', '')},{payload.get('step_num', '')}), "
                    f"active_step=({running_step.name},{running_step.step_num})"
                )

            # Event-level idempotency guard — reject duplicate step-completion signals.
            ledger = self._get_ledger()
            if event_id:
                idem_key = IdempotencyKey(
                    issue_id=str(issue_number),
                    step_num=running_step.step_num,
                    agent_type=expected_agent or completed_agent_type,
                    event_id=event_id,
                )
                if ledger.is_duplicate(idem_key):
                    logger.info(
                        "complete_step_for_issue: duplicate event suppressed for issue #%s "
                        "(step=%s, agent=%s, event_id=%s)",
                        issue_number,
                        running_step.step_num,
                        expected_agent or completed_agent_type,
                        event_id,
                    )
                    self._mark_completion_outcome(
                        workflow,
                        applied=False,
                        reason="duplicate-event-noop",
                    )
                    return workflow

            normalized_outputs = self._normalize_completion_outputs(workflow, running_step, outputs)
            normalized_outputs["workflow_id"] = str(workflow_id)
            normalized_outputs["step_id"] = running_step.name
            normalized_outputs["step_num"] = running_step.step_num

            result = await engine.complete_step(
                workflow_id=workflow_id,
                step_num=running_step.step_num,
                outputs=normalized_outputs,
            )

            if event_id:
                ledger.record(idem_key)

            if result is not None:
                self._mark_completion_outcome(result, applied=True)
            return result

    async def reset_to_agent_for_issue(self, issue_number: str, agent_ref: str) -> bool:
        """Reset workflow to a specific step/agent and mark it RUNNING.

        This is used by manual recovery flows (e.g. /continue with forced agent)
        to rewind an already-completed or drifted workflow to a known step.
        """
        workflow_id = self._resolve_workflow_id(issue_number)
        if not workflow_id:
            logger.warning("No workflow mapping found for issue #%s", issue_number)
            return False

        engine = self._get_engine()
        workflow = await engine.get_workflow(workflow_id)
        if not workflow:
            workflow = await self._recover_missing_workflow(issue_number, workflow_id, engine)
            if not workflow:
                logger.warning(
                    "reset_to_agent_for_issue: workflow %s not found (issue #%s)",
                    workflow_id,
                    issue_number,
                )
                return False

        target_step = self._match_step_by_ref(workflow, agent_ref)
        if not target_step:
            logger.warning(
                "reset_to_agent_for_issue: could not resolve target '%s' for issue #%s",
                agent_ref,
                issue_number,
            )
            return False

        now = datetime.now(UTC)
        for step in workflow.steps:
            if step.step_num < target_step.step_num:
                step.status = StepStatus.COMPLETED
                if step.completed_at is None:
                    step.completed_at = now
            elif step.step_num == target_step.step_num:
                step.status = StepStatus.RUNNING
                step.started_at = now
                step.completed_at = None
                step.error = None
                # Manual rewind starts a fresh execution branch from this point.
                step.iteration = 0
            else:
                step.status = StepStatus.PENDING
                step.started_at = None
                step.completed_at = None
                step.error = None
                step.outputs = {}
                step.retry_count = 0
                # Clear loop-guard counters so future router gotos can re-activate.
                step.iteration = 0

        workflow.state = WorkflowState.RUNNING
        workflow.current_step = target_step.step_num
        workflow.completed_at = None
        workflow.updated_at = now
        await engine.storage.save_workflow(workflow)

        logger.info(
            "reset_to_agent_for_issue: rewound issue #%s workflow %s to step %s (%s)",
            issue_number,
            workflow_id,
            target_step.step_num,
            target_step.agent.name,
        )
        return True

    async def request_approval_gate(
        self,
        workflow_id: str,
        issue_number: str,
        step_num: int,
        step_name: str,
        agent_name: str,
        approvers,
        approval_timeout: int,
        project: str = "nexus",
    ) -> bool:
        """Persist and notify about a pending approval gate."""
        self._run_callback(
            "set_pending_approval",
            issue_num=issue_number,
            step_num=step_num,
            step_name=step_name,
            approvers=approvers,
            approval_timeout=approval_timeout,
        )

        issue_num_int = self._issue_number_as_int(issue_number)
        if issue_num_int is not None:
            self._run_callback(
                "audit_log",
                issue_num_int,
                "APPROVAL_REQUESTED",
                f"step {step_num} ({step_name}), approvers={approvers}",
            )

        self._run_callback(
            "notify_approval_required",
            issue_number=issue_number,
            step_num=step_num,
            step_name=step_name,
            agent=agent_name,
            approvers=approvers,
            project=project,
        )

        logger.info(
            "Approval gate requested for workflow %s issue #%s step %s (%s)",
            workflow_id,
            issue_number,
            step_num,
            step_name,
        )
        return True


def register_plugins(registry) -> None:
    """Register built-in workflow state engine plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="workflow-state-engine",
        version="0.1.0",
        factory=lambda config: WorkflowStateEnginePlugin(config),
        description="Workflow state adapter for issue-based pause/resume/status/approval operations",
    )
