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

import type { ConversationEvent, PromptDeliveryMode, SentMessagePiece } from "./wire";
import { MAX_CONVERSATION_MESSAGE_IMAGE_BYTES } from "./pendingImages";

/** What this browser knows about what happened to a message it sent.
 *
 * Only ever what is actually known. ``waiting_for_the_agent`` is the system saying it is
 * holding the message for a busy agent: it has reached no agent, so nothing is answering
 * it and it must not read as though something is. ``answer_never_came_back`` is the send
 * that got no answer at all — it may have arrived and it may not, and saying either would
 * be a guess. Both stop mattering the moment the message's row arrives, because then the
 * record has the answer and this copy stops being drawn.
 *
 * ``sent_before_this_page`` is the one that knows nothing at all: a message this tab was
 * holding when the page went away, come back before the record has been read. Whether it
 * arrived is a question the record answers a moment later, so this says nothing while it
 * waits to be told — announcing that nobody knows, to a browser that has not yet looked,
 * puts a frightening sentence on a message that is about to turn out fine.
 */
export type OutgoingMessageKnownFate =
  | "nothing_yet"
  | "sent_before_this_page"
  | "waiting_for_the_agent"
  | "answer_never_came_back";

export type OutgoingMessage = {
  /** What this browser called the message. It rides out with the send and comes back on
   *  the message's row, which is what lets the two be recognised as one message. */
  messageId: string;
  /** The message as it will be sent, which is what this browser has until the record
   *  answers for it. A message on its way names no kept file, because nothing has kept
   *  anything yet — it holds what it is about to hand over. */
  content: SentMessagePiece[];
  senderLabel: string;
  mode: PromptDeliveryMode;
  /** When the person pressed send, in unix milliseconds. */
  sentAtUnixMilliseconds: number;
  knownFate: OutgoingMessageKnownFate;
};

const outgoingImageReservations = new Map<string, number>();
let rememberedMessagesWereScanned = false;

export function outgoingMessageImageBytes(message: OutgoingMessage): number {
  return message.content.reduce(
    (total, piece) => total + (piece.piece === "image" ? base64DecodedByteCount(piece.data) : 0),
    0
  );
}

export function reserveOutgoingMessageImages(message: OutgoingMessage): boolean {
  includeRememberedMessagesInReservations();
  if (outgoingImageReservations.has(message.messageId)) return true;
  const byteCount = outgoingMessageImageBytes(message);
  const reserved = Array.from(outgoingImageReservations.values()).reduce(
    (total, value) => total + value,
    0
  );
  if (reserved + byteCount > MAX_CONVERSATION_MESSAGE_IMAGE_BYTES) return false;
  outgoingImageReservations.set(message.messageId, byteCount);
  return true;
}

export function releaseOutgoingMessageImages(messageId: string): void {
  includeRememberedMessagesInReservations();
  if (messageIsRememberedAnywhere(messageId)) return;
  outgoingImageReservations.delete(messageId);
}

export function reserveRecalledOutgoingMessages(
  messages: readonly OutgoingMessage[]
): OutgoingMessage[] {
  return messages.filter((message) => reserveOutgoingMessageImages(message));
}

/** Test isolation for the tab-local ledger. A real tab resets it by ending. */
export function resetOutgoingImageReservationsForTest(): void {
  outgoingImageReservations.clear();
  rememberedMessagesWereScanned = false;
}

function base64DecodedByteCount(data: string): number {
  if (data === "") return 0;
  const padding = data.endsWith("==") ? 2 : data.endsWith("=") ? 1 : 0;
  return Math.floor(data.length / 4) * 3 - padding;
}

const KNOWN_FATE_NOTES: Record<OutgoingMessageKnownFate, string | null> = {
  nothing_yet: null,
  sent_before_this_page: null,
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
  content: SentMessagePiece[];
  senderLabel: string;
  mode: PromptDeliveryMode;
  sentAtUnixMilliseconds?: number;
}): OutgoingMessage {
  const sentAtUnixMilliseconds = input.sentAtUnixMilliseconds ?? Date.now();
  return {
    messageId: `${sentAtUnixMilliseconds.toString(36)}-${Math.random().toString(36).slice(2, 10)}`,
    content: input.content,
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
  includeRememberedMessagesInReservations();
  const previous = outgoingMessagesStoredUnder(conversationId);
  try {
    if (messages.length === 0) {
      window.sessionStorage.removeItem(rememberedUnder(conversationId));
    } else {
      window.sessionStorage.setItem(rememberedUnder(conversationId), JSON.stringify(messages));
    }
  } catch {
    // A browser that will not keep anything for this tab still sends messages perfectly
    // well; it just cannot survive a reload, which is what it was always going to do.
    // If an older remembered value could not be replaced, it still consumes this tab's
    // recall envelope even when the live component already stopped drawing it.
    for (const message of previous) {
      outgoingImageReservations.set(message.messageId, outgoingMessageImageBytes(message));
    }
    return;
  }
  const nextIds = new Set(messages.map((message) => message.messageId));
  for (const message of previous) {
    if (!nextIds.has(message.messageId) && !messageIsRememberedAnywhere(message.messageId)) {
      releaseOutgoingMessageImages(message.messageId);
    }
  }
}

/** The messages this tab was still holding when it was last here.
 *
 * A message whose send was in flight when the page went away comes back knowing nothing
 * rather than pretending it is new. What became of it is the record's to say, and the
 * record is read a moment after this — so it waits to be told, which is what
 * ``sent_before_this_page`` is. A message the system said it was holding comes back saying
 * that, because that is still what was last known about it.
 */
export function recallOutgoingMessages(conversationId: string): OutgoingMessage[] {
  includeRememberedMessagesInReservations();
  return outgoingMessagesStoredUnder(conversationId).map((message) =>
    message.knownFate === "nothing_yet"
      ? { ...message, knownFate: "sent_before_this_page" as const }
      : message
  );
}

function outgoingMessagesStoredUnder(conversationId: string): OutgoingMessage[] {
  try {
    return outgoingMessagesFromStored(
      window.sessionStorage.getItem(rememberedUnder(conversationId))
    );
  } catch {
    return [];
  }
}

function outgoingMessagesFromStored(stored: string | null): OutgoingMessage[] {
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
    return message === null ? [] : [message];
  });
}

function includeRememberedMessagesInReservations(): void {
  if (rememberedMessagesWereScanned) return;
  rememberedMessagesWereScanned = true;
  try {
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key === null || !key.startsWith(`${REMEMBERED_OUTGOING_MESSAGES}.`)) continue;
      for (const message of outgoingMessagesFromStored(window.sessionStorage.getItem(key))) {
        outgoingImageReservations.set(
          message.messageId,
          outgoingMessageImageBytes(message)
        );
      }
    }
  } catch {
    // Storage is an optional recall aid. The live tab ledger still enforces its own sends.
  }
}

function messageIsRememberedAnywhere(messageId: string): boolean {
  try {
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key === null || !key.startsWith(`${REMEMBERED_OUTGOING_MESSAGES}.`)) continue;
      if (
        outgoingMessagesFromStored(window.sessionStorage.getItem(key))
          .some((message) => message.messageId === messageId)
      ) {
        return true;
      }
    }
  } catch {
    return false;
  }
  return false;
}

/** The record has been read, and these are the messages it did not have.
 *
 * This is the moment a message brought back from before the page reloaded gets its answer.
 * Anything the record had has already stopped being drawn — that happens off the rows
 * themselves — so what is still here is what the record does not know about, and for those
 * the honest thing is that nobody ever said whether they arrived.
 *
 * The same list is handed back untouched when there was nothing waiting to be told, so a
 * reconnect on a conversation with nothing outstanding redraws nothing.
 */
export function afterTheRecordHasBeenRead(
  outgoing: readonly OutgoingMessage[]
): readonly OutgoingMessage[] {
  if (!outgoing.some((message) => message.knownFate === "sent_before_this_page")) {
    return outgoing;
  }
  return outgoing.map((message) =>
    message.knownFate === "sent_before_this_page"
      ? { ...message, knownFate: "answer_never_came_back" as const }
      : message
  );
}

const DELIVERY_MODES: readonly PromptDeliveryMode[] = ["run_when_free", "send_now", "steer"];
const KNOWN_FATES = Object.keys(KNOWN_FATE_NOTES) as readonly OutgoingMessageKnownFate[];

function outgoingMessageFrom(entry: unknown): OutgoingMessage | null {
  if (entry === null || typeof entry !== "object") return null;
  const held = entry as Record<string, unknown>;
  const { messageId, content, senderLabel, mode, sentAtUnixMilliseconds, knownFate } = held;
  if (typeof messageId !== "string" || messageId === "") return null;
  if (!Array.isArray(content) || content.length === 0) return null;
  const pieces = content.flatMap((piece) => {
    const read = sentMessagePieceFrom(piece);
    return read === null ? [] : [read];
  });
  if (pieces.length !== content.length) return null;
  if (typeof senderLabel !== "string") return null;
  if (!DELIVERY_MODES.includes(mode as PromptDeliveryMode)) return null;
  if (typeof sentAtUnixMilliseconds !== "number") return null;
  if (!KNOWN_FATES.includes(knownFate as OutgoingMessageKnownFate)) return null;
  return {
    messageId,
    content: pieces,
    senderLabel,
    mode: mode as PromptDeliveryMode,
    sentAtUnixMilliseconds,
    knownFate: knownFate as OutgoingMessageKnownFate
  };
}

function sentMessagePieceFrom(value: unknown): SentMessagePiece | null {
  if (value === null || typeof value !== "object") return null;
  const piece = value as Record<string, unknown>;
  if (piece.piece === "text") {
    return typeof piece.text === "string" ? { piece: "text", text: piece.text } : null;
  }
  if (
    piece.piece !== "image"
    || typeof piece.data !== "string"
    || piece.data === ""
    || typeof piece.media_type !== "string"
    || !piece.media_type.toLowerCase().startsWith("image/")
    || (piece.file_name !== undefined && typeof piece.file_name !== "string")
  ) {
    return null;
  }
  return {
    piece: "image",
    data: piece.data,
    media_type: piece.media_type,
    ...(piece.file_name === undefined ? {} : { file_name: piece.file_name })
  };
}
