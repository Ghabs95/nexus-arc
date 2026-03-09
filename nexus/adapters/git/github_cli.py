"""GitHub platform adapter using gh CLI."""

import json
import logging
import re
import subprocess
from datetime import UTC, datetime

from nexus.adapters.git.base import Comment, GitPlatform, Issue, PullRequest

logger = logging.getLogger(__name__)


class GitHubPlatform(GitPlatform):
    """GitHub platform adapter using gh CLI."""

    def __init__(self, repo: str, token: str | None = None):
        """
        Initialize GitHub adapter.

        Args:
            repo: Repository in format "owner/name"
            token: Optional GitHub token (uses gh CLI auth if not provided)
        """
        self.repo = repo
        self.token = token
        self._check_gh_cli()

    def _check_gh_cli(self) -> None:
        """Check if gh CLI is installed and authenticated."""
        try:
            subprocess.run(["gh", "--version"], capture_output=True, check=True, timeout=5)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError("gh CLI not found. Install from https://cli.github.com/")

    def _run_gh_command(self, args: list[str], timeout: int = 30) -> str:
        """Run gh CLI command and return stdout."""
        # gh api uses full URL paths; --repo is only for issue/pr subcommands
        cmd = ["gh"] + args if args and args[0] == "api" else ["gh"] + args + ["--repo", self.repo]

        env = None
        if self.token:
            import os

            env = os.environ.copy()
            env["GITHUB_TOKEN"] = self.token

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout, check=True, env=env
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError as e:
            stderr = (e.stderr or "").strip()
            logger.error(f"gh command failed: {stderr}")
            raise RuntimeError(f"GitHub CLI error: {stderr}")
        except subprocess.TimeoutExpired:
            logger.error(f"gh command timed out after {timeout}s")
            raise RuntimeError("GitHub CLI command timed out")

    async def list_open_issues(
        self,
        limit: int = 100,
        labels: list[str] | None = None,
    ) -> list[Issue]:
        """List open issues with optional label filtering."""
        args = [
            "issue",
            "list",
            "--state",
            "open",
            "--limit",
            str(limit),
            "--json",
            "number,title,body,state,labels,createdAt,updatedAt,url",
        ]
        if labels:
            args.extend(["--label", ",".join(labels)])

        try:
            output = self._run_gh_command(args)
            data = json.loads(output)
            return [self._parse_issue(item) for item in data]
        except RuntimeError:
            return []

    async def create_issue(self, title: str, body: str, labels: list[str] | None = None) -> Issue:
        """Create a new issue."""
        args = ["issue", "create", "--title", title, "--body", body]

        if labels:
            args.extend(["--label", ",".join(labels)])

        fields = "number,title,body,state,labels,createdAt,updatedAt,url"
        create_output = self._run_gh_command(args)
        issue_ref = str(create_output or "").strip().splitlines()[-1].strip()
        if not issue_ref:
            raise RuntimeError("Issue created but no issue reference returned by gh")

        issue_id = issue_ref.rstrip("/").split("/")[-1] if "github.com/" in issue_ref else issue_ref
        view_output = self._run_gh_command(["issue", "view", str(issue_id), "--json", fields])
        view_data = json.loads(view_output)
        return self._parse_issue(view_data)

    async def get_issue(self, issue_id: str) -> Issue | None:
        """Get issue by ID or number."""
        try:
            args = [
                "issue",
                "view",
                str(issue_id),
                "--json",
                "number,title,body,state,labels,createdAt,updatedAt,url",
            ]
            output = self._run_gh_command(args)
            data = json.loads(output)
            return self._parse_issue(data)
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
        """Update issue properties."""
        args = ["issue", "edit", str(issue_id)]

        if title:
            args.extend(["--title", title])
        if body:
            args.extend(["--body", body])
        if labels:
            args.extend(["--add-label", ",".join(labels)])

        self._run_gh_command(args)

        # Close/reopen if state changed
        if state == "closed":
            self._run_gh_command(["issue", "close", str(issue_id)])
        elif state == "open":
            self._run_gh_command(["issue", "reopen", str(issue_id)])

        # Fetch updated issue
        updated = await self.get_issue(issue_id)
        if not updated:
            raise RuntimeError(f"Failed to fetch updated issue {issue_id}")
        return updated

    async def add_comment(self, issue_id: str, body: str) -> Comment:
        """Add a comment to an issue."""
        args = ["issue", "comment", str(issue_id), "--body", body]
        self._run_gh_command(args)

        # Fetch the comment (last comment on the issue)
        comments = await self.get_comments(issue_id)
        if comments:
            return comments[-1]

        # Fallback: create a comment object
        return Comment(
            id="unknown",
            issue_id=str(issue_id),
            author="bot",
            body=body,
            created_at=datetime.now(UTC),
            url=f"https://github.com/{self.repo}/issues/{issue_id}",
        )

    async def get_comments(self, issue_id: str, since: datetime | None = None) -> list[Comment]:
        """Get comments for an issue."""
        args = [
            "api",
            f"/repos/{self.repo}/issues/{issue_id}/comments",
            "--jq",
            ".[]|{id,user:.user.login,body,created_at,html_url}",
        ]

        try:
            output = self._run_gh_command(args)
            if not output:
                return []

            comments = []
            for line in output.strip().split("\n"):
                if not line:
                    continue
                data = json.loads(line)
                created_at = datetime.fromisoformat(data["created_at"].replace("Z", "+00:00"))

                if since and created_at < since:
                    continue

                comment = Comment(
                    id=str(data["id"]),
                    issue_id=str(issue_id),
                    author=data["user"],
                    body=data["body"],
                    created_at=created_at,
                    url=data["html_url"],
                )
                comments.append(comment)

            return comments
        except RuntimeError:
            return []

    async def close_issue(self, issue_id: str, comment: str | None = None) -> None:
        """Close an issue."""
        if comment:
            await self.add_comment(issue_id, comment)

        self._run_gh_command(["issue", "close", str(issue_id)])
        logger.info(f"Closed issue #{issue_id}")

    async def search_linked_prs(self, issue_id: str) -> list[PullRequest]:
        """Find PRs linked to this issue."""
        issue_token = str(issue_id or "").strip()
        if not issue_token:
            return []

        try:
            issue_ref_pattern = re.compile(
                rf"(?:#{re.escape(issue_token)}\b|{re.escape(self.repo)}#{re.escape(issue_token)}\b|"
                rf"https?://github\.com/{re.escape(self.repo)}/issues/{re.escape(issue_token)}\b)",
                re.IGNORECASE,
            )

            # Prefer direct open-PR scanning over search to avoid index lag.
            open_list_output = self._run_gh_command(
                [
                    "pr",
                    "list",
                    "--state",
                    "open",
                    "--limit",
                    "100",
                    "--json",
                    "number,title,body,state,headRefName,baseRefName,url",
                ]
            )
            open_list_data = json.loads(open_list_output)

            linked_open_prs: list[PullRequest] = []
            for pr_data in open_list_data:
                title = str(pr_data.get("title") or "")
                body = str(pr_data.get("body") or "")
                if not issue_ref_pattern.search(f"{title}\n{body}"):
                    continue
                linked_open_prs.append(
                    PullRequest(
                        id=str(pr_data["number"]),
                        number=pr_data["number"],
                        title=title,
                        state=str(pr_data.get("state") or "").lower(),
                        head_branch=str(pr_data.get("headRefName") or ""),
                        base_branch=str(pr_data.get("baseRefName") or ""),
                        url=str(pr_data.get("url") or ""),
                        linked_issues=[issue_token],
                    )
                )
            if linked_open_prs:
                return linked_open_prs

            # Fallback to search query for edge cases where refs are absent in title/body.
            search_output = self._run_gh_command(
                [
                    "pr",
                    "list",
                    "--search",
                    f"#{issue_token}",
                    "--json",
                    "number,title,state,headRefName,baseRefName,url",
                ]
            )
            search_data = json.loads(search_output)
            prs: list[PullRequest] = []
            for pr_data in search_data:
                prs.append(
                    PullRequest(
                        id=str(pr_data["number"]),
                        number=pr_data["number"],
                        title=str(pr_data.get("title") or ""),
                        state=str(pr_data.get("state") or "").lower(),
                        head_branch=str(pr_data.get("headRefName") or ""),
                        base_branch=str(pr_data.get("baseRefName") or ""),
                        url=str(pr_data.get("url") or ""),
                        linked_issues=[issue_token],
                    )
                )
            return prs
        except RuntimeError:
            return []

    async def create_branch(self, branch_name: str, base_branch: str = "main") -> str:
        """Create a new branch. Returns branch URL."""
        logger.warning("Branch creation not implemented in GitHub adapter")
        return f"https://github.com/{self.repo}/tree/{branch_name}"

    async def merge_pull_request(
        self,
        pr_id: str,
        *,
        squash: bool = True,
        delete_branch: bool = True,
        auto: bool = True,
    ) -> str:
        """Merge a GitHub pull request via gh CLI."""
        args = ["pr", "merge", str(pr_id)]
        if squash:
            args.append("--squash")
        if delete_branch:
            args.append("--delete-branch")
        if auto:
            args.append("--auto")
        return self._run_gh_command(args, timeout=60)

    async def ensure_label(
        self,
        name: str,
        *,
        color: str,
        description: str = "",
    ) -> bool:
        """Ensure a GitHub label exists via gh CLI."""
        cmd = [
            "gh",
            "label",
            "create",
            str(name),
            "--color",
            str(color),
            "--description",
            str(description or ""),
            "--repo",
            self.repo,
        ]
        env = None
        if self.token:
            import os

            env = os.environ.copy()
            env["GITHUB_TOKEN"] = self.token
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
                env=env,
            )
            if result.returncode == 0:
                return True
            stderr = (result.stderr or "").strip()
            if "already exists" in stderr.lower():
                return True
            logger.warning("Failed to ensure GitHub label %s: %s", name, stderr or result.stdout)
            return False
        except subprocess.TimeoutExpired:
            logger.warning("Timed out ensuring GitHub label %s", name)
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
        """Create a PR from uncommitted changes in a local repository.

        Performs: detect changes → create branch → stage → commit → push → open PR.
        Automatically appends a GitHub closing keyword reference when the body
        does not already contain one:
        - same repo: ``Closes #<issue_number>``
        - cross repo: ``Closes <owner/repo>#<issue_number>``
        Returns None if no changes are detected.
        """
        import re as _re

        git_repo_probe = subprocess.run(
            ["git", "rev-parse", "--is-inside-work-tree"],
            cwd=repo_dir,
            text=True,
            capture_output=True,
            timeout=10,
        )
        if git_repo_probe.returncode != 0:
            logger.warning(f"Not a git repo: {repo_dir}")
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

        def _git(args: list, timeout: int = 30) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["git"] + args,
                cwd=repo_dir,
                text=True,
                capture_output=True,
                timeout=timeout,
            )

        # Detect changes
        diff = _git(["diff", "--stat", "HEAD"])
        staged = _git(["diff", "--cached", "--stat"])
        untracked = _git(["ls-files", "--others", "--exclude-standard"])
        has_changes = bool(
            (diff.stdout and diff.stdout.strip())
            or (staged.stdout and staged.stdout.strip())
            or (untracked.stdout and untracked.stdout.strip())
        )
        if not has_changes:
            logger.info(f"No uncommitted changes in {repo_dir} — skipping PR creation")
            return None

        # Resolve current branch and choose the head branch for this PR.
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
            # Create and switch only when we are on a long-lived branch.
            if not use_current_branch:
                result = _git(["checkout", "-b", branch_name])
                if result.returncode != 0:
                    logger.warning(f"Could not create branch {branch_name}: {result.stderr}")
                    return None
                switched_branch = True

            # Stage all changes
            _git(["add", "-A"])

            # Commit
            commit_msg = f"feat: resolve issue #{issue_number} (automated by Nexus)"
            _git(["commit", "-m", commit_msg])

            # Push
            push = _git(["push", "-u", "origin", branch_name], timeout=60)
            if push.returncode != 0:
                logger.warning(f"Could not push branch {branch_name}: {push.stderr}")
                if switched_branch and current_branch and current_branch != "HEAD":
                    _git(["checkout", current_branch])
                return None

            # Create PR via gh CLI
            pr_result = subprocess.run(
                [
                    "gh",
                    "pr",
                    "create",
                    "--repo",
                    self.repo,
                    "--base",
                    resolved_base,
                    "--head",
                    branch_name,
                    "--title",
                    title,
                    "--body",
                    body,
                ],
                cwd=repo_dir,
                text=True,
                capture_output=True,
                timeout=30,
            )
            if pr_result.returncode != 0:
                logger.warning(
                    f"Could not create PR for issue #{issue_number}: "
                    f"{pr_result.stderr or pr_result.stdout}"
                )
                if switched_branch and current_branch and current_branch != "HEAD":
                    _git(["checkout", current_branch])
                return None

            pr_link = pr_result.stdout.strip()
            logger.info(f"Created PR for issue #{issue_number}: {pr_link}")

            return PullRequest(
                id="0",
                number=0,
                title=title,
                state="open",
                head_branch=branch_name,
                base_branch=resolved_base,
                url=pr_link,
                linked_issues=[issue_number],
            )
        except Exception as exc:
            logger.warning(f"Error during PR creation for issue #{issue_number}: {exc}")
            return None
        finally:
            # Switch back only when we created a temporary issue branch.
            if switched_branch and current_branch and current_branch != "HEAD":
                _git(["checkout", current_branch])

    def get_workflow_type_from_issue(
        self,
        issue_number: int,
        label_prefix: str = "workflow:",
        default: str | None = None,
    ) -> str | None:
        """Extract the workflow type from an issue's labels.

        Looks for labels matching *label_prefix* (e.g. ``workflow:full``,
        ``workflow:shortened``, ``workflow:fast-track``) and returns the
        normalised workflow_type string via
        :py:meth:`~nexus.core.workflow.WorkflowDefinition.normalize_workflow_type`.

        The engine does **not** validate the extracted type against a
        hardcoded list — the workflow definition YAML is the source of
        truth for valid types.

        Args:
            issue_number: GitHub issue number.
            label_prefix: Prefix to match on labels (default ``"workflow:"``).
            default: Value returned when no matching label is found.

        Returns:
            Normalised workflow_type string, or *default*.
        """
        from nexus.core.workflow import WorkflowDefinition

        try:
            args = [
                "issue",
                "view",
                str(issue_number),
                "--json",
                "labels",
            ]
            output = self._run_gh_command(args, timeout=10)
            data = json.loads(output)
            labels = [l.get("name", "") for l in data.get("labels", [])]

            for label in labels:
                if label.startswith(label_prefix):
                    raw = label[len(label_prefix) :]
                    return WorkflowDefinition.normalize_workflow_type(raw, default=default)

            return default
        except Exception as e:
            logger.error(f"Failed to get workflow_type from issue #{issue_number}: {e}")
            return default

    def _parse_issue(self, data: dict) -> Issue:
        """Parse issue data from gh CLI JSON output."""
        labels = [label["name"] for label in data.get("labels", [])]

        return Issue(
            id=str(data["number"]),
            number=data["number"],
            title=data["title"],
            body=data.get("body", ""),
            state=data["state"].lower(),
            labels=labels,
            created_at=datetime.fromisoformat(data["createdAt"].replace("Z", "+00:00")),
            updated_at=datetime.fromisoformat(data["updatedAt"].replace("Z", "+00:00")),
            url=data["url"],
        )
