"""Completion monitoring helpers extracted from inbox_processor."""

from collections.abc import Callable
from typing import Any, Protocol, cast


class _CompletionStoreLike(Protocol):
    def scan(self) -> list[dict[str, Any]]: ...


class _WorkflowPolicyLike(Protocol):
    def build_transition_message(self, **kwargs: Any) -> str: ...
    def build_autochain_failed_message(self, **kwargs: Any) -> str: ...


class _ProcessOrchestratorLike(Protocol):
    def scan_and_process_completions(self, *args: Any, **kwargs: Any) -> None: ...


def run_completion_monitor_cycle(*, post_completion_comments_from_logs: Callable[[], None]) -> None:
    """Run one completion-monitor cycle."""
    post_completion_comments_from_logs()


def post_completion_comments_from_logs(
    *,
    base_dir: str,
    inbox_processor_started_at: float,
    completion_comments: dict,
    save_completion_comments: Callable[[dict], None],
    get_completion_replay_window_seconds: Callable[[], int],
    get_process_orchestrator: Callable[[], Any],
    get_workflow_policy_plugin: Callable[..., _WorkflowPolicyLike],
    get_completion_store: Callable[[], Any],
    resolve_project,
    resolve_repo,
    ingest_detected_completions: Callable[[list[Any], set[str]], None] | None = None,
) -> None:
    """Detect agent completions and auto-chain to the next workflow step."""
    orchestrator = cast(_ProcessOrchestratorLike, get_process_orchestrator())
    workflow_policy = get_workflow_policy_plugin(cache_key="workflow-policy:inbox")

    dedup = set(completion_comments.keys())
    replay_window_seconds = get_completion_replay_window_seconds()
    completion_store = cast(_CompletionStoreLike, get_completion_store())
    detected_completions = completion_store.scan()
    if ingest_detected_completions is not None:
        ingest_detected_completions(detected_completions, dedup)
    orchestrator.scan_and_process_completions(
        base_dir,
        dedup,
        detected_completions=detected_completions,
        resolve_project=resolve_project,
        resolve_repo=resolve_repo,
        build_transition_message=lambda **kw: workflow_policy.build_transition_message(**kw),
        build_autochain_failed_message=lambda **kw: workflow_policy.build_autochain_failed_message(
            **kw
        ),
        stale_completion_seconds=(replay_window_seconds if replay_window_seconds > 0 else None),
        stale_reference_ts=inbox_processor_started_at,
    )

    now = __import__("time").time()
    for key in dedup:
        if key not in completion_comments:
            completion_comments[key] = now
    save_completion_comments(completion_comments)
