import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import ToolRunSummary from "../src/components/conversation/ToolRunSummary.svelte";
import type { ToolRunSummary as Summary } from "../src/lib/conversation/toolCallPresentation";

const css = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;

describe("design v4.1: a tool run's chevron stands right after its label", () => {
  const closed: Summary = { sentence: "Ran 2 commands", failedCount: 0, running: null, iconPaths: [] };

  it("closed: the sentence, then the chevron, with no empty status slot between them", () => {
    const html = render(ToolRunSummary, { props: { summary: closed, callCount: 2, onToggle: () => {} } }).body;
    expect(html.indexOf("Ran 2 commands")).toBeLessThan(html.indexOf("c2-run-chevron"));
    expect(html).not.toContain("data-conversation-work-status");
  });

  it("a failure is part of the label, before the chevron", () => {
    const html = render(ToolRunSummary, { props: { summary: { ...closed, failedCount: 1 }, callCount: 2, onToggle: () => {} } }).body;
    expect(html.indexOf("1 failed")).toBeLessThan(html.indexOf("c2-run-chevron"));
  });
});

describe("design v4.1: surfaces", () => {
  const app = css("../../assets/app.css");
  it("the model chip wears the Steer chip's fill, a chevron and a middle dot before the effort", () => {
    expect(app).toMatch(/\[data-conversation-model-picker\] > \.listbox-picker-trigger \{[^}]*background: var\(--cv-control\)/);
    expect(app).toMatch(/\[data-conversation-model-picker\] \.listbox-picker-chevron-drawn \{[^}]*display: block/);
    expect(app).toContain('.model-picker-effort::before { content: "· "; }');
  });
  it("pictures in the conversation carry a 1px white 10% edge", () => {
    expect(app).toContain("[data-conversation-pane] .file-preview-media::after { box-shadow: inset 0 0 0 1px rgb(255 255 255 / 10%); }");
  });
  it("an error callout is the one tinted surface", () => {
    // v5.2: 7% red fill held by a 22% red edge.
    expect(css("../src/components/conversation/TranscriptStatusRow.svelte")).toContain(".c2-status--callout.c2-status--error { background: rgb(229 132 138 / 7%); box-shadow: inset 0 0 0 1px rgb(229 132 138 / 22%); }");
  });
  it("the empty conversation keeps its composer in its normal bottom place", () => {
    const pane = css("../src/components/conversation/ConversationPane.svelte");
    expect(pane).not.toMatch(/\[data-conversation-empty-bare\]\) > \.c2-composer/);
    expect(pane).not.toMatch(/\[data-conversation-empty-bare\]\) > \.conversation-reader/);
  });
});

describe("owner, 22 Nov: a docked conversation shows a waiting ask as its composer", () => {
  it("the pane always draws the composer, ask included, whether the conversation is at rest or opened", () => {
    const pane = css("../src/components/conversation/ConversationPane.svelte");
    // The composer is gated only by read-only, never by the dock state, and it is handed the
    // ask, the question and the key request, so at rest the ask is the dock's composer.
    const composerBlock = pane.slice(pane.indexOf("{#if readOnly}\n    <div class=\"conversation-read-only\""), pane.indexOf("<ConversationComposer"));
    expect(composerBlock).not.toContain("conversationState");
    const composer = pane.slice(pane.indexOf("<ConversationComposer"), pane.indexOf("/>", pane.indexOf("<ConversationComposer")));
    expect(composer).toContain("{ask}");
    expect(composer).toContain("{userInput}");
    expect(composer).toContain("{credentialRequest}");
    // Nothing hides the ask in the dock.
    expect(css("../../assets/app.css")).not.toMatch(/dock-state[^{]*\{[^}]*\}[^{]*data-ask-shell|data-ask-shell[^{]*\{[^}]*display:\s*none/);
  });
});
