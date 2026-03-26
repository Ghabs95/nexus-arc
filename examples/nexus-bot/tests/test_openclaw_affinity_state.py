import json

from nexus.core.openclaw_affinity_state import (
    OpenClawAffinityStateStore,
    deterministic_session_key,
    resolve_affinity_binding,
    scan_and_repair_affinity_state,
)
from nexus.core.startup_recovery import reconcile_openclaw_affinity_on_startup


def test_resolve_affinity_binding_persists_and_reuses_existing_state(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)

    created = resolve_affinity_binding(
        workflow_id="nexus-42-full",
        project_key="nexus",
        issue_number="42",
        correlation_token="ocwf-first",
        event_type="workflow_started",
        store=store,
    )

    assert created.session_key == deterministic_session_key("nexus-42-full", "nexus", issue_number="42")
    assert created.binding_status == "created"
    assert created.correlation_token == "ocwf-first"

    reused = resolve_affinity_binding(
        workflow_id="nexus-42-full",
        project_key="nexus",
        issue_number="42",
        event_type="workflow_progressed",
        store=store,
    )

    assert reused.session_key == created.session_key
    assert reused.correlation_token == "ocwf-first"
    payload = json.loads((tmp_path / "openclaw" / "affinity_state.json").read_text())
    assert payload["workflows"]["nexus-42-full"]["session_key"] == created.session_key


def test_resolve_affinity_binding_marks_configured_drift(tmp_path):
    store = OpenClawAffinityStateStore(base_dir=tmp_path)
    resolve_affinity_binding(
        workflow_id="nexus-43-full",
        project_key="nexus",
        issue_number="43",
        correlation_token="ocwf-1",
        store=store,
    )

    drifted = resolve_affinity_binding(
        workflow_id="nexus-43-full",
        project_key="nexus",
        issue_number="43",
        configured_session_key="telegram:chat:abc",
        correlation_token="ocwf-2",
        event_type="workflow_progressed",
        store=store,
    )

    assert drifted.binding_status == "drifted"
    assert drifted.binding_source == "configured"
    assert drifted.lifecycle_reason == "configured_session_key_mismatch"
    assert drifted.session_key == "telegram:chat:abc"
    assert drifted.history


def test_scan_and_repair_affinity_state_restores_missing_session_key(tmp_path):
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
    assert repaired[0].session_key == deterministic_session_key("nexus-50-full", "nexus", issue_number="50")


def test_reconcile_openclaw_affinity_on_startup_emits_restored_alert(tmp_path):
    alerts = []

    reconcile_openclaw_affinity_on_startup(
        logger=type("L", (), {"debug": lambda *args, **kwargs: None})(),
        emit_alert=lambda msg, **kwargs: alerts.append((msg, kwargs)),
        get_workflow_state_mappings=lambda: {"42": "nexus-42-full"},
        nexus_core_storage_dir=str(tmp_path),
    )

    # default store uses configured core storage dir; this function should still report repair activity
    assert alerts
    assert "Restored OpenClaw affinity" in alerts[0][0]
