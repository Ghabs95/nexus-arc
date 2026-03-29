"""LinkedIn connector primitives built on stored Nexus credentials.

This module provides a small reusable client/service layer that can be used by
OAuth onboarding, social publishing, and future connector operations without
re-implementing token/header/profile logic in multiple places.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import requests

LINKEDIN_API_BASE = "https://api.linkedin.com/v2"
LINKEDIN_OPENID_USERINFO_URL = f"{LINKEDIN_API_BASE}/userinfo"
LINKEDIN_UGC_POSTS_URL = f"{LINKEDIN_API_BASE}/ugcPosts"


def _get_user_credentials(nexus_id: str):
    from nexus.core.auth.credential_store import get_user_credentials

    return get_user_credentials(str(nexus_id))


def _decrypt_secret(envelope: str) -> str:
    from nexus.core.auth.credential_crypto import decrypt_secret

    return decrypt_secret(envelope)


class LinkedInConnectorError(RuntimeError):
    """Raised when LinkedIn credential resolution or API calls fail."""


@dataclass(frozen=True)
class LinkedInConnection:
    """Resolved LinkedIn connection details for a Nexus user."""

    nexus_id: str
    access_token: str
    author_urn: str
    expires_at: datetime | None
    connected: bool

    @property
    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        expiry = self.expires_at
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return expiry <= datetime.now(tz=UTC)


@dataclass(frozen=True)
class LinkedInAuthStatus:
    nexus_id: str
    connected: bool
    has_access_token: bool
    has_author_urn: bool
    author_urn: str | None
    expires_at: datetime | None
    is_expired: bool


class LinkedInClient:
    """Thin LinkedIn API client for a single resolved credential set."""

    def __init__(self, access_token: str, *, author_urn: str | None = None):
        token = str(access_token or "").strip()
        if not token:
            raise ValueError("access_token is required")
        self._access_token = token
        self._author_urn = str(author_urn or "").strip() or None

    @property
    def author_urn(self) -> str | None:
        return self._author_urn

    def build_headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
            "X-Restli-Protocol-Version": "2.0.0",
        }

    def get_userinfo(self, *, timeout: int = 10) -> dict[str, Any]:
        response = requests.get(
            LINKEDIN_OPENID_USERINFO_URL,
            headers={"Authorization": f"Bearer {self._access_token}"},
            timeout=timeout,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise LinkedInConnectorError("LinkedIn userinfo response was not a JSON object.")
        return data

    def get_profile_me(self, *, timeout: int = 10) -> dict[str, Any]:
        profile = dict(self.get_userinfo(timeout=timeout))
        sub = str(profile.get("sub") or "").strip()
        if sub and not profile.get("author_urn"):
            profile["author_urn"] = f"urn:li:person:{sub}"
        elif self._author_urn and not profile.get("author_urn"):
            profile["author_urn"] = self._author_urn
        return profile


class LinkedInConnectorService:
    """Resolve stored LinkedIn credentials into auth status and profile access."""

    def get_auth_status(self, *, nexus_id: str) -> LinkedInAuthStatus:
        record = _get_user_credentials(str(nexus_id))
        author_urn = str(getattr(record, "linkedin_author_urn", "") or "").strip() or None
        token_enc = str(getattr(record, "linkedin_token_enc", "") or "").strip()
        expires_at = getattr(record, "linkedin_token_expires_at", None) if record else None
        has_token = bool(token_enc)
        has_author = bool(author_urn)
        connected = has_token and has_author
        connection = None
        if connected:
            connection = self.get_connection(nexus_id=nexus_id)
        return LinkedInAuthStatus(
            nexus_id=str(nexus_id),
            connected=connected,
            has_access_token=has_token,
            has_author_urn=has_author,
            author_urn=author_urn,
            expires_at=expires_at,
            is_expired=connection.is_expired if connection else False,
        )

    def get_connection(self, *, nexus_id: str) -> LinkedInConnection:
        record = _get_user_credentials(str(nexus_id))
        if not record:
            raise LinkedInConnectorError(f"No credential record found for nexus_id={nexus_id}.")

        token_enc = str(record.linkedin_token_enc or "").strip()
        author_urn = str(record.linkedin_author_urn or "").strip()
        if not token_enc:
            raise LinkedInConnectorError("LinkedIn access token is missing.")
        if not author_urn:
            raise LinkedInConnectorError("LinkedIn author URN is missing.")

        try:
            access_token = _decrypt_secret(token_enc)
        except Exception as exc:  # pragma: no cover - defensive error wrapper
            raise LinkedInConnectorError("Unable to decrypt stored LinkedIn credentials.") from exc

        return LinkedInConnection(
            nexus_id=str(nexus_id),
            access_token=access_token,
            author_urn=author_urn,
            expires_at=record.linkedin_token_expires_at,
            connected=True,
        )

    def get_client(self, *, nexus_id: str) -> LinkedInClient:
        connection = self.get_connection(nexus_id=nexus_id)
        return LinkedInClient(connection.access_token, author_urn=connection.author_urn)

    def get_profile_me(self, *, nexus_id: str, timeout: int = 10) -> dict[str, Any]:
        connection = self.get_connection(nexus_id=nexus_id)
        client = LinkedInClient(connection.access_token, author_urn=connection.author_urn)
        return client.get_profile_me(timeout=timeout)


linkedin_connector_service = LinkedInConnectorService()

__all__ = [
    "LINKEDIN_API_BASE",
    "LINKEDIN_OPENID_USERINFO_URL",
    "LINKEDIN_UGC_POSTS_URL",
    "LinkedInAuthStatus",
    "LinkedInClient",
    "LinkedInConnection",
    "LinkedInConnectorError",
    "LinkedInConnectorService",
    "linkedin_connector_service",
]
