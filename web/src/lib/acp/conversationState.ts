import type {
  AvailableCommand,
  ContentBlock,
  PlanEntry,
  PromptRequest,
  SessionConfigOption,
  SessionInfoUpdate,
  SessionUpdate,
  UsageUpdate,
} from '@agentclientprotocol/sdk';
import type { Message, ToolCallState } from '../../vendor/acp-components-core/src/types/index';
import {
  appendSessionContent,
  appendSessionThought,
  createSessionData,
  patchSessionToolCall,
  replaceSessionPlan,
  setSessionConfigOptions,
  setSessionPartExpanded,
  setSessionToolExpanded,
  upsertSessionToolCall,
  type SessionData,
} from '../../vendor/acp-components-core/src/store/sessionStore';
import type {
  ConnectionPayload,
  ContextCompaction,
  ConversationActivity,
  ConversationPermissionRequest,
  ConversationTerminalState,
  ProgrammaticPrompt,
  QueuedPrompt,
  ServerEnvelope,
  TurnDeliveryReceipt,
  TurnDeliveryReceiptState,
} from './contracts';

export type DeepReadonly<T> =
  T extends (...arguments_: never[]) => unknown ? T
    : T extends readonly (infer Item)[] ? readonly DeepReadonly<Item>[]
      : T extends object ? { readonly [Key in keyof T]: DeepReadonly<T[Key]> }
        : T;

export interface ConversationCursor {
  readonly entityKind: 'ticket' | 'agent';
  readonly entityId: string;
  readonly acpSessionId: string;
  readonly bindingGeneration: number;
  readonly sequence: number;
}

export type TimelineReference =
  | { readonly kind: 'message'; readonly messageId: string }
  | { readonly kind: 'programmatic_prompt'; readonly promptId: string }
  | { readonly kind: 'compaction'; readonly compactionBoundaryId: string }
  | { readonly kind: 'delivery'; readonly deliveryClientMessageId: string }
  | { readonly kind: 'protocol_rejection'; readonly protocolRejectionSequence: number };

export interface ConversationConnection {
  readonly state: 'connecting' | 'open' | 'reset' | 'ready' | 'closed' | 'error' | 'disposed';
  readonly detail: string;
  readonly supportsSteer: boolean;
}

export interface CompactionViewState {
  readonly payload: DeepReadonly<ContextCompaction>;
}

export interface PermissionViewState {
  readonly request: DeepReadonly<ConversationPermissionRequest>;
  readonly submittingOptionId: string | null;
}

export interface OptimisticHumanState {
  readonly messageId: string;
  readonly deliveryState: TurnDeliveryReceiptState | 'pending';
  readonly reason: string | null;
}

export interface ProtocolRejectionViewState {
  readonly sequence: number;
  readonly rejectedSessionUpdate: string;
  readonly reason: string;
  readonly status: 'Agent sent an unsupported update';
}

export interface ProgrammaticPromptViewState {
  readonly payload: DeepReadonly<ProgrammaticPrompt>;
}

export interface UnsupportedAgentContent {
  readonly key: string;
  readonly reason: 'Unsupported agent content';
}

export interface ReadonlySessionData {
  readonly messages: readonly DeepReadonly<Message>[];
  readonly isStreaming: boolean;
  readonly pendingToolCalls: Readonly<Record<string, DeepReadonly<ToolCallState>>>;
  readonly pendingPermissions: readonly never[];
  readonly plan: readonly DeepReadonly<PlanEntry>[];
  readonly planMessageId?: string;
  readonly usage: DeepReadonly<UsageUpdate> | null;
  readonly configOptions: readonly DeepReadonly<SessionConfigOption>[];
  readonly availableCommands: readonly DeepReadonly<AvailableCommand>[];
}

export interface ConversationSnapshot {
  readonly employeeId: string;
  readonly cursor: ConversationCursor | null;
  readonly session: ReadonlySessionData;
  readonly timeline: readonly TimelineReference[];
  readonly activity: DeepReadonly<ConversationActivity> | null;
  readonly connection: ConversationConnection;
  readonly recoverableConnectionError: string | null;
  readonly protocolRejections: Readonly<Record<string, ProtocolRejectionViewState>>;
  readonly unsupportedAgentContent: readonly UnsupportedAgentContent[];
  readonly currentModeId: string | null;
  readonly sessionInfo: DeepReadonly<SessionInfoUpdate> | null;
  readonly compactions: Readonly<Record<string, CompactionViewState>>;
  readonly receipts: Readonly<Record<string, DeepReadonly<TurnDeliveryReceipt>>>;
  readonly latestReceiptClientMessageId: string | null;
  readonly latestReceiptSequence: number | null;
  readonly queue: readonly DeepReadonly<QueuedPrompt>[];
  readonly optimisticHumans: Readonly<Record<string, OptimisticHumanState>>;
  readonly permissions: Readonly<Record<string, PermissionViewState>>;
  readonly terminalStates: Readonly<Record<string, DeepReadonly<ConversationTerminalState>>>;
  readonly programmaticPrompts: Readonly<Record<string, ProgrammaticPromptViewState>>;
  readonly disposed: boolean;
}

interface MissingIdGroup {
  role: 'user' | 'agent';
  messageId: string;
}

interface FallbackBookkeeping {
  turnOrdinal: number;
  currentRole: 'user' | 'agent' | null;
  segmentOrdinal: number;
  activeMissingIdGroup: MissingIdGroup | null;
  currentAgentMessageId: string | null;
}

export interface ConversationState {
  employeeId: string;
  cursor: ConversationCursor | null;
  session: SessionData;
  timeline: TimelineReference[];
  activity: ConversationActivity | null;
  connection: ConversationConnection;
  recoverableConnectionError: string | null;
  protocolRejections: Record<string, ProtocolRejectionViewState>;
  unsupportedAgentContent: UnsupportedAgentContent[];
  currentModeId: string | null;
  sessionInfo: SessionInfoUpdate | null;
  compactions: Record<string, { payload: ContextCompaction }>;
  receipts: Record<string, TurnDeliveryReceipt>;
  latestReceiptClientMessageId: string | null;
  latestReceiptSequence: number | null;
  queue: QueuedPrompt[];
  optimisticHumans: Record<string, OptimisticHumanState>;
  permissions: Record<string, { request: ConversationPermissionRequest; submittingOptionId: string | null }>;
  terminalStates: Record<string, ConversationTerminalState>;
  programmaticPrompts: Record<string, ProgrammaticPromptViewState>;
  fallback: FallbackBookkeeping;
  disposed: boolean;
}

export interface ConversationStateDependencies {
  now(): number;
  fallbackId(turnOrdinal: number, role: 'user' | 'agent', segmentOrdinal: number): string;
}

export type ConversationTransition =
  | { kind: 'server_envelope'; envelope: ServerEnvelope; cursor?: ConversationCursor }
  | { kind: 'local_connection'; state: ConversationConnection['state']; detail: string }
  | { kind: 'recoverable_connection_error'; message: string }
  | { kind: 'clear_recoverable_connection_error' }
  | { kind: 'optimistic_prompt'; clientMessageId: string; prompt: PromptRequest }
  | {
      kind: 'local_delivery_rejected';
      clientMessageId: string;
      choice: 'normal' | 'steer' | 'send_now' | 'queue';
      reason: string;
    }
  | { kind: 'permission_response_pending'; requestId: string; optionId: string }
  | { kind: 'permission_response_failed'; requestId: string; optionId: string }
  | { kind: 'thought_expanded'; messageId: string; partIndex: number; expanded: boolean }
  | { kind: 'tool_expanded'; toolCallId: string; expanded: boolean }
  | { kind: 'session_reset'; preserveProtocolRejections?: boolean }
  | { kind: 'clear_permissions' }
  | { kind: 'dispose' };

export function createConversationState(employeeId: string): ConversationState {
  return {
    employeeId,
    cursor: null,
    session: createSessionData(),
    timeline: [],
    activity: null,
    connection: { state: 'connecting', detail: 'Connecting', supportsSteer: false },
    recoverableConnectionError: null,
    protocolRejections: {},
    unsupportedAgentContent: [],
    currentModeId: null,
    sessionInfo: null,
    compactions: {},
    receipts: {},
    latestReceiptClientMessageId: null,
    latestReceiptSequence: null,
    queue: [],
    optimisticHumans: {},
    permissions: {},
    terminalStates: {},
    programmaticPrompts: {},
    fallback: {
      turnOrdinal: 0,
      currentRole: null,
      segmentOrdinal: 0,
      activeMissingIdGroup: null,
      currentAgentMessageId: null,
    },
    disposed: false,
  };
}

function appendTimelineOnce(state: ConversationState, reference: TimelineReference): TimelineReference[] {
  const key = JSON.stringify(reference);
  return state.timeline.some((candidate) => JSON.stringify(candidate) === key)
    ? state.timeline
    : [...state.timeline, reference];
}

function copyState(state: ConversationState): ConversationState {
  return { ...state };
}

function closeMissingIdAgentGroup(state: ConversationState): ConversationState {
  return {
    ...state,
    fallback: {
      ...state.fallback,
      activeMissingIdGroup: null,
      currentAgentMessageId: null,
    },
  };
}

function selectMessageId(
  state: ConversationState,
  role: 'user' | 'agent',
  suppliedMessageId: string | null | undefined,
  dependencies: ConversationStateDependencies,
): { state: ConversationState; messageId: string } {
  const next = copyState(state);
  const roleChanged = next.fallback.currentRole !== role;
  let turnOrdinal = next.fallback.turnOrdinal;
  if (role === 'user' && roleChanged) turnOrdinal += 1;
  if (role === 'agent' && turnOrdinal === 0) turnOrdinal = 1;
  let segmentOrdinal = next.fallback.segmentOrdinal;
  let activeMissingIdGroup = roleChanged ? null : next.fallback.activeMissingIdGroup;
  let messageId = suppliedMessageId || null;
  if (messageId) {
    activeMissingIdGroup = null;
  } else if (activeMissingIdGroup?.role === role) {
    messageId = activeMissingIdGroup.messageId;
  } else {
    segmentOrdinal += 1;
    messageId = dependencies.fallbackId(turnOrdinal, role, segmentOrdinal);
    activeMissingIdGroup = { role, messageId };
  }
  next.fallback = {
    turnOrdinal,
    currentRole: role,
    segmentOrdinal,
    activeMissingIdGroup,
    currentAgentMessageId: role === 'agent' ? messageId : null,
  };
  return { state: next, messageId };
}

function withMessageTimeline(state: ConversationState, messageId: string, existed: boolean): ConversationState {
  return existed
    ? state
    : { ...state, timeline: appendTimelineOnce(state, { kind: 'message', messageId }) };
}

function reduceContentUpdate(
  state: ConversationState,
  update: Extract<SessionUpdate, { sessionUpdate: 'user_message_chunk' | 'agent_message_chunk' | 'agent_thought_chunk' }>,
  dependencies: ConversationStateDependencies,
): ConversationState {
  const role = update.sessionUpdate === 'user_message_chunk' ? 'user' : 'agent';
  const selected = selectMessageId(state, role, update.messageId, dependencies);
  const existed = selected.state.session.messages.some((message) => message.id === selected.messageId);
  const session = update.sessionUpdate === 'agent_thought_chunk'
    ? appendSessionThought(selected.state.session, selected.messageId, role, update.content, dependencies.now())
    : appendSessionContent(selected.state.session, selected.messageId, role, update.content, dependencies.now());
  return withMessageTimeline({ ...selected.state, session }, selected.messageId, existed);
}

function boundaryMessageId(
  state: ConversationState,
  dependencies: ConversationStateDependencies,
): { state: ConversationState; messageId: string; existed: boolean } {
  let next = state;
  let messageId = state.fallback.currentAgentMessageId;
  const existing = messageId
    ? state.session.messages.find((message) => message.id === messageId && message.role === 'agent')
    : undefined;
  if (!existing) {
    const selected = selectMessageId(
      { ...state, fallback: { ...state.fallback, activeMissingIdGroup: null } },
      'agent',
      undefined,
      dependencies,
    );
    next = selected.state;
    messageId = selected.messageId;
  }
  next = {
    ...next,
    fallback: { ...next.fallback, activeMissingIdGroup: null },
  };
  return { state: next, messageId: messageId!, existed: Boolean(existing) };
}

function addUnsupportedContent(state: ConversationState, key: string): ConversationState {
  if (state.unsupportedAgentContent.some((entry) => entry.key === key)) return state;
  return {
    ...state,
    unsupportedAgentContent: [
      ...state.unsupportedAgentContent,
      { key, reason: 'Unsupported agent content' },
    ],
  };
}

function reduceSessionUpdate(
  state: ConversationState,
  update: SessionUpdate,
  dependencies: ConversationStateDependencies,
): ConversationState {
  switch (update.sessionUpdate) {
    case 'user_message_chunk':
    case 'agent_message_chunk':
    case 'agent_thought_chunk':
      return reduceContentUpdate(state, update, dependencies);
    case 'tool_call': {
      const boundary = boundaryMessageId(state, dependencies);
      const tool: ToolCallState = {
        ...update,
        content: update.content ?? [],
        locations: update.locations ?? [],
        expanded: false,
      };
      const session = upsertSessionToolCall(
        boundary.state.session,
        tool,
        boundary.messageId,
        dependencies.now(),
      );
      return withMessageTimeline({ ...boundary.state, session }, boundary.messageId, boundary.existed);
    }
    case 'tool_call_update': {
      if (!state.session.pendingToolCalls.has(update.toolCallId)) {
        return addUnsupportedContent(state, `tool-update:${update.toolCallId}`);
      }
      return {
        ...state,
        session: patchSessionToolCall(state.session, update.toolCallId, update),
        fallback: { ...state.fallback, activeMissingIdGroup: null },
      };
    }
    case 'plan': {
      const boundary = boundaryMessageId(state, dependencies);
      const planMessageId = state.session.planMessageId ?? boundary.messageId;
      const existed = state.session.messages.some((message) => message.id === planMessageId);
      const session = replaceSessionPlan(
        boundary.state.session,
        update.entries,
        planMessageId,
        dependencies.now(),
      );
      return withMessageTimeline({ ...boundary.state, session }, planMessageId, existed);
    }
    case 'available_commands_update':
      return { ...state, session: { ...state.session, availableCommands: [...update.availableCommands] } };
    case 'usage_update':
      return { ...state, session: { ...state.session, usage: { ...update } } };
    case 'current_mode_update':
      return { ...state, currentModeId: update.currentModeId };
    case 'config_option_update':
      return { ...state, session: setSessionConfigOptions(state.session, [...update.configOptions]) };
    case 'session_info_update':
      return { ...state, sessionInfo: { ...update } };
    case 'plan_update':
    case 'plan_removed':
      return addUnsupportedContent(state, `${update.sessionUpdate}:${state.cursor?.sequence ?? 0}`);
    default: {
      const exhaustive: never = update;
      return exhaustive;
    }
  }
}

function replacePromptMessage(
  state: ConversationState,
  clientMessageId: string,
  prompt: PromptRequest,
  dependencies: ConversationStateDependencies,
): ConversationState {
  const optimistic = state.optimisticHumans[clientMessageId];
  const fallback = optimistic ? state.fallback : {
    ...state.fallback,
    turnOrdinal: state.fallback.turnOrdinal + 1,
    currentRole: 'user' as const,
    activeMissingIdGroup: null,
    currentAgentMessageId: null,
  };
  const messageId = optimistic?.messageId ?? clientMessageId;
  const message: Message = {
    id: messageId,
    role: 'user',
    parts: [{ type: 'content', content: [...prompt.prompt] }],
    timestamp: state.session.messages.find((candidate) => candidate.id === messageId)?.timestamp
      ?? dependencies.now(),
  };
  const existed = state.session.messages.some((candidate) => candidate.id === messageId);
  const messages = existed
    ? state.session.messages.map((candidate) => candidate.id === messageId ? message : candidate)
    : [...state.session.messages, message];
  return withMessageTimeline({
    ...state,
    session: { ...state.session, messages },
    fallback,
    optimisticHumans: {
      ...state.optimisticHumans,
      [clientMessageId]: optimistic ?? { messageId, deliveryState: 'accepted', reason: null },
    },
  }, messageId, existed);
}

function reduceEnvelope(
  state: ConversationState,
  envelope: ServerEnvelope,
  dependencies: ConversationStateDependencies,
): ConversationState {
  switch (envelope.type) {
    case 'acp_session_update': {
      const runtimeUpdate = envelope.payload.update as SessionUpdate & { sessionUpdate?: string };
      const known = [
        'user_message_chunk', 'agent_message_chunk', 'agent_thought_chunk', 'tool_call',
        'tool_call_update', 'plan', 'plan_update', 'plan_removed', 'available_commands_update',
        'current_mode_update', 'config_option_update', 'session_info_update', 'usage_update',
      ].includes(runtimeUpdate.sessionUpdate ?? '');
      return known
        ? reduceSessionUpdate(state, runtimeUpdate as SessionUpdate, dependencies)
        : addUnsupportedContent(state, `runtime-update:${runtimeUpdate.sessionUpdate ?? 'missing'}`);
    }
    case 'activity': {
      const next = { ...state, activity: envelope.payload };
      return ['idle', 'interrupted', 'failed'].includes(envelope.payload.state)
        ? closeMissingIdAgentGroup(next)
        : next;
    }
    case 'delivery_receipt': {
      const receipt = envelope.payload;
      const optimistic = state.optimisticHumans[receipt.clientMessageId];
      const next = {
        ...state,
        receipts: { ...state.receipts, [receipt.clientMessageId]: receipt },
        latestReceiptClientMessageId: receipt.clientMessageId,
        latestReceiptSequence: envelope.sequence,
        optimisticHumans: optimistic ? {
          ...state.optimisticHumans,
          [receipt.clientMessageId]: {
            ...optimistic,
            deliveryState: receipt.state,
            reason: receipt.reason ?? null,
          },
        } : state.optimisticHumans,
        timeline: appendTimelineOnce(state, {
          kind: 'delivery',
          deliveryClientMessageId: receipt.clientMessageId,
        }),
      };
      return receipt.state === 'interrupted' ? closeMissingIdAgentGroup(next) : next;
    }
    case 'queue_snapshot':
      return { ...state, queue: [...envelope.payload.items] };
    case 'context_compaction': {
      const payload = envelope.payload;
      return {
        ...state,
        compactions: { ...state.compactions, [payload.boundaryId]: { payload } },
        timeline: appendTimelineOnce(state, {
          kind: 'compaction',
          compactionBoundaryId: payload.boundaryId,
        }),
      };
    }
    case 'permission_request':
      return {
        ...state,
        permissions: {
          ...state.permissions,
          [envelope.payload.requestId]: {
            request: envelope.payload,
            submittingOptionId: null,
          },
        },
      };
    case 'permission_outcome': {
      const permissions = { ...state.permissions };
      delete permissions[envelope.payload.requestId];
      return { ...state, permissions };
    }
    case 'connection': {
      const payload: ConnectionPayload = envelope.payload;
      return {
        ...state,
        connection: {
          state: payload.state,
          detail: payload.detail,
          supportsSteer: payload.supportsSteer,
        },
        permissions: payload.state === 'closed' || payload.state === 'error' || payload.state === 'reset'
          ? {}
          : state.permissions,
      };
    }
    case 'protocol_update_rejected': {
      const rejection: ProtocolRejectionViewState = {
        sequence: envelope.sequence,
        rejectedSessionUpdate: envelope.payload.rejectedSessionUpdate,
        reason: envelope.payload.reason,
        status: 'Agent sent an unsupported update',
      };
      return {
        ...state,
        protocolRejections: { ...state.protocolRejections, [String(envelope.sequence)]: rejection },
        timeline: appendTimelineOnce(state, {
          kind: 'protocol_rejection',
          protocolRejectionSequence: envelope.sequence,
        }),
      };
    }
    case 'human_echo':
      return replacePromptMessage(state, envelope.payload.clientMessageId, envelope.payload.prompt, dependencies);
    case 'programmatic_prompt':
      return {
        ...state,
        programmaticPrompts: {
          ...state.programmaticPrompts,
          [envelope.payload.promptId]: { payload: envelope.payload },
        },
        timeline: appendTimelineOnce(state, {
          kind: 'programmatic_prompt',
          promptId: envelope.payload.promptId,
        }),
      };
    case 'terminal_state':
      return {
        ...state,
        terminalStates: {
          ...state.terminalStates,
          [envelope.payload.terminalId]: envelope.payload,
        },
      };
    default: {
      const exhaustive: never = envelope;
      return exhaustive;
    }
  }
}

function resetSessionState(
  state: ConversationState,
  preserveProtocolRejections: boolean,
): ConversationState {
  const fresh = createConversationState(state.employeeId);
  return {
    ...fresh,
    cursor: state.cursor,
    connection: state.connection,
    recoverableConnectionError: state.recoverableConnectionError,
    protocolRejections: preserveProtocolRejections ? state.protocolRejections : {},
  };
}

export function reduceConversationState(
  previous: ConversationState,
  transition: ConversationTransition,
  dependencies: ConversationStateDependencies,
): ConversationState {
  if (previous.disposed && transition.kind !== 'dispose') return previous;
  switch (transition.kind) {
    case 'server_envelope': {
      const reduced = reduceEnvelope(previous, transition.envelope, dependencies);
      return transition.cursor ? { ...reduced, cursor: transition.cursor } : reduced;
    }
    case 'local_connection':
      return {
        ...previous,
        connection: {
          ...previous.connection,
          state: transition.state,
          detail: transition.detail,
        },
        permissions: transition.state === 'closed' || transition.state === 'error' ? {} : previous.permissions,
      };
    case 'recoverable_connection_error':
      return { ...previous, recoverableConnectionError: transition.message, permissions: {} };
    case 'clear_recoverable_connection_error':
      return { ...previous, recoverableConnectionError: null };
    case 'optimistic_prompt': {
      const turnOrdinal = previous.fallback.turnOrdinal + 1;
      let session = previous.session;
      for (const block of transition.prompt.prompt) {
        session = appendSessionContent(
          session,
          transition.clientMessageId,
          'user',
          block,
          dependencies.now(),
        );
      }
      const existed = previous.session.messages.some((message) => message.id === transition.clientMessageId);
      const next = {
        ...previous,
        session,
        optimisticHumans: {
          ...previous.optimisticHumans,
          [transition.clientMessageId]: {
            messageId: transition.clientMessageId,
            deliveryState: 'pending' as const,
            reason: null,
          },
        },
        fallback: {
          ...previous.fallback,
          turnOrdinal,
          currentRole: 'user' as const,
          activeMissingIdGroup: null,
          currentAgentMessageId: null,
        },
      };
      return withMessageTimeline(next, transition.clientMessageId, existed);
    }
    case 'local_delivery_rejected': {
      const optimistic = previous.optimisticHumans[transition.clientMessageId];
      if (!optimistic) return previous;
      const receipt: TurnDeliveryReceipt = {
        clientMessageId: transition.clientMessageId,
        choice: transition.choice,
        state: 'rejected',
        reason: transition.reason,
      };
      return {
        ...previous,
        receipts: { ...previous.receipts, [transition.clientMessageId]: receipt },
        latestReceiptClientMessageId: transition.clientMessageId,
        optimisticHumans: {
          ...previous.optimisticHumans,
          [transition.clientMessageId]: {
            ...optimistic,
            deliveryState: 'rejected',
            reason: transition.reason,
          },
        },
        timeline: appendTimelineOnce(previous, {
          kind: 'delivery',
          deliveryClientMessageId: transition.clientMessageId,
        }),
      };
    }
    case 'permission_response_pending': {
      const permission = previous.permissions[transition.requestId];
      if (!permission) return previous;
      return {
        ...previous,
        permissions: {
          ...previous.permissions,
          [transition.requestId]: { ...permission, submittingOptionId: transition.optionId },
        },
      };
    }
    case 'permission_response_failed': {
      const permission = previous.permissions[transition.requestId];
      if (!permission || permission.submittingOptionId !== transition.optionId) return previous;
      return {
        ...previous,
        permissions: {
          ...previous.permissions,
          [transition.requestId]: { ...permission, submittingOptionId: null },
        },
      };
    }
    case 'thought_expanded':
      return {
        ...previous,
        session: setSessionPartExpanded(
          previous.session,
          transition.messageId,
          transition.partIndex,
          transition.expanded,
        ),
      };
    case 'tool_expanded':
      return {
        ...previous,
        session: setSessionToolExpanded(previous.session, transition.toolCallId, transition.expanded),
      };
    case 'session_reset':
      return resetSessionState(previous, transition.preserveProtocolRejections ?? false);
    case 'clear_permissions':
      return { ...previous, permissions: {} };
    case 'dispose':
      return {
        ...previous,
        disposed: true,
        permissions: {},
        connection: { ...previous.connection, state: 'disposed', detail: 'Disposed' },
      };
    default: {
      const exhaustive: never = transition;
      return exhaustive;
    }
  }
}

function cloneAndFreeze<T>(value: T): DeepReadonly<T> {
  if (Array.isArray(value)) {
    return Object.freeze(value.map((item) => cloneAndFreeze(item))) as DeepReadonly<T>;
  }
  if (value !== null && typeof value === 'object') {
    const clone: Record<string, unknown> = {};
    for (const [key, item] of Object.entries(value)) clone[key] = cloneAndFreeze(item);
    return Object.freeze(clone) as DeepReadonly<T>;
  }
  return value as DeepReadonly<T>;
}

const projectionCache = new WeakMap<ConversationState, ConversationSnapshot>();

export function projectConversationSnapshot(state: ConversationState): ConversationSnapshot {
  const cached = projectionCache.get(state);
  if (cached) return cached;
  const pendingToolCalls: Record<string, ToolCallState> = {};
  for (const [toolCallId, tool] of state.session.pendingToolCalls) pendingToolCalls[toolCallId] = tool;
  const snapshot = cloneAndFreeze({
    employeeId: state.employeeId,
    cursor: state.cursor,
    session: {
      messages: state.session.messages,
      isStreaming: state.session.isStreaming,
      pendingToolCalls,
      pendingPermissions: [],
      plan: state.session.plan,
      ...(state.session.planMessageId ? { planMessageId: state.session.planMessageId } : {}),
      usage: state.session.usage,
      configOptions: state.session.configOptions,
      availableCommands: state.session.availableCommands,
    },
    timeline: state.timeline,
    activity: state.activity,
    connection: state.connection,
    recoverableConnectionError: state.recoverableConnectionError,
    protocolRejections: state.protocolRejections,
    unsupportedAgentContent: state.unsupportedAgentContent,
    currentModeId: state.currentModeId,
    sessionInfo: state.sessionInfo,
    compactions: state.compactions,
    receipts: state.receipts,
    latestReceiptClientMessageId: state.latestReceiptClientMessageId,
    latestReceiptSequence: state.latestReceiptSequence,
    queue: state.queue,
    optimisticHumans: state.optimisticHumans,
    permissions: state.permissions,
    terminalStates: state.terminalStates,
    programmaticPrompts: state.programmaticPrompts,
    disposed: state.disposed,
  }) as ConversationSnapshot;
  projectionCache.set(state, snapshot);
  return snapshot;
}

/**
 * A stanza is one beat of agent work: a Thinking line plus the tool calls that
 * thought drove, in arrival order. Grouping is a pure projection of an agent
 * message's ordered `parts` — the server timeline stays the single source of
 * truth. A new thought burst (a `thought` part following tool calls) starts a
 * new stanza; tool calls arriving before any thought open a bare stanza the
 * following thought then fills.
 *
 * Backends like Claude rarely send `thought` parts — their rhythm is short
 * narration text, then tool calls, then more narration, then a final answer. So
 * an all-text `content` part that is immediately followed by a `tool_calls` part
 * IS that beat's thinking: it becomes the collapsed lead of a content-led
 * stanza (`thoughtPartIndex: null`, disclosure kept component-local) rather than
 * rendering as prose. Any other `content` part — mixed blocks, or not followed
 * by tool calls, including the turn's final answer — stays prose.
 */
export interface TranscriptStanza {
  readonly key: string;
  readonly messageId: string;
  /** Index of the owning `thought` part, or null for a bare (thoughtless) stanza. */
  readonly thoughtPartIndex: number | null;
  readonly thought: readonly DeepReadonly<ContentBlock>[] | null;
  /** Persisted disclosure state of the owning thought part (false for bare stanzas). */
  readonly thoughtExpanded: boolean;
  readonly steps: readonly DeepReadonly<ToolCallState>[];
  readonly hasFailure: boolean;
}

export type MessageBlock =
  | {
      readonly kind: 'content';
      readonly key: string;
      readonly partIndex: number;
      readonly content: readonly DeepReadonly<ContentBlock>[];
    }
  | { readonly kind: 'stanza'; readonly stanza: TranscriptStanza };

interface MutableStanza {
  thoughtPartIndex: number | null;
  /** Index of the lead `content` part for a content-led stanza, else null. */
  contentLeadPartIndex: number | null;
  thought: readonly DeepReadonly<ContentBlock>[] | null;
  thoughtExpanded: boolean;
  steps: DeepReadonly<ToolCallState>[];
}

/**
 * Project one agent message's ordered `parts` into the render blocks the
 * transcript shows: prose content blocks and stanzas, in arrival order. `plan`
 * parts are intentionally dropped — the plan lives outside the transcript now,
 * while its data stays untouched in the snapshot.
 */
export function groupMessageBlocks(message: DeepReadonly<Message>): MessageBlock[] {
  const blocks: MessageBlock[] = [];
  let current: MutableStanza | null = null;

  const flush = (): void => {
    if (!current) return;
    const firstStep = current.steps[0];
    const suffix = current.thoughtPartIndex !== null
      ? `p${current.thoughtPartIndex}`
      : current.contentLeadPartIndex !== null
        ? `c${current.contentLeadPartIndex}`
        : firstStep
          ? `t${firstStep.toolCallId}`
          : 'empty';
    blocks.push({
      kind: 'stanza',
      stanza: {
        key: `${message.id}:stanza:${suffix}`,
        messageId: message.id,
        thoughtPartIndex: current.thoughtPartIndex,
        thought: current.thought,
        thoughtExpanded: current.thoughtExpanded,
        steps: current.steps,
        hasFailure: current.steps.some((step) => step.status === 'failed'),
      },
    });
    current = null;
  };

  message.parts.forEach((part, partIndex) => {
    if (part.type === 'thought') {
      if (current && current.thought === null) {
        current.thought = part.thought;
        current.thoughtPartIndex = partIndex;
        current.thoughtExpanded = part.expanded ?? false;
      } else {
        flush();
        current = {
          thoughtPartIndex: partIndex,
          contentLeadPartIndex: null,
          thought: part.thought,
          thoughtExpanded: part.expanded ?? false,
          steps: [],
        };
      }
    } else if (part.type === 'tool_calls') {
      if (!current) {
        current = {
          thoughtPartIndex: null,
          contentLeadPartIndex: null,
          thought: null,
          thoughtExpanded: false,
          steps: [],
        };
      }
      current.steps.push(...part.toolCalls);
    } else if (part.type === 'content') {
      flush();
      const nextPart = message.parts[partIndex + 1];
      const leadsToolCalls =
        nextPart?.type === 'tool_calls' &&
        part.content.every((block) => block.type === 'text');
      if (leadsToolCalls) {
        // Narration that precedes tool calls is this beat's thinking: fold it
        // into the stanza as its lead instead of rendering it as prose.
        current = {
          thoughtPartIndex: null,
          contentLeadPartIndex: partIndex,
          thought: part.content,
          thoughtExpanded: false,
          steps: [],
        };
      } else {
        blocks.push({
          kind: 'content',
          key: `${message.id}:content:${partIndex}`,
          partIndex,
          content: part.content,
        });
      }
    }
  });
  flush();
  return blocks;
}

export function safeDisplayText(value: unknown): string {
  if (typeof value === 'string') return value;
  try {
    const serialized = JSON.stringify(value, null, 2);
    return serialized ?? 'Unsupported agent content';
  } catch {
    return 'Unsupported agent content';
  }
}
