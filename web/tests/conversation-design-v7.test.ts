import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync, existsSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));

describe("v8 type: one agent face (Inter), no serif", () => {
  it("Newsreader and its notice are gone; nothing in the app names a serif face", () => {
    expect(existsSync(new URL("../../assets/fonts/Newsreader.woff2", import.meta.url))).toBe(false);
    expect(existsSync(new URL("../../assets/fonts/Newsreader-Italic.woff2", import.meta.url))).toBe(false);
    expect(existsSync(new URL("../../assets/fonts/Newsreader-OFL.txt", import.meta.url))).toBe(false);
    expect(tokens).not.toContain("Newsreader");
    expect(app).not.toContain("--cv-font-answer");
    expect(source("../../THIRD_PARTY_NOTICES.md")).not.toContain("Newsreader");
    expect(`${app}\n${tokens}`.replace(/sans-serif/g, "")).not.toMatch(/(?:font-family|font|--[\w-]*font[\w-]*)\s*:[^;}]*\bserif\b/);
  });
  it("all agent prose is Inter 15/24, the final message #ececec; headings in the Inter scale, 24 above and 8 below", () => {
    expect(app).toMatch(/\[data-conversation-pane\] \.chat-a \.markdown \{\s*font-family: var\(--cv-font\);\s*font-size: 15px;\s*line-height: 24px;\s*font-weight: 400;\s*color: var\(--cv-ink\);/);
    expect(app).toContain("[data-conversation-pane] .chat-a .markdown :is(h1, h2, h3, h4, h5, h6) { margin: 24px 0 8px; font-family: var(--cv-font); font-weight: 600; letter-spacing: 0; }");
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown li::marker { color: #8f8f8f; }");
  });
  it("narration is the same face, v9 (15) 14/22 #b4b4b4; inline code 0.85em on #262626; tables (v9 8) 14/20 head over #3a3a3a, 15/24 body, 40px rows", () => {
    expect(app).toMatch(/\[data-conversation-pane\] \.chat-a--narration,[\s\S]{0,120}\{\s*font-family: var\(--cv-font\);\s*font-size: 14px;\s*line-height: 22px;\s*color: #b4b4b4;/);
    expect(app).toMatch(/\[data-conversation-pane\] \.cv-code \{\s*font-family: var\(--cv-mono\);\s*font-size: 0\.85em;[^}]*padding: 1px 5px;[^}]*border-radius: 5px;\s*background: #262626;/);
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown :is(th, td) { height: 40px;");
    expect(app).toContain("font-size: 14px; line-height: 20px; font-weight: 600; border-block-end-color: #3a3a3a; }");
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown td { color: #d9d9d9; font-size: 15px; line-height: 24px; font-weight: 400; }");
  });
});

describe("v7 rhythm", () => {
  it("40 between turns, 8 meta to content, 16 between paragraphs, 12 bubble to attachments", () => {
    const scoped = tokens.slice(tokens.indexOf("[data-conversation-pane] {"));
    for (const token of ["--cv-gap-turn: 40px;", "--cv-gap-meta: 8px;", "--cv-gap-paragraph: 16px;", "--cv-gap-bubble-stack: 12px;"])
      expect(scoped).toContain(token);
  });
});

describe("v7 the five parts", () => {
  it("1 Surface: composer, newest error, attachment cards, code blocks; bubbles #242424 radius 18 padding 12 16", () => {
    expect(app).toMatch(/\.chat-box \{[^}]*background: #1c1c1c;\s*border: 1px solid #2a2a2a;\s*border-radius: 20px;\s*box-shadow: none;/);
    expect(source("../src/components/conversation/ConversationFileCard.svelte")).toMatch(/border: 1px solid var\(--cv-surface-edge, #2a2a2a\);\s*border-radius: var\(--cv-surface-radius, 20px\);\s*background: var\(--cv-surface, #1c1c1c\);/);
    expect(source("../src/lib/markdown/codeBlocks.css")).toMatch(/border-radius: 12px;\s*background: var\(--cv-code-surface, #1a1a1a\);/);
    expect(source("../src/components/conversation/MessagePieces.svelte")).toMatch(/background: #242424;\s*color: #ececec;\s*border-radius: 18px;\s*padding: 12px 16px;/);
  });
  it("2 Button: v10 (4) asks draw no confirm pill and no decline button; Deny is an option row; v14 (2): no Stop in the ask", () => {
    expect(composer).not.toContain("chat-ask-primary");
    expect(composer).not.toContain("chat-ask-quiet");
    expect(composer).not.toContain('class="chat-ask-stop"');
    const controls = source("../src/components/conversation/composer/ComposerRunControls.svelte");
    expect(controls).not.toContain("data-conversation-ask-primary");
    expect(controls).not.toContain("data-conversation-ask-decline");
    const docked = render(PermissionAskCard, { props: { docked: true, onAnswer: () => {}, ask: { askId: "a", title: "Upload", detail: null, options: [
      { option_id: "allow", label: "Allow once", option_kind: "allow_once" }, { option_id: "deny", label: "Deny", option_kind: "reject_once" }] } } }).body;
    expect(docked).toContain('data-conversation-ask-action="deny"');
  });
  it("3 Composer control: 32 tall, text-only #d9d9d9 (v8 14: no chevron); Send/Stop a 32 round", () => {
    expect(app).toMatch(/\[data-conversation-composer\] \.chat-foot \.listbox-picker-trigger \{[^}]*height: 32px;[^}]*background: transparent;[^}]*color: #d9d9d9;/);
    expect(app).toMatch(/\.chat-send \{[^}]*width: 32px; height: 32px;/);
    expect(app).not.toMatch(/model-picker\] \.listbox-picker-chevron-drawn/);
  });
  it("4 Status line: 13/20, a 16px icon slot, label 500 #ececec, detail #b0b0b0", () => {
    const anchor = source("../src/components/conversation/TurnAnchor.svelte");
    expect(anchor).toMatch(/\.c2-meta \{[^}]*gap: 8px;[^}]*color: var\(--cv-detail-ink, #b0b0b0\);\s*font-family: var\(--cv-font\);\s*font-size: 13px;\s*line-height: 20px;/);
    expect(anchor).toContain(".c2-meta-slot { flex: none; display: inline-grid; place-items: center; width: 16px; height: 16px; }");
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain(".c2-status-title { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }");
    expect(row).toContain(".c2-status--error .c2-status-dot { background: #f08a7e; }");
  });
  it("5 Option row: min 48, radius 12; v11 (2): #2a2a2a with a #363636 edge, keycap 24 #3a3a3a radius 6; hover/focus #303030, accent edge and keycap", () => {
    const shell = source("../src/components/conversation/AskShell.svelte");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-row) { min-height: 48px; padding: 12px 16px; border-radius: 12px; background: #242424;");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-row) { background: #2a2a2a; box-shadow: inset 0 0 0 1px #363636; }");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-key) { border-radius: 6px; background: #3a3a3a;");
    // v10 (4): hover and keyboard focus (and a chosen answer) light a row: v11 (2) #303030, the
    // accent edge and an accent keycap. Nothing is lit otherwise.
    expect(shell).toContain(":global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible)) { background: #303030; box-shadow: inset 0 0 0 1px rgb(188 203 240 / 55%); }");
    expect(shell).toContain(":global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row:hover:not(:disabled), .cv-ask-row:focus-visible) .cv-ask-key) { background: var(--cv-accent); color: #141414; }");
    expect(shell).not.toContain("is-focused");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-rows) { gap: 8px; }");
  });
});

describe("v7 colour and motion", () => {
  it("failure is #f08a7e; the Queued menu's Remove is not red; v8 (10): the chart series are #bccbf0 and #e0b36b", () => {
    expect(tokens.slice(tokens.indexOf("[data-conversation-pane] {"))).toContain("--cv-danger: #f08a7e;");
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).toContain(".chat-qrow-x { color: #ececec; }");
    expect(app).toMatch(/--accent-bright: #bccbf0;\s*--chart-compare-second: #e0b36b;/);
  });
  it("the composer grows into an ask over 220 ms and its contents fade in over 120 ms; status icons fade 150 ms", () => {
    expect(composer).toContain('{ duration: 220, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" }');
    expect(composer).toContain("[{ opacity: 0 }, { opacity: 1 }], { duration: 120, easing: \"ease-out\" }");
    expect(composer).toContain('window.matchMedia("(prefers-reduced-motion: reduce)").matches');
    expect(app).toContain("animation: cv-status-icon-in 150ms ease-out;");
  });
});
