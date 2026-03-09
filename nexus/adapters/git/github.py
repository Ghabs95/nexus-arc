"""GitHub platform adapter using the GitHub REST API."""

import asyncio
import json
import logging
import os
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from datetime import UTC, datetime
from typing import Any

from nexus.adapters.git.base import Comment, GitPlatform, Issue, PullRequest

logger = logging.getLogger(__name__)


class GitHubPlatform(GitPlatform):
    """GitHub platform adapter backed by the GitHub REST API."""

    def __init__(self, repo: str, token: str | None = None):
        self.repo = repo
        self.token = str(token or "").strip() or None
        self._api_base = "https://api.github.com"
        self._check_gh_cli()

    def _check_gh_cli(self) -> None:
        """Backward-compatible init hook.

        Historically this validated the ``gh`` CLI. The adapter is API-backed
        now, so the hook only resolves token fallback from the environment.
        """

        if self.token:
            return
        env_token = str(os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
        if env_token:
            self.token = env_token
        else:
            logger.warning(
                "GitHub token missing for repo=%s. Authenticated write operations will fail.",
                self.repo,
            )

    async def list_open_issues(
        self,
        limit: int = 100,
        labels: list[str] | None = None,
    ) -> list[Issue]:
        query = f"state=open&per_page={max(1, min(limit, 100))}"
        if labels:
            query += "&labels=" + urllib.parse.quote(",".join(labels), safe="")
        try:
            data = await self._get(f"repos/{self.repo}/issues?{query}")
            issues = [self._to_issue(item) for item in data if "pull_request" not in item]
            return issues[:limit]
        except RuntimeError:
            return []

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Issue:
        payload: dict[str, Any] = {"title": title, "body": body}
        if labels:
            payload["labels"] = labels
        data = await self._post(f"repos/{self.repo}/issues", payload)
        return self._to_issue(data)

    async def get_issue(self, issue_id: str) -> Issue | None:
        try:
            data = await self._get(f"repos/{self.repo}/issues/{issue_id}")
            if "pull_request" in data:
                return None
            return self._to_issue(data)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError(f"GitHub API error: HTTP {exc.code}") from exc
        except RuntimeError:
            return None

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
            payload["body"] = body
        if state is not None:
            payload["state"] = state
        if labels is not None:
            payload["labels"] = labels
        data = await self._patch(f"repos/{self.repo}/issues/{issue_id}", payload)
        return self._to_issue(data)

    async def add_comment(self, issue_id: str, body: str) -> Comment:
        data = await self._post(f"repos/{self.repo}/issues/{issue_id}/comments", {"body": body})
        return self._to_comment(data, issue_id)

    async def get_comments(self, issue_id: str, since: datetime | None = None) -> list[Comment]:
        try:
            items = await self._get(
                f"repos/{self.repo}/issues/{issue_id}/comments?per_page=100&sort=created&direction=asc"
            )
            comments = [self._to_comment(item, issue_id) for item in items]
            if since:
                comments = [comment for comment in comments if comment.created_at >= since]
            return comments
        except RuntimeError:
            return []

    async def close_issue(self, issue_id: str, comment: str | None = None) -> None:
        if comment:
            await self.add_comment(issue_id, comment)
        await self.update_issue(issue_id, state="closed")
        logger.info("Closed issue #%s", issue_id)

    async def search_linked_prs(self, issue_id: str) -> list[PullRequest]:
        issue_token = str(issue_id or "").strip()
        if not issue_token:
            return []

        issue_ref_pattern = re.compile(
            rf"(?:#{re.escape(issue_token)}\b|{re.escape(self.repo)}#{re.escape(issue_token)}\b|"
            rf"https?://github\.com/{re.escape(self.repo)}/issues/{re.escape(issue_token)}\b)",
            re.IGNORECASE,
        )

        # Prefer direct open-PR scanning over Search API to avoid index lag.
        try:
            pulls = await self._get(f"repos/{self.repo}/pulls?state=open&per_page=100")
            linked_open_prs: list[PullRequest] = []
            for item in pulls if isinstance(pulls, list) else []:
                title = str(item.get("title") or "")
                body = str(item.get("body") or "")
                if issue_ref_pattern.search(f"{title}\n{body}"):
                    linked_open_prs.append(self._to_pr(item, linked_issues=[issue_token]))
            if linked_open_prs:
                return linked_open_prs
        except RuntimeError:
            pass

        query = urllib.parse.quote(f'repo:{self.repo} is:pr "#{issue_token}"')
        try:
            search_data = await self._get(f"search/issues?q={query}&per_page=20")
            prs: list[PullRequest] = []
            for item in search_data.get("items", []):
                pr_number = str(item.get("number", "")).strip()
                if not pr_number:
                    continue
                detail = await self._get(f"repos/{self.repo}/pulls/{pr_number}")
                prs.append(self._to_pr(detail, linked_issues=[issue_token]))
            return prs
        except RuntimeError:
            return []

    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        ref = await self._get(
            f"repos/{self.repo}/git/ref/heads/{urllib.parse.quote(base_branch, safe='')}"
        )
        sha = str(ref.get("object", {}).get("sha", "")).strip()
        if not sha:
            raise RuntimeError(f"Could not resolve base branch '{base_branch}' for {self.repo}")
        data = await self._post(
            f"repos/{self.repo}/git/refs",
            {"ref": f"refs/heads/{branch_name}", "sha": sha},
        )
        return str(data.get("url") or f"https://github.com/{self.repo}/tree/{branch_name}")

    async def merge_pull_request(
        self,
        pr_id: str,
        *,
        squash: bool = True,
        delete_branch: bool = True,
        auto: bool = True,
    ) -> str:
        pr = await self._get(f"repos/{self.repo}/pulls/{pr_id}")
        merge_data = await self._put(
            f"repos/{self.repo}/pulls/{pr_id}/merge",
            {"merge_method": "squash" if squash else "merge"},
        )
        if delete_branch:
            head_ref = str(pr.get("head", {}).get("ref", "")).strip()
            if head_ref:
                try:
                    await self._delete(
                        f"repos/{self.repo}/git/refs/heads/{urllib.parse.quote(head_ref, safe='')}"
                    )
                except RuntimeError as exc:
                    logger.warning("Failed to delete merged GitHub branch %s: %s", head_ref, exc)
        merged = bool(merge_data.get("merged"))
        return f"merged={str(merged).lower()} auto_requested={str(bool(auto)).lower()}"

    async def ensure_label(
        self,
        name: str,
        *,
        color: str,
        description: str = "",
    ) -> bool:
        try:
            await self._post(
                f"repos/{self.repo}/labels",
                {"name": str(name), "color": str(color), "description": str(description or "")},
            )
            return True
        except urllib.error.HTTPError as exc:
            body = getattr(exc, "_nexus_body", "")
            if exc.code == 422 and "already_exists" in str(body):
                return True
            logger.warning("Failed to ensure GitHub label %s: HTTP %s %s", name, exc.code, body)
            return False
        except RuntimeError as exc:
            logger.warning("Failed to ensure GitHub label %s: %s", name, exc)
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
        """Create a PR from uncommitted changes in a local repository."""
        import re as _re

        git_repo_probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_dir,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if git_repo_probe.returncode != 0:
            logger.warning("Not a git repo: %s", repo_dir)
            return None

        issue_repo_ref = (issue_repo or self.repo or "").strip()
        same_repo_issue = not issue_repo_ref or issue_repo_ref == self.repo
        if same_repo_issue:
            issue_ref = f"#{issue_number}"
            closing_ref_pattern = rf"(?:#{_re.escape(issue_number)}|{_re.escape(self.repo)}#{_re.escape(issue_number)})"
        else:
            issue_ref = f"{issue_repo_ref}#{issue_number}"
            issue_url = rf"https?://github\.com/{_re.escape(issue_repo_ref)}/issues/{_re.escape(issue_number)}"
            closing_ref_pattern = (
                rf"(?:{_re.escape(issue_repo_ref)}#{_re.escape(issue_number)}|{issue_url})"
            )

        closing_pattern = _re.compile(
            rf"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+{closing_ref_pattern}\b",
            _re.IGNORECASE,
        )
        if issue_number and not closing_pattern.search(body):
            body = f"{body}\n\nCloses {issue_ref}"

        def _git(args: list[str], timeout: int = 30) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git"] + args,
                cwd=repo_dir,
                text=True,
                capture_output=True,
                timeout=timeout,
            )

        diff = _git(["diff", "--stat", "HEAD"])
        staged = _git(["diff", "--cached", "--stat"])
        untracked = _git(["ls-files", "--others", "--exclude-standard"])
        has_changes = bool(
            (diff.stdout and diff.stdout.strip())
            or (staged.stdout and staged.stdout.strip())
            or (untracked.stdout and untracked.stdout.strip())
        )
        if not has_changes:
            logger.info("No uncommitted changes in %s — skipping PR creation", repo_dir)
            return None

        current = _git(["rev-parse", "--abbrev-ref", "HEAD"])
        current_branch = current.stdout.strip()
        resolved_base = str(base_branch or "main").strip() or "main"
        long_lived = {"main", "master", "develop", "development", "dev", "HEAD"}
        use_current_branch = bool(current_branch and current_branch not in long_lived)
        branch_name = (
            current_branch if use_current_branch else f"{branch_prefix}/issue-{issue_number}"
        )
        switched_branch = False

        try:
            if not use_current_branch:
                result = _git(["checkout", "-b", branch_name])
                if result.returncode != 0:
                    logger.warning("Could not create branch %s: %s", branch_name, result.stderr)
                    return None
                switched_branch = True

            _git(["add", "-A"])
            _git(["commit", "-m", f"feat: resolve issue #{issue_number} (automated by Nexus)"])

            push = _git(["push", "-u", "origin", branch_name], timeout=60)
            if push.returncode != 0:
                logger.warning("Could not push branch %s: %s", branch_name, push.stderr)
                return None

            pr_data = await self._post(
                f"repos/{self.repo}/pulls",
                {
                    "title": title,
                    "body": body,
                    "head": branch_name,
                    "base": resolved_base,
                },
            )
            logger.info("Created PR for issue #%s: %s", issue_number, pr_data.get("html_url", ""))
            return self._to_pr(pr_data, linked_issues=[str(issue_number)])
        except Exception as exc:
            logger.warning("Error during PR creation for issue #%s: %s", issue_number, exc)
            return None
        finally:
            if switched_branch and current_branch and current_branch != "HEAD":
                _git(["checkout", current_branch])

    def get_workflow_type_from_issue(
        self,
        issue_number: int,
        label_prefix: str = "workflow:",
        default: str | None = None,
    ) -> str | None:
        from nexus.core.workflow import WorkflowDefinition

        try:
            data = self._sync_request("GET", f"repos/{self.repo}/issues/{issue_number}")
            labels = []
            for label in data.get("labels", []):
                if isinstance(label, dict):
                    labels.append(str(label.get("name", "")))
                else:
                    labels.append(str(label))
            for label in labels:
                if label.startswith(label_prefix):
                    raw = label[len(label_prefix) :]
                    return WorkflowDefinition.normalize_workflow_type(raw, default=default)
            return default
        except Exception as exc:
            logger.error("Failed to get workflow_type from issue #%s: %s", issue_number, exc)
            return default

    def _headers(self) -> dict[str, str]:
        headers = {
            "Accept": "application/vnd.github+json",
            "Content-Type": "application/json",
            "User-Agent": "nexus-arc",
        }
        token = str(self.token or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        return headers

    def _sync_request(self, method: str, path: str, payload: dict | None = None) -> Any:
        url = f"{self._api_base}/{path.lstrip('/')}"
        data = json.dumps(payload).encode() if payload is not None else None
        req = urllib.request.Request(url, data=data, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                body = resp.read()
                if not body:
                    return {}
                return json.loads(body)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode(errors="replace")
            setattr(exc, "_nexus_body", body)
            logger.error("GitHub API %s %s → HTTP %d: %s", method, path, exc.code, body)
            raise
        except urllib.error.URLError as exc:
            raise RuntimeError(f"GitHub API request failed: {exc.reason}") from exc

    async def _get(self, path: str) -> Any:
        return await asyncio.to_thread(self._sync_request, "GET", path)

    async def _post(self, path: str, payload: dict) -> Any:
        return await asyncio.to_thread(self._sync_request, "POST", path, payload)

    async def _patch(self, path: str, payload: dict) -> Any:
        return await asyncio.to_thread(self._sync_request, "PATCH", path, payload)

    async def _put(self, path: str, payload: dict) -> Any:
        return await asyncio.to_thread(self._sync_request, "PUT", path, payload)

    async def _delete(self, path: str) -> Any:
        return await asyncio.to_thread(self._sync_request, "DELETE", path)

    @staticmethod
    def _parse_dt(value: str | None) -> datetime:
        if not value:
            return datetime.now(tz=UTC)
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    def _to_issue(self, data: dict) -> Issue:
        labels = []
        for label in data.get("labels", []):
            if isinstance(label, dict):
                labels.append(str(label.get("name", "")))
            else:
                labels.append(str(label))
        return Issue(
            id=str(data.get("id") or data.get("number") or ""),
            number=int(data.get("number") or 0),
            title=str(data.get("title") or ""),
            body=str(data.get("body") or ""),
            state=str(data.get("state") or "open").lower(),
            labels=labels,
            created_at=self._parse_dt(data.get("created_at")),
            updated_at=self._parse_dt(data.get("updated_at")),
            url=str(data.get("html_url") or data.get("url") or ""),
        )

    @staticmethod
    def _to_comment(data: dict, issue_id: str) -> Comment:
        return Comment(
            id=str(data.get("id") or ""),
            issue_id=str(issue_id),
            author=str(data.get("user", {}).get("login", "unknown")),
            body=str(data.get("body") or ""),
            created_at=GitHubPlatform._parse_dt(data.get("created_at")),
            url=str(data.get("html_url") or ""),
        )

    @staticmethod
    def _to_pr(data: dict, *, linked_issues: list[str] | None = None) -> PullRequest:
        return PullRequest(
            id=str(data.get("id") or data.get("number") or ""),
            number=int(data.get("number") or 0),
            title=str(data.get("title") or ""),
            state=str(data.get("state") or "open").lower(),
            head_branch=str(data.get("head", {}).get("ref", "")),
            base_branch=str(data.get("base", {}).get("ref", "main")),
            url=str(data.get("html_url") or ""),
            linked_issues=list(linked_issues or []),
        )
