import glob
import logging
import os
import re

from nexus.core.config import get_nexus_dir_name, BASE_DIR
from nexus.core.storage.capabilities import get_storage_capabilities

logger = logging.getLogger(__name__)


def find_task_file_by_issue(issue_num: str) -> str | None:
    """Search for a task file that references the issue number."""
    if not get_storage_capabilities().local_task_files:
        raise RuntimeError("find_task_file_by_issue is disabled in postgres mode")
    nexus_dir_name = get_nexus_dir_name()
    patterns = [
        os.path.join(BASE_DIR, "**", nexus_dir_name, "tasks", "*", "active", "*.md"),
        os.path.join(BASE_DIR, "**", nexus_dir_name, "inbox", "*", "*.md"),
    ]
    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            try:
                with open(path, encoding="utf-8") as f:
                    content = f.read()
                # Match both full URLs and short references
                if re.search(
                    r"\*\*Issue:\*\*\s*https?://[^/]+/.+/(?:issues|/-/issues)/" + re.escape(issue_num),
                    content,
                ) or re.search(
                    r"\*\*Issue:\*\*\s*#" + re.escape(issue_num) + r"(?!\d)",
                    content,
                ):
                    return path
            except Exception:
                continue
    return None
