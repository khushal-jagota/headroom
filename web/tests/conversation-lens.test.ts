import { afterEach, describe, expect, it, vi } from "vitest";

import { emptyConversationFeed, feedWithCommittedEvents } from "../src/lib/conversation/feed";
import {
  conversationFeedForLens,
  conversationLensPreference,
  conversationRowsForLens,
  conversationThreadItemsForLens,
  heldPromptIsInLens,
  rememberConversationLensPreference
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
  afterEach(() => vi.unstubAllGlobals());

  it("keeps one valid browser preference and otherwise defaults to Focus", () => {
    const values = new Map<string, string>();
    vi.stubGlobal("window", {
      localStorage: {
        getItem: (key: string) => values.get(key) ?? null,
        setItem: (key: string, value: string) => values.set(key, value)
      }
    });

    expect(conversationLensPreference()).toBe("focus");
    values.set("panels.conversation.lens", "wide");
    expect(conversationLensPreference()).toBe("focus");
    rememberConversationLensPreference("full");
    expect(conversationLensPreference()).toBe("full");
  });

  it("still works when browser preference storage is unavailable", () => {
    vi.stubGlobal("window", {
      localStorage: {
        getItem: () => { throw new Error("blocked"); },
        setItem: () => { throw new Error("blocked"); }
      }
    });

    expect(conversationLensPreference()).toBe("focus");
    expect(() => rememberConversationLensPreference("full")).not.toThrow();
  });

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
      event(9, "turn_ended", { ending: "completed", error_summary: null }),
      event(10, "proposal_delivery_failed", {
        attempt_count: 10,
        last_error: "write_to_backend_failed",
        sender_message_id: "proposal-failure-1"
      })
    ];
    const fullFeed = feedWithCommittedEvents(emptyConversationFeed(), events);
    const focused = conversationFeedForLens(fullFeed, "focus", "Khushal");

    expect(focused.events.map((row) => row.sequence)).toEqual([1, 4, 5, 6, 7, 8, 9, 10]);
    expect(focused.latestSequence).toBe(10);
    expect(conversationRowsForLens(transcriptRows(focused), "focus").map((row) => row.kind))
      .toEqual([
        "prompt", "agent_message", "permission_ask", "user_input",
        "proposal_delivery_failed"
      ]);
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

  it("shows missing replies and failed or stopped turns as compact Focus rows", () => {
    const fullFeed = feedWithCommittedEvents(emptyConversationFeed(), [
      event(1, "explicit_reply_missing", { prompt_sender: ticket }),
      event(2, "explicit_reply_missing", { prompt_sender: owner }),
      event(3, "turn_ended", { ending: "failed", error_summary: "backend exited" })
    ]);
    const focused = conversationFeedForLens(fullFeed, "focus", "owner");

    expect(focused.events.map((row) => row.sequence)).toEqual([1, 2, 3]);
    expect(conversationRowsForLens(transcriptRows(focused), "focus")).toEqual([
      expect.objectContaining({ kind: "explicit_reply_missing", sequence: 1 }),
      expect.objectContaining({ kind: "explicit_reply_missing", sequence: 2 }),
      expect.objectContaining({
        kind: "turn_ended",
        ending: "failed",
        errorSummary: "backend exited"
      })
    ]);
    const stoppedRows = transcriptRows(emptyConversationFeed(), {
      turnStoppedWithoutAnEnding: true
    });
    expect(conversationRowsForLens(stoppedRows, "focus")).toEqual([
      expect.objectContaining({ kind: "turn_stopped" })
    ]);
    expect(conversationFeedForLens(fullFeed, "full", "owner")).toBe(fullFeed);
  });

  it("settles complete turn structure before Focus hides runtime rows", () => {
    const fullFeed = feedWithCommittedEvents(emptyConversationFeed(), [
      event(1, "prompt", {
        text: "first prompt",
        sender_label: "owner",
        mode: "queue",
        sender: owner,
        recipient: ticket
      }),
      event(2, "message_to_owner", {
        text: "first reply",
        sender_label: "Ticket",
        sender: ticket,
        recipient: owner
      }),
      event(3, "turn_ended", { ending: "completed", error_summary: null }),
      event(4, "prompt", {
        text: "second prompt",
        sender_label: "owner",
        mode: "queue",
        sender: owner,
        recipient: ticket
      })
    ]);
    const rows = transcriptRows(fullFeed);
    const visibleRows = conversationRowsForLens(
      transcriptRows(conversationFeedForLens(fullFeed, "focus", "owner")),
      "focus"
    );
    const turns = conversationThreadItemsForLens(rows, visibleRows, "focus")
      .filter((item) => item.kind === "turn");

    expect(turns).toHaveLength(2);
    expect(turns[0]).toMatchObject({ turnKey: "turn:e1", settled: true, isLatest: false });
    expect(turns[1]).toMatchObject({ turnKey: "turn:e4", settled: false, isLatest: true });
  });

  it("does not draw a turn head when Focus hides its opening prompt", () => {
    const fullFeed = feedWithCommittedEvents(emptyConversationFeed(), [
      event(1, "prompt", {
        text: "supervisor prompt",
        sender_label: "Supervisor",
        mode: "queue",
        sender: { kind: "sprint_item", id: "item" },
        recipient: ticket
      }),
      event(2, "tool_call_started", {
        tool_call_id: "tool-1",
        title: "Inspect",
        tool_kind: "read",
        detail: null
      }),
      event(3, "agent_message", { text: "runtime result" }),
      event(4, "turn_ended", { ending: "completed", error_summary: null })
    ]);
    const rows = transcriptRows(fullFeed);
    const visibleRows = conversationRowsForLens(
      transcriptRows(conversationFeedForLens(fullFeed, "focus", "owner")),
      "focus"
    );

    expect(conversationThreadItemsForLens(rows, visibleRows, "focus")).toEqual([]);
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
