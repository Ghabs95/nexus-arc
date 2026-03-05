"""Tests for nexus.core.workspace — WorkspaceManager."""

import os
import time
from unittest import mock

from nexus.core.workspace import WorkspaceManager


# ---------------------------------------------------------------------------
# provision_worktree – branch_name parameter
# ---------------------------------------------------------------------------


def _mock_subprocess_run_ok(*args, **kwargs):
    """subprocess.run stub that always succeeds."""
    result = mock.MagicMock()
    result.returncode = 1  # branch does NOT exist → triggers creation
    result.stdout = ""
    result.stderr = ""
    return result


class TestProvisionWorktreeDefaultBranch:
    """Without explicit branch_name, falls back to ``nexus/issue-{N}``."""

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_default_branch_name(self, mock_isdir, mock_makedirs, mock_run, tmp_path):
        base = str(tmp_path)
        result = WorkspaceManager.provision_worktree(base, "42")

        expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")
        assert result == expected_dir

        # Second subprocess call should create worktree with default branch
        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        assert cmd == ["git", "worktree", "add", "-b", "nexus/issue-42", expected_dir]


class TestProvisionWorktreeCustomBranch:
    """When branch_name is provided, it should be used."""

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_custom_branch_name(self, mock_isdir, mock_makedirs, mock_run, tmp_path):
        base = str(tmp_path)
        result = WorkspaceManager.provision_worktree(base, "42", branch_name="feat/my-feature")

        expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")
        assert result == expected_dir

        # Should use the custom branch name
        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        assert cmd == ["git", "worktree", "add", "-b", "feat/my-feature", expected_dir]

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_empty_string_branch_falls_back_to_default(
        self, mock_isdir, mock_makedirs, mock_run, tmp_path
    ):
        base = str(tmp_path)
        WorkspaceManager.provision_worktree(base, "42", branch_name="")

        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        # Empty string is falsy, should fallback to default
        assert cmd == [
            "git",
            "worktree",
            "add",
            "-b",
            "nexus/issue-42",
            os.path.join(base, ".nexus", "worktrees", "issue-42"),
        ]

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_none_branch_falls_back_to_default(self, mock_isdir, mock_makedirs, mock_run, tmp_path):
        base = str(tmp_path)
        WorkspaceManager.provision_worktree(base, "42", branch_name=None)

        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        assert cmd == [
            "git",
            "worktree",
            "add",
            "-b",
            "nexus/issue-42",
            os.path.join(base, ".nexus", "worktrees", "issue-42"),
        ]

    @mock.patch("nexus.core.workspace.subprocess.run", side_effect=_mock_subprocess_run_ok)
    @mock.patch("os.makedirs")
    @mock.patch("os.path.isdir", return_value=False)
    def test_start_ref_is_used_for_new_branch(self, mock_isdir, mock_makedirs, mock_run, tmp_path):
        base = str(tmp_path)
        WorkspaceManager.provision_worktree(
            base,
            "42",
            branch_name="feat/my-feature",
            start_ref="origin/develop",
        )

        worktree_call = mock_run.call_args_list[-1]
        cmd = worktree_call[0][0]
        assert cmd == [
            "git",
            "worktree",
            "add",
            "-b",
            "feat/my-feature",
            os.path.join(base, ".nexus", "worktrees", "issue-42"),
            "origin/develop",
        ]


class TestCleanupWorktreeSafety:
    @mock.patch("nexus.core.workspace.WorkspaceManager.cleanup_worktree", return_value=True)
    def test_cleanup_worktree_safe_skips_when_agent_running(self, mock_cleanup, tmp_path):
        issue_dir = tmp_path / ".nexus" / "worktrees" / "issue-42"
        issue_dir.mkdir(parents=True)

        ok = WorkspaceManager.cleanup_worktree_safe(
            str(tmp_path),
            "42",
            is_issue_agent_running=lambda issue: issue == "42",
            require_clean=True,
        )

        assert ok is False
        mock_cleanup.assert_not_called()

    @mock.patch("nexus.core.workspace.WorkspaceManager.cleanup_worktree", return_value=True)
    @mock.patch("nexus.core.workspace.WorkspaceManager._is_worktree_clean_dir", return_value=False)
    def test_cleanup_worktree_safe_skips_when_dirty(self, mock_clean, mock_cleanup, tmp_path):
        issue_dir = tmp_path / ".nexus" / "worktrees" / "issue-42"
        issue_dir.mkdir(parents=True)

        ok = WorkspaceManager.cleanup_worktree_safe(
            str(tmp_path),
            "42",
            is_issue_agent_running=lambda _issue: False,
            require_clean=True,
        )

        assert ok is False
        mock_cleanup.assert_not_called()

    @mock.patch("nexus.core.workspace.WorkspaceManager.cleanup_worktree", return_value=True)
    @mock.patch("nexus.core.workspace.WorkspaceManager._is_worktree_clean_dir", return_value=True)
    def test_cleanup_worktree_safe_runs_when_idle_and_clean(
        self, mock_clean, mock_cleanup, tmp_path
    ):
        issue_dir = tmp_path / ".nexus" / "worktrees" / "issue-42"
        issue_dir.mkdir(parents=True)

        ok = WorkspaceManager.cleanup_worktree_safe(
            str(tmp_path),
            "42",
            is_issue_agent_running=lambda _issue: False,
            require_clean=True,
        )

        assert ok is True
        mock_cleanup.assert_called_once_with(str(tmp_path), "42")


class TestCleanupStaleWorktrees:
    @mock.patch("nexus.core.workspace.WorkspaceManager.cleanup_worktree", return_value=True)
    @mock.patch("nexus.core.workspace.WorkspaceManager._is_worktree_clean_dir", return_value=True)
    def test_cleanup_stale_worktrees_removes_old_clean_idle(
        self, mock_clean, mock_cleanup, tmp_path
    ):
        root = tmp_path / ".nexus" / "worktrees"
        old = root / "issue-11"
        recent = root / "issue-12"
        old.mkdir(parents=True)
        recent.mkdir(parents=True)

        now = int(time.time())
        old_ts = now - (9 * 24 * 3600)
        recent_ts = now - (2 * 3600)
        os.utime(old, (old_ts, old_ts))
        os.utime(recent, (recent_ts, recent_ts))

        stats = WorkspaceManager.cleanup_stale_worktrees(
            str(tmp_path),
            max_age_hours=24,
            is_issue_agent_running=lambda _issue: False,
            require_clean=True,
        )

        assert stats["scanned"] == 2
        assert stats["removed"] == 1
        assert stats["skipped_recent"] == 1
        mock_cleanup.assert_called_once_with(str(tmp_path), "11")
