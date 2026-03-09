import re
from types import SimpleNamespace

from nexus.core.inbox.inbox_signal_probe_service import read_latest_structured_comment


def test_read_latest_structured_comment_parses_nexus_automated_comment():
    class _Platform:
        async def get_comments(self, _issue_num):
            return [
                SimpleNamespace(
                    id=4019296323,
                    created_at="2026-03-09T00:12:00Z",
                    body=(
                        "## Verify Change Complete — reviewer\n\n"
                        "**Step ID:** `verify_change`\n"
                        "**Step Num:** 2\n\n"
                        "Ready for **@Deployer**\n\n"
                        "_Automated comment from Nexus._"
                    ),
                )
            ]

    signal = read_latest_structured_comment(
        issue_num="113",
        repo="Ghabs95/nexus-arc",
        project_name="nexus",
        get_git_platform=lambda *_a, **_k: _Platform(),
        resolve_issue_token=None,
        require_issue_requester_token=False,
        normalize_agent_reference=lambda s: str(s or "").strip().strip("`").lstrip("@"),
        step_complete_comment_re=re.compile(
            r"^\s*##\s+.+?\bcomplete\b\s+—\s+([0-9a-z_-]+)\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        ready_for_comment_re=re.compile(
            r"\bready\s+for\s+(?:\*\*)?`?@?([0-9a-z_-]+)",
            re.IGNORECASE,
        ),
        logger=SimpleNamespace(debug=lambda *_a, **_k: None),
    )

    assert signal is not None
    assert signal["completed_agent"] == "reviewer"
    assert signal["next_agent"] == "deployer"
    assert signal["step_id"] == "verify_change"
    assert signal["step_num"] == 2


def test_read_latest_structured_comment_accepts_hyphen_and_backticks():
    class _Platform:
        async def get_comments(self, _issue_num):
            return [
                SimpleNamespace(
                    id=4019296324,
                    created_at="2026-03-09T00:20:00Z",
                    body=(
                        "## Verify Change Complete - `reviewer`\n\n"
                        "**Step ID:** `verify_change`\n"
                        "**Step Num:** 2\n\n"
                        "Ready for **@Deployer**"
                    ),
                )
            ]

    signal = read_latest_structured_comment(
        issue_num="113",
        repo="Ghabs95/nexus-arc",
        project_name="nexus",
        get_git_platform=lambda *_a, **_k: _Platform(),
        resolve_issue_token=None,
        require_issue_requester_token=False,
        normalize_agent_reference=lambda s: str(s or "").strip().strip("`").lstrip("@"),
        step_complete_comment_re=re.compile(
            r"^\s*##\s+.+?\bcomplete\b\s*[-–—:]\s*`?@?([0-9a-z_-]+)`?\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        ready_for_comment_re=re.compile(
            r"\bready\s+for\s+(?:\*\*)?`?@?([0-9a-z_-]+)",
            re.IGNORECASE,
        ),
        logger=SimpleNamespace(debug=lambda *_a, **_k: None),
    )

    assert signal is not None
    assert signal["completed_agent"] == "reviewer"
    assert signal["next_agent"] == "deployer"
    assert signal["step_id"] == "verify_change"
    assert signal["step_num"] == 2


def test_read_latest_structured_comment_accepts_terminal_without_ready():
    class _Platform:
        async def get_comments(self, _issue_num):
            return [
                SimpleNamespace(
                    id=4019296325,
                    created_at="2026-03-09T00:21:00Z",
                    body=(
                        "## Emergency Deploy Completed — writer\n\n"
                        "### SOP Checklist\n\n"
                        "- [x] 1. **Intake & Triage** — `triage`\n"
                        "- [x] 2. **Root Cause Analysis** — `debug`\n"
                        "- [x] 3. **Document & Close** — `writer`\n"
                    ),
                )
            ]

    signal = read_latest_structured_comment(
        issue_num="113",
        repo="Ghabs95/nexus-arc",
        project_name="nexus",
        get_git_platform=lambda *_a, **_k: _Platform(),
        resolve_issue_token=None,
        require_issue_requester_token=False,
        normalize_agent_reference=lambda s: str(s or "").strip().strip("`").lstrip("@"),
        step_complete_comment_re=re.compile(
            r"^\s*##\s+.+?\bcomplet(?:e|ed)\b\s*[-–—:]\s*`?@?([0-9a-z_-]+)`?\s*$",
            re.IGNORECASE | re.MULTILINE,
        ),
        ready_for_comment_re=re.compile(
            r"\bready\s+for\s+(?:\*\*)?`?@?([0-9a-z_-]+)",
            re.IGNORECASE,
        ),
        logger=SimpleNamespace(debug=lambda *_a, **_k: None),
    )

    assert signal is not None
    assert signal["completed_agent"] == "writer"
    assert signal["next_agent"] == "none"
    assert signal["step_id"] == "document_close"
    assert signal["step_num"] == 3
