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
        self.attachments = None
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


def test_active_chat_session_forces_conversation(monkeypatch):
    update = _StubUpdate("Which new feature do you think we could add?")
    context = _StubContext()
    context.user_data["chat_session_active"] = True

    monkeypatch.setattr(
        routing,
        "parse_intent_result",
        lambda *_args, **_kwargs: {"intent": "task", "confidence": 0.99},
    )

    async def _unexpected_route_task(**_kwargs):
        raise AssertionError("Task routing should not run while chat session is active")

    monkeypatch.setattr(routing, "route_task_with_context", _unexpected_route_task)
    monkeypatch.setattr(
        routing,
        "run_conversation_turn",
        lambda **_kwargs: "We could add a workflow replay inspector.",
    )

    ictx = StubInteractiveContext(update, context.user_data)
    asyncio.run(routing.route_hands_free_text(ictx, _deps()))

    assert "workflow replay inspector" in ictx.edits[-1]["text"].lower()


def test_active_chat_session_allows_feature_ideation_flow(monkeypatch):
    update = _StubUpdate("Which new feature do you think we could add?")
    context = _StubContext()
    context.user_data["chat_session_active"] = True
    called = {}

    monkeypatch.setattr(
        routing,
        "parse_intent_result",
        lambda *_args, **_kwargs: {
            "intent": "conversation",
            "feature_ideation": True,
            "feature_ideation_confidence": 0.93,
            "feature_ideation_reason": "feature_request",
        },
    )

    async def _handle_feature(**kwargs):
        called.update(kwargs)
        return True

    monkeypatch.setattr(routing, "handle_feature_ideation_request", _handle_feature)
    monkeypatch.setattr(
        routing,
        "run_conversation_turn",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Chat response should not run for feature ideation")
        ),
    )

    ictx = StubInteractiveContext(update, context.user_data)
    deps = _deps()
    deps.feature_ideation_deps = {"stub": True}
    deps.orchestrator = type(
        "_StubOrchestrator",
        (),
        {"run_text_to_speech_analysis": staticmethod(lambda **_kwargs: {})},
    )()
    asyncio.run(routing.route_hands_free_text(ictx, deps))

    assert ictx.replies[-1]["text"] == "🧠 *Thinking about features...*"
    assert called["text"] == "Which new feature do you think we could add?"
    assert called["detected_feature_ideation"] is True
    assert called["detection_confidence"] == 0.93
    assert called["preferred_project_key"] == "nexus"
    assert called["preferred_agent_type"] == "designer"


def test_feature_ideation_fallback_detector_opens_count_prompt(monkeypatch):
    update = _StubUpdate("Which new features could we add to the framework?")
    context = _StubContext()
    called = {}

    monkeypatch.setattr(
        routing,
        "parse_intent_result",
        lambda *_args, **_kwargs: {
            "intent": "conversation",
            "feature_ideation": False,
            "feature_ideation_confidence": 0.0,
            "feature_ideation_reason": "not_provided",
        },
    )
    monkeypatch.setattr(
        routing,
        "detect_feature_ideation_intent",
        lambda *_args, **_kwargs: (True, 0.55, "phrase_fallback_no_model"),
    )

    async def _handle_feature(**kwargs):
        called.update(kwargs)
        return True

    monkeypatch.setattr(routing, "handle_feature_ideation_request", _handle_feature)
    monkeypatch.setattr(
        routing,
        "run_conversation_turn",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("Chat response should not run when feature fallback matches")
        ),
    )

    ictx = StubInteractiveContext(update, context.user_data)
    deps = _deps()
    deps.feature_ideation_deps = {"stub": True}
    deps.orchestrator = type(
        "_StubOrchestrator",
        (),
        {"run_text_to_speech_analysis": staticmethod(lambda **_kwargs: {})},
    )()
    asyncio.run(routing.route_hands_free_text(ictx, deps))

    assert ictx.replies[-1]["text"] == "🧠 *Thinking about features...*"
    assert called["detected_feature_ideation"] is True
    assert called["detection_reason"] == "phrase_fallback_no_model"
    assert called["preferred_project_key"] == "nexus"
    assert called["preferred_agent_type"] == "designer"


def test_feature_ideation_fallback_detector_receives_requester_and_project(monkeypatch):
    update = _StubUpdate("Could we add a few new workflow ideas?")
    context = _StubContext()
    captured = {}

    monkeypatch.setattr(
        routing,
        "parse_intent_result",
        lambda *_args, **_kwargs: {
            "intent": "conversation",
            "feature_ideation": False,
            "feature_ideation_confidence": 0.0,
            "feature_ideation_reason": "not_provided",
        },
    )

    def _detect(text, **kwargs):
        captured["text"] = text
        captured.update(kwargs)
        return False, 0.0, "model_non_match"

    monkeypatch.setattr(routing, "detect_feature_ideation_intent", _detect)
    monkeypatch.setattr(routing, "run_conversation_turn", lambda **_kwargs: "No ideation detected.")

    ictx = StubInteractiveContext(update, context.user_data)
    deps = _deps()
    deps.requester_context_builder = lambda _uid: {"nexus_id": "nexus-42"}
    deps.orchestrator = type(
        "_StubOrchestrator",
        (),
        {"run_text_to_speech_analysis": staticmethod(lambda **_kwargs: {})},
    )()

    asyncio.run(routing.route_hands_free_text(ictx, deps))

    assert captured["text"] == "Could we add a few new workflow ideas?"
    assert captured["requester_context"] == {"nexus_id": "nexus-42"}
    assert captured["project_name"] == "nexus"


def test_active_chat_session_blocks_hands_free_task_creation(monkeypatch):
    update = _StubUpdate("create task to add SSO support")
    context = _StubContext()
    context.user_data["chat_session_active"] = True

    called = {"task": False}

    async def _route_task(**_kwargs):
        called["task"] = True
        return {"success": True, "message": "should not happen"}

    monkeypatch.setattr(routing, "route_task_with_context", _route_task)

    ictx = StubInteractiveContext(update, context.user_data)
    asyncio.run(routing.route_hands_free_text(ictx, _deps()))

    assert called["task"] is False
    assert "Exit chat mode" in ictx.replies[-1]["text"]
