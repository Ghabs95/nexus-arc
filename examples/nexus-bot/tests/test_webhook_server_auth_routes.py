"""Auth guard tests for webhook server web routes."""

from __future__ import annotations

from types import SimpleNamespace


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
    assert b"Nexus Workflow Visualizer" in second.data


def test_visualizer_trailing_slash_is_served(monkeypatch):
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
    response = client.get("/visualizer/?session=sess-123")

    assert response.status_code == 302
    assert response.headers["Location"].endswith("/visualizer")


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
    assert b"Nexus Workflow Visualizer" in visualizer_response.data


def test_provider_connect_start_route_returns_service_payload(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_start_provider_account_login",
        lambda session_id, provider: {
            "started": True,
            "session_id": session_id,
            "provider": provider,
            "state": "starting",
        },
    )

    client = webhook_server.app.test_client()
    response = client.post(
        "/auth/provider-connect/start",
        json={"session_id": "sess-1", "provider": "codex"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["started"] is True
    assert payload["provider"] == "codex"


def test_provider_connect_status_route_returns_service_payload(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_provider_account_login_status",
        lambda session_id, provider: {
            "exists": True,
            "session_id": session_id,
            "provider": provider,
            "state": "pending",
            "verify_url": "https://auth.openai.com/device",
            "user_code": "ABCD-EFGH",
        },
    )

    client = webhook_server.app.test_client()
    response = client.get("/auth/provider-connect/status?session_id=sess-2&provider=codex")

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["state"] == "pending"
    assert payload["user_code"] == "ABCD-EFGH"


def test_provider_connect_pending_route_renders_waiting_page(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)

    client = webhook_server.app.test_client()
    response = client.get("/auth/provider-connect/pending?session_id=sess-9&provider=gemini")

    assert response.status_code == 200
    body = response.get_data(as_text=True)
    assert "Gemini Account Login" in body
    assert "provider_pending_status" in body
    assert "/auth/provider-connect/status?provider=" in body
    assert "window.location.replace(verifyUrl)" in body


def test_provider_connect_relay_callback_route_returns_service_payload(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_relay_provider_account_login_callback",
        lambda session_id, provider, callback_url: {
            "relayed": True,
            "session_id": session_id,
            "provider": provider,
            "state": "pending",
            "message": f"Relayed {callback_url}",
        },
    )

    client = webhook_server.app.test_client()
    response = client.post(
        "/auth/provider-connect/relay-callback",
        json={
            "session_id": "sess-3",
            "provider": "gemini",
            "callback_url": "http://localhost:41725/oauth2callback?code=abc",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["relayed"] is True
    assert payload["provider"] == "gemini"


def test_provider_connect_submit_code_route_returns_service_payload(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_submit_provider_account_login_code",
        lambda session_id, provider, authorization_code: {
            "submitted": True,
            "session_id": session_id,
            "provider": provider,
            "state": "pending",
            "message": f"submitted:{authorization_code}",
        },
    )

    client = webhook_server.app.test_client()
    response = client.post(
        "/auth/provider-connect/submit-code",
        json={
            "session_id": "sess-4",
            "provider": "gemini",
            "authorization_code": "4/0AX4XfWg-example",
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["status"] == "ok"
    assert payload["submitted"] is True
    assert payload["provider"] == "gemini"


def test_provider_connect_setup_form_renders_clickable_link_and_popup_flow():
    import webhook_server

    rendered = webhook_server._render_ai_key_form(
        session_id="lsr_session-123",
        show_api_key_inputs=True,
        copilot_checked=True,
        show_copilot_option=True,
        copilot_token_set=False,
        codex_key_set=False,
        gemini_key_set=False,
        claude_key_set=False,
        codex_account_enabled=False,
        gemini_account_enabled=False,
        claude_account_enabled=False,
        copilot_account_enabled=False,
        existing_keys_note="note",
    )

    assert "setProviderConnectStatusWithLink" in rendered
    assert "Open verification link" in rendered
    assert "primeProviderPopup(provider, sessionId);" in rendered
    assert 'if (providerKey === "codex" || providerKey === "gemini" || providerKey === "claude")' in rendered
    assert 'window.open(pendingUrl, "_blank")' in rendered
    assert 'if (provider === "gemini") {' in rendered
    assert "setGeminiProviderSections({ showCode: false, showCallback: false });" in rendered
    assert "/auth/provider-connect/pending?provider=" in rendered
    assert "Connect Codex Account" in rendered
    assert "Connect Gemini Account" in rendered
    assert "Connect Claude Account" in rendered
    assert "Submit Gemini Callback URL" in rendered
    assert "Submit Gemini Authorization Code" in rendered
    assert 'id="gemini_code_section" style="display:none;' in rendered
    assert 'id="gemini_callback_section" style="display:none;' in rendered
    assert "margin-top:0.45rem; margin-bottom:0.25rem;" in rendered
    assert "relayProviderCallback('gemini')" in rendered
    assert "submitProviderAuthCode('gemini')" in rendered
    assert "setGeminiProviderSections({" in rendered
    assert "payload.callback_url_hint" in rendered
    assert "initialPollDelay" in rendered
    assert "/auth/provider-connect/relay-callback" in rendered
    assert "/auth/provider-connect/submit-code" in rendered
    assert 'state === "rate_limited"' in rendered
    assert 'state === "interactive_required"' in rendered


def test_provider_connect_setup_form_hides_api_keys_in_account_mode():
    import webhook_server

    rendered = webhook_server._render_ai_key_form(
        session_id="lsr_session-123",
        show_api_key_inputs=False,
        copilot_checked=True,
        show_copilot_option=True,
        copilot_token_set=False,
        codex_key_set=False,
        gemini_key_set=False,
        claude_key_set=False,
        codex_account_enabled=False,
        gemini_account_enabled=False,
        claude_account_enabled=False,
        copilot_account_enabled=False,
        existing_keys_note="note",
    )

    assert "Codex/OpenAI API Key (optional)" not in rendered
    assert "Gemini API Key (optional)" not in rendered
    assert "Claude API Key (optional)" not in rendered
    assert "Copilot Token (optional)" not in rendered
    assert "NEXUS_CLI_AUTH_MODE=account" in rendered


def test_auth_start_rejects_unconfigured_provider(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_svc_resolve_login_session_id", lambda _value: "sess-1")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_ID", "")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_SECRET", "")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_ID", "")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_SECRET", "")

    client = webhook_server.app.test_client()
    response = client.get("/auth/start?session=sess-1&provider=github")

    assert response.status_code == 400
    assert b"OAuth is not configured" in response.data


def test_auth_start_redirects_when_provider_configured(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(webhook_server, "_svc_resolve_login_session_id", lambda _value: "sess-1")
    monkeypatch.setattr(
        webhook_server,
        "_svc_start_oauth_flow",
        lambda _session_id, provider="github": (f"https://oauth.test/{provider}", "state-1"),
    )
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_ID", "gh-id")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_SECRET", "gh-secret")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_ID", "")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_SECRET", "")

    client = webhook_server.app.test_client()
    response = client.get("/auth/start?session=sess-1&provider=github")

    assert response.status_code == 302
    assert response.headers["Location"] == "https://oauth.test/github"


def test_github_callback_handles_provider_error_query(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_auth_session_by_state",
        lambda _state: SimpleNamespace(session_id="sess-1"),
    )
    notified: list[str] = []
    monkeypatch.setattr(
        webhook_server,
        "_notify_onboarding_message",
        lambda session_id, text: notified.append(f"{session_id}:{text}"),
    )

    client = webhook_server.app.test_client()
    response = client.get(
        "/auth/github/callback?error=access_denied&error_description=User%20cancelled&state=state-1"
    )

    assert response.status_code == 400
    assert b"GitHub returned" in response.data
    assert notified and "GitHub OAuth failed" in notified[0]


def test_gitlab_callback_handles_provider_error_query(monkeypatch):
    import webhook_server

    monkeypatch.setattr(webhook_server, "NEXUS_AUTH_ENABLED", True)
    monkeypatch.setattr(
        webhook_server,
        "_svc_get_auth_session_by_state",
        lambda _state: SimpleNamespace(session_id="sess-2"),
    )
    notified: list[str] = []
    monkeypatch.setattr(
        webhook_server,
        "_notify_onboarding_message",
        lambda session_id, text: notified.append(f"{session_id}:{text}"),
    )

    client = webhook_server.app.test_client()
    response = client.get(
        "/auth/gitlab/callback?error=access_denied&error_description=User%20cancelled&state=state-2"
    )

    assert response.status_code == 400
    assert b"GitLab returned" in response.data
    assert notified and "GitLab OAuth failed" in notified[0]


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
