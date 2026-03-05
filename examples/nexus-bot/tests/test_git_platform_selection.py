"""Tests for project-aware git platform selection."""


def test_get_git_platform_returns_github_by_default(monkeypatch):
    from nexus.adapters.git.github import GitHubPlatform

    import nexus.core.orchestration.nexus_core_helpers as nexus_core_helpers

    monkeypatch.setattr(nexus_core_helpers, "get_project_platform", lambda _project: "github")
    monkeypatch.setattr(nexus_core_helpers, "get_git_repo", lambda _project: "org/repo")

    platform = nexus_core_helpers.get_git_platform(project_name="nexus")

    assert isinstance(platform, GitHubPlatform)


def test_get_git_platform_returns_gitlab_for_gitlab_project(monkeypatch):
    from nexus.adapters.git.gitlab import GitLabPlatform

    import nexus.core.orchestration.nexus_core_helpers as nexus_core_helpers

    monkeypatch.setattr(nexus_core_helpers, "get_project_platform", lambda _project: "gitlab")
    monkeypatch.setattr(nexus_core_helpers, "get_git_repo", lambda _project: "sampleco/backend")
    monkeypatch.setattr(
        nexus_core_helpers, "get_gitlab_base_url", lambda _project: "https://gitlab.com"
    )
    monkeypatch.setenv("GITLAB_TOKEN", "glpat-test")

    platform = nexus_core_helpers.get_git_platform(project_name="sampleco")

    assert isinstance(platform, GitLabPlatform)


def test_get_git_platform_raises_when_gitlab_token_missing(monkeypatch):
    import pytest

    import nexus.core.orchestration.nexus_core_helpers as nexus_core_helpers

    monkeypatch.setattr(nexus_core_helpers, "get_project_platform", lambda _project: "gitlab")
    monkeypatch.setattr(nexus_core_helpers, "get_git_repo", lambda _project: "sampleco/backend")
    monkeypatch.delenv("GITLAB_TOKEN", raising=False)

    with pytest.raises(ValueError, match="GITLAB_TOKEN"):
        nexus_core_helpers.get_git_platform(project_name="sampleco")


def test_get_git_platform_uses_custom_token_var(monkeypatch):
    from nexus.adapters.git.github import GitHubPlatform

    import nexus.core.orchestration.nexus_core_helpers as nexus_core_helpers

    monkeypatch.setattr(
        nexus_core_helpers,
        "_get_project_config",
        lambda: {"sampleco": {"git_token_var_name": "SAMPLECO_GITHUB_TOKEN"}},
    )
    monkeypatch.setattr(nexus_core_helpers, "get_project_platform", lambda _project: "github")
    monkeypatch.setattr(nexus_core_helpers, "get_git_repo", lambda _project: "sampleco/app")

    monkeypatch.setenv("SAMPLECO_GITHUB_TOKEN", "ghp_custom_token_123")

    platform = nexus_core_helpers.get_git_platform(project_name="sampleco")

    assert isinstance(platform, GitHubPlatform)
    assert platform.token == "ghp_custom_token_123"
