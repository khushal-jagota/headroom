import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import AskShell from "../src/components/conversation/AskShell.svelte";
import ToolRunSummary from "../src/components/conversation/ToolRunSummary.svelte";
import type { ToolCallPresentation } from "../src/lib/conversation/toolCallPresentation";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const composer = source("../src/components/conversation/ConversationComposer.svelte");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const shell = source("../src/components/conversation/AskShell.svelte");
const status = source("../src/components/conversation/TranscriptStatusRow.svelte");
const held = source("../src/components/conversation/composer/HeldPromptStack.svelte");
const mode = source("../src/components/conversation/composer/DeliveryModePicker.svelte");
const run = source("../src/components/conversation/ToolRunSummary.svelte");
const user = source("../src/components/conversation/UserMessage.svelte");

describe("v16 (1): one inline code style in answers and in Full narration", () => {
  it("mono 0.9em #e0e0e0 on #2a2a2a, radius 4, padding 0 4px, for both", () => {
    expect(app).toContain('[data-conversation-pane][data-conversation-lens="full"] .chat-thread :is(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code { font-family: var(--cv-mono); font-size: 0.9em; line-height: 1; padding: 0 4px; border-radius: 4px; background: #2a2a2a; color: #e0e0e0; }');
    const block = app.slice(app.indexOf("v16 (22 Nov, after round 22)"));
    expect(block).toContain("[data-conversation-pane] .chat-thread .markdown :not(pre) > code,\n[data-conversation-pane] .cv-code,");
  });
});

describe("v16 (2): every waiting ask leads with the accent dot", () => {
  it("a permission ask has the dot and no shield, like a question", () => {
    for (const kind of ["permission", "question"] as const) {
      const body = render(AskShell, { props: { kind, eyebrow: "Needs you", title: "T" } }).body;
      expect(body).toContain("cv-ask-dot");
      expect(body).not.toContain("data-ask-shield");
    }
    expect(shell).not.toContain("cv-ask-shield");
  });
});

describe("v16 (3): the Focus/Full control", () => {
  it("the other lens is #d0d0d0 400; the shown one #424242 with #ffffff 500", () => {
    expect(pane).toContain(".cv-segment { color: #d0d0d0; }");
    expect(pane).toContain('.cv-segment[aria-pressed="true"], .cv-segment[aria-pressed="true"]:hover { background: #424242; color: #ffffff; }');
  });
});

describe("v16 (4): attachment chips", () => {
  it("a picture has a 28px top scrim under the remove button; a file chip ends with 40px", () => {
    expect(composer).toContain(".chat-att.img::after { background: linear-gradient(rgb(0 0 0 / 55%), transparent) top / 100% 28px no-repeat; }");
    expect(composer).toContain(".chat-att.file { padding-inline-end: 40px; }");
  });
});

describe("v16 (5): disabled send has no fill and a #8a8a8a arrow", () => {
  it("in the composer and in the ask well; Stop keeps its ring", () => {
    expect(app).toContain(".chat-send:not(.on, .stop) { background: none; color: #8a8a8a; }");
    expect(composer).toContain(":global(.chat-ask-send:not(.on) .chat-ask-send-round) { background: none; color: #8a8a8a; }");
    expect(app).toContain(".chat-send.stop { background: none; box-shadow: inset 0 0 0 1px #4a4a4a; color: #ececec; }");
  });
});

describe("v16 (6): the live tool row in Full", () => {
  const running = { title: "Running", summary: "go test -race ./...", summaryIsLiteral: true, sentence: "Running go test" } as unknown as ToolCallPresentation;
  it("a running row keeps its icon, marked running, and has no spinner (only Working has one)", () => {
    const html = render(ToolRunSummary, { props: { summary: { sentence: "Ran 2 commands", failedCount: 0, running, iconPaths: ["M1 1"] }, callCount: 3, onToggle: () => {} } }).body;
    expect(html).toMatch(/<svg class="c2-run-icon[^"]*"[^>]*data-conversation-run-icon="running"/);
    expect(html).not.toContain("spinner");
  });
  it("a settled row keeps its icon, not marked running", () => {
    const html = render(ToolRunSummary, { props: { summary: { sentence: "Ran 2 commands", failedCount: 0, running: null, iconPaths: ["M1 1"] }, callCount: 2, onToggle: () => {} } }).body;
    expect(html).toContain("c2-run-icon");
    expect(html).not.toContain('data-conversation-run-icon="running"');
  });
  it("live: the icon #ececec, Running #ececec 500, the command #c4c4c4; settled icon stays quiet", () => {
    expect(run).toContain(".c2-run-summary.is-live .c2-run-icon { stroke: #ececec; }");
    expect(run).toMatch(/\.c2-run-icon \{[^}]*stroke: var\(--cv-quiet-ink, #bdbdbd\);/);
    expect(run).not.toContain("cv-spinner");
    expect(run).toContain(".c2-run-summary.is-live .c2-run-verb { color: #ececec; font-weight: var(--cv-weight-label); }");
    expect(run).toContain(".c2-run-summary.is-live .c2-run-subject { color: #c4c4c4; }");
    expect(run).toContain(".c2-run-summary, .c2-run-summary .c2-run-verb { color: #c4c4c4; }");
  });
});

describe("v16 (7): answer headings", () => {
  it("h1 and h2 19/26 600 #f2f2f2; h3 17/24 600", () => {
    expect(app).toContain("[data-conversation-pane] .chat-thread .chat-a .markdown :is(h1, h2) { font-size: 19px; line-height: 26px; font-weight: 600; color: #f2f2f2; }");
    expect(app).toContain("[data-conversation-pane] .chat-thread .chat-a .markdown h3 { font-size: 17px; line-height: 24px; font-weight: 600; color: #ececec; }");
  });
});

describe("v16 (8): links in answers", () => {
  it("the accent #bccbf0 with a 1px 40% underline 3px down; full colour on hover", () => {
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown a:not(.file-preview-artifact-link, .pv-act) { color: #bccbf0; text-decoration-line: underline; text-decoration-thickness: 1px; text-decoration-color: rgb(188 203 240 / 40%); text-underline-offset: 3px; }");
    expect(app).toContain("[data-conversation-pane] .chat-thread .markdown a:not(.file-preview-artifact-link, .pv-act):hover { color: #bccbf0; text-decoration-color: #bccbf0; }");
    expect(app).not.toContain("9db4ff");
  });
});

describe("v16 (9): the mode menu fits its content", () => {
  it("no minimum width, 12px label padding, 16px to the check, unselected #e0e0e0", () => {
    expect(mode).toMatch(/width: max-content;\s*min-width: 0;/);
    expect(mode).toMatch(/\.listbox-picker-option\) \{\s*gap: 16px;\s*padding-inline: 12px;\s*color: #e0e0e0;/);
  });
});

describe("v16 (10): the queued more button", () => {
  it("is a 28px target, icon #c4c4c4, hover #2a2a2a", () => {
    expect(held).toContain(".chat-qrow-more { width: 28px; height: 28px; color: #c4c4c4; }");
    expect(held).toContain(".chat-qrow-more:hover:not(:disabled) { background: #2a2a2a; }");
  });
});

describe("v16 (12): quiet text one step up", () => {
  it("option descriptions, attachment meta, the empty hint, From Panels, queued and steered details", () => {
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-sub) { color: #c4c4c4; }");
    expect(composer).toContain(".chat-att-meta { color: #b8b8b8; }");
    expect(pane).toMatch(/\.cv-empty-hint \{[^}]*color: #b8b8b8;/);
    expect(user).toContain(".c2-system-note { color: #c4c4c4; }");
    expect(held).toContain(".chat-qrow-note, .chat-qrow-sep, .chat-qrow-detail { color: #c4c4c4; }");
    expect(status).toContain(".c2-status[data-conversation-steered] :is(.c2-status-next, .c2-status-sep) { color: #c4c4c4; }");
  });
});

describe("v16 (13): the error card", () => {
  it("has a neutral #3a3a3a edge and a #ff8f85 title; the red dot stays", () => {
    expect(status).toContain(".c2-status--callout.c2-status--error { border-color: #3a3a3a; }");
    expect(status).toContain(".c2-status--callout.c2-status--error .c2-status-title { color: #ff8f85; }");
    expect(status).toContain(".c2-status--error .c2-status-dot { background: #f08a7e; }");
  });
});

describe("v16 (11): the grown composer", () => {
  it("has the same 12px padding at the bottom as at the top", () => {
    expect(composer).toContain(".chat-box:not(.is-compact, .has-ask) { padding-block-end: var(--cv-s3); }");
    expect(app).toMatch(/\.chat-box \{[^}]*padding: var\(--cv-s3\) var\(--cv-s2\) var\(--cv-s2\);/);
  });
});
