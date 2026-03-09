from nexus.core.workflow_runtime.workflow_signal_sync import extract_structured_completion_signals


def test_extract_structured_completion_signals_includes_nexus_automated_comments():
    comments = [
        {
            "id": "c-113",
            "createdAt": "2026-03-09T00:10:00Z",
            "body": (
                "## Verify Change Complete — reviewer\n\n"
                "Ready for **@Deployer**\n\n"
                "_Automated comment from Nexus._"
            ),
        }
    ]

    signals = extract_structured_completion_signals(comments)

    assert len(signals) == 1
    assert signals[0]["completed_agent"] == "reviewer"
    assert signals[0]["next_agent"] == "deployer"


def test_extract_structured_completion_signals_accepts_hyphen_and_backticks():
    comments = [
        {
            "id": "c-114",
            "createdAt": "2026-03-09T00:15:00Z",
            "body": (
                "## Verify Change Complete - `reviewer`\n\n"
                "Ready for **@Deployer**"
            ),
        }
    ]

    signals = extract_structured_completion_signals(comments)

    assert len(signals) == 1
    assert signals[0]["completed_agent"] == "reviewer"
    assert signals[0]["next_agent"] == "deployer"
