"""Tests for workflow policy plugin."""

from typing import Any

from nexus.plugins.builtin.workflow_policy_plugin import WorkflowPolicyPlugin


def test_workflow_policy_builders():
    plugin = WorkflowPolicyPlugin()

    transition = plugin.build_transition_message(
        issue_number="42",
        completed_agent="triage",
        next_agent="developer",
        repo="org/repo",
    )
    failed = plugin.build_autochain_failed_message(
        issue_number="42",
        completed_agent="developer",
        next_agent="qa",
        repo="org/repo",
    )
    complete = plugin.build_workflow_complete_message(
        issue_number="42",
        last_agent="qa",
        repo="org/repo",
        pr_urls=["https://github.com/org/repo/pull/1"],
    )

    assert "Agent Transition" in transition
    assert "Auto-chain Failed" in failed
    assert "Workflow Complete" in complete
    assert "https://github.com/org/repo/pull/1" in complete


def test_workflow_policy_finalize_workflow():
    captured: dict[str, Any] = {"notify": None, "pr_kwargs": None, "cleanup_kwargs": None}

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _create_pr_from_changes(**_kwargs):
        captured["pr_kwargs"] = _kwargs
        return "https://github.com/org/repo/pull/10"

    def _cleanup_worktree(**_kwargs):
        captured["cleanup_kwargs"] = _kwargs
        return True

    def _send_notification(message):
        captured["notify"] = message

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "create_pr_from_changes": _create_pr_from_changes,
            "cleanup_worktree": _cleanup_worktree,
            "send_notification": _send_notification,
        }
    )

    result = plugin.finalize_workflow(
        issue_number="42",
        repo="org/repo",
        last_agent="developer",
        project_name="nexus",
    )

    assert result["pr_urls"] == ["https://github.com/org/repo/pull/10"]
    assert result["issue_closed"] is False
    assert result["notification_sent"] is True
    assert "Workflow Complete" in captured["notify"]
    assert captured["pr_kwargs"]["issue_repo"] == "org/repo"
    assert captured["cleanup_kwargs"] == {"repo_dir": "/tmp/repo", "issue_number": "42"}


def test_workflow_policy_finalize_workflow_reuses_existing_pr():
    captured: dict[str, Any] = {
        "created": False,
        "cleanup_called": False,
        "synced_kwargs": None,
    }

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _find_existing_pr(**_kwargs):
        return "https://github.com/org/repo/pull/50"

    def _create_pr_from_changes(**_kwargs):
        captured["created"] = True
        return "https://github.com/org/repo/pull/99"

    def _sync_existing_pr_changes(**_kwargs):
        captured["synced_kwargs"] = _kwargs
        return True

    def _cleanup_worktree(**_kwargs):
        captured["cleanup_called"] = True
        return True

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "find_existing_pr": _find_existing_pr,
            "create_pr_from_changes": _create_pr_from_changes,
            "sync_existing_pr_changes": _sync_existing_pr_changes,
            "cleanup_worktree": _cleanup_worktree,
        }
    )

    result = plugin.finalize_workflow(
        issue_number="49",
        repo="org/repo",
        last_agent="writer",
        project_name="nexus",
    )

    assert result["pr_urls"] == ["https://github.com/org/repo/pull/50"]
    assert captured["created"] is False
    assert captured["synced_kwargs"] == {
        "repo": "org/repo",
        "repo_dir": "/tmp/repo",
        "issue_number": "49",
        "issue_repo": "org/repo",
        "base_branch": None,
    }
    assert captured["cleanup_called"] is True


def test_workflow_policy_finalize_uses_resolved_base_branch():
    captured: dict[str, Any] = {"pr_kwargs": None}

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _resolve_repo_branch(_project_name, _repo):
        return "develop"

    def _create_pr_from_changes(**kwargs):
        captured["pr_kwargs"] = kwargs
        return "https://github.com/org/repo/pull/10"

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "resolve_repo_branch": _resolve_repo_branch,
            "create_pr_from_changes": _create_pr_from_changes,
        }
    )

    plugin.finalize_workflow(
        issue_number="42",
        repo="org/repo",
        last_agent="developer",
        project_name="nexus",
    )

    assert captured["pr_kwargs"]["base_branch"] == "develop"


def test_workflow_policy_finalize_cleans_worktree():
    captured: dict[str, Any] = {"cleanup_called": False}

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _cleanup_worktree(**_kwargs):
        captured["cleanup_called"] = True
        return True

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "cleanup_worktree": _cleanup_worktree,
        }
    )

    plugin.finalize_workflow(
        issue_number="42",
        repo="org/repo",
        last_agent="developer",
        project_name="nexus",
    )

    assert captured["cleanup_called"] is True
