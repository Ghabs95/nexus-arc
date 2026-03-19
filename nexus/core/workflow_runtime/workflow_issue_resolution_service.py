"""Helpers for manual workflow issue lookup across configured project repos."""

from __future__ import annotations

from typing import Any


def configured_repos(project_cfg: dict[str, Any] | None) -> list[str]:
    """Return explicitly configured repos for a project config payload."""
    if not isinstance(project_cfg, dict):
        return []

    repos: list[str] = []

    single_repo = str(project_cfg.get("git_repo") or "").strip()
    if single_repo:
        repos.append(single_repo)

    repo_list = project_cfg.get("git_repos")
    if isinstance(repo_list, list):
        for repo_name in repo_list:
            value = str(repo_name or "").strip()
            if value and value not in repos:
                repos.append(value)

    return repos


def candidate_repos_for_issue_lookup(
    *,
    project_key: str,
    project_config: dict[str, dict[str, Any]],
    default_repo: str,
    preferred_config: dict[str, Any] | None = None,
) -> list[str]:
    """Return repo candidates ordered for manual issue lookup."""
    candidates: list[str] = []

    def _add_repo(value: str | None) -> None:
        repo_value = str(value or "").strip()
        if repo_value and repo_value not in candidates:
            candidates.append(repo_value)

    def _add_config(cfg: dict[str, Any] | None) -> None:
        for repo_name in configured_repos(cfg):
            _add_repo(repo_name)

    _add_config(preferred_config)

    project_cfg = project_config.get(project_key)
    normalized_project_cfg = project_cfg if isinstance(project_cfg, dict) else None
    if normalized_project_cfg is not preferred_config:
        _add_config(normalized_project_cfg)

    for other_key, other_cfg in project_config.items():
        if other_key == project_key or not isinstance(other_cfg, dict):
            continue
        if other_cfg is preferred_config or other_cfg is normalized_project_cfg:
            continue
        _add_config(other_cfg)

    if not candidates:
        _add_repo(default_repo)

    return candidates


def resolve_project_config_for_repo(
    *,
    repo: str | None,
    requested_project_key: str,
    project_config: dict[str, dict[str, Any]],
) -> tuple[str | None, dict[str, Any] | None]:
    """Resolve the configured project and config for a repository slug."""
    normalized_repo = str(repo or "").strip()
    if normalized_repo:
        for project_name, cfg in project_config.items():
            if not isinstance(cfg, dict):
                continue
            if normalized_repo in configured_repos(cfg):
                return str(project_name), cfg

    fallback_cfg = project_config.get(requested_project_key)
    if isinstance(fallback_cfg, dict):
        return requested_project_key, fallback_cfg
    return None, None
