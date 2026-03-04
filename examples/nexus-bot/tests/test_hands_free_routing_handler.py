import asyncio

from nexus.core.handlers import hands_free_routing_handler as routing


class _StubMessage:
    def __init__(self, message_id: int = 1, voice=None):
        self.message_id = message_id
        self.voice = voice


class _StubChat:
    id = 123


class _StubUser:
    id = 777


class _StubUpdate:
    def __init__(self, text: str):
        self.effective_chat = _StubChat()
        self.effective_user = _StubUser()
        self.message = _StubMessage(message_id=42)
        self.message.text = text


class _StubContext:
    def __init__(self):
        self.user_data = {}
        self.bot = _StubBot()


class _StubBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


class _StubStatus:
    def __init__(self, message_id: int = 99):
        self.message_id = message_id


class StubInteractiveContext:
    def __init__(self, update, user_data):
        self.user_id = str(update.effective_user.id)
        self.text = update.message.text
        self.user_state = user_data
        self.raw_event = update
        self.replies = []
        self.edits = []

    async def reply_text(self, text, buttons=None):
        mid = str(len(self.replies) + 100)
        self.replies.append({"text": text, "buttons": buttons, "message_id": mid})
        return mid

    async def edit_message_text(self, message_id, text, buttons=None):
        self.edits.append({"message_id": message_id, "text": text, "buttons": buttons})



class _Logger:
    def info(self, *_args, **_kwargs):
        return None


def _deps():
    return routing.HandsFreeRoutingDeps(
        logger=_Logger(),
        orchestrator=object(),
        ai_persona="You are helpful.",
        projects={"nexus": "Nexus"},
        extract_json_dict=lambda _text: None,
        get_chat_history=lambda _user_id: "",
        append_message=lambda _user_id, _role, _text: None,
        get_chat=lambda _user_id: {
            "metadata": {
                "project_key": "nexus",
                "chat_mode": "strategy",
                "primary_agent_type": "designer",
                "allowed_agent_types": ["designer", "business"],
            }
        },
        process_inbox_task=lambda *_args, **_kwargs: None,
        normalize_project_key=lambda value: value,
        save_resolved_task=lambda *_args, **_kwargs: None,
        task_confirmation_mode="off",
    )


def test_feature_followup_text_routes_as_conversation(monkeypatch):
    update = _StubUpdate("What if instead we use an adapter?")
    context = _StubContext()
    status_msg = _StubStatus()
    context.user_data[routing.FEATURE_STATE_KEY] = {
        "project": "nexus",
        "items": [{"title": "Option 1"}, {"title": "Option 2"}],
    }

    monkeypatch.setattr(
        routing,
        "parse_intent_result",
        lambda *_args, **_kwargs: {"intent": "task", "confidence": 0.95},
    )

    async def _unexpected_route_task(**_kwargs):
        raise AssertionError("Task routing should not run for feature follow-up chat text")

    monkeypatch.setattr(routing, "route_task_with_context", _unexpected_route_task)
    monkeypatch.setattr(
        routing, "run_conversation_turn", lambda **_kwargs: "Let's discuss that adapter option."
    )

    ictx = StubInteractiveContext(update, context.user_data)

    asyncio.run(routing.route_hands_free_text(ictx, _deps()))

    assert len(ictx.edits) >= 1
    assert "Nexus (designer)" in ictx.edits[-1]["text"]


def test_explicit_task_request_still_routes_task(monkeypatch):
    update = _StubUpdate("Create task for option 2 and implement it")
    context = _StubContext()
    status_msg = _StubStatus()
    context.user_data[routing.FEATURE_STATE_KEY] = {
        "project": "nexus",
        "items": [{"title": "Option 1"}, {"title": "Option 2"}],
    }

    monkeypatch.setattr(
        routing,
        "parse_intent_result",
        lambda *_args, **_kwargs: {"intent": "task", "confidence": 0.95},
    )

    called = {"task": False}

    async def _route_task(**_kwargs):
        called["task"] = True
        return {"success": True, "message": "✅ Routed to `nexus`"}

    monkeypatch.setattr(routing, "route_task_with_context", _route_task)

    ictx = StubInteractiveContext(update, context.user_data)

    asyncio.run(routing.route_hands_free_text(ictx, _deps()))

    assert called["task"] is True
    assert ictx.edits[-1]["text"] == "✅ Routed to `nexus`"


def test_build_chat_persona_strategy_mode_blocks_premature_execution_artifacts():
    persona = routing._build_chat_persona(
        _deps(),
        user_id=777,
        routed_agent_type="designer",
        detected_intent="strategy",
        routing_reason="intent=strategy",
        user_text="I was thinking about adding a feature to improve planning",
    )
    assert "do not ask for issue numbers, PR links" in persona


def test_build_chat_persona_allows_execution_artifacts_on_explicit_execution_request():
    persona = routing._build_chat_persona(
        _deps(),
        user_id=777,
        routed_agent_type="designer",
        detected_intent="strategy",
        routing_reason="intent=strategy",
        user_text="implement this and create task now",
    )
    assert "Execution details (issue/PR/branch/commit) are allowed" in persona
