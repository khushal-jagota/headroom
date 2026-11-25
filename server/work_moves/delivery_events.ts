/** A queued recovery finishes only when the record commits its actual delivery outcome. */
import type { TransactionSql } from '../core/database/contracts.ts';
import type { ConversationEventPayload } from '../conversation/events.ts';
import {
  PromptEventPayload,
  PromptDeliveryRefusedEventPayload,
  PromptDeliveryUncertainEventPayload,
  PromptDiscardedEventPayload,
} from '../conversation/events.ts';
import { read_active_operations, set_phase, write_operation } from './operations.ts';

export function observe_move_delivery(
  tx: TransactionSql,
  conversation_id: string,
  payloads: readonly ConversationEventPayload[],
  now_ms: number,
): void {
  const outcome = payloads.find(
    (payload) =>
      (payload instanceof PromptEventPayload ||
        payload instanceof PromptDeliveryRefusedEventPayload ||
        payload instanceof PromptDeliveryUncertainEventPayload ||
        payload instanceof PromptDiscardedEventPayload) &&
      payload.sender_label === 'machine move' &&
      payload.sender_message_id?.startsWith('work-move:'),
  );
  if (!outcome) return;
  const error =
    outcome instanceof PromptEventPayload
      ? null
      : outcome instanceof PromptDeliveryRefusedEventPayload
        ? `The new conversation refused the recovery prompt: ${outcome.refusal_detail ?? outcome.refusal_reason}. Start a new conversation on the destination.`
        : outcome instanceof PromptDeliveryUncertainEventPayload
          ? 'Delivery of the recovery prompt is uncertain. Check the destination conversation before another move.'
          : 'The recovery prompt was discarded. Start a new conversation on the destination.';
  for (const operation of read_active_operations(tx)) {
    if (operation.finished_at_ms !== null) continue;
    const index = operation.items.findIndex(
      (item) => item.conversation_id === conversation_id && item.result === null,
    );
    if (index < 0) continue;
    const item = operation.items[index]!;
    item.result = {
      owner: item.owner,
      title: item.title,
      machine_id: operation.machine_id,
      moved: true,
      error,
    };
    set_phase(operation, index, error === null ? 'complete' : 'failed', now_ms);
    if (operation.items.every((item) => item.result !== null)) operation.finished_at_ms = now_ms;
    write_operation(tx, operation);
  }
}
