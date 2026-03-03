from unittest.mock import MagicMock

from services.workflow.workflow_recovery_service import (
    recover_orphaned_running_agents,
    run_stuck_agents_cycle,
)


def test_run_stuck_agents_cycle_success_logs_and_clears():
    logger = MagicMock()
    calls = {"orchestrator": 0, "orphans": 0, "signals": 0, "clear": 0, "record": 0}

    run_stuck_agents_cycle(
        logger=logger,
        base_dir="/tmp/base",
        scope="stuck-agents:loop",
        orchestrator_check_stuck_agents=lambda _base: calls.__setitem__(
            "orchestrator", calls["orchestrator"] + 1
        ),
        recover_orphaned_running_agents=lambda: calls.__setitem__("orphans", calls["orphans"] + 1)
        or 2,
        recover_unmapped_issues_from_completions=lambda: calls.__setitem__(
            "signals", calls["signals"] + 1
        )
        or 1,
        clear_polling_failures=lambda _scope: calls.__setitem__("clear", calls["clear"] + 1),
        record_polling_failure=lambda _scope, _exc: calls.__setitem__(
            "record", calls["record"] + 1
        ),
    )

    assert calls == {"orchestrator": 1, "orphans": 1, "signals": 1, "clear": 1, "record": 0}
    assert logger.info.call_count >= 2


def test_run_stuck_agents_cycle_records_failure():
    logger = MagicMock()
    recorded = []

    def _boom(_base):
        raise RuntimeError("boom")

    run_stuck_agents_cycle(
        logger=logger,
        base_dir="/tmp/base",
        scope="stuck-agents:loop",
        orchestrator_check_stuck_agents=_boom,
        recover_orphaned_running_agents=lambda: 0,
        recover_unmapped_issues_from_completions=lambda: 0,
        clear_polling_failures=lambda _scope: None,
        record_polling_failure=lambda scope, exc: recorded.append((scope, str(exc))),
    )

    assert recorded == [("stuck-agents:loop", "boom")]
    logger.error.assert_called_once()


def test_recover_orphaned_running_agents_passes_repo_override():
    logger = MagicMock()
    runtime = MagicMock()
    runtime.get_workflow_state.return_value = None
    runtime.get_expected_running_agent.return_value = "developer"
    runtime.is_process_running.return_value = False
    runtime.is_pid_alive.return_value = False
    runtime.is_issue_open.return_value = True
    runtime.should_retry_dead_agent.return_value = True
    runtime.launch_agent.return_value = (4321, "codex")

    recovered = recover_orphaned_running_agents(
        max_relaunches=3,
        logger=logger,
        orchestrator=MagicMock(),
        runtime=runtime,
        load_all_mappings=lambda: {"88": "nexus-88-full"},
        load_launched_agents=lambda **_kwargs: {},
        orphan_recovery_last_attempt={},
        orphan_recovery_cooldown_seconds=0,
        resolve_project_for_issue=lambda issue_num, workflow_id=None: "nexus",
        resolve_repo_for_issue=lambda issue_num, default_project=None: "Ghabs95/nexus-arc",
        reconcile_closed_or_missing_issue=None,
    )

    assert recovered == 1
    runtime.launch_agent.assert_called_once_with(
        "88",
        "developer",
        trigger_source="orphan-recovery",
        repo_override="Ghabs95/nexus-arc",
    )


def test_recover_orphaned_running_agents_falls_back_when_runtime_does_not_accept_repo_override():
    logger = MagicMock()

    class LegacyRuntime:
        def __init__(self):
            self.calls = []

        def get_workflow_state(self, _issue_num):
            return None

        def get_expected_running_agent(self, _issue_num):
            return "developer"

        def is_process_running(self, _issue_num):
            return False

        def is_pid_alive(self, _pid):
            return False

        def is_issue_open(self, _issue_num, _repo_name):
            return True

        def should_retry_dead_agent(self, _issue_num, _expected_agent):
            return True

        def launch_agent(self, issue_num, expected_agent, *, trigger_source="orchestrator"):
            self.calls.append((issue_num, expected_agent, trigger_source))
            return (9876, "codex")

    runtime = LegacyRuntime()
    recovered = recover_orphaned_running_agents(
        max_relaunches=3,
        logger=logger,
        orchestrator=MagicMock(),
        runtime=runtime,
        load_all_mappings=lambda: {"88": "nexus-88-full"},
        load_launched_agents=lambda **_kwargs: {},
        orphan_recovery_last_attempt={},
        orphan_recovery_cooldown_seconds=0,
        resolve_project_for_issue=lambda issue_num, workflow_id=None: "nexus",
        resolve_repo_for_issue=lambda issue_num, default_project=None: "Ghabs95/nexus-arc",
        reconcile_closed_or_missing_issue=None,
    )

    assert recovered == 1
    assert runtime.calls == [("88", "developer", "orphan-recovery")]


def test_recover_orphaned_running_agents_honors_retry_guard():
    logger = MagicMock()
    runtime = MagicMock()
    runtime.get_workflow_state.return_value = None
    runtime.get_expected_running_agent.return_value = "developer"
    runtime.is_process_running.return_value = False
    runtime.is_pid_alive.return_value = False
    runtime.is_issue_open.return_value = True
    runtime.should_retry_dead_agent.return_value = True
    runtime.should_retry.return_value = False

    recovered = recover_orphaned_running_agents(
        max_relaunches=3,
        logger=logger,
        orchestrator=MagicMock(),
        runtime=runtime,
        load_all_mappings=lambda: {"88": "nexus-88-full"},
        load_launched_agents=lambda **_kwargs: {},
        orphan_recovery_last_attempt={},
        orphan_recovery_cooldown_seconds=0,
        resolve_project_for_issue=lambda issue_num, workflow_id=None: "nexus",
        resolve_repo_for_issue=lambda issue_num, default_project=None: "Ghabs95/nexus-arc",
        reconcile_closed_or_missing_issue=None,
    )

    assert recovered == 0
    runtime.launch_agent.assert_not_called()
