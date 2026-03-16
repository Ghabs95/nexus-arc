from types import SimpleNamespace


def test_run_coro_sync_closes_coroutine_on_asyncio_run_failure(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _CoroLike:
        def __init__(self):
            self.closed = False

        def __await__(self):
            yield
            return None

        def close(self):
            self.closed = True

    leaked = _CoroLike()

    monkeypatch.setattr(agent_launcher.asyncio, "get_running_loop", lambda: (_ for _ in ()).throw(RuntimeError("no loop")))

    def _raise_runtime(_candidate):
        raise RuntimeError("boom")

    monkeypatch.setattr(agent_launcher.asyncio, "run", _raise_runtime)

    value = agent_launcher._run_coro_sync(lambda: leaked)

    assert value is None
    assert leaked.closed is True


def test_get_sop_tier_from_issue_uses_sync_bridge(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _Platform:
        async def get_issue(self, _issue_number):
            return SimpleNamespace(labels=["workflow:full"])

    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "acme-org/sample-repo")
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_git_platform",
        lambda *_args, **_kwargs: _Platform(),
    )
    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)

    tier = agent_launcher.get_sop_tier_from_issue("110", project="nexus")
    assert tier == "full"


def test_get_sop_tier_from_issue_does_not_call_asyncio_run_directly(monkeypatch):
    from nexus.core.runtime import agent_launcher

    class _Platform:
        def get_issue(self, _issue_number):
            return SimpleNamespace(labels=["workflow:shortened"])

    bridge_calls = {"count": 0}

    def _fake_sync_bridge(coro_factory):
        bridge_calls["count"] += 1
        return coro_factory()

    monkeypatch.setattr(agent_launcher, "_run_coro_sync", _fake_sync_bridge)
    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "acme-org/sample-repo")
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_git_platform",
        lambda *_args, **_kwargs: _Platform(),
    )
    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)

    tier = agent_launcher.get_sop_tier_from_issue("113", project="nexus")

    assert tier == "shortened"
    assert bridge_calls["count"] == 1


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
        repo="acme-org/sample-repo",
        project_name="nexus",
    )

    assert token == "gho_test_token"


def test_invoke_ai_agent_pauses_and_notifies_when_worktree_provision_fails(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher
    from nexus.core.workspace import WorktreeProvisionError

    pause_calls = {"count": 0}
    alerts: list[str] = []
    audits: list[tuple[int, str, str]] = []

    class _Orchestrator:
        def invoke_agent(self, **kwargs):  # noqa: ANN003
            raise AssertionError("invoke_agent should not be called when worktree setup fails")

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("", 0))
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: False)
    monkeypatch.setattr(agent_launcher, "_resolve_worktree_base_repo", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(agent_launcher, "_is_git_repo", lambda _path: True)
    monkeypatch.setattr(agent_launcher, "_load_issue_body_from_project_repo", lambda *_a, **_k: ("", "", ""))
    monkeypatch.setattr(agent_launcher, "_derive_issue_branch_name", lambda **_k: "nexus/issue-42")
    monkeypatch.setattr(agent_launcher, "get_repo_branch", lambda *_a, **_k: "develop")
    monkeypatch.setattr(agent_launcher, "_run_coro_sync", lambda coro_factory: coro_factory())

    def _pause_workflow(issue_number, reason):  # noqa: ANN001
        pause_calls["count"] += 1
        assert str(issue_number) == "42"
        assert "worktree" in str(reason).lower()
        return True

    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.pause_workflow",
        _pause_workflow,
    )

    monkeypatch.setattr(
        agent_launcher,
        "emit_alert",
        lambda message, **_kwargs: alerts.append(str(message)),
    )
    monkeypatch.setattr(
        agent_launcher.AuditStore,
        "audit_log",
        lambda issue, event, message: audits.append((issue, event, str(message))),
    )

    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.provision_worktree",
        lambda *_a, **_k: (_ for _ in ()).throw(
            WorktreeProvisionError("no isolated worktree available")
        ),
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://github.com/acme-org/sample-repo/issues/42",
        tier_name="full",
        task_content="test",
        continuation=False,
        agent_type="developer",
        project_name="nexus",
        log_subdir="nexus",
    )

    assert pid is None
    assert tool is None
    assert pause_calls["count"] == 1
    assert alerts and "Could not create isolated worktree" in alerts[0]
    assert audits and audits[0][1] == "WORKTREE_PROVISION_FAILED"


def test_invoke_ai_agent_sanitizes_deprecated_helper_scripts(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    sanitized: dict[str, str] = {}

    class _Orchestrator:
        def invoke_agent(self, **kwargs):  # noqa: ANN003
            return 1234, "gemini"

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("develop", 2))
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: False)
    monkeypatch.setattr(agent_launcher, "_resolve_worktree_base_repo", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(agent_launcher, "_is_git_repo", lambda _path: True)
    monkeypatch.setattr(agent_launcher, "_load_issue_body_from_project_repo", lambda *_a, **_k: ("", "", ""))
    monkeypatch.setattr(agent_launcher, "_derive_issue_branch_name", lambda **_k: "nexus/issue-42")
    monkeypatch.setattr(agent_launcher, "get_repo_branch", lambda *_a, **_k: "develop")
    monkeypatch.setattr(agent_launcher, "_run_coro_sync", lambda coro_factory: coro_factory())

    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.provision_worktree",
        lambda *_a, **_k: str(tmp_path),
    )
    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.sanitize_worktree_helper_scripts",
        lambda path: sanitized.setdefault("path", str(path)) and [str(tmp_path / "post_comments.py")],
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://github.com/acme-org/sample-repo/issues/42",
        tier_name="full",
        task_content="test",
        continuation=False,
        agent_type="developer",
        project_name="nexus",
        log_subdir="nexus",
    )

    assert pid == 1234
    assert tool == "gemini"
    assert sanitized.get("path") == str(tmp_path)


def test_resolve_worktree_base_repo_supports_gitlab_issue_url(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    workspace_root = tmp_path / "acme-org"
    repo_dir = workspace_root / "sample-workflow"
    repo_dir.mkdir(parents=True)

    monkeypatch.setattr(
        agent_launcher,
        "_is_git_repo",
        lambda path: str(path) == str(repo_dir),
    )

    resolved = agent_launcher._resolve_worktree_base_repo(
        str(workspace_root),
        "https://gitlab.com/acme-org/sample-workflow/-/issues/1",
    )

    assert resolved == str(repo_dir)


def test_resolve_step_requires_worktree_by_step_id(tmp_path):
    from nexus.core.runtime import agent_launcher

    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        "steps:\n"
        "  - id: triage-routing\n"
        "    agent_type: triage\n"
        "    requires_worktree: false\n"
        "  - id: implement\n"
        "    agent_type: developer\n",
        encoding="utf-8",
    )

    value = agent_launcher._resolve_step_requires_worktree(
        workflow_path=str(workflow),
        tier_name="full",
        workflow_name="new_feature",
        step_id="triage-routing",
        step_num=1,
    )

    assert value is False


def test_resolve_step_requires_worktree_by_step_num(tmp_path):
    from nexus.core.runtime import agent_launcher

    workflow = tmp_path / "workflow.yaml"
    workflow.write_text(
        "steps:\n"
        "  - id: triage-routing\n"
        "    agent_type: triage\n"
        "  - id: implement\n"
        "    agent_type: developer\n"
        "    requires_worktree: true\n",
        encoding="utf-8",
    )

    value = agent_launcher._resolve_step_requires_worktree(
        workflow_path=str(workflow),
        tier_name="full",
        workflow_name="new_feature",
        step_id="unknown-step",
        step_num=2,
    )

    assert value is True


def test_invoke_ai_agent_skips_worktree_provision_for_triage(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    calls = {"provisioned": 0}

    class _Orchestrator:
        def invoke_agent(self, **kwargs):  # noqa: ANN003
            return 3210, "gemini"

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("triage", 1))
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: False)
    monkeypatch.setattr(agent_launcher, "_resolve_worktree_base_repo", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(agent_launcher, "_is_git_repo", lambda _path: False)
    monkeypatch.setattr(
        agent_launcher,
        "_load_issue_body_from_project_repo",
        lambda *_a, **_k: ().throw(AssertionError("triage should not load issue branch")),
    )
    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.provision_worktree",
        lambda *_a, **_k: calls.__setitem__("provisioned", calls["provisioned"] + 1),
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://gitlab.com/acme-org/sample-workflow/-/issues/1",
        tier_name="full",
        task_content="triage task",
        continuation=False,
        agent_type="triage",
        project_name="sample",
        log_subdir="sample",
    )

    assert pid == 3210
    assert tool == "gemini"
    assert calls["provisioned"] == 0


def test_invoke_ai_agent_honors_step_worktree_override_for_triage(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    calls = {"provisioned": 0}

    class _Orchestrator:
        def invoke_agent(self, **kwargs):  # noqa: ANN003
            return 7654, "gemini"

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("triage-routing", 1))
    monkeypatch.setattr(agent_launcher, "_resolve_step_requires_worktree", lambda **_kwargs: True)
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: False)
    monkeypatch.setattr(agent_launcher, "_resolve_worktree_base_repo", lambda *_a, **_k: str(tmp_path))
    monkeypatch.setattr(agent_launcher, "_is_git_repo", lambda _path: True)
    monkeypatch.setattr(agent_launcher, "_load_issue_body_from_project_repo", lambda *_a, **_k: ("", "", ""))
    monkeypatch.setattr(agent_launcher, "_derive_issue_branch_name", lambda **_k: "feat/issue-1")
    monkeypatch.setattr(agent_launcher, "_run_coro_sync", lambda coro_factory: coro_factory())
    monkeypatch.setattr(agent_launcher, "get_repo_branch", lambda *_a, **_k: "main")
    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.provision_worktree",
        lambda *_a, **_k: calls.__setitem__("provisioned", calls["provisioned"] + 1) or str(tmp_path),
    )
    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.sanitize_worktree_helper_scripts",
        lambda _path: [],
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://gitlab.com/acme-org/sample-workflow/-/issues/1",
        tier_name="full",
        task_content="triage task",
        continuation=False,
        agent_type="triage",
        project_name="sample",
        log_subdir="sample",
    )

    assert pid == 7654
    assert tool == "gemini"
    assert calls["provisioned"] == 1


def test_invoke_ai_agent_provisions_project_repos_with_repo_specific_base_branches(
    monkeypatch, tmp_path
):
    from nexus.core.runtime import agent_launcher

    captured: dict[str, str] = {}
    provision_calls: list[tuple[str, str, str | None]] = []
    cosmos_dir = tmp_path / "example-shared"
    workflow_dir = tmp_path / "example-project"
    cosmos_dir.mkdir()
    workflow_dir.mkdir()

    class _Orchestrator:
        def invoke_agent(self, **kwargs):  # noqa: ANN003
            captured["workspace_dir"] = str(kwargs.get("workspace_dir"))
            return 9911, "gemini"

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(
        agent_launcher, "_resolve_issue_step_context", lambda _url: ("new_feature_workflow__implementation", 7)
    )
    monkeypatch.setattr(agent_launcher, "_resolve_step_requires_worktree", lambda **_kwargs: True)
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: False)
    monkeypatch.setattr(
        agent_launcher,
        "_resolve_worktree_base_repo",
        lambda *_a, **_k: str(workflow_dir),
    )
    monkeypatch.setattr(agent_launcher, "_is_git_repo", lambda _path: True)
    monkeypatch.setattr(agent_launcher, "_load_issue_body_from_project_repo", lambda *_a, **_k: ("", "", ""))
    monkeypatch.setattr(agent_launcher, "_derive_issue_branch_name", lambda **_k: "feat/issue-1")
    monkeypatch.setattr(agent_launcher, "_run_coro_sync", lambda coro_factory: coro_factory())
    monkeypatch.setattr(
        agent_launcher,
        "get_repos",
        lambda _project: ["example-org/example-shared", "example-org/example-project"],
    )
    monkeypatch.setattr(
        agent_launcher,
        "_resolve_git_dir_for_repo",
        lambda *, project_name, repo_name, project_config, base_dir: (
            str(cosmos_dir) if repo_name == "example-org/example-shared" else str(workflow_dir)
        ),
    )
    monkeypatch.setattr(
        agent_launcher,
        "get_repo_branch",
        lambda _project, repo_slug: "develop" if repo_slug == "example-org/example-shared" else "main",
    )

    def _provision(repo_dir, issue_num, branch_name, start_ref=None):  # noqa: ANN001
        provision_calls.append((str(repo_dir), str(branch_name), str(start_ref) if start_ref else None))
        repo_name = str(repo_dir).rstrip("/").split("/")[-1]
        return str(tmp_path / f".provisioned-{repo_name}-issue-{issue_num}")

    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.provision_worktree",
        _provision,
    )
    monkeypatch.setattr(
        "nexus.core.workspace.WorkspaceManager.sanitize_worktree_helper_scripts",
        lambda _path: [],
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://gitlab.com/example-org/example-project/-/issues/1",
        tier_name="full",
        task_content="implementation task",
        continuation=False,
        agent_type="developer",
        project_name="example-org",
        log_subdir="example-org",
    )

    assert pid == 9911
    assert tool == "gemini"
    assert len(provision_calls) == 2
    assert any(call[0] == str(cosmos_dir) and call[2] == "origin/develop" for call in provision_calls)
    assert any(call[0] == str(workflow_dir) and call[2] == "origin/main" for call in provision_calls)
    assert captured.get("workspace_dir") == str(tmp_path / ".provisioned-example-project-issue-1")


def test_invoke_ai_agent_rejects_explicit_requester_mismatch(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    class _Orchestrator:
        def invoke_agent(self, **_kwargs):  # noqa: ANN003
            raise AssertionError("invoke_agent should not be called when requester mismatches binding")

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("", 0))
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: True)
    monkeypatch.setattr(
        agent_launcher,
        "_lookup_bound_issue_requester_nexus_id",
        lambda _url: "nexus-owner-1",
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://github.com/acme-org/sample-repo/issues/42",
        tier_name="full",
        task_content="test",
        continuation=False,
        agent_type="developer",
        project_name="nexus",
        log_subdir="nexus",
        requester_nexus_id="nexus-owner-2",
    )

    assert pid is None
    assert tool is None


def test_invoke_ai_agent_rejects_when_issue_not_bound_in_auth_mode(monkeypatch, tmp_path):
    from nexus.core.runtime import agent_launcher

    class _Orchestrator:
        def invoke_agent(self, **_kwargs):  # noqa: ANN003
            raise AssertionError("invoke_agent should not be called when issue binding is missing")

    monkeypatch.setattr(agent_launcher, "_get_launch_policy_plugin", lambda: None)
    monkeypatch.setattr(agent_launcher, "_resolve_workflow_path", lambda _project: None)
    monkeypatch.setattr(agent_launcher, "_resolve_issue_step_context", lambda _url: ("", 0))
    monkeypatch.setattr(agent_launcher, "_ensure_agent_definition", lambda *_a, **_k: True)
    monkeypatch.setattr(agent_launcher, "get_orchestrator", lambda _cfg: _Orchestrator())
    monkeypatch.setattr(agent_launcher, "auth_enabled", lambda: True)
    monkeypatch.setattr(
        agent_launcher,
        "_lookup_bound_issue_requester_nexus_id",
        lambda _url: None,
    )

    pid, tool = agent_launcher.invoke_ai_agent(
        agents_dir=str(tmp_path / "agents"),
        workspace_dir=str(tmp_path),
        issue_url="https://github.com/acme-org/sample-repo/issues/99",
        tier_name="full",
        task_content="test",
        continuation=False,
        agent_type="developer",
        project_name="nexus",
        log_subdir="nexus",
        requester_nexus_id="nexus-owner-99",
    )

    assert pid is None
    assert tool is None


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
        lambda *_a, **_k: (issue_body, "acme-org/sample-repo", str(task_file)),
    )
    monkeypatch.setattr(agent_launcher, "is_postgres_backend", lambda *_a, **_k: False)
    monkeypatch.setattr(
        agent_launcher,
        "PROJECT_CONFIG",
        {
            "nexus": {
                "workspace": str(tmp_path),
                "agents_dir": "agents",
                "repo": "acme-org/sample-repo",
            }
        },
    )
    monkeypatch.setattr(agent_launcher, "_project_repos", lambda *_a, **_k: ["acme-org/sample-repo"])
    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project: "acme-org/sample-repo")
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
        lambda _repo, _issue, _cfg: "https://github.com/acme-org/sample-repo/issues/113",
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
        repo_override="acme-org/sample-repo",
        requester_nexus_id="nexus-113",
    )

    assert pid == 4242
    assert tool == "claude"
    assert captured.get("exclude_tools") == ["codex", "copilot", "gemini"]
    assert captured.get("requester_nexus_id") == "nexus-113"


def test_extract_completion_payload_from_log_text_parses_curl_payload():
    from nexus.core.runtime import agent_launcher

    log_text = (
        "analysis done\n"
        "```bash\n"
        "curl -s -X POST http://webhook:8081/api/v1/completion \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  -d '{\n"
        "    \"issue_number\": \"1\",\n"
        "    \"agent_type\": \"triage\",\n"
        "    \"step_id\": \"dispatch\",\n"
        "    \"step_num\": 2,\n"
        "    \"status\": \"complete\",\n"
        "    \"summary\": \"routed to ceo\",\n"
        "    \"next_agent\": \"ceo\",\n"
        "    \"comment_markdown\": \"## Dispatch complete\"\n"
        "  }'\n"
        "```\n"
    )

    payload = agent_launcher._extract_completion_payload_from_log_text(
        log_text,
        issue_num="1",
        agent_type="triage",
    )

    assert isinstance(payload, dict)
    assert payload["issue_number"] == "1"
    assert payload["agent_type"] == "triage"
    assert payload["step_id"] == "dispatch"
    assert payload["step_num"] == 2
    assert payload["next_agent"] == "ceo"


def test_extract_completion_payload_from_log_text_handles_shell_escaped_apostrophe():
    from nexus.core.runtime import agent_launcher

    log_text = (
        "```bash\n"
        "curl -s -X POST http://webhook:8081/api/v1/completion \\\n"
        "  -H \"Content-Type: application/json\" \\\n"
        "  -d '{\n"
        "    \"issue_number\": \"1\",\n"
        "    \"agent_type\": \"triage\",\n"
        "    \"step_id\": \"dispatch\",\n"
        "    \"step_num\": 2,\n"
        "    \"summary\": \"routed\",\n"
        "    \"next_agent\": \"ceo\",\n"
        "    \"comment_markdown\": \"Founder'\\''s check\"\n"
        "  }'\n"
        "```\n"
    )

    payload = agent_launcher._extract_completion_payload_from_log_text(
        log_text,
        issue_num="1",
        agent_type="triage",
    )

    assert isinstance(payload, dict)
    assert payload["step_id"] == "dispatch"
    assert payload["step_num"] == 2
    assert payload["next_agent"] == "ceo"


def test_extract_completion_payload_from_log_text_ignores_prompt_template_placeholder():
    from nexus.core.runtime import agent_launcher

    log_text = """{"issue_number":"1","agent_type":"triage","step_id":"dispatch","step_num":2,"status":"complete","summary":"routed to ceo","next_agent":"ceo","comment_markdown":"## Dispatch Complete"}
```bash
curl -s -X POST http://webhook:8081/api/v1/completion \
  -H "Content-Type: application/json" \
  -d '{
    "issue_number": "1",
    "agent_type": "triage",
    "step_id": "dispatch",
    "step_num": 2,
    "status": "complete",
    "summary": "<one-line summary of what you did>",
    "key_findings": ["<finding 1>", "<finding 2>"],
    "next_agent": "<agent_type from workflow steps — NOT the step id or display name>",
    "comment_markdown": "## 🔍 <Step Name> Complete — triage

**Step ID:** `dispatch`
**Step Num:** 2

- Finding 1
- Finding 2"
  }'
```
"""

    payload = agent_launcher._extract_completion_payload_from_log_text(
        log_text,
        issue_num="1",
        agent_type="triage",
    )

    assert isinstance(payload, dict)
    assert payload["summary"] == "routed to ceo"
    assert payload["next_agent"] == "ceo"
    assert payload["comment_markdown"] == "## Dispatch Complete"


def test_recover_completion_from_agent_log_persists_payload(monkeypatch, tmp_path):
    import asyncio
    import inspect

    from nexus.core.runtime import agent_launcher

    log_file = tmp_path / "gemini_1_20260310_115119.log"
    log_file.write_text(
        (
            "```bash\n"
            "curl -s -X POST http://webhook:8081/api/v1/completion \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\n"
            "    \"issue_number\": \"1\",\n"
            "    \"agent_type\": \"triage\",\n"
            "    \"step_id\": \"dispatch\",\n"
            "    \"step_num\": 2,\n"
            "    \"summary\": \"routed\",\n"
            "    \"next_agent\": \"ceo\",\n"
            "    \"comment_markdown\": \"## Dispatch complete\"\n"
            "  }'\n"
            "```\n"
        ),
        encoding="utf-8",
    )

    captured: dict[str, object] = {}
    posted: dict[str, object] = {"comments": [], "added": []}

    class _FakeStore:
        def __init__(self, *, backend, storage, base_dir, nexus_dir):
            captured["backend"] = backend
            captured["storage"] = storage
            captured["base_dir"] = base_dir
            captured["nexus_dir"] = nexus_dir

        def save(self, issue_number, agent_type, data):
            captured["issue_number"] = issue_number
            captured["agent_type"] = agent_type
            captured["data"] = dict(data)
            return "dedup-recovered-1"

    monkeypatch.setattr(agent_launcher, "is_postgres_backend", lambda _backend: True)
    monkeypatch.setattr("nexus.core.completion_store.CompletionStore", _FakeStore)
    monkeypatch.setattr(
        "nexus.core.integrations.workflow_state_factory.get_storage_backend",
        lambda: "fake-storage",
    )
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_workflow_status",
        lambda _issue_num: {"workflow_id": "example-org-1-full"},
    )

    class _Platform:
        async def get_comments(self, _issue_num):
            posted["comments"].append(_issue_num)
            return []

        async def add_comment(self, issue_num, body):
            posted["added"].append((issue_num, body))
            return {"id": 123}

    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_launcher, "_get_git_platform_client", lambda *_a, **_k: _Platform())
    monkeypatch.setattr(
        agent_launcher,
        "_run_coro_sync",
        lambda coro_factory: (
            asyncio.run(value)
            if inspect.isawaitable(value := coro_factory())
            else value
        ),
    )
    monkeypatch.setattr(agent_launcher, "get_nexus_dir_name", lambda: ".nexus")
    monkeypatch.setattr(agent_launcher, "_extract_repo_from_issue_url", lambda _url: "example-org/example-project")

    dedup = agent_launcher._recover_completion_from_agent_log(
        issue_num="1",
        agent_type="triage",
        workspace_dir=str(tmp_path),
        project_key="example-org",
        tool_name="gemini",
        log_path=str(log_file),
        issue_url="https://gitlab.com/example-org/example-project/-/issues/1",
    )

    assert dedup == "dedup-recovered-1"
    assert captured["backend"] == "postgres"
    assert captured["issue_number"] == "1"
    assert captured["agent_type"] == "triage"
    data = captured["data"]
    assert isinstance(data, dict)
    assert data["workflow_id"] == "example-org-1-full"
    assert data["step_id"] == "dispatch"
    assert data["step_num"] == 2
    assert posted["comments"] == ["1"]
    assert len(posted["added"]) == 1


def test_recover_completion_from_agent_log_normalizes_double_escaped_comment_markdown(
    monkeypatch, tmp_path
):
    import asyncio
    import inspect

    from nexus.core.runtime import agent_launcher

    log_file = tmp_path / "gemini_1_20260310_115119.log"
    log_file.write_text(
        (
            "```bash\n"
            "curl -s -X POST http://webhook:8081/api/v1/completion \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\n"
            "    \"issue_number\": \"1\",\n"
            "    \"agent_type\": \"triage\",\n"
            "    \"step_id\": \"dispatch\",\n"
            "    \"step_num\": 2,\n"
            "    \"summary\": \"routed\",\n"
            "    \"next_agent\": \"ceo\",\n"
            "    \"comment_markdown\": \"## Dispatch Complete — triage\\\\n\\\\n**Step ID:** `dispatch`\\\\n**Step Num:** 2\\\\n\\\\nReady for **@Ceo**\"\n"
            "  }'\n"
            "```\n"
        ),
        encoding="utf-8",
    )

    class _FakeStore:
        def __init__(self, *, backend, storage, base_dir, nexus_dir):
            pass

        def save(self, issue_number, agent_type, data):
            return "dedup-recovered-1"

    posted: dict[str, object] = {"comments": [], "added": []}

    class _Platform:
        async def get_comments(self, _issue_num):
            posted["comments"].append(_issue_num)
            return []

        async def add_comment(self, issue_num, body):
            posted["added"].append((issue_num, body))
            return {"id": 123}

    monkeypatch.setattr(agent_launcher, "is_postgres_backend", lambda _backend: True)
    monkeypatch.setattr("nexus.core.completion_store.CompletionStore", _FakeStore)
    monkeypatch.setattr(
        "nexus.core.integrations.workflow_state_factory.get_storage_backend",
        lambda: "fake-storage",
    )
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_workflow_status",
        lambda _issue_num: {"workflow_id": "example-org-1-full"},
    )
    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_launcher, "_get_git_platform_client", lambda *_a, **_k: _Platform())
    monkeypatch.setattr(
        agent_launcher,
        "_run_coro_sync",
        lambda coro_factory: (
            asyncio.run(value)
            if inspect.isawaitable(value := coro_factory())
            else value
        ),
    )
    monkeypatch.setattr(agent_launcher, "get_nexus_dir_name", lambda: ".nexus")
    monkeypatch.setattr(agent_launcher, "_extract_repo_from_issue_url", lambda _url: "example-org/example-project")

    dedup = agent_launcher._recover_completion_from_agent_log(
        issue_num="1",
        agent_type="triage",
        workspace_dir=str(tmp_path),
        project_key="example-org",
        tool_name="gemini",
        log_path=str(log_file),
        issue_url="https://gitlab.com/example-org/example-project/-/issues/1",
    )

    assert dedup == "dedup-recovered-1"
    assert posted["comments"] == ["1"]
    assert posted["added"] == [
        (
            "1",
            "## Dispatch Complete — triage\n\n**Step ID:** `dispatch`\n**Step Num:** 2\n\nReady for **@Ceo**",
        )
    ]


def test_recover_completion_from_agent_log_skips_duplicate_comment(monkeypatch, tmp_path):
    import asyncio
    import inspect

    from nexus.core.runtime import agent_launcher

    log_file = tmp_path / "gemini_1_20260310_115119.log"
    log_file.write_text(
        (
            "```bash\n"
            "curl -s -X POST http://webhook:8081/api/v1/completion \\\n"
            "  -H \"Content-Type: application/json\" \\\n"
            "  -d '{\n"
            "    \"issue_number\": \"1\",\n"
            "    \"agent_type\": \"triage\",\n"
            "    \"step_id\": \"dispatch\",\n"
            "    \"step_num\": 2,\n"
            "    \"summary\": \"routed\",\n"
            "    \"next_agent\": \"ceo\",\n"
            "    \"comment_markdown\": \"## Dispatch Complete — triage\\n\\n**Step ID:** `dispatch`\\n**Step Num:** 2\\n\\nReady for **@Ceo**\"\n"
            "  }'\n"
            "```\n"
        ),
        encoding="utf-8",
    )

    class _FakeStore:
        def __init__(self, *, backend, storage, base_dir, nexus_dir):
            pass

        def save(self, issue_number, agent_type, data):
            return "dedup-recovered-1"

    posted: dict[str, object] = {"added": []}

    class _Platform:
        async def get_comments(self, _issue_num):
            return [
                {
                    "body": (
                        "## Dispatch Complete — triage\n\n"
                        "**Step ID:** `dispatch`\n"
                        "**Step Num:** 2\n\n"
                        "Ready for **@Ceo**"
                    )
                }
            ]

        async def add_comment(self, issue_num, body):
            posted["added"].append((issue_num, body))
            return {"id": 999}

    monkeypatch.setattr(agent_launcher, "is_postgres_backend", lambda _backend: True)
    monkeypatch.setattr("nexus.core.completion_store.CompletionStore", _FakeStore)
    monkeypatch.setattr(
        "nexus.core.integrations.workflow_state_factory.get_storage_backend",
        lambda: "fake-storage",
    )
    monkeypatch.setattr(
        "nexus.core.orchestration.nexus_core_helpers.get_workflow_status",
        lambda _issue_num: {"workflow_id": "example-org-1-full"},
    )
    monkeypatch.setattr(agent_launcher, "_resolve_requester_token_for_issue", lambda *_a, **_k: None)
    monkeypatch.setattr(agent_launcher, "_get_git_platform_client", lambda *_a, **_k: _Platform())
    monkeypatch.setattr(
        agent_launcher,
        "_run_coro_sync",
        lambda coro_factory: (
            asyncio.run(value)
            if inspect.isawaitable(value := coro_factory())
            else value
        ),
    )
    monkeypatch.setattr(agent_launcher, "get_nexus_dir_name", lambda: ".nexus")
    monkeypatch.setattr(agent_launcher, "_extract_repo_from_issue_url", lambda _url: "example-org/example-project")

    dedup = agent_launcher._recover_completion_from_agent_log(
        issue_num="1",
        agent_type="triage",
        workspace_dir=str(tmp_path),
        project_key="example-org",
        tool_name="gemini",
        log_path=str(log_file),
        issue_url="https://gitlab.com/example-org/example-project/-/issues/1",
    )

    assert dedup == "dedup-recovered-1"
    assert posted["added"] == []


# ---------------------------------------------------------------------------
# _resolve_step_context_policy
# ---------------------------------------------------------------------------


def test_resolve_step_context_policy_returns_defaults_when_no_workflow(tmp_path):
    """When no workflow file exists the function returns standard defaults."""
    from nexus.core.runtime import agent_launcher

    result = agent_launcher._resolve_step_context_policy(
        workflow_path=str(tmp_path / "missing.yaml"),
        tier_name="full",
        workflow_name="new_feature",
        next_agent_type="developer",
    )
    assert result["context_policy"] == "standard"
    assert result["require_api_discovery"] is True
    assert result["include_audit_history"] is True
    assert result["audit_limit"] == 25


def test_resolve_step_context_policy_minimal(tmp_path):
    """Step with context_policy: minimal disables discovery and history."""
    from nexus.core.runtime import agent_launcher

    wf = tmp_path / "workflow.yaml"
    wf.write_text(
        "steps:\n"
        "  - id: triage\n"
        "    agent_type: triage\n"
        "    context_policy: minimal\n",
        encoding="utf-8",
    )
    result = agent_launcher._resolve_step_context_policy(
        workflow_path=str(wf),
        tier_name="full",
        workflow_name="new_feature",
        next_agent_type="triage",
    )
    assert result["context_policy"] == "minimal"
    assert result["require_api_discovery"] is False
    assert result["include_audit_history"] is False
    assert result["audit_limit"] == 0


def test_resolve_step_context_policy_deep(tmp_path):
    """Step with context_policy: deep enables discovery and history with higher limit."""
    from nexus.core.runtime import agent_launcher

    wf = tmp_path / "workflow.yaml"
    wf.write_text(
        "steps:\n"
        "  - id: develop\n"
        "    agent_type: developer\n"
        "    context_policy: deep\n"
        "    audit_limit: 50\n",
        encoding="utf-8",
    )
    result = agent_launcher._resolve_step_context_policy(
        workflow_path=str(wf),
        tier_name="full",
        workflow_name="new_feature",
        next_agent_type="developer",
    )
    assert result["context_policy"] == "deep"
    assert result["require_api_discovery"] is True
    assert result["include_audit_history"] is True
    assert result["audit_limit"] == 50


def test_resolve_step_context_policy_explicit_overrides_policy_defaults(tmp_path):
    """Explicit boolean flags on a step override the policy-level defaults."""
    from nexus.core.runtime import agent_launcher

    wf = tmp_path / "workflow.yaml"
    wf.write_text(
        "steps:\n"
        "  - id: review\n"
        "    agent_type: reviewer\n"
        "    context_policy: standard\n"
        "    require_api_discovery: false\n"
        "    include_audit_history: false\n"
        "    audit_limit: 5\n",
        encoding="utf-8",
    )
    result = agent_launcher._resolve_step_context_policy(
        workflow_path=str(wf),
        tier_name="full",
        workflow_name="new_feature",
        next_agent_type="reviewer",
    )
    assert result["context_policy"] == "standard"
    assert result["require_api_discovery"] is False
    assert result["include_audit_history"] is False
    assert result["audit_limit"] == 5


def test_resolve_step_context_policy_no_match_returns_defaults(tmp_path):
    """When agent_type does not match any step, defaults are returned."""
    from nexus.core.runtime import agent_launcher

    wf = tmp_path / "workflow.yaml"
    wf.write_text(
        "steps:\n"
        "  - id: triage\n"
        "    agent_type: triage\n"
        "    context_policy: minimal\n",
        encoding="utf-8",
    )
    result = agent_launcher._resolve_step_context_policy(
        workflow_path=str(wf),
        tier_name="full",
        workflow_name="new_feature",
        next_agent_type="developer",  # not in the YAML
    )
    assert result["context_policy"] == "standard"
