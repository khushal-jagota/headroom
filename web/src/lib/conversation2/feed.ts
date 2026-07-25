/** What a reader is holding of a conversation, and the thing that keeps it current.
 *
 * Two kinds of thing arrive and only one of them is a row. Committed rows are the record:
 * they are numbered, they never change, and a reader keeps them. Live frames are the
 * half-finished output a backend streams while it works: they are shown and then dropped,
 * and the row they were leading up to is what replaces them. So the moment an agent
 * message lands as a row, the streamed text that preceded it is gone — never both.
 *
 * Reconnecting is not a special path. Opening a conversation, coming back to a tab, and
 * recovering from a dropped stream are all "say which row you have and take everything
 * after it", which is why the stream below only ever does that one thing.
 */

import type { ConversationEvent, ConversationLiveFrame } from "./wire";

export type ConversationFeed = {
  /** Every committed row this reader holds, ascending by sequence, one row per sequence. */
  readonly events: readonly ConversationEvent[];
  /** The highest sequence held, which is the position a reconnect asks from. */
  readonly latestSequence: number;
  /** Agent text that has not finished arriving. Empty once its row lands. */
  readonly streamingAgentText: string;
  /** Output from tool calls that are still running, by tool call id. */
  readonly toolCallProgress: Readonly<Record<string, string>>;
};

export function emptyConversationFeed(): ConversationFeed {
  return { events: [], latestSequence: 0, streamingAgentText: "", toolCallProgress: {} };
}

/** Take one committed row, and drop whatever half-finished output it supersedes.
 *
 * A row that arrives twice is the same row: it is replaced in place rather than appended,
 * so a replay that overlaps a live tail leaves the reader with exactly one of each.
 */
export function feedWithCommittedEvent(
  feed: ConversationFeed,
  event: ConversationEvent
): ConversationFeed {
  const events = mergedBySequence(feed.events, event);
  let streamingAgentText = feed.streamingAgentText;
  let toolCallProgress = feed.toolCallProgress;
  if (event.kind === "agent_message") {
    streamingAgentText = "";
  }
  if (event.kind === "tool_call_finished") {
    toolCallProgress = withoutKey(toolCallProgress, event.payload.tool_call_id);
  }
  if (event.kind === "turn_ended") {
    // Nothing half-finished outlives the turn it belonged to.
    streamingAgentText = "";
    toolCallProgress = {};
  }
  return {
    events,
    latestSequence: Math.max(feed.latestSequence, event.sequence),
    streamingAgentText,
    toolCallProgress
  };
}

export function feedWithCommittedEvents(
  feed: ConversationFeed,
  events: readonly ConversationEvent[]
): ConversationFeed {
  return events.reduce(feedWithCommittedEvent, feed);
}

export function feedWithLiveFrame(
  feed: ConversationFeed,
  frame: ConversationLiveFrame
): ConversationFeed {
  if (frame.frame === "agent_message_delta") {
    return { ...feed, streamingAgentText: feed.streamingAgentText + frame.text_delta };
  }
  return {
    ...feed,
    toolCallProgress: { ...feed.toolCallProgress, [frame.tool_call_id]: frame.detail }
  };
}

function mergedBySequence(
  events: readonly ConversationEvent[],
  event: ConversationEvent
): readonly ConversationEvent[] {
  const last = events[events.length - 1];
  if (last === undefined || event.sequence > last.sequence) return [...events, event];
  const existing = events.findIndex((held) => held.sequence === event.sequence);
  if (existing >= 0) {
    const replaced = events.slice();
    replaced[existing] = event;
    return replaced;
  }
  const before = events.findIndex((held) => held.sequence > event.sequence);
  return [...events.slice(0, before), event, ...events.slice(before)];
}

function withoutKey(
  progress: Readonly<Record<string, string>>,
  key: string
): Readonly<Record<string, string>> {
  if (!(key in progress)) return progress;
  const remaining: Record<string, string> = { ...progress };
  delete remaining[key];
  return remaining;
}

/** Whether a turn is running, told from the rows themselves.
 *
 * The snapshot answers this at the moment it was fetched; the rows answer it now. A turn
 * begins at the prompt that reached the backend and ends at its turn-ended row, so a
 * prompt after the last ending is a turn that is still going. A steer's prompt joins the
 * turn already running, which this reads the same way.
 */
export function conversationIsRunning(feed: ConversationFeed): boolean {
  let running = false;
  for (const event of feed.events) {
    if (event.kind === "prompt") running = true;
    else if (event.kind === "turn_ended") running = false;
  }
  return running;
}

/** The model and reasoning effort this conversation is running on now.
 *
 * The snapshot says what it started on; every model-changed row says what it moved to.
 * The last word wins, which is what a picker must show rather than the value from start.
 */
export function currentRunValues(
  started: { model: string | null; reasoningEffort: string | null },
  feed: ConversationFeed
): { model: string | null; reasoningEffort: string | null } {
  let current = started;
  for (const event of feed.events) {
    if (event.kind === "model_changed") {
      current = { model: event.payload.model, reasoningEffort: event.payload.reasoning_effort };
    }
  }
  return current;
}

// --- the stream that keeps a feed current -------------------------------------------------

export type ConversationStreamPorts = {
  readEventsAfter: (conversationId: string, after: number) => Promise<ConversationEvent[]>;
  openTail: (
    conversationId: string,
    after: number,
    handlers: {
      onCommittedEvent: (event: ConversationEvent) => void;
      onLiveFrame: (frame: ConversationLiveFrame) => void;
      onTrouble: () => void;
    }
  ) => () => void;
};

export type ConversationStream = {
  /** Fetch the rows this reader is missing, then tail from there. Also the reconnect. */
  connect: () => Promise<void>;
  close: () => void;
  feed: () => ConversationFeed;
};

/** Keep a feed current: fetch what is missing, then follow along.
 *
 * There is one connect and it is the whole of the reading side. First open, reload,
 * second tab, tab return, dropped stream — every one of them is this same call, because
 * every one of them is the same question: what has happened since the row I hold?
 */
export function createConversationStream(
  conversationId: string,
  ports: ConversationStreamPorts,
  onFeed: (feed: ConversationFeed) => void
): ConversationStream {
  let feed = emptyConversationFeed();
  let closeTail: (() => void) | null = null;
  let closed = false;
  let generation = 0;

  function publish(next: ConversationFeed): void {
    feed = next;
    onFeed(feed);
  }

  async function connect(): Promise<void> {
    if (closed) return;
    const opening = ++generation;
    closeTail?.();
    closeTail = null;
    const missed = await ports.readEventsAfter(conversationId, feed.latestSequence);
    if (closed || opening !== generation) return;
    publish(feedWithCommittedEvents(feed, missed));
    closeTail = ports.openTail(conversationId, feed.latestSequence, {
      onCommittedEvent: (event) => {
        if (opening !== generation) return;
        publish(feedWithCommittedEvent(feed, event));
      },
      onLiveFrame: (frame) => {
        if (opening !== generation) return;
        publish(feedWithLiveFrame(feed, frame));
      },
      onTrouble: () => {
        if (closed || opening !== generation) return;
        // One attempt, not a loop: a server that is not answering will not start
        // answering because this asked again immediately. Coming back to the tab
        // asks again, which is the same call.
        void connect().catch(() => undefined);
      }
    });
  }

  return {
    connect,
    close: () => {
      closed = true;
      closeTail?.();
      closeTail = null;
    },
    feed: () => feed
  };
}
