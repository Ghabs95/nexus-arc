"""Shared helper for resolving the best automation git token for a platform."""

from __future__ import annotations

import os


def resolve_automation_git_token(platform: str | None = None) -> str | None:
    """Return the best automation token for the given git platform.

    Preference order:
    - Platform-specific automation token (NEXUS_AUTOMATION_GITHUB_TOKEN / NEXUS_AUTOMATION_GITLAB_TOKEN)
    - Legacy generic automation token (NEXUS_AUTOMATION_GIT_TOKEN)
    - Platform-specific write/service tokens

    When a specific platform is supplied ("github" or "gitlab"), only that
    platform's env-var keys are consulted so that a GitLab token cannot
    accidentally be returned for a GitHub repo (and vice versa).

    When platform is ``None`` / empty string / unrecognised, the function
    tries GitHub keys first, then GitLab keys (legacy behaviour for
    single-platform environments).
    """
    norm_platform = str(platform or "").strip().lower()

    if norm_platform == "github":
        for key in (
            "NEXUS_AUTOMATION_GITHUB_TOKEN",
            "NEXUS_AUTOMATION_GIT_TOKEN",
            "NEXUS_GITHUB_WRITE_TOKEN",
            "GITHUB_TOKEN",
            "GH_TOKEN",
        ):
            token = str(os.getenv(key, "")).strip()
            if token:
                return token
        return None

    if norm_platform == "gitlab":
        for key in (
            "NEXUS_AUTOMATION_GITLAB_TOKEN",
            "NEXUS_AUTOMATION_GIT_TOKEN",
            "GITLAB_TOKEN",
            "GLAB_TOKEN",
        ):
            token = str(os.getenv(key, "")).strip()
            if token:
                return token
        return None

    # Unknown / unset platform — try GitHub first, then GitLab (legacy fallback)
    for key in (
        "NEXUS_AUTOMATION_GITHUB_TOKEN",
        "NEXUS_AUTOMATION_GIT_TOKEN",
        "NEXUS_GITHUB_WRITE_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "NEXUS_AUTOMATION_GITLAB_TOKEN",
        "GITLAB_TOKEN",
        "GLAB_TOKEN",
    ):
        token = str(os.getenv(key, "")).strip()
        if token:
            return token
    return None
