from __future__ import annotations

import asyncio
import glob
import inspect
import json
import os
import re
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


def _status_value(value: Any) -> str:
    return str(getattr(value, "value", value) or "").strip().lower()


def _step_agent_name(step: Any) -> str | None:
    return getattr(getattr(step, "agent", None), "name", None)


def _trim_error_summary(error: Any, *, max_len: int = 240) -> str | None:
    text = str(error or "").strip()
    if not text:
        return None
    compact = re.sub(r"\s+", " ", text)
    if len(compact) <= max_len:
        return compact
    return compact[: max_len - 3].rstrip() + "..."


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


def _classify_identity(name: Any, *, kind: str | None = None) -> dict[str, Any]:
    raw = str(name or '').strip()
    normalized = raw.lower()
    reasons: list[str] = []
    actor_type = 'unknown'
    if not raw:
        reasons.append('missing_identity')
    else:
        bot_markers = ('[bot]', '-bot', '_bot', ' bot', 'openclaw', 'codex', 'claude', 'gemini', 'copilot')
        if kind == 'active_agent' and raw:
            actor_type = 'bot'
            reasons.append('workflow_active_agent')
        elif any(marker in normalized for marker in bot_markers):
            actor_type = 'bot'
            reasons.append('matched_bot_marker')
        elif kind in {'requester_nexus_id', 'requester_login', 'requester_user', 'requested_by', 'issue_author', 'pr_author', 'comment_author'}:
            actor_type = 'human'
            reasons.append('requester_or_author_identity_hint')
        elif raw:
            actor_type = 'likely_human'
            reasons.append('plain_identity_without_bot_markers')
    return {'name': raw or None, 'classification': actor_type, 'reasons': reasons}


def _repo_slug_name(repo: Any) -> str:
    repo_text = str(repo or '').strip()
    if not repo_text:
        return ''
    return repo_text.rsplit('/', 1)[-1].lower()


def _expected_repo_roles(project_key: str, repos: list[str], task_type: str) -> list[dict[str, Any]]:
    normalized_task = str(task_type or 'feature').strip().lower()
    roles: list[dict[str, Any]] = []
    for repo in repos:
        slug = _repo_slug_name(repo)
        role = 'general'
        expected_for: list[str] = []
        notes: list[str] = []
        if slug == 'nexus-os':
            role = 'platform'
            expected_for = ['ops', 'infra', 'runtime', 'deployment', 'operator']
            notes.append('Best fit for machine/runtime/deployment or host/operator work.')
        elif slug == 'nexus-arc':
            role = 'framework'
            expected_for = ['feature', 'workflow', 'plugin', 'bridge', 'orchestration']
            notes.append('Best fit for core workflow/runtime/bridge framework changes.')
        elif slug == 'nexus':
            role = 'product'
            expected_for = ['feature', 'product', 'integration', 'control-surface', 'workflow-truth']
            notes.append('Best fit for the main Nexus repo and workflow-truth changes.')
        else:
            notes.append('No nexus-specific split heuristic available for this repo.')
        task_match = any(token in normalized_task for token in expected_for) if expected_for else False
        if project_key == 'nexus' and slug in {'nexus-arc', 'nexus'} and normalized_task in {'feature', 'bug', 'fix'}:
            task_match = True
        roles.append({'repo': repo, 'slug': slug, 'role': role, 'expected_work_types': expected_for, 'task_type_match': task_match, 'notes': notes})
    return roles


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

    @staticmethod
    def _issue_from_metadata(workflow: Any) -> str | None:
        """Return the issue_number embedded in *workflow* metadata, or None."""
        metadata = getattr(workflow, "metadata", None)
        if not isinstance(metadata, dict):
            return None
        meta_issue = metadata.get("issue_number") or metadata.get("issue")
        return str(meta_issue) if meta_issue is not None else None

    def _step_summary(self, step: Any) -> dict[str, Any]:
        return {
            "step_num": getattr(step, "step_num", None),
            "name": getattr(step, "name", None),
            "agent": _step_agent_name(step),
            "status": _status_value(getattr(step, "status", None)),
            "started_at": _iso(getattr(step, "started_at", None)),
            "completed_at": _iso(getattr(step, "completed_at", None)),
            "retry_count": int(getattr(step, "retry_count", 0) or 0),
            "error": getattr(step, "error", None),
            "error_summary": _trim_error_summary(getattr(step, "error", None)),
        }

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
                if _status_value(getattr(step, "status", None)) == "running":
                    current_step = step
                    break

        last_completed = None
        for step in steps:
            if _status_value(getattr(step, "status", None)) == "completed":
                if last_completed is None or int(getattr(step, "step_num", 0) or 0) > int(getattr(last_completed, "step_num", 0) or 0):
                    last_completed = step

        next_step = None
        if current_step is not None:
            for step in steps:
                if int(getattr(step, "step_num", 0) or 0) > int(getattr(current_step, "step_num", 0) or 0):
                    if _status_value(getattr(step, "status", None)) in {"pending", "running"}:
                        next_step = step
                        break

        state_value = _status_value(getattr(workflow, "state", None))
        return {
            "workflow_id": str(getattr(workflow, "id", "") or ""),
            "issue_number": str(issue_number or metadata.get("issue_number") or "") or None,
            "project_key": str(metadata.get("project") or "") or None,
            "tier": str(metadata.get("tier") or "") or None,
            "task_type": str(metadata.get("task_type") or "") or None,
            "state": str(state_value or ""),
            "active_agent": getattr(workflow, "active_agent_type", None),
            "current_step": self._step_summary(current_step) if current_step is not None else None,
            "last_completed_step": {
                "step_num": getattr(last_completed, "step_num", None),
                "name": getattr(last_completed, "name", None),
                "agent": _step_agent_name(last_completed),
                "completed_at": _iso(getattr(last_completed, "completed_at", None)),
            }
            if last_completed is not None
            else None,
            "next_step": {
                "step_num": getattr(next_step, "step_num", None),
                "name": getattr(next_step, "name", None),
                "agent": _step_agent_name(next_step),
                "status": _status_value(getattr(next_step, "status", None)),
            }
            if next_step is not None
            else None,
            "created_at": _iso(getattr(workflow, "created_at", None)),
            "updated_at": _iso(getattr(workflow, "updated_at", None)),
            "completed_at": _iso(getattr(workflow, "completed_at", None)),
        }

    def _timeline_items(self, workflow: Any) -> list[dict[str, Any]]:
        steps = list(getattr(workflow, "steps", []) or [])
        items = [self._step_summary(step) for step in steps]
        items.sort(key=lambda item: int(item.get("step_num") or 0))
        return items

    def _infer_diagnosis(
        self,
        *,
        workflow: dict[str, Any],
        plugin_status: dict[str, Any],
        fallback_reason: str | None,
        fallback_actions: list[str],
    ) -> tuple[str, str | None, list[str]]:
        state = str(workflow.get("state") or "unknown")
        current_step = workflow.get("current_step") if isinstance(workflow.get("current_step"), dict) else {}
        next_step = workflow.get("next_step") if isinstance(workflow.get("next_step"), dict) else {}
        timeline = workflow.get("timeline") if isinstance(workflow.get("timeline"), list) else []
        approval = workflow.get("approval") if isinstance(workflow.get("approval"), dict) else {}
        pending_approval = approval.get("pending_approval") if isinstance(approval.get("pending_approval"), dict) else None
        active_agent = str(workflow.get("active_agent") or plugin_status.get("current_agent_type") or "").strip() or None
        actions = list(fallback_actions or [])

        if pending_approval:
            step_name = pending_approval.get("step_name") or current_step.get("name") or "current step"
            approvers = list(pending_approval.get("approvers") or [])
            approver_text = f" from {', '.join(str(item) for item in approvers)}" if approvers else ""
            return "approval_required", f"Workflow is waiting for approval on step {pending_approval.get('step_num')}:{step_name}{approver_text}", [
                "continue",
                "inspect blockers",
                "inspect logs-context",
                "refresh-state",
            ]
        if state == "failed":
            return "step_failed", current_step.get("error_summary") or fallback_reason or "Current step failed", [
                "inspect logs-context",
                "retry-step",
                "refresh-state",
            ]
        if state == "paused":
            return "workflow_paused", "Workflow is paused and waiting for operator action", [
                "continue",
                "inspect blockers",
                "inspect logs-context",
                "refresh-state",
            ]
        if state == "cancelled":
            return "workflow_cancelled", "Workflow was cancelled", ["refresh-state"]
        if state == "completed":
            return "workflow_completed", "Workflow already completed", ["inspect workflow status"]

        for step in timeline:
            if str(step.get("status") or "") == "failed":
                return "step_failed", step.get("error_summary") or f"Step {step.get('step_num')} failed", [
                    "inspect logs-context",
                    "retry-step",
                    "refresh-state",
                ]

        if current_step.get("status") == "running":
            agent = current_step.get("agent") or active_agent or "unknown"
            return "agent_running", f"Current step is still running under @{agent}", [
                "inspect logs-context",
                "refresh-state",
            ]

        if next_step.get("agent"):
            return "handoff_pending", f"Next handoff should go to @{next_step.get('agent')}", [
                "continue",
                "refresh-state",
            ]

        if active_agent:
            return "agent_running", f"Workflow still expects activity from @{active_agent}", [
                "inspect logs-context",
                "refresh-state",
            ]

        return "state_unclear", fallback_reason or "Workflow state exists but no clear next handoff was inferred", actions or [
            "inspect workflow status",
            "inspect logs-context",
            "refresh-state",
        ]

    def _resolve_logs_dir(self, project_key: str | None) -> str | None:
        if not project_key:
            return None
        try:
            from nexus.core.config import PROJECT_CONFIG, get_tasks_logs_dir
        except Exception:
            return None
        cfg = PROJECT_CONFIG.get(project_key) if isinstance(PROJECT_CONFIG, dict) else None
        if not isinstance(cfg, dict):
            return None
        workspace = str(cfg.get("workspace") or "").strip()
        if not workspace:
            return None
        try:
            return get_tasks_logs_dir(workspace, project_key)
        except Exception:
            return None

    def _collect_log_context(
        self,
        *,
        issue_number: str | None,
        project_key: str | None,
        max_files: int = 2,
        max_lines_per_file: int = 15,
    ) -> list[dict[str, Any]]:
        issue_ref = re.sub(r"[^\d]", "", str(issue_number or "").strip())
        logs_dir = self._resolve_logs_dir(project_key)
        if not issue_ref or not logs_dir or not os.path.isdir(logs_dir):
            return []
        pattern = os.path.join(glob.escape(logs_dir), "**", f"*_{glob.escape(issue_ref)}_*.log")
        candidates = sorted(glob.glob(pattern, recursive=True), key=os.path.getmtime, reverse=True)
        results: list[dict[str, Any]] = []
        for path in candidates[:max_files]:
            try:
                with open(path, encoding="utf-8", errors="ignore") as handle:
                    lines = [line.rstrip() for line in handle.readlines()]
            except Exception:
                continue
            tail = [line for line in lines[-max_lines_per_file:] if str(line).strip()]
            if not tail:
                continue
            results.append(
                {
                    "file": os.path.basename(path),
                    "updated_at": _iso(datetime.fromtimestamp(os.path.getmtime(path), tz=UTC)),
                    "lines": tail,
                }
            )
        return results

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
        # Recover issue_number from workflow metadata when only workflow_id was given.
        if resolved_issue is None:
            resolved_issue = self._issue_from_metadata(workflow)
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

    async def workflow_timeline(
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
        summary = self._workflow_summary(workflow, issue_number=resolved_issue)
        timeline = self._timeline_items(workflow)
        return {
            "ok": True,
            "workflow": {**summary, "timeline": timeline},
            "timeline": timeline,
            "count": len(timeline),
            "raw_workflow": workflow,
        }

    async def workflow_summary(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        timeline_payload = await self.workflow_timeline(workflow_id=workflow_id, issue_number=issue_number)
        if not timeline_payload.get("ok"):
            return timeline_payload

        workflow = timeline_payload.get("workflow") if isinstance(timeline_payload.get("workflow"), dict) else {}
        payload = await self.workflow_status(workflow_id=workflow_id, issue_number=issue_number)
        if not payload.get("ok"):
            return payload
        plugin_status = payload.get("plugin_status") if isinstance(payload.get("plugin_status"), dict) else {}
        state = str(workflow.get("state") or "unknown")
        current_step = workflow.get("current_step") if isinstance(workflow.get("current_step"), dict) else {}
        next_step = workflow.get("next_step") if isinstance(workflow.get("next_step"), dict) else {}
        last_completed = workflow.get("last_completed_step") if isinstance(workflow.get("last_completed_step"), dict) else {}

        resolved_issue = str(workflow.get("issue_number") or issue_number or "").strip() or None
        raw_workflow = timeline_payload.get("raw_workflow")
        if raw_workflow is None:
            raw_workflow, _, _ = await self._workflow_by_ref(
                workflow_id=str(workflow.get("workflow_id") or workflow_id or "").strip() or None,
                issue_number=resolved_issue,
            )
        approval = (
            self._approval_gate_summary(raw_workflow, issue_number=resolved_issue)
            if raw_workflow is not None
            else {"pending_approval": None, "step_gates": [], "blockers": [], "blocking": False}
        )
        blockers = list(approval.get("blockers") or [])
        pending_approval = approval.get("pending_approval") if isinstance(approval.get("pending_approval"), dict) else None

        reason = None
        actions: list[str] = []
        if pending_approval:
            step_name = pending_approval.get("step_name") or current_step.get("name") or "current step"
            approvers = list(pending_approval.get("approvers") or [])
            approver_text = f" from {', '.join(str(item) for item in approvers)}" if approvers else ""
            reason = f"Waiting for approval on step {pending_approval.get('step_num')}:{step_name}{approver_text}"
            actions = ["continue", "inspect blockers", "inspect logs-context", "refresh-state"]
        elif state == "failed":
            reason = str(current_step.get("error_summary") or current_step.get("error") or "Workflow is in failed state")
            actions = ["inspect logs-context", "retry-step", "refresh-state"]
        elif state == "paused":
            reason = "Workflow is paused and needs a continue/resume action"
            actions = ["continue", "inspect blockers", "inspect logs-context", "refresh-state"]
        elif state == "cancelled":
            reason = "Workflow has been cancelled"
            actions = ["refresh-state"]
        elif state == "completed":
            reason = "Workflow completed successfully"
            actions = ["inspect workflow status"]
        elif current_step.get("status") == "running":
            reason = f"Waiting for @{current_step.get('agent') or workflow.get('active_agent') or 'unknown'} to finish current step"
            actions = ["inspect logs-context", "refresh-state"]
        elif next_step.get("agent"):
            reason = f"Next expected handoff is @{next_step.get('agent')}"
            actions = ["continue", "refresh-state"]
        elif blockers:
            reason = str(blockers[0].get("summary") or "Workflow has active blockers")
            actions = ["inspect blockers", "inspect logs-context", "refresh-state"]
        else:
            reason = "Workflow state is available but the next handoff is unclear"
            actions = ["inspect workflow status", "inspect logs-context", "refresh-state"]

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
        if pending_approval:
            summary_lines.append(
                f"approval_pending={pending_approval.get('step_num')}:{pending_approval.get('step_name') or current_step.get('name') or 'unknown'}"
            )
        elif blockers:
            summary_lines.append(f"blockers={len(blockers)}")

        workflow_enriched = {
            **workflow,
            "approval": approval,
            "blockers": blockers,
            "blocking": bool(approval.get("blocking")),
        }
        return {
            "ok": True,
            "workflow": workflow_enriched,
            "plugin_status": plugin_status,
            "reason": reason,
            "suggested_actions": actions,
            "summary": "; ".join(summary_lines),
            "approval": approval,
            "blockers": blockers,
            "blocking": bool(approval.get("blocking")),
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
        plugin_status = payload.get("plugin_status") if isinstance(payload.get("plugin_status"), dict) else {}
        diagnosis, likely_cause, actions = self._infer_diagnosis(
            workflow=workflow,
            plugin_status=plugin_status,
            fallback_reason=payload.get("reason"),
            fallback_actions=list(payload.get("suggested_actions") or []),
        )

        return {
            "ok": True,
            "diagnosis": diagnosis,
            "likely_cause": likely_cause,
            "summary": payload.get("summary"),
            "suggested_actions": actions,
            "workflow": workflow,
            "plugin_status": plugin_status,
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

    async def recent_incidents(self, *, limit: int = 20) -> dict[str, Any]:
        engine = await self._engine()
        storage = getattr(engine, "storage", None)
        if storage is None or not hasattr(storage, "list_workflows"):
            return {"ok": False, "error": "Workflow storage backend does not support listing workflows"}

        states_to_scan = [WorkflowState.FAILED, WorkflowState.PAUSED, WorkflowState.RUNNING]
        workflows: list[Any] = []
        seen_ids: set[str] = set()
        for state in states_to_scan:
            for workflow in list(await _maybe_await(storage.list_workflows(state, int(limit))) or []):
                workflow_id = str(getattr(workflow, "id", "") or "")
                if workflow_id in seen_ids:
                    continue
                seen_ids.add(workflow_id)
                workflows.append(workflow)

        incidents: list[dict[str, Any]] = []
        for workflow in workflows:
            summary = self._workflow_summary(workflow)
            summary["timeline"] = self._timeline_items(workflow)
            diagnosis, likely_cause, actions = self._infer_diagnosis(
                workflow=summary,
                plugin_status={},
                fallback_reason=None,
                fallback_actions=[],
            )
            if diagnosis == "workflow_completed":
                continue
            severity = "warning"
            if diagnosis == "step_failed":
                severity = "critical"
            elif diagnosis == "workflow_paused":
                severity = "high"
            elif any(int(step.get("retry_count") or 0) > 0 for step in summary.get("timeline") or []):
                severity = "medium"
            incidents.append(
                {
                    "workflow_id": summary.get("workflow_id"),
                    "issue_number": summary.get("issue_number"),
                    "project_key": summary.get("project_key"),
                    "state": summary.get("state"),
                    "diagnosis": diagnosis,
                    "severity": severity,
                    "likely_cause": likely_cause,
                    "current_step": summary.get("current_step"),
                    "retrying_steps": [
                        step for step in summary.get("timeline") or [] if int(step.get("retry_count") or 0) > 0
                    ],
                    "suggested_actions": actions,
                    "updated_at": summary.get("updated_at"),
                }
            )

        severity_rank = {"critical": 0, "high": 1, "medium": 2, "warning": 3}
        incidents.sort(
            key=lambda item: (
                severity_rank.get(str(item.get("severity") or "warning"), 99),
                str(item.get("updated_at") or ""),
            ),
            reverse=False,
        )
        incidents = incidents[: int(limit)]
        return {"ok": True, "items": incidents, "count": len(incidents)}

    async def workflow_logs_context(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        summary = await self.workflow_summary(workflow_id=workflow_id, issue_number=issue_number)
        if not summary.get("ok"):
            return summary
        workflow = summary.get("workflow") if isinstance(summary.get("workflow"), dict) else {}
        logs = self._collect_log_context(
            issue_number=workflow.get("issue_number"),
            project_key=workflow.get("project_key"),
        )
        return {
            "ok": True,
            "summary": summary.get("summary"),
            "reason": summary.get("reason"),
            "suggested_actions": summary.get("suggested_actions") or [],
            "workflow": workflow,
            "log_context": logs,
            "log_count": len(logs),
        }

    async def runtime_health(self) -> dict[str, Any]:
        engine = await self._engine()
        storage = getattr(engine, "storage", None)
        storage_name = type(storage).__name__ if storage is not None else None
        runtime_ops = RuntimeOpsPlugin()
        active = await self.active_workflows(limit=100)
        failures = await self.recent_failures(limit=20)
        runtime_mode = str(os.getenv("NEXUS_RUNTIME_MODE") or "").strip() or None
        openclaw_wake_mode = str(os.getenv("NEXUS_OPENCLAW_WAKE_MODE") or "").strip() or None
        warnings: list[str] = []
        if runtime_mode == "openclaw" and openclaw_wake_mode:
            warnings.append(
                "OpenClaw wake mode is enabled. Wakeful workflow notifications can loop back into the "
                "operator chat and leave OpenClaw stuck on 'Compacting context...'. Leave "
                "NEXUS_OPENCLAW_WAKE_MODE unset for passive notifications unless interactive wakeups are required."
            )
        return {
            "ok": True,
            "timestamp": datetime.now(UTC).isoformat(),
            "runtime_mode": runtime_mode,
            "storage_backend": storage_name,
            "bridge": {
                "command_bridge_auth_configured": bool(str(os.getenv("NEXUS_COMMAND_BRIDGE_AUTH_TOKEN") or "").strip()),
                "openclaw_bridge_url": bool(str(os.getenv("NEXUS_OPENCLAW_BRIDGE_URL") or "").strip()),
                "openclaw_bridge_token": bool(str(os.getenv("NEXUS_OPENCLAW_BRIDGE_TOKEN") or "").strip()),
                "openclaw_sender_id": bool(str(os.getenv("NEXUS_OPENCLAW_SENDER_ID") or "").strip()),
                "openclaw_session_key": bool(str(os.getenv("NEXUS_OPENCLAW_SESSION_KEY") or "").strip()),
                "openclaw_wake_mode": openclaw_wake_mode,
            },
            "tooling": {
                "gh": shutil.which("gh") is not None,
                "glab": shutil.which("glab") is not None,
                "pgrep": runtime_ops._pgrep_path is not None,
            },
            "active_workflow_count": int(active.get("count") or 0) if active.get("ok") else None,
            "recent_failure_count": int(failures.get("count") or 0) if failures.get("ok") else None,
            "warnings": warnings,
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
        github, gitlab = await asyncio.gather(
            asyncio.to_thread(self._cli_identity, "gh", ["auth", "status"]),
            asyncio.to_thread(self._cli_identity, "glab", ["auth", "status"]),
        )
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

    def _approval_store(self):
        try:
            from nexus.core.integrations.workflow_state_factory import get_workflow_state

            return get_workflow_state()
        except Exception:
            return None

    def _approval_gate_summary(self, workflow: Any, *, issue_number: str | None = None) -> dict[str, Any]:
        steps = list(getattr(workflow, 'steps', []) or [])
        pending_steps: list[dict[str, Any]] = []
        for step in steps:
            gates = list(getattr(step, 'approval_gates', []) or [])
            if not gates and not bool(getattr(step, 'require_human_approval', False)):
                continue
            gate_types = [str(getattr(getattr(g, 'gate_type', None), 'value', getattr(g, 'gate_type', None)) or 'custom') for g in gates if getattr(g, 'required', True)]
            tool_restrictions: list[str] = []
            for gate in gates:
                for restriction in list(getattr(gate, 'tool_restrictions', []) or []):
                    value = str(restriction or '').strip()
                    if value and value not in tool_restrictions:
                        tool_restrictions.append(value)
            pending_steps.append({
                'step_num': getattr(step, 'step_num', None),
                'step_name': getattr(step, 'name', None),
                'status': _status_value(getattr(step, 'status', None)),
                'requires_human_approval': bool(getattr(step, 'require_human_approval', False) or gate_types),
                'gate_types': gate_types,
                'tool_restrictions': tool_restrictions,
            })

        pending_record = None
        if issue_number:
            store = self._approval_store()
            getter = getattr(store, 'get_pending_approval', None) if store is not None else None
            if callable(getter):
                try:
                    candidate = getter(str(issue_number))
                    pending_record = candidate if isinstance(candidate, dict) else None
                except Exception:
                    pending_record = None

        blockers: list[dict[str, Any]] = []
        state = _status_value(getattr(workflow, 'state', None))
        if state == 'paused':
            blockers.append({'type': 'workflow_paused', 'severity': 'high', 'summary': 'Workflow is paused and waiting for an explicit operator action.'})
        if pending_record:
            blockers.append({'type': 'approval_required', 'severity': 'high', 'summary': f"Step {pending_record.get('step_num')} is waiting for approval", 'approval': {'step_num': pending_record.get('step_num'), 'step_name': pending_record.get('step_name'), 'approvers': list(pending_record.get('approvers') or []), 'approval_timeout': pending_record.get('approval_timeout')}})

        for step in steps:
            status = _status_value(getattr(step, 'status', None))
            name = str(getattr(step, 'name', '') or '')
            lower_name = name.lower()
            if status == 'pending' and any(token in lower_name for token in ('review', 'compliance', 'approval')):
                blockers.append({'type': 'downstream_gate', 'severity': 'medium', 'summary': f'Pending downstream gate on step {getattr(step, "step_num", None)}:{name}'})
            error_summary = _trim_error_summary(getattr(step, 'error', None))
            if error_summary and any(token in error_summary.lower() for token in ('approval', 'blocked', 'dependency', 'waiting for human', 'compliance')):
                blockers.append({'type': 'reported_blocker', 'severity': 'medium', 'summary': error_summary, 'step_num': getattr(step, 'step_num', None)})

        seen = set()
        unique_blockers = []
        for blocker in blockers:
            key = json.dumps(blocker, sort_keys=True, default=str)
            if key in seen:
                continue
            seen.add(key)
            unique_blockers.append(blocker)

        return {'pending_approval': pending_record, 'step_gates': pending_steps, 'blockers': unique_blockers, 'blocking': bool(unique_blockers)}

    async def workflow_blockers(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        workflow, resolved_issue, resolved_workflow_id = await self._workflow_by_ref(workflow_id=workflow_id, issue_number=issue_number)
        if workflow is None:
            return {'ok': False, 'error': 'Workflow not found', 'workflow_id': resolved_workflow_id, 'issue_number': resolved_issue}
        summary = self._workflow_summary(workflow, issue_number=resolved_issue)
        approval = self._approval_gate_summary(workflow, issue_number=resolved_issue)
        return {'ok': True, 'workflow': summary, 'approval': approval, 'blockers': approval.get('blockers') or [], 'blocking': bool(approval.get('blocking'))}

    async def workflow_authorship_audit(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
    ) -> dict[str, Any]:
        workflow, resolved_issue, resolved_workflow_id = await self._workflow_by_ref(workflow_id=workflow_id, issue_number=issue_number)
        if workflow is None:
            return {'ok': False, 'error': 'Workflow not found', 'workflow_id': resolved_workflow_id, 'issue_number': resolved_issue}
        metadata = getattr(workflow, 'metadata', {}) or {}
        provenance: list[dict[str, Any]] = []
        for kind, value in [('requester_nexus_id', metadata.get('requester_nexus_id')), ('requester_login', metadata.get('requester_login')), ('requested_by', metadata.get('requested_by')), ('issue_author', metadata.get('issue_author')), ('pr_author', metadata.get('pr_author')), ('comment_author', metadata.get('comment_author')), ('source_platform', metadata.get('source_platform')), ('active_agent', getattr(workflow, 'active_agent_type', None))]:
            info = _classify_identity(value, kind=kind)
            if info.get('name') is None:
                continue
            provenance.append({'source': kind, **info})

        bot_votes = sum(1 for item in provenance if item.get('classification') == 'bot')
        human_votes = sum(1 for item in provenance if item.get('classification') in {'human', 'likely_human'})
        overall = 'unknown'
        explicit_human_request = any(item.get('source') in {'requester_nexus_id', 'requester_login', 'requested_by', 'issue_author', 'pr_author'} and item.get('classification') in {'human', 'likely_human'} for item in provenance)
        if bot_votes > human_votes and not explicit_human_request:
            overall = 'bot_authored_or_bot_forwarded'
        elif human_votes > 0 and (human_votes >= bot_votes or explicit_human_request):
            overall = 'human_requested'
        elif provenance:
            overall = str(provenance[0].get('classification') or 'unknown')

        return {'ok': True, 'workflow': self._workflow_summary(workflow, issue_number=resolved_issue), 'authorship': {'classification': overall, 'provenance': provenance, 'human_signals': human_votes, 'bot_signals': bot_votes, 'note': 'Best-effort classification from workflow metadata/runtime identity only; secret values are never returned.'}}

    async def routing_validate(
        self,
        *,
        project_key: str,
        task_type: str = 'feature',
        workflow_id: str | None = None,
        issue_number: str | None = None,
        agent_name: str | None = None,
    ) -> dict[str, Any]:
        explained = await self.routing_explain(project_key=project_key, task_type=task_type, workflow_id=workflow_id, issue_number=issue_number, agent_name=agent_name)
        if not explained.get('ok'):
            return explained
        repos = list(explained.get('repos') or [])
        repo_roles = _expected_repo_roles(str(project_key), repos, str(task_type))
        validations: list[dict[str, Any]] = []
        default_branch = str(explained.get('default_branch') or '').strip() or None
        validations.append({'status': 'ok' if repos else 'error', 'check': 'repos_configured', 'message': f'{len(repos)} repo(s) configured' if repos else 'No repos are configured for this project.'})
        validations.append({'status': 'ok' if default_branch else 'warning', 'check': 'default_branch', 'message': f'Default branch is {default_branch}' if default_branch else 'No default branch configured; Nexus will typically fall back to main.'})
        if str(project_key) == 'nexus':
            expected = {'nexus-os', 'nexus-arc', 'nexus'}
            seen = {_repo_slug_name(repo) for repo in repos}
            missing = sorted(expected - seen)
            validations.append({'status': 'ok' if not missing else 'warning', 'check': 'nexus_repo_split', 'message': 'All expected nexus split repos are configured.' if not missing else f"Missing expected nexus split repo(s): {', '.join(missing)}"})
        active_role = next((item for item in repo_roles if item.get('task_type_match')), None)
        return {'ok': True, **explained, 'validation': {'checks': validations, 'repo_roles': repo_roles, 'recommended_repo': active_role.get('repo') if active_role else explained.get('default_repo'), 'provider_expectation': explained.get('resolved_profile', {}), 'note': 'Validation is best-effort and based on current PROJECT_CONFIG plus repo-name heuristics for the nexus multi-repo split.'}}

    async def continue_workflow(
        self,
        *,
        workflow_id: str | None = None,
        issue_number: str | None = None,
        target_agent: str | None = None,
    ) -> dict[str, Any]:
        plugin = self._workflow_plugin()
        workflow, resolved_issue, resolved_workflow_id = await self._workflow_by_ref(
            workflow_id=workflow_id,
            issue_number=issue_number,
        )
        if workflow is None:
            return {
                "ok": False,
                "error": "Workflow not found",
                "issue_number": resolved_issue,
                "workflow_id": resolved_workflow_id,
            }
        # Recover issue_number from workflow metadata when only workflow_id was given.
        if resolved_issue is None:
            resolved_issue = self._issue_from_metadata(workflow)
        if target_agent:
            if resolved_issue is None:
                return {
                    "ok": False,
                    "error": "Cannot reset workflow step: issue_number could not be resolved",
                    "workflow_id": resolved_workflow_id,
                }
            reset = getattr(plugin, "reset_to_agent_for_issue", None)
            if not callable(reset):
                return {"ok": False, "error": "Workflow plugin does not support reset_to_agent_for_issue"}
            success = await _maybe_await(reset(str(resolved_issue), str(target_agent)))
            if not success:
                return {
                    "ok": False,
                    "error": f"Could not reset workflow to agent '{target_agent}'",
                    "issue_number": resolved_issue,
                    "workflow_id": resolved_workflow_id,
                }
        return await self.workflow_status(workflow_id=resolved_workflow_id, issue_number=resolved_issue)

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
