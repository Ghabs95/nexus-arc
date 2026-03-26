"""Helpers for provider-aware repository and issue URL handling."""

from __future__ import annotations

import os
from typing import Any


def resolve_automation_token_for_platform(platform: str | None = None) -> str | None:
    """Return the best automation token for the given git platform using env vars.

    Preference order:
    1. Platform-specific automation token:
       - GitHub: NEXUS_AUTOMATION_GITHUB_TOKEN, NEXUS_GITHUB_WRITE_TOKEN, GITHUB_TOKEN, GH_TOKEN
       - GitLab: NEXUS_AUTOMATION_GITLAB_TOKEN, GITLAB_TOKEN, GLAB_TOKEN
    2. Generic automation token (any platform): NEXUS_AUTOMATION_GIT_TOKEN
    3. If platform is unknown/None: all of the above are tried in order.

    For a known platform, only tokens appropriate for that platform are checked.
    """
    norm_platform = str(platform or "").strip().lower()

    if norm_platform == "github":
        for key in ("NEXUS_AUTOMATION_GITHUB_TOKEN", "NEXUS_GITHUB_WRITE_TOKEN", "GITHUB_TOKEN", "GH_TOKEN"):
            token = str(os.getenv(key, "")).strip()
            if token:
                return token
    elif norm_platform == "gitlab":
        for key in ("NEXUS_AUTOMATION_GITLAB_TOKEN", "GITLAB_TOKEN", "GLAB_TOKEN"):
            token = str(os.getenv(key, "")).strip()
            if token:
                return token
    else:
        # Unknown/None platform — try all platform-specific tokens first
        for key in (
            "NEXUS_AUTOMATION_GITHUB_TOKEN",
            "NEXUS_AUTOMATION_GITLAB_TOKEN",
            "NEXUS_GITHUB_WRITE_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
            "GITLAB_TOKEN",
            "GLAB_TOKEN",
        ):
            token = str(os.getenv(key, "")).strip()
            if token:
                return token

    # Generic token is the final fallback for any known or unknown platform
    return str(os.getenv("NEXUS_AUTOMATION_GIT_TOKEN", "")).strip() or None


def resolve_repo(config: dict[str, Any] | None, default_repo: str) -> str:
    """Resolve repository slug from project config with legacy fallback."""
    if not isinstance(config, dict):
        return default_repo

    repo = config.get("git_repo")
    if isinstance(repo, str) and repo.strip():
        return repo.strip()
    return default_repo


def build_issue_url(repo: str, issue_num: str, config: dict[str, Any] | None) -> str:
    """Build issue URL for configured git platform (GitHub/GitLab)."""
    if not isinstance(config, dict):
        return f"https://github.com/{repo}/issues/{issue_num}"

    platform = str(config.get("git_platform") or "").lower().strip()
    if not platform:
        platform = "gitlab" if str(config.get("gitlab_base_url") or "").strip() else "github"
    if platform == "gitlab":
        base_url = str(config.get("gitlab_base_url", "https://gitlab.com")).rstrip("/")
        return f"{base_url}/{repo}/-/issues/{issue_num}"

    return f"https://github.com/{repo}/issues/{issue_num}"
