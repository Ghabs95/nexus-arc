"""Social platform publishing adapters."""

from nexus.adapters.social.base import (
    PublishResult,
    SocialPlatformAdapter,
    SocialPost,
    SocialPublishError,
)
from nexus.adapters.social.discord_publisher import DiscordSocialAdapter
from nexus.adapters.social.linkedin_publisher import LinkedInSocialAdapter
from nexus.adapters.social.meta_publisher import MetaSocialAdapter
from nexus.adapters.social.x_publisher import XSocialAdapter

__all__ = [
    "SocialPlatformAdapter",
    "SocialPost",
    "PublishResult",
    "SocialPublishError",
    "DiscordSocialAdapter",
    "XSocialAdapter",
    "LinkedInSocialAdapter",
    "MetaSocialAdapter",
]
