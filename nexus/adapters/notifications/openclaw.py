"""OpenClaw notification channel — pushes Nexus workflow events to the OpenClaw command bridge.

This allows Nexus ARC running in ``NEXUS_RUNTIME_MODE=openclaw`` to deliver
step completions, alerts, and workflow updates directly to the OpenClaw agent
session (e.g. a Telegram chat), without requiring a dedicated Nexus Telegram bot.

Configuration (env vars or project_config.yaml plugin block):
    NEXUS_OPENCLAW_BRIDGE_URL     Base URL of the OpenClaw gateway (default: http://127.0.0.1:18789)
    NEXUS_OPENCLAW_BRIDGE_TOKEN   Bearer token for the OpenClaw hooks endpoint
    NEXUS_OPENCLAW_SENDER_ID      Telegram/channel chat ID to deliver notifications to
    NEXUS_OPENCLAW_CHANNEL        Optional channel hint (e.g. "telegram")

Requires hooks to be enabled in openclaw.json:
    {
      "hooks": {
        "enabled": true,
        "token": "<same as NEXUS_OPENCLAW_BRIDGE_TOKEN>",
        "path": "/hooks",
        "allowedAgentIds": ["main"]
      }
    }
"""

from __future__ import annotations

import logging
import os
from typing import Any

from nexus.adapters.notifications.base import Message, NotificationChannel
from nexus.core.models import Severity

try:
    import aiohttp

    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

logger = logging.getLogger(__name__)

_DEFAULT_BRIDGE_URL = "http://127.0.0.1:18789"
_SEVERITY_EMOJI = {
    Severity.INFO: "ℹ️",
    Severity.WARNING: "⚠️",
    Severity.ERROR: "🚨",
    Severity.CRITICAL: "🔴",
}


def _require_aiohttp() -> None:
    if not _AIOHTTP_AVAILABLE:
        raise ImportError(
            "aiohttp is required for OpenClawNotificationChannel. "
            "Install it with: pip install nexus-arc[openclaw]"
        )


class OpenClawNotificationChannel(NotificationChannel):
    """Sends Nexus notifications to an OpenClaw agent session via the OpenClaw bridge.

    When Nexus is running in ``openclaw`` runtime mode this channel replaces (or
    supplements) the Telegram bot, so workflow step completions, alerts, and
    human-handoff prompts all arrive in the user's primary OpenClaw chat.

    Args:
        bridge_url: OpenClaw bridge URL.  Defaults to ``NEXUS_OPENCLAW_BRIDGE_URL`` env var.
        auth_token: Bearer token for the bridge.  Defaults to ``NEXUS_OPENCLAW_BRIDGE_TOKEN``.
        sender_id:  Target session/chat ID.  Defaults to ``NEXUS_OPENCLAW_SENDER_ID``.
        channel:    Optional channel hint forwarded in the payload (e.g. ``"telegram"``).
        timeout:    HTTP request timeout in seconds.
    """

    def __init__(
        self,
        bridge_url: str | None = None,
        auth_token: str | None = None,
        sender_id: str | None = None,
        channel: str | None = None,
        timeout: int = 10,
    ):
        _require_aiohttp()
        self._bridge_url = (
            (bridge_url or os.getenv("NEXUS_OPENCLAW_BRIDGE_URL") or _DEFAULT_BRIDGE_URL).rstrip("/")
        )
        self._auth_token = auth_token or os.getenv("NEXUS_OPENCLAW_BRIDGE_TOKEN") or ""
        self._sender_id = sender_id or os.getenv("NEXUS_OPENCLAW_SENDER_ID") or ""
        self._channel = channel or os.getenv("NEXUS_OPENCLAW_CHANNEL") or "telegram"
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    # ------------------------------------------------------------------
    # NotificationChannel interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "openclaw"

    async def send_message(self, user_id: str, message: Message) -> str:
        """Send a notification message to the OpenClaw session.

        Returns a synthetic message ID (``"openclaw:<status>"``).
        """
        emoji = _SEVERITY_EMOJI.get(message.severity, "ℹ️")
        text = f"{emoji} **[Nexus]** {message.text}"
        payload = self._build_payload(text, target_user=user_id or self._sender_id)
        ok = await self._post(payload)
        return f"openclaw:{'ok' if ok else 'error'}"

    async def update_message(self, message_id: str, new_text: str) -> None:
        """OpenClaw bridge does not support in-place message edits; sends a new message."""
        payload = self._build_payload(f"ℹ️ **[Nexus update]** {new_text}")
        await self._post(payload)

    async def send_alert(self, message: str, severity: Severity) -> None:
        """Broadcast a system alert to the configured sender."""
        emoji = _SEVERITY_EMOJI.get(severity, "⚠️")
        text = f"{emoji} **[Nexus alert]** {message}"
        payload = self._build_payload(text)
        await self._post(payload)

    async def request_input(self, user_id: str, prompt: str) -> str:
        """Send a prompt requesting human input.

        Note: OpenClaw does not support synchronous reply-wait via the bridge;
        this sends the prompt and returns an empty string.  The user's response
        will arrive as a normal chat message handled by the OpenClaw agent.
        """
        text = f"💬 **[Nexus needs input]** {prompt}"
        payload = self._build_payload(text, target_user=user_id or self._sender_id)
        await self._post(payload)
        return ""

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(self, text: str, target_user: str | None = None) -> dict[str, Any]:
        """Build a /hooks/agent payload that delivers a message to the OpenClaw session."""
        payload: dict[str, Any] = {
            "message": text,
            "name": "Nexus",
            "deliver": True,
            "channel": self._channel or "telegram",
            "wakeMode": "now",
        }
        # If a specific recipient is set, pass it as `to`
        recipient = target_user or self._sender_id
        if recipient:
            payload["to"] = recipient
        return payload

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._auth_token:
            headers["Authorization"] = f"Bearer {self._auth_token}"
        return headers

    async def _post(self, payload: dict[str, Any]) -> bool:
        # Use /hooks/agent to trigger an isolated agent delivery turn
        url = f"{self._bridge_url}/hooks/agent"
        try:
            async with aiohttp.ClientSession(timeout=self._timeout) as session:
                async with session.post(url, json=payload, headers=self._headers()) as resp:
                    if resp.status < 300:
                        logger.debug("OpenClaw notification delivered (status=%s)", resp.status)
                        return True
                    body = await resp.text()
                    logger.warning(
                        "OpenClaw notification failed: HTTP %s — %s", resp.status, body[:200]
                    )
                    return False
        except Exception as exc:
            logger.warning("OpenClaw notification error: %s", exc)
            return False
