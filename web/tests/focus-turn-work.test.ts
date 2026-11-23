import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteSet } from "svelte/reactivity";
import ProjectedTranscript from "../src/components/conversation/view/ProjectedTranscript.svelte";
import type {
  ConversationViewItem,
  ConversationViewPage,
  ConversationViewStatus,
  ViewHistoryScope
} from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";
import { executorAnswerSentence } from "../src/lib/conversation/executorEvidence";
import { presentTense, toolCallSentence } from "../src/lib/conversation/whatHappened";

const turn = (settled: boolean): ConversationViewItem => ({
  kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
  settled, stopped: false, started_at_unix_milliseconds: 100_000, ending: settled ? "completed" : null,
  duration_seconds: settled ? 420 : null, tool_call_count: 2, folded_message_count: 0,
  latest_message_key: null, is_latest: true
});
const group: ConversationViewItem = {
  kind: "work_group", key: "work:e2", order: { sequence: 2, slot: 0 }, changed_through_sequence: 4,
  turn_key: "turn:e1", entry_count: 2, latest_entry_key: "e3", settled: true
};
const tool = (sequence: number, command: string, status: "completed" | "failed" | "running" = "completed"): ConversationViewItem => ({
  kind: "row", key: `e${sequence}`, row_kind: "tool_call", order: { sequence, slot: 1 },
  source_sequence: sequence, sender_message_id: null, changed_through_sequence: sequence,
  created_at: sequence, turn_key: "turn:e1", work_group_key: "work:e2", fold_rule: "work",
  body: { kind: "inline", value: {
    kind: "tool_call", tool_call_id: `t${sequence}`, title: command, tool_kind: "execute",
    detail: status === "failed" ? "1 error" : "ok", started_detail: "/w", capped_detail_sequence: null, status
  } }
} as ConversationViewItem);

function page(scope: ViewHistoryScope, items: ConversationViewItem[]): ConversationViewPage {
  return {
    conversation_id: "c", protocol_version: 1, projection_version: 1, lens: "focus",
    scope, items, older: null, newer: null, body_bytes: 0
  };
}

function draw(options: { expanded: boolean; settled?: boolean; status?: Partial<ConversationViewStatus>; lens?: "focus" | "full"; failed?: boolean; running?: boolean }) {
  const expanded = new Set(options.expanded ? ["turn:turn:e1"] : []);
  const pages = [
    page({ kind: "timeline", key: null }, [turn(options.settled ?? true)]),
    page({ kind: "turn", key: "turn:e1" }, [group]),
    page({ kind: "work_group", key: "work:e2" }, [tool(3, "npm run check", options.failed ? "failed" : "completed"), tool(4, "git status", options.running ? "running" : "completed")])
  ];
  const reader = {
    id: "c", lens: options.lens ?? "focus", pages, coverage: 5, entryPhase: "ready",
    status: { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, ...options.status },
    restorationAnchor: null, restorationAttempt: 0, discardedBodies: 0, openTools: new SvelteSet<string>(),
    isExpanded: (scope: ViewHistoryScope) => expanded.has(`${scope.kind}:${scope.key}`),
    expand: async () => {}, load: async () => {}, textBodyReady: () => {}, failTextRestoration: () => {},
    body: async () => ""
  } as unknown as ConversationViewReader;
  return render(ProjectedTranscript, {
    props: { reader, models: [], ownSenderLabel: null, livenessPulse: 0 }
  }).body;
}

describe("Focus is focus: no tool activity at all", () => {
  it("draws no tool call, no count and no fold onto tool calls", () => {
    for (const expanded of [false, true]) {
      const html = draw({ expanded });
      expect(html).not.toContain("tool call");
      expect(html).not.toContain('data-conversation-row="tool_call"');
      expect(html).not.toContain("data-conversation-work-summary");
      expect(html).not.toContain("data-conversation-turn-fold");
      expect(html).not.toContain("npm run check");
    }
  });

  it("draws no running command either", () => {
    const html = draw({ expanded: false, settled: false, running: true });
    // The running turn's Working line is the live edge at the end of the thread, drawn
    // by the viewport (LiveEdge), never at the turn's top.
    expect(html).not.toContain("Working");
    expect(html).not.toContain("git status");
    expect(html).not.toContain("data-conversation-live-activity");
  });

  it("draws no head for a running turn held by an ask: the ask says it waits, not the thread", () => {
    const html = draw({
      expanded: false, settled: false,
      status: { pending_permission: { kind: "permission", request_id: "a1" } as ConversationViewStatus["pending_permission"] }
    });
    expect(html).not.toContain("Waiting for your approval");
    expect(html).not.toContain("Working");
    expect(html).not.toContain("tool call");
    // The transcript ends at the last message; the waiting ask is the composer's.
    expect(html).not.toContain("data-conversation-paused");
    expect(html).not.toContain("data-conversation-alive-word");
  });
});

describe("a turn's head in Full", () => {
  it("shows nothing at the top while the turn runs; the live line is the live edge at the end", () => {
    const html = draw({ expanded: false, lens: "full", settled: false, running: true });
    expect(html).not.toContain("data-conversation-alive-word");
    expect(html).toContain("data-conversation-work-group");
    expect(html).not.toContain("data-conversation-tool-elapsed");
  });

  it("heads the settled turn again with its meta line, over its work", () => {
    const html = draw({ expanded: false, lens: "full" });
    expect(html).toContain("data-conversation-turn-settled-head");
    expect(html).not.toContain("data-conversation-live-tail");
    expect(html.indexOf("data-conversation-turn-label")).toBeLessThan(html.indexOf("data-conversation-work-group"));
  });
});

describe("Full keeps each run of tool calls behind one summary", () => {
  it("starts closed: the summary says what the run did and no row is drawn", () => {
    const html = draw({ expanded: false, lens: "full" });
    expect(html).toContain("data-conversation-work-summary");
    expect(html).toContain("Ran 2 commands");
    expect(html).toContain('aria-expanded="false"');
    expect(html).not.toContain('data-conversation-row="tool_call"');
  });

  it("says a failure in words, so a closed run never hides it", () => {
    const html = draw({ expanded: false, lens: "full", failed: true });
    expect(html).toContain("1 failed");
    expect(html).toContain('data-conversation-work-failed="true"');
  });

  it("says the step that is running now, in the present tense", () => {
    const html = draw({ expanded: false, lens: "full", settled: false, running: true });
    expect(html).toContain("Running");
    expect(html).toContain("git status");
    // No step counts on the running line.
    expect(html).not.toContain("2 steps");
  });
});

describe("an answer that reached the machine", () => {
  it("leaves no line of its own; only pending or unconfirmed answers say anything", () => {
    expect(executorAnswerSentence("applied")).toBeNull();
    expect(executorAnswerSentence("saved")).not.toBeNull();
    expect(executorAnswerSentence("unconfirmed")).not.toBeNull();
  });
});

describe("the rest line's tool sentence", () => {
  it("reads as the row reads", () => {
    expect(toolCallSentence({
      key: "e1", kind: "tool_call", sequence: 1, createdAt: 1, toolCallId: "t", title: "bash -lc 'npm test'",
      toolKind: "execute", detail: "ok", startedDetail: "/w", cappedDetailSequence: null, status: "completed", progress: null
    })).toBe("Ran npm test");
  });
  it("says a call that is still running as it happens, and an ended one in the past", () => {
    const row = {
      key: "e1", kind: "tool_call" as const, sequence: 1, createdAt: 1, toolCallId: "t", title: "bash -lc 'npm test'",
      toolKind: "execute", detail: null, startedDetail: "/w", cappedDetailSequence: null, progress: null
    };
    expect(toolCallSentence({ ...row, status: "running" })).toBe("Running npm test");
    expect(toolCallSentence({ ...row, status: "failed" })).toBe("Ran npm test");
    expect(presentTense("Searched for TODO")).toBe("Searching for TODO");
    expect(presentTense("Read store.ts")).toBe("Reading store.ts");
    expect(presentTense("Updated to-dos 2 of 5 done")).toBe("Updating to-dos 2 of 5 done");
    // A title that does not lead with a known verb is left as the backend gave it.
    expect(presentTense("Bash")).toBe("Bash");
    expect(presentTense("Reader mode")).toBe("Reader mode");
  });
});
