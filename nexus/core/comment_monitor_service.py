"""Agent comment polling helpers extracted from inbox_processor."""

from collections.abc import Callable, Iterable
from typing import Any

_INPUT_PATTERNS = [
    "questions for @ghabs",
    "questions for `@ghabs",
    "waiting for @ghabs",
    "waiting for `@ghabs",
    "need your input",
    "please provide",
    "owner:** @ghabs",
    "owner:** `@ghabs",
    "blocker:",
    "your input to proceed",
]


def comment_needs_user_input(body: str) -> bool:
    candidate = str(body or "").lower()
    return any(pattern in candidate for pattern in _INPUT_PATTERNS)


def comment_preview(body: str, limit: int = 200) -> str:
    text = str(body or "")
    return text[:limit] + "..." if len(text) > limit else text


def run_comment_monitor_cycle(
    *,
    logger,
    iter_projects: Callable[[], Iterable[tuple[str, Any]]],
    get_project_platform: Callable[[str], str | None],
    get_repo: Callable[[str], str],
    list_workflow_issue_numbers: Callable[[str, str], list[Any]],
    get_bot_comments: Callable[[str, str, str], list[Any]],
    notify_agent_needs_input: Callable[..., bool],
    notified_comments: set[Any],
    clear_polling_failures: Callable[[str], None],
    record_polling_failure: Callable[[str, Exception], None],
    should_poll_project: Callable[[str], bool] | None = None,
    bot_author: str = "Ghabs",
) -> None:
    """Monitor issue comments and notify on agent blockers/questions."""
    loop_scope = "agent-comments:loop"

    def _is_authz_error(exc: Exception) -> bool:
        text = str(exc or "").lower()
        return (
            " 401" in text
            or " 403" in text
            or "unauthorized" in text
            or "forbidden" in text
            or "requester token" in text
        )

    try:
        all_issue_nums: list[tuple[Any, str, str]] = []
        for project_name, _cfg in iter_projects():
            project_platform = (get_project_platform(project_name) or "github").lower().strip()
            if project_platform not in {"github", "gitlab"}:
                logger.debug(
                    "Skipping Git issue polling for non-supported project %s (platform=%s)",
                    project_name,
                    project_platform,
                )
                continue

            list_scope = f"agent-comments:list-issues:{project_name}"
            if callable(should_poll_project) and not should_poll_project(str(project_name)):
                logger.info(
                    "Skipping issue polling for project %s: no setup-ready users with access.",
                    project_name,
                )
                clear_polling_failures(list_scope)
                continue

            repo = get_repo(project_name)
            try:
                for issue_number in list_workflow_issue_numbers(project_name, repo):
                    all_issue_nums.append((issue_number, project_name, repo))
                clear_polling_failures(list_scope)
            except Exception as exc:
                if _is_authz_error(exc):
                    logger.warning(
                        "Skipping issue polling for project %s due to access denial: %s",
                        project_name,
                        exc,
                    )
                    clear_polling_failures(list_scope)
                    continue
                logger.warning("Issue list failed for project %s: %s", project_name, exc)
                record_polling_failure(list_scope, exc)
                continue

        if not all_issue_nums:
            return

        for issue_num, project_name, repo in all_issue_nums:
            if not issue_num:
                continue

            comments_scope = f"agent-comments:get-comments:{project_name}"
            try:
                bot_comments = get_bot_comments(project_name, repo, str(issue_num))
                clear_polling_failures(comments_scope)
            except Exception as exc:
                if _is_authz_error(exc):
                    logger.warning(
                        "Skipping comment polling for project %s issue #%s due to access denial: %s",
                        project_name,
                        issue_num,
                        exc,
                    )
                    clear_polling_failures(comments_scope)
                    continue
                logger.warning("Failed to fetch comments for issue #%s: %s", issue_num, exc)
                record_polling_failure(comments_scope, exc)
                continue

            for comment in bot_comments or []:
                try:
                    comment_id = getattr(comment, "id", None)
                    body = str(getattr(comment, "body", "") or "")
                    if comment_id in notified_comments:
                        continue
                    if not comment_needs_user_input(body):
                        continue
                    if notify_agent_needs_input(
                        issue_num,
                        "agent",
                        comment_preview(body),
                        project=project_name,
                    ):
                        logger.info("📨 Sent input request alert for issue #%s", issue_num)
                        notified_comments.add(comment_id)
                    else:
                        logger.warning("Failed to send input alert for issue #%s", issue_num)
                except Exception as exc:
                    logger.error("Error processing comment for issue #%s: %s", issue_num, exc)

        clear_polling_failures(loop_scope)
    except Exception as exc:
        logger.error("Error in check_agent_comments: %s", exc)
        record_polling_failure(loop_scope, exc)
