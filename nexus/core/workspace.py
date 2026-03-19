"""
Core module for managing isolated workspaces via Git worktrees.
"""

import logging
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable

logger = logging.getLogger(__name__)
_BLOCKED_HELPERS_ENV = "NEXUS_BLOCKED_WORKTREE_HELPERS"
_DEFAULT_BLOCKED_HELPERS = (
    "post_comments.py",
    "writer_completion.py",
    "deployer_completion.py",
)


class WorktreeProvisionError(RuntimeError):
    """Raised when an isolated issue worktree cannot be provisioned."""


class WorkspaceManager:
    """Manages Git worktree creation and cleanup for isolated agent execution."""

    @staticmethod
    def _worktree_dir(base_repo_path: str, issue_number: str) -> str:
        issue_number_str = str(issue_number).strip()
        return os.path.join(base_repo_path, ".nexus", "worktrees", f"issue-{issue_number_str}")

    @staticmethod
    def _is_worktree_clean_dir(worktree_dir: str) -> bool:
        if not os.path.isdir(worktree_dir):
            return True
        try:
            result = subprocess.run(
                ["git", "status", "--porcelain"],
                cwd=worktree_dir,
                check=True,
                capture_output=True,
                text=True,
            )
            return not bool((result.stdout or "").strip())
        except Exception as exc:
            logger.warning("Could not determine worktree cleanliness for %s: %s", worktree_dir, exc)
            return False

    @staticmethod
    def _branch_exists(base_repo_path: str, branch_name: str, *, env: dict[str, str] | None = None) -> bool:
        normalized = str(branch_name or "").strip()
        if not normalized:
            return False
        try:
            result = subprocess.run(
                ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{normalized}"],
                cwd=base_repo_path,
                env=env,
            )
            return result.returncode == 0
        except Exception:
            return False

    @staticmethod
    def _remove_partial_worktree_dir(worktree_dir: str) -> None:
        if not os.path.exists(worktree_dir):
            return
        if os.path.exists(os.path.join(worktree_dir, ".git")):
            return
        shutil.rmtree(worktree_dir, ignore_errors=True)

    @staticmethod
    def _fallback_branch_candidates(issue_number: str, branch_name: str):
        issue_number_str = str(issue_number).strip()
        primary = f"nexus/issue-{issue_number_str}"
        normalized_branch = str(branch_name or "").strip()

        if primary != normalized_branch:
            yield primary

        suffix = f"{primary}-wt"
        yield suffix

        for index in range(2, 26):
            yield f"{suffix}-{index}"

    @staticmethod
    def is_worktree_clean(base_repo_path: str, issue_number: str) -> bool:
        """Return True when worktree has no uncommitted changes (or does not exist)."""
        return WorkspaceManager._is_worktree_clean_dir(
            WorkspaceManager._worktree_dir(base_repo_path, issue_number)
        )

    @staticmethod
    def sanitize_worktree_helper_scripts(worktree_dir: str) -> list[str]:
        """Remove deprecated helper scripts from a worktree root.

        These scripts bypass runtime completion/comment safeguards and are no
        longer supported.
        """
        configured = str(os.getenv(_BLOCKED_HELPERS_ENV, "")).strip()
        if configured:
            blocked = tuple(item.strip() for item in configured.split(",") if item.strip())
        else:
            blocked = _DEFAULT_BLOCKED_HELPERS

        removed: list[str] = []
        for filename in blocked:
            candidate = os.path.join(worktree_dir, filename)
            if not os.path.isfile(candidate):
                continue
            try:
                os.remove(candidate)
                removed.append(candidate)
            except Exception as exc:
                logger.warning("Failed to remove deprecated helper %s: %s", candidate, exc)
        if removed:
            logger.warning(
                "Removed deprecated helper scripts from worktree %s: %s",
                worktree_dir,
                ", ".join(os.path.basename(path) for path in removed),
            )
        return removed

    @staticmethod
    def provision_worktree(
        base_repo_path: str,
        issue_number: str,
        branch_name: str | None = None,
        start_ref: str | None = None,
    ) -> str:
        """
        Provision an isolated Git worktree for the given issue.

        This isolates the agent's work into a specific branch without polluting
        the main project checkout, guaranteeing that concurrent agents don't conflict.

        Args:
            base_repo_path: The absolute path to the main git repository checkout.
            issue_number: The GitHub issue number (used to generate branch/folder names).
            branch_name: Optional branch name to use (e.g. from the issue's
                Target Branch field). Falls back to ``nexus/issue-{N}`` when
                not provided.
            start_ref: Optional git ref used when creating a new branch.
                Useful for creating issue worktrees from project-specific
                integration branches (for example ``origin/develop``).

        Returns:
            The absolute path to the provisioned worktree directory.
        """
        issue_number_str = str(issue_number).strip()
        worktree_dir = WorkspaceManager._worktree_dir(base_repo_path, issue_number_str)
        branch_name = (branch_name or "").strip() or f"nexus/issue-{issue_number_str}"

        logger.info(f"Provisioning worktree for issue {issue_number_str} at {worktree_dir}")

        # Check if the worktree already exists and is valid
        if os.path.isdir(worktree_dir):
            if os.path.exists(os.path.join(worktree_dir, ".git")):
                logger.info(f"Worktree for issue {issue_number_str} already exists. Reusing.")
                return worktree_dir
            else:
                logger.warning(
                    f"Invalid worktree found at {worktree_dir} (missing .git). Cleaning up."
                )
                shutil.rmtree(worktree_dir, ignore_errors=True)

        os.makedirs(os.path.dirname(worktree_dir), exist_ok=True)

        env = os.environ.copy()

        # Check if the branch already exists locally
        branch_exists_locally = WorkspaceManager._branch_exists(
            base_repo_path,
            branch_name,
            env=env,
        )

        try:
            if branch_exists_locally:
                # Add worktree using the existing branch
                subprocess.run(
                    ["git", "worktree", "add", worktree_dir, branch_name],
                    cwd=base_repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                logger.info(f"Adding worktree using existing branch {branch_name}")
            else:
                # Add worktree and create the branch simultaneously
                command = ["git", "worktree", "add", "-b", branch_name, worktree_dir]
                normalized_start_ref = str(start_ref or "").strip()
                if normalized_start_ref:
                    command.append(normalized_start_ref)
                subprocess.run(
                    command,
                    cwd=base_repo_path,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=env,
                )
                logger.info(f"Adding worktree and creating new branch {branch_name}")

        except subprocess.CalledProcessError as e:
            stderr = str(getattr(e, "stderr", "") or "")
            logger.warning("Initial worktree provision failed for %s: %s", branch_name, stderr)

            # If the target branch is currently checked out in another worktree,
            # create a dedicated issue branch from it so we can still isolate work.
            fallback_failures: list[str] = []
            if branch_exists_locally:
                for fallback_branch in WorkspaceManager._fallback_branch_candidates(
                    issue_number_str,
                    branch_name,
                ):
                    if WorkspaceManager._branch_exists(base_repo_path, fallback_branch, env=env):
                        continue

                    WorkspaceManager._remove_partial_worktree_dir(worktree_dir)
                    fallback_cmd = [
                        "git",
                        "worktree",
                        "add",
                        "-b",
                        fallback_branch,
                        worktree_dir,
                        branch_name,
                    ]
                    try:
                        subprocess.run(
                            fallback_cmd,
                            cwd=base_repo_path,
                            check=True,
                            capture_output=True,
                            text=True,
                            env=env,
                        )
                        logger.info(
                            "Created worktree using fallback branch %s from %s",
                            fallback_branch,
                            branch_name,
                        )
                        return worktree_dir
                    except subprocess.CalledProcessError as fallback_error:
                        fallback_stderr = str(getattr(fallback_error, "stderr", "") or fallback_error)
                        fallback_failures.append(f"{fallback_branch}: {fallback_stderr}")
                        logger.error(
                            "Fallback worktree provision failed for %s from %s: %s",
                            fallback_branch,
                            branch_name,
                            fallback_stderr,
                        )

            message = (
                f"Failed to provision isolated worktree for issue #{issue_number_str} "
                f"(branch: {branch_name}): {stderr or 'unknown git error'}"
            )
            if fallback_failures:
                message = f"{message}; fallback attempts: {'; '.join(fallback_failures)}"
            logger.error(message)
            raise WorktreeProvisionError(message) from e

        return worktree_dir

    @staticmethod
    def cleanup_worktree(base_repo_path: str, issue_number: str) -> bool:
        """
        Remove the isolated Git worktree for the given issue.

        Args:
            base_repo_path: The absolute path to the main git repository checkout.
            issue_number: The GitHub issue number.

        Returns:
            True if cleanup was successful or skipped, False if an error occurred.
        """
        issue_number_str = str(issue_number).strip()
        worktree_dir = WorkspaceManager._worktree_dir(base_repo_path, issue_number_str)

        if not os.path.exists(worktree_dir):
            return True

        logger.info(f"Cleaning up worktree for issue {issue_number_str} at {worktree_dir}")

        try:
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree_dir],
                cwd=base_repo_path,
                check=True,
                capture_output=True,
                text=True,
            )
            return True
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to remove worktree: {e.stderr}")
            return False

    @staticmethod
    def cleanup_worktree_safe(
        base_repo_path: str,
        issue_number: str,
        *,
        is_issue_agent_running: Callable[[str], bool] | None = None,
        require_clean: bool = True,
    ) -> bool:
        """Safely remove issue worktree only when no agent is running and tree is clean."""
        issue_number_str = str(issue_number).strip()
        worktree_dir = WorkspaceManager._worktree_dir(base_repo_path, issue_number_str)
        if not os.path.exists(worktree_dir):
            return True

        if callable(is_issue_agent_running):
            try:
                if is_issue_agent_running(issue_number_str):
                    logger.warning(
                        "Skipping worktree cleanup for issue %s: agent process still running",
                        issue_number_str,
                    )
                    return False
            except Exception as exc:
                logger.warning(
                    "Skipping worktree cleanup for issue %s: running-state check failed (%s)",
                    issue_number_str,
                    exc,
                )
                return False

        if require_clean and not WorkspaceManager._is_worktree_clean_dir(worktree_dir):
            logger.warning(
                "Skipping worktree cleanup for issue %s: worktree has local modifications",
                issue_number_str,
            )
            return False

        return WorkspaceManager.cleanup_worktree(base_repo_path, issue_number_str)

    @staticmethod
    def cleanup_stale_worktrees(
        base_repo_path: str,
        *,
        max_age_hours: int = 168,
        is_issue_agent_running: Callable[[str], bool] | None = None,
        require_clean: bool = True,
    ) -> dict[str, int]:
        """Cleanup stale issue worktrees older than max_age_hours with safety checks."""
        worktrees_root = os.path.join(base_repo_path, ".nexus", "worktrees")
        stats = {
            "scanned": 0,
            "removed": 0,
            "skipped_recent": 0,
            "skipped_running": 0,
            "skipped_dirty": 0,
            "failed": 0,
        }
        if not os.path.isdir(worktrees_root):
            return stats

        now = time.time()
        max_age_seconds = max(1, int(max_age_hours)) * 3600

        for entry in sorted(os.listdir(worktrees_root)):
            issue_match = re.fullmatch(r"issue-(\d+)", str(entry))
            if not issue_match:
                continue

            issue_number = issue_match.group(1)
            worktree_dir = os.path.join(worktrees_root, entry)
            if not os.path.isdir(worktree_dir):
                continue

            stats["scanned"] += 1
            age_seconds = max(0, int(now - os.path.getmtime(worktree_dir)))
            if age_seconds < max_age_seconds:
                stats["skipped_recent"] += 1
                continue

            if callable(is_issue_agent_running):
                try:
                    if is_issue_agent_running(issue_number):
                        stats["skipped_running"] += 1
                        continue
                except Exception:
                    stats["failed"] += 1
                    continue

            if require_clean and not WorkspaceManager._is_worktree_clean_dir(worktree_dir):
                stats["skipped_dirty"] += 1
                continue

            if WorkspaceManager.cleanup_worktree(base_repo_path, issue_number):
                stats["removed"] += 1
            else:
                stats["failed"] += 1

        return stats
