from __future__ import annotations

import pytest

from nexus.adapters.social.base import PublishResult, SocialPlatformAdapter, SocialPost
import nexus.plugins.builtin.social_publisher_plugin as social_publisher_plugin
from nexus.plugins.builtin.social_publisher_plugin import SocialPublisherPlugin
from nexus.core.social_publish_linkedin import publish_linkedin_text


class _StubAdapter(SocialPlatformAdapter):
    def __init__(self):
        self.last_post: SocialPost | None = None

    @property
    def platform(self) -> str:
        return "linkedin"

    def validate(self, post: SocialPost) -> list[str]:
        self.last_post = post
        return []

    async def publish(self, post: SocialPost) -> PublishResult:
        self.last_post = post
        return PublishResult.ok(
            platform="linkedin",
            campaign_id=post.campaign_id,
            idempotency_key="idem-1",
            post_id="ugc-123",
        )


class _FailingDryRunAdapter(SocialPlatformAdapter):
    @property
    def platform(self) -> str:
        return "linkedin"

    def validate(self, post: SocialPost) -> list[str]:
        return ["content too short"]

    async def publish(self, post: SocialPost) -> PublishResult:
        raise NotImplementedError


@pytest.mark.asyncio
async def test_publish_returns_post_id_without_post_url_attribute():
    plugin = SocialPublisherPlugin({})
    plugin._adapters["linkedin"] = _StubAdapter()

    result = await plugin.publish(
        platform="linkedin",
        post_text="Hello LinkedIn",
        campaign_id="camp-1",
    )

    assert result["ok"] is True
    assert result["platform"] == "linkedin"
    assert result["post_id"] == "ugc-123"
    assert result["post_url"] == ""
    assert result["published_at"]


@pytest.mark.asyncio
async def test_dry_run_returns_error_field_on_validation_failure():
    plugin = SocialPublisherPlugin({})
    plugin._adapters["linkedin"] = _FailingDryRunAdapter()

    result = await plugin.dry_run(
        platform="linkedin",
        post_text="bad",
        campaign_id="camp-1",
    )

    assert result == {
        "ok": False,
        "platform": "linkedin",
        "char_count": 3,
        "preview": "bad",
        "error": "content too short",
    }


@pytest.mark.asyncio
async def test_publish_injects_default_link_preview_metadata_for_linkedin():
    adapter = _StubAdapter()
    plugin = SocialPublisherPlugin(
        {
            "linkedin": {
                "require_link_preview": True,
                "default_link_url": "https://github.com/ghabs-org/nexus-router",
                "default_link_title": "ghabs-org/nexus-router",
                "default_link_description": "AI routing runtime for Nexus",
            }
        }
    )
    plugin._adapters["linkedin"] = adapter

    result = await plugin.publish(
        platform="linkedin",
        post_text="Hello LinkedIn",
        campaign_id="camp-2",
    )

    assert result["ok"] is True
    assert adapter.last_post is not None
    assert adapter.last_post.metadata == {
        "link_url": "https://github.com/ghabs-org/nexus-router",
        "link_title": "ghabs-org/nexus-router",
        "link_description": "AI routing runtime for Nexus",
    }


@pytest.mark.asyncio
async def test_publish_requires_link_preview_when_enabled_and_missing():
    plugin = SocialPublisherPlugin({"linkedin": {"require_link_preview": True}})
    plugin._adapters["linkedin"] = _StubAdapter()

    result = await plugin.publish(
        platform="linkedin",
        post_text="Hello LinkedIn",
        campaign_id="camp-3",
    )

    assert result == {
        "ok": False,
        "platform": "linkedin",
        "error": (
            "LinkedIn publishing requires a repo/article link preview. "
            "Provide metadata.link_url or configure plugins.social_publisher.linkedin.default_link_url."
        ),
    }


@pytest.mark.asyncio
async def test_publish_persists_social_publish_event(monkeypatch):
    calls: list[dict] = []

    def _fake_record_social_publish_event(**kwargs):
        calls.append(kwargs)
        return None

    monkeypatch.setattr(social_publisher_plugin, "record_social_publish_event", _fake_record_social_publish_event)

    plugin = SocialPublisherPlugin({"nexus_id": "user-123"})
    plugin._adapters["linkedin"] = _StubAdapter()

    result = await plugin.publish(
        platform="linkedin",
        post_text="Hello LinkedIn",
        campaign_id="camp-4",
        metadata={
            "link_url": "https://github.com/ghabs-org/nexus-arc",
            "link_title": "ghabs-org/nexus-arc",
        },
    )

    assert result["ok"] is True
    assert calls == [
        {
            "nexus_id": "user-123",
            "platform": "linkedin",
            "campaign_id": "camp-4",
            "post_id": "ugc-123",
            "post_url": "",
            "idempotency_key": "idem-1",
            "published_at": result["published_at"],
            "metadata": {
                "link_url": "https://github.com/ghabs-org/nexus-arc",
                "link_title": "ghabs-org/nexus-arc",
                "link_description": None,
                "visibility": None,
            },
        }
    ]


def test_publish_linkedin_text_preserves_link_preview_metadata(monkeypatch):
    calls: list[dict] = []

    class _StubConnection:
        access_token = "token"
        author_urn = "urn:li:person:abc123"

    class _StubAdapter:
        def __init__(self, access_token: str, author_urn: str):
            assert access_token == "token"
            assert author_urn == "urn:li:person:abc123"

        async def dry_run(self, post: SocialPost):
            calls.append({"kind": "dry_run", "metadata": dict(post.metadata), "content": post.content})
            return PublishResult.ok(
                platform="linkedin",
                campaign_id=post.campaign_id,
                idempotency_key="idem-linkedin-dry",
                post_id="dry-run",
                dry_run=True,
            )

        async def publish(self, post: SocialPost):
            calls.append({"kind": "publish", "metadata": dict(post.metadata), "content": post.content})
            return PublishResult.ok(
                platform="linkedin",
                campaign_id=post.campaign_id,
                idempotency_key="idem-linkedin-live",
                post_id="ugc-999",
                dry_run=False,
            )

    monkeypatch.setattr(
        "nexus.core.social_publish_linkedin.linkedin_connector_service.get_connection",
        lambda nexus_id: _StubConnection(),
    )
    monkeypatch.setattr("nexus.core.social_publish_linkedin.LinkedInSocialAdapter", _StubAdapter)
    monkeypatch.setattr("nexus.core.social_publish_linkedin._record_social_publish_event", lambda **kwargs: None)

    result = publish_linkedin_text(
        content="hello",
        campaign_id="camp-linkedin",
        nexus_id="user-123",
        dry_run=True,
        metadata={
            "link_preview_url": "https://github.com/ghabs-org/nexus-router",
            "link_preview_title": "ghabs-org/nexus-router",
            "link_preview_description": "Router repo",
            "visibility": "PUBLIC",
        },
    )

    assert result["ok"] is True
    assert result["dry_run"] is True

    result = publish_linkedin_text(
        content="hello",
        campaign_id="camp-linkedin",
        nexus_id="user-123",
        dry_run=False,
        metadata={
            "link_preview_url": "https://github.com/ghabs-org/nexus-router",
            "link_preview_title": "ghabs-org/nexus-router",
            "link_preview_description": "Router repo",
            "visibility": "PUBLIC",
        },
    )

    assert result["ok"] is True
    assert result["dry_run"] is False
    assert result["post_id"] == "ugc-999"
    assert result["metadata"] == {
        "link_url": "https://github.com/ghabs-org/nexus-router",
        "link_title": "ghabs-org/nexus-router",
        "link_description": "Router repo",
        "visibility": "PUBLIC",
    }
    assert calls == [
        {
            "kind": "dry_run",
            "metadata": {
                "link_preview_url": "https://github.com/ghabs-org/nexus-router",
                "link_preview_title": "ghabs-org/nexus-router",
                "link_preview_description": "Router repo",
                "visibility": "PUBLIC",
                "link_url": "https://github.com/ghabs-org/nexus-router",
                "link_title": "ghabs-org/nexus-router",
                "link_description": "Router repo",
            },
            "content": "hello",
        },
        {
            "kind": "publish",
            "metadata": {
                "link_preview_url": "https://github.com/ghabs-org/nexus-router",
                "link_preview_title": "ghabs-org/nexus-router",
                "link_preview_description": "Router repo",
                "visibility": "PUBLIC",
                "link_url": "https://github.com/ghabs-org/nexus-router",
                "link_title": "ghabs-org/nexus-router",
                "link_description": "Router repo",
            },
            "content": "hello",
        },
    ][1:]
