import pytest

from nexus.core.handlers.callback_command_handlers import (
    CallbackHandlerDeps,
    route_feedback_callback_handler,
)


class _Ctx:
    def __init__(self, data: str, pending: dict):
        self.user_id = "42"
        self.user_state = {"router_feedback_pending": pending}
        self.query = type("Q", (), {"action_data": data, "message_id": "901"})()
        self.edits = []

    async def answer_callback_query(self, text=None):
        return None

    async def edit_message_text(self, *, message_id=None, text, buttons=None, parse_mode="Markdown", disable_web_page_preview=True):
        self.edits.append({"message_id": message_id, "text": text, "buttons": buttons})


def _deps(url: str = "http://router"):
    return CallbackHandlerDeps(
        logger=type("L", (), {})(),
        prompt_issue_selection=None,
        dispatch_command=None,
        get_project_label=None,
        get_repo=None,
        get_direct_issue_plugin=None,
        get_workflow_state_plugin=None,
        workflow_state_plugin_kwargs={},
        action_handlers={},
        report_bug_action=None,
        router_feedback_url=url,
    )


@pytest.mark.asyncio
async def test_route_feedback_callback_submits_router_backed_feedback(monkeypatch):
    seen = {}

    def _submit_feedback(*, router_url, payload, timeout_seconds=3.0, fallback_store_path=None):
        seen["router_url"] = router_url
        seen["payload"] = payload
        return (True, '{"ok":true}')

    monkeypatch.setattr(
        "nexus.core.handlers.callback_command_handlers.submit_feedback",
        _submit_feedback,
    )
    ctx = _Ctx(
        "routefb:fix:dec-1:reasoning",
        {"decision_id": "dec-1", "source_channel": "telegram", "metadata": {}},
    )

    await route_feedback_callback_handler(ctx, _deps())

    assert seen["router_url"] == "http://router"
    assert seen["payload"]["decision_id"] == "dec-1"
    assert seen["payload"]["corrected_task"] == "reasoning"
    assert ctx.edits[-1]["text"] == "✅ Marked wrong → reasoning."


@pytest.mark.asyncio
async def test_route_feedback_callback_submits_fallback_feedback(monkeypatch):
    seen = {}

    def _submit_feedback(*, router_url, payload, timeout_seconds=3.0, fallback_store_path=None):
        seen["router_url"] = router_url
        seen["payload"] = payload
        return (True, fallback_store_path or "stored")

    monkeypatch.setattr(
        "nexus.core.handlers.callback_command_handlers.submit_feedback",
        _submit_feedback,
    )
    ctx = _Ctx(
        "routefb:ok:fallback-1",
        {
            "decision_id": "fallback-1",
            "feedback_mode": "fallback",
            "source_channel": "telegram",
            "metadata": {"project": "nexus", "content": "Nexus: ship it"},
        },
    )

    await route_feedback_callback_handler(ctx, _deps())

    assert seen["router_url"] == "http://router"
    assert seen["payload"]["metadata"]["feedback_mode"] == "fallback"
    assert ctx.edits[-1]["text"] == "✅ Feedback recorded."


@pytest.mark.asyncio
async def test_route_feedback_callback_deduplicates(monkeypatch):
    monkeypatch.setattr(
        "nexus.core.handlers.callback_command_handlers.has_feedback_submission",
        lambda *_a, **_k: True,
    )
    ctx = _Ctx("routefb:ok:dec-1", {"decision_id": "dec-1", "metadata": {}})

    await route_feedback_callback_handler(ctx, _deps())

    assert ctx.edits[-1]["text"] == "✅ Feedback already recorded."
