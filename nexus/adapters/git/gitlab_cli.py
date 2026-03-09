"""GitLab platform adapter using glab CLI."""

import json
import logging
import os
import re
import subprocess
import urllib.parse
from datetime import UTC, datetime

from nexus.adapters.git.base import Comment, GitPlatform, Issue, PullRequest

logger = logging.getLogger(__name__)


class GitLabCLIPlatform(GitPlatform):
    """GitLab platform adapter backed by glab CLI."""

    def __init__(self, token: str, repo: str, base_url: str = "https://gitlab.com"):
        self._token = str(token or "").strip() or None
        self._repo = repo
        self._base_url = str(base_url or "https://gitlab.com").strip().rstrip("/")
        self._encoded_repo = urllib.parse.quote(repo, safe="")
        self._check_glab_cli()

    def _check_glab_cli(self) -> None:
        try:
            subprocess.run(["glab", "--version"], capture_output=True, check=True, timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("glab CLI not found. Install from https://gitlab.com/gitlab-org/cli")

    def _run_glab_command(self, args: list[str], timeout: int = 30) -> str:
        env = os.environ.copy()
        if self._token:
            env["GITLAB_TOKEN"] = self._token
            env.setdefault("GITHUB_TOKEN", self._token)
        if self._base_url:
            env["GITLAB_HOST"] = self._base_url.replace("https://", "").replace("http://", "")
        try:
            result = subprocess.run(
                ["glab"] + args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=True,
                env=env,
            )
            return (result.stdout or "").strip()
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or exc.stdout or "").strip()
            logger.error("glab command failed: %s", stderr)
            raise RuntimeError(f"GitLab CLI error: {stderr}") from exc
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError("GitLab CLI command timed out") from exc

    def _api(self, method: str, path: str, *, payload: dict | None = None, timeout: int = 30):
        args = ["api", path, "-X", method]
        if payload:
            args.extend(["--input", "-"])
            raw = json.dumps(payload)
            env = os.environ.copy()
            if self._token:
                env["GITLAB_TOKEN"] = self._token
                env.setdefault("GITHUB_TOKEN", self._token)
            if self._base_url:
                env["GITLAB_HOST"] = self._base_url.replace("https://", "").replace("http://", "")
            try:
                result = subprocess.run(
                    ["glab"] + args,
                    input=raw,
                    capture_output=True,
                    text=True,
                    timeout=timeout,
                    check=True,
                    env=env,
                )
                output = (result.stdout or "").strip()
                return json.loads(output) if output else {}
            except subprocess.CalledProcessError as exc:
                stderr = (exc.stderr or exc.stdout or "").strip()
                logger.error("glab api failed: %s", stderr)
                raise RuntimeError(f"GitLab CLI error: {stderr}") from exc
        output = self._run_glab_command(args, timeout=timeout)
        return json.loads(output) if output else {}

    async def list_open_issues(self, limit: int = 100, labels: list[str] | None = None) -> list[Issue]:
        query = f"projects/{self._encoded_repo}/issues?state=opened&per_page={max(1, min(limit, 100))}"
        if labels:
            query += "&labels=" + urllib.parse.quote(",".join(labels), safe="")
        try:
            items = self._api("GET", query)
            return [self._to_issue(item) for item in items][:limit]
        except RuntimeError:
            return []

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Issue:
        payload = {"title": title, "description": body}
        if labels:
            payload["labels"] = ",".join(labels)
        data = self._api("POST", f"projects/{self._encoded_repo}/issues", payload=payload)
        return self._to_issue(data)

    async def get_issue(self, issue_id: str) -> Issue | None:
        try:
            data = self._api("GET", f"projects/{self._encoded_repo}/issues/{issue_id}")
            return self._to_issue(data)
        except RuntimeError as exc:
            if "404" in str(exc):
                return None
            raise

    async def update_issue(self, issue_id: str, title: str | None = None, body: str | None = None, state: str | None = None, labels: list[str] | None = None) -> Issue:
        payload: dict[str, str] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["description"] = body
        if state is not None:
            payload["state_event"] = "close" if state == "closed" else "reopen"
        if labels is not None:
            payload["labels"] = ",".join(labels)
        data = self._api("PUT", f"projects/{self._encoded_repo}/issues/{issue_id}", payload=payload)
        return self._to_issue(data)

    async def add_comment(self, issue_id: str, body: str) -> Comment:
        data = self._api("POST", f"projects/{self._encoded_repo}/issues/{issue_id}/notes", payload={"body": body})
        return self._to_comment(data, issue_id)

    async def get_comments(self, issue_id: str, since: datetime | None = None) -> list[Comment]:
        try:
            items = self._api("GET", f"projects/{self._encoded_repo}/issues/{issue_id}/notes?per_page=100&sort=asc")
            comments = [self._to_comment(item, issue_id) for item in items]
            if since:
                comments = [c for c in comments if c.created_at >= since]
            return comments
        except RuntimeError:
            return []

    async def close_issue(self, issue_id: str, comment: str | None = None) -> None:
        if comment:
            await self.add_comment(issue_id, comment)
        await self.update_issue(issue_id, state="closed")

    async def search_linked_prs(self, issue_id: str) -> list[PullRequest]:
        issue_token = str(issue_id or "").strip()
        if not issue_token:
            return []

        try:
            issue_ref_pattern = re.compile(
                rf"(?:#{re.escape(issue_token)}\b|{re.escape(self._repo)}#{re.escape(issue_token)}\b|"
                rf"{re.escape(self._repo)}/-/issues/{re.escape(issue_token)}\b|/-/issues/{re.escape(issue_token)}\b)",
                re.IGNORECASE,
            )

            open_items = self._api(
                "GET",
                f"projects/{self._encoded_repo}/merge_requests?state=open&per_page=100",
            )
            linked_open_mrs: list[PullRequest] = []
            for item in open_items if isinstance(open_items, list) else []:
                title = str(item.get("title") or "")
                body = str(item.get("description") or "")
                if issue_ref_pattern.search(f"{title}\n{body}"):
                    linked_open_mrs.append(self._to_pr(item))
            if linked_open_mrs:
                return linked_open_mrs

            items = self._api(
                "GET",
                f"projects/{self._encoded_repo}/merge_requests?state=all&search=%23{issue_token}&per_page=20",
            )
            return [self._to_pr(item) for item in items]
        except RuntimeError:
            return []

    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        data = self._api("POST", f"projects/{self._encoded_repo}/repository/branches", payload={"branch": branch_name, "ref": base_branch})
        return str(data.get("web_url") or branch_name)

    async def merge_pull_request(self, pr_id: str, *, squash: bool = True, delete_branch: bool = True, auto: bool = True) -> str:
        payload = {
            "should_remove_source_branch": bool(delete_branch),
            "squash": bool(squash),
        }
        if auto:
            payload["merge_when_pipeline_succeeds"] = True
        data = self._api("PUT", f"projects/{self._encoded_repo}/merge_requests/{pr_id}/merge", payload=payload, timeout=60)
        state = str(data.get("state") or "unknown")
        web_url = str(data.get("web_url") or "")
        return f"state={state} {web_url}".strip()

    async def ensure_label(self, name: str, *, color: str, description: str = "") -> bool:
        try:
            self._api("POST", f"projects/{self._encoded_repo}/labels", payload={"name": str(name), "color": str(color), "description": str(description or "")})
            return True
        except RuntimeError as exc:
            if "already exists" in str(exc).lower() or "has already been taken" in str(exc).lower() or "409" in str(exc):
                return True
            logger.warning("Failed to ensure GitLab label %s: %s", name, exc)
            return False

    async def create_pr_from_changes(self, repo_dir: str, issue_number: str, title: str, body: str, issue_repo: str | None = None, base_branch: str = "main", branch_prefix: str = "nexus") -> PullRequest | None:
        import re as _re

        issue_repo_ref = (issue_repo or self._repo or "").strip()
        same_repo_issue = not issue_repo_ref or issue_repo_ref == self._repo
        issue_ref = f"#{issue_number}" if same_repo_issue else f"{issue_repo_ref}#{issue_number}"
        issue_url = rf"https?://[^\s]+/{_re.escape(issue_repo_ref)}/-/issues/{_re.escape(issue_number)}"
        closing_pattern = _re.compile(
            rf"(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+(?:{_re.escape(issue_ref)}|{issue_url})\b",
            _re.IGNORECASE,
        )
        if issue_number and not closing_pattern.search(body):
            body = f"{body}\n\nCloses {issue_ref}"

        current_branch = base_branch
        result = subprocess.run(["git", "rev-parse", "--abbrev-ref", "HEAD"], capture_output=True, text=True, cwd=repo_dir)
        if result.returncode == 0:
            detected = (result.stdout or "").strip()
            if detected:
                current_branch = detected
        long_lived = {"main", "master", "develop", "development", "dev", "HEAD"}
        use_current_branch = bool(current_branch and current_branch not in long_lived)
        branch_name = current_branch if use_current_branch else f"{branch_prefix}/issue-{issue_number}"
        try:
            staged = subprocess.run(["git", "diff", "--cached", "--name-only"], capture_output=True, text=True, cwd=repo_dir, check=True)
            if not (staged.stdout or "").strip():
                subprocess.run(["git", "add", "-A"], cwd=repo_dir, check=True)
            status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=repo_dir, check=True)
            if not (status.stdout or "").strip():
                logger.info("No local changes detected — skipping MR creation")
                return None
            if not use_current_branch:
                subprocess.run(["git", "checkout", "-B", branch_name], cwd=repo_dir, check=True)
            subprocess.run(["git", "commit", "-m", f"nexus: {title} (closes #{issue_number})"], cwd=repo_dir, check=True)
            subprocess.run(["git", "push", "--set-upstream", "origin", branch_name, "--force"], cwd=repo_dir, check=True)
            data = self._api("POST", f"projects/{self._encoded_repo}/merge_requests", payload={"source_branch": branch_name, "target_branch": base_branch, "title": title, "description": body, "remove_source_branch": True})
            return self._to_pr(data)
        finally:
            subprocess.run(["git", "checkout", current_branch], cwd=repo_dir, check=False, capture_output=True)

    @staticmethod
    def _parse_dt(val: str | None) -> datetime:
        if not val:
            return datetime.now(tz=UTC)
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
            created_at=GitLabCLIPlatform._parse_dt(data.get("created_at")),
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
