import logging
from typing import Any

from nexus.core.config import (
    BASE_DIR,
    PROJECT_CONFIG,
    get_inbox_storage_backend,
    get_inbox_dir,
    get_project_display_names,
    get_task_types,
    normalize_project_key,
)
from nexus.core.handlers.common_routing import extract_json_dict
from nexus.core.inbox.inbox_routing_service import (
    process_inbox_task_request,
    save_resolved_inbox_task_request,
)
from nexus.core.integrations.inbox_queue import enqueue_task
from nexus.core.utils.task_name import generate_task_name, normalize_task_name

logger = logging.getLogger(__name__)

# Config-driven registries
PROJECTS = get_project_display_names()
TYPES = get_task_types()


def _render_task_markdown(
    *,
    project: str,
    task_type: str,
    task_name: str,
    content: str,
    raw_text: str,
    requester_context: dict[str, Any] | None = None,
    attachments: list[Any] | None = None,
) -> str:
    context = requester_context if isinstance(requester_context, dict) else {}
    requester_nexus_id = str(context.get("nexus_id") or "").strip()
    requester_block = (
        f"\n**Requester Nexus ID:** `{requester_nexus_id}`"
        if requester_nexus_id
        else ""
    )
    attachments_block = ""
    if attachments:
        lines = []
        for i, att in enumerate(attachments):
            file_id = getattr(att, "file_id", None) or str(att)
            filename = getattr(att, "filename", None) or f"image_{i + 1}"
            lines.append(f"- `{filename}` (file_id: `{file_id}`)")
        attachments_block = "\n## Attachments\n" + "\n".join(lines) + "\n"
    return (
        f"# {TYPES.get(task_type, 'Task')}\n"
        f"**Project:** {PROJECTS.get(project, project)}\n"
        f"**Type:** {task_type}\n"
        f"**Task Name:** {task_name}\n"
        f"**Status:** Pending\n\n"
        f"{content}\n\n"
        f"{attachments_block}"
        f"---\n"
        f"**Source:** inbox\n"
        f"{requester_block}\n"
        f"---\n"
        f"**Raw Input:**\n{raw_text}"
    )


def _parse_classification_result(result: dict[str, Any]) -> dict[str, Any]:
    """Normalize orchestrator classification output into a plain dict payload."""
    if not isinstance(result, dict):
        return {}

    if result.get("project"):
        return result

    for field in ("response", "text", "output"):
        candidate = result.get(field)
        if isinstance(candidate, dict) and candidate:
            return candidate
        if isinstance(candidate, str) and candidate.strip():
            parsed = extract_json_dict(candidate)
            if parsed:
                merged = dict(result)
                merged.update(parsed)
                return merged

    return result


def _refine_task_description(content: str, project: str) -> str:
    """Prepend the project name if it's missing."""
    project_display = PROJECTS.get(project, project)
    if not content.lower().startswith(project.lower()) and not content.lower().startswith(
        project_display.lower()
    ):
        return f"{project_display}: {content}"
    return content


async def process_inbox_task(
    text: str,
    orchestrator,
    message_id_or_unique_id: str,
    project_hint: str | None = None,
    requester_context: dict[str, Any] | None = None,
    authorize_project=None,
    attachments: list[Any] | None = None,
) -> dict[str, Any]:
    """
    Core logic for processing a task from natural language text.
    Classifies the task, creates the markdown file in the inbox, and returns the result.

    Returns a dict with:
    - success: bool
    - message: str (Error or success message to show the user)
    - project: str (Optional)
    - content: str (Optional)
    - pending_resolution: dict (Optional, if project needs manual selection)
    """
    return process_inbox_task_request(
        text=text,
        orchestrator=orchestrator,
        message_id_or_unique_id=message_id_or_unique_id,
        project_hint=project_hint,
        logger=logger,
        normalize_project_key=normalize_project_key,
        projects=PROJECTS,
        project_config=PROJECT_CONFIG,
        types_map=TYPES,
        parse_classification_result=_parse_classification_result,
        refine_task_description=_refine_task_description,
        generate_task_name=generate_task_name,
        normalize_task_name=normalize_task_name,
        render_task_markdown=_render_task_markdown,
        get_inbox_storage_backend=get_inbox_storage_backend,
        enqueue_task=enqueue_task,
        base_dir=BASE_DIR,
        get_inbox_dir=get_inbox_dir,
        requester_context=requester_context,
        authorize_project=authorize_project,
        attachments=attachments,
    )


async def save_resolved_task(
    pending_project: dict,
    selected_project: str,
    message_id_or_unique_id: str,
    requester_context: dict[str, Any] | None = None,
    authorize_project=None,
) -> dict[str, Any]:
    """Save a task that previously lacked a clear project after the user specifies one."""
    return save_resolved_inbox_task_request(
        pending_project=pending_project,
        selected_project=selected_project,
        message_id_or_unique_id=message_id_or_unique_id,
        normalize_project_key=normalize_project_key,
        get_inbox_storage_backend=get_inbox_storage_backend,
        types_map=TYPES,
        project_config=PROJECT_CONFIG,
        refine_task_description=_refine_task_description,
        render_task_markdown=_render_task_markdown,
        enqueue_task=enqueue_task,
        base_dir=BASE_DIR,
        get_inbox_dir=get_inbox_dir,
        logger=logger,
        requester_context=requester_context,
        authorize_project=authorize_project,
    )
