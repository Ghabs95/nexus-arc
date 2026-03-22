# Design: Social Media Marketing Workflow

**Issue:** #119
**ADR:** [ADR-084](ADR-084-Social-Media-Marketing-Workflow.md)
**Branch:** `feat/design-social-marketing-workflow`
**Status:** Proposed implementation design

---

## Overview

Nexus currently optimizes software-delivery workflows. This design adds a new workflow type that automates
social media campaign planning, content generation, approval, scheduling, and publishing while preserving
existing guardrails (review gates, compliance checks, and auditability).

The workflow is designed for product operators who want to convert source artifacts (release notes, roadmap
updates, product launches, offers) into platform-specific content across channels such as X, LinkedIn,
Instagram, Facebook, and Discord.

This is a workflow-engine extension, not a new standalone product. The design intentionally reuses the
existing Nexus runtime surfaces that already handle issue orchestration, audit history, completion posting,
review routing, and requester-scoped auth.

---

## Goals

- Add a first-class workflow for marketing content lifecycle management.
- Support multi-platform content generation from one source brief.
- Enforce approval/compliance gates before publication.
- Provide deterministic scheduling, retries, and observability.
- Reuse existing Nexus workflow engine, agent model, and completion protocol.

## Non-Goals

- Building new social platforms directly into core runtime as hardcoded logic.
- Replacing existing software-delivery workflows.
- Fully autonomous publishing with no approval option.

---

## Requirements

### Functional

- Ingest campaign brief and source assets.
- Generate platform-specific drafts with channel constraints.
- Support human approval and policy validation before publish.
- Publish immediately or at scheduled times.
- Record posting outcomes and links per platform.

### Non-Functional

- Idempotent posting per campaign item and platform.
- Rate-limit aware retries with exponential backoff.
- Secure credential handling (OAuth tokens/secrets outside prompt context).
- Full audit history for generated content, approvals, and publish actions.

---

## Architecture

### Core Components

1. Workflow Definition (`social_media_marketing_workflow.yaml`)

- Defines step sequence, agent ownership, and routing conditions.

2. Campaign Context Model

- Canonical payload shared across steps:
    - campaign_id
    - objective
    - audience
    - channels[]
    - source_material_refs[]
    - brand_voice_profile
    - approval_mode (`auto` | `human_required`)
    - schedule_plan[]

3. Content Generation Service (LLM-backed)

- Converts campaign context into per-platform variants.
- Applies platform policies: character limits, hashtag caps, CTA structure, and media needs.

4. Platform Adapter Layer

- Connector interface for each platform:
    - `validate_payload(platform, post)`
    - `publish(platform, post, schedule_at)`
    - `get_post_status(platform, external_post_id)`
- Concrete adapters for X, LinkedIn, Meta (Facebook/Instagram), and Discord webhook.

5. Policy/Compliance Guard

- Runs content safety and legal checks (PII, prohibited claims, disallowed topics, token leakage).
- Blocks deploy when policy status is `blocked`.

6. Observability + Audit

- Persist step outputs and completion payloads.
- Emit normalized publish events (`queued`, `published`, `failed`, `retrying`).

### Runtime Placement

- Workflow YAML lives under `examples/workflows/` so it can be selected via project-level workflow config.
- Campaign state lives in the existing workflow state/completion storage path rather than a separate subsystem.
- Publishing credentials should reuse Nexus encrypted credential storage and web-session/requester binding patterns.

### Files and Surfaces

| Surface               | Path                                                      | Role                                                            |
|-----------------------|-----------------------------------------------------------|-----------------------------------------------------------------|
| ADR                   | `docs/ADR-084-Social-Media-Marketing-Workflow.md`         | Records the architectural decision and security posture         |
| Technical design      | `docs/DESIGN-Social-Media-Marketing-Workflow.md`          | Defines contracts, rollout, and implementation strategy         |
| Workflow definition   | `examples/workflows/social_media_marketing_workflow.yaml` | Encodes the step graph, outputs, and approval routing           |
| Project configuration | `config/project_config.yaml` or runtime project settings  | Opts a project into the workflow via `workflow_definition_path` |

### Implementation Strategy

Phase 1 is intentionally documentation-first and dry-run-first:

1. Add the workflow definition under `examples/workflows/` so projects can select it explicitly.
2. Reuse the existing enterprise agent roster (`triage`, `designer`, `developer`, `reviewer`, `compliance`, `deployer`,
   `writer`, `finalizer`) rather than inventing workflow-specific agent types.
3. Introduce a campaign context contract that can be passed through existing completion and audit storage.
4. Keep publishing in dry-run mode until at least one adapter is implemented with requester-scoped auth and compliance
   coverage.
5. Expand from dry-run to live publishing by adding adapter implementations behind the shared platform protocol.

This keeps the first increment aligned with the current Nexus ARC architecture: workflow-driven orchestration,
Git-native audit history, and runtime-resolved credentials instead of prompt-injected secrets.

---

## Workflow Sequence

1. Triage (`triage`)

- Classify request as marketing campaign vs unsupported request.
- Produce priority and channel assumptions.

2. Campaign Design (`designer`)

- Build campaign plan:
    - content pillars
    - channel mix
    - cadence
    - KPI targets

3. Content Implementation (`developer`)

- Generate content set by platform and time slot.
- Attach structured payload for compliance and publishing.

4. Review (`reviewer`)

- Validate quality, tone, and channel formatting.
- Route back to implementation on requested changes.

5. Compliance (`compliance`)

- Check governance/legal/safety constraints.
- Route back to implementation when blocked.

6. Publish (`deployer`)

- Publish approved posts immediately or schedule them.
- Return canonical result set with external post IDs and URLs.

7. Documentation & Close (`writer`)

- Produce campaign summary and KPI baseline report.
- Close issue with links to published artifacts.

8. Rejection Finalization (`finalizer`)

- Close campaign with explicit rejection reason when unrecoverable.

### Approval and Dry-Run Model

- `reviewer` validates message quality, tone, and formatting before any publish step can proceed.
- `compliance` is the hard gate for regulated claims, privacy concerns, embargoed material, and unsafe prompts.
- `deployer` should default to dry-run behavior for initial rollout, producing publish payload previews and idempotency
  keys without hitting live platform APIs.
- Human approval remains required for compliance and deploy, matching the enterprise workflow's approval posture.

---

## API and Integration Contracts

### Internal Campaign Context

The workflow passes a normalized campaign payload between steps:

```json
{
  "campaign_id": "cmp_launch_2026_04",
  "objective": "announce new workflow capability",
  "audience": [
    "founders",
    "operators"
  ],
  "brand_voice_profile": "clear, technical, confident",
  "channels": [
    "x",
    "linkedin",
    "discord"
  ],
  "approval_mode": "human_required",
  "source_material_refs": [
    "issue:119",
    "docs/ADR-084-Social-Media-Marketing-Workflow.md"
  ],
  "schedule_plan": [
    {
      "platform": "x",
      "scheduled_time_utc": "2026-04-02T14:00:00Z"
    },
    {
      "platform": "linkedin",
      "scheduled_time_utc": "2026-04-02T15:00:00Z"
    }
  ]
}
```

### Platform Adapter Contract

Each channel adapter should present the same interface to the workflow engine:

```python
class SocialPlatformAdapter(Protocol):
    def validate_payload(self, platform: str, post: dict[str, Any]) -> ValidationResult: ...

    def publish(self, platform: str, post: dict[str, Any], schedule_at: str | None) -> PublishResult: ...

    def get_post_status(self, platform: str, external_post_id: str) -> PublishStatusResult: ...
```

Normalized publish result:

```json
{
  "platform": "linkedin",
  "status": "published",
  "external_post_id": "urn:li:share:123",
  "external_url": "https://www.linkedin.com/feed/update/urn:li:share:123",
  "published_at": "2026-04-02T15:00:01Z",
  "retryable": false,
  "error_code": null,
  "error_message": null
}
```

### Auth and Credential Flow

- Publishing adapters must not source raw secrets from prompts, issue bodies, or committed config.
- OAuth tokens and refresh material should be stored via the same encrypted credential mechanisms already used by Nexus
  auth flows.
- Execution should resolve credentials in-process using requester-scoped permissions before publish starts.

### Step Output Contract

Each workflow step should emit machine-readable state additions so downstream steps can remain deterministic:

| Step         | Required outputs                                             | Purpose                                                |
|--------------|--------------------------------------------------------------|--------------------------------------------------------|
| `designer`   | `campaign_architecture`, `platform_plan`, `estimated_effort` | Locks the channel set and high-level campaign shape    |
| `developer`  | `content_bundle`, `implementation_notes`, `test_coverage`    | Produces channel-ready drafts and implementation notes |
| `reviewer`   | `review_status`, `review_comments`                           | Approves or routes back for copy changes               |
| `compliance` | `compliance_status`, `compliance_issues`                     | Records governance/security disposition                |
| `deployer`   | `deployment_status`, `publish_results`                       | Captures dry-run previews or live publish responses    |

---

## Platform Integration Strategy

### Adapter Contract

Each integration implements a shared adapter protocol so workflows remain platform-agnostic.

Required adapter capabilities:

- Auth bootstrap and token refresh.
- Payload validation against platform constraints.
- Publish/schedule operation.
- Error normalization to common retry taxonomy.

### Platform Notes

- X: character-first text flow, optional thread mode, stricter rate-limit handling.
- LinkedIn: long-form professional copy support with richer link metadata.
- Instagram/Facebook: media-first publishing path and caption optimization.
- Discord: webhook-friendly short announcements for community distribution.

### Credential Model

- Store credentials in secure runtime config and secret backends.
- Never persist raw secrets in issue comments, prompts, or workflow outputs.
- Mask token-derived values in logs and completion payloads.

### Delivery Modes

| Mode              | Purpose                                            | Allowed side effects                 |
|-------------------|----------------------------------------------------|--------------------------------------|
| `draft_only`      | Generate content bundles for review and compliance | No external API calls                |
| `dry_run_publish` | Validate payloads and simulate publish responses   | Validation-only adapter calls        |
| `live_publish`    | Submit approved content to external platforms      | Real platform publish/schedule calls |

Projects should start with `draft_only` or `dry_run_publish` and only enable `live_publish` after platform-specific
adapter tests, credential onboarding, and compliance rules are in place.

---

## Content Generation Logic

### Inputs

- Campaign brief (goal, offer, audience, tone)
- Source materials (release notes, URLs, docs)
- Channel list and scheduling constraints

### Processing Pipeline

1. Normalize brief into structured campaign context.
2. Derive message map (core claim, evidence, CTA, risk notes).
3. Generate candidate variants per platform and time slot.
4. Score variants against platform rules and brand voice.
5. Select primary + fallback post versions.
6. Emit review package for human or automated gate.

### Output Shape

- `campaign_content_bundle` containing:
    - `platform`
    - `scheduled_time_utc`
    - `copy_primary`
    - `copy_fallback`
    - `hashtags[]`
    - `media_refs[]`
    - `cta`
    - `compliance_notes[]`

Example content bundle item:

```json
{
  "platform": "x",
  "scheduled_time_utc": "2026-04-02T14:00:00Z",
  "copy_primary": "Nexus now supports a design path for automated social media marketing workflows...",
  "copy_fallback": "Designing social marketing automation in Nexus: campaign briefs, approval gates, and publish adapters...",
  "hashtags": [
    "#AI",
    "#Automation",
    "#MarketingOps"
  ],
  "media_refs": [],
  "cta": "Read the workflow design",
  "compliance_notes": [
    "No performance claims",
    "No customer data referenced"
  ]
}
```

### Prompting Rules

- Source prompts from explicit campaign inputs and referenced artifacts only; do not let adapters pull arbitrary
  repository context.
- Include platform constraints, target audience, CTA, and risk notes in the prompt frame so output remains reproducible.
- Store only normalized prompt metadata in workflow state; avoid persisting full secret-bearing request headers or
  platform access tokens.

---

## Data and State

### Workflow State Extensions

Add structured metadata fields:

- `campaign_id`
- `marketing_channels`
- `publish_results`
- `approval_decisions`
- `kpi_snapshot`

### Logical Data Model

| Entity                | Purpose                          | Key Fields                                                               |
|-----------------------|----------------------------------|--------------------------------------------------------------------------|
| `campaign_context`    | Normalized campaign brief        | `campaign_id`, `objective`, `audience`, `channels`, `approval_mode`      |
| `content_bundle_item` | Platform-specific post candidate | `platform`, `scheduled_time_utc`, `copy_primary`, `media_refs`, `cta`    |
| `approval_decision`   | Review/compliance outcome        | `step`, `status`, `reviewer`, `notes`, `decided_at`                      |
| `publish_result`      | External publication record      | `platform`, `external_post_id`, `external_url`, `status`, `published_at` |

### Idempotency

- Use `(campaign_id, platform, scheduled_time_utc)` as publish idempotency key.
- Prevent duplicate posts on retries or workflow restarts.

### Audit Expectations

- Record campaign brief normalization, generated variants, review outcomes, compliance decisions, and publish attempts
  in the existing workflow audit history.
- Completion payloads should summarize outcomes and link back to the canonical issue/PR trail instead of duplicating
  large content blobs in comments.

---

## Failure Handling

- Validation failures: route to `developer` with actionable errors.
- Transient publish errors: bounded retries with backoff.
- Permanent API errors: mark platform post `failed`, continue other platforms when safe.
- Compliance blocks: hard stop until corrected and re-reviewed.

---

## Security and Compliance

- Enforce least-privilege scopes for platform OAuth apps.
- Require explicit human approval for high-risk categories by policy.
- Capture immutable audit records for generated copy and publication decisions.
- Reuse existing OAuth onboarding and requester-scoped credential resolution instead of storing provider tokens in
  project config, issue bodies, prompts, or checked-in files.

---

## Rollout Plan

1. Phase 1: Documentation + workflow definition + dry-run mode (no live publish).
2. Phase 2: Enable Discord + one primary social platform adapter.
3. Phase 3: Expand to full channel set with KPI feedback loops.

### Initial Repository Changes

- Add the ADR capturing architecture and secret-handling constraints.
- Add this design document to define the implementation contract.
- Add `examples/workflows/social_media_marketing_workflow.yaml` as the selectable workflow definition.
- Document the new workflow in `examples/workflows/README.md`.

---

## Effort Estimate

- Phase 1 (this design + workflow contract): 1-2 engineering days.
- Phase 2 (one live adapter, approval flow, dry-run verification): 3-5 engineering days.
- Phase 3 (multi-platform rollout, KPI ingestion, retry hardening): 1-2 engineering weeks depending on adapter count and
  platform review requirements.

---

## Testing Strategy

- Unit tests for adapter payload validation and retry taxonomy.
- Contract tests for normalized adapter response schema.
- Workflow integration tests for approved/blocked/retry branches.
- Dry-run E2E tests that verify completion payload shape and audit traces.

---

## Backward Compatibility

- Existing workflow tiers and issue routing remain unchanged.
- New workflow is opt-in by project-level `workflow_definition_path`.
- No migration required for existing issue state records.

---

## Open Questions

- Which first-class platform should move from dry-run to live publish first: Discord, X, or LinkedIn?
- Should KPI readback be part of the same workflow or a follow-up analytics workflow?
- What approval policy is required for regulated or claim-sensitive campaign categories?
