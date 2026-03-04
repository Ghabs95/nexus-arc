from __future__ import annotations

import asyncio
from typing import Any, Callable, Iterable


def build_workflow_issue_number_lister(
    *,
    get_workflow_monitor_policy_plugin: Callable[..., Any],
    get_git_platform: Callable[..., Any],
    workflow_labels: Iterable[str],
) -> Callable[[str, str], list[int]]:
    labels = sorted({str(label) for label in workflow_labels if str(label).strip()})

    def _list_workflow_issue_numbers(project_name: str, repo: str) -> list[int]:
        monitor_policy = get_workflow_monitor_policy_plugin(
            list_open_issues=lambda **kwargs: asyncio.run(
                get_git_platform(kwargs["repo"], project_name=project_name).list_open_issues(
                    limit=kwargs["limit"],
                    labels=labels,
                )
            ),
            cache_key=None,
        )
        return monitor_policy.list_workflow_issue_numbers(
            repo=repo,
            workflow_labels=set(labels),
            limit=100,
        )

    return _list_workflow_issue_numbers


def build_bot_comments_getter(
    *,
    get_workflow_monitor_policy_plugin: Callable[..., Any],
    get_git_platform: Callable[..., Any],
    bot_author: str = "Ghabs95",
) -> Callable[[str, str, str], list[Any]]:
    def _get_bot_comments(project_name: str, repo: str, issue_number: str):
        monitor_policy = get_workflow_monitor_policy_plugin(
            get_comments=lambda **kwargs: asyncio.run(
                get_git_platform(kwargs["repo"], project_name=project_name).get_comments(
                    str(kwargs["issue_number"])
                )
            ),
            cache_key=None,
        )
        return monitor_policy.get_bot_comments(
            repo=repo,
            issue_number=str(issue_number),
            bot_author=bot_author,
        )

    return _get_bot_comments


def check_and_notify_pr(
    *,
    issue_num: Any,
    project: str,
    logger: Any,
    get_repo: Callable[[str], str],
    get_workflow_monitor_policy_plugin: Callable[..., Any],
    get_git_platform: Callable[..., Any],
    notify_workflow_completed: Callable[..., Any],
) -> None:
    try:
        repo = get_repo(project)
        monitor_policy = get_workflow_monitor_policy_plugin(
            search_linked_prs=lambda **kwargs: asyncio.run(
                get_git_platform(kwargs["repo"], project_name=project).search_linked_prs(
                    str(kwargs["issue_number"])
                )
            ),
            cache_key=None,
        )
        pr = monitor_policy.find_open_linked_pr(repo=repo, issue_number=str(issue_num))
        if pr:
            logger.info(f"✅ Found PR #{pr.number} for issue #{issue_num}")
            notify_workflow_completed(issue_num, project, pr_urls=[pr.url])
            return

        logger.info(f"ℹ️ No open PR found for issue #{issue_num}")
        notify_workflow_completed(issue_num, project)
    except Exception as exc:
        logger.error(f"Error checking for PR: {exc}")
        notify_workflow_completed(issue_num, project)
