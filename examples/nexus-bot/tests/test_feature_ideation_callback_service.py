import logging
from types import SimpleNamespace

import pytest
from nexus.core.feature_ideation_callback_service import handle_feature_ideation_callback


class _Ctx:
    def __init__(self, data: str, user_id: int = 1):
        self.user_id = user_id
        self.query = SimpleNamespace(data=data)
        self.user_state = {}
        self.calls = []
        self.raw_event = SimpleNamespace(message=SimpleNamespace(message_id=9))
        self.client = SimpleNamespace(name="tester")

    async def answer_callback_query(self):
        self.calls.append(("answer",))

    async def edit_message_text(self, text=None, buttons=None):
        self.calls.append(("edit", text, buttons))


def _deps():
    return SimpleNamespace(
        logger=logging.getLogger("test"),
        allowed_user_ids=[],
        projects={"p": "Project P"},
    )


@pytest.mark.asyncio
async def test_feature_callback_choose_project_updates_state():
    ctx = _Ctx("feat:choose_project")
    ctx.user_state["feature_ideation"] = {"project": "p", "items": [1], "selected_items": [2]}

    await handle_feature_ideation_callback(
        ctx=ctx,
        deps=_deps(),
        feature_state_key="feature_ideation",
        is_project_locked=lambda _s: False,
        feature_project_keyboard=lambda _d: [["k"]],
        clamp_feature_count=lambda v: int(v),
        build_feature_suggestions=lambda **_k: [],
        feature_generation_retry_text=lambda project_key, deps: "retry",
        feature_list_text=lambda *a, **k: "list",
        feature_list_keyboard=lambda *a, **k: [["b"]],
        feature_count_prompt_text=lambda *a, **k: "count",
        feature_count_keyboard=lambda *a, **k: [["c"]],
        feature_to_task_text=lambda *a, **k: "task",
        log_unauthorized_callback_access=lambda *_a: None,
    )

    assert ctx.user_state["feature_ideation"]["project"] is None
    assert ctx.calls[-1][1] == "📁 Select a project to continue feature ideation:"


@pytest.mark.asyncio
async def test_feature_callback_invalid_project_selection():
    ctx = _Ctx("feat:project:missing")

    await handle_feature_ideation_callback(
        ctx=ctx,
        deps=_deps(),
        feature_state_key="feature_ideation",
        is_project_locked=lambda _s: False,
        feature_project_keyboard=lambda _d: [["k"]],
        clamp_feature_count=lambda v: int(v),
        build_feature_suggestions=lambda **_k: [],
        feature_generation_retry_text=lambda project_key, deps: "retry",
        feature_list_text=lambda *a, **k: "list",
        feature_list_keyboard=lambda *a, **k: [["b"]],
        feature_count_prompt_text=lambda *a, **k: "count",
        feature_count_keyboard=lambda *a, **k: [["c"]],
        feature_to_task_text=lambda *a, **k: "task",
        log_unauthorized_callback_access=lambda *_a: None,
    )

    assert ctx.calls[-1][1] == "⚠️ Invalid project selection."
