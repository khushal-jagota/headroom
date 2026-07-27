// How far the user has read each conversation, kept in this browser.
//
// How far somebody has read is about that person at that screen, not about the ticket,
// so it is theirs and it stays here. A board row carries its conversation's
// `latest_turn_ended_sequence`; the row mark shows a reply waiting when that is past the
// watermark, and the pane moves the watermark while the conversation is on show.
//
// Keyed by CONVERSATION id, never by ticket id: positions are per conversation, so a
// per-ticket key would let an old conversation's high watermark suppress a fresh
// conversation's replies after New. A conversation nobody has read has no entry, which
// reads as 0 — unread.
//
// The value is a POSITION in the conversation, never a clock. Mid-turn output therefore
// never makes anything unread, and every failure path (missing storage, unreadable
// value, a write that will not stick) leaves the answer at 0, which over-shows attention
// rather than hiding a reply.

const REPLY_WATERMARK_KEY_PREFIX = "panels.replySeen.";

// Reading is what clears a mark, and reading writes nothing the server can announce. So
// the one place that knows a watermark moved says so, and a surface drawn from
// watermarks redraws. Without this a row stays lit until something unrelated refetches
// the board.
const watermarkMovedListeners = new Set<() => void>();

export function onReplyWatermarkMoved(listener: () => void): () => void {
  watermarkMovedListeners.add(listener);
  return () => watermarkMovedListeners.delete(listener);
}

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

// The last position in this conversation the user has seen. 0 means unread.
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
  const position = Number(stored);
  if (!Number.isInteger(position) || position < 0) return 0;
  return position;
}

// Record that the user has seen this conversation up to this position.
//
// The watermark only ever moves forward: an earlier position is ignored rather than
// written. Two views of one conversation can report out of order — a second tab, a late
// refetch — and a backwards write would resurrect a reply the user has already read.
export function writeReplyWatermark(conversationId: string, position: number): void {
  const store = replyWatermarkStore();
  if (!store) return;
  if (!Number.isInteger(position) || position <= readReplyWatermark(conversationId)) return;
  try {
    store.setItem(keyFor(conversationId), String(position));
  } catch {
    // A full or blocked store leaves the conversation unread, which over-shows.
    return;
  }
  for (const listener of watermarkMovedListeners) listener();
}
