# Connectors & Credentials in Nexus ARC

This document describes the emerging connector model for Nexus ARC: where
OAuth flows, token handling, provider adapters, and authenticated external
actions should live.

## Why this exists

AI agents are easy to connect to toy tools and hard to connect safely to real
systems.

The difficult part is usually not prompting. It is everything around the API:

- OAuth and app registration
- access token storage
- refresh token lifecycle
- scope boundaries
- provider-specific quirks
- auditability of external actions
- deciding which agent is allowed to use which credentialed capability

Nexus ARC is the right place to own that complexity.

## What a connector is

In Nexus ARC, a **connector** is the combination of:

1. **authentication model**
   - OAuth 2.0
   - API keys
   - service account credentials
   - sessionless token exchange when supported

2. **credential storage + lifecycle**
   - encrypted or protected storage
   - refresh / expiry tracking
   - revocation / invalidation handling

3. **provider adapter logic**
   - request/response normalization
   - pagination, retries, backoff
   - error translation
   - rate-limit awareness

4. **workflow-facing actions**
   - reusable operations that agents or workflows can invoke
   - examples: `github.issue.create`, `email.send`, `linkedin.profile.me`

5. **audit hooks**
   - what was called
   - by which workflow / operator / agent
   - with which outcome
   - without exposing raw secrets

## What belongs in Nexus ARC vs OpenClaw

### Belongs in Nexus ARC

- provider registration metadata
- OAuth start/callback/refresh flows
- token persistence
- connector configuration
- normalized provider models
- rate-limit state and retry policy
- bridge endpoints for authenticated actions
- workflow-safe external action execution
- auditing of connector usage

### Belongs in OpenClaw

- conversational UI
- operator commands and summaries
- agent interaction / planning
- approval prompts and human-facing explanations
- invoking Nexus ARC bridge commands

### Mental model

- **OpenClaw** asks for work to be done
- **Nexus ARC** knows how to do authenticated work safely

## Canonical connector lifecycle

### 1. Register app / auth config

Example inputs:

- client id
- client secret
- redirect URI
- scopes
- provider-specific metadata

### 2. Start auth flow

Nexus ARC should be able to expose a bridge action such as:

- `connector.auth.start(provider="linkedin")`

Result:

- authorization URL
- opaque state token
- callback tracking metadata

### 3. Complete callback

Nexus ARC receives the callback and stores:

- access token
- refresh token (if available)
- expiry
- scopes granted
- auth status

### 4. Expose normalized actions

Once connected, the connector exposes workflow-facing actions such as:

- `linkedin.profile.me`
- `linkedin.company.lookup`
- `github.issue.create`
- `email.send`

The action shape should be stable even if provider-specific API details change.

### 5. Track auth/runtime state

Nexus ARC should record:

- token validity
- last refresh attempt
- recent auth failures
- rate-limit state
- connector health

### 6. Audit usage

For each action, Nexus ARC should be able to answer:

- which workflow called it
- which operator or bridge client triggered it
- whether it succeeded or failed
- what provider resource was touched

## Connector design principles

### 1. Normalize late, not too early

Do not flatten provider-specific semantics so aggressively that useful detail is
lost.

Bad:
- one giant generic `social_api.call`

Better:
- explicit provider actions with normalized outputs where it helps

### 2. Hide secrets, not metadata

Secrets should never be returned to operators or agents.

But these should be visible:

- auth status
- scopes granted
- token expiry window
- connector health
- last successful use

### 3. Connector actions should be workflow-safe

An action should be designed to work inside a workflow with:

- retries
- timeouts
- idempotency assumptions
- audit trails
- approval gates where needed

### 4. Bridge-first, not runtime-hack-first

If OpenClaw or another operator surface needs a connector capability, prefer
calling a Nexus ARC bridge/operator endpoint over duplicating OAuth/API logic in
that runtime.

### 5. Policy matters

Credentialed actions should eventually support policy decisions such as:

- which workflows may use this connector
- which agents may invoke which actions
- whether human approval is required
- whether write actions are allowed at all

## Suggested connector structure

A likely long-term shape inside Nexus ARC could be something like:

```text
nexus/
  adapters/
    integrations/
      linkedin/
        client.py
        oauth.py
        models.py
        actions.py
        storage.py
        health.py
      github/
      google/
      email/
```

Possible separation of concerns:

- `oauth.py` → auth flow and token refresh
- `client.py` → low-level API client
- `models.py` → normalized objects
- `actions.py` → workflow-facing operations
- `storage.py` → token/config persistence
- `health.py` → runtime status and rate-limit tracking

## Example: LinkedIn

If Nexus ARC adds an official LinkedIn connector, the connector should probably
focus on what the official API can support safely and durably:

- auth status
- current identity/profile info
- company/profile metadata where available
- normalized lead/contact enrichment

OpenClaw/Jarvis can then do things like:

- ask Nexus whether LinkedIn is connected
- enrich CRM contacts with LinkedIn metadata
- trigger workflow actions that depend on LinkedIn auth

But the connector itself belongs in Nexus ARC.

## Minimal bridge surface to target

A practical bridge/operator surface for connectors would include:

### Auth / status
- `connector.auth.start`
- `connector.auth.complete`
- `connector.auth.status`
- `connector.auth.revoke`

### Health / metadata
- `connector.health`
- `connector.scopes`
- `connector.last-used`

### Actions
- `connector.action.invoke`

Or, better, explicit namespaced actions such as:

- `linkedin.profile.me`
- `linkedin.company.lookup`
- `email.send`
- `github.issue.create`

## Recommended rollout order

1. **document the connector model**
2. **add credential/auth state primitives**
3. **add one official connector end to end**
4. **expose bridge/operator APIs for that connector**
5. **let OpenClaw operate it as a trusted surface**

## Short version

Nexus ARC should become the place where:

- external systems are connected
- credentials are stored safely
- provider APIs are normalized
- workflows use those integrations
- agent-triggered external actions are audited

That makes Nexus ARC more than an orchestration framework.
It becomes the authenticated integration backbone for agent systems.
