import asyncio

from nexus.core.handlers import feature_ideation_handlers as handlers


def test_detect_feature_project_uses_config_aliases(monkeypatch):
    import nexus.core.config as config

    monkeypatch.setattr(
        config,
        "get_project_aliases",
        lambda: {
            "nxs": "nexus",
            "smp": "sampleco",
        },
    )

    detected = handlers.detect_feature_project(
        "Can you propose top 3 features for nxs this week?",
        projects={"nexus": "Nexus", "sampleco": "SampleCo"},
    )

    assert detected == "nexus"


def test_detect_feature_project_falls_back_to_project_keys(monkeypatch):
    import nexus.core.config as config

    monkeypatch.setattr(config, "get_project_aliases", lambda: {})

    detected = handlers.detect_feature_project(
        "What features should we add to sampleco?",
        projects={"sampleco": "SampleCo", "nexus": "Nexus"},
    )

    assert detected == "sampleco"


def test_detect_feature_ideation_intent_phrase_match_still_uses_model():
    called = {"count": 0}

    def _run_analysis(**_kwargs):
        called["count"] += 1
        return {
            "feature_ideation": False,
            "confidence": 0.99,
            "reason": "meta-discussion, not ideation",
        }

    matched, confidence, reason = handlers.detect_feature_ideation_intent(
        "Which new feature should I add?",
        run_analysis=_run_analysis,
    )
    assert called["count"] == 1
    assert matched is False
    assert confidence == 0.99
    assert "meta-discussion" in reason


def test_detect_feature_ideation_intent_model_fallback_for_non_english():
    def _run_analysis(**_kwargs):
        return {
            "feature_ideation": True,
            "confidence": 0.82,
            "reason": "user asks for new feature proposals",
        }

    matched, confidence, reason = handlers.detect_feature_ideation_intent(
        "Quale funzionalità nuova dovrei aggiungere?",
        run_analysis=_run_analysis,
    )
    assert matched is True
    assert confidence == 0.82
    assert "feature proposals" in reason


def test_detect_feature_ideation_intent_model_rejects_status_question():
    def _run_analysis(**_kwargs):
        return {
            "feature_ideation": False,
            "confidence": 0.92,
            "reason": "status question",
        }

    matched, confidence, reason = handlers.detect_feature_ideation_intent(
        "Which is the last feature implemented?",
        run_analysis=_run_analysis,
    )
    assert matched is False
    assert confidence == 0.92
    assert reason == "status question"


def test_detect_feature_ideation_intent_phrase_fallback_when_model_errors():
    matched, confidence, reason = handlers.detect_feature_ideation_intent(
        "Which new feature should I add?",
        run_analysis=lambda **_kwargs: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert matched is True
    assert confidence == 0.55
    assert reason == "phrase_fallback_model_error"


def test_detect_feature_ideation_intent_forwards_requester_and_project_context():
    captured = {}

    def _run_analysis(**kwargs):
        captured.update(kwargs)
        return {
            "feature_ideation": True,
            "confidence": 0.9,
            "reason": "feature request",
        }

    matched, confidence, reason = handlers.detect_feature_ideation_intent(
        "Which features should we add?",
        run_analysis=_run_analysis,
        requester_context={"nexus_id": "nexus-42"},
        project_name="nexus",
    )

    assert matched is True
    assert confidence == 0.9
    assert reason == "feature request"
    assert captured["task"] == "detect_feature_ideation"
    assert captured["requester_context"] == {"nexus_id": "nexus-42"}
    assert captured["project_name"] == "nexus"


class _CaptureOrchestrator:
    def __init__(self):
        self.persona = ""
        self.last_kwargs = {}

    def run_text_to_speech_analysis(self, **kwargs):
        self.last_kwargs = dict(kwargs)
        self.persona = str(kwargs.get("persona", ""))
        return {
            "items": [
                {
                    "title": "Improve onboarding conversion",
                    "summary": "Reduce onboarding drop-off with guided checklist.",
                    "why": "Higher activation and retention.",
                    "steps": ["Audit funnel", "Implement checklist", "Track activation KPI"],
                }
            ]
        }


class _ArrayTextOrchestrator:
    def run_text_to_speech_analysis(self, **_kwargs):
        return {
            "text": (
                "```json\n"
                "["
                '{"title":"Improve retention loops",'
                '"summary":"Add habit reminders tied to active goals.",'
                '"why":"Improves weekly active usage.",'
                '"steps":["Define trigger points","Implement reminder jobs","Track WAU uplift"]}'
                "]\n"
                "```"
            )
        }


class _NonJsonTextOrchestrator:
    def run_text_to_speech_analysis(self, **_kwargs):
        return {"text": "This is plain text and not valid JSON for feature items."}


class _CaptureLogger:
    def __init__(self):
        self.messages = []

    def info(self, message, *args):
        if args:
            self.messages.append(str(message) % args)
        else:
            self.messages.append(str(message))

    def warning(self, message, *args):
        if args:
            self.messages.append(str(message) % args)
        else:
            self.messages.append(str(message))


class _SingleItemDictOrchestrator:
    def run_text_to_speech_analysis(self, **_kwargs):
        return {
            "title": "Improve retention loops",
            "summary": "Add habit reminders tied to active goals.",
            "why": "Improves weekly active usage.",
            "steps": ["Define trigger points", "Implement reminder jobs", "Track WAU uplift"],
        }


class _WrappedResponseOrchestrator:
    def run_text_to_speech_analysis(self, **_kwargs):
        return {
            "session_id": "abc-123",
            "stats": {"tokens": 42},
            "response": (
                "{\n"
                '  "items": [\n'
                "    {\n"
                '      "title": "Multi-Asset Performance Benchmarking",\n'
                '      "summary": "Overlay diversified portfolio performance against market indices.",\n'
                '      "why": "Improves long-term performance visibility and retention.",\n'
                '      "steps": ["Map asset weights", "Add benchmark layer", "Ship comparison dashboard"]\n'
                "    }\n"
                "  ]\n"
                "}"
            ),
        }


class _CopilotFallbackSuccessOrchestrator:
    def __init__(self):
        self.last_kwargs = {}
        self.fallback_kwargs = {}

    def run_text_to_speech_analysis(self, **_kwargs):
        self.last_kwargs = dict(_kwargs)
        return {"text": "not-json"}

    def _run_copilot_analysis(self, *_args, **_kwargs):
        self.fallback_kwargs = dict(_kwargs)
        return {
            "items": [
                {
                    "title": "Copilot-generated roadmap slice",
                    "summary": "Break roadmap into measurable monthly increments.",
                    "why": "Improves execution predictability and visibility.",
                    "steps": ["Define milestones", "Map owner per milestone", "Track completion"],
                }
            ]
        }


class _StubChat:
    id = 123


class _StubUser:
    id = 777


class _StubMessage:
    def __init__(self, message_id: int = 1):
        self.message_id = message_id


class _StubBot:
    def __init__(self):
        self.edits = []

    async def edit_message_text(self, **kwargs):
        self.edits.append(kwargs)


class _StubQuery:
    def __init__(self, data: str, message_id: int = 1):
        self.data = data
        self.message = _StubMessage(message_id=message_id)
        self.edits = []

    async def answer(self):
        return None

    async def edit_message_text(self, text, reply_markup=None, parse_mode=None):
        self.edits.append(
            {
                "text": text,
                "reply_markup": reply_markup,
                "parse_mode": parse_mode,
            }
        )


class _FailAnswerQuery(_StubQuery):
    async def answer(self):
        raise RuntimeError("stale callback")


class _StubContext:
    def __init__(self):
        self.user_data = {}
        self.bot = _StubBot()


class _StubUpdate:
    def __init__(self, callback_query=None):
        self.callback_query = callback_query
        self.effective_chat = _StubChat()
        self.effective_user = _StubUser()


def _keyboard_callback_data(reply_markup) -> list[str]:
    if reply_markup is None:
        return []
    callbacks = []
    for row in getattr(reply_markup, "inline_keyboard", []):
        for button in row:
            callbacks.append(str(getattr(button, "callback_data", "")))
    return callbacks


def test_build_feature_suggestions_requires_agent_prompt(tmp_path):
    workspace_root = tmp_path / "workspace"
    business_dir = workspace_root / "business-os"
    business_dir.mkdir(parents=True)
    (business_dir / "README.md").write_text(
        "Business context that should not be used without prompt definition.",
        encoding="utf-8",
    )

    orchestrator = _CaptureOrchestrator()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=None,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert items == []
    assert orchestrator.persona == ""


def test_build_feature_suggestions_uses_business_context_folder(tmp_path):
    workspace_root = tmp_path / "workspace"
    business_dir = workspace_root / "business-os"
    agents_dir = workspace_root / "agents"
    business_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (business_dir / "README.md").write_text(
        "Business OS context: prioritize revenue and retention.",
        encoding="utf-8",
    )
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n"
        "    Strategic constraints and principles.\n",
        encoding="utf-8",
    )

    orchestrator = _CaptureOrchestrator()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=None,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert len(items) == 1
    assert "Dedicated Advisor Prompt" in orchestrator.persona
    assert "Context folders: business-os" in orchestrator.persona
    assert "prioritize revenue and retention" in orchestrator.persona


def test_build_feature_suggestions_uses_marketing_context_folder(tmp_path):
    workspace_root = tmp_path / "workspace"
    marketing_dir = workspace_root / "marketing-os"
    agents_dir = workspace_root / "agents"
    marketing_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (marketing_dir / "README.md").write_text(
        "Marketing OS context: focus on channel strategy and activation.",
        encoding="utf-8",
    )
    (agents_dir / "marketing.yaml").write_text(
        """
spec:
  agent_type: marketing
  prompt_template: |
    Dedicated Marketing Prompt
    Focus on acquisition and activation outcomes.
""".strip(),
        encoding="utf-8",
    )

    orchestrator = _CaptureOrchestrator()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=None,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "marketing": {
                            "context_path": "marketing-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 marketing feature",
        deps=deps,
        preferred_agent_type="marketing",
        feature_count=1,
    )

    assert len(items) == 1
    assert "Dedicated Marketing Prompt" in orchestrator.persona
    assert "Context folders: marketing-os" in orchestrator.persona
    assert "focus on channel strategy and activation" in orchestrator.persona


def test_agent_prompt_discovery_matches_spec_agent_type_without_prompt_map(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Business Prompt From AgentType Match\n",
        encoding="utf-8",
    )

    orchestrator = _CaptureOrchestrator()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=None,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert "Business Prompt From AgentType Match" in orchestrator.persona


def test_build_feature_suggestions_omitted_context_files_disables_context_loading(tmp_path):
    workspace_root = tmp_path / "workspace"
    marketing_dir = workspace_root / "marketing-os"
    agents_dir = workspace_root / "agents"
    marketing_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (marketing_dir / "README.md").write_text(
        "This should not be loaded when context_files is omitted.",
        encoding="utf-8",
    )
    (agents_dir / "marketing.yaml").write_text(
        """
spec:
  agent_type: marketing
  prompt_template: |
    Dedicated Marketing Prompt
""".strip(),
        encoding="utf-8",
    )

    orchestrator = _CaptureOrchestrator()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=None,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "marketing": {
                            "context_path": "marketing-os",
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 marketing feature",
        deps=deps,
        preferred_agent_type="marketing",
        feature_count=1,
    )

    assert len(items) == 1
    assert "This should not be loaded" not in orchestrator.persona
    assert "Context folders:" not in orchestrator.persona


def test_chat_agents_list_shape_is_supported_for_context(tmp_path):
    workspace_root = tmp_path / "workspace"
    business_dir = workspace_root / "business-os"
    agents_dir = workspace_root / "agents"
    business_dir.mkdir(parents=True)
    agents_dir.mkdir(parents=True)
    (business_dir / "README.md").write_text(
        "List-shape context should be loaded.",
        encoding="utf-8",
    )
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    orchestrator = _CaptureOrchestrator()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=None,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": [
                        {
                            "business": {
                                "context_path": "business-os",
                                "context_files": ["README.md"],
                            }
                        }
                    ]
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert len(items) == 1
    assert "List-shape context should be loaded." in orchestrator.persona


def test_build_feature_suggestions_accepts_top_level_json_array_text(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=None,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_ArrayTextOrchestrator(),
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert len(items) == 1
    assert items[0]["title"] == "Improve retention loops"


def test_build_feature_suggestions_logs_primary_non_json_response(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    logger = _CaptureLogger()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=logger,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_NonJsonTextOrchestrator(),
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert items == []
    assert any(
        "Primary feature ideation raw response (truncated):" in msg for msg in logger.messages
    )
    assert any("not valid JSON" in msg for msg in logger.messages)


def test_build_feature_suggestions_accepts_structured_single_item_dict(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_SingleItemDictOrchestrator(),
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert len(items) == 1
    assert items[0]["title"] == "Improve retention loops"


def test_build_feature_suggestions_accepts_wrapped_response_json_string(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    logger = _CaptureLogger()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=logger,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_WrappedResponseOrchestrator(),
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert len(items) == 1
    assert items[0]["title"] == "Multi-Asset Performance Benchmarking"
    assert not any("retrying with Copilot" in msg for msg in logger.messages)


def test_build_feature_suggestions_passes_requester_context_to_primary(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    orchestrator = _CaptureOrchestrator()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {"chat": {"business": {}}},
            }
        },
    )

    handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
        requester_context={"nexus_id": "nexus-42"},
    )

    assert orchestrator.last_kwargs["requester_context"] == {"nexus_id": "nexus-42"}
    assert orchestrator.last_kwargs["project_name"] == "acme"


def test_build_feature_suggestions_logs_success_when_copilot_fallback_succeeds(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    logger = _CaptureLogger()
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=logger,
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_CopilotFallbackSuccessOrchestrator(),
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {
                        "business": {
                            "context_path": "business-os",
                            "context_files": ["README.md"],
                        }
                    }
                },
            }
        },
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
    )

    assert len(items) == 1
    assert items[0]["title"] == "Copilot-generated roadmap slice"
    assert any(
        "Feature ideation success: provider=fallback primary_success=false fallback_used=true items=1"
        in msg
        for msg in logger.messages
    )


def test_build_feature_suggestions_passes_requester_context_to_fallback(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    orchestrator = _CopilotFallbackSuccessOrchestrator()
    orchestrator._resolve_analysis_provider_env = lambda requester: (
        {"GITHUB_TOKEN": "user-token"} if requester else None,
        None,
    )
    orchestrator._resolve_analysis_cwd = lambda project_key: str(workspace_root) if project_key == "acme" else None
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=orchestrator,
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {"chat": {"business": {}}},
            }
        },
    )

    handlers._build_feature_suggestions(
        project_key="acme",
        text="Propose top 1 feature",
        deps=deps,
        preferred_agent_type="business",
        feature_count=1,
        requester_context={"nexus_id": "nexus-42"},
    )

    assert orchestrator.fallback_kwargs["_provider_env"] == {"GITHUB_TOKEN": "user-token"}
    assert orchestrator.fallback_kwargs["_analysis_cwd"] == str(workspace_root)


def test_handle_feature_ideation_request_prompts_count_first(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_CaptureOrchestrator(),
        requester_context_builder=lambda user_id: {"nexus_id": f"nexus-{user_id}"},
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {"business": {}},
                },
            }
        },
    )

    update = _StubUpdate()
    context = _StubContext()
    status_msg = _StubMessage(message_id=42)

    handled = asyncio.run(
        handlers.handle_feature_ideation_request(
            update=update,
            context=context,
            status_msg=status_msg,
            text="Please propose new features for acme",
            deps=deps,
            preferred_project_key=None,
            preferred_agent_type="business",
            detected_feature_ideation=True,
            detection_confidence=0.92,
            detection_reason="test_preclassified",
        )
    )

    assert handled is True
    assert context.user_data[handlers.FEATURE_STATE_KEY]["project"] == "acme"
    assert context.user_data[handlers.FEATURE_STATE_KEY]["feature_count"] is None
    assert context.user_data[handlers.FEATURE_STATE_KEY]["project_locked"] is True
    assert context.user_data[handlers.FEATURE_STATE_KEY]["requester_context"] == {
        "nexus_id": "nexus-777"
    }
    assert context.bot.edits
    assert "How many feature proposals" in context.bot.edits[-1]["text"]
    callbacks = _keyboard_callback_data(context.bot.edits[-1]["reply_markup"])
    assert "feat:choose_project" not in callbacks


def test_handle_feature_ideation_request_shows_detected_count_hint(tmp_path):
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_CaptureOrchestrator(),
    )

    update = _StubUpdate()
    context = _StubContext()
    status_msg = _StubMessage(message_id=42)

    handled = asyncio.run(
        handlers.handle_feature_ideation_request(
            update=update,
            context=context,
            status_msg=status_msg,
            text="Please propose 4 new features for acme",
            deps=deps,
            preferred_project_key=None,
            preferred_agent_type="business",
            detected_feature_ideation=True,
            detection_confidence=0.92,
            detection_reason="test_preclassified",
        )
    )

    assert handled is True
    assert context.user_data[handlers.FEATURE_STATE_KEY]["requested_feature_count"] == 4
    assert "Detected from your message: *4*" in context.bot.edits[-1]["text"]


def test_feature_list_text_and_buttons_are_clipped_for_telegram(monkeypatch):
    monkeypatch.setattr(handlers, "FEATURE_LIST_SUMMARY_MAX_CHARS", 40)
    monkeypatch.setattr(handlers, "FEATURE_LIST_MESSAGE_MAX_CHARS", 260)
    monkeypatch.setattr(handlers, "FEATURE_BUTTON_LABEL_MAX_CHARS", 24)

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_CaptureOrchestrator(),
    )
    features = [
        {
            "title": f"Feature option {idx} with an excessively long title suffix",
            "summary": "This is a deliberately long summary that should be clipped to keep Telegram message payloads well under the edit limit.",
            "why": "Improves reliability.",
            "steps": ["Step 1", "Step 2"],
        }
        for idx in range(1, 6)
    ]

    rendered = handlers._feature_list_text(
        "acme",
        features,
        deps,
        preferred_agent_type="business",
    )
    keyboard = handlers._feature_list_keyboard(features, allow_project_change=True)

    assert len(rendered) <= handlers.FEATURE_LIST_MESSAGE_MAX_CHARS + 64
    assert "...and" in rendered
    assert len(keyboard) >= 2
    assert keyboard[0][0].label.endswith("...")
    assert len(keyboard[0][0].label) <= handlers.FEATURE_BUTTON_LABEL_MAX_CHARS


def test_feature_pick_starts_task_flow_with_selected_project(tmp_path):
    created = {"calls": []}

    async def _create_feature_task(text: str, message_id: str, project_key: str):
        created["calls"].append(
            {
                "text": text,
                "message_id": message_id,
                "project_key": project_key,
            }
        )
        return {"success": True, "message": "✅ Feature task started"}

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_CaptureOrchestrator(),
        create_feature_task=_create_feature_task,
    )

    query = _StubQuery(data="feat:pick:0", message_id=777)
    update = _StubUpdate(callback_query=query)
    context = _StubContext()
    context.user_data[handlers.FEATURE_STATE_KEY] = {
        "project": "acme",
        "items": [
            {
                "title": "Agent Consensus Engine",
                "summary": "Introduce a consensus layer for critical validation.",
                "why": "Improves quality.",
                "steps": ["Define protocol", "Integrate workflow", "Ship reconciliation"],
            }
        ],
        "agent_type": "business",
        "feature_count": 1,
        "source_text": "propose features",
    }

    asyncio.run(handlers.feature_callback_handler(update=update, context=context, deps=deps))

    assert len(created["calls"]) == 1
    assert created["calls"][0]["project_key"] == "acme"
    assert query.edits
    assert "Feature task started" in query.edits[-1]["text"]
    callbacks = _keyboard_callback_data(query.edits[-1]["reply_markup"])
    assert "feat:choose_project" not in callbacks


def test_count_selection_shows_thinking_before_generating_features(tmp_path):
    workspace_root = tmp_path / "workspace"
    agents_dir = workspace_root / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "business.yaml").write_text(
        "spec:\n"
        "  agent_type: business\n"
        "  prompt_template: |\n"
        "    Dedicated Advisor Prompt\n",
        encoding="utf-8",
    )

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_CaptureOrchestrator(),
        base_dir=str(tmp_path),
        project_config={
            "acme": {
                "workspace": "workspace",
                "agents_dir": "workspace/agents",
                "system_operations": {
                    "chat": {"business": {}},
                },
            }
        },
    )

    query = _StubQuery(data="feat:count:2", message_id=888)
    update = _StubUpdate(callback_query=query)
    context = _StubContext()
    context.user_data[handlers.FEATURE_STATE_KEY] = {
        "project": "acme",
        "project_locked": True,
        "items": [],
        "agent_type": "business",
        "feature_count": None,
        "source_text": "new features for acme",
    }

    asyncio.run(handlers.feature_callback_handler(update=update, context=context, deps=deps))

    assert len(query.edits) >= 2
    assert "Nexus thinking" in query.edits[0]["text"]


def test_back_to_feature_list_uses_cached_items_without_regeneration():
    class _NoCallOrchestrator:
        def run_text_to_speech_analysis(self, **_kwargs):
            raise AssertionError("Should not regenerate on back-to-list when items are cached")

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_NoCallOrchestrator(),
    )

    query = _StubQuery(data="feat:project:acme", message_id=900)
    update = _StubUpdate(callback_query=query)
    context = _StubContext()
    context.user_data[handlers.FEATURE_STATE_KEY] = {
        "project": "acme",
        "items": [
            {
                "title": "Cached feature",
                "summary": "Use cached suggestions.",
                "why": "Fast UI.",
                "steps": ["A", "B", "C"],
            }
        ],
        "agent_type": "business",
        "feature_count": 1,
        "source_text": "new features",
        "project_locked": True,
    }

    asyncio.run(handlers.feature_callback_handler(update=update, context=context, deps=deps))

    assert query.edits
    assert "Feature proposals for Acme" in query.edits[-1]["text"]


def test_stale_callback_answer_does_not_abort_feature_callback():
    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_CaptureOrchestrator(),
    )

    query = _FailAnswerQuery(data="feat:project:acme", message_id=901)
    update = _StubUpdate(callback_query=query)
    context = _StubContext()
    context.user_data[handlers.FEATURE_STATE_KEY] = {
        "project": "acme",
        "items": [
            {
                "title": "Cached feature",
                "summary": "Use cached suggestions.",
                "why": "Fast UI.",
                "steps": ["A", "B", "C"],
            }
        ],
        "agent_type": "business",
        "feature_count": 1,
        "source_text": "new features",
        "project_locked": True,
    }

    asyncio.run(handlers.feature_callback_handler(update=update, context=context, deps=deps))

    assert query.edits
    assert "Feature proposals for Acme" in query.edits[-1]["text"]


class _RegistryStub:
    def __init__(self, excluded=None):
        self._excluded = excluded or []

    def is_enabled(self):
        return True

    def list_excluded_titles(self, _project_key):
        return list(self._excluded)

    def filter_ideation_items(self, *, items, **_kwargs):
        kept = [item for item in items if "duplicate" not in item.get("title", "").lower()]
        removed = [item for item in items if item not in kept]
        return kept, removed


def test_build_feature_persona_includes_exclude_list():
    persona = handlers._build_feature_persona(
        project_label="Acme",
        routed_agent_type="business",
        feature_count=2,
        context_block="",
        agent_prompt="Prompt",
        excluded_titles=["Legacy billing", "Old onboarding"],
    )

    assert "Already implemented features" in persona
    assert "- Legacy billing" in persona
    assert "- Old onboarding" in persona


def test_build_feature_suggestions_filters_registry_matches(tmp_path, monkeypatch):
    monkeypatch.setattr(handlers, "_load_agent_prompt_from_definition", lambda *_a, **_k: "Prompt")
    monkeypatch.setattr(handlers, "_load_role_context", lambda *_a, **_k: "")

    class _TwoFeatureOrchestrator:
        def run_text_to_speech_analysis(self, **_kwargs):
            return {
                "items": [
                    {
                        "title": "Duplicate implemented feature",
                        "summary": "dup",
                        "why": "dup",
                        "steps": ["a"],
                    },
                    {
                        "title": "Brand new initiative",
                        "summary": "new",
                        "why": "new",
                        "steps": ["a"],
                    },
                ]
            }

    deps = handlers.FeatureIdeationHandlerDeps(
        logger=_CaptureLogger(),
        allowed_user_ids=[],
        projects={"acme": "Acme"},
        get_project_label=lambda key: "Acme" if key == "acme" else key,
        orchestrator=_TwoFeatureOrchestrator(),
        base_dir=str(tmp_path),
        project_config={"acme": {}},
        feature_registry_service=_RegistryStub(["Duplicate implemented feature"]),
        dedup_similarity=0.86,
    )

    items = handlers._build_feature_suggestions(
        project_key="acme",
        text="suggest two new features",
        deps=deps,
        preferred_agent_type="business",
        feature_count=2,
    )

    assert [item["title"] for item in items] == ["Brand new initiative"]
