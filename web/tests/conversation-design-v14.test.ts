import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import ComposerWithQueryClient from "./fixtures/ComposerWithQueryClient.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const controls = source("../src/components/conversation/composer/ComposerRunControls.svelte");
const shell = source("../src/components/conversation/AskShell.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const run = source("../src/components/conversation/ToolRunSummary.svelte");
const tool = source("../src/components/conversation/ToolCallRow.svelte");
const status = source("../src/components/conversation/TranscriptStatusRow.svelte");
const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");

const base = { draftKey: "conversation-design-v14", onSend: async () => true };
const ask = { askId: "a-1", title: "Upload build 41", detail: "xcrun altool --upload-app", options: [
  { option_id: "allow", label: "Allow once", option_kind: "allow_once" as const },
  { option_id: "deny", label: "Deny", option_kind: "reject_once" as const }] };

describe("v14 (1): one Stop style everywhere", () => {
  it("a 32px round with no fill, a 1px #4a4a4a ring and a 10px #ececec square, radius 2; hover #2a2a2a", () => {
    expect(app).toContain(".chat-send.stop { background: none; box-shadow: inset 0 0 0 1px #4a4a4a; color: #ececec; }");
    expect(app).toContain(".chat-send.stop:hover { background: #2a2a2a; }");
    expect(app).toContain(".chat-send.stop .chat-send-fill { width: 10px; height: 10px; border-radius: 2px; }");
    expect(app).toMatch(/\.chat-send \{[^}]*width: 32px; height: 32px;/);
  });
  it("is the composer's last control while the agent works; the active Send stays #f2f2f2", () => {
    const body = render(ComposerWithQueryClient, { props: { ...base, running: true, onStop: () => {} } }).body;
    expect(body).toMatch(/<button[^>]*class="chat-send stop"[^>]*data-conversation-stop/);
    expect(controls).toContain('class="chat-send stop"');
    expect(app).toContain(".chat-send.on { background: #f2f2f2; color: #121212; }");
  });
});

describe("v14 (2): asks have no Stop; the header menu leads with Stop the agent", () => {
  it("an ask's bottom row is the answer well only: pen, draft, microphone, send", () => {
    const body = render(ComposerWithQueryClient, { props: { ...base, running: true, ask, onStop: () => {}, onCancelTurn: () => {} } }).body;
    expect(body).not.toContain("data-conversation-stop");
    expect(body).not.toContain("chat-ask-stop");
    expect(body).toContain("chat-ask-pen");
    expect(body).toMatch(/class="chat-send chat-ask-send/);
    expect(composer).not.toContain("chat-ask-stop");
  });
  it("while a turn is open the menu's first item is Stop the agent, which stops through onStop", () => {
    const actions = pane.slice(pane.indexOf('<div class="chat-overflow-actions" role="menu"'));
    const first = actions.indexOf("{#if running && onStop}");
    expect(first).toBeGreaterThan(0);
    expect(first).toBeLessThan(actions.indexOf("{#if confirmArmed}"));
    expect(actions).toMatch(/data-conversation-menu-stop\s+onclick=\{stopFromTheMenu\}\s*>Stop the agent<\/button>/);
    expect(pane).toMatch(/function stopFromTheMenu\(\): void \{\s*closeMenu\(\);\s*onStop\?\.\(\);/);
  });
});

describe("v14 (3): no ask option is lit at rest", () => {
  it("the v13 first-row rest style is gone; keycaps are a neutral #3a3a3a; hover and focus keep the accent", () => {
    expect(shell).not.toContain(".cv-ask-row:first-child");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-key) { border-radius: 6px; background: #3a3a3a;");
    expect(shell).toContain(":global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible) .cv-ask-key) { background: var(--cv-accent); color: #141414; }");
  });
});

describe("v14 (4): disabled send and pager arrow", () => {
  it("disabled send #2a2a2a with a #9a9a9a arrow, on the composer and in the ask well", () => {
    expect(app).toContain(".chat-send { background: #2a2a2a; color: #9a9a9a; }");
    expect(composer).toMatch(/:global\(\.chat-ask-send-round\) \{[^}]*background: #2a2a2a;\s*color: #9a9a9a;/);
  });
  it("an unavailable pager arrow is #6e6e6e", () => {
    expect(shell).toContain(".cv-ask-context :global(.cv-icon-button:disabled) { color: #6e6e6e;");
  });
});

describe("v14 (5): narration in Full", () => {
  it("14/24 #bdbdbd, wrapped pretty; its chips are the answers' chip at 0.85em in #c8c8c8", () => {
    expect(app).toContain('[data-conversation-pane][data-conversation-lens="full"] :is(.chat-a--narration, .c2-streaming) .markdown { font-size: 14px; line-height: 24px; color: #bdbdbd; text-wrap: pretty; }');
    expect(app).toContain('[data-conversation-pane][data-conversation-lens="full"] .chat-thread :is(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code { font-size: 0.85em; line-height: 1; padding: 0 4px; border-radius: 4px; background: #232323; color: #c8c8c8; }');
  });
});

describe("v14 (6): the live tool row", () => {
  it("Running is #c4c4c4 500 at rest and lights under the pointer", () => {
    expect(run).toContain(".c2-run-summary.is-live .c2-run-verb { color: #c4c4c4; }");
    expect(run).toMatch(/\.c2-run-summary\.is-live \.c2-run-verb \{[^}]*font-weight: var\(--cv-weight-label\);/);
    expect(run).toContain(".c2-run-summary.is-live:is(:hover, :focus-visible) .c2-run-verb { color: var(--cv-ink, #ececec); }");
    expect(tool).toContain('.acp-step[data-conversation-tool-status="running"] .c2-tool-verb { color: #c4c4c4; }');
  });
});

describe("v14 (7, 8): Steered and Queued notes, the queued bubble", () => {
  it("Steered: icon and label #bdbdbd 500, detail #a6a6a6", () => {
    expect(status).toContain(".c2-status[data-conversation-steered] .c2-status-title { color: #bdbdbd; font-weight: var(--cv-weight-label); }");
    expect(status).toContain(".c2-status[data-conversation-steered] .c2-status-mark { stroke: #bdbdbd; }");
    expect(status).toContain(".c2-status[data-conversation-steered] :is(.c2-status-next, .c2-status-sep) { color: #a6a6a6; }");
  });
  it("Queued: label #bdbdbd 500 (icon #bdbdbd), detail #a6a6a6; the bubble's frame 1px #4a4a4a", () => {
    expect(held).toContain(".chat-qrow-word { color: #bdbdbd; }");
    expect(held).toMatch(/\.chat-qrow-word \{ color: #ececec; font-weight: var\(--cv-weight-label\); \}/);
    expect(held).toMatch(/\.chat-qrow-clock \{[^}]*stroke: #bdbdbd;/);
    expect(held).toContain(".chat-qrow-note, .chat-qrow-sep, .chat-qrow-detail { color: #a6a6a6; }");
    expect(held).toContain(".chat-qrow :global(.c2-bubble) { box-shadow: inset 0 0 0 1px #4a4a4a; }");
  });
});

// v14 (9, 10) were withdrawn by v15 (1, 2): see conversation-design-v15.test.ts.

describe("v14 (11): chart legend", () => {
  it("starts on the chart title's left edge inside the conversation", () => {
    expect(app).toContain("[data-conversation-pane] .chat-thread .chat-chart .goal-chart-host .snapshot-legend { padding-inline-start: 0 !important; }");
  });
});
