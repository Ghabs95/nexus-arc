import types
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

from nexus.core.task_dispatch import handle_new_task, handle_webhook_task


def test_handle_webhook_task_reroutes_mismatched_project(tmp_path):
    inbox_file = tmp_path / "task.md"
    inbox_file.write_text(
        "**Source:** webhook\n"
        "**Issue Number:** 42\n"
        "**URL:** https://github.com/acme/repo/issues/42\n"
        "**Agent Type:** triage\n"
    )

    logger = MagicMock()
    emit_alert = MagicMock()

    handled = handle_webhook_task(
        filepath=str(inbox_file),
        content=inbox_file.read_text(),
        project_name="proj-a",
        project_root=str(tmp_path),
        config={"workspace": "ws", "agents_dir": None},
        base_dir=str(tmp_path),
        logger=logger,
        emit_alert=emit_alert,
        get_repos_for_project=lambda _p: ["other/repo"],
        extract_repo_from_issue_url=lambda _u: "acme/repo",
        resolve_project_for_repo=lambda _r: "proj-b",
        reroute_webhook_task_to_project=lambda _f, _p: str(tmp_path / "rerouted.md"),
        get_tasks_active_dir=lambda _root, _proj: str(tmp_path / "active"),
        is_recent_launch=lambda _n: False,
        get_initial_agent_from_workflow=lambda _p: "triage",
        get_repo_for_project=lambda _p: "acme/repo",
        resolve_tier_for_issue=lambda *args, **kwargs: "full",
        invoke_ai_agent=lambda **kwargs: (None, None),
    )

    assert handled is True
    emit_alert.assert_called_once()
    assert "Re-routed webhook task" in emit_alert.call_args.args[0]


def test_handle_new_task_happy_path_smoke(tmp_path):
    inbox_file = tmp_path / "feature_123.md"
    inbox_file.write_text("placeholder")
    active_dir = tmp_path / "active"
    active_dir.mkdir()

    logger = MagicMock()
    emit_alert = MagicMock()
    workflow_plugin = types.SimpleNamespace(
        create_workflow_for_issue=lambda **kwargs: "wf-1",
    )

    # emulate async methods with coroutines
    async def _create_workflow_for_issue(**kwargs):
        return "wf-1"

    workflow_plugin.create_workflow_for_issue = _create_workflow_for_issue

    captured: dict[str, Any] = {
        "create_issue_called": False,
        "rename_called": False,
        "create_issue_kwargs": None,
    }

    def _create_issue(**kwargs):
        captured["create_issue_called"] = True
        captured["create_issue_kwargs"] = kwargs
        return "https://github.com/acme/repo/issues/77"

    def _rename_task_file_and_sync_issue_body(**kwargs):
        captured["rename_called"] = True
        path = Path(kwargs["task_file_path"])
        new_path = path.with_name("feature_77.md")
        path.rename(new_path)
        return str(new_path)

    async def _start_workflow(_wf, _issue):
        return True

    def _get_workflow_state_plugin(**kwargs):
        return workflow_plugin

    pid_tool: dict[str, int | None] = {"pid": None}

    def _invoke_ai_agent(**kwargs):
        pid_tool["pid"] = 1234
        return 1234, "copilot"

    handle_new_task(
        filepath=str(inbox_file),
        content="Implement feature\n**Task Name:** Nice feature",
        task_type="feature",
        project_name="proj-a",
        project_root=str(tmp_path),
        config={"workspace": "ws", "agents_dir": "agents"},
        base_dir=str(tmp_path),
        logger=logger,
        emit_alert=emit_alert,
        get_repo_for_project=lambda _p: "acme/repo",
        get_tasks_active_dir=lambda _root, _proj: str(active_dir),
        refine_issue_content=lambda content, _p: content,
        extract_inline_task_name=lambda _c: "Nice feature",
        slugify=lambda s: "nice-feature",
        generate_issue_name=lambda _c, _p: "fallback-name",
        get_sop_tier=lambda **kwargs: ("full", "SOP", "workflow:full"),
        render_checklist_from_workflow=lambda _p, _t: "",
        render_fallback_checklist=lambda _t: "- [ ] do thing",
        create_issue=_create_issue,
        rename_task_file_and_sync_issue_body=_rename_task_file_and_sync_issue_body,
        get_workflow_state_plugin=_get_workflow_state_plugin,
        workflow_state_plugin_kwargs={},
        start_workflow=_start_workflow,
        get_initial_agent_from_workflow=lambda _p: "triage",
        invoke_ai_agent=_invoke_ai_agent,
    )

    assert captured["create_issue_called"] is True
    assert captured["create_issue_kwargs"]["dedupe_key"] == "feature_123.md"
    assert captured["rename_called"] is True
    assert pid_tool["pid"] == 1234
    assert (active_dir / "feature_77.md").exists()
    emit_alert.assert_called_once()
    msg = emit_alert.call_args.args[0]
    kwargs = emit_alert.call_args.kwargs
    assert "Issue #77 created and workflow started" in msg
    assert "Workflow: wf-1" in msg
    assert kwargs["severity"] == "info"
    assert kwargs["issue_number"] == "77"
    assert kwargs["project_key"] == "proj-a"


def test_handle_new_task_runs_workflow_start_sync_once(tmp_path):
    inbox_file = tmp_path / "feature_123.md"
    inbox_file.write_text("placeholder")
    active_dir = tmp_path / "active"
    active_dir.mkdir()

    logger = MagicMock()
    emit_alert = MagicMock()

    async def _create_workflow_for_issue(**_kwargs):
        return "wf-1"

    workflow_plugin = types.SimpleNamespace(create_workflow_for_issue=_create_workflow_for_issue)

    sync_calls: dict[str, int] = {"count": 0}

    def _sync_runner(**_kwargs):
        sync_calls["count"] += 1
        return {"enabled": True, "blocked": False, "synced": [], "failures": []}

    async def _start_workflow(_workflow_id: str, _issue_number: str):
        return True

    handle_new_task(
        filepath=str(inbox_file),
        content="Implement feature",
        task_type="feature",
        project_name="proj-a",
        project_root=str(tmp_path),
        config={"workspace": "ws", "agents_dir": "agents"},
        base_dir=str(tmp_path),
        logger=logger,
        emit_alert=emit_alert,
        get_repo_for_project=lambda _p: "acme/repo",
        get_tasks_active_dir=lambda _root, _proj: str(active_dir),
        refine_issue_content=lambda content, _p: content,
        extract_inline_task_name=lambda _c: "Nice feature",
        slugify=lambda _s: "nice-feature",
        generate_issue_name=lambda _c, _p: "fallback-name",
        get_sop_tier=lambda **kwargs: ("full", "SOP", "workflow:full"),
        render_checklist_from_workflow=lambda _p, _t: "",
        render_fallback_checklist=lambda _t: "- [ ] do thing",
        create_issue=lambda **_kwargs: "https://github.com/acme/repo/issues/77",
        rename_task_file_and_sync_issue_body=lambda **kwargs: kwargs["task_file_path"],
        get_workflow_state_plugin=lambda **_kwargs: workflow_plugin,
        workflow_state_plugin_kwargs={},
        start_workflow=_start_workflow,
        get_initial_agent_from_workflow=lambda _p: "triage",
        invoke_ai_agent=lambda **_kwargs: (1234, "copilot"),
        run_workflow_start_git_sync=_sync_runner,
    )

    assert sync_calls["count"] == 1


def test_handle_new_task_blocks_initial_launch_when_sync_blocks(tmp_path):
    inbox_file = tmp_path / "feature_123.md"
    inbox_file.write_text("placeholder")
    active_dir = tmp_path / "active"
    active_dir.mkdir()

    logger = MagicMock()
    emit_alert = MagicMock()
    invoked: dict[str, int] = {"count": 0}

    async def _create_workflow_for_issue(**_kwargs):
        return "wf-1"

    workflow_plugin = types.SimpleNamespace(create_workflow_for_issue=_create_workflow_for_issue)

    def _invoke_ai_agent(**_kwargs):
        invoked["count"] += 1
        return 1234, "copilot"

    async def _start_workflow(_workflow_id: str, _issue_number: str):
        return True

    handle_new_task(
        filepath=str(inbox_file),
        content="Implement feature",
        task_type="feature",
        project_name="proj-a",
        project_root=str(tmp_path),
        config={"workspace": "ws", "agents_dir": "agents"},
        base_dir=str(tmp_path),
        logger=logger,
        emit_alert=emit_alert,
        get_repo_for_project=lambda _p: "acme/repo",
        get_tasks_active_dir=lambda _root, _proj: str(active_dir),
        refine_issue_content=lambda content, _p: content,
        extract_inline_task_name=lambda _c: "Nice feature",
        slugify=lambda _s: "nice-feature",
        generate_issue_name=lambda _c, _p: "fallback-name",
        get_sop_tier=lambda **kwargs: ("full", "SOP", "workflow:full"),
        render_checklist_from_workflow=lambda _p, _t: "",
        render_fallback_checklist=lambda _t: "- [ ] do thing",
        create_issue=lambda **_kwargs: "https://github.com/acme/repo/issues/77",
        rename_task_file_and_sync_issue_body=lambda **kwargs: kwargs["task_file_path"],
        get_workflow_state_plugin=lambda **_kwargs: workflow_plugin,
        workflow_state_plugin_kwargs={},
        start_workflow=_start_workflow,
        get_initial_agent_from_workflow=lambda _p: "triage",
        invoke_ai_agent=_invoke_ai_agent,
        run_workflow_start_git_sync=lambda **_kwargs: {"blocked": True},
    )

    assert invoked["count"] == 0


def test_handle_new_task_passes_requester_context_to_refiner(tmp_path):
    inbox_file = tmp_path / "feature_123.md"
    inbox_file.write_text("placeholder")

    captured: dict[str, Any] = {}

    def _refine(content, project_name, requester_context=None):
        captured["content"] = content
        captured["project_name"] = project_name
        captured["requester_context"] = requester_context
        return content

    async def _create_workflow_for_issue(**_kwargs):
        return "wf-1"

    async def _start_workflow(*_args, **_kwargs):
        return True

    handle_new_task(
        filepath=str(inbox_file),
        content="Implement feature",
        task_type="feature",
        project_name="proj-a",
        project_root=str(tmp_path),
        config={"workspace": "ws", "agents_dir": None},
        base_dir=str(tmp_path),
        logger=MagicMock(),
        emit_alert=MagicMock(),
        get_repo_for_project=lambda _p: "acme/repo",
        get_tasks_active_dir=lambda _root, _proj: str(tmp_path / "active"),
        refine_issue_content=_refine,
        extract_inline_task_name=lambda _c: "Nice feature",
        slugify=lambda _s: "nice-feature",
        generate_issue_name=lambda _c, _p: "fallback-name",
        get_sop_tier=lambda **kwargs: ("full", "SOP", "workflow:full"),
        render_checklist_from_workflow=lambda _p, _t: "",
        render_fallback_checklist=lambda _t: "- [ ] do thing",
        create_issue=lambda **_kwargs: "https://github.com/acme/repo/issues/77",
        rename_task_file_and_sync_issue_body=lambda **kwargs: kwargs["task_file_path"],
        get_workflow_state_plugin=lambda **_kwargs: types.SimpleNamespace(
            create_workflow_for_issue=_create_workflow_for_issue
        ),
        workflow_state_plugin_kwargs={},
        start_workflow=_start_workflow,
        get_initial_agent_from_workflow=lambda _p: "",
        invoke_ai_agent=lambda **_kwargs: (None, None),
        requester_nexus_id="nexus-42",
        requester_context={"nexus_id": "nexus-42", "platform": "telegram"},
    )

    assert captured["content"] == "Implement feature"
    assert captured["project_name"] == "proj-a"
    assert captured["requester_context"] == {"nexus_id": "nexus-42", "platform": "telegram"}


def test_handle_new_task_honors_planning_metadata(tmp_path):
    inbox_file = tmp_path / "feature_123.md"
    inbox_file.write_text("placeholder")
    active_dir = tmp_path / "active"
    active_dir.mkdir()

    captured: dict[str, Any] = {}

    async def _create_workflow_for_issue(**_kwargs):
        return "wf-1"

    workflow_plugin = types.SimpleNamespace(create_workflow_for_issue=_create_workflow_for_issue)

    async def _start_workflow(_workflow_id: str, _issue_number: str):
        return True

    def _create_issue(**kwargs):
        captured["create_issue_kwargs"] = kwargs
        return "https://github.com/acme/repo/issues/88"

    def _invoke_ai_agent(**kwargs):
        captured["invoke_ai_agent_kwargs"] = kwargs
        return 1234, "copilot"

    handle_new_task(
        filepath=str(inbox_file),
        content=(
            "Plan the social workflow\n"
            "**Task Name:** Nice feature\n"
            "**Agent Type:** designer\n"
            "**Execution Mode:** planning\n"
            "**Issue Labels:** agent:plan-requested, custom:planning\n"
        ),
        task_type="feature",
        project_name="proj-a",
        project_root=str(tmp_path),
        config={"workspace": "ws", "agents_dir": "agents"},
        base_dir=str(tmp_path),
        logger=MagicMock(),
        emit_alert=MagicMock(),
        get_repo_for_project=lambda _p: "acme/repo",
        get_tasks_active_dir=lambda _root, _proj: str(active_dir),
        refine_issue_content=lambda content, _p: content,
        extract_inline_task_name=lambda _c: "Nice feature",
        slugify=lambda _s: "nice-feature",
        generate_issue_name=lambda _c, _p: "fallback-name",
        get_sop_tier=lambda **kwargs: ("full", "SOP", "workflow:full"),
        render_checklist_from_workflow=lambda _p, _t: "",
        render_fallback_checklist=lambda _t: "- [ ] do thing",
        create_issue=_create_issue,
        rename_task_file_and_sync_issue_body=lambda **kwargs: kwargs["task_file_path"],
        get_workflow_state_plugin=lambda **_kwargs: workflow_plugin,
        workflow_state_plugin_kwargs={},
        start_workflow=_start_workflow,
        get_initial_agent_from_workflow=lambda _p: "triage",
        invoke_ai_agent=_invoke_ai_agent,
    )

    assert captured["create_issue_kwargs"]["extra_labels"] == [
        "agent:plan-requested",
        "custom:planning",
    ]
    assert captured["invoke_ai_agent_kwargs"]["agent_type"] == "designer"
    assert captured["invoke_ai_agent_kwargs"]["execution_mode"] == "planning"
