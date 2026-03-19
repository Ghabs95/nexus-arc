"""Discord notification channel adapter.

Requires the ``discord`` optional extra::

    pip install nexus-arc[discord]
"""

import asyncio
import logging

from nexus.adapters.notifications.base import Message, NotificationChannel
from nexus.core.models import Severity

try:
    import aiohttp

    _AIOHTTP_AVAILABLE = True
except ImportError:
    _AIOHTTP_AVAILABLE = False

logger = logging.getLogger(__name__)

# Severity → Discord embed colour (decimal)
_SEVERITY_COLOUR = {
    Severity.CRITICAL: 0xFF0000,
    Severity.ERROR: 0xFF6B35,
    Severity.WARNING: 0xFFB347,
    Severity.INFO: 0x36A64F,
}


def _require_aiohttp() -> None:
    if not _AIOHTTP_AVAILABLE:
        raise ImportError(
            "aiohttp is required for DiscordNotificationChannel. "
            "Install it with: pip install nexus-arc[discord]"
        )


def _severity_emoji(severity: Severity) -> str:
    return {
        Severity.CRITICAL: "🔴",
        Severity.ERROR: "🟠",
        Severity.WARNING: "🟡",
        Severity.INFO: "ℹ️",
    }.get(severity, "ℹ️")


class DiscordNotificationChannel(NotificationChannel):
    """Discord notification channel using Discord's Webhook and REST APIs.

    Two usage modes are supported:

    1. **Webhook-only** (simpler): supply ``webhook_url``. Works for
       ``send_message``, ``send_alert``, and partial ``update_message``
       support (webhook message editing).

    2. **Bot token** (full): supply ``bot_token`` and optionally
       ``webhook_url``. Required for ``request_input`` (polls the channel
       for a reply).

    Args:
        webhook_url: Discord incoming webhook URL for posting messages.
        bot_token: Discord bot token for full REST API access.
        alert_channel_id: Channel ID to broadcast system alerts to (required
            when ``webhook_url`` is not set).
    """

    _BASE = "https://discord.com/api/v10"

    def __init__(
        self,
        webhook_url: str | None = None,
        bot_token: str | None = None,
        alert_channel_id: str | None = None,
        guild_id: str | None = None,
    ):
        _require_aiohttp()
        normalized_bot_token: str | None = None
        if bot_token is not None:
            if not isinstance(bot_token, str):
                raise TypeError("bot_token must be a string if provided.")
            token = bot_token.strip()
            if not token:
                raise ValueError("bot_token must not be empty or whitespace.")
            if token.lower().startswith("bot "):
                raise ValueError(
                    "bot_token should be provided without the 'Bot ' prefix (case-insensitive); "
                    "the adapter will add the required Authorization header."
                )
            if any(ch.isspace() for ch in token):
                raise ValueError("bot_token must not contain whitespace characters.")
            normalized_bot_token = token
        if not webhook_url and not normalized_bot_token:
            raise ValueError("At least one of webhook_url or bot_token must be provided.")
        self._webhook_url = webhook_url
        self._bot_token = normalized_bot_token
        self._alert_channel_id = alert_channel_id
        self._guild_id = str(guild_id or "").strip() or None
        self._session = None
        self._session_loop = None
        self._sessions_by_loop: dict[object, aiohttp.ClientSession] = {}

    # ------------------------------------------------------------------
    # NotificationChannel interface
    # ------------------------------------------------------------------

    @property
    def name(self) -> str:
        return "discord"

    async def send_message(self, user_id: str, message: Message) -> str:
        """Post a message to a Discord channel.

        Args:
            user_id: Discord channel ID (or thread ID) to post to.
            message: Message to send.

        Returns:
            Discord message ID. When using the webhook path, returns the bare
            snowflake ID. When using the bot token path, returns
            ``channel_id:message_id`` so callers can pass it to
            ``update_message`` without tracking the channel separately.
        """
        payload = self._build_payload(message)
        if self._webhook_url:
            return await self._post_webhook(payload)
        message_id = await self._post_channel(user_id, payload)
        return f"{user_id}:{message_id}"

    async def update_message(self, message_id: str, new_text: str) -> None:
        """Edit a previously sent Discord message.

        ``message_id`` should be encoded as ``channel_id:message_id`` when
        using the bot token path, or as the bare webhook message ID when
        using a webhook.
        """
        if self._webhook_url and ":" not in message_id:
            await self._patch_webhook_message(message_id, {"content": new_text})
            return

        if ":" in message_id:
            channel_id, mid = message_id.split(":", 1)
        else:
            raise ValueError(
                "message_id must be 'channel_id:message_id' when using bot token path."
            )
        await self._patch_channel_message(channel_id, mid, {"content": new_text})

    async def send_alert(self, message: str, severity: Severity) -> None:
        """Broadcast a system alert embed to the configured alert channel."""
        emoji = _severity_emoji(severity)
        colour = _SEVERITY_COLOUR.get(severity, 0x36A64F)
        payload: dict = {
            "embeds": [
                {
                    "title": f"{emoji} [{severity.value.upper()}] System Alert",
                    "description": message,
                    "color": colour,
                }
            ]
        }

        if self._webhook_url:
            await self._post_webhook(payload)
            return

        if not self._alert_channel_id and self._guild_id and self._bot_token:
            self._alert_channel_id = await self._resolve_default_alert_channel(self._guild_id)

        if not self._alert_channel_id:
            raise ValueError(
                "alert_channel_id is required to send alerts without a webhook_url. "
                "Set DISCORD_ALERT_CHANNEL_ID or DISCORD_GUILD_ID."
            )
        await self._post_channel(self._alert_channel_id, payload)

    async def _resolve_default_alert_channel(self, guild_id: str) -> str | None:
        """Resolve best-effort default channel for alerts in a guild."""
        if not self._bot_token:
            return None

        session = self._get_session()
        headers = {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }

        try:
            async with session.get(
                f"{self._BASE}/guilds/{guild_id}",
                headers=headers,
            ) as resp:
                if resp.status < 400:
                    guild_payload = await resp.json()
                    system_channel_id = str(guild_payload.get("system_channel_id") or "").strip()
                    if system_channel_id:
                        return system_channel_id
                else:
                    logger.warning(
                        "Discord guild lookup failed for %s: status=%s",
                        guild_id,
                        resp.status,
                    )
        except Exception as exc:
            logger.warning("Discord guild lookup error for %s: %s", guild_id, exc)

        try:
            async with session.get(
                f"{self._BASE}/guilds/{guild_id}/channels",
                headers=headers,
            ) as resp:
                resp.raise_for_status()
                channels = await resp.json()
        except Exception as exc:
            logger.warning("Discord channel listing failed for guild %s: %s", guild_id, exc)
            return None

        for channel in channels:
            channel_type = int(channel.get("type", -1))
            if channel_type in {0, 5}:
                channel_id = str(channel.get("id") or "").strip()
                if channel_id:
                    return channel_id
        return None

    async def request_input(
        self, user_id: str, prompt: str, timeout: float = 60.0, poll_interval: float = 3.0
    ) -> str:
        """Send a prompt to a channel and wait for the next human reply.

        Requires ``bot_token``.

        Args:
            user_id: Discord channel ID to post to and poll.
            prompt: Prompt message to send.
            timeout: Maximum seconds to wait for a reply (default 60).
            poll_interval: Seconds between polling attempts (default 3).
        """
        import asyncio

        if not self._bot_token:
            raise ValueError("bot_token is required for request_input.")

        msg = Message(text=prompt)
        raw_id = await self._post_channel(user_id, self._build_payload(msg))

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            await asyncio.sleep(poll_interval)
            messages = await self._fetch_messages_after(user_id, raw_id)
            for m in messages:
                if not m.get("author", {}).get("bot", False):
                    return m.get("content", "")

        raise TimeoutError(f"No reply in Discord channel {user_id} within {timeout}s")

    # ------------------------------------------------------------------
    # Internal helpers — payload building
    # ------------------------------------------------------------------

    def _build_payload(self, message: Message) -> dict:
        """Build a Discord message payload from a ``Message``."""
        colour = _SEVERITY_COLOUR.get(message.severity, 0x36A64F)
        emoji = _severity_emoji(message.severity)
        payload: dict = {
            "embeds": [
                {
                    "description": f"{emoji} {message.text}",
                    "color": colour,
                }
            ]
        }
        if message.buttons:
            # Discord components require an application command context;
            # fall back to including button labels as plain text links.
            links = []
            for btn in message.buttons:
                if btn.url:
                    links.append(f"[{btn.label}]({btn.url})")
                else:
                    links.append(f"`{btn.label}`")
            payload["content"] = " | ".join(links)
        return payload

    # ------------------------------------------------------------------
    # Internal helpers — HTTP
    # ------------------------------------------------------------------

    def _get_session(self) -> "aiohttp.ClientSession":
        """Return a shared aiohttp session, creating it on first use."""
        current_loop = asyncio.get_running_loop()
        sessions_by_loop = getattr(self, "_sessions_by_loop", None)
        if not isinstance(sessions_by_loop, dict):
            sessions_by_loop = {}
            self._sessions_by_loop = sessions_by_loop

        session = getattr(self, "_session", None)
        session_loop = getattr(self, "_session_loop", None)
        if session is not None and session_loop is not None and session_loop not in sessions_by_loop:
            sessions_by_loop[session_loop] = session

        session = sessions_by_loop.get(current_loop)
        if session is not None and session.closed:
            sessions_by_loop.pop(current_loop, None)
            session = None

        if session is None:
            session = aiohttp.ClientSession()
            sessions_by_loop[current_loop] = session

        self._session = session
        self._session_loop = current_loop
        return session

    async def aclose(self) -> None:
        """Close the underlying aiohttp session, if it exists."""
        sessions_by_loop = getattr(self, "_sessions_by_loop", None)
        sessions: list[aiohttp.ClientSession] = []
        if isinstance(sessions_by_loop, dict):
            for candidate in sessions_by_loop.values():
                if candidate not in sessions:
                    sessions.append(candidate)

        session = getattr(self, "_session", None)
        if session is not None and session not in sessions:
            sessions.append(session)

        for candidate in sessions:
            if not candidate.closed:
                await candidate.close()

        self._sessions_by_loop = {}
        self._session = None
        self._session_loop = None

    async def _post_webhook(self, payload: dict) -> str:
        """POST payload to the webhook URL; returns the message ID."""
        session = self._get_session()
        try:
            async with session.post(
                f"{self._webhook_url}?wait=true",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return str(data.get("id", ""))
        except Exception:
            logger.error("Discord _post_webhook failed for webhook %s", self._webhook_url)
            raise

    async def _post_channel(self, channel_id: str, payload: dict) -> str:
        """POST payload to a channel via the REST API; returns the message ID."""
        session = self._get_session()
        try:
            async with session.post(
                f"{self._BASE}/channels/{channel_id}/messages",
                json=payload,
                headers=self._auth_headers(),
            ) as resp:
                resp.raise_for_status()
                data = await resp.json()
                return str(data.get("id", ""))
        except Exception:
            logger.error("Discord _post_channel failed for channel %s", channel_id)
            raise

    async def _patch_webhook_message(self, message_id: str, payload: dict) -> None:
        session = self._get_session()
        try:
            async with session.patch(
                f"{self._webhook_url}/messages/{message_id}",
                json=payload,
                headers={"Content-Type": "application/json"},
            ) as resp:
                resp.raise_for_status()
        except Exception:
            logger.error("Discord _patch_webhook_message failed for message %s", message_id)
            raise

    async def _patch_channel_message(self, channel_id: str, message_id: str, payload: dict) -> None:
        session = self._get_session()
        try:
            async with session.patch(
                f"{self._BASE}/channels/{channel_id}/messages/{message_id}",
                json=payload,
                headers=self._auth_headers(),
            ) as resp:
                resp.raise_for_status()
        except Exception:
            logger.error(
                "Discord _patch_channel_message failed for channel %s message %s",
                channel_id,
                message_id,
            )
            raise

    async def _fetch_messages_after(self, channel_id: str, after_id: str) -> list:
        session = self._get_session()
        try:
            async with session.get(
                f"{self._BASE}/channels/{channel_id}/messages",
                params={"after": after_id, "limit": "10"},
                headers=self._auth_headers(),
            ) as resp:
                if resp.status != 200:
                    return []
                return await resp.json()
        except Exception:
            logger.error("Discord _fetch_messages_after failed for channel %s", channel_id)
            raise

    def _auth_headers(self) -> dict:
        return {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }
