from __future__ import annotations

import asyncio
import os
import re
import time
from typing import Any

from nexus.adapters.notifications.base import Button
from nexus.core.utils.log_utils import log_unauthorized_access


def _resolve_project_only_arg(ctx: Any, deps: Any) -> str | None:
    args = list(getattr(ctx, "args", []) or [])
    if len(args) != 1:
        return None
    raw = str(args[0] or "").strip().lower()
    if not raw or raw.lstrip("#").isdigit():
        return None
    normalized = deps.normalize_project_key(raw)
    if normalized and normalized in deps.iter_project_keys():
        return normalized
    return None


def _read_latest_project_log(
    project_key: str, deps: Any, *, max_lines: int
) -> tuple[str | None, list[str]]:
    logs_dir = deps.get_project_logs_dir(project_key)
    if not logs_dir or not os.path.isdir(logs_dir):
        return None, []
    log_files = [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith(".log")]
    if not log_files:
        return None, []
    log_files.sort(key=os.path.getmtime, reverse=True)
    latest = log_files[0]
    try:
        with open(latest, encoding="utf-8") as handle:
            lines = [ln.rstrip() for ln in handle.readlines()[-max_lines:]]
        return os.path.basename(latest), lines
    except Exception:
        return os.path.basename(latest), []


async def handle_logs(ctx: Any, deps: Any) -> None:
    deps.logger.info(f"Logs requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        buttons = [
            [Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:logs:{pk}")]
            for pk in deps.iter_project_keys()
        ]
        buttons.append([Button(label="❌ Close", callback_data="flow:close")])
        await ctx.reply_text("Please select a project to view logs:", buttons=buttons)
        return

    inbox_backend = str(deps.get_inbox_storage_backend() or "").strip().lower()
    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "logs")
    if not project_key:
        return

    msg_id = await ctx.reply_text(f"📋 Fetching logs for issue #{issue_num}...")
    repo = deps.project_repo(project_key)
    details = deps.get_issue_details(issue_num, repo=repo)
    timeline = "Task Logs:\n"

    task_file = None
    if details and details.get("body"):
        match = re.search(r"Task File:\s*`([^`]+)`", details.get("body", ""))
        if match:
            task_file = match.group(1)
    if not task_file and inbox_backend != "postgres":
        task_file = deps.find_task_file_by_issue(issue_num)

    issue_logs = deps.find_issue_log_files(issue_num, task_file=task_file)
    if issue_logs:
        # Show newest logs first for operator clarity.
        issue_logs.sort(key=lambda path: os.path.getmtime(path), reverse=True)
        timeline += "\n"
        for log_file in issue_logs:
            size = os.path.getsize(log_file)
            mtime = time.strftime("%H:%M:%S", time.localtime(os.path.getmtime(log_file)))
            timeline += f"• `{os.path.basename(log_file)}` ({size}B, {mtime})\n"

        latest = issue_logs[0]
        try:
            with open(latest, encoding="utf-8") as handle:
                lines = handle.readlines()[-200:]
            timeline += f"\n**{os.path.basename(latest)}** (last 200 lines):\n"
            for line in lines:
                timeline += f"{line.rstrip()}\n"
            if not lines:
                non_empty = [lf for lf in issue_logs if os.path.getsize(lf) > 0]
                if non_empty:
                    fallback = non_empty[0]
                    with open(fallback, encoding="utf-8") as handle:
                        fallback_lines = handle.readlines()[-200:]
                    timeline += (
                        f"\n(ℹ️ Latest log is currently empty; showing most recent non-empty: "
                        f"`{os.path.basename(fallback)}`)\n"
                    )
                    for line in fallback_lines:
                        timeline += f"{line.rstrip()}\n"
        except Exception as exc:
            deps.logger.error(f"Error reading log file: {exc}", exc_info=True)
            timeline += f"\n❌ Failed to read {os.path.basename(latest)}: {exc}\n"
    else:
        latest_tail = deps.read_latest_log_tail(task_file, max_lines=200)
        if not latest_tail:
            issue_refs = deps.search_logs_for_issue(issue_num)
            if issue_refs:
                timeline += "\nReferences in service logs:\n"
                for line in issue_refs[-30:]:
                    timeline += f"{line}\n"
            else:
                timeline += "\n- No task logs found for this issue.\n"
            latest_tail = []
        if latest_tail:
            timeline += "\nLatest Task Logs:\n"
            for log_line in latest_tail:
                timeline += f"{log_line}\n"
        else:
            timeline += "\n- No task logs found.\n"

    await _send_chunked_edit(ctx, msg_id, timeline)


async def handle_logsfull(ctx: Any, deps: Any) -> None:
    deps.logger.info(f"Logsfull requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    if not ctx.args:
        buttons = [
            [Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:logsfull:{pk}")]
            for pk in deps.iter_project_keys()
        ]
        buttons.append([Button(label="❌ Close", callback_data="flow:close")])
        await ctx.reply_text("Please select a project to view full logs:", buttons=buttons)
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "logsfull")
    if not project_key:
        return

    msg_id = await ctx.reply_text(f"📋 Fetching full logs for issue #{issue_num}...")
    repo = deps.project_repo(project_key)
    issue_cfg = deps.project_config.get(project_key)
    issue_url = deps.build_issue_url(
        repo, issue_num, issue_cfg if isinstance(issue_cfg, dict) else None
    )

    details = deps.get_issue_details(issue_num, repo=repo)
    timeline = "Git Platform Activity:\n"
    if details:
        timeline += f"- Title: {details.get('title', 'N/A')}\n"
        timeline += f"- State: {details.get('state', 'open')}\n"
        timeline += f"- Last Updated: {details.get('updatedAt', 'N/A')}\n"
        if details.get("labels"):
            timeline += f"- Labels: {', '.join([l['name'] for l in details.get('labels', [])])}\n"
    else:
        timeline += "- Could not fetch issue details\n"

    system_logs = deps.search_logs_for_issue(issue_num)
    if system_logs:
        timeline += "\nBot/Processor Logs:\n"
        for log_line in system_logs:
            timeline += f"- {log_line}\n"

    task_file = None
    if details and details.get("body"):
        match = re.search(r"Task File:\s*`([^`]+)`", details.get("body", ""))
        if match:
            task_file = match.group(1)
    latest_full = deps.read_latest_log_full(task_file)
    if latest_full:
        timeline += "\nLatest Task Log (full):\n"
        for log_line in latest_full:
            timeline += f"- {log_line}\n"

    processor_log = os.path.join(deps.base_dir, "ghabs", "nexus", "inbox_processor.log")
    processor_matches = deps.read_log_matches(processor_log, issue_num, issue_url, max_lines=20)
    if processor_matches:
        timeline += "\nProcessor Log:\n"
        for log_line in processor_matches:
            timeline += f"- {log_line}\n"

    await _send_chunked_edit(ctx, msg_id, timeline)


async def handle_tail(ctx: Any, deps: Any) -> None:
    deps.logger.info(f"Tail requested by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return
    if not ctx.args:
        buttons = [
            [Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:tail:{pk}")]
            for pk in deps.iter_project_keys()
        ]
        buttons.append([Button(label="❌ Close", callback_data="flow:close")])
        await ctx.reply_text("Please select a project to tail logs:", buttons=buttons)
        return

    inbox_backend = str(deps.get_inbox_storage_backend() or "").strip().lower()
    project_key, issue_num, rest = await deps.ensure_project_issue(ctx, "tail")
    if not project_key:
        return

    max_lines = 50
    follow_seconds = 30
    refresh_seconds = 3
    if rest:
        try:
            max_lines = max(5, min(200, int(rest[0])))
        except ValueError:
            await ctx.reply_text("⚠️ Line count must be a number.")
            return
    if len(rest) > 1:
        try:
            follow_seconds = max(5, min(300, int(rest[1])))
        except ValueError:
            await ctx.reply_text("⚠️ Follow duration must be a number of seconds.")
            return

    repo = deps.project_repo(project_key)
    details = deps.get_issue_details(issue_num, repo=repo)
    task_file = None
    if details and details.get("body"):
        match = re.search(r"Task File:\s*`([^`]+)`", details.get("body", ""))
        if match:
            task_file = match.group(1)
    if not task_file and inbox_backend != "postgres":
        task_file = deps.find_task_file_by_issue(issue_num)

    def _read_tail_lines() -> list[str]:
        issue_logs = deps.find_issue_log_files(issue_num, task_file=task_file)
        if issue_logs:
            issue_logs.sort(key=os.path.getmtime, reverse=True)
            latest = issue_logs[0]
            try:
                with open(latest, encoding="utf-8") as handle:
                    tail_lines = handle.readlines()[-max_lines:]
                if not tail_lines:
                    non_empty = [lf for lf in issue_logs if os.path.getsize(lf) > 0]
                    if non_empty:
                        latest = non_empty[0]
                        with open(latest, encoding="utf-8") as handle:
                            tail_lines = handle.readlines()[-max_lines:]
                return [f"[{os.path.basename(latest)}] {line.rstrip()}" for line in tail_lines]
            except Exception as exc:
                deps.logger.error(f"Error reading log file: {exc}", exc_info=True)

        lines_local = deps.read_latest_log_tail(task_file, max_lines=max_lines)
        if lines_local:
            return lines_local
        logs_dir = deps.get_project_logs_dir(project_key)
        if logs_dir:
            log_files = [
                os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith(".log")
            ]
            if log_files:
                log_files.sort(key=os.path.getmtime, reverse=True)
                latest = log_files[0]
                try:
                    with open(latest, encoding="utf-8") as handle:
                        tail_lines = handle.readlines()[-max_lines:]
                    return [f"[{os.path.basename(latest)}] {line.rstrip()}" for line in tail_lines]
                except Exception as exc:
                    deps.logger.error(f"Error reading log file: {exc}", exc_info=True)
        return []

    session_key = (ctx.chat_id, int(ctx.user_id))
    existing_task = deps.active_tail_tasks.get(session_key)
    if existing_task and not existing_task.done():
        existing_task.cancel()

    session_token = f"{issue_num}:{time.time()}"
    deps.active_tail_sessions[session_key] = session_token
    msg_id = await ctx.reply_text(
        f"📋 Following log tail for #{issue_num} ({max_lines} lines, {follow_seconds}s)...\nUse /tailstop to stop."
    )

    async def _run_tail_follow() -> None:
        deadline = time.time() + follow_seconds
        previous_text = ""
        try:
            while True:
                if deps.active_tail_sessions.get(session_key) != session_token:
                    break
                lines = _read_tail_lines()
                text = (
                    f"📋 Live log tail (#{issue_num}, {max_lines} lines):\n" + "\n".join(lines)
                    if lines
                    else f"📋 Live log tail (#{issue_num}, {max_lines} lines):\n⚠️ No task logs found yet. Waiting for new output..."
                )
                if len(text) > 3500:
                    text = text[:3436] + "\n\n…(truncated, rerun with fewer lines)"
                if text != previous_text:
                    try:
                        await ctx.edit_message_text(message_id=msg_id, text=text, parse_mode=None)
                        previous_text = text
                    except Exception as exc:
                        deps.logger.warning(
                            f"Failed to update tail message for issue #{issue_num}: {exc}"
                        )
                if time.time() >= deadline:
                    break
                await asyncio.sleep(refresh_seconds)
        finally:
            if deps.active_tail_sessions.get(session_key) == session_token:
                deps.active_tail_sessions.pop(session_key, None)
            existing = deps.active_tail_tasks.get(session_key)
            if existing is asyncio.current_task():
                deps.active_tail_tasks.pop(session_key, None)

    deps.active_tail_tasks[session_key] = asyncio.create_task(_run_tail_follow())


async def _send_chunked_edit(ctx: Any, msg_id: Any, timeline: str, *, max_len: int = 3500) -> None:
    if len(timeline) <= max_len:
        await ctx.edit_message_text(message_id=msg_id, text=timeline, parse_mode=None)
        return
    chunks = [timeline[i : i + max_len] for i in range(0, len(timeline), max_len)]
    await ctx.edit_message_text(message_id=msg_id, text=chunks[0], parse_mode=None)
    for part in chunks[1:]:
        await ctx.reply_text(text=part, parse_mode=None)
