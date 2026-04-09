"""
nexus/agents/coordinator.py — Coordinator agent with LLM-driven delegation and nexus-router model selection.
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any
from urllib.error import URLError
from urllib.request import Request, urlopen

from .base import AgentContext, AgentOutput, BaseAgent
from .context import slice_context

if TYPE_CHECKING:
    pass  # AIProvider imported lazily to avoid nexus.plugins bootstrap

logger = logging.getLogger(__name__)


def _call_nexus_router(router_url: str, task: str, task_type: str = "general_chat") -> str | None:
    """
    Call nexus-router POST /route to get the recommended model for a task.
    Returns model name string or None if router is unavailable.
    """
    try:
        payload = json.dumps({"message": task, "task_type": task_type}).encode()
        req = Request(
            f"{router_url.rstrip('/')}/route",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(req, timeout=3) as resp:
            data = json.loads(resp.read())
            return data.get("model") or data.get("provider_model")
    except (URLError, OSError, json.JSONDecodeError, KeyError) as exc:
        logger.debug("nexus-router unavailable (%s) — using sub-agent default model", exc)
        return None


class LLMSubAgent(BaseAgent):
    """
    A sub-agent that delegates execution to an AIProvider.
    The AIProvider is passed in at construction time; imports are lazy
    to avoid triggering the full nexus plugin bootstrap at import time.
    """

    def __init__(
        self,
        name: str,
        description: str,
        ai_provider: Any,
        workspace_path: str = "/tmp",
        model_override: str | None = None,
    ) -> None:
        super().__init__(name=name, description=description)
        self.ai_provider = ai_provider
        self.workspace_path = workspace_path
        self.model_override = model_override

    async def run(self, context: AgentContext) -> AgentOutput:
        from pathlib import Path

        from nexus.adapters.ai.base import ExecutionContext

        prior = context.prior_summary()
        prompt = context.task
        if prior:
            prompt = f"Previous context:\n{prior}\n\nYour task:\n{context.task}"

        exec_ctx = ExecutionContext(
            agent_name=self.name,
            prompt=prompt,
            workspace=Path(self.workspace_path),
            metadata=context.metadata,
            model_override=self.model_override,
        )
        result = await self.ai_provider.execute_agent(exec_ctx)
        content = result.output if result.success else f"[{self.name} failed]: {result.output}"
        return AgentOutput(
            content=content,
            metadata={"success": result.success, "agent": self.name},
        )


class Coordinator(BaseAgent):
    """
    Coordinator agent with LLM-driven delegation.

    Given a task and a list of sub-agents (each with name + description),
    the Coordinator:
    1. Uses its own LLM call to decide which sub-agent handles the task
    2. Calls nexus-router to pick the best model for the chosen sub-task
    3. Runs the selected sub-agent with a sliced context
    4. Returns the sub-agent's output

    Falls back gracefully if nexus-router is unavailable or LLM delegation fails.
    """

    def __init__(
        self,
        name: str,
        sub_agents: list[BaseAgent],
        ai_provider: Any,
        router_url: str = "http://127.0.0.1:7771",
        workspace_path: str = "/tmp",
        description: str = "",
    ) -> None:
        super().__init__(name=name, description=description or "Coordinator that delegates tasks to sub-agents")
        if not sub_agents:
            raise ValueError("Coordinator requires at least one sub-agent")
        self.sub_agents = sub_agents
        self.ai_provider = ai_provider
        self.router_url = router_url
        self.workspace_path = workspace_path
        for agent in sub_agents:
            agent._parent = self

    def _build_delegation_prompt(self, context: AgentContext) -> str:
        agent_list = "\n".join(
            f"- {a.name}: {a.description}" for a in self.sub_agents
        )
        prior = context.prior_summary()
        prior_section = f"\nPrevious context:\n{prior}\n" if prior else ""
        return (
            f"You are a coordinator. Given the following task and available agents, "
            f"respond with ONLY the name of the most suitable agent to handle the task. "
            f"Do not explain — output just the agent name.\n"
            f"{prior_section}"
            f"\nAvailable agents:\n{agent_list}"
            f"\n\nTask: {context.task}"
            f"\n\nAgent name:"
        )

    async def _select_agent(self, context: AgentContext) -> BaseAgent:
        """Use LLM to select the best sub-agent for the task."""
        from pathlib import Path

        from nexus.adapters.ai.base import ExecutionContext

        prompt = self._build_delegation_prompt(context)
        exec_ctx = ExecutionContext(
            agent_name=self.name,
            prompt=prompt,
            workspace=Path(self.workspace_path),
            metadata={"coordinator": True},
            max_tokens=32,
        )
        try:
            result = await self.ai_provider.execute_agent(exec_ctx)
            chosen_name = result.output.strip().strip('"').strip("'").split("\n")[0]
            for agent in self.sub_agents:
                if agent.name.lower() == chosen_name.lower():
                    return agent
            logger.warning(
                "Coordinator chose unknown agent %r — falling back to first sub-agent", chosen_name
            )
        except Exception as exc:
            logger.warning("Coordinator LLM delegation failed (%s) — falling back to first sub-agent", exc)

        return self.sub_agents[0]

    async def run(self, context: AgentContext) -> AgentOutput:
        # 1. LLM-driven agent selection
        selected = await self._select_agent(context)
        logger.info("Coordinator selected agent: %s", selected.name)

        # 2. Ask nexus-router for the best model for this sub-task
        model = _call_nexus_router(self.router_url, task=context.task)
        if model and isinstance(selected, LLMSubAgent):
            selected.model_override = model
            logger.info("nexus-router selected model: %s for agent: %s", model, selected.name)

        # 3. Run selected agent with sliced context
        sliced = slice_context(context)
        output = await selected.run(sliced)
        output.metadata["coordinator_selected_agent"] = selected.name
        output.metadata["coordinator_model"] = model
        return output
