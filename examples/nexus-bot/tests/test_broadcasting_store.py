"""Unit tests for the :class:`_BroadcastingStore` decorator in
:mod:`integrations.workflow_state_factory`.

Verifies that SocketIO emit is called on ``map_issue`` and that all methods
delegate correctly to the wrapped inner store.
"""

from __future__ import annotations

import sys
from typing import cast
from unittest.mock import MagicMock, patch

import pytest

from nexus.core.workflow_state import WorkflowStateStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def inner() -> MagicMock:
    """A mock WorkflowStateStore that acts as the inner delegate."""
    mock = MagicMock()
    mock.get_workflow_id.return_value = "wf-42"
    mock.load_all_mappings.return_value = {"1": "wf-1"}
    mock.get_pending_approval.return_value = {"step_num": 1}
    mock.load_all_approvals.return_value = {"1": {"step_num": 1}}
    return mock


@pytest.fixture()
def broadcasting(inner: MagicMock, monkeypatch):
    """Create a _BroadcastingStore wrapping the mock inner store."""
    # Avoid import-time side effects from config module.
    monkeypatch.setenv("DATA_DIR", "/tmp/test")

    from nexus.core.integrations.workflow_state_factory import _BroadcastingStore

    return _BroadcastingStore(cast(WorkflowStateStore, cast(object, inner)))


# ---------------------------------------------------------------------------
# Delegation tests
# ---------------------------------------------------------------------------


class TestDelegation:
    def test_map_issue_delegates(self, broadcasting, inner: MagicMock) -> None:
        with patch("nexus.core.integrations.workflow_state_factory._BroadcastingStore._emit"):
            broadcasting.map_issue("10", "wf-10")
        inner.map_issue.assert_called_once_with("10", "wf-10")

    def test_get_workflow_id_delegates(self, broadcasting, inner: MagicMock) -> None:
        result = broadcasting.get_workflow_id("42")
        inner.get_workflow_id.assert_called_once_with("42")
        assert result == "wf-42"

    def test_remove_mapping_delegates(self, broadcasting, inner: MagicMock) -> None:
        broadcasting.remove_mapping("10")
        inner.remove_mapping.assert_called_once_with("10")

    def test_load_all_mappings_delegates(self, broadcasting, inner: MagicMock) -> None:
        result = broadcasting.load_all_mappings()
        inner.load_all_mappings.assert_called_once()
        assert result == {"1": "wf-1"}

    def test_set_pending_approval_delegates(self, broadcasting, inner: MagicMock) -> None:
        broadcasting.set_pending_approval("42", 3, "deploy", ["lead"], 3600)
        inner.set_pending_approval.assert_called_once_with(
            "42",
            3,
            "deploy",
            ["lead"],
            3600,
        )

    def test_clear_pending_approval_delegates(self, broadcasting, inner: MagicMock) -> None:
        broadcasting.clear_pending_approval("42")
        inner.clear_pending_approval.assert_called_once_with("42")

    def test_get_pending_approval_delegates(self, broadcasting, inner: MagicMock) -> None:
        result = broadcasting.get_pending_approval("42")
        inner.get_pending_approval.assert_called_once_with("42")
        assert result == {"step_num": 1}

    def test_load_all_approvals_delegates(self, broadcasting, inner: MagicMock) -> None:
        result = broadcasting.load_all_approvals()
        inner.load_all_approvals.assert_called_once()
        assert result == {"1": {"step_num": 1}}


# ---------------------------------------------------------------------------
# SocketIO emit tests
# ---------------------------------------------------------------------------


class TestSocketIOEmit:
    def test_map_issue_emits_event(self, broadcasting, inner: MagicMock) -> None:
        with patch("nexus.core.integrations.workflow_state_factory._BroadcastingStore._emit") as emit_mock:
            broadcasting.map_issue("10", "wf-10")
        emit_mock.assert_called_once()
        args = emit_mock.call_args
        assert args[0][0] == "workflow_mapped"
        assert args[0][1]["issue"] == "10"
        assert args[0][1]["workflow_id"] == "wf-10"

    def test_emit_noop_when_no_emitter(self, broadcasting) -> None:
        """When _socketio_emitter is None, _emit should not raise."""
        with patch.dict(sys.modules, {"nexus.core.state_manager": MagicMock(_socketio_emitter=None)}):
            # Should not raise
            broadcasting._emit("test_event", {"key": "value"})

    def test_emit_calls_emitter(self, broadcasting) -> None:
        emitter = MagicMock()
        sm_mock = MagicMock(_socketio_emitter=emitter)
        with patch.dict(sys.modules, {"nexus.core.state_manager": sm_mock}):
            broadcasting._emit("evt", {"k": "v"})
        emitter.assert_called_once_with("evt", {"k": "v"})

    def test_emit_swallows_exceptions(self, broadcasting) -> None:
        emitter = MagicMock(side_effect=RuntimeError("boom"))
        sm_mock = MagicMock(_socketio_emitter=emitter)
        with patch.dict(sys.modules, {"state_manager": sm_mock}):
            # Should log warning, not raise
            broadcasting._emit("evt", {"k": "v"})
