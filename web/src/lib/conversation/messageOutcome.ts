import type { ConversationEvent } from "./wire";

export type MessageOutcomeEvent = Extract<ConversationEvent, {
  kind: "prompt" | "prompt_delivery_refused" | "prompt_delivery_uncertain" | "prompt_discarded";
}>;

export function isMessageOutcome(event: ConversationEvent): event is MessageOutcomeEvent {
  return event.kind === "prompt" || event.kind === "prompt_delivery_refused"
    || event.kind === "prompt_delivery_uncertain" || event.kind === "prompt_discarded";
}

/** Only an explicit outcome for this message settles it. Later turns prove nothing. */
export function latestMessageOutcomes(
  events: readonly ConversationEvent[]
): ReadonlyMap<string, MessageOutcomeEvent> {
  const latest = new Map<string, MessageOutcomeEvent>();
  for (const event of events) {
    if (!isMessageOutcome(event)) continue;
    const id = event.payload.sender_message_id;
    if (id === undefined) continue;
    if (event.sequence > (latest.get(id)?.sequence ?? -1)) latest.set(id, event);
  }
  return latest;
}
