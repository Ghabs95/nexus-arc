# OpenClaw Nexus Command Plugin

This package forwards `/nexus`, `/chat`, and `/chatagents` commands from OpenClaw to the Nexus ARC HTTP command bridge.

Published package target: `@nexus-arc/openclaw-plugin`

Compatibility targets:

- Nexus ARC bridge API: `v1`
- Minimum OpenClaw plugin runtime: current manifest-based plugin runtime
- Recommended Nexus ARC package: `nexus-arc>=0.1.1`

It now matches OpenClaw's actual plugin contract:

- `openclaw.plugin.json` manifest with inline JSON Schema
- `package.json` `openclaw.extensions` entry
- `api.registerCommand(...)` command registration
- config loaded from `plugins.entries.nexus-arc.config`

Recommended plugin config:

```json5
{
  plugins: {
    entries: {
      "nexus-arc": {
        enabled: true,
        config: {
          bridgeUrl: "http://127.0.0.1:8091",
          authToken: "replace-me",
          timeoutMs: 15000,
          sourcePlatform: "openclaw",
          defaultProject: "demo",
          renderMode: "rich",
          sessionMemory: true,
          requireConfirmFor: ["implement", "respond", "stop", "kill", "reprocess"],
          autoPollAccepted: true,
          acceptedPollDelayMs: 1500,
          acceptedPollAttempts: 1
        }
      }
    }
  }
}
```

Install locally during development:

```bash
openclaw plugins install ./packages/nexus-arc
openclaw plugins enable nexus-arc
openclaw config validate
```

Planned published install:

```bash
openclaw plugins install @nexus-arc/openclaw-plugin
openclaw plugins enable nexus-arc
openclaw config validate
```

Examples:

- `/nexus help`
- `/nexus health`
- `/nexus current`
- `/nexus use demo`
- `/chat`
- `/chat Review the current workspace architecture and call out the biggest risks`
- `/chatagents demo`
- `/nexus usage demo#42`
- `/nexus usage #42`
- `/nexus refresh`
- `/nexus status demo`
- `/nexus new demo investigate flaky retries`
- `/nexus plan demo 42`
- `/nexus plan demo#42`
- `/nexus track demo#42`
- `/nexus tracked`
- `/nexus myissues`
- `/nexus implement demo#42`
- `/nexus reconcile demo#42`
- `/nexus reprocess demo#42`
- `/nexus kill demo#42 --yes`
- `/nexus pause demo#42`
- `/nexus resume demo#42`
- `/nexus stop demo#42`
- `/nexus logs demo#42`
- `/nexus wfstate demo-42-full`
- `/nexus show me the workflow state for demo#42`

The plugin now forwards richer bridge metadata with each request:

- requester identity: `operator_id`, `session_id`, `roles`
- requester metadata: raw args, message id, thread id, attachment summaries
- session hints: `context.current_project`, `context.current_issue_ref`, `context.current_workflow_id`
- client metadata: `client.plugin_version`, `client.render_mode`

It also keeps per-session local context in memory so `/nexus use <project>` and
`/nexus current` work without needing the bridge to be available.
OpenClaw chat turns can also go straight through `/chat <message>` while using
the same Nexus workspace chat memory and project context.

Risky commands can require local confirmation before they hit the bridge.
By default this covers `implement`, `respond`, `stop`, `kill`, and `reprocess`, and operators can
either run `/nexus confirm`, `/nexus cancel`, or re-run the command with
`--yes`.

When the Nexus bridge returns `usage` metadata, the plugin renders provider,
model, token, and estimated cost details directly in the OpenClaw response.
The bridge now fills that field on a best-effort basis from recent completion
storage or the latest agent log for the referenced issue/workflow.

Additional bridge-aware behavior:

- `/nexus help` tries to render the live bridge command catalog from `/api/v1/capabilities`
- `/nexus health` checks `/healthz` and reports config warnings
- accepted long-running commands can poll the workflow status endpoint once before replying
- bridge auth, allowlist, timeout, and connectivity failures render as distinct user-facing errors

Release notes:

- npm package name: `@nexus-arc/openclaw-plugin`
- manifest id remains `nexus-arc` so existing OpenClaw config stays stable
- pair this plugin with the Python bridge entrypoint `nexus-arc-bridge` or `nexus command-bridge`
