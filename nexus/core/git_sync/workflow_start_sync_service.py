"""Workflow-start git sync helpers (worktree-safe fetch)."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable
from typing import Any


def _is_network_or_auth_failure(stderr: str, stdout: str = "") -> bool:
    text = f"{stderr or ''}\n{stdout or ''}".lower()
    markers = (
        "could not resolve host",
        "failed to connect",
        "connection timed out",
        "timed out",
        "network is unreachable",
        "connection reset",
        "proxy error",
        "unable to access",
        "authentication failed",
        "permission denied",
        "could not read from remote repository",
        "repository not found",
        "access denied",
        "http 401",
        "http 403",
        "invalid username or password",
        "could not read username",
        "unable to update url base",
        "could not resolve proxy",
    )
    return any(marker in text for marker in markers)


def _wait_for_block_decision(
    *,
    issue_number: str,
    project_name: str,
    timeout_seconds: int,
    should_block_launch: Callable[[str, str], bool] | None,
    sleep_fn: Callable[[float], None],
) -> bool:
    if not callable(should_block_launch):
        return False

    deadline = time.time() + max(1, int(timeout_seconds))
    while time.time() < deadline:
        try:
            if should_block_launch(str(issue_number), str(project_name)):
                return True
        except Exception:
            return False
        sleep_fn(1.0)
    return False


def sync_project_repos_on_workflow_start(
    *,
    issue_number: str,
    project_name: str,
    project_cfg: dict[str, Any],
    resolve_git_dirs: Callable[[str], dict[str, str]],
    resolve_git_dir: Callable[[str], str | None],
    get_repos: Callable[[str], list[str]],
    get_repo_branch: Callable[[str, str], str],
    emit_alert: Callable[..., Any] | None = None,
    logger: Any | None = None,
    should_block_launch: Callable[[str, str], bool] | None = None,
    sleep_fn: Callable[[float], None] = time.sleep,
) -> dict[str, Any]:
    """Sync configured project repos with worktree-safe fetch before initial launch."""
    cfg = project_cfg if isinstance(project_cfg, dict) else {}
    git_sync = cfg.get("git_sync") if isinstance(cfg.get("git_sync"), dict) else {}

    enabled = bool(git_sync.get("on_workflow_start", False))
    if not enabled:
        return {"enabled": False, "skipped": True, "reason": "disabled"}

    retries = int(git_sync.get("network_auth_retries", 3) or 3)
    backoff_seconds = int(git_sync.get("retry_backoff_seconds", 5) or 5)
    decision_timeout_seconds = int(git_sync.get("decision_timeout_seconds", 120) or 120)

    resolved_dirs: dict[str, str] = {}
    try:
        resolved = resolve_git_dirs(project_name)
        if isinstance(resolved, dict):
            resolved_dirs.update(
                {
                    str(repo_slug): str(path)
                    for repo_slug, path in resolved.items()
                    if str(repo_slug).strip() and str(path).strip()
                }
            )
    except Exception:
        resolved_dirs = {}

    if not resolved_dirs:
        fallback_dir = resolve_git_dir(project_name)
        if fallback_dir:
            try:
                repo_names = get_repos(project_name)
            except Exception:
                repo_names = []
            if repo_names:
                for repo_name in repo_names:
                    key = str(repo_name).strip()
                    if key:
                        resolved_dirs[key] = str(fallback_dir)
            else:
                primary_repo = str(cfg.get("git_repo") or "").strip()
                if primary_repo:
                    resolved_dirs[primary_repo] = str(fallback_dir)

    if not resolved_dirs:
        return {"enabled": True, "skipped": True, "reason": "no_git_dirs"}

    synced: list[dict[str, str]] = []
    failures: list[dict[str, str]] = []
    blocked = False

    for repo_slug, repo_dir in resolved_dirs.items():
        branch = str(get_repo_branch(project_name, repo_slug) or "main").strip() or "main"
        max_attempts = max(1, retries + 1)

        for attempt in range(1, max_attempts + 1):
            result = subprocess.run(
                [
                    "git",
                    "fetch",
                    "--prune",
                    "origin",
                    f"{branch}:refs/remotes/origin/{branch}",
                ],
                cwd=repo_dir,
                capture_output=True,
                text=True,
                timeout=60,
                check=False,
            )
            if result.returncode == 0:
                synced.append({"repo": repo_slug, "branch": branch, "dir": repo_dir})
                break

            stderr = str(result.stderr or "").strip()
            stdout = str(result.stdout or "").strip()
            network_or_auth = _is_network_or_auth_failure(stderr, stdout)
            if network_or_auth and attempt < max_attempts:
                if logger:
                    logger.warning(
                        "Workflow-start git sync retry %s/%s for %s on %s: %s",
                        attempt,
                        max_attempts,
                        repo_slug,
                        branch,
                        stderr or stdout or "unknown error",
                    )
                sleep_fn(float(max(1, backoff_seconds)))
                continue

            error_msg = stderr or stdout or f"git fetch failed (code={result.returncode})"
            failures.append(
                {
                    "repo": repo_slug,
                    "branch": branch,
                    "dir": repo_dir,
                    "error": error_msg,
                    "kind": "network_auth" if network_or_auth else "other",
                }
            )

            if not network_or_auth:
                if logger:
                    logger.warning(
                        "Workflow-start git sync warning for %s on %s: %s",
                        repo_slug,
                        branch,
                        error_msg,
                    )
                break

            if logger:
                logger.warning(
                    "Workflow-start git sync exhausted retries for %s on %s: %s",
                    repo_slug,
                    branch,
                    error_msg,
                )

            if callable(emit_alert):
                emit_alert(
                    (
                        "⚠️ Workflow-start git sync failed after retries.\n"
                        f"Issue: #{issue_number}\n"
                        f"Project: {project_name}\n"
                        f"Repo: {repo_slug}\n"
                        f"Branch: {branch}\n"
                        f"Error: {error_msg}\n\n"
                        "Choose whether to block launch now. "
                        "If no action is taken, launch continues automatically."
                    ),
                    severity="warning",
                    source="workflow_start_git_sync",
                    issue_number=str(issue_number),
                    project_key=str(project_name),
                    actions=[
                        {
                            "label": "🛑 Block Launch",
                            "callback_data": f"stop_{issue_number}|{project_name}",
                        }
                    ],
                )

            blocked = _wait_for_block_decision(
                issue_number=str(issue_number),
                project_name=str(project_name),
                timeout_seconds=max(1, decision_timeout_seconds),
                should_block_launch=should_block_launch,
                sleep_fn=sleep_fn,
            )
            break

        if blocked:
            break

    return {
        "enabled": True,
        "skipped": False,
        "blocked": blocked,
        "synced": synced,
        "failures": failures,
    }

