"""Explicit runtime state container for inbox processor mutable globals."""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ProcessorRuntimeState:
    """Mutable runtime state previously stored as module globals."""

    alerted_agents: set[Any] = field(default_factory=set)
    notified_comments: set[Any] = field(default_factory=set)
    auto_chained_agents: dict[str, Any] = field(default_factory=dict)
    polling_failure_counts: dict[str, int] = field(default_factory=dict)
    orphan_recovery_last_attempt: dict[str, float] = field(default_factory=dict)
