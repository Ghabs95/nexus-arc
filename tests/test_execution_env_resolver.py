from __future__ import annotations

from nexus.core.auth import access_domain as access_svc
from nexus.core.auth import execution_env_resolver as resolver


def test_prepare_execution_env_allows_git_only_for_issue_write(monkeypatch):
    monkeypatch.setattr(access_svc, "_ensure_private_runtime_dir", lambda _path: None)
    monkeypatch.setattr(access_svc, "NEXUS_RUNTIME_DIR", "/tmp/nexus-test-runtime")

    env, error = access_svc.prepare_execution_env(
        "openclaw:user:alice",
        {"GITHUB_TOKEN": "ghu_test"},
        purpose="issue_write",
    )

    assert error is None
    assert env["GITHUB_TOKEN"] == "ghu_test"
    assert env["GITLAB_TOKEN"] == "ghu_test"
    assert env["HOME"].endswith("/openclaw:user:alice")


def test_resolve_execution_env_from_openclaw_broker(monkeypatch):
    captured: dict[str, object] = {}

    class _Response:
        ok = True
        status_code = 200

        def json(self):
            return {
                "env": {
                    "GITHUB_TOKEN": "ghu_brokered",
                    "OPENAI_API_KEY": "sk-brokered",
                },
                "expires_at": "2026-03-23T14:05:00Z",
            }

    def _fake_post(url, *, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        captured["timeout"] = timeout
        return _Response()

    monkeypatch.setattr(resolver, "NEXUS_EXECUTION_CREDENTIAL_SOURCE", "openclaw-broker")
    monkeypatch.setattr(
        resolver,
        "NEXUS_OPENCLAW_BROKER_URL",
        "http://127.0.0.1:8092/api/v1/nexus/credentials/lease",
    )
    monkeypatch.setattr(resolver, "NEXUS_OPENCLAW_BROKER_TOKEN", "shared-secret")
    monkeypatch.setattr(resolver, "NEXUS_OPENCLAW_BROKER_TIMEOUT_SECONDS", 9)
    monkeypatch.setattr(resolver.requests, "post", _fake_post)
    monkeypatch.setattr(
        resolver,
        "prepare_execution_env",
        lambda nexus_id, env, *, purpose="agent_run": (
            {"NEXUS_ID": nexus_id, **dict(env), "PURPOSE": purpose},
            None,
        ),
    )

    env, error = resolver.resolve_execution_env(
        "openclaw:user:alice",
        project_name="demo",
        repo_name="demo/repo",
        issue_url="https://github.com/demo/repo/issues/42",
        purpose="agent_run",
    )

    assert error is None
    assert captured["url"] == "http://127.0.0.1:8092/api/v1/nexus/credentials/lease"
    assert captured["headers"]["Authorization"] == "Bearer shared-secret"
    assert captured["timeout"] == 9
    assert captured["json"]["requester_nexus_id"] == "openclaw:user:alice"
    assert captured["json"]["project_name"] == "demo"
    assert captured["json"]["purpose"] == "agent_run"
    assert env["GITHUB_TOKEN"] == "ghu_brokered"
    assert env["OPENAI_API_KEY"] == "sk-brokered"
