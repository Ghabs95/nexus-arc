from __future__ import annotations

import os
import re
import time
from typing import Any

from nexus.adapters.notifications.base import Button
from nexus.core.utils.log_utils import log_unauthorized_access


async def handle_status(ctx: Any, deps: Any) -> None:
    deps.logger.info(f"Status triggered by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    project_filter = None
    issue_num: str | None = None
    if ctx.args:
        raw = ctx.args[0].strip().lower()
        if raw != "all":
            project_filter = deps.normalize_project_key(raw)
            if project_filter not in deps.iter_project_keys():
                await ctx.reply_text(f"❌ Unknown project '{raw}'.")
                return
        if len(ctx.args) > 1:
            candidate = str(ctx.args[1]).strip()
            if candidate.isdigit():
                issue_num = candidate
    else:
        buttons = [
            [Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:status:{pk}")]
            for pk in deps.iter_project_keys()
        ]
        buttons.append([Button(label="All Projects", callback_data="pickmonitor:status:all")])
        buttons.append([Button(label="❌ Close", callback_data="flow:close")])
        await ctx.reply_text("Please select a project to view its status:", buttons=buttons)
        return

    selected_projects = [project_filter] if project_filter else deps.iter_project_keys()
    status_text = "📥 Inbox Status (Pending Tasks)\n\n"
    total_tasks = 0
    inbox_backend = str(deps.get_inbox_storage_backend() or "").strip().lower()

    if inbox_backend == "postgres":
        overview = deps.get_inbox_queue_overview(limit=50) or {}
        pending_by_project = overview.get("pending_by_project", {})
        if not isinstance(pending_by_project, dict):
            pending_by_project = {}

        for project_key in selected_projects:
            project_name = deps.get_project_label(project_key)
            bucket = pending_by_project.get(project_key) or {}
            count = int(bucket.get("count", 0) or 0) if isinstance(bucket, dict) else 0
            if count <= 0:
                continue
            status_text += f"{project_name}: {count} task(s)\n"
            total_tasks += count
            samples = bucket.get("samples", []) if isinstance(bucket, dict) else []
            if not isinstance(samples, list):
                samples = []
            for item in samples[:3]:
                if not isinstance(item, dict):
                    continue
                filename = str(item.get("filename", "task.md") or "task.md")
                task_type = filename.split("_")[0]
                emoji = deps.types_map.get(task_type, "📝")
                status_text += f"  • {emoji} `{filename}` (db queue)\n"
            if count > 3:
                status_text += f"  ... +{count - 3} more\n"
            status_text += "\n"
    else:
        for project_key in selected_projects:
            project_name = deps.get_project_label(project_key)
            project_root = deps.get_project_root(project_key)
            if not project_root:
                continue
            inbox_dir = deps.get_inbox_dir(project_root, project_key)
            if not os.path.exists(inbox_dir):
                continue
            files = [f for f in os.listdir(inbox_dir) if f.endswith(".md")]
            if not files:
                continue
            repo = deps.project_repo(project_key)
            project_issue_cfg = deps.project_config.get(project_key)
            status_text += f"{project_name}: {len(files)} task(s)\n"
            total_tasks += len(files)
            for filename in files[:3]:
                task_type = filename.split("_")[0]
                emoji = deps.types_map.get(task_type, "📝")
                issue_number = deps.extract_issue_number_from_file(
                    os.path.join(inbox_dir, filename)
                )
                if issue_number:
                    issue_link = deps.build_issue_url(
                        repo,
                        issue_number,
                        project_issue_cfg if isinstance(project_issue_cfg, dict) else None,
                    )
                    issue_suffix = f" [#{issue_number}]({issue_link})"
                else:
                    issue_suffix = " (issue ?)"
                status_text += f"  • {emoji} `{filename}`{issue_suffix}\n"
            if len(files) > 3:
                status_text += f"  ... +{len(files) - 3} more\n"
            status_text += "\n"

    status_text += (
        "✨ No pending tasks in inbox!\n"
        if total_tasks == 0
        else f"Total: {total_tasks} pending task(s)"
    )
    with_issue_snapshot = bool(issue_num and project_filter)

    if with_issue_snapshot:
        if inbox_backend == "postgres":
            status_text += (
                f"\n\n📊 Workflow Snapshot — Issue #{issue_num}\n"
                "DB-only mode is enabled (`postgres`). Local task/completion file snapshot is disabled.\n"
                "Use /wfstate for workflow state and /audit for event history."
            )
        else:
            repo = deps.project_repo(project_filter)
            expected_running = deps.normalize_agent_reference(
                deps.get_expected_running_agent_from_workflow(issue_num) or ""
            )
            snapshot = deps.build_workflow_snapshot(
                issue_num=issue_num,
                repo=repo,
                get_issue_plugin=deps.get_direct_issue_plugin,
                expected_running_agent=expected_running,
                find_task_file_by_issue=deps.find_task_file_by_issue,
                read_latest_local_completion=deps.read_latest_local_completion,
                extract_structured_completion_signals=deps.extract_structured_completion_signals,
            )
            processor_signal = snapshot.get("processor_signal") or {}
            processor_type = processor_signal.get("type", "n/a")
            processor_severity = processor_signal.get("severity", "n/a")
            processor_at = processor_signal.get("timestamp", "n/a")
            processor_line = processor_signal.get("line", "n/a")
            recovery_hint = "none"
            if processor_type == "completion_mismatch":
                recovery_hint = "stale completion signal. Run /reconcile then /continue"
            elif processor_type in {"signal_drift", "retry_fuse", "pause_failed"}:
                recovery_hint = "workflow drift. Run /wfstate, then /reconcile and /continue"
            status_text += (
                "\n\n"
                f"📊 Workflow Snapshot — Issue #{issue_num}\n"
                f"Workflow State: {snapshot['workflow_state']}\n"
                f"Current Step: {snapshot['current_step']} ({snapshot['current_step_name']})\n"
                f"Current Agent: {snapshot['current_agent']}\n"
                f"Expected RUNNING Agent: {snapshot['expected_running_agent'] or expected_running or 'n/a'}\n"
                f"Process: {'running' if snapshot['running'] else 'stopped'} (PID: {snapshot['pid'] or 'n/a'})\n"
                f"Local Completion: from={snapshot['local_from'] or 'n/a'}, next={snapshot['local_next'] or 'n/a'}\n"
                f"Latest Comment: from={snapshot['comment_from'] or 'n/a'}, next={snapshot['comment_next'] or 'n/a'}\n"
                f"Drift Flags: {', '.join(snapshot['drift_flags']) if snapshot['drift_flags'] else 'none'}\n"
                "Latest Processor Signal:\n"
                f"- type: {processor_type}\n- severity: {processor_severity}\n- at: {processor_at}\n- detail: {processor_line}\n"
                f"Recovery Hint: {recovery_hint}"
            )
            if snapshot.get("workflow_pointer_mismatch"):
                status_text += (
                    "\nWorkflow Pointer Mismatch:\n"
                    f"- indexed: {snapshot['indexed_step']} ({snapshot['indexed_step_name']}) / {snapshot['indexed_agent']}\n"
                    f"- running: {snapshot['running_step']} ({snapshot['running_step_name']}) / {snapshot['running_agent']}"
                )

    await ctx.reply_text(
        status_text,
        parse_mode=None if with_issue_snapshot else "Markdown",
        disable_web_page_preview=True,
    )


async def handle_active(ctx: Any, deps: Any) -> None:
    deps.logger.info(f"Active triggered by user: {ctx.user_id}")
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(getattr(deps, "logger", None), int(ctx.user_id))
        return

    cleanup_mode = any(arg.lower() in {"cleanup", "--cleanup"} for arg in (ctx.args or []))
    project_tokens = [
        arg for arg in (ctx.args or []) if arg.lower() not in {"cleanup", "--cleanup"}
    ]
    project_filter = None
    if project_tokens:
        raw = project_tokens[0].strip().lower()
        if raw != "all":
            project_filter = deps.normalize_project_key(raw)
            if project_filter not in deps.iter_project_keys():
                await ctx.reply_text(f"❌ Unknown project '{raw}'.")
                return
    elif not cleanup_mode:
        buttons = [
            [Button(label=deps.get_project_label(pk), callback_data=f"pickmonitor:active:{pk}")]
            for pk in deps.iter_project_keys()
        ]
        buttons.append([Button(label="All Projects", callback_data="pickmonitor:active:all")])
        buttons.append([Button(label="❌ Close", callback_data="flow:close")])
        await ctx.reply_text("Please select a project to view its active tasks:", buttons=buttons)
        return

    inbox_backend = str(deps.get_inbox_storage_backend() or "").strip().lower()
    if inbox_backend == "postgres":
        if cleanup_mode:
            await ctx.reply_text(
                "⚠️ `/active cleanup` is disabled in `postgres` mode because it requires filesystem task files.",
                parse_mode="Markdown",
            )
            return
        await ctx.reply_text(
            "⚠️ `/active` is filesystem-based and is disabled in `postgres` mode. Use `/wfstate`, `/status`, and `/audit` for DB-backed visibility.",
            parse_mode="Markdown",
        )
        return

    selected_projects = [project_filter] if project_filter else deps.iter_project_keys()
    active_text = "🚀 Active Tasks (In Progress)\n\n"
    if cleanup_mode:
        active_text += "🧹 Cleanup mode: archiving closed tasks to `tasks/closed`\n\n"
    total_active = total_skipped_closed = total_archived = 0
    issue_state_cache: dict[str, str] = {}

    for project_key in selected_projects:
        display_name = deps.get_project_label(project_key)
        project_root = deps.get_project_root(project_key)
        if not project_root:
            continue
        active_dir = deps.get_tasks_active_dir(project_root, project_key)
        if not os.path.exists(active_dir):
            continue
        files = [f for f in os.listdir(active_dir) if f.endswith(".md")]
        if not files:
            continue
        repo = deps.project_repo(project_key)
        project_issue_cfg = deps.project_config.get(project_key)
        open_files: list[tuple[str, str | None]] = []
        stale_count = 0
        for filename in files:
            file_path = os.path.join(active_dir, filename)
            issue_number = deps.extract_issue_number_from_file(file_path)
            if not issue_number:
                m = re.search(r"_(\d+)\.md$", filename)
                issue_number = m.group(1) if m else None
            if issue_number:
                cache_key = f"{repo}:{issue_number}"
                if cache_key not in issue_state_cache:
                    details = deps.get_issue_details(issue_number, repo=repo)
                    issue_state_cache[cache_key] = (
                        "orphan" if not details else details.get("state", "unknown").lower()
                    )
                issue_state = issue_state_cache[cache_key]
            else:
                issue_state = "orphan"

            if issue_state in {"open", "unknown"}:
                open_files.append((filename, issue_number))
                continue

            stale_count += 1
            if cleanup_mode:
                try:
                    closed_dir = deps.get_tasks_closed_dir(project_root, project_key)
                    os.makedirs(closed_dir, exist_ok=True)
                    target_path = os.path.join(closed_dir, filename)
                    if os.path.exists(target_path):
                        base, ext = os.path.splitext(filename)
                        target_path = os.path.join(closed_dir, f"{base}_{int(time.time())}{ext}")
                    os.replace(file_path, target_path)
                    total_archived += 1
                except Exception as exc:
                    deps.logger.warning(f"Failed to archive {file_path}: {exc}")

        if not open_files:
            total_skipped_closed += stale_count
            continue
        active_text += f"{display_name}: {len(open_files)} task(s)\n"
        total_active += len(open_files)
        total_skipped_closed += stale_count
        for filename, issue_number in open_files[:3]:
            emoji = deps.types_map.get(filename.split("_")[0], "📝")
            if issue_number:
                issue_link = deps.build_issue_url(
                    repo,
                    issue_number,
                    project_issue_cfg if isinstance(project_issue_cfg, dict) else None,
                )
                issue_suffix = f" [#{issue_number}]({issue_link})"
            else:
                issue_suffix = " _(orphan — no issue reference)_"
            active_text += f"  • {emoji} `{filename}`{issue_suffix}\n"
        if len(open_files) > 3:
            active_text += f"  ... +{len(open_files) - 3} more\n"
        active_text += "\n"

    active_text += (
        "💤 No active tasks at the moment.\n"
        if total_active == 0
        else f"Total: {total_active} active task(s)"
    )
    if total_skipped_closed:
        active_text += f"\n\nℹ️ Skipped {total_skipped_closed} closed or orphan task file(s)."
    if cleanup_mode:
        active_text += f"\n📦 Archived {total_archived} closed task file(s) to `tasks/closed`."
    await ctx.reply_text(active_text, parse_mode="Markdown", disable_web_page_preview=True)
