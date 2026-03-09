"""Workflow operations service helpers used by Telegram command handlers."""

from __future__ import annotations

import json
import logging
import os
from collections import deque
from collections.abc import Callable
from inspect import isawaitable
from typing import Any

from nexus.core.config import INBOX_PROCESSOR_LOG_FILE, NEXUS_CORE_STORAGE_DIR
from nexus.core.integrations.workflow_state_factory import get_storage_backend
from nexus.core.integrations.workflow_state_factory import get_workflow_state as _get_wf_state
from nexus.core.orchestration.plugin_runtime import (
    get_runtime_ops_plugin,
    get_workflow_state_plugin,
)
from nexus.core.storage.capabilities import get_storage_capabilities

logger = logging.getLogger(__name__)


async def _resolve_maybe_await(value: Any) -> Any:
    if isawaitable(value):
        return await value
    return value


async def _latest_completion_from_storage(issue_num: str) -> dict[str, Any] | None:
    try:
        backend = get_storage_backend()
        items = await backend.list_completions(str(issue_num))
    except Exception as exc:
        logger.debug("Failed to read completion from storage for #%s: %s", issue_num, exc)
        return None
    if not items:
        return None
    payload = items[0]
    if not isinstance(payload, dict):
        return None
    return payload


async def _save_completion_to_storage(issue_num: str, signal: dict[str, str]) -> str | None:
    try:
        backend = get_storage_backend()
        payload = {
            "status": "complete",
            "agent_type": signal.get("completed_agent", ""),
            "next_agent": signal.get("next_agent", ""),
            "summary": f"Reconciled from Git comment {signal.get('comment_id', 'n/a')}",
            "source": "telegram-reconcile",
        }
        return await backend.save_completion(
            str(issue_num), str(signal.get("completed_agent", "unknown")), payload
        )
    except Exception as exc:
        logger.debug("Failed to save completion to storage for #%s: %s", issue_num, exc)
        return None


def _latest_processor_signal_for_issue(issue_num: str, max_lines: int = 3000) -> dict[str, str]:
    """Return latest inbox-processor signal line for an issue."""
    if not os.path.exists(INBOX_PROCESSOR_LOG_FILE):
        return {}

    tail_lines: deque[str] = deque(maxlen=max_lines)
    try:
        with open(INBOX_PROCESSOR_LOG_FILE, encoding="utf-8", errors="ignore") as handle:
            for line in handle:
                tail_lines.append(line.rstrip("\n"))
    except Exception:
        return {}

    target = f"issue #{issue_num}"
    priority_rules = [
        ("completion mismatch", "completion_mismatch", "blocker"),
        ("startup signal drift", "signal_drift", "warning"),
        ("retry fuse tripped", "retry_fuse", "warning"),
        ("failed to pause workflow", "pause_failed", "warning"),
        ("failed to post completion comment", "comment_post_failed", "warning"),
        ("workflow complete", "workflow_complete", "info"),
        ("auto-chained", "auto_chained", "info"),
        ("launching next agent", "launch_next_agent", "info"),
        ("agent completed", "agent_completed", "info"),
    ]

    fallback_line = ""
    for line in reversed(tail_lines):
        lowered = line.lower()
        if target not in lowered:
            continue
        if not fallback_line:
            fallback_line = line
        for needle, signal_type, severity in priority_rules:
            if needle in lowered:
                timestamp = line.split(" - ", 1)[0].strip() if " - " in line else ""
                return {
                    "type": signal_type,
                    "severity": severity,
                    "timestamp": timestamp,
                    "line": line,
                }

    if not fallback_line:
        return {}

    timestamp = fallback_line.split(" - ", 1)[0].strip() if " - " in fallback_line else ""
    return {
        "type": "generic",
        "severity": "info",
        "timestamp": timestamp,
        "line": fallback_line,
    }


async def reconcile_issue_from_signals(
    *,
    issue_num: str,
    project_key: str,
    repo: str,
    get_issue_plugin: Callable[[str], Any],
    extract_structured_completion_signals: Callable[[list[dict]], list[dict[str, str]]],
    workflow_state_plugin_kwargs: dict[str, Any],
    write_local_completion_from_signal: Callable[[str, str, dict[str, str]], str],
) -> dict[str, Any]:
    """Reconcile workflow + local completion using structured Git comments."""
    plugin = get_issue_plugin(repo)
    if not plugin:
        return {"ok": False, "error": f"Could not initialize Git platform plugin for {repo}."}

    data = plugin.get_issue(issue_num, ["comments", "title"])
    if not data:
        return {"ok": False, "error": f"Could not fetch issue #{issue_num} data."}

    signals = extract_structured_completion_signals(data.get("comments", []))
    if not signals:
        latest_completion = await _latest_completion_from_storage(str(issue_num))
        completed_agent = str((latest_completion or {}).get("agent_type") or "").strip().lower()
        next_agent = str((latest_completion or {}).get("next_agent") or "").strip().lower()
        if completed_agent and next_agent and next_agent not in {
            "none",
            "n/a",
            "null",
            "done",
            "end",
            "finish",
            "complete",
            "",
        }:
            signals = [
                {
                    "completed_agent": completed_agent,
                    "next_agent": next_agent,
                    "comment_id": "db-fallback",
                }
            ]
            logger.warning(
                "Reconcile issue #%s: no structured comments found; using DB completion fallback %s -> %s",
                issue_num,
                completed_agent,
                next_agent,
            )
        else:
            return {
                "ok": False,
                "error": f"No structured completion comments found for issue #{issue_num}.",
            }

    workflow_plugin = get_workflow_state_plugin(
        **workflow_state_plugin_kwargs,
        cache_key="workflow:state-engine",
    )

    status_before = await _resolve_maybe_await(workflow_plugin.get_workflow_status(issue_num))
    was_paused = bool(status_before and status_before.get("state") == "paused")
    if was_paused:
        await _resolve_maybe_await(workflow_plugin.resume_workflow(issue_num))

    applied: list[dict[str, str]] = []
    for signal in signals:
        outputs = {
            "status": "complete",
            "agent_type": signal["completed_agent"],
            "next_agent": signal["next_agent"],
            "summary": f"Reconciled from Git comment {signal.get('comment_id', 'n/a')}",
            "source": "telegram-reconcile",
        }
        try:
            result = await _resolve_maybe_await(
                workflow_plugin.complete_step_for_issue(
                    issue_number=issue_num,
                    completed_agent_type=signal["completed_agent"],
                    outputs=outputs,
                )
            )
            if result is not None:
                applied.append(signal)
        except Exception as exc:
            logger.debug(
                "Reconcile skipped signal for issue #%s (%s -> %s): %s",
                issue_num,
                signal["completed_agent"],
                signal["next_agent"],
                exc,
            )

    if was_paused:
        await _resolve_maybe_await(
            workflow_plugin.pause_workflow(issue_num, reason="Reconciled via Telegram")
        )

    completion_path: str | None = None
    completion_seeded = False
    selected_signal: dict[str, str] | None = None
    if applied:
        selected_signal = applied[-1]
    elif signals:
        # DB drift fallback: keep /continue resumable even when workflow rows are missing
        # but structured issue comments still provide latest handoff information.
        selected_signal = signals[-1]
        completion_seeded = True
        logger.warning(
            "Reconcile issue #%s: no workflow transitions applied; seeding completion from latest signal %s -> %s",
            issue_num,
            selected_signal.get("completed_agent"),
            selected_signal.get("next_agent"),
        )

    if selected_signal:
        if not get_storage_capabilities().local_completions:
            completion_path = await _resolve_maybe_await(
                _save_completion_to_storage(issue_num, selected_signal)
            )
        else:
            completion_path = write_local_completion_from_signal(
                project_key, issue_num, selected_signal
            )

    status_after = await _resolve_maybe_await(workflow_plugin.get_workflow_status(issue_num))
    if status_after:
        state_text = str(status_after.get("state", "unknown"))
        agent_text = str(status_after.get("current_agent", "unknown"))
        step_text = (
            f"{status_after.get('current_step', '?')}/{status_after.get('total_steps', '?')}"
        )
    else:
        state_text = "unknown"
        agent_text = "unknown"
        step_text = "?/?"

    return {
        "ok": True,
        "signals_scanned": len(signals),
        "signals_applied": len(applied),
        "completion_seeded": completion_seeded,
        "completion_file": os.path.basename(completion_path) if completion_path else "(unchanged)",
        "workflow_state": state_text,
        "workflow_step": step_text,
        "workflow_agent": agent_text,
    }


async def fetch_workflow_state_snapshot(
    *,
    issue_num: str,
    project_key: str,
    repo: str,
    get_issue_plugin: Callable[[str], Any],
    extract_structured_completion_signals: Callable[[list[dict]], list[dict[str, str]]],
    workflow_state_plugin_kwargs: dict[str, Any],
    write_local_completion_from_signal: Callable[[str, str, dict[str, str]], str],
    build_workflow_snapshot: Callable[..., dict[str, Any]],
    read_latest_local_completion: Callable[[str], dict[str, Any] | None],
) -> dict[str, Any]:
    """Reconcile and build workflow snapshot for /wfstate."""
    # 1. Reconcile from signals (guarantees local/remote state is synced before snapshot)
    await _resolve_maybe_await(
        reconcile_issue_from_signals(
            issue_num=issue_num,
            project_key=project_key,
            repo=repo,
            get_issue_plugin=get_issue_plugin,
            extract_structured_completion_signals=extract_structured_completion_signals,
            workflow_state_plugin_kwargs=workflow_state_plugin_kwargs,
            write_local_completion_from_signal=write_local_completion_from_signal,
        )
    )

    # 2. Get expected running agent reference + live workflow status
    from nexus.core.telegram.telegram_workflow_probe_service import (
        get_expected_running_agent_from_workflow,
    )

    expected_running = None
    workflow_status: dict[str, Any] | None = None
    plugin_kwargs = dict(workflow_state_plugin_kwargs or {})
    if plugin_kwargs:
        try:
            workflow_plugin = get_workflow_state_plugin(
                **plugin_kwargs,
                cache_key="workflow:state-engine:wfstate-probe",
            )
            workflow_status = await _resolve_maybe_await(
                workflow_plugin.get_workflow_status(issue_num)
            )
            expected_running = get_expected_running_agent_from_workflow(
                issue_num=issue_num,
                get_workflow_id=lambda n: _get_wf_state().get_workflow_id(n),
                workflow_plugin=workflow_plugin,
            )
        except Exception as exc:
            logger.debug(
                "Could not resolve expected running agent from workflow engine for #%s: %s",
                issue_num,
                exc,
            )

    if not expected_running:
        try:
            from nexus.core.runtime.nexus_agent_runtime import (
                get_expected_running_agent_from_workflow as legacy_probe,
            )

            expected_running = await _resolve_maybe_await(legacy_probe(issue_num))
        except Exception:
            expected_running = None

    # 3. Build snapshot
    caps = get_storage_capabilities()
    if caps.local_task_files:
        from nexus.core.runtime.bridge import find_task_file_by_issue

        find_task_file = find_task_file_by_issue
    else:
        find_task_file = lambda _issue_num: None

    if caps.local_completions:
        read_local_completion = read_latest_local_completion
        completion_source = "filesystem"
    else:
        db_completion = await _resolve_maybe_await(_latest_completion_from_storage(issue_num))
        read_local_completion = lambda _issue_num: db_completion
        completion_source = "postgres"

    snapshot = build_workflow_snapshot(
        issue_num=issue_num,
        repo=repo,
        get_issue_plugin=get_issue_plugin,
        workflow_status=workflow_status,
        expected_running_agent=expected_running or "",
        find_task_file_by_issue=find_task_file,
        read_latest_local_completion=read_local_completion,
        extract_structured_completion_signals=extract_structured_completion_signals,
        local_task_files_enabled=caps.local_task_files,
        local_workflow_files_enabled=caps.local_workflow_files,
    )
    snapshot["completion_source"] = completion_source

    return {"ok": True, "snapshot": snapshot}


def build_workflow_snapshot(
    *,
    issue_num: str,
    repo: str,
    get_issue_plugin: Callable[[str], Any],
    workflow_status: dict[str, Any] | None = None,
    expected_running_agent: str,
    find_task_file_by_issue: Callable[[str], str | None],
    read_latest_local_completion: Callable[[str], dict[str, Any] | None],
    extract_structured_completion_signals: Callable[[list[dict]], list[dict[str, str]]],
    local_task_files_enabled: bool = True,
    local_workflow_files_enabled: bool = True,
) -> dict[str, Any]:
    """Build workflow/process/local/comment snapshot used by /wfstate."""
    workflow_id = _get_wf_state().get_workflow_id(issue_num)
    workflow_file = (
        os.path.join(NEXUS_CORE_STORAGE_DIR, "workflows", f"{workflow_id}.json")
        if workflow_id and local_workflow_files_enabled
        else None
    )

    workflow_state = "unknown"
    current_step = "?/?"
    current_step_name = "unknown"
    current_agent = "unknown"
    indexed_step = "?/?"
    indexed_step_name = ""
    indexed_agent = ""
    running_step = "?/?"
    running_step_name = "unknown"
    running_agent = ""

    if isinstance(workflow_status, dict):
        workflow_state = str(workflow_status.get("state") or workflow_state)
        status_agent = str(workflow_status.get("current_agent") or "").strip()
        if status_agent:
            current_agent = status_agent
        raw_step = workflow_status.get("current_step")
        raw_total = workflow_status.get("total_steps")
        try:
            step_int = int(raw_step)
        except (TypeError, ValueError):
            step_int = None
        try:
            total_int = int(raw_total)
        except (TypeError, ValueError):
            total_int = None
        if step_int is not None and total_int is not None and total_int > 0:
            current_step = f"{step_int}/{total_int}"
        elif step_int is not None:
            current_step = f"{step_int}/?"

    if workflow_file and os.path.exists(workflow_file):
        try:
            with open(workflow_file, encoding="utf-8") as handle:
                payload = json.load(handle)
            workflow_state = str(payload.get("state", "unknown"))
            steps = payload.get("steps", [])
            raw_current_step = int(payload.get("current_step", 0) or 0)
            total_steps = len(steps)

            indexed_idx: int | None = None
            if total_steps > 0:
                for idx, step in enumerate(steps):
                    if not isinstance(step, dict):
                        continue
                    try:
                        if int(step.get("step_num", 0) or 0) == raw_current_step:
                            indexed_idx = idx
                            break
                    except (TypeError, ValueError):
                        continue

                if indexed_idx is None and 0 <= raw_current_step < total_steps:
                    indexed_idx = raw_current_step
                elif indexed_idx is None and 1 <= raw_current_step <= total_steps:
                    indexed_idx = raw_current_step - 1

            if indexed_idx is not None and 0 <= indexed_idx < total_steps:
                step = steps[indexed_idx]
                indexed_step = f"{indexed_idx + 1}/{total_steps}"
                indexed_step_name = str(step.get("name", "unknown"))
                agent = step.get("agent") if isinstance(step, dict) else None
                if isinstance(agent, dict):
                    indexed_agent = str(
                        agent.get("name") or agent.get("display_name") or ""
                    ).strip()

            for idx, step in enumerate(steps):
                if not isinstance(step, dict):
                    continue
                if str(step.get("status", "")).strip().lower() != "running":
                    continue
                running_step = f"{idx + 1}/{total_steps}"
                running_step_name = str(step.get("name", "unknown"))
                agent = step.get("agent") if isinstance(step, dict) else None
                if isinstance(agent, dict):
                    running_agent = str(
                        agent.get("name") or agent.get("display_name") or ""
                    ).strip()
                break

            if running_agent:
                current_step = running_step
                current_step_name = running_step_name
                current_agent = running_agent
            elif indexed_step != "?/?" or indexed_agent:
                current_step = indexed_step
                current_step_name = indexed_step_name
                current_agent = indexed_agent or current_agent
        except Exception as exc:
            logger.warning("wfstate failed reading workflow file for #%s: %s", issue_num, exc)

    runtime_ops = get_runtime_ops_plugin(cache_key="runtime-ops:telegram")
    pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None
    running = runtime_ops.is_issue_process_running(issue_num) if runtime_ops else False

    local = read_latest_local_completion(issue_num)
    local_next = (local or {}).get("next_agent", "")
    local_from = (local or {}).get("agent_type", "")

    plugin = get_issue_plugin(repo)
    issue_data = plugin.get_issue(issue_num, ["comments", "updatedAt"]) if plugin else {}
    comments = issue_data.get("comments", []) if isinstance(issue_data, dict) else []
    signals = extract_structured_completion_signals(comments)
    latest_signal = signals[-1] if signals else None
    comment_next = (latest_signal or {}).get("next_agent", "")
    comment_from = (latest_signal or {}).get("completed_agent", "")

    drift_flags: list[str] = []
    effective_expected_running = (
        (running_agent or "").strip().lower()
        or str(expected_running_agent or "").strip().lower()
        or str(current_agent or "").strip().lower()
    )

    if (
        effective_expected_running
        and local_next
        and effective_expected_running != local_next.lower()
    ):
        drift_flags.append("workflow_vs_local")
    if (
        effective_expected_running
        and comment_next
        and effective_expected_running != comment_next.lower()
    ):
        drift_flags.append("workflow_vs_comment")
    if local_next and comment_next and local_next.lower() != comment_next.lower():
        drift_flags.append("local_vs_comment")
    if workflow_state.lower() in {"unknown", ""} and (
        effective_expected_running or local_next or comment_next
    ):
        drift_flags.append("workflow_state_missing")

    processor_signal = _latest_processor_signal_for_issue(issue_num)

    return {
        "repo": repo,
        "workflow_id": workflow_id,
        "workflow_state": workflow_state,
        "current_step": current_step,
        "current_step_name": current_step_name,
        "current_agent": current_agent,
        "expected_running_agent": effective_expected_running,
        "indexed_step": indexed_step,
        "indexed_step_name": indexed_step_name or "unknown",
        "indexed_agent": indexed_agent or "unknown",
        "running_step": running_step,
        "running_step_name": running_step_name,
        "running_agent": running_agent,
        "workflow_pointer_mismatch": bool(
            running_agent
            and indexed_agent
            and indexed_agent.lower() != "unknown"
            and running_agent.lower() != indexed_agent.lower()
        ),
        "running": running,
        "pid": pid,
        "task_file": (find_task_file_by_issue(issue_num) if local_task_files_enabled else None),
        "workflow_file": workflow_file,
        "local": local,
        "local_from": local_from,
        "local_next": local_next,
        "latest_signal": latest_signal,
        "comment_from": comment_from,
        "comment_next": comment_next,
        "drift_flags": drift_flags,
        "processor_signal": processor_signal,
    }
