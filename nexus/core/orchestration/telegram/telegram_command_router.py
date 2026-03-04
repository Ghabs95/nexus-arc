from collections.abc import Awaitable, Callable
from typing import Any

from nexus.core.utils.command_args import normalize_command_args


async def dispatch_command(
    *,
    update: Any,
    context: Any,
    command: str,
    project_key: str,
    issue_num: str,
    rest: list[str] | None,
    command_handler_map: Callable[[], dict[str, Callable[..., Awaitable[None]]]],
    reply_unsupported: Callable[[Any], Awaitable[None]],
) -> None:
    """Dispatch command to Telegram handlers while normalizing args."""
    context.args = normalize_command_args(command, project_key, issue_num, rest)

    handler = command_handler_map().get(command)
    if handler:
        await handler(update, context)
        return
    await reply_unsupported(update)
