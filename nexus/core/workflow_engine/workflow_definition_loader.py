from typing import Any, Callable

from nexus.core.models import Agent, WorkflowStep


def parse_require_human_merge_approval(data: dict[str, Any]) -> bool:
    """Parse workflow-level approval setting from workflow definition."""
    monitoring = data.get("monitoring", {})
    require_human_merge_approval = True
    if isinstance(monitoring, dict):
        require_human_merge_approval = monitoring.get("require_human_merge_approval", True)

    # Retained because workflow YAML may still define this top-level key.
    if "require_human_merge_approval" in data:
        require_human_merge_approval = data.get("require_human_merge_approval", True)
    return bool(require_human_merge_approval)


def resolve_workflow_steps_list(
    data: dict[str, Any], workflow_type: str = ""
) -> list[dict[str, Any]]:
    """Resolve workflow steps from flat or tiered workflow definition layouts."""
    if workflow_type:
        workflow_types_mapping = data.get("workflow_types", {})
        mapped_type = workflow_types_mapping.get(workflow_type, workflow_type)

        key_prefix = mapped_type.replace("-", "_")
        keys_to_try = [
            f"{key_prefix}_workflow",
            key_prefix,
            f"{mapped_type}_workflow",
            mapped_type,
        ]
        seen: set[str] = set()
        for key in keys_to_try:
            if key in seen:
                continue
            seen.add(key)
            tier = data.get(key, {})
            if isinstance(tier, dict) and tier.get("steps"):
                steps = tier["steps"]
                return steps if isinstance(steps, list) else []
        return []

    flat = data.get("steps", [])
    if isinstance(flat, list) and flat:
        return flat

    for key, value in data.items():
        if key.endswith("_workflow") and isinstance(value, dict) and value.get("steps"):
            steps = value["steps"]
            return steps if isinstance(steps, list) else []

    return []


def build_workflow_steps(
    *,
    data: dict[str, Any],
    steps_data: list[dict[str, Any]],
    slugify: Callable[[str], str],
) -> list[WorkflowStep]:
    """Build WorkflowStep models from parsed workflow step dictionaries."""
    steps: list[WorkflowStep] = []
    for idx, step_data in enumerate(steps_data, start=1):
        if not isinstance(step_data, dict):
            raise ValueError(f"Step {idx} must be a dict")

        agent_type = step_data.get("agent_type", "agent")
        step_name = step_data.get("id") or step_data.get("name") or f"step_{idx}"
        step_desc = step_data.get("description", "")
        prompt_template = step_data.get("prompt_template") or step_desc or "Execute step"

        step_retry: int | None = step_data.get("retry")
        retry_policy = step_data.get("retry_policy")
        step_backoff_strategy: str | None = None
        step_initial_delay: float = 0.0
        if isinstance(retry_policy, dict):
            if step_retry is None:
                step_retry = retry_policy.get("max_retries")
            step_backoff_strategy = retry_policy.get("backoff")
            raw_delay = retry_policy.get("initial_delay", 0.0)
            try:
                step_initial_delay = float(raw_delay) if raw_delay else 0.0
            except (TypeError, ValueError):
                step_initial_delay = 0.0

        agent = Agent(
            name=agent_type,
            display_name=step_data.get("name", agent_type),
            description=step_desc or f"Step {idx}",
            timeout=data.get("timeout_seconds", 600),
            max_retries=2,
        )

        inputs_data = step_data.get("inputs", {})
        if isinstance(inputs_data, list):
            normalized_inputs = {}
            for entry in inputs_data:
                if isinstance(entry, dict):
                    normalized_inputs.update(entry)
            inputs_data = normalized_inputs

        parallel_raw = step_data.get("parallel", [])
        if isinstance(parallel_raw, list):
            parallel_with: list[str] = [slugify(step_id) or step_id for step_id in parallel_raw]
        else:
            parallel_with = []

        step_routes = step_data.get("routes", [])
        steps.append(
            WorkflowStep(
                step_num=idx,
                name=slugify(step_name) or step_name,
                agent=agent,
                prompt_template=prompt_template,
                condition=step_data.get("condition"),
                retry=step_retry,
                backoff_strategy=step_backoff_strategy,
                initial_delay=step_initial_delay,
                inputs=inputs_data,
                routes=step_routes,
                on_success=step_data.get("on_success"),
                final_step=bool(step_data.get("final_step", False)),
                parallel_with=parallel_with,
            )
        )

    return steps


def build_dry_run_report_fields(
    *,
    data: dict[str, Any],
    workflow_type: str,
    resolve_steps: Callable[[dict[str, Any], str], list[dict[str, Any]]],
) -> tuple[list[str], list[str]]:
    """Validate a workflow definition dict and simulate the predicted step flow."""
    errors: list[str] = []
    predicted_flow: list[str] = []

    if not isinstance(data, dict):
        return ["Workflow definition must be a dict"], []

    metadata_block = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}

    if (
        not metadata_block.get("name")
        and not metadata_block.get("id")
    ):
        errors.append("Missing required top-level field: 'name' or 'id'")

    steps = resolve_steps(data, workflow_type)
    if not steps:
        errors.append(
            f"No steps found for workflow_type={workflow_type!r}. "
            "Check that the workflow definition contains a non-empty steps list."
        )
    else:
        step_ids = {s["id"] for s in steps if isinstance(s, dict) and "id" in s}

        for idx, step in enumerate(steps, start=1):
            if not isinstance(step, dict):
                errors.append(f"Step {idx}: must be a dict, got {type(step).__name__}")
                continue

            step_label = step.get("id") or step.get("name") or f"step_{idx}"
            agent_type = step.get("agent_type", "")
            if not agent_type:
                errors.append(f"Step '{step_label}': missing 'agent_type'")

            on_success = step.get("on_success")
            if on_success and step_ids and on_success not in step_ids:
                errors.append(
                    f"Step '{step_label}': 'on_success' references unknown step id '{on_success}'"
                )

            condition = step.get("condition")
            if condition:
                try:
                    compile(condition, "<condition>", "eval")
                except SyntaxError as exc:
                    errors.append(
                        f"Step '{step_label}': malformed condition expression "
                        f"'{condition}' — {exc}"
                    )

    for idx, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            continue
        agent_type = step.get("agent_type", "")
        if agent_type == "router":
            continue

        step_label = step.get("name") or step.get("id") or f"step_{idx}"
        condition = step.get("condition")
        if not condition:
            predicted_flow.append(f"RUN  {step_label} ({agent_type})")
            continue

        try:
            result = eval(condition, {"__builtins__": {}}, {})  # noqa: S307
            status = "RUN " if result else "SKIP"
        except NameError:
            status = "RUN "
        except Exception:
            status = "SKIP"

        predicted_flow.append(f"{status} {step_label} ({agent_type}) [condition: {condition}]")

    return errors, predicted_flow


def build_prompt_context_text(
    *,
    steps: list[dict[str, Any]],
    yaml_basename: str,
    workflow_type: str,
    current_agent_type: str,
    valid_next_agents: list[str],
) -> str:
    """Render workflow steps and next-agent constraints as prompt context text."""
    if not steps:
        return ""
    tier_label = f" [{workflow_type}]" if workflow_type else ""
    lines: list[str] = [f"**Workflow Steps{tier_label} (from {yaml_basename}):**\n"]
    for idx, step_data in enumerate(steps, 1):
        agent_type = step_data.get("agent_type", "unknown")
        name = step_data.get("name", step_data.get("id", f"Step {idx}"))
        desc = step_data.get("description", "")
        if agent_type == "router":
            continue
        lines.append(f"- {idx}. **{name}** — `{agent_type}` : {desc}")

    lines.append(
        "\n**CRITICAL:** Use ONLY the agent_type names listed above. "
        "DO NOT use old agent names or reference other workflow YAML files."
    )

    seen: set[str] = set()
    display_pairs: list[str] = []
    for step_data in steps:
        agent_type = step_data.get("agent_type", "")
        if agent_type and agent_type != "router" and agent_type not in seen:
            seen.add(agent_type)
            display_pairs.append(f"`{agent_type}` → **{agent_type.title()}**")
    if display_pairs:
        lines.append(
            "\n**Display Names (for the 'Ready for @...' line in your comment):**\n"
            + ", ".join(display_pairs)
        )

    if current_agent_type and valid_next_agents:
        names = ", ".join(f"`{a}`" for a in valid_next_agents)
        if len(valid_next_agents) == 1:
            lines.append(
                f"\n**YOUR next_agent MUST be:** {names}\n"
                f"Do NOT skip ahead or pick a different agent."
            )
        else:
            lines.append(
                f"\n**YOUR next_agent MUST be one of:** {names}\n"
                f"Choose based on your classification. "
                f"Do NOT skip ahead or pick a different agent."
            )

    return "\n".join(lines)


def resolve_next_agent_types_from_steps(
    *,
    steps: list[dict[str, Any]],
    current_agent_type: str,
) -> list[str]:
    """Resolve valid next agent_type values from parsed workflow steps."""
    if not steps:
        return []

    by_id: dict[str, dict[str, Any]] = {
        s["id"]: s for s in steps if isinstance(s, dict) and "id" in s
    }
    current_steps = [
        s for s in steps if isinstance(s, dict) and s.get("agent_type") == current_agent_type
    ]
    if not current_steps:
        return []

    result: list[str] = []
    for step in current_steps:
        on_success = step.get("on_success")
        if step.get("final_step") or not on_success:
            result.append("none")
            continue

        target = by_id.get(on_success)
        if not target:
            continue

        if target.get("agent_type") == "router":
            for route in target.get("routes", []):
                if not isinstance(route, dict):
                    continue
                route_target_id = route.get("then") or route.get("default")
                if route_target_id and route_target_id in by_id:
                    result.append(by_id[route_target_id].get("agent_type", "unknown"))
                elif route_target_id:
                    result.append(str(route_target_id))
            default_route = target.get("default")
            if default_route and default_route in by_id:
                result.append(by_id[default_route].get("agent_type", "unknown"))
        else:
            result.append(target.get("agent_type", "unknown"))

    seen: set[str] = set()
    unique: list[str] = []
    for agent in result:
        if agent not in seen:
            seen.add(agent)
            unique.append(agent)
    return unique


def canonicalize_next_agent_from_steps(
    *,
    steps: list[dict[str, Any]],
    candidate: str,
    valid_next_agents: list[str],
) -> str:
    """Map a normalized candidate (step id/name) to a valid next agent_type."""
    candidate_lc = candidate.lower()
    for step in steps:
        if not isinstance(step, dict):
            continue
        step_id = str(step.get("id", "")).strip().lower()
        step_name = str(step.get("name", "")).strip().lower()
        if candidate_lc in (step_id, step_name):
            mapped = str(step.get("agent_type", "")).strip()
            if mapped in valid_next_agents:
                return mapped
    return valid_next_agents[0] if len(valid_next_agents) == 1 else ""
