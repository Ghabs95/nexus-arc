"""Regression tests for strict project/repository boundaries."""

from pathlib import Path
from unittest.mock import MagicMock, Mock, patch

import pytest


def test_extract_repo_from_issue_url_parses_owner_repo():
    from nexus.core.inbox.inbox_repo_path_service import extract_repo_from_issue_url

    repo = extract_repo_from_issue_url("https://github.com/sample-org/nexus-arc/issues/43")

    assert repo == "sample-org/nexus-arc"


def test_extract_repo_from_gitlab_issue_url_parses_namespace_repo():
    from nexus.core.inbox.inbox_repo_path_service import extract_repo_from_issue_url

    repo = extract_repo_from_issue_url("https://gitlab.com/sample-org/mobile-app/-/issues/77")

    assert repo == "sample-org/mobile-app"


def test_resolve_repo_strict_raises_on_mismatch(monkeypatch):
    from nexus.core.inbox.inbox_repo_path_service import resolve_repo_strict
    from nexus.core.project.repo_utils import project_repos_from_config

    project_config = {
        "nexus": {
            "workspace": "sample/core",
            "git_repo": "sample-org/nexus-arc",
        }
    }

    mock_logger = MagicMock()
    mock_emit = Mock()
    with pytest.raises(ValueError):
        resolve_repo_strict(
            project_name="nexus",
            issue_num="43",
            project_config=project_config,
            project_repos_from_config=project_repos_from_config,
            get_repos=lambda _project: [],
            resolve_repo_for_issue=lambda _issue, default_project=None: "sample-org/nexus",
            get_default_project=lambda: "nexus",
            get_repo=lambda _project: "sample-org/nexus-arc",
            emit_alert=mock_emit,
            logger=mock_logger,
        )
    mock_emit.assert_called_once()


def test_reroute_webhook_task_moves_file(tmp_path, monkeypatch):
    from nexus.core.inbox.inbox_repo_path_service import reroute_webhook_task_to_project

    base_dir = tmp_path / "root"
    source_dir = base_dir / "workspace-a" / ".nexus" / "inbox" / "project-a"
    source_dir.mkdir(parents=True)
    source_file = source_dir / "issue_43.md"
    source_file.write_text("test")

    project_config = {
        "project-b": {
            "workspace": "workspace-b",
            "git_repo": "sample-org/nexus-arc",
        }
    }
    moved_path = reroute_webhook_task_to_project(
        filepath=str(source_file),
        target_project="project-b",
        project_config=project_config,
        base_dir=str(base_dir),
        get_inbox_dir=lambda workspace_root, project: str(
            Path(workspace_root) / ".nexus" / "inbox" / project
        ),
    )

    assert moved_path is not None
    assert not source_file.exists()
    assert Path(moved_path).exists()
    assert str(Path(moved_path)).endswith("/.nexus/inbox/project-b/issue_43.md")


@patch("webhook_server.emit_alert", return_value=True)
def test_webhook_blocks_unmapped_repository(mock_alert):
    from webhook_server import _get_webhook_policy, handle_issue_opened

    payload = {
        "action": "opened",
        "issue": {
            "number": 55,
            "title": "Boundary test",
            "body": "Body",
            "html_url": "https://github.com/unknown/repo/issues/55",
            "user": {"login": "alice"},
            "labels": [],
        },
        "repository": {"full_name": "unknown/repo"},
        "sender": {"login": "alice"},
    }

    event = _get_webhook_policy().parse_issue_event(payload)
    result = handle_issue_opened(payload, event)

    assert result["status"] == "ignored"
    assert result["reason"] == "unmapped_repository"
    mock_alert.assert_called_once()


def test_agent_launcher_resolves_issue_body_from_matching_project_repo(monkeypatch):
    import nexus.core.runtime.agent_launcher as agent_launcher

    class PluginA:
        async def get_issue(self, issue_number):
            return type(
                "Issue",
                (),
                {
                    "body": (
                        "**Task File:** "
                        "`/tmp/base/workspace-b/.nexus/tasks/project-b/active/issue_43.md`"
                    )
                },
            )()

    class PluginB:
        async def get_issue(self, issue_number):
            return type(
                "Issue",
                (),
                {
                    "body": (
                        "**Task File:** "
                        "`/tmp/base/workspace-b/.nexus/tasks/project-b/active/issue_43.md`"
                    )
                },
            )()

    plugins = {
        "org/repo-a": PluginA(),
        "org/repo-b": PluginB(),
    }

    monkeypatch.setattr(agent_launcher, "BASE_DIR", "/tmp/base")
    monkeypatch.setattr(
        agent_launcher,
        "PROJECT_CONFIG",
        {
            "project-a": {
                "workspace": "workspace-a",
                "git_repo": "org/repo-a",
                "agents_dir": "agents/a",
            },
            "project-b": {
                "workspace": "workspace-b",
                "git_repo": "org/repo-b",
                "agents_dir": "agents/b",
            },
        },
    )
    monkeypatch.setattr(
        agent_launcher,
        "_get_git_platform_client",
        lambda repo, project_name=None, token_override=None: plugins.get(repo),
    )

    body, repo, task_file = agent_launcher._load_issue_body_from_project_repo("43")

    assert "Task File" in body
    assert repo == "org/repo-b"
    assert task_file.endswith("issue_43.md")


def test_launch_next_agent_uses_issue_body_for_shared_active_task_file(monkeypatch):
    import nexus.core.runtime.agent_launcher as agent_launcher

    issue_body = (
        "## Task\n"
        "Implementation outline:\n"
        "1. Feature for issue 86\n"
        "**Task File:** `/tmp/base/.nexus/tasks/nexus/active/task_feature-pick.md`\n"
    )

    monkeypatch.setattr(
        agent_launcher,
        "_load_issue_body_from_project_repo",
        lambda issue_number, preferred_repo=None: (
            issue_body,
            "org/repo-b",
            "/tmp/base/.nexus/tasks/nexus/active/task_feature-pick.md",
        ),
    )
    monkeypatch.setattr(
        agent_launcher,
        "PROJECT_CONFIG",
        {
            "nexus": {
                "workspace": ".nexus",
                "agents_dir": "agents",
                "git_repo": "org/repo-b",
            }
        },
    )
    monkeypatch.setattr(agent_launcher, "BASE_DIR", "/tmp/base")
    monkeypatch.setattr(agent_launcher, "_project_repos", lambda *args, **kwargs: ["org/repo-b"])
    monkeypatch.setattr(agent_launcher, "get_repos", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(agent_launcher, "get_repo", lambda _project_root: "org/repo-b")
    monkeypatch.setattr(
        agent_launcher.HostStateManager, "get_last_tier_for_issue", lambda _issue: "full"
    )
    monkeypatch.setattr(agent_launcher, "get_sop_tier_from_issue", lambda *args, **kwargs: "full")
    monkeypatch.setattr(agent_launcher, "is_recent_launch", lambda *_args, **_kwargs: False)
    monkeypatch.setattr(agent_launcher, "notify_agent_completed", lambda **_kwargs: None)

    captured: dict[str, str] = {}

    def _fake_invoke(**kwargs):
        captured["task_content"] = kwargs.get("task_content", "")
        return 999, "copilot"

    monkeypatch.setattr(agent_launcher, "invoke_ai_agent", _fake_invoke)

    pid, tool = agent_launcher.launch_next_agent("86", "developer", "orphan-recovery")

    assert pid == 999
    assert tool == "copilot"
    assert "Feature for issue 86" in captured["task_content"]
    assert "Task File:" in captured["task_content"]


def test_resolve_project_for_repo_matches_secondary_repo(monkeypatch):
    from nexus.core.inbox.inbox_repo_path_service import resolve_project_for_repo
    from nexus.core.project.repo_utils import iter_project_configs, project_repos_from_config

    project_config = {
        "sampleco": {
            "workspace": "sampleco",
            "git_repo": "acme/sampleco-backend",
            "git_repos": ["acme/sampleco-backend", "acme/sampleco-mobile"],
        }
    }
    assert (
        resolve_project_for_repo(
            repo_name="acme/sampleco-mobile",
            project_config=project_config,
            iter_project_configs=iter_project_configs,
            project_repos_from_config=project_repos_from_config,
            get_repos=lambda _project: [],
        )
        == "sampleco"
    )


def test_resolve_project_for_repo_matches_gitlab_secondary_repo(monkeypatch):
    from nexus.core.inbox.inbox_repo_path_service import resolve_project_for_repo
    from nexus.core.project.repo_utils import iter_project_configs, project_repos_from_config

    project_config = {
        "sampleco": {
            "workspace": "sampleco",
            "git_platform": "gitlab",
            "git_repo": "sampleco/backend",
            "git_repos": ["sampleco/backend", "sampleco/mobile-app"],
        }
    }
    assert (
        resolve_project_for_repo(
            repo_name="sampleco/mobile-app",
            project_config=project_config,
            iter_project_configs=iter_project_configs,
            project_repos_from_config=project_repos_from_config,
            get_repos=lambda _project: [],
        )
        == "sampleco"
    )


def test_issue_body_resolution_skips_project_probe_failures(monkeypatch):
    import nexus.core.runtime.agent_launcher as agent_launcher

    class _Issue:
        def __init__(self, body: str):
            self.body = body

    class _Platform:
        async def get_issue(self, issue_number: str):
            return _Issue(body=f"Nexus issue body for {issue_number}")

    monkeypatch.setattr(
        agent_launcher,
        "PROJECT_CONFIG",
        {
            "project_alpha": {
                "workspace": "project_alpha",
                "git_platform": "gitlab",
                "git_repo": "project_alpha/workflows",
            },
            "nexus": {
                "workspace": "ghabs",
                "git_platform": "github",
                "git_repo": "ghabs-org/nexus-arc",
            },
        },
    )
    monkeypatch.setattr(
        agent_launcher,
        "_iter_project_configs",
        lambda cfg, _get_repos: list(cfg.items()),
    )
    monkeypatch.setattr(
        agent_launcher,
        "_project_repos",
        lambda _project_key, cfg, _get_repos: [cfg.get("git_repo")] if cfg.get("git_repo") else [],
    )
    monkeypatch.setattr(agent_launcher, "is_postgres_backend", lambda _backend: True)
    monkeypatch.setattr(agent_launcher, "get_repos", lambda _project: [])

    def _fake_client(
        repo_name: str,
        project_name: str | None = None,
        token_override: str | None = None,
    ):
        if project_name == "project_alpha":
            raise ValueError("GITLAB_TOKEN is required for gitlab projects")
        return _Platform()

    monkeypatch.setattr(agent_launcher, "_get_git_platform_client", _fake_client)
    monkeypatch.setattr(
        agent_launcher,
        "_run_coro_sync",
        lambda coro_factory: __import__("asyncio").run(coro_factory()),
    )

    body, repo, task_file = agent_launcher._load_issue_body_from_project_repo("88")

    assert "Nexus issue body" in body
    assert repo == "ghabs-org/nexus-arc"
    assert task_file == ""


@patch("webhook_server._notify_lifecycle", return_value=True)
@patch("webhook_server.emit_alert", return_value=True)
def test_webhook_maps_secondary_repo_to_same_project(
    _mock_alert, _mock_notify, tmp_path, monkeypatch
):
    import webhook_server

    base_dir = tmp_path / "workspace-root"
    base_dir.mkdir(parents=True)
    monkeypatch.setattr(webhook_server, "BASE_DIR", str(base_dir))
    monkeypatch.setattr(
        webhook_server,
        "PROJECT_CONFIG",
        {
            "sampleco": {
                "workspace": "sampleco-workspace",
                "git_repo": "acme/sampleco-backend",
                "git_repos": ["acme/sampleco-backend", "acme/sampleco-mobile"],
            },
            "system_operations": {
                "inbox": "triage",
            },
        },
    )

    payload = {
        "action": "opened",
        "issue": {
            "number": 77,
            "title": "Cross-repo feature",
            "body": "Implement backend + mobile",
            "html_url": "https://github.com/acme/sampleco-mobile/issues/77",
            "user": {"login": "alice"},
            "labels": [],
        },
        "repository": {"full_name": "acme/sampleco-mobile"},
        "sender": {"login": "alice"},
    }

    event = webhook_server._get_webhook_policy().parse_issue_event(payload)
    result = webhook_server.handle_issue_opened(payload, event)

    assert result["status"] == "task_created"
    assert "sampleco" in result["task_file"]
