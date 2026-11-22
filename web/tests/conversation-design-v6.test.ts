import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
import { SvelteSet } from "svelte/reactivity";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import LiveEdge from "../src/components/conversation/view/LiveEdge.svelte";
import ConversationRestBar from "../src/components/conversation/ConversationRestBar.svelte";
import TranscriptStatusRow from "../src/components/conversation/TranscriptStatusRow.svelte";
import { conversationState } from "../src/lib/conversation/dockPresentation";
import { liveTurnOf, turnHeadAtTop } from "../src/lib/conversation/view/liveEdge";
import type { ConversationViewItem, ConversationViewStatus, ConversationViewTurn } from "../src/lib/conversation/view/contracts";
import type { ConversationViewReader } from "../src/lib/conversation/view/reader.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const composer = source("../src/components/conversation/ConversationComposer.svelte");
const viewport = source("../src/components/conversation/viewport/ConversationViewport.svelte");

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

describe("v6 layout: the live edge (items 2-5; v8 undoes item 1 and item 5)", () => {
  it("2: the running turn's head is the live edge, not its top (v8: before the queued messages)", () => {
    expect(liveTurnOf([{ items: [turn(false)] }], statusWith())?.key).toBe("turn:e1");
    expect(liveTurnOf([{ items: [turn(true)] }], statusWith())).toBeNull();
    expect(liveTurnOf([{ items: [turn(false)] }], statusWith({ stopped_without_ending: true }))).toBeNull();
    expect(turnHeadAtTop(turn(false), statusWith())).toBe(false);
    expect(turnHeadAtTop(turn(true), statusWith())).toBe(true);
    // v8 (3): the edge is drawn before the waiting messages (afterTranscript), inside the content box.
    expect(viewport.indexOf("<LiveEdge {reader}")).toBeLessThan(viewport.indexOf("{@render afterTranscript()}"));
  });

  it("2/3: the live edge says Working with the clock, and in Focus the latest narration sentence under it", () => {
    const narration = "Confirming it reproduces under -race before I write it up.";
    const focus = render(LiveEdge, { props: { reader: reader("focus", [turn(false)], statusWith({ live_narration: narration })) } }).body;
    expect(focus).toContain("data-conversation-live-edge");
    expect(focus).toContain("data-conversation-alive-word");
    expect(focus).toMatch(/data-conversation-live-narration[^>]*>Confirming it reproduces under -race before I write it up\.<\/p>/);
    const full = render(LiveEdge, { props: { reader: reader("full", [turn(false)], statusWith({ live_narration: narration })) } }).body;
    expect(full).toContain("data-conversation-alive-word");
    expect(full).not.toContain("data-conversation-live-narration");
    const waiting = render(LiveEdge, { props: { reader: reader("focus", [turn(false)], statusWith({ pending_permission: { kind: "permission", request_id: "a", row_key: "e2", source_sequence: 2 } })) } }).body;
    expect(waiting).not.toContain("data-conversation-live-edge");
    const css = source("../src/components/conversation/view/LiveEdge.svelte");
    expect(css).toMatch(/\.c2-live-narration \{[^}]*white-space: nowrap;\s*text-overflow: ellipsis;\s*color: #b0b0b0;\s*font: var\(--cv-weight-body\) 14px\/20px var\(--cv-font\);/);
  });

  it("4 (p13b): a 16px fade above the composer while content is below the fold, so it never washes a surface's fill into a gradient", () => {
    expect(viewport).toContain("--c2-bottom-fade: #000 calc(100% - 16px), transparent 100%;");
  });

  it("5 (v8 13: removed): Jump to latest always straddles the composer's top edge", () => {
    expect(app).not.toContain("container: cv-layer");
    expect(app).not.toContain("@container cv-layer");
    expect(app).toMatch(/\.cv-jump \{\s*position: absolute;\s*left: 50%;\s*bottom: -16px;\s*transform: translateX\(-50%\);/);
  });
});

describe("v6 asks (items 6-8)", () => {
  it("6: no Paused line; the dock's left side is empty while the ask card shows", () => {
    expect(composer).not.toContain("Paused");
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).not.toContain("Paused");
  });
  it("8: the dock's Needs your approval wears the accent label and dot (v7: a status line, 500, 8px dot in a 16px slot)", () => {
    const facts = { readOnly: false, activityUncertain: false, waitingForYou: true, waitingFor: "approval" as const, running: true, backgroundWorking: false, settingUp: null, elapsed: null };
    const html = render(ConversationRestBar, { props: { onOpen: () => {}, state: conversationState(facts), pageHoldsTheAsk: true } }).body;
    expect(html).toContain("cv-dot");
    expect(html).toContain("Needs your approval");
    const css = source("../src/components/conversation/ConversationRestBar.svelte");
    expect(css).toContain('[data-conversation-rest-state="waiting"] .c2-rest-label { color:var(--cv-accent); font-weight:var(--cv-weight-label); }');
    expect(css).toContain('[data-conversation-rest-state="waiting"] .cv-dot { width:8px; height:8px; margin-inline:4px; background:var(--cv-accent); }');
  });
});

describe("v6 transcript (items 9-15)", () => {
  it("9 (v8 2): narration (not the turn's final message, or any prose while live) is 15/24 #b4b4b4; the final message #ececec", () => {
    expect(app).toMatch(/\.chat-a--narration \{\s*color: #b4b4b4;\s*font-size: var\(--cv-type-prose\);\s*line-height: var\(--cv-leading-prose\);/);
    expect(app).toContain(".chat-a .markdown { color: #ececec; }");
    const row = source("../src/components/conversation/view/ProjectedRow.svelte");
    expect(row).toContain("return !each.settled || each.latest_message_key !== item.key;");
  });
  it("10: the one spinner (the Working line) is a #d9d9d9 arc on a #4a4a4a track", () => {
    for (const file of ["TurnAnchor"])
      expect(source(`../src/components/conversation/${file}.svelte`)).toMatch(/border: [\d.]+px solid #4a4a4a;\s*border-top-color: #d9d9d9;/);
  });
  it("12: an older error is one inline row: dot, This turn failed: <short cause>, Details", () => {
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain('{#if shownWeight === "callout" && status.tone === "error" && !newest}');
    expect(row).toContain('<span class="c2-status-inline-words"><span class="c2-status-inline-head">{calloutHeading}</span><span class="c2-status-sep" aria-hidden="true">·</span><span>{shortCause}</span></span>');
    // The newest (and, unmeasured, every) error is still the card with its actions.
    const html = render(TranscriptStatusRow, { props: { status: { tone: "error", title: "The turn stopped because the model's rate limit was reached. It resets in 41m.", detail: "429", next: null, action: "choose-model" } } }).body;
    expect(html).toContain("c2-status--callout");
    expect(html).not.toContain("data-conversation-error-inline");
  });
  it("13: a queued picture wears the queued frame (1px at 16%) at full strength", () => {
    const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
    expect(held).toMatch(/\.chat-qrow :global\(\.c2-piece-image-slot img\) \{[^}]*outline: 1px solid rgb\(255 255 255 \/ 16%\);/);
    expect(held).not.toMatch(/c2-piece-image-slot img\) \{[^}]*opacity/);
  });
  it("14: a heading right after Worked for gets 12px more above it", () => {
    expect(app).toContain('[data-rhythm="meta"] .chat-a .markdown > :is(h1, h2, h3, h4, h5, h6):first-child { margin-block-start: 12px; }');
  });
});

describe("v6 composer (items 16-21)", () => {
  it("16: the model and Steer chips have no fill at rest; hover #2a2a2a", () => {
    expect(app).toMatch(/\[data-conversation-model-picker\] > \.listbox-picker-trigger:hover:not\(:disabled\) \{ background: #2a2a2a;/);
    const mode = source("../src/components/conversation/composer/DeliveryModePicker.svelte");
    expect(mode).toMatch(/background: transparent;\s*color: #d9d9d9;/);
    expect(mode).toMatch(/hover:not\(:disabled\)\) \{\s*background: #2a2a2a;/);
  });
  it("17: focused or expanded, one 1px #3a3a3a border and no outer ring", () => {
    expect(composer).toContain(".chat-box.keyboard-focus, .chat-box:not(.is-compact) { border-color: #3a3a3a; outline: none; }");
    expect(app).toMatch(/\.chat-box:focus-within \{\s*border-color: #3a3a3a;\s*box-shadow: none;/);
  });
  it("19: menus open with their bottom 8px above the composer's top edge", () => {
    expect(composer).toContain('anchor.style.setProperty("--c2-menu-lift"');
    expect(composer).toContain("bottom: calc(100% + 8px + var(--c2-menu-lift, 0px));");
    expect(composer).toContain(".chat-add-menu { inset-block-end: calc(100% + 8px + var(--c2-menu-lift, 0px));");
  });
  it("20 (v7 wins): the inactive lens is a quiet Button (#bdbdbd); the Queued chip a composer control (#d9d9d9)", () => {
    expect(source("../src/components/conversation/ConversationPane.svelte")).toMatch(/\.cv-segment \{[^}]*color: #bdbdbd;/);
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).toMatch(/\.chat-qrow-chip \{[^}]*color: #d9d9d9;/);
  });

});

describe("v6.1 model picker (items 23-26)", () => {
  const picker = source("../src/components/conversation/UnifiedModelPicker.svelte");
  const listbox = source("../src/components/conversation/ListboxPicker.svelte");
  it("23: the composer uses the base picker: a chosen model goes on to its efforts, Reasoning stays in the rail", () => {
    expect(picker).not.toContain("layout");
    expect(picker).toContain('showing = "efforts";');
    expect(picker).toContain('"data-conversation-picker-reasoning": ""');
    expect(picker).not.toContain("model-picker-effort-control");
    expect(source("../src/components/conversation/composer/ComposerRunControls.svelte")).not.toContain('layout="composer"');
  });
  it("24: only the Steer menu (compact) wears the composer's floating plane; the rail panel keeps the base look", () => {
    expect(listbox).not.toMatch(/\[data-conversation-composer\]\) \.listbox-picker-panel \{/);
    expect(listbox).not.toMatch(/\[data-conversation-composer\]\) \.listbox-picker-option \{/);
    expect(listbox).toContain(":global([data-conversation-composer]) .listbox-picker-panel.compact {");
    expect(listbox).toContain('{item.value === selectedValue ? "✓" : ""}');
    expect(source("../src/components/conversation/composer/DeliveryModePicker.svelte")).toContain("drawnTick");
  });
  it("26: the composer's model picker panel is only as wide as its content", () => {
    expect(listbox).toMatch(/\[data-conversation-composer\]\) \.listbox-picker-panel\.rail \{\s*grid-template-columns: max-content max-content;\s*width: max-content;/);
    expect(listbox).not.toContain("width: 440px");
  });
});
