"""Requester-scoped execution environment resolution across auth backends."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from nexus.adapters.git.utils import build_issue_url
from nexus.core.auth.access_domain import auth_enabled, build_execution_env
from nexus.core.auth.credential_store import get_issue_requester, get_issue_requester_by_url
from nexus.core.config import (
    get_project_platform,
)

logger = logging.getLogger(__name__)


def requester_scoped_execution_enabled() -> bool:
    """Return whether execution should resolve requester-scoped credentials."""
    return bool(auth_enabled())


def resolve_issue_requester_nexus_id(
    *,
    repo_name: str,
    issue_number: str,
    project_name: str | None = None,
    issue_url: str | None = None,
) -> str | None:
    normalized_repo = str(repo_name or "").strip()
    normalized_issue = str(issue_number or "").strip()
    if not normalized_repo or not normalized_issue:
        return None

    try:
        resolved = str(get_issue_requester(normalized_repo, normalized_issue) or "").strip()
    except Exception:
        resolved = ""
    if resolved:
        return resolved

    candidate_urls: list[str] = []
    explicit_issue_url = str(issue_url or "").strip()
    if explicit_issue_url:
        candidate_urls.append(explicit_issue_url)

    project_key = str(project_name or "").strip()
    if project_key:
        try:
            platform = str(get_project_platform(project_key) or "github").strip().lower()
        except Exception:
            platform = "github"
        candidate_urls.append(
            build_issue_url(
                normalized_repo,
                normalized_issue,
                {"git_platform": platform},
            )
        )

    candidate_urls.append(
        build_issue_url(
            normalized_repo,
            normalized_issue,
            {"git_platform": "github"},
        )
    )
    candidate_urls.append(
        build_issue_url(
            normalized_repo,
            normalized_issue,
            {"git_platform": "gitlab", "gitlab_base_url": "https://gitlab.com"},
        )
    )

    seen: set[str] = set()
    for candidate in candidate_urls:
        url = str(candidate or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        try:
            resolved = str(get_issue_requester_by_url(url) or "").strip()
        except Exception:
            resolved = ""
        if resolved:
            return resolved
    return None


def select_git_token(
    env: Mapping[str, Any] | None,
    *,
    project_name: str | None = None,
    platform_name: str | None = None,
) -> str | None:
    payload = env if isinstance(env, Mapping) else {}
    platform = str(platform_name or "").strip().lower()
    if not platform and project_name:
        try:
            platform = str(get_project_platform(str(project_name)) or "github").strip().lower()
        except Exception:
            platform = "github"
    if platform == "gitlab":
        preferred_keys = ("GITLAB_TOKEN", "GLAB_TOKEN", "GITHUB_TOKEN", "GH_TOKEN")
    else:
        preferred_keys = ("GITHUB_TOKEN", "GH_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN")
    for key in preferred_keys:
        token = str(payload.get(key) or "").strip()
        if token:
            return token
    return None


def resolve_execution_env(
    requester_nexus_id: str,
    *,
    project_name: str | None = None,
    repo_name: str | None = None,
    issue_url: str | None = None,
    purpose: str = "agent_run",
) -> tuple[dict[str, str], str | None]:
    normalized_requester = str(requester_nexus_id or "").strip()
    if not normalized_requester:
        return {}, "Requester Nexus ID is required."

    return build_execution_env(normalized_requester, purpose=purpose)


def resolve_requester_git_token_for_issue(
    *,
    repo_name: str,
    issue_number: str,
    project_name: str | None = None,
    issue_url: str | None = None,
    purpose: str = "git",
) -> str | None:
    if not requester_scoped_execution_enabled():
        return None

    requester_nexus_id = resolve_issue_requester_nexus_id(
        repo_name=repo_name,
        issue_number=issue_number,
        project_name=project_name,
        issue_url=issue_url,
    )
    if not requester_nexus_id:
        logger.warning(
            "Requester binding missing for issue #%s repo=%s project=%s; refusing service-token fallback.",
            issue_number,
            repo_name,
            project_name,
        )
        return None

    user_env, env_error = resolve_execution_env(
        requester_nexus_id,
        project_name=project_name,
        repo_name=repo_name,
        issue_url=issue_url,
        purpose=purpose,
    )
    if env_error:
        logger.warning(
            "Requester token unavailable for issue #%s repo=%s project=%s requester=%s: %s",
            issue_number,
            repo_name,
            project_name,
            requester_nexus_id,
            env_error,
        )
        return None

    token = select_git_token(user_env, project_name=project_name)
    if token:
        return token

    logger.warning(
        "Requester token missing for issue #%s repo=%s project=%s requester=%s after credential resolution.",
        issue_number,
        repo_name,
        project_name,
        requester_nexus_id,
    )
    return None


