"""Meta (Facebook / Instagram) social publishing adapter.

Media-first publishing via the Meta Graph API.  Supports Facebook page posts
and Instagram media objects.  Credentials are retrieved via
:mod:`nexus.core.auth.credential_crypto` — raw tokens are never stored as
instance attributes or injected into prompts/logs.
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

_GRAPH_API_BASE = "https://graph.facebook.com/v19.0"
_MAX_FB_MESSAGE_LEN = 63206
_MAX_IG_CAPTION_LEN = 2200


class MetaSocialAdapter(SocialPlatformAdapter):
    """Publish campaign posts to a Facebook page or Instagram account.

    Args:
        page_access_token: Meta Graph API page-scoped access token.
        page_id: Facebook Page ID (for Facebook posts).
        instagram_account_id: Instagram Business account ID (for IG posts).
        target: ``"facebook"``, ``"instagram"``, or ``"both"`` (default ``"facebook"``).
    """

    def __init__(
        self,
        page_access_token: str,
        page_id: str | None = None,
        instagram_account_id: str | None = None,
        target: str = "facebook",
    ):
        if not page_access_token:
            raise ValueError("page_access_token is required for Meta adapter.")
        if target == "facebook" and not page_id:
            raise ValueError("page_id is required when target includes Facebook.")
        if target == "instagram" and not instagram_account_id:
            raise ValueError(
                "instagram_account_id is required when target includes Instagram."
            )
        if target == "both" and (not page_id or not instagram_account_id):
            raise ValueError(
                "Both page_id and instagram_account_id are required when target='both'."
            )
        self._token = page_access_token
        self._page_id = page_id
        self._ig_account_id = instagram_account_id
        self._target = target

    @property
    def platform(self) -> str:
        return "meta_facebook" if self._target == "facebook" else "meta_instagram"

    def validate(self, post: SocialPost) -> list[str]:
        errors: list[str] = []
        if not post.content:
            errors.append("content must not be empty")
        if self._target in ("facebook", "both"):
            if len(post.content) > _MAX_FB_MESSAGE_LEN:
                errors.append(
                    f"Facebook message exceeds limit of {_MAX_FB_MESSAGE_LEN} characters "
                    f"(got {len(post.content)})"
                )
        if self._target in ("instagram", "both"):
            if len(post.content) > _MAX_IG_CAPTION_LEN:
                errors.append(
                    f"Instagram caption exceeds limit of {_MAX_IG_CAPTION_LEN} characters "
                    f"(got {len(post.content)})"
                )
            if not post.media_urls:
                errors.append("Instagram posts require at least one media_url")
        return errors

    async def publish(self, post: SocialPost) -> PublishResult:
        """Publish *post* to Facebook and/or Instagram.

        Raises:
            SocialPublishError: On API or network failures.
        """
        try:
            import aiohttp
        except ImportError as exc:
            raise SocialPublishError(
                self.platform,
                "aiohttp is required: pip install aiohttp",
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

        try:
            async with aiohttp.ClientSession() as session:
                if self._target in ("facebook", "both") and self._page_id:
                    post_id = await self._publish_facebook(session, post)
                else:
                    post_id = await self._publish_instagram(session, post)
        except SocialPublishError:
            raise
        except Exception as exc:
            raise SocialPublishError(self.platform, str(exc), retryable=True) from exc

        logger.info(
            "meta_social_adapter: published campaign=%s platform=%s idempotency_key=%s post_id=%s",
            post.campaign_id,
            self.platform,
            idempotency_key,
            post_id,
        )
        return PublishResult.ok(
            platform=self.platform,
            campaign_id=post.campaign_id,
            idempotency_key=idempotency_key,
            post_id=post_id,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _graph_post(
        self, session: Any, endpoint: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        params["access_token"] = self._token
        async with session.post(
            f"{_GRAPH_API_BASE}/{endpoint}",
            params=params,
        ) as resp:
            data = await resp.json()
            if resp.status >= 400 or "error" in data:
                err = data.get("error", {})
                retryable = resp.status >= 500 or err.get("is_transient", False)
                raise SocialPublishError(
                    self.platform,
                    f"Meta Graph API error: {err.get('message', resp.status)}",
                    retryable=retryable,
                )
            return data

    async def _publish_facebook(self, session: Any, post: SocialPost) -> str:
        params: dict[str, Any] = {"message": post.content}
        if post.media_urls:
            params["link"] = post.media_urls[0]
        data = await self._graph_post(session, f"{self._page_id}/feed", params)
        return str(data.get("id", ""))

    async def _publish_instagram(self, session: Any, post: SocialPost) -> str:
        if not post.media_urls:
            raise SocialPublishError(
                self.platform, "Instagram requires at least one media_url", retryable=False
            )
        # Step 1: create media container
        container_params: dict[str, Any] = {
            "image_url": post.media_urls[0],
            "caption": post.content,
        }
        container = await self._graph_post(
            session, f"{self._ig_account_id}/media", container_params
        )
        container_id = str(container.get("id", ""))

        # Step 2: publish the container
        publish_params: dict[str, Any] = {"creation_id": container_id}
        result = await self._graph_post(
            session, f"{self._ig_account_id}/media_publish", publish_params
        )
        return str(result.get("id", ""))
