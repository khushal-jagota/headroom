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
import {
  agentMessageEvent,
  planUpdatedEvent,
  promptEvent,
  turnEndedEvent
} from "./support/conversationEvents";

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
  it("keeps the plan in the record and renders it on the turn instead of as a thread line", () => {
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
    expect(turns(items)[0]!.plan).toEqual(plan);
  });

  it("replaces the previous plan instead of merging it", () => {
    const { items } = threadFrom([
      promptEvent(1),
      planUpdatedEvent(2, [
        { text: "one", status: "in_progress" },
        { text: "two", status: "pending" }
      ]),
      planUpdatedEvent(3, [{ text: "one", status: "completed" }])
    ]);

    expect(turns(items).map((item) => item.plan)).toEqual([
      [{ text: "one", status: "completed" }]
    ]);
  });

  it("persists the newest plan after settling and moves ownership to a later turn", () => {
    const firstPlan = [{ text: "one", status: "completed" as const }];
    const secondPlan = [{ text: "two", status: "in_progress" as const }];
    const firstTurnEvents = [
      promptEvent(1, "first", "run_when_free", { createdAt: 100 }),
      planUpdatedEvent(2, firstPlan),
      turnEndedEvent(3, { ending: "completed", createdAt: 104 })
    ];
    const settledAnchors = turns(threadFrom(firstTurnEvents).items);

    expect(settledAnchors).toHaveLength(1);
    expect(settledAnchors[0]).toMatchObject({ settled: true, plan: firstPlan });

    const { items } = threadFrom([
      ...firstTurnEvents,
      promptEvent(4, "again", "run_when_free", { createdAt: 200 }),
      planUpdatedEvent(5, secondPlan)
    ]);
    const anchors = turns(items);

    expect(anchors).toHaveLength(2);
    expect(anchors[0]).toMatchObject({ settled: true, plan: null });
    expect(anchors[1]).toMatchObject({ settled: false, plan: secondPlan });
  });

  it("leaves every turn without a plan when the Conversation never planned", () => {
    const { items } = threadFrom([
      promptEvent(1),
      agentMessageEvent(2, "done"),
      turnEndedEvent(3),
      promptEvent(4, "again")
    ]);

    expect(turns(items)).toHaveLength(2);
    expect(turns(items).map((item) => item.plan)).toEqual([null, null]);
  });
});
