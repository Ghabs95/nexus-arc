import glob
import os


def get_initial_agent_from_workflow(
    *,
    project_name: str,
    workflow_type: str,
    logger,
    emit_alert,
    get_workflow_definition_path,
    workflow_definition_loader,
) -> str:
    path = get_workflow_definition_path(project_name)
    if not path:
        logger.error(f"Missing workflow_definition_path for project '{project_name}'")
        emit_alert(
            f"Missing workflow_definition_path for project '{project_name}'.",
            severity="error",
            source="inbox_processor",
            project_key=project_name,
        )
        return ""
    if not os.path.exists(path):
        logger.error(f"Workflow definition not found: {path}")
        emit_alert(
            f"Workflow definition not found: {path}",
            severity="error",
            source="inbox_processor",
            project_key=project_name,
        )
        return ""
    try:
        workflow = workflow_definition_loader(path, workflow_type=workflow_type)
        if not workflow.steps:
            logger.error(f"Workflow definition has no steps: {path}")
            emit_alert(
                f"Workflow definition has no steps: {path}",
                severity="error",
                source="inbox_processor",
                project_key=project_name,
            )
            return ""
        first_step = workflow.steps[0]
        return first_step.agent.name or first_step.agent.display_name or ""
    except Exception as exc:
        logger.error(f"Failed to read workflow definition {path}: {exc}")
        emit_alert(
            f"Failed to read workflow definition {path}: {exc}",
            severity="error",
            source="inbox_processor",
            project_key=project_name,
        )
        return ""


def find_task_file_for_issue(
    *,
    issue_num: str,
    db_only_task_mode: bool,
    base_dir: str,
    nexus_dir_name: str,
) -> str | None:
    if db_only_task_mode:
        return None
    issue = str(issue_num).strip()
    if not issue:
        return None

    patterns = [
        os.path.join(base_dir, "**", nexus_dir_name, "tasks", "*", "active", f"issue_{issue}.md"),
        os.path.join(base_dir, "**", nexus_dir_name, "tasks", "*", "active", f"*_{issue}.md"),
        os.path.join(base_dir, "**", nexus_dir_name, "tasks", "*", "closed", f"issue_{issue}.md"),
        os.path.join(base_dir, "**", nexus_dir_name, "tasks", "*", "closed", f"*_{issue}.md"),
    ]
    candidates: list[str] = []
    for pattern in patterns:
        candidates.extend(glob.glob(pattern, recursive=True))
    if not candidates:
        return None
    candidates.sort(key=os.path.getmtime, reverse=True)
    return candidates[0]


def resolve_project_from_task_file(
    *,
    task_file: str,
    project_config: dict,
    base_dir: str,
    iter_project_configs,
    get_repos,
) -> str | None:
    task_abs = os.path.abspath(task_file)
    for project_key, project_cfg in iter_project_configs(project_config, get_repos):
        workspace = project_cfg.get("workspace") if isinstance(project_cfg, dict) else None
        if not workspace:
            continue
        workspace_abs = os.path.abspath(os.path.join(base_dir, str(workspace)))
        if task_abs.startswith(workspace_abs + os.sep) or task_abs == workspace_abs:
            return project_key
    return None


def resolve_project_for_issue(
    *,
    issue_num: str,
    workflow_id: str | None,
    find_task_file_for_issue,
    resolve_project_from_task_file,
    iter_project_configs,
    project_config: dict,
    get_repos,
) -> str | None:
    task_file = find_task_file_for_issue(issue_num)
    if task_file:
        project_name = resolve_project_from_task_file(task_file)
        if project_name:
            return project_name

    normalized_workflow_id = str(workflow_id or "").strip()
    if normalized_workflow_id:
        project_keys = [str(name) for name, _ in iter_project_configs(project_config, get_repos)]
        for project_name in sorted(project_keys, key=len, reverse=True):
            if normalized_workflow_id == project_name or normalized_workflow_id.startswith(
                f"{project_name}-"
            ):
                return project_name
    return None
