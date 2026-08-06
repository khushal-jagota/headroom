import { describe, expect, it } from "vitest";

import {
  emptyConversationFeed,
  feedWithCommittedEvents
} from "../src/lib/conversation/feed";
import { transcriptRows } from "../src/lib/conversation/transcript";
import {
  threadItems,
  type ThreadItem
} from "../src/lib/conversation/threadLayout";
import type { ConversationEvent } from "../src/lib/conversation/wire";
import { planUpdatedEvent, promptEvent, turnEndedEvent } from "./support/conversationEvents";

type TurnItem = Extract<ThreadItem, { kind: "turn" }>;

function threadFrom(events: readonly ConversationEvent[]): {
  rows: ReturnType<typeof transcriptRows>;
  items: ThreadItem[];
} {
  const feed = feedWithCommittedEvents(emptyConversationFeed(), events);
  const rows = transcriptRows(feed);
  return { rows, items: threadItems(rows) };
}

function turns(items: readonly ThreadItem[]): TurnItem[] {
  return items.filter((item): item is TurnItem => item.kind === "turn");
}

describe("Conversation thread plans", () => {
  it("keeps the plan in the record but removes it from thread history", () => {
    const plan = [
      { text: "read the code", status: "completed" as const },
      { text: "write it", status: "in_progress" as const },
      { text: "test it", status: "pending" as const }
    ];
    const { rows, items } = threadFrom([
      promptEvent(1, "plan it"),
      planUpdatedEvent(2, plan)
    ]);

    expect(rows.filter((row) => row.kind === "plan_updated")).toHaveLength(1);
    expect(
      items.map((item) =>
        item.kind === "turn" ? "turn" : item.kind === "work_group" ? "work" : item.row.kind
      )
    ).toEqual(["prompt", "turn"]);
    expect(turns(items)).toHaveLength(1);
    expect(items.some((item) => item.kind === "row" && item.row.kind === "plan_updated")).toBe(false);
  });

  it("multiple plan snapshots do not alter turn anchors", () => {
    const { items } = threadFrom([
      promptEvent(1),
      planUpdatedEvent(2, [
        { text: "one", status: "in_progress" },
        { text: "two", status: "pending" }
      ]),
      planUpdatedEvent(3, [{ text: "one", status: "completed" }])
    ]);

    expect(turns(items)).toHaveLength(1);
    expect(items).toHaveLength(2);
  });

  it("plans across turns never attach to either turn", () => {
    const firstTurnEvents = [
      promptEvent(1, "first", "run_when_free", { createdAt: 100 }),
      planUpdatedEvent(2, [{ text: "one", status: "completed" }]),
      turnEndedEvent(3, { ending: "completed", createdAt: 104 })
    ];
    const { items } = threadFrom([
      ...firstTurnEvents,
      promptEvent(4, "again", "run_when_free", { createdAt: 200 }),
      planUpdatedEvent(5, [{ text: "two", status: "in_progress" }])
    ]);
    expect(turns(items)).toHaveLength(2);
  });

  it("an orphan plan does not invent a turn", () => {
    const { items } = threadFrom([
      planUpdatedEvent(1, [{ text: "one", status: "in_progress" }])
    ]);
    expect(items).toEqual([]);
  });
});
