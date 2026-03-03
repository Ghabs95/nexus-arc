"""Protocol for persistent workflow state storage.

Covers two concerns that belong in nexus-arc:
1. Issue → Workflow ID mapping
2. Approval-gate state for workflow steps
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

logger = logging.getLogger(__name__)


@runtime_checkable
class WorkflowStateStore(Protocol):
    """Pluggable store for workflow-related persistent state."""

    # ── Workflow mapping ────────────────────────────────────────────

    def map_issue(self, issue_num: str, workflow_id: str) -> None:
        """Map an issue number to a workflow ID."""
        ...

    def get_workflow_id(self, issue_num: str) -> str | None:
        """Return the workflow ID for *issue_num*, or ``None``."""
        ...

    def remove_mapping(self, issue_num: str) -> None:
        """Remove the mapping for *issue_num*."""
        ...

    def load_all_mappings(self) -> dict[str, str]:
        """Return the full ``{issue_num: workflow_id}`` dict."""
        ...

    # ── Approval gate ───────────────────────────────────────────────

    def set_pending_approval(
        self,
        issue_num: str,
        step_num: int,
        step_name: str,
        approvers: list[str],
        approval_timeout: int,
    ) -> None:
        """Record that a workflow step is waiting for approval."""
        ...

    def clear_pending_approval(self, issue_num: str) -> None:
        """Remove the approval gate record once resolved."""
        ...

    def get_pending_approval(self, issue_num: str) -> dict | None:
        """Return pending approval info, or ``None``."""
        ...

    def load_all_approvals(self) -> dict[str, dict]:
        """Return the full pending-approval dict."""
        ...
