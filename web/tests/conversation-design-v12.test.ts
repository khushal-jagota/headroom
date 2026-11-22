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

describe("v12 (3): Stop inside an ask (replaced by v13 (2))", () => {
  it("the composer's own Stop while working stays #f2f2f2; the ask's Stop is no longer a separate secondary round", () => {
    expect(app).toContain(".chat-send.stop { background: #f2f2f2; color: #141414; }");
    expect(composer).not.toContain(".chat-send.stop.chat-ask-stop");
    expect(ConversationComposer).toBeTruthy();
  });
});
