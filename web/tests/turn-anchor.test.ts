import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteMap, SvelteSet } from "svelte/reactivity";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import ToolCallRow from "../src/components/conversation/ToolCallRow.svelte";
import type { ToolCallRow as ToolCallRowType } from "../src/lib/conversation/transcript";

describe("a turn's head", () => {
  it("sets only a literal subject of its live line as code", () => {
    const draw = (summaryIsLiteral: boolean) => render(TurnAnchor, {
      props: { settled: false, startedAtUnixMilliseconds: Date.now(), activity: { sentence: "Running go test", title: "Running", summary: "go test", summaryIsLiteral } }
    }).body;
    expect(draw(true)).toContain("c2-live-subject--literal");
    expect(draw(true)).toContain("go test");
    expect(draw(false)).not.toContain("c2-live-subject--literal");
  });

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

  it("marks a running turn as live, and a finished one not", () => {
    const running = render(TurnAnchor, {
      props: { settled: false, startedAtUnixMilliseconds: Date.now(), toolCallCount: 3, onToggle: () => {} }
    }).body;
    expect(running).toContain("data-conversation-live-mark");
    const settled = render(TurnAnchor, {
      props: { settled: true, durationSeconds: 420, toolCallCount: 3, onToggle: () => {} }
    }).body;
    expect(settled).not.toContain("data-conversation-live-mark");
    const unknown = render(TurnAnchor, {
      props: { settled: false, activityUncertain: true, startedAtUnixMilliseconds: Date.now() }
    }).body;
    expect(unknown).not.toContain("data-conversation-live-mark");
  });

  it("says what the running turn is doing now, only while it is working", () => {
    const activity = { sentence: "Running go test -race -count=50 ./internal/push", title: "Running", summary: "go test -race -count=50 ./internal/push", summaryIsLiteral: true };
    const running = render(TurnAnchor, {
      props: { settled: false, startedAtUnixMilliseconds: Date.now(), activity }
    }).body;
    expect(running).toContain("data-conversation-live-activity");
    expect(running).toContain(activity.summary);
    const waiting = render(TurnAnchor, {
      props: { settled: false, startedAtUnixMilliseconds: Date.now(), activity, waiting: "question" }
    }).body;
    expect(waiting).not.toContain("data-conversation-live-activity");
    const settled = render(TurnAnchor, { props: { settled: true, durationSeconds: 60, activity } }).body;
    expect(settled).not.toContain(activity.summary);
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

  it("keeps its outcome in the column every row shares, and opens like every other row", () => {
    const silent = draw({ ...base, title: "git rm web/a.ts", status: "completed", detail: "" });
    expect(silent).toContain("c2-tool-outcome");
    expect(silent).toMatch(/class="c2-tool-chevron[ "]/);
    expect(silent).toContain('aria-expanded="false"');
    const failed = draw({ ...base, title: "npm run check", detail: "svelte-check found 1 error" });
    expect(failed.indexOf("Failed")).toBeLessThan(failed.indexOf("c2-tool-chevron"));
  });

  it("opens a call that printed nothing onto saying so", () => {
    const row = { ...base, title: "git rm web/a.ts", status: "completed" as const, detail: "" };
    const html = render(ToolCallRow, {
      props: { row, conversationId: "c", openRows: new SvelteSet<string>(["t1"]), wholeDetails: new SvelteMap<string, string>() }
    }).body;
    expect(html).toContain('aria-expanded="true"');
    expect(html).toContain("data-conversation-tool-no-output");
    expect(html).toContain("No output.");
    expect(html).not.toContain("data-conversation-tool-output");
  });

  it("sets a command as code and words about a call as words", () => {
    const command = draw({ ...base, title: "git rm web/a.ts", status: "completed", detail: "" });
    expect(command).toContain("c2-tool-subject--literal");
    const todos = draw({ ...base, title: "TodoWrite", toolKind: "think", status: "completed", detail: "ok",
      startedDetail: JSON.stringify({ todos: [{ content: "A", status: "pending" }] }) });
    expect(todos).toContain("0 of 1 done");
    expect(todos).not.toContain("c2-tool-subject--literal");
  });
});
