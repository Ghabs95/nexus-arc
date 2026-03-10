# Design: WebSocket Agent State Streaming

**Issue:** #117
**ADR:** [ADR-083](ADR-083-Live-Visualizer-Updates.md)
**Branch:** `feat/websocket-agent-state-streaming`
**Status:** Implemented

---

## Overview

This document describes the WebSocket-based streaming mechanism that pushes agent state changes
directly to the Live Visualizer, replacing the previous poll-on-refresh approach.

---

## Architecture

```
WorkflowEngine  →  EventBus  →  SocketIO Bridge  →  Flask-SocketIO  →  Browser
  (step events)    (pub/sub)    (async handler)       (/visualizer)    (Cytoscape/Mermaid)
```

### Components

| Component              | File                                             | Responsibility                                          |
|------------------------|--------------------------------------------------|---------------------------------------------------------|
| `WorkflowEngine`       | `nexus/core/workflow.py`                         | Emits typed `NexusEvent` objects (step.*, workflow.*)   |
| `EventBus`             | `nexus/core/events.py`                           | Routes events to subscribed async handlers              |
| SocketIO bridge        | `nexus/core/orchestration/nexus_core_helpers.py` | Subscribes to EventBus, maps events → SocketIO payloads |
| `HostStateManager`     | `nexus/core/state_manager.py`                    | Thread-safe SocketIO emit helpers                       |
| `MermaidRenderService` | `nexus/core/mermaid_render_service.py`           | Generates Mermaid diagram syntax from live step states  |
| Flask-SocketIO server  | `examples/nexus-bot/src/webhook_server.py`       | Hosts `/visualizer` namespace, registers emitter        |
| Visualizer frontend    | `examples/nexus-bot/src/static/visualizer.html`  | Cytoscape.js graph + Mermaid tab, listens for events    |

---

## EventBus → SocketIO Wiring

### Engine factory injection

`WorkflowStateEnginePlugin._get_engine()` now accepts an optional `event_bus` key in its
config dict. `_WORKFLOW_STATE_PLUGIN_BASE_KWARGS` passes `get_event_bus` (lazy callable) so
every engine instance created during `complete_step_for_issue()` fires events to the shared bus.

```python
# nexus/plugins/builtin/workflow_state_engine_plugin.py
def _get_engine(self) -> WorkflowEngine:
    if callable(self.engine_factory):
        return self.engine_factory()
    event_bus = self.config.get("event_bus")
    if callable(event_bus):
        event_bus = event_bus()
    return WorkflowEngine(storage=self._build_storage(), event_bus=event_bus)
```

### Bridge subscription

`_setup_socketio_event_bridge(bus)` subscribes to `step.*` and `workflow.*` patterns on the
shared EventBus. It translates each event to the ADR-083 SocketIO payload and emits via
`HostStateManager.emit_transition()`.

```python
bus.subscribe_pattern("step.*", handle_event)
bus.subscribe_pattern("workflow.*", handle_event)
```

---

## WebSocket API Contract (Namespace: `/visualizer`)

### `step_status_changed`

Emitted for every step transition: `step.started`, `step.completed`, `step.failed`, `step.skipped`.

```json
{
  "issue": "117",
  "workflow_id": "nexus-117-full",
  "step_id": "develop",
  "agent_type": "developer",
  "status": "running",
  "timestamp": 1740494242.0
}
```

**Status mapping:**

| EventBus event   | `status` value |
|------------------|----------------|
| `step.started`   | `running`      |
| `step.completed` | `done`         |
| `step.failed`    | `failed`       |
| `step.skipped`   | `skipped`      |

### `workflow_completed`

Emitted once when the workflow transitions to `completed` or `failed`.

```json
{
  "issue": "117",
  "workflow_id": "nexus-117-full",
  "status": "success",
  "summary": "Workflow success: 11/11 steps completed",
  "total_steps": 11,
  "completed_steps": 11,
  "failed_steps": 0,
  "skipped_steps": 0,
  "timestamp": 1740494500.0
}
```

### `mermaid_diagram`

Emitted after each non-skipped step event with updated diagram syntax. The frontend renders
it using `mermaid.js` (loaded via CDN) in the Mermaid tab.

```json
{
  "issue": "117",
  "workflow_id": "nexus-117-full",
  "diagram": "flowchart TD\n  I[\"Issue #117\"]\n  I --> S1([\"1/11\\ntriage\\ntriage\\n✅ completed\"])\n  ...",
  "timestamp": 1740494242.0
}
```

---

## Frontend Behaviour

The visualizer (`visualizer.html`) uses two views:

1. **Cytoscape.js graph** (default tab): Nodes are added/styled in real-time via `step_status_changed`.
2. **Mermaid tab**: Re-renders the complete diagram SVG on each `mermaid_diagram` event.

Both fall back to the `/visualizer/snapshot` REST endpoint for initial page load.

---

## No New Dependencies

All components use existing dependencies:

- `flask-socketio >= 5.3` (already in `pyproject.toml`)
- `eventlet >= 0.35` (already in `pyproject.toml`)
- No additional Python packages required.
