from unittest.mock import MagicMock

from nexus.plugins.builtin.github_issue_plugin import GitHubIssuePlugin


def test_example_usage_get_issue_requests_comments_in_api_mode(monkeypatch):
    plugin = GitHubIssuePlugin({"repo": "owner/repo"})

    platform = MagicMock()
    platform._sync_request.side_effect = [
        {
            "title": "Issue 113",
            "state": "open",
            "number": 113,
            "created_at": "2026-03-08T15:00:00Z",
            "updated_at": "2026-03-08T15:01:00Z",
            "labels": [],
        },
        [
            {
                "id": 4019285436,
                "body": "## Implement Change Complete - developer\n\nReady for **@Reviewer**",
                "created_at": "2026-03-08T15:40:54Z",
                "updated_at": "2026-03-08T15:40:54Z",
            }
        ],
    ]
    monkeypatch.setattr(plugin, "_platform", lambda issue_number=None: platform)

    issue = plugin.get_issue("113", ["title", "comments"])

    assert issue is not None
    assert issue["title"] == "Issue 113"
    assert issue["comments"][0]["body"].startswith("## Implement Change Complete")
