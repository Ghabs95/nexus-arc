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


def test_structured_completion_from_role_author_chains_next_agent():
    processed = set()
    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 7,
            "comment_body": (
                "## 🔍 Vision & Scope Complete — ceo\n\n"
                "**Step ID:** `new_feature_workflow__vision`\n"
                "**Step Num:** 3\n\n"
                "### SOP Checklist\n\n"
                "- [x] 3. **Vision & Scope** — `ceo` : Feature brief: WHAT and WHY\n"
                "- [ ] 4. **Technical Feasibility** — `cto` : Feasibility assessment, HOW and WHEN\n\n"
                "Ready for **@Cto**"
            ),
            "issue_number": "102",
            "comment_author": "ceo",
            "issue": {"user": {"login": "Ghabs95"}},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (4444, "codex"),
        check_and_notify_pr=lambda issue, project: None,
    )

    assert result["status"] == "agent_launched"
    assert result["next_agent"] == "cto"
    assert "comment_7" in processed


def test_structured_completion_author_mismatch_is_ignored():
    processed = set()
    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 8,
            "comment_body": (
                "## 🔍 Vision & Scope Complete — ceo\n\n"
                "**Step ID:** `new_feature_workflow__vision`\n"
                "**Step Num:** 3\n\n"
                "Ready for **@Cto**"
            ),
            "issue_number": "103",
            "comment_author": "random-user",
            "issue": {"user": {"login": "someone-else"}},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (5555, "codex"),
        check_and_notify_pr=lambda issue, project: None,
    )

    assert result["status"] == "ignored"
    assert "not from supported AI agent" in result["reason"]


def test_structured_cto_completion_with_long_checklist_chains_architect():
    processed = set()
    result = handle_issue_comment_event(
        event={
            "action": "created",
            "comment_id": 9,
            "comment_body": (
                "## 🔍 Technical Feasibility Complete — cto\n\n"
                "**Severity:** High\n"
                "**Target Sub-Repo:** `prj-be`, `prj-app`\n"
                "**Workflow:** new_feature\n\n"
                "**Step ID:** `new_feature_workflow__technical_feasibility`\n"
                "**Step Num:** 4\n\n"
                "### SOP Checklist\n\n"
                "- [x] 1. **Triage & Routing** — `triage` : Analyze the incoming request\n"
                "- [x] 3. **Vision & Scope** — `ceo` : Feature brief: WHAT and WHY\n"
                "- [x] 4. **Technical Feasibility** — `cto` : Feasibility assessment\n"
                "- [ ] 5. **Architecture Design** — `architect` : ADR and data flow\n"
                "- [ ] 6. **UX Design** — `designer` : Wireframes\n"
                "- [ ] 7. **Implementation** — `developer` : Code, unit tests\n"
                "- [ ] 8. **Quality Gate** — `reviewer` : Regression check\n"
                "- [ ] 9. **Compliance Gate** — `compliance` : Privacy impact\n\n"
                "Ready for **@Architect**"
            ),
            "issue_number": "104",
            "comment_author": "cto",
            "issue": {"user": {"login": "Ghabs95"}},
        },
        logger=MagicMock(),
        policy=_Policy(),
        processed_events=processed,
        launch_next_agent=lambda **kwargs: (7777, "codex"),
        check_and_notify_pr=lambda issue, project: None,
    )

    assert result["status"] == "agent_launched"
    assert result["next_agent"] == "architect"
    assert "comment_9" in processed
