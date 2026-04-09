# ADR-001: Multi-Agent Workflow Composition Architecture

**Status:** Proposed  
**Date:** 2026-04-09  
**Issue:** [#151](https://github.com/ghabs-org/nexus-arc/issues/151)  
**Authors:** Gab, Jarvis

---

## Context

Nexus ARC currently implements agent workflows as a linear step-chain (design →
developer → reviewer → compliance → deployer → writer). This works well for
single-track enterprise workflows with full audit trails but is:

- **Token-heavy** — each step carries full conversation context
- **Not composable** — no parallel execution, no sub-workflow nesting, no retry loops
- **Not reusable** — workflow logic is tightly coupled to the Nexus Workflow engine

Google's Agent Development Kit (ADK) formalizes multi-agent patterns
(Sequential, Parallel, Loop, Coordinator) that map cleanly to our needs. We
evaluated ADK as a potential dependency and rejected it (see Decision below), but
the patterns themselves are worth adopting natively.

---

## Decision

We will introduce a **three-layer multi-agent architecture** in Nexus ARC:

### Layer 1 — `nexus/agents/` (programmatic primitives)

A new lightweight module implementing the core composition primitives:

- **`SequentialAgent`** — runs sub-agents in order; each receives a minimal
  context slice (not full history) and the previous agent's output
- **`ParallelAgent`** — runs sub-agents concurrently; outputs are merged before
  returning to the caller
- **`LoopAgent`** — runs a sub-agent repeatedly until a stop condition is met
  (useful for refinement/retry loops)
- **`Coordinator`** — an LLM agent that receives a task, selects sub-agents
  dynamically using LLM-driven delegation, and aggregates results

These primitives use the existing pluggable `AIProvider` interface directly. No
DB writes, no Git artifacts — pure in-memory composition.

### Layer 2 — Nexus Workflow (enterprise wrapper)

The existing Nexus Workflow engine wires `nexus/agents/` primitives into its
step-chain, adding:

- Git artifact writing (issues, PR comments, reviews) per step output
- DB persistence for audit trail and state recovery
- Observable workflow lifecycle (started, step-complete, failed, done)

Workflow authors can now compose Sequential/Parallel/Loop primitives inside
workflow step definitions, rather than writing flat sequential chains only.

### Layer 3 — OpenClaw (conversational wrapper)

OpenClaw calls `nexus/agents/` via a new skill or direct HTTP call to the Nexus
bridge. Sub-agents are scoped to the conversation turn — no DB, no Git writes
unless explicitly requested. This enables interactive multi-agent work driven
from Telegram or other chat surfaces.

---

## Role of nexus-router

nexus-router is a **dependency of the Coordinator**, not a participant in
composition. The Coordinator calls nexus-router to resolve which model to use for
each sub-agent given the task type:

```
Coordinator
  └─ "I need a sub-agent for task=code_review"
       └─ POST nexus-router /route { task_type: "code_review" }
            └─ returns: { model: "claude-sonnet", provider: "anthropic" }
       └─ instantiates sub-agent via AIProvider(model)
```

nexus-router responsibilities remain unchanged:
- Model selection (task → best model given cost/quota/health)
- Quota and health tracking
- Routing feedback and calibration

nexus-router does **not** spawn agents, manage context passing, handle
retry logic, or write Git artifacts.

---

## Token Efficiency Principles

Token efficiency is a first-class concern at every layer:

1. **Minimal context per sub-agent** — each sub-agent receives only what it needs
   (task description + relevant prior output), not the full conversation history
2. **Output summarisation** — sub-agent outputs are summarised before being
   passed up to the Coordinator or the next step
3. **Model right-sizing** — nexus-router selects the cheapest/fastest model that
   meets the task requirements; heavy models (Claude Sonnet) only for tasks that
   need them
4. **Parallel over sequential where possible** — `ParallelAgent` reduces
   wall-clock time without multiplying token cost linearly

---

## Rejected Alternatives

### Adopt ADK (Google Agent Development Kit) directly

**Rejected because:**
- ADK is optimized for Gemini and Vertex AI; would introduce a Google/GCP dependency
- Conflicts with the existing pluggable `AIProvider` architecture
- Framework overhead not justified for our team size
- Vendor lock-in risk

The patterns (Sequential/Parallel/Loop/Coordinator) are worth adopting; the
framework is not.

### Extend nexus-router into a Coordinator

**Rejected because:**
- nexus-router's scope is model selection, not agent orchestration
- Mixing concerns would make both harder to maintain and test
- nexus-router is a stateless HTTP service; coordination requires statefulness
  (context passing, output accumulation)

### Keep the existing flat step-chain

**Rejected because:**
- Cannot express parallel execution (e.g. run security review and code review
  simultaneously)
- Cannot nest sub-workflows
- Token cost scales linearly with chain length even when steps are independent

---

## Consequences

**Positive:**
- Framework-users can compose Sequential/Parallel/Loop primitives declaratively
- Nexus Workflow gains parallel and loop capabilities without rewriting the engine
- OpenClaw gains interactive multi-agent coordination natively
- Token cost is reduced via right-sizing and minimal context slicing
- nexus-router's value increases: model selection is called more often (per
  sub-agent, not per turn)

**Negative / risks:**
- More moving parts: `nexus/agents/` layer adds complexity
- Context slicing logic needs careful design to avoid losing information
- Parallel agent output merging is non-trivial for LLM-generated content
- Testing multi-agent flows is harder than testing linear chains

---

## Implementation Plan

1. **`nexus/agents/base.py`** — `BaseAgent` abstract class with `run(context) → AgentOutput`
2. **`nexus/agents/sequential.py`** — `SequentialAgent`
3. **`nexus/agents/parallel.py`** — `ParallelAgent` (asyncio-based)
4. **`nexus/agents/loop.py`** — `LoopAgent`
5. **`nexus/agents/coordinator.py`** — `Coordinator` with nexus-router integration
6. **`nexus/agents/context.py`** — context slicing and output summarisation utilities
7. Wire into Nexus Workflow step definitions (optional, behind a feature flag)
8. Add OpenClaw bridge endpoint or skill

Each step ships with unit tests and a minimal integration test using a mock
`AIProvider`.
