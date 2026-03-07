"""Git platform adapter selection helpers."""

import os
from typing import Literal, overload

from nexus.adapters.git.base import GitPlatform
from nexus.adapters.git.github import GitHubPlatform as GitHubAPIPlatform
from nexus.adapters.git.github_cli import GitHubPlatform as GitHubCLIPlatform
from nexus.adapters.git.gitlab import GitLabPlatform as GitLabAPIPlatform
from nexus.adapters.git.gitlab_cli import GitLabCLIPlatform

VALID_GIT_TRANSPORTS = {"api", "cli"}
DEFAULT_GIT_TRANSPORT = "api"


def get_git_platform_transport() -> str:
    value = str(os.getenv("NEXUS_GIT_PLATFORM_TRANSPORT", DEFAULT_GIT_TRANSPORT)).strip().lower()
    if value in VALID_GIT_TRANSPORTS:
        return value
    return DEFAULT_GIT_TRANSPORT


@overload
def resolve_git_platform_class(
    platform_type: Literal["gitlab"],
) -> type[GitLabAPIPlatform] | type[GitLabCLIPlatform]: ...


@overload
def resolve_git_platform_class(
    platform_type: Literal["github"],
) -> type[GitHubAPIPlatform] | type[GitHubCLIPlatform]: ...


@overload
def resolve_git_platform_class(platform_type: str) -> type[GitPlatform]: ...


def resolve_git_platform_class(platform_type: str) -> type[GitPlatform]:
    platform = str(platform_type or "github").strip().lower()
    transport = get_git_platform_transport()
    if platform == "gitlab":
        return GitLabCLIPlatform if transport == "cli" else GitLabAPIPlatform
    return GitHubCLIPlatform if transport == "cli" else GitHubAPIPlatform
