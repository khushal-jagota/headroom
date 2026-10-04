import { describe, expect, it } from "vitest";
import { emptyConversationFeed, feedWithCommittedEvents } from "../src/lib/conversation/feed";
import { latestMessageOutcomes, type MessageOutcomeEvent } from "../src/lib/conversation/messageOutcome";
import { recordedMessageFateSentence } from "../src/lib/conversation/composer";
import { transcriptRows } from "../src/lib/conversation/transcript";
import { workspaceAttentionGroups } from "../src/lib/workspaceRail";
import { ticketFacts } from "./support/screenReadings";

function outcome(sequence: number, kind: "prompt" | "prompt_delivery_uncertain", id: string): MessageOutcomeEvent {
  return {
    conversation_id: "settled", sequence, created_at: sequence, kind,
    payload: { content: [{ piece: "text", text: id }], sender_label: "owner", mode: "send_now", sender_message_id: id }
  };
}

describe("message-specific settlement", () => {
  it("settles only the referenced unminted uncertain row", () => {
    const original = outcome(1, "prompt_delivery_uncertain", "same text");
    const another = outcome(2, "prompt_delivery_uncertain", "same text");
    delete original.payload.sender_message_id;
    delete another.payload.sender_message_id;
    const receipt = outcome(3, "prompt", "same text");
    delete receipt.payload.sender_message_id;
    if (receipt.kind === "prompt") receipt.payload.reconciles_sequence = 1;
    const feed = feedWithCommittedEvents(emptyConversationFeed(), [original, another, receipt]);
    expect(transcriptRows(feed).map((row) => [row.sequence, row.kind]))
      .toEqual([[2, "prompt_uncertain"], [3, "prompt"]]);
    expect(feed.events).toHaveLength(3);
  });
  it("uses the latest explicit sequence and preserves unresolved messages", () => {
    const history = [outcome(1, "prompt_delivery_uncertain", "resolved"),
      outcome(2, "prompt_delivery_uncertain", "unknown"), outcome(3, "prompt", "resolved")];
    expect(latestMessageOutcomes([...history].reverse()).get("resolved")?.kind).toBe("prompt");
    const feed = feedWithCommittedEvents(emptyConversationFeed(), history);
    expect(transcriptRows(feed).map((row) => [row.sequence, row.kind])).toEqual([
      [2, "prompt_uncertain"], [3, "prompt"]
    ]);
    expect(feed.events).toHaveLength(3);
    expect(recordedMessageFateSentence(history, "resolved", "delivery uncertain")).toBeNull();
    expect(recordedMessageFateSentence(history, "unknown", null)).toBe("delivery uncertain · do not resend");
  });

  it("does not settle an earlier uncertain prompt from a later successful message", () => {
    const feed = feedWithCommittedEvents(emptyConversationFeed(), [
      outcome(1, "prompt_delivery_uncertain", "earlier"), outcome(2, "prompt", "later")
    ]);
    expect(transcriptRows(feed).map((row) => row.kind)).toEqual(["prompt_uncertain", "prompt"]);
  });
});

it("keeps errored Tickets in a Sprint Item's nested attention groups", () => {
  const broken = { ...ticketFacts({ agent_state: "errored", ticket_status: "agent" }),
    id: "broken", title: "Broken work", priority: "P1" as const };
  expect(workspaceAttentionGroups([broken]).map((group) => [group.key, group.cards[0].id]))
    .toEqual([["errored", broken.id]]);
});
