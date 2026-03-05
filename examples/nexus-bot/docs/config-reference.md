# Configuration Reference

All configuration lives in environment variables (loaded from `.env`) and `config/project_config.yaml`.

## Environment Variables

### Core

| Variable           | Required | Default | Description                 |
|--------------------|----------|---------|-----------------------------|
| `TELEGRAM_TOKEN`   | ✅        | —       | Telegram Bot API token      |
| `TELEGRAM_CHAT_ID` | ✅        | —       | Target Telegram chat ID     |
| `ALLOWED_USER`     | ✅        | —       | Authorized Telegram user ID |

### Storage

| Variable                                       | Required | Default                    | Description                                                            |
|------------------------------------------------|----------|----------------------------|------------------------------------------------------------------------|
| `NEXUS_STORAGE_BACKEND`                        | ❌        | `filesystem`               | Primary storage backend: `filesystem` or `postgres` (`database` alias) |
| `NEXUS_WORKFLOW_BACKEND`                       | ❌        | follows primary            | Workflow state backend override                                        |
| `NEXUS_INBOX_BACKEND`                          | ❌        | follows primary            | Inbox queue backend override                                           |
| `NEXUS_STORAGE_DSN`                            | 🟡       | —                          | PostgreSQL connection string (required if using postgres)              |
| `NEXUS_RATE_LIMIT_BACKEND`                     | ❌        | `redis`                    | Rate-limit backend: `redis`, `database`, or `filesystem`               |
| `REDIS_URL`                                    | ❌        | `redis://localhost:6379/0` | Redis connection URL used by rate limits/chat memory                   |
| `NEXUS_FEATURE_REGISTRY_ENABLED`               | ❌        | `true`                     | Enable implemented-feature registry and ideation dedup                 |
| `NEXUS_FEATURE_REGISTRY_MAX_ITEMS_PER_PROJECT` | ❌        | `500`                      | Maximum implemented features retained per project                      |
| `NEXUS_FEATURE_REGISTRY_DEDUP_SIMILARITY`      | ❌        | `0.86`                     | Fuzzy similarity threshold used for ideation dedup filtering           |

### Webhook Server

| Variable                        | Required | Default | Description                                                                                   |
|---------------------------------|----------|---------|-----------------------------------------------------------------------------------------------|
| `WEBHOOK_PORT`                  | ❌        | `8081`  | Port for the webhook server                                                                   |
| `WEBHOOK_SECRET`                | ❌        | —       | GitHub webhook HMAC secret for signature verification                                         |
| `NEXUS_VISUALIZER_ENABLED`      | ❌        | `true`  | Enable/disable visualizer endpoints (`/visualizer`, `/visualizer/snapshot`, socket namespace) |
| `NEXUS_VISUALIZER_SHARED_TOKEN` | ❌        | —       | Shared token for visualizer access (works without DB auth via cookie/header)                  |

### Project

| Variable                 | Required | Default                      | Description                           |
|--------------------------|----------|------------------------------|---------------------------------------|
| `PROJECT_CONFIG_PATH`    | ❌        | `config/project_config.yaml` | Path to project configuration file    |
| `BASE_DIR`               | ❌        | `.`                          | Base directory for project workspaces |
| `NEXUS_CORE_STORAGE_DIR` | ❌        | `.nexus`                     | Root directory for Nexus state files  |

### Agent Runtime

| Variable                      | Required | Default   | Description                           |
|-------------------------------|----------|-----------|---------------------------------------|
| `AGENT_RECENT_WINDOW`         | ❌        | `120`     | Seconds to retain launched agent PIDs |
| `AGENT_TIMEOUT`               | ❌        | `3600`    | Default agent timeout in seconds      |
| `NEXUS_AGENT_TIMEOUT`         | ❌        | `3600`    | Core engine default timeout fallback  |
| `COPILOT_PROVIDER`            | ❌        | `copilot` | AI CLI provider binary name           |
| `NEXUS_FULL_WORKFLOW_CONTEXT` | ❌        | `false`   | Set `true` to inject full step schema |
| `INBOX_CHECK_INTERVAL`        | ❌        | `10`      | Polling interval for completions      |
| `AUTO_CHAIN_CYCLE`            | ❌        | `60`      | Polling interval for auto-chaining    |

## Project Config (`project_config.yaml`)

AI tool preference provider values support `copilot`, `gemini`, `codex`, and `claude`.
This applies to:

- `model_profiles.<profile>.<provider>`
- `profile_provider_priority.<profile>[]`
- `ai_tool_preferences.<agent>.provider`

```yaml
# Each key is a project identifier (used in inbox routing)
my_project:
  workspace: "../my-project"          # Relative path to workspace root
  agents_dir: "../my-agents/agents"   # Path to agent YAML definitions
  git_repo: "org/my-project"          # Primary GitHub repo (owner/name)
  git_repos: # Additional repos for multi-repo projects
    - "org/my-project"
    - "org/my-shared-lib"

another_project:
  workspace: "../another-project"
  agents_dir: "../another-agents/agents"
  git_repo: "org/another-project"

# Global settings
require_human_merge_approval: always  # always | workflow-based | never

# Optional AI routing examples
model_profiles:
  balanced:
    claude: "claude-sonnet-4"
profile_provider_priority:
  balanced: [ "claude", "codex", "gemini", "copilot" ]
ai_tool_preferences:
  writer:
    provider: "claude"
    profile: "balanced"
```

## State Files (filesystem backend)

| File                    | Location        | Contents                                           |
|-------------------------|-----------------|----------------------------------------------------|
| `launched_agents.json`  | `.nexus/state/` | Active agent PIDs with timestamps                  |
| `tracked_issues.json`   | `.nexus/state/` | User-subscribed issue notifications                |
| `user_tracking.json`    | `.nexus/state/` | UNI profiles and identity mappings                 |
| `feature_registry.json` | `.nexus/state/` | Implemented features registry (filesystem backend) |
| `audit.log`             | `logs/`         | Append-only event log                              |
| `workflow_state.json`   | `.nexus/state/` | Pause/resume/stop state per issue                  |

When `NEXUS_STORAGE_BACKEND=postgres`, UNI user tracking state (`user_tracking.json` payload)
is persisted in Postgres table `nexus_user_tracking_state` instead of local file writes.

## Rate Limits

| Scope      | Limit        | Window        |
|------------|--------------|---------------|
| Global     | 30 req/min   | 60s sliding   |
| Logs       | 5 req/min    | 60s sliding   |
| Stats      | 10 req/min   | 60s sliding   |
| Direct     | 3 req/min    | 60s sliding   |
| GitHub API | 100 req/hour | 3600s sliding |

## Service Endpoints

| Endpoint               | Method | Description                                                                                   |
|------------------------|--------|-----------------------------------------------------------------------------------------------|
| `/`                    | GET    | Visualizer access gateway/login page                                                          |
| `/health`              | GET    | Health check                                                                                  |
| `/status`              | GET    | Detailed system status                                                                        |
| `/metrics`             | GET    | Prometheus-compatible metrics                                                                 |
| `/webhook`             | POST   | GitHub webhook receiver                                                                       |
| `/api/v1/completion`   | POST   | Agent completion reporting                                                                    |
| `/visualizer/access`   | POST   | Exchanges shared visualizer token for HttpOnly cookie                                         |
| `/visualizer`          | GET    | Workflow visualizer UI (gated by `NEXUS_AUTH_ENABLED` and/or `NEXUS_VISUALIZER_SHARED_TOKEN`) |
| `/visualizer/snapshot` | GET    | Workflow state snapshot JSON (same visualizer access guard as `/visualizer`)                  |
