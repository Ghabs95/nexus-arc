import asyncio
import glob
import json
import os

from nexus.core.completion import budget_completion_payload


def read_latest_local_completion(
    *,
    issue_num: str,
    db_only_task_mode,
    get_storage_backend,
    normalize_agent_reference,
    base_dir: str,
    get_nexus_dir_name,
) -> dict | None:
    if db_only_task_mode():
        try:
            backend = get_storage_backend()
            items = asyncio.run(backend.list_completions(str(issue_num)))
        except Exception:
            return None
        if not items:
            return None
        payload = items[0] if isinstance(items[0], dict) else {}
        return {
            "file": None,
            "mtime": 0,
            "agent_type": normalize_agent_reference(
                str(payload.get("agent_type") or payload.get("_agent_type") or "")
            ).lower(),
            "next_agent": normalize_agent_reference(str(payload.get("next_agent", ""))).lower(),
        }

    pattern = os.path.join(
        base_dir,
        "**",
        get_nexus_dir_name(),
        "tasks",
        "*",
        "completions",
        f"completion_summary_{issue_num}.json",
    )
    matches = glob.glob(pattern, recursive=True)
    if not matches:
        return None

    latest = max(matches, key=os.path.getmtime)
    try:
        with open(latest, encoding="utf-8") as handle:
            payload = json.load(handle)
    except Exception:
        return None
    payload = budget_completion_payload(payload)

    return {
        "file": latest,
        "mtime": os.path.getmtime(latest),
        "agent_type": normalize_agent_reference(str(payload.get("agent_type", ""))).lower(),
        "next_agent": normalize_agent_reference(str(payload.get("next_agent", ""))).lower(),
    }


def read_latest_structured_comment(
    *,
    issue_num: str,
    repo: str,
    project_name: str,
    get_git_platform,
    normalize_agent_reference,
    step_complete_comment_re,
    ready_for_comment_re,
    logger,
) -> dict | None:
    try:
        platform = get_git_platform(repo, project_name=project_name)
        comments = asyncio.run(platform.get_comments(str(issue_num)))
    except Exception as exc:
        logger.debug(f"Startup drift check skipped for issue #{issue_num}: {exc}")
        return None

    for comment in reversed(comments or []):
        body = str(getattr(comment, "body", "") or "")
        if "_Automated comment from Nexus._" in body:
            continue

        complete_match = step_complete_comment_re.search(body)
        next_match = ready_for_comment_re.search(body)
        if not (complete_match and next_match):
            continue

        return {
            "comment_id": getattr(comment, "id", None),
            "created_at": str(getattr(comment, "created_at", "") or ""),
            "completed_agent": normalize_agent_reference(complete_match.group(1)).lower(),
            "next_agent": normalize_agent_reference(next_match.group(1)).lower(),
        }
    return None
