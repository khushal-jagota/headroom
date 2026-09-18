/** The two ways to read one conversation record. */
import type { ConversationFeed } from "./feed";
import { threadItems, type ThreadItem } from "./threadLayout";
import type { TranscriptRow } from "./transcript";
import type { ConversationEvent, HeldPrompt } from "./wire";

export type ConversationLens = "focus" | "full";

const CONVERSATION_LENS_STORAGE_KEY = "panels.conversation.lens";

/** Read the browser-wide reading preference. Missing, malformed, and unavailable storage
 * all use Focus, which is the safe first-use view. */
export function conversationLensPreference(): ConversationLens {
  try {
    const stored = typeof window === "undefined"
      ? null
      : window.localStorage.getItem(CONVERSATION_LENS_STORAGE_KEY);
    return stored === "full" || stored === "focus" ? stored : "focus";
  } catch {
    return "focus";
  }
}

/** Keep one lens preference for this browser, independent of conversation identity. */
export function rememberConversationLensPreference(lens: ConversationLens): void {
  try {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(CONVERSATION_LENS_STORAGE_KEY, lens);
    }
  } catch {
    // A private or restricted browser can still use the lens for this mounted pane.
  }
}

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
    case "explicit_reply_missing":
      return true;
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
    if (event.kind === "turn_ended") {
      // Every ending remains available to settle the complete turn structure. Focus only
      // draws endings that report a failure or stop, plus completed endings that settle
      // an ask inside the focused projection.
      if (focusedTurnHasContent || event.payload.ending !== "completed") events.push(event);
      focusedTurnHasContent = false;
    } else if (conversationEventIsInFocus(event, ownerSenderLabel)) {
      events.push(event);
      focusedTurnHasContent = true;
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
    || row.kind === "explicit_reply_missing"
    || row.kind === "permission_ask"
    || row.kind === "user_input"
    || (row.kind === "turn_ended" && row.ending !== "completed")
    || row.kind === "turn_stopped"
  ));
}

/** Build every turn from the complete record, then hide rows outside the selected lens. */
export function conversationThreadItemsForLens(
  rows: readonly TranscriptRow[],
  visibleRows: readonly TranscriptRow[],
  lens: ConversationLens
): ThreadItem[] {
  if (lens === "full") return threadItems(rows);
  const visibleKeys = new Set(visibleRows.map((row) => row.key));
  return threadItems(rows, (row) => visibleKeys.has(row.key));
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
