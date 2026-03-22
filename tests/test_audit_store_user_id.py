import asyncio

from nexus.core.audit_store import AuditStore


def test_audit_log_forwards_user_id_to_storage(monkeypatch):
    captured: dict[str, object] = {}

    class _Store:
        async def log(self, *, workflow_id, event_type, data, user_id=None):  # noqa: ANN001
            captured["workflow_id"] = workflow_id
            captured["event_type"] = event_type
            captured["data"] = data
            captured["user_id"] = user_id

    class _WorkflowState:
        def get_workflow_id(self, issue_number):  # noqa: ANN001
            return f"wf-{issue_number}"

    monkeypatch.setattr(
        "nexus.core.integrations.workflow_state_factory.get_workflow_state",
        lambda: _WorkflowState(),
    )
    monkeypatch.setattr(
        AuditStore,
        "_get_core_store",
        classmethod(lambda _cls: _Store()),
    )
    monkeypatch.setattr(
        "nexus.core.audit_store._run_coro_sync",
        lambda coro_factory: asyncio.run(coro_factory()),
    )

    AuditStore.audit_log(
        42,
        "AGENT_LAUNCHED",
        "Launched gemini agent",
        user_id="nexus-user-42",
    )

    assert captured["workflow_id"] == "wf-42"
    assert captured["event_type"] == "AGENT_LAUNCHED"
    assert captured["user_id"] == "nexus-user-42"


def test_get_autofix_events_filters_by_agent_and_type(monkeypatch):
    sample = [
        {
            "workflow_id": "wf-42",
            "timestamp": "2026-03-22T10:00:00+00:00",
            "event_type": "AUTOFIX_ATTEMPTED",
            "data": {"agent_type": "developer", "error_fingerprint": "fp-a"},
            "user_id": None,
        },
        {
            "workflow_id": "wf-42",
            "timestamp": "2026-03-22T10:01:00+00:00",
            "event_type": "AUTOFIX_FAILED",
            "data": {"agent_type": "developer", "error_fingerprint": "fp-a"},
            "user_id": None,
        },
        {
            "workflow_id": "wf-42",
            "timestamp": "2026-03-22T10:02:00+00:00",
            "event_type": "STEP_RETRY",
            "data": {"agent_type": "developer"},
            "user_id": None,
        },
    ]

    monkeypatch.setattr(AuditStore, "get_audit_history", staticmethod(lambda _i, limit=50: sample))

    matches = AuditStore.get_autofix_events(
        42,
        agent_type="developer",
        error_fingerprint="fp-a",
        limit=2,
    )

    assert len(matches) == 2
    assert all(str(item["event_type"]).startswith("AUTOFIX_") for item in matches)


def test_get_autofix_events_uses_bounded_fetch_limit(monkeypatch):
    captured: dict[str, int] = {"limit": 0}

    def _history(_issue: int, limit: int = 50):
        captured["limit"] = int(limit)
        return []

    monkeypatch.setattr(AuditStore, "get_audit_history", staticmethod(_history))

    _ = AuditStore.get_autofix_events(7, limit=9)

    assert captured["limit"] >= 45
    assert captured["limit"] <= 500
