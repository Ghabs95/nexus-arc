import types

import discord_bot as svc
import pytest


class _Response:
    def __init__(self):
        self.calls = []

    def is_done(self):
        return False

    async def edit_message(self, **kwargs):
        self.calls.append(("edit_message", kwargs))

    async def send_message(self, *args, **kwargs):
        self.calls.append(("send_message", args, kwargs))


class _Interaction:
    def __init__(self, user_id=7):
        self.user = types.SimpleNamespace(id=user_id)
        self.response = _Response()


@pytest.mark.asyncio
async def test_send_chat_menu_activates_chat_session(monkeypatch):
    monkeypatch.setattr(svc, "get_active_chat", lambda _user_id: "chat-1")
    monkeypatch.setattr(svc, "_autoselect_chat_project_from_auth", lambda *_args: (False, None))
    monkeypatch.setattr(svc, "list_chats", lambda _user_id: [{"id": "chat-1", "title": "Main"}])
    monkeypatch.setattr(svc, "get_chat", lambda _user_id, _chat_id: {"metadata": {}})
    svc._discord_chat_sessions.clear()

    interaction = _Interaction()
    await svc.send_chat_menu(interaction, 7)

    assert 7 in svc._discord_chat_sessions
    kind, kwargs = interaction.response.calls[-1]
    assert kind == "edit_message"
    assert "Exit chat mode to use hands-free task creation" in kwargs["content"]


@pytest.mark.asyncio
async def test_chat_menu_exit_button_clears_chat_session():
    svc._discord_chat_sessions.clear()
    svc._discord_chat_sessions.add(7)

    interaction = _Interaction()
    view = svc.ChatMenuView(user_id=7)
    exit_button = next(child for child in view.children if getattr(child, "custom_id", "") == "chat:exit")
    await exit_button.callback(interaction)

    assert 7 not in svc._discord_chat_sessions
    kind, kwargs = interaction.response.calls[-1]
    assert kind == "edit_message"
    assert "Chat mode exited" in kwargs["content"]


@pytest.mark.asyncio
async def test_setup_hook_removes_filesystem_only_commands_in_db_mode(monkeypatch):
    removed = []
    synced = []

    monkeypatch.setattr(svc, "DISCORD_GUILD_ID", None)
    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: types.SimpleNamespace(local_task_files=False),
    )
    monkeypatch.setattr(svc.bot.tree, "remove_command", lambda name: removed.append(name))

    async def _sync(guild=None):
        synced.append(guild)
        return []

    monkeypatch.setattr(svc.bot.tree, "sync", _sync)
    monkeypatch.setattr(svc.bot.tree, "copy_global_to", lambda guild=None: None)
    monkeypatch.setattr(svc.bot.tree, "clear_commands", lambda guild=None: None)

    await svc.setup_hook()

    assert set(removed) == set(svc.FILESYSTEM_ONLY_COMMANDS)
    assert synced == [None]
