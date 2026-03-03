from services.project.project_issue_command_deps_service import default_issue_url
from services.project.project_issue_command_deps_service import get_issue_details
from services.project.project_issue_command_deps_service import project_issue_url
from services.project.project_issue_command_deps_service import project_repo


def test_project_repo_resolves_from_project_config():
    config = {"nexus": {"git_repo": "Ghabs95/nexus-arc"}}

    def _resolve_repo(cfg, default):
        return cfg.get("git_repo", default)

    repo = project_repo(
        project_key="nexus",
        project_config=config,
        default_repo="Ghabs95/fallback",
        resolve_repo=_resolve_repo,
    )
    assert repo == "Ghabs95/nexus-arc"


def test_project_issue_url_uses_build_issue_url_with_resolved_repo():
    config = {"nexus": {"git_repo": "Ghabs95/nexus-arc"}}

    def _resolve_repo(cfg, default):
        return cfg.get("git_repo", default)

    def _build_issue_url(repo, issue_num, cfg):
        assert cfg == config["nexus"]
        return f"https://github.com/{repo}/issues/{issue_num}"

    url = project_issue_url(
        project_key="nexus",
        issue_num="123",
        project_config=config,
        default_repo="Ghabs95/fallback",
        resolve_repo=_resolve_repo,
        build_issue_url=_build_issue_url,
    )
    assert url.endswith("/Ghabs95/nexus-arc/issues/123")


def test_default_issue_url_falls_back_to_default_repo_when_resolver_fails():
    url = default_issue_url(
        issue_num="999",
        default_repo="Ghabs95/fallback",
        get_default_project=lambda: (_ for _ in ()).throw(RuntimeError("boom")),
        project_issue_url_fn=lambda _p, _i: "unreachable",
    )
    assert url == "https://github.com/Ghabs95/fallback/issues/999"


def test_get_issue_details_queries_plugin_with_expected_fields():
    captured = {"issue": None, "fields": None}

    class _Plugin:
        def get_issue(self, issue, fields):
            captured["issue"] = issue
            captured["fields"] = fields
            return {"number": issue}

    result = get_issue_details(
        issue_num="42",
        repo=None,
        default_repo="Ghabs95/nexus-arc",
        get_direct_issue_plugin=lambda _repo: _Plugin(),
        logger=type("_L", (), {"error": lambda *args, **kwargs: None})(),
    )

    assert result == {"number": "42"}
    assert captured["issue"] == "42"
    assert captured["fields"] == ["number", "title", "state", "labels", "body", "updatedAt"]
