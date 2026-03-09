"""Built-in plugin: workflow policy (finalization + notifications)."""

import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


class WorkflowPolicyPlugin:
    """Workflow policy for notification composition and completion finalization."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def _callback(self, name: str) -> Callable[..., Any] | None:
        callback = self.config.get(name)
        return callback if callable(callback) else None

    @staticmethod
    def _format_agent(agent: str) -> str:
        value = str(agent or "").strip().lstrip("@").strip()
        return f"@{value}" if value else "unknown"

    def build_transition_message(
        self,
        *,
        issue_number: str,
        completed_agent: str,
        next_agent: str,
        repo: str,
    ) -> str:
        completed = self._format_agent(completed_agent)
        launching = self._format_agent(next_agent)
        return (
            "🔗 **Agent Transition**\n\n"
            f"Issue: #{issue_number}\n"
            f"Completed: `{completed}`\n"
            f"Launching: `{launching}`\n\n"
            f"🔗 https://github.com/{repo}/issues/{issue_number}"
        )

    def build_autochain_failed_message(
        self,
        *,
        issue_number: str,
        completed_agent: str,
        next_agent: str,
        repo: str,
    ) -> str:
        completed = self._format_agent(completed_agent)
        failed = self._format_agent(next_agent)
        return (
            "❌ **Auto-chain Failed**\n\n"
            f"Issue: #{issue_number}\n"
            f"Completed: `{completed}`\n"
            f"Failed to launch: `{failed}`\n\n"
            f"🔗 https://github.com/{repo}/issues/{issue_number}"
        )

    def build_workflow_complete_message(
        self,
        *,
        issue_number: str,
        last_agent: str,
        repo: str,
        pr_urls: list[str] | None = None,
    ) -> str:
        parts = [
            "✅ **Workflow Complete**\n\n"
            f"Issue: #{issue_number}\n"
            f"Last agent: `{last_agent}`\n"
        ]
        urls = [url for url in (pr_urls or []) if url]
        if urls:
            parts.append("PRs:\n")
            parts.extend(f"- {url}\n" for url in urls)
        parts.append(f"\n🔗 https://github.com/{repo}/issues/{issue_number}")
        return "".join(parts)

    def _resolve_git_dir(self, project_name: str) -> str | None:
        resolver = self._callback("resolve_git_dir")
        if not resolver:
            return None
        return resolver(project_name)

    def _create_pr(
        self,
        *,
        repo: str,
        repo_dir: str,
        issue_number: str,
        last_agent: str,
        issue_repo: str | None = None,
        base_branch: str | None = None,
    ) -> str | None:
        creator = self._callback("create_pr_from_changes")
        if not creator:
            return None

        title = f"fix: resolve #{issue_number}"
        body = (
            f"Automated PR for issue #{issue_number}.\n\n"
            f"Workflow completed by Nexus agent chain.\n"
            f"Last agent: `{last_agent}`"
        )

        pr_link = creator(
            repo=repo,
            repo_dir=repo_dir,
            issue_number=str(issue_number),
            title=title,
            body=body,
            issue_repo=issue_repo or repo,
            base_branch=str(base_branch or "").strip() or None,
        )
        return str(pr_link) if pr_link else None

    def _resolve_repo_branch(self, *, project_name: str, repo: str) -> str | None:
        resolver = self._callback("resolve_repo_branch")
        if not resolver:
            return None
        try:
            value = resolver(project_name=project_name, repo=repo)
        except TypeError:
            value = resolver(project_name, repo)
        branch = str(value or "").strip()
        return branch or None

    def _find_existing_pr(self, *, repo: str, issue_number: str) -> str | None:
        finder = self._callback("find_existing_pr")
        if not finder:
            return None

        try:
            pr_link = finder(repo=repo, issue_number=str(issue_number))
        except Exception as exc:
            logger.warning("Error finding existing PR for issue #%s: %s", issue_number, exc)
            return None

        return str(pr_link) if pr_link else None

    def _close_issue(
        self,
        *,
        repo: str,
        issue_number: str,
        last_agent: str,
        pr_urls: list[str] | None = None,
    ) -> bool:
        closer = self._callback("close_issue")
        if not closer:
            return False

        close_comment = (
            "✅ Workflow completed. All agent steps finished successfully.\n"
            f"Last agent: `{last_agent}`"
        )
        urls = [url for url in (pr_urls or []) if url]
        if urls:
            close_comment += "\nPRs:\n" + "\n".join(f"- {url}" for url in urls)

        return bool(closer(repo=repo, issue_number=str(issue_number), comment=close_comment))

    def _notify(
        self,
        *,
        repo: str,
        issue_number: str,
        last_agent: str,
        pr_urls: list[str] | None = None,
    ) -> None:
        notifier = self._callback("send_notification")
        if not notifier:
            return

        builder = (
            self._callback("build_workflow_complete_message")
            or self.build_workflow_complete_message
        )
        message = builder(
            issue_number=str(issue_number),
            last_agent=last_agent,
            repo=repo,
            pr_urls=pr_urls or [],
        )
        notifier(message)

    def _cleanup_worktree(self, *, repo_dir: str, issue_number: str) -> bool:
        cleaner = self._callback("cleanup_worktree")
        if not cleaner:
            return False
        try:
            return bool(cleaner(repo_dir=repo_dir, issue_number=str(issue_number)))
        except Exception as exc:
            logger.warning(
                "Error cleaning up worktree for issue #%s in repo %s: %s",
                issue_number,
                repo_dir,
                exc,
            )
            return False

    def _sync_existing_pr_changes(
        self,
        *,
        repo: str,
        repo_dir: str,
        issue_number: str,
        issue_repo: str | None = None,
        base_branch: str | None = None,
    ) -> bool:
        syncer = self._callback("sync_existing_pr_changes")
        if not syncer:
            return False
        try:
            return bool(
                syncer(
                    repo=repo,
                    repo_dir=repo_dir,
                    issue_number=str(issue_number),
                    issue_repo=issue_repo,
                    base_branch=base_branch,
                )
            )
        except Exception as exc:
            logger.warning(
                "Error syncing existing PR branch for issue #%s in repo %s: %s",
                issue_number,
                repo,
                exc,
            )
            return False

    def finalize_workflow(
        self,
        *,
        issue_number: str,
        repo: str,
        last_agent: str,
        project_name: str,
    ) -> dict[str, Any]:
        """Finalize workflow and return outcome summary."""
        result: dict[str, Any] = {
            "pr_urls": [],
            "issue_closed": False,
            "notification_sent": False,
        }
        git_dirs_by_repo: dict[str, str] = {}

        if project_name:
            resolve_many = self._callback("resolve_git_dirs")
            if resolve_many:
                try:
                    resolved = resolve_many(project_name)
                    if isinstance(resolved, dict):
                        git_dirs_by_repo = {
                            str(repo_name): str(path)
                            for repo_name, path in resolved.items()
                            if repo_name and path
                        }
                except Exception as exc:
                    logger.warning("Error resolving git dirs for project %s: %s", project_name, exc)

            if not git_dirs_by_repo:
                single_dir = self._resolve_git_dir(project_name)
                if single_dir:
                    git_dirs_by_repo = {repo: single_dir}

            target_repos = list(git_dirs_by_repo.keys()) or [repo]

            for target_repo in target_repos:
                existing_pr_url = self._find_existing_pr(
                    repo=target_repo, issue_number=issue_number
                )
                if existing_pr_url:
                    result["pr_urls"].append(existing_pr_url)
                    if git_dirs_by_repo.get(target_repo):
                        self._sync_existing_pr_changes(
                            repo=target_repo,
                            repo_dir=git_dirs_by_repo[target_repo],
                            issue_number=issue_number,
                            issue_repo=repo,
                            base_branch=self._resolve_repo_branch(
                                project_name=project_name,
                                repo=target_repo,
                            ),
                        )
                    continue

                git_dir = git_dirs_by_repo.get(target_repo)
                if not git_dir:
                    continue

                try:
                    base_branch = self._resolve_repo_branch(
                        project_name=project_name,
                        repo=target_repo,
                    )
                    created_pr_url = self._create_pr(
                        repo=target_repo,
                        repo_dir=git_dir,
                        issue_number=issue_number,
                        last_agent=last_agent,
                        issue_repo=repo,
                        base_branch=base_branch,
                    )
                    if created_pr_url:
                        result["pr_urls"].append(created_pr_url)
                except Exception as exc:
                    logger.warning(
                        "Error creating PR for issue #%s in repo %s: %s",
                        issue_number,
                        target_repo,
                        exc,
                    )

        for git_dir in set(git_dirs_by_repo.values()):
            if not git_dir:
                continue
            self._cleanup_worktree(repo_dir=git_dir, issue_number=issue_number)

        try:
            self._notify(
                repo=repo,
                issue_number=issue_number,
                last_agent=last_agent,
                pr_urls=result.get("pr_urls", []),
            )
            result["notification_sent"] = self._callback("send_notification") is not None
        except Exception as exc:
            logger.warning(
                "Error sending finalization notification for issue #%s: %s", issue_number, exc
            )

        return result


def register_plugins(registry) -> None:
    """Register built-in workflow policy plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="workflow-policy",
        version="0.1.0",
        factory=lambda config: WorkflowPolicyPlugin(config),
        description="Workflow policy for notifications and finalization",
    )
