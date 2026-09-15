/** The two ways to read one conversation record. */
import type { ConversationFeed } from "./feed";
import type { TranscriptRow } from "./transcript";
import type { ConversationEvent, HeldPrompt } from "./wire";

export type ConversationLens = "focus" | "full";

/** Whether a durable row belongs in the owner's focused reading.
 *
 * New prompt rows carry trusted principals. Historical browser prompts do not, so their
 * established sender label remains the compatibility answer. Ask settlement rows stay
 * with their ask because the transcript folds each settlement into the original card.
 */
export function conversationEventIsInFocus(
  event: ConversationEvent,
  ownerSenderLabel: string
): boolean {
  switch (event.kind) {
    case "prompt":
    case "prompt_delivery_refused":
    case "prompt_delivery_uncertain":
    case "prompt_discarded":
      return event.payload.sender?.kind === "owner"
        || (
          event.payload.sender === undefined
          && event.payload.recipient === undefined
          && event.payload.sender_label === ownerSenderLabel
        );
    case "message_to_owner":
      return event.payload.recipient.kind === "owner";
    case "permission_asked":
    case "permission_answered":
    case "user_input_requested":
    case "user_input_answered":
    case "user_input_failed":
      return true;
    default:
      return false;
  }
}

/** Project the rows and readable position without changing the complete live feed. */
export function conversationFeedForLens(
  feed: ConversationFeed,
  lens: ConversationLens,
  ownerSenderLabel: string
): ConversationFeed {
  if (lens === "full") return feed;
  const events: ConversationEvent[] = [];
  let focusedTurnHasContent = false;
  for (const event of feed.events) {
    if (conversationEventIsInFocus(event, ownerSenderLabel)) {
      events.push(event);
      focusedTurnHasContent = true;
    } else if (event.kind === "turn_ended") {
      // The boundary settles focused asks and lets the read position cover a focused
      // turn. The row is structural in Focus and is removed from the visible rows below.
      if (focusedTurnHasContent) events.push(event);
      focusedTurnHasContent = false;
    }
  }
  return {
    events,
    latestSequence: events.at(-1)?.sequence ?? 0,
    streamingAgentText: "",
    toolCallProgress: {}
  };
}

/** Keep structural rows long enough to build the transcript, then show messages only. */
export function conversationRowsForLens(
  rows: readonly TranscriptRow[],
  lens: ConversationLens
): readonly TranscriptRow[] {
  if (lens === "full") return rows;
  return rows.filter((row) => (
    row.kind === "prompt"
    || row.kind === "prompt_refused"
    || row.kind === "prompt_uncertain"
    || row.kind === "prompt_discarded"
    || row.kind === "agent_message"
    || row.kind === "permission_ask"
    || row.kind === "user_input"
  ));
}

/** Held prompts are messages too, but only owner-authored ones belong in Focus. */
export function heldPromptIsInLens(
  prompt: HeldPrompt,
  lens: ConversationLens,
  ownerSenderLabel: string
): boolean {
  if (lens === "full") return true;
  return prompt.sender?.kind === "owner"
    || (
      prompt.sender == null
      && prompt.recipient == null
      && prompt.sender_label === ownerSenderLabel
    );
}

/** An unmodified F press toggles a lens unless an editable control owns the key. */
export function keyTogglesConversationLens(event: KeyboardEvent): boolean {
  if (
    event.defaultPrevented
    || event.repeat
    || event.altKey
    || event.ctrlKey
    || event.metaKey
    || event.shiftKey
    || event.key.toLowerCase() !== "f"
  ) return false;
  const target = event.target;
  return !(target instanceof Element && target.closest(
    "input, textarea, select, [contenteditable]:not([contenteditable=\"false\"]), [role=\"textbox\"]"
  ));
}
