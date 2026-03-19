"""CodexCLI AI provider implementation."""

import asyncio
import logging
import os
import shutil
import subprocess
import time
from pathlib import Path

from nexus.adapters.ai.base import AIProvider, ExecutionContext
from nexus.core.models import AgentResult, RateLimitStatus

logger = logging.getLogger(__name__)

# Task types where Codex CLI excels
_CODEX_PREFERRED_TASKS = {"code_generation", "code_review", "refactoring", "debugging"}


class CodexCLIProvider(AIProvider):
    """AI provider that delegates to the Codex CLI (`codex` binary)."""

    def __init__(
        self,
        timeout: int = 600,
        binary: str = "codex",
        model: str | None = None,
        extra_args: list[str] | None = None,
    ):
        self._timeout = timeout
        self._binary = binary
        self._model = model
        self._extra_args = extra_args or []
        self._availability_cache: dict = {}
        self._availability_ttl: int = 300  # seconds

    @property
    def name(self) -> str:
        return "codex"

    async def check_availability(self) -> bool:
        """Return True if the codex CLI binary is installed and runnable."""
        now = time.time()
        cached = self._availability_cache.get("codex")
        if cached and now - cached["at"] < self._availability_ttl:
            return cached["available"]

        available = bool(shutil.which(self._binary))
        if available:
            try:
                result = subprocess.run(
                    [self._binary, "--version"],
                    capture_output=True,
                    timeout=10,
                )
                available = result.returncode == 0
            except Exception:
                available = False

        self._availability_cache["codex"] = {"available": available, "at": now}
        return available

    async def get_rate_limit_status(self) -> RateLimitStatus:
        """Codex CLI has no programmatic rate-limit endpoint; report unlimited."""
        return RateLimitStatus(
            provider=self.name,
            is_limited=False,
        )

    def get_preference_score(self, task_type: str) -> float:
        """Return 0.9 for coding tasks, 0.65 otherwise."""
        return 0.9 if task_type in _CODEX_PREFERRED_TASKS else 0.65

    async def execute_agent(self, context: ExecutionContext) -> AgentResult:
        """Run the agent prompt through the `codex` CLI."""
        start = time.time()
        workspace = Path(context.workspace)
        workspace.mkdir(parents=True, exist_ok=True)
        self._cleanup_empty_rollout_files()

        cmd = [self._binary, "exec", "--skip-git-repo-check"]
        if self._model:
            cmd.extend(["--model", self._model])
        cmd.extend(self._extra_args)
        cmd.append(context.prompt)

        try:
            process = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(workspace),
            )
            stdout, stderr = await asyncio.wait_for(
                process.communicate(),
                timeout=context.timeout or self._timeout,
            )
            elapsed = time.time() - start
            if process.returncode == 0:
                return AgentResult(
                    success=True,
                    output=stdout.decode(errors="replace"),
                    execution_time=elapsed,
                    provider_used=self.name,
                )
            return AgentResult(
                success=False,
                output=stdout.decode(errors="replace"),
                error=stderr.decode(errors="replace"),
                execution_time=elapsed,
                provider_used=self.name,
            )
        except TimeoutError:
            return AgentResult(
                success=False,
                output="",
                error=f"Timeout after {context.timeout or self._timeout}s",
                execution_time=time.time() - start,
                provider_used=self.name,
            )
        except Exception as exc:
            return AgentResult(
                success=False,
                output="",
                error=str(exc),
                execution_time=time.time() - start,
                provider_used=self.name,
            )

    def _cleanup_empty_rollout_files(self) -> int:
        """Best-effort cleanup for zero-byte Codex rollout files."""
        home = str(os.getenv("CODEX_HOME") or os.path.expanduser("~/.codex")).strip()
        if not home:
            return 0
        sessions_dir = Path(home) / "sessions"
        if not sessions_dir.is_dir():
            return 0

        removed = 0
        now = time.time()
        for path in sessions_dir.rglob("rollout-*.jsonl"):
            try:
                if not path.is_file():
                    continue
                stat = path.stat()
                if stat.st_size != 0:
                    continue
                if (now - stat.st_mtime) < 120:
                    continue
                path.unlink()
                removed += 1
            except Exception:
                continue

        if removed:
            logger.warning("Removed %s empty Codex rollout file(s) from %s", removed, sessions_dir)
        return removed
