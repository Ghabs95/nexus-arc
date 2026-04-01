"""Tests for built-in GitHub webhook policy plugin."""

from nexus.plugins.builtin.git_webhook_policy_plugin import GitWebhookPolicyPlugin


def test_resolve_project_key_matches_single_repo_field():
    plugin = GitWebhookPolicyPlugin()
    config = {
        "nexus": {
            "git_repo": "ghabs-org/nexus-arc",
        }
    }

    project = plugin.resolve_project_key("ghabs-org/nexus-arc", config, default_project="fallback")

    assert project == "nexus"


def test_resolve_project_key_matches_repo_in_github_repos_list():
    plugin = GitWebhookPolicyPlugin()
    config = {
        "project_alpha": {
            "git_repo": "acme/project-alpha-backend",
            "git_repos": ["acme/project-alpha-backend", "acme/project-alpha-mobile"],
        }
    }

    project = plugin.resolve_project_key(
        "acme/project-alpha-mobile", config, default_project="fallback"
    )

    assert project == "project_alpha"


def test_parse_github_pull_request_closed_unmerged_event():
    plugin = GitWebhookPolicyPlugin()

    event = plugin.parse_pull_request_event(
        {
            "action": "closed",
            "pull_request": {
                "number": 18,
                "title": "Fix #42",
                "html_url": "https://github.com/acme/repo/pull/18",
                "user": {"login": "dev"},
                "merged": False,
                "merged_by": None,
            },
            "repository": {"full_name": "acme/repo"},
            "sender": {"login": "maintainer"},
        }
    )

    assert event["action"] == "closed"
    assert event["merged"] is False
    assert event["closed_by"] == "maintainer"
    assert event["merged_by"] == "unknown"
    assert event["repo"] == "acme/repo"


def test_parse_gitlab_merge_request_merged_event_prefers_merge_user():
    plugin = GitWebhookPolicyPlugin()

    event = plugin.parse_pull_request_event(
        {
            "object_kind": "merge_request",
            "user": {"username": "actor"},
            "project": {"path_with_namespace": "acme/repo"},
            "object_attributes": {
                "action": "merge",
                "state": "merged",
                "iid": 19,
                "title": "Fix #42",
                "url": "https://gitlab.com/acme/repo/-/merge_requests/19",
            },
            "merge_user": {"username": "maintainer"},
        }
    )

    assert event["action"] == "merged"
    assert event["merged"] is True
    assert event["merged_by"] == "maintainer"
    assert event["closed_by"] == "unknown"
    assert event["repo"] == "acme/repo"


def test_parse_gitlab_merge_request_action_merge_without_merged_state_is_still_merged():
    """action='merge' alone (state not yet updated to 'merged') must yield merged=True."""
    plugin = GitWebhookPolicyPlugin()

    event = plugin.parse_pull_request_event(
        {
            "object_kind": "merge_request",
            "user": {"username": "actor"},
            "project": {"path_with_namespace": "acme/repo"},
            "object_attributes": {
                "action": "merge",
                "state": "open",  # state not yet updated
                "iid": 21,
                "title": "Fix #99",
                "url": "https://gitlab.com/acme/repo/-/merge_requests/21",
            },
            "merge_user": {"username": "maintainer"},
        }
    )

    assert event["action"] == "merged"
    assert event["merged"] is True
    assert event["merged_by"] == "maintainer"
    assert event["closed_by"] == "unknown"


def test_parse_gitlab_merge_request_closed_unmerged_event_tracks_closer():
    plugin = GitWebhookPolicyPlugin()

    event = plugin.parse_pull_request_event(
        {
            "object_kind": "merge_request",
            "user": {"username": "closer"},
            "project": {"path_with_namespace": "acme/repo"},
            "object_attributes": {
                "action": "close",
                "state": "closed",
                "iid": 20,
                "title": "Fix #77",
                "url": "https://gitlab.com/acme/repo/-/merge_requests/20",
            },
        }
    )

    assert event["action"] == "closed"
    assert event["merged"] is False
    assert event["closed_by"] == "closer"
    assert event["merged_by"] == "unknown"


def test_build_pr_closed_unmerged_message_mentions_conservative_cleanup():
    plugin = GitWebhookPolicyPlugin()

    message = plugin.build_pr_closed_unmerged_message(
        {
            "number": 20,
            "title": "Fix #77",
            "repo": "acme/repo",
            "closed_by": "closer",
            "url": "https://example.test/pr/20",
        }
    )

    assert "Closed Without Merge" in message
    assert "Cleanup: `conservative (no auto-cleanup)`" in message
    assert "@closer" in message
