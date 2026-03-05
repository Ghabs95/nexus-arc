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


def test_verify_workflow_terminal_before_finalize_blocks_non_terminal():
    with patch.object(svc, "emit_alert") as mock_alert:
        ok = svc.verify_workflow_terminal_before_finalize(
            workflow_plugin=_WorkflowPlugin(),
            issue_num="42",
            project_name="proj-a",
        )
    assert ok is False
    mock_alert.assert_called_once()


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
