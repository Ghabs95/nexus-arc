# Nexus ARC (Agentic Runtime Core)

**Production-grade framework for orchestrating AI agents in multi-step workflows**

[![License](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)

---

## What is Nexus ARC?

Nexus ARC (Agentic Runtime Core) is the **Git-native AI orchestration framework**. Unlike other frameworks that log agent actions to ephemeral files, Nexus creates permanent, traceable artifacts in your Git platform (GitHub, GitLab, Bitbucket).

### Why Git-Native?

Every agent action becomes part of your development history:
- 🎯 **Issues** track what was requested and decided
- 💬 **Comments** preserve agent reasoning and handoffs
- 🔀 **Pull Requests** contain actual code changes
- ✅ **Reviews** create approval gates with full context
- 📊 **Git History** provides permanent audit trail

**The result:** Complete traceability, searchability, and accountability for AI workflows.

### Production-Ready Features

- ✅ **Reliability**: Auto-retry, timeout detection, graceful failure handling
- ✅ **State Management**: Persistent workflow state with audit trails
- ✅ **AI Orchestration**: Route work to the best AI tool (Copilot, Gemini, soon Claude and Codex)
- ✅ **Fallback Support**: Automatic failover when tools are rate-limited or unavailable
- ✅ **Pluggable Architecture**: Bring your own storage, git platform, notification system

**Think of it as Temporal meets GitHub Actions for AI agents** — workflows that integrate seamlessly with your development process.

> 📖 **Documentation:**
> - [Usage Guide & Examples](docs/USAGE.md) - How to use nexus-arc in your project
> - [Plugin Architecture](docs/PLUGINS.md) - Build and load Telegram/GitHub/AI integrations as plugins
> - [Comparison with Google ADK, LangChain, and others](docs/COMPARISON.md)
> - [Positioning & Messaging](docs/POSITIONING.md)

---

## Quick Start

### Installation

```bash
# Install the core framework
pip install nexus-arc

# Install with Telegram, Discord bots, and database support
pip install "nexus-arc[nexus-bot]"
```

For setting up the fully-featured interactive bot, see the [nexus-bot example](examples/nexus-bot/README.md) or run the installer:
```bash
bash <(curl -s https://raw.githubusercontent.com/Ghabs95/nexus-arc/main/examples/nexus-bot/install.sh)
```

### Your First Workflow

```python
from nexus.core import WorkflowEngine, YamlWorkflowLoader
from nexus.adapters.storage.file import FileStorage

# Configure storage
storage = FileStorage(base_path="./data")

# Create workflow engine
engine = WorkflowEngine(storage=storage)

# Load workflow definition using the new YAML loader
workflow = YamlWorkflowLoader.load("./workflows/feature_dev.yaml")

# Create and execute
await engine.create_workflow(workflow)
result = await engine.start_workflow(workflow.id)
```

### Define a Workflow (YAML)

```yaml
name: "Feature Development"
version: "1.0"

steps:
  - name: triage
    agent_type: triage
    prompt: "Analyze this feature request and determine complexity"
    timeout: 300
    retry: 3
    
  - name: design
    agent_type: designer
    prompt: "Create technical design for this feature"
    condition: "triage.complexity == 'high'"
    timeout: 600
    retry: 2
    
  - name: implement
    agent: developer
    prompt: "Implement the feature according to spec"
    timeout: 1800
    retry: 3
```

---

## Features

### 🔄 Workflow State Machine

Track multi-step processes with automatic state persistence:

```python
# Workflows can be paused, resumed, or stopped
await engine.pause_workflow(workflow_id)
await engine.resume_workflow(workflow_id)

# Full audit trail
history = await engine.get_audit_log(workflow_id)
```

### 🤖 AI Provider Orchestration

Intelligent routing with automatic fallback:

```python
from nexus.adapters.ai import CopilotCLI, OpenAIProvider, GeminiCLI

orchestrator = AIOrchestrator(
    providers=[
        CopilotCLI(preference="code_generation"),
        AnthropicProvider(preference="reasoning", model="claude-3"), # Coming soon
        GeminiCLI(preference="fast_analysis"),
    ],
    fallback_enabled=True
)

# Automatically picks best provider & falls back if rate-limited
result = await orchestrator.execute(agent="Architect", context=ctx)
```

### 🔌 Pluggable Adapters

Swap any component via configuration:

**Storage Backends**:
- `FileStorage` - JSON files (great for dev)
- `PostgreSQLStorage` - Production database
- `RedisStorage` - Fast, ephemeral workflows
- `S3Storage` - Serverless deployments

**Git Platforms**:
- `GitHubPlatform` - Issues, PRs, comments
- `GitLabPlatform` - Issues, MRs, notes
- `BitbucketPlatform` - Issues, PRs

**Notification Channels**:
- `TelegramNotifier` - Push notifications to chats
- `SlackNotifier` - Thread-based updates
- `EmailNotifier` - Digest & alerts
- `WebhookNotifier` - Custom integrations

**Interactive Clients**:
- `TelegramInteractivePlugin` - Two-way chat bot polling apps
- `DiscordInteractivePlugin` - Two-way chat bot via HTTP app commands

### 🛡️ Production-Ready Error Handling

- Exponential backoff with jitter
- Configurable retry limits per step
- Timeout detection & auto-kill
- User-friendly error messages
- Dead letter queue for failed workflows

### 📊 Monitoring & Metrics

Built-in observability:

```python
from nexus.monitoring import PrometheusExporter

# Export metrics
exporter = PrometheusExporter(port=9090)
engine.add_observer(exporter)

# Track:
# - Workflow success/failure rates
# - Step execution time
# - AI provider latency & costs
# - Retry & timeout counts
```

---

## Architecture

```
┌─────────────────────────────────────────────────────────┐
│                  Input Adapters                         │
│  (Telegram, Slack, Webhook, CLI, GitHub Issues)         │
└────────────────────┬────────────────────────────────────┘
                     │
                     ▼
┌──────────────────────────────────────────────────────┐
│                Workflow Engine (ARC)                 │
│  ┌────────────────────────────────────────────────┐  │
│  │  Step Manager → State Machine → Audit Logger   │  │
│  └────────────────────────────────────────────────┘  │
└────────────┬──────────────────────┬──────────────────┘
             │                      │
    ┌────────▼────────┐    ┌───────▼────────┐
    │ AI Orchestrator │    │ Storage Backend│
    │  - Provider     │    │  - State       │
    │    Selection    │    │  - Audit Log   │
    │  - Retry Logic  │    │  - Metrics     │
    │  - Fallback     │    └────────────────┘
    └────────┬────────┘
             │
    ┌────────▼─────────────────────┐
    │     AI Providers             │
    │  (Copilot, Gemini, soon      │
    │   Claude & Codex)            │
    └──────────────────────────────┘
             │
    ┌────────▼────────┐
    │ Output Adapters │
    │  (Git Platform, │
    │   Notifications)│
    └─────────────────┘
```

---

## Use Cases

### Feature Development Workflow
Multi-agent workflow that creates traceable artifacts at each step:
```yaml
workflow: feature_development
trigger: issue_created (label: feature)
steps:
  - triage: Triage & scope
    → Adds comment with complexity analysis
  - designer: Technical design
    → Creates design doc, updates issue
  - code_reviewer: Implementation review
    → Reviews PR, comments with feedback
  - debug: Root cause analysis (if needed)
    → Investigates issues, suggests fixes
  - docs: Documentation update
    → Updates docs based on changes
```
**All agent decisions preserved in GitHub for future reference.**

### Code Review Automation
AI-powered review with full traceability:
```yaml
workflow: automated_review
trigger: pull_request_opened
steps:
  - SecurityAgent: Scan for vulnerabilities
    → Posts review comments on specific lines
  - PerformanceAgent: Check efficiency
    → Suggests optimizations in PR thread
  - StyleAgent: Enforce standards
    → Requests changes with explanations
```
**Every suggestion is a PR comment, not lost in logs.**

### Bug Fix Pipeline
End-to-end bug resolution with audit trail:
```yaml
workflow: bug_fix
trigger: issue_created (label: bug)
steps:
  - TriageAgent: Assess severity
    → Labels issue, sets priority
  - DiagnosticAgent: Root cause analysis
    → Comments with findings
  - FixAgent: Implement solution
    → Creates PR with fix
  - VerificationAgent: Test fix
    → Validates and approves PR
```
**Complete history from bug report to fix, all in GitHub.**

---

## Examples

See [examples/](./examples) directory:

- **basic_workflow.py** - Simple 3-step workflow
- **github_ci.py** - Automated code review on PRs
- **support_router.py** - Support ticket classification & routing
- **doc_generator.py** - Auto-generate docs from code

---

## Configuration

### YAML Config (`nexus.yaml`)

```yaml
version: "1.0"

adapters:
  storage:
    type: postgres # options: file, postgres
    storage_config:
      connection_string: ${DATABASE_URL}
      # storage_dir: ./data # required for type: file
  
  git:
    type: github
    repo: yourorg/yourrepo
    token: ${GITHUB_TOKEN}
```

### Environment Variables

```bash
# Required
DATABASE_URL=postgresql://user:pass@localhost/nexus
GITHUB_TOKEN=ghp_your_token

# Storage Configuration (Alternative to YAML)
NEXUS_STORAGE_BACKEND=postgres
NEXUS_STORAGE_DSN=postgresql://user:pass@localhost/nexus
NEXUS_STORAGE_DIR=./data

# Optional
NEXUS_LOG_LEVEL=INFO
NEXUS_HEALTH_CHECK_PORT=8080
NEXUS_METRICS_PORT=9090
```

---

## Development

### Setup

```bash
git clone https://github.com/Ghabs95/nexus-arc
cd nexus-arc
python -m venv venv
source venv/bin/activate
pip install -e ".[dev]"
```

### Run Tests

```bash
pytest                    # Run all tests
pytest --cov             # With coverage
pytest -k test_workflow  # Specific test
```

### Code Quality

```bash
black .         # Format
ruff check .    # Lint
mypy nexus/     # Type check
```

---

## Roadmap

### v0.1 (Current)
- [x] Core workflow engine
- [x] File, Postgres, Redis storage
- [x] GitHub, GitLab git platforms
- [x] Copilot CLI, Gemini CLI, OpenAI providers
- [x] Telegram, Slack notifiers

### v0.2 (Next)
- [ ] GraphQL API for workflow management
- [ ] Web dashboard for monitoring
- [ ] Workflow versioning & rollback
- [ ] Distributed execution (Celery/RQ)
- [ ] Workflow marketplace

### v1.0
- [ ] SLA guarantees
- [ ] Multi-tenancy
- [ ] RBAC & audit compliance
- [ ] Cloud-hosted offering

---

## Contributing

We welcome contributions! See [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines.

**Good first issues**:
- Implement new adapters (Notion, Jira, Slack)
- Add example workflows
- Improve documentation
- Write integration tests

---

## License

Apache License 2.0 - see [LICENSE](LICENSE) for details.

---

## Support

- **Documentation**: https://nexus-arc.readthedocs.io *(Coming Soon! For now, see the `docs/` directory)*
- **Comparison Guide**: [vs Google ADK, LangChain, CrewAI](docs/COMPARISON.md)
- **Issues**: https://github.com/Ghabs95/nexus-arc/issues
- **Discord**: https://discord.gg/nexus-arc
- **Email**: support@nexus-arc.dev

---

## Acknowledgments

Built with inspiration from:
- **Temporal** - Workflow orchestration patterns
- **Langchain** - AI tool composition
- **Prefect** - Developer experience
- **Original Nexus** - Real-world production validation

---

**Star ⭐ this repo if you find it useful!**
