import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteMap, SvelteSet } from "svelte/reactivity";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import ToolCallRow from "../src/components/conversation/ToolCallRow.svelte";
import type { ToolCallRow as ToolCallRowType } from "../src/lib/conversation/transcript";

describe("a turn's head", () => {
  it("opens onto the work a reading keeps folded, and says how much there is", () => {
    const html = render(TurnAnchor, {
      props: { settled: true, durationSeconds: 420, toolCallCount: 11, onToggle: () => {} }
    }).body;
    expect(html).toContain("data-conversation-turn-fold");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("Worked for 7m");
    expect(html).toContain("11 tool calls");
  });

  it("stays a plain line where the reading already shows the work", () => {
    const html = render(TurnAnchor, {
      props: { settled: true, durationSeconds: 420, toolCallCount: 11 }
    }).body;
    expect(html).not.toContain("<button");
    expect(html).toContain("Worked for 7m");
  });

  it("lets a running turn's tool calls be opened beside its clock", () => {
    const html = render(TurnAnchor, {
      props: { settled: false, startedAtUnixMilliseconds: Date.now(), toolCallCount: 3, onToggle: () => {} }
    }).body;
    expect(html).toContain("Working");
    expect(html).toContain("3 tool calls");
    expect(html).toContain("data-conversation-turn-fold");
  });

  it("says the turn is waiting on the person rather than working", () => {
    for (const [waiting, sentence] of [
      ["permission", "Waiting for your approval"],
      ["question", "Waiting for your answer"]
    ] as const) {
      const html = render(TurnAnchor, {
        props: { settled: false, startedAtUnixMilliseconds: Date.now() - 75_000, waiting }
      }).body;
      expect(html).toContain(sentence);
      expect(html).not.toContain("Working for");
    }
  });
});

describe("a tool call row", () => {
  const base: ToolCallRowType = {
    key: "e1", kind: "tool_call", sequence: 1, createdAt: 1, toolCallId: "t1",
    title: "bash -lc 'rg -n \"rate|limit\" server/share'", toolKind: "execute",
    detail: "", startedDetail: "/w", cappedDetailSequence: null, status: "failed", progress: null
  };
  const draw = (row: ToolCallRowType) => render(ToolCallRow, {
    props: { row, conversationId: "c", openRows: new SvelteSet<string>(), wholeDetails: new SvelteMap<string, string>() }
  }).body;

  it("draws a search that found nothing as no matches, not as a failure", () => {
    const html = draw(base);
    expect(html).toContain("No matches");
    expect(html).not.toContain("Failed");
    expect(html).toContain('data-conversation-tool-outcome="no_matches"');
  });

  it("says a real failure in words and offers its output", () => {
    const html = draw({ ...base, title: "npm run check", detail: "svelte-check found 1 error" });
    expect(html).toContain("Failed");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("c2-tool-chevron");
  });
});
