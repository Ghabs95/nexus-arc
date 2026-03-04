"""Regression tests for WorkflowWatchService event routing semantics."""

from __future__ import annotations

from nexus.core.workflow_runtime import workflow_watch_service as watch_module
from nexus.core.workflow_runtime.workflow_watch_service import WorkflowWatchService
from nexus.core.state_manager import HostStateManager


def _build_service(monkeypatch) -> WorkflowWatchService:
    monkeypatch.setenv("NEXUS_TELEGRAM_WATCH_ENABLED", "false")
    monkeypatch.setattr(HostStateManager, "load_workflow_watch_subscriptions", lambda: {})
    monkeypatch.setattr(HostStateManager, "save_workflow_watch_subscriptions", lambda _data: None)
    return WorkflowWatchService()


def test_watch_routes_events_by_project_workflow_scope(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))

    service.start_watch(chat_id=101, user_id=1, project_key="nexus", issue_num="106")
    service.start_watch(chat_id=202, user_id=2, project_key="sample_app", issue_num="106")

    service._handle_event(
        "step_status_changed",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "step_id": "developer",
            "agent_type": "developer",
            "status": "running",
        },
    )

    assert sent_messages == [(101, "▶️ #106 developer · developer → running")]
    assert service._subscriptions["101:1"].workflow_id == "nexus-106-full"
    assert service._subscriptions["202:2"].workflow_id == ""


def test_workflow_completed_only_unsubscribes_matching_project_issue(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))

    service.start_watch(chat_id=101, user_id=1, project_key="nexus", issue_num="106")
    service.start_watch(chat_id=202, user_id=2, project_key="sample_app", issue_num="106")

    service._handle_event(
        "workflow_completed",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "status": "success",
            "summary": "done",
        },
    )

    assert "101:1" not in service._subscriptions
    assert "202:2" in service._subscriptions
    assert sent_messages == [(101, "✅ Workflow #106 completed: success — done")]


def test_mermaid_dedup_uses_content_hash_not_length(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))
    monkeypatch.setattr(watch_module, "_DEFAULT_THROTTLE_SECONDS", 0.0)

    service.start_watch(
        chat_id=101,
        user_id=1,
        project_key="nexus",
        issue_num="106",
        mermaid_enabled=True,
    )

    service._handle_event(
        "mermaid_diagram",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "diagram": "abc",
        },
    )
    service._handle_event(
        "mermaid_diagram",
        {
            "issue": "106",
            "workflow_id": "nexus-106-full",
            "diagram": "xyz",
        },
    )

    assert len(sent_messages) == 2
    assert sent_messages[0] == (101, "🧭 Workflow #106 diagram updated.")
    assert sent_messages[1] == (101, "🧭 Workflow #106 diagram updated.")


def test_start_watch_sends_initial_snapshot(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))

    def mock_fetcher(issue_num, project_key):
        return {
            "workflow_state": "running",
            "current_step": "2/5",
            "current_step_name": "developer",
            "current_agent": "developer",
        }

    service.bind_snapshot_fetcher(mock_fetcher)
    service.start_watch(chat_id=101, user_id=1, project_key="nexus", issue_num="106")

    assert len(sent_messages) == 1
    assert "Watching workflow #106 (nexus)" in sent_messages[0][1]
    assert "Status: running" in sent_messages[0][1]
    assert "Step: 2/5 (developer)" in sent_messages[0][1]
    assert "Agent: developer" in sent_messages[0][1]


def test_reconnect_sends_snapshots_to_all_subscribers(monkeypatch):
    service = _build_service(monkeypatch)
    sent_messages: list[tuple[int, str]] = []
    service._send_message = lambda chat_id, text: sent_messages.append((chat_id, text))

    def mock_fetcher(issue_num, project_key):
        return {
            "workflow_state": "running",
            "current_step": "1/1",
            "current_step_name": "task",
            "current_agent": "dev",
        }

    service.bind_snapshot_fetcher(mock_fetcher)
    service.start_watch(chat_id=101, user_id=1, project_key="p1", issue_num="1")
    service.start_watch(chat_id=202, user_id=2, project_key="p2", issue_num="2")
    # clear initial snapshots from start_watch
    sent_messages.clear()

    service._send_reconnect_snapshots()
    assert len(sent_messages) == 2
    assert "Watching workflow #1 (p1)" in sent_messages[0][1]
    assert "Watching workflow #2 (p2)" in sent_messages[1][1]
