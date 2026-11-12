import type { Principal } from '../core/contracts.ts';
import type { PromptDeliveryFate } from '../conversation/contracts.ts';
export class MessageRecordedToOwner {}
export interface MessageDeliveryResult {
  recipient: Principal;
  conversation_id: string | null;
  fate: PromptDeliveryFate | MessageRecordedToOwner;
}
/** An upload admission writes only through its owning area's write door. */
export interface MessageAdmission {
  preflight(current_conversation_id: string | null): Promise<MessageDeliveryResult | null>;
  finish(fate: PromptDeliveryFate): void;
  abort_before_dispatch(): void;
}
