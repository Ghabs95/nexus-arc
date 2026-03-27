"""Built-in plugin: GitHub webhook policy.

Normalizes GitHub webhook payloads into stable event dictionaries and provides
small policy helpers for webhook-side decisions.
"""

import hashlib
import hmac
import re
from typing import Any


class GitWebhookPolicyPlugin:
    """Policy helper for GitHub and GitLab webhook event normalization."""

    def __init__(self, config: dict[str, Any] | None = None):
        self.config = config or {}

    def parse_issue_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized issue event data from a GitHub or GitLab issues payload."""
        if "object_kind" in payload and payload["object_kind"] == "issue":
            issue = payload.get("object_attributes", {})
            project = payload.get("project", {})
            labels = payload.get("labels", [])
            label_names = [l.get("title") for l in labels if isinstance(l, dict)]
            return {
                "action": issue.get("action"),
                "number": str(issue.get("iid", "")),
                "title": issue.get("title", ""),
                "body": issue.get("description", ""),
                "url": issue.get("url", ""),
                "author": (payload.get("user", {}) or {}).get("username", ""),
                "closed_by": (payload.get("user", {}) or {}).get("username", "unknown"),
                "labels": label_names,
                "repo": project.get("path_with_namespace", "unknown"),
            }

        issue = payload.get("issue", {}) or {}
        repository = payload.get("repository", {}) or {}

        labels = issue.get("labels", []) or []
        label_names = [
            label.get("name") for label in labels if isinstance(label, dict) and label.get("name")
        ]

        return {
            "action": payload.get("action"),
            "number": str(issue.get("number", "")),
            "title": issue.get("title", ""),
            "body": issue.get("body", ""),
            "url": issue.get("html_url", ""),
            "author": issue["user"].get("login", "") if isinstance(issue.get("user"), dict) else "",
            "closed_by": (
                payload["sender"].get("login", "unknown")
                if isinstance(payload.get("sender"), dict)
                else "unknown"
            ),
            "labels": label_names,
            "repo": repository.get("full_name", "unknown"),
        }

    def parse_pull_request_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized pull request event data from a GitHub/GitLab PR payload."""
        if "object_kind" in payload and payload["object_kind"] == "merge_request":
            mr = payload.get("object_attributes", {})
            project = payload.get("project", {})
            actor = (
                payload["user"].get("username", "unknown")
                if isinstance(payload.get("user"), dict)
                else "unknown"
            )
            merged = str(mr.get("state", "")).strip().lower() == "merged"
            raw_action = str(mr.get("action", "") or "").strip().lower()
            if merged or raw_action in {"merge", "merged"}:
                action = "merged"
            elif raw_action in {"close", "closed"} or str(mr.get("state", "")).strip().lower() == "closed":
                action = "closed"
            else:
                action = raw_action or str(mr.get("state", "")).strip().lower()
            merge_user = payload.get("merge_user") or payload.get("user") or {}
            return {
                "action": action,
                "number": mr.get("iid"),
                "title": mr.get("title", ""),
                "url": mr.get("url", ""),
                "author": actor,
                "merged": merged,
                "merged_by": (
                    merge_user.get("username", "unknown") if merged and isinstance(merge_user, dict) else "unknown"
                ),
                "closed_by": actor if action == "closed" and not merged else "unknown",
                "repo": project.get("path_with_namespace", "unknown"),
            }

        pr = payload.get("pull_request", {}) or {}
        repository = payload.get("repository", {}) or {}
        sender = payload.get("sender") or {}

        return {
            "action": payload.get("action"),
            "number": pr.get("number"),
            "title": pr.get("title", ""),
            "url": pr.get("html_url", ""),
            "author": pr["user"].get("login", "") if isinstance(pr.get("user"), dict) else "",
            "merged": bool(pr.get("merged")),
            "merged_by": (
                pr["merged_by"].get("login", "unknown")
                if isinstance(pr.get("merged_by"), dict)
                else "unknown"
            ),
            "closed_by": sender.get("login", "unknown") if isinstance(sender, dict) else "unknown",
            "repo": repository.get("full_name", "unknown"),
        }

    def parse_issue_comment_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized issue_comment event data."""
        if "object_kind" in payload and payload["object_kind"] == "note":
            note = payload.get("object_attributes", {})
            issue = payload.get("issue", {})
            project = payload.get("project", {})
            return {
                "action": "created",
                "comment_id": note.get("id"),
                "comment_body": note.get("note", ""),
                "comment_author": (
                    payload["user"].get("username", "")
                    if isinstance(payload.get("user"), dict)
                    else ""
                ),
                "issue_number": str(issue.get("iid", note.get("noteable_iid", ""))),
                "issue": issue,
                "repo": project.get("path_with_namespace", "unknown"),
            }

        comment = payload.get("comment", {}) or {}
        issue = payload.get("issue", {}) or {}
        repository = payload.get("repository", {}) or {}

        return {
            "action": payload.get("action"),
            "comment_id": comment.get("id"),
            "comment_body": comment.get("body", ""),
            "comment_author": (
                comment["user"].get("login", "") if isinstance(comment.get("user"), dict) else ""
            ),
            "issue_number": str(issue.get("number", "")),
            "issue": issue,
            "repo": repository.get("full_name", "unknown"),
        }

    def parse_pull_request_review_event(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Return normalized pull_request_review event data."""
        review = payload.get("review", {}) or {}
        pr = payload.get("pull_request", {}) or {}

        return {
            "action": payload.get("action"),
            "pr_number": pr.get("number"),
            "review_state": review.get("state"),
            "reviewer": (
                review["user"].get("login", "") if isinstance(review.get("user"), dict) else ""
            ),
        }

    def dispatch_event(self, event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Dispatch raw Git event type to normalized route + event payload."""
        # GitLab mapping
        if event_type == "Issue Hook" and payload.get("object_kind") == "issue":
            return {"route": "issues", "event": self.parse_issue_event(payload)}
        if event_type == "Note Hook" and payload.get("object_kind") == "note":
            noteable_type = payload.get("object_attributes", {}).get("noteable_type")
            if noteable_type == "Issue":
                return {"route": "issue_comment", "event": self.parse_issue_comment_event(payload)}
            return {
                "route": "unhandled",
                "event": {"event_type": event_type, "noteable_type": noteable_type},
            }
        if event_type == "Merge Request Hook" and payload.get("object_kind") == "merge_request":
            return {"route": "pull_request", "event": self.parse_pull_request_event(payload)}

        # GitHub mapping
        if event_type == "issues":
            return {"route": "issues", "event": self.parse_issue_event(payload)}
        if event_type == "issue_comment":
            return {"route": "issue_comment", "event": self.parse_issue_comment_event(payload)}
        if event_type == "pull_request":
            return {"route": "pull_request", "event": self.parse_pull_request_event(payload)}
        if event_type == "pull_request_review":
            return {
                "route": "pull_request_review",
                "event": self.parse_pull_request_review_event(payload),
            }
        if event_type == "ping":
            return {"route": "ping", "event": {}}
        return {"route": "unhandled", "event": {"event_type": event_type}}

    @staticmethod
    def _normalize_review_mode(value: Any, default: str = "manual") -> str:
        normalized = str(value or "").strip().lower()
        if normalized in {"manual", "auto"}:
            return normalized
        return default

    def should_notify_pr_merged(self, review_mode: str) -> bool:
        """Backward-compatible helper retained for older callers.

        PR/MR lifecycle outcomes should always reach the human-facing surface;
        review mode only affects merge policy, not whether close/merge events are
        surfaced.
        """
        _ = review_mode
        return True

    def verify_signature(
        self,
        payload_body: bytes,
        signature_header: str | None,
        secret: str | None,
        gitlab_token_header: str | None = None,
    ) -> bool:
        """Verify GitHub or GitLab webhook signature/token."""
        if not secret:
            return True

        if gitlab_token_header is not None:
            return hmac.compare_digest(secret, gitlab_token_header)

        if not signature_header or "=" not in signature_header:
            return False

        hash_algorithm, github_signature = str(signature_header).split("=", 1)
        if hash_algorithm != "sha256":
            return False

        mac = hmac.new(
            str(secret).encode("utf-8"),
            msg=payload_body,
            digestmod=hashlib.sha256,
        )
        expected_signature = mac.hexdigest()
        return hmac.compare_digest(expected_signature, github_signature)

    def resolve_project_key(
        self,
        repo_name: str,
        project_config: dict[str, Any],
        default_project: str = "nexus",
    ) -> str:
        """Resolve project key from repository full name."""
        for project_key, project_cfg in (project_config or {}).items():
            if not isinstance(project_cfg, dict):
                continue

            single_repo = project_cfg.get("git_repo")
            if isinstance(single_repo, str) and single_repo == repo_name:
                return project_key

            repo_list = project_cfg.get("git_repos")
            if isinstance(repo_list, list) and repo_name in repo_list:
                return project_key
        return default_project

    def resolve_review_mode(
        self,
        repo_name: str,
        project_config: dict[str, Any],
        default_mode: str = "manual",
    ) -> str:
        """Resolve effective merge review mode for a repository."""
        project_key = self.resolve_project_key(repo_name, project_config)
        project_cfg = (project_config or {}).get(project_key, {})
        global_merge_queue = (project_config or {}).get("merge_queue", {})
        project_merge_queue = (
            project_cfg.get("merge_queue", {}) if isinstance(project_cfg, dict) else {}
        )

        if isinstance(project_merge_queue, dict) and "review_mode" in project_merge_queue:
            return self._normalize_review_mode(
                project_merge_queue.get("review_mode"), default=default_mode
            )
        if isinstance(global_merge_queue, dict) and "review_mode" in global_merge_queue:
            return self._normalize_review_mode(
                global_merge_queue.get("review_mode"), default=default_mode
            )
        return self._normalize_review_mode(default_mode, default="manual")

    def build_issue_closed_message(self, event: dict[str, Any]) -> str:
        """Build issue-closed lifecycle notification message."""
        return (
            "🔒 **Issue Closed**\n\n"
            f"Issue: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Closed by: @{event.get('closed_by', 'unknown')}\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def build_issue_created_message(self, event: dict[str, Any], agent_type: str) -> str:
        """Build issue-created lifecycle notification message."""
        return (
            "📥 **Issue Created**\n\n"
            f"Issue: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Author: @{event.get('author', '')}\n"
            f"Routed to: `{agent_type}`\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def build_pr_created_message(self, event: dict[str, Any]) -> str:
        """Build PR-created lifecycle notification message."""
        return (
            "🔀 **PR Created**\n\n"
            f"PR: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Author: @{event.get('author', '')}\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def build_pr_merged_message(self, event: dict[str, Any], review_mode: str) -> str:
        """Build PR-merged lifecycle notification message."""
        return (
            "✅ **PR Merged**\n\n"
            f"PR: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Merged by: @{event.get('merged_by', 'unknown')}\n"
            f"Review mode: `{self._normalize_review_mode(review_mode)}`\n"
            "Cleanup: `eligible for local worktree cleanup`\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def build_pr_closed_unmerged_message(self, event: dict[str, Any]) -> str:
        """Build PR/MR closed-without-merge lifecycle notification message."""
        return (
            "🛑 **PR/MR Closed Without Merge**\n\n"
            f"PR/MR: #{event.get('number', '')}\n"
            f"Title: {event.get('title', '')}\n"
            f"Repository: {event.get('repo', 'unknown')}\n"
            f"Closed by: @{event.get('closed_by', 'unknown')}\n"
            "Cleanup: `conservative (no auto-cleanup)`\n\n"
            f"🔗 {event.get('url', '')}"
        )

    def determine_project_from_issue(self, issue: dict[str, Any]) -> str:
        """Best-effort project key detection from issue labels/body."""
        labels = issue.get("labels", []) or []
        for label in labels:
            label_name = ""
            if isinstance(label, dict):
                label_name = str(label.get("name", "")).lower()
            elif isinstance(label, str):
                label_name = label.lower()

            project_match = re.search(
                r"(?:^|[:/\s_-])project[:/\s_-]?([a-z0-9][a-z0-9_-]*)", label_name
            )
            if project_match:
                return project_match.group(1).replace("-", "_")

            workspace_match = re.search(
                r"(?:^|[:/\s_-])workspace[:/\s_-]?([a-z0-9][a-z0-9_-]*)", label_name
            )
            if workspace_match:
                return workspace_match.group(1).replace("-", "_")

        body = str(issue.get("body", "")).lower()
        body_match = re.search(r"\b(?:project|workspace)[:\s_-]+([a-z0-9][a-z0-9_-]*)", body)
        if body_match:
            return body_match.group(1).replace("-", "_")

        return "default"


def register_plugins(registry) -> None:
    """Register built-in GitHub webhook policy plugin."""
    from nexus.plugins import PluginKind  # type: ignore[import-untyped]

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="git-webhook-policy",
        version="0.1.0",
        factory=lambda config: GitWebhookPolicyPlugin(config),
        description="GitHub webhook payload normalization and policy helpers",
    )
