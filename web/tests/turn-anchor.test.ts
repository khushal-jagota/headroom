import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteMap, SvelteSet } from "svelte/reactivity";
import TurnAnchor from "../src/components/conversation/TurnAnchor.svelte";
import ToolCallRow from "../src/components/conversation/ToolCallRow.svelte";
import type { ToolCallRow as ToolCallRowType } from "../src/lib/conversation/transcript";

describe("a turn's head", () => {
  it("never counts or opens tool calls: Focus does not show them, Full shows them in runs", () => {
    const settled = render(TurnAnchor, {
      props: { settled: true, durationSeconds: 420, toolCallCount: 11, onToggle: () => {} }
    }).body;
    expect(settled).toContain("Worked for 7m");
    expect(settled).not.toContain("tool call");
    expect(settled).not.toContain("data-conversation-turn-fold");
    const running = render(TurnAnchor, {
      props: { settled: false, startedAtUnixMilliseconds: Date.now(), toolCallCount: 3, onToggle: () => {} }
    }).body;
    expect(running).toContain("Working");
    expect(running).not.toContain("tool call");
    expect(running).not.toContain("data-conversation-turn-fold");
    expect(running).not.toContain("data-conversation-live-activity");
  });

  it("opens onto the earlier messages a settled turn folded, and says how many", () => {
    const html = render(TurnAnchor, {
      props: { settled: true, durationSeconds: 420, toolCallCount: 11, foldedMessageCount: 2, onToggle: () => {} }
    }).body;
    expect(html).toContain("data-conversation-turn-fold");
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain("Worked for 7m");
    expect(html).toContain("2 earlier messages");
    expect(html).not.toContain("tool call");
  });

  it("stays a plain line where the reading already shows everything", () => {
    const html = render(TurnAnchor, {
      props: { settled: true, durationSeconds: 420, toolCallCount: 11, foldedMessageCount: 2 }
    }).body;
    expect(html).not.toContain("<button");
    expect(html).toContain("Worked for 7m");
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
      props: { settled: false, activityUncertain: true, destinationName: "Mac 1", startedAtUnixMilliseconds: Date.now() }
    }).body;
    expect(unknown).not.toContain("data-conversation-live-mark");
    expect(unknown).toContain("Current activity on Mac 1 is unknown.");
  });

  it("keeps its live mark before the words and removes it when settled", () => {
    const running = render(TurnAnchor, {
      props: { settled: false, startedAtUnixMilliseconds: Date.now() - 75_000 }
    }).body.replace(/<!--[^]*?-->/g, "");
    expect(running).toContain('data-conversation-system-mark="spinner"');
    expect(running.indexOf('data-conversation-system-mark="spinner"')).toBeLessThan(running.indexOf("cv-system-line-words"));
    const settled = render(TurnAnchor, { props: { settled: true, durationSeconds: 420 } }).body;
    expect(settled).not.toContain("data-conversation-system-mark");
    expect(settled).toContain("Worked for 7m");
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
    // The chevron stands right after the label; the subject and the outcome follow it.
    expect(failed.indexOf("c2-tool-verb")).toBeLessThan(failed.indexOf("c2-tool-chevron"));
    expect(failed.indexOf("c2-tool-chevron")).toBeLessThan(failed.indexOf("Failed"));
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

  it("says nothing for a call that passed: the outcome column stays, with no mark", () => {
    const html = draw({ ...base, title: "git status", status: "completed", detail: "clean" });
    expect(html).toContain("c2-tool-outcome");
    expect(html).not.toContain("Failed");
    expect(html).not.toContain("c2-tool-mark--ok");
  });

  it("says a call still running in the present tense, beside the live mark", () => {
    const html = draw({ ...base, title: "npm test", status: "running", detail: null });
    expect(html).toContain("Running");
    expect(html).toContain('aria-label="Running"');
  });

  it("keeps its static icon while it runs, marked running, with no spinner of its own", () => {
    const html = draw({ ...base, title: "Run go test", status: "running", detail: "go test -race ./..." });
    expect(html).toContain('class="c2-tool-icon');
    expect(html).not.toContain("c2-tool-spin");
    expect(html).toContain('data-conversation-tool-status="running"');
  });

  it("opens a failed call onto its output in a block whose header says it failed", () => {
    const row = { ...base, title: "npm run check", detail: "src/a.ts:1 error TS2304\nExit code 1" };
    const html = render(ToolCallRow, {
      props: { row, conversationId: "c", openRows: new SvelteSet<string>(["t1"]), wholeDetails: new SvelteMap<string, string>() }
    }).body;
    expect(html).toContain("Exit code 1");
    expect(html).toContain("data-conversation-tool-copy");
    expect(html).toContain('class="c2-tool-problem');
    const passed = render(ToolCallRow, {
      props: { row: { ...row, status: "completed" as const, detail: "one\ntwo" }, conversationId: "c", openRows: new SvelteSet<string>(["t1"]), wholeDetails: new SvelteMap<string, string>() }
    }).body;
    expect(passed).toContain("Output · 2 lines");
  });
});
