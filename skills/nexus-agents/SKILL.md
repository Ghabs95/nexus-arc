---
name: nexus-agents
description: >
  Invoke nexus/agents/ multi-agent composition pipelines from chat.
  Use when you need to run a Sequential, Parallel, Loop, or Coordinator
  pipeline of specialized agents against a task — for analysis, review,
  code generation, or any multi-step reasoning that benefits from
  multiple specialized agents working together.
---

# nexus-agents skill

## What it does

Sends a task to the Nexus bridge (`POST /api/v1/agents/run`) which runs the
requested agent composition using `nexus/agents/` primitives and returns the result.

## Usage

```
POST http://127.0.0.1:8091/api/v1/agents/run
Authorization: Bearer <NEXUS_BRIDGE_TOKEN>
Content-Type: application/json

{
  "task": "Review this PR for security issues and summarise findings",
  "agent_type": "sequential",
  "agents": [
    {"name": "SecurityReviewer", "description": "Reviews code for security vulnerabilities"},
    {"name": "Summariser", "description": "Summarises findings into a concise report"}
  ]
}
```

## agent_type options

| type | behaviour |
|---|---|
| `sequential` | agents run in order; each receives prior outputs |
| `parallel` | agents run concurrently; outputs merged |
| `loop` | single agent repeats until stop_condition or max_iterations |
| `coordinator` | LLM picks the best agent; nexus-router selects model |

## LoopAgent extra fields

```json
{
  "agent_type": "loop",
  "agents": [{"name": "Refiner", "description": "Refines output"}],
  "max_iterations": 5,
  "stop_condition": "'DONE' in content"
}
```

`stop_condition` is a Python expression evaluated with `output` (AgentOutput)
and `content` (output.content) in scope.

## Response

```json
{
  "ok": true,
  "output": "Final agent output text",
  "metadata": {
    "coordinator_selected_agent": "...",
    "sequential_outputs": [...],
    "loop_iterations": 3
  }
}
```

## How to invoke from Jarvis

When Gab asks to "run agents" or "coordinate agents" on a task, call the bridge:

```python
import json, urllib.request

payload = json.dumps({
    "task": "<task>",
    "agent_type": "sequential",
    "agents": [...]
}).encode()

req = urllib.request.Request(
    "http://127.0.0.1:8091/api/v1/agents/run",
    data=payload,
    headers={
        "Content-Type": "application/json",
        "Authorization": f"Bearer {BRIDGE_TOKEN}"
    },
    method="POST"
)
with urllib.request.urlopen(req) as resp:
    result = json.loads(resp.read())
```

Then return `result["output"]` to the user.
