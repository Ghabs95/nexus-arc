from __future__ import annotations

from types import SimpleNamespace

from nexus.core.git_sync.workflow_start_sync_service import sync_project_repos_on_workflow_start


def test_sync_service_skips_when_disabled():
    result = sync_project_repos_on_workflow_start(
        issue_number="42",
        project_name="proj",
        project_cfg={},
        resolve_git_dirs=lambda _p: {},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
    )

    assert result["enabled"] is False
    assert result["skipped"] is True


def test_sync_service_fetches_all_resolved_repos(monkeypatch):
    calls: list[list[str]] = []

    def _fake_run(cmd, **kwargs):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="42",
        project_name="proj",
        project_cfg={"git_sync": {"on_workflow_start": True}},
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend", "acme/mobile": "/tmp/mobile"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, repo: "develop" if repo.endswith("backend") else "release",
    )

    assert result["blocked"] is False
    assert len(result["synced"]) == 2
    assert calls[0][-1] == "develop:refs/remotes/origin/develop"
    assert calls[1][-1] == "release:refs/remotes/origin/release"


def test_sync_service_retries_network_auth_then_continues(monkeypatch):
    attempts = {"count": 0}
    slept: list[float] = []
    alerts: list[dict] = []

    def _fake_run(_cmd, **_kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="fatal: could not resolve host")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="77",
        project_name="proj",
        project_cfg={
            "git_sync": {
                "on_workflow_start": True,
                "network_auth_retries": 2,
                "retry_backoff_seconds": 3,
                "decision_timeout_seconds": 5,
            }
        },
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
        emit_alert=lambda *args, **kwargs: alerts.append({"args": args, "kwargs": kwargs}),
        should_block_launch=lambda _issue, _project: False,
        sleep_fn=lambda value: slept.append(value),
    )

    assert attempts["count"] == 3
    assert result["blocked"] is False
    assert result["failures"][0]["kind"] == "network_auth"
    assert len(alerts) == 1
    assert slept[:2] == [3.0, 3.0]


def test_sync_service_blocks_when_stop_decision_detected(monkeypatch):
    attempts = {"count": 0}

    def _fake_run(_cmd, **_kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="authentication failed")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="88",
        project_name="proj",
        project_cfg={
            "git_sync": {
                "on_workflow_start": True,
                "network_auth_retries": 1,
                "retry_backoff_seconds": 1,
                "decision_timeout_seconds": 10,
            }
        },
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
        should_block_launch=lambda _issue, _project: True,
        sleep_fn=lambda _value: None,
    )

    assert attempts["count"] == 2
    assert result["blocked"] is True


def test_sync_service_non_network_failure_warns_and_continues(monkeypatch):
    attempts = {"count": 0}

    def _fake_run(_cmd, **_kwargs):
        attempts["count"] += 1
        return SimpleNamespace(returncode=1, stdout="", stderr="fatal: bad revision")

    monkeypatch.setattr("nexus.core.git_sync.workflow_start_sync_service.subprocess.run", _fake_run)

    result = sync_project_repos_on_workflow_start(
        issue_number="99",
        project_name="proj",
        project_cfg={"git_sync": {"on_workflow_start": True, "network_auth_retries": 5}},
        resolve_git_dirs=lambda _p: {"acme/backend": "/tmp/backend"},
        resolve_git_dir=lambda _p: None,
        get_repos=lambda _p: [],
        get_repo_branch=lambda _project, _repo: "main",
    )

    assert attempts["count"] == 1
    assert result["blocked"] is False
    assert result["failures"][0]["kind"] == "other"
