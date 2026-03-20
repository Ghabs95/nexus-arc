const PLUGIN_ID = "nexus-arc";
const PLUGIN_VERSION = "0.1.0";

type PluginConfig = {
  bridgeUrl?: string;
  authToken?: string;
  timeoutMs?: number;
  sourcePlatform?: string;
  defaultProject?: string;
  renderMode?: string;
  sessionMemory?: boolean;
  requireConfirmFor?: string[];
};

type CommandHandlerContext = {
  senderId?: string;
  senderName?: string;
  channel?: string;
  channelId?: string;
  isAuthorizedSender?: boolean;
  args?: string;
  commandBody?: string;
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
  };
};

type SessionState = {
  currentProject: string | null;
  currentIssueRef: string | null;
  currentWorkflowId: string | null;
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
};

const SUPPORTED_COMMANDS = [
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
  "agents",
  "audit",
  "stats"
] as const;

const LOCAL_COMMANDS = ["current", "use", "confirm", "cancel", "refresh"] as const;
const CONFIRMATION_TTL_MS = 2 * 60 * 1000;
const sessionStateStore = new Map<string, SessionState>();
const pendingConfirmations = new Map<string, PendingConfirmation>();

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
        : ["implement", "respond", "stop"]
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

function tokenizeArgs(rawArgs: string | undefined): string[] {
  return String(rawArgs ?? "")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
}

function parseNexusInvocation(rawArgs: string | undefined): {
  command: string;
  args: string[];
  freeform: string;
} {
  const freeform = String(rawArgs ?? "").trim();
  const tokens = tokenizeArgs(rawArgs);
  if (tokens.length === 0) {
    return { command: "", args: [], freeform: "" };
  }
  const [command, ...args] = tokens;
  return {
    command: command.toLowerCase(),
    args,
    freeform
  };
}

function buildScopedIdentity(parts: Array<string | undefined>, fallback: string): string {
  const value = parts
    .map((part) => String(part ?? "").trim())
    .filter(Boolean)
    .join(":");
  return value || fallback;
}

function parseIssueRef(value: string): { projectKey: string; issueRef: string } | null {
  const trimmed = String(value ?? "").trim();
  const match = /^([A-Za-z0-9_.-]+)#(.+)$/.exec(trimmed);
  if (!match) {
    return null;
  }
  return {
    projectKey: match[1],
    issueRef: trimmed
  };
}

function buildIssueRef(projectKey: string | null | undefined, issueNumber: string | null | undefined): string | null {
  const normalizedProjectKey = stringValue(projectKey);
  const normalizedIssueNumber = stringValue(issueNumber);
  if (!normalizedProjectKey || !normalizedIssueNumber) {
    return null;
  }
  return `${normalizedProjectKey}#${normalizedIssueNumber}`;
}

function getSessionState(sessionId: string): SessionState {
  return (
    sessionStateStore.get(sessionId) ?? {
      currentProject: null,
      currentIssueRef: null,
      currentWorkflowId: null
    }
  );
}

function setSessionState(sessionId: string, nextState: SessionState): void {
  sessionStateStore.set(sessionId, nextState);
}

function getRequesterContext(
  ctx: CommandHandlerContext,
  config: Required<PluginConfig>
): RequesterPayload {
  const senderId = String(ctx.senderId ?? "");
  const channelName = String(ctx.channel ?? "");
  const channelId = String(ctx.channelId ?? "");
  const operatorId = buildScopedIdentity(
    [config.sourcePlatform, channelName || "unknown", senderId || channelId],
    `${config.sourcePlatform}:operator`
  );
  const sessionId = buildScopedIdentity(
    [config.sourcePlatform, channelName || "unknown", channelId || senderId],
    operatorId
  );
  return {
    source_platform: config.sourcePlatform,
    operator_id: operatorId,
    sender_id: senderId,
    sender_name: String(ctx.senderName ?? ""),
    channel_id: channelId,
    channel_name: channelName,
    session_id: sessionId,
    is_authorized_sender:
      typeof ctx.isAuthorizedSender === "boolean" ? ctx.isAuthorizedSender : undefined,
    roles: ["operator"],
    access_groups: [],
    metadata: {
      command_body: String(ctx.commandBody ?? "")
    }
  };
}

function inferCommandContext(
  parsed: ReturnType<typeof parseNexusInvocation>,
  sessionState: SessionState,
  config: Required<PluginConfig>
): Record<string, unknown> {
  const context: Record<string, unknown> = {
    current_project: sessionState.currentProject || config.defaultProject || null,
    current_workflow_id: sessionState.currentWorkflowId,
    current_issue_ref: sessionState.currentIssueRef
  };
  const firstArg = parsed.args[0] ?? "";
  const issueRef = parseIssueRef(firstArg);
  if (issueRef) {
    context.current_project = issueRef.projectKey;
    context.current_issue_ref = issueRef.issueRef;
    return context;
  }
  if (firstArg && parsed.command === "status") {
    context.current_project = firstArg;
  }
  return context;
}

function isBoundedBridgeCommand(command: string): boolean {
  return new Set(SUPPORTED_COMMANDS).has(command as (typeof SUPPORTED_COMMANDS)[number]);
}

function renderCurrentState(sessionState: SessionState, config: Required<PluginConfig>): string {
  const lines = ["Nexus ARC session context:"];
  lines.push(`Project: ${sessionState.currentProject || config.defaultProject || "(unset)"}`);
  lines.push(`Issue: ${sessionState.currentIssueRef || "(unset)"}`);
  lines.push(`Workflow: ${sessionState.currentWorkflowId || "(unset)"}`);
  return lines.join("\n");
}

function handleLocalCommand(
  parsed: ReturnType<typeof parseNexusInvocation>,
  sessionState: SessionState,
  sessionId: string,
  config: Required<PluginConfig>
): CommandResponse | null {
  if (parsed.command === "current") {
    return { text: renderCurrentState(sessionState, config) };
  }
  if (parsed.command === "use") {
    const nextProject = stringValue(parsed.args[0]);
    if (!nextProject) {
      return { text: "Usage: /nexus use <project>" };
    }
    const nextState: SessionState = {
      currentProject: nextProject,
      currentIssueRef: null,
      currentWorkflowId: null
    };
    if (config.sessionMemory) {
      setSessionState(sessionId, nextState);
    }
    return { text: renderCurrentState(nextState, config) };
  }
  if (parsed.command === "cancel") {
    pendingConfirmations.delete(sessionId);
    return { text: "Canceled pending Nexus ARC confirmation." };
  }
  return null;
}

function isRiskyCommand(
  command: string,
  config: Required<PluginConfig>
): boolean {
  return new Set(config.requireConfirmFor).has(command);
}

function buildBridgeRequest(
  parsed: ReturnType<typeof parseNexusInvocation>,
  requester: RequesterPayload,
  context: Record<string, unknown>,
  client: Record<string, unknown>
): PendingConfirmation {
  const bounded = parsed.command && isBoundedBridgeCommand(parsed.command);
  return bounded
    ? {
        path: "/api/v1/commands/execute",
        payload: {
          command: parsed.command,
          args: parsed.args,
          raw_text: parsed.freeform,
          requester,
          context,
          client
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
          client
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
  setSessionState(sessionId, {
    currentProject: nextProject,
    currentIssueRef: nextIssueRef || null,
    currentWorkflowId: nextWorkflowId || null
  });
}

function renderHelpText(): string {
  return [
    "Nexus ARC bridge commands:",
    SUPPORTED_COMMANDS.join(", "),
    "",
    "Local session commands:",
    LOCAL_COMMANDS.join(", "),
    "",
    "Examples:",
    "/nexus current",
    "/nexus use demo",
    "/nexus usage demo#42",
    "/nexus refresh",
    "/nexus status demo",
    "/nexus new demo investigate agent launch retries",
    "/nexus plan demo#42",
    "/nexus implement demo#42",
    "/nexus logs demo#42",
    "/nexus show me the workflow state for demo#42",
    "",
    "You can also use freeform requests and the plugin will try to route them.",
    "Risky commands can require /nexus confirm unless you add --yes."
  ].join("\n");
}

async function callBridge(
  path: string,
  payload: Record<string, unknown>,
  config: Required<PluginConfig>
): Promise<NexusCommandResult> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);

  try {
    const headers: Record<string, string> = {
      "content-type": "application/json"
    };
    if (config.authToken) {
      headers.authorization = `Bearer ${config.authToken}`;
    }

    const response = await fetch(`${config.bridgeUrl}${path}`, {
      method: "POST",
      headers,
      body: JSON.stringify(payload),
      signal: controller.signal
    });

    const responseText = await response.text();
    const parsed = responseText ? safeJsonParse(responseText) : {};

    if (!response.ok) {
      const errorMessage =
        isRecord(parsed) && typeof parsed.error === "string"
          ? parsed.error
          : `HTTP ${response.status}`;
      throw new Error(errorMessage);
    }

    if (!isRecord(parsed) || typeof parsed.message !== "string") {
      throw new Error("Bridge returned an invalid response");
    }

    return parsed as NexusCommandResult;
  } finally {
    clearTimeout(timer);
  }
}

async function callBridgeGet(
  path: string,
  config: Required<PluginConfig>
): Promise<WorkflowStatusPayload> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), config.timeoutMs);

  try {
    const headers: Record<string, string> = {};
    if (config.authToken) {
      headers.authorization = `Bearer ${config.authToken}`;
    }

    const response = await fetch(`${config.bridgeUrl}${path}`, {
      method: "GET",
      headers,
      signal: controller.signal
    });
    const responseText = await response.text();
    const parsed = responseText ? safeJsonParse(responseText) : {};

    if (!response.ok) {
      const errorMessage =
        isRecord(parsed) && typeof parsed.error === "string"
          ? parsed.error
          : `HTTP ${response.status}`;
      throw new Error(errorMessage);
    }

    if (!isRecord(parsed)) {
      throw new Error("Bridge returned an invalid workflow status response");
    }
    return parsed as WorkflowStatusPayload;
  } finally {
    clearTimeout(timer);
  }
}

function safeJsonParse(value: string): unknown {
  try {
    return JSON.parse(value);
  } catch {
    return {};
  }
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

async function handleNexusCommand(ctx: CommandHandlerContext): Promise<CommandResponse> {
  const config = resolvePluginConfig(ctx);
  const parsed = parseNexusInvocation(ctx.args);
  const requester = getRequesterContext(ctx, config);
  const sessionState = getSessionState(requester.session_id);

  if (!parsed.command && !parsed.freeform) {
    return { text: renderHelpText() };
  }

  if (new Set(["help", "--help", "-h", "?"]).has(parsed.command)) {
    return { text: renderHelpText() };
  }

  if (parsed.command === "confirm") {
    const pending = pendingConfirmations.get(requester.session_id);
    if (!pending) {
      return { text: "There is no pending Nexus ARC confirmation." };
    }
    if (Date.now() - pending.createdAt > CONFIRMATION_TTL_MS) {
      pendingConfirmations.delete(requester.session_id);
      return { text: "The pending Nexus ARC confirmation expired. Re-run the command." };
    }
    pendingConfirmations.delete(requester.session_id);
    const confirmedResult = await callBridge(pending.path, pending.payload, config);
    updateSessionStateFromResult(
      requester.session_id,
      sessionState,
      (pending.payload.context as Record<string, unknown>) ?? {},
      confirmedResult,
      config
    );
    return { text: renderResult(confirmedResult) };
  }

  if (parsed.command === "refresh") {
    const workflowId = sessionState.currentWorkflowId || stringValue(parsed.args[0]);
    if (workflowId) {
      const statusPayload = await callBridgeGet(
        `/api/v1/workflows/${encodeURIComponent(workflowId)}`,
        config
      );
      return { text: renderWorkflowStatus(statusPayload) };
    }
    const fallbackIssueRef = sessionState.currentIssueRef || stringValue(parsed.args[0]);
    if (!fallbackIssueRef) {
      return {
        text:
          "Usage: /nexus refresh\nRun it after /nexus plan, /nexus status, /nexus usage, or /nexus use <project>."
      };
    }
    const refreshResult = await callBridge(
      "/api/v1/commands/execute",
      {
        command: "wfstate",
        args: [fallbackIssueRef],
        raw_text: `wfstate ${fallbackIssueRef}`,
        requester,
        context: inferCommandContext(
          { command: "wfstate", args: [fallbackIssueRef], freeform: `wfstate ${fallbackIssueRef}` },
          sessionState,
          config
        ),
        client: {
          plugin_version: PLUGIN_VERSION,
          render_mode: config.renderMode
        }
      },
      config
    );
    updateSessionStateFromResult(
      requester.session_id,
      sessionState,
      inferCommandContext(
        { command: "wfstate", args: [fallbackIssueRef], freeform: `wfstate ${fallbackIssueRef}` },
        sessionState,
        config
      ),
      refreshResult,
      config
    );
    return { text: renderResult(refreshResult) };
  }

  if (new Set(LOCAL_COMMANDS).has(parsed.command as (typeof LOCAL_COMMANDS)[number])) {
    return handleLocalCommand(parsed, sessionState, requester.session_id, config) as CommandResponse;
  }

  const context = inferCommandContext(parsed, sessionState, config);
  const client = {
    plugin_version: PLUGIN_VERSION,
    render_mode: config.renderMode
  };
  const yesArgs = parsed.args.filter((arg) => arg !== "--yes");
  const normalizedParsed =
    yesArgs.length === parsed.args.length
      ? parsed
      : {
          command: parsed.command,
          args: yesArgs,
          freeform: [parsed.command, ...yesArgs].filter(Boolean).join(" ")
        };
  const request = buildBridgeRequest(normalizedParsed, requester, context, client);

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

  const result = await callBridge(request.path, request.payload, config);

  updateSessionStateFromResult(requester.session_id, sessionState, context, result, config);
  return { text: renderResult(result) };
}

const plugin = {
  id: PLUGIN_ID,
  name: "Nexus ARC Command Bridge",
  configSchema: {
    type: "object",
    additionalProperties: false,
    properties: {
      bridgeUrl: { type: "string" },
      authToken: { type: "string" },
      timeoutMs: { type: "integer" },
      sourcePlatform: { type: "string" },
      defaultProject: { type: "string" },
      renderMode: { type: "string" },
      sessionMemory: { type: "boolean" },
      requireConfirmFor: {
        type: "array",
        items: { type: "string" }
      }
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
  }
};

export default plugin;
