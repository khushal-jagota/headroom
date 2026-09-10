import { afterEach, describe, expect, it, vi } from "vitest";

import {
  afterTheRecordHasBeenRead,
  mintOutgoingMessage,
  outgoingMessageNote,
  outgoingMessagesTheRecordHasNot
} from "../src/lib/conversation/outgoing";
import type { OutgoingMessage } from "../src/lib/conversation/outgoing";
import type { ConversationEvent } from "../src/lib/conversation/wire";

const sentAt = 1_700_000_000_123;

function outgoing(text: string): OutgoingMessage {
  return mintOutgoingMessage({
    content: [{ piece: "text", text }],
    senderLabel: "owner",
    mode: "run_when_free",
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
        mode: "run_when_free",
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
        mode: "run_when_free",
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
      mode: "run_when_free"
    });
    const second = mintOutgoingMessage({
      content: [{ piece: "text", text: "two" }],
      senderLabel: "owner",
      mode: "run_when_free"
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
      payload: { text: "somebody else's", sender_label: "other", mode: "run_when_free" },
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
