"""Utility to publish LinkedIn text posts resolving canonical nexus_id from chat senders.

This module exposes a small service function suitable for command-bridge or HTTP
invocation. It supports dry-run mode, idempotency, and records publish events.
"""
from __future__ import annotations

from datetime import datetime
import logging
from typing import Any

from nexus.connectors.linkedin import linkedin_connector_service, LinkedInConnectorError
from nexus.adapters.social.linkedin_publisher import LinkedInSocialAdapter
from nexus.core.social_publish import derive_idempotency_key

logger = logging.getLogger(__name__)


def _get_latest_auth_session_for_chat(chat_platform: str, chat_id: str):
    # Lazy import keeps unit tests lightweight when optional auth DB deps are missing.
    from nexus.core.auth.credential_store import get_latest_auth_session_for_chat

    return get_latest_auth_session_for_chat(chat_platform, chat_id)


def _record_social_publish_event(**kwargs):
    # Lazy import keeps unit tests lightweight when optional auth DB deps are missing.
    from nexus.core.auth.credential_store import record_social_publish_event

    return record_social_publish_event(**kwargs)


def publish_linkedin_text(
    *,
    content: str,
    campaign_id: str,
    nexus_id: str | None = None,
    chat_platform: str | None = None,
    chat_id: str | None = None,
    dry_run: bool = True,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Publish a text post to LinkedIn for the canonical nexus_id resolved.

    Args:
        content: Text content to publish.
        campaign_id: Logical campaign id for idempotency grouping.
        nexus_id: Optional canonical nexus_id. If omitted, will attempt to resolve
            from chat_platform + chat_id via nexus_auth_sessions.
        chat_platform: Chat platform name (e.g. "telegram") when resolving nexus_id.
        chat_id: Chat sender id string when resolving nexus_id.
        dry_run: If True, validate and return potential idempotency_key without calling LinkedIn.
        idempotency_key: Optional externally-provided idempotency key to enforce dedup.

    Returns:
        Dict with keys: nexus_id, platform, campaign_id, idempotency_key, dry_run, ok, error?
    """
    platform = "linkedin"
    resolved_nexus_id = None
    if nexus_id:
        resolved_nexus_id = str(nexus_id)
    else:
        if chat_platform and chat_id:
            session = _get_latest_auth_session_for_chat(chat_platform, chat_id)
            if session:
                resolved_nexus_id = str(session.nexus_id)
    if not resolved_nexus_id:
        return {"ok": False, "error": "unable to resolve nexus_id from inputs"}

    safe_campaign = str(campaign_id or "").strip()
    if not safe_campaign:
        return {"ok": False, "error": "campaign_id is required"}

    key = idempotency_key or derive_idempotency_key(safe_campaign, platform, "")

    if dry_run:
        # Dry run: validate basic constraints and return resolved nexus_id + key
        if not content or not str(content).strip():
            return {"ok": False, "error": "content must not be empty"}
        return {
            "ok": True,
            "dry_run": True,
            "nexus_id": resolved_nexus_id,
            "platform": platform,
            "campaign_id": safe_campaign,
            "idempotency_key": key,
        }

    # Live publish: resolve connection and post via adapter
    try:
        conn = linkedin_connector_service.get_connection(nexus_id=resolved_nexus_id)
    except LinkedInConnectorError as exc:
        return {"ok": False, "error": f"linkedin connection error: {exc}"}

    adapter = LinkedInSocialAdapter(conn.access_token, conn.author_urn)

    # Build minimal post object expected by adapter
    from nexus.adapters.social.base import SocialPost

    post = SocialPost(
        platform=platform,
        content=str(content),
        campaign_id=safe_campaign,
        media_urls=[],
        metadata={},
        scheduled_time_utc="",
    )

    # Use adapter.dry_run if available for validation, otherwise call publish
    try:
        result = adapter.dry_run(post)
        # adapter.dry_run returns PublishResult synchronously or may be coroutine
        if hasattr(result, "__await__"):
            import asyncio

            result = asyncio.get_event_loop().run_until_complete(result)
    except Exception as exc:
        return {"ok": False, "error": f"validation failed: {exc}"}

    if not getattr(result, "dry_run", False):
        # proceed to actual publish
        try:
            import asyncio

            result = asyncio.get_event_loop().run_until_complete(adapter.publish(post))
        except Exception as exc:
            return {"ok": False, "error": f"publish failed: {exc}"}

    # Persist audit/event
    try:
        published_at = getattr(result, "published_at", None) or datetime.now()
        _record_social_publish_event(
            platform=platform,
            campaign_id=safe_campaign,
            post_id=getattr(result, "post_id", None),
            idempotency_key=getattr(result, "idempotency_key", key),
            nexus_id=resolved_nexus_id,
            post_url=getattr(result, "post_url", None),
            published_at=published_at,
            metadata={"dry_run": result.dry_run},
        )
    except Exception:
        logger.exception("Failed to record social publish event")

    return {
        "ok": getattr(result, "success", False),
        "dry_run": getattr(result, "dry_run", False),
        "nexus_id": resolved_nexus_id,
        "platform": platform,
        "campaign_id": safe_campaign,
        "idempotency_key": getattr(result, "idempotency_key", key),
        "post_id": getattr(result, "post_id", None),
        "error": getattr(result, "error", None),
    }
