# Agent YAML definitions for Nexus Core

These are example agent definitions in the Nexus Core agent format.
Use the translator tools to convert to markdown, Python, or Copilot instructions.

## Available Agents

- **triage-agent.yaml** (`triage`) - Analyzes issues, classifies type (bug/feature/doc) and priority (P0-P3)
- **design-agent.yaml** (`design`) - Creates technical design proposals for feature requests
- **debug-agent.yaml** (`debug`) - Analyzes bug reports and suggests root causes
- **developer-agent.yaml** (`developer`) - Implements code changes based on debug analysis or design proposals
- **reviewer-agent.yaml** (`reviewer`) - Reviews pull requests for code quality and best practices
- **compliance-agent.yaml** (`compliance`) - Reviews PRs for security, privacy, and regulatory compliance
- **deployer-agent.yaml** (`deployer`) - Merges approved PRs, creates releases, and manages deployments
- **docs-agent.yaml** (`writer`) - Updates and maintains project documentation
- **summarizer-agent.yaml** (`finalizer`) - Provides final summaries and closes workflow loops

## Usage

These agents are referenced by `agent_type` in workflow YAML files (see `../workflows/development_workflow.yaml`).

Configure which AI tool (copilot/gemini) each agent uses in `project_config.yaml`:

```yaml
ai_tool_preferences:
  triage: copilot          # Issue classification
  design: copilot          # Design proposals
  debug: copilot           # Root cause analysis
  code_reviewer: copilot   # PR reviews
  docs: gemini             # Documentation (faster)
  summarizer: gemini       # Summaries (faster)
```

## Translators

To generate Python implementation template:

```bash
python ../translator/to_python.py triage-agent.yaml > triage_agent.py
```

To generate markdown documentation:

```bash
python ../translator/to_markdown.py triage-agent.yaml > TRIAGE-AGENT.md
```

## Completion Summary Format

When an agent completes its work, it should write a `completion_summary.json` file in the task logs directory. This file
provides structured information about the work completed and recommendations for the next step.

**File location:** `.nexus/tasks/{project}/completions/completion_summary_{issue_number}.json`

**Schema:**

```json
{
  "issue_number": "42",
  "agent_type": "developer",
  "step_id": "implement_auth",
  "step_num": 2,
  "status": "complete",
  "summary": "Brief description of work completed",
  "key_findings": [
    "Finding 1",
    "Finding 2",
    "Finding 3"
  ],
  "effort_breakdown": {
    "analysis": "2 hours",
    "implementation": "4 hours",
    "testing": "1 hour"
  },
  "verdict": "✅ Ready to proceed",
  "next_agent": "architect"
}
```

**Field Descriptions:**

- `status`: Agent completion status (complete, in-progress, blocked)
- `issue_number`: Git issue number being processed
- `agent_type`: Current workflow agent type
- `step_id`: Current workflow step id (must match runtime context)
- `step_num`: Current workflow step number (must match runtime context)
- `summary`: One-line summary of what was accomplished
- `key_findings`: List of important discoveries or results (optional)
- `effort_breakdown`: Time/effort spent on major tasks (optional)
- `verdict`: Assessment of work quality (optional)
- `next_agent`: Agent type for next step in workflow (e.g., "architect", "code_reviewer")

**Python Example:**

```python
import json
import os

# At the end of agent work
completion_data = {
    "issue_number": str(issue_id),
    "agent_type": "developer",
    "step_id": "implement_auth",
    "step_num": 2,
    "status": "complete",
    "summary": "Conditional step execution feature fully implemented and tested",
    "key_findings": [
        "All 14 tests pass",
        "Implementation handles context evaluation correctly",
        "No breaking changes to existing APIs"
    ],
    "effort_breakdown": {
        "code_implementation": "3 hours",
        "testing": "1 hour"
    },
    "verdict": "✅ Implementation complete and correct",
    "next_agent": "code_reviewer"
}

# Write to completions directory
project_name = "nexus"
completion_dir = os.path.expandvars(f"$HOME/.nexus/tasks/{project_name}/completions")
os.makedirs(completion_dir, exist_ok=True)
with open(os.path.join(completion_dir, f"completion_summary_{issue_id}.json"), "w") as f:
    json.dump(completion_data, f, indent=2)
```

The Nexus processor will automatically detect this file, parse it, and post a formatted GitHub comment with the
structured information.
