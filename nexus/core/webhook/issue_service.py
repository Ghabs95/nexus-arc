"""Webhook issue event handling extracted from webhook_server."""

import os
from pathlib import Path
from typing import Any

from nexus.core.execution_mode import infer_execution_mode_from_labels


def _notify_lifecycle_dedup(
    notify_lifecycle,
    message: str,
    *,
    dedup_key: str | None = None,
) -> bool:
    """Invoke lifecycle notifier while preserving compatibility with old call signatures."""
    if not callable(notify_lifecycle):
        return False
    if dedup_key:
        try:
            return bool(notify_lifecycle(message, dedup_key=dedup_key))
        except TypeError:
            pass
    return bool(notify_lifecycle(message))


def _write_webhook_task_file(inbox_dir: str, task_filename: str, task_content: str) -> str:
    Path(inbox_dir).mkdir(parents=True, exist_ok=True)
    task_file = Path(inbox_dir) / task_filename
    task_file.write_text(task_content, encoding="utf-8")
    return str(task_file)


def handle_issue_opened_event(
    *,
    event: dict[str, Any],
    logger,
    policy,
    notify_lifecycle,
    emit_alert,
    project_config: dict[str, Any],
    base_dir: str,
    project_repos,
    get_repos,
    get_tasks_active_dir,
    get_inbox_dir,
    get_inbox_storage_backend=None,
    enqueue_task=None,
    cleanup_worktree_for_issue=None,
    bind_issue_requester=None,
    find_user_credentials_by_github_identity=None,
    find_user_credentials_by_gitlab_identity=None,
) -> dict[str, Any]:
    """Handle parsed issue webhook event and create inbox task file when applicable."""
    action = event.get("action")
    issue_number = event.get("number", "")
    issue_title = event.get("title", "")
    issue_body = event.get("body", "")
    issue_author = event.get("author", "")
    issue_url = event.get("url", "")
    issue_labels = event.get("labels", [])
    repo_name = event.get("repo", "unknown")

    logger.info("📋 Issue event (%s): #%s - %s by %s", action, issue_number, issue_title, issue_author)

    if action == "closed":
        message = policy.build_issue_closed_message(event)
        _notify_lifecycle_dedup(notify_lifecycle, message)
        cleanup_ok = None
        if callable(cleanup_worktree_for_issue):
            try:
                cleanup_ok = bool(cleanup_worktree_for_issue(repo_name, str(issue_number)))
            except Exception as exc:
                logger.warning(
                    "Failed webhook issue-close worktree cleanup for issue #%s in %s: %s",
                    issue_number,
                    repo_name,
                    exc,
                )
                cleanup_ok = False
        return {
            "status": "issue_closed_notified",
            "issue": issue_number,
            "worktree_cleanup": cleanup_ok,
        }

    plan_requested = "agent:plan-requested" in issue_labels
    execution_mode = infer_execution_mode_from_labels(issue_labels)
    if action not in {"opened", "labeled"}:
        return {"status": "ignored", "reason": f"action is {action}, not opened/labeled"}
    if action == "labeled" and not plan_requested:
        return {"status": "ignored", "reason": "labeled action without agent:plan-requested"}

    workflow_labels = [l for l in issue_labels if str(l).startswith("workflow:")]
    if action == "opened" and workflow_labels:
        message = policy.build_issue_created_message(event, "workflow")
        _notify_lifecycle_dedup(
            notify_lifecycle,
            message,
            dedup_key=f"issue-created:{repo_name}:{issue_number}",
        )
        logger.info(
            "⏭️ Self-created issue #%s (workflow labels: %s): notified lifecycle only; skipping inbox task creation",
            issue_number,
            workflow_labels,
        )
        return {"status": "notified_only", "reason": "self-created issue (has workflow label)"}

    if get_inbox_storage_backend is None:
        get_inbox_storage_backend = lambda: "filesystem"
    if enqueue_task is None:
        enqueue_task = lambda **_kwargs: None

    inbox_backend = str(get_inbox_storage_backend() or "").strip().lower()
    if inbox_backend != "postgres":
        try:
            for key, cfg in project_config.items():
                if isinstance(cfg, dict) and repo_name in project_repos(key, cfg, get_repos):
                    ws = os.path.join(base_dir, cfg.get("workspace", ""))
                    active_dir = get_tasks_active_dir(ws, key)
                    task_path = os.path.join(active_dir, f"issue_{issue_number}.md")
                    if os.path.exists(task_path):
                        logger.info(
                            "⏭️ Skipping issue #%s — active task file already exists: %s",
                            issue_number,
                            task_path,
                        )
                        return {"status": "ignored", "reason": "task file already exists"}
                    break
        except Exception as exc:
            logger.warning("Could not check for existing task file: %s", exc)

    try:
        system_ops = project_config.get("system_operations", {})
        default_agent = str(system_ops.get("default") or "").strip()
        
        # Route to the planning agent when explicitly requested.
        if plan_requested:
            agent_type = str(system_ops.get("plan") or default_agent).strip()
            logger.info("📝 Routing issue #%s to %s agent based on plan label.", issue_number, agent_type)
        else:
            agent_type = str(system_ops.get("inbox") or default_agent).strip()
    except Exception as exc:
        logger.warning("⚠️ Could not load inbox config from system_operations: %s", exc)
        agent_type = ""

    try:
        project_workspace = None
        project_key = None
        for candidate_key, project_cfg in project_config.items():
            if not isinstance(project_cfg, dict):
                continue
            repos = project_repos(candidate_key, project_cfg, get_repos)
            if repo_name in repos:
                project_workspace = project_cfg.get("workspace")
                project_key = candidate_key
                logger.info(
                    "📌 Mapped repository '%s' → project '%s' (workspace: %s)",
                    repo_name,
                    project_key,
                    project_workspace,
                )
                break

        if not project_workspace or not project_key:
            message = (
                f"🚫 No project mapping for repository '{repo_name}'. "
                "Webhook issue task creation blocked to enforce project boundaries."
            )
            logger.error(message)
            emit_alert(message, severity="warning", source="webhook_server")
            return {
                "status": "ignored",
                "reason": "unmapped_repository",
                "repository": repo_name,
                "issue": issue_number,
            }

        # Best-effort requester binding for webhook-created issues so later
        # agent launches can resolve user-scoped API credentials.
        requester_nexus_id = None
        issue_number_str = str(issue_number or "").strip()
        issue_author_norm = str(issue_author or "").strip().lstrip("@").strip()
        issue_url_norm = str(issue_url or "").strip()
        if issue_number_str.isdigit() and issue_author_norm and issue_url_norm:
            try:
                if bind_issue_requester is None:
                    from nexus.core.auth.credential_store import bind_issue_requester as _bind

                    bind_issue_requester = _bind
                if find_user_credentials_by_github_identity is None:
                    from nexus.core.auth.credential_store import (
                        find_user_credentials_by_github_identity as _find_github,
                    )

                    find_user_credentials_by_github_identity = _find_github
                if find_user_credentials_by_gitlab_identity is None:
                    from nexus.core.auth.credential_store import (
                        find_user_credentials_by_gitlab_identity as _find_gitlab,
                    )

                    find_user_credentials_by_gitlab_identity = _find_gitlab

                platform = str(
                    (project_config.get(str(project_key), {}) or {}).get("git_platform") or "github"
                ).strip().lower()
                if platform == "gitlab" and callable(find_user_credentials_by_gitlab_identity):
                    record = find_user_credentials_by_gitlab_identity(
                        gitlab_username=issue_author_norm
                    )
                elif callable(find_user_credentials_by_github_identity):
                    record = find_user_credentials_by_github_identity(github_login=issue_author_norm)
                else:
                    record = None

                resolved_nexus_id = str(getattr(record, "nexus_id", "") or "").strip()
                if resolved_nexus_id and callable(bind_issue_requester):
                    bind_issue_requester(
                        repo_key=str(repo_name),
                        issue_number=int(issue_number_str),
                        issue_url=issue_url_norm,
                        project_key=str(project_key),
                        requester_nexus_id=resolved_nexus_id,
                    )
                    requester_nexus_id = resolved_nexus_id
                    logger.info(
                        "🔐 Bound webhook issue requester: issue=#%s repo=%s author=%s nexus_id=%s",
                        issue_number_str,
                        repo_name,
                        issue_author_norm,
                        resolved_nexus_id,
                    )
            except Exception as exc:
                logger.warning(
                    "Could not bind webhook issue requester for issue #%s: %s",
                    issue_number_str,
                    exc,
                )

        workspace_abs = os.path.join(base_dir, str(project_workspace or ""))
        inbox_dir = get_inbox_dir(workspace_abs, project_key)
        task_filename = f"issue_{issue_number}.md"

        task_content = f"""# Issue #{issue_number}: {issue_title}

**From:** @{issue_author}  
**URL:** {issue_url}  
**Repository:** {repo_name}  
**Agent Type:** {agent_type}
{f"**Execution Mode:** {execution_mode}\n" if execution_mode else ""}**Source:** webhook
**Issue Number:** {issue_number}

## Description

{issue_body or "_(No description provided)_"}

## Labels

{', '.join([f"`{l}`" for l in issue_labels]) if issue_labels else "_None_"}

## Status: Ready for {agent_type} agent

This issue will be routed to the {agent_type} agent as defined in the workflow.
The actual agent assignment depends on the current project's workflow configuration.
"""

        queue_id = None
        if inbox_backend == "postgres":
            # Pyre-ignore because enqueue_task is dynamically provided and kwargs aren't typed
            queue_id = enqueue_task(  # pyre-ignore[28, 19, 21, 6]
                project_key=str(project_key),
                workspace=str(project_workspace),
                filename=task_filename,
                markdown_content=task_content,
            )
            logger.info(
                "✅ Queued webhook issue task in Postgres inbox: id=%s issue=#%s agent_type=%s",
                queue_id,
                issue_number,
                agent_type,
            )
            task_file_str = None
        else:
            task_file_str = _write_webhook_task_file(inbox_dir, task_filename, task_content)
            logger.info("✅ Created task file: %s (agent_type: %s)", task_file_str, agent_type)

        message = policy.build_issue_created_message(event, agent_type)
        _notify_lifecycle_dedup(
            notify_lifecycle,
            message,
            dedup_key=f"issue-created:{repo_name}:{issue_number}",
        )

        return {
            # Keep external webhook contract stable while still exposing queue_id.
            "status": "task_created",
            "issue": issue_number,
            "task_file": task_file_str,
            "queue_id": queue_id,
            "title": issue_title,
            "agent_type": agent_type,
            "repository": repo_name,
            "requester_nexus_id": requester_nexus_id,
        }
    except Exception as exc:
        logger.error(
            "❌ Error creating task file for issue #%s: %s", issue_number, exc, exc_info=True
        )
        emit_alert(
            f"Issue processing error for #{issue_number}: {str(exc)}",
            severity="error",
            source="webhook_server",
        )
        return {"status": "error", "issue": issue_number, "error": str(exc)}
