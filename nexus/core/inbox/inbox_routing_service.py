import logging
import os
from typing import Any, Callable

AuthorizeProjectFn = (
    Callable[[str, dict[str, Any] | None], tuple[bool, str]]
    | Callable[[str], tuple[bool, str]]
)


def _write_inbox_markdown_file(
    *,
    base_dir: str,
    workspace: str,
    project: str,
    filename: str,
    markdown_content: str,
    get_inbox_dir: Callable[[str, str], str],
) -> str:
    target_dir = get_inbox_dir(os.path.join(base_dir, str(workspace)), str(project))
    os.makedirs(target_dir, exist_ok=True)
    target_file = os.path.join(target_dir, filename)
    with open(target_file, "w", encoding="utf-8") as f:
        f.write(markdown_content)
    return target_file


def process_inbox_task_request(
    *,
    text: str,
    orchestrator: Any,
    message_id_or_unique_id: str,
    project_hint: str | None,
    logger: logging.Logger,
    normalize_project_key: Callable[[str], str | None],
    projects: dict[str, str],
    project_config: dict[str, Any],
    types_map: dict[str, str],
    parse_classification_result: Callable[[dict[str, Any]], dict[str, Any]],
    refine_task_description: Callable[[str, str], str],
    generate_task_name: Callable[..., str],
    normalize_task_name: Callable[[str], str],
    render_task_markdown: Callable[..., str],
    get_inbox_storage_backend: Callable[[], str],
    enqueue_task: Callable[..., int],
    base_dir: str,
    get_inbox_dir: Callable[[str, str], str],
    requester_context: dict[str, Any] | None = None,
    authorize_project: AuthorizeProjectFn | None = None,
) -> dict[str, Any]:
    normalized_project_hint = (
        normalize_project_key(str(project_hint or "")) or str(project_hint or "").strip().lower()
    )
    known_projects = dict(projects)
    if isinstance(project_config, dict):
        for project_key in project_config.keys():
            normalized_key = str(project_key).strip().lower()
            if normalized_key:
                known_projects.setdefault(normalized_key, normalized_key)

    if normalized_project_hint in known_projects:
        logger.info(
            "Using project context '%s' directly; skipping project classification.",
            normalized_project_hint,
        )
        result: dict[str, Any] = {
            "project": normalized_project_hint,
            "type": "feature",
            "task_name": "",
            "content": text,
        }
    else:
        logger.info("Running task classification...")
        result = orchestrator.run_text_to_speech_analysis(
            text=text,
            task="classify",
            projects=list(known_projects.keys()),
            types=list(types_map.keys()),
        )
        logger.info("Analysis result: %s", result)

    try:
        result = parse_classification_result(result)
        project = result.get("project")
        if isinstance(project, str):
            project = normalize_project_key(project) or project.strip().lower()
        if (
            not project or project not in known_projects
        ) and normalized_project_hint in known_projects:
            logger.info(
                "Using contextual project fallback '%s' for inbox routing", normalized_project_hint
            )
            project = normalized_project_hint
        if not project or project not in known_projects:
            task_type = result.get("type", "feature")
            if task_type not in types_map:
                task_type = "feature"
            pending_resolution = {
                "raw_text": text or "",
                "content": result.get("text", text or ""),
                "task_type": task_type,
                "task_name": result.get("task_name", ""),
                "requester_context": requester_context if isinstance(requester_context, dict) else {},
            }
            options = ", ".join(sorted(projects.keys()))
            logger.error(
                "Project classification failed: project=%s, valid=%s",
                project,
                list(known_projects.keys()),
            )
            return {
                "success": False,
                "message": (
                    f"❌ Could not classify project (received: '{project}').\n\n"
                    f"Reply with a project key: {options}"
                ),
                "pending_resolution": pending_resolution,
            }

        task_type = result.get("type", "feature")
        if task_type not in types_map:
            logger.warning("Type '%s' not in TYPES, defaulting to 'feature'", task_type)
            task_type = "feature"

        content = refine_task_description(result.get("content") or text, str(project))
        task_name = normalize_task_name(result.get("task_name", ""))
        if not task_name:
            task_name = generate_task_name(
                orchestrator,
                content,
                known_projects.get(str(project), str(project)),
                logger=logger,
            )
        logger.info("Parsed: project=%s, type=%s, task_name=%s", project, task_type, task_name)
    except Exception as exc:
        logger.error("JSON parsing error: %s", exc, exc_info=True)
        return {"success": False, "message": "⚠️ JSON Error"}

    if callable(authorize_project):
        try:
            allowed, error_message = authorize_project(str(project), requester_context)
        except TypeError:
            allowed, error_message = authorize_project(str(project))
        if not allowed:
            return {
                "success": False,
                "message": error_message or "🔒 Unauthorized project access.",
            }

    inbox_backend = get_inbox_storage_backend()
    try:
        markdown_content = render_task_markdown(
            project=str(project),
            task_type=str(task_type),
            task_name=str(task_name),
            content=str(content),
            raw_text=str(text),
            requester_context=requester_context,
        )
    except TypeError:
        markdown_content = render_task_markdown(
            project=str(project),
            task_type=str(task_type),
            task_name=str(task_name),
            content=str(content),
            raw_text=str(text),
        )

    workspace = project
    if project in project_config:
        workspace = project_config[project].get("workspace", project)
        logger.info("Mapped project '%s' → workspace '%s'", project, workspace)
    else:
        logger.warning("Project '%s' not in PROJECT_CONFIG, using as-is for workspace", project)

    filename = f"task_{message_id_or_unique_id}.md"
    queue_id: int | None = None
    if inbox_backend == "postgres":
        try:
            queue_id = enqueue_task(
                project_key=str(project),
                workspace=str(workspace),
                filename=filename,
                markdown_content=markdown_content,
            )
            logger.info("✅ Postgres inbox task queued: id=%s project=%s", queue_id, project)
        except Exception as exc:
            logger.error("Failed to enqueue Postgres inbox task: %s", exc)
            return {
                "success": False,
                "message": f"⚠️ Failed to queue task in Postgres inbox: {exc}",
            }
    else:
        _write_inbox_markdown_file(
            base_dir=base_dir,
            workspace=str(workspace),
            project=str(project),
            filename=filename,
            markdown_content=markdown_content,
            get_inbox_dir=get_inbox_dir,
        )

    return {
        "success": True,
        "message": (
            f"✅ Routed to `{project}`\n"
            f"📝 *{content}*\n"
            + (
                f"\n📥 Queued for processor dispatch (queue id: {queue_id}). Issue/workflow creation will start shortly."
                if queue_id is not None
                else "\n📥 Task saved for processor dispatch. Issue/workflow creation will start shortly."
            )
        ),
        "project": project,
        "content": content,
    }


def save_resolved_inbox_task_request(
    *,
    pending_project: dict[str, Any],
    selected_project: str,
    message_id_or_unique_id: str,
    normalize_project_key: Callable[[str], str | None],
    get_inbox_storage_backend: Callable[[], str],
    types_map: dict[str, str],
    project_config: dict[str, Any],
    refine_task_description: Callable[[str, str], str],
    render_task_markdown: Callable[..., str],
    enqueue_task: Callable[..., int],
    base_dir: str,
    get_inbox_dir: Callable[[str, str], str],
    logger: logging.Logger,
    requester_context: dict[str, Any] | None = None,
    authorize_project: AuthorizeProjectFn | None = None,
) -> dict[str, Any]:
    inbox_backend = get_inbox_storage_backend()
    project = normalize_project_key(selected_project) or selected_project
    task_type = str(pending_project.get("task_type", "feature"))
    if task_type not in types_map:
        task_type = "feature"
    text = str(pending_project.get("raw_text", "")).strip()
    content = refine_task_description(str(pending_project.get("content", text)).strip(), project)
    task_name = str(pending_project.get("task_name", "")).strip()
    resolved_requester_context = (
        requester_context
        if isinstance(requester_context, dict)
        else pending_project.get("requester_context")
    )
    if not isinstance(resolved_requester_context, dict):
        resolved_requester_context = {}

    if callable(authorize_project):
        try:
            allowed, error_message = authorize_project(str(project), resolved_requester_context)
        except TypeError:
            allowed, error_message = authorize_project(str(project))
        if not allowed:
            return {
                "success": False,
                "message": error_message or "🔒 Unauthorized project access.",
            }

    workspace = str(project)
    if project in project_config:
        workspace = str(project_config[project].get("workspace", project))
    filename = f"task_{message_id_or_unique_id}.md"
    try:
        markdown_content = render_task_markdown(
            project=str(project),
            task_type=str(task_type),
            task_name=str(task_name),
            content=str(content),
            raw_text=str(text),
            requester_context=resolved_requester_context,
        )
    except TypeError:
        markdown_content = render_task_markdown(
            project=str(project),
            task_type=str(task_type),
            task_name=str(task_name),
            content=str(content),
            raw_text=str(text),
        )

    if inbox_backend == "postgres":
        try:
            enqueue_task(
                project_key=str(project),
                workspace=str(workspace),
                filename=filename,
                markdown_content=markdown_content,
            )
        except Exception as exc:
            logger.error("Failed to enqueue resolved Postgres inbox task: %s", exc)
            return {
                "success": False,
                "message": f"⚠️ Failed to queue task in Postgres inbox: {exc}",
            }
    else:
        _write_inbox_markdown_file(
            base_dir=base_dir,
            workspace=str(workspace),
            project=str(project),
            filename=filename,
            markdown_content=markdown_content,
            get_inbox_dir=get_inbox_dir,
        )

    return {
        "success": True,
        "message": (
            f"✅ Routed to `{project}`\n"
            f"📝 *{content}*\n"
            "\n📥 Task saved for processor dispatch. Issue/workflow creation will start shortly."
        ),
    }
