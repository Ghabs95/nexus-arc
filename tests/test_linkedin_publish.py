import pytest

from nexus.core.social_publish_linkedin import publish_linkedin_text


class DummySession:
    def __init__(self, nexus_id):
        self.nexus_id = nexus_id


def test_resolve_nexus_id_and_dry_run(monkeypatch):
    # Monkeypatch the auth session lookup to return a fake session for a chat sender
    monkeypatch.setattr(
        "nexus.core.social_publish_linkedin._get_latest_auth_session_for_chat",
        lambda platform, cid: DummySession("nexus_1234"),
    )

    res = publish_linkedin_text(
        content="Hello from test",
        campaign_id="camp-1",
        chat_platform="telegram",
        chat_id="tg_42",
        dry_run=True,
    )

    assert res["ok"] is True
    assert res["dry_run"] is True
    assert res["nexus_id"] == "nexus_1234"
    assert "idempotency_key" in res


def test_missing_resolution_returns_error():
    res = publish_linkedin_text(
        content="Hello",
        campaign_id="c2",
        dry_run=True,
    )
    assert res["ok"] is False
    assert "unable to resolve" in res["error"]
