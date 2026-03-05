"""Telegram callback/chat wrapper routing extracted from telegram_bot."""

from typing import Any


async def call_core_callback_handler(
    update: Any, context: Any, handler, *, build_ctx, deps_factory
) -> None:
    """Invoke a core callback handler with Telegram interactive context and deps."""
    await handler(build_ctx(update, context), deps_factory())


async def call_core_chat_handler(update: Any, context: Any, handler, *, build_ctx) -> None:
    """Invoke a core chat handler with Telegram interactive context."""
    await handler(build_ctx(update, context))


async def handle_named_core_callback(
    *,
    update: Any,
    context: Any,
    handler,
    build_ctx,
    deps_factory,
) -> None:
    """Thin adapter used by Telegram callback entrypoints."""
    await call_core_callback_handler(
        update,
        context,
        handler,
        build_ctx=build_ctx,
        deps_factory=deps_factory,
    )
