from nexus.core.git.direct_issue_plugin_service import get_direct_issue_plugin


class _PluginFactory:
    def __init__(self):
        self.calls = []

    def __call__(self, profile, overrides, cache_key):
        self.calls.append((profile, overrides, cache_key))
        return {"profile": profile, "cache_key": cache_key, "overrides": overrides}


def test_get_direct_issue_plugin_uses_transport_scoped_cache_key(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")
    factory = _PluginFactory()
    plugin = get_direct_issue_plugin(repo="ghabs-org/nexus-arc", get_profiled_plugin=factory)

    assert plugin["profile"] == "git_agent_launcher"
    assert len(factory.calls) == 1
    assert factory.calls[0][0] == "git_agent_launcher"
    assert factory.calls[0][2] == "git:direct:git_agent_launcher:cli:ghabs-org/nexus-arc"


def test_get_direct_issue_plugin_scopes_cache_key_by_requester(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "api")
    factory = _PluginFactory()
    plugin = get_direct_issue_plugin(
        repo="ghabs-org/nexus-arc",
        get_profiled_plugin=factory,
        requester_nexus_id="nx-123",
    )

    assert plugin["profile"] == "git_agent_launcher"
    assert len(factory.calls) == 1
    assert factory.calls[0][1]["requester_nexus_id"] == "nx-123"
    assert factory.calls[0][2] == "git:direct:git_agent_launcher:api:ghabs-org/nexus-arc:nx-123"


def test_get_direct_issue_plugin_uses_gitlab_profile_for_gitlab_project(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "api")
    factory = _PluginFactory()
    monkeypatch.setattr(
        "nexus.core.git.direct_issue_plugin_service._canonical_project_key",
        lambda _project_name: "example-org",
    )
    monkeypatch.setattr(
        "nexus.core.git.direct_issue_plugin_service._resolve_project_key_from_repo",
        lambda _repo: None,
    )
    monkeypatch.setattr(
        "nexus.core.git.direct_issue_plugin_service._resolve_project_platform",
        lambda _project_key: ("gitlab", {"gitlab_base_url": "https://gitlab.com"}),
    )

    plugin = get_direct_issue_plugin(
        repo="example-org/example-project",
        get_profiled_plugin=factory,
        project_name="example-org",
    )

    assert plugin["profile"] == "gitlab_agent_launcher"
    assert len(factory.calls) == 1
    assert factory.calls[0][0] == "gitlab_agent_launcher"
    assert factory.calls[0][1]["gitlab_base_url"] == "https://gitlab.com"
    assert (
        factory.calls[0][2]
        == "git:direct:gitlab_agent_launcher:api:example-org/example-project"
    )
