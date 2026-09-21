/**
 * The wake-up that settles an unanswered send.
 *
 * `live-conversation-unanswered-send-browser.test.mjs` reads what the screen says about a
 * send nobody answered, but it never moves the clock. These drive the clock, and they are
 * the only cover for the scheduling itself: if the wake-up never fires, the row sits at the
 * bottom of the conversation forever, which is the bug this deadline exists to kill.
 *
 * The rest of the outgoing unit tests were cut. They assert return values in a node
 * environment with no DOM. These three are failure behaviour at a seam, so they stay.
 */
import { describe, expect, it, vi } from "vitest";

import {
  SEND_DEADLINE_MILLISECONDS,
  tellWhenAWaitRunsOut
} from "../src/lib/conversation/outgoing";
import type { OutgoingMessage } from "../src/lib/conversation/outgoing";

const sentAt = 1_700_000_000_123;

describe("a send nobody answered", () => {
  function waiting(
    messageId: string,
    sentAtUnixMilliseconds: number,
    knownFate: OutgoingMessage["knownFate"] = "nothing_yet"
  ): OutgoingMessage {
    return {
      messageId,
      content: [{ piece: "text", text: messageId }],
      senderLabel: "owner",
      mode: "steer",
      sentAtUnixMilliseconds,
      knownFate
    };
  }

  it("says no answer is coming when the wait runs out, without being asked again", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(sentAt);
      const messages = [waiting("waited", sentAt)];
      const told: (readonly OutgoingMessage[])[] = [];
      const stopWaiting = tellWhenAWaitRunsOut(messages, (next) => told.push(next));

      vi.advanceTimersByTime(SEND_DEADLINE_MILLISECONDS - 1);
      expect(told).toEqual([]);

      vi.advanceTimersByTime(1);
      expect(told.map((next) => next.map((message) => message.knownFate)))
        .toEqual([["answer_never_came_back"]]);

      stopWaiting();
    } finally {
      vi.useRealTimers();
    }
  });

  it("waits on the earliest send and says nothing about the ones still in time", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(sentAt);
      const messages = [
        waiting("early", sentAt),
        waiting("late", sentAt + SEND_DEADLINE_MILLISECONDS)
      ];
      const told: (readonly OutgoingMessage[])[] = [];
      const stopWaiting = tellWhenAWaitRunsOut(messages, (next) => told.push(next));

      vi.advanceTimersByTime(SEND_DEADLINE_MILLISECONDS);
      expect(told).toHaveLength(1);
      expect(told[0]!.map((message) => [message.messageId, message.knownFate])).toEqual([
        ["early", "answer_never_came_back"],
        ["late", "nothing_yet"]
      ]);

      stopWaiting();
    } finally {
      vi.useRealTimers();
    }
  });

  it("says nothing once the reader has gone, or when nothing is waiting at all", () => {
    vi.useFakeTimers();
    try {
      vi.setSystemTime(sentAt);
      const told: (readonly OutgoingMessage[])[] = [];

      tellWhenAWaitRunsOut([waiting("abandoned", sentAt)], (next) => told.push(next))();
      tellWhenAWaitRunsOut(
        [waiting("already told", sentAt, "answer_never_came_back")],
        (next) => told.push(next)
      );
      vi.advanceTimersByTime(SEND_DEADLINE_MILLISECONDS * 2);

      expect(told).toEqual([]);
    } finally {
      vi.useRealTimers();
    }
  });
});
