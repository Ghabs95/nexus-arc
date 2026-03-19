from nexus.core.auth import access_domain as access_svc


def test_get_auth_onboarding_disabled_message_includes_key_requirements(monkeypatch):
    monkeypatch.setenv("NEXUS_AUTH_ENABLED", "false")
    monkeypatch.setenv("NEXUS_STORAGE_BACKEND", "filesystem")
    monkeypatch.delenv("NEXUS_STORAGE_DSN", raising=False)
    monkeypatch.delenv("NEXUS_PUBLIC_BASE_URL", raising=False)
    monkeypatch.delenv("NEXUS_GITHUB_CLIENT_ID", raising=False)
    monkeypatch.delenv("NEXUS_GITHUB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("NEXUS_GITLAB_CLIENT_ID", raising=False)
    monkeypatch.delenv("NEXUS_GITLAB_CLIENT_SECRET", raising=False)
    monkeypatch.delenv("NEXUS_CREDENTIALS_MASTER_KEY", raising=False)

    message = access_svc.get_auth_onboarding_disabled_message()

    assert "Auth onboarding is disabled in this environment" in message
    assert "NEXUS_AUTH_ENABLED=true" in message
    assert "NEXUS_STORAGE_BACKEND=postgres" in message
    assert "NEXUS_STORAGE_DSN" in message
    assert "NEXUS_PUBLIC_BASE_URL" in message
    assert "NEXUS_CREDENTIALS_MASTER_KEY" in message


def test_get_auth_onboarding_disabled_message_is_empty_when_enabled(monkeypatch):
    monkeypatch.setenv("NEXUS_AUTH_ENABLED", "true")

    message = access_svc.get_auth_onboarding_disabled_message()

    assert message == ""
