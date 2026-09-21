import { describe, expect, it } from "vitest";

import {
  heldPromptRowActions,
  heldPromptRowLabel,
  heldPromptRows
} from "../src/lib/conversation/heldPrompts";
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
    mode: "queue",
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
    queue_reason: "requested",
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

  it("marks an uncertain local message as one the record does not hold", () => {
    expect(heldPromptRows([], [local("unknown", 100, "answer_never_came_back")]))
      .toMatchObject([{ heldPromptId: null, state: "unknown" }]);
  });

  it("offers an uncertain row the two things this tab can do alone", () => {
    const [row] = heldPromptRows([], [local("unknown", 100, "answer_never_came_back")]);

    expect(heldPromptRowActions(row!)).toEqual(["stop_drawing", "send_again"]);
  });

  it("keeps the record's own operations on the rows the record holds", () => {
    const [row] = heldPromptRows([held("h-1", null, "server one")], []);

    expect(heldPromptRowActions(row!)).toEqual(["discard", "send_now", "steer"]);
  });

  it("offers nothing on a send that is still on its way", () => {
    const [row] = heldPromptRows([], [local("sending", 100)]);

    expect(heldPromptRowActions(row!)).toEqual([]);
  });

  it("labels file-only and mixed attachment rows without inventing message text", () => {
    const base = heldPromptRows([], [local("files", 100)])[0];
    expect(heldPromptRowLabel({
      ...base,
      content: [{
        piece: "file",
        data: "e30=",
        media_type: "application/json",
        file_name: "facts.json"
      }]
    })).toBe("File message");
    expect(heldPromptRowLabel({
      ...base,
      content: [
        { piece: "image", data: "AQ==", media_type: "image/png" },
        { piece: "file", data: "e30=", media_type: "application/json", file_name: "facts.json" }
      ]
    })).toBe("1 image · 1 file");
  });
});
