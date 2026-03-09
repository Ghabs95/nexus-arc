"""Tests for ProcessOrchestrator and AgentRuntime (Phase 2)."""

import os
import time
from typing import Any
from unittest.mock import MagicMock, patch

from nexus.core.models import (
    Agent,
    StepStatus,
    Workflow,
    WorkflowState,
    WorkflowStep,
)
from nexus.core.process_orchestrator import (
    AgentRuntime,
    ProcessOrchestrator,
    _is_terminal,
)


# ---------------------------------------------------------------------------
# Stub AgentRuntime — minimal concrete implementation for testing
# ---------------------------------------------------------------------------


class StubRuntime(AgentRuntime):
    def __init__(self, *, alert_success: bool = True, retry: bool = True):
        self.launched: list[dict] = []
        self.tracker: dict[str, dict] = {}
        self.alerts: list[str] = []
        self.audit_events: list[tuple] = []
        self.finalized: list[dict] = []
        self._retry = retry
        self._dead_retry = True
        self._alert_success = alert_success
        self._workflow_states: dict[str, str] = {}
        self._running_agents: dict[str, str | None] = {}
        self._timeout_results: dict[str, tuple[bool, Any]] = {}
        self._agent_timeout_seconds: int = 3600
        self._post_comment_success = True
        self.posted_comments: list[dict] = []
        self._is_issue_open_fn = lambda issue, repo: None

    def launch_agent(self, issue_number, agent_type, *, trigger_source="", exclude_tools=None):
        self.launched.append(
            {
                "issue": issue_number,
                "agent_type": agent_type,
                "trigger": trigger_source,
                "exclude_tools": exclude_tools,
            }
        )
        return (99999, "copilot")

    def load_launched_agents(self, recent_only=True):
        return dict(self.tracker)

    def save_launched_agents(self, data):
        self.tracker = dict(data)

    def clear_launch_guard(self, issue_number):
        pass

    def should_retry(self, issue_number, agent_type):
        return self._retry

    def should_retry_dead_agent(self, issue_number, agent_type):
        return self._dead_retry

    def send_alert(self, message):
        self.alerts.append(message)
        return self._alert_success

    def audit_log(self, issue_number, event, details=""):
        self.audit_events.append((issue_number, event, details))

    def finalize_workflow(self, issue_number, repo, last_agent, project_name):
        self.finalized.append({"issue": issue_number, "last_agent": last_agent})
        return {}

    def post_completion_comment(self, issue_number: str, repo: str, body: str) -> bool:
        self.posted_comments.append({"issue": issue_number, "repo": repo, "body": body})
        return self._post_comment_success

    def get_workflow_state(self, issue_number):
        return self._workflow_states.get(str(issue_number))

    def check_log_timeout(self, issue_number, log_file, timeout_seconds=None):
        return self._timeout_results.get(issue_number, (False, None))

    def get_agent_timeout_seconds(self, issue_number, agent_type=None):
        return self._agent_timeout_seconds

    def get_expected_running_agent(self, issue_number):
        return self._running_agents.get(str(issue_number))

    def is_issue_open(self, issue_number: str, repo: str) -> bool | None:
        return self._is_issue_open_fn(issue_number, repo)


class FuseCounterRuntime(StubRuntime):
    """Runtime stub that mutates retry-fuse counters during should_retry()."""

    def should_retry(self, issue_number, agent_type):
        issue_key = str(issue_number)
        entry = dict(self.tracker.get(issue_key, {}))
        fuse = dict(entry.get("retry_fuse", {}))
        fuse["agent"] = str(agent_type or "")
        fuse["attempts"] = int(fuse.get("attempts", 0) or 0) + 1
        entry["retry_fuse"] = fuse
        self.tracker[issue_key] = entry
        return super().should_retry(issue_number, agent_type)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_workflow(state: WorkflowState, active_agent: str | None = None) -> Workflow:
    """Build a minimal Workflow with the given state."""
    step = WorkflowStep(
        step_num=1,
        name="work",
        agent=Agent(
            name=active_agent or "dev",
            display_name="Dev",
            description="",
            timeout=60,
            max_retries=0,
        ),
        prompt_template="do {issue_url}",
        status=StepStatus.RUNNING if state == WorkflowState.RUNNING else StepStatus.COMPLETED,
    )
    wf = Workflow(
        id="wf-1",
        name="Test",
        version="1.0",
        steps=[step],
        state=state,
        current_step=1,
    )
    return wf


def _orchestrator(
    runtime: StubRuntime, complete_step_fn=None, stuck_threshold=60
) -> ProcessOrchestrator:
    if complete_step_fn is None:

        async def _noop(issue, agent, outputs, event_id=""):
            return None

        complete_step_fn = _noop
    runtime._agent_timeout_seconds = stuck_threshold
    return ProcessOrchestrator(
        runtime=runtime,
        complete_step_fn=complete_step_fn,
        nexus_dir=".nexus",
    )


# ---------------------------------------------------------------------------
# _is_terminal
# ---------------------------------------------------------------------------


class TestIsTerminal:
    def test_none_string(self):
        assert _is_terminal("none") is True

    def test_done_string(self):
        assert _is_terminal("done") is True

    def test_empty_string(self):
        assert _is_terminal("") is True

    def test_whitespace_only(self):
        assert _is_terminal("  ") is True

    def test_real_agent(self):
        assert _is_terminal("architect") is False

    def test_mixed_case(self):
        assert _is_terminal("NONE") is True


# ---------------------------------------------------------------------------
# scan_and_process_completions
# ---------------------------------------------------------------------------


class TestScanAndProcessCompletions:
    """Tests for the engine path, manual fallback, dedup, and auto-chain."""

    def _fake_summary(self, agent_type="developer", next_agent="reviewer", is_done=False):
        summary = MagicMock()
        summary.agent_type = agent_type
        summary.next_agent = next_agent
        summary.is_workflow_done = is_done
        summary.to_dict.return_value = {"agent_type": agent_type}
        return summary

    def _fake_detection(
        self,
        issue_num="42",
        dedup_key="key-42",
        file_path="/tmp/f",
        agent_type="developer",
        next_agent="reviewer",
        is_done=False,
    ):
        det = MagicMock()
        det.issue_number = issue_num
        det.dedup_key = dedup_key
        det.file_path = file_path
        det.summary = self._fake_summary(agent_type, next_agent, is_done)
        return det

    def test_engine_path_chains_next_agent(self):
        """When engine returns a running workflow the next agent should be launched."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.RUNNING)
        wf.steps[0].agent.name = "reviewer"  # active_agent_type → "reviewer"

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection()

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert len(runtime.posted_comments) == 1
        assert len(runtime.launched) == 1
        assert runtime.launched[0]["agent_type"] == "reviewer"

    def test_comment_post_failure_blocks_autochain(self):
        """Completion processing must halt when completion comment cannot be posted."""
        runtime = StubRuntime()
        runtime._post_comment_success = False

        async def complete(issue, agent, outputs, event_id=""):
            return None

        orc = _orchestrator(runtime, complete)
        dedup_seen = set()
        det = self._fake_detection(next_agent="architect")

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", dedup_seen)

        assert len(runtime.posted_comments) == 1
        assert runtime.launched == []
        assert any("comment delivery failed" in alert for alert in runtime.alerts)
        assert "key-42" not in dedup_seen

    def test_engine_path_completed_finalizes(self):
        """When engine workflow is COMPLETED, finalize is called, nothing is launched."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.COMPLETED)

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection()

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.launched == []
        assert len(runtime.finalized) == 1
        assert runtime.finalized[0]["issue"] == "42"

    def test_engine_terminal_self_loop_label_finalizes(self):
        """Decorated final-step labels should not relaunch the same completed agent."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.RUNNING)
        wf.steps[0].agent.name = "close_loop (summarizer)"

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(agent_type="summarizer", next_agent="", is_done=True)

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.launched == []
        assert len(runtime.finalized) == 1
        assert runtime.finalized[0]["issue"] == "42"

    def test_engine_terminal_self_loop_label_finalizes(self):
        """Engine labels like 'close_loop (summarizer)' should not relaunch summarizer."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.RUNNING)
        wf.steps[0].agent.name = "close_loop (summarizer)"

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(agent_type="summarizer", next_agent="", is_done=True)

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.launched == []
        assert len(runtime.finalized) == 1
        assert runtime.finalized[0]["issue"] == "42"

    def test_engine_path_failed_finalizes(self):
        """When engine workflow is FAILED, finalize is called."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.FAILED)

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection()

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.finalized[0]["issue"] == "42"
        assert runtime.launched == []

    def test_engine_no_active_agent_skips_chain(self):
        """If engine workflow is running but active_agent_type is None, skip."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.RUNNING)
        wf.steps[0].status = StepStatus.COMPLETED  # no RUNNING step → active_agent_type = None

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection()

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.launched == []

    def test_dedup_key_prevents_double_processing(self):
        """Same dedup key appearing twice must only trigger one launch."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.RUNNING)
        wf.steps[0].agent.name = "reviewer"

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det1 = self._fake_detection(dedup_key="same-key")
        det2 = self._fake_detection(dedup_key="same-key", issue_num="42")

        with patch(
            "nexus.core.process_orchestrator.scan_for_completions",
            return_value=[det1, det2],
        ):
            orc.scan_and_process_completions("/base", set())

        assert len(runtime.launched) == 1

    def test_manual_fallback_done_finalizes(self):
        """Manual path: when is_workflow_done=True, finalize is called."""
        runtime = StubRuntime()

        async def complete(issue, agent, outputs, event_id=""):
            return None  # no engine workflow

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(is_done=True)

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.finalized[0]["issue"] == "42"
        assert runtime.launched == []

    def test_manual_fallback_chains_next(self):
        """Manual path: non-terminal next_agent should trigger launch."""
        runtime = StubRuntime()

        async def complete(issue, agent, outputs, event_id=""):
            return None

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(next_agent="architect")

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.launched[0]["agent_type"] == "architect"

    def test_manual_fallback_terminal_next_agent_finalizes(self):
        """Manual path: 'none' as next_agent should finalize."""
        runtime = StubRuntime()

        async def complete(issue, agent, outputs, event_id=""):
            return None

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(next_agent="none")

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert runtime.finalized[0]["issue"] == "42"
        assert runtime.launched == []

    def test_engine_completion_mismatch_error_skips_autochain(self):
        """If complete_step rejects a mismatched completion, do not auto-chain."""
        runtime = StubRuntime()

        async def complete(issue, agent, outputs, event_id=""):
            raise ValueError("Completion agent mismatch for issue #42")

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(agent_type="developer", next_agent="debug")
        dedup_seen = set()

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", dedup_seen)

        assert len(runtime.posted_comments) == 0
        assert runtime.launched == []
        assert det.dedup_key in dedup_seen

    def test_closed_before_comment_blocks_post_and_chain(self):
        """If issue closes after completion step, skip comment and auto-chain."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.RUNNING)
        wf.steps[0].agent.name = "reviewer"

        checks = iter([True, False])
        runtime._is_issue_open_fn = lambda issue, repo: next(checks, False)

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        dedup_seen = set()
        det = self._fake_detection(dedup_key="k-closed-race")

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", dedup_seen)

        assert runtime.posted_comments == []
        assert runtime.launched == []
        assert "k-closed-race" in dedup_seen

    def test_stale_completion_replay_is_skipped(self):
        """Old completion files are skipped when replay guard threshold is enabled."""
        runtime = StubRuntime()

        async def complete(issue, agent, outputs, event_id=""):
            return None

        orc = _orchestrator(runtime, complete)
        dedup_seen = set()
        det = self._fake_detection(dedup_key="k-stale", file_path="/tmp/stale_completion.json")

        with (
            patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]),
            patch("os.path.getmtime", return_value=1000),
        ):
            orc.scan_and_process_completions(
                "/base",
                dedup_seen,
                stale_completion_seconds=300,
                stale_reference_ts=2000,
            )

        assert runtime.posted_comments == []
        assert runtime.launched == []
        assert "k-stale" in dedup_seen

    def test_failed_launch_sends_autochain_failed_alert(self):
        """When launch returns (None, None), an alert about the failure is sent."""
        runtime = StubRuntime()
        runtime.launched = []  # override: make launch fail

        def failing_launch(issue_number, agent_type, *, trigger_source="", exclude_tools=None):
            return (None, None)

        runtime.launch_agent = failing_launch  # type: ignore[method-assign]

        async def complete(issue, agent, outputs, event_id=""):
            return None

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(next_agent="architect")

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert any("Auto-chain failed" in a for a in runtime.alerts)

    def test_duplicate_suppressed_launch_does_not_send_failed_alert(self):
        """Intentional launch skip should not emit auto-chain failure alert."""
        runtime = StubRuntime()

        async def complete(issue, agent, outputs, event_id=""):
            return None

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(next_agent="architect")

        def duplicate_skip_launch(
            issue_number, agent_type, *, trigger_source="", exclude_tools=None
        ):
            return (None, "duplicate-suppressed")

        runtime.launch_agent = duplicate_skip_launch  # type: ignore[method-assign]

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set())

        assert not any("Auto-chain failed" in a for a in runtime.alerts)

    def test_resolve_project_called(self):
        """resolve_project callback is invoked with the detection file_path."""
        runtime = StubRuntime()
        wf = _make_workflow(WorkflowState.COMPLETED)

        async def complete(issue, agent, outputs, event_id=""):
            return wf

        orc = _orchestrator(runtime, complete)
        det = self._fake_detection(file_path="/tmp/myproject/file")
        seen_paths = []

        def resolve(path):
            seen_paths.append(path)
            return "myproject"

        with patch("nexus.core.process_orchestrator.scan_for_completions", return_value=[det]):
            orc.scan_and_process_completions("/base", set(), resolve_project=resolve)

        assert "/tmp/myproject/file" in seen_paths


# ---------------------------------------------------------------------------
# detect_dead_agents
# ---------------------------------------------------------------------------


class TestDetectDeadAgents:
    def _entry(self, pid=1234, agent_type="developer", tool="copilot", age_seconds=7200):
        return {
            "pid": pid,
            "timestamp": time.time() - age_seconds,
            "agent_type": agent_type,
            "tool": tool,
        }

    def test_dead_pid_sends_alert_and_retries(self):
        """A dead PID past the grace period should trigger an alert and retry."""
        runtime = StubRuntime(retry=True)
        runtime.tracker = {"55": self._entry(pid=8888, age_seconds=7200)}
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert any("Crashed" in a for a in runtime.alerts)
        assert any(e["issue"] == "55" for e in runtime.launched)

    def test_dead_pid_no_retry_sends_manual_alert(self):
        """When retries exhausted, send manual-intervention alert."""
        runtime = StubRuntime(retry=False)
        runtime.tracker = {"77": self._entry(pid=7777, age_seconds=7200)}
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert any("Manual Intervention" in a for a in runtime.alerts)
        assert runtime.launched == []

    def test_timeout_with_dead_tracked_pid_does_not_emit_orphan_alert(self, tmp_path):
        """Dead tracked PID should be handled by dead-agent detection, not orphan path."""
        runtime = StubRuntime(retry=True)
        runtime.tracker = {"49": self._entry(pid=4321, agent_type="developer", age_seconds=7200)}
        runtime._running_agents = {"49": "developer"}
        orc = _orchestrator(runtime, stuck_threshold=60)

        log_file = tmp_path / "copilot_49_20260221.log"
        log_file.write_text("stale log")

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc._handle_timeout("49", str(log_file), pid=None)

        assert not any("Orphaned Running Step" in a for a in runtime.alerts)

    def test_timeout_with_unknown_agent_skips_kill_and_notification(self, tmp_path):
        """Unknown agent identity must not trigger kill/notify timeout side effects."""
        runtime = StubRuntime(retry=True)
        runtime.tracker = {"60": self._entry(pid=6060, agent_type="unknown", age_seconds=7200)}
        orc = _orchestrator(runtime, stuck_threshold=60)

        log_file = tmp_path / "copilot_60_20260221.log"
        log_file.write_text("stale log")

        runtime.kill_process = MagicMock(return_value=True)
        runtime.notify_timeout = MagicMock()

        orc._handle_timeout("60", str(log_file), pid=None)

        runtime.kill_process.assert_not_called()
        runtime.notify_timeout.assert_not_called()
        assert any(event == "AGENT_TIMEOUT_SKIPPED_UNKNOWN" for _, event, _ in runtime.audit_events)
        assert "60" not in runtime.tracker

    def test_alive_pid_is_skipped(self):
        """Alive PIDs should not be alerted."""
        runtime = StubRuntime()
        runtime.tracker = {"10": self._entry(pid=5555, age_seconds=7200)}
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=True):
            orc.detect_dead_agents()

        assert runtime.alerts == []
        assert runtime.launched == []

    def test_grace_period_prevents_alert(self):
        """Agents launched within the threshold window are not yet marked dead."""
        runtime = StubRuntime()
        runtime.tracker = {"20": self._entry(pid=6666, age_seconds=10)}
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert runtime.alerts == []

    def test_repeated_dead_liveness_misses_trigger_retry_without_manual_cleanup(self):
        """After N failed liveness checks, stale PID entries are auto-retried."""
        runtime = StubRuntime(retry=True)
        runtime.tracker = {"21": self._entry(pid=2121, age_seconds=10)}
        orc = _orchestrator(runtime, stuck_threshold=3600)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()
            assert runtime.alerts == []
            assert runtime.launched == []

            orc.detect_dead_agents()
            assert runtime.alerts == []
            assert runtime.launched == []

            orc.detect_dead_agents()

        assert any("Crashed" in a for a in runtime.alerts)
        assert any(e["issue"] == "21" for e in runtime.launched)

    def test_stopped_workflow_skipped(self):
        """Agents in a STOPPED workflow must not be retried."""
        runtime = StubRuntime()
        runtime.tracker = {"30": self._entry(pid=3030, age_seconds=7200)}
        runtime._workflow_states["30"] = "STOPPED"
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert runtime.alerts == []

    def test_paused_workflow_skipped(self):
        """Agents in a PAUSED workflow must not be retried."""
        runtime = StubRuntime()
        runtime.tracker = {"31": self._entry(pid=3131, age_seconds=7200)}
        runtime._workflow_states["31"] = "PAUSED"
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert runtime.alerts == []

    def test_alert_failure_defers_state_mutation(self):
        """If send_alert returns False, state must NOT be mutated (retry next poll)."""
        runtime = StubRuntime(alert_success=False)
        runtime.tracker = {"40": self._entry(pid=4040, age_seconds=7200)}
        original_tracker = dict(runtime.tracker)
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        # Tracker unchanged because alert failed — we'll retry next poll.
        assert runtime.tracker == original_tracker

    def test_dedup_prevents_repeat_alerts(self):
        """Same (issue, pid) should only be alerted once per session."""
        runtime = StubRuntime()
        runtime.tracker = {"50": self._entry(pid=5050, age_seconds=7200)}
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()
            orc.detect_dead_agents()  # second call — tracker is empty now too

        assert len([a for a in runtime.alerts if "Crashed" in a]) == 1

    def test_no_pid_entry_is_skipped(self):
        """Tracker entries without a pid field must not raise."""
        runtime = StubRuntime()
        runtime.tracker = {"60": {"timestamp": time.time() - 7200, "agent_type": "dev"}}
        orc = _orchestrator(runtime, stuck_threshold=60)

        orc.detect_dead_agents()  # should not raise

        assert runtime.alerts == []

    def test_exclude_tools_passed_on_retry(self):
        """The crashed tool must be in exclude_tools on the retry launch."""
        runtime = StubRuntime(retry=True)
        runtime.tracker = {
            "70": self._entry(pid=7070, agent_type="developer", tool="gemini", age_seconds=7200)
        }
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert runtime.launched[0]["exclude_tools"] == ["gemini"]

    def test_dead_agent_retry_merges_existing_exclusions(self):
        """Retry must carry forward persisted exclusions, then append crashed tool."""
        runtime = StubRuntime(retry=True)
        runtime.tracker = {
            "74": {
                **self._entry(pid=7474, agent_type="developer", tool="gemini", age_seconds=7200),
                "exclude_tools": ["codex", "copilot"],
            }
        }
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert runtime.launched[0]["exclude_tools"] == ["codex", "copilot", "gemini"]

    def test_workflow_ineligible_dead_agent_skips_retry_and_cleans_tracker(self):
        """Dead-agent retries are skipped when workflow no longer expects that agent."""
        runtime = StubRuntime(retry=True)
        runtime._dead_retry = False
        runtime.tracker = {"71": self._entry(pid=7171, agent_type="triage", age_seconds=7200)}
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert runtime.launched == []
        assert "71" not in runtime.tracker
        assert any(event == "AGENT_DEAD_STALE" for _, event, _ in runtime.audit_events)

    def test_dead_agent_retry_preserves_retry_fuse_metadata(self):
        """Retry-fuse counters should survive tracker cleanup during dead-agent retries."""
        runtime = StubRuntime(retry=True)
        runtime.tracker = {
            "72": {
                **self._entry(pid=7272, agent_type="developer", tool="codex", age_seconds=7200),
                "retry_fuse": {
                    "agent": "developer",
                    "window_start": time.time() - 5,
                    "attempts": 2,
                    "tripped": False,
                    "alerted": False,
                    "hard_tripped": False,
                },
                "retry_fuse_trip_times": [time.time() - 5],
            }
        }
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert any(e["issue"] == "72" for e in runtime.launched)
        assert "72" in runtime.tracker
        assert runtime.tracker["72"].get("retry_fuse", {}).get("attempts") == 2

    def test_dead_agent_retry_keeps_latest_retry_fuse_counter(self):
        """detect_dead_agents must not overwrite should_retry-updated fuse counters."""
        runtime = FuseCounterRuntime(retry=True)
        runtime.tracker = {
            "73": {
                **self._entry(pid=7373, agent_type="developer", tool="codex", age_seconds=7200),
                "retry_fuse": {
                    "agent": "developer",
                    "window_start": time.time() - 5,
                    "attempts": 1,
                    "tripped": False,
                    "alerted": False,
                    "hard_tripped": False,
                },
            }
        }
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "is_pid_alive", return_value=False):
            orc.detect_dead_agents()

        assert runtime.tracker["73"]["retry_fuse"]["attempts"] == 2


# ---------------------------------------------------------------------------
# check_stuck_agents (Strategy-1)
# ---------------------------------------------------------------------------


class TestCheckStuckAgents:
    def test_timed_out_alive_pid_is_killed(self, tmp_path):
        """A log file older than the threshold with an alive PID should be killed."""
        # Create a fake log file with old mtime
        log_dir = tmp_path / ".nexus" / "tasks" / "job1" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "copilot_99_20250101_120000.log"
        log_file.write_text("agent output")
        # Make it appear old
        old_time = time.time() - 120  # 2 minutes ago, threshold=60s
        os.utime(log_file, (old_time, old_time))

        runtime = StubRuntime()
        runtime.tracker = {
            "99": {
                "pid": 9999,
                "timestamp": time.time() - 120,
                "agent_type": "developer",
                "tool": "copilot",
            }
        }

        killed_pids: list[int] = []

        def fake_kill(pid):
            killed_pids.append(pid)
            return True

        orc = _orchestrator(runtime, stuck_threshold=60)

        with (
            patch.object(runtime, "is_pid_alive", return_value=True),
            patch.object(runtime, "kill_process", side_effect=fake_kill),
            patch.object(runtime, "check_log_timeout", return_value=(True, 9999)),
        ):
            orc.check_stuck_agents(str(tmp_path))

        assert 9999 in killed_pids

    def test_not_timed_out_does_nothing(self, tmp_path):
        """Fresh logs must not trigger a kill."""
        log_dir = tmp_path / ".nexus" / "tasks" / "job1" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "copilot_88_20260101_120000.log"
        log_file.write_text("fresh output")
        # Recent mtime — not timed out

        runtime = StubRuntime()

        orc = _orchestrator(runtime, stuck_threshold=3600)

        with patch.object(runtime, "check_log_timeout", return_value=(False, None)):
            orc.check_stuck_agents(str(tmp_path))

        assert runtime.launched == []

    def test_orphaned_running_step_retries_expected_agent(self, tmp_path):
        """If RUNNING step has no live pid/tracker entry, orchestrator retries it."""
        log_dir = tmp_path / ".nexus" / "tasks" / "job1" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "copilot_44_20260101_120000.log"
        log_file.write_text("stale output")
        old_time = time.time() - 120
        os.utime(log_file, (old_time, old_time))

        runtime = StubRuntime(retry=True)
        runtime._running_agents["44"] = "debug"
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "check_log_timeout", return_value=(True, None)):
            orc.check_stuck_agents(str(tmp_path))

        assert any("Orphaned Running Step Detected" in a for a in runtime.alerts)
        assert any(
            launch["issue"] == "44"
            and launch["agent_type"] == "debug"
            and launch["trigger"] == "orphan-timeout-retry"
            for launch in runtime.launched
        )

    def test_orphaned_retry_merges_existing_exclusions(self, tmp_path):
        """Orphan retries must preserve existing exclusions from tracker state."""
        log_dir = tmp_path / ".nexus" / "tasks" / "job1" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "gemini_46_20260101_120000.log"
        log_file.write_text("stale output")
        old_time = time.time() - 120
        os.utime(log_file, (old_time, old_time))

        runtime = StubRuntime(retry=True)
        runtime.tracker = {
            "46": {
                "timestamp": time.time() - 120,
                "agent_type": "debug",
                "tool": "gemini",
                "exclude_tools": ["codex", "copilot"],
            }
        }
        runtime._running_agents["46"] = "debug"
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "check_log_timeout", return_value=(True, None)):
            orc.check_stuck_agents(str(tmp_path))

        assert any(
            launch["issue"] == "46"
            and launch["trigger"] == "orphan-timeout-retry"
            and launch["exclude_tools"] == ["codex", "copilot", "gemini"]
            for launch in runtime.launched
        )

    def test_orphaned_running_step_without_expected_agent_is_ignored(self, tmp_path):
        """No expected RUNNING agent means no orphan recovery action is taken."""
        log_dir = tmp_path / ".nexus" / "tasks" / "job1" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "copilot_45_20260101_120000.log"
        log_file.write_text("stale output")
        old_time = time.time() - 120
        os.utime(log_file, (old_time, old_time))

        runtime = StubRuntime(retry=True)
        orc = _orchestrator(runtime, stuck_threshold=60)

        with patch.object(runtime, "check_log_timeout", return_value=(True, None)):
            orc.check_stuck_agents(str(tmp_path))

        assert runtime.alerts == []
        assert runtime.launched == []
        assert not any(event == "STEP_TIMEOUT" for _, event, _ in runtime.audit_events)

    def test_timeout_retry_merges_existing_exclusions(self, tmp_path):
        """Timeout retries must preserve existing exclusions from tracker state."""
        log_dir = tmp_path / ".nexus" / "tasks" / "job1" / "logs"
        log_dir.mkdir(parents=True)
        log_file = log_dir / "gemini_47_20260101_120000.log"
        log_file.write_text("stale output")
        old_time = time.time() - 120
        os.utime(log_file, (old_time, old_time))

        runtime = StubRuntime(retry=True)
        runtime.tracker = {
            "47": {
                "pid": 4747,
                "timestamp": time.time() - 120,
                "agent_type": "developer",
                "tool": "gemini",
                "exclude_tools": ["codex", "copilot"],
            }
        }
        orc = _orchestrator(runtime, stuck_threshold=60)

        with (
            patch.object(runtime, "is_pid_alive", return_value=True),
            patch.object(runtime, "kill_process", return_value=True),
            patch.object(runtime, "check_log_timeout", return_value=(True, 4747)),
        ):
            orc.check_stuck_agents(str(tmp_path))

        assert any(
            launch["issue"] == "47"
            and launch["trigger"] == "timeout-retry"
            and launch["exclude_tools"] == ["codex", "copilot", "gemini"]
            for launch in runtime.launched
        )
