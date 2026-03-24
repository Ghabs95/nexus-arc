# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added

- **Social Platform Adapter Layer** — Implemented a robust, idempotent, and secure mechanism for multi-platform content distribution. This includes concrete Python implementations of the `SocialPlatformAdapter` protocol for Discord, X (Twitter), LinkedIn, and Meta (Facebook/Instagram). Key features include:
    - **Platform-Specific Validation** for character limits, thread logic, and rich link metadata.
    - **Campaign Context & State Model** extensions for tracking `campaign_id`, `objective`, and `content_bundle`.
    - **Idempotency & Retry Logic** using `(campaign_id, platform, scheduled_time_utc)` composite keys and exponential backoff with jitter.
    - **Encrypted Credential Management** for requester-scoped OAuth tokens.
    - **Approval Gates** for `reviewer` and `compliance` agents to block live publishing on formatting or regulatory violations.
    Documented in [ADR-085](docs/ADR-085-Social-Platform-Adapters.md) ([#123](https://github.com/Ghabs95/nexus-arc/issues/123)).
- **Social Media Marketing Workflow Design** — Designed a new workflow for automated social media content creation,
  approval, and multi-platform publishing. This includes a technical design
  document ([docs/DESIGN-Social-Media-Marketing-Workflow.md](docs/DESIGN-Social-Media-Marketing-Workflow.md)), an
  Architecture Decision Record ([ADR-084](docs/ADR-084-Social-Media-Marketing-Workflow.md)), and a selectable workflow
  definition ([examples/workflows/social_media_marketing_workflow.yaml](examples/workflows/social_media_marketing_workflow.yaml)).
  The workflow reuses existing enterprise agents (`triage`, `designer`, `developer`, `reviewer`, `compliance`,
  `deployer`, `writer`, `finalizer`) and supports platform-agnostic campaign management with dry-run-first publishing
  controls ([#119](https://github.com/Ghabs95/nexus-arc/issues/119)).
- **WebSocket Agent State Streaming** — Implemented a WebSocket-based streaming mechanism to push agent state changes
  directly to the Live Visualizer. This replaces poll-on-refresh with instantaneous updates via Socket.IO, supporting
  `step_status_changed`, `workflow_completed`, and live `mermaid_diagram` updates as required by
  ADR-083 ([#117](https://github.com/Ghabs95/nexus-arc/issues/117)).
- **Telegram Live Workflow Watch (`/watch`)** — Added a `/watch` command to the Telegram bot that subscribes to live
  workflow events from the `/visualizer` Socket.IO namespace and relays them to chat in real time. Supports
  `step_status_changed`, `workflow_completed`, and optional `mermaid_diagram` digest events. Includes
  exponential-backoff reconnect, dedup/throttle controls, and backend-safe subscription persistence for both
  `filesystem` and `postgres` storage modes. Feature is gated behind `NEXUS_TELEGRAM_WATCH_ENABLED`. Documented in
  ADR-089 ([#106](https://github.com/Ghabs95/nexus-arc/issues/106)).
- **Universal Nexus Identity (UNI)** — Refactored `UserManager` to use a platform-agnostic identity system. Users are
  now assigned a unique `nexus_id` (UUID4) and can link multiple platform identities (Telegram, Discord, etc.) to a
  single profile. This enables seamless profile synchronization and task history tracking across all supported chat
  platforms ([#86](https://github.com/Ghabs95/nexus-arc/issues/86)).
- **UNI Account Linking Safety** — `UserManager.link_identity` now prevents "identity hijacking" by rejecting re-binding
  attempts if a platform identity is already linked to a different `nexus_id`.
- **UNI Migration Logic** — Added automatic migration for legacy `telegram_id`-keyed user data to the new UNI format,
  ensuring no loss of tracking history during the transition.
- **Feature Registry & Ideation Dedup (Telegram Bot)** — Added `FeatureRegistryService` to the Telegram bot to track
  implemented features per project and suppress duplicate ideation suggestions. Supports both filesystem and PostgreSQL
  backends via `NEXUS_STORAGE_BACKEND`. Includes manual operator commands (`/feature_done`, `/feature_list`,
  `/feature_forget`) and automatic ingestion from workflow completion
  summaries ([#88](https://github.com/Ghabs95/nexus-arc/issues/88)).
- **Slack Integration** — Added first-class Slack support via `SlackEventHandlerPlugin` (EventBus subscriber for 7
  workflow lifecycle events, mrkdwn formatting) and `SlackInteractivePlugin` (Socket Mode via `slack-bolt`; no public
  URL required). Adds `SlackNotificationChannel` adapter. Requires `slack-bolt>=1.18` optional dependency (
  `pip install nexus-arc[slack]`) ([#82](https://github.com/Ghabs95/nexus-arc/issues/82)).
- **Configurable Storage Adapters** — Introduced a unified configuration mechanism for storage backends (File and
  PostgreSQL) via `WorkflowStateEnginePlugin`. Users can now switch backends using the `storage_type` configuration key
  or the `NEXUS_STORAGE_BACKEND` environment variable ([#65](https://github.com/Ghabs95/nexus-arc/issues/65)).
- **PostgreSQL Environment Variable Support** — Added `NEXUS_STORAGE_DSN` for secure PostgreSQL connection management,
  avoiding plaintext credentials in configuration files.
- **YAML Workflow Orchestration** — Introduced `YamlWorkflowLoader` for loading and validating workflow definitions from
  YAML. This enables complex multi-step AI workflows with support for parallel execution, conditional branching, and
  retry policies ([#62](https://github.com/Ghabs95/nexus-arc/issues/62)).
- **Schema Validation** — `YamlWorkflowLoader` performs comprehensive schema validation before instantiation, ensuring
  `agent_type`, `retry_policy`, and `parallel` groups are correctly defined.
- **Retry Policies in YAML** — Workflow steps can now define a `retry_policy` block in YAML, supporting `max_retries`,
  `backoff` strategy (exponential, linear, constant), and `initial_delay`.
- **Parallel Step Execution Foundation** — Added `parallel` field to YAML step definitions and
  `WorkflowStep.parallel_with` to the core model. Introduced `WorkflowEngine.get_runnable_steps()` to identify steps
  that can be executed concurrently.
- **Enhanced YAML Loading** — `YamlWorkflowLoader` exported via `nexus.core` for a cleaner public API. Support for
  `strict` mode to promote schema warnings to errors during loading.

### Changed

- `WorkflowDefinition.from_dict()` now parses `retry_policy.max_retries` from YAML into `WorkflowStep.retry`, while
  maintaining backward compatibility for explicit `retry` integer fields.
- `WorkflowStep` model updated with `parallel_with: List[str]` field to track concurrent execution dependencies.

### Security

- **Safe YAML Loading** — All YAML loading now exclusively uses `yaml.safe_load()` to prevent arbitrary code execution
  vulnerabilities from untrusted YAML content.

---

_Generated by @Writer — nexus-arc
issues [#62](https://github.com/Ghabs95/nexus-arc/issues/62), [#119](https://github.com/Ghabs95/nexus-arc/issues/119)_
