"""Built-in Slack Interactive Client Plugin.

Uses Slack Bolt in Socket Mode — no public HTTP endpoint required.
Consistent with the self-hosted Nexus deployment model (mirrors the
outbound-connection approach of Telegram polling and Discord gateway).

Requires the ``slack`` optional extra::

    pip install nexus-arc[slack]
"""

import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Any

from nexus.adapters.notifications.base import Message
from nexus.adapters.notifications.interactive import InteractiveClientPlugin
from nexus.plugins.base import PluginKind
from nexus.plugins.registry import PluginRegistry

logger = logging.getLogger(__name__)

try:
    from slack_bolt.async_app import AsyncApp
    from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler

    HAS_SLACK_BOLT = True
except ImportError:
    HAS_SLACK_BOLT = False

try:
    from slack_sdk.web.async_client import AsyncWebClient

    HAS_SLACK_SDK = True
except ImportError:
    HAS_SLACK_SDK = False


class SlackInteractivePlugin(InteractiveClientPlugin):
    """Slack interactive plugin using Bolt in Socket Mode.

    Manages an outbound WebSocket connection to Slack via Socket Mode.
    No public HTTP endpoint or reverse proxy is required — suitable for
    self-hosted Nexus deployments.

    Args:
        config: Must contain ``bot_token`` (xoxb-...) and ``app_token``
            (xapp-...).  ``signing_secret`` is required by Bolt for request
            verification even in Socket Mode.
    """

    def __init__(self, config: dict[str, Any]):
        if not HAS_SLACK_BOLT:
            raise ImportError(
                "slack-bolt is required for SlackInteractivePlugin. "
                "Install it with: pip install nexus-arc[slack]"
            )
        if not HAS_SLACK_SDK:
            raise ImportError(
                "slack-sdk is required for SlackInteractivePlugin. "
                "Install it with: pip install nexus-arc[slack]"
            )

        self._bot_token: str = config.get("bot_token", "")
        self._app_token: str = config.get("app_token", "")
        self._signing_secret: str = config.get("signing_secret", "")

        self._command_handlers: dict[str, Callable] = {}
        self._message_handler: Callable | None = None

        self._app = AsyncApp(token=self._bot_token, signing_secret=self._signing_secret)
        self._handler: AsyncSocketModeHandler | None = None
        self._handler_task: asyncio.Task | None = None

        # Web client for sending/editing messages
        self._web_client = AsyncWebClient(token=self._bot_token)

    @property
    def name(self) -> str:
        return "slack-interactive"

    # -- Lifecycle ---------------------------------------------------------

    async def start(self) -> None:
        """Establish the Socket Mode connection and begin handling events."""
        if not self._bot_token or not self._app_token:
            logger.error("SlackInteractivePlugin: bot_token and app_token are required.")
            return

        logger.info("Starting Slack Interactive Plugin (Socket Mode)...")

        # Register all queued commands
        for command, callback in self._command_handlers.items():
            self._register_slack_command(command, callback)

        # Register message handler
        if self._message_handler:
            self._register_slack_message_listener()

        self._handler = AsyncSocketModeHandler(self._app, self._app_token)

        async def _runner():
            try:
                await self._handler.start_async()
            except asyncio.CancelledError:
                logger.info("Slack Socket Mode handler task cancelled.")
            except Exception as exc:
                logger.error("Slack Socket Mode handler exited with error: %s", exc)

        self._handler_task = asyncio.create_task(_runner())
        logger.info("Slack interactive plugin started.")

    async def stop(self) -> None:
        """Close the Socket Mode connection gracefully."""
        logger.info("Stopping Slack Interactive Plugin...")
        if self._handler is not None:
            await self._handler.close_async()

        task = self._handler_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

        logger.info("Slack interactive plugin stopped.")

    # -- Command / message registration ------------------------------------

    def register_command(self, command: str, callback: Callable) -> None:
        """Queue a slash command to be registered on start()."""
        self._command_handlers[command] = callback

    def register_message_handler(self, callback: Callable) -> None:
        """Register the fall-through message handler."""
        self._message_handler = callback

    def _register_slack_command(self, command: str, callback: Callable) -> None:
        cmd_name = command if command.startswith("/") else f"/{command}"

        async def _cmd_wrapper(ack, body, client):
            await ack()
            user_id = body.get("user_id", "")
            text = body.get("text", "")
            try:
                await callback(user_id=user_id, text=text, context=[], raw_event=body)
            except Exception as exc:
                logger.error("Error executing Slack command %s: %s", command, exc, exc_info=True)

        self._app.command(cmd_name)(_cmd_wrapper)

    def _register_slack_message_listener(self) -> None:
        async def _msg_wrapper(message, say):  # noqa: ARG001
            user_id = message.get("user", "")
            text = message.get("text", "")
            if self._message_handler:
                try:
                    await self._message_handler(user_id=user_id, text=text, raw_event=message)
                except Exception as exc:
                    logger.error("Error in Slack message handler: %s", exc, exc_info=True)

        self._app.message()(_msg_wrapper)

    # -- Messaging ---------------------------------------------------------

    async def send_interactive(self, user_id: str, message: Message) -> str:
        """Post a message (with optional Block Kit buttons) to a user or channel.

        Returns:
            ``channel:ts`` composite identifier usable with ``edit_interactive``.
        """
        blocks = self._build_blocks(message)
        try:
            response = await self._web_client.chat_postMessage(
                channel=user_id,
                text=message.text,
                blocks=blocks,
                unfurl_links=False,
            )
            channel = response["channel"]
            ts = response["ts"]
            return f"{channel}:{ts}"
        except Exception as exc:
            logger.error("SlackInteractivePlugin.send_interactive failed: %s", exc)
            return ""

    async def edit_interactive(self, user_id: str, message_id: str, message: Message) -> None:
        """Update a previously sent message.

        Args:
            user_id: Unused (channel is encoded in *message_id*).
            message_id: ``channel:ts`` composite from ``send_interactive``.
            message: Updated message content.
        """
        if ":" in message_id:
            channel, ts = message_id.split(":", 1)
        else:
            channel, ts = user_id, message_id

        blocks = self._build_blocks(message)
        try:
            await self._web_client.chat_update(
                channel=channel,
                ts=ts,
                text=message.text,
                blocks=blocks,
            )
        except Exception as exc:
            logger.error(
                "SlackInteractivePlugin.edit_interactive failed for %s: %s",
                message_id,
                exc,
                exc_info=True,
            )

    # -- Internal helpers --------------------------------------------------

    def _build_blocks(self, message: Message) -> list:
        """Convert a Message into Slack Block Kit blocks."""
        blocks: list = [
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message.text},
            }
        ]
        if message.buttons:
            elements = []
            for btn in message.buttons:
                element: dict = {
                    "type": "button",
                    "text": {"type": "plain_text", "text": btn.label},
                    "action_id": btn.callback_data,
                }
                if btn.url:
                    element["url"] = btn.url
                elements.append(element)
            blocks.append({"type": "actions", "elements": elements})
        return blocks


def register_plugins(registry: PluginRegistry) -> None:
    """Register Slack interactive plugin."""
    registry.register_factory(
        kind=PluginKind.INTERACTIVE_CLIENT,
        name="slack-interactive",
        version="1.0.0",
        factory=lambda config: SlackInteractivePlugin(config),
        description="Slack interactive client using Bolt Socket Mode",
    )
