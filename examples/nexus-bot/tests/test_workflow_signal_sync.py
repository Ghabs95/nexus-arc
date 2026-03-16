from nexus.core.workflow_runtime.workflow_signal_sync import (
    extract_structured_completion_signals,
    normalize_agent_reference,
)


def test_extract_structured_completion_signals_includes_nexus_automated_comments():
    comments = [
        {
            "id": "c-113",
            "createdAt": "2026-03-09T00:10:00Z",
            "body": (
                "## Verify Change Complete — reviewer\n\n"
                "**Step ID:** `verify_change`\n"
                "**Step Num:** 2\n\n"
                "Ready for **@Deployer**\n\n"
                "_Automated comment from Nexus._"
            ),
        }
    ]

    signals = extract_structured_completion_signals(comments)

    assert len(signals) == 1
    assert signals[0]["completed_agent"] == "reviewer"
    assert signals[0]["next_agent"] == "deployer"
    assert signals[0]["step_id"] == "verify_change"
    assert signals[0]["step_num"] == "2"


def test_extract_structured_completion_signals_accepts_hyphen_and_backticks():
    comments = [
        {
            "id": "c-114",
            "createdAt": "2026-03-09T00:15:00Z",
            "body": (
                "## Verify Change Complete - `reviewer`\n\n"
                "**Step ID:** `verify_change`\n"
                "**Step Num:** 2\n\n"
                "Ready for **@Deployer**"
            ),
        }
    ]

    signals = extract_structured_completion_signals(comments)

    assert len(signals) == 1
    assert signals[0]["completed_agent"] == "reviewer"
    assert signals[0]["next_agent"] == "deployer"
    assert signals[0]["step_id"] == "verify_change"
    assert signals[0]["step_num"] == "2"


def test_extract_structured_completion_signals_accepts_terminal_comment_without_ready():
    comments = [
        {
            "id": "c-115",
            "createdAt": "2026-03-09T00:20:00Z",
            "body": (
                "## Emergency Deploy Completed — writer\n\n"
                "### SOP Checklist\n\n"
                "- [x] 1. **Intake & Triage** — `triage`\n"
                "- [x] 2. **Root Cause Analysis** — `debug`\n"
                "- [x] 3. **Document & Close** — `writer`\n"
            ),
        }
    ]

    signals = extract_structured_completion_signals(comments)

    assert len(signals) == 1
    assert signals[0]["completed_agent"] == "writer"
    assert signals[0]["next_agent"] == "none"
    assert signals[0]["step_id"] == "document_close"
    assert signals[0]["step_num"] == "3"


def test_normalize_agent_reference_rejects_instruction_placeholder():
    assert (
        normalize_agent_reference(
            "<agent_type from workflow steps — NOT the step id or display name>"
        )
        == ""
    )
