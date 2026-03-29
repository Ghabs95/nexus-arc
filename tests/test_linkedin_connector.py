from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from nexus.connectors.linkedin import LinkedInClient, LinkedInConnectorError, linkedin_connector_service


class _FakeResponse:
    def __init__(self, payload, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"http {self.status_code}")

    def json(self):
        return self._payload


def test_linkedin_client_build_headers():
    client = LinkedInClient("token-123", author_urn="urn:li:person:abc")
    headers = client.build_headers()
    assert headers["Authorization"] == "Bearer token-123"
    assert headers["X-Restli-Protocol-Version"] == "2.0.0"


def test_linkedin_client_get_profile_me_adds_author_urn(monkeypatch):
    import nexus.connectors.linkedin as linkedin_mod

    def fake_get(url, headers, timeout):
        assert url == linkedin_mod.LINKEDIN_OPENID_USERINFO_URL
        assert headers["Authorization"] == "Bearer token-123"
        assert timeout == 9
        return _FakeResponse({"sub": "abc123", "name": "Ada Lovelace"})

    monkeypatch.setattr(linkedin_mod.requests, "get", fake_get)

    profile = LinkedInClient("token-123").get_profile_me(timeout=9)
    assert profile["name"] == "Ada Lovelace"
    assert profile["author_urn"] == "urn:li:person:abc123"


def test_linkedin_connector_auth_status_uses_stored_credentials(monkeypatch):
    import nexus.connectors.linkedin as linkedin_mod

    expires_at = datetime.now(tz=UTC) + timedelta(hours=1)
    record = SimpleNamespace(
        linkedin_token_enc="enc-token",
        linkedin_author_urn="urn:li:person:abc123",
        linkedin_token_expires_at=expires_at,
    )

    monkeypatch.setattr(linkedin_mod, "_get_user_credentials", lambda nexus_id: record)
    monkeypatch.setattr(linkedin_mod, "_decrypt_secret", lambda value: "token-123")

    status = linkedin_connector_service.get_auth_status(nexus_id="nexus-user-1")
    assert status.connected is True
    assert status.has_access_token is True
    assert status.has_author_urn is True
    assert status.author_urn == "urn:li:person:abc123"
    assert status.is_expired is False
    assert status.expires_at == expires_at


def test_linkedin_connector_profile_me_uses_decrypted_token(monkeypatch):
    import nexus.connectors.linkedin as linkedin_mod

    record = SimpleNamespace(
        linkedin_token_enc="enc-token",
        linkedin_author_urn="urn:li:person:abc123",
        linkedin_token_expires_at=None,
    )

    monkeypatch.setattr(linkedin_mod, "_get_user_credentials", lambda nexus_id: record)
    monkeypatch.setattr(linkedin_mod, "_decrypt_secret", lambda value: "token-123")

    def fake_get(url, headers, timeout):
        assert headers["Authorization"] == "Bearer token-123"
        return _FakeResponse({"sub": "abc123", "localizedFirstName": "Ada"})

    monkeypatch.setattr(linkedin_mod.requests, "get", fake_get)

    profile = linkedin_connector_service.get_profile_me(nexus_id="nexus-user-1")
    assert profile["author_urn"] == "urn:li:person:abc123"
    assert profile["localizedFirstName"] == "Ada"


def test_linkedin_connector_requires_stored_credentials(monkeypatch):
    import nexus.connectors.linkedin as linkedin_mod

    monkeypatch.setattr(linkedin_mod, "_get_user_credentials", lambda nexus_id: None)

    with pytest.raises(LinkedInConnectorError, match="No credential record"):
        linkedin_connector_service.get_connection(nexus_id="missing-user")
