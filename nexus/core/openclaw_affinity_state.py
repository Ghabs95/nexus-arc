"""Persistent workflow ↔ OpenClaw affinity and correlation state.

This first runtime slice keeps a small durable routing ledger under
``NEXUS_CORE_STORAGE_DIR`` so workflow-bound OpenClaw session affinity survives
restart/redeploy and so the latest correlation token can be recovered for
operator inspection and reply-routing follow-up work.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_STATE_DIRNAME = "openclaw"
_STATE_FILENAME = "affinity_state.json"


@dataclass
class OpenClawAffinityRecord:
    workflow_id: str = ""
    issue_number: str = ""
    project_key: str = ""
    session_key: str = ""
    correlation_token: str = ""
    binding_status: str = "active"
    binding_source: str = "deterministic"
    lifecycle_reason: str = ""
    last_event_type: str = ""
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    repaired_at: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any] | None) -> "OpenClawAffinityRecord":
        data = payload if isinstance(payload, dict) else {}
        history = data.get("history", [])
        return cls(
            workflow_id=str(data.get("workflow_id", "") or ""),
            issue_number=str(data.get("issue_number", "") or ""),
            project_key=str(data.get("project_key", "") or ""),
            session_key=str(data.get("session_key", "") or ""),
            correlation_token=str(data.get("correlation_token", "") or ""),
            binding_status=str(data.get("binding_status", "active") or "active"),
            binding_source=str(data.get("binding_source", "deterministic") or "deterministic"),
            lifecycle_reason=str(data.get("lifecycle_reason", "") or ""),
            last_event_type=str(data.get("last_event_type", "") or ""),
            updated_at=str(data.get("updated_at", "") or datetime.now(UTC).isoformat()),
            repaired_at=str(data.get("repaired_at", "") or ""),
            history=list(history) if isinstance(history, list) else [],
        )


def _default_base_dir() -> Path:
    runtime_dir = os.getenv("NEXUS_RUNTIME_DIR", "/var/lib/nexus")
    configured = os.getenv("NEXUS_CORE_STORAGE_DIR") or os.path.join(runtime_dir, "nexus-arc")
    return Path(configured)


class OpenClawAffinityStateStore:
    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir is not None else _default_base_dir()
        self.state_dir = self.base_dir / _STATE_DIRNAME
        self.state_file = self.state_dir / _STATE_FILENAME

    def _ensure_dir(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> dict[str, OpenClawAffinityRecord]:
        if not self.state_file.exists():
            return {}
        try:
            with open(self.state_file, encoding="utf-8") as handle:
                payload = json.load(handle)
        except Exception as exc:
            logger.warning("Failed to load OpenClaw affinity state: %s", exc)
            return {}
        workflows = payload.get("workflows", {}) if isinstance(payload, dict) else {}
        if not isinstance(workflows, dict):
            return {}
        return {
            str(workflow_id): OpenClawAffinityRecord.from_dict(record)
            for workflow_id, record in workflows.items()
        }

    def save_all(self, records: dict[str, OpenClawAffinityRecord]) -> None:
        self._ensure_dir()
        payload = {
            "version": 1,
            "updated_at": datetime.now(UTC).isoformat(),
            "workflows": {workflow_id: record.to_dict() for workflow_id, record in records.items()},
        }
        tmp_path = self.state_file.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
        os.replace(tmp_path, self.state_file)

    def get(self, workflow_id: str) -> OpenClawAffinityRecord | None:
        return self.load_all().get(str(workflow_id or ""))

    def upsert(self, record: OpenClawAffinityRecord) -> OpenClawAffinityRecord:
        workflow_id = str(record.workflow_id or "").strip()
        if not workflow_id:
            raise ValueError("workflow_id is required for OpenClaw affinity persistence")
        records = self.load_all()
        records[workflow_id] = record
        self.save_all(records)
        return record


def deterministic_session_key(
    workflow_id: str,
    project_key: str = "",
    *,
    issue_number: str = "",
) -> str:
    if str(workflow_id or "").strip():
        prefix = f"nexus:{project_key}:workflow" if str(project_key or "").strip() else "nexus:workflow"
        return f"{prefix}:{str(workflow_id).strip()}"
    if str(issue_number or "").strip():
        prefix = f"nexus:{project_key}:issue" if str(project_key or "").strip() else "nexus:issue"
        return f"{prefix}:{str(issue_number).strip()}"
    return ""


def resolve_affinity_binding(
    *,
    workflow_id: str,
    project_key: str = "",
    issue_number: str = "",
    configured_session_key: str = "",
    correlation_token: str = "",
    event_type: str = "",
    store: OpenClawAffinityStateStore | None = None,
) -> OpenClawAffinityRecord:
    workflow_id = str(workflow_id or "").strip()
    if not workflow_id:
        raise ValueError("workflow_id is required for OpenClaw affinity binding")

    store = store or OpenClawAffinityStateStore()
    existing = store.get(workflow_id)
    configured_session_key = str(configured_session_key or "").strip()
    project_key = str(project_key or "").strip()
    issue_number = str(issue_number or "").strip()
    correlation_token = str(correlation_token or "").strip()
    event_type = str(event_type or "").strip()

    derived_session_key = deterministic_session_key(
        workflow_id,
        project_key,
        issue_number=issue_number,
    )
    resolved_session_key = configured_session_key or (existing.session_key if existing else "") or derived_session_key

    binding_source = "configured" if configured_session_key else "persisted" if existing and existing.session_key else "deterministic"
    binding_status = "active"
    lifecycle_reason = ""
    repaired_at = existing.repaired_at if existing else ""
    history = list(existing.history) if existing else []

    if existing and existing.session_key and configured_session_key and existing.session_key != configured_session_key:
        binding_status = "drifted"
        lifecycle_reason = "configured_session_key_mismatch"
        repaired_at = datetime.now(UTC).isoformat()
        history.append(
            {
                "at": repaired_at,
                "reason": lifecycle_reason,
                "previous_session_key": existing.session_key,
                "new_session_key": configured_session_key,
            }
        )
    elif existing and not existing.session_key and resolved_session_key:
        binding_status = "repaired"
        lifecycle_reason = "missing_persisted_session_key"
        repaired_at = datetime.now(UTC).isoformat()
    elif not existing and resolved_session_key:
        binding_status = "created"
        lifecycle_reason = "initialized_from_runtime"

    resolved_correlation = (
        correlation_token
        or (existing.correlation_token if existing else "")
    )

    record = OpenClawAffinityRecord(
        workflow_id=workflow_id,
        issue_number=issue_number or (existing.issue_number if existing else ""),
        project_key=project_key or (existing.project_key if existing else ""),
        session_key=resolved_session_key,
        correlation_token=resolved_correlation,
        binding_status=binding_status,
        binding_source=binding_source,
        lifecycle_reason=lifecycle_reason,
        last_event_type=event_type or (existing.last_event_type if existing else ""),
        updated_at=datetime.now(UTC).isoformat(),
        repaired_at=repaired_at,
        history=history[-20:],
    )
    return store.upsert(record)


def scan_and_repair_affinity_state(
    *,
    workflow_mappings: dict[str, Any],
    store: OpenClawAffinityStateStore | None = None,
) -> list[OpenClawAffinityRecord]:
    store = store or OpenClawAffinityStateStore()
    repaired: list[OpenClawAffinityRecord] = []
    records = store.load_all()

    for issue_number, workflow_id in (workflow_mappings or {}).items():
        workflow_id = str(workflow_id or "").strip()
        issue_number = str(issue_number or "").strip()
        if not workflow_id:
            continue
        record = records.get(workflow_id)
        if record is None:
            repaired.append(
                resolve_affinity_binding(
                    workflow_id=workflow_id,
                    issue_number=issue_number,
                    store=store,
                    event_type="startup.reconcile",
                )
            )
            continue
        changed = False
        if not str(record.issue_number or "").strip() and issue_number:
            record.issue_number = issue_number
            record.binding_status = "repaired"
            record.lifecycle_reason = "missing_issue_number"
            record.repaired_at = datetime.now(UTC).isoformat()
            changed = True
        if not str(record.session_key or "").strip():
            record.session_key = deterministic_session_key(
                workflow_id,
                record.project_key,
                issue_number=issue_number,
            )
            record.binding_status = "repaired"
            record.lifecycle_reason = "missing_persisted_session_key"
            record.repaired_at = datetime.now(UTC).isoformat()
            changed = True
        if changed:
            record.updated_at = datetime.now(UTC).isoformat()
            repaired.append(store.upsert(record))
    return repaired
