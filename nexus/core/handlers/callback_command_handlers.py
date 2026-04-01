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
from nexus.core.telegram.telegram_router_feedback_service import (
    CALLBACK_PREFIX,
    MODEL_VERDICTS,
    PENDING_KEY,
    TASK_LABELS,
    build_feedback_payload,
    build_wrong_model_prompt,
    build_wrong_task_prompt,
    clear_external_pending_feedback,
    decision_token,
    has_feedback_submission,
    load_external_pending_feedback,
    remember_feedback_submission,
    submit_feedback,
)


_ROUTE_FEEDBACK_VALID_ACTIONS = {"ok", "wrong", "fix", "wrong_task", "wrong_model"}


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
    requester_context_builder: Callable[[int], dict[str, Any]] | None = None
    router_feedback_url: str | None = None


def _resolve_direct_issue_plugin(
    deps: CallbackHandlerDeps,
    repo: str,
    requester_nexus_id: str | None,
):
    if requester_nexus_id:
        try:
            return deps.get_direct_issue_plugin(repo, requester_nexus_id=requester_nexus_id)
        except TypeError:
            return deps.get_direct_issue_plugin(repo)
    return deps.get_direct_issue_plugin(repo)


async def core_callback_router(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    query = ctx.query
    if query is None:
        return
    data = query.action_data
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
    elif data.startswith(CALLBACK_PREFIX):
        await route_feedback_callback_handler(ctx, deps)
    elif data.startswith("menu:"):
        await menu_callback_handler(ctx, deps)
    elif data == "close":
        await close_flow_handler(ctx, deps)


async def project_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query = ctx.query
    if query is None:
        return
    query_data = query.action_data

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
            message_id=query.message_id,
            text=f"Selected {deps.get_project_label(project_key)}. Now send the response message.",
        )
        return

    await deps.prompt_issue_selection(ctx, command, project_key, edit_message=True)


async def issue_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query = ctx.query
    if query is None:
        return
    query_data = query.action_data

    if not query_data:
        return

    if query_data.startswith("pickissue_manual:"):
        _, command, project_key = query_data.split(":", 2)
        ctx.user_state["pending_command"] = command
        ctx.user_state["pending_project"] = project_key
        await ctx.edit_message_text(
            message_id=query.message_id,
            text=f"Selected {deps.get_project_label(project_key)}. Send the issue number.",
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
    await ctx.edit_message_text(
        message_id=query.message_id,
        text=ctx.text,
        buttons=[],
    )
    await deps.dispatch_command(ctx, command, project_key, issue_num)


async def monitor_project_picker_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query = ctx.query
    if query is None:
        return
    query_data = query.action_data

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

    await ctx.edit_message_text(
        message_id=query.message_id,
        text="Unsupported monitoring command.",
    )


async def close_flow_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query = ctx.query
    if query is None:
        return
    await ctx.edit_message_text(
        message_id=query.message_id,
        text=ctx.text,
        buttons=[],
    )


async def flow_close_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query = ctx.query
    if query is None:
        return
    await ctx.edit_message_text(
        message_id=query.message_id,
        text="❌ Cancelled.",
    )


async def menu_callback_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await service_menu_callback_handler(ctx)


async def route_feedback_callback_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query = ctx.query
    if query is None:
        return
    query_data = str(query.action_data or "")
    parts = query_data.split(":", 4)
    if len(parts) < 3:
        await ctx.edit_message_text(message_id=query.message_id, text="⚠️ Invalid feedback action.", buttons=[])
        return
    action = parts[1]
    decision_ref = parts[2]

    if action not in _ROUTE_FEEDBACK_VALID_ACTIONS:
        await ctx.edit_message_text(message_id=query.message_id, text="⚠️ Invalid feedback action.", buttons=[])
        return

    pending = ctx.user_state.get(PENDING_KEY)
    if not isinstance(pending, dict):
        pending = load_external_pending_feedback(user_id=str(ctx.user_id or ""))
        if isinstance(pending, dict):
            ctx.user_state[PENDING_KEY] = pending
    if not isinstance(pending, dict):
        pending = {
            "decision_id": decision_ref,
            "feedback_mode": "router",
            "source_channel": getattr(getattr(ctx, "client", None), "name", None) or "openclaw",
            "metadata": {},
        }
        ctx.user_state[PENDING_KEY] = pending

    actual_decision_id = str(pending.get("decision_id") or "")
    expected_refs = {actual_decision_id, decision_token(actual_decision_id)}
    if decision_ref not in expected_refs:
        await ctx.edit_message_text(message_id=query.message_id, text="⚠️ Feedback no longer matches the latest route.", buttons=[])
        return

    user_id = str(ctx.user_id or "") or None
    if has_feedback_submission(ctx.user_state, decision_id=actual_decision_id, user_id=user_id):
        await ctx.edit_message_text(message_id=query.message_id, text="✅ Feedback already recorded.", buttons=[])
        return

    # ── ✅ Correct ──────────────────────────────────────────────────────────
    if action == "ok":
        payload = build_feedback_payload(
            meta=pending,
            verdict="correct",
            corrected_task=None,
            model_verdict=None,
            source_message_id=str(query.message_id or "") or None,
            source_user_id=user_id,
        )
        ok, _detail = submit_feedback(router_url=str(deps.router_feedback_url or ""), payload=payload)
        if not ok:
            await ctx.edit_message_text(
                message_id=query.message_id,
                text="⚠️ Feedback service unreachable. Reply later if needed.",
                buttons=[],
                parse_mode=None,
            )
            return
        remember_feedback_submission(ctx.user_state, decision_id=actual_decision_id, user_id=user_id)
        ctx.user_state.pop(PENDING_KEY, None)
        clear_external_pending_feedback(user_id=str(ctx.user_id or ""), decision_id=actual_decision_id)
        await ctx.edit_message_text(message_id=query.message_id, text="✅ Feedback recorded.", buttons=[])
        return

    # ── ❌ Wrong — Step 1: show task selection ───────────────────────────────
    if action == "wrong":
        text, buttons = build_wrong_task_prompt(pending)
        await ctx.edit_message_text(message_id=query.message_id, text=text, buttons=buttons)
        return

    # ── wrong_task — store task choice, show model verdict step ─────────────
    if action == "wrong_task":
        raw_task = parts[3] if len(parts) >= 4 else "skip"
        corrected_task: str | None = raw_task if raw_task != "skip" and raw_task in TASK_LABELS else None
        # Persist chosen task in pending so step 2 can read it
        pending["_pending_corrected_task"] = corrected_task
        ctx.user_state[PENDING_KEY] = pending
        text, buttons = build_wrong_model_prompt(pending, corrected_task)
        await ctx.edit_message_text(message_id=query.message_id, text=text, buttons=buttons)
        return

    # ── wrong_model — final: submit with both corrections ───────────────────
    if action == "wrong_model":
        # parts: routefb : wrong_model : decision_id : task_slot : verdict_key
        task_slot = parts[3] if len(parts) >= 4 else "skip"
        raw_model_verdict = parts[4] if len(parts) >= 5 else "skip"
        corrected_task = task_slot if task_slot != "skip" and task_slot in TASK_LABELS else None
        model_verdict: str | None = raw_model_verdict if raw_model_verdict in MODEL_VERDICTS else None
        payload = build_feedback_payload(
            meta=pending,
            verdict="wrong",
            corrected_task=corrected_task,
            model_verdict=model_verdict,
            source_message_id=str(query.message_id or "") or None,
            source_user_id=user_id,
        )
        ok, _detail = submit_feedback(router_url=str(deps.router_feedback_url or ""), payload=payload)
        if not ok:
            await ctx.edit_message_text(
                message_id=query.message_id,
                text="⚠️ Feedback service unreachable. Reply later with `wrong -> reasoning` if needed.",
                buttons=[],
                parse_mode=None,
            )
            return
        remember_feedback_submission(ctx.user_state, decision_id=actual_decision_id, user_id=user_id)
        ctx.user_state.pop(PENDING_KEY, None)
        clear_external_pending_feedback(user_id=str(ctx.user_id or ""), decision_id=actual_decision_id)
        parts_summary = []
        if corrected_task:
            parts_summary.append(f"task→{corrected_task}")
        if model_verdict:
            parts_summary.append(f"model→{model_verdict}")
        summary = "✅ Marked wrong" + (f" ({', '.join(parts_summary)})" if parts_summary else "") + "."
        await ctx.edit_message_text(message_id=query.message_id, text=summary, buttons=[])
        return

    # ── Legacy fix:decision_id:label (backward compat) ──────────────────────
    if action == "fix":
        corrected_task = parts[3] if len(parts) >= 4 else None
        if not corrected_task or corrected_task not in TASK_LABELS:
            await ctx.edit_message_text(message_id=query.message_id, text="⚠️ Invalid or missing correction task.", buttons=[])
            return
        payload = build_feedback_payload(
            meta=pending,
            verdict="wrong",
            corrected_task=corrected_task,
            model_verdict=None,
            source_message_id=str(query.message_id or "") or None,
            source_user_id=user_id,
        )
        ok, _detail = submit_feedback(router_url=str(deps.router_feedback_url or ""), payload=payload)
        if not ok:
            await ctx.edit_message_text(
                message_id=query.message_id,
                text="⚠️ Feedback service unreachable. Reply later with `wrong -> reasoning` if needed.",
                buttons=[],
                parse_mode=None,
            )
            return
        remember_feedback_submission(ctx.user_state, decision_id=actual_decision_id, user_id=user_id)
        ctx.user_state.pop(PENDING_KEY, None)
        clear_external_pending_feedback(user_id=str(ctx.user_id or ""), decision_id=actual_decision_id)
        await ctx.edit_message_text(message_id=query.message_id, text=f"✅ Marked wrong → {corrected_task}.", buttons=[])
        return


async def inline_keyboard_handler(ctx: InteractiveContext, deps: CallbackHandlerDeps):
    await ctx.answer_callback_query()
    query = ctx.query
    if query is None:
        return
    query_data = query.action_data

    if not query_data:
        return

    parsed = parse_inline_action(query_data)
    if not parsed:
        return
    action, issue_num, project_hint = parsed

    if not project_hint:
        await ctx.edit_message_text(
            message_id=query.message_id,
            text=(
                "❌ This action is missing project context and is no longer supported.\n"
                "Please trigger the latest message/action again."
            ),
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
            message_id=query.message_id,
            text=(
                f"✍️ To respond to issue #{issue_num}, use:\n\n"
                f"`/respond {project_hint} {issue_num} <your message>`\n\n"
                f"Example:\n"
                f"`/respond {project_hint} {issue_num} Approved, proceed with implementation`"
            ),
        )
    elif action == "approve":
        ctx.args = [issue_num]
        await ctx.edit_message_text(
            message_id=query.message_id,
            text=f"✅ Approving implementation for issue #{issue_num}...",
        )

        try:
            requester_nexus_id = None
            if callable(deps.requester_context_builder):
                requester_context = deps.requester_context_builder(int(str(ctx.user_id)))
                requester_nexus_id = str(requester_context.get("nexus_id") or "").strip() or None
            repo = deps.get_repo(project_hint)
            plugin = _resolve_direct_issue_plugin(deps, repo, requester_nexus_id)
            if not plugin or not plugin.add_comment(
                issue_num,
                "✅ Implementation approved. Please proceed.",
            ):
                await ctx.edit_message_text(
                    message_id=query.message_id,
                    text=f"❌ Error approving issue #{issue_num}",
                )
                return
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=(
                    f"✅ Implementation approved for issue #{issue_num}\n\n"
                    f"Agent will continue automatically."
                ),
            )
        except Exception as exc:
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=f"❌ Error approving: {exc}",
            )
    elif action == "reject":
        ctx.args = [issue_num]
        await ctx.edit_message_text(
            message_id=query.message_id,
            text=f"❌ Rejecting implementation for issue #{issue_num}...",
        )

        try:
            requester_nexus_id = None
            if callable(deps.requester_context_builder):
                requester_context = deps.requester_context_builder(int(str(ctx.user_id)))
                requester_nexus_id = str(requester_context.get("nexus_id") or "").strip() or None
            repo = deps.get_repo(project_hint)
            plugin = _resolve_direct_issue_plugin(deps, repo, requester_nexus_id)
            if not plugin or not plugin.add_comment(
                issue_num,
                "❌ Implementation rejected. Please revise.",
            ):
                await ctx.edit_message_text(
                    message_id=query.message_id,
                    text=f"❌ Error rejecting issue #{issue_num}",
                )
                return
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=(
                    f"❌ Implementation rejected for issue #{issue_num}\n\n"
                    f"Agent has been notified."
                ),
            )
        except Exception as exc:
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=f"❌ Error rejecting: {exc}",
            )
    elif action == "wfapprove":
        parts2 = issue_num.split("_", 1)
        real_issue = parts2[0]
        step_num = parts2[1] if len(parts2) > 1 else "?"
        await ctx.edit_message_text(
            message_id=query.message_id,
            text=f"✅ Approving workflow step {step_num} for issue #{real_issue}...",
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
                await ctx.edit_message_text(
                    message_id=query.message_id,
                    text=f"❌ No workflow found for issue #{real_issue}",
                )
                return
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=(
                    f"✅ Step {step_num} approved for issue #{real_issue}\n\n"
                    f"Workflow will continue automatically."
                ),
            )
        except Exception as exc:
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=f"❌ Error approving workflow step: {exc}",
            )
    elif action == "wfdeny":
        parts2 = issue_num.split("_", 1)
        real_issue = parts2[0]
        step_num = parts2[1] if len(parts2) > 1 else "?"
        await ctx.edit_message_text(
            message_id=query.message_id,
            text=f"❌ Denying workflow step {step_num} for issue #{real_issue}...",
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
                await ctx.edit_message_text(
                    message_id=query.message_id,
                    text=f"❌ No workflow found for issue #{real_issue}",
                )
                return
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=(
                    f"❌ Step {step_num} denied for issue #{real_issue}\n\n"
                    f"Workflow has been stopped."
                ),
            )
        except Exception as exc:
            await ctx.edit_message_text(
                message_id=query.message_id,
                text=f"❌ Error denying workflow step: {exc}",
            )
