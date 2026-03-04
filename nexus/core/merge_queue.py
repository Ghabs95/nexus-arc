"""Merge queue enqueue/worker logic extracted from inbox_processor."""

import asyncio
import logging
import os
import time
from urllib.parse import urlparse

from nexus.adapters.git.utils import build_issue_url

logger = logging.getLogger(__name__)

_last_merge_queue_run_at = 0.0
_MERGE_QUEUE_RUN_INTERVAL = max(30, int(os.getenv("NEXUS_MERGE_QUEUE_INTERVAL_SECONDS", "60")))


def _project_config() -> dict:
    from nexus.core.config import PROJECT_CONFIG

    return PROJECT_CONFIG if isinstance(PROJECT_CONFIG, dict) else {}


def _get_project_platform(project_name: str) -> str:
    from nexus.core.config import get_project_platform

    return str(get_project_platform(project_name) or "").strip().lower()


def _get_repo(project_name: str) -> str:
    from nexus.core.config import get_repo

    return str(get_repo(project_name) or "").strip()


def _emit_alert(*args, **kwargs):
    try:
        from nexus.core.integrations.notifications import emit_alert
    except Exception:
        return None
    return emit_alert(*args, **kwargs)


def _get_git_platform(repo_key: str, *, project_name: str | None):
    from nexus.core.orchestration.nexus_core_helpers import get_git_platform

    return get_git_platform(repo_key, project_name=project_name)


def _state_manager():
    from nexus.core.state_manager import HostStateManager

    return HostStateManager


def _normalize_merge_queue_review_mode(value: str) -> str:
    mode = str(value or "").strip().lower()
    return mode if mode in {"manual", "auto"} else "manual"


def _resolve_merge_queue_review_mode(project_name: str) -> str:
    project_config = _project_config()
    project_cfg = project_config.get(project_name, {}) if project_name else {}
    if isinstance(project_cfg, dict):
        project_merge_queue = project_cfg.get("merge_queue")
        if isinstance(project_merge_queue, dict) and "review_mode" in project_merge_queue:
            return _normalize_merge_queue_review_mode(str(project_merge_queue.get("review_mode")))

    global_merge_queue = project_config.get("merge_queue")
    if isinstance(global_merge_queue, dict) and "review_mode" in global_merge_queue:
        return _normalize_merge_queue_review_mode(str(global_merge_queue.get("review_mode")))

    return "manual"


def enqueue_merge_queue_prs(
    *,
    issue_num: str,
    issue_repo: str,
    project_name: str,
    pr_urls: list[str],
    alert_source: str = "inbox_processor",
) -> list[dict]:
    """Persist PRs into merge queue and emit enqueue notification."""
    review_mode = _resolve_merge_queue_review_mode(project_name)
    queued: list[dict] = []
    state_manager = _state_manager()
    project_config = _project_config()
    for pr_url in pr_urls:
        try:
            item = state_manager.enqueue_merge_candidate(
                issue_num=str(issue_num),
                project=str(project_name),
                repo=str(issue_repo),
                pr_url=str(pr_url),
                review_mode=review_mode,
            )
            queued.append(item)
        except Exception as exc:
            logger.warning("Failed to enqueue PR %s for merge queue: %s", pr_url, exc)

    if queued:
        mode_text = "automatic merge" if review_mode == "auto" else "manual approval"
        actions = [
            {"label": "📝 Logs", "callback_data": f"logs_{issue_num}|{project_name}", "url": ""},
            {
                "label": "🔗 Issue",
                "callback_data": "",
                "url": build_issue_url(
                    _get_repo(project_name),
                    issue_num,
                    (
                        project_config.get(project_name)
                        if isinstance(project_config.get(project_name), dict)
                        else None
                    ),
                ),
            },
        ]
        if review_mode == "manual":
            actions.extend(
                [
                    {
                        "label": "✅ Approve Merge",
                        "callback_data": f"mqapprove_{issue_num}|{project_name}",
                        "url": "",
                    },
                    {
                        "label": "🚀 Merge Now",
                        "callback_data": f"mqmerge_{issue_num}|{project_name}",
                        "url": "",
                    },
                ]
            )
        else:
            actions.append(
                {
                    "label": "🔄 Retry Merge",
                    "callback_data": f"mqretry_{issue_num}|{project_name}",
                    "url": "",
                }
            )
        _emit_alert(
            (
                f"📦 Merge queue updated for issue #{issue_num} ({project_name}).\n"
                f"Queued PRs: {len(queued)}\n"
                f"Review mode: `{review_mode}` ({mode_text})."
            ),
            severity="info",
            source=alert_source,
            issue_number=str(issue_num),
            project_key=str(project_name),
            actions=actions,
        )

    return queued


def _parse_github_pr_url(pr_url: str) -> tuple[str, str] | None:
    parsed = urlparse(str(pr_url or "").strip())
    if parsed.netloc not in {"github.com", "www.github.com"}:
        return None
    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 4 or parts[2] != "pull":
        return None
    pr_number = str(parts[3]).strip()
    if not pr_number.isdigit():
        return None
    return f"{parts[0]}/{parts[1]}", pr_number


def _parse_gitlab_mr_url(pr_url: str) -> tuple[str, str] | None:
    parsed = urlparse(str(pr_url or "").strip())
    if not parsed.netloc:
        return None
    parts = [segment for segment in parsed.path.split("/") if segment]
    if len(parts) < 4:
        return None

    mr_number: str | None = None
    repo_parts: list[str] = []

    if "-" in parts:
        dash_idx = parts.index("-")
        if dash_idx + 2 >= len(parts) or parts[dash_idx + 1] != "merge_requests":
            return None
        repo_parts = parts[:dash_idx]
        mr_number = parts[dash_idx + 2]
    else:
        if len(parts) < 3 or parts[-2] != "merge_requests":
            return None
        repo_parts = parts[:-2]
        mr_number = parts[-1]

    if not repo_parts or not mr_number or not mr_number.isdigit():
        return None
    return "/".join(repo_parts), mr_number


def merge_queue_auto_merge_once() -> None:
    """Process pending auto-merge queue entries (best effort)."""
    global _last_merge_queue_run_at
    now = time.time()
    if now - _last_merge_queue_run_at < _MERGE_QUEUE_RUN_INTERVAL:
        return
    _last_merge_queue_run_at = now

    try:
        state_manager = _state_manager()
        queue = state_manager.load_merge_queue()
    except Exception as exc:
        logger.warning("Could not load merge queue: %s", exc)
        return

    if not isinstance(queue, dict) or not queue:
        return

    pending = [
        item
        for item in queue.values()
        if isinstance(item, dict)
        and str(item.get("status", "")).lower() == "pending_auto_merge"
        and (
            str(item.get("review_mode", "")).lower() == "auto" or bool(item.get("manual_override"))
        )
    ]
    pending.sort(key=lambda item: float(item.get("created_at", 0.0)))

    for item in pending:
        pr_url = str(item.get("pr_url") or "").strip()
        issue_num = str(item.get("issue") or "").strip()
        project_name = str(item.get("project") or "").strip()
        repo_name = str(item.get("repo") or "").strip()
        platform_type = str(_get_project_platform(project_name) or "github").strip().lower()
        pr_number: str | None = None

        if platform_type == "gitlab":
            parsed_gl = _parse_gitlab_mr_url(pr_url)
            if parsed_gl:
                parsed_repo, pr_number = parsed_gl
                if not repo_name:
                    repo_name = parsed_repo
        else:
            parsed_gh = _parse_github_pr_url(pr_url)
            if parsed_gh:
                parsed_repo, pr_number = parsed_gh
                if not repo_name:
                    repo_name = parsed_repo

        if not pr_number:
            state_manager.update_merge_candidate(
                pr_url,
                status="blocked",
                last_error="unsupported_pr_url",
            )
            continue

        state_manager.update_merge_candidate(pr_url, status="merging", last_error="")
        try:
            if not repo_name:
                raise RuntimeError("missing_repo")
            platform = _get_git_platform(repo_name, project_name=project_name or None)
            merge_output = asyncio.run(
                platform.merge_pull_request(
                    pr_number,
                    squash=True,
                    delete_branch=True,
                    auto=True,
                )
            )
        except Exception as exc:
            error_text = str(exc).strip()[:500]
            lowered = error_text.lower()
            status = (
                "blocked"
                if any(
                    token in lowered
                    for token in (
                        "review",
                        "check",
                        "required",
                        "conflict",
                        "pipeline",
                        "not mergeable",
                        "unsupported",
                        "missing_repo",
                    )
                )
                else "failed"
            )
            state_manager.update_merge_candidate(
                pr_url,
                status=status,
                last_error=error_text or "merge_error",
                merge_command=f"{platform_type}:merge_pull_request {pr_number}",
            )
            _emit_alert(
                (
                    f"⚠️ Merge queue could not merge PR for issue #{issue_num}: {pr_url}\n"
                    f"Status: {status}\n"
                    f"Reason: {error_text or 'merge_error'}"
                ),
                severity="warning",
                source="merge_queue",
                issue_number=issue_num or None,
                project_key=project_name or None,
            )
            continue

        state_manager.update_merge_candidate(
            pr_url,
            status="merged",
            last_error="",
            manual_override=False,
            merged_at=time.time(),
            merge_command=f"{platform_type}:merge_pull_request {pr_number}",
            merge_result=(merge_output or "")[:500],
        )
        _emit_alert(
            f"✅ Merge queue merged PR for issue #{issue_num}: {pr_url}",
            severity="info",
            source="merge_queue",
            issue_number=issue_num or None,
            project_key=project_name or None,
        )
