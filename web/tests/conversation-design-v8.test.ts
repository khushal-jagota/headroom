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
import { liveTurnRunsATool } from "../src/lib/conversation/view/liveEdge";
import { COMPOSER_PLACEHOLDER, composerPlaceholderFor } from "../src/lib/conversation/runControls/logic/runSelection";
import type { ConversationViewItem, ConversationViewStatus, ConversationViewTurn } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";
import type { HeldPromptRow } from "../src/lib/conversation/heldPrompts";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const composer = source("../src/components/conversation/ConversationComposer.svelte");
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
const status = { turn_key: "turn:e1", pending_permission: null, pending_user_input: null, stopped_without_ending: false, live_narration: "Confirming it reproduces." } as unknown as ConversationViewStatus;
const reader = (lens: "focus" | "full", items: ConversationViewItem[]): ConversationViewReader => ({
  id: "c", lens, pages: [{ scope: { kind: "timeline", key: null }, items }], status, openTools: new SvelteSet<string>()
} as unknown as ConversationViewReader);

describe("v8 layout (items 1, 11, 12, 13)", () => {
  it("1: threads are top-anchored again; the empty line is centred; the lens toggle always shows", () => {
    expect(viewport).toContain(".chat-thread-content { margin-block-start: 0; }");
    expect(viewport).toContain(".chat-thread-content:has(> :global([data-conversation-empty])) { flex: 1 0 auto; justify-content: center; }");
    const live = source("../src/components/conversation/LiveConversation.svelte");
    expect(live).toContain(".conversation-empty { display: flex; align-items: center; justify-content: center; flex: 1 0 auto; align-self: stretch; }");
    expect(live).not.toContain("lensHidden");
    expect(source("../src/components/conversation/ConversationPane.svelte")).not.toContain("lensHidden");
  });
  it("11: when the composer grows, a transcript that was at its end stays at its end", () => {
    expect(viewport).toContain("if (shrank && endGapBeforeResize <= 1 && heldSent === null && !pendingOpen && placementReady)");
    expect(viewport).toContain("moveTheThread(thread, thread.scrollHeight);");
    expect(viewport).toContain("endGapBeforeResize = t.scrollHeight - t.scrollTop - t.clientHeight;");
  });
  it("12: the header's fade is 16px", () => {
    expect(viewport).toContain("--c2-top-fade: transparent 0, #000 16px;");
  });
});

describe("v8 live edge (items 3-5)", () => {
  it("3: the Working line comes before the queued messages, 24px under the turn's content", () => {
    expect(viewport.indexOf("<LiveEdge {reader}")).toBeLessThan(viewport.indexOf("{@render afterTranscript()}"));
    expect(source("../src/components/conversation/view/LiveEdge.svelte")).toContain("margin-block-start: 24px;");
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).toContain(".chat-queue-slot { flex: none; margin-block-start: 0; }");
  });
  it("3: each queued message says Queued · sends after this turn while a turn runs", () => {
    const row = { key: "a", heldPromptId: "a", senderMessageId: null, state: "held", queueReason: null,
      content: [{ piece: "text", text: "Log each 429" }] } as unknown as HeldPromptRow;
    const running = render(HeldPromptStack, { props: { rows: [row], running: true, conversationId: "c" } }).body;
    expect(running).toMatch(/class="chat-qrow-word[^"]*">Queued<\/span>(?:<!--[^>]*-->)?<span class="chat-qrow-sep[^"]*" aria-hidden="true">·<\/span><span class="chat-qrow-detail[^"]*" data-conversation-held-detail="">sends after this turn<\/span>/);
    const idle = render(HeldPromptStack, { props: { rows: [row], running: false, conversationId: "c" } }).body;
    expect(idle).not.toContain("sends after this turn");
  });
  it("4: in Full, a running tool row carries the spinner and the Working line a still dot", () => {
    expect(liveTurnRunsATool([{ items: [turn, toolRow("running")] }], "turn:e1")).toBe(true);
    expect(liveTurnRunsATool([{ items: [turn, toolRow("completed")] }], "turn:e1")).toBe(false);
    const full = render(LiveEdge, { props: { reader: reader("full", [turn, toolRow("running")]) } }).body;
    expect(full).toContain('data-conversation-live-mark="dot"');
    expect(full).not.toContain("c2-meta-spin");
    const fullIdle = render(LiveEdge, { props: { reader: reader("full", [turn, toolRow("completed")]) } }).body;
    expect(fullIdle).toContain("c2-meta-spin");
    // Focus shows no tool rows, so its Working line keeps the spinner.
    const focus = render(LiveEdge, { props: { reader: reader("focus", [turn, toolRow("running")]) } }).body;
    expect(focus).toContain("c2-meta-spin");
    expect(source("../src/components/conversation/TurnAnchor.svelte")).toContain(".c2-meta-dot { flex: none; width: 6px; height: 6px; border-radius: 50%; background: #d9d9d9; }");
  });
  it("5: the narration sentence shows in Focus only", () => {
    expect(render(LiveEdge, { props: { reader: reader("focus", [turn]) } }).body).toContain("data-conversation-live-narration");
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
  it("7: Deny is secondary, Stop turn quiet, the focused row #2c2c2c with the accent edge, the well a 1px #333 edge", () => {
    expect(composer).toMatch(/\.chat-box :global\(\.chat-ask-quiet\) \{[^}]*background: #2a2a2a;\s*color: #ececec;/);
    const shell = source("../src/components/conversation/AskShell.svelte");
    expect(shell).toMatch(/:global\(\[data-conversation-pane\]\) \.cv-ask-stop \{[^}]*background: transparent;\s*color: #bdbdbd;/);
    expect(shell).toContain("background: #2c2c2c; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%);");
    expect(composer).toContain(".chat-box.has-ask.is-compact > .chat-ta { box-shadow: inset 0 1px 0 #333, inset 0 -1px 0 #333; }");
    expect(composer).toContain(".chat-box.has-ask:not(.is-compact) > .chat-ta { box-shadow: inset 0 0 0 1px #333; }");
  });
  it("8: the placeholder is Write a message everywhere; the ask wells keep theirs", () => {
    expect(COMPOSER_PLACEHOLDER).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: null, mode: "queue", running: false, base: "Message Chief of Staff..." })).toBe("Write a message");
    expect(composerPlaceholderFor({ askWaiting: "answers", mode: "queue", running: true, base: "x" })).toBe("Or write your own answer");
    expect(composerPlaceholderFor({ askWaiting: "holds", mode: "queue", running: true, base: "x" })).toBe("Or write a message");
  });
});

describe("v8 item 14: no chevrons on dropdown or expand controls", () => {
  it("the model, Steer and Queued chips, Dock and Open conversation show their label alone", () => {
    expect(source("../src/components/conversation/composer/ComposerRunControls.svelte")).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/DeliveryModePicker.svelte")).toContain('chevron="none"');
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).not.toContain("chat-qrow-chevron");
    const pane = source("../src/components/conversation/ConversationPane.svelte");
    const head = pane.slice(pane.indexOf('class="chat-head-collapse"'), pane.indexOf("</button>", pane.indexOf('class="chat-head-collapse"')));
    expect(head).not.toContain("<svg");
    const facts = { readOnly: false, activityUncertain: false, waitingForYou: false, waitingFor: null, running: true, backgroundWorking: false, settingUp: null, elapsed: null };
    const rest = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(facts) } }).body;
    const control = rest.slice(rest.indexOf("c2-rest-control"));
    expect(control.slice(0, control.indexOf("</span></span>"))).not.toContain("<svg");
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
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown table { font-family: var(--cv-font); font-size: 14px; line-height: 20px; font-variant-numeric: normal; }");
  });
});
