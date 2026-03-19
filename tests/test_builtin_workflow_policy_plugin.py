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


def test_workflow_policy_finalize_closes_issue_for_closed_existing_pr():
    captured: dict[str, Any] = {
        "closed_kwargs": None,
        "cleanup_called": False,
    }

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _find_existing_pr_details(**_kwargs):
        return {
            "url": "https://github.com/org/repo/pull/50",
            "state": "closed",
        }

    def _validate_pr_non_empty_diff(**_kwargs):
        return True, ""

    def _close_issue(**kwargs):
        captured["closed_kwargs"] = kwargs
        return True

    def _cleanup_worktree(**_kwargs):
        captured["cleanup_called"] = True
        return True

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "find_existing_pr_details": _find_existing_pr_details,
            "validate_pr_non_empty_diff": _validate_pr_non_empty_diff,
            "close_issue": _close_issue,
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
    assert result["issue_closed"] is True
    assert captured["closed_kwargs"] == {
        "repo": "org/repo",
        "issue_number": "49",
        "comment": "✅ Workflow completed. All agent steps finished successfully.\nLast agent: `writer`\nPRs:\n- https://github.com/org/repo/pull/50",
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

    def _create_pr_from_changes(**_kwargs):
        return "https://github.com/org/repo/pull/42"

    def _validate_pr_non_empty_diff(**_kwargs):
        return True, ""

    def _cleanup_worktree(**_kwargs):
        captured["cleanup_called"] = True
        return True

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "create_pr_from_changes": _create_pr_from_changes,
            "validate_pr_non_empty_diff": _validate_pr_non_empty_diff,
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


def test_workflow_policy_finalize_blocks_empty_existing_pr_diff():
    captured: dict[str, Any] = {"message": None}

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _find_existing_pr(**_kwargs):
        return "https://gitlab.com/acme/repo/-/merge_requests/1"

    def _validate_pr_non_empty_diff(**_kwargs):
        return False, "acme/repo: existing MR has empty diff"

    def _send_notification(message):
        captured["message"] = message

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "find_existing_pr": _find_existing_pr,
            "validate_pr_non_empty_diff": _validate_pr_non_empty_diff,
            "send_notification": _send_notification,
        }
    )

    result = plugin.finalize_workflow(
        issue_number="51",
        repo="acme/repo",
        last_agent="deployer",
        project_name="acme",
    )

    assert result["pr_urls"] == []
    assert result["finalization_blocked"] is True
    assert "empty diff" in " ".join(result["blocking_reasons"])
    assert result["notification_sent"] is True
    assert "Finalization Blocked" in str(captured["message"])


def test_workflow_policy_finalize_blocks_when_no_pr_urls_created():
    captured: dict[str, Any] = {"cleanup_called": False}

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _create_pr_from_changes(**_kwargs):
        return None

    def _cleanup_worktree(**_kwargs):
        captured["cleanup_called"] = True
        return True

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "create_pr_from_changes": _create_pr_from_changes,
            "cleanup_worktree": _cleanup_worktree,
        }
    )

    result = plugin.finalize_workflow(
        issue_number="52",
        repo="acme/repo",
        last_agent="deployer",
        project_name="acme",
    )

    assert result["pr_urls"] == []
    assert result["finalization_blocked"] is True
    assert any("No non-empty PR/MR diff found" in item for item in result["blocking_reasons"])
    assert captured["cleanup_called"] is False


def test_workflow_policy_finalize_can_skip_blocked_notification():
    captured: dict[str, Any] = {"message": None}

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _create_pr_from_changes(**_kwargs):
        return None

    def _send_notification(message):
        captured["message"] = message
        return True

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "create_pr_from_changes": _create_pr_from_changes,
            "send_notification": _send_notification,
        }
    )

    result = plugin.finalize_workflow(
        issue_number="52",
        repo="acme/repo",
        last_agent="deployer",
        project_name="acme",
        emit_notifications=False,
    )

    assert result["finalization_blocked"] is True
    assert result["notification_sent"] is False
    assert captured["message"] is None


def test_workflow_policy_finalize_accepts_keyword_only_git_dir_resolver():
    captured: dict[str, Any] = {"repo_dir": None}

    def _resolve_git_dir(*, project_name):
        assert project_name == "nexus"
        return "/tmp/repo"

    def _create_pr_from_changes(**kwargs):
        captured["repo_dir"] = kwargs["repo_dir"]
        return "https://github.com/org/repo/pull/77"

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dir": _resolve_git_dir,
            "create_pr_from_changes": _create_pr_from_changes,
        }
    )

    result = plugin.finalize_workflow(
        issue_number="77",
        repo="org/repo",
        last_agent="writer",
        project_name="nexus",
    )

    assert result["pr_urls"] == ["https://github.com/org/repo/pull/77"]
    assert captured["repo_dir"] == "/tmp/repo"


def test_workflow_policy_finalize_accepts_keyword_only_git_dirs_resolver():
    captured: dict[str, Any] = {"repo_dir": None}

    def _resolve_git_dirs(*, project_name):
        assert project_name == "nexus"
        return {"org/repo": "/tmp/repo"}

    def _create_pr_from_changes(**kwargs):
        captured["repo_dir"] = kwargs["repo_dir"]
        return "https://github.com/org/repo/pull/88"

    plugin = WorkflowPolicyPlugin(
        {
            "resolve_git_dirs": _resolve_git_dirs,
            "create_pr_from_changes": _create_pr_from_changes,
        }
    )

    result = plugin.finalize_workflow(
        issue_number="88",
        repo="org/repo",
        last_agent="writer",
        project_name="nexus",
    )

    assert result["pr_urls"] == ["https://github.com/org/repo/pull/88"]
    assert captured["repo_dir"] == "/tmp/repo"
