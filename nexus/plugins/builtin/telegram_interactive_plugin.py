"""Built-in plugin: Telegram interactive client channel."""

import logging
import os
from collections.abc import Callable
from typing import Any

from nexus.adapters.notifications.base import Message
from nexus.adapters.notifications.interactive import InteractiveClientPlugin
from nexus.core.models import ImageAttachment

logger = logging.getLogger(__name__)


try:
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
    from telegram.ext import (
        Application,
        ApplicationBuilder,
        CommandHandler,
        ContextTypes,
        MessageHandler,
        filters,
    )

    HAS_TELEGRAM = True
except ImportError:
    HAS_TELEGRAM = False
    Update = Any  # type: ignore
    ContextTypes = Any  # type: ignore
    Application = Any  # type: ignore


class TelegramInteractivePlugin(InteractiveClientPlugin):
    """Telegram interactive client using python-telegram-bot or raw API."""

    def __init__(self, config: dict[str, Any]):
        if not HAS_TELEGRAM:
            raise ImportError(
                "python-telegram-bot is required for TelegramInteractivePlugin. "
                "Install it with: pip install nexus-arc[telegram]"
            )

        self.bot_token = config.get("bot_token", "")
        self.chat_id = str(config.get("chat_id", ""))
        self.parse_mode = config.get("parse_mode", "Markdown")
        self.timeout = int(config.get("timeout", 10))

        self.command_handlers: dict[str, Callable] = {}
        self.message_handler: Callable | None = None

        self._app: Application | None = None

    @property
    def name(self) -> str:
        return "telegram-interactive-http"

    async def start(self) -> None:
        """Start polling or webhook for incoming Telegram events."""
        if not self.bot_token:
            logger.error("No bot_token provided for TelegramInteractivePlugin.")
            return

        logger.info("Starting Telegram Interactive Client...")
        self._app = ApplicationBuilder().token(self.bot_token).build()
        assert self._app is not None

        # Register all accumulated commands
        for command, callback in self.command_handlers.items():
            # Create a closure to capture the correct callback
            def create_handler(cb: Callable) -> Callable:
                async def _cmd_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                    user_id = str(update.effective_user.id) if update.effective_user else ""
                    text = update.message.text if update.message and update.message.text else ""
                    await cb(user_id=user_id, text=text, context=context.args, raw_event=update)

                return _cmd_handler

            self._app.add_handler(CommandHandler(command, create_handler(callback)))

        # Register message handler
        if self.message_handler:

            async def _msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
                user_id = str(update.effective_user.id) if update.effective_user else ""
                text = update.message.text or update.message.caption or ""
                attachments: list[ImageAttachment] = []
                if update.message and update.message.photo:
                    try:
                        photo = update.message.photo[-1]
                        file = await context.bot.get_file(photo.file_id)
                        file_path = getattr(file, "file_path", "") or ""
                        filename = os.path.basename(file_path) or "photo.jpg"
                        attachments.append(
                            ImageAttachment(
                                file_id=photo.file_id,
                                filename=filename,
                                mime_type="image/jpeg",
                            )
                        )
                    except Exception as e:
                        logger.error(f"Failed to read telegram photo: {e}")

                # Ignore empty texts or commands, unless there's an image
                if (text and not text.startswith("/")) or attachments:
                    await self.message_handler(  # type: ignore
                        user_id=user_id,
                        text=text,
                        raw_event=update,
                        attachments=attachments,
                    )

            self._app.add_handler(MessageHandler((filters.TEXT | filters.PHOTO) & (~filters.COMMAND), _msg_handler))

        await self._app.initialize()
        await self._app.start()
        if self._app.updater:
            await self._app.updater.start_polling()
        logger.info("Telegram polling started successfully.")

    async def stop(self) -> None:
        """Stop listening for Telegram events."""
        if not self._app:
            return

        logger.info("Stopping Telegram Interactive Client...")
        assert self._app is not None
        if self._app.updater and self._app.updater.running:
            await self._app.updater.stop()
        if self._app.running:
            await self._app.stop()
        await self._app.shutdown()
        logger.info("Telegram client stopped.")

    def register_command(self, command: str, callback: Callable) -> None:
        """Register a specific slash command to a callback."""
        self.command_handlers[command] = callback
        logger.debug(f"Registered Telegram command: /{command}")

    def register_message_handler(self, callback: Callable) -> None:
        """Register a handler for general text messages."""
        self.message_handler = callback
        logger.debug("Registered Telegram general message handler.")

    async def send_interactive(self, user_id: str, message: Message) -> str:
        """Send a message to a user, potentially with inline keyboards."""
        if not self._app or not self._app.bot:
            logger.error("Cannot send message: Telegram app not started.")
            return ""

        reply_markup = None
        if message.buttons:
            keyboard = []
            for btn in message.buttons:
                keyboard.append([InlineKeyboardButton(btn.label, callback_data=btn.payload)])
            reply_markup = InlineKeyboardMarkup(keyboard)

        logger.debug(f"Sending interactive message to {user_id}")
        assert self._app is not None
        sent_message = await self._app.bot.send_message(
            chat_id=user_id,
            text=message.text,
            parse_mode=self.parse_mode,
            reply_markup=reply_markup,
        )
        return str(sent_message.message_id)

    async def edit_interactive(self, user_id: str, message_id: str, message: Message) -> None:
        """Edit a previously sent message."""
        logger.debug(f"Editing interactive message {message_id} for user {user_id}")
        assert self._app is not None

        reply_markup = None
        if message.interactive_actions:
            from telegram import InlineKeyboardButton, InlineKeyboardMarkup

            keyboard = []
            for action in message.interactive_actions:
                keyboard.append(
                    [InlineKeyboardButton(action.label, callback_data=action.action_id)]
                )
            reply_markup = InlineKeyboardMarkup(keyboard)

        try:
            await self._app.bot.edit_message_text(
                chat_id=user_id,
                message_id=int(message_id),
                text=message.text,
                parse_mode=self.parse_mode,
                reply_markup=reply_markup,
            )
        except Exception as e:
            if "Message is not modified" in str(e):
                pass
            else:
                logger.error(f"Failed to edit message {message_id} for user {user_id}: {e}")


def register_plugins(registry) -> None:
    """Register built-in Telegram interactive plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INTERACTIVE_CLIENT,
        name="telegram-interactive-http",
        version="0.1.0",
        factory=lambda config: TelegramInteractivePlugin(config),
        description="Telegram Interactive Client plugin",
    )
