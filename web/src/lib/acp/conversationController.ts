import type { ContentBlock, PromptRequest } from '@agentclientprotocol/sdk';
import type { BrowserAction, ServerEnvelope, TurnDeliveryChoice } from './contracts';
import {
  createConversationState,
  projectConversationSnapshot,
  reduceConversationState,
  type ConversationCursor,
  type ConversationSnapshot,
  type ConversationState,
  type ConversationStateDependencies,
} from './conversationState';
import type { PanelsTransport, PanelsTransportCallbacks } from './panelsTransport';

export interface ConversationActionResult {
  readonly ok: boolean;
  readonly reason?: string;
  readonly clientMessageId?: string;
}

export interface ConversationController {
  attach(): void;
  snapshot(): ConversationSnapshot;
  subscribe(listener: (snapshot: ConversationSnapshot) => void): () => void;
  prompt(contentBlocks: readonly ContentBlock[], deliveryChoice: TurnDeliveryChoice): ConversationActionResult;
  cancelActive(): ConversationActionResult;
  cancelQueued(clientMessageId: string): ConversationActionResult;
  newConversation(): ConversationActionResult;
  respondToPermission(requestId: string, optionId: string): ConversationActionResult;
  setThoughtExpanded(messageId: string, partIndex: number, expanded: boolean): void;
  setToolExpanded(toolCallId: string, expanded: boolean): void;
  dispose(): void;
}

export interface ConversationControllerOptions {
  employeeId: string;
  deferInitialAttach?: boolean;
  transportFactory(callbacks: PanelsTransportCallbacks): PanelsTransport;
  reconnectDelayMs: number;
  setTimer(callback: () => void, delayMs: number): unknown;
  clearTimer(timer: unknown): void;
  now(): number;
  fallbackId(turnOrdinal: number, role: 'user' | 'agent', segmentOrdinal: number): string;
  clientMessageId(): string;
  initialState?: ConversationState;
}

interface SocketEpoch {
  epoch: number;
  transport: PanelsTransport;
  blocked: boolean;
}

interface PendingInitialPrompt {
  readonly contentBlocks: readonly ContentBlock[];
  readonly deliveryChoice: TurnDeliveryChoice;
  readonly clientMessageId: string;
}

const GAP_ERROR = 'Conversation updates were missed. Reconnecting…';
const INVALID_ERROR = 'Conversation data could not be read. Reconnecting…';
export const REPLAY_UNAVAILABLE_CLOSE_REASON = 'conversation replay unavailable; retry';
export const REPLAY_UNAVAILABLE_ERROR = 'Conversation replay is unavailable. Reconnecting…';

function cursorFor(envelope: ServerEnvelope): ConversationCursor {
  return {
    entityKind: envelope.entityKind,
    entityId: envelope.entityId,
    acpSessionId: envelope.acpSessionId,
    bindingGeneration: envelope.bindingGeneration,
    sequence: envelope.sequence,
  };
}

function sameIdentity(cursor: ConversationCursor, envelope: ServerEnvelope): boolean {
  return cursor.entityKind === envelope.entityKind
    && cursor.entityId === envelope.entityId
    && cursor.acpSessionId === envelope.acpSessionId;
}

function sameAttachedEntity(cursor: ConversationCursor, envelope: ServerEnvelope): boolean {
  return cursor.entityKind === envelope.entityKind && cursor.entityId === envelope.entityId;
}

function activeTurn(snapshot: ConversationSnapshot): boolean {
  return ['thinking', 'working', 'compacting', 'waiting_for_permission'].includes(
    snapshot.activity?.state ?? '',
  );
}

export function createConversationController(options: ConversationControllerOptions): ConversationController {
  const dependencies: ConversationStateDependencies = {
    now: options.now,
    fallbackId: options.fallbackId,
  };
  let committedState = options.initialState ?? createConversationState(options.employeeId);
  let replayCandidate: ConversationState | null = null;
  let currentEpoch: SocketEpoch | null = null;
  let nextEpoch = 1;
  let failedEpoch: number | null = null;
  let reconnectTimer: unknown | null = null;
  let attached = false;
  let disposed = false;
  let pendingInitialPrompt: PendingInitialPrompt | null = null;
  const subscribers = new Set<(snapshot: ConversationSnapshot) => void>();

  const publish = (): void => {
    if (disposed) return;
    const snapshot = projectConversationSnapshot(committedState);
    for (const subscriber of subscribers) subscriber(snapshot);
  };

  const transition = (
    nextTransition: Parameters<typeof reduceConversationState>[1],
    shouldPublish = true,
  ): void => {
    committedState = reduceConversationState(committedState, nextTransition, dependencies);
    if (shouldPublish) publish();
  };

  const send = (action: BrowserAction): ConversationActionResult => {
    if (disposed) return { ok: false, reason: 'Conversation has been disposed' };
    const result = currentEpoch?.transport.send(action) ?? {
      ok: false as const,
      reason: 'Conversation is not connected',
    };
    return result.ok ? { ok: true } : result;
  };

  const deliverPrompt = (
    contentBlocks: readonly ContentBlock[],
    choice: TurnDeliveryChoice,
    clientMessageId: string,
  ): ConversationActionResult => {
    const cursor = committedState.cursor;
    if (!cursor) return { ok: false, reason: 'Conversation is not ready', clientMessageId };
    const optimisticPrompt: PromptRequest = {
      sessionId: cursor.acpSessionId ?? '',
      prompt: [...contentBlocks],
    };
    transition({ kind: 'optimistic_prompt', clientMessageId, prompt: optimisticPrompt });
    const result = send({
      type: 'prompt',
      employeeId: options.employeeId,
      clientMessageId,
      prompt: [...contentBlocks],
      deliveryChoice: choice,
    });
    if (!result.ok) {
      transition({
        kind: 'local_delivery_rejected',
        clientMessageId,
        choice,
        reason: result.reason ?? 'Conversation action could not be sent',
      });
    }
    return { ...result, clientMessageId };
  };

  const deliverPendingInitialPrompt = (): void => {
    const pending = pendingInitialPrompt;
    if (!pending) return;
    pendingInitialPrompt = null;
    deliverPrompt(pending.contentBlocks, pending.deliveryChoice, pending.clientMessageId);
  };

  const scheduleReconnect = (): void => {
    if (disposed || reconnectTimer !== null) return;
    reconnectTimer = options.setTimer(() => {
      reconnectTimer = null;
      if (!disposed) openSocket();
    }, options.reconnectDelayMs);
  };

  const closeEpochForRecovery = (epoch: SocketEpoch, message: string): void => {
    if (disposed || epoch.blocked || currentEpoch?.epoch !== epoch.epoch) return;
    epoch.blocked = true;
    failedEpoch = epoch.epoch;
    replayCandidate = null;
    transition({ kind: 'recoverable_connection_error', message });
    epoch.transport.detach();
    epoch.transport.close();
    currentEpoch = null;
    scheduleReconnect();
  };

  const reduceAdmittedEnvelope = (
    epoch: SocketEpoch,
    envelope: ServerEnvelope,
    reset: boolean,
    preserveProtocolRejections = false,
  ): void => {
    let protocolState = replayCandidate ?? committedState;
    if (reset) {
      protocolState = reduceConversationState(
        protocolState,
        { kind: 'session_reset', preserveProtocolRejections },
        dependencies,
      );
    }
    const cursor = cursorFor(envelope);
    protocolState = reduceConversationState(
      protocolState,
      { kind: 'server_envelope', envelope, cursor },
      dependencies,
    );
    if (
      envelope.type === 'connection'
      && envelope.payload.state === 'ready'
      && failedEpoch !== null
      && epoch.epoch > failedEpoch
    ) {
      protocolState = reduceConversationState(
        protocolState,
        { kind: 'clear_recoverable_connection_error' },
        dependencies,
      );
      failedEpoch = null;
    }
    if (reset || replayCandidate !== null) {
      replayCandidate = protocolState;
    } else {
      committedState = protocolState;
    }
    if (envelope.type === 'connection' && envelope.payload.state === 'ready') {
      if (replayCandidate !== null) {
        committedState = replayCandidate;
        replayCandidate = null;
      }
      publish();
      deliverPendingInitialPrompt();
    } else if (replayCandidate === null) {
      publish();
    }
  };

  const admitEnvelope = (epoch: SocketEpoch, envelope: ServerEnvelope): void => {
    if (disposed || epoch.blocked || currentEpoch?.epoch !== epoch.epoch) return;
    if (envelope.employeeId !== options.employeeId) {
      closeEpochForRecovery(epoch, INVALID_ERROR);
      return;
    }
    const cursor = (replayCandidate ?? committedState).cursor;
    if (!cursor) {
      if (
        envelope.type !== 'connection'
        || envelope.payload.state !== 'reset'
        || envelope.payload.resetBindingGeneration !== envelope.bindingGeneration
      ) {
        closeEpochForRecovery(epoch, INVALID_ERROR);
        return;
      }
      reduceAdmittedEnvelope(epoch, envelope, true);
      return;
    }
    if (envelope.bindingGeneration < cursor.bindingGeneration) return;
    if (envelope.bindingGeneration === cursor.bindingGeneration) {
      const isReset = envelope.type === 'connection' && envelope.payload.state === 'reset';
      const activatesEmptyConversation = isReset
        && cursor.acpSessionId === null
        && envelope.acpSessionId !== null;
      if (!sameIdentity(cursor, envelope) && !activatesEmptyConversation) {
        closeEpochForRecovery(epoch, INVALID_ERROR);
        return;
      }
      if (envelope.sequence <= cursor.sequence) return;
      if (isReset && envelope.payload.resetBindingGeneration !== envelope.bindingGeneration) {
        closeEpochForRecovery(epoch, INVALID_ERROR);
        return;
      }
      if (isReset) {
        if (envelope.sequence !== cursor.sequence + 1 && epoch.epoch === 1) {
          closeEpochForRecovery(epoch, GAP_ERROR);
          return;
        }
        reduceAdmittedEnvelope(epoch, envelope, true, true);
        return;
      }
      if (envelope.sequence !== cursor.sequence + 1) {
        closeEpochForRecovery(epoch, GAP_ERROR);
        return;
      }
      reduceAdmittedEnvelope(epoch, envelope, false);
      return;
    }
    if (!sameAttachedEntity(cursor, envelope)) {
      closeEpochForRecovery(epoch, INVALID_ERROR);
      return;
    }
    const isGenerationReset = envelope.type === 'connection'
      && envelope.payload.state === 'reset'
      && envelope.payload.resetBindingGeneration === envelope.bindingGeneration;
    if (!isGenerationReset) {
      closeEpochForRecovery(epoch, INVALID_ERROR);
      return;
    }
    reduceAdmittedEnvelope(epoch, envelope, true);
  };

  const handleSocketEnd = (epoch: SocketEpoch, stateName: 'closed' | 'error'): void => {
    if (disposed || epoch.blocked || currentEpoch?.epoch !== epoch.epoch) return;
    epoch.blocked = true;
    replayCandidate = null;
    epoch.transport.detach();
    epoch.transport.close();
    currentEpoch = null;
    transition({
      kind: 'local_connection',
      state: stateName,
      detail: stateName === 'closed' ? 'Conversation closed' : 'Conversation unavailable',
    });
    scheduleReconnect();
  };

  function openSocket(): void {
    if (disposed || currentEpoch) return;
    transition({ kind: 'local_connection', state: 'connecting', detail: 'Connecting' });
    const epochNumber = nextEpoch++;
    let epoch!: SocketEpoch;
    const callbacks: PanelsTransportCallbacks = {
      onOpen: () => {
        if (disposed || epoch.blocked || currentEpoch?.epoch !== epochNumber) return;
        transition({ kind: 'local_connection', state: 'open', detail: 'Connected' });
        const cursor = committedState.cursor;
        const action: BrowserAction = cursor
          ? {
              type: 'attach',
              employeeId: options.employeeId,
              lastSeenBindingGeneration: cursor.bindingGeneration,
              lastSeenSequence: cursor.sequence,
            }
          : { type: 'attach', employeeId: options.employeeId };
        const result = epoch.transport.send(action);
        if (!result.ok) closeEpochForRecovery(epoch, INVALID_ERROR);
      },
      onEnvelope: (envelope) => admitEnvelope(epoch, envelope),
      onInvalidEnvelope: () => closeEpochForRecovery(epoch, INVALID_ERROR),
      onClose: (reason) => {
        if (reason === REPLAY_UNAVAILABLE_CLOSE_REASON) {
          closeEpochForRecovery(epoch, REPLAY_UNAVAILABLE_ERROR);
          return;
        }
        handleSocketEnd(epoch, 'closed');
      },
      onError: () => handleSocketEnd(epoch, 'error'),
    };
    epoch = { epoch: epochNumber, transport: options.transportFactory(callbacks), blocked: false };
    currentEpoch = epoch;
    epoch.transport.open();
  }

  return {
    attach(): void {
      if (disposed || attached) return;
      attached = true;
      openSocket();
    },
    snapshot(): ConversationSnapshot {
      return projectConversationSnapshot(committedState);
    },
    subscribe(listener): () => void {
      if (disposed) return () => undefined;
      subscribers.add(listener);
      listener(projectConversationSnapshot(committedState));
      return () => subscribers.delete(listener);
    },
    prompt(contentBlocks, requestedChoice): ConversationActionResult {
      const snapshot = projectConversationSnapshot(committedState);
      if (!snapshot.cursor) {
        if (!options.deferInitialAttach) return { ok: false, reason: 'Conversation is not ready' };
        if (pendingInitialPrompt) {
          return { ok: false, reason: 'The first message is waiting for the conversation to connect' };
        }
        const clientMessageId = options.clientMessageId();
        pendingInitialPrompt = {
          contentBlocks: [...contentBlocks],
          deliveryChoice: 'normal',
          clientMessageId,
        };
        this.attach();
        return { ok: true, clientMessageId };
      }
      const isActive = activeTurn(snapshot);
      const choice: TurnDeliveryChoice = isActive ? requestedChoice : 'normal';
      if (choice === 'steer' && !snapshot.connection.supportsSteer) {
        return { ok: false, reason: 'Steer is unavailable for this employee' };
      }
      const clientMessageId = options.clientMessageId();
      return deliverPrompt(contentBlocks, choice, clientMessageId);
    },
    cancelActive(): ConversationActionResult {
      return send({ type: 'cancel', employeeId: options.employeeId });
    },
    cancelQueued(clientMessageId): ConversationActionResult {
      return send({
        type: 'cancel',
        employeeId: options.employeeId,
        queuedClientMessageId: clientMessageId,
      });
    },
    newConversation(): ConversationActionResult {
      return send({ type: 'new_conversation', employeeId: options.employeeId });
    },
    respondToPermission(requestId, optionId): ConversationActionResult {
      const permission = committedState.permissions[requestId];
      if (!permission || permission.submittingOptionId) {
        return { ok: false, reason: 'Permission request is no longer pending' };
      }
      const optionExists = permission.request.request.options.some((option) => option.optionId === optionId);
      if (!optionExists) return { ok: false, reason: 'Permission option was not offered by the agent' };
      transition({ kind: 'permission_response_pending', requestId, optionId });
      const result = send({
        type: 'permission_response',
        employeeId: options.employeeId,
        requestId,
        optionId,
      });
      if (!result.ok) {
        transition({ kind: 'permission_response_failed', requestId, optionId });
      }
      return result;
    },
    setThoughtExpanded(messageId, partIndex, expanded): void {
      transition({ kind: 'thought_expanded', messageId, partIndex, expanded });
    },
    setToolExpanded(toolCallId, expanded): void {
      transition({ kind: 'tool_expanded', toolCallId, expanded });
    },
    dispose(): void {
      if (disposed) return;
      disposed = true;
      pendingInitialPrompt = null;
      subscribers.clear();
      if (reconnectTimer !== null) {
        options.clearTimer(reconnectTimer);
        reconnectTimer = null;
      }
      currentEpoch?.transport.detach();
      currentEpoch?.transport.close();
      currentEpoch = null;
      replayCandidate = null;
      committedState = reduceConversationState(committedState, { kind: 'dispose' }, dependencies);
    },
  };
}
