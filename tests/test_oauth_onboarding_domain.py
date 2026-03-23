import json

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace


def test_store_ai_provider_keys_validates_codex_with_cli_login(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-user-1",
    )

    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _session_id: session)
    monkeypatch.setattr(auth_mod, "get_user_credentials", lambda _nexus_id: None)
    monkeypatch.setattr(auth_mod, "_now_utc", lambda: datetime.now(tz=UTC))

    validations: dict[str, int] = {"cli": 0, "provider": 0}

    def _fake_cli_validation(api_key: str):
        assert api_key == "sk-test-codex-key-123456"
        validations["cli"] += 1
        return True, ""

    def _fake_provider_validation(api_key: str):
        assert api_key == "sk-test-codex-key-123456"
        validations["provider"] += 1
        return True, ""

    captured_upsert: dict[str, str] = {}

    monkeypatch.setattr(
        auth_mod,
        "_validate_codex_api_key_with_codex_cli_login",
        _fake_cli_validation,
    )
    monkeypatch.setattr(
        auth_mod,
        "_validate_codex_api_key_with_provider",
        _fake_provider_validation,
    )
    monkeypatch.setattr(auth_mod, "encrypt_secret", lambda value, key_version=1: f"enc::{value}")
    monkeypatch.setattr(
        auth_mod,
        "upsert_ai_provider_keys",
        lambda **kwargs: captured_upsert.update(kwargs),
    )
    monkeypatch.setattr(auth_mod, "update_auth_session", lambda **kwargs: None)
    monkeypatch.setattr(
        auth_mod,
        "get_setup_status",
        lambda _nexus_id: {"ready": True, "project_access_count": 2},
    )

    result = auth_mod.store_ai_provider_keys(
        session_id="session-1",
        codex_api_key="sk-test-codex-key-123456",
    )

    assert validations == {"cli": 1, "provider": 1}
    assert captured_upsert["nexus_id"] == "nexus-user-1"
    assert captured_upsert["codex_api_key_enc"] == "enc::sk-test-codex-key-123456"
    assert result["ready"] is True
    assert result["project_access_count"] == 2


def test_store_ai_provider_keys_rejects_codex_when_cli_login_validation_fails(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-2",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-user-2",
    )

    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _session_id: session)
    monkeypatch.setattr(auth_mod, "get_user_credentials", lambda _nexus_id: None)
    monkeypatch.setattr(auth_mod, "_now_utc", lambda: datetime.now(tz=UTC))
    monkeypatch.setattr(
        auth_mod,
        "_validate_codex_api_key_with_codex_cli_login",
        lambda _api_key: (False, "Codex CLI login validation failed: 401 Unauthorized"),
    )

    try:
        auth_mod.store_ai_provider_keys(
            session_id="session-2",
            codex_api_key="sk-test-codex-key-123456",
        )
        assert False, "expected codex login validation error"
    except ValueError as exc:
        assert "Codex CLI login validation failed" in str(exc)


def test_import_openclaw_local_codex_account_credentials(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    source_path = tmp_path / "codex-auth.json"
    source_payload = {
        "auth_mode": "chatgpt",
        "OPENAI_API_KEY": None,
        "tokens": {
            "access_token": "access-token-1234567890",
            "refresh_token": "refresh-token-1234567890",
        },
    }
    source_path.write_text(json.dumps(source_payload), encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setenv("NEXUS_RUNTIME_MODE", "openclaw")
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("NEXUS_OPENCLAW_CODEX_AUTH_PATH", str(source_path))
    monkeypatch.setattr(auth_mod, "upsert_ai_provider_keys", lambda **kwargs: captured.update(kwargs))

    result = auth_mod.import_openclaw_local_provider_credentials(
        nexus_id="nexus-codex-user",
        provider="codex",
    )

    copied_path = tmp_path / "runtime" / "auth" / "codex" / "nexus-codex-user" / "auth.json"
    assert result["imported"] is True
    assert result["state"] == "imported"
    assert copied_path.exists()
    assert json.loads(copied_path.read_text(encoding="utf-8")) == source_payload
    assert captured["nexus_id"] == "nexus-codex-user"
    assert captured["codex_account_enabled"] is True


def test_import_openclaw_local_gemini_account_credentials(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    source_path = tmp_path / "gemini-oauth.json"
    source_payload = {
        "access_token": "gemini-access-token-1234567890",
        "refresh_token": "gemini-refresh-token-1234567890",
    }
    source_path.write_text(json.dumps(source_payload), encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setenv("NEXUS_RUNTIME_MODE", "openclaw")
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("NEXUS_OPENCLAW_GEMINI_AUTH_PATH", str(source_path))
    monkeypatch.setattr(auth_mod, "upsert_ai_provider_keys", lambda **kwargs: captured.update(kwargs))

    result = auth_mod.import_openclaw_local_provider_credentials(
        nexus_id="nexus-gemini-user",
        provider="gemini",
    )

    user_copy = tmp_path / "runtime" / "auth" / "home" / "nexus-gemini-user" / ".gemini" / "oauth_creds.json"
    provider_copy = tmp_path / "runtime" / "auth" / "gemini" / "nexus-gemini-user" / "oauth_creds.json"
    settings_path = tmp_path / "runtime" / "auth" / "home" / "nexus-gemini-user" / ".gemini" / "settings.json"
    assert result["imported"] is True
    assert result["state"] == "imported"
    assert json.loads(user_copy.read_text(encoding="utf-8")) == source_payload
    assert json.loads(provider_copy.read_text(encoding="utf-8")) == source_payload
    assert settings_path.exists()
    assert captured["nexus_id"] == "nexus-gemini-user"
    assert captured["gemini_account_enabled"] is True


def test_import_openclaw_local_copilot_token(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    source_path = tmp_path / "copilot.json"
    source_payload = {"access_token": "copilot-token-1234567890"}
    source_path.write_text(json.dumps(source_payload), encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setenv("NEXUS_RUNTIME_MODE", "openclaw")
    monkeypatch.setenv("NEXUS_OPENCLAW_COPILOT_TOKEN_PATH", str(source_path))
    monkeypatch.setattr(auth_mod, "encrypt_secret", lambda value, key_version=1: f"enc::{value}")
    monkeypatch.setattr(auth_mod, "upsert_ai_provider_keys", lambda **kwargs: captured.update(kwargs))

    result = auth_mod.import_openclaw_local_provider_credentials(
        nexus_id="nexus-copilot-user",
        provider="copilot",
    )

    assert result["imported"] is True
    assert result["state"] == "imported"
    assert captured["nexus_id"] == "nexus-copilot-user"
    assert captured["copilot_github_token_enc"] == "enc::copilot-token-1234567890"


def test_import_openclaw_local_claude_account_credentials(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    source_path = tmp_path / "claude-credentials.json"
    source_payload = {
        "claudeAiOauth": {
            "accessToken": "claude-access-token-1234567890",
            "refreshToken": "claude-refresh-token-1234567890",
        }
    }
    source_path.write_text(json.dumps(source_payload), encoding="utf-8")

    captured: dict[str, object] = {}
    monkeypatch.setenv("NEXUS_RUNTIME_MODE", "openclaw")
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("NEXUS_OPENCLAW_CLAUDE_CREDENTIALS_PATH", str(source_path))
    monkeypatch.setattr(auth_mod, "upsert_ai_provider_keys", lambda **kwargs: captured.update(kwargs))

    result = auth_mod.import_openclaw_local_provider_credentials(
        nexus_id="nexus-claude-user",
        provider="claude",
    )

    user_copy = tmp_path / "runtime" / "auth" / "home" / "nexus-claude-user" / ".claude" / "credentials.json"
    provider_copy = tmp_path / "runtime" / "auth" / "claude" / "nexus-claude-user" / "credentials.json"
    assert result["imported"] is True
    assert result["state"] == "imported"
    assert json.loads(user_copy.read_text(encoding="utf-8")) == source_payload
    assert json.loads(provider_copy.read_text(encoding="utf-8")) == source_payload
    assert captured["nexus_id"] == "nexus-claude-user"
    assert captured["claude_account_enabled"] is True


def test_import_openclaw_local_claude_metadata_file_is_rejected(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    source_path = tmp_path / "claude-credentials.json"
    source_payload = {
        "firstStartTime": "2026-03-04T17:34:04.448Z",
        "opusProMigrationComplete": True,
        "sonnet1m45MigrationComplete": True,
        "userID": "e13829a5d7b07ee3edf",
    }
    source_path.write_text(json.dumps(source_payload), encoding="utf-8")

    monkeypatch.setenv("NEXUS_RUNTIME_MODE", "openclaw")
    monkeypatch.setenv("NEXUS_OPENCLAW_CLAUDE_CREDENTIALS_PATH", str(source_path))

    result = auth_mod.import_openclaw_local_provider_credentials(
        nexus_id="nexus-claude-user",
        provider="claude",
    )

    assert result["imported"] is False
    assert result["state"] == "invalid"


def test_import_openclaw_local_provider_credentials_is_disabled_outside_openclaw(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    monkeypatch.setenv("NEXUS_RUNTIME_MODE", "standalone")

    result = auth_mod.import_openclaw_local_provider_credentials(
        nexus_id="nexus-user",
        provider="codex",
    )

    assert result["imported"] is False
    assert result["state"] == "disabled"


def test_start_oauth_flow_requires_client_secret(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-oauth-1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="pending",
        nexus_id="nexus-oauth-1",
    )

    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "update_auth_session", lambda **_kwargs: None)
    monkeypatch.setenv("NEXUS_PUBLIC_BASE_URL", "https://nexus.example")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_ID", "gh-client-id")
    monkeypatch.delenv("NEXUS_GITHUB_CLIENT_SECRET", raising=False)

    try:
        auth_mod.start_oauth_flow("session-oauth-1", provider="github")
        assert False, "Expected missing GitHub client secret error"
    except ValueError as exc:
        assert "NEXUS_GITHUB_CLIENT_SECRET is required" in str(exc)


def test_github_exchange_code_for_token_includes_redirect_uri(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_ID", "gh-client-id")
    monkeypatch.setenv("NEXUS_GITHUB_CLIENT_SECRET", "gh-client-secret")
    monkeypatch.setenv("NEXUS_PUBLIC_BASE_URL", "https://nexus.example")

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "gh-access-token"}

    def _fake_post(url, headers, data, timeout):
        captured["url"] = url
        captured["headers"] = headers
        captured["data"] = data
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(auth_mod.requests, "post", _fake_post)

    payload = auth_mod._github_exchange_code_for_token("oauth-code")

    assert payload["access_token"] == "gh-access-token"
    assert captured["url"] == "https://github.com/login/oauth/access_token"
    assert isinstance(captured["data"], dict)
    assert captured["data"]["redirect_uri"] == "https://nexus.example/auth/github/callback"


def test_gitlab_exchange_code_for_token_honors_callback_override(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    monkeypatch.setenv("NEXUS_GITLAB_BASE_URL", "https://gitlab.example")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_ID", "gl-client-id")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_SECRET", "gl-client-secret")
    monkeypatch.setenv("NEXUS_GITLAB_CALLBACK_URL", "https://auth.example/custom/gitlab/callback")

    captured: dict[str, object] = {}

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {"access_token": "gl-access-token"}

    def _fake_post(url, data, timeout):
        captured["url"] = url
        captured["data"] = data
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(auth_mod.requests, "post", _fake_post)

    payload = auth_mod._gitlab_exchange_code_for_token("oauth-code")

    assert payload["access_token"] == "gl-access-token"
    assert captured["url"] == "https://gitlab.example/oauth/token"
    assert isinstance(captured["data"], dict)
    assert captured["data"]["redirect_uri"] == "https://auth.example/custom/gitlab/callback"


def test_parse_device_auth_url_and_code_strips_ansi_sequences():
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    raw_output = (
        "Visit \x1b[36mhttps://auth.openai.com/codex/device\x1b[0m "
        "and enter code \x1b[1mABCD-EFGH\x1b[0m"
    )

    verify_url, user_code = auth_mod._parse_device_auth_url_and_code(raw_output)

    assert verify_url == "https://auth.openai.com/codex/device"
    assert user_code == "ABCD-EFGH"


def test_parse_device_auth_url_and_code_ignores_authorization_word():
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    raw_output = (
        "Open https://auth.openai.com/codex/device and complete authorization in your browser. "
        "Then enter code: A1B2C3D4E"
    )

    verify_url, user_code = auth_mod._parse_device_auth_url_and_code(raw_output)

    assert verify_url == "https://auth.openai.com/codex/device"
    assert user_code == "A1B2C3D4E"


def test_github_get_retries_with_bearer_after_token_401(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    calls: list[dict[str, object]] = []

    class _Response:
        def __init__(self, status_code: int):
            self.status_code = status_code

    responses = [_Response(401), _Response(200)]

    def _fake_get(url, headers, timeout):
        calls.append({"url": url, "headers": dict(headers), "timeout": timeout})
        return responses.pop(0)

    monkeypatch.setattr(auth_mod.requests, "get", _fake_get)

    response = auth_mod._github_get("/user", "oauth-token")

    assert response.status_code == 200
    assert len(calls) == 2
    assert calls[0]["headers"]["Authorization"] == "token oauth-token"
    assert calls[1]["headers"]["Authorization"] == "Bearer oauth-token"


def test_start_provider_account_login_launches_codex_device_auth(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-device-1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-1",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("CODEX_CLI_PATH", "codex")

    class _Proc:
        pid = 4242

        @staticmethod
        def poll():
            return None

    def _fake_popen(cmd, cwd, env, stdin, stdout, stderr, text):
        assert cmd == ["codex", "login", "--device-auth"]
        assert str(env.get("CODEX_HOME", "")).endswith("/auth/codex/nexus-device-1")
        assert stdin == auth_mod.subprocess.DEVNULL
        return _Proc()

    monkeypatch.setattr(auth_mod.subprocess, "Popen", _fake_popen)

    result = auth_mod.start_provider_account_login(session_id=session.session_id, provider="codex")

    assert result["started"] is True
    assert result["state"] == "starting"
    assert result["provider"] == "codex"


def test_start_provider_account_login_launches_gemini_and_claude(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    gemini_session = SimpleNamespace(
        session_id="session-device-gemini",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-gemini",
    )
    claude_session = SimpleNamespace(
        session_id="session-device-claude",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-claude",
    )
    session_by_id = {
        gemini_session.session_id: gemini_session,
        claude_session.session_id: claude_session,
    }

    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda sid: session_by_id.get(sid))
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_CLI_PATH", "gemini")
    monkeypatch.setenv("NEXUS_GEMINI_ACCOUNT_CONNECT_USE_PTY", "0")
    monkeypatch.setenv("CLAUDE_CLI_PATH", "claude")

    calls: list[dict[str, object]] = []

    class _Proc:
        pid = 5252

        @staticmethod
        def poll():
            return None

    def _fake_popen(cmd, cwd, env, stdin, stdout, stderr, text):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env, "stdin": stdin})
        return _Proc()

    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS.clear()
    monkeypatch.setattr(auth_mod.subprocess, "Popen", _fake_popen)

    gemini_result = auth_mod.start_provider_account_login(
        session_id=gemini_session.session_id,
        provider="gemini",
    )
    claude_result = auth_mod.start_provider_account_login(
        session_id=claude_session.session_id,
        provider="claude",
    )

    assert gemini_result["started"] is True
    assert gemini_result["state"] == "starting"
    assert gemini_result["provider"] == "gemini"
    assert calls[0]["cmd"] == ["gemini", "--debug"]
    assert str(calls[0]["env"].get("GEMINI_HOME", "")).endswith("/auth/gemini/nexus-device-gemini")
    assert str(calls[0]["env"].get("GEMINI_CLI_HOME", "")).endswith("/auth/home/nexus-device-gemini")
    assert str(calls[0]["env"].get("HOME", "")).endswith("/auth/home/nexus-device-gemini")
    assert calls[0]["env"].get("NO_BROWSER") == "true"
    assert calls[0]["stdin"] == auth_mod.subprocess.PIPE
    gemini_settings = (
        tmp_path
        / "auth"
        / "home"
        / "nexus-device-gemini"
        / ".gemini"
        / "settings.json"
    )
    assert gemini_settings.exists()
    gemini_settings_payload = json.loads(gemini_settings.read_text(encoding="utf-8"))
    assert gemini_settings_payload["security"]["auth"]["selectedType"] == "oauth-personal"
    assert gemini_settings_payload["security"]["folderTrust"]["enabled"] is False
    assert gemini_settings_payload["selectedAuthType"] == "oauth-personal"

    assert claude_result["started"] is True
    assert claude_result["state"] == "starting"
    assert claude_result["provider"] == "claude"
    assert calls[1]["cmd"] == ["claude", "auth", "login"]
    assert str(calls[1]["env"].get("CLAUDE_HOME", "")).endswith("/auth/claude/nexus-device-claude")
    assert str(calls[1]["env"].get("HOME", "")).endswith("/auth/home/nexus-device-claude")
    assert calls[1]["stdin"] == auth_mod.subprocess.DEVNULL


def test_start_provider_account_login_wraps_gemini_with_script_tty_by_default(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-device-gemini-pty",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-gemini-pty",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setenv("GEMINI_CLI_PATH", "gemini")
    monkeypatch.delenv("NEXUS_GEMINI_ACCOUNT_CONNECT_USE_PTY", raising=False)
    monkeypatch.setattr(auth_mod.shutil, "which", lambda name: "/usr/bin/script" if name == "script" else None)

    calls: list[dict[str, object]] = []

    class _Proc:
        pid = 6262

        @staticmethod
        def poll():
            return None

    def _fake_popen(cmd, cwd, env, stdin, stdout, stderr, text):
        calls.append({"cmd": cmd, "cwd": cwd, "env": env, "stdin": stdin})
        return _Proc()

    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS.clear()
    monkeypatch.setattr(auth_mod.subprocess, "Popen", _fake_popen)

    result = auth_mod.start_provider_account_login(
        session_id=session.session_id,
        provider="gemini",
    )

    assert result["started"] is True
    assert result["provider"] == "gemini"
    assert calls
    assert calls[0]["cmd"] == ["/usr/bin/script", "-q", "-e", "-c", "gemini --debug", "/dev/null"]
    assert calls[0]["stdin"] == auth_mod.subprocess.PIPE


def test_start_provider_account_login_rejects_insecure_owner(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-device-owner",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-owner",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setenv("NEXUS_RUNTIME_DIR", str(tmp_path))
    monkeypatch.setattr(
        auth_mod,
        "_ensure_private_dir",
        lambda _path: (_ for _ in ()).throw(PermissionError("insecure auth directory")),
    )

    try:
        auth_mod.start_provider_account_login(session_id=session.session_id, provider="codex")
        assert False, "Expected insecure ownership check to fail"
    except PermissionError as exc:
        assert "insecure auth directory" in str(exc)


def test_get_provider_account_login_status_marks_connected_on_success(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-device-2",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-device-2",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")

    calls: dict[str, object] = {}
    monkeypatch.setattr(
        auth_mod,
        "store_ai_provider_keys",
        lambda **kwargs: calls.update(kwargs),
    )
    monkeypatch.setattr(
        auth_mod,
        "get_setup_status",
        lambda _nid: {"codex_account_enabled": True},
    )

    log_path = tmp_path / "codex_device.log"
    log_path.write_text("Open https://auth.openai.com/device and enter ABCD-EFGH\n", encoding="utf-8")

    class _DoneProc:
        @staticmethod
        def poll():
            return 0

    key = auth_mod._device_job_key(session_id=session.session_id, provider="codex")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "codex",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": _DoneProc(),
            "log_path": str(log_path),
            "log_file": None,
        }

    result = auth_mod.get_provider_account_login_status(session_id=session.session_id, provider="codex")

    assert result["state"] == "connected"
    assert result["connected"] is True
    assert calls["session_id"] == session.session_id
    assert calls["use_codex_account"] is True


def test_get_provider_account_login_status_marks_connected_for_gemini_and_claude(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    gemini_session = SimpleNamespace(
        session_id="session-status-gemini",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-status-gemini",
    )
    claude_session = SimpleNamespace(
        session_id="session-status-claude",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-status-claude",
    )
    session_by_id = {
        gemini_session.session_id: gemini_session,
        claude_session.session_id: claude_session,
    }

    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda sid: session_by_id.get(sid))
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")

    calls: list[dict[str, object]] = []
    monkeypatch.setattr(auth_mod, "store_ai_provider_keys", lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(
        auth_mod,
        "get_setup_status",
        lambda nexus_id: {
            "gemini_account_enabled": str(nexus_id).endswith("gemini"),
            "claude_account_enabled": str(nexus_id).endswith("claude"),
        },
    )

    gemini_log = tmp_path / "gemini_device.log"
    gemini_log.write_text("Open https://example.com/device and enter code G123-4567\n", encoding="utf-8")
    claude_log = tmp_path / "claude_device.log"
    claude_log.write_text("Visit https://example.com/verify and code CLD12345\n", encoding="utf-8")

    class _DoneProc:
        @staticmethod
        def poll():
            return 0

    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[auth_mod._device_job_key(session_id=gemini_session.session_id, provider="gemini")] = {
            "provider": "gemini",
            "session_id": gemini_session.session_id,
            "nexus_id": gemini_session.nexus_id,
            "process": _DoneProc(),
            "log_path": str(gemini_log),
            "log_file": None,
        }
        auth_mod._DEVICE_AUTH_JOBS[auth_mod._device_job_key(session_id=claude_session.session_id, provider="claude")] = {
            "provider": "claude",
            "session_id": claude_session.session_id,
            "nexus_id": claude_session.nexus_id,
            "process": _DoneProc(),
            "log_path": str(claude_log),
            "log_file": None,
        }

    gemini_result = auth_mod.get_provider_account_login_status(
        session_id=gemini_session.session_id,
        provider="gemini",
    )
    claude_result = auth_mod.get_provider_account_login_status(
        session_id=claude_session.session_id,
        provider="claude",
    )

    assert gemini_result["state"] == "connected"
    assert gemini_result["connected"] is True
    assert gemini_result["provider"] == "gemini"
    assert calls[0]["session_id"] == gemini_session.session_id
    assert calls[0]["use_gemini_account"] is True

    assert claude_result["state"] == "connected"
    assert claude_result["connected"] is True
    assert claude_result["provider"] == "claude"
    assert calls[1]["session_id"] == claude_session.session_id
    assert calls[1]["use_claude_account"] is True


def test_get_provider_account_login_status_marks_codex_rate_limited(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-rate-limited-codex",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-rate-limited-codex",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(auth_mod, "get_setup_status", lambda _nid: {"codex_account_enabled": False})

    log_path = tmp_path / "codex_rate_limited.log"
    log_path.write_text(
        "Error logging in with device code: device code request failed with status 429 Too Many Requests\n",
        encoding="utf-8",
    )

    class _FailedProc:
        @staticmethod
        def poll():
            return 1

    key = auth_mod._device_job_key(session_id=session.session_id, provider="codex")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "codex",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": _FailedProc(),
            "log_path": str(log_path),
            "log_file": None,
        }

    result = auth_mod.get_provider_account_login_status(session_id=session.session_id, provider="codex")

    assert result["state"] == "rate_limited"
    assert "rate-limited" in str(result["message"]).lower()


def test_get_provider_account_login_status_marks_interactive_required_for_gemini_and_claude(
    monkeypatch, tmp_path
):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    gemini_session = SimpleNamespace(
        session_id="session-interactive-gemini",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-interactive-gemini",
    )
    claude_session = SimpleNamespace(
        session_id="session-interactive-claude",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-interactive-claude",
    )
    session_by_id = {
        gemini_session.session_id: gemini_session,
        claude_session.session_id: claude_session,
    }
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda sid: session_by_id.get(sid))
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(
        auth_mod,
        "get_setup_status",
        lambda _nid: {
            "gemini_account_enabled": False,
            "claude_account_enabled": False,
        },
    )

    gemini_log = tmp_path / "gemini_interactive.log"
    gemini_log.write_text(
        (
            "Please set an Auth method in /var/lib/nexus/auth/home/uuid/.gemini/settings.json "
            "or specify one of the following environment variables before running: GEMINI_API_KEY\n"
        ),
        encoding="utf-8",
    )
    claude_log = tmp_path / "claude_interactive.log"
    claude_log.write_text("Not logged in · Please run /login\n", encoding="utf-8")

    class _FailedProc:
        @staticmethod
        def poll():
            return 1

    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[auth_mod._device_job_key(session_id=gemini_session.session_id, provider="gemini")] = {
            "provider": "gemini",
            "session_id": gemini_session.session_id,
            "nexus_id": gemini_session.nexus_id,
            "process": _FailedProc(),
            "log_path": str(gemini_log),
            "log_file": None,
        }
        auth_mod._DEVICE_AUTH_JOBS[auth_mod._device_job_key(session_id=claude_session.session_id, provider="claude")] = {
            "provider": "claude",
            "session_id": claude_session.session_id,
            "nexus_id": claude_session.nexus_id,
            "process": _FailedProc(),
            "log_path": str(claude_log),
            "log_file": None,
        }

    gemini_result = auth_mod.get_provider_account_login_status(
        session_id=gemini_session.session_id,
        provider="gemini",
    )
    claude_result = auth_mod.get_provider_account_login_status(
        session_id=claude_session.session_id,
        provider="claude",
    )

    assert gemini_result["state"] == "interactive_required"
    assert "did not start account login" in str(gemini_result["message"]).lower()
    assert claude_result["state"] == "interactive_required"
    assert "not authenticated" in str(claude_result["message"]).lower()


def test_get_provider_account_login_status_emits_failure_output_to_logs(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-log-tail-gemini",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
        nexus_id="nexus-log-tail-gemini",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(auth_mod, "get_setup_status", lambda _nid: {"gemini_account_enabled": False})

    log_path = tmp_path / "gemini_failure.log"
    log_path.write_text(
        "Please set an Auth method in /tmp/.gemini/settings.json before running.\n",
        encoding="utf-8",
    )

    class _FailedProc:
        @staticmethod
        def poll():
            return 1

    key = auth_mod._device_job_key(session_id=session.session_id, provider="gemini")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "gemini",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": _FailedProc(),
            "log_path": str(log_path),
            "log_file": None,
        }

    log_calls: list[tuple[str, tuple[object, ...]]] = []
    monkeypatch.setattr(auth_mod.logger, "error", lambda msg, *args: log_calls.append((msg, args)))

    result = auth_mod.get_provider_account_login_status(session_id=session.session_id, provider="gemini")

    assert result["state"] == "interactive_required"
    assert log_calls
    assert "output_tail" in str(log_calls[0][0])
    assert any("settings.json" in str(arg) for arg in log_calls[0][1])


def test_relay_provider_account_login_callback_replays_localhost_callback(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-relay-gemini",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="completed",
        nexus_id="nexus-relay-gemini",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(
        auth_mod,
        "get_provider_account_login_status",
        lambda session_id, provider: {
            "exists": True,
            "session_id": session_id,
            "session_ref": f"lsr_{session_id}",
            "provider": provider,
            "state": "pending",
            "message": "pending",
        },
    )

    calls: dict[str, object] = {}

    class _RelayResponse:
        status_code = 200

    def _fake_get(url, timeout, allow_redirects):
        calls["url"] = url
        calls["timeout"] = timeout
        calls["allow_redirects"] = allow_redirects
        return _RelayResponse()

    monkeypatch.setattr(auth_mod.requests, "get", _fake_get)

    class _RunningProc:
        @staticmethod
        def poll():
            return None

    key = auth_mod._device_job_key(session_id=session.session_id, provider="gemini")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "gemini",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": _RunningProc(),
            "log_path": "",
            "log_file": None,
        }

    result = auth_mod.relay_provider_account_login_callback(
        session_id=session.session_id,
        provider="gemini",
        callback_url="http://localhost:41725/oauth2callback?code=abc&state=xyz",
    )

    assert result["relayed"] is True
    assert result["relay_http_status"] == 200
    assert result["state"] == "pending"
    assert calls["url"] == "http://localhost:41725/oauth2callback?code=abc&state=xyz"
    assert calls["allow_redirects"] is False


def test_relay_provider_account_login_callback_rejects_non_localhost_url(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-relay-invalid",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="completed",
        nexus_id="nexus-relay-invalid",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")

    result = auth_mod.relay_provider_account_login_callback(
        session_id=session.session_id,
        provider="gemini",
        callback_url="https://evil.example/oauth2callback?code=abc",
    )

    assert result["relayed"] is False
    assert result["state"] == "invalid_callback_url"


def test_get_provider_account_login_status_marks_pending_requires_gemini_code(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-gemini-code-prompt",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="completed",
        nexus_id="nexus-gemini-code-prompt",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(auth_mod, "get_setup_status", lambda _nid: {"gemini_account_enabled": False})

    log_path = tmp_path / "gemini_pending.log"
    log_path.write_text(
        (
            "Please visit the following URL to authorize the application:\n"
            "https://accounts.google.com/o/oauth2/v2/auth?foo=bar\n"
            "Enter the authorization code:\n"
        ),
        encoding="utf-8",
    )

    class _RunningProc:
        @staticmethod
        def poll():
            return None

    key = auth_mod._device_job_key(session_id=session.session_id, provider="gemini")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "gemini",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": _RunningProc(),
            "log_path": str(log_path),
            "log_file": None,
        }

    result = auth_mod.get_provider_account_login_status(
        session_id=session.session_id,
        provider="gemini",
    )

    assert result["state"] == "pending"
    assert result["requires_code"] is True
    assert "accounts.google.com" in str(result["verify_url"])
    assert "authorization code" in str(result["message"]).lower()


def test_get_provider_account_login_status_autoconnects_gemini_existing_login(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-gemini-existing-login",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="completed",
        nexus_id="nexus-gemini-existing-login",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")

    persisted: dict[str, object] = {"saved": False}

    def _fake_get_setup_status(_nid: str):
        return {"gemini_account_enabled": bool(persisted["saved"])}

    def _fake_store(**kwargs):
        assert kwargs["session_id"] == session.session_id
        assert kwargs["use_gemini_account"] is True
        persisted["saved"] = True

    monkeypatch.setattr(auth_mod, "get_setup_status", _fake_get_setup_status)
    monkeypatch.setattr(auth_mod, "store_ai_provider_keys", _fake_store)

    log_path = tmp_path / "gemini_existing.log"
    log_path.write_text(
        "Logged in with Google: demo@example.com /auth\n",
        encoding="utf-8",
    )

    class _RunningProc:
        def __init__(self):
            self.terminated = False

        @staticmethod
        def poll():
            return None

        def terminate(self):
            self.terminated = True

    proc = _RunningProc()
    key = auth_mod._device_job_key(session_id=session.session_id, provider="gemini")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "gemini",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": proc,
            "log_path": str(log_path),
            "log_file": None,
        }

    result = auth_mod.get_provider_account_login_status(
        session_id=session.session_id,
        provider="gemini",
    )

    assert proc.terminated is True
    assert result["state"] == "connected"
    assert result["connected"] is True
    assert "already authenticated" in str(result["message"]).lower()


def test_submit_provider_account_login_code_writes_to_running_gemini_job(monkeypatch, tmp_path):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    session = SimpleNamespace(
        session_id="session-gemini-submit-code",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="completed",
        nexus_id="nexus-gemini-submit-code",
    )
    monkeypatch.setattr(auth_mod, "resolve_login_session_id", lambda value: value)
    monkeypatch.setattr(auth_mod, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(auth_mod, "get_setup_status", lambda _nid: {"gemini_account_enabled": False})

    log_path = tmp_path / "gemini_submit.log"
    log_path.write_text(
        "Enter the authorization code:\nhttps://accounts.google.com/o/oauth2/v2/auth?foo=bar\n",
        encoding="utf-8",
    )

    class _FakeStdin:
        def __init__(self):
            self.writes: list[str] = []

        def write(self, value: str):
            self.writes.append(value)

        def flush(self):
            return None

    class _RunningProc:
        def __init__(self):
            self.stdin = _FakeStdin()

        @staticmethod
        def poll():
            return None

    proc = _RunningProc()
    key = auth_mod._device_job_key(session_id=session.session_id, provider="gemini")
    with auth_mod._DEVICE_AUTH_LOCK:
        auth_mod._DEVICE_AUTH_JOBS[key] = {
            "provider": "gemini",
            "session_id": session.session_id,
            "nexus_id": session.nexus_id,
            "process": proc,
            "log_path": str(log_path),
            "log_file": None,
        }

    result = auth_mod.submit_provider_account_login_code(
        session_id=session.session_id,
        provider="gemini",
        authorization_code="4/0AX4XfWg-example",
    )

    assert proc.stdin.writes == ["4/0AX4XfWg-example\n"]
    assert result["submitted"] is True
    assert result["state"] == "pending"
    assert "submitted" in str(result["message"]).lower()


def test_start_provider_account_login_for_nexus_requires_oauth(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    monkeypatch.setattr(auth_mod, "get_latest_auth_session_for_nexus", lambda _nid: None)

    result = auth_mod.start_provider_account_login_for_nexus(
        nexus_id="nexus-no-session",
        provider="codex",
    )

    assert result["started"] is False
    assert result["state"] == "oauth_required"
    assert "Run /login" in str(result["message"])


def test_start_provider_account_login_for_nexus_uses_latest_session(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    record = SimpleNamespace(
        session_id="session-ready-1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="completed",
        nexus_id="nexus-ready-1",
    )

    monkeypatch.setattr(auth_mod, "get_latest_auth_session_for_nexus", lambda _nid: record)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(auth_mod, "_now_utc", lambda: datetime.now(tz=UTC))
    monkeypatch.setattr(
        auth_mod,
        "start_provider_account_login",
        lambda session_id, provider: {
            "started": True,
            "session_id": session_id,
            "session_ref": f"lsr_{session_id}",
            "provider": provider,
            "state": "starting",
            "message": "started",
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "get_provider_account_login_status",
        lambda session_id, provider: {
            "exists": True,
            "session_id": session_id,
            "session_ref": f"lsr_{session_id}",
            "provider": provider,
            "state": "pending",
            "verify_url": "https://verify.example",
            "user_code": "ABCD-1234",
            "connected": False,
            "message": "pending",
        },
    )

    result = auth_mod.start_provider_account_login_for_nexus(
        nexus_id="nexus-ready-1",
        provider="gemini",
    )

    assert result["started"] is True
    assert result["session_id"] == "session-ready-1"
    assert result["provider"] == "gemini"
    assert result["state"] == "pending"
    assert result["verify_url"] == "https://verify.example"
    assert result["user_code"] == "ABCD-1234"


def test_start_provider_account_login_for_nexus_forces_started_false_on_failure(monkeypatch):
    import nexus.core.auth.oauth_onboarding_domain as auth_mod

    record = SimpleNamespace(
        session_id="session-ready-failure",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="completed",
        nexus_id="nexus-ready-failure",
    )

    monkeypatch.setattr(auth_mod, "get_latest_auth_session_for_nexus", lambda _nid: record)
    monkeypatch.setattr(auth_mod, "format_login_session_ref", lambda sid: f"lsr_{sid}")
    monkeypatch.setattr(auth_mod, "_now_utc", lambda: datetime.now(tz=UTC))
    monkeypatch.setattr(
        auth_mod,
        "start_provider_account_login",
        lambda session_id, provider: {
            "started": True,
            "session_id": session_id,
            "session_ref": f"lsr_{session_id}",
            "provider": provider,
            "state": "starting",
            "message": "started",
        },
    )
    monkeypatch.setattr(
        auth_mod,
        "get_provider_account_login_status",
        lambda session_id, provider: {
            "exists": True,
            "session_id": session_id,
            "session_ref": f"lsr_{session_id}",
            "provider": provider,
            "state": "failed",
            "verify_url": "",
            "user_code": "",
            "connected": False,
            "message": "failed",
        },
    )

    result = auth_mod.start_provider_account_login_for_nexus(
        nexus_id="nexus-ready-failure",
        provider="claude",
    )

    assert result["state"] == "failed"
    assert result["started"] is False
