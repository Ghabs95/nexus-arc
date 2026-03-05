# Config Bootstrap Lifecycle

This document is the canonical reference for Nexus runtime bootstrap.

## Startup Order

For executable entrypoints (bot/webhook/processor/health):

1. Call `initialize_runtime(...)` from `nexus.core.config.bootstrap`
2. Import and use `nexus.core.config` values/services
3. Start app runtime loop

## Responsibilities

`initialize_runtime(...)` coordinates:

- `bootstrap_environment(secret_file=".env")`
  - Loads `.env` once per process
  - Safe to call repeatedly (idempotent)
- `configure_runtime_logging()`
  - Optional (controlled by `configure_logging=...`)
- `initialize_runtime_directories()`
  - Ensures runtime state/log/storage dirs exist

## Placement Rules

- Call bootstrap in entrypoint modules only.
- Do not call bootstrap in reusable shared modules (handlers, deps, service utilities).
- Shared modules should assume runtime is already initialized.

## Test Lifecycle Hooks

Use these for isolation in tests:

- `nexus.core.rate_limiter.reset_rate_limiter()`
- `nexus.core.audit_store.AuditStore.reset()`
- `nexus.core.audit_store.AuditStore.configure(..., reset=True)`

These hooks avoid cross-test singleton leakage.

## Why This Exists

Nexus previously had import-time side effects in config (dotenv loading, logging setup, directory creation).  
The explicit bootstrap lifecycle removes those side effects and makes startup deterministic and testable.
