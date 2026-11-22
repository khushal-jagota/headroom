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
import { presentTense, toolCallSentence } from "../src/lib/conversation/restLine";

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
const tool = (sequence: number, command: string): ConversationViewItem => ({
  kind: "row", key: `e${sequence}`, row_kind: "tool_call", order: { sequence, slot: 1 },
  source_sequence: sequence, sender_message_id: null, changed_through_sequence: sequence,
  created_at: sequence, turn_key: "turn:e1", work_group_key: "work:e2", fold_rule: "work",
  body: { kind: "inline", value: {
    kind: "tool_call", tool_call_id: `t${sequence}`, title: command, tool_kind: "execute",
    detail: "ok", started_detail: "/w", capped_detail_sequence: null, status: "completed"
  } }
} as ConversationViewItem);

function page(scope: ViewHistoryScope, items: ConversationViewItem[]): ConversationViewPage {
  return {
    conversation_id: "c", protocol_version: 1, projection_version: 1, lens: "focus",
    scope, items, older: null, newer: null, body_bytes: 0
  };
}

function draw(options: { expanded: boolean; settled?: boolean; status?: Partial<ConversationViewStatus> }) {
  const expanded = new Set(options.expanded ? ["turn:turn:e1"] : []);
  const pages = [
    page({ kind: "timeline", key: null }, [turn(options.settled ?? true)]),
    page({ kind: "turn", key: "turn:e1" }, [group]),
    page({ kind: "work_group", key: "work:e2" }, [tool(3, "npm run check"), tool(4, "git status")])
  ];
  const reader = {
    id: "c", lens: "focus", pages, coverage: 5, entryPhase: "ready",
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

describe("Focus keeps a turn's tool activity one press away", () => {
  it("shows only the head, with its count, while the turn is closed", () => {
    const html = draw({ expanded: false });
    expect(html).toContain("2 tool calls");
    expect(html).not.toContain('data-conversation-row="tool_call"');
  });

  it("shows every tool call of an opened turn, with no second fold per run", () => {
    const html = draw({ expanded: true });
    expect(html.match(/data-conversation-row="tool_call"/g)?.length).toBe(2);
    expect(html).toContain("npm run check");
    expect(html).not.toContain("earlier tool calls");
  });

  it("says a running turn held by an ask is waiting, not working", () => {
    const html = draw({
      expanded: false, settled: false,
      status: { pending_permission: { kind: "permission", request_id: "a1" } as ConversationViewStatus["pending_permission"] }
    });
    expect(html).toContain("Waiting for your approval");
    expect(html).not.toContain("Working for");
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
