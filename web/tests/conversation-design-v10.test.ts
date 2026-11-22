import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import PermissionAskCard from "../src/components/conversation/PermissionAskCard.svelte";
import UserInputQuestionPanel from "../src/components/conversation/UserInputQuestionPanel.svelte";
import TranscriptStatusRow from "../src/components/conversation/TranscriptStatusRow.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const pane = source("../src/components/conversation/ConversationPane.svelte");
const composer = (source("../src/components/conversation/ConversationComposer.svelte") + source("../src/components/conversation/composer/ComposerAttachments.svelte") + source("../src/components/conversation/composer/ComposerAddMenu.svelte") + source("../src/components/conversation/composer/catalog.ts"));
const controls = source("../src/components/conversation/composer/ComposerRunControls.svelte");
const shell = source("../src/components/conversation/AskShell.svelte");
const v10 = app.slice(app.indexOf("v10 (22 Nov, after round 16)"));

describe("v10 (2): Steered and Queued captions", () => {
  it("the steered caption's detail is #c4c4c4", () => {
    expect(source("../src/components/conversation/UserMessage.svelte")).toContain("title: 'Steered', next: 'into the running turn'");
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain(".c2-status[data-conversation-steered] :is(.c2-status-next, .c2-status-sep) { color: #c4c4c4; }");
  });
});

describe("v10 (3, 4): asks", () => {
  const ask = { askId: "a", title: "Upload build 41", detail: "xcrun altool --upload-app", options: [
    { option_id: "allow", label: "Allow once", option_kind: "allow_once" as const },
    { option_id: "always", label: "Always allow uploads", option_kind: "allow_always" as const },
    { option_id: "deny", label: "Deny", option_kind: "reject_once" as const }] };

  it("a docked permission ask draws no Stop of its own (the composer's footer pill is the one) and no row is lit", () => {
    const html = render(PermissionAskCard, { props: { ask, docked: true, onAnswer: () => {} } }).body;
    expect(html).not.toContain("data-conversation-stop");
    expect(html).not.toContain("is-selected");
    expect(html).not.toContain("aria-pressed");
    expect(html.match(/class="cv-ask-row c2-ask-choice[^"]*"/g)?.length).toBe(3);
  });

  it("the send slot under an ask keeps the round send; its disabled state lifts for a several-answer question with a choice", () => {
    expect(controls).toContain("{#if asking || view.submit.active || view.submit.sending || !view.showStop}");
    expect(controls).toContain('class={`chat-send${view.submit.active || askCommitReady ? " on" : ""}`}');
    expect(controls).toContain("disabled={view.submit.disabled && !askCommitReady}");
    expect(controls).toContain("{#if !asking && view.showStop}");
    expect(composer).toContain('askCommitReady={userInput !== null && questionCommitReady && text.trim() === "" && attachments.length === 0}');
    expect(composer).toMatch(/if \(userInput !== null && questionPanel !== null && questionCommitReady && text\.trim\(\) === "" && attachments\.length === 0\) \{\s*questionPanel\.commit\(\);\s*return;\s*\}/);
  });

  it("a single-answer question has no forward pill; choosing a row moves on (the last choice sends)", () => {
    const panel = source("../src/components/conversation/UserInputQuestionPanel.svelte");
    expect(panel).toMatch(/if \(question\.multi_select\) \{\s*draft = toggleUserInputChoice\(draft, question, option\);\s*return;\s*\}[\s\S]*?continueOrSubmit\(\);\s*\}/);
    expect(panel).not.toContain("data-user-input-continue");
    expect(panel).not.toContain("onMount");
    const html = render(UserInputQuestionPanel, { props: { ownAnswer: "", request: { requestId: "q", questions: [
      { question_id: "a", header: "A", question: "Which style?", multi_select: false, allow_other: true, options: [{ label: "Soft", description: "" }, { label: "Line", description: "" }] }] } } }).body;
    expect(html).not.toContain("is-focused");
    expect(html).not.toContain("is-selected");
    expect(html).not.toContain("cv-button--primary");
  });

  it("rows light on hover and keyboard focus only: #2c2c2c with the accent edge", () => {
    expect(shell).toContain(":global([data-conversation-pane] .cv-ask-row:focus-visible) { outline: none; }");
  });
});

describe("v10 (5, 7, 13): the header", () => {
  it("Dock is the #232323 pill; v11 (6): its label alone, no arrow", () => {
    expect(pane).toContain(".chat-head-collapse { background: #232323; color: #d9d9d9; }");
    expect(pane).not.toContain("chat-head-collapse-arrow");
    expect(pane).not.toContain("data-conversation-dock-arrow");
  });
  it("the title is 17/24 600, ends in an ellipsis, and keeps at least 24px (12 margin + 12 gap) before Focus/Full", () => {
    expect(pane).toMatch(/\.chat-head-title \{[^}]*overflow: hidden;\s*white-space: nowrap;\s*text-overflow: ellipsis;[^}]*font: var\(--cv-weight-title\) 17px\/24px var\(--cv-font\);/);
    expect(pane).toMatch(/\.chat-head-identity \{[^}]*margin-inline-end: 12px;/);
    expect(app).toMatch(/\.chat-head \{[^}]*gap: var\(--cv-s3\);/);
  });
});

describe("v10 (6): the error card", () => {
  it("a 1px rgba(240,138,126,0.35) edge and the title in #f08a7e, no tint", () => {
    const row = source("../src/components/conversation/TranscriptStatusRow.svelte");
    expect(row).toContain(".c2-status--callout.c2-status--error { border-color: rgb(240 138 126 / 35%); }");
    expect(row).toContain(".c2-status--callout.c2-status--error .c2-status-title { color: #f08a7e; }");
    const html = render(TranscriptStatusRow, { props: { status: { tone: "error", title: "rate limit reached", next: null, detail: null } as never } }).body;
    expect(html).toContain("This turn failed");
    expect(html).toContain("c2-status--callout c2-status--error");
  });
});

describe("v10 (7, 8, 9, 12): answers", () => {
  it("headings h1/h2 17/24, h3 16/24, all 600", () => {
    expect(v10).toContain("[data-conversation-pane] .chat-thread .chat-a .markdown :is(h1, h2) { font-size: 17px; line-height: 24px; font-weight: 600; }");
    expect(v10).toContain("[data-conversation-pane] .chat-thread .chat-a .markdown :is(h3, h4, h5, h6) { font-size: 16px; line-height: 24px; font-weight: 600; }");
  });
  it("links are #bccbf0 with a 40% underline", () => {
    expect(v10).toContain("color: #bccbf0; text-decoration-color: rgb(188 203 240 / 40%);");
  });
  it("contrast: Worked for #bdbdbd, Ran 2 commands #c4c4c4, chart axis 13/16; v11 (8): dock edge 16%, thumbnails 16%", () => {
    expect(source("../src/components/conversation/TurnAnchor.svelte")).toContain(":is(.c2-meta--fold, [data-conversation-turn-settled-head]), .c2-meta--fold .c2-meta-sep { color: #bdbdbd; }");
    expect(source("../src/components/conversation/ToolRunSummary.svelte")).toContain(".c2-run-summary, .c2-run-summary .c2-run-verb { color: #c4c4c4; }");
    expect(source("../src/lib/charts/components/bar-chart.tsx")).toContain("font: showLegend ? { size: 13, lineHeight: 16 / 13 } : { size: 11 } }");
    expect(app).toContain("background: rgba(255, 255, 255, 0.16); /* v11 (8) */");
    expect(v10).toContain("[data-conversation-pane] .file-preview-media::after { box-shadow: inset 0 0 0 1px rgb(255 255 255 / 16%); }");
    expect(composer).toContain(".chat-att.img::after { content: \"\"; position: absolute; inset: 0; border-radius: inherit; box-shadow: inset 0 0 0 1px rgb(255 255 255 / 16%); pointer-events: none; background: linear-gradient(rgb(0 0 0 / 55%), transparent) top / 100% 28px no-repeat; }");
  });
  it("inline code is #232323 with 1px 4px padding; in narration its text is #b4b4b4", () => {
    expect(v10).toMatch(/\[data-conversation-pane\] \.cv-code \{ padding: 1px 4px; background: #232323; \}/);
    expect(v10).toContain(":is(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code { color: #b4b4b4; }");
  });
});

describe("v10 (10): ask labels", () => {
  it("every ask label is the conversation accent #bccbf0", () => {
    expect(source("../../assets/tokens.css")).toMatch(/\[data-conversation-pane\] \{[\s\S]*?--cv-accent: #bccbf0;/);
    expect(shell).toContain("color: var(--cv-accent);");
    expect(source("../src/components/conversation/ConversationRestBar.svelte")).not.toContain("#a9bbe3");
  });
});
