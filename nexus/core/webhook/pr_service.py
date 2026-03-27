"""Webhook pull request event handling extracted from webhook_server."""

import re
from typing import Any

_PR_MERGE_CLOSE_TERMINAL_STEP_STATUSES = frozenset({"completed", "skipped"})


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


def _normalize_pr_action(action: Any, *, merged: bool) -> str:
    normalized = str(action or "").strip().lower()
    if normalized in {"merge", "merged"}:
        return "merged"
    if normalized in {"close", "closed"}:
        return "merged" if merged else "closed"
    return normalized


def evaluate_issue_close_for_pr_merge(
    workflow_status: dict[str, Any] | None,
) -> tuple[bool, str]:
    """Return whether a merged PR may auto-close the linked issue."""
    if not isinstance(workflow_status, dict) or not workflow_status:
        return False, "workflow status unavailable"

    workflow_state = str(workflow_status.get("state", "") or "").strip().lower()
    if workflow_state != "completed":
        return False, f"workflow state is '{workflow_state or 'unknown'}'"

    steps = workflow_status.get("steps")
    if not isinstance(steps, list) or not steps:
        return False, "workflow steps unavailable"

    for index, step in enumerate(steps, start=1):
        if not isinstance(step, dict):
            return False, f"workflow step {index} metadata unavailable"
        step_name = str(step.get("name", "") or f"step-{index}").strip() or f"step-{index}"
        step_status = str(step.get("status", "") or "").strip().lower()
        if step_status not in _PR_MERGE_CLOSE_TERMINAL_STEP_STATUSES:
            return False, f"workflow step '{step_name}' is '{step_status or 'unknown'}'"

    return True, "workflow completed"


def handle_pull_request_event(
    *,
    event: dict[str, Any],
    logger,
    policy,
    notify_lifecycle,
    effective_review_mode,
    launch_next_agent,
    cleanup_worktree_for_issue=None,
    close_issue_for_issue=None,
) -> dict[str, Any]:
    """Handle parsed pull_request event."""
    action = event.get("action")
    pr_number = event.get("number")
    pr_title = event.get("title", "")
    pr_author = event.get("author", "")
    repo_name = event.get("repo", "unknown")
    merged = bool(event.get("merged"))
    normalized_action = _normalize_pr_action(action, merged=merged)

    logger.info(
        "🔀 Pull request #%s: action=%s normalized=%s by %s",
        pr_number,
        action,
        normalized_action,
        pr_author,
    )

    if normalized_action == "opened":
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

        return {"status": "pr_opened_notified", "pr": pr_number, "action": normalized_action}

    referenced_issue_refs = _extract_issue_numbers_from_text(pr_title)

    if normalized_action == "merged":
        closed_issue_refs: list[str] = []
        if callable(close_issue_for_issue):
            for issue_ref in referenced_issue_refs:
                try:
                    if close_issue_for_issue(repo_name, issue_ref):
                        closed_issue_refs.append(issue_ref)
                except Exception as exc:
                    logger.warning(
                        "Failed webhook PR-merge issue close for issue #%s in %s: %s",
                        issue_ref,
                        repo_name,
                        exc,
                    )

        cleaned_issue_refs: list[str] = []
        if callable(cleanup_worktree_for_issue):
            for issue_ref in referenced_issue_refs:
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
        message = policy.build_pr_merged_message(event, review_mode)
        notify_lifecycle(message)
        return {
            "status": "pr_merged_notified",
            "pr": pr_number,
            "action": normalized_action,
            "review_mode": review_mode,
            "cleaned_issue_refs": cleaned_issue_refs,
            "closed_issue_refs": closed_issue_refs,
        }

    if normalized_action == "closed":
        review_mode = effective_review_mode(repo_name)
        message = policy.build_pr_closed_unmerged_message(event)
        notify_lifecycle(message)
        return {
            "status": "pr_closed_unmerged_notified",
            "pr": pr_number,
            "action": normalized_action,
            "review_mode": review_mode,
            "cleaned_issue_refs": [],
            "closed_issue_refs": [],
        }

    return {"status": "logged", "pr": pr_number, "action": normalized_action}
