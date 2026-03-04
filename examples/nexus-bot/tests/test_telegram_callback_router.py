import pytest

from nexus.core.orchestration.telegram.telegram_callback_router import (
    call_core_callback_handler,
    call_core_chat_handler,
)


@pytest.mark.asyncio
async def test_call_core_callback_handler_builds_ctx_and_deps():
    seen = {}

    async def _handler(ctx, deps):
        seen["ctx"] = ctx
        seen["deps"] = deps

    await call_core_callback_handler(
        update="u",
        context="c",
        handler=_handler,
        build_ctx=lambda u, c: ("ctx", u, c),
        deps_factory=lambda: {"deps": 1},
    )

    assert seen["ctx"] == ("ctx", "u", "c")
    assert seen["deps"] == {"deps": 1}


@pytest.mark.asyncio
async def test_call_core_chat_handler_builds_ctx_only():
    seen = {}

    async def _handler(ctx):
        seen["ctx"] = ctx

    await call_core_chat_handler(
        update="u",
        context="c",
        handler=_handler,
        build_ctx=lambda u, c: ("ctx", u, c),
    )

    assert seen["ctx"] == ("ctx", "u", "c")
