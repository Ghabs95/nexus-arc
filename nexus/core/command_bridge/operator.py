from __future__ import annotations

import inspect
import os
import shutil
import subprocess
from datetime import UTC, datetime
from typing import Any

from nexus.core.models import WorkflowState
from nexus.core.orchestration.plugin_runtime import get_workflow_state_plugin
from nexus.plugins.builtin.runtime_ops_plugin import RuntimeOpsPlugin


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return str(value)


async def _maybe_await(value: Any) -> Any:
    if inspect.isawaitable(value):
        return await value
    return value


def _config() -> tuple[dict[str, Any], str | None]:
    try:
        from nexus.core.config import PROJECT_CONFIG, get_default_project, get_repo

        project_config = PROJECT_CONFIG if isinstance(PROJECT_CONFIG, dict) else {}
        default_project = get_default_project()
        default_repo = get_repo(default_project) if default_project else None
        return project_config, default_repo
    except Exception:
        return {}, None


def _resolve_tier(task_type: str) -> tuple[str | None, str | None]:
    try:
        from nexus.core.task_flow.helpers import get_sop_tier

        tier_name, _, workflow_label = get_sop_tier(str(task_type or "feature"))
        return tier_name, workflow_label
    except Exception:
        return None, None


class BridgeOperatorService:
    def __init__(self, *, workflow_state_plugin_kwargs: dict[str, Any] | None = None) -> None:
        self.workflow_state_plugin_kwargs = dict(workflow_state_plugin_kwargs or {})

    def _workflow_plugin(self):
        return get_workflow_state_plugin(
            **self.workflow_state_plugin_kwargs,
            cache_key="workflow:state-engine:command-bridge:operator",
        )

    async def _engine(self):
        plugin = self._workflow_plugin()
        getter = getattr(plugin, "_get_engine", None)
        if not callable(getter):
            raise RuntimeError("Workflow state engine plugin does not expose an engine getter")
        return await _maybe_await(getter())

    async def _workflow_by_ref(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> tuple[Any | None, str | None, str | None]:
        plugin = self._workflow_plugin()
        resolved_issue = str(issue_number or "").strip() or None
        resolved_workflow_id = str(workflow_id or "").strip() or None
        if resolved_issue is None and resolved_workflow_id is None:
            return None, None, None

        if resolved_issue is None and resolved_workflow_id is not None:
            finder = getattr(plugin, "_find_issue_for_workflow", None)
            if callable(finder):
                resolved_issue = await _maybe_await(finder(resolved_workflow_id))

        if resolved_workflow_id is None and resolved_issue is not None:
            resolver = getattr(plugin, "_resolve_workflow_id", None)
            if callable(resolver):
                resolved_workflow_id = str(await _maybe_await(resolver(resolved_issue)) or "").strip() or None

        if resolved_workflow_id is None:
            return None, resolved_issue, None

        engine = await self._engine()
        workflow = await _maybe_await(engine.get_workflow(resolved_workflow_id))
        return workflow, resolved_issue, resolved_workflow_id

    def _workflow_summary(self, workflow: Any, *, issue_number: str | None = None) -> dict[str, Any]:
        metadata = getattr(workflow, "metadata", {}) or {}
        current_step_num = int(getattr(workflow, "current_step", 0) or 0)
        steps = list(getattr(workflow, "steps", []) or [])
        current_step = None
        for step in steps:
            if int(getattr(step, "step_num", 0) or 0) == current_step_num:
                current_step = step
                break
        if current_step is None:
            for step in steps:
                status_value = getattr(getattr(step, "status", None), "value", getattr(step, "status", None))
                if str(status_value or "") == "running":
                    current_step = step
                    break

        last_completed = None
        for step in steps:
            status_value = getattr(getattr(step, "status", None), "value", getattr(step, "status", None))
            if str(status_value or "") == "completed":
                if last_completed is None or int(getattr(step, "step_num", 0) or 0) > int(getattr(last_completed, "step_num", 0) or 0):
                    last_completed = step

        next_step = None
        if current_step is not None:
            for step in steps:
                if int(getattr(step, "step_num", 0) or 0) > int(getattr(current_step, "step_num", 0) or 0):
                    status_value = getattr(getattr(step, "status", None), "value", getattr(step, "status", None))
                    if str(status_value or "") in {"pending", "running"}:
                        next_step = step
                        break

        state_value = getattr(getattr(workflow, "state", None), "value", getattr(workflow, "state", None))
        return {
            "workflow_id": str(getattr(workflow, "id", "") or ""),
            "issue_number": str(issue_number or metadata.get("issue_number") or "") or None,
            "project_key": str(metadata.get("project") or "") or None,
            "tier": str(metadata.get("tier") or "") or None,
            "task_type": str(metadata.get("task_type") or "") or None,
            "state": str(state_value or ""),
            "active_agent": getattr(workflow, "active_agent_type", None),
            "current_step": {
                "step_num": getattr(current_step, "step_num", None),
                "name": getattr(current_step, "name", None),
                "agent": getattr(getattr(current_step, "agent", None), "name", None),
                "status": getattr(getattr(current_step, "status", None), "value", getattr(current_step, "status", None)),
                "error": getattr(current_step, "error", None),
            }
            if current_step is not None
            else None,
            "last_completed_step": {
                "step_num": getattr(last_completed, "step_num", None),
                "name": getattr(last_completed, "name", None),
                "agent": getattr(getattr(last_completed, "agent", None), "name", None),
                "completed_at": _iso(getattr(last_completed, "completed_at", None)),
            }
            if last_completed is not None
            else None,
            "next_step": {
                "step_num": getattr(next_step, "step_num", None),
                "name": getattr(next_step, "name", None),
                "agent": getattr(getattr(next_step, "agent", None), "name", None),
                "status": getattr(getattr(next_step, "status", None), "value", getattr(next_step, "status", None)),
            }
            if next_step is not None
            else None,
            "created_at": _iso(getattr(workflow, "created_at", None)),
            "updated_at": _iso(getattr(workflow, "updated_at", None)),
            "completed_at": _iso(getattr(workflow, "completed_at", None)),
        }

    async def workflow_status(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        workflow, resolved_issue, resolved_workflow_id = await self._workflow_by_ref(
            workflow_id=workflow_id,
            issue_number=issue_number,
        )
        if workflow is None:
            return {
                "ok": False,
                "error": "Workflow not found",
                "workflow_id": resolved_workflow_id,
                "issue_number": resolved_issue,
            }
        plugin = self._workflow_plugin()
        plugin_status = None
        if resolved_issue:
            try:
                plugin_status = await _maybe_await(plugin.get_workflow_status(resolved_issue))
            except Exception:
                plugin_status = None
        return {
            "ok": True,
            "workflow": self._workflow_summary(workflow, issue_number=resolved_issue),
            "plugin_status": plugin_status,
        }

    async def workflow_summary(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        payload = await self.workflow_status(workflow_id=workflow_id, issue_number=issue_number)
        if not payload.get("ok"):
            return payload

        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
        plugin_status = payload.get("plugin_status") if isinstance(payload.get("plugin_status"), dict) else {}
        state = str(workflow.get("state") or "unknown")
        current_step = workflow.get("current_step") if isinstance(workflow.get("current_step"), dict) else {}
        next_step = workflow.get("next_step") if isinstance(workflow.get("next_step"), dict) else {}
        last_completed = workflow.get("last_completed_step") if isinstance(workflow.get("last_completed_step"), dict) else {}

        reason = None
        actions: list[str] = []
        if state == "failed":
            reason = str(current_step.get("error") or "Workflow is in failed state")
            actions = ["inspect recent failures", "retry-step", "refresh-state"]
        elif state == "paused":
            reason = "Workflow is paused and needs a continue/resume action"
            actions = ["continue", "refresh-state"]
        elif state == "cancelled":
            reason = "Workflow has been cancelled"
            actions = ["refresh-state"]
        elif state == "completed":
            reason = "Workflow completed successfully"
            actions = ["inspect workflow status"]
        elif current_step.get("status") == "running":
            reason = f"Waiting for @{current_step.get('agent') or workflow.get('active_agent') or 'unknown'} to finish current step"
            actions = ["inspect logs", "refresh-state"]
        elif next_step.get("agent"):
            reason = f"Next expected handoff is @{next_step.get('agent')}"
            actions = ["continue", "refresh-state"]
        else:
            reason = "Workflow state is available but the next handoff is unclear"
            actions = ["inspect workflow status", "refresh-state"]

        summary_lines = [
            f"Workflow {workflow.get('workflow_id') or workflow_id or ''}".strip(),
            f"state={state}",
        ]
        if workflow.get("issue_number"):
            summary_lines.append(f"issue=#{workflow.get('issue_number')}")
        if workflow.get("project_key"):
            summary_lines.append(f"project={workflow.get('project_key')}")
        if current_step.get("name"):
            summary_lines.append(
                f"current_step={current_step.get('step_num')}:{current_step.get('name')}@{current_step.get('agent') or 'unknown'}"
            )
        if last_completed.get("name"):
            summary_lines.append(
                f"last_completed={last_completed.get('step_num')}:{last_completed.get('name')}@{last_completed.get('agent') or 'unknown'}"
            )
        if next_step.get("name"):
            summary_lines.append(
                f"next_step={next_step.get('step_num')}:{next_step.get('name')}@{next_step.get('agent') or 'unknown'}"
            )

        return {
            "ok": True,
            "workflow": workflow,
            "plugin_status": plugin_status,
            "reason": reason,
            "suggested_actions": actions,
            "summary": "; ".join(summary_lines),
        }

    async def workflow_diagnosis(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        payload = await self.workflow_summary(workflow_id=workflow_id, issue_number=issue_number)
        if not payload.get("ok"):
            return payload

        workflow = payload.get("workflow") if isinstance(payload.get("workflow"), dict) else {}
        state = str(workflow.get("state") or "unknown")
        current_step = workflow.get("current_step") if isinstance(workflow.get("current_step"), dict) else {}
        next_step = workflow.get("next_step") if isinstance(workflow.get("next_step"), dict) else {}
        diagnosis = "unknown"
        likely_cause = payload.get("reason")
        actions = list(payload.get("suggested_actions") or [])

        if state == "failed":
            diagnosis = "step_failed"
            likely_cause = current_step.get("error") or "Current step failed"
        elif state == "paused":
            diagnosis = "workflow_paused"
            likely_cause = "Workflow is paused and waiting for operator action"
        elif state == "cancelled":
            diagnosis = "workflow_cancelled"
            likely_cause = "Workflow was cancelled"
        elif state == "completed":
            diagnosis = "workflow_completed"
            likely_cause = "Workflow already completed"
        elif current_step.get("status") == "running":
            diagnosis = "agent_running"
            likely_cause = f"Current step is still running under @{current_step.get('agent') or workflow.get('active_agent') or 'unknown'}"
        elif next_step.get("agent"):
            diagnosis = "handoff_pending"
            likely_cause = f"Next handoff should go to @{next_step.get('agent')}"
        else:
            diagnosis = "state_unclear"
            likely_cause = "Workflow state exists but no clear next handoff was inferred"

        return {
            "ok": True,
            "diagnosis": diagnosis,
            "likely_cause": likely_cause,
            "summary": payload.get("summary"),
            "suggested_actions": actions,
            "workflow": workflow,
        }

    async def active_workflows(self, *, limit: int = 20) -> dict[str, Any]:
        engine = await self._engine()
        storage = getattr(engine, "storage", None)
        if storage is None or not hasattr(storage, "list_workflows"):
            return {"ok": False, "error": "Workflow storage backend does not support listing workflows"}
        running = await _maybe_await(storage.list_workflows(WorkflowState.RUNNING, int(limit)))
        paused = await _maybe_await(storage.list_workflows(WorkflowState.PAUSED, int(limit)))
        items = [self._workflow_summary(wf) for wf in list(running or []) + list(paused or [])]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"ok": True, "items": items[: int(limit)], "count": len(items[: int(limit)])}

    async def recent_failures(self, *, limit: int = 20) -> dict[str, Any]:
        engine = await self._engine()
        storage = getattr(engine, "storage", None)
        if storage is None or not hasattr(storage, "list_workflows"):
            return {"ok": False, "error": "Workflow storage backend does not support listing workflows"}
        failed = await _maybe_await(storage.list_workflows(WorkflowState.FAILED, int(limit)))
        cancelled = await _maybe_await(storage.list_workflows(WorkflowState.CANCELLED, int(limit)))
        items = [self._workflow_summary(wf) for wf in list(failed or []) + list(cancelled or [])]
        items.sort(key=lambda item: str(item.get("updated_at") or ""), reverse=True)
        return {"ok": True, "items": items[: int(limit)], "count": len(items[: int(limit)])}

    async def runtime_health(self) -> dict[str, Any]:
        engine = await self._engine()
        storage = getattr(engine, "storage", None)
        storage_name = type(storage).__name__ if storage is not None else None
        runtime_ops = RuntimeOpsPlugin()
        active = await self.active_workflows(limit=100)
        failures = await self.recent_failures(limit=20)
        return {
            "ok": True,
            "timestamp": datetime.now(UTC).isoformat(),
            "runtime_mode": str(os.getenv("NEXUS_RUNTIME_MODE") or "").strip() or None,
            "storage_backend": storage_name,
            "bridge": {
                "command_bridge_auth_configured": bool(str(os.getenv("NEXUS_COMMAND_BRIDGE_AUTH_TOKEN") or "").strip()),
                "openclaw_bridge_url": bool(str(os.getenv("NEXUS_OPENCLAW_BRIDGE_URL") or "").strip()),
                "openclaw_bridge_token": bool(str(os.getenv("NEXUS_OPENCLAW_BRIDGE_TOKEN") or "").strip()),
                "openclaw_session_key": bool(str(os.getenv("NEXUS_OPENCLAW_SESSION_KEY") or "").strip()),
            },
            "tooling": {
                "gh": shutil.which("gh") is not None,
                "glab": shutil.which("glab") is not None,
                "pgrep": runtime_ops._pgrep_path is not None,
            },
            "active_workflow_count": int(active.get("count") or 0) if active.get("ok") else None,
            "recent_failure_count": int(failures.get("count") or 0) if failures.get("ok") else None,
        }

    def _cli_identity(self, cli_name: str, args: list[str]) -> dict[str, Any]:
        if shutil.which(cli_name) is None:
            return {"installed": False, "authenticated": None, "summary": None}
        try:
            proc = subprocess.run(
                [cli_name, *args],
                text=True,
                capture_output=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:
            return {"installed": True, "authenticated": None, "summary": f"{type(exc).__name__}: {exc}"}
        stdout = str(proc.stdout or "").strip()
        stderr = str(proc.stderr or "").strip()
        combined = "\n".join(part for part in (stdout, stderr) if part).strip()
        authenticated = proc.returncode == 0
        return {
            "installed": True,
            "authenticated": authenticated,
            "summary": combined.splitlines()[:6],
        }

    async def git_identity_status(self) -> dict[str, Any]:
        github = self._cli_identity("gh", ["auth", "status"])
        gitlab = self._cli_identity("glab", ["auth", "status"])
        env_presence = {
            "github": {
                "NEXUS_AUTOMATION_GITHUB_TOKEN": bool(str(os.getenv("NEXUS_AUTOMATION_GITHUB_TOKEN") or "").strip()),
                "NEXUS_AUTOMATION_GIT_TOKEN": bool(str(os.getenv("NEXUS_AUTOMATION_GIT_TOKEN") or "").strip()),
                "GH_TOKEN": bool(str(os.getenv("GH_TOKEN") or "").strip()),
                "GITHUB_TOKEN": bool(str(os.getenv("GITHUB_TOKEN") or "").strip()),
            },
            "gitlab": {
                "NEXUS_AUTOMATION_GITLAB_TOKEN": bool(str(os.getenv("NEXUS_AUTOMATION_GITLAB_TOKEN") or "").strip()),
                "NEXUS_AUTOMATION_GIT_TOKEN": bool(str(os.getenv("NEXUS_AUTOMATION_GIT_TOKEN") or "").strip()),
                "GLAB_TOKEN": bool(str(os.getenv("GLAB_TOKEN") or "").strip()),
                "GITLAB_TOKEN": bool(str(os.getenv("GITLAB_TOKEN") or "").strip()),
            },
        }
        return {"ok": True, "github": github, "gitlab": gitlab, "env_presence": env_presence}

    async def routing_explain(
        self,
        *,
        project_key: str,
        task_type: str = "feature",
        workflow_id: str | None = None,
        issue_number: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        project_key = str(project_key or "").strip()
        if not project_key:
            return {"ok": False, "error": "project_key is required"}
        project_config, fallback_default_repo = _config()
        cfg = project_config.get(project_key)
        if not isinstance(cfg, dict):
            return {"ok": False, "error": f"Unknown project '{project_key}'"}
        tier_name, workflow_label = _resolve_tier(str(task_type or "feature"))
        workflow_path = cfg.get("workflow_definition_path")
        default_repo = cfg.get("git_repo") or fallback_default_repo
        repos = list(cfg.get("git_repos") or ([] if not default_repo else [default_repo]))
        branches = dict(cfg.get("git_branches") or {})
        ai_prefs = dict(cfg.get("ai_tool_preferences") or {})
        model_profiles = dict(cfg.get("model_profiles") or {})
        provider_priority = dict(cfg.get("profile_provider_priority") or {})
        chosen_agent = str(agent_name or "").strip() or None
        if not chosen_agent and workflow_id:
            workflow, _, _ = await self._workflow_by_ref(workflow_id=workflow_id, issue_number=issue_number)
            if workflow is not None:
                chosen_agent = getattr(workflow, "active_agent_type", None)
        agent_pref = ai_prefs.get(chosen_agent or "") if chosen_agent else None
        profile_name = agent_pref.get("profile") if isinstance(agent_pref, dict) else None
        provider_name = agent_pref.get("provider") if isinstance(agent_pref, dict) else None
        return {
            "ok": True,
            "project_key": project_key,
            "task_type": task_type,
            "tier_name": tier_name,
            "workflow_label": workflow_label,
            "workflow_definition_path": workflow_path,
            "default_repo": default_repo,
            "repos": repos,
            "default_branch": branches.get("default"),
            "git_platform": cfg.get("git_platform"),
            "workspace": cfg.get("workspace"),
            "active_agent": chosen_agent,
            "agent_preference": agent_pref,
            "resolved_profile": {
                "name": profile_name,
                "models": model_profiles.get(profile_name) if profile_name else None,
                "provider_priority": provider_priority.get(profile_name) if profile_name else None,
                "provider": provider_name,
            },
        }

    async def continue_workflow(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        plugin = self._workflow_plugin()
        workflow, resolved_issue, _ = await self._workflow_by_ref(workflow_id=workflow_id, issue_number=issue_number)
        if workflow is None or resolved_issue is None:
            return {"ok": False, "error": "Workflow not found", "issue_number": resolved_issue}
        if target_agent:
            reset = getattr(plugin, "reset_to_agent_for_issue", None)
            if not callable(reset):
                return {"ok": False, "error": "Workflow plugin does not support reset_to_agent_for_issue"}
            success = await _maybe_await(reset(str(resolved_issue), str(target_agent)))
            if not success:
                return {"ok": False, "error": f"Could not reset workflow to agent '{target_agent}'"}
        return await self.workflow_status(issue_number=resolved_issue)

    async def retry_step(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
        target_agent: str,
    ) -> dict[str, Any]:
        return await self.continue_workflow(
            workflow_id=workflow_id,
            issue_number=issue_number,
            target_agent=target_agent,
        )

    async def cancel_workflow(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        workflow, resolved_issue, resolved_workflow_id = await self._workflow_by_ref(
            workflow_id=workflow_id,
            issue_number=issue_number,
        )
        if workflow is None or resolved_workflow_id is None:
            return {"ok": False, "error": "Workflow not found"}
        engine = await self._engine()
        try:
            await _maybe_await(engine.cancel_workflow(resolved_workflow_id))
        except Exception as exc:
            return {"ok": False, "error": str(exc), "workflow_id": resolved_workflow_id}
        return await self.workflow_status(workflow_id=resolved_workflow_id, issue_number=resolved_issue)

    async def refresh_state(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        return await self.workflow_status(workflow_id=workflow_id, issue_number=issue_number)
