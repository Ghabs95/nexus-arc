"""Startup reconciliation helpers extracted from inbox_processor."""

import asyncio
import json
import os
from collections.abc import Callable
from typing import Any


def build_startup_workflow_payload_loader(
    *,
    db_only_task_mode,
    get_storage_backend,
    logger,
    nexus_core_storage_dir: str,
):
    def _load_startup_workflow_payload(workflow_id: str) -> dict[str, Any] | None:
        if db_only_task_mode():
            try:
                storage = get_storage_backend()
                workflow = asyncio.run(storage.load_workflow(str(workflow_id)))
            except Exception as exc:
                logger.debug(
                    "Startup reconciliation could not load workflow %s from storage: %s",
                    workflow_id,
                    exc,
                )
                return None
            if workflow is None:
                return None
            steps_payload = []
            for step in getattr(workflow, "steps", []) or []:
                agent = getattr(step, "agent", None)
                steps_payload.append(
                    {
                        "status": str(
                            getattr(
                                getattr(step, "status", None),
                                "value",
                                getattr(step, "status", ""),
                            )
                            or ""
                        ),
                        "agent": {
                            "name": str(getattr(agent, "name", "") or ""),
                            "display_name": str(getattr(agent, "display_name", "") or ""),
                        },
                    }
                )
            return {
                "state": str(
                    getattr(
                        getattr(workflow, "state", None), "value", getattr(workflow, "state", "")
                    )
                    or ""
                ),
                "steps": steps_payload,
                "metadata": getattr(workflow, "metadata", {}) or {},
            }

        wf_file = os.path.join(nexus_core_storage_dir, "workflows", f"{workflow_id}.json")
        try:
            with open(wf_file, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception:
            return None
        return payload if isinstance(payload, dict) else None

    return _load_startup_workflow_payload


def reconcile_completion_signals_on_startup(
    *,
    logger,
    emit_alert: Callable[..., Any],
    get_workflow_state_mappings: Callable[[], dict[str, Any]],
    nexus_core_storage_dir: str | None,
    load_workflow_payload: Callable[[str], dict[str, Any] | None] | None = None,
    normalize_agent_reference: Callable[[str], str],
    extract_repo_from_issue_url: Callable[[str], str],
    read_latest_local_completion: Callable[[str], dict[str, Any] | None],
    read_latest_structured_comment: Callable[[str, str, str], dict[str, Any] | None],
    is_terminal_agent_reference: Callable[[str], bool],
    complete_step_for_issue: Callable[..., Any],
    local_completions_enabled: Callable[[], bool] | None = None,
) -> None:
    """Audit workflow/comment/local completion alignment and alert on drift."""
    mappings = get_workflow_state_mappings()
    if not mappings:
        return

    for issue_num, workflow_id in mappings.items():
        payload: dict[str, Any] | None = None
        if load_workflow_payload is not None:
            try:
                candidate = load_workflow_payload(str(workflow_id))
                payload = candidate if isinstance(candidate, dict) else None
            except Exception:
                payload = None
        elif nexus_core_storage_dir:
            wf_file = os.path.join(nexus_core_storage_dir, "workflows", f"{workflow_id}.json")
            try:
                with open(wf_file, encoding="utf-8") as handle:
                    loaded = json.load(handle)
                payload = loaded if isinstance(loaded, dict) else None
            except Exception:
                payload = None

        if not payload:
            continue

        state = str(payload.get("state", "")).strip().lower()
        if state not in {"running", "paused"}:
            continue

        expected_running_agent = ""
        for step in payload.get("steps", []):
            if not isinstance(step, dict):
                continue
            if str(step.get("status", "")).strip().lower() != "running":
                continue
            agent = step.get("agent")
            if not isinstance(agent, dict):
                continue
            expected_running_agent = normalize_agent_reference(
                str(agent.get("name") or agent.get("display_name") or "")
            ).lower()
            if expected_running_agent:
                break

        if not expected_running_agent:
            continue

        metadata = payload.get("metadata", {}) if isinstance(payload.get("metadata"), dict) else {}
        issue_url = str(metadata.get("issue_url", "") or "")
        repo = extract_repo_from_issue_url(issue_url)
        project_name = str(metadata.get("project_name", "") or "")

        local_signal = read_latest_local_completion(str(issue_num))
        comment_signal = (
            read_latest_structured_comment(str(issue_num), repo, project_name) if repo else None
        )

        drifts = []
        local_next = (local_signal or {}).get("next_agent", "")
        comment_next = (comment_signal or {}).get("next_agent", "")
        comment_completed = (comment_signal or {}).get("completed_agent", "")

        if (
            comment_signal
            and comment_completed
            and comment_completed == expected_running_agent
            and comment_next
            and not is_terminal_agent_reference(comment_next)
        ):
            try:
                outputs = {
                    "status": "complete",
                    "agent_type": comment_completed,
                    "next_agent": comment_next,
                    "summary": (
                        f"Auto-reconciled on startup from comment "
                        f"{comment_signal.get('comment_id', 'n/a')}"
                    ),
                    "source": "startup-auto-reconcile",
                }
                asyncio.run(
                    complete_step_for_issue(
                        str(issue_num),
                        comment_completed,
                        outputs,
                        event_id=f"startup:{comment_signal.get('comment_id', 'n/a')}",
                    )
                )
                allow_local_completion_write = True
                if local_completions_enabled is not None:
                    try:
                        allow_local_completion_write = bool(local_completions_enabled())
                    except Exception:
                        allow_local_completion_write = False
                if allow_local_completion_write and local_signal and local_signal.get("file"):
                    try:
                        with open(local_signal["file"], "w", encoding="utf-8") as handle:
                            json.dump(
                                {
                                    "status": "complete",
                                    "agent_type": comment_completed,
                                    "summary": outputs["summary"],
                                    "key_findings": [
                                        "Startup auto-reconciled from structured comment"
                                    ],
                                    "next_agent": comment_next,
                                },
                                handle,
                                indent=2,
                            )
                    except Exception as file_exc:
                        logger.debug(
                            "Startup auto-reconcile could not rewrite local completion for issue #%s: %s",
                            issue_num,
                            file_exc,
                        )
                logger.info(
                    "Startup auto-reconciled issue #%s: %s -> %s",
                    issue_num,
                    comment_completed,
                    comment_next,
                )
                continue
            except Exception as exc:
                logger.debug(
                    "Startup auto-reconcile skipped for issue #%s due to error: %s",
                    issue_num,
                    exc,
                )

        if local_next and local_next != expected_running_agent:
            drifts.append(f"local next={local_next}")
        if comment_next and comment_next != expected_running_agent:
            drifts.append(f"comment next={comment_next}")
        if local_next and comment_next and local_next != comment_next:
            drifts.append("local/comment disagree")

        if not drifts:
            continue

        emit_alert(
            f"⚠️ Startup routing drift detected for issue #{issue_num}\n"
            f"Workflow RUNNING: `{expected_running_agent}`\n"
            f"Local completion next: `{local_next or 'n/a'}`\n"
            f"Latest structured comment next: `{comment_next or 'n/a'}`\n\n"
            "No automatic state changes were made. Reconcile manually before /continue.",
            severity="warning",
            source="inbox_processor",
        )
