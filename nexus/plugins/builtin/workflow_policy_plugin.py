"""Built-in plugin: workflow policy (finalization + notifications)."""

import logging
from collections.abc import Callable
from typing import Any

from nexus.adapters.git.utils import build_issue_url

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

    def _resolve_project_config(
        self, *, project_name: str | None = None, repo: str | None = None
    ) -> dict[str, Any] | None:
        resolver = self._callback("resolve_project_config")
        if not resolver:
            return None
        try:
            value = resolver(project_name=project_name, repo=repo)
        except TypeError:
            try:
                value = resolver(project_name)
            except TypeError:
                value = resolver(repo)
        return value if isinstance(value, dict) else None

    def _build_issue_link(
        self, *, repo: str, issue_number: str, project_name: str | None = None
    ) -> str:
        cfg = self._resolve_project_config(project_name=project_name, repo=repo)
        return build_issue_url(str(repo), str(issue_number), cfg)

    def build_transition_message(
        self,
        *,
        issue_number: str,
        completed_agent: str,
        next_agent: str,
        repo: str,
        project_name: str | None = None,
    ) -> str:
        completed = self._format_agent(completed_agent)
        launching = self._format_agent(next_agent)
        return (
            "🔗 **Agent Transition**\n\n"
            f"Issue: #{issue_number}\n"
            f"Completed: `{completed}`\n"
            f"Launching: `{launching}`\n\n"
            f"🔗 {self._build_issue_link(repo=repo, issue_number=issue_number, project_name=project_name)}"
        )

    def build_autochain_failed_message(
        self,
        *,
        issue_number: str,
        completed_agent: str,
        next_agent: str,
        repo: str,
        project_name: str | None = None,
    ) -> str:
        completed = self._format_agent(completed_agent)
        failed = self._format_agent(next_agent)
        return (
            "❌ **Auto-chain Failed**\n\n"
            f"Issue: #{issue_number}\n"
            f"Completed: `{completed}`\n"
            f"Failed to launch: `{failed}`\n\n"
            f"🔗 {self._build_issue_link(repo=repo, issue_number=issue_number, project_name=project_name)}"
        )

    def build_workflow_complete_message(
        self,
        *,
        issue_number: str,
        last_agent: str,
        repo: str,
        project_name: str | None = None,
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
        parts.append(
            f"\n🔗 {self._build_issue_link(repo=repo, issue_number=issue_number, project_name=project_name)}"
        )
        return "".join(parts)

    def build_finalization_blocked_message(
        self,
        *,
        issue_number: str,
        repo: str,
        reasons: list[str],
        project_name: str | None = None,
    ) -> str:
        lines = [
            "⛔ **Workflow Finalization Blocked**\n\n",
            f"Issue: #{issue_number}\n",
            "Reason: non-empty PR/MR diff required before completion.\n\n",
        ]
        if reasons:
            lines.append("Details:\n")
            lines.extend(f"- {item}\n" for item in reasons if str(item).strip())
        lines.append(
            f"\n🔗 {self._build_issue_link(repo=repo, issue_number=issue_number, project_name=project_name)}"
        )
        return "".join(lines)

    def _resolve_git_dir(self, project_name: str) -> str | None:
        resolver = self._callback("resolve_git_dir")
        if not resolver:
            return None
        try:
            value = resolver(project_name=project_name)
        except TypeError:
            value = resolver(project_name)
        resolved = str(value or "").strip()
        return resolved or None

    def _resolve_git_dirs(self, project_name: str) -> dict[str, str]:
        resolver = self._callback("resolve_git_dirs")
        if not resolver:
            return {}
        try:
            value = resolver(project_name=project_name)
        except TypeError:
            value = resolver(project_name)
        if not isinstance(value, dict):
            return {}
        return {
            str(repo_name): str(path)
            for repo_name, path in value.items()
            if str(repo_name).strip() and str(path).strip()
        }

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

    @staticmethod
    def _normalize_existing_pr(candidate: Any) -> dict[str, str] | None:
        if not candidate:
            return None
        if isinstance(candidate, dict):
            url = str(candidate.get("url") or "").strip()
            if not url:
                return None
            return {
                "url": url,
                "state": str(candidate.get("state") or "").strip().lower(),
            }
        url = str(getattr(candidate, "url", "") or "").strip()
        if url:
            return {
                "url": url,
                "state": str(getattr(candidate, "state", "") or "").strip().lower(),
            }
        raw = str(candidate or "").strip()
        if not raw:
            return None
        return {"url": raw, "state": ""}

    def _find_existing_pr(self, *, repo: str, issue_number: str) -> dict[str, str] | None:
        detail_finder = self._callback("find_existing_pr_details")
        if detail_finder:
            try:
                details = self._normalize_existing_pr(
                    detail_finder(repo=repo, issue_number=str(issue_number))
                )
            except Exception as exc:
                logger.warning(
                    "Error finding existing PR details for issue #%s: %s", issue_number, exc
                )
                details = None
            if details:
                return details

        finder = self._callback("find_existing_pr")
        if not finder:
            return None

        try:
            return self._normalize_existing_pr(finder(repo=repo, issue_number=str(issue_number)))
        except Exception as exc:
            logger.warning("Error finding existing PR for issue #%s: %s", issue_number, exc)
            return None

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
        project_name: str | None = None,
        pr_urls: list[str] | None = None,
    ) -> None:
        notifier = self._callback("send_notification")
        if not notifier:
            return

        builder = (
            self._callback("build_workflow_complete_message")
            or self.build_workflow_complete_message
        )
        try:
            message = builder(
                issue_number=str(issue_number),
                last_agent=last_agent,
                repo=repo,
                project_name=project_name,
                pr_urls=pr_urls or [],
            )
        except TypeError:
            message = builder(
                issue_number=str(issue_number),
                last_agent=last_agent,
                repo=repo,
                pr_urls=pr_urls or [],
            )
        notifier(message)

    def _notify_finalization_blocked(
        self,
        *,
        repo: str,
        issue_number: str,
        project_name: str | None = None,
        reasons: list[str] | None = None,
    ) -> None:
        notifier = self._callback("send_notification")
        if not notifier:
            return

        builder = (
            self._callback("build_finalization_blocked_message")
            or self.build_finalization_blocked_message
        )
        try:
            message = builder(
                issue_number=str(issue_number),
                repo=repo,
                project_name=project_name,
                reasons=[str(item) for item in (reasons or []) if str(item).strip()],
            )
        except TypeError:
            message = builder(
                issue_number=str(issue_number),
                repo=repo,
                reasons=[str(item) for item in (reasons or []) if str(item).strip()],
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

    def _validate_pr_non_empty_diff(
        self,
        *,
        project_name: str,
        repo: str,
        issue_number: str,
        pr_url: str,
        repo_dir: str | None,
        base_branch: str | None,
        issue_repo: str | None = None,
    ) -> tuple[bool, str]:
        validator = self._callback("validate_pr_non_empty_diff")
        if not validator:
            return True, ""

        try:
            outcome = validator(
                project_name=project_name,
                repo=repo,
                issue_number=str(issue_number),
                pr_url=str(pr_url or ""),
                repo_dir=repo_dir,
                base_branch=base_branch,
                issue_repo=issue_repo,
            )
        except Exception as exc:
            logger.warning(
                "PR/MR diff validation failed for issue #%s in repo %s: %s",
                issue_number,
                repo,
                exc,
            )
            return False, f"{repo}: validation error ({exc})"

        if isinstance(outcome, tuple) and len(outcome) >= 2:
            return bool(outcome[0]), str(outcome[1] or "").strip()
        return bool(outcome), ""

    def finalize_workflow(
        self,
        *,
        issue_number: str,
        repo: str,
        last_agent: str,
        project_name: str,
        emit_notifications: bool = True,
    ) -> dict[str, Any]:
        """Finalize workflow and return outcome summary."""
        result: dict[str, Any] = {
            "pr_urls": [],
            "issue_closed": False,
            "notification_sent": False,
            "finalization_blocked": False,
            "blocking_reasons": [],
        }
        git_dirs_by_repo: dict[str, str] = {}
        has_open_pr = False
        has_terminal_existing_pr = False

        if project_name:
            try:
                git_dirs_by_repo = self._resolve_git_dirs(project_name)
            except Exception as exc:
                logger.warning("Error resolving git dirs for project %s: %s", project_name, exc)

            if not git_dirs_by_repo:
                single_dir = self._resolve_git_dir(project_name)
                if single_dir:
                    git_dirs_by_repo = {repo: single_dir}

            target_repos = list(git_dirs_by_repo.keys()) or [repo]

            for target_repo in target_repos:
                base_branch = self._resolve_repo_branch(
                    project_name=project_name,
                    repo=target_repo,
                )
                existing_pr = self._find_existing_pr(
                    repo=target_repo, issue_number=issue_number
                )
                if existing_pr:
                    existing_pr_url = str(existing_pr.get("url") or "").strip()
                    existing_pr_state = str(existing_pr.get("state") or "").strip().lower()
                    if git_dirs_by_repo.get(target_repo):
                        self._sync_existing_pr_changes(
                            repo=target_repo,
                            repo_dir=git_dirs_by_repo[target_repo],
                            issue_number=issue_number,
                            issue_repo=repo,
                            base_branch=base_branch,
                        )
                    is_valid_diff, validation_reason = self._validate_pr_non_empty_diff(
                        project_name=project_name,
                        repo=target_repo,
                        issue_number=issue_number,
                        pr_url=existing_pr_url,
                        repo_dir=git_dirs_by_repo.get(target_repo),
                        base_branch=base_branch,
                        issue_repo=repo,
                    )
                    if is_valid_diff:
                        result["pr_urls"].append(existing_pr_url)
                        if existing_pr_state and existing_pr_state not in {"open", "opened"}:
                            has_terminal_existing_pr = True
                        else:
                            has_open_pr = True
                    else:
                        result["blocking_reasons"].append(
                            validation_reason
                            or f"{target_repo}: existing PR/MR has empty diff ({existing_pr_url})"
                        )
                    continue

                git_dir = git_dirs_by_repo.get(target_repo)
                if not git_dir:
                    continue

                try:
                    created_pr_url = self._create_pr(
                        repo=target_repo,
                        repo_dir=git_dir,
                        issue_number=issue_number,
                        last_agent=last_agent,
                        issue_repo=repo,
                        base_branch=base_branch,
                    )
                    if created_pr_url:
                        is_valid_diff, validation_reason = self._validate_pr_non_empty_diff(
                            project_name=project_name,
                            repo=target_repo,
                            issue_number=issue_number,
                            pr_url=created_pr_url,
                            repo_dir=git_dir,
                            base_branch=base_branch,
                            issue_repo=repo,
                        )
                        if is_valid_diff:
                            result["pr_urls"].append(created_pr_url)
                            has_open_pr = True
                        else:
                            result["blocking_reasons"].append(
                                validation_reason
                                or f"{target_repo}: created PR/MR has empty diff ({created_pr_url})"
                            )
                except Exception as exc:
                    logger.warning(
                        "Error creating PR for issue #%s in repo %s: %s",
                        issue_number,
                        target_repo,
                        exc,
                    )

        if not result["pr_urls"]:
            result["blocking_reasons"].append(
                "No non-empty PR/MR diff found in target repos for this workflow."
            )

        if result["blocking_reasons"]:
            result["finalization_blocked"] = True
            if emit_notifications:
                try:
                    self._notify_finalization_blocked(
                        repo=repo,
                        issue_number=issue_number,
                        project_name=project_name,
                        reasons=[str(item) for item in result.get("blocking_reasons", [])],
                    )
                    result["notification_sent"] = self._callback("send_notification") is not None
                except Exception as exc:
                    logger.warning(
                        "Error sending finalization-blocked notification for issue #%s: %s",
                        issue_number,
                        exc,
                    )
            return result

        if has_terminal_existing_pr and not has_open_pr:
            result["issue_closed"] = self._close_issue(
                repo=repo,
                issue_number=issue_number,
                last_agent=last_agent,
                pr_urls=result.get("pr_urls", []),
            )

        for git_dir in set(git_dirs_by_repo.values()):
            if not git_dir:
                continue
            self._cleanup_worktree(repo_dir=git_dir, issue_number=issue_number)

        if emit_notifications:
            try:
                self._notify(
                    repo=repo,
                    issue_number=issue_number,
                    last_agent=last_agent,
                    project_name=project_name,
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
