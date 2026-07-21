import type { BrowserAction, ServerEnvelope } from './contracts';

export interface PanelsSocket {
  send(data: string): void;
  close(): void;
  onopen: (() => void) | null;
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event?: { reason?: unknown }) => void) | null;
  onerror: ((event?: unknown) => void) | null;
}

export interface PanelsTransportCallbacks {
  onOpen(): void;
  onEnvelope(envelope: ServerEnvelope): void;
  onInvalidEnvelope(reason: string): void;
  onClose(reason: string | null): void;
  onError(): void;
}

export interface PanelsTransport {
  open(): void;
  send(action: BrowserAction): { ok: true } | { ok: false; reason: string };
  close(): void;
  detach(): void;
}

export interface PanelsTransportOptions extends PanelsTransportCallbacks {
  url: string;
  socketFactory(url: string): PanelsSocket;
}

const envelopeTypes = new Set<ServerEnvelope['type']>([
  'acp_session_update',
  'activity',
  'delivery_receipt',
  'queue_snapshot',
  'context_compaction',
  'permission_request',
  'permission_outcome',
  'connection',
  'protocol_update_rejected',
  'human_echo',
  'terminal_state',
]);

const sessionUpdateTypes = new Set([
  'user_message_chunk',
  'agent_message_chunk',
  'agent_thought_chunk',
  'tool_call',
  'tool_call_update',
  'plan',
  'plan_update',
  'plan_removed',
  'available_commands_update',
  'current_mode_update',
  'config_option_update',
  'session_info_update',
  'usage_update',
]);

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value);
}

function isString(value: unknown): value is string {
  return typeof value === 'string' && value.length > 0;
}

function isInteger(value: unknown): value is number {
  return Number.isInteger(value) && Number(value) > 0;
}

function hasExactKeys(
  value: Record<string, unknown>,
  required: readonly string[],
  optional: readonly string[] = [],
): boolean {
  const keys = Object.keys(value);
  return required.every((key) => Object.hasOwn(value, key))
    && keys.every((key) => required.includes(key) || optional.includes(key));
}

function isPrompt(value: unknown, sessionId: string): boolean {
  return isRecord(value)
    && hasExactKeys(value, ['sessionId', 'prompt'], ['_meta'])
    && value.sessionId === sessionId
    && Array.isArray(value.prompt);
}

function isSessionNotification(value: unknown, sessionId: string): boolean {
  return isRecord(value)
    && hasExactKeys(value, ['sessionId', 'update'], ['_meta'])
    && value.sessionId === sessionId
    && isRecord(value.update)
    && isSessionUpdate(value.update);
}

function isSessionUpdate(update: Record<string, unknown>): boolean {
  if (typeof update.sessionUpdate !== 'string' || !sessionUpdateTypes.has(update.sessionUpdate)) return false;
  switch (update.sessionUpdate) {
    case 'user_message_chunk':
    case 'agent_message_chunk':
    case 'agent_thought_chunk':
      return isRecord(update.content)
        && typeof update.content.type === 'string'
        && (!('messageId' in update) || update.messageId === null || isString(update.messageId));
    case 'tool_call':
      return isString(update.toolCallId)
        && isString(update.title)
        && (!('content' in update) || update.content === null || Array.isArray(update.content))
        && (!('locations' in update) || update.locations === null || Array.isArray(update.locations));
    case 'tool_call_update':
      return isString(update.toolCallId)
        && (!('content' in update) || update.content === null || Array.isArray(update.content))
        && (!('locations' in update) || update.locations === null || Array.isArray(update.locations));
    case 'plan':
      return Array.isArray(update.entries);
    case 'available_commands_update':
      return Array.isArray(update.availableCommands)
        && update.availableCommands.every((command) => isRecord(command)
          && isString(command.name) && isString(command.description));
    case 'current_mode_update':
      return isString(update.currentModeId);
    case 'config_option_update':
      return Array.isArray(update.configOptions) && update.configOptions.every(isRecord);
    case 'session_info_update':
      return (!('title' in update) || update.title === null || typeof update.title === 'string')
        && (!('updatedAt' in update) || update.updatedAt === null || typeof update.updatedAt === 'string');
    case 'usage_update':
      return typeof update.used === 'number' && update.used >= 0
        && typeof update.size === 'number' && update.size >= 0;
    case 'plan_update':
    case 'plan_removed':
      return true;
    default:
      return false;
  }
}

function isActivity(value: Record<string, unknown>): boolean {
  return hasExactKeys(value, ['state', 'detail', 'sequence'])
    && ['connecting', 'loading', 'idle', 'thinking', 'working', 'compacting',
      'waiting_for_permission', 'interrupted', 'failed'].includes(String(value.state))
    && isString(value.detail)
    && isInteger(value.sequence);
}

function isReceipt(value: Record<string, unknown>): boolean {
  if (!hasExactKeys(value, ['clientMessageId', 'choice', 'state'], ['queuePosition', 'reason'])) return false;
  if (!isString(value.clientMessageId)
    || !['normal', 'steer', 'send_now', 'queue'].includes(String(value.choice))
    || !['accepted', 'queued', 'started', 'interrupted', 'rejected'].includes(String(value.state))) return false;
  if ('queuePosition' in value && !isInteger(value.queuePosition)) return false;
  if ('reason' in value && !isString(value.reason)) return false;
  if (value.state === 'queued' ? !isInteger(value.queuePosition) : 'queuePosition' in value) return false;
  if (value.state === 'rejected') return isString(value.reason);
  if (value.state === 'interrupted') return !('reason' in value) || isString(value.reason);
  return !('reason' in value);
}

function isQueuedPrompt(value: unknown, sessionId: string): boolean {
  return isRecord(value)
    && hasExactKeys(value, ['clientMessageId', 'prompt', 'enqueueSequence', 'enqueuedAt'])
    && isString(value.clientMessageId)
    && isPrompt(value.prompt, sessionId)
    && isInteger(value.enqueueSequence)
    && Number.isInteger(value.enqueuedAt);
}

function isCompaction(value: Record<string, unknown>): boolean {
  if (!hasExactKeys(value, ['boundaryId', 'state', 'trigger'], ['reason'])) return false;
  if (!isString(value.boundaryId)
    || !['compacting', 'compacted', 'failed'].includes(String(value.state))
    || !['explicit', 'automatic'].includes(String(value.trigger))) return false;
  if (value.state === 'failed') return isString(value.reason);
  return !('reason' in value);
}

function isPermissionRequest(value: Record<string, unknown>, sessionId: string): boolean {
  if (!hasExactKeys(value, [
    'requestId', 'employeeId', 'backendKey', 'request', 'lifecycle', 'deadlineAt', 'openedSequence',
  ])) return false;
  if (!isString(value.requestId) || !isString(value.employeeId) || !isString(value.backendKey)
    || value.lifecycle !== 'pending' || !Number.isInteger(value.deadlineAt) || !isInteger(value.openedSequence)
    || !isRecord(value.request)) return false;
  const request = value.request;
  return hasExactKeys(request, ['sessionId', 'toolCall', 'options'], ['_meta'])
    && request.sessionId === sessionId
    && isRecord(request.toolCall)
    && isString(request.toolCall.toolCallId)
    && Array.isArray(request.options)
    && request.options.every((option) => isRecord(option)
      && hasExactKeys(option, ['optionId', 'name', 'kind'], ['_meta'])
      && isString(option.optionId)
      && isString(option.name)
      && ['allow_once', 'allow_always', 'reject_once', 'reject_always'].includes(String(option.kind)));
}

function isPermissionOutcome(value: Record<string, unknown>): boolean {
  if (!hasExactKeys(value, ['requestId', 'response', 'settledSequence'], ['cancellationReason'])) return false;
  if (!isString(value.requestId) || !isInteger(value.settledSequence) || !isRecord(value.response)
    || !hasExactKeys(value.response, ['outcome'], ['_meta']) || !isRecord(value.response.outcome)) return false;
  const outcome = value.response.outcome;
  if (outcome.outcome === 'selected') {
    return hasExactKeys(outcome, ['outcome', 'optionId'], ['_meta']) && isString(outcome.optionId)
      && !('cancellationReason' in value);
  }
  return outcome.outcome === 'cancelled'
    && hasExactKeys(outcome, ['outcome'])
    && isString(value.cancellationReason);
}

function isConnection(value: Record<string, unknown>): boolean {
  if (!hasExactKeys(value, ['state', 'detail', 'supportsSteer'], ['resetBindingGeneration'])) return false;
  if (!['reset', 'ready', 'closed', 'error'].includes(String(value.state))
    || !isString(value.detail) || typeof value.supportsSteer !== 'boolean') return false;
  return value.state === 'reset'
    ? isInteger(value.resetBindingGeneration)
    : !('resetBindingGeneration' in value);
}

function isProtocolRejection(value: Record<string, unknown>): boolean {
  return hasExactKeys(value, ['rejectedSessionUpdate', 'reason', 'status'])
    && isString(value.rejectedSessionUpdate)
    && isString(value.reason)
    && value.status === 'Agent sent an unsupported update';
}

function isTerminalState(value: Record<string, unknown>): boolean {
  if (!hasExactKeys(value, ['terminalId', 'lifecycle', 'terminalOutput'])
    || !isString(value.terminalId)
    || !['active', 'released'].includes(String(value.lifecycle))
    || !isRecord(value.terminalOutput)) return false;
  const output = value.terminalOutput;
  if (!hasExactKeys(output, ['output', 'truncated'], ['exitStatus', '_meta'])
    || typeof output.output !== 'string' || typeof output.truncated !== 'boolean') return false;
  if (!('exitStatus' in output) || output.exitStatus == null) return true;
  return isRecord(output.exitStatus)
    && hasExactKeys(output.exitStatus, [], ['exitCode', 'signal', '_meta'])
    && (!('exitCode' in output.exitStatus)
      || output.exitStatus.exitCode === null
      || Number.isInteger(output.exitStatus.exitCode))
    && (!('signal' in output.exitStatus)
      || output.exitStatus.signal === null
      || typeof output.exitStatus.signal === 'string');
}

function validatePayload(value: Record<string, unknown>): boolean {
  const { type, payload, acpSessionId } = value;
  if (!isRecord(payload) || typeof acpSessionId !== 'string') return false;
  switch (type) {
    case 'acp_session_update':
      return isSessionNotification(payload, acpSessionId);
    case 'activity':
      return isActivity(payload) && payload.sequence === value.sequence;
    case 'delivery_receipt':
      return isReceipt(payload);
    case 'queue_snapshot':
      return hasExactKeys(payload, ['items']) && Array.isArray(payload.items)
        && payload.items.every((item) => isQueuedPrompt(item, acpSessionId));
    case 'context_compaction':
      return isCompaction(payload);
    case 'permission_request':
      return isPermissionRequest(payload, acpSessionId)
        && payload.employeeId === value.employeeId
        && payload.openedSequence === value.sequence;
    case 'permission_outcome':
      return isPermissionOutcome(payload) && payload.settledSequence === value.sequence;
    case 'connection':
      return isConnection(payload);
    case 'protocol_update_rejected':
      return isProtocolRejection(payload);
    case 'human_echo':
      return hasExactKeys(payload, ['clientMessageId', 'prompt'])
        && isString(payload.clientMessageId)
        && isPrompt(payload.prompt, acpSessionId);
    case 'terminal_state':
      return isTerminalState(payload);
    default:
      return false;
  }
}

export function validateServerEnvelope(value: unknown):
  | { ok: true; envelope: ServerEnvelope }
  | { ok: false; reason: string } {
  if (!isRecord(value)) return { ok: false, reason: 'Envelope must be an object' };
  if (!hasExactKeys(value, [
    'wireVersion', 'employeeId', 'entityKind', 'entityId', 'acpSessionId',
    'bindingGeneration', 'sequence', 'type', 'payload',
  ])) return { ok: false, reason: 'Envelope fields did not match the ACP browser contract' };
  if (value.wireVersion !== 1
    || !isString(value.employeeId)
    || !['ticket', 'agent'].includes(String(value.entityKind))
    || !isString(value.entityId)
    || !isString(value.acpSessionId)
    || !isInteger(value.bindingGeneration)
    || !isInteger(value.sequence)
    || typeof value.type !== 'string'
    || !envelopeTypes.has(value.type as ServerEnvelope['type'])
    || !validatePayload(value)) {
    return { ok: false, reason: 'Envelope values did not match the ACP browser contract' };
  }
  return { ok: true, envelope: value as unknown as ServerEnvelope };
}

export function createPanelsTransport(options: PanelsTransportOptions): PanelsTransport {
  let socket: PanelsSocket | null = null;
  let opened = false;
  let detached = false;
  let terminalReported = false;
  const detach = (): void => {
    detached = true;
    if (!socket) return;
    socket.onopen = null;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
  };
  return {
    open(): void {
      if (socket || detached) return;
      socket = options.socketFactory(options.url);
      socket.onopen = () => {
        if (detached) return;
        opened = true;
        options.onOpen();
      };
      socket.onmessage = (event) => {
        if (detached) return;
        if (typeof event.data !== 'string') {
          options.onInvalidEnvelope('Conversation data was not text');
          return;
        }
        let parsed: unknown;
        try {
          parsed = JSON.parse(event.data);
        } catch {
          options.onInvalidEnvelope('Conversation data was not valid JSON');
          return;
        }
        const validated = validateServerEnvelope(parsed);
        if (validated.ok) options.onEnvelope(validated.envelope);
        else options.onInvalidEnvelope(validated.reason);
      };
      socket.onerror = () => {
        if (detached || terminalReported) return;
        terminalReported = true;
        opened = false;
        options.onError();
      };
      socket.onclose = (event) => {
        if (detached || terminalReported) return;
        terminalReported = true;
        opened = false;
        const reason = typeof event?.reason === 'string' && event.reason.length > 0
          ? event.reason
          : null;
        options.onClose(reason);
      };
    },
    send(action): { ok: true } | { ok: false; reason: string } {
      if (!socket || !opened || detached || terminalReported) {
        return { ok: false, reason: 'Conversation is not connected' };
      }
      try {
        socket.send(JSON.stringify(action));
        return { ok: true };
      } catch {
        return { ok: false, reason: 'Conversation action could not be sent' };
      }
    },
    close(): void {
      opened = false;
      socket?.close();
    },
    detach,
  };
}
