"""Webhook pull request review event handling extracted from webhook_server."""

from typing import Any


def handle_pull_request_review_event(*, event: dict[str, Any], logger) -> dict[str, Any]:
    """Handle parsed pull_request_review event."""
    pr_number = event.get("pr_number")
    review_state = event.get("review_state")
    reviewer = event.get("reviewer", "")

    logger.info("👀 PR review #%s: %s by %s", pr_number, review_state, reviewer)
    return {"status": "logged", "pr": pr_number, "state": review_state}
