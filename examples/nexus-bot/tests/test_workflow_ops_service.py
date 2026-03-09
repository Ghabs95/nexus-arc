import json
import sys
import types


def test_build_workflow_snapshot_allows_workflow_file_when_local_task_files_disabled(
    monkeypatch, tmp_path
):
    from nexus.core.workflow_runtime import workflow_ops_service as svc

    workflows_dir = tmp_path / "workflows"
    workflows_dir.mkdir(parents=True)
    (workflows_dir / "wf-1.json").write_text(
        json.dumps(
            {
                "state": "running",
                "current_step": 1,
                "steps": [
                    {
                        "step_num": 1,
                        "name": "develop",
                        "status": "pending",
                        "agent": {"name": "developer"},
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    class _WFState:
        def get_workflow_id(self, issue_num):  # noqa: ANN001
            assert issue_num == "83"
            return "wf-1"

    class _IssuePlugin:
        def get_issue(self, issue_num, fields):  # noqa: ANN001
            assert issue_num == "83"
            assert "comments" in fields
            return {"comments": []}

    monkeypatch.setattr(svc, "NEXUS_CORE_STORAGE_DIR", str(tmp_path))
    monkeypatch.setattr(svc, "_get_wf_state", lambda: _WFState())
    monkeypatch.setattr(svc, "get_runtime_ops_plugin", lambda cache_key=None: None)
    monkeypatch.setattr(svc, "_latest_processor_signal_for_issue", lambda issue_num: {})

    snapshot = svc.build_workflow_snapshot(
        issue_num="83",
        repo="owner/repo",
        get_issue_plugin=lambda repo: _IssuePlugin(),
        expected_running_agent="",
        find_task_file_by_issue=lambda issue_num: "/tmp/task-83.md",
        read_latest_local_completion=lambda issue_num: None,
        extract_structured_completion_signals=lambda comments: [],
        local_task_files_enabled=False,
        local_workflow_files_enabled=True,
    )

    assert snapshot["workflow_id"] == "wf-1"
    assert snapshot["workflow_state"] == "running"
    assert snapshot["current_agent"] == "developer"
    assert snapshot["workflow_file"].endswith("/workflows/wf-1.json")
    assert snapshot["task_file"] is None


def test_build_workflow_snapshot_uses_live_workflow_status_when_file_unavailable(monkeypatch):
    from nexus.core.workflow_runtime import workflow_ops_service as svc

    class _WFState:
        def get_workflow_id(self, issue_num):  # noqa: ANN001
            assert issue_num == "106"
            return "wf-missing"

    class _IssuePlugin:
        def get_issue(self, issue_num, fields):  # noqa: ANN001
            assert issue_num == "106"
            assert "comments" in fields
            return {"comments": []}

    monkeypatch.setattr(svc, "_get_wf_state", lambda: _WFState())
    monkeypatch.setattr(svc, "get_runtime_ops_plugin", lambda cache_key=None: None)
    monkeypatch.setattr(svc, "_latest_processor_signal_for_issue", lambda issue_num: {})

    snapshot = svc.build_workflow_snapshot(
        issue_num="106",
        repo="owner/repo",
        get_issue_plugin=lambda repo: _IssuePlugin(),
        workflow_status={
            "state": "running",
            "current_step": 3,
            "total_steps": 8,
            "current_agent": "developer",
        },
        expected_running_agent="developer",
        find_task_file_by_issue=lambda issue_num: None,
        read_latest_local_completion=lambda issue_num: {
            "agent_type": "designer",
            "next_agent": "developer",
        },
        extract_structured_completion_signals=lambda comments: [],
        local_task_files_enabled=False,
        local_workflow_files_enabled=False,
    )

    assert snapshot["workflow_state"] == "running"
    assert snapshot["current_step"] == "3/8"
    assert snapshot["current_agent"] == "developer"
    assert snapshot["workflow_file"] is None


def test_build_workflow_snapshot_flags_missing_workflow_state_when_signals_exist(monkeypatch):
    from nexus.core.workflow_runtime import workflow_ops_service as svc

    class _WFState:
        def get_workflow_id(self, issue_num):  # noqa: ANN001
            assert issue_num == "106"
            return "wf-missing"

    class _IssuePlugin:
        def get_issue(self, issue_num, fields):  # noqa: ANN001
            assert issue_num == "106"
            return {"comments": []}

    monkeypatch.setattr(svc, "_get_wf_state", lambda: _WFState())
    monkeypatch.setattr(svc, "get_runtime_ops_plugin", lambda cache_key=None: None)
    monkeypatch.setattr(svc, "_latest_processor_signal_for_issue", lambda issue_num: {})

    snapshot = svc.build_workflow_snapshot(
        issue_num="106",
        repo="owner/repo",
        get_issue_plugin=lambda repo: _IssuePlugin(),
        expected_running_agent="developer",
        find_task_file_by_issue=lambda issue_num: None,
        read_latest_local_completion=lambda issue_num: {
            "agent_type": "designer",
            "next_agent": "developer",
        },
        extract_structured_completion_signals=lambda comments: [],
        local_task_files_enabled=False,
        local_workflow_files_enabled=False,
    )

    assert snapshot["workflow_state"] == "unknown"
    assert "workflow_state_missing" in snapshot["drift_flags"]


async def test_reconcile_issue_from_signals_uses_local_completion_writer_when_enabled(monkeypatch):
    from nexus.core.workflow_runtime import workflow_ops_service as svc
    from nexus.core.storage.capabilities import StorageCapabilities

    class _IssuePlugin:
        def get_issue(self, issue_num, fields):  # noqa: ANN001
            return {"comments": [{"id": "c1"}], "title": "Issue"}

    class _WorkflowPlugin:
        def __init__(self):
            self.completed: list[tuple[str, str, dict]] = []
            self.resumed = False
            self.paused = False

        def get_workflow_status(self, issue_num):  # noqa: ANN001
            if not self.completed:
                return {"state": "paused"}
            return {
                "state": "running",
                "current_agent": "developer",
                "current_step": 2,
                "total_steps": 5,
            }

        def resume_workflow(self, issue_num):  # noqa: ANN001
            self.resumed = True

        def complete_step_for_issue(
            self, issue_number, completed_agent_type, outputs
        ):  # noqa: ANN001
            self.completed.append((issue_number, completed_agent_type, outputs))
            return {"ok": True}

        def pause_workflow(self, issue_num, reason):  # noqa: ANN001
            self.paused = True

    wf_plugin = _WorkflowPlugin()
    writes: list[tuple[str, str, dict[str, str]]] = []

    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: StorageCapabilities(
            storage_backend="filesystem",
            workflow_backend="filesystem",
            inbox_backend="filesystem",
            local_task_files=True,
            local_completions=True,
            local_workflow_files=True,
        ),
    )
    monkeypatch.setattr(svc, "get_workflow_state_plugin", lambda **kwargs: wf_plugin)

    out = await svc.reconcile_issue_from_signals(
        issue_num="83",
        project_key="nexus",
        repo="owner/repo",
        get_issue_plugin=lambda repo: _IssuePlugin(),
        extract_structured_completion_signals=lambda comments: [
            {"completed_agent": "triage", "next_agent": "developer", "comment_id": "c1"}
        ],
        workflow_state_plugin_kwargs={},
        write_local_completion_from_signal=lambda project_key, issue_num, signal: (
            writes.append((project_key, issue_num, signal)),
            "/tmp/completion_summary_83.json",
        )[1],
    )

    assert out["ok"] is True
    assert out["signals_applied"] == 1
    assert out["completion_file"] == "completion_summary_83.json"
    assert wf_plugin.resumed is True
    assert wf_plugin.paused is True
    assert len(wf_plugin.completed) == 1
    assert writes and writes[0][0] == "nexus"


async def test_reconcile_issue_from_signals_uses_storage_completion_when_local_disabled(
    monkeypatch,
):
    from nexus.core.workflow_runtime import workflow_ops_service as svc
    from nexus.core.storage.capabilities import StorageCapabilities

    class _IssuePlugin:
        def get_issue(self, issue_num, fields):  # noqa: ANN001
            return {"comments": [{"id": "c2"}], "title": "Issue"}

    class _WorkflowPlugin:
        def get_workflow_status(self, issue_num):  # noqa: ANN001
            return {"state": "running", "current_agent": "qa", "current_step": 3, "total_steps": 7}

        def resume_workflow(self, issue_num):  # noqa: ANN001
            return None

        def complete_step_for_issue(
            self, issue_number, completed_agent_type, outputs
        ):  # noqa: ANN001
            return {"ok": True}

        def pause_workflow(self, issue_num, reason):  # noqa: ANN001
            return None

    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: StorageCapabilities(
            storage_backend="postgres",
            workflow_backend="filesystem",
            inbox_backend="postgres",
            local_task_files=False,
            local_completions=False,
            local_workflow_files=True,
        ),
    )
    monkeypatch.setattr(svc, "get_workflow_state_plugin", lambda **kwargs: _WorkflowPlugin())
    monkeypatch.setattr(
        svc,
        "_save_completion_to_storage",
        lambda issue_num, signal: __import__("asyncio").sleep(0, result="dedup-83"),
    )

    out = await svc.reconcile_issue_from_signals(
        issue_num="83",
        project_key="nexus",
        repo="owner/repo",
        get_issue_plugin=lambda repo: _IssuePlugin(),
        extract_structured_completion_signals=lambda comments: [
            {"completed_agent": "developer", "next_agent": "qa", "comment_id": "c2"}
        ],
        workflow_state_plugin_kwargs={},
        write_local_completion_from_signal=lambda *args: ().throw(
            AssertionError("should not write local")
        ),
    )

    assert out["ok"] is True
    assert out["completion_file"] == "dedup-83"


async def test_reconcile_issue_from_signals_falls_back_to_db_completion_when_no_comments(
    monkeypatch,
):
    from nexus.core.workflow_runtime import workflow_ops_service as svc

    class _IssuePlugin:
        def get_issue(self, issue_num, fields):  # noqa: ANN001
            return {"comments": [], "title": "Issue"}

    class _WorkflowPlugin:
        def get_workflow_status(self, issue_num):  # noqa: ANN001
            return {"state": "running", "current_agent": "developer", "current_step": 3, "total_steps": 7}

        def complete_step_for_issue(
            self, issue_number, completed_agent_type, outputs
        ):  # noqa: ANN001
            assert issue_number == "113"
            assert completed_agent_type == "reviewer"
            assert outputs.get("next_agent") == "deployer"
            return {"ok": True}

        def resume_workflow(self, issue_num):  # noqa: ANN001
            return None

        def pause_workflow(self, issue_num, reason):  # noqa: ANN001
            return None

    class _Storage:
        async def list_completions(self, issue_number):  # noqa: ANN001
            assert issue_number == "113"
            return [
                {
                    "agent_type": "reviewer",
                    "next_agent": "deployer",
                    "status": "complete",
                }
            ]

        async def save_completion(self, issue_number, agent_type, payload):  # noqa: ANN001
            return "/tmp/completion_113"

    monkeypatch.setattr(svc, "get_storage_backend", lambda: _Storage())
    monkeypatch.setattr(svc, "get_workflow_state_plugin", lambda **kwargs: _WorkflowPlugin())

    out = await svc.reconcile_issue_from_signals(
        issue_num="113",
        project_key="nexus",
        repo="owner/repo",
        get_issue_plugin=lambda repo: _IssuePlugin(),
        extract_structured_completion_signals=lambda comments: [],
        workflow_state_plugin_kwargs={},
        write_local_completion_from_signal=lambda *_a, **_k: "/tmp/completion_summary_113.json",
    )

    assert out["ok"] is True
    assert out["signals_applied"] == 1


async def test_fetch_workflow_state_snapshot_uses_capabilities_for_mixed_mode(monkeypatch):
    from nexus.core.workflow_runtime import workflow_ops_service as svc
    from nexus.core.storage.capabilities import StorageCapabilities

    monkeypatch.setattr(
        svc,
        "get_storage_capabilities",
        lambda: StorageCapabilities(
            storage_backend="postgres",
            workflow_backend="filesystem",
            inbox_backend="postgres",
            local_task_files=False,
            local_completions=False,
            local_workflow_files=True,
        ),
    )

    def _noop_reconcile(**kwargs):  # noqa: ANN003
        return {"ok": True}

    monkeypatch.setattr(svc, "reconcile_issue_from_signals", _noop_reconcile)
    monkeypatch.setattr(
        svc,
        "_latest_completion_from_storage",
        lambda issue_num: __import__("asyncio").sleep(
            0, result={"agent_type": "triage", "next_agent": "developer"}
        ),
    )

    fake_runtime_mod = types.ModuleType("nexus.core.runtime.nexus_agent_runtime")
    fake_runtime_mod.get_expected_running_agent_from_workflow = lambda issue_num: "developer"
    monkeypatch.setitem(sys.modules, "nexus.core.runtime.nexus_agent_runtime", fake_runtime_mod)

    captured: dict[str, object] = {}

    def _build_snapshot(**kwargs):  # noqa: ANN003
        captured.update(kwargs)
        return {"ok": True}

    out = await svc.fetch_workflow_state_snapshot(
        issue_num="83",
        project_key="nexus",
        repo="owner/repo",
        get_issue_plugin=lambda repo: object(),
        extract_structured_completion_signals=lambda comments: [],
        workflow_state_plugin_kwargs={},
        write_local_completion_from_signal=lambda *args: "/tmp/unused.json",
        build_workflow_snapshot=_build_snapshot,
        read_latest_local_completion=lambda issue_num: {"agent_type": "local", "next_agent": "x"},
    )

    assert out == {"ok": True, "snapshot": {"ok": True, "completion_source": "postgres"}}
    assert captured["local_task_files_enabled"] is False
    assert captured["local_workflow_files_enabled"] is True
    assert captured["expected_running_agent"] == "developer"
    assert captured["workflow_status"] is None
    find_task_file = cast(Callable[[str], Any], captured["find_task_file_by_issue"])
    read_latest_completion = cast(
        Callable[[str], dict[str, Any] | None],
        captured["read_latest_local_completion"],
    )
    assert callable(find_task_file)
    assert callable(read_latest_completion)
    assert find_task_file("83") is None
    assert read_latest_completion("83") == {
        "agent_type": "triage",
        "next_agent": "developer",
    }


from collections.abc import Callable
from typing import Any, cast
