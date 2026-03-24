"""AI provider adapters."""

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.adapters.ai.claude_provider import ClaudeProvider
from nexus.adapters.ai.codex_provider import CodexCLIProvider
from nexus.adapters.ai.copilot_provider import CopilotCLIProvider
from nexus.adapters.ai.gemini_provider import GeminiCLIProvider
from nexus.adapters.ai.openai_provider import OpenAIProvider
from nexus.adapters.ai.registry import AgentRegistry

__all__ = [
    "AIProvider",
    "ClaudeProvider",
    "ExecutionContext",
    "CodexCLIProvider",
    "CopilotCLIProvider",
    "GeminiCLIProvider",
    "OpenAIProvider",
    "AgentRegistry",
]
