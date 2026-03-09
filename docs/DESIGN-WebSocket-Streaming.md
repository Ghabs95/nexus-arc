# Technical Design: WebSocket Agent State Streaming

## Overview

This document outlines the technical design for a WebSocket-based streaming mechanism to push real-time agent state changes directly to the Live Visualizer, satisfying the requirements specified in **ADR-083**. This implementation replaces existing polling mechanisms with instantaneous event-driven updates.

## Objectives

- Implement low-latency, real-time updates for workflow step transitions.
- Eliminate polling overhead on both the Nexus webhook server and the client-side visualizer.
- Provide a hybrid visualization approach incorporating Cytoscape.js for status styling and Mermaid.js for syntax-driven live rendering.

## Architecture & Integration Strategy

The Nexus backend already incorporates `flask-socketio` and `eventlet` for basic events like `agent_registered` and `workflow_mapped`. This design expands the WebSocket infrastructure across the `/visualizer` namespace.

### Backend Components

1. **Event Hooks / State Manager Integration**:
   - The core orchestration engine must emit state change events.
   - When a step status is updated (e.g., from `pending` to `running`, or `running` to `done`/`failed`), the state manager will trigger an asynchronous WebSocket emission.
   - When the overall workflow concludes (success or failure), a terminal event is emitted.

2. **WebSocket Emissions (`flask-socketio`)**:
   - Namespace: `/visualizer`
   - The server will broadcast the following new events:
     - `step_status_changed`
     - `workflow_completed`
     - `mermaid_diagram`

### Frontend Components

1. **Cytoscape.js Updates**:
   - The web visualizer will listen for `step_status_changed`.
   - On reception, the corresponding node in the Cytoscape graph will have its styling classes updated (e.g., adding a `running` or `done` class) dynamically.

2. **Mermaid.js Live Rendering**:
   - The visualizer UI will feature a dedicated Mermaid tab.
   - A listener for the `mermaid_diagram` event will capture the new Mermaid syntax payload.
   - The payload will be passed to `mermaid.render()` to dynamically regenerate and inject the updated SVG diagram into the DOM without a full page refresh.

## Data Payloads (API Contract per ADR-083)

### `step_status_changed`
Emitted on any transition:
```json
{
  "issue": "<issue-id>",
  "workflow_id": "<workflow-id>",
  "step_id": "<step-id>",
  "agent_type": "<agent-role>",
  "status": "<pending|running|done|failed|skipped>",
  "timestamp": 1234567890.0
}
```

### `workflow_completed`
Emitted upon terminal state reached:
```json
{
  "issue": "<issue-id>",
  "workflow_id": "<workflow-id>",
  "status": "<success|failed>",
  "summary": "<result-summary>",
  "timestamp": 1234567890.0
}
```

### `mermaid_diagram`
Emitted alongside state changes to update the live Mermaid syntax:
```json
{
  "issue": "<issue-id>",
  "workflow_id": "<workflow-id>",
  "diagram": "<raw-mermaid-syntax>",
  "timestamp": 1234567890.0
}
```

## Security & Performance Considerations

- **Scalability**: `eventlet` provides asynchronous handling for concurrent WebSocket connections.
- **Payload Size**: The events are lightweight JSON structures. The Mermaid diagram payload is stringified text, which scales well for standard workflow sizes.
- **Robustness**: The frontend should implement reconnection logic (built into Socket.IO client) to handle intermittent network drops, optionally triggering a full state re-fetch upon reconnection to ensure synchronization.
