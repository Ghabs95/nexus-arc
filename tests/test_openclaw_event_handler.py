from unittest.mock import AsyncMock

import pytest


@pytest.mark.asyncio
async def test_openclaw_event_handler_sends_rich_step_failed_notification(monkeypatch):
    from nexus.core.events import StepFailed
    from nexus.plugins.builtin.openclaw_event_handler_plugin import OpenClawEventHandler

    handler = OpenClawEventHandler.__new__(OpenClawEventHandler)
    handler._subscriptions = []
    handler._last_send_ok = True
    handler._channel = type("_FakeChannel", (), {})()
    handler._channel.send_workflow_notification = AsyncMock(return_value=True)

    event = StepFailed(
        workflow_id="nexus-130-full",
        step_num=5,
        step_name="compliance",
        agent_type="compliance",
        error="Critical auth gap detected",
        data={
            "project_key": "nexus",
            "issue_number": "130",
            "repo": "ghabs-org/nexus-arc",
            "current_step": "compliance blocked",
            "blocked_reason": "Critical finding requires developer rework",
            "correlation_token": "corr-130-step5",
        },
    )

    await handler._handle(event)

    call = handler._channel.send_workflow_notification.await_args.kwargs
    assert call["event_type"] == "step.failed"
    assert call["workflow_id"] == "nexus-130-full"
    assert call["project_key"] == "nexus"
    assert call["issue_number"] == "130"
    assert call["step_num"] == 5
    assert call["agent_type"] == "compliance"
    assert call["severity"] == "error"
    assert call["blocked_reason"] == "Critical finding requires developer rework"
    assert call["correlation_token"] == "corr-130-step5"
    assert "Critical auth gap detected" in call["key_findings"][0]
    assert call["suggested_actions"] == ["show_status", "show_logs", "continue"]


@pytest.mark.asyncio
async def test_openclaw_event_handler_sends_approval_required_notification(monkeypatch):
    from nexus.core.events import ApprovalRequired
    from nexus.plugins.builtin.openclaw_event_handler_plugin import OpenClawEventHandler

    handler = OpenClawEventHandler.__new__(OpenClawEventHandler)
    handler._subscriptions = []
    handler._last_send_ok = True
    handler._channel = type("_FakeChannel", (), {})()
    handler._channel.send_workflow_notification = AsyncMock(return_value=True)

    event = ApprovalRequired(
        workflow_id="nexus-200-full",
        step_num=6,
        step_name="merge approval",
        agent="deployer",
        approvers=["gab"],
        data={"project_key": "nexus", "issue_number": "200"},
    )

    await handler._handle(event)

    call = handler._channel.send_workflow_notification.await_args.kwargs
    assert call["event_type"] == "workflow.approval_required"
    assert call["agent_type"] == "deployer"
    assert call["severity"] == "warning"
    assert call["suggested_actions"] == ["approve", "reject", "show_logs"]
    assert "Human approval is required" in call["summary"]
