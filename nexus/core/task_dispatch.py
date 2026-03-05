"""Task dispatch helpers extracted from inbox_processor."""

import asyncio
import os
import re
import shutil
from collections.abc import Callable
from typing import Any

from nexus.core.git_sync.workflow_start_sync_service import sync_project_repos_on_workflow_start
from nexus.core.storage.capabilities import get_storage_capabilities


def _local_task_files_enabled() -> bool:
    return get_storage_capabilities().local_task_files


def _should_use_local_task_file(filepath: str) -> bool:
    """Use local file operations for filesystem mode or explicit local paths."""
    if _local_task_files_enabled():
        return True
    candidate = str(filepath or "").strip()
    return bool(candidate) and "://" not in candidate


def handle_webhook_task(
    *,
    filepath: str,
    content: str,
    project_name: str,
    project_root: str,
    config: dict[str, Any],
    base_dir: str,
    logger,
    emit_alert: Callable[..., Any],
    get_repos_for_project: Callable[[str], list[str]],
    extract_repo_from_issue_url: Callable[[str], str],
    resolve_project_for_repo: Callable[[str], str | None],
    reroute_webhook_task_to_project: Callable[[str, str], str | None],
    get_tasks_active_dir: Callable[[str, str], str],
    is_recent_launch: Callable[[str], bool],
    get_initial_agent_from_workflow: Callable[[str], str],
    get_repo_for_project: Callable[[str], str],
    resolve_tier_for_issue: Callable[..., str | None],
    invoke_ai_agent: Callable[..., tuple[int | None, str | None]],
    requester_nexus_id: str | None = None,
) -> bool:
    """Handle webhook-origin task files. Returns True when consumed."""
    source_match = re.search(r"\*\*Source:\*\*\s*(.+)", content)
    source = source_match.group(1).strip().lower() if source_match else None
    if source != "webhook":
        return False

    issue_num_match = re.search(r"\*\*Issue Number:\*\*\s*(.+)", content)
    issue_url_match = re.search(r"\*\*URL:\*\*\s*(.+)", content)
    agent_type_match = re.search(r"\*\*Agent Type:\*\*\s*(.+)", content)

    issue_number = issue_num_match.group(1).strip() if issue_num_match else None
    issue_url = issue_url_match.group(1).strip() if issue_url_match else None
    agent_type = agent_type_match.group(1).strip() if agent_type_match else "triage"

    if not issue_url or not issue_number:
        logger.error("⚠️ Webhook task missing issue URL or number, skipping: %s", filepath)
        return True

    issue_repo = extract_repo_from_issue_url(issue_url)
    if not issue_repo:
        message = (
            f"🚫 Unable to parse issue repository for webhook task issue #{issue_number}. "
            "Blocking processing to avoid cross-project execution."
        )
        logger.error(message)
        emit_alert(
            message,
            severity="error",
            source="inbox_processor",
            issue_number=str(issue_number),
            project_key=project_name,
        )
        return True

    try:
        configured_repos = get_repos_for_project(project_name)
    except Exception:
        configured_repos = []

    if configured_repos and issue_repo not in configured_repos:
        reroute_project = resolve_project_for_repo(issue_repo)
        if reroute_project and reroute_project != project_name:
            rerouted_path = None
            if _should_use_local_task_file(filepath):
                rerouted_path = reroute_webhook_task_to_project(filepath, reroute_project)
            message = (
                f"⚠️ Re-routed webhook task for issue #{issue_number}: "
                f"repo {issue_repo} does not match project {project_name} ({configured_repos}); "
                + (
                    f"moved to project '{reroute_project}'."
                    if rerouted_path
                    else f"would route to project '{reroute_project}' (non-filesystem queue payload)."
                )
            )
            logger.warning(message)
            emit_alert(
                message,
                severity="warning",
                source="inbox_processor",
                issue_number=str(issue_number),
                project_key=project_name,
            )
            if rerouted_path:
                logger.info("Moved webhook task to: %s", rerouted_path)
            return True

        message = (
            f"🚫 Project boundary violation for issue #{issue_number}: "
            f"task under project '{project_name}' ({configured_repos}) "
            f"but issue URL points to '{issue_repo}'. Processing blocked."
        )
        logger.error(message)
        emit_alert(
            message,
            severity="error",
            source="inbox_processor",
            issue_number=str(issue_number),
            project_key=project_name,
        )
        return True

    logger.info("📌 Webhook task for existing issue #%s, launching agent directly", issue_number)

    if is_recent_launch(issue_number):
        logger.info(
            "⏭️ Skipping webhook launch for issue #%s — agent recently launched", issue_number
        )
        if _should_use_local_task_file(filepath):
            active_dir = get_tasks_active_dir(project_root, project_name)
            os.makedirs(active_dir, exist_ok=True)
            new_filepath = os.path.join(active_dir, os.path.basename(filepath))
            shutil.move(filepath, new_filepath)
        return True

    new_filepath = ""
    if _should_use_local_task_file(filepath):
        active_dir = get_tasks_active_dir(project_root, project_name)
        os.makedirs(active_dir, exist_ok=True)
        new_filepath = os.path.join(active_dir, os.path.basename(filepath))
        logger.info("Moving task to active: %s", new_filepath)
        shutil.move(filepath, new_filepath)

    agents_dir_val = config.get("agents_dir")
    if agents_dir_val and issue_url:
        agents_abs = os.path.join(base_dir, agents_dir_val)
        workspace_abs = os.path.join(base_dir, str(config["workspace"]))

        if not agent_type or agent_type == "triage":
            agent_type = get_initial_agent_from_workflow(project_name)
            if not agent_type:
                logger.error("Stopping launch: missing workflow definition for %s", project_name)
                emit_alert(
                    f"Stopping launch: missing workflow for {project_name}",
                    severity="error",
                    source="inbox_processor",
                    issue_number=str(issue_number),
                    project_key=project_name,
                )
                return True

        try:
            repo_for_tier = get_repo_for_project(project_name)
        except Exception:
            repo_for_tier = ""

        if not repo_for_tier:
            logger.error(
                "Missing git_repo for project '%s', cannot resolve tier for issue #%s.",
                project_name,
                issue_number,
            )
            emit_alert(
                f"Missing git_repo for project '{project_name}' (issue #{issue_number}).",
                severity="error",
                source="inbox_processor",
                issue_number=str(issue_number),
                project_key=project_name,
            )
            return True

        try:
            tier_name = resolve_tier_for_issue(
                issue_number,
                project_name,
                repo_for_tier,
                context="webhook launch",
                requester_nexus_id=requester_nexus_id,
            )
        except TypeError:
            tier_name = resolve_tier_for_issue(
                issue_number,
                project_name,
                repo_for_tier,
                context="webhook launch",
            )
        if not tier_name:
            return True

        pid, tool_used = invoke_ai_agent(
            agents_dir=agents_abs,
            workspace_dir=workspace_abs,
            issue_url=issue_url,
            tier_name=tier_name,
            task_content=content,
            log_subdir=project_name,
            agent_type=agent_type,
            project_name=project_name,
            requester_nexus_id=requester_nexus_id,
        )

        if pid and new_filepath:
            try:
                with open(new_filepath, "a") as f:
                    f.write(f"\n**Agent PID:** {pid}\n")
                    f.write(f"**Agent Tool:** {tool_used}\n")
            except Exception as exc:
                logger.error("Failed to append PID: %s", exc)

        logger.info("✅ Launched %s agent for webhook issue #%s", agent_type, issue_number)
    else:
        logger.info("ℹ️ No agents directory for %s, skipping agent launch.", project_name)

    return True


def handle_new_task(
    *,
    filepath: str,
    content: str,
    task_type: str,
    project_name: str,
    project_root: str,
    config: dict[str, Any],
    base_dir: str,
    logger,
    emit_alert: Callable[..., Any],
    get_repo_for_project: Callable[[str], str],
    get_tasks_active_dir: Callable[[str, str], str],
    refine_issue_content: Callable[[str, str], str],
    extract_inline_task_name: Callable[[str], str],
    slugify: Callable[[str], str],
    generate_issue_name: Callable[[str, str], str],
    get_sop_tier: Callable[..., tuple[str, str, str]],
    render_checklist_from_workflow: Callable[[str, str], str],
    render_fallback_checklist: Callable[[str], str],
    create_issue: Callable[..., str],
    rename_task_file_and_sync_issue_body: Callable[..., str],
    get_workflow_state_plugin,
    workflow_state_plugin_kwargs: dict[str, Any],
    start_workflow: Callable[[str, str], Any],
    get_initial_agent_from_workflow: Callable[[str], str],
    invoke_ai_agent: Callable[..., tuple[int | None, str | None]],
    get_repos_for_project: Callable[[str], list[str]] | None = None,
    get_repo_branch_for_project: Callable[[str, str], str] | None = None,
    resolve_git_dir_for_project: Callable[[str], str | None] | None = None,
    resolve_git_dirs_for_project: Callable[[str], dict[str, str]] | None = None,
    run_workflow_start_git_sync: Callable[..., dict[str, Any]] | None = None,
    requester_nexus_id: str | None = None,
    bind_issue_requester: Callable[..., None] | None = None,
    ensure_project_and_repo_access: Callable[[str, str, str], tuple[bool, str]] | None = None,
) -> None:
    """Handle standard (non-webhook) inbox task end-to-end."""
    content = refine_issue_content(content, str(project_name))

    precomputed_task_name = extract_inline_task_name(content)
    if precomputed_task_name:
        slug = slugify(precomputed_task_name)
        if slug:
            logger.info("✅ Using pre-generated task name: %s", slug)
        else:
            slug = generate_issue_name(content, project_name)
    else:
        slug = generate_issue_name(content, project_name)

    tier_name, sop_template, workflow_label = get_sop_tier(
        task_type=task_type,
        title=slug,
        body=content,
    )
    workflow_checklist = render_checklist_from_workflow(project_name, tier_name)
    sop_checklist = workflow_checklist or sop_template or render_fallback_checklist(tier_name)

    new_filepath = ""
    if _should_use_local_task_file(filepath):
        active_dir = get_tasks_active_dir(project_root, project_name)
        os.makedirs(active_dir, exist_ok=True)
        new_filepath = os.path.join(active_dir, os.path.basename(filepath))
        logger.info("Moving task to active: %s", new_filepath)
        shutil.move(filepath, new_filepath)

    type_prefixes = {
        "feature": "feat",
        "feature-simple": "feat",
        "bug": "fix",
        "hotfix": "hotfix",
        "chore": "chore",
        "refactor": "refactor",
        "improvement": "feat",
        "improvement-simple": "feat",
    }
    prefix = type_prefixes.get(
        task_type, task_type.split("-")[0] if "-" in task_type else task_type
    )
    issue_title = f"[{project_name}] {prefix}/{slug}"
    branch_name = f"{prefix}/{slug}"
    dedupe_key = os.path.basename(str(filepath or "")).strip()
    if not dedupe_key:
        dedupe_key = None

    task_file_line = f"\n**Task File:** `{new_filepath}`" if new_filepath else ""
    issue_body = f"""## Task
{content}

---

{sop_checklist}

---

**Project:** {project_name}
**Tier:** {tier_name}
**Target Branch:** `{branch_name}`{task_file_line}"""

    repo_key = get_repo_for_project(project_name)
    if callable(ensure_project_and_repo_access):
        allowed, error_message = ensure_project_and_repo_access(
            str(requester_nexus_id or ""),
            str(project_name),
            str(repo_key),
        )
        if not allowed:
            logger.error(
                "Requester access denied before issue creation for project=%s requester=%s: %s",
                project_name,
                requester_nexus_id,
                error_message,
            )
            emit_alert(
                f"🚫 Request denied for project '{project_name}': {error_message}",
                severity="error",
                source="inbox_processor",
                project_key=str(project_name),
            )
            return

    issue_url = create_issue(
        title=issue_title,
        body=issue_body,
        project=project_name,
        workflow_label=workflow_label,
        task_type=task_type,
        repo_key=repo_key,
        dedupe_key=dedupe_key,
        requester_nexus_id=requester_nexus_id,
    )

    issue_num = ""
    workflow_id = ""
    workflow_plugin = None
    if issue_url:
        issue_match = re.search(r"/issues/(\\d+)(?:$|[/?#])", str(issue_url))
        if issue_match:
            issue_num = str(issue_match.group(1))
        else:
            issue_num = (
                str(issue_url).rstrip("/").split("/")[-1].split("?", 1)[0].split("#", 1)[0]
            )
        if requester_nexus_id and callable(bind_issue_requester):
            try:
                issue_num_int = int(str(issue_num))
                bind_issue_requester(
                    repo_key=str(repo_key),
                    issue_number=issue_num_int,
                    issue_url=str(issue_url),
                    project_key=str(project_name),
                    requester_nexus_id=str(requester_nexus_id),
                )
            except Exception as exc:
                logger.warning(
                    "Failed to bind issue requester for issue=%s requester=%s: %s",
                    issue_num,
                    requester_nexus_id,
                    exc,
                )
        if new_filepath:
            old_basename = os.path.basename(new_filepath)
            new_basename = re.sub(r"_(\d+)\.md$", f"_{issue_num}.md", old_basename)
            if new_basename != old_basename:
                try:
                    try:
                        new_filepath = rename_task_file_and_sync_issue_body(
                            task_file_path=new_filepath,
                            issue_url=issue_url,
                            issue_body=issue_body,
                            project_name=project_name,
                            repo_key=repo_key,
                            requester_nexus_id=requester_nexus_id,
                        )
                    except TypeError:
                        new_filepath = rename_task_file_and_sync_issue_body(
                            task_file_path=new_filepath,
                            issue_url=issue_url,
                            issue_body=issue_body,
                            project_name=project_name,
                            repo_key=repo_key,
                        )
                except Exception as exc:
                    logger.error("Failed to rename task file to issue-number name: %s", exc)

            try:
                with open(new_filepath, "a") as f:
                    f.write(f"\n\n**Issue:** {issue_url}\n")
            except Exception as exc:
                logger.error("Failed to append issue URL: %s", exc)

        workflow_plugin = get_workflow_state_plugin(
            **workflow_state_plugin_kwargs,
            repo_key=repo_key,
            cache_key="workflow:state-engine",
        )

        workflow_id = asyncio.run(
            workflow_plugin.create_workflow_for_issue(
                issue_number=issue_num,
                issue_title=slug,
                project_name=project_name,
                tier_name=tier_name,
                task_type=task_type,
                description=content,
            )
        )
        if workflow_id:
            logger.info("✅ Created nexus-arc workflow: %s", workflow_id)
            started = asyncio.run(start_workflow(workflow_id, issue_num))
            if not started:
                logger.warning(
                    "Created workflow %s for issue #%s but failed to start it",
                    workflow_id,
                    issue_num,
                )

            if new_filepath:
                try:
                    with open(new_filepath, "a") as f:
                        f.write(f"**Workflow ID:** {workflow_id}\n")
                except Exception as exc:
                    logger.error("Failed to append workflow ID: %s", exc)

        if workflow_id:
            emit_alert(
                (
                    f"✅ Issue #{issue_num} created and workflow started for project '{project_name}'.\n"
                    f"Workflow: {workflow_id}\n"
                    f"Issue: {issue_url}"
                ),
                severity="info",
                source="inbox_processor",
                issue_number=str(issue_num),
                project_key=str(project_name),
            )
        else:
            emit_alert(
                (
                    f"✅ Issue #{issue_num} created for project '{project_name}'.\n"
                    f"Issue: {issue_url}"
                ),
                severity="info",
                source="inbox_processor",
                issue_number=str(issue_num),
                project_key=str(project_name),
            )

    agents_dir_val = config["agents_dir"]
    if agents_dir_val is not None and issue_url:
        agents_abs = os.path.join(base_dir, agents_dir_val)
        workspace_abs = os.path.join(base_dir, str(config["workspace"]))
        initial_agent = get_initial_agent_from_workflow(project_name)
        if not initial_agent:
            logger.error("Stopping launch: missing workflow definition for %s", project_name)
            emit_alert(
                f"Stopping launch: missing workflow definition for {project_name}",
                severity="error",
                source="inbox_processor",
                issue_number=str(issue_num),
                project_key=project_name,
            )
            return

        configured_repos: list[str] = []
        if callable(get_repos_for_project):
            try:
                configured_repos = [str(item).strip() for item in get_repos_for_project(project_name)]
                configured_repos = [item for item in configured_repos if item]
            except Exception:
                configured_repos = []
        if not configured_repos:
            primary_repo = str(config.get("git_repo") or "").strip()
            if primary_repo:
                configured_repos.append(primary_repo)
            repo_list = config.get("git_repos")
            if isinstance(repo_list, list):
                for repo_name in repo_list:
                    value = str(repo_name or "").strip()
                    if value and value not in configured_repos:
                        configured_repos.append(value)

        def _resolve_branch(repo_slug: str) -> str:
            if callable(get_repo_branch_for_project):
                try:
                    value = str(get_repo_branch_for_project(project_name, repo_slug) or "").strip()
                    if value:
                        return value
                except Exception:
                    pass

            branch_cfg = config.get("git_branches")
            if isinstance(branch_cfg, dict):
                per_repo = branch_cfg.get("repos")
                if isinstance(per_repo, dict):
                    candidate = per_repo.get(repo_slug)
                    if isinstance(candidate, str) and candidate.strip():
                        return candidate.strip()
                default_branch = branch_cfg.get("default")
                if isinstance(default_branch, str) and default_branch.strip():
                    return default_branch.strip()
            return "main"

        def _resolve_single_git_dir(_project: str) -> str | None:
            if callable(resolve_git_dir_for_project):
                try:
                    return resolve_git_dir_for_project(_project)
                except Exception:
                    return None
            if os.path.isdir(os.path.join(project_root, ".git")):
                return project_root
            if configured_repos:
                repo_name = configured_repos[0].split("/")[-1]
                candidate = os.path.join(project_root, repo_name)
                if os.path.isdir(os.path.join(candidate, ".git")):
                    return candidate
            return None

        def _resolve_multi_git_dirs(_project: str) -> dict[str, str]:
            if callable(resolve_git_dirs_for_project):
                try:
                    resolved = resolve_git_dirs_for_project(_project)
                    if isinstance(resolved, dict):
                        return {
                            str(repo_slug): str(path)
                            for repo_slug, path in resolved.items()
                            if str(repo_slug).strip() and str(path).strip()
                        }
                except Exception:
                    pass

            resolved: dict[str, str] = {}
            workspace_abs = project_root
            workspace_is_repo = os.path.isdir(os.path.join(workspace_abs, ".git"))
            workspace_name = os.path.basename(str(workspace_abs).rstrip(os.sep))

            for repo_slug in configured_repos:
                repo_base = repo_slug.split("/")[-1]
                if workspace_is_repo and workspace_name == repo_base:
                    resolved[repo_slug] = workspace_abs
                    continue
                candidate = os.path.join(workspace_abs, repo_base)
                if os.path.isdir(os.path.join(candidate, ".git")):
                    resolved[repo_slug] = candidate
            return resolved

        def _get_repos(_project: str) -> list[str]:
            return list(configured_repos)

        def _should_block_launch(_issue_num: str, _project_name: str) -> bool:
            if workflow_plugin is None:
                return False
            try:
                status = asyncio.run(workflow_plugin.get_workflow_status(str(_issue_num)))
            except Exception:
                return False
            state = str((status or {}).get("state", "")).strip().lower()
            return state in {"paused", "cancelled", "completed", "failed"}

        sync_runner = run_workflow_start_git_sync or sync_project_repos_on_workflow_start
        try:
            sync_result = sync_runner(
                issue_number=str(issue_num or ""),
                project_name=str(project_name),
                project_cfg=config,
                resolve_git_dirs=_resolve_multi_git_dirs,
                resolve_git_dir=_resolve_single_git_dir,
                get_repos=_get_repos,
                get_repo_branch=lambda project, repo_slug: _resolve_branch(str(repo_slug)),
                emit_alert=emit_alert,
                logger=logger,
                should_block_launch=_should_block_launch,
            )
        except Exception as sync_exc:
            logger.warning(
                "Workflow-start git sync failed unexpectedly for issue #%s: %s",
                issue_num,
                sync_exc,
            )
            sync_result = {"enabled": True, "skipped": True, "reason": "sync_error"}
        if bool((sync_result or {}).get("blocked")):
            logger.warning(
                "Initial launch blocked after workflow-start git sync decision for issue #%s",
                issue_num,
            )
            emit_alert(
                (
                    f"🚫 Initial launch blocked for issue #{issue_num} after git-sync decision.\n"
                    "Use /continue when ready."
                ),
                severity="warning",
                source="inbox_processor",
                issue_number=str(issue_num),
                project_key=project_name,
            )
            return

        pid, tool_used = invoke_ai_agent(
            agents_dir=agents_abs,
            workspace_dir=workspace_abs,
            issue_url=issue_url,
            tier_name=tier_name,
            task_content=content,
            log_subdir=project_name,
            agent_type=initial_agent,
            project_name=project_name,
            requester_nexus_id=requester_nexus_id,
        )

        if pid and new_filepath:
            try:
                with open(new_filepath, "a") as f:
                    f.write(f"**Agent PID:** {pid}\n")
                    f.write(f"**Agent Tool:** {tool_used}\n")
            except Exception as exc:
                logger.error("Failed to append PID: %s", exc)
    else:
        logger.info("ℹ️ No agents directory for %s, skipping agent invocation.", project_name)

    logger.info("✅ Dispatch complete for [%s] %s (Tier: %s)", project_name, slug, tier_name)
