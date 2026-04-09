"""Integration tests for POST /api/v1/agents/run bridge endpoint."""
from __future__ import annotations

import asyncio

from nexus.core.command_bridge.agents_handler import handle_agents_run

AGENTS = [
    {"name": "Summariser", "description": "Summarises text", "response": "Summary done"},
    {"name": "Reviewer", "description": "Reviews output", "response": "Review OK"},
]


def test_sequential_run():
    result = asyncio.run(handle_agents_run({
        "task": "Summarise this document",
        "agent_type": "sequential",
        "agents": AGENTS,
    }))
    assert result["ok"] is True
    assert "Review OK" in result["output"]
    assert "metadata" in result


def test_parallel_run():
    result = asyncio.run(handle_agents_run({
        "task": "Analyse in parallel",
        "agent_type": "parallel",
        "agents": AGENTS,
    }))
    assert result["ok"] is True
    assert "Summary done" in result["output"]
    assert "Review OK" in result["output"]


def test_loop_run():
    result = asyncio.run(handle_agents_run({
        "task": "Keep trying",
        "agent_type": "loop",
        "agents": [{"name": "Worker", "description": "Does work", "response": "done"}],
        "max_iterations": 3,
        "stop_condition": "True",  # stops immediately on first iteration
    }))
    assert result["ok"] is True
    assert result["metadata"]["loop_iterations"] == 1


def test_loop_requires_single_agent():
    result = asyncio.run(handle_agents_run({
        "task": "loop",
        "agent_type": "loop",
        "agents": AGENTS,  # two agents — should fail
    }))
    assert result["ok"] is False
    assert "exactly one" in result["error"]


def test_missing_task():
    result = asyncio.run(handle_agents_run({"agent_type": "sequential", "agents": AGENTS}))
    assert result["ok"] is False
    assert "task is required" in result["error"]


def test_missing_agents():
    result = asyncio.run(handle_agents_run({"task": "do something", "agent_type": "sequential"}))
    assert result["ok"] is False
    assert "agents list" in result["error"]


def test_unknown_agent_type():
    result = asyncio.run(handle_agents_run({
        "task": "task",
        "agent_type": "invalid_type",
        "agents": AGENTS,
    }))
    assert result["ok"] is False
    assert "unknown agent_type" in result["error"]


def test_coordinator_without_provider():
    result = asyncio.run(handle_agents_run({
        "task": "coordinate",
        "agent_type": "coordinator",
        "agents": AGENTS,
    }))
    assert result["ok"] is False
    assert "AI provider" in result["error"]
