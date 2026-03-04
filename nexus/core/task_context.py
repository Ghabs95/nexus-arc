"""Task file context resolution helpers."""

import os
import re
from collections.abc import Callable
from typing import Any


def load_task_context(
    *,
    filepath: str,
    project_config: dict[str, Any],
    base_dir: str,
    get_nexus_dir_name: Callable[[], str],
    iter_project_configs,
    get_repos,
) -> dict[str, Any] | None:
    """Resolve task file content type and project context from file path."""
    with open(filepath) as f:
        content = f.read()

    type_match = re.search(r"\*\*Type:\*\*\s*(.+)", content)
    task_type = type_match.group(1).strip().lower() if type_match else "feature"

    nexus_dir_name = get_nexus_dir_name()
    marker = f"{os.sep}{nexus_dir_name}{os.sep}inbox{os.sep}"
    project_name = None
    config = None
    project_root = None

    if marker in filepath:
        prefix, suffix = filepath.split(marker, 1)
        project_name = suffix.split(os.sep, 1)[0] if suffix else None
        project_root = prefix

    if project_name and project_name in project_config:
        cfg = project_config.get(project_name)
        if isinstance(cfg, dict):
            config = cfg

    if not config:
        for key, cfg in iter_project_configs(project_config, get_repos):
            workspace = cfg.get("workspace")
            if not workspace:
                continue
            workspace_abs = os.path.join(base_dir, workspace)
            if filepath.startswith(workspace_abs + os.sep):
                project_name = key
                config = cfg
                project_root = workspace_abs
                break

    if not config:
        return None

    requester_nexus_id = ""
    requester_platform = ""
    requester_platform_user_id = ""
    requester_nexus_match = re.search(r"\*\*Requester Nexus ID:\*\*\s*`?([^`\n]+)`?", content)
    requester_platform_match = re.search(r"\*\*Requester Platform:\*\*\s*([^\n]+)", content)
    requester_platform_user_match = re.search(
        r"\*\*Requester Platform User ID:\*\*\s*`?([^`\n]+)`?",
        content,
    )
    if requester_nexus_match:
        requester_nexus_id = str(requester_nexus_match.group(1) or "").strip()
    if requester_platform_match:
        requester_platform = str(requester_platform_match.group(1) or "").strip().lower()
    if requester_platform_user_match:
        requester_platform_user_id = str(requester_platform_user_match.group(1) or "").strip()

    return {
        "content": content,
        "task_type": task_type,
        "project_name": project_name,
        "project_root": project_root,
        "config": config,
        "requester_nexus_id": requester_nexus_id or None,
        "requester_platform": requester_platform or None,
        "requester_platform_user_id": requester_platform_user_id or None,
    }
