"""Unit tests for /watch command handler."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_ctx(user_id: str = "12345", chat_id: str = "777", args: list[str] | None = None):
    ctx = MagicMock()
    ctx.user_id = user_id
    ctx.chat_id = chat_id
    ctx.channel = "telegram"
    ctx.args = args or []
    ctx.reply_text = AsyncMock()
    return ctx


class _FakeWatchService:
    def __init__(self):
        self.enabled = True
        self.started_with = None
        self.stopped_with = None
        self.mermaid_with = None
        self.status_payload = None
        self.stop_result = 0
        self.mermaid_result = False
        self.start_result = {"ok": True, "replaced": False}

    def is_enabled(self) -> bool:
        return self.enabled

    def get_status(self, *, chat_id: int, user_id: int):
        return self.status_payload

    def start_watch(self, *, chat_id: int, user_id: int, project_key: str, issue_num: str):
        self.started_with = {
            "chat_id": chat_id,
            "user_id": user_id,
            "project_key": project_key,
            "issue_num": issue_num,
        }
        return self.start_result

    def stop_watch(self, *, chat_id: int, user_id: int, project_key=None, issue_num=None):
        self.stopped_with = {
            "chat_id": chat_id,
            "user_id": user_id,
            "project_key": project_key,
            "issue_num": issue_num,
        }
        return self.stop_result

    def set_mermaid(self, *, chat_id: int, user_id: int, enabled: bool):
        self.mermaid_with = {"chat_id": chat_id, "user_id": user_id, "enabled": enabled}
        return self.mermaid_result


def _make_deps(service: _FakeWatchService):
    from nexus.core.handlers.watch_command_handlers import WatchHandlerDeps

    return WatchHandlerDeps(
        logger=MagicMock(),
        allowed_user_ids=[],
        prompt_project_selection=AsyncMock(),
        ensure_project_issue=AsyncMock(return_value=("nexus", "106", [])),
        get_watch_service=lambda: service,
    )


@pytest.mark.asyncio
async def test_watch_prompts_project_selection_when_no_args():
    from nexus.core.handlers.watch_command_handlers import watch_handler

    ctx = _make_ctx(args=[])
    service = _FakeWatchService()
    deps = _make_deps(service)

    await watch_handler(ctx, deps)

    deps.prompt_project_selection.assert_awaited_once_with(ctx, "watch")


@pytest.mark.asyncio
async def test_watch_start_subscribes_issue():
    from nexus.core.handlers.watch_command_handlers import watch_handler

    ctx = _make_ctx(args=["nexus", "106"])
    service = _FakeWatchService()
    deps = _make_deps(service)

    await watch_handler(ctx, deps)

    assert service.started_with == {
        "chat_id": 777,
        "user_id": 12345,
        "project_key": "nexus",
        "issue_num": "106",
    }
    ctx.reply_text.assert_awaited_once()
    assert "Started live watch" in ctx.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_watch_status_renders_active_session():
    from nexus.core.handlers.watch_command_handlers import watch_handler

    ctx = _make_ctx(args=["status"])
    service = _FakeWatchService()
    service.status_payload = {"project_key": "nexus", "issue_num": "106", "mermaid_enabled": True}
    deps = _make_deps(service)

    await watch_handler(ctx, deps)

    ctx.reply_text.assert_awaited_once()
    text = ctx.reply_text.call_args.args[0]
    assert "Active watch" in text
    assert "#106" in text


@pytest.mark.asyncio
async def test_watch_stop_and_mermaid_toggle():
    from nexus.core.handlers.watch_command_handlers import watch_handler

    service = _FakeWatchService()
    deps = _make_deps(service)

    stop_ctx = _make_ctx(args=["stop"])
    service.stop_result = 1
    await watch_handler(stop_ctx, deps)
    assert service.stopped_with == {
        "chat_id": 777,
        "user_id": 12345,
        "project_key": None,
        "issue_num": None,
    }
    assert "Stopped workflow watch" in stop_ctx.reply_text.call_args.args[0]

    mermaid_ctx = _make_ctx(args=["mermaid", "on"])
    service.mermaid_result = True
    await watch_handler(mermaid_ctx, deps)
    assert service.mermaid_with == {"chat_id": 777, "user_id": 12345, "enabled": True}
    assert "Mermaid updates are now" in mermaid_ctx.reply_text.call_args.args[0]
