# ADR 084: Social Media Marketing Workflow

## Status
Proposed

## Context
There is a need to expand Nexus capabilities beyond software development workflows into automated marketing. Specifically, Nexus needs a social media marketing workflow to generate, schedule, and distribute content across various platforms automatically.

## Decision
We will implement an automated social media marketing workflow within the Nexus AI Orchestrator. 

### Architecture
1. **Content Generation Engine**: Leverage the existing AI Orchestrator to consume prompts or source materials (like release notes or blog posts) to generate platform-specific content (e.g., Twitter threads, LinkedIn posts).
2. **Platform Integrations**:
   - Implement external adapters for Twitter/X API, LinkedIn API, and Discord Webhooks.
   - Store OAuth credentials and platform tokens securely in the `project_config.yaml` or `.env`.
3. **Orchestration**:
   - Create a new workflow type `social-marketing`.
   - Add specific agents: `content-creator`, `marketing-reviewer`, and `publisher`.

### Content Generation Logic
- **Input**: User provides raw content or a topic via the Nexus chat interface or an issue.
- **Processing**: The `content-creator` agent uses LLM completion to draft variants for each target platform, adhering to character limits and best practices (e.g., hashtag insertion, formatting).
- **Approval**: Drafts are routed to the `marketing-reviewer` agent or require manual human approval via the standard `TASK_CONFIRMATION_MODE`.

## Consequences
- **Positive**: Extends Nexus utility, providing significant value to product operators and founders for go-to-market automation.
- **Negative**: Adds complexity to the agent ecosystem and requires maintaining compliance with third-party social media API rate limits and terms of service.