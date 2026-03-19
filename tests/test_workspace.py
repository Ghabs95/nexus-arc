"""Tests for nexus.core.workspace — WorkspaceManager."""

import os
import subprocess
import time
from unittest import mock

import pytest

from nexus.core.workspace import WorkspaceManager
from nexus.core.workspace import WorktreeProvisionError


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


def test_existing_branch_checked_out_uses_fallback_issue_branch(tmp_path):
    base = str(tmp_path)
    expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")

    def _fake_run(cmd, **kwargs):  # noqa: ANN001
        if cmd[:3] == ["git", "show-ref", "--verify"]:
            result = mock.MagicMock()
            result.stdout = ""
            result.stderr = ""
            result.returncode = 0 if cmd[-1] == "refs/heads/feat/my-feature" else 1
            return result
        if cmd[:4] == ["git", "worktree", "add", expected_dir]:
            raise subprocess.CalledProcessError(
                128,
                cmd,
                stderr="fatal: 'feat/my-feature' is already checked out at '/repo'",
            )
        if cmd[:4] == ["git", "worktree", "add", "-b"]:
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result
        raise AssertionError(f"Unexpected command: {cmd}")

    with (
        mock.patch("os.path.isdir", return_value=False),
        mock.patch("os.makedirs"),
        mock.patch("nexus.core.workspace.subprocess.run", side_effect=_fake_run) as mock_run,
    ):
        result = WorkspaceManager.provision_worktree(base, "42", branch_name="feat/my-feature")

    assert result == expected_dir
    last_cmd = mock_run.call_args_list[-1][0][0]
    assert last_cmd == [
        "git",
        "worktree",
        "add",
        "-b",
        "nexus/issue-42",
        expected_dir,
        "feat/my-feature",
    ]


def test_existing_branch_checked_out_uses_next_available_fallback_issue_branch(tmp_path):
    base = str(tmp_path)
    expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")

    def _fake_run(cmd, **kwargs):  # noqa: ANN001
        if cmd[:3] == ["git", "show-ref", "--verify"]:
            branch_ref = cmd[-1]
            result = mock.MagicMock()
            result.stdout = ""
            result.stderr = ""
            if branch_ref == "refs/heads/feat/my-feature":
                result.returncode = 0
                return result
            if branch_ref == "refs/heads/nexus/issue-42":
                result.returncode = 0
                return result
            if branch_ref == "refs/heads/nexus/issue-42-wt":
                result.returncode = 1
                return result
        if cmd[:4] == ["git", "worktree", "add", expected_dir]:
            raise subprocess.CalledProcessError(
                128,
                cmd,
                stderr="fatal: 'feat/my-feature' is already checked out at '/repo'",
            )
        if cmd[:4] == ["git", "worktree", "add", "-b"]:
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result
        raise AssertionError(f"Unexpected command: {cmd}")

    with (
        mock.patch("os.path.isdir", return_value=False),
        mock.patch("os.makedirs"),
        mock.patch("nexus.core.workspace.subprocess.run", side_effect=_fake_run) as mock_run,
    ):
        result = WorkspaceManager.provision_worktree(base, "42", branch_name="feat/my-feature")

    assert result == expected_dir
    last_cmd = mock_run.call_args_list[-1][0][0]
    assert last_cmd == [
        "git",
        "worktree",
        "add",
        "-b",
        "nexus/issue-42-wt",
        expected_dir,
        "feat/my-feature",
    ]


def test_existing_branch_checked_out_cleans_partial_dir_before_fallback(tmp_path):
    base = str(tmp_path)
    expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")

    def _fake_run(cmd, **kwargs):  # noqa: ANN001
        if cmd[:3] == ["git", "show-ref", "--verify"]:
            branch_ref = cmd[-1]
            result = mock.MagicMock()
            result.stdout = ""
            result.stderr = ""
            if branch_ref == "refs/heads/feat/my-feature":
                result.returncode = 0
                return result
            if branch_ref == "refs/heads/nexus/issue-42":
                result.returncode = 1
                return result
        if cmd[:4] == ["git", "worktree", "add", expected_dir]:
            os.makedirs(expected_dir, exist_ok=True)
            raise subprocess.CalledProcessError(
                128,
                cmd,
                stderr="fatal: 'feat/my-feature' is already checked out at '/repo'",
            )
        if cmd[:4] == ["git", "worktree", "add", "-b"]:
            assert not os.path.exists(expected_dir)
            result = mock.MagicMock()
            result.returncode = 0
            result.stdout = ""
            result.stderr = ""
            return result
        raise AssertionError(f"Unexpected command: {cmd}")

    with mock.patch("nexus.core.workspace.subprocess.run", side_effect=_fake_run):
        result = WorkspaceManager.provision_worktree(base, "42", branch_name="feat/my-feature")

    assert result == expected_dir


def test_provision_worktree_raises_when_initial_and_fallback_fail(tmp_path):
    base = str(tmp_path)
    expected_dir = os.path.join(base, ".nexus", "worktrees", "issue-42")

    def _fake_run(cmd, **kwargs):  # noqa: ANN001
        if cmd[:3] == ["git", "show-ref", "--verify"]:
            result = mock.MagicMock()
            result.returncode = 0  # branch exists locally
            result.stdout = ""
            result.stderr = ""
            return result
        if cmd[:4] == ["git", "worktree", "add", expected_dir]:
            raise subprocess.CalledProcessError(128, cmd, stderr="fatal: branch is checked out")
        if cmd[:4] == ["git", "worktree", "add", "-b"]:
            raise subprocess.CalledProcessError(128, cmd, stderr="fatal: fallback failed")
        raise AssertionError(f"Unexpected command: {cmd}")

    with (
        mock.patch("os.path.isdir", return_value=False),
        mock.patch("os.makedirs"),
        mock.patch("nexus.core.workspace.subprocess.run", side_effect=_fake_run),
    ):
        with pytest.raises(WorktreeProvisionError):
            WorkspaceManager.provision_worktree(base, "42", branch_name="feat/my-feature")


def test_sanitize_worktree_helper_scripts_removes_known_files(tmp_path):
    worktree = tmp_path / ".nexus" / "worktrees" / "issue-42"
    worktree.mkdir(parents=True)
    helper = worktree / "post_comments.py"
    helper.write_text("print('x')")

    removed = WorkspaceManager.sanitize_worktree_helper_scripts(str(worktree))

    assert str(helper) in removed
    assert not helper.exists()


class TestCleanupWorktreeSafety:
    @mock.patch("nexus.core.workspace.subprocess.run")
    def test_cleanup_worktree_passes_safe_directory_config(self, mock_run, tmp_path):
        issue_dir = tmp_path / ".nexus" / "worktrees" / "issue-42"
        issue_dir.mkdir(parents=True)
        mock_run.return_value = mock.MagicMock(returncode=0, stdout="", stderr="")

        ok = WorkspaceManager.cleanup_worktree(str(tmp_path), "42")

        assert ok is True
        assert mock_run.call_args[0][0] == [
            "git",
            "-c",
            f"safe.directory={tmp_path}",
            "-c",
            f"safe.directory={issue_dir}",
            "worktree",
            "remove",
            "--force",
            str(issue_dir),
        ]

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
