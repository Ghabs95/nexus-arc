"""Tests for platform-aware automation token selection in issue_finalize and nexus_agent_runtime."""

import types
from unittest.mock import AsyncMock, MagicMock, patch

import nexus.core.issue_finalize as finalize_svc


class _FakePlatform:
    def __init__(self):
        self.calls = []

    async def create_pr_from_changes(self, **kwargs):
        self.calls.append(("create_pr_from_changes", kwargs))
        return types.SimpleNamespace(url="https://github.com/acme/repo/pull/1")

    async def add_comment(self, issue_number, body):
        self.calls.append(("add_comment", issue_number, body))


def test_create_pr_from_changes_passes_platform_to_automation_token(tmp_path):
    """Platform is detected from project and forwarded to the token resolver."""
    fake_platform = _FakePlatform()
    repo_dir = tmp_path / "repo"
    worktree = repo_dir / ".nexus" / "worktrees" / "issue-7"
    worktree.mkdir(parents=True)

    with (
        patch.object(finalize_svc, "_detect_project_platform", return_value="github") as mock_detect,
        patch.object(finalize_svc, "_automation_git_token", return_value="bot-gh-token") as mock_token,
        patch.object(finalize_svc, "get_git_platform", return_value=fake_platform) as mock_platform,
    ):
        pr_url = finalize_svc.create_pr_from_changes(
            project_name="proj-a",
            repo="acme/repo",
            repo_dir=str(repo_dir),
            issue_number="7",
            title="PR title",
            body="PR body",
        )

    assert pr_url and pr_url.endswith("/pull/1")
    # Platform detection must happen with the project name
    mock_detect.assert_called_once_with("proj-a")
    # Token selection receives the detected platform
    mock_token.assert_called_once_with("github")
    # git_platform adapter is initialised with the automation token
    mock_platform.assert_called_once_with(
        "acme/repo",
        project_name="proj-a",
        token_override="bot-gh-token",
    )


def test_create_pr_from_changes_falls_back_to_requester_token_when_no_automation(tmp_path):
    """If no automation token is set, the provided token_override is used."""
    fake_platform = _FakePlatform()
    repo_dir = tmp_path / "repo"
    worktree = repo_dir / ".nexus" / "worktrees" / "issue-8"
    worktree.mkdir(parents=True)

    with (
        patch.object(finalize_svc, "_detect_project_platform", return_value="github"),
        patch.object(finalize_svc, "_automation_git_token", return_value=None),
        patch.object(finalize_svc, "get_git_platform", return_value=fake_platform) as mock_platform,
    ):
        finalize_svc.create_pr_from_changes(
            project_name="proj-a",
            repo="acme/repo",
            repo_dir=str(repo_dir),
            issue_number="8",
            title="PR",
            body="body",
            token_override="requester-token",
        )

    mock_platform.assert_called_once_with(
        "acme/repo",
        project_name="proj-a",
        token_override="requester-token",
    )


def test_post_completion_comment_passes_platform_to_automation_token():
    """post_completion_comment resolves platform before selecting the automation token."""
    from nexus.core.runtime.nexus_agent_runtime import NexusAgentRuntime

    runtime = NexusAgentRuntime(finalize_fn=lambda *a, **kw: None)
    fake_platform = _FakePlatform()

    with (
        patch(
            "nexus.core.runtime.nexus_agent_runtime._resolve_project_name_for_repo",
            return_value="proj-a",
        ),
        patch(
            "nexus.core.runtime.nexus_agent_runtime._resolve_platform_for_project",
            return_value="github",
        ) as mock_detect,
        patch(
            "nexus.core.runtime.nexus_agent_runtime._runtime_token_override",
            return_value="bot-token",
        ) as mock_token,
        patch(
            "nexus.core.orchestration.nexus_core_helpers.get_git_platform",
            return_value=fake_platform,
        ),
        patch.object(
            runtime,
            "_has_recent_duplicate_completion_comment",
            return_value=False,
        ),
    ):
        result = runtime.post_completion_comment("42", "acme/repo", "## Done")

    assert result is True
    mock_detect.assert_called_once_with("proj-a")
    mock_token.assert_called_once_with("github")
