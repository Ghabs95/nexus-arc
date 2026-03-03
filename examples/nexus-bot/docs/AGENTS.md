# Nexus Telegram Bot — Agent Instructions

You are operating inside the Nexus Telegram Bot codebase.
This file optimizes agent behavior for designing, implementing, and reviewing the bot's features.

## Mission

- Build a reliable, extensible workflow automation system.
- Orchestrate AI agents through well-defined lifecycle steps.
- Support both filesystem and PostgreSQL storage backends seamlessly.

## Scope

- Applies to all work in the `examples/nexus-bot/` directory and its integration with `nexus-arc`.

## Authority Model

1. `docs/architecture.md` — System-level design decisions
2. `docs/workflow-lifecycle.md` — Workflow tiers, states, approval gates
3. `docs/storage-backends.md` — Storage architecture
4. `docs/completion-protocol.md` — Agent completion contract
5. `config/project_config.yaml` — Project and agent configuration

If conflicts exist between docs, resolve explicitly or surface them.

## Mandatory Session Protocol

1. Read `AGENTS.md` (this file).
2. Read the relevant docs for your task:
    - Architecture overview → `docs/architecture.md`
    - Storage layer → `docs/storage-backends.md`
    - How agents report → `docs/completion-protocol.md`
    - Task routing → `docs/inbox-routing.md`
    - Workflow chain → `docs/workflow-lifecycle.md`
    - Plugin system → `docs/plugin-system.md`
    - Env vars and endpoints → `docs/config-reference.md`
    - Framework vs integration → `docs/framework-integration.md`
    - Live visualizer updates ADR → `ADR-083-Live-Visualizer-Updates.md`
3. Understand the framework boundary (nexus-arc ≠ nexus-bot integration).
4. Make atomic, scoped changes.
5. Summarize exactly what changed and why.

## Operating Rules

- **Backend-aware**: All state persistence must respect `NEXUS_STORAGE_BACKEND`. Never hardcode filesystem-only paths.
- **Framework boundary**: Do not put integration-specific logic into `nexus-arc`. Keep project-specific code in the
  nexus-bot layer.
- **Plugin pattern**: Use the plugin registry for new integrations. Don't import concrete implementations directly.
- **Completion contract**: Agents must produce structured completion summaries (not raw log dumps).
- **Approval gates**: Never bypass `require_human_merge_approval` policy checks.

## Key Code Locations

| Concern                     | File                                                |
|-----------------------------|-----------------------------------------------------|
| Bot commands                | `src/telegram_bot.py`                               |
| Webhook + inbox processor   | `src/webhook_server.py`                             |
| Host state (agents, issues) | `src/state_manager.py`                              |
| Task routing                | `src/handlers/inbox_routing_handler.py`             |
| Agent subprocess launch     | `src/runtime/agent_launcher.py`                     |
| Feature Registry service    | `src/services/feature_registry_service.py`          |
| Feature Registry handlers   | `src/handlers/feature_registry_command_handlers.py` |
| Plugin wiring               | `src/orchestration/plugin_runtime.py`               |
| Workflow helpers            | `src/orchestration/nexus_core_helpers.py`           |
| Project config              | `config/project_config.yaml`                        |

## Definition of Done

- Change works with both `filesystem` and `postgres` backends.
- No real project names or credentials in example code.
- Completion protocol respected (structured JSON, not raw text).
- Config changes documented in `docs/config-reference.md`.
