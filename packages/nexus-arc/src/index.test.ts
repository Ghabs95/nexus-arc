import test from "node:test";
import assert from "node:assert/strict";

import {
    BridgeRequestError,
    bindWorkflowSessionAffinity,
    buildReplyCorrelationId,
    buildWorkflowSessionKey,
    formatBridgeError,
    getRequesterContext,
    inferCommandContext,
    normalizeInvocationArgs,
    parseNexusInvocation,
    tokenizeInput
} from "./index.ts";

test("tokenizeInput preserves quoted phrases", () => {
    assert.deepEqual(tokenizeInput('new demo "investigate launch retries"'), [
        "new",
        "demo",
        "investigate launch retries"
    ]);
});

test("parseNexusInvocation treats unknown leading text as freeform", () => {
    const parsed = parseNexusInvocation("show me the workflow state for demo#42");

    assert.equal(parsed.command, "");
    assert.equal(parsed.explicitCommand, false);
    assert.equal(parsed.freeform, "show me the workflow state for demo#42");
});

test("parseNexusInvocation recognizes chat when bridge capabilities expose it", () => {
    const parsed = parseNexusInvocation('chat "review the current auth approach"', {
        supported_commands: ["chat", "chatagents"]
    });

    assert.equal(parsed.command, "chat");
    assert.equal(parsed.explicitCommand, true);
    assert.deepEqual(parsed.args, ["review the current auth approach"]);
});

test("normalizeInvocationArgs expands bare issue numbers from session state", () => {
    const parsed = parseNexusInvocation("plan #42", {
        supported_commands: ["plan", "wfstate"]
    });
    const normalized = normalizeInvocationArgs(
        parsed,
        {
            currentProject: "demo",
            currentIssueRef: null,
            currentWorkflowId: null,
            affinitySessionKey: null,
            affinityBoundAt: null,
            lastCorrelationId: null,
            lastMessageId: null,
            lastThreadId: null
        },
        {
            bridgeUrl: "http://127.0.0.1:8091",
            authToken: "secret",
            timeoutMs: 15000,
            sourcePlatform: "openclaw",
            defaultProject: "",
            renderMode: "rich",
            sessionMemory: true,
            requireConfirmFor: ["implement"],
            autoPollAccepted: true,
            acceptedPollDelayMs: 1500,
            acceptedPollAttempts: 1
        }
    );

    assert.equal(normalized.command, "plan");
    assert.deepEqual(normalized.args, ["demo", "42"]);
});

test("normalizeInvocationArgs expands bare issue numbers for recovery commands", () => {
    const parsed = parseNexusInvocation("reprocess #42", {
        supported_commands: ["reprocess", "wfstate"]
    });
    const normalized = normalizeInvocationArgs(
        parsed,
        {
            currentProject: "demo",
            currentIssueRef: null,
            currentWorkflowId: null,
            affinitySessionKey: null,
            affinityBoundAt: null,
            lastCorrelationId: null,
            lastMessageId: null,
            lastThreadId: null
        },
        {
            bridgeUrl: "http://127.0.0.1:8091",
            authToken: "secret",
            timeoutMs: 15000,
            sourcePlatform: "openclaw",
            defaultProject: "",
            renderMode: "rich",
            sessionMemory: true,
            requireConfirmFor: ["implement"],
            autoPollAccepted: true,
            acceptedPollDelayMs: 1500,
            acceptedPollAttempts: 1
        }
    );

    assert.equal(normalized.command, "reprocess");
    assert.deepEqual(normalized.args, ["demo", "42"]);
});

test("inferCommandContext captures raw workflow ids for wfstate", () => {
    const parsed = parseNexusInvocation("wfstate demo-42-full", {
        supported_commands: ["wfstate"]
    });

    const context = inferCommandContext(
        parsed,
        {
            currentProject: null,
            currentIssueRef: null,
            currentWorkflowId: null,
            affinitySessionKey: null,
            affinityBoundAt: null,
            lastCorrelationId: null,
            lastMessageId: null,
            lastThreadId: null
        },
        {
            bridgeUrl: "http://127.0.0.1:8091",
            authToken: "secret",
            timeoutMs: 15000,
            sourcePlatform: "openclaw",
            defaultProject: "",
            renderMode: "rich",
            sessionMemory: true,
            requireConfirmFor: ["implement"],
            autoPollAccepted: true,
            acceptedPollDelayMs: 1500,
            acceptedPollAttempts: 1
        }
    );

    assert.equal(context.current_workflow_id, "demo-42-full");
});

test("buildWorkflowSessionKey uses deterministic workflow affinity format", () => {
    assert.equal(buildWorkflowSessionKey("demo-42-full"), "nexus::workflow:demo-42-full");
});

test("bindWorkflowSessionAffinity stamps workflow-bound session state", () => {
    const binding = bindWorkflowSessionAffinity(
        "openclaw:telegram:chat-1",
        {
            currentProject: "demo",
            currentIssueRef: "demo#42",
            currentWorkflowId: null,
            affinitySessionKey: null,
            affinityBoundAt: null,
            lastCorrelationId: null,
            lastMessageId: "1001",
            lastThreadId: "thread-a"
        },
        {
            workflowId: "demo-42-full",
            issueRef: "demo#42",
            projectKey: "demo",
            correlationId: "corr-42",
            messageId: "1001",
            threadId: "thread-a"
        }
    );

    assert.equal(binding.sessionKey, "nexus::workflow:demo-42-full");
    assert.equal(binding.workflowId, "demo-42-full");
    assert.equal(binding.lastCorrelationId, "corr-42");
});

test("inferCommandContext includes affinity metadata and falls back cleanly", () => {
    const context = inferCommandContext(
        parseNexusInvocation("plan #42", {supported_commands: ["plan"]}),
        {
            currentProject: "demo",
            currentIssueRef: "demo#42",
            currentWorkflowId: null,
            affinitySessionKey: null,
            affinityBoundAt: null,
            lastCorrelationId: "corr-local",
            lastMessageId: "1001",
            lastThreadId: "thread-a"
        },
        {
            bridgeUrl: "http://127.0.0.1:8091",
            authToken: "secret",
            timeoutMs: 15000,
            sourcePlatform: "openclaw",
            defaultProject: "",
            renderMode: "rich",
            sessionMemory: true,
            requireConfirmFor: ["implement"],
            autoPollAccepted: true,
            acceptedPollDelayMs: 1500,
            acceptedPollAttempts: 1
        },
        "openclaw:telegram:chat-1"
    );

    assert.equal(context.current_issue_ref, "demo#42");
    assert.deepEqual(context.metadata, {
        affinity: {
            mode: "session-fallback",
            session_key: "nexus::session:openclaw:telegram:chat-1",
            workflow_id: null,
            bound_at: null,
            last_correlation_id: "corr-local",
            last_message_id: "1001",
            last_thread_id: "thread-a"
        }
    });
});

test("buildReplyCorrelationId correlates reply flow to session workflow and message", () => {
    assert.equal(
        buildReplyCorrelationId({
            sourcePlatform: "openclaw",
            sessionId: "openclaw:telegram:chat-1",
            workflowId: "demo-42-full",
            command: "respond",
            messageId: "1001"
        }),
        "openclaw::openclaw:telegram:chat-1::demo-42-full::respond::1001"
    );
});

test("formatBridgeError maps auth failures to friendly guidance", () => {
    const error = new BridgeRequestError("Missing bearer token", {
        code: "missing_bearer_token"
    });

    assert.match(formatBridgeError(error), /authentication failed/i);
});

test("getRequesterContext forwards OpenClaw auth-backed requester identity", () => {
    const requester = getRequesterContext(
        {
            senderId: "alice",
            senderName: "Alice",
            channel: "ghabs",
            channelId: "workspace-ghabs",
            isAuthorizedSender: true
        },
        {
            bridgeUrl: "http://127.0.0.1:8091",
            authToken: "secret",
            timeoutMs: 15000,
            sourcePlatform: "openclaw",
            defaultProject: "",
            renderMode: "rich",
            sessionMemory: true,
            requireConfirmFor: ["implement"],
            autoPollAccepted: true,
            acceptedPollDelayMs: 1500,
            acceptedPollAttempts: 1
        }
    );

    assert.equal(requester.auth_authority, "openclaw");
    assert.equal(requester.nexus_id, "openclaw:user:alice");
    assert.equal(requester.is_authorized_sender, true);
});
