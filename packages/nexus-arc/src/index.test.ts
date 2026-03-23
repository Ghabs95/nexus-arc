import test from "node:test";
import assert from "node:assert/strict";

import {
    BridgeRequestError,
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
            currentWorkflowId: null
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
            currentWorkflowId: null
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
            currentWorkflowId: null
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
