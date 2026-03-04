"""Monitoring/log command handlers extracted from telegram_bot."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus.core.monitoring.monitoring_logs_service import (
    handle_logs as _service_handle_logs,
    handle_logsfull as _service_handle_logsfull,
    handle_tail as _service_handle_tail,
)
from nexus.core.monitoring.monitoring_status_active_service import (
    handle_active as _service_handle_active,
    handle_status as _service_handle_status,
)
from nexus.core.utils.log_utils import log_unauthorized_access

from nexus.adapters.notifications.base import Button

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext


@dataclass
class MonitoringHandlersDeps:
    logger: Any
    allowed_user_ids: list[int]
    base_dir: str
    project_config: dict[str, dict[str, Any]]
    types_map: dict[str, str]
    # Removed prompt_monitor_project_selection and prompt_project_selection
    ensure_project: Callable[[InteractiveContext, str], Awaitable[str | None]]
    ensure_project_issue: Callable[
        [InteractiveContext, str], Awaitable[tuple[str | None, str | None, list[str]]]
    ]
    normalize_project_key: Callable[[str], str | None]
    iter_project_keys: Callable[[], list[str]]
    get_project_label: Callable[[str], str]
    get_project_root: Callable[[str], str | None]
    get_project_logs_dir: Callable[[str], str | None]
    get_inbox_storage_backend: Callable[[], str]
    get_inbox_queue_overview: Callable[[int], dict[str, Any]]
    project_repo: Callable[[str], str]
    get_issue_details: Callable[[str, str | None], dict[str, Any] | None]
    get_inbox_dir: Callable[[str, str], str]
    get_tasks_active_dir: Callable[[str, str], str]
    get_tasks_closed_dir: Callable[[str, str], str]
    extract_issue_number_from_file: Callable[[str], str | None]
    build_issue_url: Callable[[str, str, dict[str, Any] | None], str]
    find_task_file_by_issue: Callable[[str], str | None]
    find_issue_log_files: Callable[..., list[str]]
    read_latest_log_tail: Callable[..., list[str]]
    search_logs_for_issue: Callable[[str], list[str]]
    read_latest_log_full: Callable[[str | None], list[str]]
    read_log_matches: Callable[..., list[str]]
    active_tail_sessions: dict[tuple[int, int], str]
    active_tail_tasks: dict[tuple[int, int], asyncio.Task]
    get_retry_fuse_status: Callable[[str], dict[str, Any]]
    normalize_agent_reference: Callable[[str | None], str | None]
    get_expected_running_agent_from_workflow: Callable[[str], str | None]
    get_direct_issue_plugin: Callable[[str], Any]
    extract_structured_completion_signals: Callable[[list[dict]], list[dict[str, str]]]
    read_latest_local_completion: Callable[[str], dict[str, Any] | None]
    build_workflow_snapshot: Callable[..., dict[str, Any]]


async def status_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    await _service_handle_status(ctx, deps)


async def active_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    await _service_handle_active(ctx, deps)


async def logs_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    await _service_handle_logs(ctx, deps)


async def logsfull_handler(
    ctx: InteractiveContext,
    deps: MonitoringHandlersDeps,
) -> None:
    await _service_handle_logsfull(ctx, deps)


async def tail_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    await _service_handle_tail(ctx, deps)


async def tailstop_handler(
    ctx: InteractiveContext,
    deps: MonitoringHandlersDeps,
) -> None:
    deps.logger.info(f"Tailstop requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    session_key = (ctx.chat_id, int(ctx.user_id))
    active_task = deps.active_tail_tasks.get(session_key)
    if session_key in deps.active_tail_sessions or (active_task and not active_task.done()):
        deps.active_tail_sessions.pop(session_key, None)
        if active_task and not active_task.done():
            active_task.cancel()
        deps.active_tail_tasks.pop(session_key, None)
        await ctx.reply_text("⏹️ Stopped live tail session.")
    else:
        await ctx.reply_text("ℹ️ No active live tail session to stop.")


async def fuse_handler(ctx: InteractiveContext, deps: MonitoringHandlersDeps) -> None:
    deps.logger.info(f"Fuse status requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        # Replaced prompt_project_selection
        buttons = [
            [Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:fuse:{pk}")]
            for pk in deps.iter_project_keys()
        ]
        buttons.append([Button(label="❌ Close", callback_data="flow:close")])
        await ctx.reply_text("Please select a project to view fuse status:", buttons=buttons)
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "fuse")
    if not project_key:
        return

    status = deps.get_retry_fuse_status(issue_num)
    if not status.get("exists"):
        await ctx.reply_text(f"🧯 Retry fuse: no state recorded for issue #{issue_num}.")
        return

    attempts = int(status.get("attempts") or 0)
    max_attempts = int(status.get("max_attempts") or 0)
    remaining = max(0, max_attempts - attempts)

    window_remaining = status.get("window_remaining_seconds")
    if isinstance(window_remaining, (int, float)):
        window_remaining_text = f"{int(window_remaining)}s"
    else:
        window_remaining_text = "n/a"

    trip_count = int(status.get("trip_count_in_hard_window") or 0)
    trip_threshold = int(status.get("hard_trip_threshold") or 0)
    hard_window_seconds = int(status.get("hard_window_seconds") or 0)
    hard_window_minutes = hard_window_seconds // 60

    state_flags = []
    if status.get("hard_tripped"):
        state_flags.append("HARD-TRIPPED")
    elif status.get("tripped"):
        state_flags.append("TRIPPED")
    else:
        state_flags.append("ACTIVE")
    if status.get("alerted"):
        state_flags.append("ALERTED")

    agent = status.get("agent") or "unknown"
    text = (
        f"🧯 **Retry Fuse Status**\n\n"
        f"Project: {project_key}\n"
        f"Issue: #{issue_num}\n"
        f"Agent: {agent}\n"
        f"State: {', '.join(state_flags)}\n"
        f"Attempts in window: {attempts}/{max_attempts}\n"
        f"Remaining before trip: {remaining}\n"
        f"Window remaining: {window_remaining_text}\n"
        f"Fuse trips in last {hard_window_minutes}m: {trip_count}/{trip_threshold}"
    )
    await ctx.reply_text(text)
