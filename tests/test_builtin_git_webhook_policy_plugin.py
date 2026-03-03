"""Tests for built-in GitHub webhook policy plugin."""

from nexus.plugins.builtin.git_webhook_policy_plugin import GitWebhookPolicyPlugin


def test_resolve_project_key_matches_single_repo_field():
    plugin = GitWebhookPolicyPlugin()
    config = {
        "nexus": {
            "git_repo": "Ghabs95/nexus-arc",
        }
    }

    project = plugin.resolve_project_key("Ghabs95/nexus-arc", config, default_project="fallback")

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
