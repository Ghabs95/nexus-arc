"""Tests for the social platform adapter layer.

Covers:
- SocialPost secret guard
- PublishResult helpers
- SocialPlatformAdapter.dry_run (via concrete stub)
- derive_idempotency_key determinism and uniqueness
- _split_into_thread chunking
- SocialPublishExecutor dry-run and live-publish paths
- CampaignContext / ContentBundle / CampaignState helpers
- ApprovalGate.publish_gate and PUBLISH gate type
- publish_with_retry back-off
"""

from __future__ import annotations

import asyncio
import pytest

from nexus.adapters.social.base import (
    PublishResult,
    SocialPlatformAdapter,
    SocialPost,
    SocialPublishError,
    _guard_no_secrets,
)
from nexus.adapters.social.discord_publisher import DiscordSocialAdapter
from nexus.adapters.social.linkedin_publisher import LinkedInSocialAdapter
from nexus.adapters.social.meta_publisher import MetaSocialAdapter
from nexus.adapters.social.x_publisher import XSocialAdapter, _split_into_thread
from nexus.core.campaign import (
    ApprovalDecision,
    CampaignContext,
    CampaignState,
    CampaignStatus,
    ContentBundle,
    PlatformContent,
    PublishRecord,
)
from nexus.core.models import ApprovalGate, ApprovalGateType
from nexus.core.social_publish import (
    SocialPublishExecutor,
    derive_idempotency_key,
    publish_with_retry,
)


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


class _OKAdapter(SocialPlatformAdapter):
    """Stub adapter that always succeeds."""

    def __init__(self, platform_name: str = "stub"):
        self._platform = platform_name
        self.publish_calls: list[SocialPost] = []

    @property
    def platform(self) -> str:
        return self._platform

    def validate(self, post: SocialPost) -> list[str]:
        return []

    async def publish(self, post: SocialPost) -> PublishResult:
        self.publish_calls.append(post)
        key = derive_idempotency_key(post.campaign_id, self.platform, post.scheduled_time_utc or "")
        return PublishResult.ok(
            platform=self.platform,
            campaign_id=post.campaign_id,
            idempotency_key=key,
            post_id="msg-123",
        )


class _FailAdapter(SocialPlatformAdapter):
    """Stub adapter that always raises a retryable error."""

    def __init__(self, retryable: bool = True, fail_count: int = 99):
        self._retryable = retryable
        self._fail_count = fail_count
        self.attempts = 0

    @property
    def platform(self) -> str:
        return "fail_stub"

    def validate(self, post: SocialPost) -> list[str]:
        return []

    async def publish(self, post: SocialPost) -> PublishResult:
        self.attempts += 1
        if self.attempts <= self._fail_count:
            raise SocialPublishError("fail_stub", "simulated error", retryable=self._retryable)
        key = derive_idempotency_key(post.campaign_id, self.platform, post.scheduled_time_utc or "")
        return PublishResult.ok(
            platform=self.platform,
            campaign_id=post.campaign_id,
            idempotency_key=key,
            post_id="recovered",
        )


def _make_post(platform: str = "stub", content: str = "Hello campaign!") -> SocialPost:
    return SocialPost(platform=platform, content=content, campaign_id="camp-001")


def _make_state(channels: list[str] | None = None) -> CampaignState:
    channels = channels or ["discord"]
    ctx = CampaignContext(
        campaign_id="camp-001",
        objective="awareness",
        audience="developers",
        channels=channels,
    )
    bundle = ContentBundle(
        campaign_id="camp-001",
        platforms=[
            PlatformContent(platform=ch, copy=f"Post for {ch}") for ch in channels
        ],
    )
    state = CampaignState(campaign=ctx, content_bundle=bundle)
    return state


# ===========================================================================
# SocialPost
# ===========================================================================


def test_social_post_valid():
    post = _make_post()
    assert post.campaign_id == "camp-001"
    assert post.media_urls == []


def test_social_post_rejects_bearer_secret():
    with pytest.raises(ValueError, match="raw secret token"):
        SocialPost(platform="x", content="Bearer abc123", campaign_id="c1")


def test_social_post_rejects_access_token_in_metadata():
    with pytest.raises(ValueError, match="raw secret token"):
        SocialPost(
            platform="x",
            content="hello",
            campaign_id="c1",
            metadata={"note": "access_token=secret123"},
        )


def test_guard_no_secrets_clean():
    _guard_no_secrets("This is safe content about marketing.")


# ===========================================================================
# PublishResult
# ===========================================================================


def test_publish_result_ok():
    r = PublishResult.ok("discord", "camp-1", "key1", "msg-99")
    assert r.success
    assert r.post_id == "msg-99"
    assert r.error is None
    assert r.published_at is not None


def test_publish_result_fail():
    r = PublishResult.fail("x", "camp-1", "key1", "rate limit")
    assert not r.success
    assert r.error == "rate limit"
    assert r.post_id is None


def test_publish_result_dry_run():
    r = PublishResult.ok("linkedin", "camp-1", "key1", "dry-run", dry_run=True)
    assert r.dry_run


# ===========================================================================
# derive_idempotency_key
# ===========================================================================


def test_idempotency_key_deterministic():
    k1 = derive_idempotency_key("camp-1", "discord", "2025-01-01T00:00:00Z")
    k2 = derive_idempotency_key("camp-1", "discord", "2025-01-01T00:00:00Z")
    assert k1 == k2


def test_idempotency_key_different_platforms():
    k_discord = derive_idempotency_key("camp-1", "discord", "2025-01-01T00:00:00Z")
    k_x = derive_idempotency_key("camp-1", "x", "2025-01-01T00:00:00Z")
    assert k_discord != k_x


def test_idempotency_key_different_campaigns():
    k1 = derive_idempotency_key("camp-1", "discord", "2025-01-01T00:00:00Z")
    k2 = derive_idempotency_key("camp-2", "discord", "2025-01-01T00:00:00Z")
    assert k1 != k2


def test_idempotency_key_length():
    from nexus.core.social_publish import _IDEMPOTENCY_HASH_LENGTH

    k = derive_idempotency_key("camp-1", "x", "")
    assert len(k) == _IDEMPOTENCY_HASH_LENGTH * 2


# ===========================================================================
# _split_into_thread
# ===========================================================================


def test_split_short_message_no_split():
    text = "Hello world"
    chunks = _split_into_thread(text, limit=280)
    assert chunks == [text]


def test_split_long_message():
    text = " ".join(["word"] * 200)
    chunks = _split_into_thread(text, limit=280)
    assert len(chunks) > 1
    for chunk in chunks:
        assert len(chunk) <= 280


def test_split_preserves_content():
    text = " ".join([f"w{i}" for i in range(100)])
    chunks = _split_into_thread(text, limit=50)
    rejoined = " ".join(chunks)
    assert rejoined == text


# ===========================================================================
# SocialPlatformAdapter.dry_run
# ===========================================================================


@pytest.mark.asyncio
async def test_dry_run_success():
    adapter = _OKAdapter()
    post = _make_post("stub")
    result = await adapter.dry_run(post)
    assert result.success
    assert result.dry_run
    assert result.post_id == "dry-run"


@pytest.mark.asyncio
async def test_dry_run_validation_failure():
    class _BadAdapter(SocialPlatformAdapter):
        @property
        def platform(self) -> str:
            return "bad"

        def validate(self, post: SocialPost) -> list[str]:
            return ["content too short", "missing hashtag"]

        async def publish(self, post: SocialPost) -> PublishResult:  # pragma: no cover
            raise NotImplementedError

    adapter = _BadAdapter()
    post = _make_post("bad")
    result = await adapter.dry_run(post)
    assert not result.success
    assert result.dry_run
    assert "content too short" in result.error


# ===========================================================================
# DiscordSocialAdapter
# ===========================================================================


def test_discord_adapter_requires_credentials():
    with pytest.raises(ValueError):
        DiscordSocialAdapter()


def test_discord_adapter_validate_empty_content():
    adapter = DiscordSocialAdapter(webhook_url="https://discord.com/api/webhooks/1/abc")
    post = SocialPost(platform="discord", content="", campaign_id="c1")
    errors = adapter.validate(post)
    assert any("empty" in e for e in errors)


def test_discord_adapter_validate_too_long():
    adapter = DiscordSocialAdapter(webhook_url="https://discord.com/api/webhooks/1/abc")
    post = SocialPost(platform="discord", content="x" * 2001, campaign_id="c1")
    errors = adapter.validate(post)
    assert any("limit" in e for e in errors)


def test_discord_adapter_validate_ok():
    adapter = DiscordSocialAdapter(webhook_url="https://discord.com/api/webhooks/1/abc")
    post = SocialPost(platform="discord", content="Hello Discord!", campaign_id="c1")
    errors = adapter.validate(post)
    assert errors == []


# ===========================================================================
# XSocialAdapter
# ===========================================================================


def test_x_adapter_requires_credentials():
    with pytest.raises(ValueError):
        XSocialAdapter()


def test_x_adapter_validate_no_oauth_token():
    adapter = XSocialAdapter(bearer_token="bt123")
    post = SocialPost(platform="x", content="Hello!", campaign_id="c1")
    errors = adapter.validate(post)
    assert any("oauth_token" in e for e in errors)


def test_x_adapter_validate_too_long_no_thread_mode():
    adapter = XSocialAdapter(bearer_token="bt", oauth_token="ot")
    post = SocialPost(platform="x", content="x" * 281, campaign_id="c1")
    errors = adapter.validate(post)
    assert any("character limit" in e for e in errors)


def test_x_adapter_validate_ok_with_thread_mode():
    adapter = XSocialAdapter(bearer_token="bt", oauth_token="ot")
    post = SocialPost(platform="x", content="x" * 500, campaign_id="c1", metadata={"thread_mode": True})
    errors = adapter.validate(post)
    assert errors == []


# ===========================================================================
# LinkedInSocialAdapter
# ===========================================================================


def test_linkedin_adapter_requires_credentials():
    with pytest.raises(ValueError):
        LinkedInSocialAdapter("", "urn:li:person:abc")
    with pytest.raises(ValueError):
        LinkedInSocialAdapter("tok", "")


def test_linkedin_adapter_validate_bad_link_url():
    adapter = LinkedInSocialAdapter("tok", "urn:li:person:abc")
    post = SocialPost(
        platform="linkedin",
        content="Check this",
        campaign_id="c1",
        metadata={"link_url": "http://not-https.com"},
    )
    errors = adapter.validate(post)
    assert any("https" in e for e in errors)


def test_linkedin_adapter_validate_ok():
    adapter = LinkedInSocialAdapter("tok", "urn:li:person:abc")
    post = SocialPost(platform="linkedin", content="Professional update", campaign_id="c1")
    assert adapter.validate(post) == []


# ===========================================================================
# MetaSocialAdapter
# ===========================================================================


def test_meta_adapter_requires_page_id_for_facebook():
    with pytest.raises(ValueError, match="page_id"):
        MetaSocialAdapter(page_access_token="tok", target="facebook")


def test_meta_adapter_requires_ig_account_for_instagram():
    with pytest.raises(ValueError, match="instagram_account_id"):
        MetaSocialAdapter(page_access_token="tok", target="instagram")


def test_meta_adapter_validate_instagram_needs_media():
    adapter = MetaSocialAdapter(
        page_access_token="tok",
        instagram_account_id="ig123",
        target="instagram",
    )
    post = SocialPost(platform="meta_instagram", content="Caption", campaign_id="c1")
    errors = adapter.validate(post)
    assert any("media_url" in e for e in errors)


def test_meta_adapter_platform_name():
    fb = MetaSocialAdapter(page_access_token="tok", page_id="pg123", target="facebook")
    assert fb.platform == "meta_facebook"
    ig = MetaSocialAdapter(
        page_access_token="tok", instagram_account_id="ig123", target="instagram"
    )
    assert ig.platform == "meta_instagram"


# ===========================================================================
# publish_with_retry
# ===========================================================================


@pytest.mark.asyncio
async def test_publish_with_retry_succeeds_first_attempt():
    adapter = _OKAdapter()
    post = _make_post("stub")
    result = await publish_with_retry(adapter, post, max_retries=3, base_delay=0)
    assert result.success
    assert adapter.publish_calls


@pytest.mark.asyncio
async def test_publish_with_retry_non_retryable_error_fails_fast():
    adapter = _FailAdapter(retryable=False)
    post = SocialPost(platform="fail_stub", content="Hello", campaign_id="c1")
    result = await publish_with_retry(adapter, post, max_retries=3, base_delay=0)
    assert not result.success
    assert adapter.attempts == 1  # No retries


@pytest.mark.asyncio
async def test_publish_with_retry_exhausts_retries():
    adapter = _FailAdapter(retryable=True, fail_count=99)
    post = SocialPost(platform="fail_stub", content="Hello", campaign_id="c1")
    result = await publish_with_retry(adapter, post, max_retries=3, base_delay=0)
    assert not result.success
    assert adapter.attempts == 3


@pytest.mark.asyncio
async def test_publish_with_retry_recovers_after_transient_failure():
    adapter = _FailAdapter(retryable=True, fail_count=1)
    post = SocialPost(platform="fail_stub", content="Hello", campaign_id="c1")
    result = await publish_with_retry(adapter, post, max_retries=3, base_delay=0)
    assert result.success
    assert adapter.attempts == 2


# ===========================================================================
# SocialPublishExecutor
# ===========================================================================


@pytest.mark.asyncio
async def test_executor_dry_run():
    executor = SocialPublishExecutor()
    executor.register_adapter(_OKAdapter("discord"))
    state = _make_state(["discord"])
    results = await executor.execute(state, mode="dry_run")
    assert len(results) == 1
    assert results[0].dry_run
    assert results[0].success


@pytest.mark.asyncio
async def test_executor_live_publish():
    executor = SocialPublishExecutor()
    executor.register_adapter(_OKAdapter("discord"))
    state = _make_state(["discord"])
    results = await executor.execute(state, mode="live")
    assert results[0].success
    assert not results[0].dry_run


@pytest.mark.asyncio
async def test_executor_skips_missing_adapter():
    executor = SocialPublishExecutor()
    state = _make_state(["discord"])
    results = await executor.execute(state, mode="live")
    assert results == []
    assert state.publish_results == []


@pytest.mark.asyncio
async def test_executor_requires_content_bundle():
    executor = SocialPublishExecutor()
    ctx = CampaignContext(campaign_id="c1", objective="o", audience="a", channels=["discord"])
    state = CampaignState(campaign=ctx)
    with pytest.raises(ValueError, match="content_bundle"):
        await executor.execute(state)


@pytest.mark.asyncio
async def test_executor_updates_campaign_state():
    executor = SocialPublishExecutor()
    executor.register_adapter(_OKAdapter("discord"))
    state = _make_state(["discord"])
    await executor.execute(state, mode="live")
    assert len(state.publish_results) == 1
    assert state.publish_results[0].success
    assert state.publish_results[0].platform == "discord"


@pytest.mark.asyncio
async def test_executor_multi_channel():
    executor = SocialPublishExecutor()
    executor.register_adapter(_OKAdapter("discord"))
    executor.register_adapter(_OKAdapter("linkedin"))
    state = _make_state(["discord", "linkedin"])
    results = await executor.execute(state, mode="live")
    assert len(results) == 2
    assert all(r.success for r in results)


# ===========================================================================
# CampaignState helpers
# ===========================================================================


def test_campaign_state_approved():
    state = _make_state()
    assert not state.is_approved()
    state.add_approval(ApprovalDecision(approved=True, reviewer="alice"))
    assert state.is_approved()
    assert state.status == CampaignStatus.APPROVED


def test_campaign_state_rejected():
    state = _make_state()
    state.add_approval(ApprovalDecision(approved=False, reviewer="bob", notes="unsafe claim"))
    assert state.is_rejected()
    assert state.status == CampaignStatus.REJECTED


def test_campaign_state_all_published():
    state = _make_state(["discord", "x"])
    assert not state.all_published()
    state.add_publish_result(
        PublishRecord(platform="discord", success=True, post_id="1", idempotency_key="k1")
    )
    assert not state.all_published()
    state.add_publish_result(
        PublishRecord(platform="x", success=True, post_id="2", idempotency_key="k2")
    )
    assert state.all_published()


def test_campaign_state_to_dict():
    state = _make_state()
    d = state.to_dict()
    assert isinstance(d, dict)
    assert "campaign" in d
    assert d["campaign"]["campaign_id"] == "camp-001"


def test_content_bundle_get_platform_content():
    bundle = ContentBundle(
        campaign_id="c1",
        platforms=[PlatformContent(platform="x", copy="Hello X")],
    )
    assert bundle.get_platform_content("x").copy == "Hello X"
    assert bundle.get_platform_content("linkedin") is None


# ===========================================================================
# ApprovalGate publish gate
# ===========================================================================


def test_publish_gate_type():
    gate = ApprovalGate.publish_gate()
    assert gate.gate_type == ApprovalGateType.PUBLISH
    assert gate.required


def test_publish_gate_restricts_live_publish_tools():
    gate = ApprovalGate.publish_gate()
    assert "social:live_publish" in gate.tool_restrictions
    assert "social:publish" in gate.tool_restrictions


def test_approval_gate_type_has_publish():
    assert ApprovalGateType.PUBLISH.value == "publish"
