# Framework vs Integration Layer

This document explains the separation between **nexus-arc** (the framework) and the **nexus-bot** (your integration
layer).

## The Analogy

| Concept      | Framework (nexus-arc)  | Integration (nexus-bot)             |
|--------------|-------------------------|-------------------------------------|
| Like…        | Django/Flask            | Your web application                |
| Owns…        | Generic orchestration   | Your business logic                 |
| Knows about… | Workflows, steps, state | Your projects, tiers, notifications |

## What Lives Where

| Concern                                | Framework | Integration |
|----------------------------------------|-----------|-------------|
| Workflow state machine                 | ✅         |             |
| Storage adapters (File, Postgres)      | ✅         |             |
| Git platform adapters (GitHub, GitLab) | ✅         |             |
| Plugin registry and discovery          | ✅         |             |
| Agent execution and retry              | ✅         |             |
| Your project structure                 |           | ✅           |
| Your tier/workflow type mapping        |           | ✅           |
| Telegram bot commands                  |           | ✅           |
| Issue → Workflow mapping               |           | ✅           |
| Inbox routing logic                    |           | ✅           |
| Agent chain config                     |           | ✅           |

## Why This Matters

The framework doesn't know about:

- Your specific projects or team structure
- That you use Telegram for input
- Your tier system (full, shortened, fast-track)
- How you map GitHub issues to workflow IDs

Someone else using nexus-arc could use GitLab + Slack + completely different workflow types.

## Integration Code Locations

| Integration concern              | File                                     |
|----------------------------------|------------------------------------------|
| Project config and env vars      | `config.py`                              |
| Workflow creation from issues    | `orchestration/nexus_core_helpers.py`    |
| Plugin wiring and initialization | `orchestration/plugin_runtime.py`        |
| State persistence bridge         | `state_manager.py`                       |
| Workflow state factory           | `integrations/workflow_state_factory.py` |
| Inbox queue (postgres)           | `integrations/inbox_queue.py`            |
| Agent subprocess management      | `runtime/agent_launcher.py`              |

## Framework Code (nexus-arc)

| Framework concern   | Module                            |
|---------------------|-----------------------------------|
| Workflow engine     | `nexus.core.workflow`             |
| Workflow models     | `nexus.core.models`               |
| Agent definitions   | `nexus.core.agents`               |
| YAML loader         | `nexus.core.yaml_loader`          |
| Storage interface   | `nexus.adapters.storage.base`     |
| PostgreSQL backend  | `nexus.adapters.storage.postgres` |
| File backend        | `nexus.adapters.storage.file`     |
| Completion protocol | `nexus.core.completion`           |
| Plugin registry     | `nexus.plugins`                   |

## Adding a New Feature

1. **Define your workflow steps** in `config.py` or a YAML file
2. **Create the integration helper** in `nexus_core_helpers.py`
3. **Wire up the Telegram command** in `telegram_bot.py`
4. The framework handles orchestration, persistence, retries — you don't modify nexus-arc.

## Further Reading

- [nexus-arc/docs/ARCHITECTURE.md](../../../docs/ARCHITECTURE.md) — Framework architecture
- [nexus-arc/docs/PLUGINS.md](../../../docs/PLUGINS.md) — Plugin system internals
- [nexus-arc/docs/USAGE.md](../../../docs/USAGE.md) — Detailed integration patterns
