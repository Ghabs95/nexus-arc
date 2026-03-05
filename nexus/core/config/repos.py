import os
import subprocess
import urllib.parse
from typing import Callable


def repo_slug_from_remote_url(remote_url: str) -> str:
    """Normalize git remote URL into ``namespace/repo`` slug."""
    if not remote_url:
        return ""

    value = remote_url.strip()
    if value.startswith("git@") and ":" in value:
        value = value.split(":", 1)[1]
    elif "://" in value:
        try:
            parsed = urllib.parse.urlparse(value)
            value = parsed.path or ""
        except Exception:
            return ""

    value = value.strip().lstrip("/")
    if value.endswith(".git"):
        value = value[:-4]
    return value


def discover_workspace_repos(project_cfg: dict, base_dir: str) -> list[str]:
    """Discover repository slugs from local git remotes in workspace."""
    workspace = project_cfg.get("workspace") if isinstance(project_cfg, dict) else None
    if not workspace:
        return []

    workspace_abs = workspace if os.path.isabs(workspace) else os.path.join(base_dir, workspace)
    if not os.path.isdir(workspace_abs):
        return []

    candidates = [workspace_abs]
    try:
        for entry in os.scandir(workspace_abs):
            if entry.is_dir(follow_symlinks=False):
                candidates.append(entry.path)
    except Exception:
        pass

    repos: list[str] = []
    for candidate in candidates:
        if not os.path.isdir(os.path.join(candidate, ".git")):
            continue
        try:
            result = subprocess.run(
                ["git", "-C", candidate, "remote", "get-url", "origin"],
                capture_output=True,
                text=True,
                timeout=5,
                check=False,
            )
        except Exception:
            continue

        if result.returncode != 0:
            continue

        slug = repo_slug_from_remote_url(result.stdout.strip())
        if slug and slug not in repos:
            repos.append(slug)

    return repos


def get_repos(
    get_project_config: Callable[[], dict],
    base_dir: str,
    project: str,
) -> list[str]:
    """Get all git repositories configured for a project."""
    config = get_project_config()
    if project not in config:
        raise KeyError(
            f"Project '{project}' not found in PROJECT_CONFIG. "
            f"Available projects: {[k for k in config if isinstance(config.get(k), dict)]}"
        )

    project_cfg = config[project]
    if not isinstance(project_cfg, dict):
        raise ValueError(f"Project '{project}' configuration must be a mapping")

    repos: list[str] = []
    single_repo = project_cfg.get("git_repo")
    if isinstance(single_repo, str) and single_repo.strip():
        repos.append(single_repo.strip())

    repo_list = project_cfg.get("git_repos")
    if isinstance(repo_list, list):
        for repo_name in repo_list:
            if isinstance(repo_name, str):
                value = repo_name.strip()
                if value and value not in repos:
                    repos.append(value)

    if not repos:
        repos = discover_workspace_repos(project_cfg, base_dir)

    if not repos:
        raise ValueError(
            f"Project '{project}' is missing repository configuration and "
            "workspace auto-discovery found no git remotes"
        )

    return repos


def get_project_platform(get_project_config: Callable[[], dict], project: str) -> str:
    """Return VCS platform type for a project (``github`` or ``gitlab``)."""
    config = get_project_config()
    if project not in config or not isinstance(config[project], dict):
        raise KeyError(f"Project '{project}' not found in PROJECT_CONFIG")
    return str(config[project].get("git_platform", "github")).lower().strip()


def get_gitlab_base_url(get_project_config: Callable[[], dict], project: str) -> str:
    """Return GitLab base URL for a project."""
    config = get_project_config()
    project_cfg = config.get(project, {}) if isinstance(config, dict) else {}
    if isinstance(project_cfg, dict):
        project_url = project_cfg.get("gitlab_base_url")
        if isinstance(project_url, str) and project_url.strip():
            return project_url.strip()
    return os.getenv("GITLAB_BASE_URL", "https://gitlab.com")


def get_repo(get_repos_fn: Callable[[str], list[str]], project: str) -> str:
    """Return default repo for a project from configured repo list."""
    return get_repos_fn(project)[0]


def get_default_repo(
    get_repo_fn: Callable[[str], str], get_default_project_fn: Callable[[], str]
) -> str:
    """Return default repo for the default project."""
    return get_repo_fn(get_default_project_fn())
