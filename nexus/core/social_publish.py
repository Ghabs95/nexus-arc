"""Live publish execution, idempotency, and retry logic for social campaigns."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import random
from typing import TYPE_CHECKING, Any

from nexus.adapters.social.base import PublishResult, SocialPost, SocialPublishError, derive_idempotency_key as derive_idempotency_key  # re-export
from nexus.core.campaign import CampaignState, PublishRecord

if TYPE_CHECKING:
    from nexus.adapters.social.base import SocialPlatformAdapter

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Idempotency
# ---------------------------------------------------------------------------

_IDEMPOTENCY_HASH_LENGTH = 16  # First N hex chars of SHA-256


def derive_idempotency_key(campaign_id: str, platform: str, scheduled_time_utc: str) -> str:
    """Derive a stable idempotency key from the tuple (campaign_id, platform, scheduled_time_utc).

    The key is a short hex digest safe to embed in external API calls.  Two
    calls with the same three inputs always produce the same key, ensuring
    publish operations are idempotent across retries and workflow restarts.

    Args:
        campaign_id: Unique campaign identifier.
        platform: Target platform (e.g. "x", "linkedin").
        scheduled_time_utc: ISO-8601 UTC timestamp for the post; use "" for
            immediate publishing.

    Returns:
        Hex string of length :data:`_IDEMPOTENCY_HASH_LENGTH` × 2.
    """
    raw = f"{campaign_id}:{platform}:{scheduled_time_utc}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[: _IDEMPOTENCY_HASH_LENGTH * 2]


# ---------------------------------------------------------------------------
# Retry with exponential back-off
# ---------------------------------------------------------------------------

_DEFAULT_MAX_RETRIES = 3
_DEFAULT_BASE_DELAY = 1.0  # seconds
_DEFAULT_MAX_DELAY = 60.0  # seconds
_DEFAULT_JITTER_FACTOR = 0.25  # ±25 % jitter


def _backoff_delay(attempt: int, base: float, maximum: float, jitter: float) -> float:
    """Return the sleep duration for *attempt* (0-indexed) with bounded jitter."""
    delay = min(base * (2**attempt), maximum)
    jitter_amount = delay * jitter * (2 * random.random() - 1)
    return max(0.0, delay + jitter_amount)


async def publish_with_retry(
    adapter: "SocialPlatformAdapter",
    post: SocialPost,
    *,
    max_retries: int = _DEFAULT_MAX_RETRIES,
    base_delay: float = _DEFAULT_BASE_DELAY,
    max_delay: float = _DEFAULT_MAX_DELAY,
    jitter_factor: float = _DEFAULT_JITTER_FACTOR,
) -> PublishResult:
    """Call ``adapter.publish(post)`` with exponential back-off retries.

    Only :class:`~nexus.adapters.social.base.SocialPublishError` instances
    with ``retryable=True`` (or ``OSError`` / ``asyncio.TimeoutError``) trigger
    a retry.  Non-retryable errors surface immediately.

    Args:
        adapter: The platform adapter to use.
        post: The post to publish.
        max_retries: Total number of attempts (1 = no retries).
        base_delay: Initial back-off delay in seconds.
        max_delay: Cap on back-off delay in seconds.
        jitter_factor: Fraction of the computed delay to randomise.

    Returns:
        :class:`PublishResult` from the final successful or failed attempt.
    """
    idempotency_key = derive_idempotency_key(
        post.campaign_id, adapter.platform, post.scheduled_time_utc or ""
    )
    last_error: str = ""

    for attempt in range(max_retries):
        try:
            result = await adapter.publish(post)
            if result.success:
                logger.info(
                    "social_publish: published campaign=%s platform=%s attempt=%d post_id=%s",
                    post.campaign_id,
                    adapter.platform,
                    attempt,
                    result.post_id,
                )
            return result
        except SocialPublishError as exc:
            last_error = str(exc)
            if not exc.retryable or attempt >= max_retries - 1:
                logger.error(
                    "social_publish: non-retryable error campaign=%s platform=%s: %s",
                    post.campaign_id,
                    adapter.platform,
                    last_error,
                )
                break
            delay = _backoff_delay(attempt, base_delay, max_delay, jitter_factor)
            logger.warning(
                "social_publish: retryable error campaign=%s platform=%s attempt=%d "
                "retrying in %.2fs: %s",
                post.campaign_id,
                adapter.platform,
                attempt,
                delay,
                last_error,
            )
            await asyncio.sleep(delay)
        except (OSError, asyncio.TimeoutError) as exc:
            last_error = str(exc)
            if attempt >= max_retries - 1:
                break
            delay = _backoff_delay(attempt, base_delay, max_delay, jitter_factor)
            logger.warning(
                "social_publish: transient error campaign=%s platform=%s attempt=%d "
                "retrying in %.2fs: %s",
                post.campaign_id,
                adapter.platform,
                attempt,
                delay,
                last_error,
            )
            await asyncio.sleep(delay)

    return PublishResult.fail(
        platform=adapter.platform,
        campaign_id=post.campaign_id,
        idempotency_key=idempotency_key,
        error=last_error or "publish failed after retries",
    )


# ---------------------------------------------------------------------------
# SocialPublishExecutor
# ---------------------------------------------------------------------------


class SocialPublishExecutor:
    """Orchestrates publishing a :class:`~nexus.core.campaign.CampaignState`
    across all registered adapters.

    Adapters are registered by platform name.  The executor supports both
    ``dry_run`` (validation only) and ``live`` modes, and updates the
    ``CampaignState.publish_results`` list after each attempt.

    Example::

        executor = SocialPublishExecutor()
        executor.register_adapter(DiscordSocialAdapter(webhook_url="https://..."))
        results = await executor.execute(state, mode="dry_run")
    """

    def __init__(self) -> None:
        self._adapters: dict[str, "SocialPlatformAdapter"] = {}

    def register_adapter(self, adapter: "SocialPlatformAdapter") -> None:
        """Register *adapter* under its :attr:`~SocialPlatformAdapter.platform` key."""
        self._adapters[adapter.platform] = adapter
        logger.debug("SocialPublishExecutor: registered adapter for %r", adapter.platform)

    def unregister_adapter(self, platform: str) -> None:
        """Remove the adapter for *platform* if present."""
        self._adapters.pop(platform, None)

    async def execute(
        self,
        state: CampaignState,
        *,
        mode: str = "dry_run",
        retry_config: dict[str, Any] | None = None,
    ) -> list[PublishResult]:
        """Publish all channels in ``state.campaign.channels``.

        Args:
            state: Mutable campaign state; ``publish_results`` is updated in place.
            mode: ``"dry_run"`` (validate only) or ``"live"`` (real publish).
            retry_config: Optional overrides for :func:`publish_with_retry` kwargs.

        Returns:
            List of :class:`PublishResult` — one per channel.
        """
        if state.content_bundle is None:
            raise ValueError("CampaignState.content_bundle must be set before publishing.")

        results: list[PublishResult] = []
        retry_kwargs: dict[str, Any] = retry_config or {}

        for platform in state.campaign.channels:
            platform_content = state.content_bundle.get_platform_content(platform)
            if platform_content is None:
                logger.warning(
                    "SocialPublishExecutor: no content for platform %r in campaign %s — skipping",
                    platform,
                    state.campaign.campaign_id,
                )
                continue

            adapter = self._adapters.get(platform)
            if adapter is None:
                logger.warning(
                    "SocialPublishExecutor: no adapter for platform %r — skipping", platform
                )
                continue

            post = SocialPost(
                platform=platform,
                content=platform_content.copy,
                campaign_id=state.campaign.campaign_id,
                media_urls=platform_content.media_refs,
                metadata=platform_content.metadata,
                scheduled_time_utc=platform_content.scheduled_time_utc,
            )

            if mode == "dry_run":
                result = await adapter.dry_run(post)
            else:
                result = await publish_with_retry(adapter, post, **retry_kwargs)

            results.append(result)
            state.add_publish_result(
                PublishRecord(
                    platform=platform,
                    success=result.success,
                    post_id=result.post_id,
                    idempotency_key=result.idempotency_key,
                    dry_run=result.dry_run,
                    error=result.error,
                    published_at=result.published_at,
                )
            )

        return results
