from __future__ import annotations

from urllib.parse import parse_qs, urlparse


def test_linkedin_oauth_scopes_default_full_set(monkeypatch):
    from nexus.core.auth import oauth_onboarding_domain as auth

    monkeypatch.delenv("NEXUS_LINKEDIN_SCOPES", raising=False)
    assert auth._linkedin_oauth_scopes() == (
        "openid profile email r_profile_basicinfo r_verify w_member_social"
    )


def test_linkedin_oauth_scopes_env_override_dedupes(monkeypatch):
    from nexus.core.auth import oauth_onboarding_domain as auth

    monkeypatch.setenv(
        "NEXUS_LINKEDIN_SCOPES",
        "openid,profile,email,openid,w_member_social,r_verify",
    )
    assert auth._linkedin_oauth_scopes() == "openid profile email w_member_social r_verify"


def test_start_linkedin_oauth_uses_configured_scope_string(monkeypatch):
    from nexus.core.auth import oauth_onboarding_domain as auth

    monkeypatch.setenv("NEXUS_LINKEDIN_CLIENT_ID", "client-123")
    monkeypatch.setenv("NEXUS_LINKEDIN_CLIENT_SECRET", "secret-123")
    monkeypatch.setenv("NEXUS_PUBLIC_BASE_URL", "https://nexus.example.com")
    monkeypatch.setenv("NEXUS_LINKEDIN_SCOPES", "openid profile email w_member_social")

    class _Session:
        expires_at = auth._now_utc().replace(year=2099)

    monkeypatch.setattr(auth, "resolve_login_session_id", lambda session_id: session_id)
    monkeypatch.setattr(auth, "get_auth_session", lambda _session_id: _Session())
    monkeypatch.setattr(auth, "update_auth_session", lambda **_kwargs: None)

    url, state = auth.start_linkedin_oauth("session-1")
    parsed = parse_qs(urlparse(url).query)

    assert state
    assert parsed["scope"] == ["openid profile email w_member_social"]
