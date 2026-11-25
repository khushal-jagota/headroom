import type { Principal } from '../core/contracts.ts';
import type { PromptDeliveryFate, PromptDeliveryMode } from '../conversation/contracts.ts';
import type { WireValue } from '../executor/contracts.ts';
export class MessageRecordedToOwner {}
export class MessageAcceptedForDelivery {}
export interface MessageDeliveryResult {
  recipient: Principal;
  conversation_id: string | null;
  fate: PromptDeliveryFate | MessageRecordedToOwner | MessageAcceptedForDelivery;
}
/** An upload admission writes only through its owning area's write door. */
export interface MessageAdmission {
  preflight(current_conversation_id: string | null): Promise<MessageDeliveryResult | null>;
  finish(fate: PromptDeliveryFate): void;
  abort_before_dispatch(): void;
}

/** Authenticated custody that precedes recipient delivery. */
export interface AcceptedAgentMessage {
  readonly submission_id: string;
  readonly machine_id: string;
  readonly sender_id: string;
  readonly source_conversation_id: string;
  readonly source: Principal;
  readonly envelope: WireValue<'QueuedEnvelope'>;
  readonly recipient: Principal;
  readonly mode: PromptDeliveryMode;
  readonly message: string;
  readonly target_conversation_id: string | null;
  readonly recipient_order: number;
  readonly receipt: WireValue<'AcceptanceReceipt'>;
  readonly pending: boolean;
}

/** These exact bytes belong to home after acceptance. */
export interface RetainedMessageFile {
  readonly manifest: WireValue<'FileManifest'>;
  readonly bytes: Buffer;
}

export class RecipientDeliveryStale extends Error {}

export interface RecipientAttempt {
  readonly submission_id: string;
  readonly attempt_id: string;
}
