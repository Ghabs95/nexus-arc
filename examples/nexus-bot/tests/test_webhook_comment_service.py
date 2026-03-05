from unittest.mock import MagicMock

from nexus.core.webhook.comment_service import handle_issue_comment_event


class _Policy:
    def determine_project_from_issue(self, issue):
        return "proj-a"


def test_comment_service_ignores_non_created():
    result = handle_issue_comment_event(
        event={
            "action": "edited",
            "comment_id": 1,
            "issue_number": "42",
            "comment_author": "copilot",
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=set(),
        launch_next_agent=lambda **kwargs: (None, None),
        check_and_notify_pr=lambda issue, project: None,
    )
    assert result["status"] == "ignored"


def test_comment_service_marks_workflow_completed_and_dedups():
    processed = set()
    called = []
    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 2,
            "comment_body": "Workflow complete. All steps completed.",
            "issue_number": "42",
            "comment_author": "copilot",
            "issue": {},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (None, None),
        check_and_notify_pr=lambda issue, project: called.append((issue, project)),
    )
    assert result["status"] == "workflow_completed"
    assert ("42", "proj-a") in called
    assert "comment_2" in processed


def test_comment_service_chains_next_agent():
    processed = set()
    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 3,
            "comment_body": "Ready for @reviewer",
            "issue_number": "77",
            "comment_author": "copilot",
            "issue": {},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (1234, "copilot"),
        check_and_notify_pr=lambda issue, project: None,
    )
    assert result["status"] == "agent_launched"
    assert result["next_agent"] == "reviewer"
    assert "comment_3" in processed


def test_manual_override_resets_workflow_before_launch():
    processed = set()
    reset_calls = []
    launch_calls = []

    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 4,
            "comment_body": "Please continue with @Designer",
            "issue_number": "88",
            "comment_author": "Ghabs95",
            "issue": {"user": {"login": "Ghabs95"}},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (launch_calls.append(kwargs), (2222, "codex"))[1],
        check_and_notify_pr=lambda issue, project: None,
        reset_workflow_to_agent=lambda issue, agent: reset_calls.append((issue, agent)) or True,
    )

    assert result["status"] == "agent_launched"
    assert result["next_agent"] == "designer"
    assert reset_calls == [("88", "designer")]
    assert launch_calls and launch_calls[0]["next_agent"] == "designer"


def test_manual_override_launches_even_when_reset_fails():
    processed = set()

    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 5,
            "comment_body": "handoff @Developer",
            "issue_number": "88",
            "comment_author": "Ghabs95",
            "issue": {"user": {"login": "Ghabs95"}},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (3333, "codex"),
        check_and_notify_pr=lambda issue, project: None,
        reset_workflow_to_agent=lambda issue, agent: False,
    )

    assert result["status"] == "agent_launched"
    assert result["next_agent"] == "developer"


def test_issue_author_ready_for_comment_without_command_does_not_chain():
    processed = set()

    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 6,
            "comment_body": "Ready for @Writer",
            "issue_number": "91",
            "comment_author": "Ghabs95",
            "issue": {"user": {"login": "Ghabs95"}},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (3333, "codex"),
        check_and_notify_pr=lambda issue, project: None,
        reset_workflow_to_agent=lambda issue, agent: True,
    )

    assert result["status"] == "ignored"
    assert "manual override command not detected" in result["reason"]
    assert "comment_6" not in processed
