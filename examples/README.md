# Nexus ARC Examples

This folder demonstrates how to use Nexus ARC with real examples.

## Structure

```
examples/
├── nexus-bot/              # Full Telegram bot runtime example (migrated)
│   ├── src/                   # Bot + processor + webhook + handlers
│   ├── config/                # Example project config
│   └── README.md              # Setup/run instructions
├── workflows/                  # Workflow orchestration (YAML)
│   └── development_workflow.yaml  # Example: Multi-agent development workflow
├── agents/                     # Agent definitions (YAML)
│   ├── triage-agent.yaml       # Example: Issue triage agent
│   └── designer-agent.yaml     # Example: Feature designer agent
└── translator/                 # Agent YAML → Other formats (utilities)
    ├── to_markdown.py          # Generate markdown documentation
    ├── to_python.py            # Generate Python class template
    └── to_copilot.py           # Generate VS Code Copilot instructions
```

---

## Agent Types & Use Cases

### Nexus ARC Task Agents (This Framework)

**What:** Workflow agents that execute specific tasks in orchestrated sequences.

**Examples:**

- Triage Agent: Classify GitHub issues
- Design Agent: Create design proposals
- Code Review Agent: Review pull requests
- Docs Agent: Update documentation

**Deployment:** Run inside workflows, get orchestrated by WorkflowEngine, all decisions tracked in Git.

**When to use:** Build automation tasks that need observability, routing, and git-native audit trails.

---

### GitHub Copilot Org Agents (Different Paradigm)

**What:** Persistent personas that guide humans in VS Code Chat (not automation).

**Examples:**

- @CEO: Strategic oversight
- @CTO: Architecture decisions
- @BackendLead: Backend expertise
- @QAGuard: Quality gates

**Deployment:** `.agent/` format in VS Code workspace, invoked by human with `@AgentName`.

**When to use:** Embed team knowledge/governance into AI Chat assistants for developers.

**Reference:** See [github.com/Ghabs95/agents](https://github.com/Ghabs95/agents) for organizational agent patterns.

---

## Quick Start

### 0. Telegram Bot Runtime Example

```bash
cd examples/nexus-bot
cat README.md
```

This is the migrated production-style Telegram runtime from the `nexus` repo.

### 1. Explore Agent Definitions

Agent YAML files show the complete schema:

```bash
cat examples/agents/triage-agent.yaml    # Issue classification agent
cat examples/agents/design-agent.yaml    # Feature design proposal agent
cat examples/workflows/development_workflow.yaml  # Multi-agent workflow
```

### 2. Generate Agent Code / Docs

Use translator tools to generate documentation or Python scaffolding:

```bash
# Generate markdown documentation
python examples/translator/to_markdown.py examples/agents/triage-agent.yaml

# Generate Python class template to implement
python examples/translator/to_python.py examples/agents/triage-agent.yaml

# Generate Copilot Chat instructions
python examples/translator/to_copilot.py examples/agents/triage-agent.yaml
```

---

## Agent Framework

Agents are autonomous tools that handle specific tasks in your workflow. Nexus ARC provides a standard way to define
and orchestrate them.

### Agent Anatomy

Every agent is defined in YAML with:

- **Input schema** — what the agent needs
- **Output schema** — what the agent produces
- **Tools** — services the agent calls (GitHub, LLM, etc.)
- **AI instructions** — prompt for the LLM
- **Routing rules** — what happens after

### Example: Triage Agent

```yaml
# examples/agents/triage-agent.yaml
apiVersion: "nexus-arc/v1"
kind: "Agent"
metadata:
  name: "triage"
  description: "Analyzes GitHub issues and classifies them"

spec:
  inputs:
    issue_url:
      type: string
      description: "GitHub issue URL"

  outputs:
    classification:
      type: enum
      values: [ "bug", "feature", "doc", "support" ]
    priority:
      type: enum
      values: [ "p0-critical", "p1-high", "p2-medium", "p3-low" ]

  # Agents do not declare tools individually anymore.
  # Tools are sandboxed by the Workflow at the pipeline level.
  # They receive default generic vcs capabilities dynamically.

  ai_instructions: |
    Analyze this issue and classify it...
```

---

## Using the Translator Tools

These are example utilities to convert agent YAML to other formats. Customize them for your needs.

### Generate Markdown Documentation

```bash
python examples/translator/to_markdown.py examples/agents/triage-agent.yaml
```

Output: Formatted markdown documentation of the agent, ready to share.

### Generate Python Class Template

```bash
python examples/translator/to_python.py examples/agents/triage-agent.yaml
```

Output: Python class scaffold you fill in with actual implementation.

```python
class TriageAgent:
    async def run(self, inputs: TriageAgentInput) -> TriageAgentOutput:
        # TODO: Implement logic here
        pass
```

### Generate Copilot Instructions

```bash
python examples/translator/to_copilot.py examples/agents/triage-agent.yaml
```

Output: Instructions for VS Code Copilot Chat to help code the agent.

You can paste these instructions into Copilot and ask:
> "Implement this agent for me"

---

## Workflow Orchestration

Workflows coordinate multiple agents. See `development_workflow.yaml` for an example:

```yaml
# examples/workflows/development_workflow.yaml
name: "Nexus ARC Development Workflow"

steps:
  - id: "triage"
    agent_type: "triage"
    tools: [ vcs:read_issue, vcs:add_comment, ... ]
    on_success: "route_by_type"

  - id: "route_by_type"
    agent_type: "router"
    routes:
      - when: "classification == 'feature'"
        then: "design"
      - when: "classification == 'bug'"
        then: "debug_analysis"

  - id: "design"
    agent_type: "design"
    on_success: "close_loop"

  - id: "close_loop"
    agent_type: "summarizer"
    final_step: true
```

This workflow:

1. **Triage** incoming issue
2. **Route** to appropriate specialist (design for features, debug for bugs)
3. **Execute** specialist agent
4. **Close loop** with summary

---

## Building Your Own Agents

### Step 1: Define Agent YAML

Create `examples/agents/my-agent.yaml`:

```yaml
apiVersion: "nexus-arc/v1"
kind: "Agent"
metadata:
  name: "my-agent"
  description: "What this agent does"

spec:
  inputs:
    param1:
      type: string
      required: true

  outputs:
    result:
      type: string

  requires_tools:
    - tool1
    - tool2

  ai_instructions: |
    Instructions for the LLM...
```

### Step 2: Generate Python Template

```bash
python examples/translator/to_python.py examples/agents/my-agent.yaml > my_agent.py
```

### Step 3: Implement

Fill in the `run()` method in your generated class.

### Step 4: Register in Workflow

Add your agent to a workflow YAML:

```yaml
- id: "my-step"
  agent_type: "my-agent"
  inputs:
    param1: "value"
  on_success: "next_step"
```

### Step 5: Run

```python
engine = WorkflowEngine(...)
result = await engine.run(workflow=..., inputs={...})
```

---

## Real-World Example: Nexus ARC Development

This repo uses agents for its own development!

The workflow in `development_workflow.yaml`:

1. **Triage**: New issues automatically analyzed
2. **Design**: Feature requests get design docs
3. **Code Review**: PRs reviewed automatically
4. **Docs**: Changes trigger doc updates

All progress is tracked in GitHub as comments and PRs — complete audit trail.

---

## Tips

- **Start small** — build a simple agent first
- **Test mode** — create a test branch before running on live repos
- **Error handling** — agents should log decisions for audit
- **Reuse tools** — other agents can call the same tools
- **Iterate** — update agent YAML and regenerate as you learn

---

## Next Steps

1. Read [docs/USAGE.md](../docs/USAGE.md) for framework concepts
2. Check [docs/POSITIONING.md](../docs/POSITIONING.md) for competitive context
3. Look at [basic_workflow.py](basic_workflow.py) for code patterns
4. Create your first agent using the translator tools

---

## Questions?

See the main [README.md](../README.md) for documentation and links.
