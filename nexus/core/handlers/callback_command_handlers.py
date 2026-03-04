"""Callback and picker handlers extracted from telegram_bot."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext

from nexus.core.callbacks.callback_inline_service import (
    handle_merge_queue_inline_action,
    parse_inline_action,
)
from nexus.core.callbacks.callback_menu_service import (
    handle_menu_callback as service_menu_callback_handler,
)
from nexus.core.state_manager import HostStateManager


@dataclass
class CallbackHandlerDeps:
    logger: logging.Logger
    prompt_issue_selection: Callable[..., Awaitable[None]]
    dispatch_command: Callable[..., Awaitable[None]]
    get_project_label: Callable[[str], str]
    get_repo: Callable[[str], str]
    get_direct_issue_plugin: Callable[[str], Any]
    get_workflow_state_plugin: Callable[..., Any]
    workflow_state_plugin_kwargs: dict[str, Any]
    action_handlers: dict[str, Callable[..., Awaitable[None]]]
    report_bug_action: Callable[[InteractiveContext, str, str], Awaitable[None]]


async def core_callback_router(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    data = ctx.query.data
    monitor_prefixes = (
        "status:",
        "active:",
        "logs:",
        "logsfull:",
        "tail:",
        "fuse:",
    )
    if data.startswith("pickcmd:"):
        await project_picker_handler(ctx, deps)
    elif (
        data.startswith("pickissue")
        or data.startswith("pickissue_manual:")
        or data.startswith("pickissue_state:")
    ):
        await issue_picker_handler(ctx, deps)
    elif data.startswith("pickmonitor:") or data.startswith(monitor_prefixes):
        await monitor_project_picker_handler(ctx, deps)
    elif data.startswith("flow:close"):
        await flow_close_handler(ctx, deps)
    elif data.startswith("menu:"):
        await menu_callback_handler(ctx, deps)
    elif data == "close":
        await close_flow_handler(ctx, deps)


async def project_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data or not query_data.startswith("pickcmd:"):
        return

    _, command, project_key = query_data.split(":", 2)
    ctx.user_state["pending_command"] = command
    ctx.user_state["pending_project"] = project_key

    if command == "agents":
        ctx.user_state.pop("pending_command", None)
        ctx.user_state.pop("pending_project", None)
        await deps.dispatch_command(ctx, command, project_key, "")
        return

    pending_issue = ctx.user_state.get("pending_issue")
    if pending_issue and command != "respond":
        ctx.user_state.pop("pending_issue", None)
        await deps.dispatch_command(ctx, command, project_key, pending_issue)
        return

    if pending_issue and command == "respond":
        await ctx.edit_message_text(
            f"Selected {deps.get_project_label(project_key)}. Now send the response message."
        )
        return

    await deps.prompt_issue_selection(ctx, command, project_key, edit_message=True)


async def issue_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data:
        return

    if query_data.startswith("pickissue_manual:"):
        _, command, project_key = query_data.split(":", 2)
        ctx.user_state["pending_command"] = command
        ctx.user_state["pending_project"] = project_key
        await ctx.edit_message_text(
            f"Selected {deps.get_project_label(project_key)}. Send the issue number."
        )
        return

    if query_data.startswith("pickissue_state:"):
        _, issue_state, command, project_key = query_data.split(":", 3)
        await deps.prompt_issue_selection(
            ctx,
            command,
            project_key,
            edit_message=True,
            issue_state=issue_state,
        )
        return

    if not query_data.startswith("pickissue:"):
        return

    _, command, project_key, issue_num = query_data.split(":", 3)
    await ctx.edit_message_text(ctx.text, buttons=[])
    await deps.dispatch_command(ctx, command, project_key, issue_num)


async def monitor_project_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data:
        return

    command = ""
    project_key = ""
    extra_args: list[str] = []

    parts = query_data.split(":")
    if query_data.startswith("pickmonitor:"):
        if len(parts) < 3:
            return
        command = parts[1]
        project_key = parts[2]
        extra_args = parts[3:]
    else:
        if len(parts) < 2:
            return
        command = parts[0]
        project_key = parts[1]
        extra_args = parts[2:]

    if command in {"logs", "logsfull", "tail", "fuse"}:
        ctx.user_state["pending_command"] = command
        ctx.user_state["pending_project"] = project_key
        await deps.prompt_issue_selection(ctx, command, project_key, edit_message=True)
        return

    # Check for direct handlers in action_handlers first (status, active, etc.)
    handler = deps.action_handlers.get(command)
    if handler:
        ctx.args = [project_key] + extra_args
        await handler(ctx)
        return

    await ctx.edit_message_text("Unsupported monitoring command.")


async def close_flow_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    await ctx.edit_message_text(ctx.text, buttons=[])


async def flow_close_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    await ctx.edit_message_text("❌ Cancelled.")


async def menu_callback_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await service_menu_callback_handler(ctx)


async def inline_keyboard_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query_data = ctx.query.data

    if not query_data:
        return

    parsed = parse_inline_action(query_data)
    if not parsed:
        return
    action, issue_num, project_hint = parsed

    if not project_hint:
        await ctx.edit_message_text(
            "❌ This action is missing project context and is no longer supported.\n"
            "Please trigger the latest message/action again."
        )
        return

    if action == "report_bug":
        await deps.report_bug_action(ctx, issue_num, project_hint)
        return

    deps.logger.info(f"Inline keyboard action: {action} for issue #{issue_num}")

    if action in deps.action_handlers:
        await deps.dispatch_command(ctx, action, project_hint, issue_num)
        return
    elif action in {"mqapprove", "mqretry", "mqmerge"}:
        await handle_merge_queue_inline_action(
            ctx, action=action, issue_num=issue_num, project_hint=project_hint
        )
        return
    elif action == "respond":
        await ctx.edit_message_text(
            f"✍️ To respond to issue #{issue_num}, use:\n\n"
            f"`/respond {project_hint} {issue_num} <your message>`\n\n"
            f"Example:\n"
            f"`/respond {project_hint} {issue_num} Approved, proceed with implementation`"
        )
    elif action == "approve":
        ctx.args = [issue_num]
        await ctx.edit_message_text(f"✅ Approving implementation for issue #{issue_num}...")

        try:
            repo = deps.get_repo(project_hint)
            plugin = deps.get_direct_issue_plugin(repo)
            if not plugin or not plugin.add_comment(
                issue_num,
                "✅ Implementation approved. Please proceed.",
            ):
                await ctx.edit_message_text(f"❌ Error approving issue #{issue_num}")
                return
            await ctx.edit_message_text(
                f"✅ Implementation approved for issue #{issue_num}\n\n"
                f"Agent will continue automatically."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error approving: {exc}")
    elif action == "reject":
        ctx.args = [issue_num]
        await ctx.edit_message_text(f"❌ Rejecting implementation for issue #{issue_num}...")

        try:
            repo = deps.get_repo(project_hint)
            plugin = deps.get_direct_issue_plugin(repo)
            if not plugin or not plugin.add_comment(
                issue_num,
                "❌ Implementation rejected. Please revise.",
            ):
                await ctx.edit_message_text(f"❌ Error rejecting issue #{issue_num}")
                return
            await ctx.edit_message_text(
                f"❌ Implementation rejected for issue #{issue_num}\n\n" f"Agent has been notified."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error rejecting: {exc}")
    elif action == "wfapprove":
        parts2 = issue_num.split("_", 1)
        real_issue = parts2[0]
        step_num = parts2[1] if len(parts2) > 1 else "?"
        await ctx.edit_message_text(
            f"✅ Approving workflow step {step_num} for issue #{real_issue}..."
        )
        try:
            workflow_plugin = deps.get_workflow_state_plugin(
                **deps.workflow_state_plugin_kwargs,
                cache_key="workflow:state-engine",
            )
            approved_by = ctx.client.name
            if not workflow_plugin or not await workflow_plugin.approve_step(
                real_issue, approved_by
            ):
                await ctx.edit_message_text(f"❌ No workflow found for issue #{real_issue}")
                return
            await ctx.edit_message_text(
                f"✅ Step {step_num} approved for issue #{real_issue}\n\n"
                f"Workflow will continue automatically."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error approving workflow step: {exc}")
    elif action == "wfdeny":
        parts2 = issue_num.split("_", 1)
        real_issue = parts2[0]
        step_num = parts2[1] if len(parts2) > 1 else "?"
        await ctx.edit_message_text(
            f"❌ Denying workflow step {step_num} for issue #{real_issue}..."
        )
        try:
            workflow_plugin = deps.get_workflow_state_plugin(
                **deps.workflow_state_plugin_kwargs,
                cache_key="workflow:state-engine",
            )
            denied_by = ctx.client.name
            if not workflow_plugin or not await workflow_plugin.deny_step(
                real_issue,
                denied_by,
                reason="Denied via Interactive Client",
            ):
                await ctx.edit_message_text(f"❌ No workflow found for issue #{real_issue}")
                return
            await ctx.edit_message_text(
                f"❌ Step {step_num} denied for issue #{real_issue}\n\n"
                f"Workflow has been stopped."
            )
        except Exception as exc:
            await ctx.edit_message_text(f"❌ Error denying workflow step: {exc}")
