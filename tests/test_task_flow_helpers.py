from nexus.core.task_flow import helpers


def test_finalize_workflow_binds_keyword_only_git_dir_resolvers(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        helpers,
        "PROJECT_CONFIG",
        {
            "nexus": {
                "git_repo": "org/repo",
                "git_repos": ["org/repo"],
            }
        },
        raising=False,
    )
    monkeypatch.setattr(helpers, "BASE_DIR", "/tmp/workspace", raising=False)

    def _resolve_git_dir(*, project_name, project_config, base_dir):
        captured["single_kwargs"] = {
            "project_name": project_name,
            "project_config": project_config,
            "base_dir": base_dir,
        }
        return "/tmp/workspace/repo"

    def _resolve_git_dirs(*, project_name, get_repos, resolve_git_dir_for_repo):
        repos = list(get_repos(project_name))
        captured["multi_kwargs"] = {
            "project_name": project_name,
            "repos": repos,
            "resolved_repo_dir": resolve_git_dir_for_repo(project_name, repos[0]),
        }
        return {"org/repo": "/tmp/workspace/repo"}

    def _resolve_git_dir_for_repo(*, project_name, repo_name, project_config, base_dir):
        captured["repo_kwargs"] = {
            "project_name": project_name,
            "repo_name": repo_name,
            "project_config": project_config,
            "base_dir": base_dir,
        }
        return "/tmp/workspace/repo"

    def _finalize_workflow_core(**kwargs):
        captured["emit_notifications"] = kwargs["emit_notifications"]
        captured["single_result"] = kwargs["resolve_git_dir"]("nexus")
        captured["multi_result"] = kwargs["resolve_git_dirs"]("nexus")
        return {"finalization_blocked": True, "blocking_reasons": ["blocked"], "pr_urls": []}

    monkeypatch.setattr(helpers, "resolve_git_dir", _resolve_git_dir)
    monkeypatch.setattr(helpers, "resolve_git_dir_for_repo", _resolve_git_dir_for_repo)
    monkeypatch.setattr(helpers, "resolve_git_dirs", _resolve_git_dirs)
    monkeypatch.setattr(helpers, "_finalize_workflow_core", _finalize_workflow_core)

    result = helpers.finalize_workflow(
        issue_num="119",
        repo="org/repo",
        last_agent="writer",
        project_name="nexus",
    )

    assert captured["single_result"] == "/tmp/workspace/repo"
    assert captured["multi_result"] == {"org/repo": "/tmp/workspace/repo"}
    assert captured["single_kwargs"] == {
        "project_name": "nexus",
        "project_config": {"nexus": {"git_repo": "org/repo", "git_repos": ["org/repo"]}},
        "base_dir": "/tmp/workspace",
    }
    assert captured["multi_kwargs"] == {
        "project_name": "nexus",
        "repos": ["org/repo"],
        "resolved_repo_dir": "/tmp/workspace/repo",
    }
    assert captured["repo_kwargs"] == {
        "project_name": "nexus",
        "repo_name": "org/repo",
        "project_config": {"nexus": {"git_repo": "org/repo", "git_repos": ["org/repo"]}},
        "base_dir": "/tmp/workspace",
    }
    assert captured["emit_notifications"] is True
    assert result["finalization_blocked"] is True


def test_finalize_workflow_forwards_emit_notifications_flag(monkeypatch):
    captured: dict[str, object] = {}

    monkeypatch.setattr(helpers, "resolve_git_dir", lambda **_kwargs: None)
    monkeypatch.setattr(helpers, "resolve_git_dir_for_repo", lambda **_kwargs: None)
    monkeypatch.setattr(helpers, "resolve_git_dirs", lambda **_kwargs: {})

    def _finalize_workflow_core(**kwargs):
        captured["emit_notifications"] = kwargs["emit_notifications"]
        return {"finalization_blocked": True, "blocking_reasons": ["blocked"], "pr_urls": []}

    monkeypatch.setattr(helpers, "_finalize_workflow_core", _finalize_workflow_core)

    helpers.finalize_workflow(
        issue_num="119",
        repo="org/repo",
        last_agent="writer",
        project_name="nexus",
        emit_notifications=False,
    )

    assert captured["emit_notifications"] is False
