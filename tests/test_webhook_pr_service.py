from unittest.mock import MagicMock

from nexus.core.webhook.pr_service import (
    evaluate_issue_close_for_pr_merge,
    handle_pull_request_event,
)


class _Policy:
    def build_pr_created_message(self, event):
        return "created"

    def build_pr_merged_message(self, event, review_mode):
        return f"merged:{review_mode}"

    def build_pr_closed_unmerged_message(self, event):
        return f"closed:{event['number']}"


def test_evaluate_issue_close_for_pr_merge_allows_completed_workflow():
    allowed, reason = evaluate_issue_close_for_pr_merge(
        {
            "state": "completed",
            "steps": [
                {"name": "merge_deploy", "status": "completed"},
                {"name": "document_close", "status": "skipped"},
            ],
        }
    )

    assert allowed is True
    assert reason == "workflow completed"


def test_evaluate_issue_close_for_pr_merge_blocks_non_completed_workflow():
    allowed, reason = evaluate_issue_close_for_pr_merge(
        {
            "state": "running",
            "steps": [
                {"name": "merge_deploy", "status": "completed"},
                {"name": "document_close", "status": "pending"},
            ],
        }
    )

    assert allowed is False
    assert reason == "workflow state is 'running'"


def test_evaluate_issue_close_for_pr_merge_blocks_incomplete_steps():
    allowed, reason = evaluate_issue_close_for_pr_merge(
        {
            "state": "completed",
            "steps": [
                {"name": "merge_deploy", "status": "completed"},
                {"name": "document_close", "status": "pending"},
            ],
        }
    )

    assert allowed is False
    assert reason == "workflow step 'document_close' is 'pending'"


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


def test_handle_pull_request_event_merged_notifies_and_cleans_up():
    notifications = []
    cleanups = []
    closes = []
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
        close_issue_for_issue=lambda repo, issue: closes.append((repo, issue)) or True,
    )
    assert result["status"] == "pr_merged_notified"
    assert notifications == ["merged:manual"]
    assert result["cleaned_issue_refs"] == ["42", "77"]
    assert result["closed_issue_refs"] == ["42", "77"]
    assert closes == [("acme/repo", "42"), ("acme/repo", "77")]
    assert cleanups == [("acme/repo", "42"), ("acme/repo", "77")]


def test_handle_pull_request_event_closed_unmerged_notifies_without_cleanup():
    notifications = []
    cleanups = []
    closes = []
    result = handle_pull_request_event(
        event={
            "action": "closed",
            "merged": False,
            "number": 10,
            "title": "Close #42 and #77",
            "repo": "acme/repo",
            "author": "dev",
            "closed_by": "maintainer",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda msg: notifications.append(msg) or True,
        effective_review_mode=lambda _repo: "auto",
        launch_next_agent=lambda *args, **kwargs: (None, None),
        cleanup_worktree_for_issue=lambda repo, issue: cleanups.append((repo, issue)) or True,
        close_issue_for_issue=lambda repo, issue: closes.append((repo, issue)) or True,
    )
    assert result["status"] == "pr_closed_unmerged_notified"
    assert notifications == ["closed:10"]
    assert result["cleaned_issue_refs"] == []
    assert result["closed_issue_refs"] == []
    assert closes == []
    assert cleanups == []
