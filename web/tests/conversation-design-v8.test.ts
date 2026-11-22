import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteSet } from "svelte/reactivity";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import LiveEdge from "../src/components/conversation/view/LiveEdge.svelte";
import MessagePieces from "../src/components/conversation/MessagePieces.svelte";
import HeldPromptStack from "../src/components/conversation/composer/HeldPromptStack.svelte";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";
import { conversationState } from "../src/lib/conversation/dockPresentation";
import { COMPOSER_PLACEHOLDER, composerPlaceholderFor } from "../src/lib/conversation/runControls/logic/runSelection";
import type { ConversationViewItem, ConversationViewStatus, ConversationViewTurn } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");

const turn: ConversationViewTurn = {
  kind: "turn", key: "turn:e1", order: { sequence: 1, slot: 1 }, changed_through_sequence: 5,
  settled: false, stopped: false, started_at_unix_milliseconds: 100_000, ending: null,
  duration_seconds: null, tool_call_count: 1, folded_message_count: 0, latest_message_key: null, is_latest: true
};
const toolRow = (status: "running" | "completed"): ConversationViewItem => ({
  kind: "row", key: `tool:${status}`, row_kind: "tool_call", order: { sequence: 2, slot: 1 }, source_sequence: 2,
  sender_message_id: null, changed_through_sequence: 2, created_at: 0, turn_key: "turn:e1", work_group_key: "g1", fold_rule: "work",
  body: { kind: "inline", value: { kind: "tool_call", tool_call_id: "t1", title: "Run go test", tool_kind: "execute", detail: null, started_detail: null, capped_detail_sequence: null, status } }
});
const status = { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, stopped_without_ending: false } as unknown as ConversationViewStatus;
const reader = (lens: "focus" | "full", items: ConversationViewItem[]): ConversationViewReader => ({
  id: "c", lens, pages: [{ scope: { kind: "timeline", key: null }, items }], status, openTools: new SvelteSet<string>()
} as unknown as ConversationViewReader);

describe("v8 layout (items 1, 11, 12, 13)", () => {
  it("1: threads are top-anchored again; the empty line is centred; the lens toggle always shows", () => {
    expect(viewport).toContain(".chat-thread-content { margin-block-start: 0; }");
    expect(viewport).toContain(".chat-thread-content:has(> :global([data-conversation-empty])) { flex: 1 0 auto; justify-content: center; }");
    const live = source("../src/components/conversation/LiveConversation.svelte");
    expect(live).toContain(".conversation-empty { display: flex; align-items: flex-end; justify-content: center; flex: 1 0 auto; align-self: stretch; }");
    expect(live).not.toContain("lensHidden");
    expect(source("../src/components/conversation/ConversationPane.svelte")).not.toContain("lensHidden");
  });
  it("11: when the composer grows, a transcript that was at its end stays at its end", () => {
    expect(viewport).toContain("if (shrank && endGapBeforeResize <= 1 && heldSent === null && !pendingOpen && placementReady)");
    expect(viewport).toContain("moveTheThread(thread, thread.scrollHeight);");
    expect(viewport).toContain("endGapBeforeResize = t.scrollHeight - t.scrollTop - t.clientHeight;");
  });
  it("12 (v9 5: now 40px): the header has a fade from its bottom edge", () => {
    expect(viewport).toContain("--c2-top-fade: transparent 0, rgb(0 0 0 / 0.35) 16px, #000 40px;");
  });
});

describe("v8 live edge (items 3-5)", () => {
  it("3: the Working line comes before the queued messages, 24px under the turn's content", () => {
    expect(viewport.indexOf("<LiveEdge {reader}")).toBeLessThan(viewport.indexOf("{@render afterTranscript()}"));
    expect(source("../src/components/conversation/view/LiveEdge.svelte")).toContain("margin-block-start: 24px;");
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).toContain(".chat-queue-slot { flex: none; margin-block-start: 0; }");
  });
  it("3 (v10 2): a queued message's chip says Queued · 1 of 1, running or not", () => {
    const row = { key: "a", heldPromptId: "a", senderMessageId: null, state: "held", queueReason: null,
      content: [{ piece: "text", text: "Log each 429" }] } as unknown as HeldPromptRow;
    const running = render(HeldPromptStack, { props: { rows: [row], running: true, conversationId: "c" } }).body;
    expect(running).toMatch(/class="chat-qrow-word[^"]*">Queued<\/span>(?:<!--[^>]*-->)?<span class="chat-qrow-sep[^"]*" aria-hidden="true">·<\/span><span class="chat-qrow-detail[^"]*" data-conversation-held-detail="">1 of 1<\/span>/);
    expect(running).not.toContain("sends after this turn");
    const idle = render(HeldPromptStack, { props: { rows: [row], running: false, conversationId: "c" } }).body;
    expect(idle).toContain("1 of 1");
  });
  it("4 (replaced by v9 1): the Working line keeps the spinner in Full while a tool runs", () => {
    const full = render(LiveEdge, { props: { reader: reader("full", [turn, toolRow("running")]) } }).body;
    expect(full).toContain("c2-meta-spin");
    expect(full).not.toContain('data-conversation-live-mark="dot"');
  });
  it("5 (v9 24): no narration sentence under the Working line in any view", () => {
    expect(render(LiveEdge, { props: { reader: reader("focus", [turn]) } }).body).not.toContain("data-conversation-live-narration");
    expect(render(LiveEdge, { props: { reader: reader("full", [turn]) } }).body).not.toContain("data-conversation-live-narration");
  });
});

describe("v8 messages and asks (items 6-9)", () => {
  it("6: attachments sit inside the user bubble, above its words; 8 around them, 12 16 around the words", () => {
    const html = render(MessagePieces, { props: { bubble: true, conversationId: "c", content: [
      { piece: "text", text: "Use this export." },
      { piece: "file", stored_file_id: "f1", file_name: "export.csv", media_type: "text/csv", byte_count: 1700 }
    ] } }).body;
    expect(html.match(/data-conversation-bubble[ =>]/g)?.length).toBe(1);
    expect(html.indexOf("conversation-file-card")).toBeLessThan(html.indexOf("c2-bubble-words"));
    const words = render(MessagePieces, { props: { bubble: true, conversationId: "c", content: [{ piece: "text", text: "Hi" }] } }).body;
    expect(words).not.toContain("c2-bubble--attached");
    const css = source("../src/components/conversation/MessagePieces.svelte");
    expect(css).toContain(".c2-bubble--attached { display: flex; flex-direction: column; gap: 8px; padding: 8px; }");
    expect(css).toContain(".c2-bubble-words { padding: 4px 8px; min-width: 0; }");
  });
  it("7: the focused row (v11 (2): #303030) with the accent edge, the well a 1px #333 edge (Deny and Stop turn: see v9)", () => {
    const shell = source("../src/components/conversation/AskShell.svelte");
    expect(shell).toContain("background: #303030; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%);");
    expect(composer).toContain(".chat-box.has-ask.is-compact > .chat-ta { box-shadow: inset 0 1px 0 #333, inset 0 -1px 0 #333; }");
    expect(composer).toContain(".chat-box.has-ask:not(.is-compact) > .chat-ta { box-shadow: inset 0 0 0 1px #333; }");
  });
  it("8: the placeholder is Write a message everywhere; the ask wells keep theirs", () => {
    expect(COMPOSER_PLACEHOLDER).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: null, mode: "queue", running: false, base: "Message Chief of Staff..." })).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: "answers", mode: "queue", running: true, base: "x" })).toBe("Or write a reply");
    expect(composerPlaceholderFor({ askWaiting: "holds", mode: "queue", running: true, base: "x" })).toBe("Or write a reply");
  });
});

describe("v8 item 14: no chevrons on dropdown or expand controls", () => {
  it("the model, Steer and Queued chips show their label alone; v11 (6): Dock has no arrow, only Open conversation keeps one", () => {
    expect(source("../src/components/conversation/composer/ComposerRunControls.svelte")).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/DeliveryModePicker.svelte")).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).not.toContain("chat-qrow-chevron");
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    const head = pane.slice(pane.indexOf('class="chat-head-collapse"'), pane.indexOf("</button>", pane.indexOf('class="chat-head-collapse"')));
    // v11 (6): Dock is its label alone (the v10 down arrow is gone).
    expect(head).not.toContain("<svg");
    const facts = { readOnly: false, activityUncertain: false, waitingForYou: false, waitingFor: null, running: true, backgroundWorking: false, settingUp: null, elapsed: null };
    const rest = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(facts) } }).body;
    const control = rest.slice(rest.indexOf("c2-rest-control"));
    // v9 (25, owner): Open conversation is the one exception: a 12px up arrow after its label.
    expect(control).toMatch(/c2-rest-control-label[^>]*>Open conversation<\/span>[\s\S]*?<svg class="c2-rest-control-arrow/);
    // Pager arrows, Details › and Ran 2 commands › are not dropdown marks and stay.
    expect(source("../src/components/conversation/ToolRunSummary.svelte")).toContain('class="c2-run-chevron"');
    expect(source("../src/components/conversation/TranscriptStatusRow.svelte")).toContain("c2-status-chevron");
  });
});

describe("v8 chart (item 10)", () => {
  it("the two series are #bccbf0 and #e0b36b", () => {
    expect(app).toMatch(/--accent-bright: #bccbf0;\s*--chart-compare-second: #e0b36b;/);
  });
});

describe("p14c: tables in answers", () => {
  it("use proportional figures, so Inter's tabular hyphen does not open up words like Full-text", () => {
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown table { font-family: var(--cv-font); font-size: 15px; line-height: 24px; font-variant-numeric: normal; }");
  });
});
