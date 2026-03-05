"""Auth guard tests for webhook server web routes."""

from __future__ import annotations


def _ready_session_payload(session_id: str) -> dict:
    return {
        "exists": True,
        "session_id": session_id,
        "status": "completed",
        "expires_at": "2999-01-01T00:00:00+00:00",
        "setup": {"ready": True},
    }


def test_index_renders_dedicated_login_page_when_auth_enabled(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _session_id: {"exists": False},
    )

    client = webhook_server.app.test_client()
    response = client.get("/")

    assert response.status_code == 200
    assert b"Nexus Login" in response.data
    assert b"/auth/start" in response.data


def test_visualizer_redirects_to_login_when_auth_enabled_without_session(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _session_id: {"exists": False},
    )

    client = webhook_server.app.test_client()
    response = client.get("/visualizer")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/?next=/visualizer")


def test_visualizer_accepts_session_query_and_sets_cookie(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda session_id: _ready_session_payload(str(session_id)),
    )

    client = webhook_server.app.test_client()
    first = client.get("/visualizer?session=sess-123")
    second = client.get("/visualizer")

    assert first.status_code == 302
    assert first.headers["Location"].endswith("/visualizer")
    assert webhook_server._WEB_SESSION_COOKIE_NAME in (first.headers.get("Set-Cookie") or "")
    assert second.status_code == 200
    assert b"Nexus Live Workflow Visualizer" in second.data


def test_visualizer_snapshot_requires_auth_when_enabled(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda _session_id: {"exists": False},
    )

    client = webhook_server.app.test_client()
    response = client.get("/visualizer/snapshot")

    assert response.status_code == 401
    payload = response.get_json()
    assert payload["status"] == "unauthorized"


def test_visualizer_snapshot_returns_data_for_ready_session(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_session_and_setup_status",
        lambda session_id: _ready_session_payload(str(session_id)),
    )
    monkeypatch.setattr(
        webhook_server,
        "_collect_visualizer_snapshot",
        lambda: [{"issue": "99", "workflow_id": "wf-99", "status": {"state": "running"}}],
    )

    client = webhook_server.app.test_client()
    response = client.get("/visualizer/snapshot?session=sess-abc")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["count"] == 1
    assert payload["workflows"][0]["issue"] == "99"


def test_visualizer_requires_shared_token_when_auth_disabled(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "secret-token")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", False)

    client = webhook_server.app.test_client()
    visualizer_response = client.get("/visualizer")
    index_response = client.get("/")

    assert visualizer_response.status_code == 302
    assert visualizer_response.headers["Location"].endswith("/?next=/visualizer")
    assert index_response.status_code == 200
    assert b"Visualizer Access" in index_response.data
    assert b"/visualizer/access" in index_response.data


def test_visualizer_access_token_post_sets_cookie(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "secret-token")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", False)

    client = webhook_server.app.test_client()
    invalid = client.post("/visualizer/access", data={"token": "wrong", "next": "/visualizer"})
    assert invalid.status_code == 401

    valid = client.post("/visualizer/access", data={"token": "secret-token", "next": "/visualizer"})
    assert valid.status_code == 302
    assert valid.headers["Location"].endswith("/visualizer")
    assert webhook_server._VISUALIZER_SHARED_TOKEN_COOKIE_NAME in (valid.headers.get("Set-Cookie") or "")

    visualizer_response = client.get("/visualizer")
    assert visualizer_response.status_code == 200
    assert b"Nexus Live Workflow Visualizer" in visualizer_response.data


def test_visualizer_disabled_flag_returns_404(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "_VISUALIZER_ENABLED", False)
    monkeypatch.setattr(webhook_server, "_VISUALIZER_SHARED_TOKEN", "secret-token")
    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)

    client = webhook_server.app.test_client()
    root_response = client.get("/")
    visualizer_response = client.get("/visualizer")
    snapshot_response = client.get("/visualizer/snapshot")

    assert root_response.status_code == 404
    assert visualizer_response.status_code == 404
    assert snapshot_response.status_code == 404
