"""Built-in Discord Interactive Client Plugin."""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from nexus.adapters.notifications.base import Message
from nexus.adapters.notifications.interactive import InteractiveClientPlugin
from nexus.core.models import ImageAttachment
from nexus.plugins.base import PluginKind
from nexus.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

try:
    import discord
    from discord.ext import commands

    HAS_DISCORD = True
except ImportError:
    HAS_DISCORD = False


class DiscordInteractivePlugin(InteractiveClientPlugin):
    """
    Discord interactive plugin using discord.py.

    This plugin manages a discord Bot connection, handles incoming
    commands and messages, and dispatches them to registered callbacks.
    """

    def __init__(self, config: dict[str, Any]):
        if not HAS_DISCORD:
            raise ImportError(
                "discord.py is required for DiscordInteractivePlugin. "
                "Install it with: pip install nexus-arc[discord]"
            )

        self.bot_token = config.get("bot_token", "")
        self.command_prefix = config.get("command_prefix", "!")

        self.command_handlers: dict[str, Callable] = {}
        self.message_handler: Callable | None = None

        # Configure intents
        intents = discord.Intents.default()
        intents.message_content = True  # Required to read text messages

        # We use a commands.Bot to handle prefix commands and raw messages easily
        self._bot = commands.Bot(command_prefix=self.command_prefix, intents=intents)
        self._bot_task: asyncio.Task | None = None

    @property
    def name(self) -> str:
        return "discord-interactive-http"

    async def start(self) -> None:
        """Begin listening for events from Discord."""
        if not self.bot_token:
            logger.error("No bot_token provided for DiscordInteractivePlugin.")
            return

        logger.info("Starting Discord Interactive Client...")

        # Register commands
        for command, callback in self.command_handlers.items():
            self._register_discord_command(command, callback)

        # Register message event listener
        if self.message_handler:
            self._register_discord_message_listener()

        # Start bot in a background task
        async def _bot_runner():
            try:
                await self._bot.start(self.bot_token)
            except asyncio.CancelledError:
                logger.info("Discord bot task cancelled.")
            except Exception as e:
                logger.error(f"Discord bot exited with error: {e}")

        self._bot_task = asyncio.create_task(_bot_runner())

        # Wait until bot is ready
        try:
            await self._bot.wait_until_ready()
            logger.info(f"Discord bot logged in as {self._bot.user}")
        except Exception as e:
            logger.error(f"Failed to wait for Discord bot to be ready: {e}")

    def _register_discord_command(self, command: str, callback: Callable) -> None:
        # discord.py commands don't include the prefix in the name registration
        cmd_name = command.lstrip(self.command_prefix).lstrip("/")

        async def _cmd_wrapper(ctx: commands.Context, *args):
            user_id = str(ctx.author.id)
            # Reconstruct the message text (or just everything after the command)
            text = ctx.message.content
            # Pass to framework core callback
            try:
                await callback(user_id=user_id, text=text, context=list(args), raw_event=ctx)
            except Exception as e:
                logger.error(f"Error executing Discord command {command}: {e}", exc_info=True)

        # Register the command with the bot
        cmd = commands.Command(_cmd_wrapper, name=cmd_name)
        self._bot.add_command(cmd)

    def _register_discord_message_listener(self) -> None:
        async def on_message(message: discord.Message):
            # Ignore self
            if message.author == self._bot.user:
                return

            # Allow commands.Bot to process commands first
            ctx = await self._bot.get_context(message)
            if ctx.valid:
                # Let commands framework handle this
                return

            text = message.content or ""

            attachments: list[ImageAttachment] = []
            if message.attachments:
                for attachment in message.attachments:
                    if attachment.content_type and attachment.content_type.startswith("image/"):
                        attachments.append(
                            ImageAttachment(
                                file_id=str(attachment.id),
                                filename=attachment.filename or "image",
                                mime_type=attachment.content_type or "image/*",
                            )
                        )

            if self.message_handler:
                try:
                    await self.message_handler(
                        user_id=str(message.author.id),
                        text=text,
                        raw_event=message,
                        attachments=attachments,
                    )
                except Exception as e:
                    logger.error(f"Error in Discord message handler: {e}", exc_info=True)

        self._bot.add_listener(on_message, "on_message")

    async def stop(self) -> None:
        """Gracefully shutdown the Discord client."""
        logger.info("Stopping Discord Interactive Client...")
        await self._bot.close()

        task = self._bot_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        logger.info("Discord client stopped.")

    def register_command(self, command: str, callback: Callable) -> None:
        """Queue a command to be registered."""
        self.command_handlers[command] = callback

    def register_message_handler(self, callback: Callable) -> None:
        """Register the fall-through message handler."""
        self.message_handler = callback

    async def send_interactive(self, user_id: str, message: Message) -> str:
        """Send a message to a user, potentially with interactive buttons."""
        logger.debug(f"Sending interactive message to {user_id}")

        try:
            uid = int(user_id)
        except ValueError:
            logger.error(f"Invalid discord user_id {user_id}")
            return ""

        user = self._bot.get_user(uid)
        if not user:
            try:
                user = await self._bot.fetch_user(uid)
            except discord.NotFound:
                logger.error(f"Discord user {uid} not found.")
                return ""
            except discord.HTTPException as e:
                logger.error(f"Discord HTTP exception fetching {uid}: {e}")
                return ""

        view = discord.ui.View(timeout=None)

        if message.interactive_actions:
            for action in message.interactive_actions:
                style = discord.ButtonStyle.primary
                if action.style == "danger":
                    style = discord.ButtonStyle.danger
                elif action.style == "secondary":
                    style = discord.ButtonStyle.secondary
                elif action.style == "success":
                    style = discord.ButtonStyle.success

                button = discord.ui.Button(
                    label=action.label, style=style, custom_id=action.action_id
                )
                view.add_item(button)

        try:
            sent_msg = await user.send(content=message.text, view=view)
            return str(sent_msg.id)
        except Exception as e:
            logger.error(f"Failed to send direct message to {user_id}: {e}")
            return ""

    async def edit_interactive(self, user_id: str, message_id: str, message: Message) -> None:
        """Edit a previously sent message."""
        logger.debug(f"Editing interactive message {message_id} for user {user_id}")

        try:
            uid = int(user_id)
            mid = int(message_id)
        except ValueError:
            logger.error(f"Invalid discord user_id {user_id} or message_id {message_id}")
            return

        user = self._bot.get_user(uid)
        if not user:
            try:
                user = await self._bot.fetch_user(uid)
            except Exception as e:
                logger.error(f"Discord user {uid} not found: {e}")
                return

        view = discord.ui.View(timeout=None)

        if message.interactive_actions:
            for action in message.interactive_actions:
                style = discord.ButtonStyle.primary
                if action.style == "danger":
                    style = discord.ButtonStyle.danger
                elif action.style == "secondary":
                    style = discord.ButtonStyle.secondary
                elif action.style == "success":
                    style = discord.ButtonStyle.success

                button = discord.ui.Button(
                    label=action.label, style=style, custom_id=action.action_id
                )
                view.add_item(button)

        try:
            # We need to fetch the channel logic
            channel = user.dm_channel
            if not channel:
                channel = await user.create_dm()

            msg = await channel.fetch_message(mid)
            await msg.edit(content=message.text, view=view)
        except Exception as e:
            logger.error(
                f"Failed to edit message {message_id} for user {user_id}: {e}", exc_info=True
            )


def register_plugins(registry: PluginRegistry) -> None:
    """Register Discord interactive plugin components."""
    registry.register_factory(
        kind=PluginKind.INTERACTIVE_CLIENT,
        name="discord-interactive-http",
        version="1.0.0",
        factory=lambda config: DiscordInteractivePlugin(config),
    )
