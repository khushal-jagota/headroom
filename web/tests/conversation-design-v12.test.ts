import { describe, expect, it } from "vitest";
import { render } from "svelte/server";
// @ts-expect-error -- the tests' type check has no Node types; vitest runs in Node.
import { readFileSync } from "node:fs";
import ConversationComposer from "../src/components/conversation/ConversationComposer.svelte";

const source = (path: string): string => readFileSync(new URL(path, import.meta.url), "utf8") as string;
const app = source("../../assets/app.css");
const tokens = source("../../assets/tokens.css");
const composer = source("../src/components/conversation/ConversationComposer.svelte");

describe("v12 (1): agent answers are plain text again", () => {
  it("no answer bubble: no rule gives a final answer a surface, radius or padding", () => {
    expect(app).not.toMatch(/\.chat-thread \.chat-a:not\(\.chat-a--narration, \.c2-streaming\) \{/);
    expect(app).not.toContain(":has(.chat-chart, .goal-chart-host) { width: 100%; }");
    expect(app).not.toContain(".chat-a:not(.chat-a--narration, .c2-streaming) .markdown :not(pre) > code { background: #2a2a2a; }");
  });
  it("the reading column is 640; the dock keeps the page's 760", () => {
    expect(tokens).toContain("--cv-col: 640px;");
    expect(tokens).toContain("--page-measure: 760px;");
    expect(app).toContain('[data-conversation-dock-state="opened"] > .conversation-layer > .conversation-column {\n  width: min(var(--cv-col), calc(100% - 2 * var(--space-5)));');
  });
});

describe("v12 (2): narration in Full is flush left", () => {
  it("no indent on narration; still 13/20 #a6a6a6", () => {
    expect(app).not.toMatch(/:is\(\.chat-a--narration, \.c2-streaming\) \{ padding-inline-start: 24px; \}/);
    expect(app).toContain('[data-conversation-pane][data-conversation-lens="full"] :is(.chat-a--narration, .c2-streaming) .markdown { font-size: 13px; line-height: 20px; color: #a6a6a6; }');
  });
});

describe("v12 (3): Stop inside an ask is secondary", () => {
  it("the ask's round Stop is #2a2a2a with a #ececec square; the composer's Stop stays #f2f2f2", () => {
    expect(composer).toContain(".chat-send.stop.chat-ask-stop { background: #2a2a2a; color: #ececec; }");
    expect(composer).toContain(".chat-send.stop.chat-ask-stop:hover:not(:disabled) { background: #333333; }");
    expect(app).toContain(".chat-send.stop { background: #f2f2f2; color: #141414; }");
  });
  it("it is still the same 32px round button with the stop square, at the end of the bottom row", () => {
    expect(composer).toMatch(/class="chat-send stop chat-ask-stop" data-conversation-stop=\{true\} data-conversation-ask-stop\s+title="Stop turn" aria-label="Stop turn"[^\n]*<span class="chat-send-fill"/);
    expect(composer).toMatch(/\.chat-box\.has-ask > \.chat-foot > \.chat-ask-stop \{[^}]*order: 4;/);
    expect(ConversationComposer).toBeTruthy();
  });
});
