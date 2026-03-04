from __future__ import annotations

import os
import re
from typing import Any, Callable

import yaml


def render_checklist_from_workflow(
    *,
    project_name: str,
    tier_name: str,
    get_workflow_definition_path: Callable[[str], str | None],
) -> str:
    """Render checklist directly from workflow YAML step definitions."""
    from nexus.core.workflow import WorkflowDefinition

    workflow_path = get_workflow_definition_path(project_name)
    if not workflow_path or not os.path.exists(workflow_path):
        return ""

    try:
        with open(workflow_path, encoding="utf-8") as handle:
            definition = yaml.safe_load(handle)
    except Exception:
        return ""

    workflow_type = WorkflowDefinition.normalize_workflow_type(
        tier_name,
        default=str(tier_name or "shortened"),
    )
    steps = WorkflowDefinition._resolve_steps(definition, workflow_type)
    if not steps:
        return ""

    title_by_tier = {
        "full": "Full Flow",
        "shortened": "Shortened Flow",
        "fast-track": "Fast-Track",
    }
    title = title_by_tier.get(workflow_type, str(workflow_type).replace("_", " ").title())
    lines = [f"## SOP Checklist — {title}"]

    rendered_index = 1
    for step in steps:
        if not isinstance(step, dict) or step.get("agent_type") == "router":
            continue
        step_name = str(step.get("name") or step.get("id") or f"Step {rendered_index}").strip()
        step_desc = str(step.get("description") or "").strip()
        if step_desc:
            lines.append(f"- [ ] {rendered_index}. **{step_name}** — {step_desc}")
        else:
            lines.append(f"- [ ] {rendered_index}. **{step_name}**")
        rendered_index += 1

    return "\n".join(lines) if rendered_index > 1 else ""


def render_fallback_checklist(*, tier_name: str) -> str:
    """Render minimal fallback checklist when workflow YAML cannot be resolved."""
    heading_map = {
        "full": "Full Flow",
        "shortened": "Shortened Flow",
        "fast-track": "Fast-Track",
    }
    heading = heading_map.get(str(tier_name), str(tier_name).replace("_", " ").title())
    return (
        f"## SOP Checklist — {heading}\n"
        "- [ ] 1. **Implementation** — Complete required workflow steps\n"
        "- [ ] 2. **Verification** — Validate results\n"
        "- [ ] 3. **Documentation** — Record outcome"
    )


def get_sop_tier_for_task(
    *,
    task_type: str,
    title: str | None,
    body: str | None,
    suggest_tier_label: Callable[[str, str], str | None],
    logger: Any,
) -> tuple[str, str, str]:
    """Return (tier_name, sop_template, workflow_label) from content/task type."""
    if title or body:
        try:
            suggested_label = suggest_tier_label(title or "", body or "")
            if suggested_label:
                logger.info("WorkflowRouter suggestion: %s", suggested_label)
                if "fast-track" in suggested_label:
                    return "fast-track", "", "workflow:fast-track"
                if "shortened" in suggested_label:
                    return "shortened", "", "workflow:shortened"
                if "full" in suggested_label:
                    return "full", "", "workflow:full"
        except Exception as exc:
            logger.warning("WorkflowRouter suggestion failed: %s, falling back to task_type", exc)

    if any(t in task_type for t in ["hotfix", "chore", "simple"]):
        return "fast-track", "", "workflow:fast-track"
    if "bug" in task_type:
        return "shortened", "", "workflow:shortened"
    return "full", "", "workflow:full"


def generate_issue_name_with_ai(
    *,
    content: str,
    project_name: str,
    run_analysis: Callable[..., dict[str, Any]],
    slugify: Callable[[str], str],
    logger: Any,
) -> str:
    """Generate concise issue slug, falling back to content-based slug."""
    try:
        logger.info("Generating concise task name with orchestrator...")
        result = run_analysis(text=content[:500], task="generate_name", project_name=project_name)
        suggested_name = str(result.get("text", "")).strip().strip("\"`'").strip()
        slug = slugify(suggested_name)
        if slug:
            logger.info("Orchestrator suggested: %s", slug)
            return slug
        raise ValueError("Empty slug from orchestrator")
    except Exception as exc:
        logger.warning("Name generation failed: %s, using fallback", exc)
        body = re.sub(r"^#.*\n", "", content)
        body = re.sub(r"\*\*.*\*\*.*\n", "", body)
        return slugify(body.strip()) or "generic-task"


def refine_issue_content_with_ai(
    *,
    content: str,
    project_name: str,
    run_analysis: Callable[..., dict[str, Any]],
    logger: Any,
) -> str:
    """Refine task text before issue creation, preserving original on failure."""
    source = str(content or "").strip()
    if not source:
        return source
    try:
        logger.info("Refining issue content with orchestrator (len=%s)", len(source))
        result = run_analysis(text=source, task="refine_description", project_name=project_name)
        candidate = str((result or {}).get("text", "")).strip()
        if candidate:
            return candidate
    except Exception as exc:
        logger.warning("Issue content refinement failed: %s", exc)
    return source
