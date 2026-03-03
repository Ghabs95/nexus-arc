"""Agent monitoring and recovery - handles timeouts, retries, and failures.

Provides host-side glue between nexus-arc MonitorEngine and the
runtime-ops plugin for PID management and process killing.

For workflow routing, import directly from ``nexus.core.router``.
"""

import logging
import os
import time

import config
from audit_store import AuditStore
from orchestration.plugin_runtime import get_runtime_ops_plugin

from nexus.core.monitor import MonitorEngine

logger = logging.getLogger(__name__)


class AgentMonitor:
    """Monitors agent execution and handles timeouts/failures."""

    @staticmethod
    def check_timeout(
        issue_num: str,
        log_file: str,
        timeout_seconds: int | None = None,
    ) -> tuple[bool, int | None]:
        """Check if an agent has timed out."""
        threshold = int(timeout_seconds) if timeout_seconds else config.AGENT_TIMEOUT

        try:
            last_modified = os.path.getmtime(log_file)
            timed_out = (time.time() - float(last_modified)) > threshold
        except Exception:
            timed_out = MonitorEngine.check_log_timeout(log_file, timeout_seconds=threshold)
        pid = None

        if timed_out:
            # Check if process is still running
            runtime_ops = get_runtime_ops_plugin(cache_key="runtime-ops:monitor")
            pid = runtime_ops.find_agent_pid_for_issue(issue_num) if runtime_ops else None
            if pid:
                logger.warning("Issue #%s: Agent timeout detected (PID: %s)", issue_num, pid)
                return (True, pid)

        return (False, None)

    @staticmethod
    def should_retry(issue_num: str, agent_name: str) -> bool:
        """Legacy compatibility hook used by nexus runtime tests."""
        _ = (issue_num, agent_name)
        return True

    @staticmethod
    def kill_agent(pid: int, issue_num: str) -> bool:
        """Kill a stuck agent process.

        Prefer graceful termination first to reduce provider state corruption
        (notably Codex rollout/session files). Escalate to force kill only
        when the process remains alive after a short grace window.
        """
        try:
            runtime_ops = get_runtime_ops_plugin(cache_key="runtime-ops:monitor")
            if not runtime_ops:
                logger.error("Runtime ops plugin unavailable; cannot kill PID %s", pid)
                return False

            if not runtime_ops.kill_process(pid, force=False):
                logger.error("Failed to kill agent PID %s", pid)
                return False

            # Give the process a chance to flush and exit cleanly before escalating.
            for _ in range(20):
                if not AgentMonitor._pid_alive(pid):
                    break
                time.sleep(0.25)

            if AgentMonitor._pid_alive(pid) and not runtime_ops.kill_process(pid, force=True):
                logger.error("Failed to force kill agent PID %s", pid)
                return False

            logger.warning("Killed stuck agent PID %s for issue #%s", pid, issue_num)
            AuditStore.audit_log(
                int(issue_num) if issue_num else 0,
                "AGENT_TIMEOUT_KILL",
                f"Killed agent process PID {pid} after timeout",
            )
            return True
        except Exception as e:
            logger.error("Failed to kill agent PID %s: %s", pid, e)
            return False

    @staticmethod
    def _pid_alive(pid: int) -> bool:
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except Exception:
            return False

    @staticmethod
    def mark_failed(issue_num: str, agent_name: str, reason: str) -> None:
        """Mark an agent as permanently failed."""
        AuditStore.audit_log(int(issue_num), "AGENT_FAILED", reason)
