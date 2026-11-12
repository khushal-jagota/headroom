/** Canonical sender facts stay identical across admission, queue, and durable receipt. */
import { isDeepStrictEqual } from 'node:util';
import {
  PromptDeliveryInjected,
  PromptDeliveryRefused,
  PromptDeliveryRefusalReason,
  PromptDeliveryStarted,
  PromptDeliverySuppressed,
  PromptDeliveryUncertain,
} from '../contracts.ts';
import type { PromptDeliveryFate, PromptDeliveryMode } from '../contracts.ts';
import {
  PromptDiscardedEventPayload,
  PromptDeliveryRefusedEventPayload,
  PromptDeliveryUncertainEventPayload,
  PromptEventPayload,
} from '../events.ts';
import type { ConversationEventPayload } from '../events.ts';
import type { Message } from './contracts.ts';

export function same_message(message: Message, other: object, held = false): boolean {
  const fields = [
    'content',
    'sender_label',
    'sender',
    'recipient',
    'recovery_references',
    ...(held ? ['model_change', 'reasoning_effort_change', 'reasoning_effort_is_set'] : []),
  ] as (keyof Message)[];
  return fields.every((key) =>
    isDeepStrictEqual(message[key], (other as Partial<Message>)[key] ?? null),
  );
}
export function prompt_row(message: Message, mode: PromptDeliveryMode): PromptEventPayload {
  return new PromptEventPayload({ ...message, mode });
}
export function refused_row(
  message: Message,
  mode: PromptDeliveryMode,
  refusal_reason: PromptDeliveryRefusalReason,
  refusal_detail: string | null = null,
): PromptDeliveryRefusedEventPayload {
  return new PromptDeliveryRefusedEventPayload({
    ...message,
    mode,
    refusal_reason,
    refusal_detail,
  });
}
export function uncertain_row(
  message: Message,
  mode: PromptDeliveryMode,
): PromptDeliveryUncertainEventPayload {
  return new PromptDeliveryUncertainEventPayload({ ...message, mode });
}
export function discarded_row(message: Message, suppression_reason: string | null = null) {
  return new PromptDiscardedEventPayload({ ...message, suppression_reason });
}
export function outcome_fate(payload: ConversationEventPayload): PromptDeliveryFate {
  if (payload instanceof PromptEventPayload) {
    return payload.mode === 'steer' ? new PromptDeliveryInjected() : new PromptDeliveryStarted();
  }
  if (payload instanceof PromptDeliveryUncertainEventPayload) return new PromptDeliveryUncertain();
  if (payload instanceof PromptDiscardedEventPayload && payload.suppression_reason) {
    return new PromptDeliverySuppressed({ reason: payload.suppression_reason });
  }
  return new PromptDeliveryRefused({
    refusal_reason:
      payload instanceof PromptDeliveryRefusedEventPayload
        ? payload.refusal_reason
        : PromptDeliveryRefusalReason.write_to_backend_failed,
  });
}
