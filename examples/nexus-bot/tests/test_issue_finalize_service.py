import asyncio
import types
from unittest.mock import patch

from nexus.adapters.git.base import PullRequest
from nexus.core import issue_finalize as svc


class _FakePlatform:
    def __init__(self, prs=None):
        self.calls = []
        self._prs = prs or []

    async def create_pr_from_changes(self, **kwargs):
        self.calls.append(("create_pr_from_changes", kwargs))
        return types.SimpleNamespace(url="https://github.com/acme/repo/pull/1")

    async def close_issue(self, issue_number, comment=None):
        self.calls.append(("close_issue", issue_number, comment))
        return None

    async def search_linked_prs(self, issue_number):
        self.calls.append(("search_linked_prs", issue_number))
        return self._prs


class _WorkflowPlugin:
    async def get_workflow_status(self, issue_num):
        return {"state": "running"}


class _CompletedWorkflowPlugin:
    async def get_workflow_status(self, issue_num):
        return {"state": "completed", "issue": issue_num}


def test_verify_workflow_terminal_before_finalize_blocks_non_terminal():
    with patch.object(svc, "emit_alert") as mock_alert:
        ok = svc.verify_workflow_terminal_before_finalize(
            workflow_plugin=_WorkflowPlugin(),
            issue_num="42",
            project_name="proj-a",
        )
    assert ok is False
    mock_alert.assert_called_once()


def test_verify_workflow_terminal_before_finalize_runs_inside_event_loop():
    async def _call():
        with patch.object(svc, "emit_alert") as mock_alert:
            ok = svc.verify_workflow_terminal_before_finalize(
                workflow_plugin=_WorkflowPlugin(),
                issue_num="42",
                project_name="proj-a",
            )
        return ok, mock_alert.call_count

    ok, alert_calls = asyncio.run(_call())

    assert ok is False
    assert alert_calls == 1


def test_finalize_provider_helpers_delegate_to_git_platform():
    prs = [
        PullRequest(
            id="1",
            number=1,
            title="A",
            state="open",
            head_branch="feat/x",
            base_branch="main",
            url="https://github.com/acme/repo/pull/1",
        )
    ]
    platform = _FakePlatform(prs=prs)
    with patch.object(svc, "get_git_platform", return_value=platform):
        pr_url = svc.create_pr_from_changes(
            project_name="proj-a",
            repo="acme/repo",
            repo_dir="/tmp/repo",
            issue_number="42",
            title="PR",
            body="Body",
        )
        found = svc.find_existing_pr(project_name="proj-a", repo="acme/repo", issue_number="42")
        closed = svc.close_issue(
            project_name="proj-a", repo="acme/repo", issue_number="42", comment="done"
        )

    assert pr_url.endswith("/pull/1")
    assert found.endswith("/pull/1")
    assert closed is False  # helper wraps None -> False
    assert any(c[0] == "create_pr_from_changes" for c in platform.calls)
    create_call = next(c for c in platform.calls if c[0] == "create_pr_from_changes")
    assert create_call[1]["base_branch"] == "main"
    assert any(c[0] == "search_linked_prs" for c in platform.calls)
    assert any(c[0] == "close_issue" for c in platform.calls)


def test_finalize_provider_helpers_delegate_to_git_platform_inside_event_loop(tmp_path):
    prs = [
        PullRequest(
            id="1",
            number=1,
            title="A",
            state="merged",
            head_branch="feat/x",
            base_branch="main",
            url="https://github.com/acme/repo/pull/1",
        )
    ]
    platform = _FakePlatform(prs=prs)
    repo_dir = tmp_path / "repo"
    worktree = repo_dir / ".nexus" / "worktrees" / "issue-42"
    worktree.mkdir(parents=True)

    async def _call():
        with patch.object(svc, "get_git_platform", return_value=platform):
            pr_url = svc.create_pr_from_changes(
                project_name="proj-a",
                repo="acme/repo",
                repo_dir=str(repo_dir),
                issue_number="42",
                title="PR",
                body="Body",
            )
            found = svc.find_existing_pr(project_name="proj-a", repo="acme/repo", issue_number="42")
            details = svc.find_existing_pr_details(
                project_name="proj-a",
                repo="acme/repo",
                issue_number="42",
            )
            closed = svc.close_issue(
                project_name="proj-a",
                repo="acme/repo",
                issue_number="42",
                comment="done",
            )
            ok = svc.verify_workflow_terminal_before_finalize(
                workflow_plugin=_CompletedWorkflowPlugin(),
                issue_num="42",
                project_name="proj-a",
            )
        return pr_url, found, details, closed, ok

    pr_url, found, details, closed, ok = asyncio.run(_call())

    assert pr_url.endswith("/pull/1")
    assert found.endswith("/pull/1")
    assert details == {
        "url": "https://github.com/acme/repo/pull/1",
        "state": "merged",
    }
    assert closed is False
    assert ok is True
    assert any(c[0] == "create_pr_from_changes" for c in platform.calls)
    assert any(c[0] == "search_linked_prs" for c in platform.calls)
    assert any(c[0] == "close_issue" for c in platform.calls)


def test_cleanup_worktree_delegates_workspace_manager():
    with patch(
        "nexus.core.workspace.WorkspaceManager.cleanup_worktree_safe", return_value=True
    ) as mock_cleanup:
        ok = svc.cleanup_worktree(repo_dir="/tmp/repo", issue_number="42")
    assert ok is True
    mock_cleanup.assert_called_once_with(
        base_repo_path="/tmp/repo",
        issue_number="42",
        is_issue_agent_running=None,
        require_clean=True,
    )


def test_create_pr_from_changes_prefers_issue_worktree(tmp_path):
    platform = _FakePlatform()
    repo_dir = tmp_path / "repo"
    worktree = repo_dir / ".nexus" / "worktrees" / "issue-42"
    worktree.mkdir(parents=True)

    with patch.object(svc, "get_git_platform", return_value=platform):
        pr_url = svc.create_pr_from_changes(
            project_name="proj-a",
            repo="acme/repo",
            repo_dir=str(repo_dir),
            issue_number="42",
            title="PR",
            body="Body",
        )

    assert pr_url.endswith("/pull/1")
    create_call = next(c for c in platform.calls if c[0] == "create_pr_from_changes")
    assert create_call[1]["repo_dir"] == str(worktree)


def test_validate_pr_non_empty_diff_blocks_when_worktree_missing(tmp_path):
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir(parents=True)

    ok, reason = svc.validate_pr_non_empty_diff(
        project_name="proj-a",
        repo="acme/repo",
        issue_number="42",
        pr_url="https://gitlab.com/acme/repo/-/merge_requests/1",
        repo_dir=str(repo_dir),
        base_branch="develop",
    )

    assert ok is False
    assert "missing issue worktree" in reason


def test_validate_pr_non_empty_diff_uses_remote_gitlab_stats(tmp_path):
    repo_dir = tmp_path / "repo"
    worktree = repo_dir / ".nexus" / "worktrees" / "issue-42"
    worktree.mkdir(parents=True)

    class _GitLabPlatform:
        pass

    platform = _GitLabPlatform()
    with (
        patch.object(svc, "get_git_platform", return_value=platform),
        patch.object(svc, "_gitlab_mr_has_non_empty_diff", return_value=True),
    ):
        ok, reason = svc.validate_pr_non_empty_diff(
            project_name="proj-a",
            repo="acme/repo",
            issue_number="42",
            pr_url="https://gitlab.com/acme/repo/-/merge_requests/1",
            repo_dir=str(repo_dir),
            base_branch="develop",
        )

    assert ok is True
    assert reason == ""


def test_validate_pr_non_empty_diff_uses_remote_github_api_stats(tmp_path):
    repo_dir = tmp_path / "repo"
    worktree = repo_dir / ".nexus" / "worktrees" / "issue-42"
    worktree.mkdir(parents=True)

    class _GitHubPlatform:
        repo = "acme/repo"

        async def _get(self, path: str):
            assert path == "repos/acme/repo/pulls/7"
            return {"changedFiles": 3, "additions": 12, "deletions": 1}

    platform = _GitHubPlatform()
    with patch.object(svc, "get_git_platform", return_value=platform):
        ok, reason = svc.validate_pr_non_empty_diff(
            project_name="proj-a",
            repo="acme/repo",
            issue_number="42",
            pr_url="https://github.com/acme/repo/pull/7",
            repo_dir=str(repo_dir),
            base_branch="develop",
        )

    assert ok is True
    assert reason == ""
