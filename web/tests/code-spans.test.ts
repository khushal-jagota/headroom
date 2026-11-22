import { describe, expect, it } from "vitest";
import { codeSpans } from "../src/lib/conversation/codeSpans";

describe("code in plain words", () => {
  it("sets flags, calls, files and paths in the code face, and leaves prose alone", () => {
    expect(codeSpans("Confirm the race with -count=50")).toEqual([
      { text: "Confirm the race with ", code: false },
      { text: "-count=50", code: true }
    ]);
    expect(codeSpans("Check flush() against Add() at batcher.go:97.").filter((span) => span.code).map((span) => span.text))
      .toEqual(["flush()", "Add()", "batcher.go:97"]);
    expect(codeSpans("Run ./internal/push/... under -race").filter((span) => span.code).map((span) => span.text))
      .toEqual(["./internal/push/...", "-race"]);
  });

  it("keeps a sentence's ending punctuation out of the code face", () => {
    const spans = codeSpans("read in batcher.go:61, then stop.");
    expect(spans).toContainEqual({ text: "batcher.go:61", code: true });
    expect(spans.find((span) => span.text.startsWith(","))?.code).toBe(false);
  });

  it("takes backticks at the writer's word, and plain words stay one span", () => {
    expect(codeSpans("Use `go vet` first")).toEqual([
      { text: "Use ", code: false },
      { text: "go vet", code: true },
      { text: " first", code: false }
    ]);
    expect(codeSpans("Write the root cause up - then stop")).toEqual([
      { text: "Write the root cause up - then stop", code: false }
    ]);
  });
});
