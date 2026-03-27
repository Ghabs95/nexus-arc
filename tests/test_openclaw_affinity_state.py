"""Unit tests for nexus.core.openclaw_affinity_state.

Covers the core affinity/correlation persistence layer so that CI runs in the
top-level ``tests/`` collection path (configured as ``testpaths = ["tests"]`` in
``pyproject.toml``) exercise this framework module.
"""

import json
import os

import pytest

from nexus.core.openclaw_affinity_state import (
    OpenClawAffinityRecord,
    OpenClawAffinityStateStore,
    _infer_project_key,
    deterministic_session_key,
    resolve_affinity_binding,
    scan_and_repair_affinity_state,
)


# ---------------------------------------------------------------------------
# deterministic_session_key
# ---------------------------------------------------------------------------


def test_deterministic_session_key_with_project():
    key = deterministic_session_key("nexus-42-full", "nexus")
    assert key == "nexus:nexus:workflow:nexus-42-full"


def test_deterministic_session_key_without_project():
    key = deterministic_session_key("nexus-42-full")
    assert key == "nexus:workflow:nexus-42-full"


def test_deterministic_session_key_falls_back_to_issue():
    key = deterministic_session_key("", "nexus", issue_number="10")
    assert key == "nexus:nexus:issue:10"


def test_deterministic_session_key_empty_returns_empty():
    assert deterministic_session_key("") == ""


# ---------------------------------------------------------------------------
# _infer_project_key
# ---------------------------------------------------------------------------


def test_infer_project_key_splits_on_first_dash():
    assert _infer_project_key("nexus-50-full") == "nexus"


def test_infer_project_key_no_dash_returns_empty():
    assert _infer_project_key("singleword") == ""


def test_infer_project_key_empty_returns_empty():
    assert _infer_project_key("") == ""


# ---------------------------------------------------------------------------
# OpenClawAffinityStateStore
# ---------------------------------------------------------------------------


def test_store_load_all_returns_empty_when_file_missing(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    assert store.load_all() == {}


def test_store_upsert_persists_and_retrieves(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    record = OpenClawAffinityRecord(
        workflow_id="nexus-1-full",
        session_key="nexus:nexus:workflow:nexus-1-full",
        correlation_token="ocwf-abc",
    )
    store.upsert(record)
    loaded = store.get("nexus-1-full")
    assert loaded is not None
    assert loaded.session_key == "nexus:nexus:workflow:nexus-1-full"
    assert loaded.correlation_token == "ocwf-abc"


def test_store_upsert_raises_without_workflow_id(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    with pytest.raises(ValueError, match="workflow_id is required"):
        store.upsert(OpenClawAffinityRecord(workflow_id=""))


def test_store_upsert_creates_lock_file(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    store.upsert(OpenClawAffinityRecord(workflow_id="wf-lock-test"))
    assert (tmp_path / "openclaw" / "affinity_state.json.lock").exists()


def test_store_load_all_backs_up_corrupt_file(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    store._ensure_dir()
    store.state_file.write_text("{ not valid json !!!", encoding="utf-8")

    result = store.load_all()

    assert result == {}
    # Original file should have been renamed to a backup
    assert not store.state_file.exists()
    backups = list(store.state_dir.glob("affinity_state.json.corrupt-*"))
    assert len(backups) == 1


def test_store_save_all_and_load_all_roundtrip(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    records = {
        "wf-1": OpenClawAffinityRecord(workflow_id="wf-1", session_key="sk-1"),
        "wf-2": OpenClawAffinityRecord(workflow_id="wf-2", session_key="sk-2"),
    }
    store.save_all(records)
    loaded = store.load_all()
    assert set(loaded.keys()) == {"wf-1", "wf-2"}
    assert loaded["wf-1"].session_key == "sk-1"


# ---------------------------------------------------------------------------
# resolve_affinity_binding
# ---------------------------------------------------------------------------


def test_resolve_affinity_binding_creates_new_record(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    rec = resolve_affinity_binding(
        workflow_id="nexus-42-full",
        project_key="nexus",
        issue_number="42",
        correlation_token="ocwf-first",
        event_type="workflow_started",
        store=store,
    )
    assert rec.binding_status == "created"
    assert rec.session_key == "nexus:nexus:workflow:nexus-42-full"
    assert rec.correlation_token == "ocwf-first"
    assert rec.lifecycle_reason == "initialized_from_runtime"


def test_resolve_affinity_binding_reuses_existing_state(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    resolve_affinity_binding(
        workflow_id="nexus-42-full",
        project_key="nexus",
        issue_number="42",
        correlation_token="ocwf-first",
        store=store,
    )
    reused = resolve_affinity_binding(
        workflow_id="nexus-42-full",
        project_key="nexus",
        issue_number="42",
        event_type="workflow_progressed",
        store=store,
    )
    assert reused.session_key == "nexus:nexus:workflow:nexus-42-full"
    assert reused.correlation_token == "ocwf-first"


def test_resolve_affinity_binding_detects_drift(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    resolve_affinity_binding(workflow_id="nexus-43-full", store=store)

    drifted = resolve_affinity_binding(
        workflow_id="nexus-43-full",
        configured_session_key="telegram:chat:999",
        store=store,
    )
    assert drifted.binding_status == "drifted"
    assert drifted.lifecycle_reason == "configured_session_key_mismatch"
    assert drifted.session_key == "telegram:chat:999"
    assert drifted.history


def test_resolve_affinity_binding_repairs_missing_session_key(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    resolve_affinity_binding(workflow_id="nexus-44-full", store=store)
    records = store.load_all()
    records["nexus-44-full"].session_key = ""
    store.save_all(records)

    repaired = resolve_affinity_binding(
        workflow_id="nexus-44-full",
        store=store,
    )
    assert repaired.binding_status == "repaired"
    assert repaired.lifecycle_reason == "missing_persisted_session_key"
    assert repaired.session_key


def test_resolve_affinity_binding_raises_without_workflow_id():
    with pytest.raises(ValueError, match="workflow_id is required"):
        resolve_affinity_binding(workflow_id="")


def test_resolve_affinity_binding_persists_to_json_file(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    resolve_affinity_binding(
        workflow_id="nexus-45-full",
        project_key="nexus",
        issue_number="45",
        store=store,
    )
    payload = json.loads((tmp_path / "openclaw" / "affinity_state.json").read_text())
    assert "nexus-45-full" in payload["workflows"]


# ---------------------------------------------------------------------------
# scan_and_repair_affinity_state
# ---------------------------------------------------------------------------


def test_scan_and_repair_creates_missing_affinity_record(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    repaired = scan_and_repair_affinity_state(
        workflow_mappings={"55": "nexus-55-full"},
        store=store,
    )
    assert len(repaired) == 1
    rec = repaired[0]
    assert rec.workflow_id == "nexus-55-full"
    # project_key should be inferred from workflow_id
    assert rec.project_key == "nexus"
    assert rec.session_key == "nexus:nexus:workflow:nexus-55-full"


def test_scan_and_repair_infers_project_key_from_workflow_id(tmp_path):
    """Recovered affinity should use the same session key format as notifications."""
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    repaired = scan_and_repair_affinity_state(
        workflow_mappings={"60": "myproject-60-full"},
        store=store,
    )
    assert len(repaired) == 1
    assert repaired[0].session_key == "nexus:myproject:workflow:myproject-60-full"


def test_scan_and_repair_restores_missing_session_key(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    resolve_affinity_binding(
        workflow_id="nexus-50-full",
        project_key="nexus",
        issue_number="50",
        correlation_token="ocwf-50",
        store=store,
    )
    records = store.load_all()
    records["nexus-50-full"].session_key = ""
    store.save_all(records)

    repaired = scan_and_repair_affinity_state(
        workflow_mappings={"50": "nexus-50-full"},
        store=store,
    )
    assert len(repaired) == 1
    assert repaired[0].binding_status == "repaired"
    assert repaired[0].lifecycle_reason == "missing_persisted_session_key"
    assert repaired[0].session_key == deterministic_session_key(
        "nexus-50-full", "nexus", issue_number="50"
    )


def test_scan_and_repair_no_changes_for_healthy_records(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    resolve_affinity_binding(
        workflow_id="nexus-51-full",
        project_key="nexus",
        issue_number="51",
        store=store,
    )
    repaired = scan_and_repair_affinity_state(
        workflow_mappings={"51": "nexus-51-full"},
        store=store,
    )
    assert repaired == []


def test_scan_and_repair_skips_empty_workflow_id(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    repaired = scan_and_repair_affinity_state(
        workflow_mappings={"1": "", "2": "  "},
        store=store,
    )
    assert repaired == []


# ---------------------------------------------------------------------------
# OpenClawAffinityRecord helpers
# ---------------------------------------------------------------------------


def test_affinity_record_from_dict_handles_missing_keys():
    rec = OpenClawAffinityRecord.from_dict({})
    assert rec.workflow_id == ""
    assert rec.binding_status == "active"
    assert rec.history == []


def test_affinity_record_from_dict_handles_none():
    rec = OpenClawAffinityRecord.from_dict(None)  # type: ignore[arg-type]
    assert rec.workflow_id == ""
    assert rec.binding_status == "active"
    assert rec.history == []


def test_affinity_record_to_dict_roundtrip():
    rec = OpenClawAffinityRecord(
        workflow_id="wf-rt",
        session_key="sk-rt",
        correlation_token="ct-rt",
    )
    d = rec.to_dict()
    restored = OpenClawAffinityRecord.from_dict(d)
    assert restored.workflow_id == "wf-rt"
    assert restored.session_key == "sk-rt"
    assert restored.correlation_token == "ct-rt"
