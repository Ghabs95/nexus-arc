# OpenClaw Release Guide

This document defines the release shape for shipping Nexus ARC as an OpenClaw-integrated product.

## Product Shape

Release the integration as two versioned packages with one operator-facing story:

1. `nexus-arc` on PyPI
   Ships the Nexus ARC framework, bridge server, and CLI entrypoints.
2. `@nexus-arc/openclaw-plugin` on npm
   Ships the OpenClaw `/nexus` command plugin.

Recommended product message:

- OpenClaw is the operator interface
- Nexus ARC is the workflow runtime
- the HTTP command bridge is the stable integration boundary

## Compatibility Contract

Current compatibility target:

- Bridge API: `/api/v1`
- Plugin manifest id: `nexus-arc`
- Recommended bridge CLI: `nexus-arc-bridge`
- Compatibility handshake endpoint: `GET /api/v1/capabilities`

Suggested versioning policy:

- Additive API changes: keep within `v1`
- Breaking bridge payload or auth changes: cut `/api/v2`
- Plugin releases should document minimum Nexus ARC version

## Release Checklist

### Python package

- Ensure `pyproject.toml` has console scripts:
    - `nexus`
    - `nexus-bridge`
    - `nexus-arc-bridge`
- Verify bridge docs reference:
    - `NEXUS_COMMAND_BRIDGE_HOST`
    - `NEXUS_COMMAND_BRIDGE_PORT`
    - `NEXUS_COMMAND_BRIDGE_AUTH_TOKEN`
    - allowed source/sender configuration
- Build and test:
    - `python -m build`
    - `python -m pytest tests/test_command_bridge_router.py tests/test_command_bridge_http.py`
- Publish to TestPyPI first

### OpenClaw plugin package

- Confirm `package.json` name is `@nexus-arc/openclaw-plugin`
- Confirm `openclaw.plugin.json` version matches `package.json`
- Verify `README.md` install instructions for both local and published install paths
- Build and test:
    - `npm pack --dry-run`
    - `node --test src/index.test.ts`
- Publish to npm with public access

### End-to-end validation

- Start the bridge locally:
    - `nexus-arc-bridge`
- Install the plugin into a clean OpenClaw profile
- Verify:
    - `/nexus help`
    - `/nexus health`
    - `/nexus plan demo#42`
    - `/nexus wfstate <workflow-id>`
    - rejected sender / bad token / bridge-down errors

## Operator Install Story

### 1. Install the backend

```bash
pip install nexus-arc
nexus-arc-bridge
```

### 2. Install the OpenClaw plugin

```bash
openclaw plugins install @nexus-arc/openclaw-plugin
openclaw plugins enable nexus-arc
```

### 3. Configure OpenClaw

```json5
{
  "plugins": {
    "entries": {
      "nexus-arc": {
        "enabled": true,
        "config": {
          "bridgeUrl": "http://127.0.0.1:8091",
          "authToken": "replace-me",
          "timeoutMs": 15000,
          "sourcePlatform": "openclaw",
          "defaultProject": "demo"
        }
      }
    }
  }
}
```

## Recommended Near-Term Follow-ups

- Add a `python -m build` CI job for PyPI artifact validation
- Add an `npm pack --dry-run` CI job for the plugin package
- Add a published compatibility matrix to the docs site or README
- Add a Docker image for the bridge after the first package release stabilizes
