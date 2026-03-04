import logging
from types import SimpleNamespace

import pytest
from nexus.core.handlers.monitoring_command_handlers import fuse_handler, tailstop_handler


class _Ctx:
    def __init__(self):
        self.user_id = "1"
        self.chat_id = 10
        self.args = []
        self.replies = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return "msg"


class _Task:
    def __init__(self, done=False):
        self._done = done
        self.cancelled = False

    def done(self):
        return self._done

    def cancel(self):
        self.cancelled = True


@pytest.mark.asyncio
async def test_tailstop_stops_active_session_and_cancels_task():
    ctx = _Ctx()
    task = _Task(done=False)
    session_key = (ctx.chat_id, int(ctx.user_id))
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        active_tail_sessions={session_key: "tok"},
        active_tail_tasks={session_key: task},
    )

    await tailstop_handler(ctx, deps)

    assert task.cancelled is True
    assert session_key not in deps.active_tail_sessions
    assert session_key not in deps.active_tail_tasks
    assert "Stopped live tail session" in ctx.replies[-1][0]


@pytest.mark.asyncio
async def test_tailstop_reports_when_no_active_session():
    ctx = _Ctx()
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        active_tail_sessions={},
        active_tail_tasks={},
    )

    await tailstop_handler(ctx, deps)

    assert "No active live tail session" in ctx.replies[-1][0]


@pytest.mark.asyncio
async def test_fuse_prompts_with_close_when_no_args():
    ctx = _Ctx()
    deps = SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        iter_project_keys=lambda: ["nexus"],
        get_project_label=lambda _pk: "Nexus",
    )

    await fuse_handler(ctx, deps)

    assert "Please select a project to view fuse status" in ctx.replies[-1][0]
    buttons = ctx.replies[-1][1]["buttons"]
    assert any(getattr(btn, "label", "") == "❌ Close" for row in buttons for btn in row)
