import { describe, expect, it } from "vitest";

import {
  emptyConversationFeed,
  feedWithCommittedEvents
} from "../src/lib/conversation/feed";
import {
  foldedWorkSentence,
  hiddenWorkSentence,
  transcriptRows,
  turnFoldLabel,
  workedSentence,
  VISIBLE_RUNNING_WORK_ENTRIES
} from "../src/lib/conversation/transcript";
import {
  threadItems,
  type ThreadItem
} from "../src/lib/conversation/threadLayout";
import { messageContentText, type ConversationEvent } from "../src/lib/conversation/wire";
import {
  agentMessageEvent,
  permissionAskedEvent,
  planUpdatedEvent,
  promptEvent,
  toolCallFinishedEvent,
  toolCallStartedEvent,
  turnEndedEvent
} from "./support/conversationEvents";

type TurnItem = Extract<ThreadItem, { kind: "turn" }>;
type RowItem = Extract<ThreadItem, { kind: "row" }>;
type WorkGroup = Extract<ThreadItem, { kind: "work_group" }>;

function itemsFrom(events: readonly ConversationEvent[]): ThreadItem[] {
  return threadItems(feedRows(events));
}

function feedRows(events: readonly ConversationEvent[]) {
  return transcriptRows(feedWithCommittedEvents(emptyConversationFeed(), events));
}

function turns(items: readonly ThreadItem[]): TurnItem[] {
  return items.filter((item): item is TurnItem => item.kind === "turn");
}

function rows(items: readonly ThreadItem[]): RowItem[] {
  return items.filter((item): item is RowItem => item.kind === "row");
}

function workGroups(items: readonly ThreadItem[]): WorkGroup[] {
  return items.filter((item): item is WorkGroup => item.kind === "work_group");
}

function onlyTurn(items: readonly ThreadItem[]): TurnItem {
  const found = turns(items);
  expect(found).toHaveLength(1);
  return found[0]!;
}

function itemKinds(items: readonly ThreadItem[]): string[] {
  return items.map((item) =>
    item.kind === "turn" ? "turn" : item.kind === "work_group" ? "work" : item.row.kind
  );
}

function rowText(item: RowItem): string {
  if (!("content" in item.row)) throw new Error(`${item.row.kind} is not a message row`);
  return messageContentText(item.row.content);
}

describe("Conversation thread items", () => {
  it("anchors a running turn at its prompt and keeps only the newest call visibly current", () => {
    const items = itemsFrom([
      promptEvent(1, "do the thing"),
      toolCallStartedEvent(2, { toolCallId: "t1", title: "Read one" }),
      toolCallFinishedEvent(3, { toolCallId: "t1" }),
      toolCallStartedEvent(4, { toolCallId: "t2", title: "Read two" }),
      toolCallFinishedEvent(5, { toolCallId: "t2" }),
      toolCallStartedEvent(6, { toolCallId: "t3", title: "Read three" })
    ]);
    const groups = workGroups(items);
    const anchor = onlyTurn(items);

    expect(itemKinds(items)).toEqual(["prompt", "turn", "work"]);
    expect(groups).toHaveLength(1);
    expect(groups[0]!.entries.map((entry) => entry.title)).toEqual([
      "Read one",
      "Read two",
      "Read three"
    ]);
    expect(VISIBLE_RUNNING_WORK_ENTRIES).toBe(1);
    expect(
      groups[0]!.entries
        .slice(-VISIBLE_RUNNING_WORK_ENTRIES)
        .map((entry) => entry.title)
    ).toEqual(["Read three"]);
    expect(hiddenWorkSentence(2)).toBe("+2 previous tool calls");
    expect(hiddenWorkSentence(1)).toBe("+1 previous tool call");
    expect(anchor).toMatchObject({
      settled: false,
      toolCallCount: 3,
      foldedMessageCount: 0
    });
    expect(rows(items).map((item) => item.row.kind)).toEqual(["prompt"]);
  });

  it("settles work in chronological position and keeps only the final answer standing", () => {
    const items = itemsFrom([
      promptEvent(1, "do the thing", "run_when_free", { createdAt: 1_000 }),
      agentMessageEvent(2, "let me look", { createdAt: 1_001 }),
      toolCallStartedEvent(3, {
        toolCallId: "t1",
        title: "Read one",
        createdAt: 1_002
      }),
      toolCallFinishedEvent(4, { toolCallId: "t1", createdAt: 1_003 }),
      agentMessageEvent(5, "still going", { createdAt: 1_004 }),
      agentMessageEvent(6, "here you go", { createdAt: 1_011 }),
      turnEndedEvent(7, { ending: "completed", createdAt: 1_012 })
    ]);
    const anchor = onlyTurn(items);
    const groups = workGroups(items);

    expect(itemKinds(items)).toEqual([
      "prompt",
      "turn",
      "agent_message",
      "work",
      "agent_message",
      "agent_message",
      "turn_ended"
    ]);
    expect(anchor).toMatchObject({
      settled: true,
      durationSeconds: 12,
      toolCallCount: 1,
      foldedMessageCount: 2
    });
    expect(workedSentence(anchor.durationSeconds)).toBe("Worked for 12s");
    expect(groups).toHaveLength(1);
    expect(groups[0]!.settled).toBe(true);
    expect(groups[0]!.turnKey).toBe(anchor.turnKey);

    const behind = rows(items).filter((item) => item.behindTheFoldOf !== null);
    expect(behind.map(rowText)).toEqual([
      "let me look",
      "still going"
    ]);
    expect(behind.every((item) => item.behindTheFoldOf === anchor.turnKey)).toBe(true);

    const standing = rows(items).filter((item) => item.behindTheFoldOf === null);
    expect(standing.map((item) => item.row.kind)).toEqual([
      "prompt",
      "agent_message",
      "turn_ended"
    ]);
    expect(rowText(standing[1]!)).toBe("here you go");
  });

  it("keeps running commentary visible and separates work runs where they occurred", () => {
    const items = itemsFrom([
      promptEvent(1, "go"),
      toolCallStartedEvent(2, { toolCallId: "t1", title: "One" }),
      toolCallStartedEvent(3, { toolCallId: "t2", title: "Two" }),
      agentMessageEvent(4, "here is what I found"),
      toolCallStartedEvent(5, { toolCallId: "t3", title: "Three" }),
      agentMessageEvent(6, "and the answer")
    ]);
    const anchor = onlyTurn(items);
    const groups = workGroups(items);

    expect(itemKinds(items)).toEqual([
      "prompt",
      "turn",
      "work",
      "agent_message",
      "work",
      "agent_message"
    ]);
    expect(groups.map((group) => group.entries.map((entry) => entry.title))).toEqual([
      ["One", "Two"],
      ["Three"]
    ]);
    expect(new Set(groups.map((group) => group.turnKey))).toEqual(new Set([anchor.turnKey]));
    expect(anchor).toMatchObject({
      settled: false,
      toolCallCount: 3,
      foldedMessageCount: 0
    });
    expect(rows(items).map((item) => item.behindTheFoldOf)).toEqual([null, null, null]);
  });

  it.each([
    [
      "plan update",
      planUpdatedEvent(3, [{ text: "Inspect the result", status: "in_progress" }])
    ],
    [
      "token usage",
      {
        conversation_id: "c1",
        sequence: 3,
        kind: "token_usage" as const,
        payload: { input_tokens: 41_000, output_tokens: 920 },
        created_at: 1_700_000_000
      }
    ]
  ])("keeps one work run across a hidden %s row", (_label, hiddenEvent) => {
    const items = itemsFrom([
      promptEvent(1, "go"),
      toolCallStartedEvent(2, { toolCallId: "t1", title: "One" }),
      hiddenEvent,
      toolCallStartedEvent(4, { toolCallId: "t2", title: "Two" })
    ]);

    expect(workGroups(items).map((group) => group.entries.map((entry) => entry.title)))
      .toEqual([["One", "Two"]]);
  });

  it("keeps an agent message as a visible boundary between work runs", () => {
    const items = itemsFrom([
      promptEvent(1, "go"),
      toolCallStartedEvent(2, { toolCallId: "t1", title: "One" }),
      agentMessageEvent(3, "still working"),
      toolCallStartedEvent(4, { toolCallId: "t2", title: "Two" })
    ]);

    expect(workGroups(items).map((group) => group.entries.map((entry) => entry.title)))
      .toEqual([["One"], ["Two"]]);
  });

  it("retains interrupted answers but leaves a silent tool-only turn with no answer", () => {
    const silent = itemsFrom([
      promptEvent(1, "go"),
      toolCallStartedEvent(2, { toolCallId: "t1", title: "Read one" }),
      toolCallFinishedEvent(3, { toolCallId: "t1" }),
      turnEndedEvent(4, { ending: "interrupted" })
    ]);
    const silentAnchor = onlyTurn(silent);

    expect(silentAnchor).toMatchObject({
      settled: true,
      ending: "interrupted",
      toolCallCount: 1,
      foldedMessageCount: 0
    });
    expect(rows(silent).map((item) => item.row.kind)).toEqual(["prompt", "turn_ended"]);

    const cutOff = itemsFrom([
      promptEvent(1, "go"),
      agentMessageEvent(2, "starting"),
      agentMessageEvent(3, "half way"),
      turnEndedEvent(4, { ending: "interrupted" })
    ]);
    const cutOffAnchor = onlyTurn(cutOff);
    const standing = rows(cutOff).filter((item) => item.behindTheFoldOf === null);

    expect(cutOffAnchor.foldedMessageCount).toBe(1);
    expect(rowText(standing[1]!)).toBe("half way");
  });

  it("keeps prompts and permission asks outside a settled turn fold", () => {
    const items = itemsFrom([
      promptEvent(1, "go"),
      permissionAskedEvent(2, { askId: "a1", title: "Run the command" }),
      agentMessageEvent(3, "checking"),
      agentMessageEvent(4, "done"),
      turnEndedEvent(5)
    ]);
    const visibleRows = rows(items).filter(
      (item) => item.row.kind === "prompt" || item.row.kind === "permission_ask"
    );

    expect(visibleRows.map((item) => item.row.kind)).toEqual(["prompt", "permission_ask"]);
    expect(visibleRows.map((item) => item.behindTheFoldOf)).toEqual([null, null]);
  });

  it("keeps multiple turns, their work, and their durations separate", () => {
    const items = itemsFrom([
      promptEvent(1, "first", "run_when_free", { createdAt: 100 }),
      toolCallStartedEvent(2, { toolCallId: "t1", title: "One", createdAt: 101 }),
      turnEndedEvent(3, { ending: "completed", createdAt: 105 }),
      promptEvent(4, "again", "run_when_free", { createdAt: 200 }),
      toolCallStartedEvent(5, { toolCallId: "t2", title: "Two", createdAt: 201 })
    ]);
    const anchors = turns(items);
    const groups = workGroups(items);

    expect(anchors).toHaveLength(2);
    expect(anchors.map((item) => item.settled)).toEqual([true, false]);
    expect(anchors.map((item) => item.durationSeconds)).toEqual([5, null]);
    expect(anchors.map((item) => item.isLatest)).toEqual([false, true]);
    expect(groups.map((group) => group.turnKey)).toEqual(
      anchors.map((anchor) => anchor.turnKey)
    );
  });

  it("creates the turn anchor immediately and joins steer prompts to it", () => {
    const items = itemsFrom([
      promptEvent(1, "go", "run_when_free", { createdAt: 200 }),
      promptEvent(2, "also this", "steer", { createdAt: 209 })
    ]);
    const anchor = onlyTurn(items);

    expect(itemKinds(items)).toEqual(["prompt", "turn", "prompt"]);
    expect(anchor).toMatchObject({
      settled: false,
      toolCallCount: 0,
      startedAtUnixMilliseconds: 200_000
    });
  });

  it("settles a turn that stopped without an ending without inventing a duration", () => {
    const feed = feedWithCommittedEvents(emptyConversationFeed(), [
      promptEvent(1, "go", "run_when_free", { createdAt: 100 }),
      toolCallStartedEvent(2, { toolCallId: "t1", title: "One", createdAt: 101 })
    ]);
    const items = threadItems(
      transcriptRows(feed, { turnStoppedWithoutAnEnding: true })
    );
    const anchor = onlyTurn(items);

    expect(anchor).toMatchObject({
      settled: true,
      stopped: true,
      durationSeconds: null,
      toolCallCount: 1
    });
    expect(workGroups(items)[0]!.settled).toBe(true);
    expect(workedSentence(anchor.durationSeconds)).toBe("Worked");
  });

  it("describes the concrete contents of a turn fold", () => {
    expect(foldedWorkSentence(3, 2)).toBe("3 tool calls · 2 messages");
    expect(foldedWorkSentence(3, 0)).toBe("3 tool calls");
    expect(foldedWorkSentence(0, 1)).toBe("1 message");
    expect(foldedWorkSentence(1, 1)).toBe("1 tool call · 1 message");
    expect(foldedWorkSentence(0, 0)).toBe("");
    expect(
      turnFoldLabel({ durationSeconds: 3, ending: "completed", isLatest: true })
    ).toBe("Worked for 3s");
  });
});
