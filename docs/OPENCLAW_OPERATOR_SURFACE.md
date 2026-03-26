# OpenClaw Operator Surface for Nexus ARC

This document describes the operator-oriented Nexus ARC command bridge endpoints
intended for OpenClaw and other trusted control-plane clients.

## Purpose

The original OpenClaw integration was primarily push-based: Nexus could send
notifications and workflow events into OpenClaw.

The operator surface adds a read/control layer so OpenClaw can also:
- inspect workflow state
- summarize active or failed workflows
- inspect runtime health
- explain routing decisions
- perform safe workflow control actions

## Authentication

All `/api/v1/operator/*` endpoints use the same bearer-token protection as the
main command bridge.

Example:

```bash
curl -s http://127.0.0.1:8091/api/v1/operator/runtime-health \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

## Read Endpoints

### Runtime health

```bash
curl -s http://127.0.0.1:8091/api/v1/operator/runtime-health \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

Returns a compact runtime summary including:
- runtime mode
- storage backend
- bridge/OpenClaw env presence
- availability of `gh`, `glab`, and `pgrep`
- active/recent failure counts

### Active workflows

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/active?limit=20" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Recent failures

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/recent-failures?limit=20" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Workflow status

By workflow id:

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/status?workflow_id=nexus-123-full" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

By issue number:

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/status?issue_number=123" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Workflow summary

This is the friendlier endpoint meant for “what is going on here?” questions.
It returns:
- a compact summary string
- the inferred reason/state explanation
- suggested next actions
- underlying workflow details

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/summary?workflow_id=nexus-123-full" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Workflow timeline / step history

Returns a step-by-step execution view including:
- step number
- step name
- agent
- status
- started/completed timestamps
- retry count
- error summary when present

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/timeline?workflow_id=nexus-123-full" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Why stuck

This extends the original summary-style diagnosis so operators can distinguish:
- failed step
- paused workflow
- agent still running
- handoff pending
- cancelled/completed workflow
- unclear state that needs deeper inspection

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/why-stuck?workflow_id=nexus-123-full" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Recent incidents digest

Returns a compact digest of recent problematic workflows across failed, paused,
and retrying/running states.

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/recent-incidents?limit=20" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Authorship audit

Returns a best-effort bot-vs-human provenance summary based on workflow/issue/PR/comment/runtime identity that Nexus currently knows about.

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/authorship-audit?issue_number=123" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

This is intended for operator review, not as a security boundary. Secret values are never returned.

### Approval / blocker awareness

Returns current blocking signals including paused workflows, pending approval-gate records, and downstream review/compliance-style gates when inferable from workflow state.

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/blockers?issue_number=123" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Logs-context shortcut

Returns the current workflow summary together with recent relevant task-log
context when issue-scoped logs are available.

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/workflows/logs-context?issue_number=123" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

### Git identity status

```bash
curl -s http://127.0.0.1:8091/api/v1/operator/git/identity \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

Returns best-effort authentication/availability status for:
- `gh`
- `glab`
- relevant automation-token env presence (presence only, never secret values)

### Routing explain

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/routing/explain?project_key=nexus&task_type=feature" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

Returns a structured explanation of:
- resolved project config
- workflow path
- repo(s)
- default branch
- git platform
- agent preference/profile (when determinable)

### Routing validate

Validates the currently configured routing assumptions for a project/work-type pair. For `project_key=nexus`, this adds best-effort checks for the expected repo split across `nexus-os`, `nexus-arc`, and `nexus`, plus branch/provider expectations when they can be inferred from config.

```bash
curl -s "http://127.0.0.1:8091/api/v1/operator/routing/validate?project_key=nexus&task_type=operator" \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN"
```

## Safe Control Endpoints

### Continue workflow

```bash
curl -s -X POST http://127.0.0.1:8091/api/v1/operator/workflows/continue \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"issue_number":"123"}'
```

Optional targeted reset to an agent before continuing:

```bash
curl -s -X POST http://127.0.0.1:8091/api/v1/operator/workflows/continue \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"issue_number":"123","target_agent":"developer"}'
```

### Retry step

```bash
curl -s -X POST http://127.0.0.1:8091/api/v1/operator/workflows/retry-step \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"issue_number":"123","target_agent":"developer"}'
```

### Cancel workflow

```bash
curl -s -X POST http://127.0.0.1:8091/api/v1/operator/workflows/cancel \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"issue_number":"123"}'
```

### Refresh state

```bash
curl -s -X POST http://127.0.0.1:8091/api/v1/operator/workflows/refresh-state \
  -H "Authorization: Bearer $NEXUS_COMMAND_BRIDGE_AUTH_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"issue_number":"123"}'
```

## Notes

- These endpoints are intentionally operator-oriented and best-effort.
- They are not a replacement for deeper workflow/event inspection.
- The summary endpoint is designed for OpenClaw-style “explain this quickly” UX.
- Token/env reporting only exposes presence/availability, never secret values.
