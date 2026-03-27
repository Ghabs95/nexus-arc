const PLUGIN_ID = "nexus-arc";
const PLUGIN_VERSION = "0.2.0";

type PluginConfig = {
    bridgeUrl?: string;
    authToken?: string;
    timeoutMs?: number;
    sourcePlatform?: string;
    defaultProject?: string;
    renderMode?: string;
    sessionMemory?: boolean;
    requireConfirmFor?: string[];
    autoPollAccepted?: boolean;
    acceptedPollDelayMs?: number;
    acceptedPollAttempts?: number;
};

type CommandHandlerContext = {
    senderId?: string;
    senderName?: string;
    channel?: string;
    channelId?: string;
    threadId?: string;
    messageId?: string;
    isAuthorizedSender?: boolean;
    args?: string;
    commandBody?: string;
    attachments?: unknown[];
    config?: Record<string, unknown>;
};

type CommandResponse = {
    text: string;
};

type RegisterCommandApi = {
    registerCommand: (command: {
        name: string;
        description: string;
        acceptsArgs?: boolean;
        requireAuth?: boolean;
        handler: (ctx: CommandHandlerContext) => Promise<CommandResponse> | CommandResponse;
    }) => void;
};

type NexusCommandResult = {
    status: string;
    message: string;
    workflow_id?: string | null;
    issue_number?: string | null;
    project_key?: string | null;
    workflow?: {
        id?: string | null;
        issue_number?: string | null;
        project_key?: string | null;
        state?: string | null;
    };
    ui?: {
        title?: string;
        summary?: string;
        fields?: Array<{ label?: string; value?: string }>;
        actions?: string[];
    };
    audit?: {
        request_id?: string;
        actor?: string;
        session_id?: string;
    };
    usage?: {
        provider?: string;
        model?: string;
        input_tokens?: number | null;
        output_tokens?: number | null;
        estimated_cost_usd?: number | null;
    };
    suggested_next_commands?: string[];
};

type RequesterPayload = {
    source_platform: string;
    nexus_id?: string;
    auth_authority?: string;
    operator_id: string;
    sender_id: string;
    sender_name: string;
    channel_id: string;
    channel_name: string;
    session_id: string;
    is_authorized_sender?: boolean;
    roles: string[];
    access_groups: string[];
    metadata: {
        command_body: string;
        raw_args: string;
        message_id: string;
        thread_id: string;
        attachments_count: number;
        attachments: Array<Record<string, unknown>>;
    };
};

type SessionState = {
    currentProject: string | null;
    currentIssueRef: string | null;
    currentWorkflowId: string | null;
    affinitySessionKey: string | null;
    affinityBoundAt: number | null;
    lastCorrelationId: string | null;
    lastMessageId: string | null;
    lastThreadId: string | null;
};

export type SessionAffinityBinding = {
    workflowId: string;
    sessionId: string;
    sessionKey: string;
    issueRef: string | null;
    projectKey: string | null;
    boundAt: number;
    lastCorrelationId: string | null;
    lastMessageId: string | null;
    lastThreadId: string | null;
};

type PendingConfirmation = {
    path: string;
    payload: Record<string, unknown>;
    summary: string;
    createdAt: number;
};

type WorkflowStatusPayload = {
    ok?: boolean;
    workflow_id?: string;
    issue_number?: string;
    project_key?: string | null;
    status?: Record<string, unknown>;
    usage?: NexusCommandResult["usage"];
    error?: string;
    error_code?: string;
};

type BridgeCapabilitiesPayload = {
    ok?: boolean;
    version?: string;
    route_enabled?: boolean;
    supported_commands?: string[];
    long_running_commands?: string[];
    clarification_hint?: string;
};

type ParsedInvocation = {
    command: string;
    args: string[];
    freeform: string;
    explicitCommand: boolean;
};

type BridgeErrorOptions = {
    status?: number;
    code?: string;
    cause?: unknown;
};

export class BridgeRequestError extends Error {
    status: number | null;
    code: string;
    cause?: unknown;

    constructor(message: string, options: BridgeErrorOptions = {}) {
        super(message);
        this.name = "BridgeRequestError";
        this.status = typeof options.status === "number" ? options.status : null;
        this.code = stringValue(options.code) || "bridge_error";
        this.cause = options.cause;
    }
}

const STATIC_SUPPORTED_COMMANDS = [
    "chat",
    "chatagents",
    "status",
    "active",
    "logs",
    "wfstate",
    "usage",
    "new",
    "plan",
    "prepare",
    "implement",
    "respond",
    "track",
    "tracked",
    "untrack",
    "myissues",
    "pause",
    "resume",
    "stop",
    "continue",
    "reconcile",
    "reprocess",
    "kill",
    "agents",
    "audit",
    "stats"
] as const;

const LOCAL_COMMANDS = ["current", "use", "confirm", "cancel", "refresh", "help", "health"] as const;
const HELP_TOKENS = new Set(["help", "--help", "-h", "?"]);
const ISSUE_SCOPED_COMMANDS = new Set([
    "logs",
    "wfstate",
    "usage",
    "new",
    "plan",
    "prepare",
    "implement",
    "respond",
    "track",
    "tracked",
    "untrack",
    "pause",
    "resume",
    "stop",
    "continue",
    "reconcile",
    "reprocess",
    "kill"
]);
const CONFIRMATION_TTL_MS = 2 * 60 * 1000;
const warnedConfigKeys = new Set<string>();
const capabilitiesCache = new Map<string, BridgeCapabilitiesPayload>();
const sessionStateStore = new Map<string, SessionState>();
const workflowSessionBindings = new Map<string, SessionAffinityBinding>();
const pendingConfirmations = new Map<string, PendingConfirmation>();

function buildRequestReplayHeaders(): Record<string, string> {
    const timestamp = String(Math.floor(Date.now() / 1000));
    let nonce: string;
    const webCrypto =
        typeof globalThis !== "undefined" &&
        globalThis.crypto &&
        typeof (globalThis.crypto as Crypto).getRandomValues === "function"
            ? (globalThis.crypto as Crypto)
            : typeof crypto !== "undefined" && typeof (crypto as Crypto).getRandomValues === "function"
              ? (crypto as Crypto)
              : null;
    if (webCrypto) {
        const buf = new Uint8Array(16);
        webCrypto.getRandomValues(buf);
        nonce = Array.from(buf)
            .map((b) => b.toString(16).padStart(2, "0"))
            .join("");
    } else if (typeof crypto !== "undefined" && typeof (crypto as { randomUUID?: () => string }).randomUUID === "function") {
        nonce = (crypto as { randomUUID: () => string }).randomUUID().replace(/-/g, "");
    } else {
        nonce = `${Date.now()}-${Math.random().toString(16).slice(2)}`;
    }
    return {
        "x-nexus-timestamp": timestamp,
        "x-nexus-nonce": nonce
    };
}

function isRecord(value: unknown): value is Record<string, unknown> {
    return typeof value === "object" && value !== null;
}

function resolvePluginConfig(ctx: CommandHandlerContext): Required<PluginConfig> {
    const root = isRecord(ctx.config) ? ctx.config : {};
    const plugins = isRecord(root.plugins) ? root.plugins : {};
    const entries = isRecord(plugins.entries) ? plugins.entries : {};
    const pluginEntry = isRecord(entries[PLUGIN_ID]) ? entries[PLUGIN_ID] : {};
    const pluginConfig = isRecord(pluginEntry.config) ? pluginEntry.config : {};

    return {
        bridgeUrl: stringValue(pluginConfig.bridgeUrl) || "http://127.0.0.1:8091",
        authToken: stringValue(pluginConfig.authToken),
        timeoutMs: numberValue(pluginConfig.timeoutMs) ?? 15000,
        sourcePlatform: stringValue(pluginConfig.sourcePlatform) || "openclaw",
        defaultProject: stringValue(pluginConfig.defaultProject),
        renderMode: stringValue(pluginConfig.renderMode) || "rich",
        sessionMemory: booleanValue(pluginConfig.sessionMemory) ?? true,
        requireConfirmFor:
            stringArrayValue(pluginConfig.requireConfirmFor).length > 0
                ? stringArrayValue(pluginConfig.requireConfirmFor)
                : ["implement", "respond", "stop", "kill", "reprocess"],
        autoPollAccepted: booleanValue(pluginConfig.autoPollAccepted) ?? true,
        acceptedPollDelayMs: numberValue(pluginConfig.acceptedPollDelayMs) ?? 1500,
        acceptedPollAttempts: numberValue(pluginConfig.acceptedPollAttempts) ?? 1
    };
}

function stringValue(value: unknown): string {
    return typeof value === "string" ? value.trim() : "";
}

function numberValue(value: unknown): number | null {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function booleanValue(value: unknown): boolean | null {
    return typeof value === "boolean" ? value : null;
}

function stringArrayValue(value: unknown): string[] {
    if (!Array.isArray(value)) {
        return [];
    }
    return value.map(stringValue).filter(Boolean);
}

export function tokenizeInput(rawArgs: string | undefined): string[] {
    const input = String(rawArgs ?? "").trim();
    if (!input) {
        return [];
    }
    const tokens: string[] = [];
    let current = "";
    let quote: "'" | '"' | "" = "";
    let escaping = false;
    for (const char of input) {
        if (escaping) {
            current += char;
            escaping = false;
            continue;
        }
        if (char === "\\") {
            escaping = true;
            continue;
        }
        if (quote) {
            if (char === quote) {
                quote = "";
            } else {
                current += char;
            }
            continue;
        }
        if (char === "'" || char === '"') {
            quote = char;
            continue;
        }
        if (/\s/.test(char)) {
            if (current) {
                tokens.push(current);
                current = "";
            }
            continue;
        }
        current += char;
    }
    if (current) {
        tokens.push(current);
    }
    return tokens;
}

function normalizeSupportedCommands(capabilities?: BridgeCapabilitiesPayload): string[] {
    const fromBridge = Array.isArray(capabilities?.supported_commands)
        ? capabilities?.supported_commands?.map(stringValue).filter(Boolean)
        : [];
    return fromBridge.length > 0 ? fromBridge : [...STATIC_SUPPORTED_COMMANDS];
}

export function parseNexusInvocation(
    rawArgs: string | undefined,
    capabilities?: BridgeCapabilitiesPayload
): ParsedInvocation {
    const freeform = String(rawArgs ?? "").trim();
    const tokens = tokenizeInput(rawArgs);
    if (tokens.length === 0) {
        return {command: "", args: [], freeform: "", explicitCommand: false};
    }
    const supportedCommands = new Set<string>(normalizeSupportedCommands(capabilities));
    const localCommands = new Set<string>(LOCAL_COMMANDS as readonly string[]);
    const [first, ...rest] = tokens;
    const normalized = first.toLowerCase();
    if (supportedCommands.has(normalized) || localCommands.has(normalized) || HELP_TOKENS.has(normalized)) {
        return {
            command: HELP_TOKENS.has(normalized) ? "help" : normalized,
            args: rest,
            freeform,
            explicitCommand: true
        };
    }
    return {
        command: "",
        args: [],
        freeform,
        explicitCommand: false
    };
}

function buildScopedIdentity(parts: Array<string | undefined>, fallback: string): string {
    const value = parts
        .map((part) => String(part ?? "").trim())
        .filter(Boolean)
        .join(":");
    return value || fallback;
}

function parseIssueRef(value: string): { projectKey: string; issueRef: string; issueNumber: string } | null {
    const trimmed = String(value ?? "").trim();
    const match = /^([A-Za-z0-9_.-]+)#(?<issue>\d+)$/.exec(trimmed);
    if (!match?.groups?.issue) {
        return null;
    }
    return {
        projectKey: match[1],
        issueRef: trimmed,
        issueNumber: match.groups.issue
    };
}

function parseBareIssue(value: string): string | null {
    const match = /^#?(?<issue>\d+)$/.exec(String(value ?? "").trim());
    return match?.groups?.issue ? match.groups.issue : null;
}

function buildIssueRef(projectKey: string | null | undefined, issueNumber: string | null | undefined): string | null {
    const normalizedProjectKey = stringValue(projectKey);
    const normalizedIssueNumber = stringValue(issueNumber);
    if (!normalizedProjectKey || !normalizedIssueNumber) {
        return null;
    }
    return `${normalizedProjectKey}#${normalizedIssueNumber}`;
}

function looksLikeWorkflowId(value: string): boolean {
    const trimmed = stringValue(value);
    if (!trimmed) {
        return false;
    }
    if (parseIssueRef(trimmed) || parseBareIssue(trimmed)) {
        return false;
    }
    return /[-:_]/.test(trimmed) || /^[A-Za-z][A-Za-z0-9._-]{5,}$/.test(trimmed);
}

function normalizeIssueScopedArgs(
    command: string,
    args: string[],
    sessionState: SessionState,
    config: Required<PluginConfig>
): string[] {
    if (!command) {
        return [];
    }
    const normalized = [...args];
    if (normalized.length === 0) {
        return normalized;
    }
    const firstIssueRef = parseIssueRef(normalized[0]);
    if (firstIssueRef) {
        return [firstIssueRef.projectKey, firstIssueRef.issueNumber, ...normalized.slice(1)];
    }
    if (normalized.length >= 2) {
        const secondIssue = parseBareIssue(normalized[1]);
        if (secondIssue) {
            return [normalized[0], secondIssue, ...normalized.slice(2)];
        }
    }
    const firstIssue = parseBareIssue(normalized[0]);
    if (firstIssue && ISSUE_SCOPED_COMMANDS.has(command)) {
        const projectKey = sessionState.currentProject || config.defaultProject;
        if (projectKey) {
            return [projectKey, firstIssue, ...normalized.slice(1)];
        }
    }
    return normalized;
}

export function normalizeInvocationArgs(
    parsed: ParsedInvocation,
    sessionState: SessionState,
    config: Required<PluginConfig>
): ParsedInvocation {
    if (!parsed.command) {
        return parsed;
    }
    const normalizedArgs = normalizeIssueScopedArgs(parsed.command, parsed.args, sessionState, config);
    return {
        ...parsed,
        args: normalizedArgs
    };
}

function getSessionState(sessionId: string): SessionState {
    return (
        sessionStateStore.get(sessionId) ?? {
            currentProject: null,
            currentIssueRef: null,
            currentWorkflowId: null,
            affinitySessionKey: null,
            affinityBoundAt: null,
            lastCorrelationId: null,
            lastMessageId: null,
            lastThreadId: null
        }
    );
}

function setSessionState(sessionId: string, nextState: SessionState): void {
    sessionStateStore.set(sessionId, nextState);
}

export function buildWorkflowSessionKey(workflowId: string | null | undefined): string | null {
    const normalizedWorkflowId = stringValue(workflowId);
    return normalizedWorkflowId ? `nexus::workflow:${normalizedWorkflowId}` : null;
}

function buildFallbackSessionKey(sessionId: string): string {
    return `nexus::session:${stringValue(sessionId) || "unknown"}`;
}

export function bindWorkflowSessionAffinity(
    sessionId: string,
    state: SessionState,
    binding: {
        workflowId: string;
        issueRef?: string | null;
        projectKey?: string | null;
        correlationId?: string | null;
        messageId?: string | null;
        threadId?: string | null;
    }
): SessionAffinityBinding {
    const workflowId = stringValue(binding.workflowId);
    const sessionKey = buildWorkflowSessionKey(workflowId) || buildFallbackSessionKey(sessionId);
    const boundAt = Date.now();
    const nextBinding: SessionAffinityBinding = {
        workflowId,
        sessionId,
        sessionKey,
        issueRef: stringValue(binding.issueRef) || null,
        projectKey: stringValue(binding.projectKey) || null,
        boundAt,
        lastCorrelationId: stringValue(binding.correlationId) || null,
        lastMessageId: stringValue(binding.messageId) || null,
        lastThreadId: stringValue(binding.threadId) || null
    };
    workflowSessionBindings.set(workflowId, nextBinding);
    setSessionState(sessionId, {
        ...state,
        currentWorkflowId: workflowId || state.currentWorkflowId,
        currentIssueRef: nextBinding.issueRef || state.currentIssueRef,
        currentProject: nextBinding.projectKey || state.currentProject,
        affinitySessionKey: sessionKey,
        affinityBoundAt: boundAt,
        lastCorrelationId: nextBinding.lastCorrelationId,
        lastMessageId: nextBinding.lastMessageId,
        lastThreadId: nextBinding.lastThreadId
    });
    return nextBinding;
}

function getWorkflowSessionBinding(workflowId: string | null | undefined): SessionAffinityBinding | null {
    const normalizedWorkflowId = stringValue(workflowId);
    if (!normalizedWorkflowId) {
        return null;
    }
    return workflowSessionBindings.get(normalizedWorkflowId) ?? null;
}

export function buildReplyCorrelationId(input: {
    sourcePlatform?: string;
    sessionId?: string;
    workflowId?: string | null;
    command?: string;
    messageId?: string;
}): string {
    const parts = [
        stringValue(input.sourcePlatform) || "openclaw",
        stringValue(input.sessionId) || "session",
        stringValue(input.workflowId) || "unbound",
        stringValue(input.command) || "route",
        stringValue(input.messageId) || "message"
    ];
    return parts.join("::");
}

function normalizeAttachment(value: unknown): Record<string, unknown> {
    if (isRecord(value)) {
        return value;
    }
    return {value: String(value ?? "")};
}

export function getRequesterContext(
    ctx: CommandHandlerContext,
    config: Required<PluginConfig>
): RequesterPayload {
    const senderId = String(ctx.senderId ?? "");
    const channelName = String(ctx.channel ?? "");
    const channelId = String(ctx.channelId ?? "");
    const attachments = Array.isArray(ctx.attachments) ? ctx.attachments.map(normalizeAttachment) : [];
    const operatorId = buildScopedIdentity(
        [config.sourcePlatform, channelName || "unknown", senderId || channelId],
        `${config.sourcePlatform}:operator`
    );
    const authorizedSender =
        typeof ctx.isAuthorizedSender === "boolean" ? ctx.isAuthorizedSender : undefined;
    const requesterNexusId = authorizedSender
        ? buildScopedIdentity(
            [config.sourcePlatform, "user", senderId || operatorId],
            `${config.sourcePlatform}:nexus`
        )
        : "";
    const sessionId = buildScopedIdentity(
        [config.sourcePlatform, channelName || "unknown", channelId || senderId],
        operatorId
    );
    return {
        source_platform: config.sourcePlatform,
        nexus_id: requesterNexusId || undefined,
        auth_authority: authorizedSender ? config.sourcePlatform : undefined,
        operator_id: operatorId,
        sender_id: senderId,
        sender_name: String(ctx.senderName ?? ""),
        channel_id: channelId,
        channel_name: channelName,
        session_id: sessionId,
        is_authorized_sender: authorizedSender,
        roles: ["operator"],
        access_groups: [],
        metadata: {
            command_body: String(ctx.commandBody ?? ""),
            raw_args: String(ctx.args ?? ""),
            message_id: String(ctx.messageId ?? ""),
            thread_id: String(ctx.threadId ?? ""),
            attachments_count: attachments.length,
            attachments
        }
    };
}

export function inferCommandContext(
    parsed: ParsedInvocation,
    sessionState: SessionState,
    config: Required<PluginConfig>,
    sessionId = ""
): Record<string, unknown> {
    const boundWorkflow = getWorkflowSessionBinding(sessionState.currentWorkflowId);
    const context: Record<string, unknown> = {
        current_project: sessionState.currentProject || config.defaultProject || null,
        current_workflow_id: sessionState.currentWorkflowId,
        current_issue_ref: sessionState.currentIssueRef,
        metadata: {
            affinity: {
                mode: boundWorkflow ? "workflow-bound" : "session-fallback",
                session_key:
                    boundWorkflow?.sessionKey ||
                    sessionState.affinitySessionKey ||
                    buildFallbackSessionKey(sessionId),
                workflow_id: boundWorkflow?.workflowId || sessionState.currentWorkflowId || null,
                bound_at: boundWorkflow?.boundAt || sessionState.affinityBoundAt || null,
                last_correlation_id: sessionState.lastCorrelationId || null,
                last_message_id: sessionState.lastMessageId || null,
                last_thread_id: sessionState.lastThreadId || null
            }
        }
    };
    const firstArg = parsed.args[0] ?? "";
    const secondArg = parsed.args[1] ?? "";
    const firstIssueRef = parseIssueRef(firstArg);
    if (firstIssueRef) {
        context.current_project = firstIssueRef.projectKey;
        context.current_issue_ref = firstIssueRef.issueRef;
        return context;
    }
    const secondIssue = parseBareIssue(secondArg);
    if (firstArg && secondIssue) {
        context.current_project = firstArg;
        context.current_issue_ref = `${firstArg}#${secondIssue}`;
        return context;
    }
    if (parsed.command === "wfstate" && looksLikeWorkflowId(firstArg)) {
        context.current_workflow_id = firstArg;
        return context;
    }
    if (firstArg && parsed.command === "status") {
        context.current_project = firstArg;
    }
    return context;
}

function isBoundedBridgeCommand(command: string, capabilities?: BridgeCapabilitiesPayload): boolean {
    return new Set(normalizeSupportedCommands(capabilities)).has(command);
}

function renderCurrentState(sessionState: SessionState, config: Required<PluginConfig>): string {
    const lines = ["Nexus ARC session context:"];
    lines.push(`Project: ${sessionState.currentProject || config.defaultProject || "(unset)"}`);
    lines.push(`Issue: ${sessionState.currentIssueRef || "(unset)"}`);
    lines.push(`Workflow: ${sessionState.currentWorkflowId || "(unset)"}`);
    lines.push(`Affinity: ${sessionState.affinitySessionKey || "(session-fallback)"}`);
    if (sessionState.lastCorrelationId) {
        lines.push(`Last Correlation: ${sessionState.lastCorrelationId}`);
    }
    return lines.join("\n");
}

function handleLocalCommand(
    parsed: ParsedInvocation,
    sessionState: SessionState,
    sessionId: string,
    config: Required<PluginConfig>
): CommandResponse | null {
    if (parsed.command === "current") {
        return {text: renderCurrentState(sessionState, config)};
    }
    if (parsed.command === "use") {
        const nextProject = stringValue(parsed.args[0]);
        if (!nextProject) {
            return {text: "Usage: /nexus use <project>"};
        }
        const nextState: SessionState = {
            ...sessionState,
            currentProject: nextProject,
            currentIssueRef: null,
            currentWorkflowId: null,
            affinitySessionKey: sessionState.affinitySessionKey || buildFallbackSessionKey(sessionId)
        };
        if (config.sessionMemory) {
            setSessionState(sessionId, nextState);
        }
        return {text: renderCurrentState(nextState, config)};
    }
    if (parsed.command === "cancel") {
        pendingConfirmations.delete(sessionId);
        return {text: "Canceled pending Nexus ARC confirmation."};
    }
    return null;
}

function isRiskyCommand(command: string, config: Required<PluginConfig>): boolean {
    return new Set(config.requireConfirmFor).has(command);
}

function buildBridgeRequest(
    parsed: ParsedInvocation,
    requester: RequesterPayload,
    context: Record<string, unknown>,
    client: Record<string, unknown>,
    attachments: unknown[],
    capabilities?: BridgeCapabilitiesPayload
): PendingConfirmation {
    const bounded = parsed.command && isBoundedBridgeCommand(parsed.command, capabilities);
    const correlationId = buildReplyCorrelationId({
        sourcePlatform: requester.source_platform,
        sessionId: requester.session_id,
        workflowId: stringValue(context.current_workflow_id),
        command: parsed.command,
        messageId: requester.metadata.message_id
    });
    return bounded
        ? {
            path: "/api/v1/commands/execute",
            payload: {
                command: parsed.command,
                args: parsed.args,
                raw_text: parsed.freeform,
                requester,
                context,
                client,
                attachments,
                correlation_id: correlationId
            },
            summary: `${parsed.command} ${parsed.args.join(" ")}`.trim(),
            createdAt: Date.now()
        }
        : {
            path: "/api/v1/commands/route",
            payload: {
                raw_text: parsed.freeform,
                requester,
                context,
                client,
                attachments,
                correlation_id: correlationId
            },
            summary: parsed.freeform,
            createdAt: Date.now()
        };
}

function updateSessionStateFromResult(
    sessionId: string,
    currentState: SessionState,
    context: Record<string, unknown>,
    result: NexusCommandResult,
    config: Required<PluginConfig>
): void {
    if (!config.sessionMemory) {
        return;
    }
    const workflowProjectKey = stringValue(result.workflow?.project_key);
    const flatProjectKey = stringValue(result.project_key);
    const nextProject =
        workflowProjectKey ||
        flatProjectKey ||
        stringValue(context.current_project) ||
        currentState.currentProject ||
        config.defaultProject ||
        null;
    const workflowIssueNumber = stringValue(result.workflow?.issue_number);
    const flatIssueNumber = stringValue(result.issue_number);
    const nextIssueRef =
        buildIssueRef(nextProject, workflowIssueNumber || flatIssueNumber) ||
        stringValue(context.current_issue_ref) ||
        currentState.currentIssueRef;
    const nextWorkflowId =
        stringValue(result.workflow?.id) ||
        stringValue(result.workflow_id) ||
        stringValue(context.current_workflow_id) ||
        currentState.currentWorkflowId;
    const nextState: SessionState = {
        ...currentState,
        currentProject: nextProject,
        currentIssueRef: nextIssueRef || null,
        currentWorkflowId: nextWorkflowId || null,
        lastCorrelationId:
            stringValue(result.audit?.request_id) || currentState.lastCorrelationId || null
    };
    setSessionState(sessionId, nextState);
    if (nextWorkflowId) {
        bindWorkflowSessionAffinity(sessionId, nextState, {
            workflowId: nextWorkflowId,
            issueRef: nextIssueRef,
            projectKey: nextProject,
            correlationId: stringValue(result.audit?.request_id) || currentState.lastCorrelationId,
            messageId: currentState.lastMessageId,
            threadId: currentState.lastThreadId
        });
    }
}

function renderHelpText(capabilities?: BridgeCapabilitiesPayload): string {
    const supported = normalizeSupportedCommands(capabilities);
    return [
        "Nexus ARC bridge commands:",
        supported.join(", "),
        "",
        "Local session commands:",
        [...LOCAL_COMMANDS].join(", "),
        "",
        "Examples:",
        "/nexus current",
        "/nexus use demo",
        "/nexus chat",
        '/nexus chat "Review the current workspace architecture and call out risks."',
        "/nexus chatagents demo",
        "/nexus usage demo#42",
        "/nexus usage #42",
        "/nexus health",
        "/nexus refresh",
        "/nexus status demo",
        '/nexus new demo "investigate agent launch retries"',
        "/nexus plan demo 42",
        "/nexus implement demo#42",
        "/nexus reconcile demo#42",
        "/nexus reprocess demo#42",
        "/nexus kill demo#42 --yes",
        "/nexus wfstate demo-42-full",
        "/nexus show me the workflow state for demo#42",
        "",
        "You can also use freeform requests and the plugin will try to route them.",
        "Risky commands can require /nexus confirm unless you add --yes."
    ].join("\n");
}

function summarizeConfigWarnings(config: Required<PluginConfig>): string[] {
    const warnings: string[] = [];
    if (!config.bridgeUrl) {
        warnings.push("Bridge URL is not configured.");
    }
    if (!config.authToken) {
        warnings.push("Bridge auth token is not configured.");
    }
    return warnings;
}

function maybeWarnConfig(config: Required<PluginConfig>): void {
    const warnings = summarizeConfigWarnings(config);
    if (warnings.length === 0) {
        return;
    }
    const cacheKey = `${config.bridgeUrl}|${config.authToken ? "token" : "no-token"}`;
    if (warnedConfigKeys.has(cacheKey)) {
        return;
    }
    warnedConfigKeys.add(cacheKey);
    console.warn(`[${PLUGIN_ID}] ${warnings.join(" ")}`);
}

function safeJsonParse(value: string): unknown {
    try {
        return JSON.parse(value);
    } catch {
        return {};
    }
}

async function fetchJson(
    path: string,
    config: Required<PluginConfig>,
    init: {
        method: "GET" | "POST";
        body?: Record<string, unknown>;
    }
): Promise<{ status: number; payload: Record<string, unknown> }> {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), config.timeoutMs);

    try {
        const headers: Record<string, string> = {};
        if (init.body) {
            headers["content-type"] = "application/json";
        }
        if (config.authToken) {
            headers.authorization = `Bearer ${config.authToken}`;
        }
        Object.assign(headers, buildRequestReplayHeaders());
        let response: Response;
        try {
            response = await fetch(`${config.bridgeUrl}${path}`, {
                method: init.method,
                headers,
                body: init.body ? JSON.stringify(init.body) : undefined,
                signal: controller.signal
            });
        } catch (error) {
            if (error instanceof Error && error.name === "AbortError") {
                throw new BridgeRequestError(
                    `Nexus bridge timed out after ${config.timeoutMs} ms.`,
                    {code: "bridge_timeout", cause: error}
                );
            }
            throw new BridgeRequestError(
                "Could not reach the Nexus bridge. Check the bridge URL and network path.",
                {code: "bridge_unreachable", cause: error}
            );
        }

        const responseText = await response.text();
        const parsed = responseText ? safeJsonParse(responseText) : {};
        const payload = isRecord(parsed) ? parsed : {};
        if (!response.ok) {
            const errorMessage =
                typeof payload.error === "string" && payload.error
                    ? payload.error
                    : `HTTP ${response.status}`;
            throw new BridgeRequestError(errorMessage, {
                status: response.status,
                code: stringValue(payload.error_code) || `http_${response.status}`
            });
        }
        return {status: response.status, payload};
    } finally {
        clearTimeout(timer);
    }
}

async function callBridge(
    path: string,
    payload: Record<string, unknown>,
    config: Required<PluginConfig>
): Promise<NexusCommandResult> {
    const response = await fetchJson(path, config, {method: "POST", body: payload});
    if (typeof response.payload.message !== "string") {
        throw new BridgeRequestError("Bridge returned an invalid response.", {code: "invalid_bridge_response"});
    }
    return response.payload as NexusCommandResult;
}

async function callBridgeGet(
    path: string,
    config: Required<PluginConfig>
): Promise<Record<string, unknown>> {
    const response = await fetchJson(path, config, {method: "GET"});
    return response.payload;
}

async function getBridgeCapabilities(config: Required<PluginConfig>): Promise<BridgeCapabilitiesPayload | null> {
    const cacheKey = `${config.bridgeUrl}|${config.authToken}`;
    if (capabilitiesCache.has(cacheKey)) {
        return capabilitiesCache.get(cacheKey) ?? null;
    }
    try {
        const payload = await callBridgeGet("/api/v1/capabilities", config);
        const capabilities = payload as BridgeCapabilitiesPayload;
        capabilitiesCache.set(cacheKey, capabilities);
        return capabilities;
    } catch {
        return null;
    }
}

async function getBridgeHealth(config: Required<PluginConfig>): Promise<Record<string, unknown>> {
    return callBridgeGet("/healthz", config);
}

export function formatBridgeError(error: unknown): string {
    if (error instanceof BridgeRequestError) {
        if (error.code === "missing_bearer_token" || error.code === "invalid_bearer_token") {
            return "Nexus bridge authentication failed. Check the configured bearer token.";
        }
        if (error.code === "sender_not_allowed" || error.code === "source_not_allowed") {
            return `Nexus bridge denied this sender: ${error.message}`;
        }
        if (error.code === "bridge_timeout") {
            return error.message;
        }
        if (error.code === "bridge_unreachable") {
            return error.message;
        }
        if (error.code === "unsupported_command") {
            return `Nexus bridge did not accept that command: ${error.message}`;
        }
        return `Nexus bridge error: ${error.message}`;
    }
    if (error instanceof Error) {
        return `Nexus bridge error: ${error.message}`;
    }
    return "Nexus bridge error: Unknown failure.";
}

function renderResult(result: NexusCommandResult): string {
    const summary = stringValue(result.ui?.summary) || result.message;
    const lines: string[] = [summary];
    const title = stringValue(result.ui?.title);
    if (title && title !== summary) {
        lines.unshift(title);
    }
    const workflowId = stringValue(result.workflow?.id) || String(result.workflow_id ?? "").trim();
    if (workflowId) {
        lines.push(`Workflow: ${workflowId}`);
    }
    if (Array.isArray(result.ui?.fields)) {
        for (const field of result.ui.fields) {
            const label = stringValue(field?.label);
            const value = stringValue(field?.value);
            if (label && value) {
                lines.push(`${label}: ${value}`);
            }
        }
    }
    if (isRecord(result.usage)) {
        const provider = stringValue(result.usage.provider);
        const model = stringValue(result.usage.model);
        const inputTokens =
            typeof result.usage.input_tokens === "number" ? String(result.usage.input_tokens) : "";
        const outputTokens =
            typeof result.usage.output_tokens === "number" ? String(result.usage.output_tokens) : "";
        const estimatedCost =
            typeof result.usage.estimated_cost_usd === "number"
                ? result.usage.estimated_cost_usd.toFixed(4)
                : "";
        if (provider || model || inputTokens || outputTokens || estimatedCost) {
            lines.push("Usage:");
        }
        if (provider) {
            lines.push(`Provider: ${provider}`);
        }
        if (model) {
            lines.push(`Model: ${model}`);
        }
        if (inputTokens) {
            lines.push(`Input Tokens: ${inputTokens}`);
        }
        if (outputTokens) {
            lines.push(`Output Tokens: ${outputTokens}`);
        }
        if (estimatedCost) {
            lines.push(`Estimated Cost USD: ${estimatedCost}`);
        }
    }
    if (Array.isArray(result.suggested_next_commands) && result.suggested_next_commands.length > 0) {
        lines.push(`Next: ${result.suggested_next_commands.join(" | ")}`);
    } else if (Array.isArray(result.ui?.actions) && result.ui.actions.length > 0) {
        lines.push(`Next: ${result.ui.actions.join(" | ")}`);
    }
    return lines.filter(Boolean).join("\n");
}

function renderWorkflowStatus(payload: WorkflowStatusPayload): string {
    const lines = ["Workflow status:"];
    const workflowId = stringValue(payload.workflow_id);
    const issueNumber = stringValue(payload.issue_number);
    const projectKey = stringValue(payload.project_key);
    if (workflowId) {
        lines.push(`Workflow: ${workflowId}`);
    }
    if (projectKey) {
        lines.push(`Project: ${projectKey}`);
    }
    if (issueNumber) {
        lines.push(`Issue: ${issueNumber}`);
    }
    if (isRecord(payload.status)) {
        for (const [key, rawValue] of Object.entries(payload.status)) {
            const value = stringValue(
                typeof rawValue === "string" || typeof rawValue === "number" ? String(rawValue) : ""
            );
            if (value) {
                lines.push(`${key.replace(/_/g, " ")}: ${value}`);
            }
        }
    }
    if (isRecord(payload.usage)) {
        const provider = stringValue(payload.usage.provider);
        const model = stringValue(payload.usage.model);
        if (provider || model) {
            lines.push("Usage:");
        }
        if (provider) {
            lines.push(`Provider: ${provider}`);
        }
        if (model) {
            lines.push(`Model: ${model}`);
        }
        if (typeof payload.usage.input_tokens === "number") {
            lines.push(`Input Tokens: ${payload.usage.input_tokens}`);
        }
        if (typeof payload.usage.output_tokens === "number") {
            lines.push(`Output Tokens: ${payload.usage.output_tokens}`);
        }
        if (typeof payload.usage.estimated_cost_usd === "number") {
            lines.push(`Estimated Cost USD: ${payload.usage.estimated_cost_usd.toFixed(4)}`);
        }
    }
    return lines.join("\n");
}

function renderHealthText(
    config: Required<PluginConfig>,
    healthPayload: Record<string, unknown>,
    capabilities: BridgeCapabilitiesPayload | null
): string {
    const warnings = summarizeConfigWarnings(config);
    const lines = ["Nexus bridge health:"];
    lines.push(`Bridge URL: ${config.bridgeUrl}`);
    lines.push(`HTTP: ${healthPayload.ok === true ? "ok" : "unexpected"}`);
    lines.push(`Auth token configured: ${config.authToken ? "yes" : "no"}`);
    if (Array.isArray(capabilities?.supported_commands) && capabilities.supported_commands.length > 0) {
        lines.push(`Supported commands: ${capabilities.supported_commands.join(", ")}`);
    }
    if (warnings.length > 0) {
        lines.push(`Warnings: ${warnings.join(" ")}`);
    }
    return lines.join("\n");
}

function sleep(ms: number): Promise<void> {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

async function maybePollAcceptedWorkflow(
    result: NexusCommandResult,
    config: Required<PluginConfig>
): Promise<string> {
    const baseText = renderResult(result);
    if (!config.autoPollAccepted || result.status !== "accepted") {
        return baseText;
    }
    const workflowId = stringValue(result.workflow?.id) || stringValue(result.workflow_id);
    if (!workflowId) {
        return baseText;
    }
    const attempts = Math.max(0, Math.trunc(config.acceptedPollAttempts));
    for (let attempt = 0; attempt < attempts; attempt += 1) {
        if (config.acceptedPollDelayMs > 0) {
            await sleep(config.acceptedPollDelayMs);
        }
        try {
            const payload = (await callBridgeGet(
                `/api/v1/workflows/${encodeURIComponent(workflowId)}`,
                config
            )) as WorkflowStatusPayload;
            if (payload.ok) {
                return `${baseText}\n\n${renderWorkflowStatus(payload)}`;
            }
        } catch {
            break;
        }
    }
    return baseText;
}

async function handleNexusCommand(ctx: CommandHandlerContext): Promise<CommandResponse> {
    const config = resolvePluginConfig(ctx);
    maybeWarnConfig(config);
    const capabilities = await getBridgeCapabilities(config);
    const requester = getRequesterContext(ctx, config);
    const sessionState = {
        ...getSessionState(requester.session_id),
        lastMessageId: stringValue(requester.metadata.message_id) || getSessionState(requester.session_id).lastMessageId,
        lastThreadId: stringValue(requester.metadata.thread_id) || getSessionState(requester.session_id).lastThreadId
    };
    setSessionState(requester.session_id, sessionState);
    const parsed = normalizeInvocationArgs(parseNexusInvocation(ctx.args, capabilities ?? undefined), sessionState, config);

    if (!parsed.command && !parsed.freeform) {
        return {text: renderHelpText(capabilities ?? undefined)};
    }

    if (parsed.command === "help") {
        return {text: renderHelpText(capabilities ?? undefined)};
    }

    if (parsed.command === "health") {
        try {
            const healthPayload = await getBridgeHealth(config);
            return {text: renderHealthText(config, healthPayload, capabilities)};
        } catch (error) {
            return {text: formatBridgeError(error)};
        }
    }

    if (parsed.command === "confirm") {
        const pending = pendingConfirmations.get(requester.session_id);
        if (!pending) {
            return {text: "There is no pending Nexus ARC confirmation."};
        }
        if (Date.now() - pending.createdAt > CONFIRMATION_TTL_MS) {
            pendingConfirmations.delete(requester.session_id);
            return {text: "The pending Nexus ARC confirmation expired. Re-run the command."};
        }
        pendingConfirmations.delete(requester.session_id);
        try {
            const confirmedResult = await callBridge(pending.path, pending.payload, config);
            updateSessionStateFromResult(
                requester.session_id,
                sessionState,
                (pending.payload.context as Record<string, unknown>) ?? {},
                confirmedResult,
                config
            );
            return {text: await maybePollAcceptedWorkflow(confirmedResult, config)};
        } catch (error) {
            return {text: formatBridgeError(error)};
        }
    }

    if (parsed.command === "refresh") {
        const workflowId = sessionState.currentWorkflowId || stringValue(parsed.args[0]);
        if (workflowId) {
            try {
                const statusPayload = (await callBridgeGet(
                    `/api/v1/workflows/${encodeURIComponent(workflowId)}`,
                    config
                )) as WorkflowStatusPayload;
                return {text: renderWorkflowStatus(statusPayload)};
            } catch (error) {
                return {text: formatBridgeError(error)};
            }
        }
        const fallbackIssueRef = sessionState.currentIssueRef || stringValue(parsed.args[0]);
        if (!fallbackIssueRef) {
            return {
                text:
                    "Usage: /nexus refresh\nRun it after /nexus plan, /nexus status, /nexus usage, or /nexus use <project>."
            };
        }
        const refreshParsed = normalizeInvocationArgs(
            {
                command: "wfstate",
                args: [fallbackIssueRef],
                freeform: `wfstate ${fallbackIssueRef}`,
                explicitCommand: true
            },
            sessionState,
            config
        );
        const refreshContext = inferCommandContext(refreshParsed, sessionState, config, requester.session_id);
        try {
            const refreshResult = await callBridge(
                "/api/v1/commands/execute",
                {
                    command: "wfstate",
                    args: refreshParsed.args,
                    raw_text: refreshParsed.freeform,
                    requester,
                    context: refreshContext,
                    client: {
                        plugin_version: PLUGIN_VERSION,
                        render_mode: config.renderMode
                    },
                    attachments: Array.isArray(ctx.attachments) ? ctx.attachments : []
                },
                config
            );
            updateSessionStateFromResult(
                requester.session_id,
                sessionState,
                refreshContext,
                refreshResult,
                config
            );
            return {text: renderResult(refreshResult)};
        } catch (error) {
            return {text: formatBridgeError(error)};
        }
    }

    if (new Set(LOCAL_COMMANDS).has(parsed.command as (typeof LOCAL_COMMANDS)[number])) {
        return handleLocalCommand(parsed, sessionState, requester.session_id, config) as CommandResponse;
    }

    if (parsed.command === "wfstate" && looksLikeWorkflowId(parsed.args[0] ?? "")) {
        try {
            const payload = (await callBridgeGet(
                `/api/v1/workflows/${encodeURIComponent(parsed.args[0])}`,
                config
            )) as WorkflowStatusPayload;
            if (!payload.ok) {
                return {text: payload.error || `Workflow '${parsed.args[0]}' was not found.`};
            }
            if (config.sessionMemory) {
                const nextState: SessionState = {
                    ...sessionState,
                    currentProject: stringValue(payload.project_key) || sessionState.currentProject,
                    currentIssueRef:
                        buildIssueRef(stringValue(payload.project_key), stringValue(payload.issue_number)) ||
                        sessionState.currentIssueRef,
                    currentWorkflowId: stringValue(payload.workflow_id) || sessionState.currentWorkflowId
                };
                setSessionState(requester.session_id, nextState);
                if (nextState.currentWorkflowId) {
                    bindWorkflowSessionAffinity(requester.session_id, nextState, {
                        workflowId: nextState.currentWorkflowId,
                        issueRef: nextState.currentIssueRef,
                        projectKey: nextState.currentProject,
                        correlationId: sessionState.lastCorrelationId,
                        messageId: nextState.lastMessageId,
                        threadId: nextState.lastThreadId
                    });
                }
            }
            return {text: renderWorkflowStatus(payload)};
        } catch (error) {
            return {text: formatBridgeError(error)};
        }
    }

    const context = inferCommandContext(parsed, sessionState, config, requester.session_id);
    const client = {
        plugin_version: PLUGIN_VERSION,
        render_mode: config.renderMode,
        metadata: {
            source_plugin: PLUGIN_ID
        }
    };
    const yesArgs = parsed.args.filter((arg) => arg !== "--yes");
    const normalizedParsed =
        yesArgs.length === parsed.args.length
            ? parsed
            : {
                ...parsed,
                args: yesArgs,
                freeform:
                    parsed.explicitCommand
                        ? [parsed.command, ...yesArgs].filter(Boolean).join(" ")
                        : parsed.freeform
            };
    const request = buildBridgeRequest(
        normalizedParsed,
        requester,
        context,
        client,
        Array.isArray(ctx.attachments) ? ctx.attachments : [],
        capabilities ?? undefined
    );

    if (
        normalizedParsed.command &&
        isRiskyCommand(normalizedParsed.command, config) &&
        yesArgs.length === parsed.args.length
    ) {
        pendingConfirmations.set(requester.session_id, request);
        return {
            text: [
                `Confirmation required for \`${request.summary}\`.`,
                "Run /nexus confirm to continue, /nexus cancel to abort, or re-run with --yes."
            ].join("\n")
        };
    }

    try {
        const result = await callBridge(request.path, request.payload, config);
        updateSessionStateFromResult(requester.session_id, sessionState, context, result, config);
        return {text: await maybePollAcceptedWorkflow(result, config)};
    } catch (error) {
        return {text: formatBridgeError(error)};
    }
}

function buildExplicitInvocation(command: string, rawArgs: string | undefined): ParsedInvocation {
    const args = tokenizeInput(rawArgs);
    return {
        command,
        args,
        freeform: [command, ...args].filter(Boolean).join(" "),
        explicitCommand: true
    };
}

async function handleExplicitBridgeCommand(
    ctx: CommandHandlerContext,
    command: "chat" | "chatagents"
): Promise<CommandResponse> {
    const config = resolvePluginConfig(ctx);
    maybeWarnConfig(config);
    const capabilities = await getBridgeCapabilities(config);
    const requester = getRequesterContext(ctx, config);
    const sessionState = {
        ...getSessionState(requester.session_id),
        lastMessageId: stringValue(requester.metadata.message_id) || getSessionState(requester.session_id).lastMessageId,
        lastThreadId: stringValue(requester.metadata.thread_id) || getSessionState(requester.session_id).lastThreadId
    };
    setSessionState(requester.session_id, sessionState);
    const parsed = normalizeInvocationArgs(buildExplicitInvocation(command, ctx.args), sessionState, config);
    const context = inferCommandContext(parsed, sessionState, config, requester.session_id);
    const client = {
        plugin_version: PLUGIN_VERSION,
        render_mode: config.renderMode,
        metadata: {
            source_plugin: PLUGIN_ID
        }
    };
    const request = buildBridgeRequest(
        parsed,
        requester,
        context,
        client,
        Array.isArray(ctx.attachments) ? ctx.attachments : [],
        capabilities ?? undefined
    );
    try {
        const result = await callBridge(request.path, request.payload, config);
        updateSessionStateFromResult(requester.session_id, sessionState, context, result, config);
        return {text: await maybePollAcceptedWorkflow(result, config)};
    } catch (error) {
        return {text: formatBridgeError(error)};
    }
}

const plugin = {
    id: PLUGIN_ID,
    name: "Nexus ARC Command Bridge",
    configSchema: {
        type: "object",
        additionalProperties: false,
        properties: {
            bridgeUrl: {type: "string"},
            authToken: {type: "string"},
            timeoutMs: {type: "integer"},
            sourcePlatform: {type: "string"},
            defaultProject: {type: "string"},
            renderMode: {type: "string"},
            sessionMemory: {type: "boolean"},
            requireConfirmFor: {
                type: "array",
                items: {type: "string"}
            },
            autoPollAccepted: {type: "boolean"},
            acceptedPollDelayMs: {type: "integer"},
            acceptedPollAttempts: {type: "integer"}
        }
    },
    register(api: RegisterCommandApi): void {
        api.registerCommand({
            name: "nexus",
            description: "Forward commands to the Nexus ARC command bridge",
            acceptsArgs: true,
            requireAuth: true,
            handler: handleNexusCommand
        });
        api.registerCommand({
            name: "chat",
            description: "Run a Nexus ARC workspace-aware chat turn",
            acceptsArgs: true,
            requireAuth: true,
            handler: (ctx) => handleExplicitBridgeCommand(ctx, "chat")
        });
        api.registerCommand({
            name: "chatagents",
            description: "List Nexus ARC workspace chat agents",
            acceptsArgs: true,
            requireAuth: true,
            handler: (ctx) => handleExplicitBridgeCommand(ctx, "chatagents")
        });
    }
};

export default plugin;
