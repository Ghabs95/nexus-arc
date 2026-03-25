"""Social Publisher Plugin.

Handles ``social:publish`` and ``social:dry_run`` tool calls from workflow agents.
Registered as a ``PluginKind.EVENT_HANDLER`` plugin; hooks into the workflow
completion pipeline to execute live publishes after the publisher agent approves.

Supported platforms (configured via project_config.yaml or env vars):
  - linkedin   → LinkedInSocialAdapter
  - discord    → DiscordSocialAdapter
  - x          → XSocialAdapter
  - meta       → MetaSocialAdapter

Configuration per platform in project_config.yaml:
  plugins:
    social_publisher:
      linkedin:
        access_token: "..."          # or env: NEXUS_LINKEDIN_ACCESS_TOKEN
        author_urn: "urn:li:person:..."  # or env: NEXUS_LINKEDIN_AUTHOR_URN
      discord:
        webhook_url: "..."           # or env: NEXUS_DISCORD_WEBHOOK_URL
      x:
        api_key: "..."
        api_secret: "..."
        access_token: "..."
        access_secret: "..."
"""

from __future__ import annotations

import logging
import os
from typing import Any

from nexus.adapters.social.base import SocialPost
from nexus.core.campaign import CampaignContext
from nexus.plugins.base import PluginHealthStatus

logger = logging.getLogger(__name__)


def _build_linkedin_adapter(config: dict[str, Any]):
    from nexus.adapters.social.linkedin_publisher import LinkedInSocialAdapter

    token = config.get("access_token") or os.getenv("NEXUS_LINKEDIN_ACCESS_TOKEN") or ""
    author_urn = config.get("author_urn") or os.getenv("NEXUS_LINKEDIN_AUTHOR_URN") or ""
    if not token or not author_urn:
        raise ValueError(
            "LinkedIn adapter requires access_token and author_urn. "
            "Set them in project_config.yaml plugins.social_publisher.linkedin "
            "or via NEXUS_LINKEDIN_ACCESS_TOKEN / NEXUS_LINKEDIN_AUTHOR_URN env vars."
        )
    return LinkedInSocialAdapter(access_token=token, author_urn=author_urn)


def _build_discord_adapter(config: dict[str, Any]):
    from nexus.adapters.social.discord_publisher import DiscordSocialAdapter

    webhook = config.get("webhook_url") or os.getenv("NEXUS_DISCORD_SOCIAL_WEBHOOK_URL") or ""
    if not webhook:
        raise ValueError(
            "Discord social adapter requires webhook_url. "
            "Set it in project_config.yaml plugins.social_publisher.discord "
            "or via NEXUS_DISCORD_SOCIAL_WEBHOOK_URL env var."
        )
    return DiscordSocialAdapter(webhook_url=webhook)


def _build_x_adapter(config: dict[str, Any]):
    from nexus.adapters.social.x_publisher import XSocialAdapter

    return XSocialAdapter(
        api_key=config.get("api_key") or os.getenv("NEXUS_X_API_KEY") or "",
        api_secret=config.get("api_secret") or os.getenv("NEXUS_X_API_SECRET") or "",
        access_token=config.get("access_token") or os.getenv("NEXUS_X_ACCESS_TOKEN") or "",
        access_secret=config.get("access_secret") or os.getenv("NEXUS_X_ACCESS_SECRET") or "",
    )


def _build_meta_adapter(config: dict[str, Any]):
    from nexus.adapters.social.meta_publisher import MetaSocialAdapter

    return MetaSocialAdapter(
        page_access_token=config.get("page_access_token") or os.getenv("NEXUS_META_PAGE_TOKEN") or "",
        page_id=config.get("page_id") or os.getenv("NEXUS_META_PAGE_ID") or "",
    )


_ADAPTER_BUILDERS = {
    "linkedin": _build_linkedin_adapter,
    "discord": _build_discord_adapter,
    "x": _build_x_adapter,
    "meta": _build_meta_adapter,
}


class SocialPublisherPlugin:
    """Executes social publish and dry_run operations for workflow publisher agents.

    Instantiated with platform credentials from project_config.yaml.
    Exposes ``dry_run()`` and ``publish()`` methods called by the workflow processor
    when it detects a publisher agent completion with ``social:publish`` intent.
    """

    def __init__(self, config: dict[str, Any]):
        self._config = config
        self._adapters: dict[str, Any] = {}
        self._last_ok = True
        self._build_configured_adapters()

    def _build_configured_adapters(self) -> None:
        for platform, builder in _ADAPTER_BUILDERS.items():
            platform_cfg = self._config.get(platform, {})
            if not platform_cfg and not _has_env_creds(platform):
                continue
            try:
                self._adapters[platform] = builder(platform_cfg)
                logger.info("SocialPublisherPlugin: %s adapter initialized", platform)
            except Exception as exc:
                logger.warning("SocialPublisherPlugin: failed to init %s adapter: %s", platform, exc)

    def available_platforms(self) -> list[str]:
        return list(self._adapters.keys())

    async def dry_run(self, *, platform: str, post_text: str, campaign_id: str = "") -> dict[str, Any]:
        """Validate post content without publishing. Returns dry_run result dict."""
        adapter = self._adapters.get(platform)
        if not adapter:
            return {"ok": False, "error": f"No adapter configured for platform: {platform}"}

        post = SocialPost(
            content=post_text,
            platform=platform,
            campaign_id=campaign_id or "dry-run",
            scheduled_time_utc="",
        )
        try:
            result = await adapter.dry_run(post)
            return {
                "ok": result.success,
                "platform": platform,
                "char_count": len(post_text),
                "preview": post_text[:100],
                "error": result.error_message if not result.success else None,
            }
        except Exception as exc:
            logger.error("SocialPublisherPlugin dry_run error (%s): %s", platform, exc)
            return {"ok": False, "platform": platform, "error": str(exc)}

    async def publish(
        self,
        *,
        platform: str,
        post_text: str,
        campaign_id: str,
        issue_number: str = "",
    ) -> dict[str, Any]:
        """Publish post to the given platform. Returns publish result dict."""
        adapter = self._adapters.get(platform)
        if not adapter:
            return {"ok": False, "error": f"No adapter configured for platform: {platform}"}

        post = SocialPost(
            content=post_text,
            platform=platform,
            campaign_id=campaign_id,
            scheduled_time_utc="",
        )
        try:
            result = await adapter.publish(post)
            if result.success:
                self._last_ok = True
                return {
                    "ok": True,
                    "platform": platform,
                    "post_url": result.post_url or "",
                    "post_id": result.post_id or "",
                }
            self._last_ok = False
            return {"ok": False, "platform": platform, "error": result.error_message or "Unknown error"}
        except Exception as exc:
            self._last_ok = False
            logger.error("SocialPublisherPlugin publish error (%s): %s", platform, exc)
            return {"ok": False, "platform": platform, "error": str(exc)}

    async def on_load(self, registry: Any) -> None:
        logger.info(
            "SocialPublisherPlugin loaded. Available platforms: %s",
            self.available_platforms() or ["none — add credentials to enable"],
        )

    async def on_unload(self) -> None:
        logger.info("SocialPublisherPlugin unloaded")

    async def health_check(self) -> PluginHealthStatus:
        platforms = self.available_platforms()
        return PluginHealthStatus(
            healthy=bool(platforms) and self._last_ok,
            name="social-publisher",
            details=f"Platforms: {', '.join(platforms) or 'none configured'}",
        )


def _has_env_creds(platform: str) -> bool:
    """Return True if any env var credential exists for this platform."""
    env_keys = {
        "linkedin": ["NEXUS_LINKEDIN_ACCESS_TOKEN"],
        "discord": ["NEXUS_DISCORD_SOCIAL_WEBHOOK_URL"],
        "x": ["NEXUS_X_API_KEY"],
        "meta": ["NEXUS_META_PAGE_TOKEN"],
    }
    return any(os.getenv(k) for k in env_keys.get(platform, []))


# ---------------------------------------------------------------------------
# Plugin registration
# ---------------------------------------------------------------------------


def register_plugins(registry: Any) -> None:
    """Register the social publisher plugin."""
    from nexus.plugins.base import PluginKind

    registry.register_factory(
        kind=PluginKind.EVENT_HANDLER,
        name="social-publisher",
        version="1.0.0",
        factory=lambda config: SocialPublisherPlugin(config),
        description="Executes social:publish and social:dry_run for workflow publisher agents",
    )
