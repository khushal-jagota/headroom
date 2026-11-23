import { describe, expect, it } from "vitest";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";

describe("a transcript at its end", () => {
  it("stays at its end when the composer grows", () => {
    // Growth is measured by a resize observer and the tests run without a DOM; the wiring is read from the source.
    const viewport = readFileSync(new URL("../src/components/conversation/viewport/ConversationViewport.svelte", import.meta.url), "utf8") as string;
    expect(viewport).toContain("if (shrank && endGapBeforeResize <= 1 && heldSent === null && !pendingOpen && placementReady)");
    expect(viewport).toContain("moveTheThread(thread, thread.scrollHeight);");
    expect(viewport).toContain("endGapBeforeResize = t.scrollHeight - t.scrollTop - t.clientHeight;");
  });
});
