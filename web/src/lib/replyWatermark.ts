// How far the user has read each conversation, kept in this browser.
//
// NOT WIRED YET. The reply dot still runs on the server's agent_reply_state (the ticket
// conversation projection plus the acknowledge-on-open endpoint). This module is the
// browser half of what replaces it.
//
// THE SEAM: when the transcript surface lands, a board row gains its conversation's
// latest turn-ended line number. The row mark then computes
// `reply_waiting = latestTurnEndedLine > readReplyWatermark(conversation_id)`, the
// ticket pane calls writeReplyWatermark when the user views the conversation, and the
// projection machinery, its agent_reply_state field and the acknowledge endpoint all
// die together.
//
// Keyed by CONVERSATION id, never by ticket id: line numbers are per conversation, so a
// per-ticket key would let an old conversation's high watermark suppress a fresh
// conversation's replies after New. A conversation nobody has read has no entry, which
// reads as 0 — unread.
//
// The value is a notebook LINE NUMBER: a position in the conversation, never a clock.
// Mid-turn output therefore never makes anything unread, and every failure path
// (missing storage, unreadable value, a write that will not stick) leaves the answer at
// 0, which over-shows attention rather than hiding a reply.

const REPLY_WATERMARK_KEY_PREFIX = "panels.replySeen.";

function replyWatermarkStore(): Storage | null {
  try {
    return globalThis.localStorage ?? null;
  } catch {
    // Reading the property itself throws where storage is blocked outright.
    return null;
  }
}

function keyFor(conversationId: string): string {
  return `${REPLY_WATERMARK_KEY_PREFIX}${conversationId}`;
}

// The last notebook line of this conversation the user has seen. 0 means unread.
export function readReplyWatermark(conversationId: string): number {
  const store = replyWatermarkStore();
  if (!store) return 0;
  let stored: string | null;
  try {
    stored = store.getItem(keyFor(conversationId));
  } catch {
    return 0;
  }
  if (stored === null) return 0;
  const lineNumber = Number(stored);
  if (!Number.isInteger(lineNumber) || lineNumber < 0) return 0;
  return lineNumber;
}

// Record that the user has seen this conversation up to this notebook line.
//
// The watermark only ever moves forward: a lower line number is ignored rather than
// written. Two views of one conversation can report out of order — a second tab, a late
// refetch — and a backwards write would resurrect a reply the user has already read.
export function writeReplyWatermark(conversationId: string, lineNumber: number): void {
  const store = replyWatermarkStore();
  if (!store) return;
  if (!Number.isInteger(lineNumber) || lineNumber <= readReplyWatermark(conversationId)) return;
  try {
    store.setItem(keyFor(conversationId), String(lineNumber));
  } catch {
    // A full or blocked store leaves the conversation unread, which over-shows.
  }
}
