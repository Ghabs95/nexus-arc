"""Base interface and shared models for social platform publishing adapters."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class SocialPlatform(str, Enum):
    """Supported social publishing platforms."""

    DISCORD = "discord"
    X = "x"
    LINKEDIN = "linkedin"
    META_FACEBOOK = "meta_facebook"
    META_INSTAGRAM = "meta_instagram"


@dataclass
class SocialPost:
    """Platform-agnostic content post ready for publishing.

    The ``metadata`` field carries platform-specific controls such as
    thread mode for X, hashtags for LinkedIn, or media type for Meta.
    Raw secrets must never appear in any field of this object.
    """

    platform: str
    content: str
    campaign_id: str
    media_urls: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    scheduled_time_utc: str | None = None

    def __post_init__(self) -> None:
        _guard_no_secrets(self.content)
        for v in self.metadata.values():
            if isinstance(v, str):
                _guard_no_secrets(v)


@dataclass
class PublishResult:
    """Result of a single platform publish (or dry-run validation)."""

    success: bool
    platform: str
    campaign_id: str
    idempotency_key: str
    post_id: str | None = None
    dry_run: bool = False
    error: str | None = None
    published_at: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def ok(
        cls,
        platform: str,
        campaign_id: str,
        idempotency_key: str,
        post_id: str,
        *,
        dry_run: bool = False,
    ) -> "PublishResult":
        return cls(
            success=True,
            platform=platform,
            campaign_id=campaign_id,
            idempotency_key=idempotency_key,
            post_id=post_id,
            dry_run=dry_run,
            published_at=datetime.now(UTC).isoformat(),
        )

    @classmethod
    def fail(
        cls,
        platform: str,
        campaign_id: str,
        idempotency_key: str,
        error: str,
        *,
        dry_run: bool = False,
    ) -> "PublishResult":
        return cls(
            success=False,
            platform=platform,
            campaign_id=campaign_id,
            idempotency_key=idempotency_key,
            error=error,
            dry_run=dry_run,
        )


# ---------------------------------------------------------------------------
# Secret guard
# ---------------------------------------------------------------------------

_SECRET_PREFIXES = ("Bearer ", "Token ", "sk-", "xoxb-", "xoxp-")
_SECRET_PATTERNS = ("access_token=", "client_secret=", "api_key=")


def _guard_no_secrets(value: str) -> None:
    """Raise if *value* appears to contain a raw secret token.

    This is a best-effort heuristic — it is not a substitute for proper
    credential management, but it catches accidental injection at runtime.
    """
    lower = value.lower()
    for prefix in _SECRET_PREFIXES:
        if prefix.lower() in lower:
            raise ValueError(
                f"SocialPost content appears to contain a raw secret token "
                f"(matched prefix {prefix!r}). "
                "Use Nexus credential management instead of injecting secrets."
            )
    for pattern in _SECRET_PATTERNS:
        if pattern in lower:
            raise ValueError(
                f"SocialPost content appears to contain a raw secret token "
                f"(matched pattern {pattern!r}). "
                "Use Nexus credential management instead of injecting secrets."
            )


# ---------------------------------------------------------------------------
# Adapter base class
# ---------------------------------------------------------------------------


class SocialPlatformAdapter(ABC):
    """Abstract base class for social platform publishing adapters.

    Concrete implementations must provide :meth:`publish` and
    :meth:`validate`.  The :meth:`dry_run` method calls :meth:`validate`
    and returns a :class:`PublishResult` without hitting the real API.

    Credentials are never stored as attributes — adapters retrieve them via
    :mod:`nexus.core.auth.credential_crypto` at call time.
    """

    @property
    @abstractmethod
    def platform(self) -> str:
        """Canonical platform identifier (matches :class:`SocialPlatform` value)."""

    @abstractmethod
    async def publish(self, post: SocialPost) -> PublishResult:
        """Publish *post* to the live platform API.

        Implementations must:
        - Derive and honour the idempotency key.
        - Never log raw credentials.
        - Raise :class:`SocialPublishError` on unrecoverable errors.
        """

    @abstractmethod
    def validate(self, post: SocialPost) -> list[str]:
        """Return a (possibly empty) list of validation error strings.

        Must not make any network calls.
        """

    async def dry_run(self, post: SocialPost) -> PublishResult:
        """Validate *post* and return a simulated :class:`PublishResult`.

        No network call is made.  Returns success when :meth:`validate`
        finds no errors, otherwise returns failure with the first error.
        """
        from nexus.adapters.social.base import derive_idempotency_key

        key = derive_idempotency_key(
            post.campaign_id, self.platform, post.scheduled_time_utc or ""
        )
        errors = self.validate(post)
        if errors:
            return PublishResult.fail(
                platform=self.platform,
                campaign_id=post.campaign_id,
                idempotency_key=key,
                error="; ".join(errors),
                dry_run=True,
            )
        return PublishResult.ok(
            platform=self.platform,
            campaign_id=post.campaign_id,
            idempotency_key=key,
            post_id="dry-run",
            dry_run=True,
        )


# ---------------------------------------------------------------------------
# Custom exception
# ---------------------------------------------------------------------------


class SocialPublishError(RuntimeError):
    """Raised by :class:`SocialPlatformAdapter` implementations on publish failures."""

    def __init__(self, platform: str, message: str, retryable: bool = True):
        super().__init__(f"[{platform}] {message}")
        self.platform = platform
        self.retryable = retryable


# ---------------------------------------------------------------------------
# Idempotency helper (lives here to avoid circular imports with social_publish)
# ---------------------------------------------------------------------------

import hashlib as _hashlib

_IDEMPOTENCY_HASH_LENGTH = 16


def derive_idempotency_key(campaign_id: str, platform: str, scheduled_time_utc: str) -> str:
    """Derive a stable idempotency key from (campaign_id, platform, scheduled_time_utc)."""
    raw = f"{campaign_id}:{platform}:{scheduled_time_utc}".encode("utf-8")
    return _hashlib.sha256(raw).hexdigest()[: _IDEMPOTENCY_HASH_LENGTH * 2]
