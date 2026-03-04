import logging
import os
from typing import Any, Awaitable, Callable


async def handle_task_confirmation_callback(
    *,
    update: Any,
    context: Any,
    allowed_user_ids: set[int] | list[int] | tuple[int, ...] | None,
    logger: logging.Logger,
    route_task_with_context: Callable[..., Awaitable[dict[str, Any]]],
    orchestrator: Any,
    get_chat: Callable[..., Any],
    process_inbox_task: Callable[..., Awaitable[Any]],
    requester_context_builder: Callable[[Any], dict[str, str]] | None = None,
    authorize_project: Callable[[str, dict[str, Any] | None], tuple[bool, str]] | None = None,
) -> None:
    query = getattr(update, "callback_query", None)
    if not query:
        return
    await query.answer()

    effective_user = getattr(update, "effective_user", None)
    user_id = getattr(effective_user, "id", None)
    if allowed_user_ids and user_id not in allowed_user_ids:
        logger.warning("Unauthorized callback access attempt by ID: %s", user_id)
        return

    data = query.data or ""
    pending = context.user_data.get("pending_task_confirmation")
    if not pending:
        await query.edit_message_text("⚠️ Task confirmation expired. Send the request again.")
        return

    if data == "taskconfirm:cancel":
        context.user_data.pop("pending_task_confirmation", None)
        context.user_data.pop("pending_task_edit", None)
        await query.edit_message_text("❎ Task creation canceled.")
        return

    if data == "taskconfirm:edit":
        context.user_data["pending_task_edit"] = True
        await query.edit_message_text(
            "✏️ Send the updated task text now.\n\n"
            "I will show the confirmation preview again before creating anything.\n"
            "Type `cancel` to abort."
        )
        return

    if data != "taskconfirm:confirm":
        await query.edit_message_text("⚠️ Unknown confirmation action.")
        return

    text = str(pending.get("text") or "").strip()
    message_id = str(
        pending.get("message_id") or getattr(getattr(query, "message", None), "message_id", "")
    )
    context.user_data.pop("pending_task_confirmation", None)

    requester_context = (
        requester_context_builder(effective_user) if callable(requester_context_builder) else None
    )
    result = await route_task_with_context(
        user_id=user_id,
        text=text,
        orchestrator=orchestrator,
        message_id=message_id,
        get_chat=get_chat,
        process_inbox_task=process_inbox_task,
        requester_context=requester_context,
        authorize_project=authorize_project,
    )
    if not result.get("success") and "pending_resolution" in result:
        context.user_data["pending_task_project_resolution"] = result["pending_resolution"]

    await query.edit_message_text(result.get("message", "⚠️ Task processing completed."))


async def handle_save_task_selection(
    *,
    update: Any,
    context: Any,
    logger: logging.Logger,
    orchestrator: Any,
    projects: dict[str, str],
    types_map: dict[str, str],
    project_config: dict[str, dict],
    base_dir: str,
    get_inbox_storage_backend: Callable[[], str],
    enqueue_task: Callable[..., int],
    get_inbox_dir: Callable[[str, str], str],
    transcribe_voice_message: Callable[[str, Any], Awaitable[str | None]],
    conversation_end: Any,
    requester_context_builder: Callable[[Any], dict[str, str]] | None = None,
    authorize_project: Callable[[str, dict[str, Any] | None], tuple[bool, str]] | None = None,
) -> Any:
    project = context.user_data["project"]
    task_type = context.user_data["type"]

    logger.info(
        "Selection task received: user=%s message_id=%s project=%s type=%s has_voice=%s",
        update.effective_user.id,
        update.message.message_id if update.message else None,
        project,
        task_type,
        bool(update.message and update.message.voice),
    )

    if update.message.voice:
        msg = await update.message.reply_text("🎧 Transcribing (CLI)...")
        text = await transcribe_voice_message(update.message.voice.file_id, context)
        await context.bot.delete_message(
            chat_id=update.effective_chat.id, message_id=msg.message_id
        )
    else:
        text = update.message.text

    if not text:
        await update.message.reply_text("⚠️ Transcription failed. Please try again.")
        return conversation_end

    if str(text).strip().lower() in {"cancel", "/cancel"}:
        await update.message.reply_text("❌ Cancelled.")
        return conversation_end

    # /new capture should be lightweight: store/queue raw user text.
    # Refinement and name generation are handled once in the processor path.
    refined_text = text
    task_name = ""

    workspace = project
    if project in project_config:
        workspace = project_config[project].get("workspace", project)

    requester_context = (
        requester_context_builder(update.effective_user) if callable(requester_context_builder) else {}
    )
    if not isinstance(requester_context, dict):
        requester_context = {}
    if callable(authorize_project):
        allowed, error_message = authorize_project(str(project), requester_context)
        if not allowed:
            await update.message.reply_text(error_message or "🔒 Unauthorized project access.")
            return conversation_end

    filename = f"{task_type}_{update.message.message_id}.md"
    task_name_line = f"**Task Name:** {task_name}\n" if task_name else ""
    requester_lines: list[str] = []
    requester_nexus_id = str(requester_context.get("nexus_id") or "").strip()
    requester_platform = str(requester_context.get("platform") or "").strip()
    requester_platform_user_id = str(requester_context.get("platform_user_id") or "").strip()
    if requester_nexus_id:
        requester_lines.append(f"**Requester Nexus ID:** `{requester_nexus_id}`")
    if requester_platform:
        requester_lines.append(f"**Requester Platform:** {requester_platform}")
    if requester_platform_user_id:
        requester_lines.append(f"**Requester Platform User ID:** `{requester_platform_user_id}`")
    requester_block = ("\n" + "\n".join(requester_lines)) if requester_lines else ""
    markdown_content = (
        f"# {types_map[task_type]}\n**Project:** {projects[project]}\n**Type:** {task_type}\n"
        f"{task_name_line}**Status:** Pending\n\n"
        f"{refined_text}\n\n"
        f"{requester_block}\n"
        f"---\n"
        f"**Source:** inbox\n"
        f"---\n"
        f"**Raw Input:**\n{text}"
    )

    inbox_backend = str(get_inbox_storage_backend() or "").strip().lower()
    if inbox_backend == "postgres":
        queue_id = enqueue_task(
            project_key=str(project),
            workspace=str(workspace),
            filename=filename,
            markdown_content=markdown_content,
        )
        await update.message.reply_text(
            "✅ Queued to inbox\n"
            f"Project: {project}\n"
            f"Queue ID: {queue_id}\n\n"
            "Issue number is assigned later by the processor when it creates the remote issue."
        )
    else:
        target_dir = get_inbox_dir(os.path.join(base_dir, workspace), project)
        os.makedirs(target_dir, exist_ok=True)
        file_path = os.path.join(target_dir, filename)
        with open(file_path, "w") as f:
            f.write(markdown_content)

        await update.message.reply_text(
            "✅ Saved to inbox\n"
            f"Project: {project}\n"
            f"File: {filename}\n\n"
            "Issue number is assigned later by the processor when it creates the remote issue."
        )
    return conversation_end
