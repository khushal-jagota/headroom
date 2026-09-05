import { afterEach, describe, expect, it, vi } from "vitest";
import {
  createConversationStream,
  emptyConversationFeed,
  HALF_FINISHED_OUTPUT_INTERVAL_MS,
  type ConversationStreamPorts
} from "../src/lib/conversation/feed";
import type { ConversationEvent } from "../src/lib/conversation/wire";
import { agentMessageEvent, promptEvent } from "./support/conversationEvents";

function deferred<Value>() {
  let resolve!: (value: Value | PromiseLike<Value>) => void;
  const promise = new Promise<Value>((settle) => {
    resolve = settle;
  });
  return { promise, resolve };
}

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

});
