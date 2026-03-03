from services.git.direct_issue_plugin_service import get_direct_issue_plugin


class _PluginFactory:
    def __init__(self, should_fail_primary: bool = False):
        self.should_fail_primary = should_fail_primary
        self.calls = []

    def __call__(self, profile, overrides, cache_key):
        self.calls.append((profile, overrides, cache_key))
        if profile == "git_telegram" and self.should_fail_primary:
            raise RuntimeError("primary unavailable")
        return {"profile": profile, "cache_key": cache_key, "overrides": overrides}


def test_get_direct_issue_plugin_uses_neutral_cache_key():
    factory = _PluginFactory()
    plugin = get_direct_issue_plugin(repo="Ghabs95/nexus-arc", get_profiled_plugin=factory)

    assert plugin["profile"] == "git_telegram"
    assert factory.calls[0][2] == "git:direct:Ghabs95/nexus-arc"


def test_get_direct_issue_plugin_falls_back_when_primary_fails():
    factory = _PluginFactory(should_fail_primary=True)
    plugin = get_direct_issue_plugin(repo="Ghabs95/nexus-arc", get_profiled_plugin=factory)

    assert plugin["profile"] == "git_agent_launcher"
    assert len(factory.calls) == 2
    assert factory.calls[0][0] == "git_telegram"
    assert factory.calls[1][0] == "git_agent_launcher"
    assert factory.calls[1][2] == "git:direct:Ghabs95/nexus-arc"
