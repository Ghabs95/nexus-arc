import logging
from pathlib import Path
from types import SimpleNamespace

import pytest
from nexus.core.telegram.telegram_task_capture_service import (
    handle_save_task_selection,
    handle_task_confirmation_callback,
)


class _Query:
    def __init__(self, data="taskconfirm:confirm", message_id=123):
        self.data = data
        self.message = SimpleNamespace(message_id=message_id)
        self.answered = False
        self.edits = []

    async def answer(self):
        self.answered = True

    async def edit_message_text(self, text):
        self.edits.append(text)


class _Context:
    def __init__(self, user_data=None):
        self.user_data = user_data or {}


class _MsgObj:
    def __init__(self, text=None, voice=None, message_id=1):
        self.text = text
        self.voice = voice
        self.message_id = message_id
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return SimpleNamespace(message_id=999)


def _update(query, user_id=1):
    return SimpleNamespace(callback_query=query, effective_user=SimpleNamespace(id=user_id))


@pytest.mark.asyncio
async def test_task_confirmation_expired():
    query = _Query()
    ctx = _Context({})

    async def _route_task_with_context(**kwargs):
        raise AssertionError("should not be called")

    await handle_task_confirmation_callback(
        update=_update(query),
        context=ctx,
        allowed_user_ids=None,
        logger=logging.getLogger("test"),
        route_task_with_context=_route_task_with_context,
        orchestrator=object(),
        get_chat=lambda *_a, **_k: None,
        process_inbox_task=lambda *_a, **_k: None,
    )

    assert query.answered is True
    assert query.edits[-1] == "⚠️ Task confirmation expired. Send the request again."


@pytest.mark.asyncio
async def test_task_confirmation_cancel_clears_pending():
    query = _Query(data="taskconfirm:cancel")
    ctx = _Context(
        {
            "pending_task_confirmation": {"text": "x"},
            "pending_task_edit": True,
        }
    )

    async def _route_task_with_context(**kwargs):
        raise AssertionError("should not be called")

    await handle_task_confirmation_callback(
        update=_update(query),
        context=ctx,
        allowed_user_ids=None,
        logger=logging.getLogger("test"),
        route_task_with_context=_route_task_with_context,
        orchestrator=object(),
        get_chat=lambda *_a, **_k: None,
        process_inbox_task=lambda *_a, **_k: None,
    )

    assert "pending_task_confirmation" not in ctx.user_data
    assert "pending_task_edit" not in ctx.user_data
    assert query.edits[-1] == "❎ Task creation canceled."


@pytest.mark.asyncio
async def test_task_confirmation_edit_sets_pending_edit():
    query = _Query(data="taskconfirm:edit")
    ctx = _Context({"pending_task_confirmation": {"text": "x"}})

    async def _route_task_with_context(**kwargs):
        raise AssertionError("should not be called")

    await handle_task_confirmation_callback(
        update=_update(query),
        context=ctx,
        allowed_user_ids=None,
        logger=logging.getLogger("test"),
        route_task_with_context=_route_task_with_context,
        orchestrator=object(),
        get_chat=lambda *_a, **_k: None,
        process_inbox_task=lambda *_a, **_k: None,
    )

    assert ctx.user_data["pending_task_edit"] is True
    assert "Send the updated task text now" in query.edits[-1]


@pytest.mark.asyncio
async def test_task_confirmation_confirm_routes_and_sets_pending_resolution():
    query = _Query(data="taskconfirm:confirm", message_id=55)
    ctx = _Context({"pending_task_confirmation": {"text": " hello "}})
    seen = {}

    async def _route_task_with_context(**kwargs):
        seen.update(kwargs)
        return {
            "success": False,
            "pending_resolution": {"choices": ["a"]},
            "message": "Need project",
        }

    await handle_task_confirmation_callback(
        update=_update(query, user_id=42),
        context=ctx,
        allowed_user_ids=None,
        logger=logging.getLogger("test"),
        route_task_with_context=_route_task_with_context,
        orchestrator="orch",
        get_chat=lambda *_a, **_k: None,
        process_inbox_task=lambda *_a, **_k: None,
    )

    assert seen["user_id"] == 42
    assert seen["text"] == "hello"
    assert seen["message_id"] == "55"
    assert seen["orchestrator"] == "orch"
    assert "pending_task_confirmation" not in ctx.user_data
    assert ctx.user_data["pending_task_project_resolution"] == {"choices": ["a"]}
    assert query.edits[-1] == "Need project"


@pytest.mark.asyncio
async def test_task_confirmation_unauthorized_returns_after_answer():
    query = _Query(data="taskconfirm:confirm")
    ctx = _Context({"pending_task_confirmation": {"text": "x"}})

    async def _route_task_with_context(**kwargs):
        raise AssertionError("should not be called")

    await handle_task_confirmation_callback(
        update=_update(query, user_id=999),
        context=ctx,
        allowed_user_ids={1, 2},
        logger=logging.getLogger("test"),
        route_task_with_context=_route_task_with_context,
        orchestrator=object(),
        get_chat=lambda *_a, **_k: None,
        process_inbox_task=lambda *_a, **_k: None,
    )

    assert query.answered is True
    assert query.edits == []
    assert "pending_task_confirmation" in ctx.user_data


@pytest.mark.asyncio
async def test_save_task_selection_text_writes_file(tmp_path: Path):
    msg = _MsgObj(text="raw text", message_id=321)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=msg,
        effective_chat=SimpleNamespace(id=99),
    )
    ctx = _Context({"project": "proj", "type": "bug"})
    ctx.bot = SimpleNamespace(delete_message=lambda **_k: None)

    end = object()

    async def _transcribe_unused(*_a, **_k):
        return None

    out = await handle_save_task_selection(
        update=update,
        context=ctx,
        logger=logging.getLogger("test"),
        orchestrator=object(),
        projects={"proj": "Project A"},
        types_map={"bug": "Bug"},
        project_config={"proj": {"workspace": "ws"}},
        base_dir=str(tmp_path),
        get_inbox_storage_backend=lambda: "filesystem",
        enqueue_task=lambda **_k: 0,
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox" / project),
        transcribe_voice_message=_transcribe_unused,
        conversation_end=end,
    )

    assert out is end
    assert "✅ Saved to inbox" in msg.replies[-1][0]
    assert "Project: proj" in msg.replies[-1][0]
    assert "File: bug_321.md" in msg.replies[-1][0]
    content = (tmp_path / "inbox" / "proj" / "bug_321.md").read_text()
    assert "# Bug" in content
    assert "**Project:** Project A" in content
    assert "**Task Name:**" not in content
    assert "raw text" in content
    assert "**Raw Input:**\nraw text" in content


@pytest.mark.asyncio
async def test_save_task_selection_postgres_queues_task(tmp_path: Path):
    msg = _MsgObj(text="raw text", message_id=456)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=msg,
        effective_chat=SimpleNamespace(id=99),
    )
    ctx = _Context({"project": "proj", "type": "bug"})
    ctx.bot = SimpleNamespace(delete_message=lambda **_k: None)
    captured = {}

    end = object()

    async def _transcribe_unused(*_a, **_k):
        return None

    def _enqueue_task(**kwargs):
        captured.update(kwargs)
        return 77

    out = await handle_save_task_selection(
        update=update,
        context=ctx,
        logger=logging.getLogger("test"),
        orchestrator=object(),
        projects={"proj": "Project A"},
        types_map={"bug": "Bug"},
        project_config={"proj": {"workspace": "ws"}},
        base_dir=str(tmp_path),
        get_inbox_storage_backend=lambda: "postgres",
        enqueue_task=_enqueue_task,
        get_inbox_dir=lambda root, project: str(tmp_path / "inbox" / project),
        transcribe_voice_message=_transcribe_unused,
        conversation_end=end,
    )

    assert out is end
    assert captured["project_key"] == "proj"
    assert captured["workspace"] == "ws"
    assert captured["filename"] == "bug_456.md"
    assert "# Bug" in captured["markdown_content"]
    assert "raw text" in captured["markdown_content"]
    assert "✅ Queued to inbox" in msg.replies[-1][0]
    assert "Project: proj" in msg.replies[-1][0]
    assert "Queue ID: 77" in msg.replies[-1][0]
    assert not (tmp_path / "inbox" / "proj" / "bug_456.md").exists()


@pytest.mark.asyncio
async def test_save_task_selection_failed_transcription_returns_end():
    voice = SimpleNamespace(file_id="v1")
    msg = _MsgObj(text=None, voice=voice, message_id=8)
    deleted = {}

    async def _delete_message(**kwargs):
        deleted.update(kwargs)

    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=7),
        message=msg,
        effective_chat=SimpleNamespace(id=99),
    )
    ctx = _Context({"project": "proj", "type": "bug"})
    ctx.bot = SimpleNamespace(delete_message=_delete_message)

    end = object()

    async def _transcribe_none(*_a, **_k):
        return None

    out = await handle_save_task_selection(
        update=update,
        context=ctx,
        logger=logging.getLogger("test"),
        orchestrator=object(),
        projects={"proj": "Project A"},
        types_map={"bug": "Bug"},
        project_config={},
        base_dir="/tmp",
        get_inbox_storage_backend=lambda: "filesystem",
        enqueue_task=lambda **_k: 0,
        get_inbox_dir=lambda root, project: f"/tmp/{project}",
        transcribe_voice_message=_transcribe_none,
        conversation_end=end,
    )

    assert out is end
    assert any("Transcribing" in t for t, _ in msg.replies)
    assert msg.replies[-1][0] == "⚠️ Transcription failed. Please try again."
    assert deleted["chat_id"] == 99
