from typing import Any


async def start_selection_flow(
    *,
    update: Any,
    allowed_user_ids: set[int] | list[int] | tuple[int, ...] | None,
    projects: dict[str, str],
    inline_keyboard_button_cls,
    inline_keyboard_markup_cls,
    select_project_state: Any,
) -> Any:
    if allowed_user_ids and update.effective_user.id not in allowed_user_ids:
        return None
    keyboard = [
        [inline_keyboard_button_cls(name, callback_data=code)] for code, name in projects.items()
    ]
    keyboard.append([inline_keyboard_button_cls("❌ Close", callback_data="flow:close")])
    await update.message.reply_text(
        "📂 **Select Project:**",
        reply_markup=inline_keyboard_markup_cls(keyboard),
        parse_mode="Markdown",
    )
    return select_project_state


async def project_selected_flow(
    *,
    update: Any,
    context: Any,
    projects: dict[str, str],
    types_map: dict[str, str],
    inline_keyboard_button_cls,
    inline_keyboard_markup_cls,
    select_type_state: Any,
) -> Any:
    query = update.callback_query
    await query.answer()
    context.user_data["project"] = query.data
    keyboard = [
        [inline_keyboard_button_cls(name, callback_data=code)] for code, name in types_map.items()
    ]
    keyboard.append([inline_keyboard_button_cls("❌ Close", callback_data="flow:close")])
    await query.edit_message_text(
        f"📂 Project: **{projects[query.data]}**\n\n🛠 **Select Type:**",
        reply_markup=inline_keyboard_markup_cls(keyboard),
        parse_mode="Markdown",
    )
    return select_type_state


async def type_selected_flow(*, update: Any, context: Any, input_task_state: Any) -> Any:
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    await query.edit_message_text("📝 **Speak or Type the task:**", parse_mode="Markdown")
    return input_task_state


async def cancel_selection_flow(*, update: Any, conversation_end: Any) -> Any:
    await update.message.reply_text("❌ Cancelled.")
    return conversation_end
