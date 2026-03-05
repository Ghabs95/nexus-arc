"""Webhook issue comment event handling extracted from webhook_server."""

import re
from typing import Any

_MANUAL_OVERRIDE_RE = re.compile(
    r"(?:^|\b)/?(?:handoff|continue|resume|rerun|re-run|retry)\s+"
    r"(?:with\s+|to\s+)?@([A-Za-z][A-Za-z0-9_-]{0,63})\b",
    re.IGNORECASE,
)


def _resolve_supported_agent_author(author: str) -> str | None:
    """Resolve comment author to canonical agent label when supported."""
    normalized = str(author or "").strip().lower()
    if not normalized:
        return None

    # Match current and likely bot account variants used by AI agents.
    agent_keywords = ["copilot", "codex", "gemini", "claude", "nexus-bot", "nexus-arc"]
    for kw in agent_keywords:
        if kw in normalized:
            return kw
    return None


def _extract_next_agent(comment_body: str) -> str | None:
    """Extract handoff target from comment text.

    When multiple mentions exist, prefer the last one because manual handoff
    comments often include quoted context with earlier @mentions.
    """
    mentions = re.findall(r"@([A-Za-z][A-Za-z0-9_-]{0,63})", str(comment_body or ""))
    if not mentions:
        return None

    return mentions[-1].lower()


def _extract_manual_override_agent(comment_body: str) -> str | None:
    """Extract explicit manual override target from a command-style comment."""
    matches = _MANUAL_OVERRIDE_RE.findall(str(comment_body or ""))
    if not matches:
        return None
    return str(matches[-1]).strip().lower()


def handle_issue_comment_event(
    *,
    event: dict[str, Any],
    logger,
    policy,
    processed_events: set[str],
    launch_next_agent,
    check_and_notify_pr,
    reset_workflow_to_agent=None,
) -> dict[str, Any]:
    """Handle parsed issue_comment event."""
    action = event.get("action")
    comment_id = event.get("comment_id")
    comment_body = event.get("comment_body", "")
    issue_number = event.get("issue_number", "")
    comment_author = event.get("comment_author", "")
    repo_name = str(event.get("repo", "") or "").strip()
    issue = event.get("issue", {})

    logger.info("📝 Issue comment: #%s by %s (action: %s)", issue_number, comment_author, action)

    if action != "created":
        return {"status": "ignored", "reason": f"action is {action}, not created"}

    event_key = f"comment_{comment_id}"
    if event_key in processed_events:
        logger.info("⏭️ Already processed comment %s", comment_id)
        return {"status": "duplicate"}

    completion_markers = [
        r"workflow\s+complete",
        r"ready\s+for\s+review",
        r"ready\s+to\s+merge",
        r"implementation\s+complete",
        r"all\s+steps\s+completed",
    ]
    is_completion = any(
        re.search(pattern, str(comment_body or ""), re.IGNORECASE) for pattern in completion_markers
    )
    mentions = re.findall(r"@([A-Za-z][A-Za-z0-9_-]{0,63})", str(comment_body or ""))
    next_agent = _extract_next_agent(comment_body)
    manual_override_agent = _extract_manual_override_agent(comment_body)
    completed_agent = _resolve_supported_agent_author(comment_author)
    manual_issue_author = str((issue.get("user") or {}).get("login") or "").strip()

    if completed_agent is None:
        # Manual override: allow explicit command-style handoff by the issue author only.
        if not (manual_issue_author and comment_author == manual_issue_author):
            return {
                "status": "ignored",
                "reason": f"not from supported AI agent ({comment_author})",
            }
        if not manual_override_agent:
            return {
                "status": "ignored",
                "reason": "manual override command not detected",
            }
        next_agent = manual_override_agent
        logger.info(
            "⚠️ Manual chain override accepted for issue #%s by issue author %s -> @%s (mentions=%s)",
            issue_number,
            comment_author,
            next_agent,
            mentions,
        )
        if callable(reset_workflow_to_agent):
            try:
                reset_ok = bool(reset_workflow_to_agent(issue_number, next_agent))
            except Exception as exc:
                logger.warning(
                    "⚠️ Manual override reset failed for issue #%s -> @%s: %s",
                    issue_number,
                    next_agent,
                    exc,
                )
            else:
                if reset_ok:
                    logger.info(
                        "🧭 Manual override reset workflow for issue #%s to @%s",
                        issue_number,
                        next_agent,
                    )
                else:
                    logger.warning(
                        "⚠️ Manual override could not reset workflow for issue #%s to @%s",
                        issue_number,
                        next_agent,
                    )

    if is_completion and not next_agent:
        logger.info("✅ Workflow completion detected for issue #%s", issue_number)
        project = policy.determine_project_from_issue(issue)
        check_and_notify_pr(issue_number, project)
        processed_events.add(event_key)
        return {"status": "workflow_completed", "issue": issue_number}

    if next_agent:
        logger.info("🔗 Chaining to @%s for issue #%s", next_agent, issue_number)
        try:
            pid, _ = launch_next_agent(
                issue_number=issue_number,
                next_agent=next_agent,
                trigger_source=(completed_agent or f"manual-{comment_author}"),
                repo_override=repo_name or None,
            )
            if pid:
                processed_events.add(event_key)
                return {"status": "agent_launched", "issue": issue_number, "next_agent": next_agent}
            return {"status": "launch_failed", "issue": issue_number, "next_agent": next_agent}
        except Exception as exc:
            logger.error("❌ Failed to launch next agent: %s", exc)
            return {"status": "error", "message": str(exc)}

    return {"status": "no_action"}
