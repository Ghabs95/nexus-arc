from collections.abc import Callable

from runtime.nexus_agent_runtime import NexusAgentRuntime

from nexus.core.completion_store import CompletionStore
from nexus.core.process_orchestrator import ProcessOrchestrator

_process_orchestrator: ProcessOrchestrator | None = None
_completion_store: CompletionStore | None = None


def get_process_orchestrator(
    *,
    finalize_fn,
    resolve_project: Callable[[str], str | None],
    resolve_repo,
    complete_step_fn,
    nexus_dir: str,
) -> ProcessOrchestrator:
    global _process_orchestrator
    if _process_orchestrator is not None:
        return _process_orchestrator

    runtime = NexusAgentRuntime(
        finalize_fn=finalize_fn,
        resolve_project=resolve_project,
        resolve_repo=resolve_repo,
    )
    _process_orchestrator = ProcessOrchestrator(
        runtime=runtime,
        complete_step_fn=complete_step_fn,
        nexus_dir=nexus_dir,
    )
    return _process_orchestrator


def get_completion_store(
    *,
    backend: str,
    storage,
    base_dir: str,
    nexus_dir: str,
) -> CompletionStore:
    global _completion_store
    if _completion_store is not None:
        return _completion_store

    _completion_store = CompletionStore(
        backend=backend,
        storage=storage,
        base_dir=base_dir,
        nexus_dir=nexus_dir,
    )
    return _completion_store
