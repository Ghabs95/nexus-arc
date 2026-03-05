import logging
from types import SimpleNamespace
from typing import Any

import pytest

from nexus.core.telegram.telegram_hands_free_service import handle_hands_free_message


class _Msg:
    def __init__(self, *, text=None, voice=None, message_id=1):
        self.text = text
        self.voice = voice
        self.message_id = message_id
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=777)


class _Bot:
    def __init__(self):
        self.deleted = []
        self.edits = []

    async def delete_message(self, **kwargs):
        self.deleted.append(kwargs)

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


def _update(*, user_id=1, text=None, voice=None):
    return SimpleNamespace(
        effective_user=SimpleNamespace(id=user_id),
        effective_chat=SimpleNamespace(id=55),
        message=_Msg(text=text, voice=voice, message_id=123),
    )


def _context(user_data=None):
    ctx = SimpleNamespace(user_data=user_data or {})
    ctx.bot = _Bot()
    return ctx


async def _false_async(*_a, **_k):
    return False


async def _none_transcribe(*_a, **_k):
    return None


def _rename_chat(_user_id: int, _chat_id: Any, _title: str) -> Any:
    return False


@pytest.mark.asyncio
async def test_hands_free_unauthorized_returns_early():
    update = _update(user_id=999, text="hello")
    ctx = _context()

    await handle_hands_free_message(
        update=update,
        context=ctx,
        logger=logging.getLogger("test"),
        allowed_user_ids={1, 2},
        get_active_chat=lambda _u: None,
        rename_chat=_rename_chat,
        chat_menu_handler=_false_async,
        handle_pending_issue_input=_false_async,
        transcribe_voice_message=_none_transcribe,
        inline_keyboard_button_cls=SimpleNamespace,
        inline_keyboard_markup_cls=lambda rows: rows,
        resolve_pending_project_selection=_false_async,
        build_ctx=lambda u, c: (u, c),
        hands_free_routing_deps_factory=lambda: {},
        get_chat=lambda _u: {},
        handle_feature_ideation_request=_false_async,
        feature_ideation_deps_factory=lambda: {},
        route_hands_free_text=_false_async,
    )

    assert update.message.replies == []


@pytest.mark.asyncio
async def test_hands_free_pending_chat_rename_cancel():
    update = _update(text="cancel")
    ctx = _context({"pending_chat_rename": True})

    await handle_hands_free_message(
        update=update,
        context=ctx,
        logger=logging.getLogger("test"),
        allowed_user_ids=None,
        get_active_chat=lambda _u: None,
        rename_chat=_rename_chat,
        chat_menu_handler=_false_async,
        handle_pending_issue_input=_false_async,
        transcribe_voice_message=_none_transcribe,
        inline_keyboard_button_cls=SimpleNamespace,
        inline_keyboard_markup_cls=lambda rows: rows,
        resolve_pending_project_selection=_false_async,
        build_ctx=lambda u, c: (u, c),
        hands_free_routing_deps_factory=lambda: {},
        get_chat=lambda _u: {},
        handle_feature_ideation_request=_false_async,
        feature_ideation_deps_factory=lambda: {},
        route_hands_free_text=_false_async,
    )

    assert "pending_chat_rename" not in ctx.user_data
    assert update.message.replies[-1][0] == "❎ Rename canceled."


@pytest.mark.asyncio
async def test_hands_free_command_guard_ignores_commands():
    update = _update(text="/help")
    ctx = _context()
    called = {"route": False}

    async def _route(*_a, **_k):
        called["route"] = True

    await handle_hands_free_message(
        update=update,
        context=ctx,
        logger=logging.getLogger("test"),
        allowed_user_ids=None,
        get_active_chat=lambda _u: None,
        rename_chat=_rename_chat,
        chat_menu_handler=_false_async,
        handle_pending_issue_input=_false_async,
        transcribe_voice_message=_none_transcribe,
        inline_keyboard_button_cls=SimpleNamespace,
        inline_keyboard_markup_cls=lambda rows: rows,
        resolve_pending_project_selection=_false_async,
        build_ctx=lambda u, c: (u, c),
        hands_free_routing_deps_factory=lambda: {},
        get_chat=lambda _u: {},
        handle_feature_ideation_request=_false_async,
        feature_ideation_deps_factory=lambda: {},
        route_hands_free_text=_route,
    )

    assert called["route"] is False
    assert update.message.replies == []


@pytest.mark.asyncio
async def test_hands_free_pending_task_edit_builds_confirmation_preview():
    update = _update(text="revised task")
    ctx = _context({"pending_task_edit": True})

    class _Btn:
        def __init__(self, text, callback_data=None):
            self.text = text
            self.callback_data = callback_data

    class _Markup:
        def __init__(self, rows):
            self.rows = rows

    await handle_hands_free_message(
        update=update,
        context=ctx,
        logger=logging.getLogger("test"),
        allowed_user_ids=None,
        get_active_chat=lambda _u: None,
        rename_chat=_rename_chat,
        chat_menu_handler=_false_async,
        handle_pending_issue_input=_false_async,
        transcribe_voice_message=_none_transcribe,
        inline_keyboard_button_cls=_Btn,
        inline_keyboard_markup_cls=_Markup,
        resolve_pending_project_selection=_false_async,
        build_ctx=lambda u, c: (u, c),
        hands_free_routing_deps_factory=lambda: {},
        get_chat=lambda _u: {},
        handle_feature_ideation_request=_false_async,
        feature_ideation_deps_factory=lambda: {},
        route_hands_free_text=_false_async,
    )

    assert ctx.user_data["pending_task_edit"] is False
    assert ctx.user_data["pending_task_confirmation"]["text"] == "revised task"
    text, kwargs = update.message.replies[-1]
    assert "Confirm task creation" in text
    labels = [row[0].text for row in kwargs["reply_markup"].rows]
    assert labels == ["✅ Confirm", "✏️ Edit", "❌ Cancel"]
