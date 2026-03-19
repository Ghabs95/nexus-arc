from unittest.mock import MagicMock

from nexus.core.comment_monitor_service import run_comment_monitor_cycle


def test_run_comment_monitor_cycle_skips_token_required_list_failure():
    cleared: list[str] = []
    recorded: list[tuple[str, str]] = []

    run_comment_monitor_cycle(
        logger=MagicMock(),
        iter_projects=lambda: [("wallible", {})],
        get_project_platform=lambda _p: "gitlab",
        get_repo=lambda _p: "acme/wallible",
        list_workflow_issue_numbers=lambda *_args: (_ for _ in ()).throw(
            ValueError(
                "GitLab token required for project 'wallible'. "
                "Provide requester-scoped token_override."
            )
        ),
        get_bot_comments=lambda *_args: [],
        notify_agent_needs_input=lambda *args, **kwargs: True,
        notified_comments=set(),
        clear_polling_failures=lambda scope: cleared.append(scope),
        record_polling_failure=lambda scope, exc: recorded.append((scope, str(exc))),
    )

    assert recorded == []
    assert "agent-comments:list-issues:wallible" in cleared


def test_run_comment_monitor_cycle_skips_not_found_list_failure():
    cleared: list[str] = []
    recorded: list[tuple[str, str]] = []

    run_comment_monitor_cycle(
        logger=MagicMock(),
        iter_projects=lambda: [("biome", {})],
        get_project_platform=lambda _p: "github",
        get_repo=lambda _p: "acme/biome",
        list_workflow_issue_numbers=lambda *_args: (_ for _ in ()).throw(
            RuntimeError("HTTP Error 404: Not Found")
        ),
        get_bot_comments=lambda *_args: [],
        notify_agent_needs_input=lambda *args, **kwargs: True,
        notified_comments=set(),
        clear_polling_failures=lambda scope: cleared.append(scope),
        record_polling_failure=lambda scope, exc: recorded.append((scope, str(exc))),
    )

    assert recorded == []
    assert "agent-comments:list-issues:biome" in cleared
