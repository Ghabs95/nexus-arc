import asyncio
import glob
import json
import os
import re

from nexus.core.completion import budget_completion_payload

_STEP_ID_COMMENT_RE = re.compile(r"^\s*\*\*Step ID:\*\*\s*`?([a-zA-Z0-9_-]+)`?\s*$", re.MULTILINE)
_STEP_NUM_COMMENT_RE = re.compile(
    r"^\s*\*\*Step (?:Num|Number):\*\*\s*([0-9]+)\s*$",
    re.MULTILINE,
)
_CHECKLIST_DONE_STEP_RE = re.compile(
    r"^\s*-\s*\[x\]\s*([0-9]+)\.\s+\*\*([^*]+)\*\*\s*[-–—:]\s*`?@?([a-zA-Z0-9_-]+)`?\s*$",
    re.IGNORECASE | re.MULTILINE,
)


def _coerce_step_num(value) -> int:
    try:
        step_num = int(value)
    except (TypeError, ValueError):
        return 0
    return step_num if step_num > 0 else 0


def _normalize_step_id_from_label(label: str) -> str:
    value = str(label or "").strip().lower()
    value = re.sub(r"[^a-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)
    return value.strip("_")


def _derive_step_from_checklist(
    body: str,
    *,
    completed_agent: str,
    normalize_agent_reference,
) -> tuple[str, int]:
    fallback_step_id = ""
    fallback_step_num = 0
    for match in _CHECKLIST_DONE_STEP_RE.finditer(body or ""):
        candidate_num = _coerce_step_num(match.group(1))
        if candidate_num <= 0:
            continue
        candidate_label = str(match.group(2) or "")
        candidate_agent = normalize_agent_reference(str(match.group(3) or "")).lower()
        candidate_step_id = _normalize_step_id_from_label(candidate_label)
        if not candidate_step_id:
            continue
        fallback_step_id = candidate_step_id
        fallback_step_num = candidate_num
        if candidate_agent == completed_agent:
            return candidate_step_id, candidate_num
    return fallback_step_id, fallback_step_num


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
            "step_id": normalize_agent_reference(str(payload.get("step_id", ""))).lower(),
            "step_num": _coerce_step_num(payload.get("step_num", 0)),
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
        "step_id": normalize_agent_reference(str(payload.get("step_id", ""))).lower(),
        "step_num": _coerce_step_num(payload.get("step_num", 0)),
    }


def read_latest_structured_comment(
    *,
    issue_num: str,
    repo: str,
    project_name: str,
    get_git_platform,
    resolve_issue_token=None,
    require_issue_requester_token: bool = False,
    normalize_agent_reference,
    step_complete_comment_re,
    ready_for_comment_re,
    step_id_comment_re=None,
    step_num_comment_re=None,
    logger,
) -> dict | None:
    try:
        token_override = (
            resolve_issue_token(str(project_name), str(repo), str(issue_num))
            if callable(resolve_issue_token)
            else None
        )
        if require_issue_requester_token and not token_override:
            raise PermissionError(
                f"No requester token available for {project_name}/{repo} issue #{issue_num}"
            )
        platform = get_git_platform(
            repo,
            project_name=project_name,
            token_override=token_override,
        )
        comments = asyncio.run(platform.get_comments(str(issue_num)))
    except Exception as exc:
        logger.debug(f"Startup drift check skipped for issue #{issue_num}: {exc}")
        return None

    step_id_re = step_id_comment_re or _STEP_ID_COMMENT_RE
    step_num_re = step_num_comment_re or _STEP_NUM_COMMENT_RE

    for comment in reversed(comments or []):
        body = str(getattr(comment, "body", "") or "")

        complete_match = step_complete_comment_re.search(body)
        next_match = ready_for_comment_re.search(body)
        step_id_match = step_id_re.search(body)
        step_num_match = step_num_re.search(body)
        if not complete_match:
            continue

        completed_agent = normalize_agent_reference(complete_match.group(1)).lower()
        step_id = normalize_agent_reference(step_id_match.group(1)).lower() if step_id_match else ""
        step_num = _coerce_step_num(step_num_match.group(1) if step_num_match else 0)

        if (not step_id or step_num <= 0) and completed_agent:
            fallback_step_id, fallback_step_num = _derive_step_from_checklist(
                body,
                completed_agent=completed_agent,
                normalize_agent_reference=normalize_agent_reference,
            )
            if not step_id:
                step_id = fallback_step_id
            if step_num <= 0:
                step_num = fallback_step_num
        if not step_id or step_num <= 0:
            continue

        return {
            "comment_id": getattr(comment, "id", None),
            "created_at": str(getattr(comment, "created_at", "") or ""),
            "completed_agent": completed_agent,
            "next_agent": (
                normalize_agent_reference(next_match.group(1)).lower() if next_match else "none"
            ),
            "step_id": step_id,
            "step_num": step_num,
        }
    return None
