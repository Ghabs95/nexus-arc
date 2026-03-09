"""Generic context for interactive chat command handlers."""

from dataclasses import dataclass
from typing import Any, Optional

from nexus.adapters.notifications.base import Button, Message
from nexus.adapters.notifications.interactive import InteractiveClientPlugin


@dataclass
class InteractiveQuery:
    action_data: str
    message_id: str


@dataclass
class InteractiveContext:
    """
    A platform-agnostic context passed to interactive handlers.
    Abstracts away telegram.Update or discord.Interaction.
    """

    client: InteractiveClientPlugin
    user_id: str
    text: str
    args: list[str]
    raw_event: Any
    user_state: dict[str, Any]
    query: Optional["InteractiveQuery"] = None
    attachments: list[Any] | None = None

    @property
    def platform(self) -> str:
        """Returns the base platform name (e.g. 'telegram', 'discord')."""
        return self.client.name.split("-")[0].lower()

    async def reply_text(self, text: str, buttons: list[list[Button]] | None = None) -> str:
        """
        Send a message back to the user in the current chat.

        Returns:
            The message ID of the sent message.
        """
        # Note: We pass flat buttons or nested buttons to Message
        # The underlying plugin needs to handle the abstraction of Button layouts.
        msg = Message(text=text, buttons=buttons)  # type: ignore
        return await self.client.send_interactive(self.user_id, msg)

    async def edit_message_text(
        self, message_id: str, text: str, buttons: list[list[Button]] | None = None
    ) -> None:
        """
        Edit a previously sent message.
        """
        msg = Message(text=text, buttons=buttons)  # type: ignore
        await self.client.edit_interactive(self.user_id, message_id, msg)

    async def answer_callback_query(self, text: str | None = None) -> None:
        """Acknowledge a callback query if applicable."""
        pass
