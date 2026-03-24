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


def test_resolve_execution_env_uses_credential_store(monkeypatch):
    monkeypatch.setattr(
        resolver,
        "build_execution_env",
        lambda requester_id, *, purpose="agent_run": (
            {"NEXUS_ID": requester_id, "GITHUB_TOKEN": "ghu_store", "PURPOSE": purpose},
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
    assert env["NEXUS_ID"] == "openclaw:user:alice"
    assert env["GITHUB_TOKEN"] == "ghu_store"
    assert env["PURPOSE"] == "agent_run"
