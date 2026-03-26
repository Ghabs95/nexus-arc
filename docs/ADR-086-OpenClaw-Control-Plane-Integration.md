# ADR-086: OpenClaw Control Plane Integration

**Status:** Accepted
**Date:** 2026-03-26
**Issue:** [#130](https://github.com/ghabs-org/nexus-arc/issues/130)
**PR:** [#131](https://github.com/ghabs-org/nexus-arc/pull/131)
**References:** nexus-os issue #3, roadmap/2026-q2-openclaw-control-plane.md

## Context

Human operators using OpenClaw (the conversational control plane) need: rich actionable status notifications, stable session affinity, reply-to-workflow routing, and structured plan-mode summaries.

## Decision — Four-Pillar Architecture

**Pillar 1 — Rich Workflow Notifications:** WorkflowNotificationPayload v1 typed envelope (schema_version, event_type, workflow_id, step_id, step_num, agent_type, severity, summary, key_findings, correlation_token, session_key, timestamp_utc). Dispatched by nexus/adapters/notifications/openclaw.py::send_workflow_notification().

**Pillar 2 — Stable Session Affinity:** Keys follow nexus::workflow:<workflow_id>. Stored in WorkflowState.metadata, validated on every bridge callback.

**Pillar 3 — Reply-to-Workflow Routing:** POST /api/v1/bridge/openclaw/reply requires API-key/JWT bearer auth + nonce/TTL replay protection before merge.

**Pillar 4 — Structured Plan-Mode Summaries:** PlanModeSummary dataclass rendered as numbered operator-facing list before execution.

## Phased Implementation Plan

### Phase 1 — Foundation (P0)
1. WorkflowNotificationPayload v1 — nexus/adapters/notifications/openclaw.py
2. send_workflow_notification() dispatch
3. Session-key generation & storage — nexus/core/workflow_state.py
4. PlanModeSummary + WorkflowEngine.plan_mode flag — nexus/core/engine.py
5. Bridge reply endpoint skeleton — nexus/command_bridge/router.py
6. Unit tests — tests/test_openclaw_notifications.py

### Phase 2 — Routing & Auth (P0 compliance blockers)
7. API-key/JWT bearer auth + nonce/TTL replay protection on bridge reply endpoint
8. TLS enforcement on all dispatch paths
9. correlation_token to asyncio.Event resolution
10. reply_text input sanitisation

### Phase 3 — Plan Mode & Operator UX
11. PlanModeSummary rendering in OpenClaw plugin
12. Operator approval gate for requires_human_approval steps
13. End-to-end integration tests on feat/openclaw branch

## Security

- Bridge reply endpoint MUST NOT ship unauthenticated (critical compliance blocker).
- TLS required on all dispatch paths.
- correlation_token is a routing hint only, not a credential.

## Consequences

- Establishes stable versioned Nexus-OpenClaw contract enabling human-in-the-loop workflows.
- Risk: asyncio.Event lost on worker restart — mitigate by persisting correlation_token mapping in WorkflowState.

## Related ADRs

- ADR-001: Interactive Client Plugin
- ADR-083: Live Visualizer Updates
- docs/OPENCLAW_RELEASE.md
