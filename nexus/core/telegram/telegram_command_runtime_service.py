from telegram import Update


def rate_limited(*, rate_limiter, logger, action: str, limit=None):
    def decorator(func):
        async def wrapper(update: Update, context):
            user_id = update.effective_user.id
            allowed, error_msg = rate_limiter.check_limit(user_id, action, limit)
            if not allowed:
                await update.message.reply_text(error_msg)
                logger.warning(f"Rate limit blocked: user={user_id}, action={action}")
                return
            rate_limiter.record_request(user_id, action)
            return await func(update, context)

        return wrapper

    return decorator


async def handle_progress_command(
    *, update, logger, allowed_user_ids, load_launched_agents, time_module
):
    logger.info(f"Progress requested by user: {update.effective_user.id}")
    if allowed_user_ids and update.effective_user.id not in allowed_user_ids:
        logger.warning(f"Unauthorized access attempt by ID: {update.effective_user.id}")
        return

    launched_agents = load_launched_agents()
    if not launched_agents:
        await update.effective_message.reply_text("ℹ️ No active agents tracked.")
        return

    now = time_module.time()
    lines = ["📊 *Agent Progress*\n"]
    for issue_num, info in sorted(launched_agents.items(), key=lambda x: x[0]):
        if not isinstance(info, dict):
            continue
        agent_type = info.get("agent_type", "unknown")
        tool = info.get("tool", "unknown")
        tier = info.get("tier", "unknown")
        ts = info.get("timestamp", 0)
        exclude = info.get("exclude_tools", [])
        elapsed = int(now - ts) if ts else 0
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        duration_str = f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"
        line = (
            f"• Issue *#{issue_num}* — `{agent_type}` via `{tool}`\n"
            f"  Tier: `{tier}` | Running: `{duration_str}`"
        )
        if exclude:
            line += f"\n  Excluded tools: `{', '.join(exclude)}`"
        lines.append(line)

    await update.effective_message.reply_text("\n".join(lines), parse_mode="Markdown")
