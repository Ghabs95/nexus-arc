"""Tests for dead/stuck-agent detection after Phase-3 refactor.

The dead- and stuck-agent detection logic has moved to
``nexus.core.ProcessOrchestrator`` (nexus-arc repo).  The full behavioural
test suite lives in nexus-arc's ``tests/test_process_orchestrator.py``.

This file verifies the nexus-side integration:
  - ``NexusAgentRuntime`` correctly delegates to ``AgentMonitor``,
    ``HostStateManager``, and ``notifications``
  - ``check_stuck_agents()`` in inbox_processor delegates to the orchestrator
    and records polling failures on exception
"""

import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

# ---------------------------------------------------------------------------
# NexusAgentRuntime hook tests
# ---------------------------------------------------------------------------


class TestNexusAgentRuntimeShouldRetry:
    def test_delegates_to_agent_monitor(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with (
            patch("nexus.core.state_manager.HostStateManager.load_launched_agents", return_value={}),
            patch("nexus.core.state_manager.HostStateManager.save_launched_agents"),
            patch("nexus.core.runtime.agent_monitor.AgentMonitor") as MockMonitor,
        ):
            MockMonitor.should_retry.return_value = True
            result = runtime.should_retry("42", "developer")

        MockMonitor.should_retry.assert_called_once_with("42", "developer")
        assert result is True

    def test_max_retries_returns_false(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with (
            patch("nexus.core.state_manager.HostStateManager.load_launched_agents", return_value={}),
            patch("nexus.core.state_manager.HostStateManager.save_launched_agents"),
            patch("nexus.core.runtime.agent_monitor.AgentMonitor") as MockMonitor,
        ):
            MockMonitor.should_retry.return_value = False
            result = runtime.should_retry("42", "developer")

        assert result is False

    def test_retry_fuse_trips_and_blocks_retry(self):
        from nexus.core.runtime.nexus_agent_runtime import (
            RETRY_FUSE_MAX_ATTEMPTS,
            NexusAgentRuntime,
        )

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with (
            patch("nexus.core.state_manager.HostStateManager.load_launched_agents", return_value={"42": {}}),
            patch("nexus.core.state_manager.HostStateManager.save_launched_agents") as save_mock,
            patch("nexus.core.runtime.agent_monitor.AgentMonitor") as MockMonitor,
            patch.object(runtime, "send_alert", return_value=True) as alert_mock,
        ):
            MockMonitor.should_retry.return_value = True

            # First calls are allowed by fuse and delegated to AgentMonitor
            for _ in range(RETRY_FUSE_MAX_ATTEMPTS):
                assert runtime.should_retry("42", "debug") is True

            # Next call trips fuse and blocks retry
            assert runtime.should_retry("42", "debug") is False

        assert alert_mock.called
        assert save_mock.called


class TestRetryFuseStatus:
    def test_returns_empty_status_when_missing(self):
        from nexus.core.runtime.nexus_agent_runtime import get_retry_fuse_status

        with patch("nexus.core.state_manager.HostStateManager.load_launched_agents", return_value={}):
            status = get_retry_fuse_status("404", now_ts=1700000000.0)

        assert status["exists"] is False
        assert status["attempts"] == 0
        assert status["trip_count_in_hard_window"] == 0

    def test_prunes_old_trip_times_from_hard_window(self):
        from nexus.core.runtime.nexus_agent_runtime import (
            RETRY_FUSE_HARD_WINDOW_SECONDS,
            get_retry_fuse_status,
        )

        now = 1700000000.0
        entry = {
            "retry_fuse": {
                "agent": "debug",
                "window_start": now - 30,
                "attempts": 2,
                "tripped": False,
                "alerted": False,
                "hard_tripped": False,
            },
            "retry_fuse_trip_times": [
                now - 10,
                now - (RETRY_FUSE_HARD_WINDOW_SECONDS + 1),
            ],
        }

        with patch(
            "nexus.core.state_manager.HostStateManager.load_launched_agents", return_value={"44": entry}
        ):
            status = get_retry_fuse_status("44", now_ts=now)

        assert status["exists"] is True
        assert status["agent"] == "debug"
        assert status["attempts"] == 2
        assert status["trip_count_in_hard_window"] == 1

    def test_retry_fuse_hard_stop_after_two_trips(self):
        from nexus.core.runtime.nexus_agent_runtime import (
            RETRY_FUSE_HARD_WINDOW_SECONDS,
            RETRY_FUSE_MAX_ATTEMPTS,
            NexusAgentRuntime,
        )

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        now = time.time()
        prior_trip = now - min(60, RETRY_FUSE_HARD_WINDOW_SECONDS - 1)

        seeded_entry = {
            "retry_fuse": {
                "agent": "debug",
                "window_start": now,
                "attempts": RETRY_FUSE_MAX_ATTEMPTS,
                "tripped": False,
                "alerted": False,
                "hard_tripped": False,
            },
            "retry_fuse_trip_times": [prior_trip],
        }

        with (
            patch(
                "nexus.core.state_manager.HostStateManager.load_launched_agents",
                return_value={"44": seeded_entry},
            ),
            patch("nexus.core.state_manager.HostStateManager.save_launched_agents") as save_mock,
            patch("nexus.core.runtime.agent_monitor.AgentMonitor") as MockMonitor,
            patch.object(runtime, "send_alert", return_value=True) as alert_mock,
        ):
            MockMonitor.should_retry.return_value = True

            assert runtime.should_retry("44", "debug") is False

        assert alert_mock.called
        assert save_mock.called


class TestNexusAgentRuntimeGetWorkflowState:
    def test_returns_cancelled_string(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value={"state": "cancelled"}):
                    result = runtime.get_workflow_state("10")

        assert result == "CANCELLED"

    def test_returns_paused_string(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value={"state": "paused"}):
                    result = runtime.get_workflow_state("11")

        assert result == "PAUSED"

    def test_returns_none_for_active(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value={"state": "active"}):
                    result = runtime.get_workflow_state("12")

        assert result is None

    def test_returns_none_for_missing_mapping(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            mock_wf.return_value.get_workflow_id.return_value = None
            result = runtime.get_workflow_state("10")

        assert result is None


class TestNexusAgentRuntimeIsProcessRunning:
    def test_true_when_runtime_ops_reports_running(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        ops = MagicMock()
        ops.is_issue_process_running.return_value = True

        with patch("nexus.core.orchestration.plugin_runtime.get_runtime_ops_plugin", return_value=ops):
            assert runtime.is_process_running("106") is True

    def test_true_when_pid_fallback_detects_alive_process(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        ops = MagicMock()
        ops.is_issue_process_running.return_value = False

        with (
            patch("nexus.core.orchestration.plugin_runtime.get_runtime_ops_plugin", return_value=ops),
            patch(
                "nexus.core.state_manager.HostStateManager.load_launched_agents",
                return_value={"106": {"pid": 12345}},
            ),
            patch("nexus.core.runtime.nexus_agent_runtime.os.kill", return_value=None) as kill_mock,
        ):
            assert runtime.is_process_running("106") is True
            kill_mock.assert_called_once_with(12345, 0)

    def test_false_when_runtime_ops_and_pid_fallback_fail(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        ops = MagicMock()
        ops.is_issue_process_running.return_value = False

        with (
            patch("nexus.core.orchestration.plugin_runtime.get_runtime_ops_plugin", return_value=ops),
            patch(
                "nexus.core.state_manager.HostStateManager.load_launched_agents",
                return_value={"106": {"pid": 12345}},
            ),
            patch("nexus.core.runtime.nexus_agent_runtime.os.kill", side_effect=OSError("not running")),
        ):
            assert runtime.is_process_running("106") is False


class TestNexusAgentRuntimeShouldRetryDeadAgent:
    def test_true_when_matching_running_step(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        payload = {
            "state": "active",
            "steps": [
                {"status": "RUNNING", "agent": {"name": "triage", "display_name": "Triage"}},
            ],
        }

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value=payload):
                    result = runtime.should_retry_dead_agent("44", "triage")

        assert result is True

    def test_false_when_no_running_step_matches(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        payload = {
            "state": "active",
            "steps": [
                {"status": "COMPLETED", "agent": {"name": "triage"}},
                {"status": "RUNNING", "agent": {"name": "debug"}},
            ],
        }

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value=payload):
                    result = runtime.should_retry_dead_agent("44", "triage")

        assert result is False


class TestNexusAgentRuntimeGetExpectedRunningAgent:
    def test_returns_running_agent_name(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        payload = {
            "state": "active",
            "steps": [
                {"status": "COMPLETED", "agent": {"name": "triage"}},
                {"status": "RUNNING", "agent": {"name": "debug", "display_name": "Debug"}},
            ],
        }

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value=payload):
                    result = runtime.get_expected_running_agent("44")

        assert result == "debug"

    def test_returns_none_for_terminal_workflow(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        payload = {"state": "completed", "steps": []}

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value=payload):
                    result = runtime.get_expected_running_agent("44")

        assert result is None

    def test_false_when_workflow_terminal(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        payload = {"state": "completed", "steps": []}

        with patch("nexus.core.integrations.workflow_state_factory.get_workflow_state") as mock_wf:
            with patch("builtins.open", create=True):
                with patch("json.load", return_value=payload):
                    result = runtime.should_retry_dead_agent("44", "triage")

        assert result is False


class TestNexusAgentRuntimeAuditLog:
    def test_delegates_to_audit_store(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.audit_store.AuditStore") as MockAudit:
            runtime.audit_log("55", "AGENT_DEAD", "PID 1234 exited")

        MockAudit.audit_log.assert_called_once_with(55, "AGENT_DEAD", "PID 1234 exited")

    def test_empty_details_passes_none(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.audit_store.AuditStore") as MockAudit:
            runtime.audit_log("55", "AGENT_DEAD")

        MockAudit.audit_log.assert_called_once_with(55, "AGENT_DEAD", None)


class TestNexusAgentRuntimeSendAlert:
    def test_delegates_to_telegram(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.integrations.notifications.emit_alert", return_value=True) as mock_tg:
            result = runtime.send_alert("hello")

        mock_tg.assert_called_once_with("hello", severity="warning", source="agent_runtime")
        assert result is True

    def test_returns_false_on_failure(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch("nexus.core.integrations.notifications.emit_alert", return_value=False):
            result = runtime.send_alert("hello")

        assert result is False


class TestNexusAgentRuntimePostCompletionComment:
    def test_returns_true_on_success(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        mock_platform = MagicMock()
        mock_platform.add_comment = AsyncMock(return_value=True)
        mock_platform.get_comments = AsyncMock(return_value=[])

        with patch("nexus.core.orchestration.nexus_core_helpers.get_git_platform", return_value=mock_platform):
            result = runtime.post_completion_comment("44", "owner/repo", "body")

        assert result is True

    def test_skips_automated_comment_when_recent_agent_comment_exists(self):
        from nexus.adapters.git.base import Comment

        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        recent_comment = Comment(
            id="1",
            issue_id="44",
            author="bot-user",
            body="## 🔨 Implement Change Complete — developer\n\nReady for **@Reviewer**",
            created_at=datetime.now(UTC) - timedelta(minutes=2),
            url="https://example.com/comment/1",
        )
        mock_platform = MagicMock()
        mock_platform.get_comments = AsyncMock(return_value=[recent_comment])
        mock_platform.add_comment = AsyncMock(return_value=True)

        with patch("nexus.core.orchestration.nexus_core_helpers.get_git_platform", return_value=mock_platform):
            result = runtime.post_completion_comment("44", "owner/repo", "body")

        assert result is True
        mock_platform.add_comment.assert_not_called()

    def test_does_not_skip_when_ready_for_without_structured_header(self):
        from nexus.adapters.git.base import Comment

        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        recent_comment = Comment(
            id="2",
            issue_id="44",
            author="bot-user",
            body="Ready for **@Reviewer**",
            created_at=datetime.now(UTC) - timedelta(minutes=2),
            url="https://example.com/comment/2",
        )
        mock_platform = MagicMock()
        mock_platform.get_comments = AsyncMock(return_value=[recent_comment])
        mock_platform.add_comment = AsyncMock(return_value=True)

        with patch("nexus.core.orchestration.nexus_core_helpers.get_git_platform", return_value=mock_platform):
            result = runtime.post_completion_comment("44", "owner/repo", "body")

        assert result is True
        mock_platform.add_comment.assert_called_once()

    def test_env_override_extends_recent_comment_window(self, monkeypatch):
        from nexus.adapters.git.base import Comment

        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        monkeypatch.setenv("NEXUS_RECENT_AGENT_COMMENT_WINDOW_SECONDS", "3600")

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        older_comment = Comment(
            id="3",
            issue_id="44",
            author="bot-user",
            body="## 🔨 Implement Change Complete — developer\n\nReady for **@Reviewer**",
            created_at=datetime.now(UTC) - timedelta(minutes=20),
            url="https://example.com/comment/3",
        )
        mock_platform = MagicMock()
        mock_platform.get_comments = AsyncMock(return_value=[older_comment])
        mock_platform.add_comment = AsyncMock(return_value=True)

        with patch("nexus.core.orchestration.nexus_core_helpers.get_git_platform", return_value=mock_platform):
            result = runtime.post_completion_comment("44", "owner/repo", "body")

        assert result is True
        mock_platform.add_comment.assert_not_called()

    def test_invalid_env_uses_default_window(self, monkeypatch):
        from nexus.adapters.git.base import Comment

        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        monkeypatch.setenv("NEXUS_RECENT_AGENT_COMMENT_WINDOW_SECONDS", "invalid")

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        older_comment = Comment(
            id="4",
            issue_id="44",
            author="bot-user",
            body="## 🔨 Implement Change Complete — developer\n\nReady for **@Reviewer**",
            created_at=datetime.now(UTC) - timedelta(minutes=20),
            url="https://example.com/comment/4",
        )
        mock_platform = MagicMock()
        mock_platform.get_comments = AsyncMock(return_value=[older_comment])
        mock_platform.add_comment = AsyncMock(return_value=True)

        with patch("nexus.core.orchestration.nexus_core_helpers.get_git_platform", return_value=mock_platform):
            result = runtime.post_completion_comment("44", "owner/repo", "body")

        assert result is True
        mock_platform.add_comment.assert_called_once()

    def test_returns_false_on_exception(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)

        with patch(
            "nexus.core.orchestration.nexus_core_helpers.get_git_platform", side_effect=RuntimeError("boom")
        ):
            result = runtime.post_completion_comment("44", "owner/repo", "body")

        assert result is False


class TestNexusAgentRuntimeIsIssueOpen:
    def test_calls_platform_get_issue_with_single_argument(self):
        from nexus.adapters.git.base import Issue
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        mock_platform = MagicMock()
        mock_platform.get_issue = AsyncMock(
            return_value=Issue(
                id="83",
                number=83,
                title="x",
                body="",
                state="open",
                labels=[],
                created_at=datetime.now(UTC),
                updated_at=datetime.now(UTC),
                url="https://github.com/owner/repo/issues/83",
            )
        )

        with patch("nexus.core.orchestration.nexus_core_helpers.get_git_platform", return_value=mock_platform):
            result = runtime.is_issue_open("83", "owner/repo")

        mock_platform.get_issue.assert_awaited_once_with("83")
        assert result is True

    def test_returns_false_when_issue_missing(self):
        from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

        runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
        mock_platform = MagicMock()
        mock_platform.get_issue = AsyncMock(return_value=None)

        with patch("nexus.core.orchestration.nexus_core_helpers.get_git_platform", return_value=mock_platform):
            result = runtime.is_issue_open("83", "owner/repo")

        assert result is False


# ---------------------------------------------------------------------------
# check_stuck_agents delegation smoke test
# ---------------------------------------------------------------------------


class TestCheckStuckAgentsDelegates:
    def test_delegates_to_process_orchestrator(self):
        """run_stuck_agents_cycle() must delegate to orchestrator checker."""
        from nexus.core.workflow_runtime.workflow_recovery_service import run_stuck_agents_cycle

        mock_orc = MagicMock()
        run_stuck_agents_cycle(
            logger=MagicMock(),
            base_dir="/tmp",
            scope="stuck-agents:loop",
            orchestrator_check_stuck_agents=mock_orc.check_stuck_agents,
            recover_orphaned_running_agents=lambda: 0,
            recover_unmapped_issues_from_completions=lambda: 0,
            clear_polling_failures=lambda _scope: None,
            record_polling_failure=lambda _scope, _exc: None,
        )

        mock_orc.check_stuck_agents.assert_called_once_with("/tmp")

    def test_records_polling_failure_on_exception(self):
        """An exception in run_stuck_agents_cycle must record a polling failure."""
        from nexus.core.workflow_runtime.workflow_recovery_service import run_stuck_agents_cycle
        from nexus.core.processor_runtime_state import ProcessorRuntimeState

        state = ProcessorRuntimeState()
        mock_orc = MagicMock()
        mock_orc.check_stuck_agents.side_effect = RuntimeError("boom")

        def _record(scope: str, _exc: Exception) -> None:
            state.polling_failure_counts[scope] = state.polling_failure_counts.get(scope, 0) + 1

        run_stuck_agents_cycle(
            logger=MagicMock(),
            base_dir="/tmp",
            scope="stuck-agents:loop",
            orchestrator_check_stuck_agents=mock_orc.check_stuck_agents,
            recover_orphaned_running_agents=lambda: 0,
            recover_unmapped_issues_from_completions=lambda: 0,
            clear_polling_failures=lambda _scope: None,
            record_polling_failure=_record,
        )

        assert state.polling_failure_counts.get("stuck-agents:loop", 0) >= 1
