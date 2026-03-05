from typing import Any, Callable


async def handle_rename_chat(
    *,
    update: Any,
    context: Any,
    allowed_user_ids: set[int] | list[int] | tuple[int, ...] | None,
    get_active_chat: Callable[[int], Any],
    rename_chat: Callable[[int, Any, str], Any],
) -> None:
    if allowed_user_ids and update.effective_user.id not in allowed_user_ids:
        return

    user_id = update.effective_user.id
    active_chat_id = get_active_chat(user_id)
    if not active_chat_id:
        await update.message.reply_text(
            "⚠️ No active chat found. Use /chat to create or select one."
        )
        return

    new_name = " ".join(context.args).strip()
    if not new_name:
        await update.message.reply_text("⚠️ Usage: `/rename <new name>`", parse_mode="Markdown")
        return

    rename_chat(user_id, active_chat_id, new_name)
    await update.message.reply_text(
        f"✅ Active chat renamed to: *{new_name}*", parse_mode="Markdown"
    )


async def call_core_chat_wrapper(
    *, update: Any, context: Any, call_core_chat_handler, handler
) -> None:
    await call_core_chat_handler(update, context, handler)
