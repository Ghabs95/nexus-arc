"""Tests for built-in GitHub issue plugin."""

import subprocess
from unittest.mock import MagicMock

from nexus.plugins.builtin.github_issue_cli_plugin import GitHubIssueCLIPlugin


class _Result:
    def __init__(self, stdout: str):
        self.stdout = stdout
        self.returncode = 0


def test_builtin_plugin_retries_without_labels(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 3, "timeout": 30})
    calls = {"count": 0}

    def _fake_run(cmd, check, timeout, capture_output, text):
        calls["count"] += 1
        if "--label" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="label failed")
        return _Result("https://github.com/owner/repo/issues/42\n")

    monkeypatch.setattr("nexus.plugins.builtin.github_issue_cli_plugin.subprocess.run", _fake_run)

    url = plugin.create_issue(
        title="Test",
        body="Body",
        labels=["project:nexus", "type:feature", "workflow:fast-track"],
    )

    assert calls["count"] == 4
    assert url == "https://github.com/owner/repo/issues/42"


def test_builtin_plugin_add_comment(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})
    captured = {"cmd": None}

    def _fake_run(cmd, check, timeout, capture_output, text):
        captured["cmd"] = cmd
        return _Result("ok")

    monkeypatch.setattr("nexus.plugins.builtin.github_issue_cli_plugin.subprocess.run", _fake_run)

    success = plugin.add_comment("42", "hello")

    assert success is True
    assert captured["cmd"] == [
        "gh",
        "issue",
        "comment",
        "42",
        "--repo",
        "owner/repo",
        "--body",
        "hello",
    ]


def test_builtin_plugin_issue_ops(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})
    commands = []

    def _fake_run(cmd, check, timeout, capture_output, text):
        commands.append((cmd, check))
        if cmd[:3] == ["gh", "issue", "view"]:
            return _Result('{"title":"T","body":"B"}')
        if cmd[:3] == ["gh", "issue", "list"]:
            return _Result('[{"number":1,"title":"A","state":"open"}]')
        return _Result("ok")

    monkeypatch.setattr("nexus.plugins.builtin.github_issue_cli_plugin.subprocess.run", _fake_run)

    assert plugin.ensure_label("agent:requested", "E6E6FA", "Requested") is True
    assert plugin.add_label("42", "agent:requested") is True
    issue = plugin.get_issue("42", ["title", "body"])
    assert issue == {"title": "T", "body": "B"}
    assert plugin.add_assignee("42", "@me") is True
    assert plugin.update_issue_body("42", "new body") is True
    assert plugin.close_issue("42") is True
    issues = plugin.list_issues(state="open", limit=5)
    assert issues == [{"number": 1, "title": "A", "state": "open"}]

    assert commands[0][0][:3] == ["gh", "label", "create"]
    assert commands[0][1] is False
    assert commands[1][0][:3] == ["gh", "issue", "edit"]
    assert commands[1][1] is True
    assert commands[2][0][:3] == ["gh", "issue", "view"]
    assert commands[2][1] is True
    assert commands[3][0][:3] == ["gh", "issue", "edit"]
    assert commands[3][1] is True
    assert commands[4][0][:3] == ["gh", "issue", "edit"]
    assert commands[4][1] is True
    assert commands[5][0][:3] == ["gh", "issue", "close"]
    assert commands[5][1] is True
    assert commands[6][0][:3] == ["gh", "issue", "list"]
    assert commands[6][1] is True


def test_builtin_plugin_ensure_label_returns_true_when_label_exists(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})

    class _ExistsResult:
        returncode = 1
        stdout = ""
        stderr = 'label with name "agent:requested" already exists'

    def _fake_run(cmd, check, timeout, capture_output, text):
        return _ExistsResult()

    monkeypatch.setattr("nexus.plugins.builtin.github_issue_cli_plugin.subprocess.run", _fake_run)

    assert plugin.ensure_label("agent:requested", "E6E6FA", "Requested") is True


def test_builtin_plugin_lists_issues_via_api(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "api")
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})
    data = [
        {"number": 1, "title": "A", "state": "open", "labels": [], "html_url": "u1"},
        {"number": 2, "title": "PR", "state": "open", "pull_request": {}, "labels": []},
    ]

    platform = MagicMock()
    platform._sync_request.return_value = data
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    issues = plugin.list_issues(state="open", limit=5)

    assert issues == [{"number": 1, "title": "A", "state": "open"}]
    platform._sync_request.assert_called_once_with(
        "GET",
        "repos/owner/repo/issues?state=open&per_page=5",
    )


def test_builtin_plugin_get_issue_includes_comments_via_api(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "api")
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})

    issue_payload = {
        "title": "T",
        "body": "B",
        "state": "open",
        "number": 113,
        "html_url": "https://github.com/owner/repo/issues/113",
        "created_at": "2026-03-08T15:00:00Z",
        "updated_at": "2026-03-08T15:01:00Z",
        "labels": [{"name": "workflow:shortened"}],
    }
    comments_payload = [
        {
            "id": 4019285436,
            "body": "## Implement Change Complete - developer\\n\\nReady for **@Reviewer**",
            "created_at": "2026-03-08T15:40:54Z",
            "updated_at": "2026-03-08T15:40:54Z",
        }
    ]

    calls: list[tuple[str, str]] = []

    def _sync_request(method, endpoint, payload=None):  # noqa: ANN001
        calls.append((method, endpoint))
        assert payload is None
        if endpoint.endswith("/comments"):
            return comments_payload
        return issue_payload

    platform = MagicMock()
    platform._sync_request.side_effect = _sync_request
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    issue = plugin.get_issue("113", ["title", "comments", "updatedAt"])

    assert issue == {
        "title": "T",
        "comments": [
            {
                "id": 4019285436,
                "body": "## Implement Change Complete - developer\\n\\nReady for **@Reviewer**",
                "createdAt": "2026-03-08T15:40:54Z",
                "updatedAt": "2026-03-08T15:40:54Z",
            }
        ],
        "updatedAt": "2026-03-08T15:01:00Z",
    }
    assert calls == [
        ("GET", "repos/owner/repo/issues/113"),
        ("GET", "repos/owner/repo/issues/113/comments"),
    ]


def test_builtin_plugin_get_issue_skips_comments_call_when_not_requested(monkeypatch):
    monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "api")
    plugin = GitHubIssueCLIPlugin({"repo": "owner/repo", "max_attempts": 2, "timeout": 30})

    platform = MagicMock()
    platform._sync_request.return_value = {
        "title": "T",
        "body": "B",
        "state": "open",
        "number": 113,
        "labels": [],
    }
    monkeypatch.setattr(plugin, "_platform", lambda: platform)

    issue = plugin.get_issue("113", ["title"])

    assert issue == {"title": "T"}
    platform._sync_request.assert_called_once_with("GET", "repos/owner/repo/issues/113")
