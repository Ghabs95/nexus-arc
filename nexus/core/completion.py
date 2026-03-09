"""Completion protocol for agent-to-framework communication.

Defines the contract between agents and the orchestration framework:
1. Schema for structured completion output (JSON)
2. Prompt instructions generator (tells agents what to produce)
3. Completion parser/validator (detects and reads agent output)
4. Comment builder (formats structured output for Git platform comments)
"""

import glob
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any

from nexus.core.prompt_budget import apply_prompt_budget

logger = logging.getLogger(__name__)

_COMPLETION_SUMMARY_MAX_CHARS = int(os.getenv("AI_COMPLETION_SUMMARY_MAX_CHARS", "900"))
_COMPLETION_FINDING_MAX_CHARS = int(os.getenv("AI_COMPLETION_FINDING_MAX_CHARS", "260"))
_COMPLETION_FINDINGS_MAX_ITEMS = int(os.getenv("AI_COMPLETION_FINDINGS_MAX_ITEMS", "8"))
_COMPLETION_VERDICT_MAX_CHARS = int(os.getenv("AI_COMPLETION_VERDICT_MAX_CHARS", "280"))
_COMPLETION_EFFORT_MAX_ITEMS = int(os.getenv("AI_COMPLETION_EFFORT_MAX_ITEMS", "12"))
_COMPLETION_EFFORT_VALUE_MAX_CHARS = int(os.getenv("AI_COMPLETION_EFFORT_VALUE_MAX_CHARS", "180"))
_COMPLETION_EXTRA_STRING_MAX_CHARS = int(os.getenv("AI_COMPLETION_EXTRA_STRING_MAX_CHARS", "1200"))
_CONTEXT_SUMMARY_MAX_CHARS = int(os.getenv("AI_CONTEXT_SUMMARY_MAX_CHARS", "1200"))

# Values that indicate "no next agent" / workflow is done
_TERMINAL_VALUES = frozenset(
    {
        "none",
        "n/a",
        "null",
        "no",
        "end",
        "done",
        "finish",
        "complete",
        "",
    }
)


def _budget_text_field(value: Any, *, max_chars: int, summary_cap: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    budget = apply_prompt_budget(
        text,
        max_chars=max_chars,
        summary_max_chars=min(summary_cap, max_chars),
    )
    return str(budget["text"]).strip()


def _budget_token_field(value: Any, *, max_chars: int) -> str:
    text = re.sub(r"\s+", " ", str(value or "").strip())
    if not text:
        return ""
    return text[:max_chars].strip()


def _normalize_findings(value: Any) -> list[str]:
    findings: list[str] = []
    if isinstance(value, list):
        source_items = value
    elif value is None:
        source_items = []
    else:
        source_items = [value]

    for item in source_items:
        finding = _budget_text_field(
            item,
            max_chars=_COMPLETION_FINDING_MAX_CHARS,
            summary_cap=min(_CONTEXT_SUMMARY_MAX_CHARS, 180),
        )
        if finding:
            findings.append(finding)

    if len(findings) > _COMPLETION_FINDINGS_MAX_ITEMS:
        omitted = len(findings) - _COMPLETION_FINDINGS_MAX_ITEMS
        findings = findings[:_COMPLETION_FINDINGS_MAX_ITEMS]
        findings.append(f"... {omitted} additional finding(s) omitted for budget.")
    return findings


def _normalize_step_id(value: Any) -> str:
    return _budget_token_field(value, max_chars=120)


def _normalize_step_num(value: Any) -> int:
    try:
        step_num = int(value)
    except (TypeError, ValueError):
        return 0
    return step_num if step_num > 0 else 0


def _normalize_effort_breakdown(value: Any) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    normalized: dict[str, str] = {}
    for idx, (task, effort) in enumerate(value.items()):
        if idx >= _COMPLETION_EFFORT_MAX_ITEMS:
            break
        key = _budget_text_field(
            _budget_token_field(task, max_chars=80),
            max_chars=80,
            summary_cap=80,
        )
        effort_text = _budget_text_field(
            effort,
            max_chars=_COMPLETION_EFFORT_VALUE_MAX_CHARS,
            summary_cap=min(_CONTEXT_SUMMARY_MAX_CHARS, 160),
        )
        if key and effort_text:
            normalized[key] = effort_text
    return normalized


def budget_completion_payload(data: dict[str, Any]) -> dict[str, Any]:
    """Normalize + budget completion payload fields for compact handoff storage."""
    payload = dict(data or {})
    payload["status"] = _budget_token_field(payload.get("status", "complete"), max_chars=32)
    payload["agent_type"] = _budget_token_field(payload.get("agent_type", "unknown"), max_chars=80)
    payload["step_id"] = _normalize_step_id(payload.get("step_id", ""))
    payload["step_num"] = _normalize_step_num(payload.get("step_num", 0))
    payload["summary"] = _budget_text_field(
        payload.get("summary", ""),
        max_chars=_COMPLETION_SUMMARY_MAX_CHARS,
        summary_cap=min(_CONTEXT_SUMMARY_MAX_CHARS, 700),
    )
    payload["key_findings"] = _normalize_findings(payload.get("key_findings", []))
    payload["next_agent"] = _budget_token_field(payload.get("next_agent", ""), max_chars=80)
    payload["verdict"] = _budget_text_field(
        payload.get("verdict", ""),
        max_chars=_COMPLETION_VERDICT_MAX_CHARS,
        summary_cap=min(_CONTEXT_SUMMARY_MAX_CHARS, 220),
    )
    payload["effort_breakdown"] = _normalize_effort_breakdown(payload.get("effort_breakdown", {}))

    for key in list(payload.keys()):
        if key in {
            "status",
            "agent_type",
            "step_id",
            "step_num",
            "summary",
            "key_findings",
            "next_agent",
            "verdict",
            "effort_breakdown",
        }:
            continue
        value = payload.get(key)
        if isinstance(value, str) and len(value) > _COMPLETION_EXTRA_STRING_MAX_CHARS:
            payload[key] = _budget_text_field(
                value,
                max_chars=_COMPLETION_EXTRA_STRING_MAX_CHARS,
                summary_cap=min(_CONTEXT_SUMMARY_MAX_CHARS, 700),
            )

    payload["status"] = payload["status"] or "complete"
    payload["agent_type"] = payload["agent_type"] or "unknown"
    return payload


@dataclass
class CompletionSummary:
    """Parsed agent completion output.

    Attributes:
        status: Agent outcome, typically "complete" or "error".
        agent_type: The agent_type that produced this summary.
        step_id: Canonical workflow step identifier for this completion.
        step_num: Workflow step number for this completion.
        summary: One-line human-readable summary.
        key_findings: List of notable findings / outputs.
        next_agent: The agent_type that should run next, or "" if workflow is done.
        verdict: Optional verdict / recommendation.
        effort_breakdown: Optional mapping of task → effort description.
        raw: The full raw dict for any extra fields.
    """

    status: str = "complete"
    agent_type: str = "unknown"
    step_id: str = ""
    step_num: int = 0
    summary: str = ""
    key_findings: list[str] = field(default_factory=list)
    next_agent: str = ""
    verdict: str = ""
    effort_breakdown: dict[str, str] = field(default_factory=dict)
    alignment_score: float | None = None
    alignment_summary: str = ""
    alignment_artifacts: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def is_workflow_done(self) -> bool:
        """True when this completion signals no further agent should run."""
        return self.next_agent.strip().lower() in _TERMINAL_VALUES

    @staticmethod
    def from_dict(data: dict[str, Any]) -> "CompletionSummary":
        """Build a CompletionSummary from a raw JSON dict."""
        payload = budget_completion_payload(data)
        alignment_score = data.get("alignment_score")
        try:
            if alignment_score is not None and alignment_score != "":
                alignment_score = float(alignment_score)
            else:
                alignment_score = None
        except (TypeError, ValueError):
            alignment_score = None
        return CompletionSummary(
            status=str(payload.get("status", "complete")),
            agent_type=str(payload.get("agent_type", "unknown")),
            step_id=str(payload.get("step_id", "")),
            step_num=_normalize_step_num(payload.get("step_num", 0)),
            summary=str(payload.get("summary", "")),
            key_findings=_normalize_findings(payload.get("key_findings", [])),
            next_agent=str(payload.get("next_agent", "")),
            verdict=str(payload.get("verdict", "")),
            effort_breakdown=_normalize_effort_breakdown(payload.get("effort_breakdown", {})),
            alignment_score=alignment_score,
            alignment_summary=data.get("alignment_summary", ""),
            alignment_artifacts=list(data.get("alignment_artifacts", []) or []),
            raw=payload,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize back to a plain dict."""
        d: dict[str, Any] = budget_completion_payload(
            {
                "status": self.status,
                "agent_type": self.agent_type,
                "step_id": self.step_id,
                "step_num": self.step_num,
                "summary": self.summary,
                "key_findings": self.key_findings,
                "next_agent": self.next_agent,
                "verdict": self.verdict,
                "effort_breakdown": self.effort_breakdown,
            }
        )
        if not d.get("verdict"):
            d.pop("verdict", None)
        if not d.get("effort_breakdown"):
            d.pop("effort_breakdown", None)
        if self.effort_breakdown:
            d["effort_breakdown"] = self.effort_breakdown
        if self.alignment_score is not None:
            d["alignment_score"] = self.alignment_score
        if self.alignment_summary:
            d["alignment_summary"] = self.alignment_summary
        if self.alignment_artifacts:
            d["alignment_artifacts"] = list(self.alignment_artifacts)
        return d


# ---------------------------------------------------------------------------
# Comment builder — structured output → Git platform comment
# ---------------------------------------------------------------------------


def build_completion_comment(completion: CompletionSummary) -> str:
    """Build a Git-platform comment body from an agent completion summary.

    Returns a Markdown string suitable for ``GitPlatform.add_comment()``.
    """
    sections: list[str] = []
    sections.append("### ✅ Agent Completed")
    if completion.step_id:
        sections.append(f"**Step ID:** `{completion.step_id}`")
    if completion.step_num > 0:
        sections.append(f"**Step Num:** {completion.step_num}")

    summary_text = _budget_text_field(
        completion.summary,
        max_chars=_COMPLETION_SUMMARY_MAX_CHARS,
        summary_cap=min(_CONTEXT_SUMMARY_MAX_CHARS, 700),
    )
    if summary_text:
        sections.append(f"**Summary:** {summary_text}")

    if completion.status and completion.status != "complete":
        sections.append(f"**Status:** {completion.status}")

    findings = _normalize_findings(completion.key_findings)
    if findings:
        items = "\n".join(f"- {f}" for f in findings)
        sections.append(f"**Key Findings:**\n{items}")

    effort_breakdown = _normalize_effort_breakdown(completion.effort_breakdown)
    if effort_breakdown:
        items = "\n".join(f"- {t}: {e}" for t, e in effort_breakdown.items())
        sections.append(f"**Effort Breakdown:**\n{items}")

    verdict = _budget_text_field(
        completion.verdict,
        max_chars=_COMPLETION_VERDICT_MAX_CHARS,
        summary_cap=min(_CONTEXT_SUMMARY_MAX_CHARS, 220),
    )
    if verdict:
        sections.append(f"**Verdict:** {verdict}")

    if completion.next_agent and not completion.is_workflow_done:
        display_name = completion.next_agent.title()
        sections.append(f"**Next:** Ready for `@{display_name}`")

    sections.append("_Automated comment from Nexus._")
    return "\n\n".join(sections)


# ---------------------------------------------------------------------------
# Prompt instructions generator
# ---------------------------------------------------------------------------


def generate_completion_instructions(
    issue_number: str,
    agent_type: str,
    step_id: str,
    step_num: int,
    workflow_steps_text: str = "",
    nexus_dir: str = ".nexus",
    project_name: str = "",
    completion_backend: str = "filesystem",
    webhook_url: str = "",
) -> str:
    """Generate prompt instructions that tell an agent how to produce output.

    This is the *writer* side of the completion protocol.  It tells the agent
    exactly what deliverables to produce so the framework can detect and parse
    them.

    Args:
        issue_number: Issue number being worked on.
        agent_type: The agent_type running this step.
        workflow_steps_text: Pre-formatted workflow steps text for context.
        nexus_dir: Name of the .nexus directory (default ".nexus").
        project_name: Project subdirectory name (e.g. "nexus"). Completions are
            written to ``tasks/<project_name>/completions/``.
        completion_backend: Storage backend for completions — ``"filesystem"``
            or ``"postgres"``.  When ``"postgres"``, the agent POSTs to the
            webhook endpoint instead of writing a local JSON file.
        webhook_url: Base URL of the webhook server (e.g.
            ``http://localhost:8081``).  Required when *completion_backend*
            is ``"postgres"``.

    Returns:
        Prompt text to append to the agent's instructions.
    """
    resolved_project_name = str(project_name or "").strip() or "nexus"
    endpoint_base = str(webhook_url or "").strip().rstrip("/") or "<WEBHOOK_BASE_URL>"

    # --- Build Deliverable 2 based on backend ---
    if completion_backend == "postgres":
        deliverable_2 = (
            f"## Deliverable 2: POST completion summary to the API\n\n"
            f"POST your structured results to the completion endpoint. "
            f"Use this command template:\n\n"
            f"```bash\n"
            f"curl -s -X POST {endpoint_base}/api/v1/completion \\\n"
            f'  -H "Content-Type: application/json" \\\n'
            f"  -d '{{\n"
            f'    "issue_number": "{issue_number}",\n'
            f'    "agent_type": "{agent_type}",\n'
            f'    "step_id": "{step_id}",\n'
            f'    "step_num": {step_num},\n'
            f'    "status": "complete",\n'
            f'    "summary": "<one-line summary of what you did>",\n'
            f'    "key_findings": ["<finding 1>", "<finding 2>"],\n'
            f'    "next_agent": "<agent_type from workflow steps — NOT the step id or display name>"\n'
            f"  }}'\n"
            f"```\n\n"
            f"Replace the `<placeholder>` values with real data from your analysis.\n"
            f"**Do NOT write any local JSON files.**"
        )
    else:
        completions_script = (
            f"```bash\n"
            f"WORKSPACE_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)\n"
            f"COMPLETIONS_DIR=$(find \"$WORKSPACE_ROOT\" -maxdepth 4 -path '*/{nexus_dir}/tasks/{resolved_project_name}/completions' -type d 2>/dev/null | head -1)\n"
            f'if [ -z "$COMPLETIONS_DIR" ]; then COMPLETIONS_DIR="$WORKSPACE_ROOT/{nexus_dir}/tasks/{resolved_project_name}/completions"; mkdir -p "$COMPLETIONS_DIR"; fi\n'
        )
        deliverable_2 = (
            f"## Deliverable 2: Write completion summary JSON\n\n"
            f"Write a JSON file with your structured results. Use this command template:\n\n"
            f"**IMPORTANT:** The `{nexus_dir}/` directory lives at the **workspace root** "
            f"(the top-level directory you were launched in). "
            f"Do NOT create a new `{nexus_dir}/` folder inside sub-repos or subdirectories.\n\n"
            + completions_script
            + f'python3 -c \'import json,os; p=os.path.join(os.environ["COMPLETIONS_DIR"], "completion_summary_{issue_number}.json"); d={{"status":"complete","agent_type":"{agent_type}","step_id":"{step_id}","step_num":{step_num},"summary":"<one-line summary of what you did>","key_findings":["<finding 1>","<finding 2>"],"next_agent":"<agent_type from workflow steps — NOT the step id or display name>"}}; open(p, "w", encoding="utf-8").write(json.dumps(d, indent=2))\'\n'
            f"```\n\n"
            f"Replace the `<placeholder>` values with real data from your analysis."
        )

    static_intro = (
        "**WHEN YOU FINISH — TWO MANDATORY DELIVERABLES:**\n\n"
        "Follow the workflow mapping above and produce both deliverables exactly once.\n"
        "Do not invoke other agents. Keep your completion concise and concrete.\n\n"
        "Use the runtime `step_id` and `step_num` values exactly as provided.\n\n"
    )
    runtime_context = (
        f"Runtime context:\n"
        f"- issue_number: {issue_number}\n"
        f"- agent_type: {agent_type}\n"
        f"- step_id: {step_id}\n"
        f"- step_num: {step_num}\n"
        f"- completion_backend: {completion_backend}\n\n"
    )

    output = (
        static_intro
        + f"{workflow_steps_text}\n\n"
        + runtime_context
        + f"## Deliverable 1: Post a structured issue comment\n\n"
        f"Use your project's Git platform tooling (CLI/API/integration) to post a "
        f"**structured** comment on issue #{issue_number}.\n"
        f"The comment MUST follow this format (adapt sections to your work):\n\n"
        f"```\n"
        f"## 🔍 <Step Name> Complete — <agent_type>\n\n"
        f"**Severity:** <Critical|High|Medium|Low>\n"
        f"**Target Sub-Repo:** `<repo-name>`\n"
        f"**Workflow:** <workflow type>\n\n"
        f"**Step ID:** `{step_id}`\n"
        f"**Step Num:** {step_num}\n\n"
        f"### Findings\n\n"
        f"<Describe what you analyzed/discovered. Use bullet points for key findings:>\n"
        f"- Finding 1\n"
        f"- Finding 2\n"
        f"- Finding 3\n\n"
        f"### SOP Checklist\n\n"
        f"Build the checklist from the workflow steps above (use those exact step labels and agent_type tokens).\n"
        f"Mark your completed step as `[x]` and leave future steps as `[ ]`.\n"
        f"Do NOT invent step names, use legacy agent names, or skip required steps.\n\n"
        f"Ready for **@<Display Name>**  *(omit this line when next_agent is terminal/`none`)*\n"
        f"```\n\n"
        f"**IMPORTANT:** The comment must contain real findings from YOUR analysis, "
        f"not placeholder text.\n"
        f"Adapt the template to your role ({agent_type}). Include concrete details.\n"
        f"Do not change `Step ID` or `Step Num`; they must match runtime context exactly.\n"
        f"The header must end with your current runtime agent_type token: `{agent_type}`.\n"
        f"For the 'Ready for @...' line, use the **Display Names** mapping from "
        f"the workflow steps above (e.g., `Ready for **@Developer**`). "
        f"Do NOT use the raw agent_type.\n"
        f"If your `next_agent` is terminal (`none`, `done`, `complete`), do NOT include any "
        f"'Ready for @...' line. Never write `@none` in comments.\n"
        f"Before posting, quickly check recent comments: if a completion comment from your same "
        f"agent already exists for this run, do not post a duplicate completion comment.\n\n"
        f"{deliverable_2}\n\n"
        f"After posting the comment and "
        f"{'POSTing the completion' if completion_backend == 'postgres' else 'writing the JSON'}"
        f", **EXIT immediately**."
    )
    logger.debug(
        "Completion instructions generated: chars=%s issue=%s agent=%s",
        len(output),
        issue_number,
        agent_type,
    )
    return output


# ---------------------------------------------------------------------------
# Completion detector — file-based scanning
# ---------------------------------------------------------------------------


@dataclass
class DetectedCompletion:
    """A completion summary detected on disk."""

    file_path: str
    issue_number: str
    summary: CompletionSummary

    @property
    def dedup_key(self) -> str:
        """Deduplication key: ``{issue}:{agent_type}:{filename}``."""
        basename = os.path.basename(self.file_path)
        return f"{self.issue_number}:{self.summary.agent_type}:{basename}"


def scan_for_completions(
    base_dir: str,
    nexus_dir: str = ".nexus",
) -> list[DetectedCompletion]:
    """Scan directories under *base_dir* for ``completion_summary_*.json`` files.

    Args:
        base_dir: Root directory to search recursively.
        nexus_dir: Name of the nexus state directory (default ".nexus").

    Returns:
        List of detected completion summaries, each parsed and validated.
    """
    results: list[DetectedCompletion] = []
    candidates_by_issue: dict[str, list[str]] = {}
    seen_paths: set[str] = set()
    patterns = [
        os.path.join(
            base_dir,
            "**",
            nexus_dir,
            "tasks",
            "*",
            "completions",
            "completion_summary_*.json",
        ),
    ]

    for pattern in patterns:
        for path in glob.glob(pattern, recursive=True):
            normalized = os.path.abspath(path)
            if normalized in seen_paths:
                continue
            seen_paths.add(normalized)

            match = re.search(r"completion_summary_(\d+)\.json$", path)
            if not match:
                continue
            issue_number = match.group(1)
            candidates_by_issue.setdefault(issue_number, []).append(path)

    for issue_number, candidate_paths in sorted(
        candidates_by_issue.items(), key=lambda item: int(item[0])
    ):
        sorted_paths = sorted(candidate_paths, key=os.path.getmtime, reverse=True)
        parsed = False
        for path in sorted_paths:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                summary = CompletionSummary.from_dict(data)
                results.append(
                    DetectedCompletion(
                        file_path=path,
                        issue_number=issue_number,
                        summary=summary,
                    )
                )
                parsed = True
                break
            except json.JSONDecodeError as exc:
                logger.warning(f"Invalid JSON in {path}: {exc}")
            except Exception as exc:
                logger.warning(f"Error reading completion file {path}: {exc}")

        if not parsed:
            logger.warning(
                "No valid completion summary could be parsed for issue %s from %d file(s)",
                issue_number,
                len(sorted_paths),
            )

    return results
