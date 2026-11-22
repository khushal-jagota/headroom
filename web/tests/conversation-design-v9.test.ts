import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteSet } from "svelte/reactivity";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import LiveEdge from "../src/components/conversation/view/LiveEdge.svelte";
import ToolCallRow from "../src/components/conversation/ToolCallRow.svelte";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";
import HeldPromptStack from "../src/components/conversation/composer/HeldPromptStack.svelte";
import TranscriptStatusRow from "../src/components/conversation/TranscriptStatusRow.svelte";
import MessagePieces from "../src/components/conversation/MessagePieces.svelte";
import { conversationState } from "../src/lib/conversation/dockPresentation";
import { turnEndingStatus } from "../src/lib/conversation/transcript";
import { dockEdgeOpacity, DOCK_EDGE_FADE_MS } from "../src/lib/conversation/layout/dock";
import { CONVERSATION_CARD_MS } from "../src/lib/conversation/surfaceMotion";
import type { ConversationViewItem, ConversationViewStatus, ConversationViewTurn } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";
import type { ToolCallRow as ToolCallRowData } from "../src/lib/conversation/transcript";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const composer = source("../src/components/conversation/ConversationComposer.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");

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
const workingFacts = { readOnly: false, activityUncertain: false, waitingForYou: false, waitingFor: null, running: true, backgroundWorking: false, settingUp: null, elapsed: "2m 14s" };

describe("v9 (1): one live indicator in every view", () => {
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

describe("v9 (2): the permission ask", () => {
  const ask = { askId: "a", title: "Upload build 41", detail: null, options: [
    { option_id: "allow", label: "Allow once", option_kind: "allow_once" },
    { option_id: "always", label: "Always allow uploads", option_kind: "allow_always" },
    { option_id: "deny", label: "Deny", option_kind: "reject_once" }] };
  it("docked, Deny is option row 3; there is no separate Deny button", () => {
    const html = render(PermissionAskCard, { props: { ask, docked: true, onAnswer: () => {} } }).body;
    const order = [...html.matchAll(/data-conversation-ask-action="([^"]+)"/g)].map(match => match[1]);
    expect(order).toEqual(["allow", "always", "deny"]);
    expect(html).toMatch(/data-conversation-ask-action="deny"[^>]*aria-keyshortcuts="3"/);
  });
  it("v10 (4) replaces the primary pill: the card has no commit; a row answers at once", () => {
    const card = source("../src/components/conversation/PermissionAskCard.svelte");
    expect(card).not.toContain("commitLabel");
    expect(card).not.toContain("declineAction");
    expect(card).toMatch(/function pick\(optionId: string\): void \{\s*if \(busy \|\| !answerAvailable\) return;\s*onAnswer\(optionId\);\s*\}/);
  });
});

describe("v9 (3, 16): resting shapes and the header", () => {
  it("model and Steer chips and Open conversation rest on #232323 (v11 (4): the queued note has no pill)", () => {
    expect(app).toMatch(/\[data-conversation-model-picker\] > \.listbox-picker-trigger \{[^}]*background: #232323;/);
    expect(source("../src/components/conversation/composer/DeliveryModePicker.svelte")).toMatch(/padding-inline: 12px;\s*background: #232323;/);
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).not.toContain("chat-qrow-chip");
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain("border-radius:999px; background:#232323; color:#d9d9d9;");
  });
  it("Focus and Full sit in one segmented track, 16px before Dock", () => {
    expect(pane).toMatch(/\.cv-segmented \{[^}]*gap: 2px;\s*padding: 2px;\s*border-radius: 10px;\s*background: #1c1c1c;\s*margin-inline-end: 4px;/);
    expect(pane).toMatch(/\.cv-segment\[aria-pressed="true"\], \.cv-segment\[aria-pressed="true"\]:hover \{ background: #2a2a2a; color: #ececec; \}/);
  });
});

describe("v9 (5, 8, 9, 15, 21, 22): measures", () => {
  it("the header's fade is 40px", () => {
    expect(source("../src/components/conversation/viewport/ConversationViewport.svelte")).toContain("--c2-top-fade: transparent 0, rgb(0 0 0 / 0.35) 16px, #000 40px;");
  });
  it("tables: head 14/20 600 over a #3a3a3a rule; body 15/24", () => {
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown th { padding-block-start: 0; color: #f2f2f2; font-size: 14px; line-height: 20px; font-weight: 600; border-block-end-color: #3a3a3a; }");
  });
  it("v11 (8): disabled send #333333/#bdbdbd; disabled pager #a6a6a6", () => {
    expect(app).toMatch(/\.chat-send \{[^}]*background: #262626;\s*color: #7a7a7a;/);
    const shell = source("../src/components/conversation/AskShell.svelte");
    expect(shell).toMatch(/:global\(\[data-conversation-pane\]\) \.cv-ask-stop \{[^}]*background: #2a2a2a;\s*color: #ececec;/);
    expect(shell).toContain(":global([data-conversation-pane]) .cv-ask-context :global(.cv-icon-button:disabled) { color: #5a5a5a; background: none; cursor: default; pointer-events: none; }");
  });
  it("narration in Full is 14/22; v12 (1): the column is 640", () => {
    expect(app).toMatch(/\[data-conversation-pane\] \.c2-streaming \.markdown \{\s*font-family: var\(--cv-font\);\s*font-size: 14px;\s*line-height: 22px;\s*color: #b4b4b4;/);
    expect(tokens).toContain("--cv-col: 640px;");
    // Docked on a work page, the conversation keeps the page's 760 column.
    expect(app).not.toMatch(/\n\.conversation-layer > \.conversation-column \{\s*width: min\(var\(--cv-col\)/);
  });
});

describe("v9 (7): a rate-limited turn is one card", () => {
  it("says the cause and when it lifts, with Choose another model as its one primary", () => {
    const html = render(TranscriptStatusRow, { props: { status: turnEndingStatus("failed", "rate limit reached for gpt-6.1-sol (resets in 41m)")!, role: "alert" } }).body;
    expect(html).toContain(">The model's rate limit was reached. It resets in 41m.</p>");
    expect(html).toContain('class="cv-button c2-status-choose c2-status-main"');
    expect(html).toContain("data-conversation-status-details");
  });
});

describe("v9 (10, 14, 17, 24, 25): the dock", () => {
  it("v9 (24, owner): no narration sentence under the Working line, in the dock or the transcript", () => {
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(workingFacts as never) } }).body;
    expect(html).not.toContain("narration");
    expect(pane).not.toContain("live_narration");
    expect(render(LiveEdge, { props: { reader: reader("focus") } }).body).not.toContain("narration");
    expect(source("../src/lib/conversation/view/contracts.ts")).not.toContain("live_narration");
  });
  it("v9 (25, owner): Open conversation has a 12px up arrow after its label", () => {
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(workingFacts as never) } }).body;
    expect(html).toMatch(/>Open conversation<\/span>[\s\S]*?<svg class="c2-rest-control-arrow[^"]*" viewBox="0 0 16 16" data-conversation-open-arrow=""><path d="m4 10 4-4 4 4"><\/path><\/svg>/);
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).toContain(".c2-rest-control-arrow { flex:none; width:12px; height:12px;");
  });
  it("with a proposal on the page, Needs your approval wears its dot; v10 (10): in #bccbf0 like every ask label", () => {
    const bar = source("../src/components/conversation/ConversationRestBar.svelte");
    expect(bar).toContain("[data-conversation-rest-page-waits-row] .c2-rest-label { color:#bccbf0; }");
    expect(bar).toContain("[data-conversation-rest-page-waits-row] .cv-dot { width:6px; height:6px; margin-inline:0; background:#bccbf0; }");
    expect(bar).not.toContain("#a9bbe3");
  });
  it("the dock zone has a 1px white top edge (v11 (8): 16%) across the dock's column only, faded by the dock's move", () => {
    expect(app).toMatch(/:is\(\.manager-page, \.ticket-page\) > \.conversation-layer::after \{\s*left: 50%;\s*right: auto;\s*width: min\(var\(--page-measure\), calc\(100% - 2 \* var\(--space-5\)\)\);\s*transform: translateX\(-50%\);\s*height: 1px;\s*background: rgba\(255, 255, 255, 0\.16\);[^\n]*\s*opacity: var\(--conversation-dock-line, 1\);/);
    expect(app).toContain(':is(.manager-page, .ticket-page)[data-conversation-dock-state="opened"]:not([data-conversation-dock-transition]) > .conversation-layer::after { opacity: 0; }');
  });
  it("the edge fades over 180ms: out at the start of opening, in over the end of the return", () => {
    expect(DOCK_EDGE_FADE_MS).toBe(180);
    const at = (ms: number) => ms / CONVERSATION_CARD_MS;
    // Opening: gone 180ms in, half gone at 90ms.
    expect(dockEdgeOpacity(1, 0, 0)).toBe(1);
    expect(dockEdgeOpacity(1, 0, at(90))).toBeCloseTo(0.5);
    expect(dockEdgeOpacity(1, 0, at(180))).toBe(0);
    expect(dockEdgeOpacity(1, 0, 1)).toBe(0);
    // Returning: nothing while the layer is still tall, fully there as the dock lands.
    const start = CONVERSATION_CARD_MS - 180;
    expect(dockEdgeOpacity(0, 1, at(start))).toBe(0);
    expect(dockEdgeOpacity(0, 1, at(start + 90))).toBeCloseTo(0.5);
    expect(dockEdgeOpacity(0, 1, 1)).toBe(1);
  });
});

describe("v9 (11, 12, 19, 20): thread pieces", () => {
  it("From Panels is a left-aligned status line", () => {
    expect(source("../src/components/conversation/UserMessage.svelte")).toMatch(/\.c2-system-note \{\s*align-self: flex-start;[^}]*text-align: start;/);
  });
  it("v10 (2): every queued message carries the same chip, Queued · n of m", () => {
    const row = (key: string) => ({ key, heldPromptId: key, senderMessageId: null, state: "held", queueReason: null,
      content: [{ piece: "text", text: `Message ${key}` }] }) as unknown as HeldPromptRow;
    const html = render(HeldPromptStack, { props: { rows: [row("a"), row("b")], running: true, conversationId: "c" } }).body;
    expect(html).not.toContain("sends after this turn");
    expect(html.match(/chat-qrow-word[^"]*">Queued</g)?.length).toBe(2);
    expect(html).toMatch(/data-conversation-held-detail="">1 of 2<[\s\S]*data-conversation-held-detail="">2 of 2</);
  });
  it("file cards fill the bubble; a one-image bubble is as wide as its image", () => {
    const one = render(MessagePieces, { props: { bubble: true, conversationId: "c", content: [
      { piece: "image", stored_file_id: "i1", file_name: "chart.png", media_type: "image/png", byte_count: 10 },
      { piece: "text", text: "Match this." }] } }).body;
    expect(one).toContain("c2-bubble--one-image");
    const file = render(MessagePieces, { props: { bubble: true, conversationId: "c", content: [
      { piece: "file", stored_file_id: "f1", file_name: "export.csv", media_type: "text/csv", byte_count: 1700 },
      { piece: "text", text: "Use this." }] } }).body;
    expect(file).not.toContain("c2-bubble--one-image");
    const css = source("../src/components/conversation/MessagePieces.svelte");
    expect(css).toContain(".c2-bubble-attachments > :global(.conversation-file-card) { align-self: stretch; width: auto; max-width: none; }");
    expect(css).toContain(".c2-bubble--one-image { min-width: 240px; }");
    expect(css).toContain(".c2-bubble--one-image > .c2-bubble-words { width: 0; min-width: 100%; }");
  });
  it("v11 (3): every ask's Stop is the composer's round Stop, after the well at the row's right end", () => {
    expect(composer).toContain('{#if panelTakesTheBox && running && onStop}');
    expect(composer).toMatch(/class="chat-ask-stop" data-conversation-stop=\{true\} data-conversation-ask-stop/);
    expect(composer).toMatch(/\.chat-box\.has-ask > \.chat-foot > \.chat-ask-stop \{[^}]*order: 4;/);
    const question = composer.slice(composer.indexOf("<UserInputQuestionPanel"), composer.indexOf("/>", composer.indexOf("<UserInputQuestionPanel")));
    expect(question).toContain("onStop={undefined}");
    const permission = composer.slice(composer.indexOf("<PermissionAskCard"), composer.indexOf("/>", composer.indexOf("<PermissionAskCard")));
    expect(permission).toContain("onStop={undefined}");
    expect(composer).toContain("<ConversationCredentialRequestCard request={credentialRequest} framed={false} onStop={undefined} />");
  });
});
