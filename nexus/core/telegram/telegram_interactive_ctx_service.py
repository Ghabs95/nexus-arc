async def ctx_call_telegram_handler(*, ctx, handler, ctx_telegram_runtime) -> None:
    update, context = ctx_telegram_runtime(ctx)
    context.args = list(ctx.args or [])
    await handler(update, context)


def ctx_telegram_runtime(ctx):
    update = getattr(ctx, "raw_event", None)
    context = getattr(ctx, "telegram_context", None)
    if update is None or context is None:
        raise RuntimeError("Missing Telegram runtime in interactive context")
    return update, context


async def ctx_prompt_issue_selection(
    *,
    ctx,
    command: str,
    project_key: str,
    prompt_issue_selection,
    ctx_telegram_runtime,
    edit_message: bool = False,
    issue_state: str = "open",
) -> None:
    update, context = ctx_telegram_runtime(ctx)
    await prompt_issue_selection(
        update,
        context,
        command,
        project_key,
        edit_message=edit_message,
        issue_state=issue_state,
    )


async def ctx_prompt_project_selection(
    *,
    ctx,
    command: str,
    prompt_project_selection,
    ctx_telegram_runtime,
) -> None:
    update, context = ctx_telegram_runtime(ctx)
    await prompt_project_selection(update, context, command)


async def ctx_ensure_project_issue(
    *,
    ctx,
    command: str,
    ensure_project_issue,
    ctx_telegram_runtime,
) -> tuple[str | None, str | None, list[str]]:
    update, context = ctx_telegram_runtime(ctx)
    context.args = list(getattr(ctx, "args", []) or [])
    return await ensure_project_issue(update, context, command)


async def ctx_ensure_project(
    *,
    ctx,
    command: str,
    get_single_project_key,
    normalize_project_key,
    iter_project_keys,
    ctx_prompt_project_selection,
) -> str | None:
    args = list(getattr(ctx, "args", []) or [])
    if not args:
        single_project = get_single_project_key()
        if single_project:
            return single_project
        await ctx_prompt_project_selection(ctx, command)
        return None
    candidate = normalize_project_key(str(args[0]))
    if candidate in iter_project_keys():
        return candidate
    await ctx.reply_text(f"❌ Unknown project '{args[0]}'.")
    return None


async def ctx_dispatch_command(
    *,
    ctx,
    command: str,
    project_key: str,
    issue_num: str,
    dispatch_command,
    ctx_telegram_runtime,
    rest: list[str] | None = None,
) -> None:
    update, context = ctx_telegram_runtime(ctx)
    await dispatch_command(update, context, command, project_key, issue_num, rest)
