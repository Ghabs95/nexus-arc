from types import SimpleNamespace


def test_get_sop_tier_from_issue_uses_sync_bridge(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _Platform:
        async def get_issue(self, _issue_number):
            return SimpleNamespace(labels=["workflow:full"])

    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "Ghabs95/nexus-arc")
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_git_platform",
        lambda *_args, **_kwargs: _Platform(),
    )
    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)
    monkeypatch.setattr(
        agent_launcher,
        "_run_coro_sync",
        lambda factory: SimpleNamespace(labels=["workflow:full"]),
    )

    def _unexpected_asyncio_run(*_args, **_kwargs):
        raise AssertionError("asyncio.run should not be called")

    monkeypatch.setattr(agent_launcher.asyncio, "run", _unexpected_asyncio_run)

    tier = agent_launcher.get_sop_tier_from_issue("110", project="nexus")
    assert tier == "full"


def test_resolve_requester_token_for_issue_falls_back_to_issue_url(monkeypatch):
    from nexus.core.runtime import agent_launcher

    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: True)
    monkeypatch.setattr(agent_launcher, "get_issue_requester", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_launcher, "get_issue_requester_by_url", lambda _url: "user-123")
    monkeypatch.setattr(
        agent_launcher,
        "build_execution_env",
        lambda _nexus_id: ({"GITHUB_TOKEN": "gho_test_token"}, None),
    )
    monkeypatch.setattr(agent_launcher, "get_project_platform", lambda _project: "github")

    token = agent_launcher._resolve_requester_token_for_issue(
        issue_number="110",
        repo="Ghabs95/nexus-arc",
        project_name="nexus",
    )

    assert token == "gho_test_token"
