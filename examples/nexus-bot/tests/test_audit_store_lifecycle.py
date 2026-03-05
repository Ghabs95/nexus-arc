"""Lifecycle tests for core audit store singleton helpers."""

from nexus.core.audit_store import AuditStore


def test_audit_store_configure_and_reset_changes_singleton(tmp_path):
    first_dir = tmp_path / "audit-a"
    second_dir = tmp_path / "audit-b"

    AuditStore.configure(storage_dir=str(first_dir), reset=True)
    first_store = AuditStore._get_core_store()

    AuditStore.configure(storage_dir=str(second_dir), reset=True)
    second_store = AuditStore._get_core_store()

    assert first_store is not second_store

    AuditStore.reset()
