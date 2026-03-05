"""Unit tests for agent_monitor module."""

from unittest.mock import MagicMock, patch

from nexus.core.router import WorkflowRouter
from nexus.core.runtime.agent_monitor import AgentMonitor


class TestAgentMonitor:
    """Tests for AgentMonitor class."""

    @patch("nexus.core.runtime.agent_monitor.get_runtime_ops_plugin")
    def test_kill_agent_success(self, mock_get_runtime_ops):
        """Test kill_agent successfully terminates via runtime-ops plugin."""
        runtime_ops = MagicMock()
        runtime_ops.kill_process.return_value = True
        mock_get_runtime_ops.return_value = runtime_ops

        with patch.object(AgentMonitor, "_pid_alive", return_value=False):
            result = AgentMonitor.kill_agent(12345, "42")
        assert result is True
        runtime_ops.kill_process.assert_called_once_with(12345, force=False)

    @patch("nexus.core.runtime.agent_monitor.get_runtime_ops_plugin")
    def test_kill_agent_failure(self, mock_get_runtime_ops):
        """Test kill_agent handles plugin kill failures gracefully."""
        runtime_ops = MagicMock()
        runtime_ops.kill_process.return_value = False
        mock_get_runtime_ops.return_value = runtime_ops

        result = AgentMonitor.kill_agent(12345, "42")
        assert result is False
        runtime_ops.kill_process.assert_called_once_with(12345, force=False)

    @patch("nexus.core.runtime.agent_monitor.get_runtime_ops_plugin")
    @patch("nexus.core.runtime.agent_monitor.time.sleep", return_value=None)
    def test_kill_agent_escalates_to_force_after_grace(self, _mock_sleep, mock_get_runtime_ops):
        """Escalate to force kill when graceful termination does not stop the PID."""
        runtime_ops = MagicMock()
        runtime_ops.kill_process.side_effect = [True, True]
        mock_get_runtime_ops.return_value = runtime_ops

        # Stay alive throughout grace loop and final check.
        with patch.object(AgentMonitor, "_pid_alive", side_effect=[True] * 25):
            result = AgentMonitor.kill_agent(12345, "42")

        assert result is True
        assert runtime_ops.kill_process.call_args_list[0].kwargs == {"force": False}
        assert runtime_ops.kill_process.call_args_list[1].kwargs == {"force": True}

    @patch("nexus.core.runtime.agent_monitor.get_runtime_ops_plugin")
    @patch("os.path.getmtime")
    @patch("time.time")
    def test_check_timeout_detected(self, mock_time, mock_getmtime, mock_get_runtime_ops):
        """Test check_timeout detects timeout when plugin reports active PID."""
        mock_time.return_value = 1000.0
        mock_getmtime.return_value = 1000.0 - (16 * 60)  # 16 minutes ago

        runtime_ops = MagicMock()
        runtime_ops.find_agent_pid_for_issue.return_value = 12345
        mock_get_runtime_ops.return_value = runtime_ops

        timed_out, pid = AgentMonitor.check_timeout("42", "/tmp/test.log", timeout_seconds=180)

        assert timed_out is True
        assert pid == 12345

    @patch("nexus.core.runtime.agent_monitor.get_runtime_ops_plugin")
    @patch("os.path.getmtime")
    @patch("time.time")
    def test_check_timeout_not_detected(self, mock_time, mock_getmtime, mock_get_runtime_ops):
        """Test check_timeout when no timeout has occurred."""
        mock_time.return_value = 1000.0
        mock_getmtime.return_value = 1000.0 - 60  # 1 minute ago

        runtime_ops = MagicMock()
        runtime_ops.find_agent_pid_for_issue.return_value = None
        mock_get_runtime_ops.return_value = runtime_ops

        timed_out, pid = AgentMonitor.check_timeout("42", "/tmp/test.log")

        assert timed_out is False
        assert pid is None


class TestWorkflowRouter:
    """Tests for nexus.core.router.WorkflowRouter."""

    def test_detect_workflow_tier_explicit_full(self):
        labels = ["workflow:full", "feature"]
        assert WorkflowRouter.detect_tier_from_labels(labels) == "full"

    def test_detect_workflow_tier_explicit_shortened(self):
        labels = ["workflow:shortened", "bug"]
        assert WorkflowRouter.detect_tier_from_labels(labels) == "shortened"

    def test_detect_workflow_tier_explicit_fast_track(self):
        labels = ["workflow:fast-track", "critical"]
        assert WorkflowRouter.detect_tier_from_labels(labels) == "fast-track"

    def test_detect_workflow_tier_auto_critical(self):
        labels = ["priority:critical"]
        assert WorkflowRouter.detect_tier_from_labels(labels) == "fast-track"

    def test_detect_workflow_tier_auto_bug(self):
        labels = ["bug", "backend"]
        assert WorkflowRouter.detect_tier_from_labels(labels) == "shortened"

    def test_detect_workflow_tier_auto_feature(self):
        labels = ["feature", "enhancement"]
        assert WorkflowRouter.detect_tier_from_labels(labels) == "full"

    def test_detect_workflow_tier_default(self):
        labels = ["documentation", "question"]
        assert WorkflowRouter.detect_tier_from_labels(labels) == "full"

    def test_suggest_tier_label_critical(self):
        result = WorkflowRouter.suggest_tier_label(
            "URGENT: Production down", "Critical hotfix needed ASAP"
        )
        assert result == "workflow:fast-track"

    def test_suggest_tier_label_bug(self):
        result = WorkflowRouter.suggest_tier_label(
            "Fix broken login", "There's a bug in the authentication system"
        )
        assert result == "workflow:shortened"

    def test_suggest_tier_label_feature(self):
        result = WorkflowRouter.suggest_tier_label(
            "Add new dashboard", "We need a new feature for analytics"
        )
        assert result == "workflow:full"

    def test_suggest_tier_label_no_match(self):
        result = WorkflowRouter.suggest_tier_label(
            "Question about docs", "Just wondering about something"
        )
        assert result is None
