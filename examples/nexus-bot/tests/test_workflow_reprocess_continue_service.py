import logging
from types import SimpleNamespace

import pytest

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
