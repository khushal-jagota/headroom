import { createStore } from 'zustand/vanilla';
import type { Message, ToolCallState, PermissionRequest } from '../types';
import type {
  SessionId,
  ContentBlock,
  StopReason,
  PlanEntry,
  UsageUpdate,
  SessionConfigOption,
  AvailableCommand,
  ToolCallUpdate,
} from '@agentclientprotocol/sdk';
import { generateId } from '../utils/id';

export interface SessionData {
  messages: Message[];
  isStreaming: boolean;
  pendingToolCalls: Map<string, ToolCallState>;
  pendingPermissions: PermissionRequest[];
  plan: PlanEntry[];
  planMessageId?: string;
  usage: UsageUpdate | null;
  configOptions: SessionConfigOption[];
  availableCommands: AvailableCommand[];
}

interface SessionStoreState {
  sessions: Map<SessionId, SessionData>;
  ensureSession: (id: SessionId) => void;
  removeSession: (id: SessionId) => void;
  resetSession: (id: SessionId) => void;
  addMessage: (sessionId: SessionId, msg: Message) => void;
  updateMessage: (sessionId: SessionId, id: string, update: Partial<Message>) => void;
  appendContent: (sessionId: SessionId, messageId: string, role: Message['role'], block: ContentBlock) => void;
  appendThought: (sessionId: SessionId, messageId: string, role: Message['role'], block: ContentBlock) => void;
  setIsStreaming: (sessionId: SessionId, value: boolean) => void;
  setStopReason: (sessionId: SessionId, reason: StopReason) => void;
  upsertToolCall: (sessionId: SessionId, tool: ToolCallState) => void;
  updateToolCall: (sessionId: SessionId, id: string, update: Partial<ToolCallState>) => void;
  addPermissionRequest: (sessionId: SessionId, request: PermissionRequest) => void;
  removePermissionRequest: (sessionId: SessionId, requestId?: string) => void;
  rejectAllPermissions: (sessionId: SessionId) => void;
  setPlan: (sessionId: SessionId, entries: PlanEntry[]) => void;
  setUsage: (sessionId: SessionId, usage: UsageUpdate) => void;
  setConfigOptions: (sessionId: SessionId, configOptions: SessionConfigOption[]) => void;
  setAvailableCommands: (sessionId: SessionId, commands: AvailableCommand[]) => void;
  setPartExpanded: (sessionId: SessionId, messageId: string, partIndex: number, expanded: boolean) => void;
  setToolCallExpanded: (sessionId: SessionId, toolCallId: string, expanded: boolean) => void;
}

export function createSessionData(): SessionData {
  return {
    messages: [],
    isStreaming: false,
    pendingToolCalls: new Map(),
    pendingPermissions: [],
    plan: [],
    usage: null,
    configOptions: [],
    availableCommands: [],
  };
}

function hasAnnotations(block: ContentBlock): boolean {
  return 'annotations' in block && block.annotations != null;
}

function appendBlockToMessage(
  message: Message,
  partType: 'content' | 'thought',
  block: ContentBlock,
): Message {
  const parts = message.parts;
  const lastPart = parts[parts.length - 1];
  if (partType === 'content' && lastPart?.type === 'content') {
    const blocks = lastPart.content;
    const lastBlock = blocks[blocks.length - 1];
    if (
      lastBlock?.type === 'text' &&
      block.type === 'text' &&
      !hasAnnotations(lastBlock) &&
      !hasAnnotations(block)
    ) {
      return {
        ...message,
        parts: [
          ...parts.slice(0, -1),
          {
            ...lastPart,
            content: [...blocks.slice(0, -1), { ...lastBlock, text: lastBlock.text + block.text }],
          },
        ],
      };
    }
    return {
      ...message,
      parts: [...parts.slice(0, -1), { ...lastPart, content: [...blocks, block] }],
    };
  }
  if (partType === 'thought' && lastPart?.type === 'thought') {
    const blocks = lastPart.thought;
    const lastBlock = blocks[blocks.length - 1];
    if (
      lastBlock?.type === 'text' &&
      block.type === 'text' &&
      !hasAnnotations(lastBlock) &&
      !hasAnnotations(block)
    ) {
      return {
        ...message,
        parts: [
          ...parts.slice(0, -1),
          {
            ...lastPart,
            thought: [...blocks.slice(0, -1), { ...lastBlock, text: lastBlock.text + block.text }],
          },
        ],
      };
    }
    return {
      ...message,
      parts: [...parts.slice(0, -1), { ...lastPart, thought: [...blocks, block] }],
    };
  }
  return partType === 'content'
    ? { ...message, parts: [...parts, { type: 'content', content: [block] }] }
    : { ...message, parts: [...parts, { type: 'thought', thought: [block], expanded: false }] };
}

function updateOrCreateMessage(
  data: SessionData,
  messageId: string,
  role: Message['role'],
  timestamp: number,
  update: (message: Message) => Message,
): Message[] {
  const index = data.messages.findIndex((message) => message.id === messageId);
  if (index < 0) {
    return [...data.messages, update({ id: messageId, role, parts: [], timestamp })];
  }
  const messages = [...data.messages];
  messages[index] = update(messages[index]);
  return messages;
}

export function appendSessionContent(
  data: SessionData,
  messageId: string,
  role: Message['role'],
  block: ContentBlock,
  timestamp: number,
): SessionData {
  return {
    ...data,
    messages: updateOrCreateMessage(data, messageId, role, timestamp, (message) =>
      appendBlockToMessage(message, 'content', block),
    ),
  };
}

export function appendSessionThought(
  data: SessionData,
  messageId: string,
  role: Message['role'],
  block: ContentBlock,
  timestamp: number,
): SessionData {
  return {
    ...data,
    messages: updateOrCreateMessage(data, messageId, role, timestamp, (message) =>
      appendBlockToMessage(message, 'thought', block),
    ),
  };
}

function withToolInMessages(
  messages: Message[],
  tool: ToolCallState,
  messageId: string,
  timestamp: number,
): Message[] {
  let found = false;
  const reconciled = messages.map((message) => {
    let changed = false;
    const parts = message.parts.map((part) => {
      if (part.type !== 'tool_calls') return part;
      const index = part.toolCalls.findIndex((candidate) => candidate.toolCallId === tool.toolCallId);
      if (index < 0) return part;
      found = true;
      changed = true;
      const toolCalls = [...part.toolCalls];
      toolCalls[index] = tool;
      return { ...part, toolCalls };
    });
    return changed ? { ...message, parts } : message;
  });
  if (found) return reconciled;
  return updateOrCreateMessage(
    { ...createSessionData(), messages: reconciled },
    messageId,
    'agent',
    timestamp,
    (message) => {
      const lastPart = message.parts[message.parts.length - 1];
      if (lastPart?.type === 'tool_calls') {
        return {
          ...message,
          parts: [
            ...message.parts.slice(0, -1),
            { ...lastPart, toolCalls: [...lastPart.toolCalls, tool] },
          ],
        };
      }
      return { ...message, parts: [...message.parts, { type: 'tool_calls', toolCalls: [tool] }] };
    },
  );
}

export function upsertSessionToolCall(
  data: SessionData,
  incoming: ToolCallState,
  messageId: string,
  timestamp: number,
): SessionData {
  const existing = data.pendingToolCalls.get(incoming.toolCallId);
  const tool: ToolCallState = {
    ...incoming,
    content: incoming.content ?? [],
    locations: incoming.locations ?? [],
    expanded: existing?.expanded ?? false,
  };
  const pendingToolCalls = new Map(data.pendingToolCalls);
  pendingToolCalls.set(tool.toolCallId, tool);
  return {
    ...data,
    pendingToolCalls,
    messages: withToolInMessages(data.messages, tool, messageId, timestamp),
  };
}

export function patchSessionToolCall(
  data: SessionData,
  toolCallId: string,
  update: ToolCallUpdate,
): SessionData {
  const existing = data.pendingToolCalls.get(toolCallId);
  if (!existing) return data;
  const tool: ToolCallState = { ...existing };
  if (update.title != null) tool.title = update.title;
  if (update.kind != null) tool.kind = update.kind;
  if (update.status != null) tool.status = update.status;
  if ('content' in update) tool.content = update.content ?? [];
  if ('locations' in update) tool.locations = update.locations ?? [];
  if ('rawInput' in update) tool.rawInput = update.rawInput;
  if ('rawOutput' in update) tool.rawOutput = update.rawOutput;
  const pendingToolCalls = new Map(data.pendingToolCalls);
  pendingToolCalls.set(toolCallId, tool);
  return {
    ...data,
    pendingToolCalls,
    messages: withToolInMessages(data.messages, tool, '', 0),
  };
}

export function replaceSessionPlan(
  data: SessionData,
  entries: PlanEntry[],
  messageId: string,
  timestamp: number,
): SessionData {
  const ownerId = data.planMessageId ?? messageId;
  const messages = updateOrCreateMessage(data, ownerId, 'agent', timestamp, (message) => {
    const existingPlanIndex = message.parts.findIndex((part) => part.type === 'plan');
    if (existingPlanIndex < 0) {
      return { ...message, parts: [...message.parts, { type: 'plan', plan: entries }] };
    }
    const parts = [...message.parts];
    parts[existingPlanIndex] = { type: 'plan', plan: entries };
    return { ...message, parts };
  });
  return { ...data, plan: entries, planMessageId: ownerId, messages };
}

export function setSessionConfigOptions(
  data: SessionData,
  configOptions: SessionConfigOption[],
): SessionData {
  return { ...data, configOptions };
}

export function setSessionPartExpanded(
  data: SessionData,
  messageId: string,
  partIndex: number,
  expanded: boolean,
): SessionData {
  return {
    ...data,
    messages: data.messages.map((message) => {
      if (message.id !== messageId) return message;
      const part = message.parts[partIndex];
      if (!part || part.type !== 'thought') return message;
      const parts = [...message.parts];
      parts[partIndex] = { ...part, expanded };
      return { ...message, parts };
    }),
  };
}

export function setSessionToolExpanded(
  data: SessionData,
  toolCallId: string,
  expanded: boolean,
): SessionData {
  const existing = data.pendingToolCalls.get(toolCallId);
  if (!existing) return data;
  const pendingToolCalls = new Map(data.pendingToolCalls);
  const tool = { ...existing, expanded };
  pendingToolCalls.set(toolCallId, tool);
  return {
    ...data,
    pendingToolCalls,
    messages: withToolInMessages(data.messages, tool, '', 0),
  };
}

function rejectPendingPermissions(requests: PermissionRequest[] | undefined): PermissionRequest[] {
  for (const request of requests ?? []) {
    try {
      request.reject();
    } catch {
      // One consumer callback must not prevent the remaining requests from settling.
    }
  }
  return [];
}

function updateSession(
  set: (updater: (state: SessionStoreState) => Partial<SessionStoreState> | SessionStoreState) => void,
  sessionId: SessionId,
  updater: (data: SessionData) => SessionData,
): void {
  set((state) => {
    const data = state.sessions.get(sessionId);
    if (!data) return state;
    const sessions = new Map(state.sessions);
    sessions.set(sessionId, updater(data));
    return { sessions };
  });
}

export const sessionStore = createStore<SessionStoreState>((set) => ({
  sessions: new Map(),
  ensureSession: (id) => set((state) => {
    if (state.sessions.has(id)) return state;
    const sessions = new Map(state.sessions);
    sessions.set(id, createSessionData());
    return { sessions };
  }),
  removeSession: (id) => {
    rejectPendingPermissions(sessionStore.getState().sessions.get(id)?.pendingPermissions);
    set((state) => {
      const sessions = new Map(state.sessions);
      sessions.delete(id);
      return { sessions };
    });
  },
  resetSession: (id) => {
    rejectPendingPermissions(sessionStore.getState().sessions.get(id)?.pendingPermissions);
    set((state) => {
      const sessions = new Map(state.sessions);
      sessions.set(id, createSessionData());
      return { sessions };
    });
  },
  addMessage: (id, message) => updateSession(set, id, (data) => ({ ...data, messages: [...data.messages, message] })),
  updateMessage: (id, messageId, update) => updateSession(set, id, (data) => ({
    ...data,
    messages: data.messages.map((message) => message.id === messageId ? { ...message, ...update } : message),
  })),
  appendContent: (id, messageId, role, block) => updateSession(set, id, (data) =>
    appendSessionContent(data, messageId, role, block, Date.now()),
  ),
  appendThought: (id, messageId, role, block) => updateSession(set, id, (data) =>
    appendSessionThought(data, messageId, role, block, Date.now()),
  ),
  setIsStreaming: (id, value) => updateSession(set, id, (data) => ({ ...data, isStreaming: value })),
  setStopReason: (id, reason) => updateSession(set, id, (data) => {
    if (data.messages.length === 0) return data;
    const messages = [...data.messages];
    messages[messages.length - 1] = { ...messages[messages.length - 1], stopReason: reason };
    return { ...data, messages };
  }),
  upsertToolCall: (id, tool) => updateSession(set, id, (data) =>
    upsertSessionToolCall(data, tool, generateId('msg'), Date.now()),
  ),
  updateToolCall: (id, toolId, update) => updateSession(set, id, (data) =>
    patchSessionToolCall(data, toolId, { toolCallId: toolId, ...update }),
  ),
  addPermissionRequest: (id, request) => updateSession(set, id, (data) => ({
    ...data,
    pendingPermissions: [...data.pendingPermissions, request],
  })),
  removePermissionRequest: (id, requestId) => updateSession(set, id, (data) => ({
    ...data,
    pendingPermissions: data.pendingPermissions.filter((request) => request.id !== requestId),
  })),
  rejectAllPermissions: (id) => {
    rejectPendingPermissions(sessionStore.getState().sessions.get(id)?.pendingPermissions);
    updateSession(set, id, (data) => ({ ...data, pendingPermissions: [] }));
  },
  setPlan: (id, entries) => updateSession(set, id, (data) =>
    replaceSessionPlan(data, entries, generateId('plan'), Date.now()),
  ),
  setUsage: (id, usage) => updateSession(set, id, (data) => ({ ...data, usage })),
  setConfigOptions: (id, options) => updateSession(set, id, (data) => setSessionConfigOptions(data, options)),
  setAvailableCommands: (id, commands) => updateSession(set, id, (data) => ({ ...data, availableCommands: commands })),
  setPartExpanded: (id, messageId, partIndex, expanded) => updateSession(set, id, (data) =>
    setSessionPartExpanded(data, messageId, partIndex, expanded),
  ),
  setToolCallExpanded: (id, toolCallId, expanded) => updateSession(set, id, (data) =>
    setSessionToolExpanded(data, toolCallId, expanded),
  ),
}));
