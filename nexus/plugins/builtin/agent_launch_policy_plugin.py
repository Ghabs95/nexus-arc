"""Built-in plugin: Agent launch prompt composition policy."""

import logging
import os
import re

from nexus.core.completion import generate_completion_instructions
from nexus.core.knowledge_alignment import KnowledgeAlignmentService
from nexus.core.prompt_budget import apply_prompt_budget, prompt_prefix_fingerprint
from nexus.core.workflow import WorkflowDefinition

logger = logging.getLogger(__name__)


class AgentLaunchPolicyPlugin:
    """Compose workflow/agent prompts for launch and continuation flows."""

    def __init__(self, config: dict | None = None):
        self.config = config or {}
        self._alignment_service = KnowledgeAlignmentService()
        self.prompt_max_chars = int(
            self.config.get("ai_prompt_max_chars")
            or os.getenv("AI_PROMPT_MAX_CHARS", "16000")
            or 16000
        )
        self.summary_max_chars = int(
            self.config.get("ai_context_summary_max_chars")
            or os.getenv("AI_CONTEXT_SUMMARY_MAX_CHARS", "1200")
            or 1200
        )

    def build_agent_prompt(
        self,
        *,
        issue_url: str,
        tier_name: str,
        task_content: str,
        agent_type: str,
        continuation: bool = False,
        continuation_prompt: str | None = None,
        workflow_path: str = "",
        nexus_dir: str = ".nexus",
        project_name: str = "",
        repo_path: str = "",
        step_id: str = "",
        step_num: int = 0,
    ) -> str:
        """Build launch prompt used by orchestrator agent invocation."""
        workflow_type = WorkflowDefinition.normalize_workflow_type(tier_name)
        alignment_context = self._build_alignment_context(
            task_content=task_content,
            workflow_type=workflow_type,
            repo_path=repo_path,
        )
        alignment_output_requirements = self._get_alignment_output_requirements(agent_type)
        task_budget = apply_prompt_budget(
            task_content or "",
            max_chars=min(self.prompt_max_chars, 7000),
            summary_max_chars=self.summary_max_chars,
        )
        task_payload = str(task_budget["text"])
        continuation_payload = continuation_prompt
        if continuation_prompt:
            continuation_budget = apply_prompt_budget(
                continuation_prompt,
                max_chars=min(self.prompt_max_chars, 3000),
                summary_max_chars=min(self.summary_max_chars, 800),
            )
            continuation_payload = str(continuation_budget["text"])

        instructions = self._get_comment_and_summary_instructions(
            issue_url=issue_url,
            agent_type=agent_type,
            workflow_path=workflow_path,
            workflow_type=workflow_type,
            nexus_dir=nexus_dir,
            project_name=project_name,
            step_id=step_id,
            step_num=step_num,
        )
        agent_identity = self._resolve_launch_agent_identity(project_name=project_name)
        if alignment_output_requirements:
            instructions = f"{instructions}\n\n{alignment_output_requirements}"

        if continuation:
            if continuation_payload and continuation_payload.startswith("You are @"):
                prompt = (
                    f"{continuation_payload}\n\n"
                    f"Review the previous work in issue comments and task file, then complete your step.\n\n"
                    f"**GIT WORKFLOW (CRITICAL):**\n"
                    f"1. Check the issue body for **Target Branch** and the intended starting branch.\n"
                    f"2. Identify the correct sub-repo within the provided workspace "
                    f"(e.g., backend-service, web-app, worker-service)\n"
                    f"3. Treat the current workspace/branch as pre-provisioned by Nexus. "
                    f"Do not run `git checkout <target-branch>`, `git checkout -b ...`, or "
                    f"`git switch` to another feature branch.\n"
                    f"4. If you need to sync first, only update from the specified starting branch "
                    f"(for example `develop` or `main`) without switching to any other branch.\n"
                    f"5. Make your changes and commit with descriptive messages\n"
                    f"6. Push only the branch already prepared in this workspace.\n"
                    f"7. Include branch name in your issue comment "
                    f"(e.g., 'Pushed to feat/new-feature in backend-service')\n\n"
                    f"⛔ **GIT SAFETY RULES (STRICT):**\n"
                    f"❌ NEVER push to protected branches: `main`, `develop`, `master`, `test`, `staging`, "
                    f"`production`\n"
                    f"❌ NEVER delete any branch: No `git branch -d` or `git push --delete`\n"
                    f"❌ NEVER run `git checkout <branch>`, `git switch <branch>`, or create a new feature branch manually\n"
                    f"✅ ONLY push to the dedicated branch already prepared by Nexus for this workspace\n"
                    f"✅ Valid branch prefixes: feat/*, fix/*, hotfix/*, chore/*, refactor/*, docs/*, "
                    f"build/*, ci/*\n"
                    f"⚠️  Violating these rules can break production and cause team disruption\n\n"
                    f"{alignment_context}"
                    f"{instructions}\n\n"
                    f"Runtime context:\n"
                    f"Issue: {issue_url}\n"
                    f"Tier: {tier_name}\n"
                    f"Workflow Tier: {workflow_type}\n\n"
                    f"Task context:\n{task_payload}"
                )
                logger.debug(
                    "Agent prompt built: chars=%s prefix_fp=%s mode=continuation",
                    len(prompt),
                    prompt_prefix_fingerprint(prompt),
                )
                return prompt

            base_prompt = continuation_payload or "Please continue with the next step."
            merge_policy = self._get_merge_policy_block(agent_type)
            prompt = (
                f"You are the {agent_identity} agent. You previously started working on this task.\n\n"
                f"**Assigned Workflow Step:** `{agent_type}`\n\n"
                f"{base_prompt}\n\n"
                f"{merge_policy}"
                f"{instructions}\n\n"
                f"Runtime context:\n"
                f"Issue: {issue_url}\n"
                f"Tier: {tier_name}\n"
                f"Workflow Tier: {workflow_type}\n\n"
                f"{base_prompt}\n\n"
                f"{alignment_context}"
                f"{merge_policy}"
                f"Task content:\n{task_payload}"
            )
            logger.debug(
                "Agent prompt built: chars=%s prefix_fp=%s mode=continuation",
                len(prompt),
                prompt_prefix_fingerprint(prompt),
            )
            return prompt

        prompt = (
            f"You are the {agent_identity} agent. A new task has arrived and an issue has been created.\n\n"
            f"**Assigned Workflow Step:** `{agent_type}`\n\n"
            f"**YOUR JOB:** Execute the `{agent_type}` workflow step for this issue.\n\n"
            f"REQUIRED ACTIONS:\n"
            f"1. Read the issue body and understand the task\n"
            f"2. Perform your role-specific work for this step according to the agent definition\n"
            f"3. Keep changes focused and evidence-based\n"
            f"4. Record outcomes and route to the correct next agent via completion output\n\n"
            f"**DO NOT:**\n"
            f"❌ Invoke, launch, or chain other agents directly\n"
            f"❌ Edit unrelated files or expand scope beyond this step\n\n"
            f"{instructions}\n\n"
            f"Runtime context:\n"
            f"Issue: {issue_url}\n"
            f"Tier: {tier_name}\n"
            f"Workflow Tier: {workflow_type}\n\n"
            f"Task details:\n{task_payload}"
        )
        logger.debug(
            "Agent prompt built: chars=%s prefix_fp=%s mode=initial",
            len(prompt),
            prompt_prefix_fingerprint(prompt),
        )
        return prompt

    def _resolve_launch_agent_identity(self, *, project_name: str = "") -> str:
        """Resolve launch prompt identity from system_operations config."""
        system_operations = self._resolve_system_operations(project_name=project_name)
        configured_identity = str(system_operations.get("launch") or "").strip()
        return configured_identity or "launch"

    def _resolve_system_operations(self, *, project_name: str = "") -> dict:
        """Resolve system_operations from static config or project resolver."""
        resolver = self.config.get("system_operations_resolver")
        normalized_project = str(project_name or "").strip()
        default_project = str(self.config.get("default_project_name") or "").strip()
        project_for_resolution = normalized_project or default_project
        if project_for_resolution and callable(resolver):
            try:
                resolved = resolver(project_for_resolution)
            except Exception:
                resolved = None
            if isinstance(resolved, dict):
                return resolved

        configured = self.config.get("system_operations")
        if isinstance(configured, dict):
            return configured
        return {}

    def _build_alignment_context(
        self, *, task_content: str, workflow_type: str, repo_path: str
    ) -> str:
        """Build repository-backed feature-alignment context block."""
        root = str(repo_path or "").strip() or "."
        try:
            result = self._alignment_service.evaluate(
                request_text=task_content,
                workflow_type=workflow_type,
                repo_path=root,
                max_hits=3,
            )
        except Exception:
            return (
                "**Feature Alignment Report:**\n"
                "- Alignment score: 0.00\n"
                "- Matched artifacts: none\n"
                "- Gaps: unavailable (indexing failed)\n"
                "- Recommended actions: continue fail-open and document assumptions.\n\n"
            )

        matched = ", ".join(result.artifact_paths) if result.artifact_paths else "none"
        gaps = ", ".join(result.gaps) if result.gaps else "none"
        actions = " ".join(f"- {item}" for item in result.recommended_next_actions) or "- none"
        return (
            "**Feature Alignment Report:**\n"
            f"- Alignment score: {result.alignment_score:.2f}\n"
            f"- Alignment summary: {result.alignment_summary}\n"
            f"- Matched artifacts: {matched}\n"
            f"- Gaps: {gaps}\n"
            f"- Recommended actions: {actions}\n\n"
        )

    @staticmethod
    def _get_alignment_output_requirements(agent_type: str) -> str:
        """Require designer step to emit machine-readable alignment outputs."""
        if agent_type != "designer":
            return ""
        return (
            "**Designer Output Contract (required for this feature):**\n"
            "- Include `alignment_score` (0.0-1.0), `alignment_summary`, and `alignment_artifacts` "
            "in both your structured issue comment findings and completion summary JSON.\n"
            "- Keep values deterministic and tied to repository-native artifacts (docs/ADRs/README).\n"
        )

    @staticmethod
    def _get_merge_policy_block(agent_type: str) -> str:
        """Return merge-policy instructions for deployer agents, empty for others."""
        if agent_type not in {"deployer", "ops"}:
            return ""
        return (
            "**⚠️ PR CREATION (REQUIRED if no PR exists):**\n"
            "First check whether a PR/MR already exists for the issue branch using the project's "
            "Git platform tooling.\n"
            "If none is found, CREATE the PR/MR — this is always allowed.\n"
            "For the title: derive it from the issue title using conventional commits format\n"
            '(e.g. "feat: add retry logic" or "fix: handle nil pointer" — NOT "fix: resolve #N").\n\n'
            "**⛔ PR MERGE POLICY (applies ONLY to merging, not creation):**\n"
            "This project enforces `merge_queue.review_mode: manual`.\n"
            "You MUST NOT run merge commands automatically.\n"
            "Instead:\n"
            "1. Ensure the PR/MR exists (create it if missing)\n"
            "2. Verify the PR/MR is ready (CI green, reviews approved)\n"
            "3. Post this comment on the issue:\n"
            "   `🚀 Deployment ready. PR requires human review before merge.`\n"
            "4. Do NOT merge. A human will merge after review.\n\n"
        )

    def _get_workflow_steps_for_prompt(
        self,
        *,
        workflow_path: str,
        agent_type: str,
        workflow_type: str,
    ) -> str:
        """Read workflow file and format steps for prompt context."""
        if not workflow_path:
            return ""
        if not os.path.exists(workflow_path):
            return ""

        return WorkflowDefinition.to_prompt_context(
            workflow_path,
            current_agent_type=agent_type,
            workflow_type=workflow_type,
        )

    def _get_comment_and_summary_instructions(
        self,
        *,
        issue_url: str,
        agent_type: str,
        workflow_path: str,
        workflow_type: str,
        nexus_dir: str,
        project_name: str = "",
        step_id: str = "",
        step_num: int = 0,
    ) -> str:
        """Return instructions for issue completion comment and summary file."""
        issue_match = re.search(r"/issues/(\d+)", issue_url or "")
        issue_num = issue_match.group(1) if issue_match else "UNKNOWN"

        workflow_steps = self._get_workflow_steps_for_prompt(
            workflow_path=workflow_path,
            agent_type=agent_type,
            workflow_type=workflow_type,
        )

        return generate_completion_instructions(
            issue_number=issue_num,
            agent_type=agent_type,
            step_id=step_id,
            step_num=step_num,
            workflow_steps_text=workflow_steps,
            nexus_dir=nexus_dir,
            project_name=project_name,
            completion_backend=self.config.get("completion_backend", "filesystem"),
            webhook_url=self.config.get("webhook_url", ""),
        )


def register_plugins(registry) -> None:
    """Register built-in agent launch policy plugin."""
    from nexus.plugins import PluginKind

    registry.register_factory(
        kind=PluginKind.INPUT_ADAPTER,
        name="agent-launch-policy",
        version="0.1.0",
        factory=lambda config: AgentLaunchPolicyPlugin(config),
        description="Agent launch prompt composition policy",
    )
