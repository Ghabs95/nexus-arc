import os
import shlex
import subprocess
import tempfile
import threading
import time
from collections.abc import Mapping
from typing import Any, Callable, TextIO

_BLOCKED_GIT_CLI_BIN_DIR: str | None = None
_CLI_AUTH_MODES = {"account"}
_PROVIDER_API_KEY_ENV: dict[str, tuple[str, ...]] = {
    "codex": ("OPENAI_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "claude": ("ANTHROPIC_API_KEY",),
}


def merge_execution_mode_env(
    *,
    provider: str,
    env: dict[str, str] | None = None,
    execution_mode: str | None = None,
    execution_mode_config: Mapping[str, Any] | None = None,
) -> dict[str, str]:
    merged_env = dict(env or {})
    normalized_mode = str(execution_mode or "").strip().lower()
    if normalized_mode:
        merged_env["NEXUS_EXECUTION_MODE"] = normalized_mode
        merged_env[f"NEXUS_{str(provider or '').strip().upper()}_EXECUTION_MODE"] = normalized_mode

    if isinstance(execution_mode_config, Mapping):
        env_overrides = execution_mode_config.get("env")
        if isinstance(env_overrides, Mapping):
            for key, value in env_overrides.items():
                key_text = str(key or "").strip()
                if not key_text:
                    continue
                merged_env[key_text] = str(value or "")
    return merged_env


def resolve_execution_mode_cli_args(
    execution_mode_config: Mapping[str, Any] | None,
) -> list[str]:
    if not isinstance(execution_mode_config, Mapping):
        return []
    raw_args = execution_mode_config.get("args")
    if not isinstance(raw_args, list):
        return []
    args: list[str] = []
    for value in raw_args:
        text = str(value or "").strip()
        if text:
            args.append(text)
    return args


def resolve_cli_auth_mode(env: Mapping[str, Any] | None = None) -> str:
    raw = str((env or {}).get("NEXUS_CLI_AUTH_MODE") or os.getenv("NEXUS_CLI_AUTH_MODE", "account"))
    normalized = raw.strip().lower()
    if normalized in _CLI_AUTH_MODES:
        return normalized
    if not normalized:
        return "account"
    raise ValueError(
        "NEXUS_CLI_AUTH_MODE supports only 'account' in this build. "
        f"Received: {normalized!r}"
    )


def prepare_provider_cli_env(
    *,
    provider: str,
    env: dict[str, str] | None = None,
    logger: Any | None = None,
) -> tuple[dict[str, str], str]:
    """Prepare provider CLI env and resolve effective auth mode."""
    provider_name = str(provider or "").strip().lower()
    prepared_env = dict(env or {})
    key_env_vars = _PROVIDER_API_KEY_ENV.get(provider_name, ())
    resolve_cli_auth_mode(prepared_env)

    stripped: list[str] = []
    for key_name in key_env_vars:
        if str(prepared_env.get(key_name) or os.getenv(key_name) or "").strip():
            stripped.append(key_name)
        prepared_env[key_name] = ""

    if stripped:
        log_info = getattr(logger, "info", None) if logger is not None else None
        if callable(log_info):
            log_info(
                "🔐 %s CLI auth mode=account (masked key vars stripped: %s)",
                provider_name,
                ", ".join(sorted(stripped)),
            )

    return prepared_env, "account"


def _resolve_git_transport(env: dict[str, str] | None = None) -> str:
    raw = str(
        (env or {}).get("NEXUS_GIT_PLATFORM_TRANSPORT")
        or os.getenv("NEXUS_GIT_PLATFORM_TRANSPORT", "api")
    ).strip().lower()
    return raw if raw in {"api", "cli"} else "api"


def _blocked_git_cli_dir() -> str:
    global _BLOCKED_GIT_CLI_BIN_DIR
    if _BLOCKED_GIT_CLI_BIN_DIR and os.path.isdir(_BLOCKED_GIT_CLI_BIN_DIR):
        return _BLOCKED_GIT_CLI_BIN_DIR

    bin_dir = tempfile.mkdtemp(prefix="nexus-block-git-cli-")
    shim_body = (
        "#!/bin/sh\n"
        'echo "nexus: gh/glab disabled when NEXUS_GIT_PLATFORM_TRANSPORT=api" >&2\n'
        "exit 127\n"
    )
    for name in ("gh", "glab"):
        path = os.path.join(bin_dir, name)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(shim_body)
        os.chmod(path, 0o755)
    _BLOCKED_GIT_CLI_BIN_DIR = bin_dir
    return bin_dir


def apply_git_transport_env_policy(merged_env: dict[str, str]) -> dict[str, str]:
    transport = _resolve_git_transport(merged_env)
    merged_env["NEXUS_GIT_PLATFORM_TRANSPORT"] = transport
    if transport == "api":
        blocked_dir = _blocked_git_cli_dir()
        current_path = str(merged_env.get("PATH") or "")
        merged_env["PATH"] = f"{blocked_dir}:{current_path}" if current_path else blocked_dir
        merged_env["NEXUS_GIT_CLI_DISABLED"] = "1"
    else:
        merged_env.pop("NEXUS_GIT_CLI_DISABLED", None)
    return merged_env


def _resolve_copilot_permissions(copilot_permissions: Mapping[str, Any] | None) -> tuple[list[str], bool, bool]:
    if not isinstance(copilot_permissions, Mapping):
        return [], False, False

    allow_urls_raw = copilot_permissions.get("allow_urls")
    allow_urls: list[str] = []
    if isinstance(allow_urls_raw, list):
        seen: set[str] = set()
        for value in allow_urls_raw:
            url = str(value or "").strip()
            if not url or url in seen:
                continue
            seen.add(url)
            allow_urls.append(url)

    allow_all_urls = bool(copilot_permissions.get("allow_all_urls") is True)
    allow_all_paths = bool(copilot_permissions.get("allow_all_paths") is True)
    return allow_urls, allow_all_urls, allow_all_paths


def _prepare_log_path(
    *,
    prefix: str,
    workspace_dir: str,
    issue_num: str | None,
    log_subdir: str | None,
    get_tasks_logs_dir: Callable[[str, str | None], str],
) -> str:
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = get_tasks_logs_dir(workspace_dir, log_subdir)
    os.makedirs(log_dir, exist_ok=True)
    log_suffix = f"{issue_num}_{timestamp}" if issue_num else timestamp
    return os.path.join(log_dir, f"{prefix}_{log_suffix}.log")


def _stream_process_output(
    *,
    stream: TextIO | None,
    log_path: str,
    logger: Any,
    output_label: str,
) -> None:
    if stream is None:
        return

    with stream:
        with open(log_path, "w", encoding="utf-8") as log_file:
            for chunk in iter(stream.readline, ""):
                if not chunk:
                    break
                log_file.write(chunk)
                log_file.flush()
                text = chunk.rstrip()
                if text:
                    logger.info("[%s] %s", output_label, text)


def _start_output_tee(
    *,
    process: subprocess.Popen[Any],
    log_path: str,
    logger: Any,
    output_label: str,
) -> None:
    stream = getattr(process, "stdout", None)
    if stream is None:
        with open(log_path, "w", encoding="utf-8"):
            pass
        return

    threading.Thread(
        target=_stream_process_output,
        kwargs={
            "stream": stream,
            "log_path": log_path,
            "logger": logger,
            "output_label": output_label,
        },
        daemon=True,
    ).start()


def _redact_command_for_logs(cmd: list[str]) -> str:
    if not cmd:
        return ""

    rendered: list[str] = []
    redact_next = False
    for index, part in enumerate(cmd):
        text = str(part)
        if redact_next:
            rendered.append("<prompt>")
            redact_next = False
            continue
        if text in {"-p", "--prompt"}:
            rendered.append(text)
            redact_next = True
            continue
        if index == len(cmd) - 1 and len(text) > 120:
            rendered.append("<prompt>")
            continue
        rendered.append(shlex.quote(text))
    return " ".join(rendered)


def _monitor_process_lifecycle(
    *,
    process: subprocess.Popen[Any],
    logger: Any,
    output_label: str,
) -> None:
    def _runner() -> None:
        exit_code = None
        try:
            wait_fn = getattr(process, "wait", None)
            if callable(wait_fn):
                exit_code = wait_fn()
            else:
                while True:
                    exit_code = process.poll()
                    if exit_code is not None:
                        break
                    time.sleep(0.5)
        except Exception as exc:
            log_warning = getattr(logger, "warning", None)
            if callable(log_warning):
                log_warning("[agent:%s] lifecycle monitoring failed: %s", output_label, exc)
            return

        log_info = getattr(logger, "info", None)
        if callable(log_info):
            log_info("[agent:%s] exit pid=%s code=%s", output_label, process.pid, exit_code)

    threading.Thread(target=_runner, daemon=True).start()


def _launch_process_with_log(
    *,
    cmd: list[str],
    workspace_dir: str,
    env: dict[str, str] | None,
    log_path: str,
    logger: Any,
    launched_message: str,
    output_label: str,
) -> subprocess.Popen[Any]:
    try:
        merged_env = {**os.environ}
        if env:
            merged_env.update(env)
        merged_env = apply_git_transport_env_policy(merged_env)
        process = subprocess.Popen(
            cmd,
            cwd=workspace_dir,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            env=merged_env,
            text=True,
            bufsize=1,
        )
        logger.info(
            "[agent:%s] launch pid=%s cwd=%s log=%s cmd=%s",
            output_label,
            process.pid,
            workspace_dir,
            log_path,
            _redact_command_for_logs(cmd),
        )
        _start_output_tee(
            process=process,
            log_path=log_path,
            logger=logger,
            output_label=output_label,
        )
        _monitor_process_lifecycle(
            process=process,
            logger=logger,
            output_label=output_label,
        )
        logger.info(launched_message, process.pid)
        return process
    except Exception:
        raise


def invoke_copilot_agent_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    copilot_provider: Any,
    copilot_cli_path: str,
    copilot_model: str,
    copilot_supports_model: bool,
    copilot_permissions: dict[str, Any] | None,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    rate_limited_error: type[Exception],
    logger: Any,
    agent_prompt: str,
    workspace_dir: str,
    agents_dir: str,
    base_dir: str,
    issue_num: str | None = None,
    log_subdir: str | None = None,
    env: dict[str, str] | None = None,
    execution_mode: str | None = None,
    execution_mode_config: Mapping[str, Any] | None = None,
) -> int | None:
    if not check_tool_available(copilot_provider):
        raise tool_unavailable_error("Copilot not available")

    cmd = [
        copilot_cli_path,
        "-p",
        agent_prompt,
        "--add-dir",
        base_dir,
        "--add-dir",
        workspace_dir,
        "--add-dir",
        agents_dir,
        "--allow-all-tools",
    ]
    allow_urls, allow_all_urls, allow_all_paths = _resolve_copilot_permissions(copilot_permissions)
    if allow_all_urls:
        cmd.append("--allow-all-urls")
    else:
        for allowed_url in allow_urls:
            cmd.extend(["--allow-url", allowed_url])
    if allow_all_paths:
        cmd.append("--allow-all-paths")
    if copilot_model and copilot_supports_model:
        cmd.extend(["--model", copilot_model])
    cmd.extend(resolve_execution_mode_cli_args(execution_mode_config))
    log_path = _prepare_log_path(
        prefix="copilot",
        workspace_dir=workspace_dir,
        issue_num=issue_num,
        log_subdir=log_subdir,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )

    merged_env = merge_execution_mode_env(
        provider="copilot",
        env=env,
        execution_mode=execution_mode,
        execution_mode_config=execution_mode_config,
    )

    logger.info("🤖 Launching Copilot CLI agent")
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=merged_env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 Copilot launched (PID: %s)",
            output_label="copilot",
        )

        def _read_log_excerpt(max_chars: int = 2000) -> str:
            try:
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    data = handle.read()
                if len(data) <= max_chars:
                    return data
                return data[-max_chars:]
            except Exception:
                return ""

        # Detect near-immediate startup failure so orchestrator can fallback.
        # Copilot quota failures often exit after a few seconds, not instantly.
        exit_code = None
        deadline = time.time() + 5.0
        while time.time() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.5)
        if exit_code is not None:
            excerpt = _read_log_excerpt().lower()
            if (
                "402" in excerpt
                or "quota" in excerpt
                or "rate limit" in excerpt
                or "ratelimit" in excerpt
                or "429" in excerpt
                or "too many requests" in excerpt
            ):
                raise rate_limited_error(
                    f"Copilot exited immediately with rate limit/quota (exit={exit_code})"
                )
            raise tool_unavailable_error(f"Copilot exited immediately (exit={exit_code})")
        return process.pid
    except Exception as exc:
        logger.error("❌ Copilot launch failed: %s", exc)
        raise


def invoke_gemini_agent_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    gemini_provider: Any,
    gemini_cli_path: str,
    gemini_model: str,
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
    if not check_tool_available(gemini_provider):
        raise tool_unavailable_error("Gemini CLI not available")

    provider_env, auth_mode = prepare_provider_cli_env(
        provider="gemini",
        env=merge_execution_mode_env(
            provider="gemini",
            env=env,
            execution_mode=execution_mode,
            execution_mode_config=execution_mode_config,
        ),
        logger=logger,
    )

    cmd = [
        gemini_cli_path,
        "--prompt",
        agent_prompt,
        "--include-directories",
        agents_dir,
        "--yolo",
    ]
    if gemini_model:
        cmd.extend(["--model", gemini_model])
    cmd.extend(resolve_execution_mode_cli_args(execution_mode_config))

    log_path = _prepare_log_path(
        prefix="gemini",
        workspace_dir=workspace_dir,
        issue_num=issue_num,
        log_subdir=log_subdir,
        get_tasks_logs_dir=get_tasks_logs_dir,
    )

    logger.info("🤖 Launching Gemini CLI agent")
    logger.info("   Auth mode: %s", auth_mode)
    logger.info("   Workspace: %s", workspace_dir)
    logger.info("   Log: %s", log_path)

    def _read_log_excerpt(max_chars: int = 2000) -> str:
        try:
            with open(log_path, encoding="utf-8", errors="replace") as handle:
                data = handle.read()
            if len(data) <= max_chars:
                return data
            return data[-max_chars:]
        except Exception:
            return ""

    def _looks_rate_limited(log_excerpt: str) -> bool:
        text = str(log_excerpt or "").lower()
        if not text:
            return False
        hard_markers = (
            "ratelimitexceeded",
            "status 429",
            "status: 429",
            "too many requests",
            "statustext: 'too many requests'",
            "no capacity available",
            "exhausted your capacity",
            "quota will reset",
        )
        return any(marker in text for marker in hard_markers)

    def _gemini_retry_loop_count(log_excerpt: str) -> int:
        text = str(log_excerpt or "").lower()
        if not text:
            return 0
        markers = (
            "attempt 1 failed:",
            "retryablequotaerror",
            "quota will reset",
        )
        return sum(text.count(marker) for marker in markers)

    try:
        process = _launch_process_with_log(
            cmd=cmd,
            workspace_dir=workspace_dir,
            env=provider_env,
            log_path=log_path,
            logger=logger,
            launched_message="🚀 Gemini launched (PID: %s)",
            output_label="gemini",
        )

        # Startup probe: detect both immediate exits and live quota/rate-limit loops.
        # Gemini can stay alive while repeatedly logging retries when quota is exhausted.
        exit_code = None
        # Keep startup probe short; if Gemini survives initial checks we let
        # post-launch watchdogs monitor quota loops and trigger fallback quickly.
        deadline = time.time() + 5.0
        retry_loop_cap = 3
        while time.time() < deadline:
            log_excerpt = _read_log_excerpt().lower()
            retry_loop_count = _gemini_retry_loop_count(log_excerpt)
            if retry_loop_count >= retry_loop_cap:
                try:
                    process.kill()
                except Exception:
                    pass
                raise rate_limited_error(
                    f"Gemini quota rate limit retry loop detected at startup (markers={retry_loop_count}, cap={retry_loop_cap})"
                )
            if _looks_rate_limited(log_excerpt):
                try:
                    process.kill()
                except Exception:
                    pass
                raise rate_limited_error("Gemini quota/rate limit detected at startup")

            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.3)

        if exit_code is not None:
            log_excerpt = _read_log_excerpt().lower()
            if _looks_rate_limited(log_excerpt):
                raise rate_limited_error(
                    f"Gemini exited immediately with rate limit (exit={exit_code})"
                )
            raise tool_unavailable_error(f"Gemini exited immediately (exit={exit_code})")
        return process.pid
    except Exception as exc:
        logger.error("❌ Gemini launch failed: %s", exc)
        raise
