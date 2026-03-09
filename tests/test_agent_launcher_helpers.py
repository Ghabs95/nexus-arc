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


def test_launch_next_agent_merges_persisted_and_runtime_exclusions(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    task_file = tmp_path / "issue-113-task.md"
    task_file.write_text("Do work")
    issue_body = f"**Task File:** `{task_file}`\n\nTask details"
    captured: dict[str, object] = {}

    monkeypatch.setattr(agent_launcher, "is_recent_launch", lambda *_a, **_k: False)
    monkeypatch.setattr(
        agent_launcher,
        "_load_issue_body_from_project_repo",
        lambda *_a, **_k: (issue_body, "Ghabs95/nexus-arc", str(task_file)),
    )
    monkeypatch.setattr(agent_launcher, "is_postgres_backend", lambda *_a, **_k: False)
    monkeypatch.setattr(
        agent_launcher,
        "PROJECT_CONFIG",
        {
            "nexus": {
                "workspace": str(tmp_path),
                "agents_dir": "agents",
                "repo": "Ghabs95/nexus-arc",
            }
        },
    )
    monkeypatch.setattr(agent_launcher, "_project_repos", lambda *_a, **_k: ["Ghabs95/nexus-arc"])
    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "Ghabs95/nexus-arc")
    monkeypatch.setattr(
        "nexus.core.state_manager.HostStateManager.get_last_tier_for_issue",
        lambda _issue: "full",
    )
    monkeypatch.setattr(
        "nexus.core.state_manager.HostStateManager.load_launched_agents",
        lambda recent_only=False: {"113": {"exclude_tools": ["codex", "copilot"]}},
    )
    monkeypatch.setattr(agent_launcher, "get_sop_tier_from_issue", lambda *_a, **_k: "full")
    monkeypatch.setattr(
        agent_launcher,
        "build_issue_url",
        lambda _repo, _issue, _cfg: "https://github.com/Ghabs95/nexus-arc/issues/113",
    )

    def _fake_invoke_ai_agent(**kwargs):
        captured.update(kwargs)
        return 4242, "claude"

    monkeypatch.setattr(agent_launcher, "invoke_ai_agent", _fake_invoke_ai_agent)

    pid, tool = agent_launcher.launch_next_agent(
        issue_number="113",
        next_agent="developer",
        trigger_source="dead-agent-retry",
        exclude_tools=["gemini"],
        repo_override="Ghabs95/nexus-arc",
    )

    assert pid == 4242
    assert tool == "claude"
    assert captured.get("exclude_tools") == ["codex", "copilot", "gemini"]
