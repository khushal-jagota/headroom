/** The small Codex wire contracts consumed by Panels, not an upstream schema. */
import { is_json_record, JsonNumber } from '../../../core/http/numeric_json.ts';
export class CodexProtocolError extends Error {}

type ObjectValue = Record<string, unknown>;
function object(value: unknown): ObjectValue {
  if (!is_json_record(value)) throw new CodexProtocolError('Codex value must be an object');
  return value;
}
function string(value: unknown): string {
  if (typeof value !== 'string') throw new CodexProtocolError('Codex value must be a string');
  return value;
}
function identifier(value: unknown): string {
  const result = string(value);
  if (!/\S/.test(result)) throw new CodexProtocolError('Codex identifier must not be blank');
  return result;
}
function boolean(value: unknown): boolean {
  if (typeof value !== 'boolean') throw new CodexProtocolError('Codex value must be a boolean');
  return value;
}
function integer(value: unknown): number {
  if (value instanceof JsonNumber) {
    if (value.kind !== 'integer') throw new CodexProtocolError('Codex value must be an integer');
    return value.value;
  }
  if (typeof value !== 'number' || !Number.isInteger(value))
    throw new CodexProtocolError('Codex value must be an integer');
  return value;
}
function optional<T>(value: unknown, read: (value: unknown) => T): T | null {
  return value === undefined || value === null ? null : read(value);
}
function list<T>(value: unknown, read: (value: unknown) => T): T[] {
  if (!Array.isArray(value)) throw new CodexProtocolError('Codex value must be an array');
  return value.map(read);
}
function literal<const T extends string>(value: unknown, choices: readonly T[]): T {
  if (typeof value !== 'string' || !choices.includes(value as T))
    throw new CodexProtocolError(`Codex value must be one of ${choices.join(', ')}`);
  return value as T;
}
function turn_identity(value: unknown): { threadId: string; turnId: string } {
  const item = object(value);
  return { threadId: identifier(item.threadId), turnId: identifier(item.turnId) };
}

export interface ClientInfo {
  name: string;
  title?: string | null;
  version: string;
}
export interface InitializeCapabilities {
  experimentalApi: boolean;
}
export interface InitializeParams {
  clientInfo: ClientInfo;
  capabilities?: InitializeCapabilities | null;
}
export interface TextUserInput {
  type: 'text';
  text: string;
}
export interface LocalImageUserInput {
  type: 'localImage';
  path: string;
}
export interface SkillUserInput {
  type: 'skill';
  name: string;
  path: string;
}
export interface MentionUserInput {
  type: 'mention';
  name: string;
  path: string;
}
export type UserInput = TextUserInput | LocalImageUserInput | SkillUserInput | MentionUserInput;
export interface DangerFullAccessSandboxPolicy {
  type: 'dangerFullAccess';
}
export interface WorkspaceWriteSandboxPolicy {
  type: 'workspaceWrite';
}
export interface ThreadStartParams {
  cwd: string;
  model?: string | null;
  approvalPolicy: 'never' | 'untrusted';
  approvalsReviewer: 'user';
  sandbox: 'danger-full-access' | 'workspace-write';
  developerInstructions?: string | null;
}
export interface ThreadResumeParams extends ThreadStartParams {
  threadId: string;
}
export interface TurnStartParams {
  threadId: string;
  input: UserInput[];
  model?: string | null;
  effort?: string | null;
  approvalPolicy: 'never' | 'untrusted';
  approvalsReviewer: 'user';
  sandboxPolicy: DangerFullAccessSandboxPolicy | WorkspaceWriteSandboxPolicy;
}
export interface TurnSteerParams {
  threadId: string;
  expectedTurnId: string;
  input: UserInput[];
}
export interface TurnInterruptParams {
  threadId: string;
  turnId: string;
}
export interface SkillsListParams {
  cwds: string[];
}
export interface AppsInstalledParams {
  threadId: string;
  forceRefresh: boolean;
}
export interface AppsReadParams {
  appIds: string[];
  threadId: string;
  includeTools: boolean;
}
export interface PluginInstalledParams {
  cwds: string[];
}
export interface ThreadCompactStartParams {
  threadId: string;
}
export interface CustomReviewTarget {
  type: 'custom';
  instructions: string;
}
export interface UncommittedChangesReviewTarget {
  type: 'uncommittedChanges';
}
export interface ReviewStartParams {
  threadId: string;
  delivery: 'inline';
  target: CustomReviewTarget | UncommittedChangesReviewTarget;
}
export interface ThreadGoalGetParams {
  threadId: string;
}
export interface ThreadGoalSetParams {
  threadId: string;
  objective?: string | null;
  status: 'active';
}
export interface ThreadGoalClearParams {
  threadId: string;
}

export const TurnSteerResponse = {
  read(value: unknown) {
    return { turnId: identifier(object(value).turnId) };
  },
};
export type TurnSteerResponse = ReturnType<typeof TurnSteerResponse.read>;
export const Thread = {
  read(value: unknown) {
    const item = object(value);
    return { id: identifier(item.id), ephemeral: boolean(item.ephemeral) };
  },
};
export type Thread = ReturnType<typeof Thread.read>;
export const ThreadStartResponse = {
  read(value: unknown) {
    return { thread: Thread.read(object(value).thread) };
  },
};
export type ThreadStartResponse = ReturnType<typeof ThreadStartResponse.read>;
export const ThreadResumeResponse = ThreadStartResponse;
export type ThreadResumeResponse = ThreadStartResponse;
export const TurnError = {
  read(value: unknown) {
    const item = object(value);
    return {
      message: string(item.message),
      codexErrorInfo: optional(item.codexErrorInfo, (value) =>
        typeof value === 'string' ? value : object(value),
      ),
      additionalDetails: optional(item.additionalDetails, string),
    };
  },
};
export type TurnError = ReturnType<typeof TurnError.read>;
export const Turn = {
  read(value: unknown) {
    const item = object(value);
    return {
      id: identifier(item.id),
      status: literal(item.status, ['inProgress', 'completed', 'failed', 'interrupted']),
      error: optional(item.error, TurnError.read),
    };
  },
};
export type Turn = ReturnType<typeof Turn.read>;
export const TurnStartResponse = {
  read(value: unknown) {
    return { turn: Turn.read(object(value).turn) };
  },
};
export type TurnStartResponse = ReturnType<typeof TurnStartResponse.read>;
export const ThreadTurnNotification = {
  read(value: unknown) {
    const item = object(value);
    return { threadId: identifier(item.threadId), turn: Turn.read(item.turn) };
  },
};
export type ThreadTurnNotification = ReturnType<typeof ThreadTurnNotification.read>;
export const TurnStartedNotification = ThreadTurnNotification;
export type TurnStartedNotification = ThreadTurnNotification;
export const TurnCompletedNotification = ThreadTurnNotification;
export type TurnCompletedNotification = ThreadTurnNotification;
export const TurnNotification = { read: turn_identity };
export type TurnNotification = ReturnType<typeof turn_identity>;
export const MessageDeltaNotification = {
  read(value: unknown) {
    const item = object(value);
    return { ...turn_identity(item), itemId: identifier(item.itemId), delta: string(item.delta) };
  },
};
export type MessageDeltaNotification = ReturnType<typeof MessageDeltaNotification.read>;
export const AgentMessageDeltaNotification = MessageDeltaNotification;
export type AgentMessageDeltaNotification = MessageDeltaNotification;
export const CommandExecutionOutputDeltaNotification = MessageDeltaNotification;
export type CommandExecutionOutputDeltaNotification = MessageDeltaNotification;
export const McpToolCallProgressNotification = {
  read(value: unknown) {
    const item = object(value);
    return {
      ...turn_identity(item),
      itemId: identifier(item.itemId),
      message: string(item.message),
    };
  },
};
export type McpToolCallProgressNotification = ReturnType<
  typeof McpToolCallProgressNotification.read
>;
export const ReasoningTextDeltaNotification = TurnNotification;
export type ReasoningTextDeltaNotification = TurnNotification;
export const ReasoningSummaryTextDeltaNotification = TurnNotification;
export type ReasoningSummaryTextDeltaNotification = TurnNotification;

export const AgentMessageThreadItem = {
  read(value: unknown) {
    const item = object(value);
    return {
      type: literal(item.type, ['agentMessage']),
      id: identifier(item.id),
      text: string(item.text),
    };
  },
};
export type AgentMessageThreadItem = ReturnType<typeof AgentMessageThreadItem.read>;
export const ReasoningThreadItem = {
  read(value: unknown) {
    return { type: literal(object(value).type, ['reasoning']) };
  },
};
export type ReasoningThreadItem = ReturnType<typeof ReasoningThreadItem.read>;
export const ContextCompactionThreadItem = {
  read(value: unknown) {
    return { type: literal(object(value).type, ['contextCompaction']) };
  },
};
export type ContextCompactionThreadItem = ReturnType<typeof ContextCompactionThreadItem.read>;
export const CommandExecutionThreadItem = {
  read(value: unknown) {
    const item = object(value);
    return {
      type: literal(item.type, ['commandExecution']),
      id: identifier(item.id),
      command: string(item.command),
      cwd: string(item.cwd),
      status: literal(item.status, ['inProgress', 'completed', 'failed', 'declined']),
      aggregatedOutput: optional(item.aggregatedOutput, string),
    };
  },
};
export type CommandExecutionThreadItem = ReturnType<typeof CommandExecutionThreadItem.read>;
export const FileChangeKind = {
  read(value: unknown) {
    return { type: string(object(value).type) };
  },
};
export type FileChangeKind = ReturnType<typeof FileChangeKind.read>;
export const FileChange = {
  read(value: unknown) {
    const item = object(value);
    return { path: string(item.path), kind: FileChangeKind.read(item.kind) };
  },
};
export type FileChange = ReturnType<typeof FileChange.read>;
export const FileChangeThreadItem = {
  read(value: unknown) {
    const item = object(value);
    return {
      type: literal(item.type, ['fileChange']),
      id: identifier(item.id),
      changes: list(item.changes, FileChange.read),
      status: literal(item.status, ['inProgress', 'completed', 'failed', 'declined']),
    };
  },
};
export type FileChangeThreadItem = ReturnType<typeof FileChangeThreadItem.read>;
export const McpToolCallError = {
  read(value: unknown) {
    return { message: string(object(value).message) };
  },
};
export type McpToolCallError = ReturnType<typeof McpToolCallError.read>;
export const McpToolCallThreadItem = {
  read(value: unknown) {
    const item = object(value);
    return {
      type: literal(item.type, ['mcpToolCall']),
      id: identifier(item.id),
      server: string(item.server),
      tool: string(item.tool),
      status: literal(item.status, ['inProgress', 'completed', 'failed']),
      error: optional(item.error, McpToolCallError.read),
    };
  },
};
export type McpToolCallThreadItem = ReturnType<typeof McpToolCallThreadItem.read>;
export const UnconsumedThreadItem = {
  read(value: unknown) {
    return { type: string(object(value).type) };
  },
};
export type UnconsumedThreadItem = ReturnType<typeof UnconsumedThreadItem.read>;
export type ThreadItem =
  | AgentMessageThreadItem
  | ReasoningThreadItem
  | ContextCompactionThreadItem
  | CommandExecutionThreadItem
  | FileChangeThreadItem
  | McpToolCallThreadItem
  | UnconsumedThreadItem;
export function decode_item(value: unknown): ThreadItem {
  const item = object(value);
  switch (string(item.type)) {
    case 'agentMessage':
      return AgentMessageThreadItem.read(item);
    case 'reasoning':
      return ReasoningThreadItem.read(item);
    case 'contextCompaction':
      return ContextCompactionThreadItem.read(item);
    case 'commandExecution':
      return CommandExecutionThreadItem.read(item);
    case 'fileChange':
      return FileChangeThreadItem.read(item);
    case 'mcpToolCall':
      return McpToolCallThreadItem.read(item);
    default:
      return UnconsumedThreadItem.read(item);
  }
}
export const ItemNotification = {
  read(value: unknown) {
    const item = object(value);
    return { ...turn_identity(item), item: decode_item(item.item) };
  },
};
export type ItemNotification = ReturnType<typeof ItemNotification.read>;
export const ItemStartedNotification = ItemNotification;
export type ItemStartedNotification = ItemNotification;
export const ItemCompletedNotification = ItemNotification;
export type ItemCompletedNotification = ItemNotification;
export const TurnPlanStep = {
  read(value: unknown) {
    const item = object(value);
    return {
      step: string(item.step),
      status: literal(item.status, ['pending', 'inProgress', 'completed']),
    };
  },
};
export type TurnPlanStep = ReturnType<typeof TurnPlanStep.read>;
export const TurnPlanUpdatedNotification = {
  read(value: unknown) {
    const item = object(value);
    return { ...turn_identity(item), plan: list(item.plan, TurnPlanStep.read) };
  },
};
export type TurnPlanUpdatedNotification = ReturnType<typeof TurnPlanUpdatedNotification.read>;
export const TokenUsage = {
  read(value: unknown) {
    const item = object(value);
    return {
      inputTokens: integer(item.inputTokens),
      outputTokens: integer(item.outputTokens),
      cachedInputTokens: integer(item.cachedInputTokens),
    };
  },
};
export type TokenUsage = ReturnType<typeof TokenUsage.read>;
export const ThreadTokenUsage = {
  read(value: unknown) {
    return { total: TokenUsage.read(object(value).total) };
  },
};
export type ThreadTokenUsage = ReturnType<typeof ThreadTokenUsage.read>;
export const ThreadTokenUsageUpdatedNotification = {
  read(value: unknown) {
    const item = object(value);
    return { ...turn_identity(item), tokenUsage: ThreadTokenUsage.read(item.tokenUsage) };
  },
};
export type ThreadTokenUsageUpdatedNotification = ReturnType<
  typeof ThreadTokenUsageUpdatedNotification.read
>;
export const ErrorNotification = {
  read(value: unknown) {
    const item = object(value);
    return {
      ...turn_identity(item),
      error: TurnError.read(item.error),
      willRetry: boolean(item.willRetry),
    };
  },
};
export type ErrorNotification = ReturnType<typeof ErrorNotification.read>;

export const DisplayInterface = {
  read(value: unknown) {
    const item = object(value);
    return {
      displayName: optional(item.displayName, string),
      shortDescription: optional(item.shortDescription, string),
    };
  },
};
export type DisplayInterface = ReturnType<typeof DisplayInterface.read>;
export const SkillMetadata = {
  read(value: unknown) {
    const item = object(value);
    return {
      name: string(item.name),
      path: string(item.path),
      enabled: boolean(item.enabled),
      description: string(item.description),
      shortDescription: optional(item.shortDescription, string),
      interface: optional(item.interface, DisplayInterface.read),
    };
  },
};
export type SkillMetadata = ReturnType<typeof SkillMetadata.read>;
export const SkillsListEntry = {
  read(value: unknown) {
    return { skills: list(object(value).skills, SkillMetadata.read) };
  },
};
export type SkillsListEntry = ReturnType<typeof SkillsListEntry.read>;
export const SkillsListResponse = {
  read(value: unknown) {
    return { data: list(object(value).data, SkillsListEntry.read) };
  },
};
export type SkillsListResponse = ReturnType<typeof SkillsListResponse.read>;
const EmptyObject = {
  read(value: unknown): Record<string, never> {
    object(value);
    return {};
  },
};
export const SkillsChangedNotification = EmptyObject;
export type SkillsChangedNotification = Record<string, never>;
export const InstalledApp = {
  read(value: unknown) {
    const item = object(value);
    return {
      id: identifier(item.id),
      enabled: boolean(item.enabled),
      callable: boolean(item.callable),
      runtimeName: optional(item.runtimeName, string),
    };
  },
};
export type InstalledApp = ReturnType<typeof InstalledApp.read>;
export const AppsInstalledResponse = {
  read(value: unknown) {
    return { apps: list(object(value).apps, InstalledApp.read) };
  },
};
export type AppsInstalledResponse = ReturnType<typeof AppsInstalledResponse.read>;
export const ConnectorMetadata = {
  read(value: unknown) {
    const item = object(value);
    return {
      id: identifier(item.id),
      name: string(item.name),
      description: optional(item.description, string),
    };
  },
};
export type ConnectorMetadata = ReturnType<typeof ConnectorMetadata.read>;
export const AppsReadResponse = {
  read(value: unknown) {
    return { apps: list(object(value).apps, ConnectorMetadata.read) };
  },
};
export type AppsReadResponse = ReturnType<typeof AppsReadResponse.read>;
export const AppListUpdatedNotification = EmptyObject;
export type AppListUpdatedNotification = Record<string, never>;
export const PluginSummary = {
  read(value: unknown) {
    const item = object(value);
    return {
      name: string(item.name),
      installed: boolean(item.installed),
      enabled: boolean(item.enabled),
      availability: optional(item.availability, string),
      disabledReason: optional(item.disabledReason, string),
      interface: optional(item.interface, DisplayInterface.read),
    };
  },
};
export type PluginSummary = ReturnType<typeof PluginSummary.read>;
export const PluginMarketplaceEntry = {
  read(value: unknown) {
    const item = object(value);
    return { name: string(item.name), plugins: list(item.plugins, PluginSummary.read) };
  },
};
export type PluginMarketplaceEntry = ReturnType<typeof PluginMarketplaceEntry.read>;
export const PluginInstalledResponse = {
  read(value: unknown) {
    return { marketplaces: list(object(value).marketplaces, PluginMarketplaceEntry.read) };
  },
};
export type PluginInstalledResponse = ReturnType<typeof PluginInstalledResponse.read>;
export const ThreadCompactStartResponse = EmptyObject;
export type ThreadCompactStartResponse = Record<string, never>;
export const ReviewStartResponse = TurnStartResponse;
export type ReviewStartResponse = TurnStartResponse;
export const ThreadGoal = {
  read(value: unknown) {
    const item = object(value);
    return {
      objective: string(item.objective),
      status: string(item.status),
      timeUsedSeconds: integer(item.timeUsedSeconds),
      tokenBudget: optional(item.tokenBudget, integer),
      tokensUsed: integer(item.tokensUsed),
    };
  },
};
export type ThreadGoal = ReturnType<typeof ThreadGoal.read>;
export const ThreadGoalGetResponse = {
  read(value: unknown) {
    return { goal: optional(object(value).goal, ThreadGoal.read) };
  },
};
export type ThreadGoalGetResponse = ReturnType<typeof ThreadGoalGetResponse.read>;
export const ThreadGoalSetResponse = {
  read(value: unknown) {
    return { goal: ThreadGoal.read(object(value).goal) };
  },
};
export type ThreadGoalSetResponse = ReturnType<typeof ThreadGoalSetResponse.read>;
export const ThreadGoalClearResponse = {
  read(value: unknown) {
    return { cleared: boolean(object(value).cleared) };
  },
};
export type ThreadGoalClearResponse = ReturnType<typeof ThreadGoalClearResponse.read>;
export const ReasoningEffortOption = {
  read(value: unknown) {
    return { reasoningEffort: string(object(value).reasoningEffort) };
  },
};
export type ReasoningEffortOption = ReturnType<typeof ReasoningEffortOption.read>;
export const CatalogModel = {
  read(value: unknown) {
    const item = object(value);
    return {
      model: string(item.model),
      displayName: string(item.displayName),
      hidden: boolean(item.hidden),
      supportedReasoningEfforts: list(item.supportedReasoningEfforts, ReasoningEffortOption.read),
      defaultReasoningEffort: string(item.defaultReasoningEffort),
      isDefault: boolean(item.isDefault),
    };
  },
};
export type CatalogModel = ReturnType<typeof CatalogModel.read>;
export const ModelListResponse = {
  read(value: unknown) {
    const item = object(value);
    return {
      data: list(item.data, CatalogModel.read),
      nextCursor: optional(item.nextCursor, string),
    };
  },
};
export type ModelListResponse = ReturnType<typeof ModelListResponse.read>;
export const CommandExecutionRequestApprovalParams = {
  read(value: unknown) {
    const item = object(value);
    return {
      ...turn_identity(item),
      itemId: identifier(item.itemId),
      command: optional(item.command, string),
      cwd: optional(item.cwd, string),
      reason: optional(item.reason, string),
    };
  },
};
export type CommandExecutionRequestApprovalParams = ReturnType<
  typeof CommandExecutionRequestApprovalParams.read
>;
export const FileChangeRequestApprovalParams = {
  read(value: unknown) {
    const item = object(value);
    return {
      ...turn_identity(item),
      reason: optional(item.reason, string),
      grantRoot: optional(item.grantRoot, string),
    };
  },
};
export type FileChangeRequestApprovalParams = ReturnType<
  typeof FileChangeRequestApprovalParams.read
>;
export const CommandExecutionRequestApprovalResponse = {
  read(value: unknown) {
    return {
      decision: literal(object(value).decision, [
        'accept',
        'acceptForSession',
        'decline',
        'cancel',
      ]),
    };
  },
};
export type CommandExecutionRequestApprovalResponse = ReturnType<
  typeof CommandExecutionRequestApprovalResponse.read
>;
export const FileChangeRequestApprovalResponse = CommandExecutionRequestApprovalResponse;
export type FileChangeRequestApprovalResponse = CommandExecutionRequestApprovalResponse;
export const ToolRequestUserInputOption = {
  read(value: unknown) {
    const item = object(value);
    return { label: string(item.label), description: string(item.description) };
  },
};
export type ToolRequestUserInputOption = ReturnType<typeof ToolRequestUserInputOption.read>;
export const ToolRequestUserInputQuestion = {
  read(value: unknown) {
    const item = object(value);
    return {
      id: identifier(item.id),
      header: string(item.header),
      question: string(item.question),
      isSecret: item.isSecret === undefined ? false : boolean(item.isSecret),
      isOther: item.isOther === undefined ? false : boolean(item.isOther),
      options: optional(item.options, (value) => list(value, ToolRequestUserInputOption.read)),
    };
  },
};
export type ToolRequestUserInputQuestion = ReturnType<typeof ToolRequestUserInputQuestion.read>;
export const ToolRequestUserInputParams = {
  read(value: unknown) {
    const item = object(value);
    return {
      ...turn_identity(item),
      itemId: identifier(item.itemId),
      questions: list(item.questions, ToolRequestUserInputQuestion.read),
    };
  },
};
export type ToolRequestUserInputParams = ReturnType<typeof ToolRequestUserInputParams.read>;
export const ToolRequestUserInputAnswer = {
  read(value: unknown) {
    return { answers: list(object(value).answers, string) };
  },
};
export type ToolRequestUserInputAnswer = ReturnType<typeof ToolRequestUserInputAnswer.read>;
export const ToolRequestUserInputResponse = {
  read(value: unknown) {
    const answers = object(object(value).answers);
    return {
      answers: Object.fromEntries(
        Object.entries(answers).map(([key, answer]) => [
          key,
          ToolRequestUserInputAnswer.read(answer),
        ]),
      ),
    };
  },
};
export type ToolRequestUserInputResponse = ReturnType<typeof ToolRequestUserInputResponse.read>;
export const DeviceCodeLoginResponse = {
  read(value: unknown) {
    const item = object(value);
    return {
      type: literal(item.type, ['chatgptDeviceCode']),
      loginId: identifier(item.loginId),
      userCode: identifier(item.userCode),
      verificationUrl: identifier(item.verificationUrl),
    };
  },
};
export type DeviceCodeLoginResponse = ReturnType<typeof DeviceCodeLoginResponse.read>;
export const AccountLoginCompletedNotification = {
  read(value: unknown) {
    const item = object(value);
    return {
      loginId: optional(item.loginId, identifier),
      success: boolean(item.success),
      error: optional(item.error, string),
    };
  },
};
export type AccountLoginCompletedNotification = ReturnType<
  typeof AccountLoginCompletedNotification.read
>;
export const CONSUMED_SERVER_NOTIFICATIONS = {
  'turn/started': TurnStartedNotification,
  'turn/completed': TurnCompletedNotification,
  'item/agentMessage/delta': AgentMessageDeltaNotification,
  'item/started': ItemStartedNotification,
  'item/completed': ItemCompletedNotification,
  'item/commandExecution/outputDelta': CommandExecutionOutputDeltaNotification,
  'item/mcpToolCall/progress': McpToolCallProgressNotification,
  'item/reasoning/textDelta': ReasoningTextDeltaNotification,
  'item/reasoning/summaryTextDelta': ReasoningSummaryTextDeltaNotification,
  'turn/plan/updated': TurnPlanUpdatedNotification,
  'thread/tokenUsage/updated': ThreadTokenUsageUpdatedNotification,
  error: ErrorNotification,
  'skills/changed': SkillsChangedNotification,
  'app/list/updated': AppListUpdatedNotification,
} as const;
export const HANDLED_SERVER_REQUESTS = {
  'item/commandExecution/requestApproval': CommandExecutionRequestApprovalParams,
  'item/fileChange/requestApproval': FileChangeRequestApprovalParams,
  'item/tool/requestUserInput': ToolRequestUserInputParams,
} as const;
export type ConsumedNotification = ReturnType<
  (typeof CONSUMED_SERVER_NOTIFICATIONS)[keyof typeof CONSUMED_SERVER_NOTIFICATIONS]['read']
>;
export type HandledServerRequest = ReturnType<
  (typeof HANDLED_SERVER_REQUESTS)[keyof typeof HANDLED_SERVER_REQUESTS]['read']
>;
export function decode_notification(method: string, value: unknown): ConsumedNotification | null {
  const reader = Object.hasOwn(CONSUMED_SERVER_NOTIFICATIONS, method)
    ? CONSUMED_SERVER_NOTIFICATIONS[method as keyof typeof CONSUMED_SERVER_NOTIFICATIONS]
    : null;
  return reader === null ? null : reader.read(value);
}
export function decode_server_request(method: string, value: unknown): HandledServerRequest | null {
  const reader = Object.hasOwn(HANDLED_SERVER_REQUESTS, method)
    ? HANDLED_SERVER_REQUESTS[method as keyof typeof HANDLED_SERVER_REQUESTS]
    : null;
  return reader === null ? null : reader.read(value);
}
