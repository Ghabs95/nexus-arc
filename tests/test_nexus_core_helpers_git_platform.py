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


def test_get_git_platform_uses_polling_token_for_github(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "_get_project_config",
        lambda: {"nexus": {"git_token_var_name": "GITHUB_TOKEN"}},
    )
    monkeypatch.setattr(helpers, "get_default_project", lambda: "nexus")
    monkeypatch.setattr(helpers, "get_git_repo", lambda _p: "Ghabs95/nexus-arc")
    monkeypatch.setattr(helpers, "get_project_platform", lambda _p: "github")
    monkeypatch.setattr(helpers, "resolve_git_platform_class", lambda _p: _GitHubDummy)
    monkeypatch.setattr(
        "nexus.core.auth.access_domain.resolve_project_polling_git_token",
        lambda _project_key: ("gho_polling_token", "u-1"),
    )

    platform = helpers.get_git_platform(
        repo="Ghabs95/nexus-arc",
        project_name="nexus",
        token_override=None,
        allow_env_token_fallback=False,
    )

    assert isinstance(platform, _GitHubDummy)
    assert platform.token == "gho_polling_token"


def test_get_git_platform_uses_polling_token_for_gitlab(monkeypatch):
    monkeypatch.setattr(
        helpers,
        "_get_project_config",
        lambda: {"wlbl": {"git_token_var_name": "GITLAB_TOKEN"}},
    )
    monkeypatch.setattr(helpers, "get_default_project", lambda: "wlbl")
    monkeypatch.setattr(helpers, "get_git_repo", lambda _p: "wallible/wlbl-workflow-os")
    monkeypatch.setattr(helpers, "get_project_platform", lambda _p: "gitlab")
    monkeypatch.setattr(helpers, "get_gitlab_base_url", lambda _p: "https://gitlab.com")
    monkeypatch.setattr(helpers, "resolve_git_platform_class", lambda _p: _GitLabDummy)
    monkeypatch.setattr(
        "nexus.core.auth.access_domain.resolve_project_polling_git_token",
        lambda _project_key: ("glpat_polling_token", "u-2"),
    )

    platform = helpers.get_git_platform(
        repo="wallible/wlbl-workflow-os",
        project_name="wlbl",
        token_override=None,
        allow_env_token_fallback=False,
    )

    assert isinstance(platform, _GitLabDummy)
    assert platform.token == "glpat_polling_token"
