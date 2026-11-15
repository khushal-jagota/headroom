import { describe, expect, it } from "vitest";
import { whatThisRowSays } from "../src/lib/conversation/restLine";
import type { TranscriptRow } from "../src/lib/conversation/transcript";

function turnEnded(ending: "completed" | "failed" | "interrupted", errorSummary: string | null): TranscriptRow {
  return {
    key: "end",
    kind: "turn_ended",
    sequence: 1,
    createdAt: 1,
    ending,
    errorSummary,
    automaticCompactionResult: null
  } as TranscriptRow;
}

describe("the rest line after a turn ends", () => {
  it("says nothing for a completed or interrupted turn, and the error for a failed one", () => {
    expect(whatThisRowSays(turnEnded("completed", null), "you")).toBeNull();
    expect(whatThisRowSays(turnEnded("interrupted", null), "you")).toBeNull();
    expect(whatThisRowSays(turnEnded("failed", "backend exited"), "you")).toEqual({ who: null, text: "backend exited" });
  });
});
