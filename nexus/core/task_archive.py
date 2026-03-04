"""Task archive helpers extracted from inbox_processor."""

import os
import re
import time
from typing import Any


def archive_closed_task_files(
    *,
    issue_num: str,
    project_name: str,
    project_config: dict[str, Any],
    base_dir: str,
    get_tasks_active_dir,
    get_tasks_closed_dir,
    logger,
) -> int:
    """Archive active task files for a closed issue into tasks/closed."""
    projects_to_scan = []
    if project_name and project_name in project_config:
        projects_to_scan.append(project_name)

    projects_to_scan.extend(
        key
        for key in project_config
        if key
        not in {
            "workflow_definition_path",
            "shared_agents_dir",
            "nexus_dir",
            "merge_queue",
            "ai_tool_preferences",
            "system_operations",
        }
        and key not in projects_to_scan
    )

    archived_count = 0
    issue_pattern = re.compile(r"\*\*Issue:\*\*\s*https?://[^/]+/.+/(?:issues|/-/issues)/(\d+)")

    for project_key in projects_to_scan:
        project_cfg = project_config.get(project_key, {})
        if not isinstance(project_cfg, dict):
            continue

        workspace_rel = project_cfg.get("workspace")
        if not workspace_rel:
            continue

        project_root = os.path.join(base_dir, workspace_rel)
        active_dir = get_tasks_active_dir(project_root, project_key)
        if not os.path.isdir(active_dir):
            continue

        closed_dir = get_tasks_closed_dir(project_root, project_key)

        for filename in os.listdir(active_dir):
            if not filename.endswith(".md"):
                continue

            source_path = os.path.join(active_dir, filename)
            matched = False

            if filename == f"issue_{issue_num}.md":
                matched = True
            else:
                try:
                    with open(source_path) as f:
                        content = f.read()
                    match = issue_pattern.search(content)
                    matched = bool(match and match.group(1) == str(issue_num))
                except Exception as exc:
                    logger.warning("Could not inspect active task file %s: %s", source_path, exc)
                    continue

            if not matched:
                continue

            target_path = os.path.join(closed_dir, filename)
            if os.path.exists(target_path):
                stem, ext = os.path.splitext(filename)
                target_path = os.path.join(closed_dir, f"{stem}_{int(time.time())}{ext}")

            try:
                os.makedirs(closed_dir, exist_ok=True)
                os.replace(source_path, target_path)
                archived_count += 1
            except Exception as exc:
                logger.warning("Failed to archive task file %s: %s", source_path, exc)

    return archived_count
