import json
from unittest.mock import MagicMock

from nexus.core.startup_recovery import reconcile_completion_signals_on_startup


def test_startup_recovery_returns_when_no_mappings(tmp_path):
    reconcile_completion_signals_on_startup(
        logger=MagicMock(),
        emit_alert=MagicMock(),
        get_workflow_state_mappings=lambda: {},
        nexus_core_storage_dir=str(tmp_path),
        normalize_agent_reference=lambda s: s,
        extract_repo_from_issue_url=lambda _u: "",
        read_latest_local_completion=lambda _i: None,
        read_latest_structured_comment=lambda *_args: None,
        is_terminal_agent_reference=lambda _a: False,
        complete_step_for_issue=lambda *args, **kwargs: None,
    )


def test_startup_recovery_emits_drift_alert(tmp_path):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "wf-1.json").write_text(
        json.dumps(
            {
                "state": "RUNNING",
                "steps": [
                    {"status": "RUNNING", "agent": {"name": "developer"}},
                ],
                "metadata": {
                    "issue_url": "https://github.com/acme/repo/issues/42",
                    "project_name": "proj-a",
                },
            }
        )
    )
    alerts = []

    reconcile_completion_signals_on_startup(
        logger=MagicMock(),
        emit_alert=lambda msg, **kwargs: alerts.append((msg, kwargs)),
        get_workflow_state_mappings=lambda: {"42": "wf-1"},
        nexus_core_storage_dir=str(tmp_path),
        normalize_agent_reference=lambda s: str(s or ""),
        extract_repo_from_issue_url=lambda _u: "acme/repo",
        read_latest_local_completion=lambda _i: {"next_agent": "triage"},
        read_latest_structured_comment=lambda *_args: {"next_agent": "designer"},
        is_terminal_agent_reference=lambda _a: False,
        complete_step_for_issue=lambda *args, **kwargs: None,
    )

    assert len(alerts) == 1
    assert "Startup routing drift detected for issue #42" in alerts[0][0]


def test_startup_recovery_auto_reconciles_from_local_signal(tmp_path):
    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir()
    (workflows_dir / "wf-2.json").write_text(
        json.dumps(
            {
                "state": "RUNNING",
                "steps": [
                    {"status": "RUNNING", "agent": {"name": "developer"}},
                ],
                "metadata": {
                    "issue_url": "https://github.com/acme/repo/issues/42",
                    "project_name": "proj-a",
                },
            }
        )
    )

    alerts = []
    calls = []

    async def _complete_step(issue_number, completed_agent_type, outputs, event_id=None):
        calls.append(
            {
                "issue_number": issue_number,
                "completed_agent_type": completed_agent_type,
                "outputs": outputs,
                "event_id": event_id,
            }
        )
        return {"ok": True}

    reconcile_completion_signals_on_startup(
        logger=MagicMock(),
        emit_alert=lambda msg, **kwargs: alerts.append((msg, kwargs)),
        get_workflow_state_mappings=lambda: {"42": "wf-2"},
        nexus_core_storage_dir=str(tmp_path),
        normalize_agent_reference=lambda s: str(s or ""),
        extract_repo_from_issue_url=lambda _u: "acme/repo",
        read_latest_local_completion=lambda _i: {
            "agent_type": "developer",
            "next_agent": "reviewer",
        },
        read_latest_structured_comment=lambda *_args: None,
        is_terminal_agent_reference=lambda _a: False,
        complete_step_for_issue=_complete_step,
    )

    assert alerts == []
    assert len(calls) == 1
    assert calls[0]["issue_number"] == "42"
    assert calls[0]["completed_agent_type"] == "developer"
    assert calls[0]["outputs"]["next_agent"] == "reviewer"
