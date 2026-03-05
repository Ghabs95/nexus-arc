import os
from typing import Callable


def get_nexus_dir_name(get_project_config: Callable[[], dict]) -> str:
    """Return configured nexus directory name (defaults to .nexus)."""
    config = get_project_config()
    return str(config.get("nexus_dir", ".nexus"))


def get_nexus_dir(get_project_config: Callable[[], dict], workspace: str | None = None) -> str:
    """Return Nexus directory path under a workspace."""
    target_workspace = workspace if workspace is not None else os.getcwd()
    return os.path.join(target_workspace, get_nexus_dir_name(get_project_config))


def get_inbox_dir(
    get_project_config: Callable[[], dict],
    workspace: str | None = None,
    project: str | None = None,
) -> str:
    """Return inbox directory path, optionally scoped to a project."""
    inbox_dir = os.path.join(get_nexus_dir(get_project_config, workspace), "inbox")
    if project:
        inbox_dir = os.path.join(inbox_dir, project)
    return inbox_dir


def get_tasks_active_dir(
    get_project_config: Callable[[], dict], workspace: str, project: str
) -> str:
    return os.path.join(get_nexus_dir(get_project_config, workspace), "tasks", project, "active")


def get_tasks_closed_dir(
    get_project_config: Callable[[], dict], workspace: str, project: str
) -> str:
    return os.path.join(get_nexus_dir(get_project_config, workspace), "tasks", project, "closed")


def get_tasks_logs_dir(get_project_config: Callable[[], dict], workspace: str, project: str) -> str:
    return os.path.join(get_nexus_dir(get_project_config, workspace), "tasks", project, "logs")
