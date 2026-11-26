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
/** What later happened to an owner message that home accepted, read from stored rows only. */
export interface OwnerSendOutcome {
  readonly message_id: string;
  readonly conversation_id: string | null;
  readonly outcome: 'pending' | 'delivered' | 'refused' | 'discarded' | 'uncertain';
  readonly refusal_reason: string | null;
  readonly reason: string | null;
}
/**
 * Why home refused an owner message before it took custody. Each comes with its sentence in
 * the outcome's reason. Delivery refusals after custody keep the conversation's own reasons.
 */
export const OwnerSendRefusalReason = {
  /** The send itself was refused, for example a lifecycle fence or an invalid choice. */
  refused_before_acceptance: 'refused_before_acceptance',
  /** The recipient or its conversation no longer exists. */
  recipient_unavailable: 'recipient_unavailable',
  /** The recipient's machine can never join again: it has no machine credential. */
  machine_unavailable: 'machine_unavailable',
  conversation_save_failed: 'conversation_save_failed',
  home_restarted_before_acceptance: 'home_restarted_before_acceptance',
  recipient_conversation_replaced: 'recipient_conversation_replaced',
} as const;
export type OwnerSendRefusalReason =
  (typeof OwnerSendRefusalReason)[keyof typeof OwnerSendRefusalReason];
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
