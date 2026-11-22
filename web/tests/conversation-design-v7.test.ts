import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync, existsSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const composer = source("../src/components/conversation/ConversationComposer.svelte");

describe("v7 type: two voices", () => {
  it("Newsreader is bundled with its OFL licence and declared for the pane's answers", () => {
    expect(existsSync(new URL("../../assets/fonts/Newsreader.woff2", import.meta.url))).toBe(true);
    expect(existsSync(new URL("../../assets/fonts/Newsreader-Italic.woff2", import.meta.url))).toBe(true);
    expect(source("../../assets/fonts/Newsreader-OFL.txt")).toContain("SIL OPEN FONT LICENSE Version 1.1");
    expect(tokens).toMatch(/@font-face \{\s*font-family: "Newsreader";\s*font-style: italic;/);
    const scoped = tokens.slice(tokens.indexOf("[data-conversation-pane] {"));
    expect(scoped).toContain('--cv-font-answer: "Newsreader"');
    // Only the conversation pane uses the face, so only the pane loads it.
    expect(app.match(/var\(--cv-font-answer\)/g)?.length).toBeGreaterThan(0);
    for (const rule of app.split("}").filter((block) => block.includes("var(--cv-font-answer)")))
      expect(rule.trim().startsWith("[data-conversation-pane]") || rule.includes("\n[data-conversation-pane]")).toBe(true);
  });
  it("final agent messages are 17/28 Newsreader #ececec; headings 22/30 and 18/26 600, 24 above and 8 below", () => {
    expect(app).toMatch(/\.chat-a:not\(\.chat-a--narration\):not\(\.c2-streaming\) \.markdown \{\s*font-family: var\(--cv-font-answer\);\s*font-size: 17px;\s*line-height: 28px;\s*font-weight: 400;\s*color: var\(--cv-ink\);/);
    expect(app).toMatch(/\.markdown :is\(h1, h2\) \{ font-size: 22px; line-height: 30px; \}/);
    expect(app).toMatch(/\.markdown :is\(h3, h4, h5, h6\) \{ font-size: 18px; line-height: 26px; \}/);
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown li::marker { color: #8f8f8f; }");
  });
  it("narration is the UI voice 14/22 #bdbdbd; inline code 0.85em on #262626; tables 13/20 head, 14/20 body, 40px rows", () => {
    expect(app).toMatch(/\[data-conversation-pane\] \.chat-a--narration,[\s\S]{0,120}\{\s*font-family: var\(--cv-font\);\s*font-size: 14px;\s*line-height: 22px;\s*color: var\(--cv-quiet-ink\);/);
    expect(app).toMatch(/\[data-conversation-pane\] \.cv-code \{\s*font-family: var\(--cv-mono\);\s*font-size: 0\.85em;[^}]*padding: 1px 5px;[^}]*border-radius: 5px;\s*background: #262626;/);
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown :is(th, td) { height: 40px;");
    expect(app).toContain("font-size: 13px; line-height: 20px; font-weight: 600; border-block-end-color: #2a2a2a; }");
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
  it("2 Button: the ask's primary 32 pill #f2f2f2/#141414; Deny is a quiet button beside it", () => {
    expect(composer).toMatch(/\.chat-box :global\(\.chat-ask-primary\) \{[^}]*height: 32px;\s*padding: 0 14px;[^}]*background: #f2f2f2;\s*color: #141414;/);
    expect(composer).toMatch(/\.chat-box :global\(\.chat-ask-quiet\) \{[^}]*height: 32px;[^}]*background: transparent;\s*color: #bdbdbd;/);
    const controls = source("../src/components/conversation/composer/ComposerRunControls.svelte");
    expect(controls.indexOf("data-conversation-ask-decline")).toBeLessThan(controls.indexOf("data-conversation-ask-primary"));
    const docked = render(PermissionAskCard, { props: { docked: true, onAnswer: () => {}, ask: { askId: "a", title: "Upload", detail: null, options: [
      { option_id: "allow", label: "Allow once", option_kind: "allow_once" }, { option_id: "deny", label: "Deny", option_kind: "reject_once" }] } } }).body;
    expect(docked).not.toContain('data-conversation-ask-action="deny"');
  });
  it("3 Composer control: 32 tall, text-only #d9d9d9, a 12px chevron; Send/Stop a 32 round", () => {
    expect(app).toMatch(/\[data-conversation-composer\] \.chat-foot \.listbox-picker-trigger \{[^}]*height: 32px;[^}]*background: transparent;[^}]*color: #d9d9d9;/);
    expect(app).toMatch(/\.chat-send \{[^}]*width: 32px; height: 32px;/);
    expect(app).toMatch(/model-picker\] \.listbox-picker-chevron-drawn \{[^}]*width: 12px;\s*height: 12px;/);
  });
  it("4 Status line: 13/20, a 16px icon slot, label 500 #ececec, detail #b0b0b0", () => {
    const anchor = source("../src/components/conversation/TurnAnchor.svelte");
    expect(anchor).toMatch(/\.c2-meta \{[^}]*gap: 8px;[^}]*color: var\(--cv-detail-ink, #b0b0b0\);\s*font-family: var\(--cv-font\);\s*font-size: 13px;\s*line-height: 20px;/);
    expect(anchor).toContain(".c2-meta-slot { flex: none; display: inline-grid; place-items: center; width: 16px; height: 16px; }");
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain(".c2-status-title { color: var(--cv-ink, #ececec); font-weight: var(--cv-weight-label); }");
    expect(row).toContain(".c2-status--error .c2-status-dot { background: #f08a7e; }");
  });
  it("5 Option row: min 48, #242424, radius 12; keycap 24 #2e2e2e radius 6; focused ring and accent keycap", () => {
    const shell = source("../src/components/conversation/AskShell.svelte");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-row) { min-height: 48px; padding: 12px 16px; border-radius: 12px; background: #242424;");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-key) { border-radius: 6px; background: #2e2e2e;");
    expect(shell).toContain(":global([data-conversation-pane] :is(.cv-ask-row.is-selected, .cv-ask-row.is-focused) .cv-ask-key) { background: var(--cv-accent); color: #141414; }");
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-rows) { gap: 8px; }");
  });
});

describe("v7 colour and motion", () => {
  it("failure is #f08a7e; the Queued menu's Remove is not red; the chart series are neutral", () => {
    expect(tokens.slice(tokens.indexOf("[data-conversation-pane] {"))).toContain("--cv-danger: #f08a7e;");
    expect(source("../src/components/conversation/composer/HeldPromptStack.svelte")).toContain(".chat-qrow-x { color: #ececec; }");
    expect(app).toMatch(/--accent-bright: #ececec;\s*--chart-compare-second: #8f8f8f;/);
  });
  it("the composer grows into an ask over 220 ms and its contents fade in over 120 ms; status icons fade 150 ms", () => {
    expect(composer).toContain('{ duration: 220, easing: "cubic-bezier(0.2, 0.7, 0.3, 1)" }');
    expect(composer).toContain("[{ opacity: 0 }, { opacity: 1 }], { duration: 120, easing: \"ease-out\" }");
    expect(composer).toContain('window.matchMedia("(prefers-reduced-motion: reduce)").matches');
    expect(app).toContain("animation: cv-status-icon-in 150ms ease-out;");
  });
});
