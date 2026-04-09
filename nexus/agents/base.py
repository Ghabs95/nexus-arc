"""
nexus/agents/base.py — BaseAgent abstract class and core data types.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentOutput:
    """Output produced by a single agent run."""
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.content


@dataclass
class AgentContext:
    """
    Minimal context passed to each sub-agent.
    Always sliced — sub-agents receive only their task + prior outputs,
    never the full conversation history.
    """
    task: str
    prior_outputs: list[AgentOutput] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_task(self, task: str) -> "AgentContext":
        """Return a new context with a different task, keeping prior outputs."""
        return AgentContext(task=task, prior_outputs=self.prior_outputs.copy(), metadata=self.metadata.copy())

    def with_output(self, output: AgentOutput) -> "AgentContext":
        """Return a new context with output appended to prior_outputs."""
        return AgentContext(
            task=self.task,
            prior_outputs=self.prior_outputs + [output],
            metadata=self.metadata.copy(),
        )

    def prior_summary(self) -> str:
        """Compact string summary of all prior outputs for prompt injection."""
        if not self.prior_outputs:
            return ""
        parts = [f"[{i+1}] {o.content}" for i, o in enumerate(self.prior_outputs)]
        return "\n".join(parts)


class BaseAgent(ABC):
    """Abstract base class for all Nexus agents."""

    def __init__(self, name: str, description: str = "") -> None:
        self.name = name
        self.description = description
        self._parent: "BaseAgent | None" = None

    @abstractmethod
    async def run(self, context: AgentContext) -> AgentOutput:
        """Execute the agent with the given context and return an output."""
        ...

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}(name={self.name!r})"
