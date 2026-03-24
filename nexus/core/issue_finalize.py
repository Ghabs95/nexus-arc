"""Workflow finalization helpers extracted from inbox_processor."""

import asyncio
import inspect
import json
import logging
import os
import re
import subprocess
import threading
from typing import Any

logger = logging.getLogger(__name__)
_GITHUB_PR_URL_RE = re.compile(r"github\.com/[^/]+/[^/]+/pull/([0-9]+)", re.IGNORECASE)
_GITLAB_MR_URL_RE = re.compile(r"/-/merge_requests/([0-9]+)", re.IGNORECASE)


def _run_sync(awaitable_factory):
    def _close_awaitable_if_needed(candidate: object | None) -> None:
        if candidate is None or not inspect.isawaitable(candidate):
            return
        close = getattr(candidate, "close", None)
        if callable(close):
            try:
                close()
            except Exception:
                return

    try:
        asyncio.get_running_loop()
        in_running_loop = True
    except RuntimeError:
        in_running_loop = False

    if not in_running_loop:
        candidate = None
        try:
            candidate = awaitable_factory()
            if not inspect.isawaitable(candidate):
                return candidate
            return asyncio.run(candidate)
        except Exception:
            _close_awaitable_if_needed(candidate)
            raise

    holder: dict[str, object | BaseException | None] = {"value": None, "error": None}

    def _runner() -> None:
        candidate = None
        try:
            candidate = awaitable_factory()
            if not inspect.isawaitable(candidate):
                holder["value"] = candidate
                return
            holder["value"] = asyncio.run(candidate)
        except BaseException as exc:
            _close_awaitable_if_needed(candidate)
            holder["error"] = exc

    worker = threading.Thread(target=_runner, daemon=True)
    worker.start()
    worker.join(timeout=30)
    if worker.is_alive():
        raise TimeoutError("Timed out waiting for async helper to finish")
    error = holder.get("error")
    if isinstance(error, BaseException):
        raise error
    return holder.get("value")


def emit_alert(*args, **kwargs):
    """Proxy host alert emitter."""
    try:
        from nexus.core.integrations.notifications import emit_alert as _host_emit_alert
    except Exception:
        return None
    return _host_emit_alert(*args, **kwargs)


def get_git_platform(
    repo_key: str,
    *,
    project_name: str,
    token_override: str | None = None,
):
    """Resolve provider adapter from host orchestration helper."""
    try:
        from nexus.core.orchestration.nexus_core_helpers import get_git_platform as _host_get_git_platform
    except Exception as exc:
        raise RuntimeError("Host get_git_platform is not available") from exc
    return _host_get_git_platform(
        repo_key,
        project_name=project_name,
        token_override=token_override,
    )


def _is_git_repo(path: str) -> bool:
    try:
        probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=path,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        return probe.returncode == 0 and str(probe.stdout or "").strip() == "true"
    except Exception:
        return False


def _issue_worktree_path(repo_dir: str, issue_number: str) -> str:
    return os.path.join(
        str(repo_dir),
        ".nexus",
        "worktrees",
        f"issue-{str(issue_number).strip()}",
    )


def _resolve_issue_worktree_dir(
    *,
    repo_dir: str,
    issue_number: str,
    base_branch: str | None = None,
    create_if_missing: bool = False,
) -> str | None:
    worktree_dir = _issue_worktree_path(repo_dir, issue_number)
    if os.path.isdir(worktree_dir):
        return worktree_dir
    if not create_if_missing:
        return None
    if not _is_git_repo(str(repo_dir)):
        return str(repo_dir)

    from nexus.core.workspace import WorktreeProvisionError, WorkspaceManager

    branch_name = f"nexus/issue-{str(issue_number).strip()}"
    normalized_base = str(base_branch or "").strip()
    start_ref = f"origin/{normalized_base}" if normalized_base else None
    try:
        created = WorkspaceManager.provision_worktree(
            str(repo_dir),
            str(issue_number),
            branch_name=branch_name,
            start_ref=start_ref,
        )
        WorkspaceManager.sanitize_worktree_helper_scripts(created)
        logger.info(
            "Provisioned missing issue worktree for issue #%s in %s",
            issue_number,
            repo_dir,
        )
        return created
    except WorktreeProvisionError as exc:
        logger.warning(
            "Could not provision issue worktree for issue #%s in %s: %s",
            issue_number,
            repo_dir,
            exc,
        )
        return None


def _git(args: list[str], *, cwd: str, timeout: int = 30) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", f"safe.directory={str(cwd or '').strip()}"] + args,
        cwd=cwd,
        text=True,
        capture_output=True,
        timeout=timeout,
        check=False,
    )


def _repo_has_non_empty_diff(repo_dir: str, *, base_branch: str | None = None) -> tuple[bool, str]:
    status = _git(["status", "--porcelain"], cwd=repo_dir)
    if status.returncode == 0 and str(status.stdout or "").strip():
        return True, "has uncommitted changes"

    base = str(base_branch or "").strip()
    candidates: list[str] = []
    for ref in (f"origin/{base}" if base else "", base, "origin/main", "main", "origin/master", "master"):
        candidate = str(ref or "").strip()
        if not candidate or candidate in candidates:
            continue
        candidates.append(candidate)

    for ref in candidates:
        verify = _git(["rev-parse", "--verify", ref], cwd=repo_dir)
        if verify.returncode != 0:
            continue
        diff = _git(["diff", "--name-only", f"{ref}...HEAD"], cwd=repo_dir)
        if diff.returncode != 0:
            continue
        if str(diff.stdout or "").strip():
            return True, f"non-empty diff vs {ref}"
        return False, f"empty diff vs {ref}"

    return False, "could not resolve base ref for diff comparison"


def _gitlab_mr_has_non_empty_diff(platform, mr_iid: str) -> bool | None:
    fetch = getattr(platform, "_get", None)
    encoded_repo = getattr(platform, "_encoded_repo", "")
    if not callable(fetch) or not str(encoded_repo or "").strip():
        return None
    try:
        payload = _run_sync(lambda: fetch(f"projects/{encoded_repo}/merge_requests/{mr_iid}/changes"))
    except Exception:
        return None

    changes_count_raw = str((payload or {}).get("changes_count") or "").strip()
    if changes_count_raw:
        numeric = "".join(ch for ch in changes_count_raw if ch.isdigit())
        if numeric:
            return int(numeric) > 0
        if changes_count_raw == "0":
            return False

    changes = (payload or {}).get("changes")
    if isinstance(changes, list):
        return len(changes) > 0
    return None


def _github_pr_has_non_empty_diff(platform, pr_number: str) -> bool | None:
    runner = getattr(platform, "_run_gh_command", None)
    payload = None

    if callable(runner):
        args = ["pr", "view", str(pr_number)]
        repo_name = str(getattr(platform, "repo", "") or "").strip()
        if repo_name:
            args.extend(["--repo", repo_name])
        args.extend(["--json", "changedFiles,additions,deletions"])
        try:
            output = runner(args, timeout=30)
            payload = json.loads(output)
        except Exception:
            payload = None

    if payload is None:
        fetch = getattr(platform, "_get", None)
        repo_name = str(getattr(platform, "repo", "") or "").strip()
        if not callable(fetch) or not repo_name:
            return None
        try:
            payload = _run_sync(lambda: fetch(f"repos/{repo_name}/pulls/{pr_number}"))
        except Exception:
            return None

    try:
        changed_files = int(payload.get("changedFiles", 0) or 0)
        additions = int(payload.get("additions", 0) or 0)
        deletions = int(payload.get("deletions", 0) or 0)
    except Exception:
        return None

    return (changed_files > 0) or ((additions + deletions) > 0)


def verify_workflow_terminal_before_finalize(
    *,
    workflow_plugin,
    issue_num: str,
    project_name: str,
    alert_source: str = "inbox_processor",
) -> bool:
    """Return True when finalization may proceed; emit alert on non-terminal state."""
    try:
        if workflow_plugin and hasattr(workflow_plugin, "get_workflow_status"):
            status = _run_sync(lambda: workflow_plugin.get_workflow_status(str(issue_num)))
            state = str((status or {}).get("state", "")).strip().lower()
            if state and state not in {"completed", "failed", "cancelled"}:
                logger.warning(
                    "Skipping finalize for issue #%s: workflow state is non-terminal (%s)",
                    issue_num,
                    state,
                )
                emit_alert(
                    "⚠️ Finalization blocked for "
                    f"issue #{issue_num}: workflow state is `{state}` (expected terminal).",
                    severity="warning",
                    source=alert_source,
                    issue_number=str(issue_num),
                    project_key=project_name,
                )
                return False
    except Exception as exc:
        logger.warning(
            "Could not verify workflow state before finalize for issue #%s: %s",
            issue_num,
            exc,
        )
    return True


def create_pr_from_changes(
    *,
    project_name: str,
    repo: str,
    repo_dir: str,
    issue_number: str,
    title: str,
    body: str,
    issue_repo: str | None = None,
    token_override: str | None = None,
    base_branch: str | None = None,
) -> str | None:
    target_repo_dir = _resolve_issue_worktree_dir(
        repo_dir=str(repo_dir),
        issue_number=str(issue_number),
        base_branch=base_branch,
        create_if_missing=True,
    )
    if not target_repo_dir:
        logger.warning(
            "Cannot create PR/MR for issue #%s in %s: issue worktree unavailable",
            issue_number,
            repo,
        )
        return None
    if str(target_repo_dir) != str(repo_dir):
        logger.info("Using issue worktree for PR creation on issue #%s: %s", issue_number, target_repo_dir)

    platform = get_git_platform(
        repo,
        project_name=project_name,
        token_override=token_override,
    )
    pr_result = _run_sync(
        lambda: platform.create_pr_from_changes(
            repo_dir=target_repo_dir,
            issue_number=issue_number,
            title=title,
            body=body,
            issue_repo=issue_repo,
            base_branch=str(base_branch or "").strip() or "main",
        )
    )
    return pr_result.url if pr_result else None


def close_issue(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
    comment: str | None = None,
    token_override: str | None = None,
) -> bool:
    platform = get_git_platform(
        repo,
        project_name=project_name,
        token_override=token_override,
    )
    return bool(_run_sync(lambda: platform.close_issue(issue_number, comment=comment)))


def find_existing_pr(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
    token_override: str | None = None,
) -> str | None:
    details = find_existing_pr_details(
        project_name=project_name,
        repo=repo,
        issue_number=issue_number,
        token_override=token_override,
    )
    if not details:
        return None
    return str(details.get("url") or "").strip() or None


def find_existing_pr_details(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
    token_override: str | None = None,
) -> dict[str, str] | None:
    platform = get_git_platform(
        repo,
        project_name=project_name,
        token_override=token_override,
    )
    linked = _run_sync(lambda: platform.search_linked_prs(str(issue_number)))
    if not linked:
        return None
    open_pr = next((pr for pr in linked if str(pr.state).lower() == "open"), None)
    selected = open_pr or linked[0]
    url = str(getattr(selected, "url", "") or "").strip()
    if not url:
        return None
    return {
        "url": url,
        "state": str(getattr(selected, "state", "") or "").strip().lower(),
    }


def validate_pr_non_empty_diff(
    *,
    project_name: str,
    repo: str,
    issue_number: str,
    pr_url: str,
    repo_dir: str | None,
    base_branch: str | None = None,
    issue_repo: str | None = None,
    token_override: str | None = None,
) -> tuple[bool, str]:
    normalized_repo_dir = str(repo_dir or "").strip()
    if not normalized_repo_dir:
        return False, f"{repo}: missing local repo_dir for PR/MR diff validation"

    # Check the remote PR/MR diff FIRST (before requiring the local worktree).
    # The worktree may have been cleaned up by the time finalization runs (e.g. after
    # the writer step completes and before the PR is merged), but the remote API
    # still has the authoritative diff.
    normalized_url = str(pr_url or "").strip()
    platform = None
    if normalized_url:
        try:
            platform = get_git_platform(
                repo,
                project_name=project_name,
                token_override=token_override,
            )
        except Exception:
            platform = None

        github_match = _GITHUB_PR_URL_RE.search(normalized_url)
        if github_match and platform is not None:
            remote_ok = _github_pr_has_non_empty_diff(platform, github_match.group(1))
            if remote_ok is True:
                return True, ""
            if remote_ok is False:
                return False, f"{repo}: GitHub PR has empty diff ({normalized_url})"

        gitlab_match = _GITLAB_MR_URL_RE.search(normalized_url)
        if gitlab_match and platform is not None:
            remote_ok = _gitlab_mr_has_non_empty_diff(platform, gitlab_match.group(1))
            if remote_ok is True:
                return True, ""
            if remote_ok is False:
                return False, f"{repo}: GitLab MR has empty diff ({normalized_url})"

    # Fall back to local worktree diff check when remote check was inconclusive.
    target_repo_dir = _resolve_issue_worktree_dir(
        repo_dir=normalized_repo_dir,
        issue_number=str(issue_number),
        base_branch=base_branch,
        create_if_missing=False,
    )
    if not target_repo_dir:
        return (
            False,
            f"{repo}: missing issue worktree .nexus/worktrees/issue-{issue_number} (finalization blocked)",
        )

    local_ok, local_reason = _repo_has_non_empty_diff(
        target_repo_dir,
        base_branch=base_branch,
    )
    if local_ok:
        return True, ""

    issue_repo_ref = str(issue_repo or "").strip()
    context = f" issue_repo={issue_repo_ref}" if issue_repo_ref else ""
    return (
        False,
        f"{repo}: non-empty diff not found ({local_reason}) in {target_repo_dir}.{context}",
    )


def cleanup_worktree(
    *,
    repo_dir: str,
    issue_number: str,
    is_issue_agent_running_fn=None,
) -> bool:
    from nexus.core.workspace import WorkspaceManager

    return bool(
        WorkspaceManager.cleanup_worktree_safe(
            base_repo_path=repo_dir,
            issue_number=str(issue_number),
            is_issue_agent_running=is_issue_agent_running_fn,
            require_clean=True,
        )
    )


def sync_existing_pr_changes(
    *,
    repo_dir: str,
    issue_number: str,
    commit_message: str | None = None,
    issue_repo: str | None = None,
    repo: str | None = None,
    base_branch: str | None = None,
) -> bool:
    del issue_repo, repo  # callback signature compatibility
    target_repo_dir = _resolve_issue_worktree_dir(
        repo_dir=str(repo_dir),
        issue_number=str(issue_number),
        base_branch=base_branch,
        create_if_missing=True,
    )
    if not target_repo_dir:
        logger.warning(
            "Cannot sync PR branch for issue #%s in %s: issue worktree unavailable",
            issue_number,
            repo_dir,
        )
        return False

    status = _git(["status", "--porcelain"], cwd=target_repo_dir)
    if status.returncode != 0:
        logger.warning(
            "Cannot inspect git status for issue #%s in %s: %s",
            issue_number,
            target_repo_dir,
            status.stderr,
        )
        return False
    if not (status.stdout or "").strip():
        return True

    add = _git(["add", "-A"], cwd=target_repo_dir)
    if add.returncode != 0:
        logger.warning(
            "Cannot stage changes for issue #%s in %s: %s",
            issue_number,
            target_repo_dir,
            add.stderr,
        )
        return False

    message = (
        str(commit_message).strip()
        if str(commit_message or "").strip()
        else f"chore: sync final workflow changes for issue #{issue_number}"
    )
    commit = _git(["commit", "-m", message], cwd=target_repo_dir)
    if commit.returncode != 0:
        # Nothing-to-commit can happen when only ignored files changed.
        status_after_add = _git(["status", "--porcelain"], cwd=target_repo_dir)
        if status_after_add.returncode == 0 and not (status_after_add.stdout or "").strip():
            return True
        logger.warning(
            "Cannot commit changes for issue #%s in %s: %s",
            issue_number,
            target_repo_dir,
            commit.stderr,
        )
        return False

    push = _git(["push", "-u", "origin", "HEAD"], cwd=target_repo_dir, timeout=60)
    if push.returncode != 0:
        logger.warning(
            "Cannot push final changes for issue #%s in %s: %s",
            issue_number,
            target_repo_dir,
            push.stderr,
        )
        return False
    return True


def finalize_workflow(
    *,
    issue_num: str,
    repo: str,
    last_agent: str,
    project_name: str,
    logger,
    get_workflow_state_plugin,
    workflow_state_plugin_kwargs: dict,
    verify_workflow_terminal_before_finalize_fn,
    get_workflow_policy_plugin,
    resolve_git_dir,
    resolve_git_dirs,
    create_pr_from_changes_fn,
    resolve_repo_branch_fn,
    find_existing_pr_fn,
    find_existing_pr_details_fn=None,
    cleanup_worktree_fn,
    close_issue_fn,
    send_notification,
    enqueue_merge_queue_prs,
    archive_closed_task_files,
    project_config: dict,
    base_dir: str,
    get_tasks_active_dir,
    get_tasks_closed_dir,
    resolve_token_override_fn=None,
    sync_existing_pr_changes_fn=None,
    emit_notifications: bool = True,
) -> dict[str, Any]:
    blocked_result = {
        "pr_urls": [],
        "issue_closed": False,
        "notification_sent": False,
        "finalization_blocked": True,
        "blocking_reasons": [],
    }
    try:
        workflow_plugin = get_workflow_state_plugin(
            **workflow_state_plugin_kwargs,
            cache_key="workflow:state-engine",
        )
        if not verify_workflow_terminal_before_finalize_fn(
            workflow_plugin=workflow_plugin,
            issue_num=str(issue_num),
            project_name=project_name,
            alert_source="inbox_processor",
        ):
            blocked_result["blocking_reasons"] = ["Workflow is not terminal yet."]
            return blocked_result
    except Exception as exc:
        logger.warning(
            "Could not verify workflow state before finalize for issue #%s: %s",
            issue_num,
            exc,
        )

    workflow_policy = get_workflow_policy_plugin(
        resolve_git_dir=resolve_git_dir,
        resolve_git_dirs=resolve_git_dirs,
        create_pr_from_changes=create_pr_from_changes_fn,
        resolve_repo_branch=resolve_repo_branch_fn,
        find_existing_pr=find_existing_pr_fn,
        find_existing_pr_details=find_existing_pr_details_fn,
        cleanup_worktree=cleanup_worktree_fn,
        sync_existing_pr_changes=(
            sync_existing_pr_changes_fn if callable(sync_existing_pr_changes_fn) else None
        ),
        validate_pr_non_empty_diff=(
            lambda **kwargs: validate_pr_non_empty_diff(
                project_name=project_name,
                repo=str(kwargs.get("repo", "") or repo),
                issue_number=str(kwargs.get("issue_number", "") or issue_num),
                pr_url=str(kwargs.get("pr_url", "") or ""),
                repo_dir=kwargs.get("repo_dir"),
                base_branch=kwargs.get("base_branch"),
                issue_repo=kwargs.get("issue_repo"),
                token_override=(
                    resolve_token_override_fn(
                        project_name,
                        str(kwargs.get("repo", "") or repo),
                        str(kwargs.get("issue_number", "") or issue_num),
                    )
                    if callable(resolve_token_override_fn)
                    else None
                ),
            )
        ),
        close_issue=close_issue_fn,
        send_notification=send_notification,
        resolve_project_config=(
            lambda *, project_name=None, repo=None: (
                project_config.get(str(project_name or "").strip())
                if str(project_name or "").strip() in project_config
                else None
            )
        ),
        cache_key="workflow-policy:finalize",
    )

    result = workflow_policy.finalize_workflow(
        issue_number=str(issue_num),
        repo=repo,
        last_agent=last_agent,
        project_name=project_name,
        emit_notifications=emit_notifications,
    )
    if result.get("finalization_blocked"):
        reasons = [str(item) for item in (result.get("blocking_reasons") or []) if str(item).strip()]
        if reasons:
            logger.warning(
                "Finalization blocked for issue #%s: %s",
                issue_num,
                " | ".join(reasons),
            )
        else:
            logger.warning("Finalization blocked for issue #%s", issue_num)
        return result

    pr_urls = result.get("pr_urls") if isinstance(result, dict) else None
    if isinstance(pr_urls, list) and pr_urls:
        for pr_link in pr_urls:
            logger.info("🔀 Created/linked PR for issue #%s: %s", issue_num, pr_link)
        enqueue_merge_queue_prs(
            issue_num=str(issue_num),
            issue_repo=repo,
            project_name=project_name,
            pr_urls=[str(url) for url in pr_urls if str(url).strip()],
        )
    if result.get("issue_closed"):
        logger.info("🔒 Closed issue #%s", issue_num)
        archived = archive_closed_task_files(
            issue_num=str(issue_num),
            project_name=project_name,
            project_config=project_config,
            base_dir=base_dir,
            get_tasks_active_dir=get_tasks_active_dir,
            get_tasks_closed_dir=get_tasks_closed_dir,
            logger=logger,
        )
        if archived:
            logger.info("📦 Archived %s task file(s) for closed issue #%s", archived, issue_num)
    return result if isinstance(result, dict) else {
        "pr_urls": [],
        "issue_closed": False,
        "notification_sent": False,
        "finalization_blocked": False,
        "blocking_reasons": [],
    }
