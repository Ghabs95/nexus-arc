from unittest.mock import MagicMock

from nexus.plugins.builtin.gitlab_issue_plugin import GitLabIssuePlugin


def test_example_usage_gitlab_get_issue_requests_comments(monkeypatch):
    plugin = GitLabIssuePlugin({"repo": "group/project"})

    platform = MagicMock()
    platform._sync_request.side_effect = [
        {
            "iid": 113,
            "title": "Issue 113",
            "state": "opened",
            "created_at": "2026-03-08T15:00:00Z",
            "updated_at": "2026-03-08T15:01:00Z",
            "labels": [],
        },
        [
            {
                "id": 901,
                "body": "## Verify Change Complete - reviewer\n\nReady for **@Deployer**",
                "created_at": "2026-03-08T15:45:23Z",
                "updated_at": "2026-03-08T15:45:23Z",
            }
        ],
    ]
    monkeypatch.setattr(plugin, "_platform", lambda issue_number=None: platform)

    issue = plugin.get_issue("113", ["title", "comments"])

    assert issue is not None
    assert issue["title"] == "Issue 113"
    assert issue["comments"][0]["body"].startswith("## Verify Change Complete")
