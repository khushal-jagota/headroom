import { describe, expect, it } from "vitest";

import {
  conversationIsRunning,
  conversationLiveness,
  emptyConversationFeed,
  feedWithCommittedEvents
} from "../src/lib/conversation/feed";
import {
  outgoingMessagesTheRecordHasNot,
  type OutgoingMessage
} from "../src/lib/conversation/outgoing";
import { threadItems } from "../src/lib/conversation/threadLayout";
import { transcriptRows } from "../src/lib/conversation/transcript";
import { promptEvent, turnEndedEvent } from "./support/conversationEvents";

describe("Conversation turn boundaries", () => {
  it("keeps a late accepted steer receipt outside the completed turn boundary", () => {
    const feed = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1, "incumbent"),
      turnEndedEvent(2),
      promptEvent(3, "steered", "steer", {
        payload: { sender_message_id: "steer-id" }
      })
    ]);

    expect(conversationIsRunning(feed)).toBe(false);
    expect(conversationLiveness(feed, { latestSequence: 2, isRunning: false })).toEqual({
      isRunning: false,
      turnStoppedWithoutAnEnding: false
    });
    expect(conversationLiveness(feed, { latestSequence: 3, isRunning: false })).toEqual({
      isRunning: false,
      turnStoppedWithoutAnEnding: false
    });

    const rows = transcriptRows(feed);
    expect(rows.map((row) => row.kind)).toEqual(["prompt", "turn_ended", "prompt"]);
    expect(rows.at(-1)).toMatchObject({
      kind: "prompt",
      mode: "steer",
      content: [{ piece: "text", text: "steered" }]
    });
    const outgoing: OutgoingMessage = {
      messageId: "steer-id",
      content: [{ piece: "text", text: "steered" }],
      senderLabel: "owner",
      mode: "steer",
      sentAtUnixMilliseconds: 1_700_000_000_000,
      knownFate: "nothing_yet"
    };
    expect(outgoingMessagesTheRecordHasNot([outgoing], feed.events)).toEqual([]);
    expect(threadItems(rows).filter((item) => item.kind === "turn")).toEqual([
      expect.objectContaining({
        turnKey: "turn:e1",
        settled: true,
        stopped: false,
        isLatest: true
      })
    ]);
  });

  it("leaves a replacement ordinary turn in control when its late steer receipt arrives", () => {
    const feed = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1, "incumbent"),
      turnEndedEvent(2),
      promptEvent(3, "replacement"),
      promptEvent(4, "steered", "steer")
    ]);

    expect(conversationIsRunning(feed)).toBe(true);
    expect(conversationLiveness(feed, { latestSequence: 4, isRunning: true })).toEqual({
      isRunning: true,
      turnStoppedWithoutAnEnding: false
    });

    const turns = threadItems(transcriptRows(feed)).filter((item) => item.kind === "turn");
    expect(turns).toHaveLength(2);
    expect(turns[0]).toMatchObject({
      turnKey: "turn:e1",
      settled: true,
      isLatest: false
    });
    expect(turns[1]).toMatchObject({
      turnKey: "turn:e3",
      settled: false,
      isLatest: true
    });
  });
});
