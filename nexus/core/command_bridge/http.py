"""Minimal HTTP bridge for commanding Nexus ARC from OpenClaw."""

from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Any
from wsgiref.simple_server import make_server

from nexus.core.command_bridge.models import CommandRequest, CommandResult, ReplyRequest
from nexus.core.command_bridge.router import CommandRouter

_logger = logging.getLogger(__name__)

# How far in the past or future a request timestamp may be (seconds).
_DEFAULT_REPLAY_WINDOW = 300


@dataclass
class CommandBridgeConfig:
    host: str = "127.0.0.1"
    port: int = 8091
    auth_token: str = ""
    allowed_sources: list[str] | None = None
    allowed_sender_ids: list[str] | None = None
    require_tls: bool = False
    replay_protection_enabled: bool = False
    replay_window_seconds: int = _DEFAULT_REPLAY_WINDOW


class _NonceCache:
    """Thread-safe in-memory nonce store for replay protection."""

    _CLEANUP_THRESHOLD = 500

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._seen: dict[str, float] = {}  # nonce -> expiry unix timestamp

    def check_and_add(self, nonce: str, expiry: float) -> bool:
        """Return True if *nonce* is fresh (not seen before), False on replay."""
        now = time.time()
        with self._lock:
            if len(self._seen) >= self._CLEANUP_THRESHOLD:
                expired = [k for k, v in self._seen.items() if v < now]
                for k in expired:
                    del self._seen[k]
            if nonce in self._seen:
                return False
            self._seen[nonce] = expiry
            return True


_NONCE_CACHE = _NonceCache()


def create_command_bridge_app(
    router: CommandRouter,
    *,
    config: CommandBridgeConfig,
):
    """Create a WSGI app for the Nexus command bridge."""

    def _app(environ, start_response):
        try:
            method = str(environ.get("REQUEST_METHOD", "GET") or "GET").upper()
            path = str(environ.get("PATH_INFO", "/") or "/")
            if path == "/healthz":
                return _json_response(start_response, 200, {"ok": True})

            if path.startswith("/api/v1/"):
                tls_error = _check_tls(environ, config=config)
                if tls_error is not None:
                    return _json_response(
                        start_response,
                        tls_error[0],
                        {"error": tls_error[1], "error_code": tls_error[2]},
                    )
                auth_error = _authorize_request(environ, config=config)
                if auth_error is not None:
                    return _json_response(
                        start_response,
                        auth_error[0],
                        {"error": auth_error[1], "error_code": auth_error[2]},
                    )

            if method == "GET" and path == "/api/v1/capabilities":
                payload = router.get_capabilities()
                return _json_response(start_response, 200, payload)

            if method == "POST" and path == "/api/v1/commands/execute":
                payload = _load_json_body(environ)
                request = CommandRequest.from_dict(payload)
                allow_error = _validate_requester(request, config=config)
                if allow_error is not None:
                    return _json_response(
                        start_response,
                        403,
                        {"error": allow_error[0], "error_code": allow_error[1]},
                    )
                result = asyncio.run(router.execute(request))
                return _command_result_response(start_response, result)

            if method == "POST" and path == "/api/v1/commands/route":
                payload = _load_json_body(environ)
                request = CommandRequest.from_dict(payload)
                allow_error = _validate_requester(request, config=config)
                if allow_error is not None:
                    return _json_response(
                        start_response,
                        403,
                        {"error": allow_error[0], "error_code": allow_error[1]},
                    )
                result = asyncio.run(router.route(request))
                return _command_result_response(start_response, result)

            if method == "GET" and path.startswith("/api/v1/workflows/"):
                workflow_id = path.rsplit("/", 1)[-1]
                payload = asyncio.run(router.get_workflow_status(workflow_id))
                status_code = 200 if payload.get("ok") else 404
                return _json_response(start_response, status_code, payload)

            if method == "POST" and path == "/api/v1/bridge/openclaw/reply":
                payload = _load_json_body(environ)
                _validate_reply_payload(payload)
                reply = ReplyRequest.from_dict(payload)
                result = asyncio.run(router.receive_reply(reply))
                return _command_result_response(start_response, result)

            return _json_response(start_response, 404, {"error": "Not found"})
        except ValueError as exc:
            return _json_response(
                start_response, 400, {"error": str(exc), "error_code": "invalid_request"}
            )
        except Exception:
            _logger.exception("Unexpected error in command bridge request handler")
            return _json_response(
                start_response,
                500,
                {"error": "Internal server error", "error_code": "internal_error"},
            )

    return _app


def run_command_bridge_server(
    router: CommandRouter,
    *,
    config: CommandBridgeConfig,
) -> None:
    """Run the command bridge using the stdlib WSGI server."""

    app = create_command_bridge_app(router, config=config)
    if not config.require_tls and config.host not in ("127.0.0.1", "::1", "localhost"):
        _logger.warning(
            "Nexus command bridge is listening on %s without TLS enforcement. "
            "Set require_tls=True and place a TLS-terminating proxy in front of the bridge.",
            config.host,
        )
    with make_server(config.host, int(config.port), app) as server:
        _logger.info("Nexus command bridge listening on http://%s:%s", config.host, config.port)
        server.serve_forever()


def _check_tls(
    environ: dict[str, Any], *, config: CommandBridgeConfig
) -> tuple[int, str, str] | None:
    if not config.require_tls:
        return None
    proto = str(environ.get("HTTP_X_FORWARDED_PROTO") or "").strip().lower()
    if proto != "https":
        return (
            403,
            "TLS is required: ensure a TLS-terminating proxy sets X-Forwarded-Proto: https",
            "tls_required",
        )
    return None


def _authorize_request(
    environ: dict[str, Any], *, config: CommandBridgeConfig
) -> tuple[int, str, str] | None:
    expected = str(config.auth_token or "").strip()
    if not expected:
        return 503, "Command bridge auth token is not configured", "auth_token_not_configured"
    header = str(environ.get("HTTP_AUTHORIZATION", "") or "").strip()
    if not header.startswith("Bearer "):
        return 401, "Missing bearer token", "missing_bearer_token"
    token = header.partition("Bearer ")[2].strip()
    if token != expected:
        return 401, "Invalid bearer token", "invalid_bearer_token"
    if config.replay_protection_enabled:
        return _check_replay_protection(environ, config=config)
    return None


def _check_replay_protection(
    environ: dict[str, Any], *, config: CommandBridgeConfig
) -> tuple[int, str, str] | None:
    window = int(config.replay_window_seconds or _DEFAULT_REPLAY_WINDOW)
    raw_ts = str(environ.get("HTTP_X_NEXUS_TIMESTAMP") or "").strip()
    if not raw_ts:
        return 401, "Missing X-Nexus-Timestamp header", "missing_timestamp"
    try:
        req_ts = float(raw_ts)
    except ValueError:
        return 401, "Invalid X-Nexus-Timestamp value", "invalid_timestamp"
    skew = abs(time.time() - req_ts)
    if skew > window:
        return 401, "Request timestamp is outside the acceptable window", "timestamp_out_of_window"
    nonce = str(environ.get("HTTP_X_NEXUS_NONCE") or "").strip()
    if not nonce:
        return 401, "Missing X-Nexus-Nonce header", "missing_nonce"
    # Nonces are kept until req_ts + window so that every replay within the
    # acceptance window is caught regardless of when it arrives.
    expiry = req_ts + window
    if not _NONCE_CACHE.check_and_add(nonce, expiry):
        return 401, "Request nonce has already been used (replay detected)", "replay_detected"
    return None


def _validate_requester(
    request: CommandRequest, *, config: CommandBridgeConfig
) -> tuple[str, str] | None:
    requester = request.requester
    if config.require_authorized_sender and requester.is_authorized_sender is not True:
        return "Authenticated OpenClaw requester is required", "requester_not_authorized"
    allowed_sources = [str(item or "").strip().lower() for item in (config.allowed_sources or []) if str(item or "").strip()]
    if allowed_sources:
        source = str(requester.source_platform or "").strip().lower()
        if source not in allowed_sources:
            return f"Source '{requester.source_platform}' is not allowed", "source_not_allowed"
    allowed_sender_ids = [str(item or "").strip() for item in (config.allowed_sender_ids or []) if str(item or "").strip()]
    if allowed_sender_ids:
        sender_id = str(requester.sender_id or "").strip()
        if sender_id not in allowed_sender_ids:
            return f"Sender '{sender_id}' is not allowed", "sender_not_allowed"
    return None


def _validate_reply_payload(payload: dict[str, Any]) -> None:
    """Raise ValueError if the reply payload fails basic schema validation."""
    if not isinstance(payload, dict):
        raise ValueError("Reply payload must be a JSON object")
    correlation_id = str(payload.get("correlation_id") or "").strip()
    if not correlation_id:
        raise ValueError("Reply payload must include a non-empty 'correlation_id'")
    content = payload.get("content")
    if content is not None and not isinstance(content, str):
        raise ValueError("Reply payload 'content' must be a string")
    sender_id = payload.get("sender_id")
    if sender_id is not None and not isinstance(sender_id, str):
        raise ValueError("Reply payload 'sender_id' must be a string")


def _load_json_body(environ: dict[str, Any]) -> dict[str, Any]:
    length_header = str(environ.get("CONTENT_LENGTH", "") or "").strip()
    try:
        length = int(length_header) if length_header else 0
    except ValueError as exc:
        raise ValueError("Invalid Content-Length header") from exc
    body = environ["wsgi.input"].read(length) if length > 0 else b"{}"
    if not body:
        return {}
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError("Invalid JSON body") from exc
    if not isinstance(payload, dict):
        raise ValueError("JSON body must be an object")
    return payload


def _command_result_response(start_response, result: CommandResult):
    status_code = 202 if result.status == "accepted" else 200
    return _json_response(start_response, status_code, result.to_dict())


def _json_response(start_response, status_code: int, payload: dict[str, Any]):
    body = json.dumps(payload, ensure_ascii=True).encode("utf-8")
    start_response(
        f"{status_code} {_status_text(status_code)}",
        [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ],
    )
    return [body]


def _status_text(status_code: int) -> str:
    mapping = {
        200: "OK",
        202: "Accepted",
        400: "Bad Request",
        401: "Unauthorized",
        403: "Forbidden",
        404: "Not Found",
        500: "Internal Server Error",
        503: "Service Unavailable",
    }
    return mapping.get(status_code, "OK")
