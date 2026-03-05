"""Watch command handler for live workflow Telegram updates."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from nexus.core.utils.log_utils import log_unauthorized_access

if TYPE_CHECKING:
    from nexus.core.interactive.context import InteractiveContext


@dataclass
class WatchHandlerDeps:
    logger: logging.Logger
    allowed_user_ids: list[int]
    prompt_project_selection: Callable[..., Awaitable[None]]
    ensure_project_issue: Callable[..., Awaitable[tuple[str | None, str | None, list[str]]]]
    get_watch_service: Callable[[], Any]


async def watch_handler(
    ctx: InteractiveContext,
    deps: WatchHandlerDeps,
) -> None:
    """Handle `/watch` commands for live workflow updates."""
    deps.logger.info("Watch requested by user: %s", ctx.user_id)
    if deps.allowed_user_ids and int(ctx.user_id) not in deps.allowed_user_ids:
        log_unauthorized_access(deps.logger, ctx.user_id)
        return

    service = deps.get_watch_service()
    if not service.is_enabled():
        await ctx.reply_text("⚠️ `/watch` is disabled by `NEXUS_TELEGRAM_WATCH_ENABLED`.")
        return

    if not ctx.args:
        await deps.prompt_project_selection(ctx, "watch")
        return

    subcommand = str(ctx.args[0]).strip().lower()
    chat_id = int(ctx.chat_id)
    user_id = int(ctx.user_id)

    if subcommand == "status":
        status = service.get_status(chat_id=chat_id, user_id=user_id)
        if not status:
            await ctx.reply_text("ℹ️ No active `/watch` session for this chat.")
            return
        mermaid = "on" if status.get("mermaid_enabled") else "off"
        await ctx.reply_text(
            "👀 Active watch\n"
            f"- project: `{status.get('project_key', 'n/a')}`\n"
            f"- issue: `#{status.get('issue_num', 'n/a')}`\n"
            f"- mermaid: `{mermaid}`"
        )
        return

    if subcommand == "stop":
        original_args = list(ctx.args)
        project_key = None
        issue_num = None
        if len(original_args) > 1:
            ctx.args = original_args[1:]
            project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "watch")
            ctx.args = original_args
            if not project_key:
                return
        stopped = service.stop_watch(
            chat_id=chat_id,
            user_id=user_id,
            project_key=project_key,
            issue_num=issue_num,
        )
        if stopped:
            await ctx.reply_text("⏹️ Stopped workflow watch.")
        else:
            await ctx.reply_text("ℹ️ No matching `/watch` session to stop.")
        return

    if subcommand == "mermaid":
        if len(ctx.args) < 2:
            await ctx.reply_text("Usage: `/watch mermaid on|off`")
            return
        mode = str(ctx.args[1]).strip().lower()
        if mode not in {"on", "off"}:
            await ctx.reply_text("Usage: `/watch mermaid on|off`")
            return
        changed = service.set_mermaid(chat_id=chat_id, user_id=user_id, enabled=(mode == "on"))
        if not changed:
            await ctx.reply_text(
                "ℹ️ No active `/watch` session. Start with `/watch <project> <issue#>`."
            )
            return
        await ctx.reply_text(f"🧭 Mermaid updates are now `{mode}`.")
        return

    project_key, issue_num, _ = await deps.ensure_project_issue(ctx, "watch")
    if not project_key:
        return

    result = service.start_watch(
        chat_id=chat_id,
        user_id=user_id,
        project_key=project_key,
        issue_num=issue_num,
    )
    replaced = bool(result.get("replaced"))
    prefix = "🔄 Updated" if replaced else "👀 Started"
    await ctx.reply_text(
        f"{prefix} live watch for `{project_key}` issue `#{issue_num}`.\n"
        "Use `/watch status` to inspect or `/watch stop` to unsubscribe."
    )
