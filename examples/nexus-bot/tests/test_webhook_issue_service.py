from pathlib import Path
from unittest.mock import MagicMock

from nexus.core.webhook.issue_service import handle_issue_opened_event


class _Policy:
    def build_issue_closed_message(self, event):
        return f"closed:{event.get('number')}"

    def build_issue_created_message(self, event, agent_type):
        return f"created:{event.get('number')}:{agent_type}"


def test_handle_issue_opened_event_blocks_unmapped_repository(tmp_path):
    alerts = []
    result = handle_issue_opened_event(
        event={
            "action": "opened",
            "number": 55,
            "title": "Boundary test",
            "body": "Body",
            "author": "alice",
            "url": "https://github.com/unknown/repo/issues/55",
            "labels": [],
            "repo": "unknown/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda _m: True,
        emit_alert=lambda msg, **kwargs: alerts.append((msg, kwargs)),
        project_config={"proj-a": {"workspace": "ws-a", "git_repo": "acme/repo"}},
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: ["acme/repo"],
        get_repos=lambda _key: ["acme/repo"],
        get_tasks_active_dir=lambda root, project: str(tmp_path / "active"),
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox"),
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "unmapped_repository"
    assert len(alerts) == 1


def test_handle_issue_opened_event_creates_task_file(tmp_path):
    notifications = []
    inbox_dir = tmp_path / "workspace-a" / ".nexus" / "inbox" / "proj-a"

    result = handle_issue_opened_event(
        event={
            "action": "opened",
            "number": 77,
            "title": "Cross-repo feature",
            "body": "Implement backend + mobile",
            "author": "alice",
            "url": "https://github.com/acme/sampleco-mobile/issues/77",
            "labels": [],
            "repo": "acme/sampleco-mobile",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda m: notifications.append(m) or True,
        emit_alert=lambda *args, **kwargs: True,
        project_config={
            "sampleco": {
                "workspace": "workspace-a",
                "git_repo": "acme/sampleco-backend",
                "git_repos": ["acme/sampleco-backend", "acme/sampleco-mobile"],
            },
            "system_operations": {"inbox": "triage"},
        },
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: cfg.get("git_repos", [cfg.get("git_repo")]),
        get_repos=lambda _key: [],
        get_tasks_active_dir=lambda root, project: str(
            tmp_path / "workspace-a" / ".nexus" / "tasks" / project / "active"
        ),
        get_inbox_dir=lambda root, project: str(inbox_dir),
    )

    assert result["status"] == "task_created"
    task_path = Path(result["task_file"])
    assert task_path.exists()
    assert "Source:** webhook" in task_path.read_text()
    assert notifications


def test_handle_issue_closed_event_triggers_worktree_cleanup():
    notifications = []
    cleanups = []

    result = handle_issue_opened_event(
        event={
            "action": "closed",
            "number": 88,
            "title": "Done",
            "body": "",
            "author": "alice",
            "url": "https://github.com/acme/repo/issues/88",
            "labels": [],
            "repo": "acme/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda m: notifications.append(m) or True,
        emit_alert=lambda *args, **kwargs: True,
        project_config={},
        base_dir="/tmp",
        project_repos=lambda key, cfg, get_repos: [],
        get_repos=lambda _key: [],
        get_tasks_active_dir=lambda root, project: "/tmp",
        get_inbox_dir=lambda root, project: "/tmp",
        cleanup_worktree_for_issue=lambda repo, issue: cleanups.append((repo, issue)) or True,
    )

    assert result["status"] == "issue_closed_notified"
    assert result["worktree_cleanup"] is True
    assert cleanups == [("acme/repo", "88")]
    assert notifications == ["closed:88"]


def test_handle_issue_labeled_plan_requested_creates_planning_task(tmp_path):
    inbox_dir = tmp_path / "workspace-a" / ".nexus" / "inbox" / "proj-a"

    result = handle_issue_opened_event(
        event={
            "action": "labeled",
            "number": 90,
            "title": "Need design first",
            "body": "Draft a clear implementation plan.",
            "author": "alice",
            "url": "https://github.com/acme/repo/issues/90",
            "labels": ["agent:plan-requested"],
            "repo": "acme/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda _m: True,
        emit_alert=lambda *args, **kwargs: True,
        project_config={
            "proj-a": {
                "workspace": "workspace-a",
                "git_repo": "acme/repo",
            },
            "system_operations": {"default": "triage", "inbox": "triage", "plan": "designer"},
        },
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: cfg.get("git_repos", [cfg.get("git_repo")]),
        get_repos=lambda _key: [],
        get_tasks_active_dir=lambda root, project: str(
            tmp_path / "workspace-a" / ".nexus" / "tasks" / project / "active"
        ),
        get_inbox_dir=lambda root, project: str(inbox_dir),
    )

    assert result["status"] == "task_created"
    assert result["agent_type"] == "designer"


def test_handle_issue_labeled_without_plan_label_is_ignored(tmp_path):
    result = handle_issue_opened_event(
        event={
            "action": "labeled",
            "number": 91,
            "title": "Label update only",
            "body": "",
            "author": "alice",
            "url": "https://github.com/acme/repo/issues/91",
            "labels": ["priority:high"],
            "repo": "acme/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda _m: True,
        emit_alert=lambda *args, **kwargs: True,
        project_config={"proj-a": {"workspace": "workspace-a", "git_repo": "acme/repo"}},
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: [cfg.get("git_repo")],
        get_repos=lambda _key: [],
        get_tasks_active_dir=lambda root, project: str(tmp_path / "active"),
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox"),
    )

    assert result["status"] == "ignored"
    assert result["reason"] == "labeled action without agent:plan-requested"


def test_handle_issue_opened_with_workflow_label_notifies_but_skips_task_creation(tmp_path):
    notifications = []
    result = handle_issue_opened_event(
        event={
            "action": "opened",
            "number": 110,
            "title": "Workflow-generated issue",
            "body": "Generated from inbox flow",
            "author": "nexus-bot",
            "url": "https://github.com/acme/repo/issues/110",
            "labels": ["workflow:full"],
            "repo": "acme/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda m: notifications.append(m) or True,
        emit_alert=lambda *args, **kwargs: True,
        project_config={"proj-a": {"workspace": "workspace-a", "git_repo": "acme/repo"}},
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: [cfg.get("git_repo")],
        get_repos=lambda _key: [],
        get_tasks_active_dir=lambda root, project: str(tmp_path / "active"),
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox"),
    )

    assert result["status"] == "notified_only"
    assert result["reason"] == "self-created issue (has workflow label)"
    assert notifications == ["created:110:workflow"]


def test_handle_issue_opened_event_binds_requester_identity(tmp_path):
    inbox_dir = tmp_path / "workspace-a" / ".nexus" / "inbox" / "proj-a"
    captured: dict[str, str] = {}

    class _Cred:
        nexus_id = "nexus-user-123"

    result = handle_issue_opened_event(
        event={
            "action": "opened",
            "number": 111,
            "title": "Auth binding test",
            "body": "Use stored credentials",
            "author": "alice",
            "url": "https://github.com/acme/repo/issues/111",
            "labels": [],
            "repo": "acme/repo",
        },
        logger=MagicMock(),
        policy=_Policy(),
        notify_lifecycle=lambda _m: True,
        emit_alert=lambda *args, **kwargs: True,
        project_config={
            "proj-a": {
                "workspace": "workspace-a",
                "git_repo": "acme/repo",
            },
            "system_operations": {"default": "triage", "inbox": "triage"},
        },
        base_dir=str(tmp_path),
        project_repos=lambda key, cfg, get_repos: cfg.get("git_repos", [cfg.get("git_repo")]),
        get_repos=lambda _key: [],
        get_tasks_active_dir=lambda root, project: str(
            tmp_path / "workspace-a" / ".nexus" / "tasks" / project / "active"
        ),
        get_inbox_dir=lambda root, project: str(inbox_dir),
        bind_issue_requester=lambda **kwargs: captured.update(
            {k: str(v) for k, v in kwargs.items()}
        ),
        find_user_credentials_by_github_identity=lambda **_kwargs: _Cred(),
        find_user_credentials_by_gitlab_identity=lambda **_kwargs: None,
    )

    assert result["status"] == "task_created"
    assert result["requester_nexus_id"] == "nexus-user-123"
    assert captured["repo_key"] == "acme/repo"
    assert captured["issue_number"] == "111"
    assert captured["requester_nexus_id"] == "nexus-user-123"
