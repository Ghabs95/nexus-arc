"""
nexus/agents/context.py — Context slicing and output summarisation utilities.
"""
from __future__ import annotations

from .base import AgentContext, AgentOutput


def slice_context(context: AgentContext, task: str | None = None) -> AgentContext:
    """
    Return a minimal context slice for a sub-agent.
    Replaces the task if provided; always copies prior_outputs.
    Never passes raw conversation history — only the explicit task + prior outputs.
    """
    return AgentContext(
        task=task or context.task,
        prior_outputs=context.prior_outputs.copy(),
        metadata=context.metadata.copy(),
    )


def summarise_outputs(outputs: list[AgentOutput], max_chars: int = 2000) -> str:
    """
    Summarise a list of agent outputs into a compact string for prompt injection.
    Truncates if total length exceeds max_chars to control token usage.
    """
    if not outputs:
        return ""
    parts = []
    total = 0
    for i, output in enumerate(outputs):
        entry = f"[Agent {i+1}]: {output.content}"
        if total + len(entry) > max_chars:
            parts.append(f"[Agent {i+1}]: <truncated>")
            break
        parts.append(entry)
        total += len(entry)
    return "\n".join(parts)


def merge_outputs(outputs: list[AgentOutput], separator: str = "\n\n") -> AgentOutput:
    """
    Merge multiple agent outputs into a single AgentOutput by concatenation.
    Used by ParallelAgent's default merge strategy.
    """
    merged_content = separator.join(o.content for o in outputs)
    merged_metadata: dict = {}
    for o in outputs:
        merged_metadata.update(o.metadata)
    return AgentOutput(content=merged_content, metadata=merged_metadata)
