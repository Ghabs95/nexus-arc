from types import SimpleNamespace

import pytest

from nexus.core.telegram.telegram_bootstrap_ui_service import handle_help


class _Message:
    def __init__(self):
        self.calls: list[tuple[str, str | None]] = []

    async def reply_text(self, text: str, parse_mode: str | None = None):
        self.calls.append((text, parse_mode))


@pytest.mark.asyncio
async def test_handle_help_sends_plain_text_without_markdown_parse_mode():
    message = _Message()
    logger = SimpleNamespace(info=lambda *_a, **_k: None, warning=lambda *_a, **_k: None)
    update = SimpleNamespace(
        effective_user=SimpleNamespace(id=42),
        message=message,
    )

    await handle_help(update=update, logger=logger, allowed_user_ids=[])

    assert len(message.calls) == 1
    assert message.calls[0][1] is None
    assert "**" not in message.calls[0][0]
