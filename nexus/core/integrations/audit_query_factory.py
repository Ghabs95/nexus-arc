"""Audit query factory — returns the correct AuditQueryProvider.

Selects Loki when ``LOKI_URL`` is configured, otherwise falls back to
file-based scanning of local JSONL audit files.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nexus.core.config import NEXUS_CORE_STORAGE_DIR

if TYPE_CHECKING:
    from nexus.core.audit_query import AuditQueryProvider

logger = logging.getLogger(__name__)

_instance: AuditQueryProvider | None = None


def get_audit_query() -> AuditQueryProvider:
    """Return the singleton AuditQueryProvider."""
    global _instance
    if _instance is not None:
        return _instance

    # Try Loki first — production path
    try:
        from nexus.core.config import LOKI_URL  # noqa: WPS433

        if LOKI_URL:
            from nexus.adapters.analytics.loki import LokiAnalyticsAdapter

            _instance = LokiAnalyticsAdapter(loki_url=LOKI_URL)
            logger.info("AuditQueryProvider: using Loki at %s", LOKI_URL)
            return _instance
    except (ImportError, AttributeError):
        pass

    # Fallback — local JSONL files
    from pathlib import Path

    from nexus.adapters.analytics.file_audit_query import FileAuditQueryProvider

    audit_dir = Path(NEXUS_CORE_STORAGE_DIR) / "audit"
    _instance = FileAuditQueryProvider(audit_dir=audit_dir)
    logger.info("AuditQueryProvider: using file-based fallback at %s", audit_dir)
    return _instance
