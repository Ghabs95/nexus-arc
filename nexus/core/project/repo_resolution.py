from __future__ import annotations

import asyncio
import os
import re
from typing import Any, Callable, Iterable


def resolve_repo_for_issue(
    *,
    issue_num: str,
    default_project: str | None,
    project_config: dict[str, Any],
    get_default_project: Callable[[], str],
    get_repo: Callable[[str | None], str],
    iter_project_configs: Callable[
        [dict[str, Any], Callable[[str], list[str]]], Iterable[tuple[str, Any]]
    ],
    project_repos_from_config: Callable[
        [str, dict[str, Any], Callable[[str], list[str]]], list[str]
    ],
    get_repos: Callable[[str], list[str]],
    get_git_platform: Callable[..., Any],
    extract_repo_from_issue_url: Callable[[str], str],
    base_dir: str,
) -> str:
    """Resolve the repository that owns an issue across all configured project repos."""
    default_repo = get_repo(default_project) if default_project else get_repo(get_default_project())

    repo_candidates: list[str] = []
    if default_project and default_project in project_config:
        repo_candidates.extend(
            project_repos_from_config(default_project, project_config[default_project], get_repos)
        )
    if default_repo and default_repo not in repo_candidates:
        repo_candidates.append(default_repo)

    for project_key, cfg in iter_project_configs(project_config, get_repos):
        for repo_name in project_repos_from_config(project_key, cfg, get_repos):
            if repo_name not in repo_candidates:
                repo_candidates.append(repo_name)

    for repo_name in repo_candidates:
        matched_project = _find_project_for_repo(
            repo_name,
            project_config=project_config,
            iter_project_configs=iter_project_configs,
            project_repos_from_config=project_repos_from_config,
            get_repos=get_repos,
        ) or (default_project or get_default_project())

        try:
            issue = asyncio.run(
                get_git_platform(repo_name, project_name=matched_project).get_issue(str(issue_num))
            )
        except Exception:
            continue
        if not issue:
            continue

        issue_url = str(getattr(issue, "url", "") or "")
        url_repo = extract_repo_from_issue_url(issue_url)
        if url_repo:
            return url_repo

        body = str(getattr(issue, "body", "") or "")
        if _task_file_matches_repo_from_body(
            body=body,
            repo_name=repo_name,
            project_config=project_config,
            iter_project_configs=iter_project_configs,
            project_repos_from_config=project_repos_from_config,
            get_repos=get_repos,
            base_dir=base_dir,
        ):
            return repo_name

        return repo_name

    return default_repo


def _find_project_for_repo(
    repo_name: str,
    *,
    project_config: dict[str, Any],
    iter_project_configs: Callable[
        [dict[str, Any], Callable[[str], list[str]]], Iterable[tuple[str, Any]]
    ],
    project_repos_from_config: Callable[
        [str, dict[str, Any], Callable[[str], list[str]]], list[str]
    ],
    get_repos: Callable[[str], list[str]],
) -> str | None:
    for project_key, cfg in iter_project_configs(project_config, get_repos):
        if repo_name in project_repos_from_config(project_key, cfg, get_repos):
            return project_key
    return None


def _task_file_matches_repo_from_body(
    *,
    body: str,
    repo_name: str,
    project_config: dict[str, Any],
    iter_project_configs: Callable[
        [dict[str, Any], Callable[[str], list[str]]], Iterable[tuple[str, Any]]
    ],
    project_repos_from_config: Callable[
        [str, dict[str, Any], Callable[[str], list[str]]], list[str]
    ],
    get_repos: Callable[[str], list[str]],
    base_dir: str,
) -> bool:
    task_file_match = re.search(r"\*\*Task File:\*\*\s*`([^`]+)`", body)
    if not task_file_match:
        return False
    task_file = task_file_match.group(1)
    for project_key, cfg in iter_project_configs(project_config, get_repos):
        workspace = cfg.get("workspace")
        if not workspace:
            continue
        workspace_abs = os.path.join(base_dir, str(workspace))
        if task_file.startswith(workspace_abs):
            project_repos = project_repos_from_config(project_key, cfg, get_repos)
            if repo_name in project_repos:
                return True
    return False
