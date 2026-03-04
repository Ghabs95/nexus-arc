"""Tests for issue-level tool exclusion persistence in agent launcher."""

import time
from types import SimpleNamespace


def test_invoke_persists_gemini_exclusion_when_rate_limited(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _FakeOrchestrator:
        def __init__(self):
            self._rate_limits = {"gemini": {"until": time.time() + 300, "retries": 1}}

        def invoke_agent(self, **_kwargs):
            return 1234, SimpleNamespace(value="copilot")

    state = {}

    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _FakeOrchestrator())
    monkeypatch.setattr(agent_launcher, "record_agent_launch", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(agent_launcher.AuditStore, "audit_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent_launcher.HostStateManager, "load_launched_agents", lambda **_kwargs: dict(state)
    )
    monkeypatch.setattr(
        agent_launcher.HostStateManager, "save_launched_agents", lambda data: state.update(data)
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir="/tmp/agents",
        workspace_dir="/tmp/workspace",
        issue_url="https://github.com/acme/repo/issues/55",
        tier_name="full",
        task_content="task",
        continuation=True,
        agent_type="writer",
        project_name="nexus",
    )

    assert pid == 1234
    assert tool == "copilot"
    assert state["55"]["exclude_tools"] == ["gemini"]


def test_tool_unavailable_persists_gemini_exclusion(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _FakeOrchestrator:
        def __init__(self):
            self._rate_limits = {"gemini": {"until": time.time() + 300, "retries": 1}}

        def invoke_agent(self, **_kwargs):
            raise agent_launcher.ToolUnavailableError(
                "All AI tools exhausted. Tried: ['gemini(rate-limited)']"
            )

    state = {}

    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _FakeOrchestrator())
    monkeypatch.setattr(agent_launcher.AuditStore, "audit_log", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent_launcher.HostStateManager, "load_launched_agents", lambda **_kwargs: dict(state)
    )
    monkeypatch.setattr(
        agent_launcher.HostStateManager, "save_launched_agents", lambda data: state.update(data)
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir="/tmp/agents",
        workspace_dir="/tmp/workspace",
        issue_url="https://github.com/acme/repo/issues/55",
        tier_name="full",
        task_content="task",
        continuation=True,
        agent_type="writer",
        project_name="nexus",
    )

    assert pid is None
    assert tool is None
    assert state["55"]["exclude_tools"] == ["gemini"]


def test_log_indicates_any_quota_failure_detects_session_summary():
    from nexus.core.runtime import agent_launcher

    text = (
        "402 You have no quota\n\n"
        "Total usage est: 0 Premium requests\n"
        "API time spent: 0s\n"
        "Total session time: 4s\n"
    )
    assert agent_launcher._log_indicates_any_quota_failure(text) is True


def test_log_indicates_any_quota_failure_false_without_summary():
    from nexus.core.runtime import agent_launcher

    text = "402 You have no quota"
    assert agent_launcher._log_indicates_any_quota_failure(text) is False
