"""
nexus/agents/__init__.py — Public API for the nexus.agents module.
"""
from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.context import merge_outputs, slice_context, summarise_outputs
from nexus.agents.coordinator import Coordinator, LLMSubAgent
from nexus.agents.loop import LoopAgent
from nexus.agents.parallel import ParallelAgent
from nexus.agents.sequential import SequentialAgent

__all__ = [
    # Core types
    "AgentContext",
    "AgentOutput",
    "BaseAgent",
    # Context utilities
    "slice_context",
    "summarise_outputs",
    "merge_outputs",
    # Composition primitives
    "SequentialAgent",
    "ParallelAgent",
    "LoopAgent",
    "Coordinator",
    "LLMSubAgent",
]
