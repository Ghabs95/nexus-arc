from __future__ import annotations

from dataclasses import dataclass

from nexus.core.config import NEXUS_INBOX_BACKEND, NEXUS_STORAGE_BACKEND, NEXUS_WORKFLOW_BACKEND


@dataclass(frozen=True)
class StorageCapabilities:
    storage_backend: str
    workflow_backend: str
    inbox_backend: str
    local_task_files: bool
    local_completions: bool
    local_workflow_files: bool


def _norm_backend(value: str | None, default: str = "filesystem") -> str:
    candidate = str(value or "").strip().lower()
    return candidate or default


def build_storage_capabilities(
    *,
    storage_backend: str | None,
    workflow_backend: str | None,
    inbox_backend: str | None,
) -> StorageCapabilities:
    """Build backend capability flags from configured backend names."""
    storage = _norm_backend(storage_backend)
    workflow = _norm_backend(workflow_backend, default=storage)
    inbox = _norm_backend(inbox_backend, default=storage)
    return StorageCapabilities(
        storage_backend=storage,
        workflow_backend=workflow,
        inbox_backend=inbox,
        # Task/completion local files track the storage backend behavior today.
        local_task_files=(storage == "filesystem"),
        local_completions=(storage == "filesystem"),
        # Workflow JSON files depend on the workflow backend, not host-state storage.
        local_workflow_files=(workflow == "filesystem"),
    )


def get_storage_capabilities() -> StorageCapabilities:
    """Return centralized storage/workflow/inbox capability flags."""
    return build_storage_capabilities(
        storage_backend=NEXUS_STORAGE_BACKEND,
        workflow_backend=NEXUS_WORKFLOW_BACKEND,
        inbox_backend=NEXUS_INBOX_BACKEND,
    )
