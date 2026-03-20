# OpenClaw Nexus Command Plugin

This package forwards `/nexus` commands from OpenClaw to the Nexus ARC HTTP command bridge.

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
          requireConfirmFor: ["implement", "respond", "stop"]
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

Examples:

- `/nexus help`
- `/nexus current`
- `/nexus use demo`
- `/nexus usage demo#42`
- `/nexus refresh`
- `/nexus status demo`
- `/nexus new demo investigate flaky retries`
- `/nexus plan demo#42`
- `/nexus track demo#42`
- `/nexus tracked`
- `/nexus myissues`
- `/nexus implement demo#42`
- `/nexus pause demo#42`
- `/nexus resume demo#42`
- `/nexus stop demo#42`
- `/nexus logs demo#42`
- `/nexus show me the workflow state for demo#42`

The plugin now forwards richer bridge metadata with each request:

- requester identity: `operator_id`, `session_id`, `roles`
- session hints: `context.current_project`, `context.current_issue_ref`
- client metadata: `client.plugin_version`, `client.render_mode`

It also keeps per-session local context in memory so `/nexus use <project>` and
`/nexus current` work without needing the bridge to be available.

Risky commands can require local confirmation before they hit the bridge.
By default this covers `implement`, `respond`, and `stop`, and operators can
either run `/nexus confirm`, `/nexus cancel`, or re-run the command with
`--yes`.

When the Nexus bridge returns `usage` metadata, the plugin renders provider,
model, token, and estimated cost details directly in the OpenClaw response.
The bridge now fills that field on a best-effort basis from recent completion
storage or the latest agent log for the referenced issue/workflow.
