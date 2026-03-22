from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime


def _runtime() -> NexusAgentRuntime:
    return NexusAgentRuntime(finalize_fn=lambda *args, **kwargs: None)


def test_autofix_retry_allowed_when_recent_validation(monkeypatch):
    runtime = _runtime()

    monkeypatch.setattr(
        "nexus.core.audit_store.AuditStore.get_autofix_events",
        lambda issue_num, agent_type=None, limit=12: [
            {"event_type": "AUTOFIX_VALIDATED", "data": {"agent_type": "developer"}},
            {"event_type": "AUTOFIX_FAILED", "data": {"agent_type": "developer"}},
        ],
    )

    assert runtime._autofix_retry_allowed("42", "developer") is True


def test_autofix_retry_blocked_on_failure_streak(monkeypatch):
    runtime = _runtime()
    logged: list[tuple[int, str, str]] = []

    monkeypatch.setattr(
        "nexus.core.audit_store.AuditStore.get_autofix_events",
        lambda issue_num, agent_type=None, limit=12: [
            {"event_type": "AUTOFIX_FAILED", "data": {"agent_type": "developer"}},
            {"event_type": "AUTOFIX_FAILED", "data": {"agent_type": "developer"}},
            {"event_type": "AUTOFIX_FAILED", "data": {"agent_type": "developer"}},
        ],
    )
    monkeypatch.setattr(
        "nexus.core.audit_store.AuditStore.audit_log",
        lambda issue_num, event, details, user_id=None: logged.append(
            (int(issue_num), str(event), str(details))
        ),
    )

    assert runtime._autofix_retry_allowed("42", "developer") is False
    assert logged
    assert logged[0][1] == "AUTOFIX_RETRY_GUARD_BLOCKED"


def test_autofix_retry_allowed_when_issue_not_numeric(monkeypatch):
    runtime = _runtime()

    monkeypatch.setattr(
        "nexus.core.audit_store.AuditStore.get_autofix_events",
        lambda issue_num, agent_type=None, limit=12: [
            {"event_type": "AUTOFIX_FAILED", "data": {"agent_type": "developer"}},
        ],
    )

    assert runtime._autofix_retry_allowed("issue-abc", "developer") is True
