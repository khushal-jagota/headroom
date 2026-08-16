import { afterEach, describe, expect, it, vi } from "vitest";

import {
  conversationIsRunning,
  conversationLiveness,
  createConversationStream,
  currentRunValues,
  emptyConversationFeed,
  feedWithCommittedEvent,
  feedWithCommittedEvents,
  feedWithLiveFrame,
  liveFrameIsASignOfLife,
  HALF_FINISHED_OUTPUT_INTERVAL_MS,
  HIDDEN_HALF_FINISHED_OUTPUT_INTERVAL_MS,
  type ConversationFeed,
  type ConversationStream,
  type ConversationStreamPorts
} from "../src/lib/conversation/feed";
import type {
  ConversationEvent,
  ConversationLiveFrame
} from "../src/lib/conversation/wire";
import {
  agentMessageEvent,
  permissionAskedEvent,
  promptEvent,
  toolCallFinishedEvent,
  turnEndedEvent
} from "./support/conversationEvents";

function deferred<Value>() {
  let resolve!: (value: Value | PromiseLike<Value>) => void;
  const promise = new Promise<Value>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

describe("Conversation feed record", () => {
  it("merges overlapping rows once, replaces matching sequences, and orders late rows", () => {
    let replayed = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1),
      agentMessageEvent(2, "one"),
      agentMessageEvent(3, "two")
    ]);
    replayed = feedWithCommittedEvents(replayed, [
      agentMessageEvent(2, "one"),
      agentMessageEvent(3, "two"),
      agentMessageEvent(4, "three")
    ]);

    expect(replayed.events.map((event) => event.sequence)).toEqual([1, 2, 3, 4]);
    expect(replayed.latestSequence).toBe(4);

    let replaced = feedWithCommittedEvent(emptyConversationFeed(), agentMessageEvent(2, "first"));
    replaced = feedWithCommittedEvent(replaced, agentMessageEvent(2, "second"));
    expect(replaced.events).toHaveLength(1);
    expect(replaced.events[0]).toEqual(agentMessageEvent(2, "second"));

    let ordered = feedWithCommittedEvent(emptyConversationFeed(), agentMessageEvent(3, "late"));
    ordered = feedWithCommittedEvent(ordered, promptEvent(1));
    expect(ordered.events.map((event) => event.sequence)).toEqual([1, 3]);
    expect(ordered.latestSequence).toBe(3);
  });

  it("holds transient text and tool progress until committed rows supersede them", () => {
    let feed = feedWithCommittedEvent(emptyConversationFeed(), promptEvent(1));
    feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "he" });
    feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "llo" });
    feed = feedWithLiveFrame(feed, {
      frame: "tool_call_progress",
      tool_call_id: "t1",
      detail: "reading"
    });
    feed = feedWithLiveFrame(feed, {
      frame: "tool_call_progress",
      tool_call_id: "t2",
      detail: "writing"
    });

    expect(feed.streamingAgentText).toBe("hello");
    expect(feed.toolCallProgress).toEqual({ t1: "reading", t2: "writing" });
    expect(feed.events).toHaveLength(1);

    feed = feedWithCommittedEvent(feed, toolCallFinishedEvent(2, { toolCallId: "t1" }));
    expect(feed.toolCallProgress).toEqual({ t2: "writing" });

    feed = feedWithCommittedEvent(feed, agentMessageEvent(3, "hello there"));
    expect(feed.streamingAgentText).toBe("");
    feed = feedWithLiveFrame(feed, { frame: "agent_message_delta", text_delta: "partial" });
    feed = feedWithCommittedEvent(feed, turnEndedEvent(4, { ending: "interrupted" }));
    expect(feed.streamingAgentText).toBe("");
    expect(feed.toolCallProgress).toEqual({});
  });

  it("takes a frame with nothing in it as a sign of life and leaves the feed alone", () => {
    const feed = feedWithCommittedEvent(emptyConversationFeed(), promptEvent(1));

    for (const frame of [
      { frame: "model_thinking" },
      { frame: "held_prompts_changed" }
    ] as const) {
      expect(liveFrameIsASignOfLife(feed, frame)).toBe(true);
      // The same feed back, not an equal one: a thread with no new words in it is not
      // rebuilt to say the agent is still there.
      expect(feedWithLiveFrame(feed, frame)).toBe(feed);
    }
  });

  it("takes text and tool progress as signs of life too, and keeps what they carry", () => {
    const feed = feedWithCommittedEvent(emptyConversationFeed(), promptEvent(1));
    const delta = { frame: "agent_message_delta", text_delta: "he" } as const;
    const progress = {
      frame: "tool_call_progress",
      tool_call_id: "t1",
      detail: "reading"
    } as const;

    expect(liveFrameIsASignOfLife(feed, delta)).toBe(true);
    expect(liveFrameIsASignOfLife(feed, progress)).toBe(true);
    expect(feedWithLiveFrame(feed, delta).streamingAgentText).toBe("he");
    expect(feedWithLiveFrame(feed, progress).toolCallProgress).toEqual({ t1: "reading" });
  });

  it("rejects ghost frames once the turn is over", () => {
    const ended = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1),
      agentMessageEvent(2, "finished"),
      turnEndedEvent(3)
    ]);

    expect(
      feedWithLiveFrame(ended, { frame: "agent_message_delta", text_delta: "ghost" })
    ).toBe(ended);
    expect(
      feedWithLiveFrame(ended, {
        frame: "tool_call_progress",
        tool_call_id: "t1",
        detail: "ghost"
      })
    ).toBe(ended);
    expect(feedWithLiveFrame(ended, { frame: "model_thinking" })).toBe(ended);
    expect(liveFrameIsASignOfLife(ended, { frame: "model_thinking" })).toBe(false);
  });

  it("ignores a live frame this browser does not understand", () => {
    const running = feedWithCommittedEvent(emptyConversationFeed(), promptEvent(1));
    const unknownFrame = {
      frame: "something_new",
      payload: 1
    } as unknown as ConversationLiveFrame;

    expect(feedWithLiveFrame(running, unknownFrame)).toBe(running);
    expect(liveFrameIsASignOfLife(running, unknownFrame)).toBe(false);
  });

  it("reads running state from the newest prompt or ending row", () => {
    const idle = emptyConversationFeed();
    const running = feedWithCommittedEvent(idle, promptEvent(1));
    const ended = feedWithCommittedEvent(running, turnEndedEvent(2));

    expect(conversationIsRunning(idle)).toBe(false);
    expect(conversationIsRunning(running)).toBe(true);
    expect(conversationIsRunning(ended)).toBe(false);
  });
});

describe("Conversation feed liveness", () => {
  it("recognises a stopped turn only from a snapshot that has seen every held row", () => {
    const feed = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1),
      permissionAskedEvent(2, { askId: "a1", title: "Run ls" })
    ]);

    expect(conversationLiveness(feed, { latestSequence: 2, isRunning: false })).toEqual({
      isRunning: false,
      turnStoppedWithoutAnEnding: true
    });
    expect(conversationLiveness(feed, { latestSequence: 1, isRunning: false })).toEqual({
      isRunning: true,
      turnStoppedWithoutAnEnding: false
    });
    expect(conversationLiveness(feed, null)).toEqual({
      isRunning: true,
      turnStoppedWithoutAnEnding: false
    });
  });

  it("believes fresh running and idle snapshots without inventing a stopped turn", () => {
    const ended = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1),
      turnEndedEvent(2)
    ]);

    expect(conversationLiveness(ended, { latestSequence: 3, isRunning: true })).toEqual({
      isRunning: true,
      turnStoppedWithoutAnEnding: false
    });
    expect(conversationLiveness(ended, { latestSequence: 2, isRunning: false })).toEqual({
      isRunning: false,
      turnStoppedWithoutAnEnding: false
    });
  });

  it("uses the last model-change row or the Conversation's starting values", () => {
    const firstChange = {
      conversation_id: "c1",
      sequence: 1,
      kind: "model_changed",
      payload: { model: "opus", reasoning_effort: "high" },
      created_at: 1_700_000_000
    } satisfies ConversationEvent;
    const lastChange = {
      ...firstChange,
      sequence: 2,
      payload: { model: "sonnet", reasoning_effort: "low" }
    } satisfies ConversationEvent;
    const changed = feedWithCommittedEvents(emptyConversationFeed(), [firstChange, lastChange]);

    expect(currentRunValues({ model: "fable", reasoningEffort: null }, changed)).toEqual({
      model: "sonnet",
      reasoningEffort: "low"
    });
    expect(
      currentRunValues({ model: "fable", reasoningEffort: null }, emptyConversationFeed())
    ).toEqual({ model: "fable", reasoningEffort: null });
  });
});

describe("Conversation stream", () => {
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fetches then tails, publishes live work, and reconnects after closing the old tail", async () => {
    vi.useFakeTimers();
    const reconnectRead = deferred<ConversationEvent[]>();
    const replacementOpened = deferred<void>();
    const calls: string[] = [];
    let firstTailHandlers: Parameters<ConversationStreamPorts["openTail"]>[2] | null = null;
    let connectedCount = 0;
    let heldPromptsChangedCount = 0;

    const ports: ConversationStreamPorts = {
      readEventsAfter: (conversationId, after) => {
        calls.push(`read:${conversationId}:${after}`);
        if (after === 0) {
          return Promise.resolve([promptEvent(1), agentMessageEvent(2, "first")]);
        }
        return reconnectRead.promise;
      },
      openTail: (conversationId, after, handlers) => {
        calls.push(`open:${conversationId}:${after}`);
        if (after === 2) firstTailHandlers = handlers;
        if (after === 3) replacementOpened.resolve(undefined);
        return () => calls.push(`close:${after}`);
      }
    };
    let published = emptyConversationFeed();
    const stream = createConversationStream(
      "c1",
      ports,
      (next) => {
        published = next;
      },
      () => {
        connectedCount += 1;
      },
      () => {
        heldPromptsChangedCount += 1;
      }
    );

    await stream.connect();

    expect(calls).toEqual(["read:c1:0", "open:c1:2"]);
    expect(published.latestSequence).toBe(2);
    expect(connectedCount).toBe(1);
    expect(firstTailHandlers).not.toBeNull();

    firstTailHandlers!.onLiveFrame({
      frame: "agent_message_delta",
      text_delta: "streaming"
    });
    await vi.advanceTimersByTimeAsync(HALF_FINISHED_OUTPUT_INTERVAL_MS);
    expect(published.streamingAgentText).toBe("streaming");
    firstTailHandlers!.onLiveFrame({ frame: "held_prompts_changed" });
    expect(heldPromptsChangedCount).toBe(1);

    firstTailHandlers!.onTrouble();
    expect(calls.slice(-2)).toEqual(["close:2", "read:c1:2"]);

    reconnectRead.resolve([agentMessageEvent(3, "second")]);
    await replacementOpened.promise;

    expect(calls.slice(-1)).toEqual(["open:c1:3"]);
    expect(published.events.map((event) => event.sequence)).toEqual([1, 2, 3]);
    expect(published.streamingAgentText).toBe("");
    expect(stream.feed()).toBe(published);
    expect(connectedCount).toBe(2);

    stream.close();
    expect(calls.at(-1)).toBe("close:3");
  });

  it("gathers up streamed text between screen updates, and draws a row the moment it lands", async () => {
    vi.useFakeTimers();
    const live = await streamOnOneOpenTurn();

    live.tail.onLiveFrame({ frame: "agent_message_delta", text_delta: "one " });
    live.tail.onLiveFrame({ frame: "agent_message_delta", text_delta: "two " });
    live.tail.onLiveFrame({ frame: "agent_message_delta", text_delta: "three" });

    // Nothing has been drawn yet, and everything has been kept.
    expect(live.publishedCount).toBe(1);
    expect(live.stream.feed().streamingAgentText).toBe("one two three");

    await vi.advanceTimersByTimeAsync(HALF_FINISHED_OUTPUT_INTERVAL_MS);
    expect(live.publishedCount).toBe(2);
    expect(live.published.streamingAgentText).toBe("one two three");

    // A row waits for nothing, and it takes the half-finished text with it.
    live.tail.onLiveFrame({ frame: "agent_message_delta", text_delta: " and more" });
    live.tail.onCommittedEvent(agentMessageEvent(3, "one two three and more"));
    expect(live.publishedCount).toBe(3);
    expect(live.published.streamingAgentText).toBe("");

    // The drawing that was waiting was cancelled by the row rather than left to run.
    await vi.advanceTimersByTimeAsync(HALF_FINISHED_OUTPUT_INTERVAL_MS);
    expect(live.publishedCount).toBe(3);

    live.stream.close();
  });

  it("draws half-finished output far less often for a tab nobody is looking at", async () => {
    vi.useFakeTimers();
    vi.stubGlobal("document", { visibilityState: "hidden" });
    const live = await streamOnOneOpenTurn();

    live.tail.onLiveFrame({ frame: "agent_message_delta", text_delta: "unwatched" });
    await vi.advanceTimersByTimeAsync(HALF_FINISHED_OUTPUT_INTERVAL_MS);
    expect(live.publishedCount).toBe(1);

    await vi.advanceTimersByTimeAsync(
      HIDDEN_HALF_FINISHED_OUTPUT_INTERVAL_MS - HALF_FINISHED_OUTPUT_INTERVAL_MS
    );
    expect(live.publishedCount).toBe(2);
    expect(live.published.streamingAgentText).toBe("unwatched");

    live.stream.close();
  });

  it("says a frame is a sign of life the moment it arrives, however little it carries", async () => {
    vi.useFakeTimers();
    const live = await streamOnOneOpenTurn();

    live.tail.onLiveFrame({ frame: "model_thinking" });
    live.tail.onLiveFrame({ frame: "model_thinking" });

    // Thinking says the agent is alive now, and it never waits for a screen update. It
    // also draws nothing: the thread it would redraw has no new words in it.
    expect(live.signsOfLife).toBe(2);
    expect(live.publishedCount).toBe(1);
    await vi.advanceTimersByTimeAsync(HIDDEN_HALF_FINISHED_OUTPUT_INTERVAL_MS);
    expect(live.publishedCount).toBe(1);

    live.stream.close();
  });

  it("stops waiting to draw when the reader closes the conversation", async () => {
    vi.useFakeTimers();
    const live = await streamOnOneOpenTurn();

    live.tail.onLiveFrame({ frame: "agent_message_delta", text_delta: "half a word" });
    live.stream.close();

    await vi.advanceTimersByTimeAsync(HIDDEN_HALF_FINISHED_OUTPUT_INTERVAL_MS);
    expect(live.publishedCount).toBe(1);
  });
});

/** A connected stream on a conversation with a turn open, and everything it has said. */
type OpenTurnStream = {
  stream: ConversationStream;
  tail: Parameters<ConversationStreamPorts["openTail"]>[2];
  published: ConversationFeed;
  publishedCount: number;
  signsOfLife: number;
};

async function streamOnOneOpenTurn(): Promise<OpenTurnStream> {
  const live: OpenTurnStream = {
    stream: null as unknown as ConversationStream,
    tail: null as unknown as OpenTurnStream["tail"],
    published: emptyConversationFeed(),
    publishedCount: 0,
    signsOfLife: 0
  };
  live.stream = createConversationStream(
    "c1",
    {
      readEventsAfter: () => Promise.resolve([promptEvent(1)]),
      openTail: (_conversationId, _after, handlers) => {
        live.tail = handlers;
        return () => undefined;
      }
    },
    (next) => {
      live.published = next;
      live.publishedCount += 1;
    },
    undefined,
    undefined,
    () => {
      live.signsOfLife += 1;
    }
  );
  await live.stream.connect();
  return live;
}
