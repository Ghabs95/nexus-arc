import pytest

from nexus.core.orchestration import nexus_core_helpers as helpers


class _GitHubDummy:
    def __init__(self, repo: str, token: str | None = None):
        self.repo = repo
        self.token = token


class _GitLabDummy:
    def __init__(self, token: str, repo: str, base_url: str):
        self.repo = repo
        self.token = token
        self.base_url = base_url


def test_get_git_platform_github_without_fallback_uses_empty_token(monkeypatch):
    monkeypatch.delenv("NEXUS_AUTH_ENABLED", raising=False)
    monkeypatch.setattr(
        helpers,
        "_get_project_config",
        lambda: {"nexus": {"git_token_var_name": "GITHUB_TOKEN"}},
    )
    monkeypatch.setattr(helpers, "get_default_project", lambda: "nexus")
    monkeypatch.setattr(helpers, "get_git_repo", lambda _p: "ghabs-org/nexus-arc")
    monkeypatch.setattr(helpers, "get_project_platform", lambda _p: "github")
    monkeypatch.setattr(helpers, "resolve_git_platform_class", lambda _p: _GitHubDummy)
    platform = helpers.get_git_platform(
        repo="ghabs-org/nexus-arc",
        project_name="nexus",
        token_override=None,
        allow_env_token_fallback=False,
    )

    assert isinstance(platform, _GitHubDummy)
    assert platform.token == ""


def test_get_git_platform_gitlab_without_fallback_requires_token(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "_get_project_config",
        lambda: {"example-org": {"git_token_var_name": "GITLAB_TOKEN"}},
    )
    monkeypatch.setattr(helpers, "get_default_project", lambda: "example-org")
    monkeypatch.setattr(helpers, "get_git_repo", lambda _p: "example-org/example-project")
    monkeypatch.setattr(helpers, "get_project_platform", lambda _p: "gitlab")
    monkeypatch.setattr(helpers, "get_gitlab_base_url", lambda _p: "https://gitlab.com")
    monkeypatch.setattr(helpers, "resolve_git_platform_class", lambda _p: _GitLabDummy)
    with pytest.raises(ValueError, match="GitLab token required"):
        helpers.get_git_platform(
            repo="example-org/example-project",
            project_name="example-org",
            token_override=None,
            allow_env_token_fallback=False,
        )
