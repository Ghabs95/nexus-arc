"""Discord social publishing adapter.

Publishes campaign posts to Discord channels via webhook or bot REST API.
Credentials are retrieved via :mod:`nexus.core.auth.credential_crypto` —
raw tokens are never stored as instance attributes or injected into logs.
"""

from __future__ import annotations

import logging
from typing import Any

from nexus.adapters.social.base import (
    PublishResult,
    SocialPlatformAdapter,
    SocialPost,
    SocialPublishError,
)
from nexus.core.social_publish import derive_idempotency_key

logger = logging.getLogger(__name__)

_MAX_DISCORD_CONTENT_LEN = 2000  # Discord message character limit


class DiscordSocialAdapter(SocialPlatformAdapter):
    """Publish campaign posts to a Discord channel via webhook or bot API.

    Credentials are passed at construction time; they are never logged.
    Use :mod:`nexus.core.auth.credential_crypto` to decrypt stored tokens
    before constructing this adapter.

    Args:
        webhook_url: Discord incoming webhook URL.
        bot_token: Discord bot token (without the ``Bot `` prefix).
        channel_id: Target channel ID when using bot token without a webhook.
    """

    def __init__(
        self,
        webhook_url: str | None = None,
        bot_token: str | None = None,
        channel_id: str | None = None,
    ):
        if not webhook_url and not bot_token:
            raise ValueError("Either webhook_url or bot_token must be provided.")
        self._webhook_url = webhook_url
        self._bot_token = bot_token
        self._channel_id = channel_id

    @property
    def platform(self) -> str:
        return "discord"

    def validate(self, post: SocialPost) -> list[str]:
        errors: list[str] = []
        if not post.content:
            errors.append("content must not be empty")
        elif len(post.content) > _MAX_DISCORD_CONTENT_LEN:
            errors.append(
                f"content exceeds Discord limit of {_MAX_DISCORD_CONTENT_LEN} characters "
                f"(got {len(post.content)})"
            )
        if not self._webhook_url and not self._channel_id:
            errors.append("channel_id is required when bot_token is used without a webhook_url")
        return errors

    async def publish(self, post: SocialPost) -> PublishResult:
        """Publish *post* to Discord.

        Raises:
            SocialPublishError: On network or API failures.
        """
        try:
            import aiohttp
        except ImportError as exc:
            raise SocialPublishError(
                self.platform,
                "aiohttp is required: pip install nexus-arc[discord]",
                retryable=False,
            ) from exc

        idempotency_key = derive_idempotency_key(
            post.campaign_id, self.platform, post.scheduled_time_utc or ""
        )

        errors = self.validate(post)
        if errors:
            return PublishResult.fail(
                platform=self.platform,
                campaign_id=post.campaign_id,
                idempotency_key=idempotency_key,
                error="; ".join(errors),
            )

        payload = self._build_payload(post)
        try:
            async with aiohttp.ClientSession() as session:
                if self._webhook_url:
                    message_id = await self._post_webhook(session, payload)
                else:
                    message_id = await self._post_channel(session, payload)
        except Exception as exc:
            raise SocialPublishError(self.platform, str(exc), retryable=True) from exc

        logger.info(
            "discord_social_adapter: published campaign=%s idempotency_key=%s message_id=%s",
            post.campaign_id,
            idempotency_key,
            message_id,
        )
        return PublishResult.ok(
            platform=self.platform,
            campaign_id=post.campaign_id,
            idempotency_key=idempotency_key,
            post_id=message_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_payload(self, post: SocialPost) -> dict[str, Any]:
        payload: dict[str, Any] = {"content": post.content}
        if post.media_urls:
            payload["embeds"] = [{"image": {"url": url}} for url in post.media_urls[:4]]
        return payload

    async def _post_webhook(self, session: Any, payload: dict[str, Any]) -> str:
        async with session.post(
            f"{self._webhook_url}?wait=true",
            json=payload,
            headers={"Content-Type": "application/json"},
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise SocialPublishError(
                    self.platform,
                    f"Discord webhook returned HTTP {resp.status}: {text}",
                    retryable=resp.status >= 500,
                )
            data = await resp.json()
            return str(data.get("id", ""))

    async def _post_channel(self, session: Any, payload: dict[str, Any]) -> str:
        headers = {
            "Authorization": f"Bot {self._bot_token}",
            "Content-Type": "application/json",
        }
        async with session.post(
            f"https://discord.com/api/v10/channels/{self._channel_id}/messages",
            json=payload,
            headers=headers,
        ) as resp:
            if resp.status >= 400:
                text = await resp.text()
                raise SocialPublishError(
                    self.platform,
                    f"Discord API returned HTTP {resp.status}: {text}",
                    retryable=resp.status >= 500,
                )
            data = await resp.json()
            return str(data.get("id", ""))
