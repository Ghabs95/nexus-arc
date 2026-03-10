# System Architecture

High-level architecture for the Nexus chat bot runtimes — a workflow automation system that orchestrates AI agents to
complete software development tasks.

## System Overview

```
┌────────────────────────────────────────────────────────────────┐
│                         User Layer                             │
└────────────────────────────────────────────────────────────────┘
                              │
                    ┌─────────▼─────────┐
                    │   Telegram API    │
                    └─────────┬─────────┘
                              │
┌─────────────────────────────▼────────────────────────────────┐
│                   Nexus Bot (telegram_bot.py)                │
│  - Command handlers (/new, /status, /pause, /logs, etc.)    │
│  - Inline keyboard callbacks                                 │
│  - Rate limiting (sliding window)                            │
│  - User authentication                                       │
└───────────────┬───────────────────────────────┬──────────────┘
                │                               │
        ┌───────▼────────┐             ┌────────▼────────┐
        │ State Manager  │             │ User Manager    │
        │ - Workflow     │             │ - Tracking      │
        │ - Agents       │             │ - Projects      │
        │ - Audit log    │             │ - Stats         │
        └───────┬────────┘             └─────────────────┘
                │
┌───────────────▼──────────────────────────────────────────────┐
│               Webhook Server (webhook_server.py)             │
│  - GitHub webhook receiver (signature verification)          │
│  - Completion endpoint (/api/v1/completion)                  │
│  - Inbox processor (monitor → create issue → launch agent)   │
│  - Auto-chain agents on completion                           │
│  - Timeout/retry logic                                       │
└───┬──────────────────────────────────────┬───────────────────┘
    │                                      │
    │                             ┌────────▼────────┐
    │                             │ Agent Monitor    │
    │                             │ - Timeout track  │
    │                             │ - Retry logic    │
    │                             │ - PID tracking   │
    │                             └─────────────────┘
    │
┌───▼─────────────────────────────────────────────────────────┐
│                 GitHub API (via gh CLI)                      │
│  - Create issues with workflow labels                       │
│  - Monitor comments for completion markers                  │
│  - Search for linked PRs                                    │
│  - Post agent updates and handoffs                          │
└───┬─────────────────────────────────────────────────────────┘
    │
┌───▼─────────────────────────────────────────────────────────┐
│               AI Agents (subprocess)                         │
│  - @ProjectLead — Triage and routing                        │
│  - @Architect — Design and ADR                              │
│  - Tier 2 Leads — Implementation                            │
│  - @QAGuard — Quality assurance                             │
│  - @OpsCommander — Deployment                               │
│  - @Scribe — Documentation                                  │
└─────────────────────────────────────────────────────────────┘
```

## Data Flow

### Task Submission

```
User (Voice Note / Text) → Telegram → Bot Transcribes (AI)
                                         ↓
                                 Auto-route to Project
                                         ↓
                              Save to Inbox (FS or Postgres)
                                         ↓
                           Inbox Processor Detects Task
                                         ↓
                             Create GitHub Issue
                                         ↓
                          Launch @ProjectLead Agent
```

### Workflow Execution

```
Agent Starts → Posts to GitHub
                     ↓
              Completes Work
                     ↓
         Writes completion summary
   (JSON file or POST /api/v1/completion)
                     ↓
      Processor detects completion
                     ↓
         Auto-chains to Next Agent
                     ↓
              [Repeat until done]
                     ↓
         Final Agent Completes
                     ↓
      Search for Linked PR
                     ↓
  Notify User with Review Buttons
```

## Core Components

| Component            | File                                   | Purpose                                          |
|----------------------|----------------------------------------|--------------------------------------------------|
| **Telegram Bot**     | `telegram_bot.py`                      | User interface, commands, callbacks              |
| **Webhook Server**   | `webhook_server.py`                    | GitHub events, completion endpoint, agent launch |
| **State Manager**    | `state_manager.py`                     | Persist launched agents, tracked issues          |
| **Inbox Routing**    | `handlers/inbox_routing_handler.py`    | Route tasks to projects                          |
| **Config**           | `config.py`                            | All env vars, project config, storage backends   |
| **Agent Launcher**   | `runtime/agent_launcher.py`            | Subprocess management for AI agents              |
| **Feature Registry** | `services/feature_registry_service.py` | Dedup ideation and track implemented features    |

## Services

The system runs as Linux systemd services:

| Service          | Description                                 |
|------------------|---------------------------------------------|
| `nexus-telegram` | Telegram bot (long-polling or webhook mode) |
| `nexus-discord`  | Discord bot (gateway/slash commands)        |
| `nexus-webhook`  | GitHub webhook receiver + inbox processor   |
| `nexus-health`   | Health check / metrics endpoint             |

All services auto-restart on failure via `Restart=always`.

## Identity Management (UNI)

Nexus uses a platform-agnostic **Universal Nexus Identity (UNI)** system to track users across multiple chat platforms (
Telegram, Discord, etc.).

### Core Concepts

- **Nexus ID:** A canonical UUID4 assigned to every unique user.
- **Identities Map:** A mapping of platform-specific identifiers (e.g., `telegram:123456`, `discord:987654321`) to a
  single `Nexus ID`.
- **Profile Synchronization:** User preferences, project tracking, and task history are keyed by the `Nexus ID`,
  allowing a user to maintain their context when switching platforms.

### Account Linking

Users can link multiple platform identities to their Nexus account. The `UserManager` prevents "identity hijacking" by
rejecting re-binding attempts if a platform identity is already linked to a different `Nexus ID`.

### Migration

The system includes automatic migration for legacy `telegram_id` records. Upon first contact, legacy records are
converted to the UNI format with a newly generated `Nexus ID`.
