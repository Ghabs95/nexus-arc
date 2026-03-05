import pytest

from nexus.core.orchestration.telegram.telegram_command_router import dispatch_command


@pytest.mark.asyncio
async def test_dispatch_command_sets_project_issue_args_and_calls_handler():
    called = {"args": None}

    async def _handler(update, context):
        called["args"] = list(context.args)

    class _Ctx:
        args = []

    await dispatch_command(
        update=object(),
        context=_Ctx(),
        command="status",
        project_key="proj-a",
        issue_num="42",
        rest=["tail"],
        command_handler_map=lambda: {"status": _handler},
        reply_unsupported=lambda _u: _handler(_u, _Ctx()),
    )

    assert called["args"] == ["proj-a", "42", "tail"]


@pytest.mark.asyncio
async def test_dispatch_command_project_only_command_uses_project_args():
    called = {"args": None}

    async def _handler(update, context):
        called["args"] = list(context.args)

    class _Ctx:
        args = []

    await dispatch_command(
        update=object(),
        context=_Ctx(),
        command="agents",
        project_key="proj-a",
        issue_num="42",
        rest=["extra"],
        command_handler_map=lambda: {"agents": _handler},
        reply_unsupported=lambda _u: _handler(_u, _Ctx()),
    )

    assert called["args"] == ["proj-a", "extra"]
