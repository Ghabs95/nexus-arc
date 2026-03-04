from unittest.mock import MagicMock, patch

from nexus.core.report_scheduler import ReportScheduler


def test_tracked_issues_status_normalizes_legacy_entries(monkeypatch):
    # Patch get_user_manager before constructing ReportScheduler
    with patch("nexus.core.report_scheduler.get_user_manager", return_value=MagicMock()):
        scheduler = ReportScheduler()

    monkeypatch.setattr(
        scheduler.state_manager,
        "load_tracked_issues",
        lambda: {
            "1": {
                "added_at": "2026-02-18T02:15:53.142355",
                "last_seen_state": None,
                "last_seen_labels": [],
            },
            "2": {"status": "closed", "project": "sampleco"},
        },
    )

    status = scheduler._get_tracked_issues_status()

    assert status["total_issues"] == 2
    assert status["status_counts"]["active"] == 1
    assert status["status_counts"]["closed"] == 1
