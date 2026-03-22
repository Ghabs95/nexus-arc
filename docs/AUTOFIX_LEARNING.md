# Autofix Learning Contract

This document defines the Nexus ARC autofix learning event contract and a safe rollout path.

## Purpose

Autofix learning captures structured signals from repair attempts so retries can be safer and less repetitive.

The design is bounded and audit-friendly:

- no autonomous policy rewrites
- no hidden state outside workflow/audit artifacts
- no bypass of review or deployment gates

## Event Contract

Autofix learning uses audit events emitted by the workflow completion path.

### Event Types

- `AUTOFIX_ATTEMPTED`
- `AUTOFIX_FAILED`
- `AUTOFIX_VALIDATED`
- `AUTOFIX_RETRY_GUARD_BLOCKED`

### Payload Shape

All autofix events carry a `data` object with these normalized fields when available:

- `step_num` (int)
- `step_name` (string)
- `agent_type` (string)
- `retry_count` (int)
- `retry_planned` (bool)
- `strategy` (string, optional)
- `error_fingerprint` (string, optional)
- `error_excerpt` (string, optional)

### Fingerprinting Rules

`error_fingerprint` is normalized to reduce noisy variation:

- file paths replaced with `<path>`
- numbers replaced with `<num>`
- commit-like hex tokens replaced with `<hex>`
- whitespace collapsed

## Retrieval API

Use `AuditStore.get_autofix_events(...)` for bounded lookups:

- filters by `agent_type`
- filters by `error_fingerprint`
- filters by event type list
- uses a bounded fetch window to avoid full-history scans

For additional matching logic, use `find_similar_autofix_attempts(...)`.

## Retry Guard Behavior

`NexusAgentRuntime.should_retry(...)` now includes an autofix-history guard.

The guard blocks retries when recent history suggests repeated unsafe loops, for example:

- consecutive `AUTOFIX_FAILED` streaks
- dense recent failures without a `AUTOFIX_VALIDATED`

When blocked, the runtime emits `AUTOFIX_RETRY_GUARD_BLOCKED` for traceability.

## Rollout Plan

1. Observe-only

- Enable event emission and retrieval in dashboards/reports.
- Do not alter retry policy.

2. Soft guard

- Enable guard with warning/alert only.
- Monitor false positives.

3. Enforced guard

- Block retries for clear repeated-failure patterns.
- Keep manual override paths (`/reprocess`, `/resume`).

4. Continuous tuning

- Tune streak/window thresholds from real incident data.
- Promote stable patterns into project memory/instructions.

## Operational Notes

- Keep limits small for hot paths.
- Treat `AUTOFIX_VALIDATED` as a reset signal for failure streaks.
- Prefer explicit, auditable transitions over hidden autonomous behavior.
