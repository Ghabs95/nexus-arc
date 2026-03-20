import subprocess
import time
from collections.abc import Mapping
from typing import Any, Callable

from nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers import (
    _prepare_log_path,
    _launch_process_with_log,
    merge_execution_mode_env,
    prepare_provider_cli_env,
    resolve_execution_mode_cli_args,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.subprocess_utils import (
    run_cli_prompt,
    wrap_timeout_error,
)


def invoke_claude_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    claude_provider: Any,
    claude_cli_path: str,
    claude_model: str,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    logger: Any,
    agent_prompt: str,
    workspace_dir: str,
    agents_dir: str,
    issue_num: str | None = None,
    log_subdir: str | None = None,
    env: dict[str, str] | None = None,
    execution_mode: str | None = None,
    execution_mode_config: Mapping[str, Any] | None = None,
) -> int | None:
    if not check_tool_available(claude_provider):
        raise tool_unavailable_error("Claude CLI not available")

    provider_env, auth_mode = prepare_provider_cli_env(
        provider="claude",
        env=merge_execution_mode_env(
            provider="claude",
            env=env,
            execution_mode=execution_mode,
            execution_mode_config=execution_mode_config,
        ),
        logger=logger,
    )

    cmd = [claude_cli_path, "-p", agent_prompt]
    cmd.extend(resolve_execution_mode_cli_args(execution_mode_config))
    # Note: Anthropic CLI might have specific flags for models if needed.
    # Currently omitting --model as Claude Code doesn't typically require it in simple prompt mode.

    log_path = _prepare_log_path(
        prefix="claude",
        workspace_dir=workspace_dir,
        issue_num=issue_num,
        log_subdir=log_subdir,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )

    logger.info("🤖 Launching Claude CLI agent")
    logger.info("   Auth mode: %s", auth_mode)
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=provider_env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 Claude launched (PID: %s)",
            output_label="claude",
        )

        # Simple startup probe (similar to Gemini/Copilot)
        exit_code = None
        deadline = time.time() + 3.0
        while time.time() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.2)

        if exit_code is not None:
            raise tool_unavailable_error(f"Claude exited immediately (exit={exit_code})")

        return process.pid
    except Exception as exc:
        logger.error("❌ Claude launch failed: %s", exc)
        raise


def run_claude_analysis_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    claude_provider: Any,
    claude_cli_path: str,
    claude_model: str,
    build_analysis_prompt: Callable[..., str],
    parse_analysis_result: Callable[[str, str], dict[str, Any]],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    text: str,
    task: str,
    timeout: int,
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    if not check_tool_available(claude_provider):
        raise tool_unavailable_error("Claude CLI not available")

    prompt = build_analysis_prompt(text, task, **kwargs)
    try:
        cmd = [claude_cli_path, "-p", prompt]
        result = run_cli_prompt(cmd, timeout=timeout)
        if result.returncode != 0:
            stderr = result.stderr or ""
            stdout = result.stdout or ""
            combined = f"{stderr}\n{stdout}".lower()
            if "rate limit" in combined or "quota" in combined or "429" in combined:
                raise rate_limited_error(f"Claude rate-limited: {stderr or stdout}")
            raise Exception(f"Claude error: {stderr or stdout}")
        return parse_analysis_result(result.stdout or "", task)
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="Claude", timeout=timeout) from exc
