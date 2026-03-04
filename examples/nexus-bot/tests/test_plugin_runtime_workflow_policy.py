"""Tests for workflow policy plugin runtime wiring."""


def test_get_workflow_policy_plugin_forwards_find_existing_pr(monkeypatch):
    from nexus.core.orchestration import plugin_runtime

    captured = {}

    def _fake_profiled_plugin(profile, overrides=None, cache_key=None):
        captured["profile"] = profile
        captured["overrides"] = overrides or {}
        captured["cache_key"] = cache_key
        return object()

    monkeypatch.setattr(plugin_runtime, "get_profiled_plugin", _fake_profiled_plugin)

    def _resolve_git_dir(_project_name):
        return "/tmp/repo"

    def _create_pr_from_changes(**_kwargs):
        return "https://example/pr/1"

    def _find_existing_pr(**_kwargs):
        return "https://example/pr/existing"

    def _cleanup_worktree(**_kwargs):
        return True

    def _close_issue(**_kwargs):
        return True

    def _send_notification(_message):
        return None

    plugin_runtime.get_workflow_policy_plugin(
        resolve_git_dir=_resolve_git_dir,
        create_pr_from_changes=_create_pr_from_changes,
        find_existing_pr=_find_existing_pr,
        cleanup_worktree=_cleanup_worktree,
        close_issue=_close_issue,
        send_notification=_send_notification,
        cache_key="workflow-policy:test",
    )

    assert captured["profile"] == "workflow_policy"
    assert captured["cache_key"] == "workflow-policy:test"
    assert captured["overrides"]["resolve_git_dir"] is _resolve_git_dir
    assert captured["overrides"]["create_pr_from_changes"] is _create_pr_from_changes
    assert captured["overrides"]["find_existing_pr"] is _find_existing_pr
    assert captured["overrides"]["cleanup_worktree"] is _cleanup_worktree
    assert captured["overrides"]["close_issue"] is _close_issue
    assert captured["overrides"]["send_notification"] is _send_notification
