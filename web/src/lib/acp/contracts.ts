import type {
  PromptRequest,
  RequestPermissionRequest,
  RequestPermissionResponse,
  SessionNotification,
  TerminalOutputResponse,
} from '@agentclientprotocol/sdk';
import type { Message, Session } from '../../vendor/acp-components-core/src/types/index';

// These imports keep the browser transcript state anchored to the pinned donor; ACP payload unions
// themselves remain owned by @agentclientprotocol/sdk. Message and Session are consumed by ACP-03
// rather than redeclared in this contract-only slice.

export type ConversationEntityKind = 'ticket' | 'agent';
export type ConversationActivityState =
  | 'connecting'
  | 'loading'
  | 'idle'
  | 'thinking'
  | 'working'
  | 'compacting'
  | 'waiting_for_permission'
  | 'interrupted'
  | 'failed';
export type TurnDeliveryChoice = 'normal' | 'steer' | 'send_now' | 'queue';
export type TurnDeliveryReceiptState =
  | 'accepted'
  | 'queued'
  | 'started'
  | 'interrupted'
  | 'rejected';

export interface ConversationActivity {
  state: ConversationActivityState;
  detail: string;
  sequence: number;
}

export interface TurnDeliveryReceipt {
  clientMessageId: string;
  choice: TurnDeliveryChoice;
  state: TurnDeliveryReceiptState;
  queuePosition?: number;
  reason?: string;
}

export interface QueuedPrompt {
  clientMessageId: string;
  prompt: PromptRequest;
  enqueueSequence: number;
  enqueuedAt: number;
}

export interface QueueSnapshot {
  items: QueuedPrompt[];
}

export interface ContextCompaction {
  boundaryId: string;
  state: 'compacting' | 'compacted' | 'failed';
  trigger: 'explicit' | 'automatic';
  reason?: string;
}

export interface ConversationPermissionRequest {
  requestId: string;
  employeeId: string;
  backendKey: string;
  request: RequestPermissionRequest;
  lifecycle: 'pending' | 'answered' | 'cancelled';
  deadlineAt: number;
  openedSequence: number;
}

export interface ConversationPermissionOutcome {
  requestId: string;
  response: RequestPermissionResponse;
  cancellationReason?: string;
  settledSequence: number;
}

export interface ConnectionPayload {
  state: 'reset' | 'ready' | 'closed' | 'error';
  detail: string;
  supportsSteer: boolean;
  resetBindingGeneration?: number;
}

export interface ProtocolUpdateRejectedPayload {
  rejectedSessionUpdate: string;
  reason: string;
  status: 'Agent sent an unsupported update';
}

export interface HumanEcho {
  clientMessageId: string;
  prompt: PromptRequest;
}

export interface ProgrammaticPrompt {
  promptId: string;
  prompt: PromptRequest;
  source: 'worker' | 'role';
}

export interface ConversationTerminalState {
  terminalId: string;
  lifecycle: 'active' | 'released';
  terminalOutput: TerminalOutputResponse;
}

interface ServerEnvelopeBase {
  wireVersion: 1;
  employeeId: string;
  entityKind: ConversationEntityKind;
  entityId: string;
  acpSessionId: string;
  bindingGeneration: number;
  sequence: number;
}

export type TerminalStateEnvelope = ServerEnvelopeBase & {
  type: 'terminal_state';
  payload: ConversationTerminalState;
};

export type ServerEnvelope =
  | (ServerEnvelopeBase & { type: 'acp_session_update'; payload: SessionNotification })
  | (ServerEnvelopeBase & { type: 'activity'; payload: ConversationActivity })
  | (ServerEnvelopeBase & { type: 'delivery_receipt'; payload: TurnDeliveryReceipt })
  | (ServerEnvelopeBase & { type: 'queue_snapshot'; payload: QueueSnapshot })
  | (ServerEnvelopeBase & { type: 'context_compaction'; payload: ContextCompaction })
  | (ServerEnvelopeBase & { type: 'permission_request'; payload: ConversationPermissionRequest })
  | (ServerEnvelopeBase & { type: 'permission_outcome'; payload: ConversationPermissionOutcome })
  | (ServerEnvelopeBase & { type: 'connection'; payload: ConnectionPayload })
  | (ServerEnvelopeBase & {
      type: 'protocol_update_rejected';
      payload: ProtocolUpdateRejectedPayload;
    })
  | (ServerEnvelopeBase & { type: 'human_echo'; payload: HumanEcho })
  | (ServerEnvelopeBase & { type: 'programmatic_prompt'; payload: ProgrammaticPrompt })
  | TerminalStateEnvelope;

export type BrowserAction =
  | {
      type: 'attach';
      employeeId: string;
      lastSeenBindingGeneration?: number;
      lastSeenSequence?: number;
    }
  | {
      type: 'prompt';
      employeeId: string;
      clientMessageId: string;
      prompt: PromptRequest;
      deliveryChoice: TurnDeliveryChoice;
    }
  | { type: 'cancel'; employeeId: string; queuedClientMessageId?: string }
  | { type: 'new_conversation'; employeeId: string }
  | { type: 'permission_response'; employeeId: string; requestId: string; optionId: string };
