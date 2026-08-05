import { describe, expect, it } from "vitest";

import { heldPromptRows } from "../src/lib/conversation/heldPrompts";
import type { OutgoingMessage } from "../src/lib/conversation/outgoing";
import type { HeldPrompt } from "../src/lib/conversation/wire";

function local(
  messageId: string,
  sentAtUnixMilliseconds: number,
  knownFate: OutgoingMessage["knownFate"] = "nothing_yet"
): OutgoingMessage {
  return {
    messageId,
    content: [{ piece: "text", text: `local ${messageId}` }],
    senderLabel: "owner",
    mode: "run_when_free",
    sentAtUnixMilliseconds,
    knownFate
  };
}

function held(
  heldPromptId: string,
  senderMessageId: string | null | undefined,
  text: string
): HeldPrompt {
  return {
    held_prompt_id: heldPromptId,
    sender_message_id: senderMessageId,
    sender_label: "owner",
    sent_at_unix_milliseconds: 100,
    text
  };
}

describe("held prompt composer rows", () => {
  it("preserves server FIFO and appends unmatched local sends in send order", () => {
    const rows = heldPromptRows(
      [held("h-2", null, "server two"), held("h-1", undefined, "server one")],
      [local("late", 300), local("early", 200)]
    );

    expect(rows.map((row) => row.key)).toEqual([
      "held:h-2",
      "held:h-1",
      "local:early",
      "local:late"
    ]);
    expect(rows.slice(0, 2).every((row) => row.heldPromptId !== null)).toBe(true);
    expect(rows.slice(2).every((row) => row.heldPromptId === null)).toBe(true);
  });

  it("merges an optimistic copy by sender id and keeps the server row actionable", () => {
    const rows = heldPromptRows(
      [held("server-id", "same-message", "server copy")],
      [local("same-message", 99, "waiting_for_the_agent")]
    );

    expect(rows).toHaveLength(1);
    expect(rows[0]).toMatchObject({
      key: "held:server-id",
      heldPromptId: "server-id",
      senderMessageId: "same-message",
      content: [{ piece: "text", text: "local same-message" }],
      state: "held"
    });
  });

  it("keeps uncertain local messages inert", () => {
    expect(heldPromptRows([], [local("unknown", 100, "answer_never_came_back")]))
      .toMatchObject([{ heldPromptId: null, state: "unknown" }]);
  });
});
