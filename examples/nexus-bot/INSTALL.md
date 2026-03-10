# Nexus Chat Bot — Installation Guide

This guide covers two deployment scenarios for the Nexus Telegram/Discord bot runtimes:

| Scenario       | External Deps      | Best For                                           |
|----------------|--------------------|----------------------------------------------------|
| **Lite**       | None               | Solo developer, quick start, local experimentation |
| **Enterprise** | PostgreSQL + Redis | Production, multi-project, persistent chat memory  |

---

## Prerequisites (Both Scenarios)

- Python 3.11+
- A Telegram Bot Token (from [@BotFather](https://t.me/BotFather))
- Your Telegram User ID (from [@userinfobot](https://t.me/userinfobot))
- A GitHub **or** GitLab Personal Access Token (for issue integration)
- A Discord Bot Token (optional, if you want to use the Discord UI)
- At least one AI provider CLI installed (`copilot`, `gemini`, `codex`, or `claude`)

> **Note:** Nexus supports both **GitHub** and **GitLab** as first-class VCS platforms.
> Each project can independently target either platform via `git_platform` in `project_config.yaml`.

---

## 1. Clone & Install

```bash
# Clone nexus-arc
git clone https://github.com/Ghabs95/nexus-arc.git
cd nexus-arc

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install nexus-arc with all bot dependencies
pip install -e ".[nexus-bot]"

# Install the Nexus bot console scripts
cd examples/nexus-bot
pip install -e .
```

After installation, six commands become available:

| Command              | Service                                |
|----------------------|----------------------------------------|
| `nexus-telegram-bot` | Telegram interactive bot runtime       |
| `nexus-discord-bot`  | Discord interactive bot runtime        |
| `nexus-processor`    | Inbox processor (agent execution loop) |
| `nexus-webhook`      | GitHub/GitLab webhook receiver         |
| `nexus-health`       | Health-check HTTP endpoint             |

---

## 2. Configure (Interactive Setup)

The easiest way to configure the bot, generate the `.env` file, and install external CLIs (like Copilot, Gemini, Ollama,
etc.) is to use our interactive setup wizard:

```bash
# If you cloned the repository (from step 1):
./examples/nexus-bot/install.sh
```

> **Prefer a quick curl install instead of cloning the whole repo?**
> You can download and run the setup script directly using bash:
> ```bash
> curl -fsSL https://raw.githubusercontent.com/Ghabs95/nexus-arc/main/examples/nexus-bot/install.sh | bash
> ```
> *(A Python version `install.py` is also available if preferred).*

The wizard will ask you for your Storage preferences (Lite vs Enterprise), ask to setup Postgres/Redis for you, and
securely configure your Tokens.

Alternatively, you can configure it manually by copying the `.env`:

```bash
cd examples/nexus-bot
cp .env.example .env
```

Edit `.env` with your credentials. The sections below show the key variables for each scenario.

---

## Scenario A — Lite (Filesystem, No External Deps)

Everything runs on the local filesystem. No database, no Redis.

### What Works

| Feature                                                  | Status |
|----------------------------------------------------------|--------|
| Telegram and Discord interactive commands                | ✅      |
| Workflow orchestration & auto-chaining                   | ✅      |
| GitHub issue integration (issues, PRs, comments, labels) | ✅      |
| GitLab issue integration (issues, MRs, comments, labels) | ✅      |
| Agent execution (Copilot, Gemini, Codex, Claude)         | ✅      |
| Inbox task queue (file-based)                            | ✅      |
| Workflow state persistence (file-based)                  | ✅      |
| Webhook processing                                       | ✅      |

### What's Limited

| Feature                            | Status   | Why                        |
|------------------------------------|----------|----------------------------|
| Chat memory (conversation context) | ❌        | Requires Redis             |
| Multi-platform state sync          | ❌        | Requires Redis             |
| Inbox deduplication                | ⚠️ Basic | Full dedupe needs Postgres |
| High-availability / multi-instance | ❌        | File locks are single-node |

### `.env` (Key Variables)

```bash
# === Required ===
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_ALLOWED_USER_IDS=your_user_id

# Git platform tokens (set one or both depending on your projects)
GITHUB_TOKEN=ghp_your_token
# GITLAB_TOKEN=glpat-your_token
# GITLAB_BASE_URL=https://gitlab.com   # or your self-hosted instance

# === Storage: Filesystem ===
NEXUS_STORAGE_BACKEND=filesystem
BASE_DIR=/path/to/your/repos
NEXUS_RUNTIME_DIR=/var/lib/nexus        # or any writable directory
NEXUS_CORE_STORAGE_DIR=/var/lib/nexus/nexus-arc

# === Redis: Disabled (leave empty or comment out) ===
# REDIS_URL=

# === Project config ===
PROJECT_CONFIG_PATH=./config/project_config.yaml
```

### Run

```bash
# Start bot runtimes (foreground)
nexus-telegram-bot
nexus-discord-bot

# In separate terminals:
nexus-processor
nexus-webhook      # only if you need GitHub/GitLab webhooks
nexus-health       # optional health endpoint
```

---

## Scenario B — Enterprise (PostgreSQL + Redis)

Full-featured deployment with persistent queue, chat memory, and deduplication.

### What's Added Over Lite

| Feature                                              | Status |
|------------------------------------------------------|--------|
| Chat memory (conversation context across restarts)   | ✅      |
| Multi-platform state sync (e.g., Telegram ↔ Discord) | ✅      |
| Inbox deduplication (replay protection)              | ✅      |
| Concurrent inbox processing                          | ✅      |
| Production-grade persistence                         | ✅      |

### Infrastructure Setup

```bash
# PostgreSQL
sudo apt install postgresql
sudo -u postgres createuser nexus --pwprompt
sudo -u postgres createdb nexus --owner=nexus

# Redis
sudo apt install redis-server
sudo systemctl enable redis-server
```

### `.env` (Key Variables)

```bash
# === Required ===
TELEGRAM_TOKEN=your_bot_token
TELEGRAM_ALLOWED_USER_IDS=your_user_id

# Git platform tokens (set one or both depending on your projects)
GITHUB_TOKEN=ghp_your_token
# GITLAB_TOKEN=glpat-your_token
# GITLAB_BASE_URL=https://gitlab.com

# === Storage: PostgreSQL ===
NEXUS_STORAGE_BACKEND=postgres
NEXUS_HOST_STATE_BACKEND=postgres
NEXUS_STORAGE_DSN=postgresql://nexus:your_password@127.0.0.1:5432/nexus
BASE_DIR=/home/ubuntu/git
NEXUS_RUNTIME_DIR=/var/lib/nexus

# === Redis: Chat memory ===
REDIS_URL=redis://localhost:6379/0

# === Project config ===
PROJECT_CONFIG_PATH=/opt/nexus/config/project_config.yaml
```

### Run with Docker Compose

```bash
cd examples/nexus-bot
docker compose up -d
```

This starts five services (`telegram`, `discord`, `processor`, `webhook`, `health`) as containers.

### Run with systemd (Production)

```bash
# Install systemd unit files
cd examples/nexus-bot
sudo cp nexus-*.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable nexus-telegram nexus-discord nexus-processor nexus-webhook nexus-health
sudo systemctl start nexus-telegram nexus-discord nexus-processor nexus-webhook nexus-health
```

---

## 3. Project Configuration

Both scenarios require a `project_config.yaml` that defines your projects, workflows, and AI preferences.

A starter config is included at `config/project_config.yaml`. Key sections:

```yaml
# Workflow definition (path relative to BASE_DIR)
workflow_definition_path: nexus-arc/examples/workflows/enterprise_workflow.yaml

# Shared agent definitions
shared_agents_dir: nexus-arc/examples/agents

# GitHub project (default platform)
my_app:
  workspace: my-app
  git_repo: YourOrg/my-app
  agents_dir: path/to/agents

# GitLab project (set git_platform explicitly)
my_service:
  workspace: my-service
  git_platform: gitlab                   # ← switches to GitLab adapter
  git_repo: YourGroup/my-service
  # gitlab_base_url: https://gitlab.example.com  # for self-hosted instances
  agents_dir: path/to/agents
```

> **Tip:** Each project can use a different git platform. You can mix GitHub and GitLab projects in the same config.

See the [full example config](config/project_config.yaml) for all available options.

---

## Storage Backend Summary

```
┌─────────────────────┬──────────────┬──────────────┐
│ Domain              │ Filesystem   │ Enterprise   │
├─────────────────────┼──────────────┼──────────────┤
│ Workflow state      │ JSON files   │ PostgreSQL   │
│ Inbox queue         │ .md files    │ PostgreSQL   │
│ Chat memory         │ —            │ Redis        │
│ Tracked issues      │ JSON file    │ PostgreSQL   │
│ Merge Queue         │ JSON file    │ PostgreSQL   │
│ Audit log           │ JSONL file   │ JSONL file   │
│ Agent logs          │ Log files    │ Log files    │
└─────────────────────┴──────────────┴──────────────┘
```

---

## Verification

After starting the bot, send `/start` in your Telegram chat. You should see the welcome keyboard. Then try:

```
/help          → list all commands
/status        → show pending inbox tasks
/menu          → interactive command menu
/plan          → request a technical implementation plan
```

If using the health check, visit `http://localhost:8080/health` to confirm all services are running.
