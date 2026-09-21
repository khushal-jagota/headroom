import { afterEach, describe, expect, it, vi } from "vitest";

import {
  SEND_DEADLINE_MILLISECONDS,
  afterTheRecordHasBeenRead,
  afterWaitingLongEnoughForAnAnswer,
  anOutgoingMessageIsWaitingOnTheRecord,
  mintOutgoingMessage,
  outgoingMessageNote,
  outgoingMessagesByPlace,
  outgoingMessagesTheRecordHasNot,
  whenTheNextWaitRunsOut
} from "../src/lib/conversation/outgoing";
import type { OutgoingMessage } from "../src/lib/conversation/outgoing";
import type { ConversationEvent } from "../src/lib/conversation/wire";

const sentAt = 1_700_000_000_123;

function outgoing(text: string): OutgoingMessage {
  return mintOutgoingMessage({
    content: [{ piece: "text", text }],
    senderLabel: "owner",
    mode: "queue",
    sentAtUnixMilliseconds: sentAt
  });
}

function senderMessageRecordEvent(
  sequence: number,
  kind:
    | "prompt"
    | "prompt_delivery_refused"
    | "prompt_delivery_uncertain"
    | "prompt_discarded",
  message: OutgoingMessage
): ConversationEvent {
  const base = {
    conversation_id: "conversation-1",
    sequence,
    created_at: 1_700_000_000
  };
  if (kind === "prompt") {
    return {
      ...base,
      kind,
      payload: {
        text: "one",
        sender_label: "owner",
        mode: "queue",
        sender_message_id: message.messageId,
        sent_at_unix_milliseconds: message.sentAtUnixMilliseconds
      }
    };
  }
  if (kind === "prompt_delivery_refused") {
    return {
      ...base,
      kind,
      payload: {
        text: "one",
        sender_label: "owner",
        mode: "queue",
        refusal_reason: "backend_did_not_start",
        sender_message_id: message.messageId
      }
    };
  }
  if (kind === "prompt_delivery_uncertain") {
    return {
      ...base,
      kind,
      payload: {
        text: "one",
        sender_label: "owner",
        mode: "steer",
        sender_message_id: message.messageId
      }
    };
  }
  return {
    ...base,
    kind,
    payload: {
      text: "one",
      sender_label: "owner",
      sender_message_id: message.messageId
    }
  };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("outgoing Conversation messages", () => {
  it("mints unique identities at the exact send time", () => {
    vi.spyOn(Date, "now").mockReturnValue(sentAt);
    vi.spyOn(Math, "random").mockReturnValueOnce(0.1).mockReturnValueOnce(0.2);

    const first = mintOutgoingMessage({
      content: [{ piece: "text", text: "one" }],
      senderLabel: "owner",
      mode: "queue"
    });
    const second = mintOutgoingMessage({
      content: [{ piece: "text", text: "two" }],
      senderLabel: "owner",
      mode: "queue"
    });

    expect(first.messageId).not.toBe(second.messageId);
    expect(first.sentAtUnixMilliseconds).toBe(sentAt);
    expect(second.sentAtUnixMilliseconds).toBe(sentAt);
    expect(first.knownFate).toBe("nothing_yet");
  });

  it("shows only the notes justified by a known fate", () => {
    const message = outgoing("one");
    expect(outgoingMessageNote(message)).toBeNull();
    expect(outgoingMessageNote({ ...message, knownFate: "sent_before_this_page" })).toBeNull();
    expect(outgoingMessageNote({ ...message, knownFate: "waiting_for_the_agent" }))
      .toBe("waiting for the agent to be free");
    expect(outgoingMessageNote({ ...message, knownFate: "answer_never_came_back" }))
      .toBe("the server never said whether this arrived");
  });

  it.each([
    "prompt",
    "prompt_delivery_refused",
    "prompt_delivery_uncertain",
    "prompt_discarded"
  ] as const)(
    "stops drawing a message when its %s row arrives",
    (kind) => {
      const first = outgoing("one");
      const second = outgoing("two");
      expect(outgoingMessagesTheRecordHasNot(
        [first, second],
        [senderMessageRecordEvent(1, kind, first)]
      )).toEqual([second]);
    }
  );

  it("keeps messages when no row carries their sender identity", () => {
    const messages = [outgoing("one"), outgoing("two")];
    const unrelated = {
      conversation_id: "conversation-1",
      sequence: 1,
      kind: "prompt",
      payload: { text: "somebody else's", sender_label: "other", mode: "queue" },
      created_at: 1_700_000_000
    } satisfies ConversationEvent;

    expect(outgoingMessagesTheRecordHasNot(messages, [])).toBe(messages);
    expect(outgoingMessagesTheRecordHasNot(messages, [unrelated])).toBe(messages);
  });

  it("settles recalled in-flight messages only after the record is read", () => {
    const held = { ...outgoing("held"), knownFate: "waiting_for_the_agent" as const };
    const recalled = { ...outgoing("in flight"), knownFate: "sent_before_this_page" as const };

    const settled = afterTheRecordHasBeenRead([held, recalled]);

    expect(settled).toEqual([
      held,
      { ...recalled, knownFate: "answer_never_came_back" }
    ]);
    expect(outgoingMessageNote(settled[1]))
      .toBe("the server never said whether this arrived");
    expect(afterTheRecordHasBeenRead(settled)).toBe(settled);
  });
});

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

  const deadlinePassed = sentAt + SEND_DEADLINE_MILLISECONDS;

  it("says no answer is coming once the wait is over, and not before", () => {
    const messages = [waiting("waited", sentAt), waiting("just-sent", sentAt + 1)];

    expect(afterWaitingLongEnoughForAnAnswer(messages, deadlinePassed)
      .map((message) => [message.messageId, message.knownFate]))
      .toEqual([["waited", "answer_never_came_back"], ["just-sent", "nothing_yet"]]);
    expect(afterWaitingLongEnoughForAnAnswer(messages, deadlinePassed - 1))
      .toBe(messages);
  });

  it("leaves a message brought back from before the page alone, however old", () => {
    const recalled = [waiting("recalled", 0, "sent_before_this_page")];

    expect(afterWaitingLongEnoughForAnAnswer(recalled, deadlinePassed)).toBe(recalled);
  });

  it("leaves a message the system said it was holding alone", () => {
    const held = [waiting("queued", sentAt, "waiting_for_the_agent")];

    expect(afterWaitingLongEnoughForAnAnswer(held, deadlinePassed)).toBe(held);
  });

  it("reports when the earliest wait runs out, and nothing when none is waiting", () => {
    expect(whenTheNextWaitRunsOut([waiting("late", sentAt + 50), waiting("early", sentAt)]))
      .toBe(sentAt + SEND_DEADLINE_MILLISECONDS);
    expect(whenTheNextWaitRunsOut([])).toBeNull();
    expect(whenTheNextWaitRunsOut([waiting("told", sentAt, "answer_never_came_back")]))
      .toBeNull();
  });

  it("knows when the record is the only thing that can settle what this tab holds", () => {
    expect(anOutgoingMessageIsWaitingOnTheRecord([waiting("sending", sentAt)])).toBe(false);
    expect(anOutgoingMessageIsWaitingOnTheRecord(
      [waiting("uncertain", sentAt, "answer_never_came_back")]
    )).toBe(true);
    expect(anOutgoingMessageIsWaitingOnTheRecord(
      [waiting("reloaded", sentAt, "sent_before_this_page")]
    )).toBe(true);
  });

  it("draws an unanswered send above the composer instead of under every row", () => {
    const placed = outgoingMessagesByPlace(
      [
        waiting("on-its-way", sentAt),
        waiting("uncertain", sentAt, "answer_never_came_back"),
        waiting("queued", sentAt, "waiting_for_the_agent"),
        waiting("in-the-record-queue", sentAt)
      ],
      {
        composerStackMessageIds: [],
        serverHeldSenderMessageIds: new Set(["in-the-record-queue"])
      }
    );

    expect(placed.thread.map((message) => message.messageId)).toEqual(["on-its-way"]);
    expect(placed.composerStack.map((message) => message.messageId))
      .toEqual(["uncertain", "queued", "in-the-record-queue"]);
  });

  it("keeps a copy this pane already put above the composer there", () => {
    const placed = outgoingMessagesByPlace(
      [waiting("promoted", sentAt)],
      { composerStackMessageIds: ["promoted"], serverHeldSenderMessageIds: new Set() }
    );

    expect(placed.thread).toEqual([]);
    expect(placed.composerStack.map((message) => message.messageId)).toEqual(["promoted"]);
  });

  it("carries the composer's picks so a send again runs on what was asked for", () => {
    expect(mintOutgoingMessage({
      content: [{ piece: "text", text: "picked" }],
      senderLabel: "owner",
      mode: "send_now",
      sentAtUnixMilliseconds: sentAt,
      pickedModel: "opus",
      pickedReasoningEffort: "high"
    })).toMatchObject({ pickedModel: "opus", pickedReasoningEffort: "high" });
    expect(outgoing("nothing picked").pickedModel).toBeUndefined();
  });
});
