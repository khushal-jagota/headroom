import { describe, expect, it } from "vitest";

import { emptyConversationFeed, feedWithCommittedEvents } from "../src/lib/conversation/feed";
import {
  conversationFeedForLens,
  conversationRowsForLens,
  heldPromptIsInLens
} from "../src/lib/conversation/lens";
import { transcriptRows } from "../src/lib/conversation/transcript";
import type { ConversationEvent, HeldPrompt } from "../src/lib/conversation/wire";

const owner = { kind: "owner", id: "owner" } as const;
const ticket = { kind: "ticket", id: "t_one" } as const;

function event(
  sequence: number,
  kind: ConversationEvent["kind"],
  payload: Record<string, unknown>
): ConversationEvent {
  return {
    conversation_id: "c1",
    sequence,
    kind,
    payload,
    created_at: 1_700_000_000 + sequence
  } as ConversationEvent;
}

describe("Conversation lenses", () => {
  it("focuses on owner messages, addressed replies, asks, and ask settlements", () => {
    const events = [
      event(1, "prompt", {
        text: "owner prompt",
        sender_label: "Khushal",
        mode: "queue",
        sender: owner,
        recipient: ticket
      }),
      event(2, "agent_message", { text: "runtime prose" }),
      event(3, "tool_call_started", {
        tool_call_id: "tool-1",
        title: "Read",
        tool_kind: "read",
        detail: null
      }),
      event(4, "message_to_owner", {
        text: "explicit reply",
        sender_label: "Ticket one",
        sender: ticket,
        recipient: owner
      }),
      event(5, "permission_asked", {
        ask_id: "ask-1",
        title: "Approve",
        detail: null,
        options: []
      }),
      event(6, "permission_answered", { ask_id: "ask-1", option_id: "allow" }),
      event(7, "user_input_requested", { request_id: "input-1", questions: [] }),
      event(8, "user_input_failed", { request_id: "input-1", detail: "invalid" }),
      event(9, "turn_ended", { ending: "completed", error_summary: null })
    ];
    const fullFeed = feedWithCommittedEvents(emptyConversationFeed(), events);
    const focused = conversationFeedForLens(fullFeed, "focus", "Khushal");

    expect(focused.events.map((row) => row.sequence)).toEqual([1, 4, 5, 6, 7, 8, 9]);
    expect(focused.latestSequence).toBe(9);
    expect(conversationRowsForLens(transcriptRows(focused), "focus").map((row) => row.kind))
      .toEqual(["prompt", "agent_message", "permission_ask", "user_input"]);
    expect(conversationFeedForLens(fullFeed, "full", "Khushal")).toBe(fullFeed);
  });

  it("keeps historical owner prompts through the established sender-label fallback", () => {
    const legacyOwnerPrompt = event(3, "prompt", {
      text: "before principals",
      sender_label: "owner",
      mode: "queue"
    });
    const legacyAutomaticPrompt = event(4, "prompt", {
      text: "automatic",
      sender_label: "worker loop",
      mode: "queue"
    });
    const focused = conversationFeedForLens(
      feedWithCommittedEvents(emptyConversationFeed(), [legacyOwnerPrompt, legacyAutomaticPrompt]),
      "focus",
      "owner"
    );

    expect(focused.events).toEqual([legacyOwnerPrompt]);
    expect(focused.latestSequence).toBe(3);
  });

  it("keeps an owner-addressed missing-reply marker and hides another principal's marker", () => {
    const focused = conversationFeedForLens(
      feedWithCommittedEvents(emptyConversationFeed(), [
        event(1, "explicit_reply_missing", { prompt_sender: ticket }),
        event(2, "explicit_reply_missing", { prompt_sender: owner })
      ]),
      "focus",
      "owner"
    );

    expect(focused.events.map((row) => row.sequence)).toEqual([2]);
  });

  it("applies the same principal and legacy rules to held prompts", () => {
    const prompt = (sender: HeldPrompt["sender"], senderLabel: string): HeldPrompt => ({
      held_prompt_id: "held",
      text: "wait",
      sender_message_id: null,
      sender_label: senderLabel,
      sent_at_unix_milliseconds: 1,
      sender,
      recipient: sender == null ? null : ticket,
      queue_reason: "requested"
    });

    expect(heldPromptIsInLens(prompt(owner, "Khushal"), "focus", "Khushal")).toBe(true);
    expect(heldPromptIsInLens(prompt(ticket, "Ticket"), "focus", "Khushal")).toBe(false);
    expect(heldPromptIsInLens(prompt(null, "Khushal"), "focus", "Khushal")).toBe(true);
    expect(heldPromptIsInLens(prompt(ticket, "Ticket"), "full", "Khushal")).toBe(true);
  });
});
