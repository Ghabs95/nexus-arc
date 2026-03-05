from unittest.mock import MagicMock

from nexus.core.webhook.pr_service import handle_pull_request_event


class _Policy:
    def build_pr_created_message(self, event):
        return "created"

    def should_notify_pr_merged(self, review_mode):
        return review_mode == "auto"

    def build_pr_merged_message(self, event, review_mode):
        return f"merged:{review_mode}"


def test_handle_pull_request_event_opened_notifies_and_autoqueues():
    notifications = []
    launches = []
    result = handle_pull_request_event(
        event={
            "action": "opened",
            "number": 10,
            "title": "Fix #42",
            "author": "dev",
            "repo": "acme/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda msg: notifications.append(msg) or True,
        effective_review_mode=lambda _repo: "manual",
        launch_next_agent=lambda *args, **kwargs: launches.append((args, kwargs))
        or (123, "copilot"),
    )
    assert result["status"] == "pr_opened_notified"
    assert notifications == ["created"]
    assert launches


def test_handle_pull_request_event_merged_manual_skips_notification():
    notifications = []
    cleanups = []
    result = handle_pull_request_event(
        event={
            "action": "closed",
            "merged": True,
            "number": 10,
            "title": "Close #42 and #77",
            "repo": "acme/repo",
            "author": "dev",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda msg: notifications.append(msg) or True,
        effective_review_mode=lambda _repo: "manual",
        launch_next_agent=lambda *args, **kwargs: (None, None),
        cleanup_worktree_for_issue=lambda repo, issue: cleanups.append((repo, issue)) or True,
    )
    assert result["status"] == "pr_merged_skipped_manual_review"
    assert notifications == []
    assert result["cleaned_issue_refs"] == ["42", "77"]
    assert cleanups == [("acme/repo", "42"), ("acme/repo", "77")]


def test_handle_pull_request_event_merged_auto_notifies():
    notifications = []
    result = handle_pull_request_event(
        event={
            "action": "closed",
            "merged": True,
            "number": 10,
            "title": "Fix #42",
            "repo": "acme/repo",
            "author": "dev",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda msg: notifications.append(msg) or True,
        effective_review_mode=lambda _repo: "auto",
        launch_next_agent=lambda *args, **kwargs: (None, None),
        cleanup_worktree_for_issue=lambda _repo, _issue: True,
    )
    assert result["status"] == "pr_merged_notified"
    assert notifications == ["merged:auto"]
    assert result["cleaned_issue_refs"] == ["42"]
