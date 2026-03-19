import os
import subprocess
import time
from typing import Any, Callable

from .agent_invokers import _monitor_process_lifecycle, _redact_command_for_logs
from .agent_invokers import (
    _start_output_tee,
    apply_git_transport_env_policy,
    prepare_provider_cli_env,
)


def _looks_like_codex_auth_failure(excerpt: str) -> bool:
    text = str(excerpt or "").lower()
    auth_markers = (
        "401 unauthorized",
        "missing bearer",
        "authentication in header",
        "invalid_api_key",
        "missing api key",
    )
    return any(marker in text for marker in auth_markers)


def _looks_like_codex_bwrap_namespace_failure(excerpt: str) -> bool:
    text = str(excerpt or "").lower()
    markers = (
        "bwrap:",
        "no permissions to create a new namespace",
        "kernel.unprivileged_userns_clone",
    )
    return all(marker in text for marker in markers)


def _build_codex_exec_cmd(
    *,
    codex_cli_path: str,
    codex_model: str,
    agent_prompt: str,
    sandbox_mode: str,
) -> list[str]:
    cmd = [
        codex_cli_path,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        sandbox_mode,
    ]
    if sandbox_mode == "workspace-write":
        cmd.extend(
            [
                "-c",
                'sandbox_permissions=["network-access"]',
            ]
        )
    if codex_model:
        cmd.extend(["--model", codex_model])
    cmd.append(agent_prompt)
    return cmd


def _cleanup_empty_rollout_files(*, logger: Any, codex_home: str | None = None) -> int:
    """Remove zero-byte Codex rollout session files that can crash reconcile startup."""
    home = str(codex_home or os.getenv("CODEX_HOME") or os.path.expanduser("~/.codex")).strip()
    if not home:
        return 0

    sessions_dir = os.path.join(home, "sessions")
    if not os.path.isdir(sessions_dir):
        return 0

    removed = 0
    now = time.time()
    for root, _dirs, files in os.walk(sessions_dir):
        for name in files:
            if not (name.startswith("rollout-") and name.endswith(".jsonl")):
                continue
            path = os.path.join(root, name)
            try:
                if os.path.getsize(path) != 0:
                    continue
                # Avoid racing with files currently being created by a live process.
                if (now - os.path.getmtime(path)) < 120:
                    continue
                os.remove(path)
                removed += 1
            except Exception:
                # Best-effort hygiene only.
                continue

    if removed:
        log_warning = getattr(logger, "warning", None) or getattr(logger, "info", None)
        if callable(log_warning):
            log_warning(
                "Removed %s empty Codex rollout session file(s) from %s",
                removed,
                sessions_dir,
            )
    return removed


def _terminate_process_for_retry(process: subprocess.Popen[Any]) -> None:
    terminate = getattr(process, "terminate", None)
    if callable(terminate):
        try:
            terminate()
        except Exception:
            pass

    deadline = time.time() + 2.0
    while time.time() < deadline:
        try:
            if process.poll() is not None:
                return
        except Exception:
            return
        time.sleep(0.1)

    kill = getattr(process, "kill", None)
    if callable(kill):
        try:
            kill()
        except Exception:
            pass


def invoke_codex_cli(
    *,
    check_tool_available: Callable[[Any], bool],
    codex_provider: Any,
    codex_cli_path: str,
    codex_model: str,
    get_tasks_logs_dir: Callable[[str, str | None], str],
    tool_unavailable_error: type[Exception],
    logger: Any,
    agent_prompt: str,
    workspace_dir: str,
    issue_num: str | None = None,
    log_subdir: str | None = None,
    env: dict[str, str] | None = None,
) -> int | None:
    if not check_tool_available(codex_provider):
        raise tool_unavailable_error("Codex CLI not available")

    provider_env, auth_mode = prepare_provider_cli_env(
        provider="codex",
        env=env,
        logger=logger,
    )
    effective_openai_key = str(provider_env.get("OPENAI_API_KEY") or "").strip()

    requester_hint = str(
        provider_env.get("NEXUS_REQUESTER_ID") or os.getenv("NEXUS_REQUESTER_ID") or ""
    ).strip()
    masked_requester = f"{requester_hint[:8]}..." if len(requester_hint) > 8 else (requester_hint or "none")
    logger.info(
        "🔐 Codex auth diagnostic: issue=%s requester=%s mode=%s has_openai_key=%s openai_key_len=%s env_has_openai=%s",
        issue_num or "unknown",
        masked_requester,
        auth_mode,
        bool(effective_openai_key),
        len(effective_openai_key),
        bool(str(provider_env.get("OPENAI_API_KEY") or "").strip()),
    )

    _cleanup_empty_rollout_files(logger=logger)

    # Only include the danger-full-access fallback when the operator has explicitly
    # opted in via NEXUS_CODEX_ALLOW_DANGER_SANDBOX=1. Automatically escalating
    # privileges on hosts without unprivileged user namespaces would be a silent
    # security escalation.
    effective_env = {**os.environ, **(env or {})}
    allow_danger_sandbox = (
        str(effective_env.get("NEXUS_CODEX_ALLOW_DANGER_SANDBOX") or "").strip() == "1"
    )
    sandbox_modes = ["workspace-write", "danger-full-access"] if allow_danger_sandbox else ["workspace-write"]

    timestamp = time.strftime("%Y%m%d_%H%M%S")
    log_dir = get_tasks_logs_dir(workspace_dir, log_subdir)
    os.makedirs(log_dir, exist_ok=True)
    log_suffix = f"{issue_num}_{timestamp}" if issue_num else timestamp
    log_path = os.path.join(log_dir, f"codex_{log_suffix}.log")

    logger.info("🤖 Launching Codex CLI agent")
    logger.info("   Workspace: %s", workspace_dir)
    log_debug = getattr(logger, "debug", None) or getattr(logger, "info", None)
    if callable(log_debug):
        log_debug("   Log: %s", log_path)

    try:
        merged_env = {**os.environ}
        if provider_env:
            merged_env.update(provider_env)
        merged_env = apply_git_transport_env_policy(merged_env)
        # Ensure inherited host sandbox flags don't force-disable network for
        # Codex child commands (e.g., gh issue comment/view).
        merged_env.pop("CODEX_SANDBOX_NETWORK_DISABLED", None)
        def _read_log_excerpt(path: str, max_chars: int = 3000) -> str:
            try:
                with open(path, encoding="utf-8", errors="replace") as handle:
                    data = handle.read()
                if len(data) <= max_chars:
                    return data
                return data[-max_chars:]
            except Exception:
                return ""

        for retry_index, sandbox_mode in enumerate(sandbox_modes):
            attempt_log_path = (
                log_path if retry_index == 0 else f"{log_path}.retry{retry_index}"
            )
            cmd = _build_codex_exec_cmd(
                codex_cli_path=codex_cli_path,
                codex_model=codex_model,
                agent_prompt=agent_prompt,
                sandbox_mode=sandbox_mode,
            )
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
                "codex",
                process.pid,
                workspace_dir,
                attempt_log_path,
                _redact_command_for_logs(cmd),
            )
            _start_output_tee(
                process=process,
                log_path=attempt_log_path,
                logger=logger,
                output_label="codex",
            )
            _monitor_process_lifecycle(
                process=process,
                logger=logger,
                output_label="codex",
            )

            # Detect near-immediate startup/auth failures so the orchestrator can
            # fallback to another provider in the same launch cycle.
            exit_code = None
            deadline = time.time() + 8.0
            retry_for_bwrap_failure = False
            while time.time() < deadline:
                excerpt = _read_log_excerpt(attempt_log_path)
                if (
                    sandbox_mode == "workspace-write"
                    and _looks_like_codex_bwrap_namespace_failure(excerpt)
                ):
                    logger.warning(
                        "Codex workspace-write sandbox is unavailable on this host; "
                        "retrying launch with danger-full-access."
                    )
                    _terminate_process_for_retry(process)
                    retry_for_bwrap_failure = True
                    break
                exit_code = process.poll()
                if exit_code is not None:
                    break
                time.sleep(0.5)

            if retry_for_bwrap_failure:
                continue

            if exit_code is None:
                logger.info("🚀 Codex launched (PID: %s)", process.pid)
                return process.pid

            excerpt = _read_log_excerpt(attempt_log_path)
            if _looks_like_codex_auth_failure(excerpt):
                raise tool_unavailable_error(
                    f"Codex account login required or expired (exit={exit_code}). "
                    "Run `codex login` in the runtime environment."
                )
            if (
                sandbox_mode == "workspace-write"
                and _looks_like_codex_bwrap_namespace_failure(excerpt)
            ):
                logger.warning(
                    "Codex workspace-write sandbox is unavailable on this host; "
                    "retrying launch with danger-full-access."
                )
                continue
            raise tool_unavailable_error(f"Codex exited immediately (exit={exit_code})")

        raise tool_unavailable_error("Codex launch retry budget exhausted")
    except Exception as exc:
        logger.error("❌ Codex launch failed: %s", exc)
        raise
