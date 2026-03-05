"""Unified Audit Store for Nexus Core.

Provides a centralized way to read and write audit events using the configured
storage backend.
"""

import logging
from datetime import datetime, UTC
from typing import Any

from nexus.adapters.storage.base import StorageBackend
from nexus.core.models import AuditEvent

logger = logging.getLogger(__name__)


class WorkflowAuditStore:
    """Storage-level audit store bound to a StorageBackend."""

    def __init__(self, storage: StorageBackend):
        """
        Initialize AuditStore.

        Args:
            storage: Conforming StorageBackend implementation.
        """
        self.storage = storage

    async def log(
        self,
        workflow_id: str,
        event_type: str,
        data: dict[str, Any] | None = None,
        user_id: str | None = None,
    ) -> None:
        """Log an audit event."""
        event = AuditEvent(
            workflow_id=workflow_id,
            timestamp=datetime.now(UTC),
            event_type=event_type,
            data=data or {},
            user_id=user_id,
        )
        await self.storage.append_audit_event(event)

    async def get_workflow_history(self, workflow_id: str, limit: int = 100) -> list[AuditEvent]:
        """Get audit history for a specific workflow."""
        return await self.storage.get_audit_log(workflow_id)


# Backward compatibility alias for existing imports.
AuditStore = WorkflowAuditStore
