/** A message this browser has sent that the record does not have yet.
 *
 * Sending is not a round trip a person should have to watch. The message exists the
 * moment Enter is pressed — this browser has the text, and it mints the message's id and
 * the instant it was sent right there — so it is drawn straight away and the network work
 * happens underneath it.
 *
 * That leaves one question, and it is the only thing this module decides: when has the
 * record caught up? A sent message becomes exactly one of three rows — delivered, refused,
 * or discarded — and each of them carries back the id this browser minted. So the answer
 * is "when a row carrying its id arrives", whichever of the three that row is. At that
 * point the local copy simply stops being drawn: the row is the message now, and it is
 * drawn where the record puts it rather than being swapped into place.
 */

import type { ConversationEvent, PromptDeliveryMode } from "./wire";

/** What this browser knows about what happened to a message it sent.
 *
 * Only ever what is actually known. ``waiting_for_the_agent`` is the system saying it is
 * holding the message for a busy agent: it has reached no agent, so nothing is answering
 * it and it must not read as though something is. ``answer_never_came_back`` is the send
 * that got no answer at all — it may have arrived and it may not, and saying either would
 * be a guess. Both stop mattering the moment the message's row arrives, because then the
 * record has the answer and this copy stops being drawn.
 */
export type OutgoingMessageKnownFate =
  | "nothing_yet"
  | "waiting_for_the_agent"
  | "answer_never_came_back";

export type OutgoingMessage = {
  /** What this browser called the message. It rides out with the send and comes back on
   *  the message's row, which is what lets the two be recognised as one message. */
  messageId: string;
  text: string;
  senderLabel: string;
  mode: PromptDeliveryMode;
  /** When the person pressed send, in unix milliseconds. */
  sentAtUnixMilliseconds: number;
  knownFate: OutgoingMessageKnownFate;
};

const KNOWN_FATE_NOTES: Record<OutgoingMessageKnownFate, string | null> = {
  nothing_yet: null,
  waiting_for_the_agent: "waiting for the agent to be free",
  answer_never_came_back: "the server never said whether this arrived"
};

/** What a message on its way says about itself, or nothing when there is nothing to say. */
export function outgoingMessageNote(message: OutgoingMessage): string | null {
  return KNOWN_FATE_NOTES[message.knownFate] ?? null;
}

/** Give a message its identity and its instant, before anything is sent.
 *
 * The id only has to be unique within one conversation, so it is the instant plus enough
 * randomness that two messages in the same millisecond are still two messages.
 */
export function mintOutgoingMessage(input: {
  text: string;
  senderLabel: string;
  mode: PromptDeliveryMode;
  sentAtUnixMilliseconds?: number;
}): OutgoingMessage {
  const sentAtUnixMilliseconds = input.sentAtUnixMilliseconds ?? Date.now();
  return {
    messageId: `${sentAtUnixMilliseconds.toString(36)}-${Math.random().toString(36).slice(2, 10)}`,
    text: input.text,
    senderLabel: input.senderLabel,
    mode: input.mode,
    sentAtUnixMilliseconds,
    knownFate: "nothing_yet"
  };
}

/** The ids the record is holding messages under, over every row that names one. */
export function senderMessageIdsInTheRecord(
  events: readonly ConversationEvent[]
): ReadonlySet<string> {
  const held = new Set<string>();
  for (const event of events) {
    if (
      event.kind === "prompt"
      || event.kind === "prompt_delivery_refused"
      || event.kind === "prompt_discarded"
    ) {
      const messageId = event.payload.sender_message_id;
      if (messageId !== undefined) held.add(messageId);
    }
  }
  return held;
}

/** The messages still worth drawing: the ones no row has come back for.
 *
 * The same list is handed back untouched when the record has caught up with none of
 * them, so a feed that grew by something else does not redraw what is already on screen.
 */
export function outgoingMessagesTheRecordHasNot(
  outgoing: readonly OutgoingMessage[],
  events: readonly ConversationEvent[]
): readonly OutgoingMessage[] {
  if (outgoing.length === 0) return outgoing;
  const held = senderMessageIdsInTheRecord(events);
  const remaining = outgoing.filter((message) => !held.has(message.messageId));
  return remaining.length === outgoing.length ? outgoing : remaining;
}

// --- surviving a reload ---------------------------------------------------------------------

/** A message that is waiting for a busy agent exists in this tab and nowhere else: it has
 *  no row yet, and the system knows only how many it is holding, never their words. So a
 *  reload would take a person's text away without a trace — which is the one thing this
 *  system says must never happen to text somebody handed over. It is kept for the tab, and
 *  the record takes it away in the ordinary way as soon as it has a row for it. */
const REMEMBERED_OUTGOING_MESSAGES = "panels.conversation.outgoing";

function rememberedUnder(conversationId: string): string {
  return `${REMEMBERED_OUTGOING_MESSAGES}.${conversationId}`;
}

export function rememberOutgoingMessages(
  conversationId: string,
  messages: readonly OutgoingMessage[]
): void {
  try {
    if (messages.length === 0) {
      window.sessionStorage.removeItem(rememberedUnder(conversationId));
      return;
    }
    window.sessionStorage.setItem(rememberedUnder(conversationId), JSON.stringify(messages));
  } catch {
    // A browser that will not keep anything for this tab still sends messages perfectly
    // well; it just cannot survive a reload, which is what it was always going to do.
  }
}

/** The messages this tab was still holding when it was last here.
 *
 * A message whose send was in flight when the page went away is one nobody ever heard the
 * answer to, and it comes back saying exactly that rather than pretending it is new.
 */
export function recallOutgoingMessages(conversationId: string): OutgoingMessage[] {
  let stored: string | null = null;
  try {
    stored = window.sessionStorage.getItem(rememberedUnder(conversationId));
  } catch {
    return [];
  }
  if (stored === null) return [];
  let parsed: unknown;
  try {
    parsed = JSON.parse(stored);
  } catch {
    return [];
  }
  if (!Array.isArray(parsed)) return [];
  return parsed.flatMap((entry) => {
    const message = outgoingMessageFrom(entry);
    if (message === null) return [];
    return [
      message.knownFate === "nothing_yet"
        ? { ...message, knownFate: "answer_never_came_back" as const }
        : message
    ];
  });
}

const DELIVERY_MODES: readonly PromptDeliveryMode[] = ["run_when_free", "send_now", "steer"];
const KNOWN_FATES = Object.keys(KNOWN_FATE_NOTES) as readonly OutgoingMessageKnownFate[];

function outgoingMessageFrom(entry: unknown): OutgoingMessage | null {
  if (entry === null || typeof entry !== "object") return null;
  const held = entry as Record<string, unknown>;
  const { messageId, text, senderLabel, mode, sentAtUnixMilliseconds, knownFate } = held;
  if (typeof messageId !== "string" || messageId === "") return null;
  if (typeof text !== "string" || typeof senderLabel !== "string") return null;
  if (!DELIVERY_MODES.includes(mode as PromptDeliveryMode)) return null;
  if (typeof sentAtUnixMilliseconds !== "number") return null;
  if (!KNOWN_FATES.includes(knownFate as OutgoingMessageKnownFate)) return null;
  return {
    messageId,
    text,
    senderLabel,
    mode: mode as PromptDeliveryMode,
    sentAtUnixMilliseconds,
    knownFate: knownFate as OutgoingMessageKnownFate
  };
}
