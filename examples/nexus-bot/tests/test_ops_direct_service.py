import logging
from types import SimpleNamespace

import pytest
from nexus.core import ops_direct_service as svc
from nexus.core.ops_direct_service import handle_direct_request


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


@pytest.mark.asyncio
async def test_direct_service_usage_returns_handled():
    ctx = _Ctx(args=["only", "two"])
    deps = SimpleNamespace(logger=logging.getLogger("test"), allowed_user_ids=[], project_config={})
    handled = await handle_direct_request(
        ctx,
        deps,
        resolve_agent_type=lambda *a, **k: None,
        build_direct_chat_persona=lambda *a, **k: "",
    )
    assert handled is True
    assert "Usage: /direct" in ctx.replies[-1]


@pytest.mark.asyncio
async def test_direct_service_handles_issue_fallback_path():
    ctx = _Ctx(args=["acme", "@dev", "hello"])
    plugin = SimpleNamespace(
        create_issue=lambda **_k: "https://github.com/acme/repo/issues/12",
        add_comment=lambda *_a, **_k: None,
    )
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        project_config={"acme": {"agents_dir": "agents"}},
        base_dir="/tmp",
        nexus_dir_name="nexus",
        get_repo=lambda project: "acme/repo",
        get_direct_issue_plugin=lambda repo: plugin,
    )
    original = svc.resolve_agents_for_project
    try:
        svc.resolve_agents_for_project = lambda *_a, **_k: {"dev": "developer.md"}
        handled = await handle_direct_request(
            ctx,
            deps,
            resolve_agent_type=lambda *a, **k: None,
            build_direct_chat_persona=lambda *a, **k: "",
        )
    finally:
        svc.resolve_agents_for_project = original
    assert handled is True
    assert ctx.edits
    assert "Direct request created" in str(ctx.edits[-1].get("text", ""))
