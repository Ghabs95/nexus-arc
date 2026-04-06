from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus.core.handlers import callback_command_handlers as callback_handlers
from nexus.core.handlers.callback_command_handlers import CallbackHandlerDeps, route_feedback_callback_handler
from nexus.core.telegram import telegram_router_feedback_service as feedback_service


class _DummyCtx:
    def __init__(self, *, action_data: str, user_id: str, user_state: dict):
        self.user_id = user_id
        self.user_state = user_state
        self.query = SimpleNamespace(action_data=action_data, message_id="42")
        self.edits: list[dict] = []
        self.answered = 0

    async def answer_callback_query(self, text: str | None = None):
        del text
        self.answered += 1

    async def edit_message_text(self, *, message_id: str, text: str, buttons=None, parse_mode=None):
        self.edits.append(
            {
                "message_id": message_id,
                "text": text,
                "buttons": buttons,
                "parse_mode": parse_mode,
            }
        )


def _deps() -> CallbackHandlerDeps:
    return CallbackHandlerDeps(
        logger=SimpleNamespace(info=lambda *a, **k: None, warning=lambda *a, **k: None),
        prompt_issue_selection=lambda *a, **k: None,
        dispatch_command=lambda *a, **k: None,
        get_project_label=lambda value: value,
        get_repo=lambda value: value,
        get_direct_issue_plugin=lambda *a, **k: None,
        get_workflow_state_plugin=lambda *a, **k: None,
        workflow_state_plugin_kwargs={},
        action_handlers={},
        report_bug_action=lambda *a, **k: None,
        router_feedback_url="http://router.test",
    )


def test_feedback_token_round_trips_meta(tmp_path):
    store_path = tmp_path / "tokens.json"
    decision_id = "11111111-1111-4111-8111-111111111111"
    meta = {
        "decision_id": decision_id,
        "task_type": "reasoning",
        "selected_model": "openai/gpt-5",
        "source_channel": "telegram",
        "metadata": {"source_message_preview": "older routed turn"},
    }

    feedback_service.register_feedback_token(
        user_id="user-1",
        decision_id=decision_id,
        meta=meta,
        store_path=str(store_path),
    )

    token = feedback_service.decision_token(decision_id)
    assert feedback_service.resolve_feedback_token(
        user_id="user-1",
        decision_ref=token,
        store_path=str(store_path),
    ) == decision_id
    assert feedback_service.load_feedback_meta_for_ref(
        user_id="user-1",
        decision_ref=token,
        store_path=str(store_path),
    ) == meta


def test_load_feedback_meta_for_ref_accepts_uuid_decision_id(tmp_path):
    """UUID decision_id passed as decision_ref should resolve meta via token derivation."""
    store_path = tmp_path / "tokens.json"
    decision_id = "33333333-3333-4333-8333-333333333333"
    meta = {
        "decision_id": decision_id,
        "task_type": "coding",
        "selected_model": "anthropic/claude-opus",
        "source_channel": "telegram",
        "metadata": {"source_message_preview": "uuid ref test turn"},
    }

    feedback_service.register_feedback_token(
        user_id="user-2",
        decision_id=decision_id,
        meta=meta,
        store_path=str(store_path),
    )

    # Passing the full UUID as decision_ref should still return the stored meta.
    assert feedback_service.load_feedback_meta_for_ref(
        user_id="user-2",
        decision_ref=decision_id,
        store_path=str(store_path),
    ) == meta


@pytest.mark.asyncio
async def test_route_feedback_callback_accepts_older_card_when_token_resolves(monkeypatch):
    old_decision = "11111111-1111-4111-8111-111111111111"
    latest_decision = "22222222-2222-4222-8222-222222222222"
    old_meta = {
        "decision_id": old_decision,
        "feedback_mode": "router",
        "task_type": "reasoning",
        "selected_model": "openai/gpt-5",
        "source_channel": "telegram",
        "metadata": {"source_message_preview": "first routed reply"},
    }
    latest_meta = {
        "decision_id": latest_decision,
        "feedback_mode": "router",
        "task_type": "coding",
        "selected_model": "anthropic/claude-sonnet",
        "source_channel": "telegram",
        "metadata": {"source_message_preview": "newer routed reply"},
    }
    token = feedback_service.decision_token(old_decision)
    submitted: dict = {}

    monkeypatch.setattr(callback_handlers, "load_feedback_meta_for_ref", lambda **kwargs: dict(old_meta))
    monkeypatch.setattr(callback_handlers, "resolve_feedback_token", lambda **kwargs: old_decision)
    monkeypatch.setattr(callback_handlers, "has_feedback_submission", lambda *a, **k: False)
    monkeypatch.setattr(
        callback_handlers,
        "submit_feedback",
        lambda *, router_url, payload: (submitted.update({"router_url": router_url, "payload": payload}) or True, "ok"),
    )

    ctx = _DummyCtx(
        action_data=f"routefb:ok:{token}",
        user_id="user-1",
        user_state={feedback_service.PENDING_KEY: dict(latest_meta)},
    )

    await route_feedback_callback_handler(ctx, _deps())

    assert submitted["router_url"] == "http://router.test"
    assert submitted["payload"]["decision_id"] == old_decision
    assert submitted["payload"]["metadata"]["task_type"] == "reasoning"
    assert submitted["payload"]["metadata"]["selected_model"] == "openai/gpt-5"
    assert ctx.edits[-1]["text"] == "✅ Feedback recorded."
    assert feedback_service.PENDING_KEY not in ctx.user_state
