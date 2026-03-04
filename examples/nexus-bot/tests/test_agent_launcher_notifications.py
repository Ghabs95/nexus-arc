from nexus.core.runtime.agent_launcher import _completed_agent_from_trigger


def test_returns_none_for_system_trigger_sources():
    assert _completed_agent_from_trigger("orphan-recovery") is None
    assert _completed_agent_from_trigger("orphan-timeout-retry") is None
    assert _completed_agent_from_trigger("timeout-retry") is None
    assert _completed_agent_from_trigger("completion-scan") is None
    assert _completed_agent_from_trigger("github_webhook") is None
    assert _completed_agent_from_trigger("manual-recover") == "manual"


def test_returns_agent_name_for_agent_like_trigger():
    assert _completed_agent_from_trigger("Developer") == "developer"
    assert _completed_agent_from_trigger("@reviewer") == "reviewer"
