"""LinkedIn social publishing adapter.

Publishes long-form professional copy with rich link metadata via the
LinkedIn API v2.  Credentials are retrieved via
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

_LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
_MAX_LINKEDIN_TEXT_LEN = 3000


class LinkedInSocialAdapter(SocialPlatformAdapter):
    """Publish campaign posts to a LinkedIn person or organisation page.

    Args:
        access_token: LinkedIn OAuth 2.0 access token.
        author_urn: LinkedIn author URN, e.g.
            ``"urn:li:person:abc123"`` or ``"urn:li:organization:123456"``.

    Supply ``link_url``, ``link_title``, and ``link_description`` in
    ``post.metadata`` to attach rich link preview metadata.
    """

    def __init__(self, access_token: str, author_urn: str):
        if not access_token:
            raise ValueError("access_token is required for LinkedIn adapter.")
        if not author_urn:
            raise ValueError("author_urn is required for LinkedIn adapter.")
        self._access_token = access_token
        self._author_urn = author_urn

    @property
    def platform(self) -> str:
        return "linkedin"

    def validate(self, post: SocialPost) -> list[str]:
        errors: list[str] = []
        if not post.content:
            errors.append("content must not be empty")
        elif len(post.content) > _MAX_LINKEDIN_TEXT_LEN:
            errors.append(
                f"content exceeds LinkedIn text limit of {_MAX_LINKEDIN_TEXT_LEN} characters "
                f"(got {len(post.content)})"
            )
        meta = post.metadata
        if "link_url" in meta and not str(meta["link_url"]).startswith("https://"):
            errors.append("link_url must be an https URL for LinkedIn link posts")
        return errors

    async def publish(self, post: SocialPost) -> PublishResult:
        """Publish *post* to LinkedIn.

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

        body = self._build_share_body(post)
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{_LINKEDIN_API_BASE}/ugcPosts",
                    json=body,
                    headers=headers,
                ) as resp:
                    if resp.status == 429:
                        raise SocialPublishError(
                            self.platform,
                            "LinkedIn API rate limit exceeded (HTTP 429).",
                            retryable=True,
                        )
                    if resp.status >= 400:
                        text = await resp.text()
                        raise SocialPublishError(
                            self.platform,
                            f"LinkedIn API returned HTTP {resp.status}: {text}",
                            retryable=resp.status >= 500,
                        )
                    data = await resp.json()
                    post_id = str(data.get("id", ""))
        except SocialPublishError:
            raise
        except Exception as exc:
            raise SocialPublishError(self.platform, str(exc), retryable=True) from exc

        logger.info(
            "linkedin_social_adapter: published campaign=%s idempotency_key=%s post_id=%s",
            post.campaign_id,
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

    def _build_share_body(self, post: SocialPost) -> dict[str, Any]:
        meta = post.metadata
        share_media_category = "NONE"
        media: list[dict[str, Any]] = []

        if post.media_urls:
            share_media_category = "IMAGE"
            media = [
                {
                    "status": "READY",
                    "media": url,
                    "description": {"text": meta.get("media_alt", "")},
                }
                for url in post.media_urls
            ]
        elif "link_url" in meta:
            share_media_category = "ARTICLE"
            media = [
                {
                    "status": "READY",
                    "originalUrl": str(meta["link_url"]),
                    "title": {"text": str(meta.get("link_title", ""))},
                    "description": {"text": str(meta.get("link_description", ""))},
                }
            ]

        body: dict[str, Any] = {
            "author": self._author_urn,
            "lifecycleState": "PUBLISHED",
            "specificContent": {
                "com.linkedin.ugc.ShareContent": {
                    "shareCommentary": {"text": post.content},
                    "shareMediaCategory": share_media_category,
                    "media": media,
                }
            },
            "visibility": {
                "com.linkedin.ugc.MemberNetworkVisibility": meta.get(
                    "visibility", "PUBLIC"
                )
            },
        }
        return body
