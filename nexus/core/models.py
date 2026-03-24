"""Core data models for Nexus workflows."""

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class WorkflowState(Enum):
    """Workflow execution state."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepStatus(Enum):
    """Individual step execution status."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class Severity(Enum):
    """Alert/notification severity levels."""

    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


class ApprovalGateType(Enum):
    """Types of approval gates that can be applied to workflow steps."""

    PR_MERGE = "pr_merge"  # Blocks PR merge operations
    DEPLOYMENT = "deployment"  # Blocks deployment operations
    DATA_ACCESS = "data_access"  # Blocks sensitive data access
    PUBLISH = "publish"  # Blocks social-media publish operations
    CUSTOM = "custom"  # Custom approval gate


@dataclass
class ApprovalGate:
    """Approval gate configuration for workflow steps."""

    gate_type: ApprovalGateType
    required: bool = True  # If True, human approval required
    tool_restrictions: list[str] = field(default_factory=list)  # Blocked commands/tools
    approval_message: str | None = None  # Custom message for agent
    metadata: dict[str, Any] = field(default_factory=dict)

    @staticmethod
    def pr_merge_gate() -> "ApprovalGate":
        """Create a PR merge approval gate."""
        return ApprovalGate(
            gate_type=ApprovalGateType.PR_MERGE,
            required=True,
            tool_restrictions=["vcs:merge_pr", "git push origin main", "git push origin master"],
            approval_message=(
                "🚨 **PR MERGE APPROVAL POLICY (CRITICAL):**\n"
                "❌ DO NOT merge Pull Requests automatically\n"
                "❌ DO NOT use `vcs:merge_pr` command\n"
                "✅ You MAY create PRs with `vcs:create_pr`\n"
                "✅ Post PR link in your review comment\n"
                "✅ Human approval REQUIRED before merge\n"
                "⚠️  Violating this can break production - wait for human review"
            ),
        )

    @staticmethod
    def publish_gate() -> "ApprovalGate":
        """Create a social-media publish approval gate.

        Blocks deployer from executing live publish operations until the
        reviewer and compliance agents have explicitly approved the content.
        """
        return ApprovalGate(
            gate_type=ApprovalGateType.PUBLISH,
            required=True,
            tool_restrictions=["social:live_publish", "social:publish"],
            approval_message=(
                "🚨 **SOCIAL PUBLISH APPROVAL POLICY:**\n"
                "❌ DO NOT execute live_publish before reviewer and compliance approval\n"
                "❌ DO NOT call social:live_publish or social:publish directly\n"
                "✅ Run dry_run validation first — fix all errors before proceeding\n"
                "✅ Reviewer and compliance agents MUST approve content\n"
                "✅ Regulated claims and unsafe prompts block this gate automatically\n"
                "⚠️  Publishing non-compliant content may violate platform policies"
            ),
        )


@dataclass
class Agent:
    """AI agent definition."""

    name: str
    display_name: str
    description: str
    provider_preference: str | None = None  # "openai", "copilot", "gemini", etc.
    timeout: int = 600  # seconds
    max_retries: int = 3
    allowed_tools: list[str] = field(default_factory=list)  # Restricted sandbox tools, if any

    def __str__(self) -> str:
        return f"@{self.name}"

    def __hash__(self) -> int:
        return hash(self.name)


@dataclass
class WorkflowStep:
    """Single step in a workflow execution."""

    step_num: int
    name: str
    agent: Agent
    prompt_template: str
    condition: str | None = None  # Python expression, e.g. "prev_step.result.tier == 'high'"
    timeout: int | None = None  # Override agent default
    retry: int | None = None  # Override agent default
    backoff_strategy: str | None = None  # Retry backoff strategy: exponential, linear, constant
    initial_delay: float = 0.0  # Initial delay in seconds before first retry
    inputs: dict[str, Any] = field(default_factory=dict)
    outputs: dict[str, Any] = field(default_factory=dict)
    status: StepStatus = StepStatus.PENDING
    started_at: datetime | None = None
    completed_at: datetime | None = None
    error: str | None = None
    retry_count: int = 0  # Number of retries attempted so far
    require_human_approval: bool = False  # If this inline step specifically needs approval
    approval_gates: list[ApprovalGate] = field(default_factory=list)  # Approval gates for this step
    routes: list[dict[str, Any]] = field(
        default_factory=list
    )  # Router branch definitions (router steps only)
    on_success: str | None = None  # Named step (id) to activate after success
    final_step: bool = False  # Mark step as terminal for workflow completion
    iteration: int = 0  # Times this step has been re-activated via a workflow goto
    parallel_with: list[str] = field(default_factory=list)  # Step ids that run alongside this step

    def __str__(self) -> str:
        return f"Step {self.step_num}: {self.name} ({self.agent.name})"

    def has_approval_gate(self, gate_type: ApprovalGateType) -> bool:
        """Check if step has a specific approval gate type."""
        return any(gate.gate_type == gate_type and gate.required for gate in self.approval_gates)

    def get_approval_constraints(self) -> str:
        """Get combined approval constraint messages for all gates."""
        messages = [
            str(gate.approval_message)
            for gate in self.approval_gates
            if gate.required and gate.approval_message
        ]
        return "\n\n".join(messages) if messages else ""

    def get_tool_restrictions(self) -> list[str]:
        """Get all tool restrictions from approval gates."""
        restrictions = []
        for gate in self.approval_gates:
            if gate.required:
                restrictions.extend(gate.tool_restrictions)
        return list(set(restrictions))  # Deduplicate


@dataclass
class Workflow:
    """Complete workflow definition and state."""

    id: str
    name: str
    version: str
    description: str = ""
    steps: list[WorkflowStep] = field(default_factory=list)
    state: WorkflowState = WorkflowState.PENDING
    current_step: int = 0
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    updated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    completed_at: datetime | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    require_human_merge_approval: bool = True  # Workflow-level PR merge approval policy
    require_approval_for: list[str] = field(default_factory=list)  # Step IDs requiring human approval

    def get_step(self, step_num: int) -> WorkflowStep | None:
        """Get step by number."""
        for step in self.steps:
            if step.step_num == step_num:
                return step
        return None

    def get_next_step(self) -> WorkflowStep | None:
        """Get the next pending step."""
        return self.get_step(self.current_step + 1)

    def is_complete(self) -> bool:
        """Check if workflow is complete."""
        return self.state in (
            WorkflowState.COMPLETED,
            WorkflowState.FAILED,
            WorkflowState.CANCELLED,
        )

    @property
    def active_agent_type(self) -> str | None:
        """Return the agent_type of the currently RUNNING step, or None.

        Useful after ``WorkflowEngine.complete_step()`` to discover which agent
        should run next without inspecting individual steps.
        """
        step = self.get_step(self.current_step)
        if step and step.status == StepStatus.RUNNING:
            return step.agent.name
        return None

    def apply_approval_gates(self) -> None:
        """Apply workflow-level approval gates to all steps.

        This should be called after workflow is loaded from YAML to ensure
        workflow-level policies are applied to individual steps.
        """
        import os
        
        strict_approvals = str(os.getenv("NEXUS_STRICT_APPROVALS", "false")).lower() == "true"

        if self.require_human_merge_approval:
            pr_merge_gate = ApprovalGate.pr_merge_gate()
            for step in self.steps:
                # Add PR merge gate if not already present
                if not step.has_approval_gate(ApprovalGateType.PR_MERGE):
                    step.approval_gates.append(pr_merge_gate)

        human_approval_gate = ApprovalGate(
            gate_type=ApprovalGateType.CUSTOM,
            required=True,
            approval_message=(
                "🚨 **HUMAN APPROVAL REQUIRED:**\n"
                "This step requires human approval before proceeding."
            ),
        )

        for step in self.steps:
            needs_approval = (
                strict_approvals 
                or step.name in self.require_approval_for 
                or f"step_{step.step_num}" in self.require_approval_for
                or getattr(step, "id", None) in self.require_approval_for
                or step.require_human_approval
            )
            if needs_approval and not step.has_approval_gate(ApprovalGateType.CUSTOM):
                step.approval_gates.append(human_approval_gate)

    def __len__(self) -> int:
        return len(self.steps)


@dataclass
class ImageAttachment:
    """An image attached to a task input."""

    file_id: str
    filename: str = ""
    mime_type: str = "image/jpeg"


@dataclass
class Task:
    """Input task to be processed by workflow."""

    id: str
    workflow_id: str
    title: str
    description: str
    created_by: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    metadata: dict[str, Any] = field(default_factory=dict)
    attachments: list[ImageAttachment] | None = None

    def __str__(self) -> str:
        return f"Task #{self.id}: {self.title}"


@dataclass
class AgentResult:
    """Result from agent execution."""

    success: bool
    output: str
    metadata: dict[str, Any] = field(default_factory=dict)
    execution_time: float = 0.0
    provider_used: str | None = None
    error: str | None = None
    retry_count: int = 0


@dataclass
class AuditEvent:
    """Single audit log entry."""

    workflow_id: str
    timestamp: datetime
    event_type: str  # e.g., "STEP_STARTED", "STEP_COMPLETED", "WORKFLOW_PAUSED"
    data: dict[str, Any]
    user_id: str | None = None

    def __str__(self) -> str:
        return f"[{self.timestamp.isoformat()}] {self.event_type}: {self.workflow_id}"


@dataclass
class RateLimitStatus:
    """Rate limit status for an AI provider."""

    provider: str
    is_limited: bool
    reset_at: datetime | None = None
    requests_remaining: int | None = None
    requests_limit: int | None = None


@dataclass
class DryRunReport:
    """Result of a workflow dry-run validation and simulation."""

    errors: list[str] = field(default_factory=list)
    predicted_flow: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """Return True when no configuration errors were detected."""
        return len(self.errors) == 0


class DelegationStatus(Enum):
    """Lifecycle status of an agent delegation."""

    PENDING = "pending"
    ACTIVE = "active"
    COMPLETED = "completed"
    FAILED = "failed"
    EXPIRED = "expired"


@dataclass
class DelegationRequest:
    """Request issued by a lead agent to delegate a sub-task to another agent."""

    lead_agent: str
    sub_agent: str
    issue_number: str
    workflow_id: str
    task_description: str
    task_context: dict[str, Any] = field(default_factory=dict)
    delegation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    status: DelegationStatus = DelegationStatus.PENDING
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    expires_at: str | None = None

    def __post_init__(self) -> None:
        # Normalize to ACTIVE on construction so callers don't have to set it
        if self.status == DelegationStatus.PENDING:
            self.status = DelegationStatus.ACTIVE


@dataclass
class DelegationCallback:
    """Structured callback emitted by a sub-agent upon completing a delegation."""

    delegation_id: str
    sub_agent: str
    lead_agent: str
    issue_number: str
    workflow_id: str
    result: dict[str, Any]
    success: bool
    error: str | None = None
    completed_at: str | None = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class WorkflowExecution:
    """Complete workflow execution context."""

    workflow: Workflow
    task: Task
    current_context: dict[str, Any] = field(default_factory=dict)
    audit_log: list[AuditEvent] = field(default_factory=list)

    def add_audit_event(self, event_type: str, data: dict[str, Any]) -> None:
        """Add an audit event."""
        event = AuditEvent(
            workflow_id=self.workflow.id,
            timestamp=datetime.now(UTC),
            event_type=event_type,
            data=data,
        )
        self.audit_log.append(event)
