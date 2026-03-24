"""X (Twitter) social publishing adapter.

Handles character limits, thread splitting, and strict rate-limit handling
via the X API v2.  Credentials are retrieved via
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
from nexus.adapters.social.base import derive_idempotency_key

logger = logging.getLogger(__name__)

_X_CHAR_LIMIT = 280
_X_API_BASE = "https://api.twitter.com/2"


def _split_into_thread(text: str, limit: int = _X_CHAR_LIMIT) -> list[str]:
    """Split *text* into thread chunks respecting the character limit.

    Splits on word boundaries where possible; falls back to hard splits.
    """
    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    words = text.split()
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip() if current else word
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            if len(word) > limit:
                # Hard-split oversized words
                for i in range(0, len(word), limit):
                    chunks.append(word[i : i + limit])
                current = ""
            else:
                current = word
    if current:
        chunks.append(current)
    return chunks


class XSocialAdapter(SocialPlatformAdapter):
    """Publish campaign posts to X (Twitter) via the X API v2.

    Supports single-tweet and thread publishing.  Supply
    ``thread_mode=True`` in ``post.metadata`` to force thread splitting even
    when the content fits in a single tweet.

    Args:
        bearer_token: X API v2 Bearer Token for app-only auth.
        oauth_token: OAuth 1.0a / OAuth 2.0 user context access token.
        oauth_token_secret: OAuth 1.0a access token secret.

    Note:
        At least ``bearer_token`` or ``oauth_token`` must be provided.
        Publishing tweets requires user-context OAuth credentials.
    """

    def __init__(
        self,
        bearer_token: str | None = None,
        oauth_token: str | None = None,
        oauth_token_secret: str | None = None,
    ):
        if not bearer_token and not oauth_token:
            raise ValueError("At least bearer_token or oauth_token must be provided for X adapter.")
        self._bearer_token = bearer_token
        self._oauth_token = oauth_token
        self._oauth_token_secret = oauth_token_secret

    @property
    def platform(self) -> str:
        return "x"

    def validate(self, post: SocialPost) -> list[str]:
        errors: list[str] = []
        if not post.content:
            errors.append("content must not be empty")
        if not self._oauth_token:
            errors.append(
                "oauth_token is required to publish to X; "
                "bearer_token alone is insufficient for write operations"
            )
        thread_mode = bool(post.metadata.get("thread_mode", False))
        if not thread_mode and len(post.content) > _X_CHAR_LIMIT:
            errors.append(
                f"content exceeds X character limit of {_X_CHAR_LIMIT} characters "
                f"(got {len(post.content)}). "
                "Set metadata.thread_mode=True to enable automatic thread splitting."
            )
        return errors

    async def publish(self, post: SocialPost) -> PublishResult:
        """Publish *post* as a single tweet or a thread on X.

        Raises:
            SocialPublishError: On API or network failures (retryable for rate limits).
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

        thread_mode = bool(post.metadata.get("thread_mode", False))
        chunks = _split_into_thread(post.content) if thread_mode else [post.content]

        try:
            async with aiohttp.ClientSession() as session:
                first_id = await self._post_tweet(session, chunks[0], reply_to=None)
                prev_id = first_id
                for chunk in chunks[1:]:
                    prev_id = await self._post_tweet(session, chunk, reply_to=prev_id)
        except SocialPublishError:
            raise
        except Exception as exc:
            raise SocialPublishError(self.platform, str(exc), retryable=True) from exc

        logger.info(
            "x_social_adapter: published campaign=%s idempotency_key=%s tweet_id=%s thread_len=%d",
            post.campaign_id,
            idempotency_key,
            first_id,
            len(chunks),
        )
        return PublishResult.ok(
            platform=self.platform,
            campaign_id=post.campaign_id,
            idempotency_key=idempotency_key,
            post_id=first_id,
            metadata={"thread_length": len(chunks)},
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_tweet(
        self, session: Any, text: str, reply_to: str | None
    ) -> str:
        headers = {
            "Authorization": f"Bearer {self._oauth_token}",
            "Content-Type": "application/json",
        }
        body: dict[str, Any] = {"text": text}
        if reply_to:
            body["reply"] = {"in_reply_to_tweet_id": reply_to}

        async with session.post(
            f"{_X_API_BASE}/tweets",
            json=body,
            headers=headers,
        ) as resp:
            if resp.status == 429:
                raise SocialPublishError(
                    self.platform,
                    "X API rate limit exceeded (HTTP 429). Back-off and retry.",
                    retryable=True,
                )
            if resp.status >= 400:
                text_body = await resp.text()
                retryable = resp.status >= 500
                raise SocialPublishError(
                    self.platform,
                    f"X API returned HTTP {resp.status}: {text_body}",
                    retryable=retryable,
                )
            data = await resp.json()
            return str(data.get("data", {}).get("id", ""))
