from types import SimpleNamespace

import pytest

from nexus.core.handlers.issue_command_handlers import plan_handler


class _Ctx:
    def __init__(self, *, args, user_id="7", raw_event=None):
        self.args = args
        self.user_id = user_id
        self.raw_event = raw_event or SimpleNamespace(message_id="11")
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)
        return "msg-1"


@pytest.mark.asyncio
async def test_plan_handler_creates_new_planning_task_for_explicit_project():
    ctx = _Ctx(args=["proj-a", "Design", "the", "rollout"])
    seen = {}

    async def _create_planning_task(**kwargs):
        seen.update(kwargs)
        return {"message": "queued"}

    deps = SimpleNamespace(
        logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
        allowed_user_ids=[],
        project_config={"proj-a": {"workspace": "ws-a"}},
        create_planning_task=_create_planning_task,
        requester_context_builder=lambda user_id: {
            "platform": "telegram",
            "platform_user_id": str(user_id),
            "nexus_id": "nx-7",
        },
    )

    await plan_handler(ctx, deps)

    assert seen["project_key"] == "proj-a"
    assert seen["text"] == "Design the rollout"
    assert seen["message_id"] == "11"
    assert seen["requester_context"]["nexus_id"] == "nx-7"
    assert ctx.replies[-1] == "queued"


@pytest.mark.asyncio
async def test_plan_handler_uses_single_project_as_default():
    ctx = _Ctx(args=["Draft", "an", "ADR"])
    seen = {}

    async def _create_planning_task(**kwargs):
        seen.update(kwargs)
        return {"message": "queued"}

    deps = SimpleNamespace(
        logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
        allowed_user_ids=[],
        project_config={"proj-a": {"workspace": "ws-a"}},
        create_planning_task=_create_planning_task,
        requester_context_builder=None,
    )

    await plan_handler(ctx, deps)

    assert seen["project_key"] == "proj-a"
    assert seen["text"] == "Draft an ADR"
    assert ctx.replies[-1] == "queued"


@pytest.mark.asyncio
async def test_plan_handler_requires_project_when_multiple_projects_exist():
    ctx = _Ctx(args=["Draft", "an", "ADR"])
    deps = SimpleNamespace(
        logger=SimpleNamespace(info=lambda *_args, **_kwargs: None),
        allowed_user_ids=[],
        project_config={"proj-a": {"workspace": "ws-a"}, "proj-b": {"workspace": "ws-b"}},
        create_planning_task=None,
        requester_context_builder=None,
    )

    await plan_handler(ctx, deps)

    assert ctx.replies[-1] == "Usage: /plan <project> <planning request>"
