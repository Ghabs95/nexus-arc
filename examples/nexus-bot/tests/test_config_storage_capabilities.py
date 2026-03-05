from nexus.core.storage.capabilities import build_storage_capabilities


def test_build_storage_capabilities_splits_storage_vs_workflow_backends():
    caps = build_storage_capabilities(
        storage_backend="postgres",
        workflow_backend="filesystem",
        inbox_backend="postgres",
    )

    assert caps.storage_backend == "postgres"
    assert caps.workflow_backend == "filesystem"
    assert caps.inbox_backend == "postgres"
    assert caps.local_task_files is False
    assert caps.local_completions is False
    assert caps.local_workflow_files is True


def test_build_storage_capabilities_all_postgres_disables_local_file_capabilities():
    caps = build_storage_capabilities(
        storage_backend="postgres",
        workflow_backend="postgres",
        inbox_backend="postgres",
    )

    assert caps.local_task_files is False
    assert caps.local_completions is False
    assert caps.local_workflow_files is False
