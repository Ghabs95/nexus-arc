from types import SimpleNamespace

from nexus.core.workflow_runtime import workflow_control_service as svc


def test_prepare_continue_context_stops_on_human_handoff(tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "NEXUS_STORAGE_BACKEND", "filesystem", raising=False)

    completion_file = tmp_path / "completion_summary_119.json"
    completion_file.write_text("{}", encoding="utf-8")

    completion = SimpleNamespace(
        issue_number="119",
        file_path=str(completion_file),
        summary=SimpleNamespace(
            is_workflow_done=False,
            agent_type="reviewer",
            next_agent="human",
        ),
    )

    ctx = svc.prepare_continue_context(
        issue_num="119",
        project_key="nexus",
        rest_tokens=[],
        base_dir=str(tmp_path),
        project_config={"nexus": {"agents_dir": "agents", "workspace": "."}},
        default_repo="Ghabs95/nexus-arc",
        find_task_file_by_issue=lambda _n: None,
        get_issue_details=lambda _n, _repo=None: {"state": "open", "title": "x", "body": "y"},
        resolve_project_config_from_task=lambda _p: (
            "nexus",
            {"agents_dir": "agents", "workspace": "."},
        ),
        get_runtime_ops_plugin=lambda **_k: SimpleNamespace(
            find_agent_pid_for_issue=lambda _n: None
        ),
        scan_for_completions=lambda _base: [completion],
        normalize_agent_reference=lambda ref: str(ref or "").strip().lower() or None,
        get_expected_running_agent_from_workflow=lambda _n: "human",
        get_sop_tier_from_issue=lambda _n, _p: None,
        get_sop_tier=lambda _t: ("full", None, None),
    )

    assert ctx["status"] == "awaiting_human"
    assert ctx["resumed_from"] == "reviewer"
    assert "waiting for human action" in ctx["message"]
