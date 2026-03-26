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
        "nexus.core.command_bridge.router.hands_free_bridge_deps",
        lambda **_kwargs: SimpleNamespace(
            requester_context_builder=None,
            feature_ideation_deps=SimpleNamespace(requester_context_builder=None),
        ),
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
async def test_execute_binds_bridge_requester_identity(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, object] = {}

    class _FakeUser:
        nexus_id = "generated-user"

    class _FakeUserManager:
        def get_or_create_user_by_identity(self, *, platform, platform_user_id, username=None, first_name=None):
            captured["created"] = {
                "platform": platform,
                "platform_user_id": platform_user_id,
                "username": username,
                "first_name": first_name,
            }
            return _FakeUser()

        def merge_users(self, target_nexus_id, source_nexus_id):
            captured["merged"] = {
                "target_nexus_id": target_nexus_id,
                "source_nexus_id": source_nexus_id,
            }
            return target_nexus_id

    monkeypatch.setattr("nexus.core.command_bridge.router.get_user_manager", lambda: _FakeUserManager())

    async def _status_handler(*, client, user_id, text, args, raw_event=None, attachments=None):
        del user_id, text, args, raw_event, attachments
        ctx = router.build_context(client=client, user_id="alice", text="status demo", args=["demo"])
        await ctx.reply_text("Status ready")

    router.register_command("status", _status_handler)

    await router.execute(
        CommandRequest(
            command="status",
            args=["demo"],
            requester=RequesterContext(
                source_platform="openclaw",
                sender_id="alice",
                sender_name="Alice",
                nexus_id="openclaw:user:alice",
                auth_authority="openclaw",
                is_authorized_sender=True,
            ),
        )
    )

    assert captured["created"] == {
        "platform": "openclaw",
        "platform_user_id": "alice",
        "username": "Alice",
        "first_name": "Alice",
    }
    assert captured["merged"] == {
        "target_nexus_id": "openclaw:user:alice",
        "source_nexus_id": "generated-user",
    }


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

    class _FakeOperatorService:
        async def workflow_status(self, *, workflow_id=None, issue_number=None):
            assert workflow_id == "demo-42-full"
            assert issue_number is None
            return {
                "ok": True,
                "workflow": {
                    "workflow_id": "demo-42-full",
                    "issue_number": "42",
                    "project_key": None,
                },
                "plugin_status": {"state": "running"},
            }

    router.operator_service = _FakeOperatorService()

    payload = await router.get_workflow_status("demo-42-full")

    assert payload["ok"] is True
    assert payload["plugin_status"] == {"state": "running"}
    assert payload["usage"]["provider"] == "openai"
    assert payload["usage"]["input_tokens"] == 90


def test_get_capabilities_reports_bridge_enabled_commands(router: CommandRouter):
    capabilities = router.get_capabilities()

    assert capabilities["ok"] is True
    assert capabilities["route_enabled"] is True
    assert "chat" in capabilities["supported_commands"]
    assert "chatagents" in capabilities["supported_commands"]
    assert "kill" in capabilities["supported_commands"]
    assert "plan" in capabilities["supported_commands"]
    assert "reconcile" in capabilities["supported_commands"]
    assert "reprocess" in capabilities["supported_commands"]
    assert "wfstate" in capabilities["supported_commands"]
    assert "plan" in capabilities["long_running_commands"]
    assert "reprocess" in capabilities["long_running_commands"]


@pytest.mark.asyncio
async def test_execute_chat_with_args_routes_through_hands_free_text(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    captured: dict[str, object] = {}

    async def _fake_route_hands_free_text(ctx, deps):
        del deps
        captured["text"] = ctx.text
        captured["chat_session_active"] = ctx.user_state.get("chat_session_active")
        await ctx.reply_text("Workspace chat reply")

    monkeypatch.setattr("nexus.core.command_bridge.router.route_hands_free_text", _fake_route_hands_free_text)

    result = await router.execute(
        CommandRequest(
            command="chat",
            args=["Review", "the", "workspace"],
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert captured["text"] == "Review the workspace"
    assert captured["chat_session_active"] is True
    assert result.status == "success"
    assert result.message == "Workspace chat reply"


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


@pytest.mark.asyncio
async def test_router_operator_helpers_delegate_to_service(router: CommandRouter):
    class _FakeOperatorService:
        async def runtime_health(self):
            return {"ok": True, "runtime": "ok"}

        async def active_workflows(self, *, limit: int = 20):
            return {"ok": True, "limit": limit}

        async def recent_failures(self, *, limit: int = 20):
            return {"ok": True, "limit": limit}

        async def recent_incidents(self, *, limit: int = 20):
            return {"ok": True, "limit": limit}

        async def git_identity_status(self):
            return {"ok": True, "github": {"installed": True}}

        async def routing_explain(self, **kwargs):
            return {"ok": True, **kwargs}

        async def continue_workflow(self, **kwargs):
            return {"ok": True, "action": "continue", **kwargs}

        async def retry_step(self, **kwargs):
            return {"ok": True, "action": "retry", **kwargs}

        async def cancel_workflow(self, **kwargs):
            return {"ok": True, "action": "cancel", **kwargs}

        async def refresh_state(self, **kwargs):
            return {"ok": True, "action": "refresh", **kwargs}

    router.operator_service = _FakeOperatorService()

    assert (await router.get_runtime_health())["runtime"] == "ok"
    assert (await router.get_active_workflows(limit=5))["limit"] == 5
    assert (await router.get_recent_failures(limit=3))["limit"] == 3
    assert (await router.get_recent_incidents(limit=4))["limit"] == 4
    assert (await router.get_git_identity_status())["github"]["installed"] is True
    assert (await router.explain_routing(project_key="nexus"))["project_key"] == "nexus"
    assert (await router.continue_workflow(issue_number="42"))["action"] == "continue"
    assert (await router.retry_workflow_step(issue_number="42", target_agent="developer"))["action"] == "retry"
    assert (await router.cancel_workflow(issue_number="42"))["action"] == "cancel"
    assert (await router.refresh_workflow_state(issue_number="42"))["action"] == "refresh"


@pytest.mark.asyncio
async def test_router_workflow_summary_helper_delegates_to_service(router: CommandRouter):
    class _FakeOperatorService:
        async def workflow_summary(self, **kwargs):
            return {"ok": True, "summary": "demo", **kwargs}

        async def workflow_timeline(self, **kwargs):
            return {"ok": True, "timeline": [{"step_num": 1}], **kwargs}

        async def workflow_logs_context(self, **kwargs):
            return {"ok": True, "log_context": [{"file": "demo.log"}], **kwargs}

    router.operator_service = _FakeOperatorService()

    payload = await router.get_workflow_summary(workflow_id="demo-42-full")
    timeline = await router.get_workflow_timeline(workflow_id="demo-42-full")
    logs_context = await router.get_workflow_logs_context(workflow_id="demo-42-full")

    assert payload["ok"] is True
    assert payload["summary"] == "demo"
    assert payload["workflow_id"] == "demo-42-full"
    assert timeline["timeline"][0]["step_num"] == 1
    assert logs_context["log_context"][0]["file"] == "demo.log"


@pytest.mark.asyncio
async def test_router_workflow_diagnosis_helper_delegates_to_service(router: CommandRouter):
    class _FakeOperatorService:
        async def workflow_diagnosis(self, **kwargs):
            return {"ok": True, "diagnosis": "handoff_pending", **kwargs}

    router.operator_service = _FakeOperatorService()

    payload = await router.get_workflow_diagnosis(workflow_id="demo-42-full")

    assert payload["ok"] is True
    assert payload["diagnosis"] == "handoff_pending"
    assert payload["workflow_id"] == "demo-42-full"


@pytest.mark.asyncio
async def test_route_maps_workflow_summary_request_to_summary(router: CommandRouter):
    async def _summary_handler(*, client, user_id, text, args, raw_event=None, attachments=None):
        del user_id, text, raw_event, attachments
        ctx = router.build_context(client=client, user_id="alice", text="summary", args=args)
        await ctx.reply_text("Workflow summary")

    router.register_command("summary", _summary_handler)

    result = await router.route(
        CommandRequest(
            raw_text="why is workflow demo#42 stuck?",
            requester=RequesterContext(source_platform="openclaw", sender_id="alice"),
        )
    )

    assert result.status == "success"
    assert result.message == "Workflow summary"


@pytest.mark.asyncio
async def test_get_workflow_status_includes_backwards_compat_status_key(
    router: CommandRouter, monkeypatch: pytest.MonkeyPatch
):
    async def _fake_usage(*, project_key=None, issue_number=None, workflow_id=None):
        del project_key, issue_number, workflow_id
        return None

    monkeypatch.setattr("nexus.core.command_bridge.router.collect_bridge_usage_payload", _fake_usage)

    class _FakeOperatorService:
        async def workflow_status(self, *, workflow_id=None, issue_number=None):
            return {
                "ok": True,
                "workflow": {"workflow_id": "demo-42-full", "issue_number": "42", "project_key": None},
                "plugin_status": {"state": "running", "phase": "executing"},
            }

    router.operator_service = _FakeOperatorService()

    payload = await router.get_workflow_status("demo-42-full")

    assert payload["ok"] is True
    # The legacy "status" key must be present and mirror plugin_status.
    assert "status" in payload
    assert payload["status"] == {"state": "running", "phase": "executing"}
