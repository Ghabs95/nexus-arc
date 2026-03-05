"""Workflow recovery loop actions extracted from inbox_processor."""

from collections.abc import Callable
from typing import Any


def run_stuck_agents_cycle(
    *,
    logger,
    base_dir: str,
    scope: str,
    orchestrator_check_stuck_agents: Callable[[str], None],
    recover_orphaned_running_agents: Callable[[], int],
    recover_unmapped_issues_from_completions: Callable[[], int],
    clear_polling_failures: Callable[[str], None],
    record_polling_failure: Callable[[str, Exception], None],
) -> None:
    """Run one periodic stuck-agent + recovery cycle with shared failure bookkeeping."""
    try:
        orchestrator_check_stuck_agents(base_dir)
        recovered = recover_orphaned_running_agents()
        if recovered:
            logger.info("Recovered %s orphaned workflow issue(s)", recovered)
        recovered_from_signals = recover_unmapped_issues_from_completions()
        if recovered_from_signals:
            logger.info(
                "Recovered %s issue(s) from completion signals",
                recovered_from_signals,
            )
        clear_polling_failures(scope)
    except Exception as exc:
        logger.error("Error in check_stuck_agents: %s", exc)
        record_polling_failure(scope, exc)


def recover_orphaned_running_agents(
    *,
    max_relaunches: int,
    logger,
    orchestrator,
    runtime: Any,
    load_all_mappings: Callable[[], dict],
    load_launched_agents: Callable[..., dict],
    orphan_recovery_last_attempt: dict[str, float],
    orphan_recovery_cooldown_seconds: float,
    resolve_project_for_issue: Callable[..., str | None],
    resolve_repo_for_issue: Callable[..., str],
    reconcile_closed_or_missing_issue: Callable[[str, str, str], None] | None = None,
) -> int:
    """Relaunch missing processes for workflows still marked RUNNING."""
    if runtime is None:
        return 0

    try:
        mappings = load_all_mappings()
    except Exception as exc:
        logger.debug("Orphan recovery skipped (mapping load failed): %s", exc)
        return 0

    if not isinstance(mappings, dict) or not mappings:
        return 0

    launched = load_launched_agents(recent_only=False)
    if not isinstance(launched, dict):
        launched = {}

    now = __import__("time").time()
    recovered = 0

    issue_keys = [str(key) for key in mappings.keys()]
    issue_keys.sort(key=lambda value: int(value) if value.isdigit() else value)

    for issue_num in issue_keys:
        if recovered >= max_relaunches:
            break

        last_attempt = orphan_recovery_last_attempt.get(issue_num, 0.0)
        if (now - last_attempt) < orphan_recovery_cooldown_seconds:
            continue

        workflow_state = runtime.get_workflow_state(issue_num)
        if workflow_state in {"PAUSED", "STOPPED", "COMPLETED", "FAILED", "CANCELLED"}:
            orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        expected_agent = runtime.get_expected_running_agent(issue_num)
        if not expected_agent:
            continue

        if runtime.is_process_running(issue_num):
            orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        tracker_entry = launched.get(issue_num, {})
        if not isinstance(tracker_entry, dict):
            tracker_entry = {}
        tracker_pid = tracker_entry.get("pid")
        if isinstance(tracker_pid, int) and tracker_pid > 0 and runtime.is_pid_alive(tracker_pid):
            continue

        workflow_id = str(mappings.get(issue_num, "") or "")
        project_name = resolve_project_for_issue(issue_num, workflow_id=workflow_id)
        if not project_name:
            logger.info(
                "Skipping orphan recovery for issue #%s: unable to resolve project (workflow_id=%s)",
                issue_num,
                workflow_id or "unknown",
            )
            orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        repo_name = resolve_repo_for_issue(issue_num, default_project=project_name)
        issue_open = runtime.is_issue_open(issue_num, repo_name)
        if issue_open is not True:
            status_label = "unknown" if issue_open is None else "closed/missing"
            logger.info(
                "Skipping orphan recovery for issue #%s: remote issue not confirmed open in %s (status=%s)",
                issue_num,
                repo_name,
                status_label,
            )
            if issue_open is False and callable(reconcile_closed_or_missing_issue):
                try:
                    reconcile_closed_or_missing_issue(issue_num, repo_name, workflow_id)
                except Exception as exc:
                    logger.warning(
                        "Failed closed/missing issue reconciliation for issue #%s in %s: %s",
                        issue_num,
                        repo_name,
                        exc,
                    )
            orphan_recovery_last_attempt.pop(issue_num, None)
            continue

        if not runtime.should_retry_dead_agent(issue_num, expected_agent):
            continue

        # Enforce retry-fuse limits for orphan recovery relaunches.
        if hasattr(runtime, "should_retry") and not runtime.should_retry(issue_num, expected_agent):
            logger.info(
                "Skipping orphan recovery for issue #%s: retry guard rejected relaunch for %s",
                issue_num,
                expected_agent,
            )
            continue

        orphan_recovery_last_attempt[issue_num] = now
        launch_kwargs = {
            "trigger_source": "orphan-recovery",
            "repo_override": repo_name,
        }
        try:
            pid, tool = runtime.launch_agent(
                issue_num,
                expected_agent,
                **launch_kwargs,
            )
        except TypeError:
            # Backward-compatible fallback for runtimes that don't yet accept repo_override.
            launch_kwargs.pop("repo_override", None)
            pid, tool = runtime.launch_agent(
                issue_num,
                expected_agent,
                **launch_kwargs,
            )
        if pid:
            recovered += 1
            logger.warning(
                "Recovered orphaned workflow issue #%s by launching %s (PID %s, tool=%s)",
                issue_num,
                expected_agent,
                pid,
                tool,
            )
        else:
            logger.info(
                "Orphan recovery launch skipped/failed for issue #%s (agent=%s, reason=%s)",
                issue_num,
                expected_agent,
                tool,
            )

    return recovered
