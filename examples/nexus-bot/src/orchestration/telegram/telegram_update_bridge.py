import logging
import re
from types import SimpleNamespace
from typing import Any

from nexus.core.utils.message_formatting import (
    clip_message_text,
    flatten_markdown_table,
    normalize_markdown_headers,
)

logger = logging.getLogger(__name__)
TELEGRAM_TEXT_LIMIT = 4096


def buttons_to_reply_markup(buttons, inline_keyboard_button_cls, inline_keyboard_markup_cls):
    """Convert abstract buttons into Telegram InlineKeyboardMarkup."""
    if not buttons:
        return None

    keyboard = []
    for row in buttons:
        keyboard_row = []
        for btn in row:
            label = getattr(btn, "label", "")
            callback_data = getattr(btn, "callback_data", None)
            url = getattr(btn, "url", None)
            if url:
                keyboard_row.append(inline_keyboard_button_cls(label, url=url))
            else:
                keyboard_row.append(
                    inline_keyboard_button_cls(label, callback_data=callback_data or "")
                )
        if keyboard_row:
            keyboard.append(keyboard_row)
    return inline_keyboard_markup_cls(keyboard) if keyboard else None


def _normalize_telegram_markdown(text: str, parse_mode: str | None) -> str:
    """Normalize common GFM markdown into Telegram legacy Markdown."""
    if parse_mode != "Markdown" or not text:
        return text

    text = flatten_markdown_table(text)
    text = normalize_markdown_headers(text)

    # Convert GFM bold/italic to Telegram legacy single-asterisk style
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)
    text = re.sub(r"__(.+?)__", r"*\1*", text)
    return text


def _clip_telegram_text(text: str, limit: int = TELEGRAM_TEXT_LIMIT) -> str:
    """Ensure outgoing Telegram text respects message length limits."""
    return clip_message_text(text, limit)


def _is_parse_entity_error(exc: Exception) -> bool:
    return "Can't parse entities" in str(exc)


async def _reply_with_parse_fallback(
    effective_message: Any,
    text: str,
    *,
    reply_markup: Any,
    parse_mode: str | None,
    disable_web_page_preview: bool,
):
    normalized_text = _clip_telegram_text(_normalize_telegram_markdown(text, parse_mode))
    try:
        return await effective_message.reply_text(
            normalized_text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as exc:
        if parse_mode and _is_parse_entity_error(exc):
            logger.warning("Retrying reply_text without parse_mode due to Telegram entity parsing")
            return await effective_message.reply_text(
                normalized_text,
                reply_markup=reply_markup,
                parse_mode=None,
                disable_web_page_preview=disable_web_page_preview,
            )
        raise


async def _edit_with_parse_fallback(
    callable_edit,
    *,
    text: str,
    reply_markup: Any,
    parse_mode: str | None,
    disable_web_page_preview: bool,
):
    normalized_text = _clip_telegram_text(_normalize_telegram_markdown(text, parse_mode))
    try:
        return await callable_edit(
            text=normalized_text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
            disable_web_page_preview=disable_web_page_preview,
        )
    except Exception as exc:
        if parse_mode and _is_parse_entity_error(exc):
            logger.warning(
                "Retrying edit_message_text without parse_mode due to Telegram entity parsing"
            )
            return await callable_edit(
                text=normalized_text,
                reply_markup=reply_markup,
                parse_mode=None,
                disable_web_page_preview=disable_web_page_preview,
            )
        raise


def build_telegram_interactive_ctx(
    update: Any,
    context: Any,
    *,
    buttons_to_reply_markup_fn,
):
    """Build the interactive context wrapper used by core Telegram handlers."""
    query_obj = update.callback_query
    effective_message = update.effective_message

    class _TelegramInteractiveCtx:
        def __init__(self):
            self.user_id = str(getattr(getattr(update, "effective_user", None), "id", ""))
            self.chat_id = int(getattr(getattr(update, "effective_chat", None), "id", 0) or 0)
            self.text = str(getattr(effective_message, "text", "") or "")
            self.args = list(getattr(context, "args", []) or [])
            self.raw_event = update
            self.telegram_context = context
            self.user_state = getattr(context, "user_data", {})
            self.client = SimpleNamespace(name="telegram")
            self.query = (
                SimpleNamespace(
                    data=str(getattr(query_obj, "data", "") or ""),
                    action_data=str(getattr(query_obj, "data", "") or ""),
                    message_id=str(getattr(getattr(query_obj, "message", None), "message_id", "")),
                )
                if query_obj is not None
                else None
            )

        async def reply_text(
            self,
            text: str,
            buttons=None,
            parse_mode: str | None = "Markdown",
            disable_web_page_preview: bool = True,
        ) -> str:
            reply_markup = buttons_to_reply_markup_fn(buttons)
            msg = await _reply_with_parse_fallback(
                effective_message,
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )
            return str(getattr(msg, "message_id", ""))

        async def edit_message_text(
            self,
            text: str,
            message_id: str | None = None,
            buttons=None,
            parse_mode: str | None = "Markdown",
            disable_web_page_preview: bool = True,
        ) -> None:
            reply_markup = buttons_to_reply_markup_fn(buttons)
            if query_obj is not None and hasattr(query_obj, "edit_message_text"):
                await _edit_with_parse_fallback(
                    query_obj.edit_message_text,
                    text=text,
                    reply_markup=reply_markup,
                    parse_mode=parse_mode,
                    disable_web_page_preview=disable_web_page_preview,
                )
                return

            target_message_id = message_id
            if target_message_id is None and effective_message is not None:
                target_message_id = str(getattr(effective_message, "message_id", ""))
            if isinstance(target_message_id, str) and target_message_id.isdigit():
                target_message_id = int(target_message_id)

            async def _edit_via_bot(**kwargs):
                return await context.bot.edit_message_text(
                    chat_id=getattr(getattr(update, "effective_chat", None), "id", None),
                    message_id=target_message_id,
                    **kwargs,
                )

            await _edit_with_parse_fallback(
                _edit_via_bot,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
                disable_web_page_preview=disable_web_page_preview,
            )

        async def answer_callback_query(self, text: str | None = None) -> None:
            if query_obj is not None and hasattr(query_obj, "answer"):
                await query_obj.answer(text=text)

    return _TelegramInteractiveCtx()
