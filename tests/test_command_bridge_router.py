from __future__ import annotations

from types import SimpleNamespace

import pytest

from nexus.core.command_bridge.models import CommandRequest, RequesterContext, UsagePayload
from nexus.core.command_bridge.router import CommandRouter


@pytest.fixture
def router(monkeypatch) -> CommandRouter:
    stub_deps = SimpleNamespace(
        workflow_state_plugin_kwargs={},
        requester_context_builder=None,
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.workflow_bridge_deps",
        lambda **_kwargs: SimpleNamespace(**stub_deps.__dict__),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.monitoring_bridge_deps",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.ops_bridge_deps",
        lambda **_kwargs: SimpleNamespace(requester_context_builder=None),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.issue_bridge_deps",
        lambda **_kwargs: SimpleNamespace(),
    )
    monkeypatch.setattr(
        "nexus.core.command_bridge.router.get_workflow_state",
        lambda: SimpleNamespace(
            get_workflow_id=lambda issue_num: "demo-42-full" if str(issue_num) == "42" else None,
            load_all_mappings=lambda: {"42": "demo-42-full"},
        ),
    )
    return CommandRouter(allowed_user_ids=[], default_source_platform="openclaw")


@pytest.mark.asyncio
async def test_execute_returns_structured_result_and_captured_messages(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    async def _fake_usage(*, project_key=None, issue_number=None, workflow_id=None):
        assert project_key == "demo"
        assert issue_number == "42"
        assert workflow_id == "demo-42-full"
        return UsagePayload(
            provider="openai",
            model="gpt-5.4",
            input_tokens=123,
            output_tokens=45,
            estimated_cost_usd=0.67,
            metadata={"source": "test"},
        )

    monkeypatch.setattr("nexus.core.command_bridge.router.collect_bridge_usage_payload", _fake_usage)

    async def _plan_handler(*, client, user_id, text, args, raw_event=None, attachments=None):
        del user_id, text, raw_event, attachments
        ctx = router.build_context(
            client=client,
            user_id="alice",
            text="plan demo #42",
            args=args,
        )
        message_id = await ctx.reply_text("Planning issue #42")
        await ctx.edit_message_text(message_id=message_id, text="Plan queued for issue #42")

    router.register_command("plan", _plan_handler)

    result = await router.execute(
        CommandRequest(
            command="plan",
            args=["demo#42"],
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "accepted"
    assert result.workflow_id == "demo-42-full"
    assert result.issue_number == "42"
    assert result.message == "Plan queued for issue #42"
    assert result.usage is not None
    assert result.usage.provider == "openai"
    assert result.usage.input_tokens == 123
    assert result.data["messages"][-1]["edited"] is True


@pytest.mark.asyncio
async def test_route_maps_freeform_text_to_supported_command(router: CommandRouter):
    captured: dict[str, object] = {}

    async def _logs_handler(*, client, user_id, text, args, raw_event=None, attachments=None):
        del user_id, raw_event, attachments
        captured["text"] = text
        captured["args"] = list(args)
        ctx = router.build_context(
            client=client,
            user_id="alice",
            text=text,
            args=args,
        )
        await ctx.reply_text("Showing logs")

    router.register_command("logs", _logs_handler)

    result = await router.route(
        CommandRequest(
            raw_text="show logs for demo#42",
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "success"
    assert result.issue_number == "42"
    assert captured["args"] == ["demo", "42"]


@pytest.mark.asyncio
async def test_route_returns_clarification_for_unknown_freeform(router: CommandRouter):
    result = await router.route(
        CommandRequest(
            raw_text="please do a dance",
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "clarification"
    assert "supported Nexus ARC command" in result.message


@pytest.mark.asyncio
async def test_execute_prefers_handler_supplied_bridge_usage(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    async def _fallback_usage(*, project_key=None, issue_number=None, workflow_id=None):
        del project_key, issue_number, workflow_id
        return UsagePayload(provider="fallback", input_tokens=1, output_tokens=1)

    monkeypatch.setattr("nexus.core.command_bridge.router.collect_bridge_usage_payload", _fallback_usage)

    async def _status_handler(*, client, user_id, text, args, raw_event=None, attachments=None):
        del user_id, text, args, attachments
        assert isinstance(raw_event, dict)
        raw_event["bridge_usage"] = {
            "provider": "openai",
            "model": "gpt-5.4-mini",
            "input_tokens": 22,
            "output_tokens": 8,
            "estimated_cost_usd": 0.11,
        }
        ctx = router.build_context(client=client, user_id="alice", text="status demo", args=["demo"])
        await ctx.reply_text("Status ready")

    router.register_command("status", _status_handler)

    result = await router.execute(
        CommandRequest(
            command="status",
            args=["demo"],
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.usage is not None
    assert result.usage.provider == "openai"
    assert result.usage.model == "gpt-5.4-mini"
    assert result.usage.input_tokens == 22


@pytest.mark.asyncio
async def test_get_workflow_status_includes_usage(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    async def _fake_usage(*, project_key=None, issue_number=None, workflow_id=None):
        assert project_key is None
        assert issue_number == "42"
        assert workflow_id == "demo-42-full"
        return UsagePayload(
            provider="openai",
            model="gpt-5.4",
            input_tokens=90,
            output_tokens=30,
            estimated_cost_usd=0.44,
            metadata={"source": "test"},
        )

    monkeypatch.setattr("nexus.core.command_bridge.router.collect_bridge_usage_payload", _fake_usage)
    router.workflow_deps.workflow_state_plugin_kwargs = {}

    class _WorkflowPlugin:
        async def get_workflow_status(self, issue_number):
            assert issue_number == "42"
            return {"state": "running"}

    monkeypatch.setattr(
        "nexus.core.command_bridge.router.get_workflow_state_plugin",
        lambda **_kwargs: _WorkflowPlugin(),
    )

    payload = await router.get_workflow_status("demo-42-full")

    assert payload["ok"] is True
    assert payload["usage"]["provider"] == "openai"
    assert payload["usage"]["input_tokens"] == 90


@pytest.mark.asyncio
async def test_execute_usage_command_returns_usage_summary(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    async def _fake_usage(*, project_key=None, issue_number=None, workflow_id=None):
        assert project_key == "demo"
        assert issue_number == "42"
        assert workflow_id == "demo-42-full"
        return UsagePayload(
            provider="openai",
            model="gpt-5.4",
            input_tokens=120,
            output_tokens=50,
            estimated_cost_usd=0.5,
            metadata={"source": "completion_storage", "total_tokens": 170},
        )

    monkeypatch.setattr("nexus.core.command_bridge.router.collect_bridge_usage_payload", _fake_usage)

    result = await router.execute(
        CommandRequest(
            command="usage",
            args=["demo#42"],
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "success"
    assert result.usage is not None
    assert "Nexus ARC usage summary" in result.message
    assert "Provider: openai" in result.message


@pytest.mark.asyncio
async def test_route_maps_spend_request_to_usage(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    async def _fake_usage(*, project_key=None, issue_number=None, workflow_id=None):
        del workflow_id
        assert project_key == "demo"
        assert issue_number == "42"
        return UsagePayload(provider="openai", input_tokens=10, output_tokens=5)

    monkeypatch.setattr("nexus.core.command_bridge.router.collect_bridge_usage_payload", _fake_usage)

    result = await router.route(
        CommandRequest(
            raw_text="show spending for demo#42",
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "success"
    assert result.usage is not None
    assert result.usage.provider == "openai"
