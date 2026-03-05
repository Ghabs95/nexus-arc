# Copilot Instructions - Nexus Core

## Feature Placement Policy

You are responsible for placing code in the correct layer.

1. Classify every request before editing:
   - `FRAMEWORK`: reusable capabilities, core abstractions, runtime behavior, APIs, tool/memory/orchestration/transport logic.
   - `EXAMPLE`: demo wiring, sample UX text, tutorial-only glue in `examples/nexus-bot`.

2. Default to `FRAMEWORK` unless the user explicitly asks for example work using terms like:
   - "example", "demo", "sample", "nexus-bot".

3. Mandatory pre-edit output (first line of your response):
   - `Placement: FRAMEWORK` or `Placement: EXAMPLE`
   - `Reason: <one sentence>`

4. If placement is ambiguous, ask one direct question before coding:
   - "Should I implement this in Nexus ARC framework, or only in examples/nexus-bot?"

5. Never place reusable logic in `examples/nexus-bot`.

6. For every `FRAMEWORK` feature, also implement usage in `examples/nexus-bot` to validate and demonstrate the feature:
   - Implement framework capability first.
   - Then wire it in `examples/nexus-bot` as a consumer.
   - Keep reusable logic in framework files; example files should only consume framework APIs.

7. If modifying `examples/nexus-bot`, include this explicit line:
   - `Example change justification: "<quoted user request fragment>"`
   - If no quote exists and the change is not required by rule 6, do not modify example files.

## Framework Architecture

Nexus Core is an **agent-agnostic workflow orchestration framework**. It provides primitives for defining multi-agent workflows without prescribing specific agent implementations.

### Agent Type System

**CRITICAL:** The framework uses **agent_type** (string), NOT hardcoded agent names or @mentions.

```yaml
# ✅ CORRECT - Framework pattern
steps:
  - id: analyze
    agent_type: "triage"  # Abstract type, not a specific agent name
    prompt_template: "Analyze {issue_url}"
    
# ❌ WRONG - Coupled to specific implementation
steps:
  - id: analyze
    agent: "@ProjectLead"  # Hard-coded agent name
    prompt_template: "Analyze {issue_url}"
```

### Examples vs Framework

- **Framework code** (`nexus/core/*.py`): Agent-agnostic, works with any agent_type string
- **Example implementations** (`examples/agents/*.yaml`): Sample agents demonstrating the framework

When working on **framework code**: Do not reference specific agent types
When working on **example workflows/agents**: Use agent_type strings defined in `examples/agents/` folder

### Example Agents (Demonstration Only)

The `examples/` folder contains sample agent implementations to demonstrate the framework.
See `examples/agents/*.yaml` for:
- Agent YAML schema structure
- Input/output contract patterns
- Tool integration examples
- Prompt template patterns

**Note:** These are examples only. Real deployments define their own agent types based on their needs.

### Documentation Context

README.md and ARCHITECTURE.md may contain:
- "Before/After" comparisons showing migration from hardcoded names to agent_type pattern
- Historical examples with old agent names (ProjectLead, @Atlas, etc.) - these are for illustration only

When referencing agents in code or workflows, always use the `agent_type` field with string values.

## Coding Conventions

- PEP 8 style, 100-char lines, double quotes
- Type hints on all function signatures
- Small, readable functions - avoid clever abstractions
- Minimal comments - code should be self-documenting
- Async/await patterns throughout

## Testing

- Write tests for all new features
- Use pytest with async support
- Mock external dependencies (GitHub API, etc.)
- Tests live in `tests/` directory

## Agent Implementation

When implementing agents:
1. Check `examples/agents/` for the agent YAML definition
2. Follow the input/output schema exactly
3. Use the tools specified in the workflow step definition
4. Return structured output matching the schema
5. Add error handling and retries as specified

## Workflow Context

- Workflows defined in YAML (`examples/workflows/*.yaml`)
- Steps use `agent_type` field to route to correct agent
- Conditions support Python expressions for control flow
- All workflows are asynchronous and stateful
