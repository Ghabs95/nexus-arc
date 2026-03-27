from __future__ import annotations

import io
import json
import time

from nexus.core.command_bridge.http import CommandBridgeConfig, create_command_bridge_app
from nexus.core.command_bridge.models import CommandResult, ReplyRequest
from nexus.core.command_bridge.reply_security import issue_reply_token, validate_reply_token


class _FakeOperatorService:
    async def workflow_status(self, *, workflow_id=None, issue_number=None):
        return {
            "ok": True,
            "workflow": {
                "workflow_id": workflow_id or "demo-42-full",
                "issue_number": issue_number or "42",
                "project_key": "demo",
                "state": "running",
            },
        }


class _FakeRouter:
    def __init__(self):
        self.operator_service = _FakeOperatorService()

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

    async def get_runtime_health(self):
        return {"ok": True, "runtime_mode": "openclaw"}

    async def get_active_workflows(self, *, limit: int = 20):
        return {"ok": True, "count": 1, "items": [{"workflow_id": "demo-42-full"}], "limit": limit}

    async def get_recent_failures(self, *, limit: int = 20):
        return {"ok": True, "count": 1, "items": [{"workflow_id": "demo-99-full"}], "limit": limit}

    async def get_recent_incidents(self, *, limit: int = 20):
        return {"ok": True, "count": 1, "items": [{"workflow_id": "demo-88-full", "severity": "high"}], "limit": limit}

    async def get_git_identity_status(self):
        return {"ok": True, "github": {"installed": True}}

    async def get_workflow_summary(self, *, workflow_id=None, issue_number=None):
        return {"ok": True, "summary": "demo summary", "workflow_id": workflow_id, "issue_number": issue_number}

    async def get_workflow_timeline(self, *, workflow_id=None, issue_number=None):
        return {"ok": True, "workflow_id": workflow_id, "issue_number": issue_number, "timeline": [{"step_num": 1, "name": "triage"}]}

    async def get_workflow_diagnosis(self, *, workflow_id=None, issue_number=None):
        return {"ok": True, "diagnosis": "agent_running", "likely_cause": "demo cause", "workflow_id": workflow_id, "issue_number": issue_number}

    async def get_workflow_authorship_audit(self, *, workflow_id=None, issue_number=None):
        return {"ok": True, "workflow_id": workflow_id, "issue_number": issue_number, "authorship": {"classification": "human_requested"}}

    async def get_workflow_blockers(self, *, workflow_id=None, issue_number=None):
        return {"ok": True, "workflow_id": workflow_id, "issue_number": issue_number, "blocking": True, "blockers": [{"type": "approval_required"}]}

    async def get_workflow_logs_context(self, *, workflow_id=None, issue_number=None):
        return {"ok": True, "workflow_id": workflow_id, "issue_number": issue_number, "log_context": [{"file": "demo.log", "lines": ["hello"]}]}

    async def explain_routing(self, **kwargs):
        return {"ok": True, **kwargs}

    async def validate_routing(self, **kwargs):
        return {"ok": True, "validation": {"recommended_repo": "Ghabs95/nexus-os"}, **kwargs}

    async def continue_workflow(self, **kwargs):
        return {"ok": True, "action": "continue", **kwargs}

    async def retry_workflow_step(self, **kwargs):
        return {"ok": True, "action": "retry-step", **kwargs}

    async def cancel_workflow(self, **kwargs):
        return {"ok": True, "action": "cancel", **kwargs}

    async def refresh_workflow_state(self, **kwargs):
        return {"ok": True, "action": "refresh-state", **kwargs}

    async def receive_reply(self, reply: ReplyRequest):
        claims = validate_reply_token(
            reply.reply_token,
            secret="secret",
            correlation_id=reply.correlation_id,
            workflow_id=str(reply.workflow_id or "").strip(),
            session_id=str(reply.session_id or "").strip(),
            sender_id=str(reply.sender_id or "").strip(),
            action=str(reply.action or "").strip(),
        )
        action_map = {
            "show_status": "status",
            "show_logs": "logs",
            "continue": "continue",
            "cancel": "cancel",
            "refresh_state": "refresh_state",
            "": "ack",
        }
        action_name = action_map.get(str(reply.action or "").strip(), str(reply.action or "").strip())
        return CommandResult(
            status="success",
            message="Reply received",
            data={
                "correlation_id": reply.correlation_id,
                "received": True,
                "reply_action": action_name,
                "reply_context": {"workflow_id": claims.workflow_id},
            },
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
    reply_token, _claims = issue_reply_token(
        secret="secret",
        correlation_id="corr-001",
        workflow_id="demo-42-full",
        sender_id="alice",
        allowed_actions=["show_status"],
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
            "workflow_id": "demo-42-full",
            "action": "show_status",
            "reply_token": reply_token,
        },
    )

    assert status.startswith("200")
    assert payload["status"] == "success"
    assert payload["data"]["correlation_id"] == "corr-001"
    assert payload["data"]["received"] is True
    assert payload["data"]["reply_action"] == "status"


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
        payload={"content": "No correlation id here", "reply_token": "dummy"},
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
        payload={"correlation_id": "corr-001", "content": "hi", "reply_token": "dummy"},
    )

    assert status.startswith("401")


def test_reply_endpoint_rejects_replayed_reply_token():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )
    reply_token, _claims = issue_reply_token(
        secret="secret",
        correlation_id="corr-002",
        workflow_id="demo-42-full",
        sender_id="alice",
        allowed_actions=["show_status"],
    )
    payload = {
        "correlation_id": "corr-002",
        "content": "status?",
        "sender_id": "alice",
        "workflow_id": "demo-42-full",
        "action": "show_status",
        "reply_token": reply_token,
    }

    first_status, first_payload = _call_app(
        app,
        method="POST",
        path="/api/v1/bridge/openclaw/reply",
        auth="Bearer secret",
        payload=payload,
    )
    assert first_status.startswith("200")
    assert first_payload["status"] == "success"

    second_status, second_payload = _call_app(
        app,
        method="POST",
        path="/api/v1/bridge/openclaw/reply",
        auth="Bearer secret",
        payload=payload,
    )

    assert second_status.startswith("410")
    assert second_payload["error_code"] == "reply_replay_detected"


def test_reply_endpoint_rejects_correlation_mismatch():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )
    reply_token, _claims = issue_reply_token(
        secret="secret",
        correlation_id="corr-003",
        workflow_id="demo-42-full",
        sender_id="alice",
        allowed_actions=["continue"],
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/bridge/openclaw/reply",
        auth="Bearer secret",
        payload={
            "correlation_id": "corr-wrong",
            "sender_id": "alice",
            "workflow_id": "demo-42-full",
            "action": "continue",
            "reply_token": reply_token,
        },
    )

    assert status.startswith("409")
    assert payload["error_code"] == "reply_correlation_mismatch"


def test_operator_runtime_health_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/runtime-health",
        auth="Bearer secret",
    )

    assert status.startswith("200")
    assert payload["runtime_mode"] == "openclaw"


def test_operator_active_workflows_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/active",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "limit=5"},
    )

    assert status.startswith("200")
    assert payload["count"] == 1


def test_operator_continue_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/operator/workflows/continue",
        auth="Bearer secret",
        payload={"issue_number": "42", "target_agent": "developer"},
    )

    assert status.startswith("200")
    assert payload["action"] == "continue"
    assert payload["issue_number"] == "42"


def test_operator_routing_explain_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/routing/explain",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "project_key=nexus&task_type=feature"},
    )

    assert status.startswith("200")
    assert payload["project_key"] == "nexus"


def test_operator_workflow_summary_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/summary",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "workflow_id=demo-42-full"},
    )

    assert status.startswith("200")
    assert payload["summary"] == "demo summary"


def test_operator_workflow_timeline_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/timeline",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "workflow_id=demo-42-full"},
    )

    assert status.startswith("200")
    assert payload["timeline"][0]["name"] == "triage"


def test_operator_workflow_why_stuck_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/why-stuck",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "workflow_id=demo-42-full"},
    )

    assert status.startswith("200")
    assert payload["diagnosis"] == "agent_running"


def test_operator_recent_incidents_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/recent-incidents",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "limit=3"},
    )

    assert status.startswith("200")
    assert payload["items"][0]["severity"] == "high"


def test_operator_workflow_logs_context_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/logs-context",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "workflow_id=demo-42-full"},
    )

    assert status.startswith("200")
    assert payload["log_context"][0]["file"] == "demo.log"


def test_operator_workflow_authorship_audit_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/authorship-audit",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "workflow_id=demo-42-full"},
    )

    assert status.startswith("200")
    assert payload["authorship"]["classification"] == "human_requested"


def test_operator_workflow_blockers_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/blockers",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "workflow_id=demo-42-full"},
    )

    assert status.startswith("200")
    assert payload["blocking"] is True
    assert payload["blockers"][0]["type"] == "approval_required"


def test_operator_routing_validate_endpoint_returns_payload():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/routing/validate",
        auth="Bearer secret",
        extra_headers={"QUERY_STRING": "project_key=nexus&task_type=operator"},
    )

    assert status.startswith("200")
    assert payload["validation"]["recommended_repo"] == "Ghabs95/nexus-os"


def test_operator_workflow_status_returns_400_when_no_ref():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/status",
        auth="Bearer secret",
    )

    assert status.startswith("400")
    assert payload["ok"] is False
    assert payload["error"] == "Missing query parameter: provide 'workflow_id' or 'issue_number'."


def test_operator_workflow_summary_returns_400_when_no_ref():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/summary",
        auth="Bearer secret",
    )

    assert status.startswith("400")
    assert payload["ok"] is False
    assert payload["error"] == "Missing query parameter: provide 'workflow_id' or 'issue_number'."


def test_operator_workflow_why_stuck_returns_400_when_no_ref():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="GET",
        path="/api/v1/operator/workflows/why-stuck",
        auth="Bearer secret",
    )

    assert status.startswith("400")
    assert payload["ok"] is False
    assert payload["error"] == "Missing query parameter: provide 'workflow_id' or 'issue_number'."


def test_operator_retry_step_returns_400_when_target_agent_missing():
    app = create_command_bridge_app(
        _FakeRouter(),
        config=CommandBridgeConfig(auth_token="secret"),
    )

    status, payload = _call_app(
        app,
        method="POST",
        path="/api/v1/operator/workflows/retry-step",
        auth="Bearer secret",
        payload={"workflow_id": "demo-42-full"},
    )

    assert status.startswith("400")
    assert payload["error"] == "target_agent is required for retry-step requests"
