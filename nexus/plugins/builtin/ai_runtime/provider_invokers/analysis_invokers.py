import subprocess
from typing import Any, Callable

from nexus.plugins.builtin.ai_runtime.provider_invokers.agent_invokers import (
    prepare_provider_cli_env,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.codex_invoker import (
    _build_codex_exec_cmd,
)
from nexus.plugins.builtin.ai_runtime.provider_invokers.subprocess_utils import (
    run_cli_prompt,
    wrap_timeout_error,
)


def run_gemini_analysis_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    gemini_provider: Any,
    gemini_cli_path: str,
    gemini_model: str,
    build_analysis_prompt: Callable[..., str],
    parse_analysis_result: Callable[[str, str], dict[str, Any]],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    text: str,
    task: str,
    timeout: int,
    kwargs: dict[str, Any],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if not check_tool_available(gemini_provider):
        raise tool_unavailable_error("Gemini CLI not available")

    provider_env, _auth_mode = prepare_provider_cli_env(provider="gemini", env=env)
    prompt = build_analysis_prompt(text, task, **kwargs)
    try:
        cmd = [gemini_cli_path, "-p", prompt]
        if gemini_model:
            cmd.extend(["--model", gemini_model])
        result = run_cli_prompt(cmd, timeout=timeout, env=provider_env, cwd=cwd)
        if result.returncode != 0:
            stderr = result.stderr or ""
            if "rate limit" in stderr.lower() or "quota" in stderr.lower():
                raise rate_limited_error(f"Gemini rate-limited: {stderr}")
            raise Exception(f"Gemini error: {stderr}")
        return parse_analysis_result(result.stdout or "", task)
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="Gemini", timeout=timeout) from exc


def run_copilot_analysis_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    copilot_provider: Any,
    copilot_cli_path: str,
    copilot_model: str,
    copilot_supports_model: bool,
    build_analysis_prompt: Callable[..., str],
    parse_analysis_result: Callable[[str, str], dict[str, Any]],
    tool_unavailable_error: type[Exception],
    text: str,
    task: str,
    timeout: int,
    kwargs: dict[str, Any],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if not check_tool_available(copilot_provider):
        raise tool_unavailable_error("Copilot CLI not available")

    prompt = build_analysis_prompt(text, task, **kwargs)
    try:
        cmd = [copilot_cli_path, "-p", prompt]
        if copilot_model and copilot_supports_model:
            cmd.extend(["--model", copilot_model])
        result = run_cli_prompt(cmd, timeout=timeout, env=env, cwd=cwd)
        if result.returncode != 0:
            raise Exception(f"Copilot error: {result.stderr}")
        return parse_analysis_result(result.stdout or "", task)
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="Copilot", timeout=timeout) from exc


def run_codex_analysis_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    codex_provider: Any,
    codex_cli_path: str,
    codex_model: str,
    build_analysis_prompt: Callable[..., str],
    parse_analysis_result: Callable[[str, str], dict[str, Any]],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    text: str,
    task: str,
    timeout: int,
    kwargs: dict[str, Any],
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if not check_tool_available(codex_provider):
        raise tool_unavailable_error("Codex CLI not available")

    provider_env, _auth_mode = prepare_provider_cli_env(provider="codex", env=env)

    prompt = build_analysis_prompt(text, task, **kwargs)
    cmd = _build_codex_exec_cmd(
        codex_cli_path=codex_cli_path,
        codex_model=codex_model,
        agent_prompt=prompt,
    )

    try:
        result = run_cli_prompt(cmd, timeout=timeout, env=provider_env, cwd=cwd)
        if result.returncode != 0:
            stderr = result.stderr or ""
            stdout = result.stdout or ""
            combined = f"{stderr}\n{stdout}".lower()
            if "rate limit" in combined or "quota" in combined:
                raise rate_limited_error(f"Codex rate-limited: {stderr or stdout}")
            raise Exception(f"Codex error: {stderr or stdout}")
        return parse_analysis_result(result.stdout or "", task)
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="Codex", timeout=timeout) from exc


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
    env: dict[str, str] | None = None,
    cwd: str | None = None,
) -> dict[str, Any]:
    if not check_tool_available(claude_provider):
        raise tool_unavailable_error("Claude CLI not available")

    provider_env, _auth_mode = prepare_provider_cli_env(provider="claude", env=env)
    prompt = build_analysis_prompt(text, task, **kwargs)
    cmd = [claude_cli_path, "exec"]
    if claude_model:
        cmd.extend(["--model", claude_model])
    cmd.append(prompt)

    try:
        result = run_cli_prompt(cmd, timeout=timeout, env=provider_env, cwd=cwd)
        if result.returncode != 0:
            stderr = result.stderr or ""
            stdout = result.stdout or ""
            combined = f"{stderr}\n{stdout}".lower()
            if "rate limit" in combined or "quota" in combined:
                raise rate_limited_error(f"Claude rate-limited: {stderr or stdout}")
            raise Exception(f"Claude error: {stderr or stdout}")
        return parse_analysis_result(result.stdout or "", task)
    except subprocess.TimeoutExpired as exc:
        raise wrap_timeout_error(exc, provider_name="Claude", timeout=timeout) from exc
