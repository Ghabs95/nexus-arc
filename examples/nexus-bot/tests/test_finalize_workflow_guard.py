"""Tests for finalize workflow terminal-state guard."""


def test_finalize_workflow_skips_non_terminal_state(monkeypatch):
    from inbox_processor import _finalize_workflow

    class _WorkflowPlugin:
        async def get_workflow_status(self, issue_number: str):
            return {"state": "running", "issue": issue_number}

    class _Policy:
        def finalize_workflow(self, **_kwargs):
            raise AssertionError("finalize_workflow should not be called for non-terminal state")

    alerts = []

    monkeypatch.setattr(
        "inbox_processor.get_workflow_state_plugin", lambda **_kwargs: _WorkflowPlugin()
    )
    monkeypatch.setattr("inbox_processor.get_workflow_policy_plugin", lambda **_kwargs: _Policy())
    monkeypatch.setattr(
        "inbox_processor.emit_alert", lambda message, **kwargs: alerts.append(message) or True
    )

    _finalize_workflow("55", "sample-org/nexus-arc", "writer", "nexus")

    assert alerts
    assert "Finalization blocked" in alerts[0]
