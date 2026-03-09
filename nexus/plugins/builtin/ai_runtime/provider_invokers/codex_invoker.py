import os
import subprocess
import time
from typing import Any, Callable

from .agent_invokers import _monitor_process_lifecycle, _redact_command_for_logs
from .agent_invokers import _start_output_tee


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

    effective_openai_key = str(
        (env or {}).get("OPENAI_API_KEY") or os.getenv("OPENAI_API_KEY") or ""
    ).strip()

    requester_hint = str(
        (env or {}).get("NEXUS_REQUESTER_ID") or os.getenv("NEXUS_REQUESTER_ID") or ""
    ).strip()
    masked_requester = f"{requester_hint[:8]}..." if len(requester_hint) > 8 else (requester_hint or "none")
    logger.info(
        "🔐 Codex auth diagnostic: issue=%s requester=%s has_openai_key=%s openai_key_len=%s env_has_openai=%s",
        issue_num or "unknown",
        masked_requester,
        bool(effective_openai_key),
        len(effective_openai_key),
        bool(str((env or {}).get("OPENAI_API_KEY") or "").strip()),
    )
    if not effective_openai_key:
        raise tool_unavailable_error(
            "Codex requires OPENAI_API_KEY for this launch context"
        )

    _cleanup_empty_rollout_files(logger=logger)

    # Force writable workspace + network permission so Codex can post issue comments
    # and write completion summaries during workflow handoff.
    cmd = [
        codex_cli_path,
        "exec",
        "--skip-git-repo-check",
        "--sandbox",
        "workspace-write",
        "-c",
        'sandbox_permissions=["network-access"]',
    ]
    if codex_model:
        cmd.extend(["--model", codex_model])
    cmd.append(agent_prompt)

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
        if env:
            merged_env.update(env)
        # Ensure inherited host sandbox flags don't force-disable network for
        # Codex child commands (e.g., gh issue comment/view).
        merged_env.pop("CODEX_SANDBOX_NETWORK_DISABLED", None)
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
            log_path,
            _redact_command_for_logs(cmd),
        )
        _start_output_tee(
            process=process,
            log_path=log_path,
            logger=logger,
            output_label="codex",
        )
        _monitor_process_lifecycle(
            process=process,
            logger=logger,
            output_label="codex",
        )

        def _read_log_excerpt(max_chars: int = 3000) -> str:
            try:
                with open(log_path, encoding="utf-8", errors="replace") as handle:
                    data = handle.read()
                if len(data) <= max_chars:
                    return data
                return data[-max_chars:]
            except Exception:
                return ""

        # Detect near-immediate startup/auth failures so the orchestrator can
        # fallback to another provider in the same launch cycle.
        exit_code = None
        deadline = time.time() + 8.0
        while time.time() < deadline:
            exit_code = process.poll()
            if exit_code is not None:
                break
            time.sleep(0.5)

        if exit_code is not None:
            excerpt = _read_log_excerpt().lower()
            auth_markers = (
                "401 unauthorized",
                "missing bearer",
                "authentication in header",
                "invalid_api_key",
                "missing api key",
            )
            if any(marker in excerpt for marker in auth_markers):
                raise tool_unavailable_error(
                    f"Codex authentication failed (exit={exit_code})"
                )
            raise tool_unavailable_error(f"Codex exited immediately (exit={exit_code})")

        logger.info("🚀 Codex launched (PID: %s)", process.pid)
        return process.pid
    except Exception as exc:
        logger.error("❌ Codex launch failed: %s", exc)
        raise
