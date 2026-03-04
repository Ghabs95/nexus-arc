import contextlib
import logging
from typing import Any, Awaitable, Callable


async def handle_hands_free_message(
    *,
    update: Any,
    context: Any,
    logger: logging.Logger,
    allowed_user_ids: set[int] | list[int] | tuple[int, ...] | None,
    get_active_chat: Callable[[int], Any],
    rename_chat: Callable[[int, Any, str], Any],
    chat_menu_handler: Callable[[Any, Any], Awaitable[Any]],
    handle_pending_issue_input: Callable[[Any, Any], Awaitable[bool]],
    transcribe_voice_message: Callable[[str, Any], Awaitable[str | None]],
    inline_keyboard_button_cls: Any,
    inline_keyboard_markup_cls: Any,
    resolve_pending_project_selection: Callable[[Any, Any], Awaitable[bool]],
    build_ctx: Callable[[Any, Any], Any],
    hands_free_routing_deps_factory: Callable[[], Any],
    get_chat: Callable[[int], Any],
    handle_feature_ideation_request: Callable[..., Awaitable[bool]],
    feature_ideation_deps_factory: Callable[[], Any],
    route_hands_free_text: Callable[[Any, Any], Awaitable[Any]],
) -> None:
    try:
        logger.info(
            "Hands-free task received: user=%s message_id=%s has_voice=%s has_text=%s",
            update.effective_user.id,
            update.message.message_id if update.message else None,
            bool(update.message and update.message.voice),
            bool(update.message and update.message.text),
        )
        if allowed_user_ids and update.effective_user.id not in allowed_user_ids:
            logger.warning("Unauthorized access attempt by ID: %s", update.effective_user.id)
            return

        if context.user_data.get("pending_chat_rename"):
            if update.message.voice:
                await update.message.reply_text(
                    "⚠️ Please send the new chat name as text (or type `cancel`)."
                )
                return

            candidate = (update.message.text or "").strip()
            if not candidate:
                await update.message.reply_text(
                    "⚠️ Chat name cannot be empty. Send a name or type `cancel`."
                )
                return

            if candidate.lower() in {"cancel", "/cancel"}:
                context.user_data.pop("pending_chat_rename", None)
                await update.message.reply_text("❎ Rename canceled.")
                return

            user_id = update.effective_user.id
            active_chat_id = get_active_chat(user_id)
            if not active_chat_id:
                context.user_data.pop("pending_chat_rename", None)
                await update.message.reply_text(
                    "⚠️ No active chat found. Use /chat to create or select one."
                )
                return

            renamed = rename_chat(user_id, active_chat_id, candidate)
            context.user_data.pop("pending_chat_rename", None)
            if not renamed:
                await update.message.reply_text(
                    "⚠️ Could not rename the active chat. Please try again."
                )
                return

            await update.message.reply_text(
                f"✅ Active chat renamed to: *{candidate}*",
                parse_mode="Markdown",
            )
            await chat_menu_handler(update, context)
            return

        if (not update.message.voice) and await handle_pending_issue_input(update, context):
            return

        if context.user_data.get("pending_task_edit"):
            if not update.message.voice:
                candidate = (update.message.text or "").strip().lower()
                if candidate in {"cancel", "/cancel"}:
                    context.user_data.pop("pending_task_edit", None)
                    context.user_data.pop("pending_task_confirmation", None)
                    await update.message.reply_text("❎ Task edit canceled.")
                    return

            if update.message.voice:
                msg = await update.message.reply_text("🎧 Transcribing your edited task...")
                revised_text = await transcribe_voice_message(update.message.voice.file_id, context)
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id, message_id=msg.message_id
                )
            else:
                revised_text = (update.message.text or "").strip()

            if not revised_text:
                await update.message.reply_text(
                    "⚠️ I couldn't read the edited task text. Please try again."
                )
                return

            context.user_data["pending_task_edit"] = False
            context.user_data["pending_task_confirmation"] = {
                "text": revised_text,
                "message_id": str(update.message.message_id),
            }
            preview = revised_text if len(revised_text) <= 300 else f"{revised_text[:300]}..."
            keyboard = inline_keyboard_markup_cls(
                [
                    [inline_keyboard_button_cls("✅ Confirm", callback_data="taskconfirm:confirm")],
                    [inline_keyboard_button_cls("✏️ Edit", callback_data="taskconfirm:edit")],
                    [inline_keyboard_button_cls("❌ Cancel", callback_data="taskconfirm:cancel")],
                ]
            )
            await update.message.reply_text(
                "🛡️ *Confirm task creation*\n\n" "Updated request preview:\n\n" f"_{preview}_",
                reply_markup=keyboard,
                parse_mode="Markdown",
            )
            return

        if update.message.text and update.message.text.startswith("/"):
            logger.info("Ignoring command in hands_free_handler: %s", update.message.text)
            return

        if await resolve_pending_project_selection(
            build_ctx(update, context), hands_free_routing_deps_factory()
        ):
            return

        text = ""

        if update.message.voice:
            logger.info("Processing voice message...")
            text = await transcribe_voice_message(update.message.voice.file_id, context)
            if not text:
                logger.warning("Voice transcription returned empty text")
                await update.message.reply_text("⚠️ Transcription failed")
                return
        else:
            logger.info("Processing text input... text=%s", (update.message.text or "")[:50])
            text = update.message.text
        interactive_ctx = build_ctx(update, context)
        interactive_ctx.text = text
        await route_hands_free_text(interactive_ctx, hands_free_routing_deps_factory())
    except Exception as exc:
        logger.error("Unexpected error in hands_free_handler: %s", exc, exc_info=True)
        with contextlib.suppress(Exception):
            await update.message.reply_text(f"❌ Error: {str(exc)[:100]}")
