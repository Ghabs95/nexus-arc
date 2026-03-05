import asyncio
import logging
import threading
from typing import Any

logger = logging.getLogger(__name__)


def _extract_agent_ref(step: Any) -> str | None:
    agent = getattr(step, "agent", None)
    name = str(getattr(agent, "name", "") or "").strip()
    display_name = str(getattr(agent, "display_name", "") or "").strip()
    if name:
        return name
    if display_name:
        return display_name
    return None


def get_expected_running_agent_from_workflow(
    *, issue_num: str, get_workflow_id, workflow_plugin
) -> str | None:
    workflow_id = get_workflow_id(str(issue_num))
    if not workflow_id:
        return None

    def _load_workflow() -> Any:
        async def _runner() -> Any:
            engine = workflow_plugin._get_engine()
            return await engine.get_workflow(workflow_id)

        try:
            asyncio.get_running_loop()
            in_running_loop = True
        except RuntimeError:
            in_running_loop = False

        if not in_running_loop:
            try:
                return asyncio.run(_runner())
            except Exception as exc:
                logger.warning(
                    "Workflow probe failed for issue #%s workflow_id=%s: %r",
                    issue_num,
                    workflow_id,
                    exc,
                )
                return None

        holder: dict[str, Any] = {"value": None, "error": None}

        def _thread_target() -> None:
            try:
                holder["value"] = asyncio.run(_runner())
            except Exception as inner_exc:
                holder["error"] = inner_exc

        worker = threading.Thread(target=_thread_target, daemon=True)
        worker.start()
        worker.join(timeout=10)
        if worker.is_alive() or holder["error"] is not None:
            if holder["error"] is not None:
                logger.warning(
                    "Workflow probe thread failed for issue #%s workflow_id=%s: %r",
                    issue_num,
                    workflow_id,
                    holder["error"],
                )
            return None
        return holder["value"]

    workflow = _load_workflow()
    if not workflow:
        return None

    state_obj = getattr(workflow, "state", None)
    state = str(getattr(state_obj, "value", state_obj or "")).strip().lower()
    if state in {"completed", "failed", "cancelled"}:
        return None

    steps = list(getattr(workflow, "steps", []) or [])
    for step in steps:
        status_obj = getattr(step, "status", None)
        status = str(getattr(status_obj, "value", status_obj or "")).strip().lower()
        if status != "running":
            continue

        running_agent = _extract_agent_ref(step)
        if running_agent:
            return running_agent

    # Fallback: if no step is marked RUNNING (common after stale/dead process cleanup),
    # align /continue with the workflow's current_step pointer instead of defaulting to triage.
    current_step_num = getattr(workflow, "current_step", None)
    try:
        current_step_int = int(current_step_num)
    except (TypeError, ValueError):
        current_step_int = None

    if current_step_int is not None:
        for step in steps:
            step_num = getattr(step, "step_num", None)
            try:
                if int(step_num) != current_step_int:
                    continue
            except (TypeError, ValueError):
                continue
            fallback_agent = _extract_agent_ref(step)
            if fallback_agent:
                logger.info(
                    "Workflow probe fallback used current_step for issue #%s: step=%s agent=%s",
                    issue_num,
                    current_step_int,
                    fallback_agent,
                )
                return fallback_agent

    return None
