"""Tests for direct nexus-arc plugin imports from Nexus app."""


def test_github_issue_plugin_exports_api_symbols():
    from nexus.plugins.builtin.github_issue_plugin import (
        GitHubIssuePlugin,
        register_plugins,
    )

    assert callable(register_plugins)
    assert GitHubIssuePlugin.__name__ == "GitHubIssuePlugin"
