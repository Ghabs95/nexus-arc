"""Workflow command handlers extracted from telegram_bot."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus.core.config import NEXUS_CORE_STORAGE_DIR, NEXUS_STATE_DIR, NEXUS_STORAGE_BACKEND
from nexus.core.inbox.inbox_persistence_service import load_json_state_file
from nexus.core.inbox.inbox_persistence_service import save_json_state_file
from nexus.core.integrations.workflow_state_factory import get_workflow_state
from nexus.core.runtime.bridge import clear_launch_guard
from nexus.core.state_manager import HostStateManager
from nexus.core.utils.log_utils import log_unauthorized_access
from nexus.core.workflow_runtime.workflow_reprocess_continue_service import (
    handle_continue as _service_handle_continue,
    handle_reprocess as _service_handle_reprocess,
)

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext


@dataclass
class WorkflowHandlerDeps:
    logger: Any
    allowed_user_ids: list[int]
    base_dir: str
    default_repo: str
    project_config: dict[str, dict[str, Any]]
    workflow_state_plugin_kwargs: dict[str, Any]
    prompt_project_selection: Callable[[InteractiveContext, str], Awaitable[None]]
    ensure_project_issue: Callable[
        [InteractiveContext, str], Awaitable[tuple[str | None, str | None, list[str]]]
    ]
    find_task_file_by_issue: Callable[[str], str | None]
    project_repo: Callable[[str], str]
    get_issue_details: Callable[[str, str | None], dict[str, Any] | None]
    resolve_project_config_from_task: Callable[[str], tuple[str | None, dict[str, Any] | None]]
    invoke_ai_agent: Callable[..., tuple[int | None, str | None]]
    get_sop_tier_from_issue: Callable[[str, str | None], str | None]
    get_sop_tier: Callable[[str], tuple[str, Any, Any]]
    get_last_tier_for_issue: Callable[[str], str | None]
    prepare_continue_context: Callable[..., dict[str, Any]]
    kill_issue_agent: Callable[..., dict[str, Any]]
    get_runtime_ops_plugin: Callable[..., Any]
    get_workflow_state_plugin: Callable[..., Any]
    fetch_workflow_state_snapshot: Callable[..., Awaitable[dict[str, Any]]]
    scan_for_completions: Callable[[str], list[Any]]
    normalize_agent_reference: Callable[[str | None], str | None]
    get_expected_running_agent_from_workflow: Callable[[str], str | None]
    reconcile_issue_from_signals: Callable[..., Awaitable[dict[str, Any]]]
    get_direct_issue_plugin: Callable[[str], Any]
    extract_structured_completion_signals: Callable[[list[dict]], list[dict[str, str]]]
    write_local_completion_from_signal: Callable[[str, str, dict[str, str]], str]
    build_workflow_snapshot: Callable[..., dict[str, Any]]
    read_latest_local_completion: Callable[[str], dict[str, Any] | None]
    workflow_pause_handler: Callable[[InteractiveContext], Awaitable[None]]
    workflow_resume_handler: Callable[[InteractiveContext], Awaitable[None]]
    workflow_stop_handler: Callable[[InteractiveContext], Awaitable[None]]


async def reprocess_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    from nexus.adapters.git.utils import build_issue_url, resolve_repo

    await _service_handle_reprocess(
        ctx, deps, build_issue_url=build_issue_url, resolve_repo=resolve_repo
    )


async def continue_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    from nexus.core.task_flow.helpers import finalize_workflow as _finalize_workflow

    await _service_handle_continue(ctx, deps, finalize_workflow=_finalize_workflow)


async def kill_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Kill requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "kill")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "kill")
    if not project_key:
        return

    kill_result = deps.kill_issue_agent(
        issue_num=issue_num, get_runtime_ops_plugin=deps.get_runtime_ops_plugin
    )
    if kill_result["status"] == "not_running":
        await ctx.reply_text(kill_result["message"])
        return

    msg_id = await ctx.reply_text(
        f"🔪 Killing agent for issue #{issue_num} (PID: {kill_result.get('pid', 'n/a')})..."
    )

    if kill_result["status"] == "killed":
        text = f"✅ Agent killed (PID: {kill_result['pid']}).\n\nUse /reprocess {issue_num} to restart."
    elif kill_result["status"] == "stopped":
        text = f"✅ Agent stopped (PID: {kill_result['pid']}).\n\nUse /reprocess {issue_num} to restart."
    else:
        text = f"❌ Error: {kill_result.get('message', 'Unknown kill error')}"

    await ctx.edit_message_text(
        message_id=msg_id,
        text=text,
    )


async def reconcile_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Reconcile requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "reconcile")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "reconcile")
    if not project_key:
        return

    repo = deps.project_repo(project_key)

    msg_id = await ctx.reply_text(
        f"🔄 Reconciling issue #{issue_num} from structured Git comments..."
    )

    result = await deps.reconcile_issue_from_signals(
        issue_num=issue_num,
        project_key=project_key,
        repo=repo,
        get_issue_plugin=deps.get_direct_issue_plugin,
        extract_structured_completion_signals=deps.extract_structured_completion_signals,
        workflow_state_plugin_kwargs=deps.workflow_state_plugin_kwargs,
        write_local_completion_from_signal=deps.write_local_completion_from_signal,
    )

    if not result.get("ok"):
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"⚠️ {result.get('error', 'Reconcile failed.')}",
        )
        return

    await ctx.edit_message_text(
        message_id=msg_id,
        text=(
            f"✅ Reconcile completed for issue #{issue_num}\n\n"
            f"Signals scanned: {result['signals_scanned']}\n"
            f"Signals applied to workflow: {result['signals_applied']}\n"
            f"Local completion updated: `{result['completion_file']}`\n"
            f"Current workflow: `{result['workflow_state']}` | "
            f"Step {result['workflow_step']} | Agent `{result['workflow_agent']}`"
        ),
    )


async def wfstate_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Wfstate requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "wfstate")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "wfstate")
    if not project_key:
        return

    repo = deps.project_repo(project_key)

    msg_id = await ctx.reply_text(f"📊 Fetching workflow state for issue #{issue_num}...")

    state = await deps.fetch_workflow_state_snapshot(
        issue_num=issue_num,
        project_key=project_key,
        repo=repo,
        get_issue_plugin=deps.get_direct_issue_plugin,
        extract_structured_completion_signals=deps.extract_structured_completion_signals,
        workflow_state_plugin_kwargs=deps.workflow_state_plugin_kwargs,
        write_local_completion_from_signal=deps.write_local_completion_from_signal,
        build_workflow_snapshot=deps.build_workflow_snapshot,
        read_latest_local_completion=deps.read_latest_local_completion,
    )

    if not state.get("ok"):
        await ctx.edit_message_text(
            message_id=msg_id,
            text=f"⚠️ {state.get('error', 'Failed to fetch workflow state.')}",
        )
        return

    snapshot = state["snapshot"]

    def _as_dict(value: object) -> dict[str, object]:
        return value if isinstance(value, dict) else {}

    processor_signal = _as_dict(snapshot.get("processor_signal"))
    processor_type = processor_signal.get("type", "n/a")
    processor_severity = processor_signal.get("severity", "n/a")
    processor_at = processor_signal.get("timestamp", "n/a")
    processor_line = processor_signal.get("line", "n/a")

    recovery_hint = "none"
    if processor_type == "completion_mismatch":
        recovery_hint = "workflow/comment mismatch. Run /reconcile then /continue"
    elif processor_type in {"signal_drift", "retry_fuse", "pause_failed"}:
        recovery_hint = "workflow drift. Run /wfstate, then /reconcile and /continue"
    elif "workflow_state_missing" in (snapshot.get("drift_flags") or []):
        recovery_hint = (
            "workflow state rows missing in backend. Run /reconcile then /continue "
            "(or /resume if the workflow is paused)."
        )
    elif "workflow_vs_local" in (snapshot.get("drift_flags") or []):
        recovery_hint = (
            "workflow pointer lags completion signal. Run /continue to advance using completion next_agent "
            "(or /continue <project> <issue#> from:<agent> to force a specific step)."
        )
    elif "workflow_vs_comment" in (snapshot.get("drift_flags") or []):
        recovery_hint = "workflow differs from latest structured comment. Run /reconcile then /continue"
    elif "local_vs_comment" in (snapshot.get("drift_flags") or []):
        recovery_hint = "local completion disagrees with comment signal. Run /reconcile, then /wfstate"

    def _agent_label_with_canonical(display_value: str, canonical_value: str) -> str:
        display = str(display_value or "").strip()
        canonical = str(canonical_value or "").strip()
        if display and canonical:
            if display.lower() == canonical.lower():
                return display
            return f"{display} ({canonical})"
        return display or canonical or "N/A"

    current_agent_display = _agent_label_with_canonical(
        str(snapshot.get("current_agent", "")),
        str(snapshot.get("expected_running_agent", "")),
    )
    expected_agent_display = _agent_label_with_canonical(
        str(snapshot.get("expected_running_agent", "")),
        str(snapshot.get("expected_running_agent", "")),
    )
    completion_source = str(snapshot.get("completion_source", "filesystem")).strip().lower()
    completion_label_prefix = (
        "DB Completion"
        if completion_source in {"postgres", "db", "database"}
        else "Local Completion"
    )

    text = f"📊 Workflow Snapshot — Issue #{issue_num}\n\n"
    local_completion = _as_dict(snapshot.get("local"))
    latest_signal = _as_dict(snapshot.get("latest_signal"))

    summary = {
        "Repo": snapshot.get("repo", "N/A"),
        "Workflow ID": snapshot.get("workflow_id", "N/A"),
        "Workflow State": snapshot.get("workflow_state", "N/A"),
        "Current Step": f"{snapshot.get('current_step', 'N/A')} ({snapshot.get('current_step_name', 'N/A')})",
        "Current Agent": current_agent_display,
        "Expected RUNNING Agent": expected_agent_display,
        "Process": "running" if snapshot.get("running") else "stopped",
        "PID": snapshot.get("pid", "N/A"),
        "Task File": snapshot.get("task_file", "N/A"),
        "Workflow File": snapshot.get("workflow_file", "N/A"),
        f"{completion_label_prefix} (from)": snapshot.get("local_from", "N/A"),
        f"{completion_label_prefix} (next)": snapshot.get("local_next", "N/A"),
        f"{completion_label_prefix} (status)": local_completion.get("status", "N/A"),
        f"{completion_label_prefix} (updated)": local_completion.get("mtime", "N/A"),
        f"{completion_label_prefix} (file)": local_completion.get("path", "N/A"),
        "Latest Structured Comment (from)": snapshot.get("comment_from", "N/A"),
        "Latest Structured Comment (next)": snapshot.get("comment_next", "N/A"),
        "Latest Structured Comment (comment_id)": latest_signal.get("comment_id", "N/A"),
        "Latest Structured Comment (created)": latest_signal.get("created", "N/A"),
        "Latest Processor Signal (type)": processor_type,
        "Latest Processor Signal (severity)": processor_severity,
        "Latest Processor Signal (at)": processor_at,
        "Latest Processor Signal (detail)": processor_line,
        "Recovery Hint": recovery_hint,
        "Drift Flags": (
            ", ".join(f"`{flag}`" for flag in snapshot["drift_flags"])
            if snapshot.get("drift_flags")
            else "none"
        ),
    }

    for k, v in summary.items():
        text += f"- **{k}**: {v}\n"

    if snapshot.get("workflow_pointer_mismatch"):
        text += "\n⚠️ **Workflow Pointer Mismatch**:\n"
        text += f"- **indexed step**: {snapshot.get('indexed_step', 'N/A')} ({snapshot.get('indexed_step_name', 'N/A')}) / {snapshot.get('indexed_agent', 'N/A')}\n"
        text += f"- **running step**: {snapshot.get('running_step', 'N/A')} ({snapshot.get('running_step_name', 'N/A')}) / {snapshot.get('running_agent', 'N/A')}\n"

    await ctx.edit_message_text(
        message_id=msg_id,
        text=text,
    )


async def pause_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Pause requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "pause")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "pause")
    if not project_key:
        return

    ctx.args = [project_key, issue_num]
    await deps.workflow_pause_handler(ctx)


async def resume_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Resume requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "resume")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "resume")
    if not project_key:
        return

    ctx.args = [project_key, issue_num]
    await deps.workflow_resume_handler(ctx)


async def stop_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Stop requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "stop")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "stop")
    if not project_key:
        return

    ctx.args = [project_key, issue_num]
    await deps.workflow_stop_handler(ctx)


async def forget_handler(
    ctx: InteractiveContext,
    deps: WorkflowHandlerDeps,
) -> None:
    deps.logger.info(f"Forget requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "forget")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "forget")
    if not project_key:
        return

    if project_key not in deps.project_config:
        await ctx.reply_text("❌ Invalid project.")
        return

    workflow_id = get_workflow_state().get_workflow_id(str(issue_num))
    workflow_file_deleted = False
    if workflow_id:
        workflow_file = os.path.join(NEXUS_CORE_STORAGE_DIR, "workflows", f"{workflow_id}.json")
        if os.path.exists(workflow_file):
            try:
                os.remove(workflow_file)
                workflow_file_deleted = True
            except OSError as exc:
                deps.logger.warning(
                    "Failed to delete workflow file for issue #%s: %s",
                    issue_num,
                    exc,
                )

    runtime_ops = deps.get_runtime_ops_plugin(cache_key="runtime-ops:workflow")
    pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None
    killed = False
    if pid and runtime_ops:
        killed = bool(runtime_ops.kill_process(pid, force=True))

    launched = HostStateManager.load_launched_agents(recent_only=False)
    issue_key = str(issue_num)
    launched_keys_removed = [
        key
        for key, value in launched.items()
        if key == issue_key
        or key.startswith(f"{issue_key}_")
        or (isinstance(value, dict) and str(value.get("issue", "")) == issue_key)
    ]
    for key in launched_keys_removed:
        launched.pop(key, None)
    launched_removed = bool(launched_keys_removed)
    HostStateManager.save_launched_agents(launched)

    tracked = HostStateManager.load_tracked_issues()
    tracked_removed = tracked.pop(str(issue_num), None) is not None
    HostStateManager.save_tracked_issues(tracked)

    get_workflow_state().remove_mapping(str(issue_num))
    get_workflow_state().clear_pending_approval(str(issue_num))

    cleared_guards = clear_launch_guard(str(issue_num))

    try:
        completion_path = os.path.join(NEXUS_STATE_DIR, "completion_comments.json")
        completion_data = load_json_state_file(
            path=completion_path,
            logger=deps.logger,
            warn_only=True,
            storage_backend=NEXUS_STORAGE_BACKEND,
            state_key="completion_comments",
        )
        if isinstance(completion_data, dict):
            to_delete = [key for key in completion_data if key.startswith(f"{issue_num}:")]
            for key in to_delete:
                completion_data.pop(key, None)
            if to_delete:
                save_json_state_file(
                    path=completion_path,
                    data=completion_data,
                    logger=deps.logger,
                    warn_only=True,
                    storage_backend=NEXUS_STORAGE_BACKEND,
                    state_key="completion_comments",
                )
    except Exception as exc:
        deps.logger.debug("completion_comments cleanup skipped for issue #%s: %s", issue_num, exc)

    await ctx.reply_text(
        "🧹 **Issue state forgotten**\n\n"
        f"Issue: #{issue_num}\n"
        f"Project: {project_key}\n"
        f"Workflow mapping: cleared{' (file deleted)' if workflow_file_deleted else ''}\n"
        f"Tracker state: {'removed' if launched_removed else 'not present'}\n"
        f"Tracked issue: {'removed' if tracked_removed else 'not present'}\n"
        f"Running PID: {'killed' if killed else ('found but kill failed' if pid else 'none')}\n"
        f"Launch guards cleared: {cleared_guards}\n\n"
        "This issue will no longer auto-retry or emit orphan notifications unless relaunched manually."
    )
