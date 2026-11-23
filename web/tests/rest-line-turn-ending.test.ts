import { describe, expect, it } from "vitest";
import { whatThisRowSays } from "../src/lib/conversation/whatHappened";
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
  it("says nothing for a completed or interrupted turn, and the plain reason for a failed one", () => {
    expect(whatThisRowSays(turnEnded("completed", null), "you")).toBeNull();
    expect(whatThisRowSays(turnEnded("interrupted", null), "you")).toBeNull();
    expect(whatThisRowSays(turnEnded("failed", "codex app-server exited with code 1"), "you"))
      .toEqual({ who: null, text: "The turn stopped because the agent's process exited." });
    expect(whatThisRowSays(turnEnded("failed", "stream disconnected before completion: rate limit reached for gpt-6.1-sol"), "you"))
      .toEqual({ who: null, text: "The turn stopped because the model's rate limit was reached." });
    expect(whatThisRowSays(turnEnded("failed", null), "you")).toEqual({ who: null, text: "The turn stopped because of an error." });
  });
});
