"""Analytics data models shared by analytics adapters/services."""

from dataclasses import dataclass, field


@dataclass
class AgentMetrics:
    """Performance metrics for a specific agent."""

    agent_name: str
    launches: int = 0
    timeouts: int = 0
    retries: int = 0
    failures: int = 0
    successes: int = 0
    avg_duration_seconds: float | None = None


@dataclass
class SystemMetrics:
    """Overall system performance metrics."""

    total_workflows: int = 0
    completed_workflows: int = 0
    active_workflows: int = 0
    failed_workflows: int = 0
    total_timeouts: int = 0
    total_retries: int = 0
    completion_rate: float = 0.0
    avg_workflow_duration_hours: float | None = None
    issues_per_tier: dict[str, int] = field(default_factory=dict)


__all__ = ["AgentMetrics", "SystemMetrics"]
