"""Tests for direct nexus-arc plugin imports from Nexus app."""


def test_github_issue_plugin_shim_exports_core_symbols():
    from nexus.plugins.builtin.github_issue_plugin import (
        GitHubIssueCLIPlugin,
        register_plugins,
    )

    assert callable(register_plugins)
    assert GitHubIssueCLIPlugin.__name__ == "GitHubIssueCLIPlugin"
