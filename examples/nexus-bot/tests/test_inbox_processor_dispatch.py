from unittest.mock import MagicMock


def test_process_file_returns_after_webhook_dispatch(tmp_path, monkeypatch):
    from nexus.core.inbox.inbox_task_processor_service import process_task_context

    inbox_file = tmp_path / "task.md"
    inbox_file.write_text("x")

    calls = {"webhook": 0, "new": 0}

    def _webhook(**kwargs):
        calls["webhook"] += 1
        return True

    def _new(**kwargs):
        calls["new"] += 1

    task_ctx = {
        "content": "body",
        "task_type": "feature",
        "project_name": "proj-a",
        "project_root": str(tmp_path),
        "config": {"workspace": "ws"},
    }
    deps = {
        "logger": MagicMock(),
        "base_dir": str(tmp_path),
        "emit_alert": lambda *args, **kwargs: None,
        "get_repos_for_project": lambda *_: [],
        "extract_repo_from_issue_url": lambda *_: "",
        "resolve_project_for_repo": lambda *_: None,
        "reroute_webhook_task_to_project": lambda *_: None,
        "get_tasks_active_dir": lambda *_: str(tmp_path),
        "is_recent_launch": lambda *_: False,
        "get_initial_agent_from_workflow": lambda *_: "triage",
        "get_repo_for_project": lambda *_: "owner/repo",
        "resolve_tier_for_issue": lambda *_: "full",
        "invoke_ai_agent": lambda *_args, **_kwargs: (None, None),
        "handle_webhook_task": _webhook,
        "handle_new_task": _new,
        "refine_issue_content": lambda *_: ("", ""),
        "extract_inline_task_name": lambda *_: "",
        "slugify": lambda value: value,
        "generate_issue_name": lambda *_: "",
        "get_sop_tier": lambda *_: ("full", "", ""),
        "render_checklist_from_workflow": lambda *_: "",
        "render_fallback_checklist": lambda *_: "",
        "create_issue": lambda *_args, **_kwargs: {},
        "rename_task_file_and_sync_issue_body": lambda *_args, **_kwargs: None,
        "get_workflow_state_plugin": lambda **_kwargs: None,
        "workflow_state_plugin_kwargs": {},
        "start_workflow": lambda *_args, **_kwargs: None,
    }
    process_task_context(task_ctx=task_ctx, filepath=str(inbox_file), deps=deps)

    assert calls["webhook"] == 1
    assert calls["new"] == 0


def test_process_file_routes_to_new_task_when_not_webhook(tmp_path, monkeypatch):
    from nexus.core.inbox.inbox_task_processor_service import process_task_context

    inbox_file = tmp_path / "task.md"
    inbox_file.write_text("x")

    calls = {"webhook": 0, "new": 0}

    def _webhook(**kwargs):
        calls["webhook"] += 1
        return False

    def _new(**kwargs):
        calls["new"] += 1

    task_ctx = {
        "content": "body",
        "task_type": "feature",
        "project_name": "proj-a",
        "project_root": str(tmp_path),
        "config": {"workspace": "ws"},
    }
    deps = {
        "logger": MagicMock(),
        "base_dir": str(tmp_path),
        "emit_alert": lambda *args, **kwargs: None,
        "get_repos_for_project": lambda *_: [],
        "extract_repo_from_issue_url": lambda *_: "",
        "resolve_project_for_repo": lambda *_: None,
        "reroute_webhook_task_to_project": lambda *_: None,
        "get_tasks_active_dir": lambda *_: str(tmp_path),
        "is_recent_launch": lambda *_: False,
        "get_initial_agent_from_workflow": lambda *_: "triage",
        "get_repo_for_project": lambda *_: "owner/repo",
        "resolve_tier_for_issue": lambda *_: "full",
        "invoke_ai_agent": lambda *_args, **_kwargs: (None, None),
        "handle_webhook_task": _webhook,
        "handle_new_task": _new,
        "refine_issue_content": lambda *_: ("", ""),
        "extract_inline_task_name": lambda *_: "",
        "slugify": lambda value: value,
        "generate_issue_name": lambda *_: "",
        "get_sop_tier": lambda *_: ("full", "", ""),
        "render_checklist_from_workflow": lambda *_: "",
        "render_fallback_checklist": lambda *_: "",
        "create_issue": lambda *_args, **_kwargs: {},
        "rename_task_file_and_sync_issue_body": lambda *_args, **_kwargs: None,
        "get_workflow_state_plugin": lambda **_kwargs: None,
        "workflow_state_plugin_kwargs": {},
        "start_workflow": lambda *_args, **_kwargs: None,
    }
    process_task_context(task_ctx=task_ctx, filepath=str(inbox_file), deps=deps)

    assert calls["webhook"] == 1
    assert calls["new"] == 1
