from nexus.core.webhook.pr_service import evaluate_issue_close_for_pr_merge


def test_evaluate_issue_close_for_pr_merge_allows_completed_workflow():
    allowed, reason = evaluate_issue_close_for_pr_merge(
        {
            "state": "completed",
            "steps": [
                {"name": "merge_deploy", "status": "completed"},
                {"name": "document_close", "status": "skipped"},
            ],
        }
    )

    assert allowed is True
    assert reason == "workflow completed"


def test_evaluate_issue_close_for_pr_merge_blocks_non_completed_workflow():
    allowed, reason = evaluate_issue_close_for_pr_merge(
        {
            "state": "running",
            "steps": [
                {"name": "merge_deploy", "status": "completed"},
                {"name": "document_close", "status": "pending"},
            ],
        }
    )

    assert allowed is False
    assert reason == "workflow state is 'running'"


def test_evaluate_issue_close_for_pr_merge_blocks_incomplete_steps():
    allowed, reason = evaluate_issue_close_for_pr_merge(
        {
            "state": "completed",
            "steps": [
                {"name": "merge_deploy", "status": "completed"},
                {"name": "document_close", "status": "pending"},
            ],
        }
    )

    assert allowed is False
    assert reason == "workflow step 'document_close' is 'pending'"
