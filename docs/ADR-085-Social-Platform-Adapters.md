# ADR 085: Social Platform Adapters Implementation

## Status
Accepted

## Context
As defined in ADR 084, Nexus requires a social media marketing workflow. A core component of this workflow is the **Social Platform Adapter Layer**, which abstracts the intricacies of publishing across various platforms (Discord, X/Twitter, LinkedIn, Meta). 

The goal is to establish a robust, idempotent, and secure mechanism for automated content distribution while respecting platform-specific constraints, rate limits, and security policies.

## Decision
We will implement the `SocialPlatformAdapter` protocol in Python to normalize publishing and validation across all supported channels.

### 1. Platform Adapter Layer
We will define a `SocialPlatformAdapter` protocol with concrete implementations for each target platform:
- **DiscordAdapter**: First live integration, primarily used for dry-run verification and internal notifications.
- **XAdapter (Twitter)**: Implements strict rate-limit handling and logic for character limits and thread creation.
- **LinkedInAdapter**: Supports long-form professional copy and rich link metadata.
- **MetaAdapter (Facebook/Instagram)**: Focuses on media-first publishing capabilities.

The protocol will enforce standard methods:
- `validate(post)`
- `publish(post)`
- `dry_run(post)`

### 2. Campaign Context & State Model
We will extend the Nexus workflow state models in Python to support the new campaign payload schema.
- Add fields for tracking `campaign_id`, `objective`, `audience`, and targeted channels.
- Define a structured `content_bundle` schema for storing platform-specific copy, media references, and scheduled times.
- Integrate audit state fields: `publish_results` and `approval_decisions` to track the lifecycle of each campaign.

### 3. Credential Management & Auth Flow
Security is paramount. We will integrate with Nexus's existing encrypted credential mechanisms:
- Use requester-scoped OAuth tokens for third-party platforms.
- Strictly enforce that no raw secrets or access tokens are injected into LLM prompts, issue bodies, or project configurations.

### 4. Live Publish Execution & Idempotency
To ensure reliability and prevent duplicate posts:
- Add logic to the deployer agent to transition from `dry_run_publish` (validation only) to `live_publish` mode.
- Derive idempotency keys using the composite: `(campaign_id, platform, scheduled_time_utc)`. This prevents duplicate posts during retries or workflow restarts.
- Implement exponential backoff retry mechanisms tuned for external social API rate limits.

### 5. Policy & Approval Gates
Harden the compliance and review gates in the Python runtime:
- The `reviewer` agent will validate formatting and platform constraints.
- The `compliance` agent will block the deployer if regulated claims or unsafe prompts are detected.

## Consequences
- **Positive**: Provides a scalable and unified interface for adding future social platforms. Ensures high security by leveraging existing encrypted credential stores.
- **Negative**: Increased complexity in the Python runtime and state models. Requires careful handling of varied external API rate limits and error responses.
- **Next Steps**: The developer agent will proceed with the concrete Python implementations of the protocol and state model extensions based on this design.
