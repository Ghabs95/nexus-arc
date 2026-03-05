from __future__ import annotations

from collections.abc import Awaitable, Callable

CallbackHandler = Callable[[], Awaitable[None]]


async def dispatch_callback_action(
    *,
    action: str,
    handlers: dict[str, CallbackHandler],
    default_handler: CallbackHandler | None = None,
) -> bool:
    """Dispatch a callback action to a registered async handler.

    Returns ``True`` when an action was handled, ``False`` when no handler matched
    and no default handler was provided.
    """
    handler = handlers.get(action)
    if handler is not None:
        await handler()
        return True
    if default_handler is not None:
        await default_handler()
        return True
    return False
