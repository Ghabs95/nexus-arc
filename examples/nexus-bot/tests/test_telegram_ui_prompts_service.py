from types import SimpleNamespace

import pytest
from nexus.core.telegram.telegram_ui_prompts_service import (
    prompt_issue_selection,
    prompt_project_selection,
)


class _Btn:
    def __init__(self, text, callback_data=None):
        self.text = text
        self.callback_data = callback_data


class _Markup:
    def __init__(self, keyboard):
        self.keyboard = keyboard


class _Msg:
    def __init__(self):
        self.calls = []

    async def reply_text(self, text, reply_markup=None):
        self.calls.append((text, reply_markup))


class _Query:
    def __init__(self):
        self.calls = []

    async def edit_message_text(self, text, reply_markup=None):
        self.calls.append((text, reply_markup))


@pytest.mark.asyncio
async def test_prompt_issue_selection_empty_open_state_shows_toggle_and_manual():
    msg = _Msg()
    update = SimpleNamespace(effective_message=msg, callback_query=None)

    await prompt_issue_selection(
        update=update,
        command="status",
        project_key="proj",
        list_project_issues=lambda *_a, **_k: [],
        get_project_label=lambda k: f"Label-{k}",
        inline_keyboard_button_cls=_Btn,
        inline_keyboard_markup_cls=_Markup,
        issue_state="open",
    )

    text, markup = msg.calls[-1]
    assert text == "No open issues found for Label-proj."
    labels = [row[0].text for row in markup.keyboard]
    assert labels == ["📦 Closed issues", "✏️ Enter manually", "❌ Close"]


@pytest.mark.asyncio
async def test_prompt_issue_selection_populated_closed_state_uses_edit_message():
    query = _Query()
    update = SimpleNamespace(effective_message=_Msg(), callback_query=query)
    issues = [{"number": 7, "title": "A" * 80}]

    await prompt_issue_selection(
        update=update,
        command="logs",
        project_key="proj",
        list_project_issues=lambda *_a, **_k: issues,
        get_project_label=lambda _k: "Project One",
        inline_keyboard_button_cls=_Btn,
        inline_keyboard_markup_cls=_Markup,
        issue_state="closed",
        edit_message=True,
    )

    text, markup = query.calls[-1]
    assert text == "📦 Closed issues for /logs (Project One):"
    first_label = markup.keyboard[0][0].text
    assert first_label.startswith("#7 — ")
    assert first_label.endswith("...")
    tail_labels = [row[0].text for row in markup.keyboard[-3:]]
    assert tail_labels == ["🔓 Open issues", "✏️ Enter manually", "❌ Close"]


@pytest.mark.asyncio
async def test_prompt_project_selection_single_project_agents_dispatches_directly():
    msg = _Msg()
    update = SimpleNamespace(effective_message=msg)
    ctx = SimpleNamespace(user_data={})
    seen = {}

    async def _dispatch(update, context, command, project_key, issue_num):
        seen["dispatch"] = (command, project_key, issue_num)

    async def _prompt_issue(update, context, command, project_key):
        seen["prompt_issue"] = (command, project_key)

    await prompt_project_selection(
        update=update,
        context=ctx,
        command="agents",
        get_single_project_key=lambda: "proj",
        dispatch_command=_dispatch,
        prompt_issue_selection=_prompt_issue,
        iter_project_keys=lambda: ["proj"],
        get_project_label=lambda k: k,
        inline_keyboard_button_cls=_Btn,
        inline_keyboard_markup_cls=_Markup,
    )

    assert ctx.user_data["pending_command"] == "agents"
    assert ctx.user_data["pending_project"] == "proj"
    assert seen["dispatch"] == ("agents", "proj", "")
    assert "prompt_issue" not in seen
    assert msg.calls == []


@pytest.mark.asyncio
async def test_prompt_project_selection_multi_project_shows_buttons():
    msg = _Msg()
    update = SimpleNamespace(effective_message=msg)
    ctx = SimpleNamespace(user_data={})

    async def _dispatch(*_a, **_k):
        raise AssertionError("should not dispatch")

    async def _prompt_issue(*_a, **_k):
        raise AssertionError("should not prompt issue")

    await prompt_project_selection(
        update=update,
        context=ctx,
        command="status",
        get_single_project_key=lambda: None,
        dispatch_command=_dispatch,
        prompt_issue_selection=_prompt_issue,
        iter_project_keys=lambda: ["a", "b"],
        get_project_label=lambda k: f"L-{k}",
        inline_keyboard_button_cls=_Btn,
        inline_keyboard_markup_cls=_Markup,
    )

    text, markup = msg.calls[-1]
    assert text == "Select a project for /status:"
    assert [row[0].text for row in markup.keyboard] == ["L-a", "L-b", "❌ Close"]
    assert ctx.user_data["pending_command"] == "status"
