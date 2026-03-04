import asyncio
from types import SimpleNamespace

import pytest
from nexus.core.monitoring.monitoring_logs_service import handle_logs, handle_logsfull, handle_tail


class _Ctx:
    def __init__(self, args=None):
        self.args = args or []
        self.user_id = "1"
        self.chat_id = 10
        self.replies = []
        self.edits = []

    async def reply_text(self, text, **kwargs):
        self.replies.append((text, kwargs))
        return "msg-1"

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


def _deps():
    class _Logger:
        def info(self, *a, **k):
            pass

        def warning(self, *a, **k):
            pass

        def error(self, *a, **k):
            pass

    return SimpleNamespace(
        logger=_Logger(),
        allowed_user_ids=[],
        iter_project_keys=lambda: ["acme"],
        get_project_label=lambda pk: "Acme",
        ensure_project_issue=lambda ctx, cmd: asyncio.sleep(0, result=("acme", "42", [])),
        project_repo=lambda pk: "owner/repo",
        get_issue_details=lambda issue, repo=None: None,
        find_task_file_by_issue=lambda issue: None,
        find_issue_log_files=lambda issue, task_file=None: [],
        read_latest_log_tail=lambda task_file, max_lines=200: [],
        search_logs_for_issue=lambda issue: [],
        project_config={},
        build_issue_url=lambda *a, **k: "u",
        read_latest_log_full=lambda task_file: [],
        base_dir="/tmp",
        read_log_matches=lambda *a, **k: [],
        get_project_logs_dir=lambda project_key: None,
        get_inbox_storage_backend=lambda: "filesystem",
        active_tail_sessions={},
        active_tail_tasks={},
    )


@pytest.mark.asyncio
async def test_logs_prompts_for_project_without_args():
    ctx = _Ctx()
    await handle_logs(ctx, _deps())
    assert "Please select a project to view logs" in ctx.replies[-1][0]
    buttons = ctx.replies[-1][1]["buttons"]
    assert any(getattr(btn, "label", "") == "❌ Close" for row in buttons for btn in row)


@pytest.mark.asyncio
async def test_logsfull_prompts_for_project_without_args():
    ctx = _Ctx()
    await handle_logsfull(ctx, _deps())
    assert "Please select a project to view full logs" in ctx.replies[-1][0]
    buttons = ctx.replies[-1][1]["buttons"]
    assert any(getattr(btn, "label", "") == "❌ Close" for row in buttons for btn in row)


@pytest.mark.asyncio
async def test_tail_prompts_with_close_when_no_args():
    ctx = _Ctx()
    await handle_tail(ctx, _deps())
    assert "Please select a project to tail logs" in ctx.replies[-1][0]
    buttons = ctx.replies[-1][1]["buttons"]
    assert any(getattr(btn, "label", "") == "❌ Close" for row in buttons for btn in row)


@pytest.mark.asyncio
async def test_tail_validates_non_numeric_lines():
    ctx = _Ctx(args=["acme", "42", "bad"])
    deps = _deps()
    deps.ensure_project_issue = lambda ctx, cmd: asyncio.sleep(0, result=("acme", "42", ["bad"]))
    await handle_tail(ctx, deps)
    assert "Line count must be a number" in ctx.replies[-1][0]
