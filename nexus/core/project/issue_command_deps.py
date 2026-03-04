from __future__ import annotations

from typing import Any


def project_repo(*, project_key: str, project_config: dict[str, Any], default_repo: str, resolve_repo):
    config = project_config.get(project_key)
    normalized_cfg = config if isinstance(config, dict) else None
    return resolve_repo(normalized_cfg, default_repo)


def project_issue_url(
    *,
    project_key: str,
    issue_num: str,
    project_config: dict[str, Any],
    default_repo: str,
    resolve_repo,
    build_issue_url,
):
    config = project_config.get(project_key)
    normalized_cfg = config if isinstance(config, dict) else None
    repo = project_repo(
        project_key=project_key,
        project_config=project_config,
        default_repo=default_repo,
        resolve_repo=resolve_repo,
    )
    return build_issue_url(repo, issue_num, normalized_cfg)


def default_issue_url(*, issue_num: str, default_repo: str, get_default_project, project_issue_url_fn):
    try:
        project_key = get_default_project()
        return project_issue_url_fn(project_key, issue_num)
    except Exception:
        return f"https://github.com/{default_repo}/issues/{issue_num}"


def get_issue_details(*, issue_num: str, repo: str | None, default_repo: str, get_direct_issue_plugin, logger):
    try:
        resolved_repo = repo or default_repo
        plugin = get_direct_issue_plugin(resolved_repo)
        if not plugin:
            return None
        return plugin.get_issue(
            str(issue_num),
            ["number", "title", "state", "labels", "body", "updatedAt"],
        )
    except Exception as exc:
        logger.error("Failed to fetch issue %s: %s", issue_num, exc)
        return None
