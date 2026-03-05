from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from nexus.core.auth import access_domain as access_svc
from nexus.core.auth import credential_store as store_svc
from nexus.core.auth import oauth_onboarding_domain as auth_svc


def test_store_ai_provider_keys_accepts_claude_only(monkeypatch):
    session = SimpleNamespace(
        session_id="session-1",
        nexus_id="nexus-1",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
    )

    captured: dict[str, Any] = {}

    monkeypatch.setattr(auth_svc, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(auth_svc, "_validate_claude_api_key_with_provider", lambda _k: (True, ""))
    monkeypatch.setattr(auth_svc, "encrypt_secret", lambda value, key_version=1: f"enc:{value}")
    monkeypatch.setattr(auth_svc, "update_auth_session", lambda **_kwargs: None)
    monkeypatch.setattr(auth_svc, "get_setup_status", lambda _nid: {"ready": True, "project_access_count": 2})
    monkeypatch.setattr(
        auth_svc,
        "get_user_credentials",
        lambda _nid: SimpleNamespace(
            codex_api_key_enc=None,
            gemini_api_key_enc=None,
            claude_api_key_enc=None,
            copilot_github_token_enc=None,
            github_token_enc=None,
            github_login=None,
        ),
    )

    def _capture_upsert(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(auth_svc, "upsert_ai_provider_keys", _capture_upsert)

    result = auth_svc.store_ai_provider_keys(
        session_id="session-1",
        claude_api_key="sk-ant-" + ("x" * 24),
    )

    assert result["ready"] is True
    assert captured["nexus_id"] == "nexus-1"
    assert captured["codex_api_key_enc"] is None
    assert captured["gemini_api_key_enc"] is None
    assert str(captured["claude_api_key_enc"]).startswith("enc:sk-ant-")


def test_store_ai_provider_keys_rejects_copilot_without_linked_github(monkeypatch):
    session = SimpleNamespace(
        session_id="session-2",
        nexus_id="nexus-2",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="oauth_done",
    )

    monkeypatch.setattr(auth_svc, "get_auth_session", lambda _sid: session)
    monkeypatch.setattr(
        auth_svc,
        "get_user_credentials",
        lambda _nid: SimpleNamespace(
            codex_api_key_enc=None,
            gemini_api_key_enc=None,
            claude_api_key_enc=None,
            copilot_github_token_enc=None,
            github_token_enc=None,
            github_login=None,
        ),
    )

    try:
        auth_svc.store_ai_provider_keys(session_id="session-2", allow_copilot=True)
        assert False, "Expected ValueError"
    except ValueError as exc:
        assert "Copilot requires a linked GitHub account" in str(exc)


def test_schema_migrations_include_claude_and_gitlab_oauth():
    if not getattr(store_svc, "_SA_AVAILABLE", False):
        return

    executed: list[str] = []

    class _Conn:
        def execute(self, statement):
            executed.append(str(statement))

    class _BeginCtx:
        def __enter__(self):
            return _Conn()

        def __exit__(self, exc_type, exc, tb):
            return False

    class _Engine:
        def begin(self):
            return _BeginCtx()

    store_svc._run_schema_migrations(_Engine())

    assert any("claude_api_key_enc" in stmt for stmt in executed)
    assert any("gitlab_refresh_token_enc" in stmt for stmt in executed)
    assert any("oauth_provider" in stmt for stmt in executed)
    assert any("chat_platform" in stmt for stmt in executed)
    assert any("onboarding_message_id" in stmt for stmt in executed)


def test_get_setup_status_counts_claude_key_for_readiness(monkeypatch):
    monkeypatch.setenv("NEXUS_AUTH_ENABLED", "true")
    monkeypatch.setattr(access_svc, "maybe_sync_user_project_access", lambda _nid: True)

    record = store_svc.CredentialRecord(
        nexus_id="nexus-3",
        auth_provider="gitlab",
        github_user_id=None,
        github_login=None,
        github_token_enc=None,
        github_refresh_token_enc=None,
        github_token_expires_at=None,
        gitlab_user_id=1,
        gitlab_username="gl-user",
        gitlab_token_enc="enc-gl",
        gitlab_refresh_token_enc=None,
        gitlab_token_expires_at=None,
        codex_api_key_enc=None,
        gemini_api_key_enc=None,
        claude_api_key_enc="enc-claude",
        copilot_github_token_enc=None,
        org_verified=True,
        org_verified_at=datetime.now(tz=UTC),
        last_access_sync_at=datetime.now(tz=UTC),
        key_version=1,
    )
    monkeypatch.setattr(access_svc, "get_user_credentials", lambda _nid: record)
    monkeypatch.setattr(
        access_svc,
        "get_user_project_access",
        lambda _nid: [SimpleNamespace(project_key="nexus")],
    )

    status = access_svc.get_setup_status("nexus-3")

    assert status["claude_key_set"] is True
    assert status["ai_provider_key_set"] is True
    assert status["ready"] is True


def test_build_execution_env_refreshes_expired_gitlab_token_and_injects_claude(monkeypatch):
    monkeypatch.setenv("NEXUS_AUTH_ENABLED", "true")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_ID", "client-id")
    monkeypatch.setenv("NEXUS_GITLAB_CLIENT_SECRET", "client-secret")

    record = store_svc.CredentialRecord(
        nexus_id="nexus-4",
        auth_provider="gitlab",
        github_user_id=None,
        github_login=None,
        github_token_enc=None,
        github_refresh_token_enc=None,
        github_token_expires_at=None,
        gitlab_user_id=4,
        gitlab_username="gl-user",
        gitlab_token_enc="enc-old-access",
        gitlab_refresh_token_enc="enc-old-refresh",
        gitlab_token_expires_at=datetime.now(tz=UTC) - timedelta(seconds=5),
        codex_api_key_enc=None,
        gemini_api_key_enc=None,
        claude_api_key_enc="enc-claude",
        copilot_github_token_enc=None,
        org_verified=True,
        org_verified_at=datetime.now(tz=UTC),
        last_access_sync_at=datetime.now(tz=UTC),
        key_version=3,
    )

    monkeypatch.setattr(access_svc, "get_user_credentials", lambda _nid: record)

    def _fake_decrypt(value: str) -> str:
        mapping = {
            "enc-old-access": "old-access-token",
            "enc-old-refresh": "old-refresh-token",
            "enc-claude": "sk-ant-claude-key",
        }
        return mapping[value]

    monkeypatch.setattr(access_svc, "decrypt_secret", _fake_decrypt)
    monkeypatch.setattr(
        access_svc,
        "encrypt_secret",
        lambda value, key_version=1: f"enc::{key_version}::{value}",
    )

    updates: dict[str, object] = {}
    monkeypatch.setattr(
        access_svc,
        "update_gitlab_oauth_tokens",
        lambda **kwargs: updates.update(kwargs),
    )

    class _Response:
        status_code = 200

        @staticmethod
        def json():
            return {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 3600,
            }

    monkeypatch.setattr(access_svc.requests, "post", lambda *args, **kwargs: _Response())

    env, err = access_svc.build_execution_env("nexus-4")

    assert err is None
    assert env["GITLAB_TOKEN"] == "new-access-token"
    assert env["GITHUB_TOKEN"] == "new-access-token"
    assert env["ANTHROPIC_API_KEY"] == "sk-ant-claude-key"
    assert env["CLAUDE_API_KEY"] == "sk-ant-claude-key"
    assert updates["nexus_id"] == "nexus-4"
    assert str(updates["gitlab_token_enc"]).endswith("new-access-token")


def test_complete_github_oauth_reuses_existing_nexus_identity(monkeypatch):
    session = SimpleNamespace(
        session_id="sess-gh",
        nexus_id="nexus-discord",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="pending",
        oauth_provider="github",
    )

    monkeypatch.setattr(auth_svc, "_assert_valid_callback_session", lambda _state, _provider: session)
    monkeypatch.setattr(
        auth_svc,
        "_github_exchange_code_for_token",
        lambda _code: {"access_token": "gh-token", "refresh_token": "gh-refresh", "expires_in": 3600},
    )
    monkeypatch.setattr(auth_svc, "_fetch_github_profile", lambda _token: {"id": 42, "login": "Ghabs95"})
    monkeypatch.setattr(auth_svc, "_fetch_github_org_logins", lambda _token: {"mybiohackingdata"})
    monkeypatch.setattr(auth_svc, "_allowed_github_orgs", lambda: set())
    monkeypatch.setattr(auth_svc, "encrypt_secret", lambda value, key_version=1: f"enc:{value}")

    monkeypatch.setattr(
        auth_svc,
        "find_user_credentials_by_github_identity",
        lambda **_kwargs: SimpleNamespace(nexus_id="nexus-telegram"),
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(auth_svc, "upsert_github_credentials", lambda **kwargs: captured.setdefault("upsert", kwargs))
    monkeypatch.setattr(auth_svc, "sync_user_project_access", lambda **kwargs: captured.setdefault("sync", kwargs) or 3)
    monkeypatch.setattr(auth_svc, "update_auth_session", lambda **kwargs: captured.setdefault("session", kwargs))

    result = auth_svc.complete_github_oauth(code="abc", state="xyz")

    assert result["nexus_id"] == "nexus-telegram"
    assert result["source_nexus_id"] == "nexus-discord"
    assert captured["upsert"]["nexus_id"] == "nexus-telegram"
    assert captured["sync"]["nexus_id"] == "nexus-telegram"
    assert captured["session"]["nexus_id"] == "nexus-telegram"


def test_complete_gitlab_oauth_reuses_existing_nexus_identity(monkeypatch):
    session = SimpleNamespace(
        session_id="sess-gl",
        nexus_id="nexus-discord",
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=5),
        status="pending",
        oauth_provider="gitlab",
    )

    monkeypatch.setattr(auth_svc, "_assert_valid_callback_session", lambda _state, _provider: session)
    monkeypatch.setattr(
        auth_svc,
        "_gitlab_exchange_code_for_token",
        lambda _code: {"access_token": "gl-token", "refresh_token": "gl-refresh", "expires_in": 3600},
    )
    monkeypatch.setattr(auth_svc, "_fetch_gitlab_profile", lambda _token: {"id": 99, "username": "Ghabs"})
    monkeypatch.setattr(auth_svc, "_fetch_gitlab_group_paths", lambda _token: {"mybiohackingdata/developer"})
    monkeypatch.setattr(auth_svc, "_allowed_gitlab_groups", lambda: set())
    monkeypatch.setattr(auth_svc, "encrypt_secret", lambda value, key_version=1: f"enc:{value}")

    monkeypatch.setattr(
        auth_svc,
        "find_user_credentials_by_gitlab_identity",
        lambda **_kwargs: SimpleNamespace(nexus_id="nexus-telegram"),
    )

    captured: dict[str, Any] = {}
    monkeypatch.setattr(auth_svc, "upsert_gitlab_credentials", lambda **kwargs: captured.setdefault("upsert", kwargs))
    monkeypatch.setattr(auth_svc, "sync_user_gitlab_project_access", lambda **kwargs: captured.setdefault("sync", kwargs) or 4)
    monkeypatch.setattr(auth_svc, "update_auth_session", lambda **kwargs: captured.setdefault("session", kwargs))

    result = auth_svc.complete_gitlab_oauth(code="abc", state="xyz")

    assert result["nexus_id"] == "nexus-telegram"
    assert result["source_nexus_id"] == "nexus-discord"
    assert captured["upsert"]["nexus_id"] == "nexus-telegram"
    assert captured["sync"]["nexus_id"] == "nexus-telegram"
    assert captured["session"]["nexus_id"] == "nexus-telegram"


def test_compute_project_grants_users_only_and_team_user_intersection():
    config = {
        "nexus": {
            "workspace": "nexus",
            "access_control": {
                "github_teams": ["Ghabs95/nexus-team"],
                "github_users": ["alice"],
            },
        },
        "acme": {
            "workspace": "acme",
            "access_control": {
                "github_users": ["bob"],
            },
        },
    }

    # In team + in user allowlist -> allowed.
    grants_alice = access_svc.compute_project_grants_for_github_acl(
        team_slugs={"ghabs95/nexus-team"},
        github_login="alice",
        project_config=config,
    )
    assert ("nexus", "alice") in grants_alice

    # In team but not in explicit user allowlist -> denied.
    grants_charlie = access_svc.compute_project_grants_for_github_acl(
        team_slugs={"ghabs95/nexus-team"},
        github_login="charlie",
        project_config=config,
    )
    assert ("nexus", "charlie") not in grants_charlie
    assert all(project != "nexus" for project, _source in grants_charlie)

    # Users-only project works without team membership.
    grants_bob = access_svc.compute_project_grants_for_github_acl(
        team_slugs=set(),
        github_login="bob",
        project_config=config,
    )
    assert ("acme", "bob") in grants_bob


def test_compute_gitlab_project_grants_users_only_and_group_user_intersection():
    config = {
        "nexus": {
            "workspace": "nexus",
            "access_control": {
                "gitlab_groups": ["ghabs/nexus-team"],
                "gitlab_users": ["anna"],
            },
        },
        "acme": {
            "workspace": "acme",
            "access_control": {
                "gitlab_users": ["mario"],
            },
        },
    }

    grants_anna = access_svc.compute_project_grants_for_gitlab_acl(
        group_paths={"ghabs/nexus-team"},
        gitlab_username="anna",
        project_config=config,
    )
    assert ("nexus", "anna") in grants_anna

    grants_luca = access_svc.compute_project_grants_for_gitlab_acl(
        group_paths={"ghabs/acme-team"},
        gitlab_username="luca",
        project_config=config,
    )
    assert all(project != "acme" for project, _source in grants_luca)

    grants_mario = access_svc.compute_project_grants_for_gitlab_acl(
        group_paths=set(),
        gitlab_username="mario",
        project_config=config,
    )
    assert ("acme", "mario") in grants_mario


def test_compute_gitlab_project_grants_accepts_top_level_group():
    config = {
        "acme": {
            "workspace": "acme",
            "access_control": {"gitlab_groups": ["acme"]},
        }
    }
    grants = access_svc.compute_project_grants_for_gitlab_acl(
        group_paths={"acme"},
        gitlab_username=None,
        project_config=config,
    )
    assert ("acme", "acme") in grants
