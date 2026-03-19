"""Tests for webhook lifecycle notifications."""

from unittest.mock import AsyncMock, patch


def _issue_payload(action: str) -> dict:
    return {
        "action": action,
        "issue": {
            "number": 41,
            "title": "Example issue",
            "body": "Body",
            "html_url": "https://github.com/acme/repo/issues/41",
            "user": {"login": "alice"},
            "labels": [],
        },
        "repository": {"full_name": "sample-org/nexus-arc"},
        "sender": {"login": "bob"},
    }


def _pr_payload(action: str, merged: bool = False) -> dict:
    return {
        "action": action,
        "pull_request": {
            "number": 10,
            "title": "Fix issue",
            "html_url": "https://github.com/acme/repo/pull/10",
            "user": {"login": "dev"},
            "merged": merged,
            "merged_by": {"login": "maintainer"},
        },
        "repository": {"full_name": "sample-org/nexus-arc"},
    }


@patch("webhook_server._notify_lifecycle", return_value=True)
def test_issue_closed_sends_notification(mock_notify):
    from webhook_server import _get_webhook_policy, handle_issue_opened

    payload = _issue_payload("closed")
    event = _get_webhook_policy().parse_issue_event(payload)
    result = handle_issue_opened(payload, event)

    assert result["status"] == "issue_closed_notified"
    mock_notify.assert_called_once()


@patch("webhook_server._notify_lifecycle", return_value=True)
def test_pr_opened_sends_notification(mock_notify):
    from webhook_server import _get_webhook_policy, handle_pull_request

    payload = _pr_payload("opened")
    event = _get_webhook_policy().parse_pull_request_event(payload)
    result = handle_pull_request(payload, event)

    assert result["status"] == "pr_opened_notified"
    mock_notify.assert_called_once()


@patch("webhook_server._effective_review_mode", return_value="manual")
@patch("webhook_server._notify_lifecycle", return_value=True)
def test_pr_merged_skips_when_manual_review_policy(mock_notify, mock_policy):
    from webhook_server import _get_webhook_policy, handle_pull_request

    payload = _pr_payload("closed", merged=True)
    event = _get_webhook_policy().parse_pull_request_event(payload)
    result = handle_pull_request(payload, event)

    assert result["status"] == "pr_merged_skipped_manual_review"
    mock_policy.assert_called_once()
    mock_notify.assert_not_called()


@patch("webhook_server._effective_review_mode", return_value="auto")
@patch("webhook_server._notify_lifecycle", return_value=True)
def test_pr_merged_notifies_when_policy_allows(mock_notify, mock_policy):
    from webhook_server import _get_webhook_policy, handle_pull_request

    payload = _pr_payload("closed", merged=True)
    event = _get_webhook_policy().parse_pull_request_event(payload)
    result = handle_pull_request(payload, event)

    assert result["status"] == "pr_merged_notified"
    mock_policy.assert_called_once()
    mock_notify.assert_called_once()


@patch("webhook_server._repo_to_project_key", return_value="nexus")
@patch("webhook_server.get_git_platform")
@patch("webhook_server._get_runtime_workflow_plugin")
def test_close_issue_for_pr_merge_skips_until_workflow_complete(
    mock_workflow_plugin,
    mock_get_platform,
    _mock_project_key,
):
    from webhook_server import _close_issue_for_pr_merge

    workflow_plugin = AsyncMock()
    workflow_plugin.get_workflow_status.return_value = {
        "state": "running",
        "steps": [
            {"name": "merge_deploy", "status": "completed"},
            {"name": "document_close", "status": "pending"},
        ],
    }
    mock_workflow_plugin.return_value = workflow_plugin

    platform = AsyncMock()
    mock_get_platform.return_value = platform

    closed = _close_issue_for_pr_merge("acme/repo", "42")

    assert closed is False
    platform.close_issue.assert_not_called()


@patch("webhook_server._repo_to_project_key", return_value="nexus")
@patch("webhook_server.get_git_platform")
@patch("webhook_server._get_runtime_workflow_plugin")
def test_close_issue_for_pr_merge_closes_completed_workflow(
    mock_workflow_plugin,
    mock_get_platform,
    _mock_project_key,
):
    from webhook_server import _close_issue_for_pr_merge

    workflow_plugin = AsyncMock()
    workflow_plugin.get_workflow_status.return_value = {
        "state": "completed",
        "steps": [
            {"name": "merge_deploy", "status": "completed"},
            {"name": "document_close", "status": "completed"},
        ],
    }
    mock_workflow_plugin.return_value = workflow_plugin

    platform = AsyncMock()
    platform.close_issue.return_value = True
    mock_get_platform.return_value = platform

    closed = _close_issue_for_pr_merge("acme/repo", "42")

    assert closed is True
    platform.close_issue.assert_awaited_once()
