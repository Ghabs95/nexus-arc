"""GitLab platform adapter using the GitLab REST API.

Uses only the Python standard library (``urllib``) so no extra dependencies
are required.  Pass a personal access token via ``token``.

Example::

    from nexus.adapters.git.gitlab import GitLabPlatform

    gl = GitLabPlatform(
        base_url="https://gitlab.com",
        token="glpat-…",
        repo="mygroup/myproject",
    )
"""

import asyncio
import json
import logging
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from nexus.adapters.git.base import Comment, GitPlatform, Issue, PullRequest

logger = logging.getLogger(__name__)


class GitLabPlatform(GitPlatform):
    """GitLab platform adapter (REST API v4).

    Args:
        base_url: GitLab instance base URL (default ``https://gitlab.com``).
        token: Personal access token (``glpat-…``) or deploy token.
        repo: Namespace/path of the project (e.g.  ``"mygroup/myproject"``).
    """

    def __init__(
        self,
        token: str,
        repo: str,
        base_url: str = "https://gitlab.com",
    ):
        self._token = token
        self._repo = repo
        self._encoded_repo = urllib.parse.quote(repo, safe="")
        self._api_base = f"{base_url.rstrip('/')}/api/v4"

    # ------------------------------------------------------------------
    # GitPlatform interface
    # ------------------------------------------------------------------

    async def list_open_issues(
        self,
        limit: int = 100,
        labels: list[str] | None = None,
    ) -> list[Issue]:
        query = f"state=opened&per_page={max(1, min(limit, 100))}&order_by=updated_at&sort=desc"
        if labels:
            query += "&labels=" + urllib.parse.quote(",".join(labels), safe="")
        items = await self._get(f"projects/{self._encoded_repo}/issues?{query}")
        issues = [self._to_issue(item) for item in items]
        return issues[:limit]

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Issue:
        payload: dict[str, Any] = {"title": title, "description": body}
        if labels:
            payload["labels"] = ",".join(labels)
        data = await self._post(f"projects/{self._encoded_repo}/issues", payload)
        return self._to_issue(data)

    async def get_issue(self, issue_id: str) -> Issue | None:
        try:
            data = await self._get(f"projects/{self._encoded_repo}/issues/{issue_id}")
            return self._to_issue(data)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    async def update_issue(
        self,
        issue_id: str,
        title: str | None = None,
        body: str | None = None,
        state: str | None = None,
        labels: list[str] | None = None,
    ) -> Issue:
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["description"] = body
        if state is not None:
            # GitLab uses "close" / "reopen" as state_event
            payload["state_event"] = "close" if state == "closed" else "reopen"
        if labels is not None:
            payload["labels"] = ",".join(labels)
        data = await self._put(f"projects/{self._encoded_repo}/issues/{issue_id}", payload)
        return self._to_issue(data)

    async def add_comment(self, issue_id: str, body: str) -> Comment:
        data = await self._post(
            f"projects/{self._encoded_repo}/issues/{issue_id}/notes",
            {"body": body},
        )
        return self._to_comment(data, issue_id)

    async def get_comments(self, issue_id: str, since: datetime | None = None) -> list[Comment]:
        url = f"projects/{self._encoded_repo}/issues/{issue_id}/notes?per_page=100&sort=asc"
        items = await self._get(url)
        comments = [self._to_comment(c, issue_id) for c in items]
        if since:
            comments = [c for c in comments if c.created_at >= since]
        return comments

    async def close_issue(self, issue_id: str, comment: str | None = None) -> None:
        if comment:
            await self.add_comment(issue_id, comment)
        await self.update_issue(issue_id, state="closed")

    async def search_linked_prs(self, issue_id: str) -> list[PullRequest]:
        """Find merge requests linked to an issue."""
        issue_token = str(issue_id or "").strip()
        if not issue_token:
            return []

        issue_ref_pattern = re.compile(
            rf"(?:#{re.escape(issue_token)}\b|{re.escape(self._repo)}#{re.escape(issue_token)}\b|"
            rf"{re.escape(self._repo)}/-/issues/{re.escape(issue_token)}\b|/-/issues/{re.escape(issue_token)}\b)",
            re.IGNORECASE,
        )

        # Prefer direct open-MR scanning over search indexing.
        try:
            open_mrs = await self._get(
                f"projects/{self._encoded_repo}/merge_requests?state=open&per_page=100"
            )
            linked_open_mrs: list[PullRequest] = []
            for mr in open_mrs if isinstance(open_mrs, list) else []:
                title = str(mr.get("title") or "")
                body = str(mr.get("description") or "")
                if issue_ref_pattern.search(f"{title}\n{body}"):
                    linked_open_mrs.append(self._to_pr(mr))
            if linked_open_mrs:
                return linked_open_mrs
        except Exception:
            pass

        mrs = await self._get(
            f"projects/{self._encoded_repo}/merge_requests"
            f"?state=all&search=%23{issue_token}&per_page=20"
        )
        return [self._to_pr(mr) for mr in mrs]

    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        data = await self._post(
            f"projects/{self._encoded_repo}/repository/branches",
            {"branch": branch_name, "ref": base_branch},
        )
        return data.get("web_url", branch_name)

    async def merge_pull_request(
        self,
        pr_id: str,
        *,
        squash: bool = True,
        delete_branch: bool = True,
        auto: bool = True,
    ) -> str:
        """Merge a GitLab merge request by IID."""
        payload: dict[str, Any] = {
            "should_remove_source_branch": bool(delete_branch),
            "squash": bool(squash),
        }
        if auto:
            payload["merge_when_pipeline_succeeds"] = True
        data = await self._put(
            f"projects/{self._encoded_repo}/merge_requests/{pr_id}/merge",
            payload,
        )
        state = str(data.get("state") or "unknown")
        web_url = str(data.get("web_url") or "")
        return f"state={state} {web_url}".strip()

    async def ensure_label(
        self,
        name: str,
        *,
        color: str,
        description: str = "",
    ) -> bool:
        """Ensure a GitLab project label exists."""
        try:
            await self._post(
                f"projects/{self._encoded_repo}/labels",
                {
                    "name": str(name),
                    "color": str(color),
                    "description": str(description or ""),
                },
            )
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 409:
                return True
            logger.warning("Failed to ensure GitLab label %s: HTTP %s", name, exc.code)
            return False
        except Exception as exc:
            logger.warning("Failed to ensure GitLab label %s: %s", name, exc)
            return False

    async def create_pr_from_changes(
        self,
        repo_dir: str,
        issue_number: str,
        title: str,
        body: str,
        issue_repo: str | None = None,
        base_branch: str = "main",
        branch_prefix: str = "nexus",
    ) -> PullRequest | None:
        """Push local changes and open a GitLab merge request."""
        import subprocess

        issue_repo_ref = (issue_repo or self._repo or "").strip()
        same_repo_issue = not issue_repo_ref or issue_repo_ref == self._repo
        issue_ref = f"#{issue_number}" if same_repo_issue else f"{issue_repo_ref}#{issue_number}"

        import re as _re

        issue_url = (
            rf"https?://[^\s]+/{_re.escape(issue_repo_ref)}/-/issues/{_re.escape(issue_number)}"
        )
        closing_pattern = _re.compile(
            rf"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
            rf"(?:{_re.escape(issue_ref)}|{issue_url})\b",
            _re.IGNORECASE,
        )
        if issue_number and not closing_pattern.search(body):
            body = f"{body}\n\nCloses {issue_ref}"

        branch_name = f"{branch_prefix}/issue-{issue_number}"
        current_branch = base_branch
        current_branch_result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            cwd=repo_dir,
        )
        if current_branch_result.returncode == 0:
            detected = (current_branch_result.stdout or "").strip()
            if detected:
                current_branch = detected

        long_lived = {"main", "master", "develop", "development", "dev", "HEAD"}
        use_current_branch = bool(current_branch and current_branch not in long_lived)
        if use_current_branch:
            branch_name = current_branch

        try:
            # Stage + commit + push
            result = subprocess.run(
                ["git", "diff", "--cached", "--name-only"],
                capture_output=True,
                cwd=repo_dir,
            )
            if not result.stdout.strip():
                subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)

            status = subprocess.run(
                ["git", "status", "--porcelain"],
                capture_output=True,
                cwd=repo_dir,
            )
            if not status.stdout.strip():
                logger.info("No local changes detected — skipping MR creation")
                return None

            if not use_current_branch:
                subprocess.run(["git", "checkout", "-B", branch_name], cwd=repo_dir, check=True)
            subprocess.run(
                ["git", "commit", "-m", f"nexus: {title} (closes #{issue_number})"],
                cwd=repo_dir,
                check=True,
            )
            subprocess.run(
                ["git", "push", "--set-upstream", "origin", branch_name, "--force"],
                cwd=repo_dir,
                check=True,
            )

            data = await self._post(
                f"projects/{self._encoded_repo}/merge_requests",
                {
                    "source_branch": branch_name,
                    "target_branch": base_branch,
                    "title": title,
                    "description": body,
                    "remove_source_branch": True,
                },
            )
            return self._to_pr(data)
        finally:
            subprocess.run(
                ["git", "checkout", current_branch],
                cwd=repo_dir,
                check=False,
                capture_output=True,
            )

    # ------------------------------------------------------------------
    # HTTP helpers (sync + asyncio.to_thread wrapper)
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        token = str(self._token or "").strip()
        headers = {"Content-Type": "application/json"}
        # GitLab PATs/project tokens typically use PRIVATE-TOKEN; OAuth access tokens require Bearer.
        if token.startswith("glpat-") or token.startswith("gltok-"):
            headers["PRIVATE-TOKEN"] = token
        else:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _sync_request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self._api_base}/{path.lstrip('/')}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                return json.loads(resp.read())
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            logger.error("GitLab API %s %s → HTTP %d: %s", method, path, exc.code, body)
            raise

    async def _get(self, path: str) -> Any:
        return await asyncio.to_thread(self._sync_request, "GET", path)

    async def _post(self, path: str, payload: dict) -> Any:
        return await asyncio.to_thread(self._sync_request, "POST", path, payload)

    async def _put(self, path: str, payload: dict) -> Any:
        return await asyncio.to_thread(self._sync_request, "PUT", path, payload)

    # ------------------------------------------------------------------
    # Model converters
    # ------------------------------------------------------------------

    @staticmethod
    def _parse_dt(val: str | None) -> datetime:
        if not val:
            return datetime.now(tz=UTC)
        # GitLab returns ISO 8601 with 'Z' or '+HH:MM'
        return datetime.fromisoformat(val.replace("Z", "+00:00"))

    def _to_issue(self, data: dict) -> Issue:
        return Issue(
            id=str(data["id"]),
            number=data["iid"],
            title=data.get("title", ""),
            body=data.get("description") or "",
            state=data.get("state", "opened").replace("opened", "open"),
            labels=[lbl if isinstance(lbl, str) else lbl["name"] for lbl in data.get("labels", [])],
            created_at=self._parse_dt(data.get("created_at")),
            updated_at=self._parse_dt(data.get("updated_at")),
            url=data.get("web_url", ""),
        )

    @staticmethod
    def _to_comment(data: dict, issue_id: str) -> Comment:
        return Comment(
            id=str(data["id"]),
            issue_id=str(issue_id),
            author=data.get("author", {}).get("username", "unknown"),
            body=data.get("body", ""),
            created_at=GitLabPlatform._parse_dt(data.get("created_at")),
            url=data.get("noteable_url", ""),
        )

    @staticmethod
    def _to_pr(data: dict) -> PullRequest:
        return PullRequest(
            id=str(data["id"]),
            number=data.get("iid", 0),
            title=data.get("title", ""),
            state=data.get("state", "opened").replace("opened", "open"),
            head_branch=data.get("source_branch", ""),
            base_branch=data.get("target_branch", "main"),
            url=data.get("web_url", ""),
        )
