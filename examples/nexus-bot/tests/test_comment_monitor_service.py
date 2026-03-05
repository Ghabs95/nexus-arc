import types
from unittest.mock import MagicMock

from nexus.core.comment_monitor_service import (
    comment_needs_user_input,
    comment_preview,
    run_comment_monitor_cycle,
)


def test_comment_helpers_detect_and_preview():
    assert comment_needs_user_input("Need your input please provide details") is True
    assert comment_needs_user_input("normal status update") is False
    assert comment_preview("a" * 210).endswith("...")


def test_run_comment_monitor_cycle_skips_non_github_and_dedups():
    logger = MagicMock()
    notified = {10}
    alerts = []
    cleared = []
    recorded = []

    comments = [
        types.SimpleNamespace(id=10, body="blocker: old"),  # dedup
        types.SimpleNamespace(id=11, body="Questions for @ghabs about config"),
    ]

    run_comment_monitor_cycle(
        logger=logger,
        iter_projects=lambda: [("gitlabproj", {}), ("ghproj", {})],
        get_project_platform=lambda p: "gitlab" if p == "gitlabproj" else "github",
        get_repo=lambda p: f"acme/{p}",
        list_workflow_issue_numbers=lambda project, repo: [77] if project == "ghproj" else [55],
        get_bot_comments=lambda project, repo, issue: comments if project == "ghproj" else [],
        notify_agent_needs_input=lambda issue, who, preview, project=None: alerts.append(
            (issue, who, preview, project)
        )
        or True,
        notified_comments=notified,
        clear_polling_failures=lambda scope: cleared.append(scope),
        record_polling_failure=lambda scope, exc: recorded.append((scope, str(exc))),
    )

    assert recorded == []
    assert any(scope == "agent-comments:loop" for scope in cleared)
    assert len(alerts) == 1
    assert alerts[0][0] == 77
    assert 11 in notified


def test_run_comment_monitor_cycle_records_list_failure():
    recorded = []

    run_comment_monitor_cycle(
        logger=MagicMock(),
        iter_projects=lambda: [("ghproj", {})],
        get_project_platform=lambda _p: "github",
        get_repo=lambda _p: "acme/repo",
        list_workflow_issue_numbers=lambda *_args: (_ for _ in ()).throw(RuntimeError("list boom")),
        get_bot_comments=lambda *_args: [],
        notify_agent_needs_input=lambda *args, **kwargs: True,
        notified_comments=set(),
        clear_polling_failures=lambda _scope: None,
        record_polling_failure=lambda scope, exc: recorded.append((scope, str(exc))),
    )

    assert recorded == [("agent-comments:list-issues:ghproj", "list boom")]


def test_run_comment_monitor_cycle_skips_project_when_polling_gate_denies():
    listed: list[tuple[str, str]] = []
    cleared: list[str] = []
    recorded: list[tuple[str, str]] = []
    project_key = "project-alpha"

    run_comment_monitor_cycle(
        logger=MagicMock(),
        iter_projects=lambda: [(project_key, {})],
        get_project_platform=lambda _p: "github",
        get_repo=lambda _p: f"acme/{project_key}",
        list_workflow_issue_numbers=lambda project, repo: listed.append((project, repo)) or [1],
        get_bot_comments=lambda *_args: [],
        notify_agent_needs_input=lambda *args, **kwargs: True,
        notified_comments=set(),
        clear_polling_failures=lambda scope: cleared.append(scope),
        record_polling_failure=lambda scope, exc: recorded.append((scope, str(exc))),
        should_poll_project=lambda _project: False,
    )

    assert listed == []
    assert recorded == []
    assert f"agent-comments:list-issues:{project_key}" in cleared
