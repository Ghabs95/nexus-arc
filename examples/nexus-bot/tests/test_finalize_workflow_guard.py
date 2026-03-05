"""Tests for finalize workflow terminal-state guard."""


def test_finalize_workflow_skips_non_terminal_state(monkeypatch):
    from nexus.core.issue_finalize import verify_workflow_terminal_before_finalize

    class _WorkflowPlugin:
        async def get_workflow_status(self, issue_number: str):
            return {"state": "running", "issue": issue_number}

    alerts = []
    monkeypatch.setattr(
        "nexus.core.issue_finalize.emit_alert",
        lambda message, **_kwargs: alerts.append(message) or True,
    )
    allowed = verify_workflow_terminal_before_finalize(
        workflow_plugin=_WorkflowPlugin(),
        issue_num="55",
        project_name="nexus",
    )
    assert allowed is False
    assert alerts and "Finalization blocked" in alerts[0]
