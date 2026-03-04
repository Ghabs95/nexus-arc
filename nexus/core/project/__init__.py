"""Project-level helpers and repo-resolution utilities."""

from nexus.core.project.catalog import (
    get_project_label,
    get_project_workspace,
    get_single_project_key,
    iter_project_keys,
    single_key,
)
from nexus.core.project.issue_command_deps import (
    default_issue_url,
    get_issue_details,
    project_issue_url,
    project_repo,
)
from nexus.core.project.repo_resolution import resolve_repo_for_issue
from nexus.core.project.repo_utils import iter_project_configs, project_repos_from_config
from nexus.core.project.registry import (
    get_project_aliases,
    get_project_registry,
    normalize_project_key,
)

__all__ = [
    "default_issue_url",
    "get_issue_details",
    "get_project_label",
    "get_project_aliases",
    "get_project_registry",
    "get_project_workspace",
    "get_single_project_key",
    "iter_project_configs",
    "iter_project_keys",
    "normalize_project_key",
    "project_issue_url",
    "project_repo",
    "project_repos_from_config",
    "resolve_repo_for_issue",
    "single_key",
]
