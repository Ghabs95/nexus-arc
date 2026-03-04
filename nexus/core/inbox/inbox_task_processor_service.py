import inspect
import os
import re


def _invoke_with_supported_kwargs(fn, kwargs: dict):
    """Call fn with only kwargs supported by its signature."""
    try:
        signature = inspect.signature(fn)
        parameters = signature.parameters
        if any(param.kind == inspect.Parameter.VAR_KEYWORD for param in parameters.values()):
            return fn(**kwargs)
        allowed = {name: value for name, value in kwargs.items() if name in parameters}
        return fn(**allowed)
    except Exception:
        return fn(**kwargs)


def process_task_payload(
    *,
    project_key: str,
    workspace: str,
    filename: str,
    content: str,
    project_config: dict,
    base_dir: str,
    logger,
    process_task_context_fn,
) -> bool:
    """Process a Postgres inbox payload without writing a temporary task file."""
    logger.info(
        "Processing queued task payload: project=%s workspace=%s filename=%s",
        project_key,
        workspace,
        filename,
    )
    try:
        cfg = project_config.get(str(project_key))
        if not isinstance(cfg, dict):
            logger.warning(
                "⚠️ No project config for queued task project '%s', skipping.", project_key
            )
            return False
        type_match = re.search(r"\*\*Type:\*\*\s*(.+)", str(content or ""))
        task_type = type_match.group(1).strip().lower() if type_match else "feature"
        requester_nexus_match = re.search(
            r"\*\*Requester Nexus ID:\*\*\s*`?([^`\n]+)`?",
            str(content or ""),
        )
        requester_platform_match = re.search(
            r"\*\*Requester Platform:\*\*\s*([^\n]+)",
            str(content or ""),
        )
        requester_platform_user_match = re.search(
            r"\*\*Requester Platform User ID:\*\*\s*`?([^`\n]+)`?",
            str(content or ""),
        )
        task_ctx = {
            "content": str(content or ""),
            "task_type": task_type,
            "project_name": str(project_key),
            "project_root": os.path.join(
                base_dir, str(workspace or cfg.get("workspace", project_key))
            ),
            "config": cfg,
            "requester_nexus_id": (
                str(requester_nexus_match.group(1)).strip()
                if requester_nexus_match
                else None
            ),
            "requester_platform": (
                str(requester_platform_match.group(1)).strip().lower()
                if requester_platform_match
                else None
            ),
            "requester_platform_user_id": (
                str(requester_platform_user_match.group(1)).strip()
                if requester_platform_user_match
                else None
            ),
        }
        synthetic_path = f"postgres://inbox/{project_key}/{filename}"
        return bool(process_task_context_fn(task_ctx=task_ctx, filepath=synthetic_path))
    except Exception as exc:
        logger.error(
            "Failed to process queued task payload for project=%s filename=%s: %s",
            project_key,
            filename,
            exc,
        )
        return False


def process_task_context(*, task_ctx: dict[str, object], filepath: str, deps) -> bool:
    content = task_ctx["content"]
    task_type = str(task_ctx["task_type"])
    project_name = task_ctx["project_name"]
    project_root = task_ctx["project_root"]
    config = task_ctx["config"]
    requester_nexus_id = str(task_ctx.get("requester_nexus_id") or "").strip() or None

    deps["logger"].info(f"Project: {project_name}")

    webhook_kwargs = dict(
        filepath=filepath,
        content=str(content),
        project_name=str(project_name),
        project_root=str(project_root),
        config=config,
        base_dir=deps["base_dir"],
        logger=deps["logger"],
        emit_alert=deps["emit_alert"],
        get_repos_for_project=deps["get_repos_for_project"],
        extract_repo_from_issue_url=deps["extract_repo_from_issue_url"],
        resolve_project_for_repo=deps["resolve_project_for_repo"],
        reroute_webhook_task_to_project=deps["reroute_webhook_task_to_project"],
        get_tasks_active_dir=deps["get_tasks_active_dir"],
        is_recent_launch=deps["is_recent_launch"],
        get_initial_agent_from_workflow=deps["get_initial_agent_from_workflow"],
        get_repo_for_project=deps["get_repo_for_project"],
        resolve_tier_for_issue=deps["resolve_tier_for_issue"],
        invoke_ai_agent=deps["invoke_ai_agent"],
        requester_nexus_id=requester_nexus_id,
    )
    if _invoke_with_supported_kwargs(deps["handle_webhook_task"], webhook_kwargs):
        return True

    new_task_kwargs = dict(
        filepath=filepath,
        content=str(content),
        task_type=task_type,
        project_name=str(project_name),
        project_root=str(project_root),
        config=config,
        base_dir=deps["base_dir"],
        logger=deps["logger"],
        emit_alert=deps["emit_alert"],
        get_repo_for_project=deps["get_repo_for_project"],
        get_tasks_active_dir=deps["get_tasks_active_dir"],
        refine_issue_content=deps["refine_issue_content"],
        extract_inline_task_name=deps["extract_inline_task_name"],
        slugify=deps["slugify"],
        generate_issue_name=deps["generate_issue_name"],
        get_sop_tier=deps["get_sop_tier"],
        render_checklist_from_workflow=deps["render_checklist_from_workflow"],
        render_fallback_checklist=deps["render_fallback_checklist"],
        create_issue=deps["create_issue"],
        rename_task_file_and_sync_issue_body=deps["rename_task_file_and_sync_issue_body"],
        get_workflow_state_plugin=deps["get_workflow_state_plugin"],
        workflow_state_plugin_kwargs=deps["workflow_state_plugin_kwargs"],
        start_workflow=deps["start_workflow"],
        get_initial_agent_from_workflow=deps["get_initial_agent_from_workflow"],
        invoke_ai_agent=deps["invoke_ai_agent"],
        requester_nexus_id=requester_nexus_id,
        bind_issue_requester=deps.get("bind_issue_requester"),
        ensure_project_and_repo_access=deps.get("ensure_project_and_repo_access"),
    )
    _invoke_with_supported_kwargs(deps["handle_new_task"], new_task_kwargs)
    return True
