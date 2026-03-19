import logging
from types import SimpleNamespace

import pytest

from nexus.core.workflow_runtime import workflow_control_service as control_service
from nexus.core.workflow_runtime import workflow_reprocess_continue_service as continue_service
from nexus.core.workflow_runtime.workflow_reprocess_continue_service import (
    _maybe_reset_continue_workflow_position,
    _launch_continue_agent,
    handle_continue,
    handle_reprocess,
)


class _Ctx:
    def __init__(self, args=None, user_id="1"):
        self.args = args or []
        self.user_id = user_id
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kwargs):
        self.replies.append(text)
        return "msg"

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


class _CtxEditFails(_Ctx):
    async def edit_message_text(self, **kwargs):
        raise RuntimeError("edit failed")


@pytest.mark.asyncio
async def test_reprocess_service_prompts_when_no_args():
    ctx = _Ctx()
    seen = {}
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        prompt_project_selection=lambda c, cmd: seen.setdefault("cmd", cmd),
    )

    async def _prompt(c, cmd):
        seen["cmd"] = cmd

    deps.prompt_project_selection = _prompt
    await handle_reprocess(
        ctx, deps, build_issue_url=lambda *a, **k: "", resolve_repo=lambda *a, **k: ""
    )
    assert seen["cmd"] == "reprocess"


@pytest.mark.asyncio
async def test_continue_service_prompts_when_no_args():
    ctx = _Ctx()
    seen = {}
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
    )

    async def _prompt(c, cmd):
        seen["cmd"] = cmd

    deps.prompt_project_selection = _prompt
    await handle_continue(ctx, deps, finalize_workflow=lambda *a, **k: None)
    assert seen["cmd"] == "continue"


@pytest.mark.asyncio
async def test_launch_continue_agent_replaces_progress_message_on_launch_error():
    ctx = _Ctx()
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        invoke_ai_agent=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    continue_ctx = {
        "resumed_from": "designer",
        "agent_type": "triage",
        "agents_abs": "/tmp/agents",
        "workspace_abs": "/tmp/workspace",
        "issue_url": "https://github.com/acme/repo/issues/86",
        "tier_name": "full",
        "content": "task",
        "continuation_prompt": "continue",
        "log_subdir": "nexus",
    }

    await _launch_continue_agent(ctx, deps, issue_num="86", continue_ctx=continue_ctx)

    assert len(ctx.replies) == 1
    assert "Continuing issue #86" in ctx.replies[0]
    assert len(ctx.edits) == 1
    assert ctx.edits[0]["message_id"] == "msg"
    assert "Failed to continue agent for issue #86" in ctx.edits[0]["text"]


@pytest.mark.asyncio
async def test_launch_continue_agent_falls_back_to_reply_when_edit_fails():
    deleted = {}

    async def _delete_message(**kwargs):
        deleted.update(kwargs)

    ctx = _CtxEditFails()
    ctx.chat_id = 999
    ctx.telegram_context = SimpleNamespace(bot=SimpleNamespace(delete_message=_delete_message))
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        invoke_ai_agent=lambda **kwargs: (123, "copilot"),
    )
    continue_ctx = {
        "resumed_from": "designer",
        "agent_type": "triage",
        "agents_abs": "/tmp/agents",
        "workspace_abs": "/tmp/workspace",
        "issue_url": "https://github.com/acme/repo/issues/86",
        "tier_name": "full",
        "content": "task",
        "continuation_prompt": "continue",
        "log_subdir": "nexus",
    }

    await _launch_continue_agent(ctx, deps, issue_num="86", continue_ctx=continue_ctx)

    # First reply is transient progress; second is fallback final status.
    assert len(ctx.replies) == 2
    assert "Continuing issue #86" in ctx.replies[0]
    assert "Agent continued for issue #86" in ctx.replies[1]
    assert deleted["chat_id"] == 999


@pytest.mark.asyncio
async def test_launch_continue_agent_forwards_requester_nexus_id():
    captured: dict[str, object] = {}
    ctx = _Ctx()
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        invoke_ai_agent=lambda **kwargs: captured.update(kwargs) or (123, "copilot"),
    )
    continue_ctx = {
        "resumed_from": "designer",
        "agent_type": "triage",
        "agents_abs": "/tmp/agents",
        "workspace_abs": "/tmp/workspace",
        "issue_url": "https://github.com/acme/repo/issues/86",
        "tier_name": "full",
        "content": "task",
        "continuation_prompt": "continue",
        "log_subdir": "nexus",
        "requester_nexus_id": "nexus-user-86",
    }

    await _launch_continue_agent(ctx, deps, issue_num="86", continue_ctx=continue_ctx)

    assert captured.get("requester_nexus_id") == "nexus-user-86"


@pytest.mark.asyncio
async def test_maybe_reset_continue_workflow_position_resets_for_recovered_next_agent():
    called = {}

    class _WorkflowPlugin:
        async def reset_to_agent_for_issue(self, issue_num, agent_type):
            called["issue_num"] = issue_num
            called["agent_type"] = agent_type
            return True

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        workflow_state_plugin_kwargs={},
        get_workflow_state_plugin=lambda **kwargs: _WorkflowPlugin(),
    )
    ctx = _Ctx()
    ok = await _maybe_reset_continue_workflow_position(
        ctx,
        deps,
        issue_num="106",
        continue_ctx={
            "forced_agent_override": False,
            "sync_workflow_to_agent": True,
            "agent_type": "developer",
        },
    )

    assert ok is True
    assert called == {"issue_num": "106", "agent_type": "developer"}


@pytest.mark.asyncio
async def test_maybe_reset_continue_workflow_position_resets_when_workflow_failed():
    called = {}

    class _WorkflowPlugin:
        async def get_workflow_status(self, issue_num):
            assert issue_num == "106"
            return {"state": "failed"}

        async def reset_to_agent_for_issue(self, issue_num, agent_type):
            called["issue_num"] = issue_num
            called["agent_type"] = agent_type
            return True

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        workflow_state_plugin_kwargs={},
        get_workflow_state_plugin=lambda **kwargs: _WorkflowPlugin(),
    )
    ctx = _Ctx()
    ok = await _maybe_reset_continue_workflow_position(
        ctx,
        deps,
        issue_num="106",
        continue_ctx={
            "forced_agent_override": False,
            "sync_workflow_to_agent": False,
            "agent_type": "reviewer",
        },
    )

    assert ok is True
    assert called == {"issue_num": "106", "agent_type": "reviewer"}


@pytest.mark.asyncio
async def test_maybe_reset_continue_workflow_position_rejects_placeholder_agent_type():
    class _WorkflowPlugin:
        async def get_workflow_status(self, issue_num):
            assert issue_num == "119"
            return {"state": "failed"}

        async def reset_to_agent_for_issue(self, issue_num, agent_type):
            raise AssertionError("reset_to_agent_for_issue should not be called")

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        workflow_state_plugin_kwargs={},
        get_workflow_state_plugin=lambda **kwargs: _WorkflowPlugin(),
    )
    ctx = _Ctx()
    ok = await _maybe_reset_continue_workflow_position(
        ctx,
        deps,
        issue_num="119",
        continue_ctx={
            "forced_agent_override": False,
            "sync_workflow_to_agent": False,
            "agent_type": "<agent_type from workflow steps — NOT the step id or display name>",
        },
    )

    assert ok is False
    assert ctx.replies == ["❌ Missing target agent for workflow reset on issue #119."]


@pytest.mark.asyncio
async def test_maybe_reset_continue_workflow_position_skips_when_running_and_no_override():
    called = {"reset": 0}

    class _WorkflowPlugin:
        async def get_workflow_status(self, issue_num):
            assert issue_num == "106"
            return {"state": "running"}

        async def reset_to_agent_for_issue(self, issue_num, agent_type):
            called["reset"] += 1
            return True

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        workflow_state_plugin_kwargs={},
        get_workflow_state_plugin=lambda **kwargs: _WorkflowPlugin(),
    )
    ctx = _Ctx()
    ok = await _maybe_reset_continue_workflow_position(
        ctx,
        deps,
        issue_num="106",
        continue_ctx={
            "forced_agent_override": False,
            "sync_workflow_to_agent": False,
            "agent_type": "reviewer",
        },
    )

    assert ok is True
    assert called["reset"] == 0
    assert ctx.replies == []


@pytest.mark.asyncio
async def test_continue_service_reconciles_before_launch_when_ready(monkeypatch):
    ctx = _Ctx(args=["nexus", "106"])
    calls = {"prepare": 0, "reconcile": 0, "launched_agent": None}

    async def _ensure(_ctx, _deps, _command):
        return "nexus", "106", []

    def _prepare(_issue_num, _project_key, _rest, _deps, requester_nexus_id=None):
        calls["prepare"] += 1
        if calls["prepare"] == 1:
            return {
                "status": "ready",
                "forced_agent_override": False,
                "agent_type": "designer",
                "resumed_from": "triage",
            }
        return {
            "status": "ready",
            "forced_agent_override": False,
            "agent_type": "developer",
            "resumed_from": "designer",
            "agents_abs": "/tmp/agents",
            "workspace_abs": "/tmp/workspace",
            "issue_url": "https://github.com/acme/repo/issues/106",
            "tier_name": "full",
            "content": "task",
            "continuation_prompt": "continue",
            "log_subdir": "nexus",
        }

    async def _status_outcome(*_args, **_kwargs):
        return False

    async def _maybe_reset(*_args, **_kwargs):
        return True

    async def _launch(_ctx, _deps, *, issue_num, continue_ctx):
        assert issue_num == "106"
        calls["launched_agent"] = continue_ctx.get("agent_type")

    async def _reconcile(**_kwargs):
        calls["reconcile"] += 1
        return {"ok": True, "signals_applied": 1}

    monkeypatch.setattr(continue_service, "_ensure_project_issue_for_command", _ensure)
    monkeypatch.setattr(continue_service, "_prepare_continue_context", _prepare)
    monkeypatch.setattr(continue_service, "_handle_continue_status_outcome", _status_outcome)
    monkeypatch.setattr(continue_service, "_maybe_reset_continue_workflow_position", _maybe_reset)
    monkeypatch.setattr(continue_service, "_launch_continue_agent", _launch)

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        project_repo=lambda _project: "Ghabs95/nexus-arc",
        reconcile_issue_from_signals=_reconcile,
        get_direct_issue_plugin=lambda _repo: None,
        extract_structured_completion_signals=lambda _comments: [],
        workflow_state_plugin_kwargs={},
        write_local_completion_from_signal=lambda *_a, **_k: "",
    )

    await handle_continue(ctx, deps, finalize_workflow=lambda *a, **k: None)

    assert calls["reconcile"] == 1
    assert calls["prepare"] == 2
    assert calls["launched_agent"] == "developer"


@pytest.mark.asyncio
async def test_continue_service_stops_when_recovered_state_waits_on_human(monkeypatch):
    ctx = _Ctx(args=["nexus", "119"])

    async def _ensure(_ctx, _deps, _command):
        return "nexus", "119", []

    async def _status_outcome(_ctx, _deps, *, issue_num, continue_ctx, finalize_workflow):
        assert issue_num == "119"
        assert continue_ctx["status"] == "awaiting_human"
        await _ctx.reply_text(continue_ctx["message"])
        return True

    monkeypatch.setattr(continue_service, "_ensure_project_issue_for_command", _ensure)
    monkeypatch.setattr(
        continue_service,
        "_prepare_continue_context",
        lambda *_args, **_kwargs: {
            "status": "awaiting_human",
            "message": "⏸️ Issue #119 is waiting for human action, not another agent launch.",
        },
    )
    monkeypatch.setattr(continue_service, "_handle_continue_status_outcome", _status_outcome)

    async def _unexpected(*_args, **_kwargs):
        raise AssertionError("launch/reset should not run when waiting on human")

    monkeypatch.setattr(continue_service, "_maybe_reset_continue_workflow_position", _unexpected)
    monkeypatch.setattr(continue_service, "_launch_continue_agent", _unexpected)

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        requester_context_builder=None,
    )

    await handle_continue(ctx, deps, finalize_workflow=lambda *a, **k: None)

    assert ctx.replies == ["⏸️ Issue #119 is waiting for human action, not another agent launch."]


@pytest.mark.asyncio
async def test_continue_service_reports_blocked_finalization_for_completed_workflow(monkeypatch):
    ctx = _Ctx(args=["nexus", "119"])
    captured: dict[str, object] = {}

    async def _ensure(_ctx, _deps, _command):
        return "nexus", "119", []

    monkeypatch.setattr(continue_service, "_ensure_project_issue_for_command", _ensure)
    monkeypatch.setattr(
        continue_service,
        "_prepare_continue_context",
        lambda *_args, **_kwargs: {
            "status": "workflow_done_open",
            "repo": "Ghabs95/nexus-arc",
            "resumed_from": "writer",
            "project_name": "nexus",
        },
    )

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        requester_context_builder=None,
    )

    await handle_continue(
        ctx,
        deps,
        finalize_workflow=lambda *a, **k: (
            captured.update({"emit_notifications": k.get("emit_notifications")}) or {
                "finalization_blocked": True,
                "blocking_reasons": ["No non-empty PR/MR diff found in target repos for this workflow."],
            }
        ),
    )

    assert len(ctx.replies) == 1
    assert "running finalization now" in ctx.replies[0]
    assert len(ctx.edits) == 1
    assert captured["emit_notifications"] is False
    assert "finalization is blocked" in ctx.edits[0]["text"]
    assert "Issue remains open." in ctx.edits[0]["text"]
    assert "No non-empty PR/MR diff found in target repos for this workflow." in ctx.edits[0]["text"]


@pytest.mark.asyncio
async def test_continue_service_reports_closed_issue_when_finalization_closes(monkeypatch):
    ctx = _Ctx(args=["nexus", "119"])
    captured: dict[str, object] = {}

    async def _ensure(_ctx, _deps, _command):
        return "nexus", "119", []

    monkeypatch.setattr(continue_service, "_ensure_project_issue_for_command", _ensure)
    monkeypatch.setattr(
        continue_service,
        "_prepare_continue_context",
        lambda *_args, **_kwargs: {
            "status": "workflow_done_open",
            "repo": "Ghabs95/nexus-arc",
            "resumed_from": "writer",
            "project_name": "nexus",
        },
    )

    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        requester_context_builder=None,
    )

    await handle_continue(
        ctx,
        deps,
        finalize_workflow=lambda *a, **k: (
            captured.update({"emit_notifications": k.get("emit_notifications")}) or {
                "issue_closed": True,
                "pr_urls": ["https://github.com/Ghabs95/nexus-arc/pull/120"],
            }
        ),
    )

    assert len(ctx.replies) == 1
    assert "running finalization now" in ctx.replies[0]
    assert len(ctx.edits) == 1
    assert captured["emit_notifications"] is False
    assert "Issue finalized and closed." in ctx.edits[0]["text"]


def test_example_consumer_prepare_continue_context_skips_stale_human_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(control_service, "NEXUS_STORAGE_BACKEND", "filesystem", raising=False)

    completion_file = tmp_path / "completion_summary_119.json"
    completion_file.write_text("{}", encoding="utf-8")

    completion = SimpleNamespace(
        issue_number="119",
        file_path=str(completion_file),
        summary=SimpleNamespace(
            is_workflow_done=False,
            agent_type="reviewer",
            next_agent="human",
        ),
    )

    continue_ctx = control_service.prepare_continue_context(
        issue_num="119",
        project_key="nexus",
        rest_tokens=[],
        base_dir=str(tmp_path),
        project_config={"nexus": {"agents_dir": "agents", "workspace": "."}},
        default_repo="Ghabs95/nexus-arc",
        find_task_file_by_issue=lambda _n: None,
        get_issue_details=lambda _n, _repo=None, requester_nexus_id=None: {
            "state": "open",
            "title": "x",
            "body": "y",
        },
        resolve_project_config_from_task=lambda _p: (
            "nexus",
            {"agents_dir": "agents", "workspace": "."},
        ),
        get_runtime_ops_plugin=lambda **_k: SimpleNamespace(
            find_agent_pid_for_issue=lambda _n: None
        ),
        scan_for_completions=lambda _base: [completion],
        normalize_agent_reference=lambda ref: str(ref or "").strip().lower() or None,
        get_expected_running_agent_from_workflow=lambda _n: "writer",
        get_sop_tier_from_issue=lambda _n, _p: None,
        get_sop_tier=lambda _t: ("full", None, None),
    )

    assert continue_ctx["status"] == "ready"
    assert continue_ctx["agent_type"] == "writer"


def test_example_consumer_prepare_continue_context_prefers_completed_workflow_over_stale_human_handoff(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(control_service, "NEXUS_STORAGE_BACKEND", "postgres", raising=False)
    monkeypatch.setattr(
        control_service,
        "_read_latest_completion_from_storage",
        lambda _issue: {
            "status": "complete",
            "agent_type": "deployer",
            "next_agent": "human",
            "is_workflow_done": False,
            "summary": {},
        },
    )
    monkeypatch.setattr(
        control_service,
        "_read_workflow_status_snapshot",
        lambda _issue: {"state": "completed", "current_agent_type": "writer", "summary": {}},
    )

    continue_ctx = control_service.prepare_continue_context(
        issue_num="119",
        project_key="nexus",
        rest_tokens=[],
        base_dir=str(tmp_path),
        project_config={"nexus": {"agents_dir": "agents", "workspace": "."}},
        default_repo="Ghabs95/nexus-arc",
        find_task_file_by_issue=lambda _n: None,
        get_issue_details=lambda _n, _repo=None, requester_nexus_id=None: {
            "state": "open",
            "title": "x",
            "body": "y",
        },
        resolve_project_config_from_task=lambda _p: (
            "nexus",
            {"agents_dir": "agents", "workspace": "."},
        ),
        get_runtime_ops_plugin=lambda **_k: SimpleNamespace(
            find_agent_pid_for_issue=lambda _n: None
        ),
        scan_for_completions=lambda _base: [],
        normalize_agent_reference=lambda ref: str(ref or "").strip().lower() or None,
        get_expected_running_agent_from_workflow=lambda _n: None,
        get_sop_tier_from_issue=lambda _n, _p: None,
        get_sop_tier=lambda _t: ("full", None, None),
    )

    assert continue_ctx["status"] == "workflow_done_open"
    assert continue_ctx["resumed_from"] == "writer"
