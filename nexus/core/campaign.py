"""Campaign context and state models for social-media workflow orchestration."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import Enum
from typing import Any


class CampaignStatus(str, Enum):
    """Lifecycle status of a campaign."""

    DRAFT = "draft"
    PENDING_APPROVAL = "pending_approval"
    APPROVED = "approved"
    SCHEDULED = "scheduled"
    PUBLISHING = "publishing"
    PUBLISHED = "published"
    REJECTED = "rejected"
    FAILED = "failed"


@dataclass
class CampaignContext:
    """Core campaign metadata carried through a workflow execution.

    Args:
        campaign_id: Unique identifier for the campaign (used in idempotency keys).
        objective: High-level marketing objective, e.g. "product_launch", "awareness".
        audience: Target audience descriptor (never contains PII).
        channels: List of platform names to publish to, e.g. ["discord", "x", "linkedin"].
        metadata: Freeform campaign-specific data (titles, dates, tags, etc.).
    """

    campaign_id: str
    objective: str
    audience: str
    channels: list[str] = field(default_factory=list)
    status: CampaignStatus = CampaignStatus.DRAFT
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PlatformContent:
    """Platform-specific content slice within a :class:`ContentBundle`.

    Args:
        platform: Target platform identifier (e.g. "x", "linkedin").
        copy: The finalized copy text for this platform.
        media_refs: List of media asset references (URLs or storage keys).
        scheduled_time_utc: ISO-8601 UTC datetime string for scheduled publishing.
        metadata: Extra platform controls (thread mode, hashtags, link preview, etc.).
    """

    platform: str
    copy: str
    media_refs: list[str] = field(default_factory=list)
    scheduled_time_utc: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ContentBundle:
    """Structured output of the content-generation phase.

    Holds per-platform copy, media references, and scheduling info for one
    campaign execution.  Stored in workflow state and consumed by the deployer.
    """

    campaign_id: str
    platforms: list[PlatformContent] = field(default_factory=list)
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def get_platform_content(self, platform: str) -> PlatformContent | None:
        """Return the :class:`PlatformContent` for *platform*, or ``None``."""
        for item in self.platforms:
            if item.platform == platform:
                return item
        return None


@dataclass
class ApprovalDecision:
    """Human or automated approval/rejection decision for a campaign.

    Args:
        approved: ``True`` if the campaign passed review.
        reviewer: Identity of the reviewer (username or agent_type).
        notes: Optional free-text rationale.
        decided_at: ISO-8601 UTC timestamp of the decision.
    """

    approved: bool
    reviewer: str
    notes: str = ""
    decided_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())


@dataclass
class PublishRecord:
    """Single publish outcome stored in :attr:`CampaignState.publish_results`."""

    platform: str
    success: bool
    post_id: str | None
    idempotency_key: str
    dry_run: bool = False
    error: str | None = None
    published_at: str | None = None


@dataclass
class CampaignState:
    """Full mutable state of a campaign as it moves through the workflow.

    This object is serialised into workflow context and updated by each agent
    step (designer, reviewer, compliance, deployer).

    Args:
        campaign: Immutable campaign context (id, objective, audience, channels).
        content_bundle: Platform-specific copy and media refs; set by designer.
        approval_decisions: Ordered list of approval/rejection decisions.
        publish_results: Per-platform publish outcomes from the deployer.
        status: Current campaign lifecycle status.
    """

    campaign: CampaignContext
    content_bundle: ContentBundle | None = None
    approval_decisions: list[ApprovalDecision] = field(default_factory=list)
    publish_results: list[PublishRecord] = field(default_factory=list)
    status: CampaignStatus = CampaignStatus.DRAFT
    updated_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    # ------------------------------------------------------------------
    # Convenience helpers
    # ------------------------------------------------------------------

    def is_approved(self) -> bool:
        """Return ``True`` if the most recent approval decision is approved."""
        if not self.approval_decisions:
            return False
        return self.approval_decisions[-1].approved

    def is_rejected(self) -> bool:
        """Return ``True`` if the most recent approval decision is rejected."""
        if not self.approval_decisions:
            return False
        return not self.approval_decisions[-1].approved

    def add_approval(self, decision: ApprovalDecision) -> None:
        """Append *decision* and update :attr:`status` accordingly."""
        self.approval_decisions.append(decision)
        self.status = CampaignStatus.APPROVED if decision.approved else CampaignStatus.REJECTED
        self.updated_at = datetime.now(UTC).isoformat()

    def add_publish_result(self, record: PublishRecord) -> None:
        """Append *record* to :attr:`publish_results`."""
        self.publish_results.append(record)
        self.updated_at = datetime.now(UTC).isoformat()

    def all_published(self) -> bool:
        """Return ``True`` when every channel in the campaign has a success record."""
        if not self.campaign.channels:
            return False
        published_platforms = {r.platform for r in self.publish_results if r.success}
        return set(self.campaign.channels).issubset(published_platforms)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a plain dict suitable for workflow state storage."""
        import dataclasses

        return dataclasses.asdict(self)
