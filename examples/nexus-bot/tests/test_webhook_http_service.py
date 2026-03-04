from unittest.mock import MagicMock

from nexus.core.webhook.http_service import process_webhook_request


class _Policy:
    def __init__(self, route, event=None):
        self._route = route
        self._event = event or {}

    def dispatch_event(self, event_type, payload):
        return {"route": self._route, "event": self._event}


def _handlers():
    return {
        "handle_issue_opened": lambda payload, event: {"status": "issue"},
        "handle_issue_comment": lambda payload, event: {"status": "comment"},
        "handle_pull_request": lambda payload, event: {"status": "pr"},
        "handle_pull_request_review": lambda payload, event: {"status": "review"},
    }


def test_process_webhook_request_invalid_signature():
    body, status = process_webhook_request(
        payload_body=b"{}",
        headers={},
        payload_json={},
        logger=MagicMock(),
        verify_signature=lambda body, sig, token: False,
        get_webhook_policy=lambda: _Policy("ping"),
        emit_alert=lambda *args, **kwargs: True,
        **_handlers(),
    )
    assert status == 403
    assert body["error"] == "Invalid signature"


def test_process_webhook_request_missing_event_type():
    body, status = process_webhook_request(
        payload_body=b"{}",
        headers={"X-Hub-Signature-256": "x"},
        payload_json={},
        logger=MagicMock(),
        verify_signature=lambda body, sig, token: True,
        get_webhook_policy=lambda: _Policy("ping"),
        emit_alert=lambda *args, **kwargs: True,
        **_handlers(),
    )
    assert status == 400
    assert body["error"] == "No event type"


def test_process_webhook_request_ping_route():
    body, status = process_webhook_request(
        payload_body=b"{}",
        headers={"X-Hub-Signature-256": "x", "X-GitHub-Event": "ping"},
        payload_json={},
        logger=MagicMock(),
        verify_signature=lambda body, sig, token: True,
        get_webhook_policy=lambda: _Policy("ping"),
        emit_alert=lambda *args, **kwargs: True,
        **_handlers(),
    )
    assert status == 200
    assert body == {"status": "pong"}


def test_process_webhook_request_reports_handler_exception():
    alerts = []
    handlers = _handlers()
    handlers["handle_pull_request"] = lambda payload, event: (_ for _ in ()).throw(
        RuntimeError("boom")
    )
    body, status = process_webhook_request(
        payload_body=b"{}",
        headers={"X-Hub-Signature-256": "x", "X-GitHub-Event": "pull_request"},
        payload_json={},
        logger=MagicMock(),
        verify_signature=lambda body, sig, token: True,
        get_webhook_policy=lambda: _Policy("pull_request"),
        emit_alert=lambda *args, **kwargs: alerts.append((args, kwargs)) or True,
        **handlers,
    )
    assert status == 500
    assert "boom" in body["error"]
    assert alerts
