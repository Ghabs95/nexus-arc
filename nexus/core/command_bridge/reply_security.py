"""Security helpers for Nexus ↔ OpenClaw reply/control loop tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

_DEFAULT_TTL_SECONDS = 15 * 60


class ReplyTokenError(ValueError):
    """Raised when a reply token is missing, invalid, stale, or replayed."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass
class ReplyTokenClaims:
    correlation_id: str
    workflow_id: str = ""
    session_id: str = ""
    session_key: str = ""
    sender_id: str = ""
    allowed_actions: list[str] = field(default_factory=list)
    issued_at: int = 0
    expires_at: int = 0
    nonce: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "correlation_id": self.correlation_id,
            "workflow_id": self.workflow_id,
            "session_id": self.session_id,
            "session_key": self.session_key,
            "sender_id": self.sender_id,
            "allowed_actions": list(self.allowed_actions or []),
            "iat": int(self.issued_at or 0),
            "exp": int(self.expires_at or 0),
            "nonce": self.nonce,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "ReplyTokenClaims":
        data = payload if isinstance(payload, dict) else {}
        allowed_actions = data.get("allowed_actions", [])
        return cls(
            correlation_id=str(data.get("correlation_id") or ""),
            workflow_id=str(data.get("workflow_id") or ""),
            session_id=str(data.get("session_id") or ""),
            session_key=str(data.get("session_key") or ""),
            sender_id=str(data.get("sender_id") or ""),
            allowed_actions=[str(item or "").strip() for item in allowed_actions if str(item or "").strip()]
            if isinstance(allowed_actions, list)
            else [],
            issued_at=int(data.get("iat") or 0),
            expires_at=int(data.get("exp") or 0),
            nonce=str(data.get("nonce") or ""),
        )


class _ReplyNonceCache:
    """In-process nonce store for reply-token replay detection.

    .. note::
        This cache is per-process. In multi-worker deployments (e.g. multiple WSGI
        workers or containers behind a load balancer), a token could be replayed
        against a different worker and still be accepted.  For full replay protection
        across workers, replace this with a shared store (Redis, memcached, etc.) and
        override ``_USED_REPLY_NONCES`` before starting the server.
    """
    _CLEANUP_THRESHOLD = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}

    def check_and_add(self, nonce: str, expiry: float) -> bool:
        now = time.time()
        with self._lock:
            if len(self._seen) >= self._CLEANUP_THRESHOLD:
                expired = [key for key, value in self._seen.items() if value < now]
                for key in expired:
                    del self._seen[key]
            if nonce in self._seen:
                return False
            self._seen[nonce] = expiry
            return True


_USED_REPLY_NONCES = _ReplyNonceCache()


def _b64url_encode(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padded = value + ("=" * ((4 - len(value) % 4) % 4))
    return base64.urlsafe_b64decode(padded.encode("ascii"))


def _json_dumps(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")


def _normalize_actions(actions: list[str] | None) -> list[str]:
    return [str(item or "").strip().lower() for item in (actions or []) if str(item or "").strip()]


def issue_reply_token(
    *,
    secret: str,
    correlation_id: str,
    workflow_id: str = "",
    session_id: str = "",
    session_key: str = "",
    sender_id: str = "",
    allowed_actions: list[str] | None = None,
    ttl_seconds: int = _DEFAULT_TTL_SECONDS,
    now: int | None = None,
) -> tuple[str, ReplyTokenClaims]:
    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        raise ReplyTokenError("Reply token secret is not configured", code="reply_secret_not_configured")
    normalized_correlation_id = str(correlation_id or "").strip()
    if not normalized_correlation_id:
        raise ReplyTokenError("Reply correlation id is required", code="missing_correlation_id")
    issued_at = int(now if now is not None else time.time())
    expires_at = issued_at + max(1, int(ttl_seconds or _DEFAULT_TTL_SECONDS))
    claims = ReplyTokenClaims(
        correlation_id=normalized_correlation_id,
        workflow_id=str(workflow_id or "").strip(),
        session_id=str(session_id or "").strip(),
        session_key=str(session_key or "").strip(),
        sender_id=str(sender_id or "").strip(),
        allowed_actions=_normalize_actions(allowed_actions),
        issued_at=issued_at,
        expires_at=expires_at,
        nonce=uuid.uuid4().hex,
    )
    payload_bytes = _json_dumps(claims.to_dict())
    body = _b64url_encode(payload_bytes)
    signature = hmac.new(normalized_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    return f"{body}.{_b64url_encode(signature)}", claims


def validate_reply_token(
    token: str,
    *,
    secret: str,
    correlation_id: str,
    workflow_id: str = "",
    session_id: str = "",
    sender_id: str = "",
    action: str = "",
    now: int | None = None,
) -> ReplyTokenClaims:
    normalized_secret = str(secret or "").strip()
    if not normalized_secret:
        raise ReplyTokenError("Reply token secret is not configured", code="reply_secret_not_configured")
    normalized_token = str(token or "").strip()
    if not normalized_token or "." not in normalized_token:
        raise ReplyTokenError("Reply token is missing or malformed", code="invalid_reply_token")
    body, signature = normalized_token.split(".", 1)
    expected = hmac.new(normalized_secret.encode("utf-8"), body.encode("ascii"), hashlib.sha256).digest()
    try:
        actual = _b64url_decode(signature)
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise ReplyTokenError("Reply token signature is malformed", code="invalid_reply_token") from exc
    if not hmac.compare_digest(expected, actual):
        raise ReplyTokenError("Reply token signature is invalid", code="invalid_reply_token")
    try:
        payload = json.loads(_b64url_decode(body).decode("utf-8"))
    except Exception as exc:  # pragma: no cover - defensive parse guard
        raise ReplyTokenError("Reply token payload is malformed", code="invalid_reply_token") from exc
    claims = ReplyTokenClaims.from_dict(payload)
    current_time = int(now if now is not None else time.time())
    if not claims.correlation_id:
        raise ReplyTokenError("Reply token is missing a correlation id", code="invalid_reply_token")
    if claims.correlation_id != str(correlation_id or "").strip():
        raise ReplyTokenError("Reply correlation does not match the issued token", code="reply_correlation_mismatch")
    if claims.expires_at and current_time > claims.expires_at:
        raise ReplyTokenError("Reply token expired; request a fresh workflow update before acting", code="reply_token_expired")
    normalized_workflow_id = str(workflow_id or "").strip()
    if normalized_workflow_id and claims.workflow_id and claims.workflow_id != normalized_workflow_id:
        raise ReplyTokenError("Reply workflow does not match the issued token", code="reply_workflow_mismatch")
    normalized_session_id = str(session_id or "").strip()
    if normalized_session_id and claims.session_id and claims.session_id != normalized_session_id:
        raise ReplyTokenError("Reply session does not match the issued token", code="reply_session_mismatch")
    normalized_sender_id = str(sender_id or "").strip()
    if normalized_sender_id and claims.sender_id and claims.sender_id != normalized_sender_id:
        raise ReplyTokenError("Reply sender does not match the issued token", code="reply_sender_mismatch")
    normalized_action = str(action or "").strip().lower()
    if normalized_action and claims.allowed_actions and normalized_action not in claims.allowed_actions:
        raise ReplyTokenError(
            f"Reply action '{normalized_action}' is not allowed for this token",
            code="reply_action_not_allowed",
        )
    nonce = str(claims.nonce or "").strip()
    if not nonce:
        raise ReplyTokenError("Reply token is missing a nonce", code="invalid_reply_token")
    if not _USED_REPLY_NONCES.check_and_add(nonce, float(claims.expires_at or (current_time + _DEFAULT_TTL_SECONDS))):
        raise ReplyTokenError("Reply token has already been used", code="reply_replay_detected")
    return claims
