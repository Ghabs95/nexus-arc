import pytest

from nexus.core.callbacks.callback_registry_service import dispatch_callback_action


@pytest.mark.asyncio
async def test_dispatch_callback_action_uses_specific_handler():
    seen: list[str] = []

    async def _a():
        seen.append("a")

    async def _default():
        seen.append("default")

    handled = await dispatch_callback_action(
        action="a",
        handlers={"a": _a},
        default_handler=_default,
    )
    assert handled is True
    assert seen == ["a"]


@pytest.mark.asyncio
async def test_dispatch_callback_action_uses_default_or_returns_false():
    seen: list[str] = []

    async def _default():
        seen.append("default")

    handled = await dispatch_callback_action(
        action="missing",
        handlers={},
        default_handler=_default,
    )
    assert handled is True
    assert seen == ["default"]

    handled_none = await dispatch_callback_action(
        action="missing",
        handlers={},
        default_handler=None,
    )
    assert handled_none is False
