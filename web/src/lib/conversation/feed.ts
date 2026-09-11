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
  return {
    events: [],
    latestSequence: 0,
    streamingAgentText: "",
    toolCallProgress: {}
  };
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
    ...feed,
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

/** Whether a frame says the thing running this conversation is alive right now.
 *
 * Every frame this browser understands says it, whatever else it carries — that it arrived
 * at all is the one thing a content-free frame can honestly report. Two kinds do not. A
 * frame that arrives after its own turn's rows is a ghost: the tail can hand over a piece
 * of text that was queued behind the replay it interrupted, and rows are the record, so
 * when they say nothing is running there is nothing alive behind them. And a frame this
 * browser does not know yet says nothing, because the server may be ahead of it and a page
 * that guessed at the shape would report something nobody sent.
 *
 * A sign of life is not content and never becomes a row. It is kept apart from the feed
 * for exactly that reason: a conversation that is only thinking must move the one line that
 * says so without putting the transcript through a rebuild it has no new words for.
 */
export function liveFrameIsASignOfLife(
  feed: ConversationFeed,
  frame: ConversationLiveFrame
): boolean {
  if (!conversationIsRunning(feed)) return false;
  return (
    frame.frame === "agent_message_delta"
    || frame.frame === "tool_call_progress"
    || frame.frame === "model_thinking"
    || frame.frame === "held_prompts_changed"
  );
}

export function feedWithLiveFrame(
  feed: ConversationFeed,
  frame: ConversationLiveFrame
): ConversationFeed {
  // A ghost frame and a frame this browser does not know are not drawn — see above.
  if (!liveFrameIsASignOfLife(feed, frame)) return feed;
  switch (frame.frame) {
    case "agent_message_delta":
      return { ...feed, streamingAgentText: feed.streamingAgentText + frame.text_delta };
    case "tool_call_progress":
      return {
        ...feed,
        toolCallProgress: { ...feed.toolCallProgress, [frame.tool_call_id]: frame.detail }
      };
    default:
      // Thinking has nothing to show and nothing to keep; queue content lives in the
      // conversation snapshot, which the stream callback refreshes. Both leave the feed
      // exactly as it was, which is what stops them redrawing a thread nothing was added to.
      return feed;
  }
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

/** Whether the rows leave a turn open.
 *
 * A turn begins at an ordinary prompt that reached the backend and ends at its
 * turn-ended row, so the newest of those two rows settles it. A steer receipt records
 * admission to a turn that already existed. It never opens one, even when its provider
 * answer arrives after that turn ended.
 *
 * This is what the rows say, which is not always the whole story — see
 * ``conversationLiveness``, which is what a surface should ask.
 */
export function conversationIsRunning(feed: ConversationFeed): boolean {
  for (let at = feed.events.length - 1; at >= 0; at -= 1) {
    const event = feed.events[at];
    if (event?.kind === "turn_ended") return false;
    if (event?.kind === "prompt" && event.payload.mode !== "steer") return true;
  }
  return false;
}

/** What the conversation system says about itself, at the row it had seen when it said it. */
export type ConversationLivenessSnapshot = {
  latestSequence: number;
  isRunning: boolean;
};

export type ConversationLiveness = {
  isRunning: boolean;
  /** The rows leave a turn open and the system says none is running: the turn stopped
   *  without its ending ever being written. A server that went away mid-turn leaves
   *  exactly this, and it is the only thing that does. */
  turnStoppedWithoutAnEnding: boolean;
};

/** Whether a turn is running, from the rows and the system's own answer together.
 *
 * The rows are the record and they are almost always the fresher of the two, so they
 * win — a surface following a live tail must not be dragged backwards by a snapshot
 * taken before the last row landed.
 *
 * The exception is the one case the rows cannot describe. Ending a turn is a row, so a
 * process that stops mid-turn writes no ending, and the rows are left saying "running"
 * for as long as they exist. Only the system can say otherwise, and it can only be
 * believed when it had already seen every row this reader holds. That is what freshness
 * means here, and it is why the comparison is against the newest row rather than a clock.
 */
export function conversationLiveness(
  feed: ConversationFeed,
  snapshot: ConversationLivenessSnapshot | null
): ConversationLiveness {
  const rowsSayRunning = conversationIsRunning(feed);
  if (snapshot === null || snapshot.latestSequence < feed.latestSequence) {
    return { isRunning: rowsSayRunning, turnStoppedWithoutAnEnding: false };
  }
  return {
    isRunning: snapshot.isRunning,
    turnStoppedWithoutAnEnding: rowsSayRunning && !snapshot.isRunning
  };
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

/** How often half-finished output reaches the reader.
 *
 * Text arrives a few characters at a time, and every one of those redraws the thread and
 * re-renders the whole of the answer written so far, which grows with the answer. So the
 * deltas between one screen update and the next are gathered up and drawn together. At a
 * tenth of a second the words still appear as they are being written. Committed rows never
 * wait: a row is the record, and it is drawn the moment it lands.
 */
export const HALF_FINISHED_OUTPUT_INTERVAL_MS = 100;

/** The same, for a tab nobody is looking at. The work costs exactly as much there and the
 *  words are not being read; coming back draws whatever has accumulated. */
export const HIDDEN_HALF_FINISHED_OUTPUT_INTERVAL_MS = 1_000;

/** Whether nobody is looking at this tab. Asked of a document only where there is one:
 *  this module is exercised headlessly, without a browser. */
function theTabIsHidden(): boolean {
  return typeof document !== "undefined" && document.visibilityState === "hidden";
}

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
  /** Everything the stream holds, including half-finished output that has not been drawn
   *  yet. Rows are always here the moment they land. */
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
  onFeed: (feed: ConversationFeed) => void,
  /** Called once the rows are in and the tail is open. This is where a reader asks the
   *  system about itself again: reconnecting is what happens after a server went away,
   *  and the rows alone cannot tell you that a turn stopped when it did. */
  onConnected?: () => void,
  onHeldPromptsChanged?: () => void,
  /** A frame arrived that says the thing running this conversation is alive right now.
   *  Told separately from the feed and never made to wait, because it is not content:
   *  the line that says a turn is working moves on this and on nothing else. */
  onSignOfLife?: () => void
): ConversationStream {
  let feed = emptyConversationFeed();
  let closeTail: (() => void) | null = null;
  let closed = false;
  let generation = 0;
  let drawingHalfFinishedOutput: ReturnType<typeof setTimeout> | null = null;

  function stopWaitingToDraw(): void {
    if (drawingHalfFinishedOutput === null) return;
    clearTimeout(drawingHalfFinishedOutput);
    drawingHalfFinishedOutput = null;
  }

  function publish(next: ConversationFeed): void {
    stopWaitingToDraw();
    feed = next;
    onFeed(feed);
  }

  /** Keep half-finished output, and draw it with whatever else arrives before the next
   *  screen update. A frame that changed nothing is not drawn at all. */
  function publishOnTheNextScreenUpdate(next: ConversationFeed): void {
    if (next === feed) return;
    feed = next;
    if (drawingHalfFinishedOutput !== null) return;
    drawingHalfFinishedOutput = setTimeout(
      () => {
        drawingHalfFinishedOutput = null;
        if (closed) return;
        onFeed(feed);
      },
      theTabIsHidden()
        ? HIDDEN_HALF_FINISHED_OUTPUT_INTERVAL_MS
        : HALF_FINISHED_OUTPUT_INTERVAL_MS
    );
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
        if (frame.frame === "held_prompts_changed") onHeldPromptsChanged?.();
        if (liveFrameIsASignOfLife(feed, frame)) onSignOfLife?.();
        publishOnTheNextScreenUpdate(feedWithLiveFrame(feed, frame));
      },
      onTrouble: () => {
        if (closed || opening !== generation) return;
        // One attempt, not a loop: a server that is not answering will not start
        // answering because this asked again immediately. Coming back to the tab
        // asks again, which is the same call.
        void connect().catch(() => undefined);
      }
    });
    onConnected?.();
  }

  return {
    connect,
    close: () => {
      closed = true;
      stopWaitingToDraw();
      closeTail?.();
      closeTail = null;
    },
    feed: () => feed
  };
}
