from __future__ import annotations

import io
import json
import time

from nexus.core.command_bridge.http import CommandBridgeConfig, create_command_bridge_app
from nexus.core.command_bridge.models import CommandResult, ReplyRequest


class _FakeRouter:
    def get_capabilities(self):
        return {
            "ok": True,
            "version": "v1",
            "route_enabled": True,
            "supported_commands": ["plan", "wfstate"],
            "long_running_commands": ["plan"],
        }

    async def execute(self, request):
        return CommandResult(
            status="accepted",
            message=f"executed {request.command}",
            workflow_id="demo-42-full",
            issue_number="42",
            project_key="demo",
        )

    async def route(self, request):
        return CommandResult(status="success", message=f"routed {request.raw_text}")

    async def get_workflow_status(self, workflow_id: str):
        if workflow_id == "demo-42-full":
            return {"ok": True, "workflow_id": workflow_id, "status": {"state": "running"}}
        return {"ok": False, "error": "missing"}

    async def receive_reply(self, reply: ReplyRequest):
        return CommandResult(
            status="success",
            message="Reply received",
            data={"correlation_id": reply.correlation_id, "received": True},
        )


def _call_app(
    app,
    *,
    method: str,
    path: str,
    payload: dict | None = None,
    auth: str | None = None,
    extra_headers: dict | None = None,
):
    body = json.dumps(payload or {}).encode("utf-8")
    status_holder: dict[str, object] = {}

    def _start_response(status, headers):
        status_holder["status"] = status
        status_holder["headers"] = headers

    environ = {
        "REQUEST_METHOD": method,
        "PATH_INFO": path,
        "CONTENT_LENGTH": str(len(body)),
        "wsgi.input": io.BytesIO(body),
    }
    if auth is not None:
        environ["HTTP_AUTHORIZATION"] = auth
    for header_name, header_value in (extra_headers or {}).items():
        environ[header_name] = header_value
    response = b"".join(app(environ, _start_response))
    return status_holder["status"], json.loads(response.decode("utf-8"))


def test_healthz_is_public():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(app, method="GET", path="/healthz")

    assert status.startswith("200")
    assert payload == {"ok": True}


def test_execute_requires_bearer_auth():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/commands/execute",
        payload={"command": "plan"},
    )

    assert status.startswith("401")
    assert "bearer token" in payload["error"].lower()
    assert payload["error_code"] == "missing_bearer_token"


def test_capabilities_endpoint_requires_auth_and_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    unauthorized_status, unauthorized_payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
    )
    authorized_status, authorized_payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
    )

    assert unauthorized_status.startswith("401")
    assert unauthorized_payload["error_code"] == "missing_bearer_token"
    assert authorized_status.startswith("200")
    assert authorized_payload["supported_commands"] == ["plan", "wfstate"]


def test_execute_returns_accepted_response():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(
            auth_token="secret",
            allowed_sources=["openclaw"],
            allowed_sender_ids=["alice"],
        ),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/commands/execute",
        auth="Bearer secret",
        payload={
            "command": "plan",
            "args": ["demo", "42"],
            "requester": {"source_platform": "openclaw", "sender_id": "alice"},
        },
    )

    assert status.startswith("202")
    assert payload["workflow_id"] == "demo-42-full"
    assert payload["status"] == "accepted"


def test_execute_rejects_sender_allowlist_with_structured_error():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(
            auth_token="secret",
            allowed_sources=["openclaw"],
            allowed_sender_ids=["alice"],
        ),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/commands/execute",
        auth="Bearer secret",
        payload={
            "command": "plan",
            "args": ["demo", "42"],
            "requester": {"source_platform": "openclaw", "sender_id": "mallory"},
        },
    )

    assert status.startswith("403")
    assert payload["error_code"] == "sender_not_allowed"


def test_execute_requires_authenticated_requester_when_configured():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(
            auth_token="secret",
            allowed_sources=["openclaw"],
            require_authorized_sender=True,
        ),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/commands/execute",
        auth="Bearer secret",
        payload={
            "command": "plan",
            "args": ["demo", "42"],
            "requester": {"source_platform": "openclaw", "sender_id": "alice"},
        },
    )

    assert status.startswith("403")
    assert payload["error_code"] == "requester_not_authorized"


def test_workflow_status_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/workflows/demo-42-full",
        auth="Bearer secret",
    )

    assert status.startswith("200")
    assert payload["status"]["state"] == "running"


def test_500_does_not_leak_exception_details(monkeypatch):
    class _BrokenRouter:
        def get_capabilities(self):
            raise RuntimeError("secret internal path: /etc/nexus/tokens.json")

    app = create_command_bridge_app(
        _BrokenRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
    )

    assert status.startswith("500")
    assert payload["error"] == "Internal server error"
    assert payload["error_code"] == "internal_error"
    assert "secret" not in payload["error"]
    assert "/etc" not in payload["error"]


def test_tls_enforcement_rejects_non_https():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret", require_tls=True),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
    )

    assert status.startswith("403")
    assert payload["error_code"] == "tls_required"


def test_tls_enforcement_allows_https_via_forwarded_proto():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret", require_tls=True),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
        extra_headers={"HTTP_X_FORWARDED_PROTO": "https"},
    )

    assert status.startswith("200")
    assert payload["supported_commands"] == ["plan", "wfstate"]


def test_replay_protection_rejects_missing_timestamp():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret", replay_protection_enabled=True),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
    )

    assert status.startswith("401")
    assert payload["error_code"] == "missing_timestamp"


def test_replay_protection_rejects_stale_timestamp():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret", replay_protection_enabled=True),
    )

    stale_ts = str(time.time() - 400)
    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
        extra_headers={"HTTP_X_NEXUS_TIMESTAMP": stale_ts, "HTTP_X_NEXUS_NONCE": "abc123"},
    )

    assert status.startswith("401")
    assert payload["error_code"] == "timestamp_out_of_window"


def test_replay_protection_rejects_duplicate_nonce():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret", replay_protection_enabled=True),
    )

    import uuid
    fresh_ts = str(time.time())
    nonce = f"unique-nonce-{uuid.uuid4().hex}"

    first_status, _ = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
        extra_headers={"HTTP_X_NEXUS_TIMESTAMP": fresh_ts, "HTTP_X_NEXUS_NONCE": nonce},
    )
    assert first_status.startswith("200"), f"First request should succeed, got {first_status}"

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/capabilities",
        auth="Bearer secret",
        extra_headers={"HTTP_X_NEXUS_TIMESTAMP": fresh_ts, "HTTP_X_NEXUS_NONCE": nonce},
    )

    assert status.startswith("401")
    assert payload["error_code"] == "replay_detected"


def test_reply_endpoint_accepts_valid_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/bridge/openclaw/reply",
        auth="Bearer secret",
        payload={
            "correlation_id": "corr-001",
            "content": "Done!",
            "sender_id": "alice",
        },
    )

    assert status.startswith("200")
    assert payload["status"] == "success"
    assert payload["data"]["correlation_id"] == "corr-001"
    assert payload["data"]["received"] is True


def test_reply_endpoint_rejects_missing_correlation_id():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/bridge/openclaw/reply",
        auth="Bearer secret",
        payload={"content": "No correlation id here"},
    )

    assert status.startswith("400")
    assert payload["error_code"] == "invalid_request"


def test_reply_endpoint_requires_auth():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/bridge/openclaw/reply",
        payload={"correlation_id": "corr-001", "content": "hi"},
    )

    assert status.startswith("401")
