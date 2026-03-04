import pytest

from nexus.core.callbacks.callback_menu_service import handle_menu_callback, menu_section_text


class _Ctx:
    def __init__(self, data: str, text: str = "orig"):
        self.query = type("Q", (), {"data": data})()
        self.text = text
        self.calls = []

    async def answer_callback_query(self):
        self.calls.append(("answer",))

    async def edit_message_text(self, text, buttons=None):
        self.calls.append(("edit", text, buttons))


def test_menu_section_text_unknown():
    assert menu_section_text("missing") == "Unknown menu option."


@pytest.mark.asyncio
async def test_handle_menu_callback_root_renders_root_menu():
    ctx = _Ctx("menu:root")
    await handle_menu_callback(ctx)
    kind, text, buttons = ctx.calls[-1]
    assert kind == "edit"
    assert "Nexus Menu" in text
    assert buttons[0][0].callback_data == "menu:chat"


@pytest.mark.asyncio
async def test_handle_menu_callback_close_clears_buttons():
    ctx = _Ctx("menu:close", text="same")
    await handle_menu_callback(ctx)
    assert ctx.calls[-1] == ("edit", "same", [])
