import { describe, expect, it } from "vitest";

import { taskProgressFrom } from "../src/lib/conversation/taskProgress";
import type { TranscriptRow } from "../src/lib/conversation/transcript";

function row(kind: string, fields: Record<string, unknown> = {}): TranscriptRow {
  return { key: kind, kind, sequence: 1, createdAt: 1_000, ...fields } as TranscriptRow;
}

const plan = [
  { text: "read", status: "completed" as const },
  { text: "write", status: "in_progress" as const },
  { text: "test", status: "pending" as const }
];

describe("conversation task progress", () => {
  it("reads the current ordinal, counts, and active turn from the latest plan", () => {
    const progress = taskProgressFrom([
      row("prompt", { content: [], senderLabel: "owner", mode: "run_when_free" }),
      row("plan_updated", { entries: plan })
    ]);

    expect(progress).toMatchObject({
      entries: plan,
      currentPosition: 2,
      currentEntry: plan[1],
      completedCount: 1,
      hasUnfinishedEntry: true,
      turnRunning: true
    });
  });

  it("replaces the previous snapshot and keeps the latest plan across turns", () => {
    const latest = [{ text: "ship", status: "in_progress" as const }];
    const progress = taskProgressFrom([
      row("prompt", { content: [], senderLabel: "owner", mode: "run_when_free" }),
      row("plan_updated", { entries: plan }),
      row("turn_ended", { ending: "completed", errorSummary: null }),
      row("prompt", { content: [], senderLabel: "owner", mode: "run_when_free" }),
      row("plan_updated", { entries: latest })
    ]);

    expect(progress.entries).toEqual(latest);
    expect(progress.currentEntry).toEqual(latest[0]);
    expect(progress.turnRunning).toBe(true);
  });

  it("distinguishes no current step, completed plans, and stopped turns", () => {
    const completed = taskProgressFrom([
      row("prompt", { content: [], senderLabel: "owner", mode: "run_when_free" }),
      row("plan_updated", { entries: [{ text: "done", status: "completed" }] }),
      row("turn_stopped")
    ]);

    expect(completed.currentPosition).toBeNull();
    expect(completed.currentEntry).toBeNull();
    expect(completed.hasUnfinishedEntry).toBe(false);
    expect(completed.turnRunning).toBe(false);
    expect(taskProgressFrom([]).entries).toEqual([]);
  });
});
