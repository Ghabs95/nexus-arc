"""Tests for Coordinator agent."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from nexus.agents.base import AgentContext, AgentOutput, BaseAgent
from nexus.agents.coordinator import Coordinator, _call_nexus_router

# ── Helpers ──────────────────────────────────────────────────────────────────


class FixedAgent(BaseAgent):
    """Simple sub-agent that returns a fixed response."""

    def __init__(self, name: str, description: str, response: str):
        super().__init__(name=name, description=description)
        self.response = response
        self.ran = False

    async def run(self, context: AgentContext) -> AgentOutput:
        self.ran = True
        return AgentOutput(content=self.response, metadata={"agent": self.name})


def make_mock_provider(delegation_response: str = "CodeReviewer"):
    """Return a mock AIProvider that returns a fixed delegation choice."""
    provider = MagicMock()
    result = MagicMock()
    result.success = True
    result.output = delegation_response
    provider.execute_agent = AsyncMock(return_value=result)
    return provider


# ── Tests ─────────────────────────────────────────────────────────────────────


def test_coordinator_delegates_to_correct_agent():
    reviewer = FixedAgent("CodeReviewer", "Reviews code", "LGTM")
    writer = FixedAgent("ContentWriter", "Writes content", "Here is your content")
    provider = make_mock_provider(delegation_response="CodeReviewer")

    coord = Coordinator("coord", [reviewer, writer], provider)
    ctx = AgentContext(task="Review this PR")
    result = asyncio.run(coord.run(ctx))

    assert reviewer.ran is True
    assert writer.ran is False
    assert result.content == "LGTM"
    assert result.metadata["coordinator_selected_agent"] == "CodeReviewer"


def test_coordinator_fallback_on_unknown_agent():
    """If LLM returns unknown agent name, coordinator falls back to first sub-agent."""
    a = FixedAgent("Alpha", "First agent", "alpha response")
    b = FixedAgent("Beta", "Second agent", "beta response")
    provider = make_mock_provider(delegation_response="NonExistentAgent")

    coord = Coordinator("coord", [a, b], provider)
    ctx = AgentContext(task="do something")
    result = asyncio.run(coord.run(ctx))

    assert a.ran is True
    assert result.content == "alpha response"


def test_coordinator_fallback_on_provider_failure():
    """If LLM call fails, coordinator falls back to first sub-agent."""
    a = FixedAgent("Alpha", "First", "alpha")
    provider = MagicMock()
    provider.execute_agent = AsyncMock(side_effect=Exception("LLM failed"))

    coord = Coordinator("coord", [a], provider)
    ctx = AgentContext(task="task")
    result = asyncio.run(coord.run(ctx))

    assert a.ran is True
    assert result.content == "alpha"


def test_coordinator_requires_sub_agents():
    provider = make_mock_provider()
    with pytest.raises(ValueError, match="at least one sub-agent"):
        Coordinator("empty", [], provider)


def test_coordinator_calls_nexus_router():
    """Coordinator should attempt to call nexus-router for model selection."""
    a = FixedAgent("Alpha", "First", "result")
    provider = make_mock_provider("Alpha")

    with patch("nexus.agents.coordinator._call_nexus_router") as mock_router:
        mock_router.return_value = "claude-sonnet"
        coord = Coordinator("coord", [a], provider)
        ctx = AgentContext(task="test")
        result = asyncio.run(coord.run(ctx))

    mock_router.assert_called_once()
    assert result.metadata["coordinator_model"] == "claude-sonnet"


def test_coordinator_works_without_nexus_router():
    """Coordinator must work when nexus-router is unavailable."""
    a = FixedAgent("Alpha", "First", "result")
    provider = make_mock_provider("Alpha")

    with patch("nexus.agents.coordinator._call_nexus_router") as mock_router:
        mock_router.return_value = None  # router unavailable
        coord = Coordinator("coord", [a], provider)
        ctx = AgentContext(task="test")
        result = asyncio.run(coord.run(ctx))

    assert result.content == "result"
    assert result.metadata["coordinator_model"] is None


def test_call_nexus_router_unavailable():
    """_call_nexus_router should return None if router is unreachable."""
    result = _call_nexus_router("http://127.0.0.1:19999", "task")
    assert result is None


def test_coordinator_case_insensitive_name_match():
    """Agent name matching should be case-insensitive."""
    a = FixedAgent("CodeReviewer", "Reviewer", "reviewed")
    provider = make_mock_provider(delegation_response="codereviewer")

    coord = Coordinator("coord", [a], provider)
    ctx = AgentContext(task="review")
    result = asyncio.run(coord.run(ctx))

    assert a.ran is True
    assert result.content == "reviewed"
