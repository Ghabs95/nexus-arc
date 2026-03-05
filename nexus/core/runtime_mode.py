def is_postgres_backend(storage_backend: str | None) -> bool:
    return str(storage_backend or "").strip().lower() == "postgres"


def is_issue_process_running(issue_number: str, *, cache_key: str) -> bool:
    from nexus.core.orchestration.plugin_runtime import get_runtime_ops_plugin

    runtime_ops = get_runtime_ops_plugin(cache_key=cache_key)
    if runtime_ops is None or not hasattr(runtime_ops, "is_issue_process_running"):
        raise RuntimeError("runtime ops plugin is unavailable")
    return bool(runtime_ops.is_issue_process_running(str(issue_number)))
