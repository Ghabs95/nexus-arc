# ADR 084: Social Media Marketing Workflow

## Status

Proposed

## Context

There is a need to expand Nexus capabilities beyond software development workflows into automated marketing.
Specifically, Nexus needs a social media marketing workflow to generate, schedule, and distribute content across various
platforms automatically.

The existing Nexus runtime already provides the primitives needed to add this safely:

- workflow routing and multi-step orchestration,
- requester-scoped auth/session management,
- completion/audit protocols, and
- project-level workflow definitions under `examples/workflows/`.

The missing piece is a workflow-specific design that explains how campaign data moves through the system without
introducing ad hoc credential handling or one-off agent types.

## Decision

We will implement an automated social media marketing workflow within the Nexus AI Orchestrator.

Detailed implementation design: `docs/DESIGN-Social-Media-Marketing-Workflow.md`.

The first implementation increment remains documentation-first and dry-run-first:

- add an opt-in workflow definition at `examples/workflows/social_media_marketing_workflow.yaml`,
- keep publish execution behind requester-scoped credential resolution and approval gates,
- introduce shared campaign/publish contracts before any live adapter rollout, and
- preserve backward compatibility by leaving existing workflow tiers and project mappings unchanged.

### Architecture

1. **Content Generation Engine**: Leverage the existing AI Orchestrator to consume prompts or source materials (like
   release notes or blog posts) to generate platform-specific content (e.g., Twitter threads, LinkedIn posts).
2. **Platform Integrations**:
    - Implement external adapters for X, LinkedIn, Meta surfaces (Facebook/Instagram), and Discord webhooks.
    - Reuse Nexus requester-scoped auth/session and encrypted credential storage patterns for publish credentials and
      token refresh; do not place raw tokens in issue comments, prompts, `project_config.yaml`, or checked-in `.env`
      files.
3. **Orchestration**:
    - Define a dedicated workflow YAML for marketing campaigns.
    - Reuse the existing enterprise agent types (`triage`, `designer`, `developer`, `reviewer`, `compliance`,
      `deployer`, `writer`, `finalizer`) so the workflow fits the current engine and handoff model.
4. **Implementation Contract**:
    - Add a campaign context model, normalized content bundle output, platform adapter interface, approval routing, and
      publish idempotency keys.

### Content Generation Logic

- **Input**: User provides raw content or a topic via the Nexus chat interface or an issue.
- **Processing**: The `developer` implementation step uses LLM completion to draft variants for each target platform,
  adhering to platform constraints (length, media requirements, hashtag policy, CTA format, and scheduling metadata).
- **Approval**: Drafts are routed through the existing `reviewer` and `compliance` steps, with optional human approval
  gates before publish.

### API and Data Contracts

- Campaign workflow state carries normalized fields such as `campaign_id`, `channels`, `schedule_plan`, `approval_mode`,
  `content_bundle`, and `publish_results`.
- Platform adapters expose a shared contract:
    - `validate_payload(platform, post)`
    - `publish(platform, post, schedule_at)`
    - `get_post_status(platform, external_post_id)`
- Publishing uses an idempotency key derived from `(campaign_id, platform, scheduled_time_utc)` to prevent duplicate
  posts during retries or workflow restarts.

## Consequences

- **Positive**: Extends Nexus utility, providing significant value to product operators and founders for go-to-market
  automation.
- **Negative**: Adds complexity to the agent ecosystem and requires maintaining compliance with third-party social media
  API rate limits and terms of service.
- **Operational trade-off**: Initial rollout should remain dry-run/documentation-first until adapter contracts, approval
  gates, and credential flows are validated.
- **Validation expectation**: New workflow definitions and adapter contracts should ship with workflow-loading,
  contract, and dry-run regression coverage before any live publishing mode is enabled.
