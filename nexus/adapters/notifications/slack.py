"""Slack notification channel adapter.

Requires the ``slack`` optional extra::

    pip install nexus-arc[slack]
"""

import logging

from nexus.adapters.notifications.base import Message, NotificationChannel
from nexus.core.models import Severity

try:
    from slack_sdk import WebClient
    from slack_sdk.errors import SlackApiError

    _SLACK_SDK_AVAILABLE = True
except ImportError:
    _SLACK_SDK_AVAILABLE = False

logger = logging.getLogger(__name__)

# Severity → Slack colour sidestrip
_SEVERITY_COLOUR = {
    Severity.CRITICAL: "#FF0000",
    Severity.ERROR: "#FF6B35",
    Severity.WARNING: "#FFB347",
    Severity.INFO: "#36A64F",
}


def _require_slack_sdk() -> None:
    if not _SLACK_SDK_AVAILABLE:
        raise ImportError(
            "slack-sdk is required for SlackNotificationChannel. "
            "Install it with: pip install nexus-arc[slack]"
        )


def _severity_emoji(severity: Severity) -> str:
    return {
        Severity.CRITICAL: "🔴",
        Severity.ERROR: "🟠",
        Severity.WARNING: "🟡",
        Severity.INFO: "ℹ️",
    }.get(severity, "ℹ️")


class SlackNotificationChannel(NotificationChannel):
    """Slack notification channel using the Slack Web API.

    Args:
        token: Slack bot OAuth token (``xoxb-...``).
        default_channel: Default channel to post system alerts to (e.g. ``#ops``).
        webhook_url: Optional incoming-webhook URL as a simpler alternative for
            send_alert when a full API token is not required.
    """

    def __init__(
        self,
        token: str,
        default_channel: str = "#general",
        webhook_url: str | None = None,
    ):
        _require_slack_sdk()
        self._client = WebClient(token=token)
        self._default_channel = default_channel
        self._webhook_url = webhook_url

    @property
    def name(self) -> str:
        return "slack"

    async def send_message(self, user_id: str, message: Message) -> str:
        """Post a message to a Slack user (DM) or channel.

        Args:
            user_id: Slack user ID (``U…``) or channel name / ID.
            message: Message to send.

        Returns:
            Slack ``ts`` (timestamp) of the posted message, usable as message_id.
        """
        blocks = self._build_blocks(message)
        try:
            response = self._client.chat_postMessage(
                channel=user_id,
                text=message.text,  # fallback for notifications
                blocks=blocks,
                unfurl_links=False,
            )
            return response["ts"]
        except SlackApiError as exc:
            logger.error("Slack send_message failed: %s", exc.response["error"])
            raise

    async def update_message(self, message_id: str, new_text: str) -> None:
        """Update a previously posted message by its ``ts``.

        Note: requires knowing the channel; encode as ``channel:ts`` in
        message_id when using this method across channels.
        """
        # message_id may be "channel:ts" or bare "ts" in the default channel
        if ":" in message_id:
            channel, ts = message_id.split(":", 1)
        else:
            channel, ts = self._default_channel, message_id

        try:
            self._client.chat_update(channel=channel, ts=ts, text=new_text)
        except SlackApiError as exc:
            logger.error("Slack update_message failed: %s", exc.response["error"])
            raise

    async def send_alert(self, message: str, severity: Severity) -> None:
        """Broadcast a system alert to the default channel.

        Falls back to incoming-webhook URL if configured (no full token needed).
        """
        emoji = _severity_emoji(severity)
        text = f"{emoji} *[{severity.value.upper()}]* {message}"

        if self._webhook_url:
            self._send_via_webhook(text)
            return

        try:
            self._client.chat_postMessage(
                channel=self._default_channel,
                text=text,
                attachments=[
                    {
                        "color": _SEVERITY_COLOUR.get(severity, "#36A64F"),
                        "text": message,
                    }
                ],
            )
        except SlackApiError as exc:
            logger.error("Slack send_alert failed: %s", exc.response["error"])
            raise

    async def request_input(self, user_id: str, prompt: str) -> str:
        """Send a DM prompt to a user and wait for the next message.

        Note: This is a simplified synchronous poll — production deployments
        should use Slack's interactivity / Socket Mode instead.
        """
        import time

        msg = Message(text=prompt)
        ts = await self.send_message(user_id, msg)

        # Poll the conversation history for a reply (up to 60s)
        deadline = time.time() + 60
        while time.time() < deadline:
            try:
                history = self._client.conversations_history(
                    channel=user_id,
                    oldest=ts,
                    limit=5,
                )
                for m in history.get("messages", []):
                    if m.get("ts") != ts and m.get("user") == user_id:
                        return m.get("text", "")
            except SlackApiError:
                pass
            time.sleep(3)

        raise TimeoutError(f"No reply from Slack user {user_id} within 60s")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

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

    def _send_via_webhook(self, text: str) -> None:
        """POST a simple text payload to the incoming-webhook URL."""
        import json
        import urllib.request

        payload = json.dumps({"text": text}).encode()
        req = urllib.request.Request(
            self._webhook_url,
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status not in (200, 201):
                raise RuntimeError(f"Slack webhook returned HTTP {resp.status}")
