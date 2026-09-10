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
import {
  MAX_CONVERSATION_MESSAGE_FILE_BYTES,
  base64DecodedByteCount as decodedFileByteCount,
  conversationFileMediaType
} from "./pendingFiles";

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
const outgoingFileReservations = new Map<string, number>();
let rememberedMessagesWereScanned = false;

export function outgoingMessageImageBytes(message: OutgoingMessage): number {
  return message.content.reduce(
    (total, piece) => total + (piece.piece === "image" ? base64DecodedByteCount(piece.data) : 0),
    0
  );
}

export function outgoingMessageFileBytes(message: OutgoingMessage): number {
  return message.content.reduce(
    (total, piece) => total + (piece.piece === "file" ? decodedFileByteCount(piece.data) : 0),
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

export function reserveOutgoingMessageFiles(message: OutgoingMessage): boolean {
  includeRememberedMessagesInReservations();
  if (outgoingFileReservations.has(message.messageId)) return true;
  const byteCount = outgoingMessageFileBytes(message);
  const reserved = Array.from(outgoingFileReservations.values()).reduce(
    (total, value) => total + value,
    0
  );
  if (reserved + byteCount > MAX_CONVERSATION_MESSAGE_FILE_BYTES) return false;
  outgoingFileReservations.set(message.messageId, byteCount);
  return true;
}

export function releaseOutgoingMessageFiles(messageId: string): void {
  includeRememberedMessagesInReservations();
  if (messageIsRememberedAnywhere(messageId)) return;
  outgoingFileReservations.delete(messageId);
}

export function reserveRecalledOutgoingMessages(
  messages: readonly OutgoingMessage[]
): OutgoingMessage[] {
  return messages.filter((message) => {
    if (!reserveOutgoingMessageImages(message)) return false;
    if (reserveOutgoingMessageFiles(message)) return true;
    outgoingImageReservations.delete(message.messageId);
    return false;
  });
}

/** Test isolation for the tab-local ledger. A real tab resets it by ending. */
export function resetOutgoingImageReservationsForTest(): void {
  outgoingImageReservations.clear();
  outgoingFileReservations.clear();
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
      || event.kind === "prompt_delivery_uncertain"
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
const OUTGOING_MESSAGE_DATABASE = "panels-conversation-outgoing";
const OUTGOING_MESSAGE_STORE = "messages";

type RememberedMessageReservation = {
  message_id: string;
  image_bytes: number;
  file_bytes: number;
};

type RememberedOutgoingReference = {
  storage: "indexed_db";
  store_key: string;
  reservations: RememberedMessageReservation[];
};

export type OutgoingMessageFileStore = {
  read: (storageKey: string) => Promise<unknown>;
  write: (storageKey: string, messages: readonly OutgoingMessage[]) => Promise<void>;
  remove: (storageKey: string) => Promise<void>;
};

let outgoingMessageDatabase: Promise<IDBDatabase> | null = null;
let outgoingMessageFileStore: OutgoingMessageFileStore = {
  read: async (storageKey) => indexedDbRequest(
    (await openOutgoingMessageDatabase())
      .transaction(OUTGOING_MESSAGE_STORE, "readonly")
      .objectStore(OUTGOING_MESSAGE_STORE)
      .get(storageKey)
  ),
  write: async (storageKey, messages) => {
    const transaction = (await openOutgoingMessageDatabase())
      .transaction(OUTGOING_MESSAGE_STORE, "readwrite");
    const completed = indexedDbTransactionDone(transaction);
    await indexedDbRequest(
      transaction.objectStore(OUTGOING_MESSAGE_STORE).put(messages, storageKey)
    );
    await completed;
  },
  remove: async (storageKey) => {
    const transaction = (await openOutgoingMessageDatabase())
      .transaction(OUTGOING_MESSAGE_STORE, "readwrite");
    const completed = indexedDbTransactionDone(transaction);
    await indexedDbRequest(
      transaction.objectStore(OUTGOING_MESSAGE_STORE).delete(storageKey)
    );
    await completed;
  }
};
const rememberTails = new Map<string, Promise<boolean>>();

function indexedDbRequest<T>(request: IDBRequest<T>): Promise<T> {
  return new Promise((resolve, reject) => {
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB request failed"));
  });
}

function indexedDbTransactionDone(transaction: IDBTransaction): Promise<void> {
  return new Promise((resolve, reject) => {
    transaction.oncomplete = () => resolve();
    transaction.onabort = () => reject(transaction.error ?? new Error("IndexedDB transaction aborted"));
    transaction.onerror = () => reject(transaction.error ?? new Error("IndexedDB transaction failed"));
  });
}

function openOutgoingMessageDatabase(): Promise<IDBDatabase> {
  if (outgoingMessageDatabase !== null) return outgoingMessageDatabase;
  outgoingMessageDatabase = new Promise((resolve, reject) => {
    const request = indexedDB.open(OUTGOING_MESSAGE_DATABASE, 1);
    request.onupgradeneeded = () => {
      if (!request.result.objectStoreNames.contains(OUTGOING_MESSAGE_STORE)) {
        request.result.createObjectStore(OUTGOING_MESSAGE_STORE);
      }
    };
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error ?? new Error("IndexedDB open failed"));
  });
  return outgoingMessageDatabase;
}

/** Replace IndexedDB only inside a test. A real tab always uses its private database. */
export function setOutgoingMessageFileStoreForTest(store: OutgoingMessageFileStore): void {
  outgoingMessageFileStore = store;
  rememberTails.clear();
}

function rememberedUnder(conversationId: string): string {
  return `${REMEMBERED_OUTGOING_MESSAGES}.${conversationId}`;
}

export function outgoingPersistenceConversationId(
  openedConversationId: string | null,
  intendedConversationId: string | null,
  messageId: string
): string {
  return openedConversationId ?? intendedConversationId ?? `pending:${messageId}`;
}

/** Move one tab's compact pointer when a first send receives its canonical id.
 *
 * A failed move leaves the source usable. The live conversation then keeps that identity
 * as its active persistence key until record reconciliation empties it. */
export async function moveRememberedOutgoingMessages(
  fromConversationId: string,
  toConversationId: string
): Promise<boolean> {
  if (fromConversationId === toConversationId) return true;
  await rememberTails.get(fromConversationId);
  await rememberTails.get(toConversationId);
  const fromKey = rememberedUnder(fromConversationId);
  const toKey = rememberedUnder(toConversationId);
  let wroteTarget = false;
  try {
    const stored = window.sessionStorage.getItem(fromKey);
    if (stored === null) return true;
    window.sessionStorage.setItem(toKey, stored);
    wroteTarget = true;
    window.sessionStorage.removeItem(fromKey);
    return true;
  } catch {
    // If copying worked but removing the source did not, prefer the known source. The
    // caller will keep writing/cleaning there, so do not knowingly leave two pointers.
    if (wroteTarget) {
      try { window.sessionStorage.removeItem(toKey); } catch { /* best effort */ }
    }
    return false;
  }
}

export function rememberOutgoingMessages(
  conversationId: string,
  messages: readonly OutgoingMessage[]
): Promise<boolean> {
  includeRememberedMessagesInReservations();
  const previousTail = rememberTails.get(conversationId) ?? Promise.resolve(true);
  const nextTail = previousTail.then(() => rememberOutgoingMessagesNow(conversationId, messages));
  rememberTails.set(conversationId, nextTail);
  return nextTail.finally(() => {
    if (rememberTails.get(conversationId) === nextTail) rememberTails.delete(conversationId);
  });
}

async function rememberOutgoingMessagesNow(
  conversationId: string,
  messages: readonly OutgoingMessage[]
): Promise<boolean> {
  const storageKey = rememberedUnder(conversationId);
  const previousReservations = rememberedReservationsFromStored(
    safelyReadSessionValue(storageKey)
  );
  const previousReference = rememberedReferenceFromStored(safelyReadSessionValue(storageKey));
  const carriesFiles = messages.some((message) => outgoingMessageFileBytes(message) > 0);
  if (messages.length === 0) {
    try {
      window.sessionStorage.removeItem(storageKey);
    } catch {
      return true;
    }
    if (previousReference !== null) {
      try {
        await outgoingMessageFileStore.remove(previousReference.store_key);
      } catch {
        // The compact session reference is gone. A stale unreachable IndexedDB value
        // cannot block another send, so cleanup failure changes no live state.
      }
    }
  } else if (carriesFiles) {
    const storeKey = previousReference?.store_key
      ?? `${conversationId}:${messages[0]?.messageId ?? "outgoing"}`;
    try {
      await outgoingMessageFileStore.write(storeKey, messages);
      const reference: RememberedOutgoingReference = {
        storage: "indexed_db",
        store_key: storeKey,
        reservations: messages.map(messageReservation)
      };
      window.sessionStorage.setItem(storageKey, JSON.stringify(reference));
    } catch {
      return false;
    }
  } else {
    try {
      window.sessionStorage.setItem(storageKey, JSON.stringify(messages));
    } catch {
      // Preserve the established best-effort behavior for text and image-only messages.
      return true;
    }
    if (previousReference !== null) {
      try {
        await outgoingMessageFileStore.remove(previousReference.store_key);
      } catch {
        // The new session value no longer names this data. Cleanup is independent.
      }
    }
  }
  const nextIds = new Set(messages.map((message) => message.messageId));
  for (const reservation of previousReservations) {
    if (!nextIds.has(reservation.message_id) && !messageIsRememberedAnywhere(reservation.message_id)) {
      releaseOutgoingMessageImages(reservation.message_id);
      releaseOutgoingMessageFiles(reservation.message_id);
    }
  }
  return true;
}

/** The messages this tab was still holding when it was last here.
 *
 * A message whose send was in flight when the page went away comes back knowing nothing
 * rather than pretending it is new. What became of it is the record's to say, and the
 * record is read a moment after this — so it waits to be told, which is what
 * ``sent_before_this_page`` is. A message the system said it was holding comes back saying
 * that, because that is still what was last known about it.
 */
export async function recallOutgoingMessages(conversationId: string): Promise<OutgoingMessage[]> {
  includeRememberedMessagesInReservations();
  await rememberTails.get(conversationId);
  return (await outgoingMessagesStoredUnder(conversationId)).map((message) =>
    message.knownFate === "nothing_yet"
      ? { ...message, knownFate: "sent_before_this_page" as const }
      : message
  );
}

async function outgoingMessagesStoredUnder(conversationId: string): Promise<OutgoingMessage[]> {
  const stored = safelyReadSessionValue(rememberedUnder(conversationId));
  const reference = rememberedReferenceFromStored(stored);
  if (reference !== null) {
    try {
      return outgoingMessagesFromUnknown(await outgoingMessageFileStore.read(reference.store_key));
    } catch {
      return [];
    }
  }
  return outgoingMessagesFromStored(stored);
}

function safelyReadSessionValue(key: string): string | null {
  try {
    return window.sessionStorage.getItem(key);
  } catch {
    return null;
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
  return outgoingMessagesFromUnknown(parsed);
}

function outgoingMessagesFromUnknown(value: unknown): OutgoingMessage[] {
  if (!Array.isArray(value)) return [];
  return value.flatMap((entry) => {
    const message = outgoingMessageFrom(entry);
    return message === null ? [] : [message];
  });
}

function messageReservation(message: OutgoingMessage): RememberedMessageReservation {
  return {
    message_id: message.messageId,
    image_bytes: outgoingMessageImageBytes(message),
    file_bytes: outgoingMessageFileBytes(message)
  };
}

function rememberedReferenceFromStored(stored: string | null): RememberedOutgoingReference | null {
  if (stored === null) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(stored);
  } catch {
    return null;
  }
  if (parsed === null || typeof parsed !== "object") return null;
  const value = parsed as Record<string, unknown>;
  if (
    value.storage !== "indexed_db"
    || typeof value.store_key !== "string"
    || value.store_key === ""
    || !Array.isArray(value.reservations)
  ) return null;
  const reservations = value.reservations.flatMap((entry): RememberedMessageReservation[] => {
    if (entry === null || typeof entry !== "object") return [];
    const reservation = entry as Record<string, unknown>;
    if (
      typeof reservation.message_id !== "string"
      || typeof reservation.image_bytes !== "number"
      || typeof reservation.file_bytes !== "number"
      || reservation.image_bytes < 0
      || reservation.file_bytes < 0
    ) return [];
    return [{
      message_id: reservation.message_id,
      image_bytes: reservation.image_bytes,
      file_bytes: reservation.file_bytes
    }];
  });
  if (reservations.length !== value.reservations.length) return null;
  return { storage: "indexed_db", store_key: value.store_key, reservations };
}

function rememberedReservationsFromStored(stored: string | null): RememberedMessageReservation[] {
  const reference = rememberedReferenceFromStored(stored);
  if (reference !== null) return reference.reservations;
  return outgoingMessagesFromStored(stored).map(messageReservation);
}

function includeRememberedMessagesInReservations(): void {
  if (rememberedMessagesWereScanned) return;
  rememberedMessagesWereScanned = true;
  try {
    for (let index = 0; index < window.sessionStorage.length; index += 1) {
      const key = window.sessionStorage.key(index);
      if (key === null || !key.startsWith(`${REMEMBERED_OUTGOING_MESSAGES}.`)) continue;
      for (const reservation of rememberedReservationsFromStored(window.sessionStorage.getItem(key))) {
        outgoingImageReservations.set(reservation.message_id, reservation.image_bytes);
        outgoingFileReservations.set(reservation.message_id, reservation.file_bytes);
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
      if (rememberedReservationsFromStored(window.sessionStorage.getItem(key))
        .some((reservation) => reservation.message_id === messageId)) {
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
    piece.piece === "file"
    && typeof piece.data === "string"
    && piece.data !== ""
    && typeof piece.media_type === "string"
    && typeof piece.file_name === "string"
    && piece.file_name !== ""
    && conversationFileMediaType(piece.file_name) === piece.media_type
  ) {
    return {
      piece: "file",
      data: piece.data,
      media_type: piece.media_type,
      file_name: piece.file_name
    };
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
