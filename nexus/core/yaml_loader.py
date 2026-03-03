"""YAML-Based Workflow Loader with schema validation.

This module provides :class:`YamlWorkflowLoader`, a clean public API for
loading Nexus workflow definitions from YAML files or dicts.  It extends
:class:`~nexus.core.workflow.WorkflowDefinition` with:

- Explicit schema validation before instantiation.
- Support for ``retry_policy`` blocks per step.
- Support for ``parallel`` step groups.
- ``load()`` / ``load_from_dict()`` helpers that mirror the ``from_yaml`` /
  ``from_dict`` interface under a more discoverable name.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

from nexus.core.models import Workflow
from nexus.core.workflow import WorkflowDefinition

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

#: Valid backoff strategies accepted in a ``retry_policy`` block.
RETRY_BACKOFF_STRATEGIES = ("exponential", "linear", "constant")


# ---------------------------------------------------------------------------
# YamlWorkflowLoader
# ---------------------------------------------------------------------------


class YamlWorkflowLoader:
    """Load and validate Nexus workflow definitions from YAML.

    All public methods are *static* so the class can be used without
    instantiation, matching the :class:`~nexus.core.workflow.WorkflowDefinition`
    pattern.

    Example usage::

        workflow = YamlWorkflowLoader.load(
            "examples/workflows/enterprise_workflow.yaml",
            workflow_type="full",
        )

        # Validate without loading
        errors = YamlWorkflowLoader.validate("path/to/workflow.yaml")
        if errors:
            raise ValueError(errors)
    """

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @staticmethod
    def load(
        yaml_path: str,
        workflow_id: str | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        metadata: dict[str, Any] | None = None,
        workflow_type: str = "",
        strict: bool = False,
    ) -> Workflow:
        """Load a workflow from a YAML file.

        Validates the YAML structure before instantiation.  When *strict* is
        ``True``, any validation warning is promoted to an error and a
        :exc:`ValueError` is raised.

        Args:
            yaml_path: Path to the YAML workflow definition file.
            workflow_id: Optional explicit workflow ID override.
            name_override: Override the workflow name read from the file.
            description_override: Override the workflow description.
            metadata: Extra key-value pairs to attach to the workflow.
            workflow_type: Tier selector (``"full"``, ``"shortened"``,
                ``"fast-track"`` or any custom tier defined in the file).
            strict: Raise on schema warnings in addition to errors.

        Returns:
            A fully instantiated :class:`~nexus.core.models.Workflow`.

        Raises:
            FileNotFoundError: When *yaml_path* does not exist.
            ValueError: On YAML parse failure, schema errors, or (in strict
                mode) schema warnings.
        """
        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Workflow YAML not found: {yaml_path}")

        with path.open("r", encoding="utf-8") as fh:
            try:
                data = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                raise ValueError(f"Failed to parse YAML from {yaml_path}: {exc}") from exc

        return YamlWorkflowLoader.load_from_dict(
            data,
            workflow_id=workflow_id,
            name_override=name_override,
            description_override=description_override,
            metadata=metadata,
            workflow_type=workflow_type,
            strict=strict,
        )

    @staticmethod
    def load_from_dict(
        data: dict[str, Any],
        workflow_id: str | None = None,
        name_override: str | None = None,
        description_override: str | None = None,
        metadata: dict[str, Any] | None = None,
        workflow_type: str = "",
        strict: bool = False,
    ) -> Workflow:
        """Load a workflow from an already-parsed dict.

        Args:
            data: Parsed workflow definition dict.
            workflow_id: Optional explicit workflow ID.
            name_override: Override the workflow name.
            description_override: Override the description.
            metadata: Extra metadata to attach.
            workflow_type: Tier selector (see :meth:`load`).
            strict: Raise on schema warnings in addition to errors.

        Returns:
            A fully instantiated :class:`~nexus.core.models.Workflow`.

        Raises:
            ValueError: On schema errors (or warnings in strict mode).
        """
        errors, warnings = YamlWorkflowLoader._validate_dict(data, workflow_type)

        if errors:
            raise ValueError(
                "Workflow YAML schema validation failed:\n" + "\n".join(f"  - {e}" for e in errors)
            )

        if strict and warnings:
            raise ValueError(
                "Workflow YAML schema warnings (strict mode):\n"
                + "\n".join(f"  - {w}" for w in warnings)
            )

        if warnings:
            for w in warnings:
                logger.warning("YamlWorkflowLoader: %s", w)

        return WorkflowDefinition.from_dict(
            data,
            workflow_id=workflow_id,
            name_override=name_override,
            description_override=description_override,
            metadata=metadata,
            workflow_type=workflow_type,
        )

    @staticmethod
    def validate(
        yaml_path: str,
        workflow_type: str = "",
    ) -> list[str]:
        """Validate a YAML file and return a list of error strings.

        Returns an empty list when the file is valid.

        Args:
            yaml_path: Path to the YAML workflow definition file.
            workflow_type: Tier selector.

        Returns:
            List of error strings (empty means valid).
        """
        path = Path(yaml_path)
        if not path.exists():
            return [f"File not found: {yaml_path}"]

        with path.open("r", encoding="utf-8") as fh:
            try:
                data = yaml.safe_load(fh)
            except yaml.YAMLError as exc:
                return [f"YAML parse error: {exc}"]

        errors, _ = YamlWorkflowLoader._validate_dict(data, workflow_type)
        return errors

    @staticmethod
    def validate_dict(
        data: dict[str, Any],
        workflow_type: str = "",
    ) -> list[str]:
        """Validate a workflow definition dict and return a list of error strings.

        Args:
            data: Parsed workflow definition dict.
            workflow_type: Tier selector.

        Returns:
            List of error strings (empty means valid).
        """
        errors, _ = YamlWorkflowLoader._validate_dict(data, workflow_type)
        return errors

    # ------------------------------------------------------------------
    # Internal validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_dict(
        data: Any,
        workflow_type: str = "",
    ) -> tuple[list[str], list[str]]:
        """Validate a workflow definition dict.

        Returns:
            ``(errors, warnings)`` — lists of strings.  Errors are fatal;
            warnings are informational only.
        """
        errors: list[str] = []
        warnings: list[str] = []

        if not isinstance(data, dict):
            errors.append(f"Workflow definition must be a mapping, got {type(data).__name__}")
            return errors, warnings

        metadata_block = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

        # Must have at least a name or id
        if (
            not metadata_block.get("name")
            and not metadata_block.get("id")
        ):
            errors.append("Missing required field: 'name' or 'id'")

        # Resolve steps for the selected tier
        steps = WorkflowDefinition._resolve_steps(data, workflow_type)
        if not steps:
            errors.append(
                f"No steps found for workflow_type={workflow_type!r}. "
                "Ensure the definition has a non-empty 'steps' list or matching tier section."
            )
            return errors, warnings

        if not isinstance(steps, list):
            errors.append("'steps' must be a list")
            return errors, warnings

        # Build step id set for reference validation
        step_ids = {s["id"] for s in steps if isinstance(s, dict) and "id" in s}

        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                errors.append(f"Step {idx}: must be a mapping, got {type(step).__name__}")
                continue

            label = step.get("id") or step.get("name") or f"step_{idx}"

            # agent_type is required
            agent_type = step.get("agent_type", "")
            if not agent_type:
                errors.append(f"Step '{label}': missing required field 'agent_type'")

            # on_success must reference a known step id (when step ids are used)
            on_success = step.get("on_success")
            if on_success and step_ids and on_success not in step_ids:
                errors.append(
                    f"Step '{label}': 'on_success' references unknown step id '{on_success}'"
                )

            # condition must be syntactically valid Python
            condition = step.get("condition")
            if condition:
                try:
                    compile(str(condition), "<condition>", "eval")
                except SyntaxError as exc:
                    errors.append(
                        f"Step '{label}': malformed 'condition' expression "
                        f"'{condition}' — {exc}"
                    )

            # retry_policy validation
            retry_policy = step.get("retry_policy")
            if retry_policy is not None:
                if not isinstance(retry_policy, dict):
                    errors.append(
                        f"Step '{label}': 'retry_policy' must be a mapping, "
                        f"got {type(retry_policy).__name__}"
                    )
                else:
                    max_retries = retry_policy.get("max_retries")
                    if max_retries is not None and (
                        not isinstance(max_retries, int) or max_retries < 0
                    ):
                        errors.append(
                            f"Step '{label}': 'retry_policy.max_retries' must be a "
                            f"non-negative integer, got {max_retries!r}"
                        )
                    backoff = retry_policy.get("backoff")
                    if backoff is not None and backoff not in RETRY_BACKOFF_STRATEGIES:
                        errors.append(
                            f"Step '{label}': 'retry_policy.backoff' must be one of "
                            f"{RETRY_BACKOFF_STRATEGIES}, got {backoff!r}"
                        )
                    initial_delay = retry_policy.get("initial_delay")
                    if initial_delay is not None and (
                        not isinstance(initial_delay, (int, float)) or initial_delay < 0
                    ):
                        errors.append(
                            f"Step '{label}': 'retry_policy.initial_delay' must be "
                            f"a non-negative number, got {initial_delay!r}"
                        )

            # parallel field must be a list of strings
            parallel = step.get("parallel")
            if parallel is not None:
                if not isinstance(parallel, list):
                    errors.append(
                        f"Step '{label}': 'parallel' must be a list of step ids, "
                        f"got {type(parallel).__name__}"
                    )
                else:
                    for entry in parallel:
                        if not isinstance(entry, str):
                            errors.append(
                                f"Step '{label}': 'parallel' entries must be strings, "
                                f"got {type(entry).__name__}"
                            )
                    # Warn if parallel references an unknown step id
                    if step_ids:
                        unknown = [p for p in parallel if isinstance(p, str) and p not in step_ids]
                        for u in unknown:
                            warnings.append(
                                f"Step '{label}': 'parallel' references unknown step id '{u}'"
                            )

        return errors, warnings
