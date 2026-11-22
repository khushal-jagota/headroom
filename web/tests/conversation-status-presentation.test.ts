import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import ToolRunSummary from "../src/components/conversation/ToolRunSummary.svelte";
import type { ToolRunSummary as Summary } from "../src/lib/conversation/toolCallPresentation";
import { SvelteSet } from "svelte/reactivity";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";
import ProjectedTranscript from "../src/components/conversation/view/ProjectedTranscript.svelte";

import { conversationState } from "../src/lib/conversation/dockPresentation";
import type { ToolCallPresentation } from "../src/lib/conversation/toolCallPresentation";
import type { ConversationViewItem, ConversationViewPage, ViewHistoryScope } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

import TranscriptStatusRow from "../src/components/conversation/TranscriptStatusRow.svelte";
import LiveEdge from "../src/components/conversation/view/LiveEdge.svelte";
import { liveTurnOf, turnHeadAtTop } from "../src/lib/conversation/view/liveEdge";
import type { ConversationViewStatus, ConversationViewTurn } from "../src/lib/conversation/view/contracts";

import ToolCallRow from "../src/components/conversation/ToolCallRow.svelte";
import { turnEndingStatus } from "../src/lib/conversation/transcript";

import type { ToolCallRow as ToolCallRowData } from "../src/lib/conversation/transcript";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");
const anchor = source("../src/components/conversation/TurnAnchor.svelte");
const run = source("../src/components/conversation/ToolRunSummary.svelte");
const status = source("../src/components/conversation/TranscriptStatusRow.svelte");

describe("a tool run's chevron stands right after its label", () => {
  const closed: Summary = { sentence: "Ran 2 commands", failedCount: 0, running: null, iconPaths: [] };
  it("closed: the sentence, then the chevron, with no empty status slot between them", () => {
    const html = render(ToolRunSummary, { props: { summary: closed, callCount: 2, onToggle: () => { } } }).body;
    expect(html.indexOf("Ran 2 commands")).toBeLessThan(html.indexOf("c2-run-chevron"));
    expect(html).not.toContain("data-conversation-work-status");
  });
  it("a failure is part of the label, before the chevron", () => {
    const html = render(ToolRunSummary, { props: { summary: { ...closed, failedCount: 1 }, callCount: 2, onToggle: () => {} } }).body;
    expect(html.indexOf("1 failed")).toBeLessThan(html.indexOf("c2-run-chevron"));
  });
});

describe("surfaces", () => {
  const css = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
  it("the newest error is a Surface (no tint); failure is said by the #f08a7e dot alone", () => {
    const row = css("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toMatch(/\.c2-status--callout \{[^}]*border: 1px solid var\(--cv-surface-edge, #2a2a2a\);\s*border-radius: var\(--cv-surface-radius, 20px\);\s*background: var\(--cv-surface, #1c1c1c\);/);
    expect(row).not.toContain("rgb(229 132 138");
    expect(row).toContain(".c2-status--error .c2-status-dot { background: #f08a7e; }");
  });
});

describe("one primary button style", () => {
  it("an error card's Resend is the primary, Choose another model the secondary, Details quiet", () => {
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain(":global(.c2-status-main) { background: #f2f2f2; color: #141414; }");
    expect(row).toContain(":global(.c2-status-second) { background: #2a2a2a; color: var(--cv-ink, #ececec); }");
    expect(row).toMatch(/:global\(\.c2-status-main\), :global\(\.c2-status-second\) \{[^}]*height: 32px;[^}]*padding: 0 14px;\s*border-radius: 999px;/);
    expect(row).toMatch(/\.c2-status-toggle \{[^}]*background: transparent;\s*color: var\(--cv-quiet-ink, #bdbdbd\);/);
  });
});

describe("the Working line is always the last thing in the transcript", () => {
  const turn = (settled: boolean): ConversationViewItem => ({
    kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
    settled, stopped: false, started_at_unix_milliseconds: 100_000, ending: settled ? "completed" : null,
    duration_seconds: settled ? 420 : null, tool_call_count: 2, folded_message_count: 0,
    latest_message_key: null, is_latest: true
  });
  const group: ConversationViewItem = {
    kind: "work_group", key: "work:e2", order: { sequence: 2, slot: 0 }, changed_through_sequence: 4,
    turn_key: "turn:e1", entry_count: 2, latest_entry_key: "e3", settled: false
  };
  const tool = (sequence: number, command: string, status: "completed" | "running"): ConversationViewItem => ({
    kind: "row", key: `e${sequence}`, row_kind: "tool_call", order: { sequence, slot: 1 },
    source_sequence: sequence, sender_message_id: null, changed_through_sequence: sequence,
    created_at: sequence, turn_key: "turn:e1", work_group_key: "work:e2", fold_rule: "work",
    body: {
      kind: "inline", value: {
        kind: "tool_call", tool_call_id: `t${sequence}`, title: command, tool_kind: "execute",
        detail: "ok", started_detail: "/w", capped_detail_sequence: null, status
      }
    }
  } as ConversationViewItem);
  const page = (scope: ViewHistoryScope, items: ConversationViewItem[]): ConversationViewPage => ({
    conversation_id: "c", protocol_version: 1, projection_version: 1, lens: "full", scope, items, older: null, newer: null, body_bytes: 0
  });
  const draw = (settled: boolean): string => {
    const pages = [
      page({ kind: "timeline", key: null }, [turn(settled), group]),
      page({ kind: "work_group", key: "work:e2" }, [tool(3, "npm run check", "completed"), tool(4, "go test -race ./...", settled ? "completed" : "running")])
    ];
    const reader = {
      id: "c", lens: "full", pages, coverage: 5, entryPhase: "ready",
      status: { turn_key: "turn:e1", pending_permission: null, pending_user_input: null },
      restorationAnchor: null, restorationAttempt: 0, discardedBodies: 0, openTools: new SvelteSet<string>(),
      isExpanded: () => false, expand: async () => { }, load: async () => { }, textBodyReady: () => { }, failTextRestoration: () => { },
      body: async () => ""
    } as unknown as ConversationViewReader;
    return render(ProjectedTranscript, { props: { reader, models: [], ownSenderLabel: null, livenessPulse: 0 } }).body;
  };
  it("while the turn runs, its top shows nothing; the live line is the live edge at the end", () => {
    const html = draw(false);
    expect(html).not.toContain("data-conversation-alive-word");
    expect(html).toContain("data-conversation-work-group");
    expect(html).not.toContain("data-conversation-tool-elapsed");
    // Settled, "Worked for" is back at the top of the turn.
    expect(draw(true)).toContain("data-conversation-turn-settled-head");
  });
  it("once settled, the turn's meta line heads the turn again, over its work", () => {
    const html = draw(true);
    expect(html).not.toContain("data-conversation-live-tail");
    expect(html.indexOf("data-conversation-turn-label")).toBeLessThan(html.indexOf("data-conversation-work-group"));
  });
});

describe("turn timing at the top, Paused under a waiting ask", () => {
  const turn: ConversationViewItem = {
    kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
    settled: false, stopped: false, started_at_unix_milliseconds: 100_000, ending: null,
    duration_seconds: null, tool_call_count: 0, folded_message_count: 0, latest_message_key: null, is_latest: true
  } as ConversationViewItem;
  const draw = (status: Record<string, unknown>): string => {
    const pages: ConversationViewPage[] = [{
      conversation_id: "c", protocol_version: 1, projection_version: 1, lens: "focus",
      scope: { kind: "timeline", key: null } as ViewHistoryScope, items: [turn], older: null, newer: null, body_bytes: 0
    } as ConversationViewPage];
    const reader = {
      id: "c", lens: "focus", pages, coverage: 5, entryPhase: "ready",
      status: { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, ...status },
      restorationAnchor: null, restorationAttempt: 0, discardedBodies: 0, openTools: new SvelteSet<string>(),
      isExpanded: () => false, expand: async () => { }, load: async () => { }, textBodyReady: () => { }, failTextRestoration: () => { },
      body: async () => ""
    } as unknown as ConversationViewReader;
    return render(ProjectedTranscript, { props: { reader, models: [], ownSenderLabel: null, livenessPulse: 0 } }).body;
  };
  it("under a waiting ask the transcript ends at the last message; the Paused line is the composer's", () => {
    const html = draw({ pending_permission: { kind: "permission", request_id: "a1" } });
    expect(html).not.toContain("data-conversation-paused");
    expect(html).not.toContain("data-conversation-alive-word");

    const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
    expect(composer).not.toContain("chat-paused");
  });
  it("the dock's Working · time is spaced as the transcript's (three parts, 6px apart)", () => {
    const facts = { readOnly: false, activityUncertain: false, waitingForYou: false, waitingFor: null, running: true, backgroundWorking: false, settingUp: null, elapsed: "2m 14s" };
    const html = render(ConversationRestBar, { props: { onOpen: () => { }, state: conversationState(facts as never) } }).body;
    expect(html).toMatch(/c2-rest-label[^>]*>Working<\/span><span class="c2-rest-sep[^"]*" aria-hidden="true">·<\/span><span class="c2-rest-clock/);
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain(".c2-rest-working { display:inline-flex; align-items:center; gap:6px; }");
  });
  it("the Working label is a status label, 13/20 500 #ececec", () => {
    expect(source("../src/components/conversation/TurnAnchor.svelte")).toContain(".c2-meta-working { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }");
  });
});

describe("errors", () => {
  const status = { tone: "error" as const, title: "The turn stopped because the model's rate limit was reached.", detail: "429", next: "Send a message once the limit resets.", action: "choose-model" as const };
  it("a callout counts as the newest until measured; an older one is an inline status line", () => {
    const html = render(TranscriptStatusRow, { props: { status } }).body;
    expect(html).toContain('data-conversation-callout-newest="true"');
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain("registerCallout(root");
    expect(row).toContain('data-conversation-error-inline');
  });
  it("Details is a quiet Button, 13/20 500 #bdbdbd", () => {
    expect(source("../src/components/conversation/TranscriptStatusRow.svelte")).toMatch(/\.c2-status-toggle \{[^}]*height: 32px;[^}]*color: var\(--cv-quiet-ink, #bdbdbd\);\s*font-size: 13px;/);
  });
});

describe("the live edge", () => {
  const turn = (settled: boolean): ConversationViewTurn => ({
    kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
    settled, stopped: false, started_at_unix_milliseconds: 100_000, ending: settled ? "completed" : null,
    duration_seconds: settled ? 140 : null, tool_call_count: 0, folded_message_count: 0, latest_message_key: null, is_latest: true
  });
  const statusWith = (extra: Partial<ConversationViewStatus> = {}): ConversationViewStatus => ({
    turn_key: "turn:e1", pending_permission: null, pending_user_input: null, stopped_without_ending: false, ...extra
  } as ConversationViewStatus);
  const reader = (lens: "focus" | "full", items: ConversationViewItem[], status: ConversationViewStatus): ConversationViewReader => ({
    id: "c", lens, pages: [{ scope: { kind: "timeline", key: null }, items }], status, openTools: new SvelteSet<string>()
  } as unknown as ConversationViewReader);
  it("the running turn's head is the live edge, not its top", () => {
    expect(liveTurnOf([{ items: [turn(false)] }], statusWith())?.key).toBe("turn:e1");
    expect(liveTurnOf([{ items: [turn(true)] }], statusWith())).toBeNull();
    expect(liveTurnOf([{ items: [turn(false)] }], statusWith({ stopped_without_ending: true }))).toBeNull();
    expect(turnHeadAtTop(turn(false), statusWith())).toBe(false);
    expect(turnHeadAtTop(turn(true), statusWith())).toBe(true);

    expect(viewport.indexOf("<LiveEdge {reader}")).toBeLessThan(viewport.indexOf("{@render afterTranscript()}"));
  });
  it("2 : the live edge says Working with the clock and no narration sentence", () => {
    const focus = render(LiveEdge, { props: { reader: reader("focus", [turn(false)], statusWith()) } }).body;
    expect(focus).toContain("data-conversation-live-edge");
    expect(focus).toContain("data-conversation-alive-word");
    expect(focus).not.toContain("data-conversation-live-narration");
    const full = render(LiveEdge, { props: { reader: reader("full", [turn(false)], statusWith()) } }).body;
    expect(full).toContain("data-conversation-alive-word");
    expect(full).not.toContain("data-conversation-live-narration");
    const waiting = render(LiveEdge, { props: { reader: reader("focus", [turn(false)], statusWith({ pending_permission: { kind: "permission", request_id: "a", row_key: "e2", source_sequence: 2 } })) } }).body;
    expect(waiting).not.toContain("data-conversation-live-edge");
    expect(source("../src/components/conversation/view/LiveEdge.svelte")).not.toContain("c2-live-narration");
  });
});

describe("transcript", () => {
  it("the one spinner (the Working line) is a #d9d9d9 arc on a #4a4a4a track", () => {
    for (const file of ["TurnAnchor"])
      expect(source(`../src/components/conversation/${file}.svelte`)).toMatch(/border: [\d.]+px solid #4a4a4a;\s*border-top-color: #d9d9d9;/);
  });
  it("an older error is one inline row: dot, This turn failed: <short cause>, Details", () => {
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain('{#if shownWeight === "callout" && status.tone === "error" && !newest}');
    expect(row).toContain('<span class="c2-status-inline-words"><span class="c2-status-inline-head">{calloutHeading}</span><span class="c2-status-sep" aria-hidden="true">·</span><span>{shortCause}</span></span>');
    // The newest (and, unmeasured, every) error is still the card with its actions.
    const html = render(TranscriptStatusRow, { props: { status: { tone: "error", title: "The turn stopped because the model's rate limit was reached. It resets in 41m.", detail: "429", next: null, action: "choose-model" } } }).body;
    expect(html).toContain("c2-status--callout");
    expect(html).not.toContain("data-conversation-error-inline");
  });
});

describe("shared presentation", () => {
  it("4 Status line: 13/20, a 16px icon slot, label 500 #ececec, detail #b0b0b0", () => {
    const anchor = source("../src/components/conversation/TurnAnchor.svelte");
    expect(anchor).toMatch(/\.c2-meta \{[^}]*gap: 8px;[^}]*color: var\(--cv-detail-ink, #b0b0b0\);\s*font-family: var\(--cv-font\);\s*font-size: 13px;\s*line-height: 20px;/);
    expect(anchor).toContain(".c2-meta-slot { flex: none; display: inline-grid; place-items: center; width: 16px; height: 16px; }");
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain(".c2-status-title { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }");
    expect(row).toContain(".c2-status--error .c2-status-dot { background: #f08a7e; }");
  });
});

describe("live edge", () => {
  it("the Working line comes before the queued messages, 24px under the turn's content", () => {
    expect(viewport.indexOf("<LiveEdge {reader}")).toBeLessThan(viewport.indexOf("{@render afterTranscript()}"));
    expect(source("../src/components/conversation/view/LiveEdge.svelte")).toContain("margin-block-start: 24px;");
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).toContain(".chat-queue-slot { flex: none; margin-block-start: 0; }");
  });
});

describe("one live indicator in every view", () => {
  const turn: ConversationViewTurn = {
    kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
    settled: false, stopped: false, started_at_unix_milliseconds: 100_000, ending: null,
    duration_seconds: null, tool_call_count: 1, folded_message_count: 0, latest_message_key: null, is_latest: true
  };
  const runningTool: ConversationViewItem = {
    kind: "row", key: "tool:running", row_kind: "tool_call", order: { sequence: 2, slot: 1 }, source_sequence: 2,
    sender_message_id: null, changed_through_sequence: 2, created_at: 0, turn_key: "turn:e1", work_group_key: "g1", fold_rule: "work",
    body: { kind: "inline", value: { kind: "tool_call", tool_call_id: "t1", title: "Run go test", tool_kind: "execute", detail: null, started_detail: null, capped_detail_sequence: null, status: "running" } }
  };
  const status = { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, stopped_without_ending: false } as unknown as ConversationViewStatus;
  const reader = (lens: "focus" | "full"): ConversationViewReader => ({
    id: "c", lens, pages: [{ scope: { kind: "timeline", key: null }, items: [turn, runningTool] }], status, openTools: new SvelteSet<string>()
  } as unknown as ConversationViewReader);
  it("the Working line has the spinner in Focus and in Full, even while a tool runs", () => {
    for (const lens of ["focus", "full"] as const) {
      const html = render(LiveEdge, { props: { reader: reader(lens) } }).body;
      expect(html).toContain("c2-meta-spin");
      expect(html).not.toContain("c2-meta-dot");
    }
  });
  it("a running tool row keeps its static icon and says Running, no spinner", () => {
    const row = { kind: "tool_call", key: "t", toolCallId: "t1", title: "Run go test", toolKind: "execute", detail: "go test -race ./...", startedDetail: "go test -race ./...", cappedDetailSequence: null, status: "running", sequence: 1, createdAt: 0 } as unknown as ToolCallRowData;
    const html = render(ToolCallRow, { props: { row, conversationId: "c", openRows: new SvelteSet<string>(), wholeDetails: new Map() as never } }).body;
    expect(html).toContain('class="c2-tool-icon');
    expect(html).not.toContain("c2-tool-spin");
    expect(html).toContain('data-conversation-tool-status="running"');
  });
});

describe("a rate-limited turn is one card", () => {
  const status = { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, stopped_without_ending: false } as unknown as ConversationViewStatus;
  it("says the cause and when it lifts, with Choose another model as its one primary", () => {
    const html = render(TranscriptStatusRow, { props: { status: turnEndingStatus("failed", "rate limit reached for gpt-6.1-sol (resets in 41m)")!, role: "alert" } }).body;
    expect(html).toContain(">The model's rate limit was reached. It resets in 41m.</p>");
    expect(html).toContain('class="cv-button c2-status-choose c2-status-main"');
    expect(html).toContain("data-conversation-status-details");
  });
});

describe("quiet text contrast", () => {
  it("Worked for #c4c4c4, the Working timer #b8b8b8", () => {
    expect(anchor).toContain(":is(.c2-meta--fold, [data-conversation-turn-settled-head]), .c2-meta--fold .c2-meta-sep { color: #c4c4c4; }");
    expect(anchor).toContain(".c2-meta-clock { color: #b8b8b8; }");
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain(".c2-rest-clock { color:#b8b8b8; }");
  });
});

describe("the live tool row in Full", () => {
  const running = { title: "Running", summary: "go test -race ./...", summaryIsLiteral: true, sentence: "Running go test" } as unknown as ToolCallPresentation;
  it("a running row keeps its icon, marked running, and has no spinner (only Working has one)", () => {
    const html = render(ToolRunSummary, { props: { summary: { sentence: "Ran 2 commands", failedCount: 0, running, iconPaths: ["M1 1"] }, callCount: 3, onToggle: () => { } } }).body;
    expect(html).toMatch(/<svg class="c2-run-icon[^"]*"[^>]*data-conversation-run-icon="running"/);
    expect(html).not.toContain("spinner");
  });
  it("a settled row keeps its icon, not marked running", () => {
    const html = render(ToolRunSummary, { props: { summary: { sentence: "Ran 2 commands", failedCount: 0, running: null, iconPaths: ["M1 1"] }, callCount: 2, onToggle: () => { } } }).body;
    expect(html).toContain("c2-run-icon");
    expect(html).not.toContain('data-conversation-run-icon="running"');
  });
  it("live: Running #ececec 500, the command #c4c4c4; the icon stays the quiet settled icon )", () => {
    expect(run).not.toContain(".c2-run-summary.is-live .c2-run-icon");
    expect(run).toMatch(/\.c2-run-icon \{[^}]*stroke: var\(--cv-quiet-ink, #bdbdbd\);/);
    expect(run).not.toContain("cv-spinner");
    expect(run).toContain(".c2-run-summary.is-live .c2-run-verb { color: #ececec; font-weight: var(--cv-weight-label); }");
    expect(run).toContain(".c2-run-summary.is-live .c2-run-subject { color: #c4c4c4; }");
    expect(run).toContain(".c2-run-summary, .c2-run-summary .c2-run-verb { color: #c4c4c4; }");
  });
});

describe("the error card", () => {
  it("keeps its edge (no neutral override) and a #ff8f85 title; the red dot stays", () => {
    expect(status).not.toContain(".c2-status--callout.c2-status--error { border-color: #3a3a3a; }");
    expect(status).toContain(".c2-status--callout.c2-status--error .c2-status-title { color: #ff8f85; }");
    expect(status).toContain(".c2-status--error .c2-status-dot { background: #f08a7e; }");
  });
});
