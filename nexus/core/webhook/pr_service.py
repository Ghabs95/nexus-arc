"""Webhook pull request event handling extracted from webhook_server."""

import re
from typing import Any


def _extract_issue_numbers_from_text(text: str) -> list[str]:
    if not text:
        return []
    # Preserve first-seen order while deduplicating.
    ordered: list[str] = []
    seen: set[str] = set()
    for match in re.findall(r"#(\d+)", str(text)):
        if match in seen:
            continue
        seen.add(match)
        ordered.append(match)
    return ordered


def handle_pull_request_event(
    *,
    event: dict[str, Any],
    logger,
    policy,
    notify_lifecycle,
    effective_review_mode,
    launch_next_agent,
    cleanup_worktree_for_issue=None,
) -> dict[str, Any]:
    """Handle parsed pull_request event."""
    action = event.get("action")
    pr_number = event.get("number")
    pr_title = event.get("title", "")
    pr_author = event.get("author", "")
    repo_name = event.get("repo", "unknown")
    merged = bool(event.get("merged"))

    logger.info("🔀 Pull request #%s: %s by %s", pr_number, action, pr_author)

    if action == "opened":
        message = policy.build_pr_created_message(event)
        notify_lifecycle(message)

        issue_refs = _extract_issue_numbers_from_text(pr_title)
        if issue_refs:
            referenced_issue = issue_refs[0]
            logger.info(
                "PR #%s references issue #%s — auto-queuing reviewer",
                pr_number,
                referenced_issue,
            )
            try:
                launch_next_agent(referenced_issue, "reviewer", trigger_source="pr_opened")
            except Exception as exc:
                logger.warning(
                    "Failed to auto-queue reviewer for issue #%s: %s",
                    referenced_issue,
                    exc,
                )

        return {"status": "pr_opened_notified", "pr": pr_number, "action": action}

    if action == "closed" and merged:
        cleaned_issue_refs: list[str] = []
        if callable(cleanup_worktree_for_issue):
            for issue_ref in _extract_issue_numbers_from_text(pr_title):
                try:
                    if cleanup_worktree_for_issue(repo_name, issue_ref):
                        cleaned_issue_refs.append(issue_ref)
                except Exception as exc:
                    logger.warning(
                        "Failed webhook PR-merge worktree cleanup for issue #%s in %s: %s",
                        issue_ref,
                        repo_name,
                        exc,
                    )

        review_mode = effective_review_mode(repo_name)
        should_notify = policy.should_notify_pr_merged(review_mode)
        if should_notify:
            message = policy.build_pr_merged_message(event, review_mode)
            notify_lifecycle(message)
            return {
                "status": "pr_merged_notified",
                "pr": pr_number,
                "action": action,
                "review_mode": review_mode,
                "cleaned_issue_refs": cleaned_issue_refs,
            }

        logger.info(
            "Skipping PR merged notification for #%s due to review mode '%s'",
            pr_number,
            review_mode,
        )
        return {
            "status": "pr_merged_skipped_manual_review",
            "pr": pr_number,
            "action": action,
            "review_mode": review_mode,
            "cleaned_issue_refs": cleaned_issue_refs,
        }

    return {"status": "logged", "pr": pr_number, "action": action}
