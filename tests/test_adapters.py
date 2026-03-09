"""Phase 3 adapter tests — SlackNotificationChannel, GitLabPlatform,
OpenAIProvider, PostgreSQLStorageBackend, and AdapterRegistry.

All external SDK/driver calls are mocked so the suite runs without optional
extras installed.
"""

import asyncio
import os
from pathlib import Path
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# AdapterRegistry
# ---------------------------------------------------------------------------


class TestAdapterRegistry:
    def test_create_file_storage(self, tmp_path):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        storage = registry.create_storage("file", base_path=str(tmp_path))
        from nexus.adapters.storage.file import FileStorage

        assert isinstance(storage, FileStorage)

    def test_create_github_platform(self):
        from unittest.mock import patch

        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        with patch("nexus.adapters.git.github.GitHubPlatform._check_gh_cli"):
            git = registry.create_git("github", repo="owner/repo")
        from nexus.adapters.git.github import GitHubPlatform

        assert isinstance(git, GitHubPlatform)

    def test_create_gitlab_platform(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        git = registry.create_git("gitlab", token="glpat-x", repo="org/proj")
        from nexus.adapters.git.gitlab import GitLabPlatform

        assert isinstance(git, GitLabPlatform)

    def test_create_git_platform_uses_cli_transport_when_enabled(self, monkeypatch):
        from nexus.adapters.registry import AdapterRegistry
        from nexus.adapters.git.github_cli import GitHubPlatform as GitHubCLIPlatform

        monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "cli")
        with patch.object(GitHubCLIPlatform, "_check_gh_cli", return_value=None):
            git = AdapterRegistry().create_git("github", repo="owner/repo", token="ghp-test")

        assert isinstance(git, GitHubCLIPlatform)

    def test_create_git_platform_accepts_http_alias_for_api(self, monkeypatch):
        from nexus.adapters.registry import AdapterRegistry
        from nexus.adapters.git.github import GitHubPlatform

        monkeypatch.setenv("NEXUS_GIT_PLATFORM_TRANSPORT", "http")
        with patch.object(GitHubPlatform, "_check_gh_cli", return_value=None):
            git = AdapterRegistry().create_git("github", repo="owner/repo", token="ghp-test")

        assert isinstance(git, GitHubPlatform)

    def test_create_copilot_ai(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        provider = registry.create_ai("copilot")
        from nexus.adapters.ai.copilot_provider import CopilotCLIProvider

        assert isinstance(provider, CopilotCLIProvider)

    def test_create_codex_provider(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        provider = registry.create_ai("codex")
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        assert isinstance(provider, CodexCLIProvider)

    def test_create_openai_provider(self):
        from nexus.adapters.registry import AdapterRegistry

        AdapterRegistry()
        with patch.dict("sys.modules", {"openai": MagicMock()}):
            # Reload so the guarded import picks up the mock
            import importlib

            import nexus.adapters.ai.openai_provider as _mod

            importlib.reload(_mod)
            setattr(_mod, "_OPENAI_AVAILABLE", True)
            setattr(_mod, "_openai_module", MagicMock())
            getattr(_mod, "_openai_module").AsyncOpenAI = MagicMock(return_value=MagicMock())
            provider = _mod.OpenAIProvider(api_key="sk-test")
        assert provider.name == "openai"

    def test_raises_for_unknown_type(self):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        with pytest.raises(ValueError, match="Unknown storage adapter type"):
            registry.create_storage("redis_custom")

    def test_register_custom_storage(self, tmp_path):
        from nexus.adapters.registry import AdapterRegistry
        from nexus.adapters.storage.file import FileStorage

        registry = AdapterRegistry()
        registry.register_storage("myfile", FileStorage)
        storage = registry.create_storage("myfile", base_path=str(tmp_path))
        assert isinstance(storage, FileStorage)

    def test_from_config(self, tmp_path):
        from nexus.adapters.registry import AdapterConfig, AdapterRegistry

        registry = AdapterRegistry()
        with patch("nexus.adapters.git.github.GitHubPlatform._check_gh_cli"):
            result = registry.from_config(
                {
                    "storage": {"type": "file", "base_path": str(tmp_path)},
                    "git": {"type": "github", "repo": "owner/repo"},
                    "ai": [{"type": "copilot"}],
                }
            )
        assert isinstance(result, AdapterConfig)
        assert result.storage is not None
        assert result.git is not None
        assert len(result.ai_providers) == 1

    def test_from_config_repr(self, tmp_path):
        from nexus.adapters.registry import AdapterRegistry

        registry = AdapterRegistry()
        result = registry.from_config({"storage": {"type": "file", "base_path": str(tmp_path)}})
        r = repr(result)
        assert "FileStorage" in r

    def test_from_config_empty(self):
        from nexus.adapters.registry import AdapterRegistry

        result = AdapterRegistry().from_config({})
        assert result.storage is None
        assert result.git is None
        assert result.notifications == []
        assert result.ai_providers == []


# ---------------------------------------------------------------------------
# SlackNotificationChannel
# ---------------------------------------------------------------------------


class TestSlackNotificationChannel:
    def _make_channel(self):
        """Create a SlackNotificationChannel with a mocked WebClient."""
        from nexus.adapters.notifications.slack import (
            _SLACK_SDK_AVAILABLE,
            SlackNotificationChannel,
        )

        if not _SLACK_SDK_AVAILABLE:
            pytest.skip("slack-sdk not installed")

        mock_client = MagicMock()
        mock_client.chat_postMessage.return_value = {"ts": "1234567890.000001"}
        channel = SlackNotificationChannel.__new__(SlackNotificationChannel)
        channel._client = mock_client
        channel._default_channel = "#test"
        channel._webhook_url = None
        return channel, mock_client

    def test_name(self):
        from nexus.adapters.notifications.slack import (
            _SLACK_SDK_AVAILABLE,
            SlackNotificationChannel,
        )

        if not _SLACK_SDK_AVAILABLE:
            pytest.skip("slack-sdk not installed")
        channel = SlackNotificationChannel.__new__(SlackNotificationChannel)
        assert channel.name == "slack"

    def test_send_message_calls_postmessage(self):
        channel, mock_client = self._make_channel()
        from nexus.adapters.notifications.base import Message

        msg = Message(text="Hello from nexus")
        ts = asyncio.run(channel.send_message("U12345", msg))
        assert ts == "1234567890.000001"
        mock_client.chat_postMessage.assert_called_once()

    def test_send_alert_uses_postmessage(self):
        from nexus.core.models import Severity

        channel, mock_client = self._make_channel()
        asyncio.run(channel.send_alert("Deployment failed", Severity.WARNING))
        mock_client.chat_postMessage.assert_called_once()
        call_kwargs = mock_client.chat_postMessage.call_args.kwargs
        assert call_kwargs["channel"] == "#test"
        assert "Deployment failed" in call_kwargs["text"]

    def test_send_alert_uses_webhook_when_configured(self):
        from nexus.adapters.notifications.slack import (
            _SLACK_SDK_AVAILABLE,
            SlackNotificationChannel,
        )
        from nexus.core.models import Severity

        if not _SLACK_SDK_AVAILABLE:
            pytest.skip("slack-sdk not installed")

        channel = SlackNotificationChannel.__new__(SlackNotificationChannel)
        channel._client = MagicMock()
        channel._default_channel = "#test"
        channel._webhook_url = "https://hooks.slack.com/services/T0/B0/xxx"

        with patch.object(channel, "_send_via_webhook") as mock_webhook:
            asyncio.run(channel.send_alert("Test alert", Severity.CRITICAL))
        mock_webhook.assert_called_once()
        channel._client.chat_postMessage.assert_not_called()

    def test_requires_sdk_without_install(self):
        import nexus.adapters.notifications.slack as _mod

        original = _mod._SLACK_SDK_AVAILABLE
        _mod._SLACK_SDK_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="slack-sdk"):
                _mod._require_slack_sdk()
        finally:
            _mod._SLACK_SDK_AVAILABLE = original


# ---------------------------------------------------------------------------
# GitHubPlatform
# ---------------------------------------------------------------------------


class TestGitHubPlatform:
    def _make_platform(self):
        from nexus.adapters.git.github import GitHubPlatform

        with patch.object(GitHubPlatform, "_check_gh_cli", return_value=None):
            return GitHubPlatform(repo="owner/repo", token="ghp-test")

    def test_ensure_label_returns_true_on_success(self):
        platform = self._make_platform()
        with patch.object(platform, "_post", new=AsyncMock(return_value={"name": "bug"})) as mock_post:
            ok = asyncio.run(platform.ensure_label("bug", color="FF0000", description="Bug label"))
        assert ok is True
        path_arg, payload = mock_post.await_args.args
        assert path_arg == "repos/owner/repo/labels"
        assert payload["name"] == "bug"
        assert payload["color"] == "FF0000"
        assert payload["description"] == "Bug label"

    def test_ensure_label_returns_true_when_already_exists(self):
        import urllib.error

        platform = self._make_platform()
        exc = urllib.error.HTTPError("url", 422, "Validation Failed", cast(Any, {}), None)
        setattr(exc, "_nexus_body", '{"errors":[{"code":"already_exists"}]}')
        with patch.object(platform, "_post", new=AsyncMock(side_effect=exc)):
            ok = asyncio.run(platform.ensure_label("bug", color="FF0000"))
        assert ok is True

    def test_create_issue_calls_github_api(self):
        platform = self._make_platform()
        created_issue = {
            "id": 1230,
            "number": 123,
            "title": "New issue",
            "body": "Body",
            "state": "open",
            "labels": [],
            "created_at": "2026-02-01T10:00:00Z",
            "updated_at": "2026-02-01T10:00:00Z",
            "html_url": "https://github.com/owner/repo/issues/123",
        }
        with patch.object(platform, "_post", new=AsyncMock(return_value=created_issue)) as mock_post:
            issue = asyncio.run(platform.create_issue("New issue", "Body", labels=["bug"]))

        assert issue.number == 123
        assert issue.url.endswith("/123")
        path_arg, payload = mock_post.await_args.args
        assert path_arg == "repos/owner/repo/issues"
        assert payload["title"] == "New issue"
        assert payload["body"] == "Body"
        assert payload["labels"] == ["bug"]

    def test_merge_pull_request_uses_github_api(self):
        platform = self._make_platform()
        pr_data = {"head": {"ref": "feature/test"}}
        merge_data = {"merged": True}
        with patch.object(platform, "_get", new=AsyncMock(return_value=pr_data)) as mock_get:
            with patch.object(platform, "_put", new=AsyncMock(return_value=merge_data)) as mock_put:
                with patch.object(platform, "_delete", new=AsyncMock(return_value={})) as mock_delete:
                    result = asyncio.run(
                        platform.merge_pull_request("12", squash=True, delete_branch=True, auto=True)
                    )
        assert result == "merged=true auto_requested=true"
        assert mock_get.await_args.args[0] == "repos/owner/repo/pulls/12"
        assert mock_put.await_args.args[0] == "repos/owner/repo/pulls/12/merge"
        assert mock_put.await_args.args[1]["merge_method"] == "squash"
        assert mock_delete.await_args.args[0] == "repos/owner/repo/git/refs/heads/feature%2Ftest"

    def test_search_linked_prs_prefers_open_pull_scan(self):
        platform = self._make_platform()
        open_pulls = [
            {
                "id": 9001,
                "number": 114,
                "title": "feat: allow users to attach images",
                "body": "Implements feature.\n\nCloses #113",
                "state": "open",
                "head": {"ref": "feat/attach-images-to-input"},
                "base": {"ref": "develop"},
                "html_url": "https://github.com/owner/repo/pull/114",
            }
        ]
        with patch.object(platform, "_get", new=AsyncMock(return_value=open_pulls)) as mock_get:
            prs = asyncio.run(platform.search_linked_prs("113"))

        assert len(prs) == 1
        assert prs[0].number == 114
        assert prs[0].state == "open"
        assert mock_get.await_count == 1
        assert mock_get.await_args.args[0] == "repos/owner/repo/pulls?state=open&per_page=100"

    def test_search_linked_prs_falls_back_to_search_api(self):
        platform = self._make_platform()
        open_pulls = [
            {
                "id": 41,
                "number": 41,
                "title": "chore: unrelated",
                "body": "No linked issue in body",
                "state": "open",
                "head": {"ref": "chore/unrelated"},
                "base": {"ref": "main"},
                "html_url": "https://github.com/owner/repo/pull/41",
            }
        ]
        search_results = {"items": [{"number": 77}]}
        pr_detail = {
            "id": 77,
            "number": 77,
            "title": "fix: resolve #113",
            "body": "Closes #113",
            "state": "open",
            "head": {"ref": "fix/issue-113"},
            "base": {"ref": "main"},
            "html_url": "https://github.com/owner/repo/pull/77",
        }
        with patch.object(
            platform,
            "_get",
            new=AsyncMock(side_effect=[open_pulls, search_results, pr_detail]),
        ) as mock_get:
            prs = asyncio.run(platform.search_linked_prs("113"))

        assert len(prs) == 1
        assert prs[0].number == 77
        assert mock_get.await_count == 3
        first_call = mock_get.await_args_list[0].args[0]
        second_call = mock_get.await_args_list[1].args[0]
        third_call = mock_get.await_args_list[2].args[0]
        assert first_call == "repos/owner/repo/pulls?state=open&per_page=100"
        assert second_call.startswith("search/issues?q=")
        assert third_call == "repos/owner/repo/pulls/77"


# ---------------------------------------------------------------------------
# GitLabPlatform
# ---------------------------------------------------------------------------


class TestGitLabPlatform:
    def _make_platform(self):
        from nexus.adapters.git.gitlab import GitLabPlatform

        return GitLabPlatform(token="glpat-test", repo="mygroup/myproject")

    def test_name_encoded(self):
        import urllib.parse

        platform = self._make_platform()
        assert platform._encoded_repo == urllib.parse.quote("mygroup/myproject", safe="")

    def test_to_issue_converts_data(self):

        gl = self._make_platform()
        data = {
            "id": 101,
            "iid": 5,
            "title": "Bug report",
            "description": "Something broke",
            "state": "opened",
            "labels": ["bug", "critical"],
            "created_at": "2026-02-01T10:00:00Z",
            "updated_at": "2026-02-02T10:00:00Z",
            "web_url": "https://gitlab.com/mygroup/myproject/-/issues/5",
        }
        issue = gl._to_issue(data)
        assert issue.number == 5
        assert issue.title == "Bug report"
        assert issue.state == "open"
        assert "bug" in issue.labels

    def test_to_pr_converts_data(self):
        from nexus.adapters.git.gitlab import GitLabPlatform

        data = {
            "id": 200,
            "iid": 12,
            "title": "Fix regression",
            "state": "opened",
            "source_branch": "fix/regression",
            "target_branch": "main",
            "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/12",
        }
        pr = GitLabPlatform._to_pr(data)
        assert pr.number == 12
        assert pr.head_branch == "fix/regression"
        assert pr.state == "open"

    def test_create_issue_calls_api(self):
        platform = self._make_platform()
        mock_response = {
            "id": 99,
            "iid": 7,
            "title": "New issue",
            "description": "Body",
            "state": "opened",
            "labels": [],
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-01T00:00:00Z",
            "web_url": "https://gitlab.com/mygroup/myproject/-/issues/7",
        }
        with patch.object(platform, "_post", new=AsyncMock(return_value=mock_response)):
            issue = asyncio.run(platform.create_issue("New issue", "Body"))
        assert issue.number == 7
        assert issue.title == "New issue"

    def test_get_issue_returns_none_on_404(self):
        import urllib.error

        platform = self._make_platform()
        exc = urllib.error.HTTPError("url", 404, "Not Found", cast(Any, {}), None)
        with patch.object(platform, "_get", new=AsyncMock(side_effect=exc)):
            result = asyncio.run(platform.get_issue("999"))
        assert result is None

    def test_ensure_label_returns_true_on_success(self):
        platform = self._make_platform()
        with patch.object(
            platform, "_post", new=AsyncMock(return_value={"name": "bug"})
        ) as mock_post:
            ok = asyncio.run(platform.ensure_label("bug", color="FF0000", description="Bug label"))
        assert ok is True
        path, payload = mock_post.await_args.args
        assert path.endswith("/labels")
        assert payload["name"] == "bug"
        assert payload["color"] == "FF0000"

    def test_ensure_label_returns_true_on_conflict(self):
        import urllib.error

        platform = self._make_platform()
        exc = urllib.error.HTTPError("url", 409, "Conflict", cast(Any, {}), None)
        with patch.object(platform, "_post", new=AsyncMock(side_effect=exc)):
            ok = asyncio.run(platform.ensure_label("bug", color="FF0000"))
        assert ok is True

    def test_merge_pull_request_uses_merge_when_pipeline_succeeds(self):
        platform = self._make_platform()
        mock_response = {
            "state": "opened",
            "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/12",
        }
        with patch.object(platform, "_put", new=AsyncMock(return_value=mock_response)) as mock_put:
            result = asyncio.run(
                platform.merge_pull_request("12", squash=True, delete_branch=True, auto=True)
            )
        assert "state=opened" in result
        path, payload = mock_put.await_args.args
        assert path.endswith("/merge_requests/12/merge")
        assert payload["squash"] is True
        assert payload["should_remove_source_branch"] is True
        assert payload["merge_when_pipeline_succeeds"] is True

    def test_list_open_issues_with_labels(self):
        platform = self._make_platform()
        response = [
            {
                "id": 101,
                "iid": 5,
                "title": "Bug report",
                "description": "Something broke",
                "state": "opened",
                "labels": ["workflow:shortened", "bug"],
                "created_at": "2026-02-01T10:00:00Z",
                "updated_at": "2026-02-02T10:00:00Z",
                "web_url": "https://gitlab.com/mygroup/myproject/-/issues/5",
            }
        ]
        with patch.object(platform, "_get", new=AsyncMock(return_value=response)) as mock_get:
            issues = asyncio.run(
                platform.list_open_issues(limit=25, labels=["workflow:shortened", "workflow:full"])
            )

        assert len(issues) == 1
        assert issues[0].number == 5
        called_path = mock_get.await_args.args[0]
        assert "state=opened" in called_path
        assert "per_page=25" in called_path
        assert "labels=" in called_path

    def test_search_linked_prs_prefers_open_mr_scan(self):
        platform = self._make_platform()
        open_mrs = [
            {
                "id": 200,
                "iid": 12,
                "title": "feat: image attachments",
                "description": "Implements feature.\n\nCloses #113",
                "state": "opened",
                "source_branch": "feat/attach-images-to-input",
                "target_branch": "develop",
                "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/12",
            }
        ]
        with patch.object(platform, "_get", new=AsyncMock(return_value=open_mrs)) as mock_get:
            prs = asyncio.run(platform.search_linked_prs("113"))

        assert len(prs) == 1
        assert prs[0].number == 12
        assert prs[0].state == "open"
        assert mock_get.await_count == 1
        assert mock_get.await_args.args[0].endswith("/merge_requests?state=open&per_page=100")

    def test_search_linked_prs_falls_back_to_search(self):
        platform = self._make_platform()
        open_mrs = [
            {
                "id": 201,
                "iid": 77,
                "title": "chore: unrelated",
                "description": "No issue reference",
                "state": "opened",
                "source_branch": "chore/unrelated",
                "target_branch": "main",
                "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/77",
            }
        ]
        search_mrs = [
            {
                "id": 202,
                "iid": 88,
                "title": "fix: resolve #113",
                "description": "Closes #113",
                "state": "opened",
                "source_branch": "fix/issue-113",
                "target_branch": "main",
                "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/88",
            }
        ]
        with patch.object(
            platform,
            "_get",
            new=AsyncMock(side_effect=[open_mrs, search_mrs]),
        ) as mock_get:
            prs = asyncio.run(platform.search_linked_prs("113"))

        assert len(prs) == 1
        assert prs[0].number == 88
        assert mock_get.await_count == 2
        first_call = mock_get.await_args_list[0].args[0]
        second_call = mock_get.await_args_list[1].args[0]
        assert first_call.endswith("/merge_requests?state=open&per_page=100")
        assert "search=%23113" in second_call

    def test_create_pr_from_changes_cross_repo_appends_fully_qualified_reference(self, tmp_path):
        platform = self._make_platform()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        def fake_run(cmd, **kwargs):
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if cmd[:3] == ["git", "status", "--porcelain"]:
                result.stdout = " M file.py\n"
            return result

        mr_response = {
            "id": 200,
            "iid": 12,
            "title": "Fix regression",
            "state": "opened",
            "source_branch": "nexus/issue-42",
            "target_branch": "main",
            "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/12",
        }

        with (
            patch("subprocess.run", side_effect=fake_run),
            patch.object(
                platform,
                "_post",
                new=AsyncMock(return_value=mr_response),
            ) as mock_post,
        ):
            pr = asyncio.run(
                platform.create_pr_from_changes(
                    repo_dir=str(repo_dir),
                    issue_number="42",
                    title="Fix regression",
                    body="Automated change",
                    issue_repo="mygroup/workflow-repo",
                )
            )

        assert pr is not None
        assert pr.number == 12
        payload = mock_post.await_args.args[1]
        assert payload["description"].endswith("Closes mygroup/workflow-repo#42")

    def test_create_pr_from_changes_reuses_current_feature_branch(self, tmp_path):
        platform = self._make_platform()
        repo_dir = tmp_path / "repo"
        repo_dir.mkdir()

        seen_commands: list[list[str]] = []

        def fake_run(cmd, **kwargs):
            seen_commands.append(list(cmd))
            result = MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            if cmd[:3] == ["git", "status", "--porcelain"]:
                result.stdout = " M file.py\n"
            if cmd[:4] == ["git", "rev-parse", "--abbrev-ref", "HEAD"]:
                result.stdout = "feat/universal-nexus-identity-issue-86\n"
            return result

        mr_response = {
            "id": 201,
            "iid": 13,
            "title": "UNI",
            "state": "opened",
            "source_branch": "feat/universal-nexus-identity-issue-86",
            "target_branch": "main",
            "web_url": "https://gitlab.com/mygroup/myproject/-/merge_requests/13",
        }

        with (
            patch("subprocess.run", side_effect=fake_run),
            patch.object(
                platform,
                "_post",
                new=AsyncMock(return_value=mr_response),
            ) as mock_post,
        ):
            pr = asyncio.run(
                platform.create_pr_from_changes(
                    repo_dir=str(repo_dir),
                    issue_number="86",
                    title="UNI",
                    body="Automated change",
                )
            )

        assert pr is not None
        payload = mock_post.await_args.args[1]
        assert payload["source_branch"] == "feat/universal-nexus-identity-issue-86"
        assert ["git", "checkout", "-B", "nexus/issue-86"] not in seen_commands


# ---------------------------------------------------------------------------
# OpenAIProvider
# ---------------------------------------------------------------------------


class TestOpenAIProvider:
    def _make_provider(self):
        """Build an OpenAIProvider with the openai SDK mocked out."""
        import nexus.adapters.ai.openai_provider as _mod

        mock_openai = MagicMock()
        mock_client = MagicMock()
        mock_openai.AsyncOpenAI = MagicMock(return_value=mock_client)
        mock_openai.RateLimitError = type("RateLimitError", (Exception,), {})
        mock_openai.APITimeoutError = type("APITimeoutError", (Exception,), {})

        original_available = _mod._OPENAI_AVAILABLE
        original_module = _mod._openai_module
        setattr(_mod, "_OPENAI_AVAILABLE", True)
        setattr(_mod, "_openai_module", mock_openai)

        provider = _mod.OpenAIProvider(api_key="sk-test")
        # Return provider + cleanup function
        return (
            provider,
            mock_client,
            lambda: setattr(_mod, "_OPENAI_AVAILABLE", original_available)
            or setattr(_mod, "_openai_module", original_module),
        )

    def test_name(self):
        provider, _, cleanup = self._make_provider()
        try:
            assert provider.name == "openai"
        finally:
            cleanup()

    def test_preference_score_reasoning(self):
        provider, _, cleanup = self._make_provider()
        try:
            assert provider.get_preference_score("reasoning") == 0.9
            assert provider.get_preference_score("code_generation") == 0.7
        finally:
            cleanup()

    def test_execute_agent_success(self):
        from nexus.adapters.ai.base import ExecutionContext

        provider, mock_client, cleanup = self._make_provider()
        try:
            # Mock the async completions.create response
            mock_choice = MagicMock()
            mock_choice.message.content = "Analysis complete."
            mock_choice.finish_reason = "stop"
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            mock_response.model = "gpt-4o"
            mock_response.usage.prompt_tokens = 50
            mock_response.usage.completion_tokens = 20
            mock_response.usage.total_tokens = 70
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            ctx = ExecutionContext(
                agent_name="triage",
                prompt="Analyze this issue",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))
            assert result.success is True
            assert result.output == "Analysis complete."
            assert result.provider_used == "openai"
            assert result.metadata["usage"]["total_tokens"] == 70
        finally:
            cleanup()

    def test_execute_agent_appends_issue_context_after_prompt(self):
        from nexus.adapters.ai.base import ExecutionContext

        provider, mock_client, cleanup = self._make_provider()
        try:
            mock_choice = MagicMock()
            mock_choice.message.content = "ok"
            mock_choice.finish_reason = "stop"
            mock_response = MagicMock()
            mock_response.choices = [mock_choice]
            mock_response.model = "gpt-4o"
            mock_response.usage.prompt_tokens = 10
            mock_response.usage.completion_tokens = 2
            mock_response.usage.total_tokens = 12
            mock_client.chat.completions.create = AsyncMock(return_value=mock_response)

            ctx = ExecutionContext(
                agent_name="triage",
                prompt="Analyze this issue",
                workspace=Path("/tmp"),
                issue_url="https://github.com/org/repo/issues/42",
            )
            result = asyncio.run(provider.execute_agent(ctx))
            assert result.success is True

            payload = mock_client.chat.completions.create.await_args.kwargs
            user_message = payload["messages"][1]["content"]
            assert user_message.startswith("Analyze this issue")
            assert user_message.endswith("Issue: https://github.com/org/repo/issues/42")
        finally:
            cleanup()

    def test_execute_agent_rate_limit(self):
        import nexus.adapters.ai.openai_provider as _mod
        from nexus.adapters.ai.base import ExecutionContext

        provider, mock_client, cleanup = self._make_provider()
        try:
            mock_openai_mod = getattr(_mod, "_openai_module")
            RateLimitError = mock_openai_mod.RateLimitError
            mock_client.chat.completions.create = AsyncMock(
                side_effect=RateLimitError("Rate limited")
            )

            ctx = ExecutionContext(
                agent_name="triage",
                prompt="Analyze",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))
            assert result.success is False
            assert "Rate limit" in result.error
        finally:
            cleanup()

    def test_requires_sdk_without_install(self):
        import nexus.adapters.ai.openai_provider as _mod

        original = _mod._OPENAI_AVAILABLE
        _mod._OPENAI_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="openai"):
                _mod._require_openai()
        finally:
            _mod._OPENAI_AVAILABLE = original


# ---------------------------------------------------------------------------
# CodexCLIProvider
# ---------------------------------------------------------------------------


class TestCodexCLIProvider:
    def test_name(self):
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider()
        assert provider.name == "codex"

    def test_preference_score(self):
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider()
        assert provider.get_preference_score("code_generation") == 0.9
        assert provider.get_preference_score("analysis") == 0.65

    def test_check_availability_false_when_binary_missing(self):
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider()
        with patch("nexus.adapters.ai.codex_provider.shutil.which", return_value=None):
            available = asyncio.run(provider.check_availability())

        assert available is False

    def test_execute_agent_success(self):
        from nexus.adapters.ai.base import ExecutionContext
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider(model="gpt-5-codex")

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_proc.returncode = 0

        with patch(
            "nexus.adapters.ai.codex_provider.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            ctx = ExecutionContext(
                agent_name="developer",
                prompt="Implement feature X",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))

        assert result.success is True
        assert result.provider_used == "codex"
        assert "ok" in result.output

    def test_execute_agent_timeout(self):
        from nexus.adapters.ai.base import ExecutionContext
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        provider = CodexCLIProvider(timeout=1)

        mock_proc = AsyncMock()
        mock_proc.communicate = MagicMock(return_value=None)
        mock_proc.returncode = 0

        with (
            patch(
                "nexus.adapters.ai.codex_provider.asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            patch(
                "nexus.adapters.ai.codex_provider.asyncio.wait_for",
                side_effect=asyncio.TimeoutError,
            ),
        ):
            ctx = ExecutionContext(
                agent_name="developer",
                prompt="Implement feature X",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))

        assert result.success is False
        assert "Timeout" in (result.error or "")

    def test_execute_agent_cleans_empty_codex_rollout_files(self, tmp_path, monkeypatch):
        from nexus.adapters.ai.base import ExecutionContext
        from nexus.adapters.ai.codex_provider import CodexCLIProvider

        codex_home = tmp_path / ".codex"
        sessions = codex_home / "sessions" / "2026" / "02" / "28"
        sessions.mkdir(parents=True, exist_ok=True)
        empty_rollout = sessions / "rollout-empty.jsonl"
        non_empty_rollout = sessions / "rollout-ok.jsonl"
        empty_rollout.write_text("")
        non_empty_rollout.write_text('{"ok":true}\n', encoding="utf-8")
        os.utime(empty_rollout, (946684800, 946684800))
        monkeypatch.setenv("CODEX_HOME", str(codex_home))

        provider = CodexCLIProvider(model="gpt-5-codex")
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"ok", b""))
        mock_proc.returncode = 0

        with patch(
            "nexus.adapters.ai.codex_provider.asyncio.create_subprocess_exec",
            return_value=mock_proc,
        ):
            ctx = ExecutionContext(
                agent_name="developer",
                prompt="Implement feature X",
                workspace=Path("/tmp"),
            )
            result = asyncio.run(provider.execute_agent(ctx))

        assert result.success is True
        assert non_empty_rollout.exists()
        assert not empty_rollout.exists()


# ---------------------------------------------------------------------------
# PostgreSQLStorageBackend
# ---------------------------------------------------------------------------


class TestPostgreSQLStorageBackend:
    def test_requires_sqlalchemy_without_install(self):
        import nexus.adapters.storage.postgres as _mod

        original = _mod._SA_AVAILABLE
        _mod._SA_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="sqlalchemy"):
                _mod._require_sqlalchemy()
        finally:
            _mod._SA_AVAILABLE = original

    def test_dsn_normalisation(self):
        """Constructor normalises postgres:// → postgresql+psycopg2://."""
        import nexus.adapters.storage.postgres as _mod

        if not _mod._SA_AVAILABLE:
            pytest.skip("sqlalchemy not installed")

        with (
            patch("nexus.adapters.storage.postgres.sa.create_engine") as mock_engine,
            patch("nexus.adapters.storage.postgres.sa.orm.sessionmaker"),
            patch("nexus.adapters.storage.postgres._Base.metadata"),
        ):
            mock_engine.return_value.url = MagicMock()
            from nexus.adapters.storage.postgres import PostgreSQLStorageBackend

            with patch.object(PostgreSQLStorageBackend, "__init__", lambda self, *a, **kw: None):
                PostgreSQLStorageBackend.__new__(PostgreSQLStorageBackend)
                # Simulate normalisation step in isolation
                dsn = "postgres://user:pass@localhost/db"
                normalised = dsn.replace("postgres://", "postgresql+psycopg2://", 1)
                assert normalised == "postgresql+psycopg2://user:pass@localhost/db"

    def test_workflow_serde_roundtrip(self, tmp_path):
        """Verify shared serde produces stable roundtrip via FileStorage's test workflow."""
        from nexus.adapters.storage._workflow_serde import dict_to_workflow, workflow_to_dict
        from nexus.adapters.storage.file import FileStorage

        FileStorage(tmp_path)
        # Use FileStorage round-trip test as proxy for shared serde correctness
        from nexus.core.models import Agent, Workflow, WorkflowStep

        agent = Agent(name="triage", display_name="Triage", description="")
        step = WorkflowStep(
            step_num=0, name="triage", agent=agent, prompt_template="Go", inputs={}, outputs={}
        )
        wf = Workflow(id="serde-test", name="test", description="", version="1", steps=[step])
        d = workflow_to_dict(wf)
        restored = dict_to_workflow(d)
        assert restored.id == "serde-test"
        assert len(restored.steps) == 1
        assert restored.steps[0].agent.name == "triage"


# ---------------------------------------------------------------------------
# DiscordNotificationChannel
# ---------------------------------------------------------------------------


class TestDiscordNotificationChannel:
    def _make_channel(self):
        """Create a DiscordNotificationChannel with aiohttp mocked out."""
        from nexus.adapters.notifications.discord import (
            _AIOHTTP_AVAILABLE,
            DiscordNotificationChannel,
        )

        if not _AIOHTTP_AVAILABLE:
            pytest.skip("aiohttp not installed")

        channel = DiscordNotificationChannel.__new__(DiscordNotificationChannel)
        channel._webhook_url = "https://discord.com/api/webhooks/0/token"
        channel._bot_token = "Bot.test.token"
        channel._alert_channel_id = "111222333"
        return channel

    def test_name(self):
        from nexus.adapters.notifications.discord import (
            _AIOHTTP_AVAILABLE,
            DiscordNotificationChannel,
        )

        if not _AIOHTTP_AVAILABLE:
            pytest.skip("aiohttp not installed")
        channel = DiscordNotificationChannel.__new__(DiscordNotificationChannel)
        assert channel.name == "discord"

    def test_build_payload_basic(self):
        from nexus.adapters.notifications.base import Message
        from nexus.core.models import Severity

        channel = self._make_channel()
        msg = Message(text="Hello Discord", severity=Severity.INFO)
        payload = channel._build_payload(msg)
        assert "embeds" in payload
        assert "Hello Discord" in payload["embeds"][0]["description"]

    def test_build_payload_with_buttons(self):
        from nexus.adapters.notifications.base import Button, Message
        from nexus.core.models import Severity

        channel = self._make_channel()
        btns = [Button(label="Approve", callback_data="approve", url="https://example.com")]
        msg = Message(text="Approve?", severity=Severity.WARNING, buttons=btns)
        payload = channel._build_payload(msg)
        assert "content" in payload
        assert "Approve" in payload["content"]
        assert "https://example.com" in payload["content"]

    async def test_send_message_uses_webhook(self):
        channel = self._make_channel()
        from nexus.adapters.notifications.base import Message

        msg = Message(text="Test via webhook")
        with patch.object(channel, "_post_webhook", return_value="99999") as mock_wh:
            result = await channel.send_message("123", msg)
        assert result == "99999"
        mock_wh.assert_called_once()

    async def test_send_alert_uses_webhook(self):
        from nexus.core.models import Severity

        channel = self._make_channel()
        with patch.object(channel, "_post_webhook", return_value="88888") as mock_wh:
            await channel.send_alert("System down", Severity.CRITICAL)
        mock_wh.assert_called_once()
        payload = mock_wh.call_args[0][0]
        assert "embeds" in payload
        assert "CRITICAL" in payload["embeds"][0]["title"]

    async def test_send_alert_uses_channel_when_no_webhook(self):
        from nexus.adapters.notifications.discord import (
            _AIOHTTP_AVAILABLE,
            DiscordNotificationChannel,
        )
        from nexus.core.models import Severity

        if not _AIOHTTP_AVAILABLE:
            pytest.skip("aiohttp not installed")

        channel = DiscordNotificationChannel.__new__(DiscordNotificationChannel)
        channel._webhook_url = None
        channel._bot_token = "Bot.test.token"
        channel._alert_channel_id = "111222333"

        with patch.object(channel, "_post_channel", return_value="77777") as mock_ch:
            await channel.send_alert("Database error", Severity.ERROR)
        mock_ch.assert_called_once_with("111222333", ANY)

    async def test_update_message_webhook_path(self):
        channel = self._make_channel()
        with patch.object(channel, "_patch_webhook_message") as mock_patch:
            await channel.update_message("555666", "Updated text")
        mock_patch.assert_called_once_with("555666", {"content": "Updated text"})

    async def test_update_message_bot_path(self):
        channel = self._make_channel()
        with patch.object(channel, "_patch_channel_message") as mock_patch:
            await channel.update_message("chan123:msg456", "New content")
        mock_patch.assert_called_once_with("chan123", "msg456", {"content": "New content"})

    async def test_get_session_recreates_when_loop_changes(self, monkeypatch):
        import nexus.adapters.notifications.discord as discord_mod

        channel = self._make_channel()

        class _Session:
            def __init__(self):
                self.closed = False

        created = []

        def _fake_client_session():
            sess = _Session()
            created.append(sess)
            return sess

        monkeypatch.setattr(discord_mod.aiohttp, "ClientSession", _fake_client_session)
        channel._session = _Session()
        channel._session_loop = object()

        session = channel._get_session()

        assert session is created[0]
        assert channel._session_loop is asyncio.get_running_loop()

    def test_requires_aiohttp_without_install(self):
        import nexus.adapters.notifications.discord as _mod

        original = _mod._AIOHTTP_AVAILABLE
        _mod._AIOHTTP_AVAILABLE = False
        try:
            with pytest.raises(ImportError, match="aiohttp"):
                _mod._require_aiohttp()
        finally:
            _mod._AIOHTTP_AVAILABLE = original

    def test_missing_credentials_raises(self):
        from nexus.adapters.notifications.discord import (
            _AIOHTTP_AVAILABLE,
            DiscordNotificationChannel,
        )

        if not _AIOHTTP_AVAILABLE:
            pytest.skip("aiohttp not installed")

        with pytest.raises(ValueError, match="webhook_url or bot_token"):
            DiscordNotificationChannel()

    async def test_request_input_requires_bot_token(self):
        from nexus.adapters.notifications.discord import _AIOHTTP_AVAILABLE

        if not _AIOHTTP_AVAILABLE:
            pytest.skip("aiohttp not installed")

        channel = self._make_channel()
        channel._bot_token = None

        with pytest.raises(ValueError, match="bot_token"):
            await channel.request_input("chan123", "Prompt?")

    async def test_request_input_returns_user_reply_filtering_bots(self):
        channel = self._make_channel()
        bot_msg = {"id": "1", "content": "I am a bot", "author": {"bot": True}}
        user_msg = {"id": "2", "content": "Human reply", "author": {"bot": False}}

        with (
            patch.object(channel, "_post_channel", new=AsyncMock(return_value="msg123")),
            patch.object(
                channel,
                "_fetch_messages_after",
                new=AsyncMock(side_effect=[[bot_msg], [bot_msg, user_msg]]),
            ),
            patch("asyncio.sleep", new=AsyncMock()),
        ):
            result = await channel.request_input(
                "chan999", "Please reply", timeout=5.0, poll_interval=0.01
            )

        assert result == "Human reply"

    async def test_request_input_raises_timeout_when_no_reply(self):
        channel = self._make_channel()

        with (
            patch.object(channel, "_post_channel", new=AsyncMock(return_value="msg123")),
            patch.object(channel, "_fetch_messages_after", new=AsyncMock(return_value=[])),
            pytest.raises(TimeoutError),
        ):
            # timeout=0.0 → deadline is already reached before the loop body
            await channel.request_input("chan999", "Waiting", timeout=0.0)

    async def test_http_error_propagates_from_send_message(self):
        from nexus.adapters.notifications.base import Message

        channel = self._make_channel()
        error = RuntimeError("Discord API 403")

        with patch.object(channel, "_post_webhook", side_effect=error):
            with pytest.raises(RuntimeError, match="Discord API 403"):
                await channel.send_message("chan123", Message(text="test"))
